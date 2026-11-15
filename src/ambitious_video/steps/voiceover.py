from __future__ import annotations

from ambitious_video.domain.types import Voiceover


def compile(clip: Voiceover, _ctx: object) -> dict[str, object]:
    return {"type": "voiceover", "at": clip.at, "text": clip.text, "duration": clip.duration}
