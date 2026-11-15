from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ambitious_kit.domain.envelope import failure

MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+$", re.ASCII)
OWNED_BY_THE_RUNNER = re.compile(r"\s*(BEGIN|COMMIT|END|ROLLBACK|PRAGMA\s+user_version)\b", re.IGNORECASE)
COMMENT_OR_QUOTED = re.compile(
    r"""--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\]""",
    re.DOTALL,
)
OPENS_TRIGGER = re.compile(r"\s*CREATE\s+(?:(?:TEMP|TEMPORARY)\s+)?TRIGGER\b", re.IGNORECASE)
CLOSES_TRIGGER = re.compile(r"\s*END\s*", re.IGNORECASE)


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str


def top_level_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    inside_trigger = False
    for piece in COMMENT_OR_QUOTED.sub(" ", sql).split(";"):
        if inside_trigger:
            inside_trigger = CLOSES_TRIGGER.fullmatch(piece) is None
        else:
            statements.append(piece)
            inside_trigger = OPENS_TRIGGER.match(piece) is not None
    return tuple(statements)


def checked_migrations(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    for index, migration in enumerate(migrations):
        match = MIGRATION_NAME.fullmatch(migration.name)
        expected = f"{index + 1:04d}"
        if match is None:
            raise failure(
                "E_MIGRATION",
                f'The migration "{migration.name}" is not named NNNN_name.',
                "Name it with four digits, an underscore and lower-case words, for example 0001_create_greetings.sql.",
            )
        if match.group(1) != expected:
            raise failure(
                "E_MIGRATION",
                f'The migration "{migration.name}" should be number {expected}.',
                "Number migrations from 0001 with no gaps.",
            )
        if any(OWNED_BY_THE_RUNNER.match(statement) for statement in top_level_statements(migration.sql)):
            raise failure(
                "E_MIGRATION",
                f'The migration "{migration.name}" contains a BEGIN, COMMIT, END, ROLLBACK '
                "or PRAGMA user_version statement.",
                "Delete those statements. The migration runner wraps each file in one transaction "
                "and sets user_version itself. The BEGIN and END of a trigger body are fine.",
            )
    return tuple(migrations)


def pending_migrations(migrations: Sequence[Migration], user_version: int) -> tuple[Migration, ...]:
    if user_version > len(migrations):
        raise failure(
            "E_MIGRATION",
            f"The database is at schema version {user_version}, but this binary only knows up to {len(migrations)}.",
            "Install the newer binary. A module never downgrades a database.",
        )
    return tuple(migrations[user_version:])
