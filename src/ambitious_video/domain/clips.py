from __future__ import annotations

from ambitious_video.domain.types import (
    AudioClip,
    Clip,
    GeneratedScene,
    ImageClip,
    LipSync,
    LocalMouth,
    ScreenRecording,
    Speech,
    TitleCard,
    VideoClip,
    Voiceover,
)

VISUAL = frozenset({"title_card", "screen_recording", "video", "image", "generated_scene", "lip_sync", "mouth"})


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
    if isinstance(clip, Speech):
        return "speech"
    if isinstance(clip, AudioClip):
        return "audio"
    if isinstance(clip, GeneratedScene):
        return "generated_scene"
    if isinstance(clip, LipSync):
        return "lip_sync"
    if isinstance(clip, LocalMouth):
        return "mouth"
    raise TypeError(type(clip).__name__)


def placed_at(clip: Clip) -> float | None:
    if isinstance(clip, Voiceover | Speech | AudioClip):
        return clip.at
    return None
