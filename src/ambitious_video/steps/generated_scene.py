from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ambitious_video.domain.types import GeneratedScene, ImageClip, RelativePath, TitleCard, VideoClip
from ambitious_video.steps import image, title_card, video
from ambitious_video.steps.common import Canvas, fit, pil

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage

STILL = "still:"
FRAMES = "step:"
COUNT = "count:"


def _fallback(clip: GeneratedScene) -> dict[str, object]:
    spare = clip.fallback
    if isinstance(spare, TitleCard):
        return title_card.compile(spare, None)
    if isinstance(spare, ImageClip):
        return image.compile(spare, None)
    if isinstance(spare, VideoClip):
        return video.compile(spare, None)
    raise TypeError(type(spare).__name__)


def compile(clip: GeneratedScene, _ctx: object) -> dict[str, object]:
    zoom = None if clip.kenburns is None else {"from": clip.kenburns.zoom_from, "to": clip.kenburns.zoom_to}
    return {
        "type": "generated_scene",
        "duration": clip.duration,
        "prompt": clip.prompt,
        "resolution": clip.resolution,
        "aspect_ratio": clip.aspect_ratio,
        "seed": clip.seed,
        "image": clip.image,
        "kenburns": zoom,
        "fallbackClip": _fallback(clip),
    }


def _counted(canvas: Canvas, folder: str) -> int:
    key = COUNT + str(canvas.step)
    known = canvas.folders.get(key)
    if known is None:
        known = str(len(list(Path(folder).glob("f*.png"))))
        canvas.folders[key] = known
    return int(known)


def frame(clip: GeneratedScene, local: int, count: int, canvas: Canvas) -> PilImage:
    image_module, _draw, _font = pil()
    still = canvas.folders.get(STILL + str(canvas.step))
    if still is not None:
        shown = ImageClip(RelativePath(still), clip.duration, clip.kenburns)
        return image.frame(shown, local, count, canvas)
    folder = canvas.folders.get(FRAMES + str(canvas.step))
    held = 0 if folder is None else _counted(canvas, folder)
    if folder is None or held == 0:
        return image_module.new("RGB", (canvas.width, canvas.height), canvas.bg)
    path = Path(folder) / f"f{min(local, held - 1):04d}.png"
    return fit(image_module.open(path).convert("RGB"), canvas)
