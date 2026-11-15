#!/usr/bin/env python3
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

BUDGET = 10
BUDGET_COMMENT = re.compile(r"^# (SAFETY|WHY): \S")
LICENSE = re.compile(r"Copyright|SPDX-License-Identifier")
TYPE_IGNORE_WITH_REASON = re.compile(r"^# type: ignore\[[a-z0-9-]+(, ?[a-z0-9-]+)*\] +# \S.{2,}$")
NEVER_SCANNED = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".ruff_cache"})


@dataclass(frozen=True)
class Comment:
    path: Path
    line: int
    text: str
    starts_its_line: bool


@dataclass(frozen=True)
class FileReport:
    banned: tuple[tuple[Path, int], ...]
    budget: tuple[tuple[Path, int], ...]


def python_files(root: Path, excluded: frozenset[str]) -> list[Path]:
    skipped = NEVER_SCANNED | excluded
    return sorted(path for path in root.rglob("*.py") if not any(part in skipped for part in path.relative_to(root).parts))


def comments_in(path: Path, source: str) -> list[Comment]:
    return [
        Comment(path, token.start[0], token.string, token.line[: token.start[1]].strip() == "")
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    ]


def license_header_lines(comments: list[Comment], lines: list[str]) -> frozenset[int]:
    first_line = 2 if lines[:1] and lines[0].startswith("#!") else 1
    header: list[Comment] = []
    for comment in comments[first_line - 1 :]:
        if comment.line != first_line + len(header) or not comment.starts_its_line:
            break
        header.append(comment)
    if any(LICENSE.search(comment.text) for comment in header):
        return frozenset(comment.line for comment in header)
    return frozenset()


def sits_above_code(comment: Comment, lines: list[str]) -> bool:
    if comment.line >= len(lines):
        return False
    below = lines[comment.line].strip()
    return below != "" and (not below.startswith("#") or BUDGET_COMMENT.match(below) is not None)


def string_statement_lines(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    ]


def check_file(path: Path) -> FileReport:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    comments = comments_in(path, source)
    license_lines = license_header_lines(comments, lines)
    banned: list[tuple[Path, int]] = [(path, line) for line in string_statement_lines(source)]
    budget: list[tuple[Path, int]] = []
    for comment in comments:
        if comment.line == 1 and comment.text.startswith("#!"):
            continue
        if comment.line in license_lines or TYPE_IGNORE_WITH_REASON.match(comment.text):
            continue
        if BUDGET_COMMENT.match(comment.text) and comment.starts_its_line and sits_above_code(comment, lines):
            budget.append((path, comment.line))
        else:
            banned.append((path, comment.line))
    return FileReport(tuple(banned), tuple(budget))


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    reports = [check_file(path) for path in python_files(root, frozenset())]
    banned = [place for report in reports for place in report.banned]
    budget = [place for report in reports for place in report.budget]
    left = max(0, BUDGET - len(budget))
    problems = [
        (
            path,
            line,
            "Delete the comment. Encode the rule in a type, a name or a test.",
        )
        for path, line in banned
    ]
    if len(budget) > BUDGET:
        problems += [(path, line, f"The comment budget is {BUDGET} and {left} remain.") for path, line in budget]
    for path, line, message in sorted(problems):
        sys.stdout.write(f"{path.relative_to(root).as_posix()}:{line}: {message}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
