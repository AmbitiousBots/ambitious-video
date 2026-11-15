from __future__ import annotations

import os
import sqlite3
from collections.abc import Sequence
from importlib.resources.abc import Traversable
from pathlib import Path
from urllib.parse import quote

from ambitious_kit.domain.envelope import failure
from ambitious_kit.domain.migrations import Migration, checked_migrations, pending_migrations
from ambitious_kit.paths import refuse_agent_data

BUSY_TIMEOUT_SECONDS = 5.0

type Db = sqlite3.Connection
DbError = sqlite3.Error


def read_user_version(db: sqlite3.Connection) -> int:
    row = db.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def open_db(path: str, *, readonly: bool) -> sqlite3.Connection:
    file = refuse_agent_data(path)
    if readonly and not os.path.exists(file):
        raise failure(
            "E_MIGRATION",
            f"The database {file} does not exist yet.",
            "Run selfcheck to create it and apply its migrations.",
        )
    try:
        if readonly:
            db = sqlite3.connect(f"file:{quote(file)}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_SECONDS, autocommit=True)
        else:
            os.makedirs(os.path.dirname(file), exist_ok=True)
            db = sqlite3.connect(file, timeout=BUSY_TIMEOUT_SECONDS, autocommit=True)
        try:
            db.execute("PRAGMA query_only=ON" if readonly else "PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            read_user_version(db)
            return db
        except BaseException:
            db.close()
            raise
    except (sqlite3.Error, OSError) as error:
        raise failure(
            "E_DB",
            f"The database {file} could not be opened for {'reading' if readonly else 'writing'}: {error}.",
            "Run selfcheck and read data.problems.",
        ) from error


def migrate(db: sqlite3.Connection, migrations: Sequence[Migration]) -> list[str]:
    known = checked_migrations(migrations)
    applied: list[str] = []
    for migration in pending_migrations(known, read_user_version(db)):
        version = known.index(migration) + 1
        db.execute("BEGIN IMMEDIATE")
        try:
            db.executescript(migration.sql)
            db.execute(f"PRAGMA user_version = {version}")
            db.execute("COMMIT")
        except sqlite3.Error as error:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise failure(
                "E_MIGRATION",
                f"The migration {migration.name} failed and was rolled back: {error}.",
                f"The database is still at schema version {version - 1}. Fix the migration, "
                "or restore the database from a backup, then run selfcheck again.",
            ) from error
        applied.append(migration.name)
    return applied


def require_current_schema(db: sqlite3.Connection, migrations: Sequence[Migration]) -> None:
    user_version = read_user_version(db)
    pending_migrations(migrations, user_version)
    if user_version < len(migrations):
        raise failure(
            "E_MIGRATION",
            f"The database is at schema version {user_version} and this binary needs {len(migrations)}.",
            "Run selfcheck to apply the pending migrations.",
        )


def integrity_check(db: sqlite3.Connection) -> str:
    return "; ".join(str(row[0]) for row in db.execute("PRAGMA integrity_check").fetchall())


def load_migrations(directory: Traversable | Path) -> tuple[Migration, ...]:
    files = sorted(
        (entry for entry in directory.iterdir() if entry.name.endswith(".sql")), key=lambda entry: entry.name
    )
    return checked_migrations(
        [Migration(entry.name.removesuffix(".sql"), entry.read_text(encoding="utf-8")) for entry in files]
    )
