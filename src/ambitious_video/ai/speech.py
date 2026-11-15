from __future__ import annotations

import json
import urllib.error
import urllib.request


class SpeechError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(str(status))
        self.status = status


def speak(text: str, voice: str, key: str) -> bytes:
    voice_id = "eve" if voice == "default" else voice
    payload = json.dumps(
        {
            "text": text,
            "voice_id": voice_id,
            "language": "auto",
            "output_format": {"codec": "mp3"},
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.x.ai/v1/tts",
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read()
        if key.encode() in detail:
            detail = b""
        raise SpeechError(error.code) from None
    if not isinstance(body, bytes):
        raise SpeechError(0)
    return body
