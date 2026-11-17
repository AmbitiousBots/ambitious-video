from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ambitious_video.domain.captions import Caption, segments
from ambitious_video.domain.clips import VISUAL, kind_of, placed_at
from ambitious_video.domain.load import say
from ambitious_video.domain.types import (
    AudioClip,
    Brand,
    CaptionsTrack,
    Clip,
    ClipTrack,
    GeneratedScene,
    ImageClip,
    LipSync,
    LocalMouth,
    RelativePath,
    Script,
    Speech,
    VideoClip,
    Voiceover,
)

CHARACTERS_PER_SECOND = 18
MAX_SECONDS = 1800
SILENT = "No sound on that track is in this clip."


def frames_of(seconds: float, fps: int, *, minimum: int) -> int:
    counted = int(round(seconds * fps))
    return counted if counted >= minimum else minimum


def speech_frames(text: str, fps: int) -> int:
    counted = int(round(len(text) * fps / CHARACTERS_PER_SECOND))
    return counted if counted >= 1 else 1


@dataclass(frozen=True, slots=True)
class Fault:
    code: str
    at: str
    message: str


@dataclass(frozen=True, slots=True)
class Span:
    track: str
    index: int
    step: int
    clip: Clip
    start: int
    frames: int
    kind: str

    @property
    def end(self) -> int:
        return self.start + self.frames


@dataclass(frozen=True, slots=True)
class Mark:
    at: int
    frames: int

    @property
    def half(self) -> int:
        return self.frames // 2


@dataclass(frozen=True, slots=True)
class Timeline:
    spans: tuple[Span, ...]
    cuts: tuple[Mark, ...]
    total: int
    fps: int
    width: int
    height: int
    visual_track: str | None
    brand: Brand | None
    faults: tuple[Fault, ...]
    captions: tuple[Caption, ...]
    disclosure: str | None = None


def _asset_paths(script: Script) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    if script.brand is not None and script.brand.logo is not None:
        found.append((script.brand.logo, "image"))
    for track in script.tracks:
        if not isinstance(track, ClipTrack):
            continue
        for clip in track.clips:
            if isinstance(clip, VideoClip):
                found.append((clip.path, "video"))
            elif isinstance(clip, ImageClip):
                found.append((clip.path, "image"))
            elif isinstance(clip, AudioClip):
                found.append((clip.path, "audio"))
            elif isinstance(clip, GeneratedScene):
                if clip.image is not None:
                    found.append((clip.image, "image"))
                if isinstance(clip.fallback, VideoClip):
                    found.append((clip.fallback.path, "video"))
                elif isinstance(clip.fallback, ImageClip):
                    found.append((clip.fallback.path, "image"))
            elif isinstance(clip, LipSync):
                if clip.video is not None:
                    found.append((clip.video, "video"))
                if clip.image is not None:
                    found.append((clip.image, "image"))
                if clip.mouth is not None:
                    found.append((clip.mouth.sprite, "image"))
            elif isinstance(clip, LocalMouth):
                found.append((clip.shape.sprite, "image"))
    return tuple(found)


def asset_paths(script: Script) -> tuple[tuple[str, str], ...]:
    return _asset_paths(script)


def _length(
    clip: Clip,
    fps: int,
    media: Mapping[str, float | None] | None,
    require: bool,
    at: str,
    faults: list[Fault],
) -> int | None:
    if isinstance(clip, Voiceover | Speech):
        if clip.duration is not None:
            return frames_of(clip.duration, fps, minimum=1)
        return speech_frames(clip.text, fps)
    if isinstance(clip, VideoClip):
        start = 0.0 if clip.start is None else clip.start
        end = clip.end
        known = media.get(clip.path) if media is not None else None
        if end is None:
            if known is None:
                faults.append(Fault("E_MEDIA", at, "The video length is not known."))
                return None
            end = known
        if require:
            if known is None:
                faults.append(Fault("E_MEDIA", at, "The video file is missing."))
                return None
            if start > known or end > known + 1e-6:
                faults.append(Fault("E_MEDIA", at, "in or out is outside the file."))
                return None
        if end <= start:
            faults.append(Fault("E_MEDIA", at, "out must be later than in."))
            return None
        return frames_of(end - start, fps, minimum=1)
    if isinstance(clip, AudioClip):
        known = media.get(clip.path) if media is not None else None
        if require and known is None:
            faults.append(Fault("E_MEDIA", at, "The audio file is missing."))
            return None
        if known is None:
            return 0
        return frames_of(known, fps, minimum=1)
    if isinstance(clip, ImageClip) and require:
        known = media.get(clip.path) if media is not None else None
        if known is None:
            faults.append(Fault("E_MEDIA", at, "The image file is missing."))
            return None
    if isinstance(clip, GeneratedScene) and require and clip.image is not None:
        known = media.get(clip.image) if media is not None else None
        if known is None:
            faults.append(Fault("E_MEDIA", say(at, "/image"), "The image file is missing."))
            return None
    if isinstance(clip, LipSync | LocalMouth) and require and not _presenter_ok(clip, media, at, faults):
        return None
    return frames_of(clip.duration, fps, minimum=1)


def _presenter_ok(
    clip: LipSync | LocalMouth, media: Mapping[str, float | None] | None, at: str, faults: list[Fault]
) -> bool:
    known = {} if media is None else media
    before = len(faults)
    shape = clip.shape if isinstance(clip, LocalMouth) else clip.mouth
    if shape is not None and known.get(shape.sprite) is None:
        faults.append(Fault("E_MEDIA", say(at, "/fallback/sprite"), "The image file is missing."))
    if isinstance(clip, LipSync) and clip.image is not None and known.get(clip.image) is None:
        faults.append(Fault("E_MEDIA", say(at, "/image"), "The image file is missing."))
    if isinstance(clip, LipSync) and clip.video is not None:
        length = known.get(clip.video)
        if length is None:
            faults.append(Fault("E_MEDIA", say(at, "/video"), "The video file is missing."))
        elif length + 1e-6 < clip.duration:
            faults.append(Fault("E_MEDIA", say(at, "/video"), "The video is shorter than the clip."))
    return len(faults) == before


def heard(spans: tuple[Span, ...], span: Span) -> tuple[Span, ...]:
    if not isinstance(span.clip, LipSync | LocalMouth):
        return ()
    track = span.clip.voice
    found = [
        item
        for item in spans
        if item.track == track
        and isinstance(item.clip, Speech | AudioClip)
        and item.start < span.end
        and (item.start >= span.start or item.end > span.start)
    ]
    return tuple(sorted(found, key=lambda item: (item.start, item.index)))


def _place(script: Script, media: Mapping[str, float | None] | None, *, require: bool) -> Timeline:
    faults: list[Fault] = []
    if require and script.brand is not None and script.brand.logo is not None:
        known = None if media is None else media.get(script.brand.logo)
        if known is None:
            faults.append(Fault("E_MEDIA", "/brand/logo", "The logo file is missing."))
    spans: list[Span] = []
    synced: list[tuple[Span, str]] = []
    step = 0
    visual_track: str | None = None
    for track_index, track in enumerate(script.tracks):
        if not isinstance(track, ClipTrack):
            continue
        cursor = 0
        placed: list[Span] = []
        for clip_index, clip in enumerate(track.clips):
            at = say("/tracks/", str(track_index), "/clips/", str(clip_index))
            count = _length(clip, script.output.fps, media, require, at, faults)
            anchor = placed_at(clip)
            if count is None:
                step += 1
                continue
            start = frames_of(anchor, script.output.fps, minimum=0) if anchor is not None else cursor
            if anchor is None:
                cursor = start + count
            span = Span(track.id, clip_index, step, clip, start, count, kind_of(clip))
            placed.append(span)
            spans.append(span)
            if isinstance(clip, LipSync):
                synced.append((span, say(at, "/voice")))
            if visual_track is None and span.kind in VISUAL:
                visual_track = track.id
            step += 1
        ordered = sorted(placed, key=lambda item: (item.start, item.index))
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.start < earlier.end:
                faults.append(
                    Fault(
                        "E_OVERLAP",
                        say("/tracks/", str(track_index)),
                        "Clips on a track overlap.",
                    )
                )
                break
    for span, at in synced:
        if not heard(tuple(spans), span):
            faults.append(Fault("E_INPUT_SCHEMA", at, SILENT))
    total = max((span.end for span in spans), default=0)
    if total == 0 and not faults:
        faults.append(Fault("E_RANGE", "/tracks", "The video has no duration."))
    if total > script.output.fps * MAX_SECONDS:
        faults.append(Fault("E_RANGE", "/tracks", "The video is longer than 1800 seconds."))
    cuts = _cuts(script, spans, visual_track, total, faults)
    return Timeline(
        tuple(spans),
        tuple(cuts),
        total,
        script.output.fps,
        script.output.width,
        script.output.height,
        visual_track,
        script.brand,
        tuple(faults),
        _captions(script, spans),
        script.disclosure if any(isinstance(span.clip, GeneratedScene | LipSync) for span in spans) else None,
    )


def _captions(script: Script, spans: list[Span]) -> tuple[Caption, ...]:
    found: list[Caption] = []
    for track in script.tracks:
        if not isinstance(track, CaptionsTrack):
            continue
        spoken = [span for span in spans if span.track == track.source and isinstance(span.clip, Voiceover | Speech)]
        for span in sorted(spoken, key=lambda item: (item.start, item.index)):
            if isinstance(span.clip, Voiceover | Speech):
                found.extend(segments(track.id, span.clip.text, span.start, span.frames))
    return tuple(found)


def _cuts(
    script: Script,
    spans: list[Span],
    visual_track: str | None,
    total: int,
    faults: list[Fault],
) -> list[Mark]:
    visual = [span for span in spans if span.track == visual_track and span.kind in VISUAL]
    endings = {span.end: span for span in visual}
    starts = {span.start: span for span in visual}
    marks: list[Mark] = []
    for index, cut in enumerate(script.cuts):
        at = say("/cuts/", str(index))
        boundary = frames_of(cut.at, script.output.fps, minimum=0)
        width = frames_of(cut.duration, script.output.fps, minimum=1)
        outgoing = endings.get(boundary)
        incoming = starts.get(boundary)
        if outgoing is None or incoming is None or boundary == 0 or boundary == total:
            faults.append(Fault("E_RANGE", at, "A cut must sit on a clip boundary."))
            continue
        if outgoing.frames < width // 2 or incoming.frames < width - width // 2:
            faults.append(Fault("E_RANGE", at, "The cut is longer than a clip."))
            continue
        if boundary - width // 2 < 0 or boundary + (width - width // 2) > total:
            faults.append(Fault("E_RANGE", at, "The cut runs off the video."))
            continue
        marks.append(Mark(boundary, width))
    marks.sort(key=lambda mark: mark.at)
    for earlier, later in zip(marks, marks[1:], strict=False):
        if later.at - later.half < earlier.at - earlier.half + earlier.frames:
            faults.append(Fault("E_RANGE", "/cuts", "Two cuts overlap."))
            break
    return marks


def timeline_for(
    script: Script, media: Mapping[str, float | None] | None, *, require_files: bool
) -> Timeline:
    return _place(script, media, require=require_files)


def seconds_json(frames: int, fps: int) -> int | float:
    if fps <= 0:
        return 0
    if frames % fps == 0:
        return frames // fps
    return round(frames / fps, 3)


def graph_of(timeline: Timeline) -> str:
    lines = [
        say("size=", str(timeline.width), "x", str(timeline.height)),
        say("fps=", str(timeline.fps)),
    ]
    for span in timeline.spans:
        lines.append(
            say(
                "[",
                str(span.step),
                ":v] ",
                span.kind,
                " start=",
                str(span.start),
                " frames=",
                str(span.frames),
            )
        )
    for mark in timeline.cuts:
        lines.append(say("xfade at=", str(mark.at), " frames=", str(mark.frames)))
    lines.append(say("concat frames=", str(timeline.total)))
    return "\n".join(lines)


def output_name(path: RelativePath) -> str:
    return path
