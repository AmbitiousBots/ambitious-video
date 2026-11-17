from __future__ import annotations

from ambitious_video.domain.types import Speech


def compile(clip: Speech, _ctx: object) -> dict[str, object]:
    return {
        "type": "speech",
        "at": clip.at,
        "text": clip.text,
        "duration": clip.duration,
        "provider": clip.provider,
        "model": clip.model,
        "voice": clip.voice,
    }
