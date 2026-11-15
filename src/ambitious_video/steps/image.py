from __future__ import annotations

from typing import TYPE_CHECKING

from ambitious_video.domain.types import ImageClip
from ambitious_video.steps.common import Canvas, open_image, pil

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage


def compile(clip: ImageClip, _ctx: object) -> dict[str, object]:
    zoom = None if clip.kenburns is None else {"from": clip.kenburns.zoom_from, "to": clip.kenburns.zoom_to}
    return {"type": "image", "path": clip.path, "duration": clip.duration, "kenburns": zoom}


def _cover(image: PilImage, width: int, height: int, zoom: float) -> PilImage:
    image_module, _draw, _font = pil()
    scale = max(width / image.width, height / image.height) * zoom
    resized_w = max(width, int(round(image.width * scale)))
    resized_h = max(height, int(round(image.height * scale)))
    resized = image.resize((resized_w, resized_h), image_module.Resampling.LANCZOS)
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def frame(clip: ImageClip, local: int, count: int, canvas: Canvas) -> PilImage:
    source = open_image(canvas, clip.path)
    start = 1.0 if clip.kenburns is None else clip.kenburns.zoom_from
    end = start if clip.kenburns is None else clip.kenburns.zoom_to
    if count <= 1:
        zoom = start
    else:
        zoom = start + (end - start) * (local / (count - 1))
    return _cover(source, canvas.width, canvas.height, zoom)
