from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.domain.money import Price
from ambitious_video.domain.types import GeneratedScene, PaidClip
from ambitious_video.providers.base import Patience, ProviderError, RequestLost, RunResult
from ambitious_video.providers.http import DeferredProvider, Request, Sender, as_object
from ambitious_video.providers.pricing import price_of

REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
RESOLUTIONS = ("480p", "720p", "1080p")
SHAPES = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "5:2")
MIN_SECONDS = 1
MAX_SECONDS = 15
GONE = frozenset({"failed", "expired"})
UNKNOWN = frozenset({404, 410})
DONE = "done"
REFERENCE = "image"
FALLBACK_TYPE = "image/png"


@dataclass(frozen=True, slots=True)
class XaiVideoConfig:
    submit_url: str = "https://api.x.ai/v1/videos/generations"
    status_url: str = "https://api.x.ai/v1/videos/"
    download_schemes: tuple[str, ...] = ("https",)
    timeout_seconds: float = 120
    poll_seconds: float = 5
    wait_seconds: float = 600


def _data_url(path: Path) -> str:
    kind = mimetypes.guess_type(path.name)[0] or FALLBACK_TYPE
    return "data:" + kind + ";base64," + base64.b64encode(path.read_bytes()).decode()


def _video_url(body: Mapping[str, object]) -> str | None:
    video = body.get("video")
    found = video.get("url") if isinstance(video, dict) else None
    return found if isinstance(found, str) else None


class XaiVideo(DeferredProvider):
    kind: Final = "scene"
    id: Final = "xai-video"
    models: Final = ("grok-imagine-video", "grok-imagine-video-1.5", "grok-imagine-video-1.5-lite")
    secret: Final = "XAI_API_KEY"
    seedable: Final = False
    adapter_version: Final = 1
    output: Final = "video"

    def __init__(self, config: XaiVideoConfig | None = None) -> None:
        self.config: Final = XaiVideoConfig() if config is None else config
        self.patience = Patience(self.config.poll_seconds, self.config.wait_seconds)

    def faults(self, step: GeneratedScene) -> tuple[tuple[str, str], ...]:
        found: list[tuple[str, str]] = []
        whole = step.duration == int(step.duration)
        if not whole or not MIN_SECONDS <= step.duration <= MAX_SECONDS:
            found.append(("duration", "Use whole seconds from 1 to 15."))
        if step.resolution is not None and step.resolution not in RESOLUTIONS:
            found.append(("resolution", "Use 480p, 720p or 1080p."))
        if step.aspect_ratio is not None and step.aspect_ratio not in SHAPES:
            found.append(("aspect_ratio", "The provider has no such shape."))
        return tuple(found)

    def params(self, step: PaidClip) -> JsonObject:
        if not isinstance(step, GeneratedScene):
            raise TypeError(type(step).__name__)
        built: dict[str, str | int] = {"model": step.model, "prompt": step.prompt, "duration": int(step.duration)}
        if step.aspect_ratio is not None:
            built["aspect_ratio"] = step.aspect_ratio
        if step.resolution is not None:
            built["resolution"] = step.resolution
        return dict(built)

    def price(self, model: str) -> Price:
        return price_of(self.id, model)

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def start(self, params: JsonObject, inputs: Mapping[str, Path], exchange: Sender) -> str:
        sent = dict(params)
        if REFERENCE in inputs:
            sent[REFERENCE] = {"url": _data_url(inputs[REFERENCE])}
        answer = exchange(Request(self.config.submit_url, json.dumps(sent).encode(), self.config.timeout_seconds))
        if isinstance(answer, int):
            raise ProviderError(self.id, answer)
        body = as_object(answer)
        found = None if body is None else body.get("request_id")
        if not isinstance(found, str) or REQUEST_ID.fullmatch(found) is None:
            raise ProviderError(self.id, 0)
        return found

    def poll(self, request_id: str, exchange: Sender, out: Path) -> RunResult | None:
        if REQUEST_ID.fullmatch(request_id) is None:
            raise RequestLost(self.id, 0)
        answer = exchange(Request(self.config.status_url + request_id, None, self.config.timeout_seconds))
        if isinstance(answer, int) and answer in UNKNOWN:
            raise RequestLost(self.id, answer)
        if isinstance(answer, int):
            raise ProviderError(self.id, answer)
        body = as_object(answer)
        if body is None:
            raise ProviderError(self.id, 0)
        status = body.get("status")
        if status in GONE:
            raise RequestLost(self.id, 0)
        address = _video_url(body)
        if address is None and status == DONE:
            raise ProviderError(self.id, 0)
        if address is None:
            return None
        video = exchange.download(address, self.config.timeout_seconds, self.config.download_schemes)
        if isinstance(video, int) or video == b"":
            raise ProviderError(self.id, video if isinstance(video, int) else 0)
        out.write_bytes(video)
        return RunResult(out)
