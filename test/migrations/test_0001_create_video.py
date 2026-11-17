from __future__ import annotations

from pathlib import Path

from ambitious_kit.db.sqlite import load_migrations, migrate, open_db, read_user_version

ROOT = Path(__file__).resolve().parents[2]


def test_migration_keeps_rows(tmp_path: Path) -> None:
    database = tmp_path / "video.sqlite"
    migrations = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")[:1]
    db = open_db(str(database), readonly=False)
    try:
        assert read_user_version(db) == 0
        applied = migrate(db, migrations)
        assert applied == ["0001_create_video"]
        assert read_user_version(db) == 1
        db.execute(
            "INSERT INTO jobs (id, script_sha256, state, created_at, finished_at, "
            "output_path, output_sha256, script_json, script_dir, duration_sec, width, height, fps) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "j0000000000000001",
                "ab" * 32,
                "queued",
                "2026-01-01T00:00:00.000Z",
                None,
                "out/demo.mp4",
                None,
                "{}",
                "/tmp/demo",
                "1",
                1280,
                720,
                30,
            ),
        )
        db.execute(
            "INSERT INTO ledger (job_id, step, step_index, units, usd, idem_key) VALUES (?, ?, ?, ?, ?, ?)",
            ("j0000000000000001", "voiceover", 0, "4", "0.000060", "j0000000000000001:0:" + "cd" * 32),
        )
        db.execute(
            "INSERT INTO assets (sha256, path, kind) VALUES (?, ?, ?)",
            ("ef" * 32, "assets/still.png", "image"),
        )
        assert migrate(db, migrations) == []
        job = db.execute("SELECT state, width FROM jobs").fetchone()
        line = db.execute("SELECT usd FROM ledger").fetchone()
        asset = db.execute("SELECT kind FROM assets").fetchone()
        assert job == ("queued", 1280)
        assert line == ("0.000060",)
        assert asset == ("image",)
        assert read_user_version(db) == 1
    finally:
        db.close()


def test_ledger_idem_key_is_unique(tmp_path: Path) -> None:
    from ambitious_kit.db.sqlite import load_migrations, migrate, open_db

    from ambitious_video.db.store import insert_ledger

    database = tmp_path / "video.sqlite"
    db = open_db(str(database), readonly=False)
    try:
        migrate(db, load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations"))
        db.execute(
            "INSERT INTO jobs (id, script_sha256, state, created_at, output_path, "
            "script_json, script_dir, duration_sec, width, height, fps) "
            "VALUES ('job', ?, 'rendering', '2026-01-01T00:00:00.000Z', 'out.mp4', '{}', '/tmp', '1', 16, 16, 1)",
            ("ab" * 32,),
        )
        assert insert_ledger(db, job_id="job", step="voiceover", step_index=0, units="3", usd="1", idem_key="same")
        assert not insert_ledger(
            db, job_id="job", step="voiceover", step_index=0, units="3", usd="1", idem_key="same"
        )
        count = db.execute("SELECT count(*) FROM ledger").fetchone()
        assert count == (1,)
    finally:
        db.close()
