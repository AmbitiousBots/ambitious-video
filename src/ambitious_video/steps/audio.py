from __future__ import annotations

from ambitious_video.domain.types import AudioClip


def compile(clip: AudioClip, _ctx: object) -> dict[str, object]:
    return {
        "type": "audio",
        "path": clip.path,
        "at": clip.at,
        "gain_db": clip.gain_db,
        "duck_under": clip.duck_under,
    }
