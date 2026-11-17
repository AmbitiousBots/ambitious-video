from __future__ import annotations

import sqlite3


def _sql(*parts: str) -> str:
    return "".join(parts)


def stored_request(db: sqlite3.Connection, step_key: str) -> str | None:
    found = db.execute(
        _sql("SELECT request_id ", "FROM provider_requests ", "WHERE step_key = ?"),
        (step_key,),
    ).fetchone()
    if not isinstance(found, tuple) or not isinstance(found[0], str):
        return None
    return found[0]


def store_request(db: sqlite3.Connection, step_key: str, provider: str, model: str, request_id: str, when: str) -> None:
    db.execute(
        _sql(
            "INSERT OR REPLACE ",
            "INTO provider_requests ",
            "(step_key, provider, model, ",
            "request_id, created_at) ",
            "VALUES (?, ?, ?, ?, ?)",
        ),
        (step_key, provider, model, request_id, when),
    )


def forget_request(db: sqlite3.Connection, step_key: str) -> None:
    db.execute(_sql("DELETE FROM provider_requests ", "WHERE step_key = ?"), (step_key,))
