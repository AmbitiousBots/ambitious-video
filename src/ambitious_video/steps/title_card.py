from __future__ import annotations

from typing import TYPE_CHECKING

from ambitious_video.domain.types import TitleCard
from ambitious_video.steps.common import Canvas, font_for, measure, open_image, pil, wrap

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage


def compile(clip: TitleCard, _ctx: object) -> dict[str, object]:
    return {"type": "title_card", "duration": clip.duration, "text": clip.text}


def frame(clip: TitleCard, _local: int, _count: int, canvas: Canvas) -> PilImage:
    image_module, draw_module, _font = pil()
    image = image_module.new("RGB", (canvas.width, canvas.height), canvas.bg)
    draw = draw_module.Draw(image)
    size = max(18, canvas.height // 10)
    font = font_for(size)
    limit = canvas.width - canvas.width // 6
    while size > 18:
        font = font_for(size)
        lines = wrap(draw, clip.text, font, limit)
        if lines and max(measure(draw, line, font)[0] for line in lines) <= limit:
            break
        size -= 4
    lines = wrap(draw, clip.text, font, limit)
    subtitle_font = font_for(max(14, size // 2))
    subtitle = () if clip.subtitle is None else wrap(draw, clip.subtitle, subtitle_font, limit)
    line_h = measure(draw, "Ag", font)[1] + 8
    sub_h = measure(draw, "Ag", subtitle_font)[1] + 6 if subtitle else 0
    block = line_h * len(lines) + 24 + sub_h
    top = max(0, (canvas.height - block) // 2)
    for line in lines:
        wide, _high = measure(draw, line, font)
        draw.text(((canvas.width - wide) // 2, top), line, fill=canvas.fg, font=font)
        top += line_h
    bar = min(160, canvas.width // 8)
    draw.rectangle((canvas.width // 2 - bar // 2, top + 8, canvas.width // 2 + bar // 2, top + 16), fill=canvas.accent)
    top += 28
    for line in subtitle:
        wide, _high = measure(draw, line, subtitle_font)
        draw.text(((canvas.width - wide) // 2, top), line, fill=canvas.fg, font=subtitle_font)
        top += sub_h
    if canvas.logo is not None:
        logo = open_image(canvas, canvas.logo)
        fitted = logo.copy()
        fitted.thumbnail((canvas.width // 8, canvas.height // 8))
        image.paste(fitted, (canvas.width // 24, canvas.height // 24))
    return image
