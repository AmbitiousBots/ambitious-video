from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final
from urllib.parse import quote, urlencode

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.domain.money import Price
from ambitious_video.domain.types import PaidClip, Speech
from ambitious_video.providers.base import ProviderError, RunResult
from ambitious_video.providers.http import KeyHeader, MediaProvider, Request, Sender
from ambitious_video.providers.pricing import price_of

HOST = "https://api.elevenlabs.io"
KEY_HEADER = KeyHeader("xi-api-key", "")
MODEL_IDS: Final[Mapping[str, str]] = {
    "eleven-v4": "eleven_v4",
    "eleven-flash": "eleven_flash_v2_5",
}


@dataclass(frozen=True, slots=True)
class ElevenLabsSpeechConfig:
    endpoint: str = HOST + "/v1/text-to-speech"
    output_format: str = "mp3_44100_128"
    timeout_seconds: float = 120


class ElevenLabsSpeech(MediaProvider):
    kind: Final = "speech"
    id: Final = "elevenlabs-speech"
    models: Final = tuple(MODEL_IDS)
    secret: Final = "ELEVENLABS_API_KEY"
    seedable: Final = False
    adapter_version: Final = 1

    def __init__(self, config: ElevenLabsSpeechConfig | None = None) -> None:
        self.config: Final = ElevenLabsSpeechConfig() if config is None else config

    def params(self, step: PaidClip) -> JsonObject:
        if not isinstance(step, Speech):
            raise TypeError(type(step).__name__)
        return {
            "model": step.model,
            "model_id": MODEL_IDS.get(step.model, step.model),
            "text": step.text,
            "voice_id": step.voice,
            "output_format": self.config.output_format,
        }

    def price(self, model: str) -> Price:
        return price_of(self.id, model)

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def render(self, params: JsonObject, inputs: Mapping[str, Path], exchange: Sender, out: Path) -> RunResult:
        voice = quote(str(params["voice_id"]), safe="")
        chosen = urlencode({"output_format": str(params["output_format"])})
        url = self.config.endpoint + "/" + voice + "?" + chosen
        sent = {"text": params["text"], "model_id": params["model_id"]}
        answer = exchange(Request(url, json.dumps(sent).encode(), self.config.timeout_seconds, KEY_HEADER))
        if isinstance(answer, int):
            raise ProviderError(self.id, answer)
        out.write_bytes(answer)
        return RunResult(out)
