from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.domain.money import Price
from ambitious_video.domain.types import PaidClip, Speech
from ambitious_video.providers.base import ProviderError, RunResult
from ambitious_video.providers.pricing import price_of


@dataclass(frozen=True, slots=True)
class XaiSpeechConfig:
    endpoint: str = "https://api.x.ai/v1/tts"
    language: str = "auto"
    codec: str = "mp3"
    timeout_seconds: int = 120


def _post(config: XaiSpeechConfig, payload: bytes, secret: str) -> bytes | int:
    request = urllib.request.Request(
        config.endpoint,
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + secret,
            "Content-Type": "application/json",
        },
    )
    del secret
    status = 0
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
            body = response.read()
        if isinstance(body, bytes):
            return body
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            error.read()
        except Exception:
            pass
    except Exception:
        status = 0
    return status


class XaiSpeech:
    kind: Final = "speech"
    id: Final = "xai-speech"
    models: Final = ("tts",)
    secret: Final = "XAI_API_KEY"
    seedable: Final = False
    adapter_version: Final = 2

    def __init__(self, config: XaiSpeechConfig | None = None) -> None:
        self.config: Final = XaiSpeechConfig() if config is None else config

    def params(self, step: PaidClip) -> JsonObject:
        if not isinstance(step, Speech):
            raise TypeError(type(step).__name__)
        return {
            "model": step.model,
            "text": step.text,
            "voice_id": step.voice,
            "language": self.config.language,
            "output_format": {"codec": self.config.codec},
        }

    def price(self, model: str) -> Price:
        return price_of(self.id, model)

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult:
        sent = {name: value for name, value in params.items() if name != "model"}
        answer = _post(self.config, json.dumps(sent).encode(), secret)
        del secret
        if isinstance(answer, int):
            raise ProviderError(self.id, answer)
        out.write_bytes(answer)
        return RunResult(out)
