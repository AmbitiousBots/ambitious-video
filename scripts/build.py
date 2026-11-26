from __future__ import annotations

import sys
from pathlib import Path

import ambitious_kit
from ambitious_kit.build import build_zipapp

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    target = ROOT / "dist" / "ambitious-video-0.1.1.pyz"
    build_zipapp(
        target,
        [
            ROOT / "src" / "ambitious_video",
            Path(ambitious_kit.__file__).resolve().parent,
        ],
        "ambitious_video.cli.main:main",
    )
    link = ROOT / "dist" / "ambitious-video.pyz"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target.name)
    sys.stdout.write(f"{target}\n")


if __name__ == "__main__":
    main()
