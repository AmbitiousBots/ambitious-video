from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Final

import pytest
import yaml
from ambitious_kit.db.sqlite import Db, load_migrations, migrate, open_db
from ambitious_kit.domain.envelope import Failure, JsonObject
from PIL import Image

from ambitious_video import providers
from ambitious_video.cli import engine
from ambitious_video.db.store import list_ledger, read_job
from ambitious_video.domain.fallback import fallback_of
from ambitious_video.domain.load import load_script
from ambitious_video.domain.money import Price
from ambitious_video.domain.template import expand, load_template
from ambitious_video.domain.types import GeneratedScene, ImageClip, PaidClip, TitleCard, VideoClip
from ambitious_video.media.film import Made, canvas_for, frame_at, prepare
from ambitious_video.providers.base import Patience, Provider, ProviderError, RequestLost, RunResult
from ambitious_video.providers.xai_image import XaiImage, XaiImageConfig
from ambitious_video.providers.xai_video import XaiVideo, XaiVideoConfig

ROOT = Path(__file__).resolve().parents[1]
STILL = ROOT / "examples" / "assets" / "still.png"
MIGRATIONS = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")
SECRET = "not-a-real-key-5566778899"
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 4, 1, tzinfo=UTC)
TAG = "AI-generated scenes"
MANIFEST_STEP = {"index", "type", "stepKey", "provider", "model", "paid", "cached", "usd"}


def _clip_bytes(folder: Path, color: str = "red") -> bytes:
    path = folder / f"made-{color}.mp4"
    source = f"color=c={color}:size=64x36:rate=5:duration=2"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, stdin=subprocess.DEVNULL, check=True)
    return path.read_bytes()


def _picture_bytes(color: tuple[int, int, int] = (20, 160, 60)) -> bytes:
    held = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(held, format="PNG")
    return held.getvalue()


class _Service(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str | None, bytes]] = []
    video = b""
    picture = b""
    status = "pending"
    refuse = 0

    def _answer(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)

    def _noted(self, method: str, body: bytes) -> bool:
        kind = type(self)
        kind.seen.append((method, self.path, self.headers.get("Authorization"), body))
        if kind.refuse:
            self._answer(json.dumps({"error": "refused " + SECRET}).encode(), kind.refuse)
        return kind.refuse == 0

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self._noted("POST", body):
            return
        kind = type(self)
        if self.path == "/v1/images/generations":
            encoded = base64.b64encode(kind.picture).decode()
            self._answer(json.dumps({"data": [{"b64_json": encoded}]}).encode())
            return
        sent = sum(1 for method, path, _key, _body in kind.seen if (method, path) == ("POST", self.path))
        self._answer(json.dumps({"request_id": f"req-{sent}"}).encode())

    def do_GET(self) -> None:
        if not self._noted("GET", b""):
            return
        kind = type(self)
        if self.path.startswith("/v1/videos/hop"):
            self.send_response(302)
            self.send_header("Location", "/elsewhere/" + self.path.rsplit("/", 1)[-1])
            self.end_headers()
            return
        if self.path.startswith("/files/"):
            self.send_response(302)
            self.send_header("Location", "/download")
            self.send_header("Content-Length", "268435457")
            self.end_headers()
            return
        if self.path == "/download":
            self._answer(kind.video)
            return
        name = self.path.rsplit("/", 1)[-1]
        if kind.status != "done":
            self._answer(json.dumps({"status": kind.status}).encode())
            return
        address = f"http://127.0.0.1:{self.server.server_address[1]}/files/{name}.mp4"
        self._answer(json.dumps({"status": "done", "video": {"url": address, "duration": 2}}).encode())

    def log_message(self, *_args: object) -> None:
        return None


def _sent(method: str, prefix: str) -> list[tuple[str, str, str | None, bytes]]:
    return [row for row in _Service.seen if row[0] == method and row[1].startswith(prefix)]


@pytest.fixture
def service(tmp_path: Path) -> Iterator[str]:
    _Service.seen = []
    _Service.video = _clip_bytes(tmp_path)
    _Service.picture = _picture_bytes()
    _Service.status = "pending"
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


def _video_adapter(base: str, wait: float = 0.15) -> XaiVideo:
    return XaiVideo(
        XaiVideoConfig(
            submit_url=base + "/v1/videos/generations",
            status_url=base + "/v1/videos/",
            download_schemes=("http",),
            timeout_seconds=5,
            poll_seconds=0.02,
            wait_seconds=wait,
        )
    )


@pytest.fixture
def local_xai(service: str) -> Iterator[str]:
    kept = [providers.find("xai-video"), providers.find("xai-image")]
    providers.unregister("xai-video")
    providers.unregister("xai-image")
    providers.register(_video_adapter(service))
    providers.register(XaiImage(XaiImageConfig(endpoint=service + "/v1/images/generations", timeout_seconds=5)))
    try:
        yield service
    finally:
        providers.unregister("xai-video")
        providers.unregister("xai-image")
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


def _scene(**changes: object) -> dict[str, object]:
    clip: dict[str, object] = {
        "type": "generated_scene",
        "provider": "xai-video",
        "model": "grok-imagine-video",
        "prompt": "A quiet street at sunrise, no people, no text",
        "duration": 2,
        "fallback": {"type": "image", "path": "assets/still.png"},
    }
    clip.update(changes)
    return {name: value for name, value in clip.items() if value is not None}


def _script(*scenes: Mapping[str, object], disclosure: str | None = TAG) -> dict[str, object]:
    card = {"type": "title_card", "duration": 1.0, "text": "Example Co."}
    script: dict[str, object] = {
        "schemaVersion": 2,
        "output": {"path": "out/scenes.mp4", "width": 160, "height": 90, "fps": 5},
        "tracks": [{"id": "main", "clips": [card, *scenes]}],
    }
    if disclosure is not None:
        script["disclosure"] = disclosure
    return script


def _folder(dest: Path) -> Path:
    folder = dest / "project"
    (folder / "assets").mkdir(parents=True)
    shutil.copy(STILL, folder / "assets" / "still.png")
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


def test_a_scene_that_times_out_is_polled_again_and_never_sent_twice(tmp_path: Path, local_xai: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_scene())
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    plan = engine.plan_data(script, folder)
    key = next(str(step["stepKey"]) for step in _rows(plan, "steps") if step["type"] == "generated_scene")
    db = _open(home)
    try:
        with pytest.raises(Failure) as waiting:
            _render(db, script, folder, "job-one", FIRST, env)
        assert [error.code for error in waiting.value.errors] == ["E_PENDING"]
        assert waiting.value.data == {
            "jobId": "job-one",
            "index": 1,
            "stepKey": key,
            "provider": "xai-video",
            "requestId": "req-1",
        }
        job = read_job(db, "job-one")
        assert job is not None and job.state == "failed"
        assert len(_sent("POST", "/v1/videos/generations")) == 1 and len(_sent("GET", "/v1/videos/req-1")) >= 1
        assert _requests(db) == [(key, "xai-video", "req-1")] and list_ledger(db) == ()
        assert not (folder / "out" / "scenes.mp4").exists()
        states = [entry["state"] for entry in _rows(engine.cache_list(db, None), "entries")]
        assert states == ["calling"]

        with pytest.raises(Failure) as other:
            _render(db, script, folder, "job-two", FIRST, env)
        assert [error.code for error in other.value.errors] == ["E_PENDING"]
        assert other.value.data["requestId"] == "req-1" and len(_sent("POST", "/v1/videos/generations")) == 1

        _Service.status = "done"
        retried = _render(db, "job-one", folder, "", LATER, env)
        assert len(_sent("POST", "/v1/videos/generations")) == 1
        assert {path for _method, path, _key, _body in _sent("GET", "/v1/videos/")} == {"/v1/videos/req-1"}
        (step,) = _rows(retried, "steps")
        assert (step["stepKey"], step["cached"], step["usd"]) == (key, False, "0.100000")
        ledger = list_ledger(db)
        assert [(line.job_id, line.idem_key, line.units, line.usd) for line in ledger] == [
            ("job-one", key, "2", "0.100000")
        ]
        assert retried["costUsd"] == "0.100000" and _requests(db) == []
        done = read_job(db, "job-one")
        assert done is not None and done.state == "rendered"

        calls = len(_Service.seen)
        again = _render(db, script, folder, "job-three", LATER, {"AMBITIOUS_HOME": str(home)})
        assert len(_Service.seen) == calls and list_ledger(db) == ledger
        (cached,) = _rows(again, "steps")
        assert (cached["stepKey"], cached["cached"], cached["usd"]) == (key, True, "0.000000")
        assert again["costUsd"] == "0.000000" and again["sha256"] == retried["sha256"]
        estimate = engine.estimate_data(script, folder, {"AMBITIOUS_HOME": str(home)})
        assert estimate["usd"] == "0.000000" and estimate["secretsNeeded"] == []
    finally:
        db.close()
    keyed = [row for row in _Service.seen if row[2] is not None]
    assert keyed and all(row[2] == "Bearer " + SECRET for row in keyed)
    assert all(row[2] is None for row in _sent("GET", "/files/"))
    assert SECRET.encode() not in _everything_written(home, folder)
    assert SECRET not in json.dumps([waiting.value.data, retried, again, estimate, plan])


def test_a_generation_the_provider_gives_up_on_is_forgotten_and_not_billed(tmp_path: Path, local_xai: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_scene())
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    _Service.status = "failed"
    db = _open(home)
    try:
        with pytest.raises(Failure) as lost:
            _render(db, script, folder, "job-one", FIRST, env)
        assert [error.code for error in lost.value.errors] == ["E_PROVIDER"]
        assert lost.value.data["provider"] == "xai-video"
        assert _requests(db) == [] and list_ledger(db) == ()
        assert _rows(engine.cache_list(db, None), "entries") == []
        _Service.status = "done"
        made = _render(db, script, folder, "job-two", LATER, env)
        assert len(_sent("POST", "/v1/videos/generations")) == 2 and len(list_ledger(db)) == 1
        assert made["costUsd"] == "0.100000"
    finally:
        db.close()


def test_a_poll_that_fails_keeps_the_request_for_the_retry(tmp_path: Path, local_xai: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_scene())
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    db = _open(home)
    try:
        with pytest.raises(Failure):
            _render(db, script, folder, "job-one", FIRST, env)
        for status in (503, 401, 429):
            _Service.refuse = status
            with pytest.raises(Failure) as refused:
                _render(db, "job-one", folder, "", LATER, env)
            (error,) = refused.value.errors
            assert error.code == "E_PROVIDER" and "--resend" in error.hint
            data = refused.value.data
            assert (data["provider"], data["status"], data["requestId"]) == ("xai-video", status, "req-1")
            assert [request for _key, _provider, request in _requests(db)] == ["req-1"] and list_ledger(db) == ()
        _Service.refuse = 0
        _Service.status = "done"
        _render(db, "job-one", folder, "", LATER, env)
        assert len(_sent("POST", "/v1/videos/generations")) == 1 and len(list_ledger(db)) == 1
    finally:
        db.close()
    assert SECRET not in json.dumps(refused.value.data) + refused.value.errors[0].hint


@pytest.mark.parametrize("status", [404, 410])
def test_a_stored_request_the_provider_no_longer_knows_is_sent_again(
    tmp_path: Path, local_xai: str, status: int
) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_scene())
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    db = _open(home)
    try:
        with pytest.raises(Failure) as waiting:
            _render(db, script, folder, "job-one", FIRST, env)
        assert [error.code for error in waiting.value.errors] == ["E_PENDING"]
        assert [request for _key, _provider, request in _requests(db)] == ["req-1"]
        _Service.refuse = status
        with pytest.raises(Failure) as lost:
            _render(db, "job-one", folder, "", LATER, env)
        (error,) = lost.value.errors
        assert error.code == "E_PROVIDER" and "Render again" in error.hint
        assert lost.value.data["provider"] == "xai-video"
        assert _requests(db) == [] and list_ledger(db) == ()
        assert _rows(engine.cache_list(db, None), "entries") == []
        assert len(_sent("POST", "/v1/videos/generations")) == 1
        _Service.refuse = 0
        _Service.status = "done"
        made = _render(db, script, folder, "job-two", LATER, env)
        assert len(_sent("POST", "/v1/videos/generations")) == 2
        assert _sent("GET", "/v1/videos/")[-1][1] == "/v1/videos/req-2"
        assert made["costUsd"] == "0.100000" and len(list_ledger(db)) == 1 and _requests(db) == []
    finally:
        db.close()


def test_resend_drops_a_stored_request_only_when_the_owner_asks(tmp_path: Path, local_xai: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    script = _script(_scene())
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    db = _open(home)
    try:
        with pytest.raises(Failure):
            _render(db, script, folder, "job-one", FIRST, env)
        with pytest.raises(Failure) as again:
            _render(db, "job-one", folder, "", LATER, env)
        assert again.value.data["requestId"] == "req-1" and len(_sent("POST", "/v1/videos/generations")) == 1
        with pytest.raises(Failure) as resent:
            _render(db, "job-one", folder, "", LATER, env, resend=True)
        assert [error.code for error in resent.value.errors] == ["E_PENDING"]
        assert resent.value.data["requestId"] == "req-2" and len(_sent("POST", "/v1/videos/generations")) == 2
        assert [request for _key, _provider, request in _requests(db)] == ["req-2"] and list_ledger(db) == ()
        _Service.status = "done"
        made = _render(db, "job-one", folder, "", LATER, env)
        assert len(_sent("POST", "/v1/videos/generations")) == 2 and _requests(db) == []
        assert made["costUsd"] == "0.100000" and len(list_ledger(db)) == 1
        calls = len(_Service.seen)
        cached = _render(db, script, folder, "job-two", LATER, env, resend=True)
        assert len(_Service.seen) == calls and cached["costUsd"] == "0.000000" and len(list_ledger(db)) == 1
    finally:
        db.close()


def test_every_scene_is_sent_before_the_first_one_is_waited_for(tmp_path: Path, local_xai: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    other = _scene(prompt="A harbour at dusk, no people, no text")
    script = _script(_scene(), other, _scene())
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    db = _open(home)
    try:
        with pytest.raises(Failure) as waiting:
            _render(db, script, folder, "job-one", FIRST, env)
        assert [error.code for error in waiting.value.errors] == ["E_PENDING"]
        assert waiting.value.data["requestId"] == "req-1"
        assert len(_sent("POST", "/v1/videos/generations")) == 2
        assert sorted(request for _key, _provider, request in _requests(db)) == ["req-1", "req-2"]
        assert _sent("GET", "/v1/videos/req-2") == [] and list_ledger(db) == ()
        _Service.status = "done"
        made = _render(db, "job-one", folder, "", LATER, env)
        assert len(_sent("POST", "/v1/videos/generations")) == 2 and _requests(db) == []
        assert made["costUsd"] == "0.200000" and len(list_ledger(db)) == 2
        assert [step["cached"] for step in _rows(made, "steps")] == [False, False, True]
    finally:
        db.close()


def test_bytes_that_are_not_a_video_are_not_stored_or_billed(tmp_path: Path, local_xai: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    _Service.status = "done"
    _Service.video = b"not a video"
    _Service.picture = b"not a picture"
    still = _scene(provider="xai-image", model="grok-imagine-image")
    db = _open(home)
    try:
        for job, script in (("job-one", _script(_scene())), ("job-two", _script(still))):
            with pytest.raises(Failure) as lost:
                _render(db, script, folder, job, FIRST, env)
            assert [error.code for error in lost.value.errors] == ["E_PROVIDER"]
        assert _requests(db) == [] and list_ledger(db) == ()
        assert _rows(engine.cache_list(db, None), "entries") == []
    finally:
        db.close()


def test_a_still_and_a_moving_scene_are_made_once_and_cached_across_jobs(tmp_path: Path, local_xai: str) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    still = _scene(provider="xai-image", model="grok-imagine-image-2.0", kenburns={"from": 1.0, "to": 1.2})
    script = _script(_scene(), still)
    env = {"AMBITIOUS_HOME": str(home), "XAI_API_KEY": SECRET}
    _Service.status = "done"
    db = _open(home)
    try:
        first = _render(db, script, folder, "job-one", FIRST, env)
        steps = _rows(first, "steps")
        assert [(step["provider"], step["model"], step["usd"], step["cached"]) for step in steps] == [
            ("xai-video", "grok-imagine-video", "0.100000", False),
            ("xai-image", "grok-imagine-image-2.0", "0.040000", False),
        ]
        assert first["costUsd"] == "0.140000"
        assert [line.idem_key for line in list_ledger(db)] == [step["stepKey"] for step in steps]
        (asked,) = _sent("POST", "/v1/images/generations")
        assert json.loads(asked[3]) == {
            "model": "grok-imagine-image-2.0",
            "prompt": "A quiet street at sunrise, no people, no text",
            "n": 1,
            "response_format": "b64_json",
        }
        calls, ledger = len(_Service.seen), list_ledger(db)
        second = _render(db, script, folder, "job-two", LATER, {"AMBITIOUS_HOME": str(home)})
        assert len(_Service.seen) == calls and list_ledger(db) == ledger
        assert all(step["cached"] is True and step["usd"] == "0.000000" for step in _rows(second, "steps"))
        assert second["costUsd"] == "0.000000" and second["sha256"] == first["sha256"]
        db.execute("BEGIN IMMEDIATE")
        checked = engine.verify_job(db, "job-two", LATER)
        db.execute("COMMIT")
        assert checked["passed"] is True
        entries = _rows(engine.cache_list(db, None), "entries")
        kinds = {(entry["provider"], entry["kind"], entry["state"]) for entry in entries}
        assert kinds == {("xai-video", "scene", "ready"), ("xai-image", "scene", "ready")}
    finally:
        db.close()
    assert SECRET.encode() not in _everything_written(home, folder)


def test_estimates_price_a_scene_per_second_and_a_still_per_picture(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)
    env = {"AMBITIOUS_HOME": str(tmp_path / "home")}
    rates = {"grok-imagine-video": "0.05", "grok-imagine-video-1.5": "0.08", "grok-imagine-video-1.5-lite": "0.02"}
    for model, rate in rates.items():
        data = engine.estimate_data(_script(_scene(model=model, duration=8)), folder, env)
        (line,) = [row for row in _rows(data, "breakdown") if "provider" in row]
        assert (line["step"], line["provider"], line["model"]) == ("generated_scene", "xai-video", model)
        assert (line["unit"], line["units"], line["usdPerUnit"]) == ("second", "8", rate)
        assert Decimal(str(line["usd"])) == 8 * Decimal(rate) and len(str(line["usd"]).partition(".")[2]) == 6
        assert (line["sourceUrl"], line["checkedAt"]) == ("https://docs.x.ai/docs/models", "2026-11-16")
        assert line["cached"] is False and data["usd"] == line["usd"]
        assert data["secretsNeeded"] == [
            {"env": "XAI_API_KEY", "provider": "xai-video", "steps": [1], "usd": line["usd"]}
        ]
    four = [_scene(prompt=f"Scene {number}", duration=8) for number in range(4)]
    assert engine.estimate_data(_script(*four), folder, env)["usd"] == "1.600000"
    for model, rate in {"grok-imagine-image": "0.02", "grok-imagine-image-2.0": "0.04"}.items():
        still = _scene(provider="xai-image", model=model, duration=6)
        priced = engine.estimate_data(_script(still), folder, env)
        (line,) = [row for row in _rows(priced, "breakdown") if "provider" in row]
        assert (line["unit"], line["units"], line["usdPerUnit"]) == ("image", "1", rate)
        assert Decimal(str(line["usd"])) == Decimal(rate)


def test_only_what_is_sent_to_the_provider_changes_the_step_key(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)

    def keys(script: Mapping[str, object]) -> tuple[str, str]:
        plan = engine.plan_data(dict(script), folder)
        output = plan["output"]
        assert isinstance(output, dict)
        return str(_rows(plan, "steps")[1]["stepKey"]), str(output["stepKey"])

    plain = keys(_script(_scene()))
    assert keys(_script(_scene())) == plain
    assert keys(_script(_scene(fallback={"type": "title_card", "text": "Open every day"})))[0] == plain[0]
    for change in ({"prompt": "Another street"}, {"duration": 3}, {"resolution": "720p"}, {"aspect_ratio": "9:16"}):
        assert keys(_script(_scene(**change)))[0] != plain[0]
    assert keys(_script(_scene(model="grok-imagine-video-1.5")))[0] != plain[0]
    retagged = keys(_script(_scene(), disclosure="Made with AI"))
    assert retagged[0] == plain[0] and retagged[1] != plain[1]

    still = {"provider": "xai-image", "model": "grok-imagine-image"}
    slow = keys(_script(_scene(**still, kenburns={"from": 1.0, "to": 1.1})))
    fast = keys(_script(_scene(**still, kenburns={"from": 1.0, "to": 1.5})))
    assert slow[0] == fast[0] and slow[1] != fast[1]

    shutil.copy(STILL, folder / "assets" / "reference.png")
    with_image = keys(_script(_scene(image="assets/reference.png")))
    assert with_image[0] != plain[0]
    Image.new("RGB", (32, 32), (200, 10, 10)).save(folder / "assets" / "reference.png")
    assert keys(_script(_scene(image="assets/reference.png")))[0] != with_image[0]
    plan = engine.plan_data(_script(_scene(image="assets/reference.png")), folder)
    assert {"assets/reference.png", "assets/still.png"} <= {str(asset["path"]) for asset in _rows(plan, "assets")}


def test_offline_shows_the_fallback_for_the_same_time_and_costs_nothing(tmp_path: Path, no_network: None) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    still = _scene(provider="xai-image", model="grok-imagine-image", fallback={"type": "title_card", "text": "Open"})
    script = _script(_scene(), still)
    env = {"AMBITIOUS_HOME": str(home)}
    paid, free = engine.plan_data(script, folder), engine.plan_data(script, folder, offline=True)
    assert paid["durationSec"] == free["durationSec"] == 5 and paid["planSha256"] != free["planSha256"]
    assert [(step["atFrame"], step["frames"]) for step in _rows(paid, "steps")] == [
        (step["atFrame"], step["frames"]) for step in _rows(free, "steps")
    ]
    marks = [(step["type"], step["paid"], step.get("fallback")) for step in _rows(free, "steps")]
    assert marks == [("title_card", False, None), ("image", False, "image"), ("title_card", False, "title_card")]
    lost = _rows(free, "steps")[1]["fallbackFor"]
    assert lost == {"type": "generated_scene", "provider": "xai-video", "model": "grok-imagine-video"}
    estimate = engine.estimate_data(script, folder, env, offline=True)
    assert estimate["usd"] == "0.000000" and estimate["secretsNeeded"] == []
    db = _open(home)
    try:
        with pytest.raises(Failure) as refused:
            _render(db, script, folder, "job-zero", FIRST, env)
        assert [error.code for error in refused.value.errors] == ["E_NO_KEY"] and list_ledger(db) == ()
        one = _render(db, script, folder, "job-one", FIRST, env, offline=True)
        two = _render(db, script, folder, "job-two", LATER, env, offline=True)
        assert one["costUsd"] == "0.000000" and one["sha256"] == two["sha256"] and list_ledger(db) == ()
        assert [step["fallback"] for step in _rows(one, "steps")] == ["image", "title_card"]
        db.execute("BEGIN IMMEDIATE")
        checked = engine.verify_job(db, "job-two", LATER)
        db.execute("COMMIT")
        assert checked["passed"] is True
    finally:
        db.close()


def test_a_fallback_takes_the_time_of_its_scene() -> None:
    def loaded(fallback: Mapping[str, object]) -> GeneratedScene:
        script, problems = load_script(_script(_scene(duration=3, fallback=dict(fallback))))
        assert script is not None and problems == ()
        clip = script.tracks[0].clips[1]  # type: ignore[union-attr] # the first track holds clips
        assert isinstance(clip, GeneratedScene)
        return clip

    card = loaded({"type": "title_card", "text": "Open", "subtitle": "Every day"})
    assert fallback_of(card).clip == TitleCard(3, "Open", "Every day") and fallback_of(card).name == "title_card"
    picture = loaded({"type": "image", "path": "assets/still.png", "kenburns": {"from": 1, "to": 1.1}})
    shown = fallback_of(picture).clip
    assert isinstance(shown, ImageClip) and shown.duration == 3 and shown.kenburns is not None
    part = loaded({"type": "video", "path": "assets/broll.mp4", "in": 2})
    assert fallback_of(part).clip == VideoClip("assets/broll.mp4", 2, 5)  # type: ignore[arg-type] # a plain path
    assert fallback_of(part).instead_of is part


def test_a_scene_the_provider_cannot_make_is_refused_before_anything_runs(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)
    still = {"provider": "xai-image", "model": "grok-imagine-image"}
    at = "/tracks/0/clips/1"
    cases: list[tuple[dict[str, object], str, str]] = [
        (_scene(seed=7), "/seed", "takes no seed"),
        (_scene(kenburns={"from": 1.0, "to": 1.1}), "/kenburns", "still picture"),
        (_scene(duration=20), "/duration", "1 to 15"),
        (_scene(duration=2.5), "/duration", "whole seconds"),
        (_scene(resolution="2k"), "/resolution", "480p"),
        (_scene(aspect_ratio="7:1"), "/aspect_ratio", "no such shape"),
        (_scene(**still, resolution="720p"), "/resolution", "1k or 2k"),
        (_scene(**still, image="assets/still.png"), "/image", "takes no image"),
        (_scene(provider="xai-speech", model="tts"), "/provider", "does not do this step"),
        (_scene(model="grok-imagine-image"), "/model", "no such model"),
        (_scene(provider="no-such-maker"), "/provider", "No provider"),
    ]
    for clip, field, words in cases:
        found = _problems(_script(clip), folder)
        assert [(place, words in message) for place, message in found] == [(at + field, True)]
    missing = _problems(_script(_scene(), disclosure=None), folder)
    assert missing == [("", "A generated scene needs disclosure text.")]
    shapes: list[dict[str, object]] = [
        _scene(fallback=None),
        _scene(fallback={"type": "title_card", "text": "Open", "duration": 2}),
        _scene(fallback={"type": "audio", "path": "assets/still.png"}),
        _scene(seed=-1),
        _scene(resolution="large"),
        _scene(api_key=SECRET),
    ]
    for clip in shapes:
        assert all(place.startswith(at) for place, _message in _problems(_script(clip), folder))


def test_validate_checks_the_reference_picture_and_the_fallback_file(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)
    at = "/tracks/0/clips/1"
    assert engine.validate_data(_script(_scene()), folder)["ok"] is True
    gone = _problems(_script(_scene(fallback={"type": "image", "path": "assets/none.png"})), folder, files=True)
    assert gone == [(at, "The image file is missing.")]
    part = _problems(_script(_scene(fallback={"type": "video", "path": "assets/none.mp4"})), folder, files=True)
    assert part == [(at, "The video file is missing.")]
    (folder / "assets" / "short.mp4").write_bytes(_clip_bytes(tmp_path))
    short = _scene(duration=8, fallback={"type": "video", "path": "assets/short.mp4"})
    assert _problems(_script(short), folder, files=True) == [(at, "in or out is outside the file.")]
    fits = _scene(fallback={"type": "video", "path": "assets/short.mp4"})
    assert engine.validate_data(_script(fits), folder)["ok"] is True
    lost = _problems(_script(_scene(image="assets/none.png")), folder, files=True)
    assert lost == [(at + "/image", "The image file is missing.")]


class TestScene:
    __test__ = False
    kind: Final = "scene"
    id: Final = "test-scene"
    models: Final = ("draft",)
    secret: Final = "TEST_SCENE_KEY"
    seedable: Final = True
    adapter_version: Final = 3
    output: Final = "video"
    patience: Final = Patience(0.01, 0.05)

    def __init__(self, video: bytes) -> None:
        self.video = video
        self.submitted: list[JsonObject] = []
        self.polled: list[str] = []
        self.ready = False

    def faults(self, step: GeneratedScene) -> tuple[tuple[str, str], ...]:
        return (("prompt", "Say more."),) if len(step.prompt) < 4 else ()

    def params(self, step: PaidClip) -> JsonObject:
        assert isinstance(step, GeneratedScene)
        return {"model": step.model, "ask": step.prompt, "seconds": step.duration, "seed": step.seed}

    def price(self, model: str) -> Price:
        return Price(self.id, model, "second", Decimal("0.25"), "https://prices.example/test-scene", "2026-11-16")

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def submit(self, params: JsonObject, inputs: Mapping[str, Path], secret: str) -> str:
        assert secret == SECRET
        self.submitted.append({**params, "inputs": sorted(inputs)})
        return f"ticket-{len(self.submitted)}"

    def collect(self, request_id: str, secret: str, out: Path) -> RunResult | None:
        assert secret == SECRET
        self.polled.append(request_id)
        if not self.ready:
            return None
        out.write_bytes(self.video)
        return RunResult(out)

    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult:
        raise AssertionError("a provider that answers later is submitted and polled")


@pytest.fixture
def fake_scene(tmp_path: Path) -> Iterator[TestScene]:
    adapter = TestScene(_clip_bytes(tmp_path, "blue"))
    providers.register(adapter)
    try:
        yield adapter
    finally:
        providers.unregister(adapter.id)


TEMPLATE = {
    "templateVersion": 1,
    "id": "street-scene",
    "title": "A scene and a closing card",
    "inputs": {
        "place": {"type": "text", "required": True},
        "picture": {"type": "image", "required": True},
        "closing": {"type": "text", "required": True},
    },
    "output": {"path": "out/street.mp4", "width": 160, "height": 90, "fps": 5},
    "budget": {"maxUsd": 5},
    "disclosure": TAG,
    "scenes": [
        {
            "id": "scene",
            "show": {
                "type": "generated_scene",
                "provider": "test-scene",
                "model": "draft",
                "prompt": "A slow shot of {{place}}",
                "duration": 2,
                "seed": 11,
                "image": "{{picture}}",
                "fallback": {"type": "image", "path": "{{picture}}"},
            },
        }
    ],
    "endCard": {"duration": 1.0, "text": "{{closing}}"},
}
INPUTS = {
    "template": "street-scene",
    "inputs": {"place": "a market square", "picture": "assets/still.png", "closing": "See you soon"},
}


def test_a_template_fills_a_generated_scene_and_needs_disclosure(no_network: None, fake_scene: TestScene) -> None:
    template, problems = load_template(TEMPLATE)
    assert template is not None and problems == ()
    made = expand(template, INPUTS, None)
    assert made.script is not None and made.problems == ()
    tracks = made.script["tracks"]
    assert isinstance(tracks, list)
    clip = tracks[0]["clips"][0]
    assert (clip["prompt"], clip["image"]) == ("A slow shot of a market square", "assets/still.png")
    assert clip["fallback"] == {"type": "image", "path": "assets/still.png"}
    assert made.script["disclosure"] == TAG and made.script["template"] == {"id": "street-scene", "templateVersion": 1}
    silent, missing = load_template({name: value for name, value in TEMPLATE.items() if name != "disclosure"})
    assert silent is None and [(item.at, item.message) for item in missing] == [
        ("", "A generated scene needs disclosure text.")
    ]
    wrong = json.loads(json.dumps(TEMPLATE).replace("A slow shot of {{place}}", "{{picture}}"))
    refused, why = load_template(wrong)
    assert refused is None and [item.at for item in why] == ["/scenes/0/show/prompt"]


def test_every_render_writes_a_manifest_of_where_each_step_came_from(
    tmp_path: Path, no_network: None, fake_scene: TestScene
) -> None:
    folder, home = _folder(tmp_path), tmp_path / "home"
    template, _problems_found = load_template(TEMPLATE)
    assert template is not None
    script = expand(template, INPUTS, None).script
    assert script is not None
    quiet = {"AMBITIOUS_HOME": str(home)}
    env = {**quiet, "TEST_SCENE_KEY": SECRET}
    plan = engine.plan_data(script, folder)
    estimate = engine.estimate_data(script, folder, quiet)
    assert estimate["usd"] == "0.500000"
    owed = {"env": "TEST_SCENE_KEY", "provider": "test-scene", "steps": [0], "usd": "0.500000"}
    assert estimate["secretsNeeded"] == [owed]

    def manifest(data: Mapping[str, object]) -> dict[str, object]:
        path = Path(str(data["manifest"]))
        assert path == folder / "out" / "street.mp4.manifest.json"
        text = path.read_text(encoding="utf-8")
        found = json.loads(text)
        assert isinstance(found, dict) and text == json.dumps(found, sort_keys=True, indent=2) + "\n"
        return found

    db = _open(home)
    try:
        with pytest.raises(Failure) as waiting:
            _render(db, script, folder, "job-one", FIRST, env)
        assert [error.code for error in waiting.value.errors] == ["E_PENDING"]
        assert waiting.value.data["requestId"] == "ticket-1" and not (folder / "out").exists()
        fake_scene.ready = True
        first = _render(db, "job-one", folder, "", LATER, env)
        assert len(fake_scene.submitted) == 1 and set(fake_scene.polled) == {"ticket-1"}
        assert fake_scene.submitted[0] == {
            "model": "draft",
            "ask": "A slow shot of a market square",
            "seconds": 2,
            "seed": 11,
            "inputs": ["image"],
        }
        one = manifest(first)
        assert set(one) == {
            "manifestVersion",
            "jobId",
            "template",
            "planSha256",
            "offline",
            "disclosure",
            "costUsd",
            "output",
            "steps",
        }
        assert one["template"] == {"id": "street-scene", "templateVersion": 1}
        assert one["planSha256"] == plan["planSha256"] == first["planSha256"]
        assert (one["manifestVersion"], one["jobId"], one["offline"]) == (1, "job-one", False)
        assert (one["disclosure"], one["costUsd"]) == (TAG, "0.500000")
        planned = _rows(plan, "steps")
        assert one["output"] == {
            "file": "street.mp4",
            "sha256": first["sha256"],
            "stepKey": plan["output"]["stepKey"],  # type: ignore[index] # the plan has an output object
        }
        steps = _rows(one, "steps")
        assert all(set(step) == MANIFEST_STEP for step in steps)
        assert [(step["index"], step["stepKey"]) for step in steps] == [
            (step["index"], step["stepKey"]) for step in planned
        ]
        assert [(step["type"], step["provider"], step["model"], step["cached"], step["usd"]) for step in steps] == [
            ("generated_scene", "test-scene", "draft", False, "0.500000"),
            ("title_card", "local", None, False, "0.000000"),
        ]
        assert [line.idem_key for line in list_ledger(db)] == [steps[0]["stepKey"]]

        second = _render(db, script, folder, "job-two", LATER, quiet)
        two = manifest(second)
        assert len(fake_scene.submitted) == 1 and len(list_ledger(db)) == 1
        billed = [(step["cached"], step["usd"]) for step in _rows(two, "steps")]
        assert billed == [(True, "0.000000"), (False, "0.000000")]
        assert (two["costUsd"], two["jobId"], two["planSha256"]) == ("0.000000", "job-two", one["planSha256"])
        assert two["output"] == one["output"]

        free = manifest(_render(db, script, folder, "job-three", LATER, quiet, offline=True))
        assert free["offline"] is True and free["planSha256"] != one["planSha256"]
        fallen = _rows(free, "steps")[0]
        assert (fallen["type"], fallen["provider"], fallen["fallback"]) == ("image", "local", "image")
        assert fallen["fallbackFor"] == {"type": "generated_scene", "provider": "test-scene", "model": "draft"}

        plain = dict(_script(_scene(provider="test-scene", model="draft")))
        bare = manifest_of_plain = _render(db, plain, folder, "job-four", LATER, quiet, offline=True)
        assert json.loads(Path(str(bare["manifest"])).read_text(encoding="utf-8"))["template"] is None
        assert manifest_of_plain["manifest"].endswith("scenes.mp4.manifest.json")  # type: ignore[union-attr] # a path
    finally:
        db.close()
    assert SECRET.encode() not in _everything_written(home, folder)
    assert SECRET not in json.dumps([first, second, one, two, free])


def test_frames_of_a_generated_scene_carry_the_tag_and_other_frames_do_not(tmp_path: Path, no_network: None) -> None:
    folder = _folder(tmp_path)
    made = tmp_path / "made.png"
    made.write_bytes(_picture_bytes())
    still = _scene(provider="xai-image", model="grok-imagine-image")

    def frames(tag: str) -> tuple[bytes, bytes]:
        raw = _script(still, disclosure=tag)
        script, timeline, _taken = engine.studied(raw, folder, require_files=True, offline=False)
        assert script.disclosure == tag and timeline.disclosure == tag
        canvas = canvas_for(timeline, folder)
        prepare(timeline, canvas, tmp_path, folder, {1: Made(made, True)})
        return frame_at(timeline, 2, canvas).tobytes(), frame_at(timeline, 8, canvas).tobytes()

    card, tagged = frames(TAG)
    other_card, other_tagged = frames("Made with a model")
    assert card == other_card and tagged != other_tagged
    plain = Image.open(made).convert("RGB").resize((160, 90))
    shown = Image.frombytes("RGB", (160, 90), tagged)
    corner, rest = (80, 0, 160, 20), (0, 30, 160, 90)
    assert shown.crop(corner).tobytes() != plain.crop(corner).tobytes()
    assert shown.crop(rest).tobytes() == plain.crop(rest).tobytes()
    _script_off, offline, _taken = engine.studied(_script(still), folder, require_files=True, offline=True)
    assert offline.disclosure is None

    def faded(tag: str) -> bytes:
        raw = {**_script(still, disclosure=tag), "cuts": [{"at": 1.0, "transition": "crossfade", "duration": 0.4}]}
        _faded, timeline, _none = engine.studied(raw, folder, require_files=True, offline=False)
        canvas = canvas_for(timeline, folder)
        prepare(timeline, canvas, tmp_path, folder, {1: Made(made, True)})
        assert frame_at(timeline, 1, canvas).tobytes() == card
        return frame_at(timeline, 4, canvas).tobytes()

    assert faded(TAG) != faded("Made with a model")
    _card, long_tag = frames(" ".join(["generated"] * 20))
    wrapped = Image.frombytes("RGB", (160, 90), long_tag)
    assert wrapped.crop(rest).tobytes() != plain.crop(rest).tobytes()


def test_xai_video_submits_once_then_polls_and_downloads_without_the_key(tmp_path: Path, service: str) -> None:
    adapter = _video_adapter(service)
    listed: Provider = adapter
    assert (listed.kind, listed.id, listed.secret, listed.seedable) == ("scene", "xai-video", "XAI_API_KEY", False)
    assert adapter.models == ("grok-imagine-video", "grok-imagine-video-1.5", "grok-imagine-video-1.5-lite")
    assert XaiVideo().config.submit_url == "https://api.x.ai/v1/videos/generations"
    assert XaiVideo().config.status_url == "https://api.x.ai/v1/videos/" and XaiVideo().patience == Patience(5, 600)
    script, _none = load_script(_script(_scene(duration=8, resolution="720p", aspect_ratio="16:9")))
    assert script is not None
    clip = script.tracks[0].clips[1]  # type: ignore[union-attr] # the first track holds clips
    assert isinstance(clip, GeneratedScene)
    params = adapter.params(clip)
    assert params == {
        "model": "grok-imagine-video",
        "prompt": "A quiet street at sunrise, no people, no text",
        "duration": 8,
        "resolution": "720p",
        "aspect_ratio": "16:9",
    }
    assert adapter.quote(params, Decimal(8)) == Decimal("0.40")
    request_id = adapter.submit(params, {"image": STILL}, SECRET)
    (posted,) = _sent("POST", "/v1/videos/generations")
    sent = json.loads(posted[3])
    assert request_id == "req-1" and posted[2] == "Bearer " + SECRET and SECRET.encode() not in posted[3]
    picture = sent.pop("image")
    encoded = base64.b64encode(STILL.read_bytes()).decode()
    assert sent == params and picture == {"url": "data:image/png;base64," + encoded}
    out = tmp_path / "out.bin"
    assert adapter.collect(request_id, SECRET, out) is None and not out.exists()
    _Service.status = "done"
    found = adapter.collect(request_id, SECRET, out)
    assert found == RunResult(out) and out.read_bytes() == _Service.video
    assert [row[2] for row in _sent("GET", "/v1/videos/req-1")] == ["Bearer " + SECRET] * 2
    assert [row[2] for row in _sent("GET", "/files/")] == [None]
    assert [row[2] for row in _sent("GET", "/download")] == [None]
    _Service.status = "failed"
    with pytest.raises(RequestLost):
        adapter.collect(request_id, SECRET, out)
    with pytest.raises(RequestLost):
        adapter.collect("../other", SECRET, out)
    strict = XaiVideo(XaiVideoConfig(status_url=service + "/v1/videos/", timeout_seconds=5))
    _Service.status = "done"
    with pytest.raises(ProviderError) as unsafe:
        strict.collect(request_id, SECRET, tmp_path / "plain.bin")
    assert not isinstance(unsafe.value, RequestLost) and not (tmp_path / "plain.bin").exists()
    assert len(_sent("GET", "/files/")) == 1
    for status, lost in ((404, True), (410, True), (401, False), (403, False), (429, False), (500, False)):
        _Service.refuse = status
        with pytest.raises(ProviderError) as answered:
            adapter.collect(request_id, SECRET, out)
        assert (answered.value.status, isinstance(answered.value, RequestLost)) == (status, lost)
    _Service.refuse = 0
    with pytest.raises(ProviderError) as moved:
        adapter.collect("hop-1", SECRET, out)
    assert moved.value.status == 302 and not isinstance(moved.value, RequestLost)
    assert _sent("GET", "/elsewhere/") == [] and len(_sent("GET", "/v1/videos/hop-1")) == 1


def _locals_of(error: BaseException) -> str:
    parts = [str(error), repr(error.args), str(error.__cause__), str(error.__context__)]
    frame = error.__traceback__
    while frame is not None:
        parts.extend(repr(value) for value in frame.tb_frame.f_locals.values())
        frame = frame.tb_next
    return " ".join(parts)


def test_a_refused_scene_call_drops_the_key(tmp_path: Path, service: str) -> None:
    video = _video_adapter(service)
    still = XaiImage(XaiImageConfig(endpoint=service + "/v1/images/generations", timeout_seconds=5))
    out = tmp_path / "out.bin"
    _Service.refuse = 401
    calls = (
        lambda: video.submit({"model": "grok-imagine-video"}, {}, SECRET),
        lambda: video.collect("req-1", SECRET, out),
        lambda: video.run({"model": "grok-imagine-video"}, {}, SECRET, out),
        lambda: still.run({"model": "grok-imagine-image"}, {}, SECRET, out),
    )
    for call in calls:
        with pytest.raises(ProviderError) as caught:
            call()
        assert caught.value.status == 401 and caught.value.__cause__ is None
        assert SECRET not in _locals_of(caught.value)
        failure = engine.provider_failure(caught.value)
        assert SECRET not in json.dumps([failure.data, [[error.message, error.hint] for error in failure.errors]])
    assert not out.exists()
    _Service.refuse = 0
    with pytest.raises(ProviderError) as slow:
        video.run({"model": "grok-imagine-video"}, {}, SECRET, out)
    assert SECRET not in _locals_of(slow.value) and len(_sent("POST", "/v1/videos/generations")) == 3


def test_an_http_200_application_error_drops_the_echoed_key(tmp_path: Path, service: str) -> None:
    still = XaiImage(XaiImageConfig(endpoint=service + "/v1/images/generations"))
    _Service.refuse = 200
    out = tmp_path / "image"
    with pytest.raises(ProviderError) as caught:
        still.run({}, {}, SECRET, out)
    assert (caught.value.provider, caught.value.status) == (still.id, 0)
    assert caught.value.__context__ is None and SECRET not in _locals_of(caught.value)
    assert not out.exists()


def test_xai_image_asks_for_one_picture_and_writes_its_bytes(tmp_path: Path, service: str) -> None:
    adapter = XaiImage(XaiImageConfig(endpoint=service + "/v1/images/generations", timeout_seconds=5))
    assert (adapter.kind, adapter.id, adapter.output, adapter.secret) == ("scene", "xai-image", "image", "XAI_API_KEY")
    assert adapter.models == ("grok-imagine-image", "grok-imagine-image-2.0") and adapter.seedable is False
    assert XaiImage().config.endpoint == "https://api.x.ai/v1/images/generations"
    still = _scene(provider="xai-image", model="grok-imagine-image", resolution="2k", aspect_ratio="9:16")
    script, _none = load_script(_script(still))
    assert script is not None
    clip = script.tracks[0].clips[1]  # type: ignore[union-attr] # the first track holds clips
    assert isinstance(clip, GeneratedScene)
    params = adapter.params(clip)
    assert params == {
        "model": "grok-imagine-image",
        "prompt": "A quiet street at sunrise, no people, no text",
        "n": 1,
        "response_format": "b64_json",
        "resolution": "2k",
        "aspect_ratio": "9:16",
    }
    out = tmp_path / "out.bin"
    assert adapter.run(params, {}, SECRET, out) == RunResult(out) and out.read_bytes() == _Service.picture
    (posted,) = _sent("POST", "/v1/images/generations")
    assert json.loads(posted[3]) == params and posted[2] == "Bearer " + SECRET
    assert adapter.quote(params, Decimal(4)) == Decimal("0.08")


def test_the_example_with_generated_scenes_plans_and_prices(no_network: None, tmp_path: Path) -> None:
    raw = yaml.safe_load((ROOT / "examples" / "generated-scenes.yaml").read_text(encoding="utf-8"))
    env = {"AMBITIOUS_HOME": str(tmp_path / "home")}
    data = engine.estimate_data(raw, ROOT / "examples", env)
    assert data["usd"] == "0.420000" and data["durationSec"] == 16
    assert data["secretsNeeded"] == [
        {"env": "XAI_API_KEY", "provider": "xai-image, xai-video", "steps": [1, 2], "usd": "0.420000"}
    ]
    assert engine.validate_data(raw, ROOT / "examples")["ok"] is True
    assert engine.estimate_data(raw, ROOT / "examples", env, offline=True)["usd"] == "0.000000"
    assert not (tmp_path / "home").exists()
