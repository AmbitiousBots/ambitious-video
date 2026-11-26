from __future__ import annotations

import array
import io
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import wave
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from ambitious_kit.db.sqlite import Db, load_migrations, migrate, open_db
from ambitious_kit.domain.envelope import Failure

from ambitious_video.cli import engine
from ambitious_video.db import step_cache
from ambitious_video.db.lock import Hold
from ambitious_video.db.store import list_ledger
from ambitious_video.providers.base import ProviderError

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "samples" / "example-bookshop"
MIGRATIONS = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")
ENV_PATH = os.pathsep.join([str(ROOT / "src"), str(ROOT / "vendor")])
Speaker = Callable[[str, str, str], bytes]
SECRET = "not-a-real-key-0123456789"
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


class FakeSpeech:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, text: str, voice: str, key: str) -> bytes:
        assert key == SECRET
        self.calls.append((text, voice))
        tone = array.array("h", [int(8000 * math.sin(index * (200 + len(text)) / 4800)) for index in range(4800)])
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(48000)
            handle.writeframes(tone.tobytes())
        return buffer.getvalue()


def _never(_text: str, _voice: str, _key: str) -> bytes:
    raise AssertionError("a cached step must not call the provider")


class _Answer(io.BytesIO):
    def __enter__(self) -> _Answer:
        return self


@contextmanager
def _served(speaker: Speaker) -> Iterator[None]:
    unwanted: list[str] = []

    def opened(_opener: object, request: object, timeout: float = 0) -> _Answer:
        assert getattr(request, "full_url", "") == "https://api.x.ai/v1/tts" and timeout > 0
        sent = json.loads(getattr(request, "data", b""))
        assert set(sent) == {"text", "voice_id", "language", "output_format"}
        assert hasattr(request, "get_header")
        key = str(request.get_header("Authorization")).removeprefix("Bearer ")
        if speaker is _never:
            unwanted.append(sent["text"])
        return _Answer(speaker(sent["text"], sent["voice_id"], key))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("urllib.request.OpenerDirector.open", opened)
        yield
    assert unwanted == []


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a cached render must not open a socket")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("socket.create_connection", refuse)


def _project(dest: Path) -> tuple[dict[str, object], Path]:
    folder = dest / "project"
    shutil.copytree(SAMPLE, folder)
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    assert isinstance(script, dict)
    for track in script["tracks"]:
        for clip in track.get("clips", []):
            if clip["type"] == "voiceover":
                clip.update({"type": "speech", "provider": "xai-speech", "model": "tts", "voice": "eve"})
    script["output"] = {"path": "out/sample.mp4", "width": 180, "height": 320, "fps": 5}
    return script, folder


def _open(home: Path) -> Db:
    db = open_db(str(home / "video" / "video.sqlite"), readonly=False)
    migrate(db, MIGRATIONS)
    return db


def _render(
    db: Db,
    script: Mapping[str, object],
    folder: Path,
    job: str,
    when: datetime,
    env: Mapping[str, str],
    speaker: Speaker,
    cache: Path | None = None,
) -> dict[str, object]:
    db.execute("BEGIN IMMEDIATE")
    try:
        with _served(speaker):
            data = engine.render_new(db, dict(script), folder, job, when, env, cache)
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return dict(data)


def _steps(data: Mapping[str, object]) -> list[dict[str, object]]:
    steps = data["steps"]
    assert isinstance(steps, list)
    return steps


def _spoken(script: Mapping[str, object]) -> list[str]:
    tracks = script["tracks"]
    assert isinstance(tracks, list)
    return [clip["text"] for track in tracks for clip in track.get("clips", []) if clip["type"] == "speech"]


def _cli(home: Path, args: list[str], now: str) -> dict[str, object]:
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = ENV_PATH
    env["AMBITIOUS_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "ambitious_video.cli.main", *args, "--now", now, "--seed", "1"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    payload["exit"] = result.returncode
    return payload


def _everything_written(home: Path, folder: Path) -> bytes:
    found = b""
    for base in (home, folder):
        for path in sorted(base.rglob("*")):
            if path.is_file():
                found += path.read_bytes()
    return found


def test_a_new_job_with_the_same_inputs_is_not_billed_again(tmp_path: Path, no_network: None) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    fake = FakeSpeech()
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env, fake)
        lines = list_ledger(db)
        spoken = _spoken(script)
        assert fake.calls == [(text, "eve") for text in spoken] and len(set(spoken)) == len(spoken) > 1
        assert [step["cached"] for step in _steps(first)] == [False] * len(spoken)
        assert [line.idem_key for line in lines] == [step["stepKey"] for step in _steps(first)]
        assert {line.job_id for line in lines} == {"job-one"}
        assert Decimal(str(first["costUsd"])) == sum(Decimal(line.usd) for line in lines) > 0

        second = _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)}, _never)
        assert len(fake.calls) == len(spoken)
        assert list_ledger(db) == lines
        assert [step["cached"] for step in _steps(second)] == [True] * len(spoken)
        assert [step["usd"] for step in _steps(second)] == ["0.000000"] * len(spoken)
        assert second["costUsd"] == "0.000000"
        assert [step["stepKey"] for step in _steps(second)] == [step["stepKey"] for step in _steps(first)]
        assert second["planSha256"] == first["planSha256"] == engine.plan_data(script, folder)["planSha256"]
        assert second["cacheDir"] == str((home / "video" / "cache").resolve())
        assert second["jobId"] == "job-two" and first["jobId"] == "job-one"
    finally:
        db.close()
    assert SECRET.encode() not in _everything_written(home, folder)
    assert SECRET not in json.dumps([first, second])


def test_cached_renders_give_the_same_file(tmp_path: Path, no_network: None) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    db = _open(home)
    try:
        warm = _render(
            db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, FakeSpeech()
        )
        again = _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)}, _never)
        third = _render(db, script, folder, "job-three", FIRST, {"AMBITIOUS_HOME": str(home)}, _never)
    finally:
        db.close()
    assert all(step["cached"] is True for step in _steps(again) + _steps(third))
    assert again["sha256"] == third["sha256"] == warm["sha256"]
    assert len(str(again["sha256"])) == 64


def test_an_uncached_paid_step_without_a_key_stops_before_any_call(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    db = _open(home)
    try:
        with pytest.raises(Failure) as caught:
            _render(db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home)}, _never)
        assert [error.code for error in caught.value.errors] == ["E_NO_KEY"]
        assert list_ledger(db) == ()
        assert db.execute("SELECT count(*) FROM jobs").fetchone() == (0,)
    finally:
        db.close()
    assert not (home / "video" / "cache").exists()


def test_the_same_words_twice_in_one_video_are_called_once(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    tracks = script["tracks"]
    assert isinstance(tracks, list)
    voice = next(track for track in tracks if track["id"] == "voice")
    voice["clips"][1]["text"] = voice["clips"][0]["text"]
    home = tmp_path / "home"
    fake = FakeSpeech()
    db = _open(home)
    try:
        data = _render(db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, fake)
        assert len(fake.calls) == len(set(_spoken(script))) == len(_spoken(script)) - 1
        assert len(list_ledger(db)) == len(fake.calls)
        assert [step["cached"] for step in _steps(data)][:2] == [False, True]
    finally:
        db.close()


def test_cache_dir_comes_from_the_flag_then_the_script_then_home(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    flagged = tmp_path / "flag-cache"
    db = _open(home)
    try:
        by_flag = _render(
            db, {**script, "cache": {"dir": "kept"}}, folder, "job-one", FIRST, env, FakeSpeech(), flagged
        )
        by_script = _render(db, {**script, "cache": {"dir": "kept"}}, folder, "job-two", FIRST, env, FakeSpeech())
        rows = step_cache.list_rows(db)
    finally:
        db.close()
    assert by_flag["cacheDir"] == str(flagged.resolve())
    assert by_script["cacheDir"] == str((folder / "kept").resolve())
    assert {row.dir for row in rows} == {str(flagged.resolve()), str((folder / "kept").resolve())}
    keys = {str(step["stepKey"]) for step in _steps(by_flag)}
    assert {path.name for path in flagged.iterdir() if path.is_dir() and not path.name.startswith(".")} == keys
    assert not (home / "video" / "cache").exists()
    entry = flagged / sorted(keys)[0]
    assert sorted(path.name for path in entry.iterdir()) == ["meta.json", "output.bin"]
    meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
    assert meta["stepKey"] == entry.name and meta["kind"] == "speech"
    assert (meta["provider"], meta["model"]) == ("xai-speech", "tts")
    assert set(meta) == {"stepKey", "kind", "provider", "model", "adapterVersion", "file", "sha256", "bytes"}


def test_a_cache_filled_elsewhere_is_used_and_bills_nothing(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    shared = tmp_path / "shared-cache"
    one = _open(tmp_path / "home-one")
    two = _open(tmp_path / "home-two")
    try:
        env = {"AMBITIOUS_HOME": str(tmp_path / "home-one"), "XAI_API_KEY": SECRET}
        _render(one, script, folder, "job-one", FIRST, env, FakeSpeech(), shared)
        data = _render(
            two, script, folder, "job-two", FIRST, {"AMBITIOUS_HOME": str(tmp_path / "home-two")}, _never, shared
        )
        assert list_ledger(two) == ()
        assert all(step["cached"] is True for step in _steps(data))
        assert len(step_cache.list_rows(two)) == len(_steps(data))
    finally:
        one.close()
        two.close()


def test_a_call_that_was_stored_but_not_yet_billed_is_billed_once(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env, FakeSpeech())
        key = str(_steps(first)[0]["stepKey"])
        db.execute("DELETE FROM ledger WHERE idem_key = ?", (key,))
        db.execute("UPDATE step_cache SET state = 'calling' WHERE step_key = ?", (key,))
        second = _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)}, _never)
        third = _render(db, script, folder, "job-three", LATER, {"AMBITIOUS_HOME": str(home)}, _never)
        lines = [line for line in list_ledger(db) if line.idem_key == key]
        assert len(lines) == 1 and lines[0].job_id == "job-two"
        assert _steps(second)[0]["cached"] is False and _steps(second)[0]["usd"] == lines[0].usd
        assert all(step["cached"] is True for step in _steps(third))
        assert {row.state for row in step_cache.list_rows(db)} == {"ready"}
    finally:
        db.close()


def test_a_failed_call_leaves_no_entry_no_row_and_no_ledger_line(tmp_path: Path) -> None:
    def broken(_text: str, _voice: str, key: str) -> bytes:
        raise urllib.error.HTTPError("https://api.x.ai/v1/tts", 500, "broken", None, io.BytesIO(key.encode()))  # type: ignore[arg-type] # no headers

    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    db = _open(home)
    try:
        with pytest.raises(ProviderError) as caught:
            _render(db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, broken)
        assert (caught.value.provider, caught.value.status) == ("xai-speech", 500)
        assert SECRET not in str(caught.value) and caught.value.__cause__ is None
        assert list_ledger(db) == () and step_cache.list_rows(db) == ()
        assert db.execute("SELECT state FROM jobs").fetchall() == [("failed",)]
    finally:
        db.close()
    cache = home / "video" / "cache"
    assert [path.name for path in cache.iterdir()] == [".locks"]


def test_a_changed_cache_file_is_refused(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env, FakeSpeech())
        key = str(_steps(first)[0]["stepKey"])
        (home / "video" / "cache" / key / "output.bin").write_bytes(b"not the stored bytes")
        with pytest.raises(Failure) as caught:
            _render(db, script, folder, "job-two", LATER, env, _never)
        assert [error.code for error in caught.value.errors] == ["E_CACHE"]
        assert caught.value.data == {"stepKey": key}
    finally:
        db.close()


def test_cache_list_reads_and_cache_prune_removes_only_when_asked(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    fake = FakeSpeech()
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env, fake)
    finally:
        db.close()
    keys = sorted(str(step["stepKey"]) for step in _steps(first))
    cache = home / "video" / "cache"
    database = home / "video" / "video.sqlite"
    before = database.read_bytes()

    listed = _cli(home, ["cache", "list"], "2026-01-02T00:00:00Z")
    assert listed["exit"] == 0, listed
    data = listed["data"]
    assert isinstance(data, dict)
    assert [entry["stepKey"] for entry in data["entries"]] == keys
    assert all(entry["present"] and entry["state"] == "ready" for entry in data["entries"])
    assert all(entry["dir"] == str(cache.resolve()) and entry["provider"] == "xai-speech" for entry in data["entries"])
    assert all(entry["createdAt"] == entry["usedAt"] == "2026-01-01T00:00:00.000Z" for entry in data["entries"])
    assert data["count"] == len(keys) and data["bytes"] == sum(entry["bytes"] for entry in data["entries"])
    assert database.read_bytes() == before
    assert _cli(home, ["cache", "list"], "2026-01-02T00:00:00Z")["data"] == data

    refused = _cli(home, ["cache", "prune"], "2027-01-01T00:00:00Z")
    assert refused["exit"] == 1 and refused["errors"][0]["code"] == "E_USAGE"
    wrong = _cli(home, ["cache", "prune", "--older-than", "soon"], "2027-01-01T00:00:00Z")
    assert wrong["exit"] == 1 and wrong["errors"][0]["code"] == "E_USAGE"
    for days in ("36501", "740000", "999999", "1000000"):
        huge = _cli(home, ["cache", "prune", "--older-than", days], "2027-01-01T00:00:00Z")
        assert huge["exit"] == 1 and huge["errors"][0]["code"] == "E_USAGE", huge
    assert sorted(path.name for path in cache.iterdir() if not path.name.startswith(".")) == keys
    young = _cli(home, ["cache", "prune", "--older-than", "90"], "2026-02-01T00:00:00Z")
    assert young["exit"] == 0 and young["data"]["removed"] == [] and young["data"]["kept"] == len(keys)
    assert sorted(path.name for path in cache.iterdir() if not path.name.startswith(".")) == keys
    elsewhere = _cli(
        home, ["cache", "prune", "--older-than", "90", "--cache", str(tmp_path / "other")], "2027-01-01T00:00:00Z"
    )
    assert elsewhere["exit"] == 0 and elsewhere["data"]["removed"] == []

    pruned = _cli(home, ["cache", "prune", "--older-than=90"], "2027-01-01T00:00:00Z")
    assert pruned["exit"] == 0, pruned
    assert sorted(entry["stepKey"] for entry in pruned["data"]["removed"]) == keys
    assert pruned["data"]["freedBytes"] == data["bytes"] and pruned["data"]["kept"] == 0
    assert [path.name for path in cache.iterdir() if not path.name.startswith(".")] == []
    assert _cli(home, ["cache", "list"], "2027-01-01T00:00:00Z")["data"]["entries"] == []
    ledger = _cli(home, ["ledger"], "2027-01-01T00:00:00Z")["data"]
    assert sorted(line["idemKey"] for line in ledger["lines"]) == keys


def test_using_an_entry_keeps_it_from_being_pruned(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    db = _open(home)
    try:
        _render(
            db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, FakeSpeech()
        )
        _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)}, _never)
        rows = step_cache.list_rows(db)
    finally:
        db.close()
    assert {row.created_at for row in rows} == {"2026-01-01T00:00:00.000Z"}
    assert {row.used_at for row in rows} == {"2026-03-01T09:30:00.000Z"}
    pruned = _cli(home, ["cache", "prune", "--older-than", "30"], "2026-03-15T00:00:00Z")
    assert pruned["exit"] == 0 and pruned["data"]["removed"] == [] and pruned["data"]["kept"] == len(rows)


def test_cache_flags_are_refused_where_they_mean_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    planned = _cli(
        home,
        ["validate", "--cache", str(tmp_path / "cache"), "--in", str(SAMPLE / "script.json")],
        "2026-01-01T00:00:00Z",
    )
    assert planned["exit"] == 1 and planned["errors"][0]["code"] == "E_USAGE"
    assert planned["command"] == "validate"
    listed = _cli(home, ["cache", "list", "--older-than", "3"], "2026-01-01T00:00:00Z")
    assert listed["exit"] == 1 and listed["errors"][0]["code"] == "E_USAGE"
    assert not home.exists()


def _ledger_matches_costs(db: Db, renders: list[dict[str, object]], calls: int) -> list[str]:
    lines = list_ledger(db)
    assert len(lines) == calls
    assert sum(Decimal(line.usd) for line in lines) == sum(Decimal(str(data["costUsd"])) for data in renders) > 0
    billed = [step for data in renders for step in _steps(data) if step["cached"] is False]
    assert sorted(Decimal(line.usd) for line in lines) == sorted(Decimal(str(step["usd"])) for step in billed)
    keys = [line.idem_key for line in lines]
    assert len(set(keys)) == len(keys)
    return keys


def test_a_second_cache_folder_reuses_the_result_and_bills_nothing(tmp_path: Path, no_network: None) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    fake = FakeSpeech()
    other = tmp_path / "other-cache"
    db = _open(home)
    try:
        first = _render(
            db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, fake
        )
        second = _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)}, _never, other)
        third = _render(
            db, {**script, "cache": {"dir": "kept"}}, folder, "job-three", LATER, {"AMBITIOUS_HOME": str(home)}, _never
        )
        spoken = _spoken(script)
        assert len(fake.calls) == len(spoken)
        keys = _ledger_matches_costs(db, [first, second, third], len(spoken))
        assert keys == [step["stepKey"] for step in _steps(first)]
        assert all(step["cached"] is True for step in _steps(second) + _steps(third))
        assert second["costUsd"] == third["costUsd"] == "0.000000"
        assert second["sha256"] == first["sha256"]
        rows = step_cache.list_rows(db)
        assert {row.state for row in rows} == {"ready"} and len(rows) == 3 * len(spoken)
    finally:
        db.close()
    for key in keys:
        copied = (other / key / "output.bin").read_bytes()
        assert copied == (home / "video" / "cache" / key / "output.bin").read_bytes()
        assert copied == (folder / "kept" / key / "output.bin").read_bytes()


def test_two_renders_at_once_with_two_cache_folders_call_once(tmp_path: Path, no_network: None) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    fake = FakeSpeech()
    both_started = threading.Barrier(2, timeout=30)
    overlap = threading.Event()
    results: dict[str, dict[str, object]] = {}
    failures: list[BaseException] = []

    def slow(text: str, voice: str, key: str) -> bytes:
        overlap.wait(0.3)
        return fake(text, voice, key)

    def work(job: str) -> None:
        db = _open(home)
        try:
            both_started.wait()
            env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
            output = {"path": "out/" + job + ".mp4", "width": 180, "height": 320, "fps": 5}
            results[job] = _render(db, {**script, "output": output}, folder, job, FIRST, env, slow, tmp_path / job)
        except BaseException as error:
            failures.append(error)
        finally:
            db.close()

    _open(home).close()
    threads = [threading.Thread(target=work, args=(job,)) for job in ("job-a", "job-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    assert not failures, failures
    spoken = _spoken(script)
    assert sorted(fake.calls) == sorted((text, "eve") for text in spoken)
    renders = [results["job-a"], results["job-b"]]
    db = _open(home)
    try:
        keys = _ledger_matches_costs(db, renders, len(spoken))
        rows = step_cache.list_rows(db)
        assert {row.state for row in rows} == {"ready"} and len(rows) == 2 * len(spoken)
    finally:
        db.close()
    assert sorted(keys) == sorted(str(step["stepKey"]) for step in _steps(renders[0]))
    for key in keys:
        billed = [step["cached"] for data in renders for step in _steps(data) if step["stepKey"] == key]
        assert sorted(billed) == [False, True]
        assert (tmp_path / "job-a" / key / "output.bin").read_bytes() == (
            tmp_path / "job-b" / key / "output.bin"
        ).read_bytes()


def test_a_damaged_result_in_another_folder_is_not_copied(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    fake = FakeSpeech()
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env, fake)
        key = str(_steps(first)[0]["stepKey"])
        (home / "video" / "cache" / key / "output.bin").write_bytes(b"not the stored bytes")
        with pytest.raises(Failure) as caught:
            _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)}, _never, tmp_path / "other")
        assert [error.code for error in caught.value.errors] == ["E_NO_KEY"]
        second = _render(db, script, folder, "job-three", LATER, env, fake, tmp_path / "other")
        spoken = _spoken(script)
        assert len(fake.calls) == len(spoken) + 1
        assert [step["cached"] for step in _steps(second)] == [False] + [True] * (len(spoken) - 1)
        keys = _ledger_matches_costs(db, [first, second], len(spoken) + 1)
        assert keys[-1] == key + "#2"
    finally:
        db.close()


def test_a_pruned_result_is_called_again_and_gets_its_own_line(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    fake = FakeSpeech()
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env, fake)
        spoken = _spoken(script)
        db.execute("BEGIN IMMEDIATE")
        pruned = engine.cache_prune(db, None, 30, LATER)
        db.execute("COMMIT")
        assert len(pruned["removed"]) == len(spoken) and step_cache.list_rows(db) == ()
        second = _render(db, script, folder, "job-two", LATER, env, fake)
        third = _render(db, script, folder, "job-three", LATER, {"AMBITIOUS_HOME": str(home)}, _never)
        assert len(fake.calls) == 2 * len(spoken)
        assert [step["cached"] for step in _steps(second)] == [False] * len(spoken)
        assert all(step["cached"] is True for step in _steps(third))
        keys = _ledger_matches_costs(db, [first, second, third], 2 * len(spoken))
        step_keys = [str(step["stepKey"]) for step in _steps(first)]
        assert keys == step_keys + [key + "#2" for key in step_keys]
        assert [line.job_id for line in list_ledger(db)] == ["job-one"] * len(spoken) + ["job-two"] * len(spoken)

        db.execute("BEGIN IMMEDIATE")
        engine.cache_prune(db, None, 0, datetime(2026, 6, 1, tzinfo=UTC))
        db.execute("COMMIT")
        fourth = _render(db, script, folder, "job-four", datetime(2026, 6, 2, tzinfo=UTC), env, fake)
        keys = _ledger_matches_costs(db, [first, second, third, fourth], 3 * len(spoken))
        assert keys[2 * len(spoken) :] == [key + "#3" for key in step_keys]
    finally:
        db.close()


def test_a_deleted_entry_folder_is_called_again_and_gets_its_own_line(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    fake = FakeSpeech()
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env, fake)
        spoken = _spoken(script)
        key = str(_steps(first)[1]["stepKey"])
        shutil.rmtree(home / "video" / "cache" / key)
        with pytest.raises(Failure) as caught:
            _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)}, _never)
        assert [error.code for error in caught.value.errors] == ["E_NO_KEY"]
        second = _render(db, script, folder, "job-three", LATER, env, fake)
        assert len(fake.calls) == len(spoken) + 1
        assert [step["cached"] for step in _steps(second)] == [index != 1 for index in range(len(spoken))]
        keys = _ledger_matches_costs(db, [first, second], len(spoken) + 1)
        assert keys == [str(step["stepKey"]) for step in _steps(first)] + [key + "#2"]
        assert second["sha256"] == first["sha256"]
    finally:
        db.close()


def test_prune_leaves_an_entry_that_a_render_holds(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    db = _open(home)
    try:
        first = _render(
            db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, FakeSpeech()
        )
        keys = sorted(str(step["stepKey"]) for step in _steps(first))
        cache = home / "video" / "cache"
        hold = Hold(str(step_cache.lock_path(cache, keys[0])))
        try:
            assert hold.try_lock()
            db.execute("BEGIN IMMEDIATE")
            pruned = engine.cache_prune(db, None, 30, LATER)
            db.execute("COMMIT")
        finally:
            hold.close()
        assert sorted(entry["stepKey"] for entry in pruned["removed"]) == keys[1:] and pruned["kept"] == 1
        assert [row.step_key for row in step_cache.list_rows(db)] == [keys[0]]
        assert (cache / keys[0] / "output.bin").is_file()
        assert step_cache.lock_path(cache, keys[1]).is_file()
    finally:
        db.close()


def test_prune_refuses_an_entry_folder_that_is_a_link(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    db = _open(home)
    try:
        first = _render(
            db, script, folder, "job-one", FIRST, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, FakeSpeech()
        )
        key = sorted(str(step["stepKey"]) for step in _steps(first))[0]
        entry = home / "video" / "cache" / key
        moved = tmp_path / "moved"
        entry.rename(moved)
        entry.symlink_to(moved, target_is_directory=True)
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(Failure) as refused:
            engine.cache_prune(db, None, 0, LATER)
        db.execute("ROLLBACK")
        assert refused.value.errors[0].code == "E_CACHE"
        assert (moved / "output.bin").is_file()
    finally:
        db.close()


def test_a_render_with_an_older_clock_does_not_move_last_use_back(tmp_path: Path) -> None:
    script, folder = _project(tmp_path)
    home = tmp_path / "home"
    db = _open(home)
    try:
        _render(
            db, script, folder, "job-one", LATER, {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}, FakeSpeech()
        )
        _render(db, script, folder, "job-two", FIRST, {"AMBITIOUS_HOME": str(home)}, _never)
        assert {row.used_at for row in step_cache.list_rows(db)} == {"2026-03-01T09:30:00.000Z"}
    finally:
        db.close()
