from __future__ import annotations

import sys
from pathlib import Path

import ambitious_kit
import yaml
from ambitious_kit.build import build_zipapp

ROOT = Path(__file__).resolve().parent.parent
SKIPPED = {".so", ".pyc"}


def pure_copy(source: Path, dest: Path) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in SKIPPED:
            continue
        target = dest / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def main() -> None:
    target = ROOT / "dist" / "ambitious-video-0.1.0.pyz"
    stage = ROOT / "dist" / "yaml"
    if stage.exists():
        for path in sorted(stage.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    pure_copy(Path(yaml.__file__).resolve().parent, stage)
    build_zipapp(
        target,
        [
            ROOT / "src" / "ambitious_video",
            Path(ambitious_kit.__file__).resolve().parent,
            stage,
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
