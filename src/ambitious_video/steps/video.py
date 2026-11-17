from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ambitious_video.domain.types import VideoClip
from ambitious_video.steps.common import Canvas, fit, pil

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage


def compile(clip: VideoClip, _ctx: object) -> dict[str, object]:
    return {"type": "video", "path": clip.path, "in": clip.start, "out": clip.end}


def frame(clip: VideoClip, local: int, _count: int, canvas: Canvas) -> PilImage:
    image_module, _draw, _font = pil()
    folder = canvas.folders.get("step:" + str(canvas.step))
    path = Path(folder) / f"f{local:04d}.png" if folder is not None else Path()
    if not path.is_file():
        image = image_module.new("RGB", (canvas.width, canvas.height), canvas.bg)
        return image
    return fit(image_module.open(path).convert("RGB"), canvas)


def say_key(path: str) -> str:
    return "video:" + path
