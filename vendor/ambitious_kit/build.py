from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
SHEBANG = b"#!/usr/bin/env python3\n"
SKIPPED_DIRECTORIES = frozenset({"__pycache__"})


def _package_files(package: Path) -> list[Path]:
    return sorted(
        path
        for path in package.rglob("*")
        if path.is_file() and not SKIPPED_DIRECTORIES.intersection(path.relative_to(package).parts)
    )


def build_zipapp(target: Path, packages: Sequence[Path], main: str) -> None:
    module, _, function = main.partition(":")
    if module == "" or function == "":
        raise ValueError('main must look like "package.module:function".')
    entries: list[tuple[str, bytes]] = [
        ("__main__.py", f"import sys\n\nfrom {module} import {function}\n\nsys.exit({function}())\n".encode())
    ]
    for package in packages:
        entries.extend(
            (f"{package.name}/{path.relative_to(package).as_posix()}", path.read_bytes())
            for path in _package_files(package)
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as archive:
        archive.write(SHEBANG)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name, content in entries:
                info = zipfile.ZipInfo(name, FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                bundle.writestr(info, content)
    target.chmod(0o755)
