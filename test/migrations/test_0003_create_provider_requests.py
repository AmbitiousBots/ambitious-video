from __future__ import annotations

from pathlib import Path

import pytest
from ambitious_kit.db.sqlite import DbError, load_migrations, migrate, open_db, read_user_version

ROOT = Path(__file__).resolve().parents[2]
ROW = (
    "INSERT INTO provider_requests (step_key, provider, model, request_id, created_at) "
    "VALUES (?, 'xai-video', 'grok-imagine-video', ?, '2026-01-01T00:00:00.000Z')"
)
CACHED = (
    "INSERT INTO step_cache (step_key, dir, kind, provider, model, state, created_at, used_at) "
    "VALUES (?, 'cache-a', 'scene', 'xai-video', 'grok-imagine-video', 'calling', "
    "'2026-01-01T00:00:00.000Z', '2026-01-01T00:00:00.000Z')"
)


def test_migration_keeps_rows_and_adds_one_request_per_step_key(tmp_path: Path) -> None:
    migrations = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")
    assert [migration.name for migration in migrations] == [
        "0001_create_video",
        "0002_create_step_cache",
        "0003_create_provider_requests",
    ]
    db = open_db(str(tmp_path / "video.sqlite"), readonly=False)
    try:
        assert migrate(db, migrations[:2]) == ["0001_create_video", "0002_create_step_cache"]
        db.execute(CACHED, ("ef" * 32,))
        assert migrate(db, migrations) == ["0003_create_provider_requests"]
        assert read_user_version(db) == 3
        assert db.execute("SELECT state FROM step_cache").fetchone() == ("calling",)
        db.execute(ROW, ("ef" * 32, "req-1"))
        with pytest.raises(DbError):
            db.execute(ROW, ("ef" * 32, "req-2"))
        with pytest.raises(DbError):
            db.execute(ROW, ("short", "req-3"))
        with pytest.raises(DbError):
            db.execute(ROW, ("ab" * 32, ""))
        assert db.execute("SELECT step_key, request_id FROM provider_requests").fetchall() == [("ef" * 32, "req-1")]
        assert migrate(db, migrations) == []
    finally:
        db.close()
