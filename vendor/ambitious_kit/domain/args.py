from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

VALUE_FLAGS = ("in", "db", "now", "seed")
FLAG_USAGE = "[--in file] [--db path] [--now ISO8601] [--seed int] [--json]"


@dataclass(frozen=True)
class ParsedArgs:
    words: tuple[str, ...]
    input_file: str | None
    db: str | None
    now: str | None
    seed: str | None
    problems: tuple[str, ...]


def parse_args(argv: Sequence[str]) -> ParsedArgs:
    words: list[str] = []
    problems: list[str] = []
    values: dict[str, str] = {}
    json_seen = False
    index = 0
    while index < len(argv):
        token = argv[index]
        index += 1
        if not token.startswith("--"):
            words.append(token)
            continue
        name, equals, inline = token[2:].partition("=")
        if name == "json" and equals == "":
            if json_seen:
                problems.append("The flag --json was given more than once.")
            json_seen = True
            continue
        if name not in VALUE_FLAGS:
            problems.append(f'Unknown flag "{token}".')
            continue
        if equals == "=":
            value: str | None = inline
        elif index < len(argv) and not argv[index].startswith("--"):
            value = argv[index]
            index += 1
        else:
            value = None
        if value is None or value == "":
            problems.append(f"The flag --{name} needs a value.")
            continue
        if name in values:
            problems.append(f"The flag --{name} was given more than once.")
        values[name] = value
    return ParsedArgs(
        words=tuple(words),
        input_file=values.get("in"),
        db=values.get("db"),
        now=values.get("now"),
        seed=values.get("seed"),
        problems=tuple(problems),
    )
