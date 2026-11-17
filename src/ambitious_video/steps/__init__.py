from __future__ import annotations

from ambitious_video.domain.clips import kind_of
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
from ambitious_video.steps import (
    audio,
    generated_scene,
    image,
    lip_sync,
    screen_recording,
    speech,
    title_card,
    video,
    voiceover,
)
from ambitious_video.steps.common import Canvas

COMPILE = {
    "title_card": title_card.compile,
    "screen_recording": screen_recording.compile,
    "video": video.compile,
    "image": image.compile,
    "voiceover": voiceover.compile,
    "speech": speech.compile,
    "audio": audio.compile,
    "generated_scene": generated_scene.compile,
    "lip_sync": lip_sync.compile,
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
    if isinstance(clip, GeneratedScene):
        return generated_scene.compile(clip, None)
    if isinstance(clip, LipSync):
        return lip_sync.compile(clip, None)
    if isinstance(clip, LocalMouth):
        return lip_sync.compile_mouth(clip, None)
    raise TypeError(kind_of(clip))


def visual_frame(
    clip: TitleCard | ImageClip | VideoClip | ScreenRecording | GeneratedScene | LipSync | LocalMouth,
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
    if isinstance(clip, GeneratedScene):
        return generated_scene.frame(clip, local, count, canvas)
    if isinstance(clip, LipSync):
        return lip_sync.frame(clip, local, count, canvas)
    if isinstance(clip, LocalMouth):
        return lip_sync.mouth_frame(clip, local, count, canvas)
    return screen_recording.frame(clip, local, count, canvas)


__all__ = ["COMPILE", "compile_clip", "visual_frame", "AudioClip", "Voiceover"]
