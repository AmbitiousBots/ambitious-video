from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.domain.money import Price
from ambitious_video.domain.types import LipSync, PaidClip
from ambitious_video.providers.base import Patience, ProviderError, RequestLost, RunResult
from ambitious_video.providers.http import DeferredProvider, KeyHeader, Request, Sender, as_object
from ambitious_video.providers.pricing import price_of

REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
SUFFIX = re.compile(r"^\.[a-z0-9]{1,8}$")
KEY_HEADER = KeyHeader("x-api-key", "")
PICTURE_MODELS = frozenset({"sync-3"})
GONE = frozenset({"FAILED", "REJECTED"})
UNKNOWN = frozenset({404, 410})
DONE = "COMPLETED"
VIDEO = "video"
IMAGE = "image"
AUDIO = "audio"
SYNC_MODE = "cut_off"
ANY_TYPE = "application/octet-stream"
KINDS = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
}
FORM_TYPE = "multipart/form-data; boundary="
PART = "Content-Disposition: form-data; name="
BREAK = b"\r\n"


@dataclass(frozen=True, slots=True)
class SyncLipsyncConfig:
    submit_url: str = "https://api.sync.so/v2/generate"
    status_url: str = "https://api.sync.so/v2/generate/"
    download_schemes: tuple[str, ...] = ("https",)
    timeout_seconds: float = 300
    poll_seconds: float = 5
    wait_seconds: float = 600


def _part(name: str, body: bytes, file_name: str | None = None) -> bytes:
    head = PART + json.dumps(name)
    if file_name is not None:
        kind = KINDS.get(Path(file_name).suffix, ANY_TYPE)
        head += "; filename=" + json.dumps(file_name) + "\r\nContent-Type: " + kind
    return head.encode() + BREAK + BREAK + body + BREAK


def _file_name(field: str, path: Path) -> str:
    suffix = path.suffix.lower()
    return field + suffix if SUFFIX.fullmatch(suffix) is not None else field


def form_of(fields: Mapping[str, str], files: Mapping[str, Path]) -> tuple[bytes, str]:
    parts = [_part(name, fields[name].encode()) for name in sorted(fields)]
    parts += [_part(name, files[name].read_bytes(), _file_name(name, files[name])) for name in sorted(files)]
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part)
    boundary = "form-" + digest.hexdigest()[:40]
    line = b"--" + boundary.encode()
    return b"".join(line + BREAK + part for part in parts) + line + b"--" + BREAK, FORM_TYPE + boundary


def _output_url(body: Mapping[str, object]) -> str | None:
    found = body.get("outputUrl")
    return found if isinstance(found, str) else None


class SyncLipsync(DeferredProvider):
    kind: Final = "lipsync"
    id: Final = "sync-lipsync"
    models: Final = ("lipsync-2", "lipsync-2-pro", "sync-3")
    secret: Final = "SYNC_API_KEY"
    seedable: Final = False
    adapter_version: Final = 1

    def __init__(self, config: SyncLipsyncConfig | None = None) -> None:
        self.config: Final = SyncLipsyncConfig() if config is None else config
        self.patience = Patience(self.config.poll_seconds, self.config.wait_seconds)

    def lip_faults(self, step: LipSync) -> tuple[tuple[str, str], ...]:
        if step.image is not None and step.model not in PICTURE_MODELS:
            return ((IMAGE, "Only sync-3 takes a picture."),)
        return ()

    def params(self, step: PaidClip) -> JsonObject:
        if not isinstance(step, LipSync):
            raise TypeError(type(step).__name__)
        if step.image is not None:
            return {"model": step.model, "presenter": IMAGE}
        return {"model": step.model, "presenter": VIDEO, "options": {"sync_mode": SYNC_MODE}}

    def price(self, model: str) -> Price:
        return price_of(self.id, model)

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def start(self, params: JsonObject, inputs: Mapping[str, Path], exchange: Sender) -> str:
        shown = str(params["presenter"])
        fields = {"model": str(params["model"])}
        if "options" in params:
            fields["options"] = json.dumps(params["options"], sort_keys=True)
        try:
            body, kind = form_of(fields, {shown: inputs[shown], AUDIO: inputs[AUDIO]})
        except (KeyError, OSError):
            raise ProviderError(self.id, 0) from None
        answer = exchange(Request(self.config.submit_url, body, self.config.timeout_seconds, KEY_HEADER, kind))
        if isinstance(answer, int):
            raise ProviderError(self.id, answer)
        made = as_object(answer)
        found = None if made is None else made.get("id")
        if not isinstance(found, str) or REQUEST_ID.fullmatch(found) is None:
            raise ProviderError(self.id, 0)
        return found

    def poll(self, request_id: str, exchange: Sender, out: Path) -> RunResult | None:
        if REQUEST_ID.fullmatch(request_id) is None:
            raise RequestLost(self.id, 0)
        answer = exchange(Request(self.config.status_url + request_id, None, self.config.timeout_seconds, KEY_HEADER))
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
        if status != DONE:
            return None
        address = _output_url(body)
        if address is None:
            raise ProviderError(self.id, 0)
        video = exchange.download(address, self.config.timeout_seconds, self.config.download_schemes)
        if isinstance(video, int) or video == b"":
            raise ProviderError(self.id, video if isinstance(video, int) else 0)
        out.write_bytes(video)
        return RunResult(out)
