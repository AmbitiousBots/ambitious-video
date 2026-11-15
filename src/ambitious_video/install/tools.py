from __future__ import annotations

import shutil
import subprocess

from ambitious_kit.domain.envelope import JsonObject


def _version(command: str) -> str:
    if shutil.which(command) is None:
        return ""
    result = subprocess.run([command, "-version"], capture_output=True, text=True, check=False)
    lines = result.stdout.splitlines()
    return lines[0] if lines else ""


def tool_report() -> JsonObject:
    pillow_version = ""
    try:
        import PIL

        pillow_version = str(PIL.__version__)
    except ImportError:
        pillow_version = ""
    playwright_version = ""
    try:
        import playwright

        playwright_version = str(getattr(playwright, "__version__", ""))
    except ImportError:
        playwright_version = ""
    return {
        "ffmpeg": {"version": _version("ffmpeg"), "ok": shutil.which("ffmpeg") is not None},
        "ffprobe": {"version": _version("ffprobe"), "ok": shutil.which("ffprobe") is not None},
        "pillow": {"version": pillow_version, "ok": pillow_version != ""},
        "playwright": {"version": playwright_version, "ok": playwright_version != ""},
    }
