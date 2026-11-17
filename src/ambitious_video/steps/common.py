from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ambitious_video.domain.need import NeedTool

if TYPE_CHECKING:
    from PIL import Image as PilModule
    from PIL import ImageDraw as PilDrawModule
    from PIL import ImageFont as PilFontModule
    from PIL.Image import Image as PilImage
    from PIL.ImageDraw import ImageDraw as PilDraw
    from PIL.ImageFont import ImageFont as PilFont


def pil() -> tuple[PilModule, PilDrawModule, PilFontModule]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise NeedTool("pillow") from error
    return Image, ImageDraw, ImageFont


@dataclass
class Canvas:
    width: int
    height: int
    bg: tuple[int, int, int]
    fg: tuple[int, int, int]
    accent: tuple[int, int, int]
    root: Path
    logo: str | None
    step: int
    pictures: dict[str, PilImage]
    folders: dict[str, str]


def rgb_of(text: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if text is None or len(text) != 7 or not text.startswith("#"):
        return default
    return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))


def font_for(size: int) -> PilFont:
    _image, _draw, image_font = pil()
    return image_font.load_default(size=size)


def open_image(canvas: Canvas, path: str) -> PilImage:
    image_module, _draw, _font = pil()
    found = canvas.pictures.get(path)
    if found is not None:
        return found
    image = image_module.open(canvas.root / path).convert("RGB")
    canvas.pictures[path] = image
    return image


def measure(draw: PilDraw, text: str, font: PilFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0]), int(box[3] - box[1])


def wrap(draw: PilDraw, text: str, font: PilFont, width: int) -> tuple[str, ...]:
    words = text.split()
    if not words:
        return ()
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = current + " " + word
        wide, _high = measure(draw, trial, font)
        if wide <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return tuple(lines)


def open_logo(canvas: Canvas, path: str) -> PilImage:
    image_module, _draw, _font = pil()
    key = "logo:" + path
    found = canvas.pictures.get(key)
    if found is not None:
        return found
    image = image_module.open(canvas.root / path).convert("RGBA")
    canvas.pictures[key] = image
    return image


def fit(image: PilImage, canvas: Canvas) -> PilImage:
    image_module, _draw, _font = pil()
    size = (canvas.width, canvas.height)
    if image.size == size:
        return image
    scale = min(canvas.width / image.width, canvas.height / image.height)
    wide = min(canvas.width, max(1, int(round(image.width * scale))))
    high = min(canvas.height, max(1, int(round(image.height * scale))))
    resized = image.resize((wide, high), image_module.Resampling.LANCZOS)
    framed = image_module.new("RGB", size, canvas.bg)
    framed.paste(resized, ((canvas.width - wide) // 2, (canvas.height - high) // 2))
    return framed
