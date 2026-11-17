from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.domain.money import Price
from ambitious_video.domain.types import GeneratedScene, PaidClip
from ambitious_video.providers.base import ProviderError, RunResult
from ambitious_video.providers.http import as_object, send
from ambitious_video.providers.pricing import price_of

RESOLUTIONS = ("1k", "2k")
ENCODED = "b64_json"


@dataclass(frozen=True, slots=True)
class XaiImageConfig:
    endpoint: str = "https://api.x.ai/v1/images/generations"
    timeout_seconds: float = 120


def _picture(body: Mapping[str, object] | None) -> bytes | None:
    listed = None if body is None else body.get("data")
    first = listed[0] if isinstance(listed, list) and listed else None
    encoded = first.get(ENCODED) if isinstance(first, dict) else None
    if not isinstance(encoded, str) or encoded == "":
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None


class XaiImage:
    kind: Final = "scene"
    id: Final = "xai-image"
    models: Final = ("grok-imagine-image", "grok-imagine-image-2.0")
    secret: Final = "XAI_API_KEY"
    seedable: Final = False
    adapter_version: Final = 1
    output: Final = "image"

    def __init__(self, config: XaiImageConfig | None = None) -> None:
        self.config: Final = XaiImageConfig() if config is None else config

    def faults(self, step: GeneratedScene) -> tuple[tuple[str, str], ...]:
        found: list[tuple[str, str]] = []
        if step.resolution is not None and step.resolution not in RESOLUTIONS:
            found.append(("resolution", "Use 1k or 2k."))
        if step.image is not None:
            found.append(("image", "This provider takes no image."))
        return tuple(found)

    def params(self, step: PaidClip) -> JsonObject:
        if not isinstance(step, GeneratedScene):
            raise TypeError(type(step).__name__)
        built: dict[str, str | int] = {"model": step.model, "prompt": step.prompt, "n": 1, "response_format": ENCODED}
        if step.aspect_ratio is not None:
            built["aspect_ratio"] = step.aspect_ratio
        if step.resolution is not None:
            built["resolution"] = step.resolution
        return dict(built)

    def price(self, model: str) -> Price:
        return price_of(self.id, model)

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult:
        answer = send(self.config.endpoint, json.dumps(dict(params)).encode(), secret, self.config.timeout_seconds)
        del secret
        if isinstance(answer, int):
            raise ProviderError(self.id, answer)
        picture = _picture(as_object(answer))
        if picture is None:
            raise ProviderError(self.id, 0)
        out.write_bytes(picture)
        return RunResult(out)
