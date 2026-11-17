from __future__ import annotations

from pathlib import Path

import pytest
from ambitious_kit.db.sqlite import DbError, load_migrations, migrate, open_db, read_user_version

ROOT = Path(__file__).resolve().parents[2]
ROW = (
    "INSERT INTO step_cache (step_key, dir, kind, provider, model, state, sha256, bytes, created_at, used_at) "
    "VALUES (?, ?, 'speech', 'xai', 'tts', ?, ?, ?, '2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')"
)


def test_migration_keeps_rows_and_adds_the_step_cache(tmp_path: Path) -> None:
    migrations = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")
    assert [migration.name for migration in migrations[:2]] == ["0001_create_video", "0002_create_step_cache"]
    db = open_db(str(tmp_path / "video.sqlite"), readonly=False)
    try:
        assert migrate(db, migrations[:1]) == ["0001_create_video"]
        db.execute(
            "INSERT INTO jobs (id, script_sha256, state, created_at, output_path, "
            "script_json, script_dir, duration_sec, width, height, fps) "
            "VALUES ('job', ?, 'rendered', '2026-01-01T00:00:00.000Z', 'out.mp4', '{}', 'demo', '1', 16, 16, 1)",
            ("ab" * 32,),
        )
        db.execute(
            "INSERT INTO ledger (job_id, step, step_index, units, usd, idem_key) VALUES (?, ?, ?, ?, ?, ?)",
            ("job", "voiceover", 0, "4", "0.000060", "job:0:" + "cd" * 32),
        )
        assert migrate(db, migrations[:2]) == ["0002_create_step_cache"]
        assert read_user_version(db) == 2
        assert db.execute("SELECT state FROM jobs").fetchone() == ("rendered",)
        assert db.execute("SELECT usd, idem_key FROM ledger").fetchone() == ("0.000060", "job:0:" + "cd" * 32)
        db.execute(ROW, ("ef" * 32, "cache-a", "ready", "12" * 32, 9))
        db.execute(ROW, ("ef" * 32, "cache-b", "calling", None, None))
        with pytest.raises(DbError):
            db.execute(ROW, ("ef" * 32, "cache-a", "ready", "12" * 32, 9))
        with pytest.raises(DbError):
            db.execute(ROW, ("ab" * 32, "cache-a", "unknown", None, None))
        with pytest.raises(DbError):
            db.execute(ROW, ("short", "cache-a", "ready", None, None))
        assert db.execute("SELECT count(*) FROM step_cache").fetchone() == (2,)
        assert migrate(db, migrations[:2]) == []
    finally:
        db.close()
