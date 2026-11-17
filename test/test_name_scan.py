from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "schemas", "templates", "examples", ".github")
DOCUMENT_SUFFIXES = frozenset({".md"})
SKIPPED_PARTS = frozenset(
    {"__pycache__", ".git", ".venv", "venv", "vendor", "dist", "node_modules", ".mypy_cache", ".ruff_cache"}
)
SCAN_ITSELF = Path(__file__).resolve()

ALLOWED: dict[str, str] = {
    "ambitious_video": "The Python package name. Renaming it would break every import and the built binary.",
    "ambitious-video": "The project and binary name, used in output paths and temporary folder names.",
    "AMBITIOUS_HOME": "The environment variable the module contract defines for the data directory.",
    "ambitious_kit": "The vendored kit package this module imports its command runner from.",
    "ambitious_home": "The vendored kit function that reads AMBITIOUS_HOME; the default cache folder is found with it.",
    "/workspace/ambitious": "The default of AMBITIOUS_HOME that the module contract pins.",
    ".cache/ambitious": "The cache folder where spec/check.sh keeps the pinned model checker download.",
    "Ambitious Corporation": (
        "The holder of this project's Apache-2.0 copyright. It is named in the copyright line of NOTICE, "
        "and in CONTRIBUTING.md where that line is explained, and nowhere else."
    ),
    "https://github.com/AmbitiousBots/ambitious-video/security/advisories/new": (
        "The private security report form. A GitHub issue template contact link must be an absolute address, "
        "so this one address stays, and only in .github/ISSUE_TEMPLATE/config.yml."
    ),
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


def kept(path: Path) -> bool:
    return path.is_file() and path != SCAN_ITSELF and not SKIPPED_PARTS & set(path.relative_to(ROOT).parts)


def tracked() -> set[Path] | None:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    names = [name for name in listed.stdout.split("\0") if name != ""]
    if listed.returncode != 0 or not names:
        return None
    return {ROOT / name for name in names}


def scanned_files() -> list[Path]:
    found: set[Path] = set()
    for top in SCANNED:
        found.update(path for path in (ROOT / top).rglob("*") if kept(path))
    found.update(path for path in ROOT.iterdir() if kept(path))
    found.update(path for path in ROOT.rglob("*") if path.suffix in DOCUMENT_SUFFIXES and kept(path))
    in_repository = tracked()
    return sorted(found if in_repository is None else found & in_repository)


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
    assert {"src", "schemas", "templates", "examples"} <= tops
    assert sum(1 for path in files if readable(path) != "") > 50


def test_the_scan_covers_the_documents() -> None:
    names = {path.relative_to(ROOT).as_posix() for path in scanned_files()}
    top_level = {"README.md", "NOTICE", "LICENSE", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md"}
    elsewhere = {"spec/README.md", "schemas/README.md", ".github/pull_request_template.md"}
    assert top_level | elsewhere | {"pyproject.toml", ".github/ISSUE_TEMPLATE/config.yml"} <= names
    assert not any(name.startswith(("vendor/", ".venv/", ".git/")) for name in names)


def test_the_security_form_is_named_in_one_file_only() -> None:
    form = next(name for name in ALLOWED if name.startswith("https://"))
    holders = [path.relative_to(ROOT).as_posix() for path in scanned_files() if form in readable(path)]
    assert holders == [".github/ISSUE_TEMPLATE/config.yml"]


def test_the_copyright_holder_is_named_in_the_notice_only() -> None:
    holders = [
        path.relative_to(ROOT).as_posix() for path in scanned_files() if "Ambitious Corporation" in readable(path)
    ]
    assert holders == ["CONTRIBUTING.md", "NOTICE"]


def test_the_documents_name_no_company_as_author() -> None:
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Copyright 2026 Ambitious Corporation" in notice
    assert "The ambitious-video contributors" not in notice
    assert "Corporation" not in readme
    assert "the project contributors" in readme
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")


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
        "github.com/AmbitiousBots/ambitious-video",
        "the ambitious-kit repository",
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
        "Copyright 2026 The ambitious-video contributors",
        "Copyright 2026 Ambitious Corporation",
        "keeps it in ~/.cache/ambitious/",
    ],
)
def test_the_scan_allows(text: str) -> None:
    assert hits(text) == []


def test_every_allowed_name_has_a_reason() -> None:
    assert all(len(reason) > 20 for reason in ALLOWED.values())
