from __future__ import annotations

import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import ambitious_kit
import yaml
from ambitious_kit.build import build_zipapp

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    target = ROOT / "dist" / "ambitious-video-0.1.1.pyz"
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as staging:
        app = Path(staging) / "ambitious_video"
        bundled_yaml = Path(staging) / "yaml"
        shutil.copytree(ROOT / "src" / app.name, app, ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache"))
        shutil.copytree(
            Path(yaml.__file__).parent,
            bundled_yaml,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.so"),
        )
        build_zipapp(
            target,
            [app, Path(ambitious_kit.__file__).resolve().parent, bundled_yaml],
            "ambitious_video.cli.main:main",
        )
    link = ROOT / "dist" / "ambitious-video.pyz"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(target.name)
    sys.stdout.write(f"{target}\n")


if __name__ == "__main__":
    main()
