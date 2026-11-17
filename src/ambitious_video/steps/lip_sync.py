from __future__ import annotations

from typing import TYPE_CHECKING

from ambitious_video.domain.types import LipSync, LocalMouth, MouthShape
from ambitious_video.steps.common import Canvas, open_image, pil, rgb_of
from ambitious_video.steps.generated_scene import stored_frame
from ambitious_video.steps.image import cover

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage

CLOSED = 0.08
DARK = (30, 16, 20)


def _shape(shape: MouthShape) -> dict[str, object]:
    return {
        "type": "mouth",
        "sprite": shape.sprite,
        "x": shape.x,
        "y": shape.y,
        "width": shape.width,
        "height": shape.height,
        "color": shape.color,
    }


def compile(clip: LipSync, _ctx: object) -> dict[str, object]:
    return {
        "type": "lip_sync",
        "duration": clip.duration,
        "video": clip.video,
        "image": clip.image,
        "voice": clip.voice,
        "fallbackClip": {"type": "none"} if clip.mouth is None else _shape(clip.mouth),
    }


def compile_mouth(clip: LocalMouth, _ctx: object) -> dict[str, object]:
    return {**_shape(clip.shape), "duration": clip.duration, "voice": clip.voice}


def frame(_clip: LipSync, local: int, _count: int, canvas: Canvas) -> PilImage:
    return stored_frame(local, canvas)


def mouth_frame(clip: LocalMouth, local: int, _count: int, canvas: Canvas) -> PilImage:
    _image, draw_module, _font = pil()
    shape = clip.shape
    shown = cover(open_image(canvas, shape.sprite), canvas.width, canvas.height, 1.0).copy()
    heard = canvas.levels.get(canvas.step, ())
    level = heard[local] if local < len(heard) else 0.0
    wide = max(2.0, shape.width * canvas.width)
    high = max(2.0, shape.height * canvas.height * max(level, CLOSED))
    middle = (shape.x * canvas.width, shape.y * canvas.height)
    box = (middle[0] - wide / 2, middle[1] - high / 2, middle[0] + wide / 2, middle[1] + high / 2)
    draw_module.Draw(shown).ellipse(box, fill=rgb_of(shape.color, DARK))
    return shown
