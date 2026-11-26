from __future__ import annotations

import array
import email
import json
import math
import shutil
import subprocess
import threading
import wave
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from ambitious_kit.db.sqlite import Db, load_migrations, migrate, open_db
from ambitious_kit.domain.envelope import Failure
from PIL import Image

from ambitious_video import providers
from ambitious_video.cli import engine
from ambitious_video.db.store import list_ledger, read_job
from ambitious_video.domain.fallback import fallback_of
from ambitious_video.domain.load import load_script
from ambitious_video.domain.template import expand, load_template
from ambitious_video.domain.types import ImageClip, LipSync, LocalMouth, VideoClip
from ambitious_video.media.audio import RATE, Piece, levels, mixed
from ambitious_video.media.film import Made, canvas_for, frame_at, prepare
from ambitious_video.providers.base import (
    Deferred,
    LipSyncer,
    Patience,
    Provider,
    ProviderError,
    RequestLost,
    RunResult,
)
from ambitious_video.providers.syncso_lipsync import SyncLipsync, SyncLipsyncConfig, form_of
from ambitious_video.providers.xai_speech import XaiSpeech, XaiSpeechConfig

ROOT = Path(__file__).resolve().parents[1]
STILL = ROOT / "examples" / "assets" / "still.png"
MIGRATIONS = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")
SPEECH_SECRET = "not-a-real-key-1122334455"
SYNC_SECRET = "not-a-real-key-9988776655"
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 4, 1, tzinfo=UTC)
TAG = "AI-generated lip movement"
MOUTH = {"type": "mouth", "sprite": "assets/still.png", "x": 0.5, "y": 0.6, "width": 0.2, "height": 0.2}
MOUTH_BOX = (60, 40, 100, 68)


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    bearer: str | None
    api_key: str | None
    content_type: str | None
    body: bytes


def _clip_bytes(folder: Path, color: str, seconds: int) -> bytes:
    path = folder / f"made-{color}.mp4"
    source = f"color=c={color}:size=64x36:rate=5:duration={seconds}"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, stdin=subprocess.DEVNULL, check=True)
    return path.read_bytes()


def _tone(path: Path, quiet_seconds: float, loud_seconds: float) -> None:
    quiet = [0] * int(RATE * quiet_seconds)
    loud = [int(20000 * math.sin(2 * math.pi * 220 * index / RATE)) for index in range(int(RATE * loud_seconds))]
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(array.array("h", quiet + loud).tobytes())


class _Service(BaseHTTPRequestHandler):
    seen: list[Call] = []
    video = b""
    spoken = b""
    status = "PENDING"
    refuse = 0

    def _answer(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)

    def _noted(self, method: str, body: bytes) -> bool:
        kind = type(self)
        headers = self.headers
        kind.seen.append(
            Call(
                method,
                self.path,
                headers.get("Authorization"),
                headers.get("x-api-key"),
                headers.get("Content-Type"),
                body,
            )
        )
        if kind.refuse:
            self._answer(b"{}", kind.refuse)
        return kind.refuse == 0

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self._noted("POST", body):
            return
        kind = type(self)
        if self.path == "/v1/tts":
            self._answer(kind.spoken)
            return
        sent = sum(1 for call in kind.seen if (call.method, call.path) == ("POST", self.path))
        self._answer(json.dumps({"id": f"gen-{sent}", "status": "PENDING", "outputUrl": ""}).encode(), 201)

    def do_GET(self) -> None:
        if not self._noted("GET", b""):
            return
        kind = type(self)
        if self.path.startswith("/v2/generate/hop"):
            self.send_response(302)
            self.send_header("Location", "/elsewhere/" + self.path.rsplit("/", 1)[-1])
            self.end_headers()
            return
        if self.path.startswith("/files/"):
            self._answer(kind.video)
            return
        name = self.path.rsplit("/", 1)[-1]
        address = ""
        if kind.status == "COMPLETED":
            address = f"http://127.0.0.1:{self.server.server_address[1]}/files/{name}.mp4"
        self._answer(json.dumps({"id": name, "status": kind.status, "outputUrl": address}).encode())

    def log_message(self, *_args: object) -> None:
        return None


def _sent(method: str, prefix: str) -> list[Call]:
    return [call for call in _Service.seen if call.method == method and call.path.startswith(prefix)]


def _parts(call: Call) -> dict[str, tuple[str | None, str, bytes]]:
    assert call.content_type is not None and call.content_type.startswith("multipart/form-data; boundary=form-")
    parsed = email.message_from_bytes(b"Content-Type: " + call.content_type.encode() + b"\r\n\r\n" + call.body)
    found: dict[str, tuple[str | None, str, bytes]] = {}
    for part in parsed.get_payload():  # type: ignore[union-attr] # a multipart body is a list of parts
        name = part.get_param("name", header="content-disposition")
        body = part.get_payload(decode=True)
        assert isinstance(name, str) and isinstance(body, bytes)
        found[name] = (part.get_filename(), part.get_content_type(), body)
    return found


@pytest.fixture
def service(tmp_path: Path) -> Iterator[str]:
    _Service.seen = []
    _Service.video = _clip_bytes(tmp_path, "red", 2)
    spoken = tmp_path / "spoken.wav"
    _tone(spoken, 0.2, 1.0)
    _Service.spoken = spoken.read_bytes()
    _Service.status = "PENDING"
    _Service.refuse = 0
    server = HTTPServer(("127.0.0.1", 0), _Service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _adapter(base: str, wait: float = 0.15) -> SyncLipsync:
    return SyncLipsync(
        SyncLipsyncConfig(
            submit_url=base + "/v2/generate",
            status_url=base + "/v2/generate/",
            download_schemes=("http",),
            timeout_seconds=5,
            poll_seconds=0.02,
            wait_seconds=wait,
        )
    )


@pytest.fixture
def local_sync(service: str) -> Iterator[str]:
    kept = [providers.find("sync-lipsync"), providers.find("xai-speech")]
    providers.unregister("sync-lipsync")
    providers.unregister("xai-speech")
    providers.register(_adapter(service))
    providers.register(XaiSpeech(XaiSpeechConfig(endpoint=service + "/v1/tts", timeout_seconds=5)))
    try:
        yield service
    finally:
        providers.unregister("sync-lipsync")
        providers.unregister("xai-speech")
        for adapter in kept:
            assert adapter is not None
            providers.register(adapter)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("this test must not open a socket")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("socket.create_connection", refuse)
    monkeypatch.setattr("urllib.request.urlopen", refuse)


def _lip(**changes: object) -> dict[str, object]:
    clip: dict[str, object] = {
        "type": "lip_sync",
        "provider": "sync-lipsync",
        "model": "lipsync-2",
        "video": "assets/presenter.mp4",
        "voice": "voice",
        "duration": 2,
    }
    clip.update(changes)
    return {name: value for name, value in clip.items() if value is not None}


def _line(text: str = "A line for the presenter.") -> dict[str, object]:
    return {
        "type": "speech",
        "at": 1.2,
        "duration": 1.5,
        "text": text,
        "provider": "xai-speech",
        "model": "tts",
        "voice": "eve",
    }


RECORDED = {"type": "audio", "path": "assets/line.wav", "at": 1.0}


def _script(
    lip: Mapping[str, object],
    *sounds: Mapping[str, object],
    fps: int = 5,
    title: str = "Example Co.",
    disclosure: str | None = TAG,
) -> dict[str, object]:
    card = {"type": "title_card", "duration": 1.0, "text": title}
    script: dict[str, object] = {
        "schemaVersion": 2,
        "output": {"path": "out/presenter.mp4", "width": 160, "height": 90, "fps": fps},
        "tracks": [{"id": "main", "clips": [card, lip]}, {"id": "voice", "clips": list(sounds) or [_line()]}],
    }
    if disclosure is not None:
        script["disclosure"] = disclosure
    return script


def _folder(dest: Path, presenter_seconds: int = 3) -> Path:
    folder = dest / "project"
    (folder / "assets").mkdir(parents=True)
    shutil.copy(STILL, folder / "assets" / "still.png")
    (folder / "assets" / "presenter.mp4").write_bytes(_clip_bytes(dest, "blue", presenter_seconds))
    _tone(folder / "assets" / "line.wav", 1.0, 1.0)
    return folder


def _open(home: Path) -> Db:
    db = open_db(str(home / "video" / "video.sqlite"), readonly=False)
    migrate(db, MIGRATIONS)
    return db


def _render(
    db: Db,
    script: Mapping[str, object] | str,
    folder: Path,
    job: str,
    when: datetime,
    env: Mapping[str, str],
    *,
    offline: bool = False,
    resend: bool = False,
) -> dict[str, object]:
    db.execute("BEGIN IMMEDIATE")
    try:
        if isinstance(script, str):
            data = engine.render_again(db, script, None, when, env, offline=offline, resend=resend)
        else:
            data = engine.render_new(db, dict(script), folder, job, when, env, offline=offline, resend=resend)
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return dict(data)


def _rows(data: Mapping[str, object], name: str) -> list[dict[str, object]]:
    rows = data[name]
    assert isinstance(rows, list)
    return rows


def _key_of(plan: Mapping[str, object], kind: str) -> str:
    return next(str(step["stepKey"]) for step in _rows(plan, "steps") if step["type"] == kind)


def _requests(db: Db) -> list[tuple[str, str, str]]:
    found = db.execute("SELECT step_key, provider, request_id FROM provider_requests ORDER BY step_key").fetchall()
    return [(str(row[0]), str(row[1]), str(row[2])) for row in found]


def _everything_written(*folders: Path) -> bytes:
    found = b""
    for base in folders:
        for path in sorted(base.rglob("*")):
            if path.is_file():
                found += path.read_bytes()
    return found


def _problems(script: Mapping[str, object], folder: Path, *, files: bool = False) -> list[tuple[str, str]]:
    with pytest.raises(Failure) as caught:
        if files:
            engine.validate_data(dict(script), folder)
        else:
            engine.plan_data(dict(script), folder)
    rows = caught.value.data["problems"]
    assert isinstance(rows, list)
    return [(str(row["at"]), str(row["message"])) for row in rows]


def _keys(home: Path) -> dict[str, str]:
    return {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SPEECH_SECRET, "SYNC_API_KEY": SYNC_SECRET}


def test_lip_sync_is_estimated_per_output_frame_at_the_output_fps(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)
    env = {"AMBITIOUS_HOME": str(tmp_path / "home")}
    data = engine.estimate_data(_script(_lip(duration=30), fps=25), folder, env)
    line = _rows(data, "breakdown")[1]
    key = line.pop("stepKey")
    assert isinstance(key, str) and len(key) == 64
    assert line == {
        "step": "lip_sync",
        "index": 1,
        "units": "750",
        "usd": "1.500000",
        "provider": "sync-lipsync",
        "model": "lipsync-2",
        "unit": "frame",
        "usdPerUnit": "0.002",
        "sourceUrl": "https://sync.so/pricing",
        "checkedAt": "2026-11-16",
        "cached": False,
    }
    assert data["secretsNeeded"] == [
        {"env": "SYNC_API_KEY", "provider": "sync-lipsync", "steps": [1], "usd": "1.500000"},
        {"env": "XAI_API_KEY", "provider": "xai-speech", "steps": [2], "usd": "0.000375"},
    ]
    assert data["usd"] == "1.500375"

    def priced(fps: int, model: str, seconds: int = 30) -> tuple[object, object]:
        lip = _lip(duration=seconds, model=model)
        found = _rows(engine.estimate_data(_script(lip, fps=fps), folder, env), "breakdown")[1]
        return found["units"], found["usd"]

    assert priced(30, "lipsync-2") == ("900", "1.800000")
    assert priced(25, "lipsync-2", 10) == ("250", "0.500000")
    assert priced(25, "lipsync-2-pro") == ("750", "2.490000")
    assert priced(25, "sync-3") == ("750", "3.990000")
    offline = engine.estimate_data(_script(_lip(duration=30), fps=25), folder, env, offline=True)
    assert offline["usd"] == "0.000000" and offline["secretsNeeded"] == []
    assert not (tmp_path / "home").exists()


def test_a_lip_sync_that_times_out_is_polled_again_and_never_sent_twice(tmp_path: Path, local_sync: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_lip())
    env = _keys(home)
    plan = engine.plan_data(script, folder)
    key, speech = _key_of(plan, "lip_sync"), _key_of(plan, "speech")
    db = _open(home)
    try:
        with pytest.raises(Failure) as waiting:
            _render(db, script, folder, "job-one", FIRST, env)
        assert [error.code for error in waiting.value.errors] == ["E_PENDING"]
        assert waiting.value.data == {
            "jobId": "job-one",
            "index": 1,
            "stepKey": key,
            "provider": "sync-lipsync",
            "requestId": "gen-1",
        }
        job = read_job(db, "job-one")
        assert job is not None and job.state == "failed"
        assert len(_sent("POST", "/v2/generate")) == 1 and len(_sent("GET", "/v2/generate/gen-1")) >= 1
        assert _requests(db) == [(key, "sync-lipsync", "gen-1")]
        assert [line.idem_key for line in list_ledger(db)] == [speech]
        assert not (folder / "out" / "presenter.mp4").exists()

        with pytest.raises(Failure) as other:
            _render(db, script, folder, "job-two", FIRST, env)
        assert [error.code for error in other.value.errors] == ["E_PENDING"]
        assert other.value.data["requestId"] == "gen-1" and len(_sent("POST", "/v2/generate")) == 1

        _Service.status = "COMPLETED"
        retried = _render(db, "job-one", folder, "", LATER, env)
        assert len(_sent("POST", "/v2/generate")) == 1 and len(_sent("POST", "/v1/tts")) == 1
        assert {call.path for call in _sent("GET", "/v2/generate/")} == {"/v2/generate/gen-1"}
        steps = {step["index"]: step for step in _rows(retried, "steps")}
        assert (steps[1]["stepKey"], steps[1]["cached"], steps[1]["usd"]) == (key, False, "0.020000")
        assert (steps[2]["stepKey"], steps[2]["cached"], steps[2]["usd"]) == (speech, True, "0.000000")
        ledger = list_ledger(db)
        assert [(line.job_id, line.idem_key, line.step, line.units, line.usd) for line in ledger] == [
            ("job-one", speech, "speech", "25", "0.000375"),
            ("job-one", key, "lip_sync", "10", "0.020000"),
        ]
        assert retried["costUsd"] == "0.020000" and _requests(db) == []
        done = read_job(db, "job-one")
        assert done is not None and done.state == "rendered"
    finally:
        db.close()
    assert all(call.api_key == SYNC_SECRET and call.bearer is None for call in _sent("POST", "/v2/generate"))
    assert all(call.api_key == SYNC_SECRET for call in _sent("GET", "/v2/generate/"))
    assert all(call.api_key is None and call.bearer is None for call in _sent("GET", "/files/"))
    written = _everything_written(home, folder)
    assert SYNC_SECRET.encode() not in written and SPEECH_SECRET.encode() not in written
    said = json.dumps([waiting.value.data, other.value.data, retried, plan])
    assert SYNC_SECRET not in said and SPEECH_SECRET not in said


def test_a_lip_synced_clip_is_made_once_from_the_presenter_and_the_speech_and_cached_across_jobs(
    tmp_path: Path, local_sync: str
) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_lip())
    env = _keys(home)
    _Service.status = "COMPLETED"
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env)
        (posted,) = _sent("POST", "/v2/generate")
        parts = _parts(posted)
        assert sorted(parts) == ["audio", "model", "options", "video"]
        assert parts["model"][2] == b"lipsync-2" and json.loads(parts["options"][2]) == {"sync_mode": "cut_off"}
        assert parts["video"] == ("video.mp4", "video/mp4", (folder / "assets" / "presenter.mp4").read_bytes())
        name, kind, sound = parts["audio"]
        assert name == "audio.wav" and kind == "audio/wav" and sound[:4] == b"RIFF"
        assert len(sound) == 44 + 2 * 2 * RATE and any(sound[44 + 2 * RATE // 2 :])
        assert not any(sound[44 : 44 + 2 * RATE // 10])
        assert first["costUsd"] == "0.020375" and [step["cached"] for step in _rows(first, "steps")] == [False, False]
        manifest = json.loads(Path(str(first["manifest"])).read_text(encoding="utf-8"))
        made = manifest["steps"][1]
        assert (made["type"], made["provider"], made["model"]) == ("lip_sync", "sync-lipsync", "lipsync-2")
        assert (made["paid"], made["cached"], made["usd"]) == (True, False, "0.020000")
        assert manifest["disclosure"] == TAG

        calls, ledger = len(_Service.seen), list_ledger(db)
        again = _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)})
        assert len(_Service.seen) == calls and list_ledger(db) == ledger and len(ledger) == 2
        assert [(step["cached"], step["usd"]) for step in _rows(again, "steps")] == [(True, "0.000000")] * 2
        assert again["costUsd"] == "0.000000" and again["sha256"] == first["sha256"]
        estimate = engine.estimate_data(script, folder, {"AMBITIOUS_HOME": str(home)})
        assert estimate["usd"] == "0.000000" and estimate["secretsNeeded"] == []
        assert [line["cached"] for line in _rows(estimate, "breakdown")[1:]] == [True, True]
        verified = engine.verify_job(db, "job-two", LATER)
        assert verified["passed"] is True
    finally:
        db.close()
    written = _everything_written(home, folder)
    assert SYNC_SECRET.encode() not in written and SPEECH_SECRET.encode() not in written
    assert SYNC_SECRET not in json.dumps([first, again, estimate, manifest])


def test_the_step_key_of_a_lip_sync_follows_its_presenter_and_its_sound(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)

    def keys(script: Mapping[str, object]) -> tuple[str, str, str]:
        plan = engine.plan_data(dict(script), folder)
        steps = _rows(plan, "steps")
        assert steps[1]["hears"] == [2] and steps[1]["paid"] is True
        return str(steps[1]["stepKey"]), str(steps[2]["stepKey"]), str(plan["planSha256"])

    lip, speech, plan = keys(_script(_lip()))
    assert keys(_script(_lip())) == (lip, speech, plan)
    other_lip, other_speech, other_plan = keys(_script(_lip(), title="Another title"))
    assert (other_lip, other_speech) == (lip, speech) and other_plan != plan
    new_lip, new_speech, _plan = keys(_script(_lip(), _line("Other words for the presenter.")))
    assert new_speech != speech and new_lip != lip
    assert keys(_script(_lip(model="lipsync-2-pro")))[:2] != (lip, speech)
    assert keys(_script(_lip(model="lipsync-2-pro")))[1] == speech
    assert keys(_script(_lip(), fps=10))[0] != lip
    moved = {**_line(), "at": 1.4}
    assert keys(_script(_lip(), moved))[0] != lip
    recorded = keys(_script(_lip(), RECORDED))[0]
    assert recorded != lip
    _tone(folder / "assets" / "line.wav", 0.5, 1.5)
    assert keys(_script(_lip(), RECORDED))[0] != recorded
    (folder / "assets" / "presenter.mp4").write_bytes(_clip_bytes(tmp_path, "green", 3))
    assert keys(_script(_lip()))[0] != lip and keys(_script(_lip()))[1] == speech
    early = {**_line(), "at": 0.6, "duration": 1.0}
    assert keys(_script(_lip(), early))[0] not in {lip, recorded}


def test_a_lip_sync_that_hears_no_sound_is_refused_before_anything_is_priced(tmp_path: Path, no_network: None) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "SYNC_API_KEY": SYNC_SECRET, "XAI_API_KEY": SPEECH_SECRET}
    silent = (
        {**_line(), "at": 3.5},
        {**_line(), "at": 0.2, "duration": 0.6},
        {**_line(), "at": 0.4, "duration": 0.6},
        {**RECORDED, "at": 3.0},
    )
    db = _open(home)
    try:
        for sound in silent:
            script = _script(_lip(), sound)
            for attempt in range(4):
                with pytest.raises(Failure) as refused:
                    if attempt == 0:
                        engine.validate_data(dict(script), folder)
                    elif attempt == 1:
                        engine.plan_data(dict(script), folder)
                    elif attempt == 2:
                        engine.estimate_data(dict(script), folder, env)
                    else:
                        engine.render_new(db, dict(script), folder, "job-silent", FIRST, env)
                assert [error.code for error in refused.value.errors] == ["E_INPUT_SCHEMA"], sound
                assert _rows(refused.value.data, "problems") == [
                    {
                        "code": "E_INPUT_SCHEMA",
                        "at": "/tracks/0/clips/1/voice",
                        "message": "No sound on that track is in this clip.",
                    }
                ]
        assert not list_ledger(db) and read_job(db, "job-silent") is None
    finally:
        db.close()
    edge = _script(_lip(), {**_line(), "at": 2.8})
    assert _rows(engine.plan_data(edge, folder), "steps")[1]["hears"] == [2]
    running_in = _script(_lip(), {**_line(), "at": 0.6, "duration": 1.0})
    assert _rows(engine.plan_data(running_in, folder), "steps")[1]["hears"] == [2]
    offline = _script(_lip(fallback=MOUTH), {**RECORDED, "at": 3.0})
    assert _rows(engine.plan_data(offline, folder, offline=True), "steps")[1]["hears"] == []


def test_offline_shows_the_presenter_or_a_drawn_mouth_and_costs_nothing(tmp_path: Path, no_network: None) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home)}
    plain = _script(_lip(), RECORDED)
    drawn = _script(_lip(fallback=MOUTH), RECORDED)
    plan = engine.plan_data(drawn, folder, offline=True)
    step = _rows(plan, "steps")[1]
    assert (step["type"], step["paid"], step["provider"], step["fallback"]) == ("mouth", False, "local", "mouth")
    assert step["fallbackFor"] == {"type": "lip_sync", "provider": "sync-lipsync", "model": "lipsync-2"}
    assert step["hears"] == [2] and plan == engine.plan_data(drawn, folder, offline=True)
    still = _rows(engine.plan_data(plain, folder, offline=True), "steps")[1]
    assert (still["type"], still["path"], still["fallback"], still["paid"]) == (
        "video",
        "assets/presenter.mp4",
        "none",
        False,
    )
    estimate = engine.estimate_data(drawn, folder, env, offline=True)
    assert estimate["usd"] == "0.000000" and estimate["secretsNeeded"] == []
    assert engine.plan_view(drawn, folder, env, offline=True)["secretsNeeded"] == []
    with pytest.raises(Failure) as refused:
        engine.render_new(_open(home), dict(drawn), folder, "job-keyless", FIRST, env)
    assert [error.code for error in refused.value.errors] == ["E_NO_KEY"]
    assert refused.value.data == {
        "missing": [{"env": "SYNC_API_KEY", "provider": "sync-lipsync", "steps": [1], "usd": "0.020000"}]
    }
    db = _open(home)
    try:
        one = _render(db, drawn, folder, "job-one", FIRST, env, offline=True)
        two = _render(db, drawn, folder, "job-two", LATER, env, offline=True)
        manifest = json.loads(Path(str(two["manifest"])).read_text(encoding="utf-8"))
        assert engine.verify_job(db, "job-two", LATER)["passed"] is True
        db.execute("COMMIT")
        shown = _render(db, plain, folder, "job-three", LATER, env, offline=True)
        assert one["costUsd"] == two["costUsd"] == shown["costUsd"] == "0.000000"
        assert one["sha256"] == two["sha256"] != shown["sha256"] and list_ledger(db) == ()
        assert [(item["index"], item["fallback"]) for item in _rows(one, "steps")] == [(1, "mouth")]
        assert [(item["index"], item["fallback"]) for item in _rows(shown, "steps")] == [(1, "none")]
        assert one["planSha256"] == plan["planSha256"]
        assert manifest["offline"] is True and manifest["steps"][1]["fallback"] == "mouth"
        assert engine.cache_list(db, None)["count"] == 0
    finally:
        db.close()
    quieter = _rows(engine.plan_data(drawn, folder, offline=True), "steps")[1]["stepKey"]
    _tone(folder / "assets" / "line.wav", 0.2, 1.8)
    assert _rows(engine.plan_data(drawn, folder, offline=True), "steps")[1]["stepKey"] != quieter


def test_the_drawn_mouth_opens_with_the_loudness_of_the_recorded_sound(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)
    sound = mixed(10, 5, (Piece(folder / "assets" / "line.wav", 0, 0.0, ()),))
    heard = levels(sound, 10, 5)
    assert heard == (0.0,) * 5 + (1.0,) * 5 and levels(mixed(10, 5, ()), 10, 5) == (0.0,) * 10
    half = mixed(10, 5, (Piece(folder / "assets" / "line.wav", 0, -6.0, ()),))
    assert levels(half, 10, 5) == heard

    def frames(script: Mapping[str, object]) -> list[Image.Image]:
        _script_off, timeline, taken = engine.studied(dict(script), folder, require_files=True, offline=True)
        assert timeline.disclosure is None and taken[1].name in {"mouth", "none"}
        canvas = canvas_for(timeline, folder)
        prepare(timeline, canvas, tmp_path / "work", folder)
        return [frame_at(timeline, index, canvas) for index in range(5, 15)]

    drawn = frames(_script(_lip(fallback=MOUTH), RECORDED))
    assert drawn == frames(_script(_lip(fallback=MOUTH), RECORDED))
    closed, opened = drawn[2], drawn[7]
    assert all(frame.tobytes() == closed.tobytes() for frame in drawn[:5])
    assert all(frame.tobytes() == opened.tobytes() for frame in drawn[5:])
    assert closed.crop(MOUTH_BOX).tobytes() != opened.crop(MOUTH_BOX).tobytes()
    for box in ((0, 0, 160, 35), (0, 72, 160, 90), (0, 0, 55, 90), (105, 0, 160, 90)):
        assert closed.crop(box).tobytes() == opened.crop(box).tobytes()
    assert opened.getpixel((80, 54)) == (30, 16, 20) and closed.getpixel((80, 46)) != (30, 16, 20)
    painted = frames(_script(_lip(fallback={**MOUTH, "color": "#00FF00"}), RECORDED))
    assert painted[7].getpixel((80, 54)) == (0, 255, 0)
    silent = frames(_script(_lip(fallback=MOUTH)))
    assert all(frame.tobytes() == closed.tobytes() for frame in silent)
    sprite = frames(_script(_lip(video=None, image="assets/still.png", model="sync-3"), RECORDED))
    for box in ((0, 0, 160, 35), (0, 72, 160, 90)):
        assert sprite[7].crop(box).tobytes() == opened.crop(box).tobytes()
    assert sprite[7].crop(MOUTH_BOX).tobytes() != opened.crop(MOUTH_BOX).tobytes()


def test_a_lip_sync_the_provider_cannot_make_is_refused_before_anything_runs(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path, presenter_seconds=1)
    picture = _lip(video=None, image="assets/still.png")
    assert _problems(_script(picture), folder) == [("/tracks/0/clips/1/image", "Only sync-3 takes a picture.")]
    assert engine.plan_data(_script({**picture, "model": "sync-3"}), folder)["durationSec"] == 3
    assert _problems(_script(_lip(model="lipsync-9")), folder) == [
        ("/tracks/0/clips/1/model", "The provider has no such model.")
    ]
    assert _problems(_script(_lip(provider="xai-speech", model="tts")), folder) == [
        ("/tracks/0/clips/1/provider", "That provider does not do this step.")
    ]
    assert _problems(_script(_lip(provider="no-such-provider")), folder) == [
        ("/tracks/0/clips/1/provider", "No provider has that id.")
    ]
    short = _problems(_script(_lip()), folder, files=True)
    assert ("/tracks/0/clips/1/video", "The video is shorter than the clip.") in short
    missing = _problems(
        _script(_lip(video="assets/nobody.mp4", fallback={**MOUTH, "sprite": "assets/no.png"})), folder, files=True
    )
    assert ("/tracks/0/clips/1/video", "The video file is missing.") in missing
    assert ("/tracks/0/clips/1/fallback/sprite", "The image file is missing.") in missing

    def refused(script: Mapping[str, object]) -> list[tuple[str, str]]:
        loaded, problems = load_script(script)
        assert loaded is None
        return [(item.at, getattr(item, "message", "")) for item in problems]

    assert refused(_script(_lip(), disclosure=None)) == [("", "A generated scene needs disclosure text.")]
    assert refused(_script(_lip(voice="main"))) == [("/tracks/0/clips/1/voice", "voice must name a track with sound.")]
    assert refused(_script(_lip(voice="nobody"))) == [
        ("/tracks/0/clips/1/voice", "voice must name a track with sound.")
    ]
    captions = {"type": "voiceover", "at": 1.0, "text": "Only shown."}
    assert refused(_script(_lip(), captions)) == [("/tracks/0/clips/1/voice", "voice must name a track with sound.")]
    assert refused(_script(_lip(image="assets/still.png"))) == [("/tracks/0/clips/1", "Give one of video or image.")]
    assert refused(_script(_lip(video=None))) == [("/tracks/0/clips/1", "Give one of video or image.")]
    assert refused(_script(_lip(fallback={"type": "title_card", "text": "Hello"}))) == [
        ("/tracks/0/clips/1/fallback/type", "Use none or mouth.")
    ]
    assert refused(_script(_lip(fallback={**MOUTH, "x": 1.5}))) == [
        ("/tracks/0/clips/1/fallback/x", "This number is too large.")
    ]
    assert refused(_script(_lip(fallback={"type": "none", "sprite": "assets/still.png"}))) == [
        ("/tracks/0/clips/1/fallback/sprite", "This field is not allowed.")
    ]
    assert refused(_script(_lip(api_key="not-a-real-key"))) == [
        ("/tracks/0/clips/1/api_key", "This field is not allowed.")
    ]


def test_a_generation_the_provider_fails_is_forgotten_not_billed_and_sent_again_on_request(
    tmp_path: Path, local_sync: str
) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_lip(), RECORDED)
    env = _keys(home)
    key = _key_of(engine.plan_data(script, folder), "lip_sync")
    db = _open(home)
    try:
        _Service.status = "FAILED"
        with pytest.raises(Failure) as lost:
            _render(db, script, folder, "job-one", FIRST, env)
        assert [error.code for error in lost.value.errors] == ["E_PROVIDER"]
        assert lost.value.data == {"provider": "sync-lipsync", "stepKey": key}
        assert _requests(db) == [] and list_ledger(db) == () and len(_sent("POST", "/v2/generate")) == 1

        _Service.status = "PROCESSING"
        with pytest.raises(Failure) as waiting:
            _render(db, "job-one", folder, "", FIRST, env)
        assert waiting.value.data["requestId"] == "gen-2" and len(_sent("POST", "/v2/generate")) == 2
        with pytest.raises(Failure) as again:
            _render(db, "job-one", folder, "", FIRST, env)
        assert again.value.data["requestId"] == "gen-2" and len(_sent("POST", "/v2/generate")) == 2
        with pytest.raises(Failure) as resent:
            _render(db, "job-one", folder, "", FIRST, env, resend=True)
        assert resent.value.data["requestId"] == "gen-3" and len(_sent("POST", "/v2/generate")) == 3
        assert _requests(db) == [(key, "sync-lipsync", "gen-3")] and list_ledger(db) == ()

        _Service.status = "COMPLETED"
        _Service.video = b"not a video"
        with pytest.raises(Failure) as broken:
            _render(db, "job-one", folder, "", FIRST, env)
        assert [error.code for error in broken.value.errors] == ["E_PROVIDER"]
        assert _requests(db) == [] and list_ledger(db) == () and engine.cache_list(db, None)["count"] == 0
    finally:
        db.close()
    assert len(_sent("POST", "/v1/tts")) == 0
    sound = _parts(_sent("POST", "/v2/generate")[0])["audio"][2]
    assert not any(sound[44 : 44 + RATE]) and any(sound[44 + 2 * RATE + 2 * RATE // 2 :])


def test_sync_lipsync_uploads_once_then_polls_and_downloads_without_the_key(tmp_path: Path, service: str) -> None:
    adapter = _adapter(service)
    listed: Provider = adapter
    assert (listed.kind, listed.id, listed.secret, listed.seedable) == (
        "lipsync",
        "sync-lipsync",
        "SYNC_API_KEY",
        False,
    )
    assert adapter.models == ("lipsync-2", "lipsync-2-pro", "sync-3")
    assert isinstance(adapter, Deferred) and isinstance(adapter, LipSyncer)
    assert SyncLipsync().config.submit_url == "https://api.sync.so/v2/generate"
    assert SyncLipsync().config.status_url == "https://api.sync.so/v2/generate/"
    assert SyncLipsync().config.download_schemes == ("https",) and SyncLipsync().patience == Patience(5, 600)
    folder = _folder(tmp_path)
    script, _none = load_script(_script(_lip()))
    assert script is not None
    clip = script.tracks[0].clips[1]  # type: ignore[union-attr] # the first track holds clips
    assert isinstance(clip, LipSync)
    params = adapter.params(clip)
    assert params == {"model": "lipsync-2", "presenter": "video", "options": {"sync_mode": "cut_off"}}
    assert adapter.quote(params, Decimal(750)) == Decimal("1.50")
    files = {"video": folder / "assets" / "presenter.mp4", "audio": folder / "assets" / "line.wav"}
    assert form_of({"model": "lipsync-2"}, files) == form_of({"model": "lipsync-2"}, files)
    request_id = adapter.submit(params, files, SYNC_SECRET)
    (posted,) = _sent("POST", "/v2/generate")
    assert request_id == "gen-1" and posted.api_key == SYNC_SECRET and SYNC_SECRET.encode() not in posted.body
    assert _parts(posted)["audio"] == ("audio.wav", "audio/wav", files["audio"].read_bytes())
    still, _none = load_script(_script(_lip(video=None, image="assets/still.png", model="sync-3")))
    assert still is not None
    picture = still.tracks[0].clips[1]  # type: ignore[union-attr] # the first track holds clips
    assert isinstance(picture, LipSync) and adapter.lip_faults(picture) == () and adapter.lip_faults(clip) == ()
    shown = adapter.params(picture)
    assert shown == {"model": "sync-3", "presenter": "image"}
    adapter.submit(shown, {"image": STILL, "audio": files["audio"]}, SYNC_SECRET)
    sent = _parts(_sent("POST", "/v2/generate")[1])
    assert sorted(sent) == ["audio", "image", "model"] and sent["image"] == (
        "image.png",
        "image/png",
        STILL.read_bytes(),
    )
    out = tmp_path / "out.bin"
    assert adapter.collect(request_id, SYNC_SECRET, out) is None and not out.exists()
    _Service.status = "PROCESSING"
    assert adapter.collect(request_id, SYNC_SECRET, out) is None and not out.exists()
    _Service.status = "COMPLETED"
    found = adapter.collect(request_id, SYNC_SECRET, out)
    assert found == RunResult(out) and out.read_bytes() == _Service.video
    assert [call.api_key for call in _sent("GET", "/v2/generate/gen-1")] == [SYNC_SECRET] * 3
    assert [(call.api_key, call.bearer) for call in _sent("GET", "/files/")] == [(None, None)]
    for status in ("FAILED", "REJECTED"):
        _Service.status = status
        with pytest.raises(RequestLost):
            adapter.collect(request_id, SYNC_SECRET, out)
    with pytest.raises(RequestLost):
        adapter.collect("../other", SYNC_SECRET, out)
    strict = SyncLipsync(SyncLipsyncConfig(status_url=service + "/v2/generate/", timeout_seconds=5))
    _Service.status = "COMPLETED"
    with pytest.raises(ProviderError) as unsafe:
        strict.collect(request_id, SYNC_SECRET, tmp_path / "plain.bin")
    assert not isinstance(unsafe.value, RequestLost) and not (tmp_path / "plain.bin").exists()
    assert len(_sent("GET", "/files/")) == 1
    for status, lost in ((404, True), (410, True), (401, False), (403, False), (429, False), (500, False)):
        _Service.refuse = status
        with pytest.raises(ProviderError) as answered:
            adapter.collect(request_id, SYNC_SECRET, out)
        assert (answered.value.status, isinstance(answered.value, RequestLost)) == (status, lost)
    _Service.refuse = 0
    with pytest.raises(ProviderError) as moved:
        adapter.collect("hop-1", SYNC_SECRET, out)
    assert moved.value.status == 302 and not isinstance(moved.value, RequestLost)
    assert _sent("GET", "/elsewhere/") == [] and len(_sent("GET", "/v2/generate/hop-1")) == 1


def _locals_of(error: BaseException) -> str:
    parts = [str(error), repr(error.args), str(error.__cause__), str(error.__context__)]
    frame = error.__traceback__
    while frame is not None:
        parts.extend(repr(value) for value in frame.tb_frame.f_locals.values())
        frame = frame.tb_next
    return " ".join(parts)


def test_a_refused_lip_sync_call_drops_the_key(tmp_path: Path, service: str) -> None:
    adapter = _adapter(service)
    folder = _folder(tmp_path)
    files = {"video": folder / "assets" / "presenter.mp4", "audio": folder / "assets" / "line.wav"}
    params = {"model": "lipsync-2", "presenter": "video"}
    out = tmp_path / "out.bin"
    _Service.refuse = 401
    calls = (
        lambda: adapter.submit(params, files, SYNC_SECRET),
        lambda: adapter.collect("gen-1", SYNC_SECRET, out),
        lambda: adapter.run(params, files, SYNC_SECRET, out),
    )
    for call in calls:
        with pytest.raises(ProviderError) as caught:
            call()
        assert caught.value.status == 401 and caught.value.__cause__ is None
        assert SYNC_SECRET not in _locals_of(caught.value)
        failure = engine.provider_failure(caught.value)
        assert SYNC_SECRET not in json.dumps([failure.data, [[error.message, error.hint] for error in failure.errors]])
    assert not out.exists()
    _Service.refuse = 0
    with pytest.raises(ProviderError) as nothing:
        adapter.submit(params, {"video": files["video"]}, SYNC_SECRET)
    assert nothing.value.status == 0 and SYNC_SECRET not in _locals_of(nothing.value)
    with pytest.raises(ProviderError) as gone:
        adapter.submit(params, {**files, "video": folder / "assets" / "nobody.mp4"}, SYNC_SECRET)
    assert gone.value.status == 0 and len(_sent("POST", "/v2/generate")) == 2
    with pytest.raises(ProviderError) as slow:
        adapter.run(params, files, SYNC_SECRET, out)
    assert SYNC_SECRET not in _locals_of(slow.value) and len(_sent("POST", "/v2/generate")) == 3


def test_frames_of_a_lip_synced_clip_carry_the_tag_and_its_fallback_does_not(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)
    made = tmp_path / "made.mp4"
    made.write_bytes(_clip_bytes(tmp_path, "red", 2))

    def frames(tag: str) -> tuple[bytes, bytes]:
        raw = _script(_lip(), RECORDED, disclosure=tag)
        script, timeline, _taken = engine.studied(raw, folder, require_files=True, offline=False)
        assert script.disclosure == tag and timeline.disclosure == tag
        canvas = canvas_for(timeline, folder)
        prepare(timeline, canvas, tmp_path / tag.replace(" ", "-"), folder, {1: Made(made, False)})
        return frame_at(timeline, 2, canvas).tobytes(), frame_at(timeline, 8, canvas).tobytes()

    card, tagged = frames(TAG)
    other_card, other_tagged = frames("Lips moved by a model")
    assert card == other_card and tagged != other_tagged
    shown = Image.frombytes("RGB", (160, 90), tagged)
    red, _green, blue = shown.getpixel((80, 70))
    assert red > 200 and blue < 60
    assert shown.crop((80, 0, 160, 20)).tobytes() != Image.new("RGB", (80, 20), shown.getpixel((80, 70))).tobytes()


TEMPLATE: dict[str, object] = {
    "templateVersion": 1,
    "id": "presenter-line",
    "title": "A presenter says one line",
    "disclosure": TAG,
    "inputs": {
        "presenter": {"type": "video", "required": True},
        "face": {"type": "image", "required": True},
        "line": {"type": "text", "required": True},
        "voice": {"type": "voice", "default": "eve"},
    },
    "output": {"path": "out/presenter.mp4", "width": 160, "height": 90, "fps": 5},
    "speech": {"provider": "xai-speech", "model": "tts", "voice": "{{voice}}"},
    "scenes": [
        {
            "id": "talk",
            "show": {
                "type": "lip_sync",
                "provider": "sync-lipsync",
                "model": "lipsync-2",
                "video": "{{presenter}}",
                "voice": "voice",
                "duration": 2,
                "fallback": {**MOUTH, "sprite": "{{face}}"},
            },
            "say": [{"at": 0.2, "duration": 1.5, "text": "{{line}}"}],
        }
    ],
}
INPUTS = {
    "template": "presenter-line",
    "inputs": {"presenter": "assets/presenter.mp4", "face": "assets/still.png", "line": "Hello and welcome."},
}


def test_a_template_asks_for_lip_sync_with_slots_and_needs_disclosure(tmp_path: Path, no_network: None) -> None:
    template, problems = load_template(TEMPLATE)
    assert template is not None and problems == ()
    made = expand(template, INPUTS, None)
    assert made.script is not None and made.problems == ()
    tracks = made.script["tracks"]
    assert isinstance(tracks, list)
    clip = tracks[0]["clips"][0]
    assert (clip["video"], clip["fallback"]["sprite"]) == ("assets/presenter.mp4", "assets/still.png")
    assert tracks[1]["clips"][0]["text"] == "Hello and welcome." and made.script["disclosure"] == TAG
    folder = _folder(tmp_path)
    estimate = engine.estimate_data(dict(made.script), folder, {"AMBITIOUS_HOME": str(tmp_path / "home")})
    assert [(line["step"], line["units"], line["usd"]) for line in _rows(estimate, "breakdown")] == [
        ("lip_sync", "10", "0.020000"),
        ("speech", "18", "0.000270"),
    ]
    assert engine.validate_data(dict(made.script), folder)["ok"] is True
    silent, missing = load_template({name: value for name, value in TEMPLATE.items() if name != "disclosure"})
    assert silent is None and [(item.at, item.message) for item in missing] == [
        ("", "A generated scene needs disclosure text.")
    ]
    wrong = json.loads(json.dumps(TEMPLATE).replace('"{{presenter}}"', '"{{face}}"'))
    refused, why = load_template(wrong)
    assert refused is None and [item.at for item in why] == ["/scenes/0/show/video"]
    for name in ("announcement", "short-explainer", "welcome"):
        starter = (ROOT / "templates" / f"{name}.json").read_text(encoding="utf-8")
        assert "lip_sync" not in starter


def test_the_fallback_of_a_lip_sync_is_the_presenter_or_the_mouth() -> None:
    def fallen(lip: Mapping[str, object]) -> tuple[str, object]:
        script, problems = load_script(_script(lip))
        assert script is not None and problems == ()
        clip = script.tracks[0].clips[1]  # type: ignore[union-attr] # the first track holds clips
        assert isinstance(clip, LipSync)
        found = fallback_of(clip)
        assert found.instead_of is clip
        return found.name, found.clip

    name, shown = fallen(_lip())
    assert (
        name == "none"
        and isinstance(shown, VideoClip)
        and (shown.path, shown.start, shown.end)
        == (
            "assets/presenter.mp4",
            0.0,
            2.0,
        )
    )
    name, still = fallen(_lip(video=None, image="assets/still.png", model="sync-3", fallback={"type": "none"}))
    assert name == "none" and isinstance(still, ImageClip) and (still.path, still.duration) == ("assets/still.png", 2.0)
    name, mouth = fallen(_lip(fallback=MOUTH))
    assert name == "mouth" and isinstance(mouth, LocalMouth) and (mouth.duration, mouth.voice) == (2.0, "voice")
    assert (mouth.shape.sprite, mouth.shape.color) == ("assets/still.png", "#1E1014")


def test_the_lip_sync_example_plans_and_prices(no_network: None, tmp_path: Path) -> None:
    raw = json.loads((ROOT / "examples" / "lip-sync.json").read_text(encoding="utf-8"))
    env = {"AMBITIOUS_HOME": str(tmp_path / "home")}
    data = engine.estimate_data(raw, ROOT / "examples", env)
    lip = _rows(data, "breakdown")[1]
    assert (lip["provider"], lip["model"], lip["unit"], lip["units"], lip["usd"]) == (
        "sync-lipsync",
        "sync-3",
        "frame",
        "150",
        "0.798000",
    )
    assert data["durationSec"] == 8 and [entry["env"] for entry in _rows(data, "secretsNeeded")] == [
        "SYNC_API_KEY",
        "XAI_API_KEY",
    ]
    assert engine.validate_data(raw, ROOT / "examples")["ok"] is True
    offline = engine.plan_data(raw, ROOT / "examples", offline=True)
    assert [step.get("fallback") for step in _rows(offline, "steps")] == [None, "mouth", "captions"]
    assert engine.estimate_data(raw, ROOT / "examples", env, offline=True)["usd"] == "0.000000"
    assert not (tmp_path / "home").exists()
