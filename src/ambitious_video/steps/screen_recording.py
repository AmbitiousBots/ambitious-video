from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ambitious_video.domain.need import NeedTool
from ambitious_video.domain.types import Click, ScreenRecording, Scroll, Wait
from ambitious_video.steps.common import Canvas, pil

if TYPE_CHECKING:
    from PIL.Image import Image as PilImage


def compile(clip: ScreenRecording, _ctx: object) -> dict[str, object]:
    actions: list[dict[str, object]] = []
    for action in clip.actions:
        if isinstance(action, Click):
            actions.append({"click": action.selector})
        elif isinstance(action, Wait):
            actions.append({"wait": action.seconds})
        elif isinstance(action, Scroll):
            actions.append({"scroll": action.pixels})
    return {"type": "screen_recording", "url": clip.url, "duration": clip.duration, "actions": actions}


def frame(clip: ScreenRecording, local: int, count: int, canvas: Canvas) -> PilImage:
    image_module, _draw, _font = pil()
    folder = canvas.folders.get("step:" + str(canvas.step))
    if folder is None:
        return image_module.new("RGB", (canvas.width, canvas.height), canvas.bg)
    shots = sorted(Path(folder).glob("shot*.png"))
    if not shots:
        return image_module.new("RGB", (canvas.width, canvas.height), canvas.bg)
    pick = min(len(shots) - 1, local * len(shots) // max(count, 1))
    return image_module.open(shots[pick]).convert("RGB").resize((canvas.width, canvas.height))


def record(clip: ScreenRecording, folder: Path, width: int, height: int) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise NeedTool("playwright") from error
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(clip.url)
        page.screenshot(path=str(folder / "shot000.png"))
        for index, action in enumerate(clip.actions, start=1):
            if isinstance(action, Click):
                page.click(action.selector)
            elif isinstance(action, Wait):
                page.wait_for_timeout(int(action.seconds * 1000))
            elif isinstance(action, Scroll):
                page.mouse.wheel(0, action.pixels)
            page.screenshot(path=str(folder / f"shot{index:03d}.png"))
        browser.close()
