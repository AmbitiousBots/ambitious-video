#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path

LIMIT = 40
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src"


def literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > LIMIT:
            found.append((node.lineno, node.value))
    return found


def main() -> int:
    problems: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        for line, text in literals(path):
            problems.append(f"{path.relative_to(ROOT)}:{line}: {text[:60]!r}")
    if problems:
        sys.stderr.write("string literals longer than 40 characters:\n")
        sys.stderr.write("\n".join(problems) + "\n")
        return 1
    sys.stdout.write("check_copy: ok\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
