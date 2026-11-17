from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from ambitious_video.domain.clips import kind_of
from ambitious_video.domain.types import (
    Clip,
    ClipTrack,
    ImageClip,
    LipSync,
    LocalMouth,
    PaidClip,
    Script,
    Speech,
    Track,
    VideoClip,
    Voiceover,
)

CAPTIONS = "captions"
NONE = "none"
MOUTH = "mouth"


@dataclass(frozen=True, slots=True)
class Fallback:
    name: str
    clip: Clip
    instead_of: PaidClip


def fallback_of(clip: PaidClip) -> Fallback:
    if isinstance(clip, Speech):
        return Fallback(CAPTIONS, Voiceover(clip.at, clip.text, clip.duration), clip)
    if isinstance(clip, LipSync):
        if clip.mouth is not None:
            return Fallback(MOUTH, LocalMouth(clip.duration, clip.voice, clip.mouth), clip)
        if clip.video is not None:
            return Fallback(NONE, VideoClip(clip.video, 0.0, clip.duration), clip)
        if clip.image is not None:
            return Fallback(NONE, ImageClip(clip.image, clip.duration, None), clip)
        raise TypeError(kind_of(clip))
    return Fallback(kind_of(clip.fallback), clip.fallback, clip)


def with_fallbacks(
    script: Script, declined: Callable[[PaidClip], bool]
) -> tuple[Script, Mapping[tuple[str, int], Fallback]]:
    taken: dict[tuple[str, int], Fallback] = {}
    tracks: list[Track] = []
    for track in script.tracks:
        if not isinstance(track, ClipTrack):
            tracks.append(track)
            continue
        clips: list[Clip] = []
        for index, clip in enumerate(track.clips):
            if isinstance(clip, PaidClip) and declined(clip):
                found = fallback_of(clip)
                taken[(track.id, index)] = found
                clips.append(found.clip)
            else:
                clips.append(clip)
        tracks.append(replace(track, clips=tuple(clips)))
    return replace(script, tracks=tuple(tracks)), taken
