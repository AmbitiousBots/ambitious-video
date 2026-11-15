from __future__ import annotations

from ambitious_video.domain.types import (
    AudioClip,
    Clip,
    ImageClip,
    ScreenRecording,
    TitleCard,
    VideoClip,
    Voiceover,
)

VISUAL = frozenset({"title_card", "screen_recording", "video", "image"})


def kind_of(clip: Clip) -> str:
    if isinstance(clip, TitleCard):
        return "title_card"
    if isinstance(clip, ScreenRecording):
        return "screen_recording"
    if isinstance(clip, VideoClip):
        return "video"
    if isinstance(clip, ImageClip):
        return "image"
    if isinstance(clip, Voiceover):
        return "voiceover"
    if isinstance(clip, AudioClip):
        return "audio"
    raise TypeError(type(clip).__name__)


def placed_at(clip: Clip) -> float | None:
    if isinstance(clip, Voiceover | AudioClip):
        return clip.at
    return None
