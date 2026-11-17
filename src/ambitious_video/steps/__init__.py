from __future__ import annotations

from ambitious_video.domain.clips import kind_of
from ambitious_video.domain.types import (
    AudioClip,
    Clip,
    ImageClip,
    ScreenRecording,
    Speech,
    TitleCard,
    VideoClip,
    Voiceover,
)
from ambitious_video.steps import audio, image, screen_recording, speech, title_card, video, voiceover
from ambitious_video.steps.common import Canvas

COMPILE = {
    "title_card": title_card.compile,
    "screen_recording": screen_recording.compile,
    "video": video.compile,
    "image": image.compile,
    "voiceover": voiceover.compile,
    "speech": speech.compile,
    "audio": audio.compile,
}


def compile_clip(clip: Clip) -> dict[str, object]:
    if isinstance(clip, TitleCard):
        return title_card.compile(clip, None)
    if isinstance(clip, ScreenRecording):
        return screen_recording.compile(clip, None)
    if isinstance(clip, VideoClip):
        return video.compile(clip, None)
    if isinstance(clip, ImageClip):
        return image.compile(clip, None)
    if isinstance(clip, Voiceover):
        return voiceover.compile(clip, None)
    if isinstance(clip, Speech):
        return speech.compile(clip, None)
    if isinstance(clip, AudioClip):
        return audio.compile(clip, None)
    raise TypeError(kind_of(clip))


def visual_frame(
    clip: TitleCard | ImageClip | VideoClip | ScreenRecording,
    local: int,
    count: int,
    canvas: Canvas,
) -> object:
    if isinstance(clip, TitleCard):
        return title_card.frame(clip, local, count, canvas)
    if isinstance(clip, ImageClip):
        return image.frame(clip, local, count, canvas)
    if isinstance(clip, VideoClip):
        return video.frame(clip, local, count, canvas)
    return screen_recording.frame(clip, local, count, canvas)


__all__ = ["COMPILE", "compile_clip", "visual_frame", "AudioClip", "Voiceover"]
