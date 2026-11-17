from __future__ import annotations

import json
import urllib.error
import urllib.request

from ambitious_kit.domain.envelope import JsonValue

PROVIDER = "xai"
MODEL = "tts"
ADAPTER_VERSION = 1
KEY_ENV = "XAI_API_KEY"


class SpeechError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(str(status))
        self.status = status


def request_params(text: str, voice: str) -> dict[str, JsonValue]:
    voice_id = "eve" if voice == "default" else voice
    return {
        "text": text,
        "voice_id": voice_id,
        "language": "auto",
        "output_format": {"codec": "mp3"},
    }


def speak(text: str, voice: str, key: str) -> bytes:
    payload = json.dumps(request_params(text, voice)).encode()
    request = urllib.request.Request(
        "https://api.x.ai/v1/tts",
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
    )
    status: int | None = None
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            error.read()
        except Exception:
            pass
    except Exception:
        status = 0
    if status is None and isinstance(body, bytes):
        return body
    code = 0 if status is None else status
    del request
    del key
    raise SpeechError(code)
