from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import NoReturn

from ambitious_kit.domain.envelope import failure

from ambitious_video.domain.jobs import allowed
from ambitious_video.domain.types import JobState

HINT = "Run selfcheck and read data.problems."


def _sql(*parts: str) -> str:
    return "".join(parts)


def _bad() -> NoReturn:
    raise failure("E_DB", "The database returned a bad row.", HINT)


def _tuple(value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        _bad()
    return value


def _text(row: tuple[object, ...], index: int) -> str:
    value = row[index]
    if not isinstance(value, str):
        _bad()
    return value


def _optional_text(row: tuple[object, ...], index: int) -> str | None:
    value = row[index]
    if value is None:
        return None
    if not isinstance(value, str):
        _bad()
    return value


def _whole(row: tuple[object, ...], index: int) -> int:
    value = row[index]
    if isinstance(value, bool) or not isinstance(value, int):
        _bad()
    return value


@dataclass(frozen=True, slots=True)
class JobRow:
    id: str
    script_sha256: str
    state: str
    created_at: str
    finished_at: str | None
    output_path: str
    output_sha256: str | None
    script_json: str
    script_dir: str
    duration_sec: str
    width: int
    height: int
    fps: int


def _job(value: object) -> JobRow:
    row = _tuple(value)
    return JobRow(
        _text(row, 0),
        _text(row, 1),
        _text(row, 2),
        _text(row, 3),
        _optional_text(row, 4),
        _text(row, 5),
        _optional_text(row, 6),
        _text(row, 7),
        _text(row, 8),
        _text(row, 9),
        _whole(row, 10),
        _whole(row, 11),
        _whole(row, 12),
    )


_JOB_COLUMNS = _sql(
    "id, script_sha256, state, created_at, ",
    "finished_at, output_path, ",
    "output_sha256, script_json, script_dir, ",
    "duration_sec, width, height, fps",
)


def count_jobs(db: sqlite3.Connection) -> int:
    row = _tuple(db.execute("SELECT count(*) FROM jobs").fetchone())
    return _whole(row, 0)


def insert_job(db: sqlite3.Connection, row: JobRow) -> None:
    db.execute(
        _sql("INSERT INTO jobs (", _JOB_COLUMNS, ") ", "VALUES (", "?, ?, ?, ?, ?, ?, ?, ", "?, ?, ?, ?, ?, ?)"),
        (
            row.id,
            row.script_sha256,
            row.state,
            row.created_at,
            row.finished_at,
            row.output_path,
            row.output_sha256,
            row.script_json,
            row.script_dir,
            row.duration_sec,
            row.width,
            row.height,
            row.fps,
        ),
    )


def read_job(db: sqlite3.Connection, job_id: str) -> JobRow | None:
    found = db.execute(_sql("SELECT ", _JOB_COLUMNS, " FROM jobs WHERE id = ?"), (job_id,)).fetchone()
    if found is None:
        return None
    return _job(found)


def list_jobs(db: sqlite3.Connection) -> tuple[JobRow, ...]:
    rows = db.execute(_sql("SELECT ", _JOB_COLUMNS, " FROM jobs ORDER BY created_at, id")).fetchall()
    return tuple(_job(row) for row in rows)


def _state(db: sqlite3.Connection, job_id: str) -> JobState | None:
    row = read_job(db, job_id)
    if row is None:
        return None
    return _as_state(row.state)


def _as_state(value: str) -> JobState:
    if value == "queued":
        return "queued"
    if value == "rendering":
        return "rendering"
    if value == "rendered":
        return "rendered"
    if value == "verified":
        return "verified"
    if value == "failed":
        return "failed"
    if value == "retried":
        return "retried"
    _bad()


def move_job(db: sqlite3.Connection, job_id: str, wanted: JobState, when: str | None) -> bool:
    state = _state(db, job_id)
    if state is None or not allowed(state, wanted):
        return False
    if when is None:
        db.execute("UPDATE jobs SET state = ? WHERE id = ?", (wanted, job_id))
    else:
        db.execute(
            _sql("UPDATE jobs SET state = ?, ", "finished_at = ? WHERE id = ?"),
            (wanted, when, job_id),
        )
    return True


def mark_rendered(db: sqlite3.Connection, job_id: str, when: str, output_sha256: str) -> bool:
    state = _state(db, job_id)
    if state is None or not allowed(state, "rendered"):
        return False
    db.execute(
        _sql(
            "UPDATE jobs SET state = ?, ",
            "finished_at = ?, ",
            "output_sha256 = ? WHERE id = ?",
        ),
        ("rendered", when, output_sha256, job_id),
    )
    return True


def insert_ledger(
    db: sqlite3.Connection,
    *,
    job_id: str,
    step: str,
    step_index: int,
    units: str,
    usd: str,
    idem_key: str,
) -> bool:
    before = db.execute("SELECT count(*) FROM ledger").fetchone()
    db.execute(
        _sql(
            "INSERT OR IGNORE INTO ledger ",
            "(job_id, step, step_index, ",
            "units, usd, idem_key) ",
            "VALUES (?, ?, ?, ?, ?, ?)",
        ),
        (job_id, step, step_index, units, usd, idem_key),
    )
    after = db.execute("SELECT count(*) FROM ledger").fetchone()
    return _whole(_tuple(before), 0) != _whole(_tuple(after), 0)


@dataclass(frozen=True, slots=True)
class LedgerRow:
    job_id: str
    step: str
    step_index: int
    units: str
    usd: str
    idem_key: str


def list_ledger(db: sqlite3.Connection) -> tuple[LedgerRow, ...]:
    rows = db.execute(
        _sql(
            "SELECT job_id, step, step_index, ",
            "units, usd, idem_key ",
            "FROM ledger ORDER BY id",
        )
    ).fetchall()
    built: list[LedgerRow] = []
    for value in rows:
        row = _tuple(value)
        built.append(
            LedgerRow(_text(row, 0), _text(row, 1), _whole(row, 2), _text(row, 3), _text(row, 4), _text(row, 5))
        )
    return tuple(built)


def insert_asset(db: sqlite3.Connection, sha256: str, path: str, kind: str) -> None:
    db.execute(
        _sql("INSERT OR IGNORE INTO assets ", "(sha256, path, kind) VALUES (?, ?, ?)"),
        (sha256, path, kind),
    )
