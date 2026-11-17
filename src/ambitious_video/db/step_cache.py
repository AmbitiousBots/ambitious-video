from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

BODY = "output.bin"
META = "meta.json"
LOCKS = ".locks"
STEP_KEY = re.compile(r"^[0-9a-f]{64}$")


class CacheDamaged(Exception):
    def __init__(self, step_key: str) -> None:
        super().__init__(step_key)
        self.step_key = step_key


@dataclass(frozen=True, slots=True)
class Label:
    kind: str
    provider: str
    model: str
    adapter_version: int


@dataclass(frozen=True, slots=True)
class Entry:
    step_key: str
    path: Path
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class CacheRow:
    step_key: str
    dir: str
    kind: str
    provider: str
    model: str
    state: str
    sha256: str | None
    size: int | None
    created_at: str
    used_at: str


def _sql(*parts: str) -> str:
    return "".join(parts)


def entry_dir(root: Path, step_key: str) -> Path:
    if STEP_KEY.fullmatch(step_key) is None:
        raise CacheDamaged(step_key)
    return root / step_key


def lock_path(root: Path, step_key: str) -> Path:
    return root / LOCKS / (entry_dir(root, step_key).name + ".lock")


def read_entry(root: Path, step_key: str) -> Entry | None:
    folder = entry_dir(root, step_key)
    body = folder / BODY
    meta = folder / META
    if not body.is_file() or not meta.is_file():
        return None
    try:
        noted: object = json.loads(meta.read_text(encoding="utf-8"))
    except ValueError:
        raise CacheDamaged(step_key) from None
    data = body.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(noted, dict) or noted.get("stepKey") != step_key or noted.get("sha256") != digest:
        raise CacheDamaged(step_key)
    return Entry(step_key, body, digest, len(data))


def write_entry(root: Path, step_key: str, label: Label, data: bytes) -> Entry:
    folder = entry_dir(root, step_key)
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    noted = {
        "stepKey": step_key,
        "kind": label.kind,
        "provider": label.provider,
        "model": label.model,
        "adapterVersion": label.adapter_version,
        "file": BODY,
        "sha256": digest,
        "bytes": len(data),
    }
    partial = Path(tempfile.mkdtemp(prefix=".partial-", dir=root))
    try:
        (partial / BODY).write_bytes(data)
        text = json.dumps(noted, sort_keys=True, separators=(",", ":"))
        (partial / META).write_text(text + "\n", encoding="utf-8")
        if folder.exists():
            shutil.rmtree(folder)
        os.replace(partial, folder)
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    return Entry(step_key, folder / BODY, digest, len(data))


def remove_entry(root: Path, step_key: str) -> bool:
    folder = entry_dir(root, step_key)
    lock = lock_path(root, step_key)
    if lock.is_file():
        lock.unlink()
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def row_state(db: sqlite3.Connection, step_key: str, where: str) -> str | None:
    found = db.execute(
        _sql("SELECT state FROM step_cache ", "WHERE step_key = ? AND dir = ?"),
        (step_key, where),
    ).fetchone()
    if not isinstance(found, tuple) or not isinstance(found[0], str):
        return None
    return found[0]


def mark_calling(db: sqlite3.Connection, step_key: str, where: str, label: Label, when: str) -> None:
    db.execute(
        _sql(
            "INSERT INTO step_cache (step_key, dir, ",
            "kind, provider, model, state, ",
            "created_at, used_at) ",
            "VALUES (?, ?, ?, ?, ?, 'calling', ?, ?) ",
            "ON CONFLICT (step_key, dir) DO UPDATE ",
            "SET state = 'calling', used_at = ?",
        ),
        (step_key, where, label.kind, label.provider, label.model, when, when, when),
    )


def mark_ready(db: sqlite3.Connection, entry: Entry, where: str, label: Label, when: str) -> None:
    db.execute(
        _sql(
            "INSERT INTO step_cache (step_key, dir, ",
            "kind, provider, model, state, ",
            "sha256, bytes, created_at, used_at) ",
            "VALUES (?, ?, ?, ?, ?, 'ready', ",
            "?, ?, ?, ?) ",
            "ON CONFLICT (step_key, dir) DO UPDATE ",
            "SET state = 'ready', sha256 = ?, ",
            "bytes = ?, used_at = ?",
        ),
        (
            entry.step_key,
            where,
            label.kind,
            label.provider,
            label.model,
            entry.sha256,
            entry.size,
            when,
            when,
            entry.sha256,
            entry.size,
            when,
        ),
    )


def delete_row(db: sqlite3.Connection, step_key: str, where: str) -> None:
    db.execute(
        _sql("DELETE FROM step_cache ", "WHERE step_key = ? AND dir = ?"),
        (step_key, where),
    )


def _row(value: object) -> CacheRow:
    if not isinstance(value, tuple) or len(value) != 10:
        raise sqlite3.DatabaseError("step_cache")
    texts = [item for item in (*value[:6], *value[8:]) if isinstance(item, str)]
    digest, size = value[6], value[7]
    sized = size is None or (isinstance(size, int) and not isinstance(size, bool))
    if len(texts) != 8 or not (digest is None or isinstance(digest, str)) or not sized:
        raise sqlite3.DatabaseError("step_cache")
    return CacheRow(
        texts[0],
        texts[1],
        texts[2],
        texts[3],
        texts[4],
        texts[5],
        digest if isinstance(digest, str) else None,
        size if isinstance(size, int) else None,
        texts[6],
        texts[7],
    )


def list_rows(db: sqlite3.Connection) -> tuple[CacheRow, ...]:
    rows = db.execute(
        _sql(
            "SELECT step_key, dir, kind, provider, ",
            "model, state, sha256, bytes, ",
            "created_at, used_at FROM step_cache ",
            "ORDER BY step_key, dir",
        )
    ).fetchall()
    return tuple(_row(value) for value in rows)
