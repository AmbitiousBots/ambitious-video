from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "schemas", "templates", "examples")
SKIPPED_PARTS = frozenset({"__pycache__"})

ALLOWED: dict[str, str] = {
    "ambitious_video": "The Python package name. Renaming it would break every import and the built binary.",
    "ambitious-video": "The project and binary name, used in output paths and temporary folder names.",
    "AMBITIOUS_HOME": "The environment variable the module contract defines for the data directory.",
    "ambitious_kit": "The vendored kit package this module imports its command runner from.",
    "/workspace/ambitious": "The default of AMBITIOUS_HOME that the module contract pins.",
}

REFUSED: dict[str, re.Pattern[str]] = {
    "ambitiousbots": re.compile(r"ambitious[\s._-]*bots", re.IGNORECASE),
    "ambitious bot": re.compile(r"ambitious[\s._-]*bot", re.IGNORECASE),
    "alexander": re.compile(r"alexander", re.IGNORECASE),
    "dash": re.compile(r"(?<![a-z])dash(?![a-z])", re.IGNORECASE),
    "hal": re.compile(r"(?<![a-z])hal(?![a-z])", re.IGNORECASE),
    "ambitious (outside the allowed names)": re.compile(r"ambitious", re.IGNORECASE),
    "made for you by": re.compile(r"made[\s_-]+for[\s_-]+you[\s_-]+by", re.IGNORECASE),
}


def without_allowed(text: str) -> str:
    for name in sorted(ALLOWED, key=len, reverse=True):
        text = text.replace(name, " ")
    return text


def hits(text: str) -> list[str]:
    cleaned = without_allowed(text)
    return [name for name, pattern in REFUSED.items() if pattern.search(cleaned)]


def scanned_files() -> list[Path]:
    found: list[Path] = []
    for top in SCANNED:
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and not SKIPPED_PARTS & set(path.relative_to(ROOT).parts):
                found.append(path)
    return found


def readable(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return ""


def test_no_company_names_in_shipped_folders() -> None:
    problems: list[str] = []
    for path in scanned_files():
        relative = path.relative_to(ROOT).as_posix()
        for name in hits(relative):
            problems.append(f"{relative}: the file name contains {name!r}")
        for number, line in enumerate(readable(path).splitlines(), start=1):
            for name in hits(line):
                problems.append(f"{relative}:{number}: contains {name!r}")
    assert problems == [], "\n".join(problems)


def test_the_scan_covers_the_shipped_folders() -> None:
    files = scanned_files()
    tops = {path.relative_to(ROOT).parts[0] for path in files}
    assert {"src", "schemas", "examples"} <= tops
    assert sum(1 for path in files if readable(path) != "") > 50


@pytest.mark.parametrize(
    "text",
    [
        "https://ambitiousbots.com",
        "github.com/AmbitiousBots/project",
        "Made by an Ambitious bot",
        "the ambitious_bot helper",
        "Alexander",
        "a Dash, here",
        "DASH_TOKEN",
        "HAL",
        "the hal-9 unit",
        "Part of Ambitious",
        "Made for you by a robot",
    ],
)
def test_the_scan_refuses(text: str) -> None:
    assert hits(text) != []


@pytest.mark.parametrize(
    "text",
    [
        "from ambitious_video.domain import types",
        "import ambitious_kit",
        "out/ambitious-video-0.1.0.pyz",
        "$AMBITIOUS_HOME/video/video.sqlite",
        "AMBITIOUS_HOME defaults to /workspace/ambitious",
        "a dashboard, a shall, a halt and a chalk line",
        "Example Plumbing Co.",
    ],
)
def test_the_scan_allows(text: str) -> None:
    assert hits(text) == []


def test_every_allowed_name_has_a_reason() -> None:
    assert all(len(reason) > 20 for reason in ALLOWED.values())
