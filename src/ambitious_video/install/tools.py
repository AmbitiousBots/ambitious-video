from __future__ import annotations

import shutil
import subprocess
from importlib import metadata

from ambitious_kit.domain.envelope import JsonObject, JsonValue

NEEDED_FOR = {
    "ffmpeg": "render and verify",
    "ffprobe": "validate, render and verify",
    "pillow": "render and verify",
    "playwright": "screen_recording clips",
}
NO_PLAYWRIGHT = "".join(
    (
        "Playwright is not installed. ",
        "Only screen_recording clips need it. ",
        "Title cards, images, video files, ",
        "captions and audio work without it. ",
        "To record a screen, install it: ",
        "pip install playwright, ",
        "then playwright install chromium.",
    )
)


def _version(command: str) -> str:
    if shutil.which(command) is None:
        return ""
    result = subprocess.run([command, "-version"], capture_output=True, text=True, check=False)
    lines = result.stdout.splitlines()
    return lines[0] if lines else ""


def _playwright() -> tuple[str, bool]:
    try:
        import playwright
    except ImportError:
        return "", False
    named = getattr(playwright, "__version__", "")
    if isinstance(named, str) and named != "":
        return named, True
    try:
        return metadata.version("playwright"), True
    except metadata.PackageNotFoundError:
        return "", True


def tool_report() -> JsonObject:
    pillow_version = ""
    try:
        import PIL

        pillow_version = str(PIL.__version__)
    except ImportError:
        pillow_version = ""
    playwright_version, playwright_found = _playwright()
    playwright: dict[str, JsonValue] = {"version": playwright_version, "ok": playwright_found}
    if not playwright_found:
        playwright["hint"] = NO_PLAYWRIGHT
    return {
        "ffmpeg": {"version": _version("ffmpeg"), "ok": shutil.which("ffmpeg") is not None},
        "ffprobe": {"version": _version("ffprobe"), "ok": shutil.which("ffprobe") is not None},
        "pillow": {"version": pillow_version, "ok": pillow_version != ""},
        "playwright": playwright,
    }


def tool_warnings(report: JsonObject) -> list[str]:
    found: list[str] = []
    for name, needed in NEEDED_FOR.items():
        entry = report.get(name)
        if isinstance(entry, dict) and entry.get("ok") is True:
            continue
        if name == "playwright":
            found.append(NO_PLAYWRIGHT)
        else:
            found.append("".join((name, " is not installed. ", needed, " need it.")))
    return found
