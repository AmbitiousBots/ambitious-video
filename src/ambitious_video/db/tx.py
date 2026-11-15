from __future__ import annotations

import sqlite3


def commit(db: sqlite3.Connection) -> None:
    if db.in_transaction:
        db.execute("COMMIT")


def begin(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
