from __future__ import annotations

import hashlib
import os
import platform
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ambitious_kit.db.sqlite import integrity_check, migrate, open_db, read_user_version
from ambitious_kit.domain.envelope import ErrorEntry, Failure, JsonObject
from ambitious_kit.domain.migrations import Migration

NOT_CHECKED = "not checked"


@dataclass(frozen=True)
class Finding:
    problem: str
    error: ErrorEntry


@dataclass(frozen=True)
class DbReport:
    integrity: str
    user_version: int
    applied: tuple[str, ...]
    findings: tuple[Finding, ...]


def _findings_of(error: Exception) -> tuple[Finding, ...]:
    if isinstance(error, Failure):
        return tuple(Finding(entry.message, entry) for entry in error.errors)
    return (
        Finding(
            f"The self-check could not finish: {error}.",
            ErrorEntry("E_INTERNAL", "The self-check hit an unexpected error.", "Report it with the stderr log."),
        ),
    )


def _check_db(module: str, db_path: str, migrations: Sequence[Migration]) -> DbReport:
    try:
        db = open_db(db_path, readonly=False)
    except Failure as error:
        return DbReport(NOT_CHECKED, 0, (), _findings_of(error))
    try:
        applied: tuple[str, ...] = ()
        findings: tuple[Finding, ...] = ()
        try:
            applied = tuple(migrate(db, migrations))
        except Failure as error:
            findings = _findings_of(error)
        integrity = integrity_check(db)
        if integrity != "ok":
            findings += (
                Finding(
                    f"The database failed its integrity check: {integrity}.",
                    ErrorEntry(
                        "E_DB",
                        "The database failed its integrity check.",
                        f"Restore {module}.sqlite from a backup, then run selfcheck again.",
                    ),
                ),
            )
        return DbReport(integrity, read_user_version(db), applied, findings)
    except sqlite3.Error as error:
        return DbReport(NOT_CHECKED, 0, (), _findings_of(error))
    finally:
        db.close()


def selfcheck(*, module: str, db_path: str, binary_path: str, migrations: Sequence[Migration]) -> JsonObject:
    report = _check_db(module, db_path, migrations)
    binary = os.path.realpath(binary_path)
    data: JsonObject = {
        "runtime": {"name": "python", "version": platform.python_version()},
        "binary": {"path": binary, "sha256": hashlib.sha256(Path(binary).read_bytes()).hexdigest()},
        "db": {
            "path": db_path,
            "integrityCheck": report.integrity,
            "userVersion": report.user_version,
            "migrationsApplied": list(report.applied),
        },
        "problems": [finding.problem for finding in report.findings],
    }
    if report.findings:
        raise Failure([finding.error for finding in report.findings], data)
    return data
