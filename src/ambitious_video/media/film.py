from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ambitious_video.domain.clips import VISUAL
from ambitious_video.domain.need import NeedTool
from ambitious_video.domain.timeline import Mark, Span, Timeline
from ambitious_video.domain.types import (
    Brand,
    ImageClip,
    ScreenRecording,
    TitleCard,
    VideoClip,
    Voiceover,
)
from ambitious_video.media.probe import extract_frames
from ambitious_video.steps.common import Canvas, fit, rgb_of

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage
from ambitious_video.steps.image import frame as image_frame
from ambitious_video.steps.screen_recording import frame as screen_frame
from ambitious_video.steps.screen_recording import record
from ambitious_video.steps.title_card import frame as title_frame
from ambitious_video.steps.video import frame as video_frame


def canvas_for(timeline: Timeline, root: Path) -> Canvas:
    colors = None if timeline.brand is None else timeline.brand.colors
    bg = rgb_of(None if colors is None else str(colors.bg), (17, 17, 17))
    fg = rgb_of(None if colors is None else str(colors.fg), (255, 255, 255))
    accent = rgb_of(None if colors is None else str(colors.accent), (51, 102, 153))
    logo = None if timeline.brand is None or timeline.brand.logo is None else str(timeline.brand.logo)
    return Canvas(timeline.width, timeline.height, bg, fg, accent, root, logo, 0, {}, {})


def prepare(timeline: Timeline, canvas: Canvas, work: Path, root: Path) -> None:
    for span in timeline.spans:
        folder = work / f"step-{span.step}"
        if isinstance(span.clip, VideoClip):
            start = 0.0 if span.clip.start is None else span.clip.start
            extract_frames(str(root / span.clip.path), start, span.frames, timeline.fps, folder)
            canvas.folders["step:" + str(span.step)] = str(folder)
        elif isinstance(span.clip, ScreenRecording):
            record(span.clip, folder, timeline.width, timeline.height)
            canvas.folders["step:" + str(span.step)] = str(folder)


def _sample(span: Span, local: int, canvas: Canvas) -> PilImage:
    clamped = min(max(local, 0), max(span.frames - 1, 0))
    canvas.step = span.step
    if isinstance(span.clip, TitleCard):
        return title_frame(span.clip, clamped, span.frames, canvas)
    if isinstance(span.clip, ImageClip):
        return image_frame(span.clip, clamped, span.frames, canvas)
    if isinstance(span.clip, VideoClip):
        return video_frame(span.clip, clamped, span.frames, canvas)
    if isinstance(span.clip, ScreenRecording):
        return screen_frame(span.clip, clamped, span.frames, canvas)
    from ambitious_video.steps.common import pil

    image_module, _draw, _font = pil()
    return image_module.new("RGB", (canvas.width, canvas.height), canvas.bg)


def _covering(spans: tuple[Span, ...] | list[Span], index: int) -> Span | None:
    for span in spans:
        if span.start <= index < span.end:
            return span
    return None


def _blank(canvas: Canvas) -> PilImage:
    from ambitious_video.steps.common import pil

    image_module, _draw, _font = pil()
    return image_module.new("RGB", (canvas.width, canvas.height), canvas.bg)


def _blend(left: PilImage, right: PilImage, alpha: float) -> PilImage:
    if alpha <= 0:
        return left
    if alpha >= 1:
        return right
    from ambitious_video.steps.common import pil

    image_module, _draw, _font = pil()
    return image_module.blend(left, right, alpha)


def _cut_at(marks: tuple[Mark, ...], index: int) -> Mark | None:
    for mark in marks:
        start = mark.at - mark.half
        if start <= index < start + mark.frames:
            return mark
    return None


def _caption(timeline: Timeline, index: int) -> str | None:
    for span in timeline.spans:
        if isinstance(span.clip, Voiceover) and span.start <= index < span.end:
            return span.clip.text
    return None


def _draw_caption(image: PilImage, text: str, canvas: Canvas) -> PilImage:
    from ambitious_video.steps.common import font_for, measure, pil, wrap

    image_module, draw_module, _font = pil()
    frame = image.copy()
    draw = draw_module.Draw(frame)
    font = font_for(max(16, canvas.height // 24))
    lines = wrap(draw, text, font, canvas.width - canvas.width // 8)
    if not lines:
        return frame
    line_h = measure(draw, "Ag", font)[1] + 6
    bar_h = line_h * len(lines) + 24
    top = canvas.height - bar_h
    draw.rectangle((0, top, canvas.width, canvas.height), fill=canvas.bg)
    y = top + 12
    for line in lines:
        wide, _high = measure(draw, line, font)
        draw.text(((canvas.width - wide) // 2, y), line, fill=canvas.fg, font=font)
        y += line_h
    return frame


def frame_at(timeline: Timeline, index: int, canvas: Canvas) -> PilImage:
    visual = tuple(span for span in timeline.spans if span.track == timeline.visual_track and span.kind in VISUAL)
    mark = _cut_at(timeline.cuts, index)
    if mark is not None:
        left = _covering([span for span in visual if span.end == mark.at], mark.at - 1)
        right = _covering([span for span in visual if span.start == mark.at], mark.at)
        if left is not None and right is not None:
            alpha = (index - (mark.at - mark.half)) / mark.frames
            image = _blend(
                _sample(left, index - left.start, canvas),
                _sample(right, index - right.start, canvas),
                alpha,
            )
        else:
            image = _blank(canvas)
    else:
        span = _covering(visual, index)
        image = _blank(canvas) if span is None else _sample(span, index - span.start, canvas)
    seen = False
    tracks: list[str] = []
    for span in timeline.spans:
        if span.track not in tracks:
            tracks.append(span.track)
    for track in tracks:
        if track == timeline.visual_track:
            seen = True
            continue
        if not seen:
            continue
        overlay = _covering(
            tuple(span for span in timeline.spans if span.track == track and span.kind in VISUAL),
            index,
        )
        if overlay is not None:
            image = _sample(overlay, index - overlay.start, canvas)
    caption = _caption(timeline, index)
    if caption is not None:
        image = _draw_caption(image, caption, canvas)
    return image


def encode(timeline: Timeline, canvas: Canvas, audio: Path, dest: Path) -> None:
    from ambitious_video.steps.common import pil

    _image, _draw, _font = pil()
    command = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        str(timeline.width) + "x" + str(timeline.height),
        "-r",
        str(timeline.fps),
        "-i",
        "pipe:0",
        "-i",
        str(audio),
        "-frames:v",
        str(timeline.total),
        "-c:v",
        "libx264rgb",
        "-qp",
        "0",
        "-pix_fmt",
        "rgb24",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-threads",
        "1",
        "-fflags",
        "+bitexact",
        "-flags",
        "+bitexact",
        "-map_metadata",
        "-1",
        str(dest),
    ]
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as error:
        raise NeedTool("ffmpeg") from error
    if proc.stdin is None or proc.stderr is None:
        raise NeedTool("ffmpeg")
    try:
        for index in range(timeline.total):
            frame = fit(frame_at(timeline, index, canvas), canvas)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        proc.stdin.close()
    reported = proc.stderr.read().decode("utf-8", errors="replace")
    if proc.wait() != 0:
        sys.stderr.write(reported)
        raise NeedTool("ffmpeg") from None


def save_frame(timeline: Timeline, canvas: Canvas, index: int, dest: Path) -> None:
    frame_at(timeline, index, canvas).save(dest)


def pixels(path: Path) -> bytes:
    from ambitious_video.steps.common import pil

    image_module, _draw, _font = pil()
    return bytes(image_module.open(path).convert("RGB").tobytes())


def brand_of(brand: Brand | None) -> Brand | None:
    return brand
