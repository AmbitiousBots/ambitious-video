from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from ambitious_video.domain.types import Clip, ClipTrack, PaidClip, Script, Speech, Track, Voiceover

CAPTIONS = "captions"


@dataclass(frozen=True, slots=True)
class Fallback:
    name: str
    clip: Clip
    instead_of: PaidClip


def fallback_of(clip: PaidClip) -> Fallback:
    if isinstance(clip, Speech):
        return Fallback(CAPTIONS, Voiceover(clip.at, clip.text, clip.duration), clip)
    raise TypeError(type(clip).__name__)


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
