from __future__ import annotations

import array
import base64
import io
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import wave
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Final

import ambitious_kit.cli.runner as runner
import pytest
import yaml
from ambitious_kit.db.sqlite import Db, load_migrations, migrate, open_db
from ambitious_kit.domain.envelope import Failure, JsonObject
from PIL import Image

from ambitious_video import providers
from ambitious_video.cli import engine, main
from ambitious_video.db.store import list_ledger
from ambitious_video.domain.fallback import CAPTIONS, fallback_of, with_fallbacks
from ambitious_video.domain.load import load_script
from ambitious_video.domain.money import Price
from ambitious_video.domain.plan import plan_sha
from ambitious_video.domain.types import ClipTrack, PaidClip, Speech, Voiceover
from ambitious_video.providers.base import Provider, RunResult
from ambitious_video.providers.elevenlabs_speech import ElevenLabsSpeech, ElevenLabsSpeechConfig
from ambitious_video.providers.xai_image import XaiImage, XaiImageConfig
from ambitious_video.providers.xai_speech import XaiSpeech, XaiSpeechConfig
from ambitious_video.providers.xai_video import XaiVideo, XaiVideoConfig

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "samples"
SLUGS = ("example-accounting", "example-bookshop", "example-fitness", "example-plumbing")
MIGRATIONS = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")
SENTINEL = "sentinel-key-value-41c7e2b9d6"
OTHER_SENTINEL = "sentinel-key-value-8a03f5d1c4"
NOW = "2026-01-01T00:00:00Z"
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 4, 1, tzinfo=UTC)
SMALL = {"width": 320, "height": 180}


def _tone(text: str) -> bytes:
    tone = array.array("h", [int(8000 * math.sin(index * (200 + len(text)) / 4800)) for index in range(4800)])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(tone.tobytes())
    return buffer.getvalue()


class KeyedSpeech:
    kind: Final = "speech"
    models: Final = ("only",)
    seedable: Final = False
    adapter_version: Final = 1

    def __init__(self, provider_id: str, secret: str, value: str, rate: str) -> None:
        self.id: Final = provider_id
        self.secret: Final = secret
        self.value: Final = value
        self.rate: Final = rate
        self.calls: list[str] = []

    def params(self, step: PaidClip) -> JsonObject:
        assert isinstance(step, Speech)
        return {"model": step.model, "say": step.text, "as": step.voice}

    def price(self, model: str) -> Price:
        source = "https://prices.example/" + self.id
        return Price(self.id, model, "character", Decimal(self.rate), source, "2026-11-16")

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult:
        assert secret == self.value and inputs == {}
        self.calls.append(str(params["say"]))
        out.write_bytes(_tone(str(params["say"])))
        return RunResult(out)


@pytest.fixture
def two_providers() -> Iterator[tuple[KeyedSpeech, KeyedSpeech]]:
    scenes = KeyedSpeech("first-speech", "FIRST_SPEECH_KEY", SENTINEL, "0.001")
    voice = KeyedSpeech("second-speech", "SECOND_SPEECH_KEY", OTHER_SENTINEL, "0.01")
    providers.register(scenes)
    providers.register(voice)
    try:
        yield scenes, voice
    finally:
        providers.unregister(scenes.id)
        providers.unregister(voice.id)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise OSError("this test must not open a socket")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("socket.create_connection", refuse)
    monkeypatch.setattr("socket.getaddrinfo", refuse)
    monkeypatch.setattr("urllib.request.urlopen", refuse)


def _sample(dest: Path, slug: str, spoken_by: tuple[str, ...]) -> tuple[dict[str, object], Path]:
    folder = dest / slug
    shutil.copytree(SAMPLES / slug, folder)
    script = yaml.safe_load((folder / "script.yaml").read_text(encoding="utf-8"))
    assert isinstance(script, dict)
    script["output"] = {**script["output"], **SMALL}
    count = 0
    for track in script["tracks"]:
        for clip in track.get("clips", []):
            if clip["type"] == "voiceover" and spoken_by:
                provider = spoken_by[count % len(spoken_by)]
                model = "tts" if provider == "xai-speech" else "only"
                clip.update({"type": "speech", "provider": provider, "model": model, "voice": "narrator"})
                count += 1
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
    *,
    offline: bool = False,
) -> dict[str, object]:
    db.execute("BEGIN IMMEDIATE")
    try:
        data = engine.render_new(db, dict(script), folder, job, when, env, offline=offline)
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


def _steps_of(plan: Mapping[str, object], provider: str) -> list[dict[str, object]]:
    return [step for step in _rows(plan, "steps") if step["provider"] == provider]


def _entry(env: str, provider: str, lines: list[dict[str, object]]) -> dict[str, object]:
    total = sum((Decimal(str(line["usd"])) for line in lines), start=Decimal(0))
    return {
        "env": env,
        "provider": provider,
        "steps": [line["index"] for line in lines],
        "usd": format(total, ".6f"),
    }


def _everything_written(base: Path) -> bytes:
    return b"\n".join(path.read_bytes() for path in sorted(base.rglob("*")) if path.is_file())


OWN_VOICE = "Zk3QpV7mXy1LrT9aBc2D"
PER_CHARACTER = Decimal("0.00008")


class _Calls(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, dict[str, str], bytes]] = []
    video = b""
    picture = b""

    def _answer(self, body: bytes) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def _note(self, method: str, body: bytes) -> None:
        headers = {name.lower(): value for name, value in self.headers.items()}
        type(self).seen.append((method, self.path, headers, body))

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._note("POST", body)
        if self.path.startswith("/v1/text-to-speech/"):
            self._answer(_tone(json.loads(body)["text"]))
        elif self.path == "/v1/images/generations":
            encoded = base64.b64encode(type(self).picture).decode()
            self._answer(json.dumps({"data": [{"b64_json": encoded}]}).encode())
        else:
            self._answer(json.dumps({"request_id": f"req-{len(type(self).seen)}"}).encode())

    def do_GET(self) -> None:
        self._note("GET", b"")
        if self.path.startswith("/files/"):
            self._answer(type(self).video)
            return
        address = f"http://127.0.0.1:{self.server.server_address[1]}/files/made.mp4"
        self._answer(json.dumps({"status": "done", "video": {"url": address, "duration": 4}}).encode())

    def log_message(self, *_args: object) -> None:
        return None


class _XaiCalls(_Calls):
    seen: list[tuple[str, str, dict[str, str], bytes]] = []


class _VoiceCalls(_Calls):
    seen: list[tuple[str, str, dict[str, str], bytes]] = []


def _made_video(folder: Path) -> bytes:
    path = folder / "made.mp4"
    source = "color=c=red:size=64x36:rate=5:duration=4"
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, stdin=subprocess.DEVNULL, check=True)
    made = path.read_bytes()
    path.unlink()
    return made


def _made_picture() -> bytes:
    held = io.BytesIO()
    Image.new("RGB", (64, 36), (20, 160, 60)).save(held, format="PNG")
    return held.getvalue()


@pytest.fixture
def xai_and_elevenlabs(tmp_path: Path) -> Iterator[tuple[type[_Calls], type[_Calls]]]:
    _XaiCalls.seen = []
    _XaiCalls.video = _made_video(tmp_path)
    _XaiCalls.picture = _made_picture()
    _VoiceCalls.seen = []
    servers = [HTTPServer(("127.0.0.1", 0), kind) for kind in (_XaiCalls, _VoiceCalls)]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    xai, voices = (f"http://127.0.0.1:{server.server_port}" for server in servers)
    local: tuple[Provider, ...] = (
        XaiVideo(
            XaiVideoConfig(
                submit_url=xai + "/v1/videos/generations",
                status_url=xai + "/v1/videos/",
                download_schemes=("http",),
                timeout_seconds=5,
                poll_seconds=0.02,
                wait_seconds=5,
            )
        ),
        XaiImage(XaiImageConfig(endpoint=xai + "/v1/images/generations", timeout_seconds=5)),
        ElevenLabsSpeech(ElevenLabsSpeechConfig(endpoint=voices + "/v1/text-to-speech", timeout_seconds=5)),
    )
    shipped = [providers.find(adapter.id) for adapter in local]
    for adapter in local:
        providers.unregister(adapter.id)
        providers.register(adapter)
    try:
        yield _XaiCalls, _VoiceCalls
    finally:
        for adapter, kept in zip(local, shipped, strict=True):
            providers.unregister(adapter.id)
            assert kept is not None
            providers.register(kept)
        for server, thread in zip(servers, threads, strict=True):
            server.shutdown()
            server.server_close()
            thread.join()


def _spoken_with_scenes(dest: Path) -> tuple[dict[str, object], Path]:
    script, folder = _sample(dest, "example-bookshop", ())
    made = (("xai-video", "grok-imagine-video"), ("xai-image", "grok-imagine-image"))
    for track in script["tracks"]:
        clips = track.get("clips", [])
        stills = [index for index, clip in enumerate(clips) if clip["type"] == "image"]
        for index, (provider, model) in zip(stills, made, strict=False):
            clips[index] = {
                "type": "generated_scene",
                "provider": provider,
                "model": model,
                "prompt": "A quiet shelf of books in morning light, no people, no text",
                "duration": 4,
                "fallback": {"type": "image", "path": clips[index]["path"]},
            }
        for clip in clips:
            if clip["type"] == "voiceover":
                spoken = {"type": "speech", "provider": "elevenlabs-speech", "model": "eleven-v4", "voice": OWN_VOICE}
                clip.update(spoken)
    return script, folder


def _lines_of(estimate: Mapping[str, object], *names: str) -> list[dict[str, object]]:
    return [line for line in _rows(estimate, "breakdown") if line.get("provider") in names]


def test_only_the_missing_key_of_an_uncached_step_is_asked_for(
    tmp_path: Path, xai_and_elevenlabs: tuple[type[_Calls], type[_Calls]]
) -> None:
    xai, voices = xai_and_elevenlabs
    script, folder = _spoken_with_scenes(tmp_path)
    home = tmp_path / "home"
    bare = {"AMBITIOUS_HOME": str(home)}
    one_key = {**bare, "XAI_API_KEY": SENTINEL}
    both_keys = {**one_key, "ELEVENLABS_API_KEY": OTHER_SENTINEL}

    estimate = engine.estimate_data(script, folder, one_key)
    plan = engine.plan_view(script, folder, one_key)
    speech = _lines_of(estimate, "elevenlabs-speech")
    scenes = _lines_of(estimate, "xai-video", "xai-image")
    assert len(speech) == 5 and [line["provider"] for line in scenes] == ["xai-video", "xai-image"]
    for line in speech:
        assert (line["step"], line["model"], line["unit"]) == ("speech", "eleven-v4", "character")
        assert Decimal(str(line["usd"])) == Decimal(str(line["units"])) * PER_CHARACTER
    wanted = _entry("ELEVENLABS_API_KEY", "elevenlabs-speech", speech)
    assert estimate["secretsNeeded"] == [wanted] == plan["secretsNeeded"]
    assert wanted["steps"] == [step["index"] for step in _steps_of(plan, "elevenlabs-speech")]
    assert all(step["type"] == "speech" for step in _steps_of(plan, "elevenlabs-speech"))
    assert Decimal(str(wanted["usd"])) > 0 and set(wanted) == {"env", "provider", "steps", "usd"}

    neither = engine.estimate_data(script, folder, bare)["secretsNeeded"]
    assert neither == [wanted, _entry("XAI_API_KEY", "xai-image, xai-video", scenes)]
    assert sum(Decimal(str(entry["usd"])) for entry in neither) == Decimal(str(estimate["usd"]))
    assert engine.estimate_data(script, folder, both_keys)["secretsNeeded"] == []
    assert engine.estimate_data(script, folder, {**both_keys, "ELEVENLABS_API_KEY": ""})["secretsNeeded"] == [wanted]

    db = _open(home)
    try:
        with pytest.raises(Failure) as refused:
            _render(db, script, folder, "job-zero", FIRST, one_key)
        assert [error.code for error in refused.value.errors] == ["E_NO_KEY"]
        hint = refused.value.errors[0].hint
        assert "ELEVENLABS_API_KEY" in hint and "XAI_API_KEY" not in hint and "--offline" in hint
        assert refused.value.data == {"missing": [wanted]}
        assert xai.seen == [] and voices.seen == [] and list_ledger(db) == ()
        assert db.execute("SELECT count(*) FROM jobs").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM provider_requests").fetchone() == (0,)
        assert not (home / "video" / "cache").exists()

        with pytest.raises(Failure) as refused_twice:
            _render(db, script, folder, "job-zero", FIRST, bare)
        assert [error.code for error in refused_twice.value.errors] == ["E_NO_KEY", "E_NO_KEY"]
        assert refused_twice.value.data == {"missing": neither}
        assert xai.seen == [] and voices.seen == []

        first = _render(db, script, folder, "job-one", FIRST, both_keys)
        assert first["costUsd"] == estimate["usd"]
        assert len(voices.seen) == len(speech) and len(list_ledger(db)) == len(speech) + len(scenes)
        for method, path, headers, body in voices.seen:
            assert (method, path) == ("POST", f"/v1/text-to-speech/{OWN_VOICE}?output_format=mp3_44100_128")
            assert headers["xi-api-key"] == OTHER_SENTINEL and "authorization" not in headers
            assert json.loads(body)["model_id"] == "eleven_v4" and SENTINEL.encode() not in body
        posted = [path for method, path, _headers, _body in xai.seen if method == "POST"]
        assert sorted(posted) == ["/v1/images/generations", "/v1/videos/generations"]
        keyed = [headers for _method, path, headers, _body in xai.seen if path.startswith("/v1/")]
        assert all(headers["authorization"] == "Bearer " + SENTINEL for headers in keyed)
        assert not any("xi-api-key" in headers or OTHER_SENTINEL in str(headers) for *_, headers, _body in xai.seen)

        after = engine.estimate_data(script, folder, one_key)
        assert after["secretsNeeded"] == [] and after["usd"] == "0.000000"
        assert engine.plan_view(script, folder, bare)["secretsNeeded"] == []
        calls = (len(xai.seen), len(voices.seen))
        second = _render(db, script, folder, "job-two", LATER, bare)
        assert second["costUsd"] == "0.000000" and (len(xai.seen), len(voices.seen)) == calls

        repainted = json.loads(json.dumps(script))
        scene = next(
            clip
            for track in repainted["tracks"]
            for clip in track.get("clips", [])
            if clip.get("provider") == "xai-image"
        )
        scene["prompt"] = scene["prompt"] + ", seen from the door"
        assert engine.estimate_data(repainted, folder, one_key)["secretsNeeded"] == []
        only_xai = engine.estimate_data(repainted, folder, bare)["secretsNeeded"]
        assert [(entry["env"], entry["provider"]) for entry in only_xai] == [("XAI_API_KEY", "xai-image")]

        changed = json.loads(json.dumps(script))
        target = next(
            clip
            for track in changed["tracks"]
            for clip in track.get("clips", [])
            if clip.get("provider") == "elevenlabs-speech"
        )
        target["text"] = target["text"] + " Changed."
        partly = engine.estimate_data(changed, folder, one_key)
        (owed,) = [line for line in _rows(partly, "breakdown") if line.get("cached") is False]
        assert partly["secretsNeeded"] == [_entry("ELEVENLABS_API_KEY", "elevenlabs-speech", [owed])]
        assert Decimal(str(owed["usd"])) == len(target["text"]) * PER_CHARACTER
        with pytest.raises(Failure) as refused_again:
            _render(db, changed, folder, "job-three", LATER, one_key)
        assert refused_again.value.data == {"missing": partly["secretsNeeded"]}
        assert (len(xai.seen), len(voices.seen)) == calls
        dumped = "\n".join(db.iterdump())
    finally:
        db.close()
    shown = json.dumps([estimate, plan, neither, first, after, second, partly, only_xai, refused.value.data])
    for held in (shown, dumped, str(refused.value.errors), str(refused_again.value.errors)):
        assert SENTINEL not in held and OTHER_SENTINEL not in held
    written = _everything_written(tmp_path)
    assert SENTINEL.encode() not in written and OTHER_SENTINEL.encode() not in written


def test_secrets_needed_is_not_part_of_the_plan_hash(
    tmp_path: Path, two_providers: tuple[KeyedSpeech, KeyedSpeech]
) -> None:
    scenes, voice = two_providers
    script, folder = _sample(tmp_path, "example-bookshop", (scenes.id, voice.id))
    bare = {"AMBITIOUS_HOME": str(tmp_path / "home")}
    asked = engine.plan_view(script, folder, bare)
    keyed = engine.plan_view(script, folder, {**bare, scenes.secret: SENTINEL, voice.secret: OTHER_SENTINEL})
    assert len(_rows(asked, "secretsNeeded")) == 2 and keyed["secretsNeeded"] == []
    assert asked["planSha256"] == keyed["planSha256"] == engine.plan_data(script, folder)["planSha256"]
    assert plan_sha(asked) == asked["planSha256"]
    assert {name: value for name, value in asked.items() if name != "secretsNeeded"} == engine.plan_data(script, folder)


def test_one_variable_read_by_two_providers_is_asked_for_once(tmp_path: Path) -> None:
    one = KeyedSpeech("shared-a", "SHARED_SPEECH_KEY", SENTINEL, "0.001")
    two = KeyedSpeech("shared-b", "SHARED_SPEECH_KEY", SENTINEL, "0.002")
    providers.register(one)
    providers.register(two)
    try:
        script, folder = _sample(tmp_path, "example-bookshop", (two.id, one.id))
        estimate = engine.estimate_data(script, folder, {"AMBITIOUS_HOME": str(tmp_path / "home")})
    finally:
        providers.unregister(one.id)
        providers.unregister(two.id)
    (entry,) = _rows(estimate, "secretsNeeded")
    paid = [line for line in _rows(estimate, "breakdown") if "provider" in line]
    assert entry == _entry("SHARED_SPEECH_KEY", "shared-a, shared-b", paid) and entry["usd"] == estimate["usd"]


class _Speaker(BaseHTTPRequestHandler):
    seen: list[str] = []
    status = 200

    def do_POST(self) -> None:
        sent = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).seen.append(str(self.headers.get("Authorization")))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(_tone(json.loads(sent)["text"]) if type(self).status == 200 else b"refused")

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def local_xai() -> Iterator[None]:
    _Speaker.seen = []
    _Speaker.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Speaker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shipped = providers.find("xai-speech")
    assert shipped is not None
    providers.unregister("xai-speech")
    endpoint = f"http://127.0.0.1:{server.server_port}/v1/tts"
    providers.register(XaiSpeech(XaiSpeechConfig(endpoint=endpoint, timeout_seconds=5)))
    try:
        yield
    finally:
        providers.unregister("xai-speech")
        providers.register(shipped)
        server.shutdown()
        server.server_close()
        thread.join()


class _Shell:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        self.monkeypatch = monkeypatch
        self.capsys = capsys
        self.printed: list[str] = []

    def __call__(self, *args: str) -> dict[str, object]:
        self.monkeypatch.setattr(sys, "argv", ["ambitious-video", *args, "--now", NOW, "--seed", "1"])
        code = main.main()
        captured = self.capsys.readouterr()
        self.printed.extend([captured.out, captured.err])
        payload = json.loads(captured.out)
        assert isinstance(payload, dict)
        payload["exit"] = code
        return payload


@pytest.fixture
def shell(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> _Shell:
    monkeypatch.setattr(json, "loads", json.loads)
    monkeypatch.setattr(runner, "selfcheck", runner.selfcheck)
    monkeypatch.setattr(runner, "_read_stdin", runner._read_stdin)
    return _Shell(monkeypatch, capsys)


def test_a_key_value_is_never_stored_or_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    local_xai: None,
    shell: _Shell,
) -> None:
    caplog.set_level(logging.DEBUG)
    script, folder = _sample(tmp_path, "example-plumbing", ("xai-speech",))
    home = tmp_path / "home"
    path = folder / "video.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    monkeypatch.setenv("AMBITIOUS_HOME", str(home))
    monkeypatch.delenv("XAI_API_KEY", raising=False)

    asked = shell("estimate", "--in", str(path))
    assert [entry["env"] for entry in asked["data"]["secretsNeeded"]] == ["XAI_API_KEY"]
    refused = shell("render", "--in", str(path))
    assert refused["exit"] == 1 and refused["errors"][0]["code"] == "E_NO_KEY"
    assert refused["data"]["missing"] == asked["data"]["secretsNeeded"] and _Speaker.seen == []

    monkeypatch.setenv("XAI_API_KEY", SENTINEL)
    planned = shell("plan", "--in", str(path))
    priced = shell("estimate", "--in", str(path))
    assert planned["data"]["secretsNeeded"] == [] == priced["data"]["secretsNeeded"]
    rendered = shell("render", "--in", str(path))
    assert rendered["exit"] == 0, rendered
    spoken = [step for step in rendered["data"]["steps"] if step["provider"] == "xai-speech"]
    assert len(spoken) > 1 and all(step["cached"] is False for step in spoken)
    assert _Speaker.seen == ["Bearer " + SENTINEL] * len(spoken)
    assert Decimal(rendered["data"]["costUsd"]) > 0

    job = folder / "job.json"
    job.write_text(json.dumps({"jobId": rendered["data"]["jobId"]}), encoding="utf-8")
    assert shell("verify", "--in", str(job))["exit"] == 0
    ledger = shell("ledger")
    assert len(ledger["data"]["lines"]) == len(spoken)
    assert shell("jobs")["exit"] == 0 and len(shell("cache", "list")["data"]["entries"]) == len(spoken)

    _Speaker.status = 401
    changed = json.loads(json.dumps(script))
    changed["tracks"][1]["clips"][0]["text"] = "A line that was never spoken before."
    other = folder / "changed.json"
    other.write_text(json.dumps(changed), encoding="utf-8")
    failed = shell("render", "--in", str(other))
    assert failed["exit"] == 1 and failed["errors"][0]["code"] == "E_PROVIDER"
    assert failed["data"] == {"provider": "xai-speech", "status": 401}
    assert _Speaker.seen[-1] == "Bearer " + SENTINEL and len(_Speaker.seen) == len(spoken) + 1
    retried = shell("render", "--in", str(other), "--offline")
    assert retried["exit"] == 0 and len(_Speaker.seen) == len(spoken) + 1

    store = home / "video" / "cache"
    assert len([entry for entry in store.iterdir() if (entry / "output.bin").is_file()]) == len(spoken)
    assert (folder / str(script["output"]["path"])).is_file()
    db = _open(home)
    try:
        dumped = "\n".join(db.iterdump())
    finally:
        db.close()
    assert "ledger" in dumped and "step_cache" in dumped and rendered["data"]["jobId"] in dumped
    for held in (dumped, json.dumps(planned), json.dumps(rendered), "".join(shell.printed), caplog.text):
        assert SENTINEL not in held
    assert len(shell.printed) > 20 and "XAI_API_KEY" in "".join(shell.printed)
    assert SENTINEL.encode() not in _everything_written(tmp_path)
    assert SENTINEL.encode("utf-16-le") not in _everything_written(tmp_path)


@pytest.mark.parametrize("slug", SLUGS)
def test_a_sample_renders_offline_for_nothing_with_no_socket_and_the_same_bytes(
    slug: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_network: None
) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    script, folder = _sample(tmp_path / "paid", slug, ("xai-speech",))
    plain, plain_folder = _sample(tmp_path / "plain", slug, ())
    homes = [tmp_path / "home-a", tmp_path / "home-b", tmp_path / "home-c"]
    envs = [{"AMBITIOUS_HOME": str(home)} for home in homes]

    online = engine.plan_view(script, folder, envs[0])
    paid = [step for step in _rows(online, "steps") if step["paid"] is True]
    assert len(paid) > 1 and [entry["env"] for entry in _rows(online, "secretsNeeded")] == ["XAI_API_KEY"]

    plan = engine.plan_view(script, folder, envs[0], offline=True)
    assert engine.canonical(plan) == engine.canonical(engine.plan_view(script, folder, envs[1], offline=True))
    assert plan["secretsNeeded"] == [] and plan["planSha256"] != online["planSha256"]
    assert not any(step["paid"] for step in _rows(plan, "steps"))
    marked = [step for step in _rows(plan, "steps") if "fallback" in step]
    assert [step["index"] for step in marked] == [step["index"] for step in paid]
    for step in marked:
        assert (step["fallback"], step["type"]) == (CAPTIONS, "voiceover")
        assert (step["provider"], step["model"]) == ("local", None)
        assert step["fallbackFor"] == {"type": "speech", "provider": "xai-speech", "model": "tts"}
    assert plan["captions"] == online["captions"] and plan["durationSec"] == online["durationSec"]

    estimate = engine.estimate_data(script, folder, envs[0], offline=True)
    assert estimate["usd"] == "0.000000" and estimate["secretsNeeded"] == []
    lines = _rows(estimate, "breakdown")
    assert all(line["usd"] == "0.000000" and "provider" not in line for line in lines)
    assert [line["index"] for line in lines if line.get("fallback") == CAPTIONS] == [step["index"] for step in paid]

    renders: list[dict[str, object]] = []
    for home, env, job in zip(homes[:2], envs[:2], ("job-a", "job-b"), strict=True):
        db = _open(home)
        try:
            renders.append(_render(db, script, folder, job, FIRST if job == "job-a" else LATER, env, offline=True))
            assert list_ledger(db) == () and engine.cache_list(db, None)["entries"] == []
        finally:
            db.close()
        assert not (home / "video" / "cache").exists()
    db = _open(homes[2])
    try:
        captions_only = _render(db, plain, plain_folder, "job-c", FIRST, envs[2])
    finally:
        db.close()

    one, two = renders
    assert one["costUsd"] == two["costUsd"] == "0.000000"
    assert one["sha256"] == two["sha256"] == captions_only["sha256"] and len(str(one["sha256"])) == 64
    assert one["planSha256"] == two["planSha256"] == plan["planSha256"]
    for step in _rows(one, "steps"):
        assert (step["fallback"], step["cached"], step["usd"]) == (CAPTIONS, False, "0.000000")
        assert step["provider"] == "local"
        assert step["fallbackFor"] == {"type": "speech", "provider": "xai-speech", "model": "tts"}
    assert [step["index"] for step in _rows(one, "steps")] == [step["index"] for step in paid]
    assert _rows(one, "steps") == _rows(two, "steps")


def test_offline_takes_the_fallback_even_when_the_key_is_set_or_the_step_is_cached(
    tmp_path: Path, two_providers: tuple[KeyedSpeech, KeyedSpeech], no_network: None
) -> None:
    scenes, voice = two_providers
    script, folder = _sample(tmp_path, "example-fitness", (scenes.id,))
    home = tmp_path / "home"
    keyed = {"AMBITIOUS_HOME": str(home), scenes.secret: SENTINEL}
    db = _open(home)
    try:
        offline = _render(db, script, folder, "job-one", FIRST, keyed, offline=True)
        assert scenes.calls == [] and offline["costUsd"] == "0.000000" and list_ledger(db) == ()
        spoken = _render(db, script, folder, "job-two", FIRST, keyed)
        assert len(scenes.calls) == len(_rows(spoken, "steps")) > 1 and "fallback" not in _rows(spoken, "steps")[0]
        again = _render(db, script, folder, "job-three", LATER, keyed, offline=True)
        assert again["sha256"] == offline["sha256"] != spoken["sha256"]
        assert all(step["fallback"] == CAPTIONS for step in _rows(again, "steps"))
        assert len(list_ledger(db)) == len(scenes.calls)
    finally:
        db.close()


def test_a_fallback_is_a_free_clip_with_the_same_words_and_time() -> None:
    line = Speech(1.5, "Hello there.", 2.0, "xai-speech", "tts", "eve")
    found = fallback_of(line)
    assert (found.name, found.clip, found.instead_of) == (CAPTIONS, Voiceover(1.5, "Hello there.", 2.0), line)
    raw = yaml.safe_load((ROOT / "examples" / "all-clip-types.yaml").read_text(encoding="utf-8"))
    script, problems = load_script(raw)
    assert script is not None and problems == ()
    changed, taken = with_fallbacks(script, lambda clip: clip.at < 10)
    kept, same = with_fallbacks(script, lambda _clip: False)
    assert same == {} and kept == script
    assert sorted(taken) == [("voice", 0), ("voice", 1)]
    voice = next(track for track in changed.tracks if isinstance(track, ClipTrack) and track.id == "voice")
    assert [type(clip) for clip in voice.clips] == [Voiceover, Voiceover, Speech, Speech, Voiceover]
    assert [track.id for track in changed.tracks] == [track.id for track in script.tracks]


def _cli(home: Path, args: list[str]) -> dict[str, object]:
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT / "vendor")])
    env["AMBITIOUS_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "ambitious_video.cli.main", *args, "--now", NOW, "--seed", "1"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict) and result.stderr == ""
    payload["exit"] = result.returncode
    return payload


def test_the_offline_flag_plans_prices_renders_and_verifies_with_no_key(tmp_path: Path) -> None:
    script, folder = _sample(tmp_path, "example-accounting", ("xai-speech",))
    home = tmp_path / "home"
    path = folder / "video.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    source = ["--in", str(path)]

    online = _cli(home, ["plan", *source])
    assert online["exit"] == 0 and [entry["env"] for entry in online["data"]["secretsNeeded"]] == ["XAI_API_KEY"]
    assert not home.exists()
    refused = _cli(home, ["render", *source])
    assert refused["exit"] == 1 and refused["data"]["missing"] == online["data"]["secretsNeeded"]
    assert [error["code"] for error in refused["errors"]] == ["E_NO_KEY"]

    planned = _cli(home, ["plan", "--offline", *source])
    priced = _cli(home, ["estimate", *source, "--offline"])
    assert planned["exit"] == 0 and planned["data"]["secretsNeeded"] == [] == priced["data"]["secretsNeeded"]
    assert priced["data"]["usd"] == "0.000000"
    rendered = _cli(home, ["render", "--offline", *source])
    assert rendered["exit"] == 0 and rendered["data"]["costUsd"] == "0.000000"
    assert rendered["data"]["planSha256"] == planned["data"]["planSha256"] != online["data"]["planSha256"]
    assert rendered["data"]["steps"] and all(step["fallback"] == CAPTIONS for step in rendered["data"]["steps"])
    job = folder / "job.json"
    job.write_text(json.dumps({"jobId": rendered["data"]["jobId"]}), encoding="utf-8")
    checked = _cli(home, ["verify", "--in", str(job)])
    assert checked["exit"] == 0 and checked["data"]["passed"] is True
    assert _cli(home, ["ledger"])["data"]["lines"] == []


def test_the_offline_flag_is_refused_where_it_means_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = ["--in", str(SAMPLES / "example-bookshop" / "script.yaml")]
    wrong = (["plan", "--resend"], ["estimate", "--resend"], ["render", "--resend=yes"])
    for args in (["validate", "--offline"], ["render", "--offline=yes"], ["ledger", "--offline"], *wrong):
        refused = _cli(home, [*args, *source] if args[0] != "ledger" else args)
        assert refused["exit"] == 1 and refused["errors"][0]["code"] == "E_USAGE", args
    assert not home.exists()


def test_the_guide_tells_a_bot_how_to_ask_for_a_key(tmp_path: Path) -> None:
    guide = _cli(tmp_path / "home", ["guide"])["data"]
    keys = guide["keys"]
    assert isinstance(keys, list) and len(keys) >= 4
    text = " ".join(keys)
    for phrase in ("secretsNeeded", "once", "--offline", "E_NO_KEY", "never"):
        assert phrase in text, phrase
    shown = _cli(tmp_path / "home", ["--help"])["data"]
    assert any(row["flag"] == "--offline" for row in shown["flags"])
    assert any(row["flag"] == "--resend" for row in shown["flags"])
    assert "--offline" in shown["usage"]
