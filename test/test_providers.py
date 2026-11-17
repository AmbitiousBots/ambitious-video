from __future__ import annotations

import array
import io
import json
import math
import shutil
import threading
import urllib.error
import wave
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Final

import pytest
import yaml
from ambitious_kit.db.sqlite import Db, load_migrations, migrate, open_db
from ambitious_kit.domain.envelope import Failure, JsonObject

from ambitious_video import providers
from ambitious_video.cli import engine
from ambitious_video.db.store import list_ledger
from ambitious_video.domain.money import UNITS, Price
from ambitious_video.domain.types import PaidClip, Speech
from ambitious_video.providers.base import Provider, ProviderError, RunResult
from ambitious_video.providers.pricing import PriceMissing, PriceTableBroken, parse_prices, price_of, price_table
from ambitious_video.providers.xai_speech import XaiSpeech, XaiSpeechConfig

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "samples" / "example-bookshop"
MIGRATIONS = load_migrations(ROOT / "src" / "ambitious_video" / "db" / "migrations")
SECRET = "not-a-real-key-9876543210"
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 4, 1, tzinfo=UTC)
PAID_LINE = {
    "step",
    "index",
    "units",
    "usd",
    "provider",
    "model",
    "unit",
    "usdPerUnit",
    "sourceUrl",
    "checkedAt",
    "stepKey",
    "cached",
}
LIST_PRICES = {
    ("xai-speech", "tts"): ("character", "0.000015", "https://docs.x.ai/docs/models"),
    ("xai-video", "grok-imagine-video"): ("second", "0.05", "https://docs.x.ai/docs/models"),
    ("xai-video", "grok-imagine-video-1.5"): ("second", "0.08", "https://docs.x.ai/docs/models"),
    ("xai-video", "grok-imagine-video-1.5-lite"): ("second", "0.02", "https://docs.x.ai/docs/models"),
    ("xai-image", "grok-imagine-image"): ("image", "0.02", "https://docs.x.ai/docs/models"),
    ("xai-image", "grok-imagine-image-2.0"): ("image", "0.04", "https://docs.x.ai/docs/models"),
    ("elevenlabs-speech", "eleven-v4"): ("character", "0.00008", "https://elevenlabs.io/pricing/api"),
    ("elevenlabs-speech", "eleven-flash"): ("character", "0.00004", "https://elevenlabs.io/pricing/api"),
    ("sync-lipsync", "lipsync-2"): ("frame", "0.002", "https://sync.so/pricing"),
    ("sync-lipsync", "lipsync-2-pro"): ("frame", "0.00332", "https://sync.so/pricing"),
    ("sync-lipsync", "sync-3"): ("frame", "0.00532", "https://sync.so/pricing"),
}


def _tone(text: str) -> bytes:
    tone = array.array("h", [int(8000 * math.sin(index * (200 + len(text)) / 4800)) for index in range(4800)])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(tone.tobytes())
    return buffer.getvalue()


class TestSpeech:
    __test__ = False
    kind: Final = "speech"
    id: Final = "test-speech"
    models: Final = ("small", "large")
    secret: Final = "TEST_SPEECH_KEY"
    seedable: Final = False
    adapter_version: Final = 7

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def params(self, step: PaidClip) -> JsonObject:
        assert isinstance(step, Speech)
        return {"model": step.model, "say": step.text, "as": step.voice}

    def price(self, model: str) -> Price:
        rate = {"small": "0.001", "large": "0.01"}[model]
        return Price(self.id, model, "character", Decimal(rate), "https://prices.example/test-speech", "2026-11-16")

    def quote(self, params: JsonObject, units: Decimal) -> Decimal:
        return self.price(str(params["model"])).usd_per_unit * units

    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult:
        assert secret == SECRET and inputs == {}
        self.calls.append((str(params["say"]), str(params["as"])))
        out.write_bytes(_tone(str(params["say"])))
        return RunResult(out)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("this test must not open a socket")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("socket.create_connection", refuse)
    monkeypatch.setattr("urllib.request.urlopen", refuse)


@pytest.fixture
def fake_speech() -> Iterator[TestSpeech]:
    adapter = TestSpeech()
    providers.register(adapter)
    try:
        yield adapter
    finally:
        providers.unregister(adapter.id)


def _project(dest: Path, provider: str, model: str) -> tuple[dict[str, object], Path]:
    folder = dest / "project"
    shutil.copytree(SAMPLE, folder)
    script = yaml.safe_load((folder / "script.yaml").read_text(encoding="utf-8"))
    assert isinstance(script, dict)
    for track in script["tracks"]:
        for clip in track.get("clips", []):
            if clip["type"] == "voiceover":
                clip.update({"type": "speech", "provider": provider, "model": model, "voice": "narrator"})
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
    cache: Path | None = None,
) -> dict[str, object]:
    db.execute("BEGIN IMMEDIATE")
    try:
        data = engine.render_new(db, dict(script), folder, job, when, env, cache)
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    return dict(data)


def _lines(data: Mapping[str, object], name: str) -> list[dict[str, object]]:
    rows = data[name]
    assert isinstance(rows, list)
    return rows


def _paid(estimate: Mapping[str, object]) -> list[dict[str, object]]:
    return [line for line in _lines(estimate, "breakdown") if "provider" in line]


def _everything_written(*folders: Path) -> bytes:
    found = b""
    for base in folders:
        for path in sorted(base.rglob("*")):
            if path.is_file():
                found += path.read_bytes()
    return found


def test_a_provider_registered_only_in_a_test_runs_plan_estimate_cache_and_render(
    tmp_path: Path, fake_speech: TestSpeech, no_network: None
) -> None:
    script, folder = _project(tmp_path, "test-speech", "large")
    home = tmp_path / "home"
    quiet = {"AMBITIOUS_HOME": str(home)}
    keyed = {**quiet, "TEST_SPEECH_KEY": SECRET}

    plan = engine.plan_data(script, folder)
    spoken = [step for step in _lines(plan, "steps") if step["type"] == "speech"]
    assert len(spoken) > 1 and all(step["paid"] is True for step in spoken)
    assert {(step["provider"], step["model"]) for step in spoken} == {("test-speech", "large")}
    assert engine.canonical(plan) == engine.canonical(engine.plan_data(script, folder))

    before = engine.estimate_data(script, folder, quiet)
    lines = _paid(before)
    assert [line["stepKey"] for line in lines] == [step["stepKey"] for step in spoken]
    assert all(set(line) == PAID_LINE and line["cached"] is False for line in lines)
    assert all(Decimal(str(line["usd"])) == Decimal(str(line["units"])) * Decimal("0.01") for line in lines)
    assert {line["sourceUrl"] for line in lines} == {"https://prices.example/test-speech"}
    assert Decimal(str(before["usd"])) == sum(Decimal(str(line["usd"])) for line in lines) > 0

    db = _open(home)
    try:
        with pytest.raises(Failure) as refused:
            _render(db, script, folder, "job-zero", FIRST, quiet)
        assert [error.code for error in refused.value.errors] == ["E_NO_KEY"]
        owed = {
            "env": "TEST_SPEECH_KEY",
            "provider": "test-speech",
            "steps": [step["index"] for step in spoken],
            "usd": before["usd"],
        }
        assert refused.value.data == {"missing": [owed]} and before["secretsNeeded"] == [owed]
        assert fake_speech.calls == [] and list_ledger(db) == ()

        first = _render(db, script, folder, "job-one", FIRST, keyed)
        steps = _lines(first, "steps")
        assert fake_speech.calls == [(str(step["text"]), "narrator") for step in spoken]
        assert [step["stepKey"] for step in steps] == [step["stepKey"] for step in spoken]
        assert all(step["cached"] is False and step["provider"] == "test-speech" for step in steps)
        ledger = list_ledger(db)
        assert [line.idem_key for line in ledger] == [step["stepKey"] for step in steps]
        assert first["costUsd"] == before["usd"] and first["planSha256"] == plan["planSha256"]

        after = engine.estimate_data(script, folder, quiet)
        assert after["usd"] == "0.000000"
        assert all(line["cached"] is True and line["usd"] == "0.000000" for line in _paid(after))
        assert [line["units"] for line in _paid(after)] == [line["units"] for line in lines]

        second = _render(db, script, folder, "job-two", LATER, quiet)
        assert len(fake_speech.calls) == len(spoken) and list_ledger(db) == ledger
        assert all(step["cached"] is True for step in _lines(second, "steps"))
        assert second["costUsd"] == "0.000000" and second["sha256"] == first["sha256"]

        listed = _lines(engine.cache_list(db, None), "entries")
        assert sorted(entry["stepKey"] for entry in listed) == sorted(str(step["stepKey"]) for step in spoken)
        assert {(entry["provider"], entry["model"], entry["kind"]) for entry in listed} == {
            ("test-speech", "large", "speech")
        }

        moved = _render(db, script, folder, "job-three", LATER, quiet, tmp_path / "other")
        assert len(fake_speech.calls) == len(spoken) and list_ledger(db) == ledger
        assert moved["costUsd"] == "0.000000" and all(step["cached"] is True for step in _lines(moved, "steps"))
        assert len(_lines(engine.cache_list(db, tmp_path / "other"), "entries")) == len(spoken)
    finally:
        db.close()
    assert SECRET.encode() not in _everything_written(home, folder)
    assert SECRET not in json.dumps([plan, before, first, after, second, moved])


def test_a_cheaper_model_of_the_same_provider_is_another_step_and_another_price(
    tmp_path: Path, fake_speech: TestSpeech
) -> None:
    large, folder = _project(tmp_path, "test-speech", "large")
    small = json.loads(json.dumps(large).replace('"model": "large"', '"model": "small"'))
    env = {"AMBITIOUS_HOME": str(tmp_path / "home")}
    one, two = engine.estimate_data(large, folder, env), engine.estimate_data(small, folder, env)
    assert Decimal(str(one["usd"])) == 10 * Decimal(str(two["usd"]))
    assert {line["usdPerUnit"] for line in _paid(two)} == {"0.001"}
    assert not {line["stepKey"] for line in _paid(one)} & {line["stepKey"] for line in _paid(two)}


def test_estimate_names_the_provider_the_model_the_units_and_the_price_source(tmp_path: Path, no_network: None) -> None:
    script, folder = _project(tmp_path, "xai-speech", "tts")
    data = engine.estimate_data(script, folder, {"AMBITIOUS_HOME": str(tmp_path / "home")})
    lines = _lines(data, "breakdown")
    paid = _paid(data)
    words = [clip["text"] for track in script["tracks"] for clip in track.get("clips", []) if clip["type"] == "speech"]
    assert [line["units"] for line in paid] == [str(len(text)) for text in words] and len(paid) > 1
    for line in paid:
        assert set(line) == PAID_LINE and line["step"] == "speech"
        assert (line["provider"], line["model"], line["unit"]) == ("xai-speech", "tts", "character")
        assert (line["usdPerUnit"], line["sourceUrl"]) == ("0.000015", "https://docs.x.ai/docs/models")
        assert line["checkedAt"] == "2026-11-16" and line["cached"] is False
        assert Decimal(str(line["usd"])) == Decimal(str(line["units"])) * Decimal("0.000015")
        assert len(str(line["usd"]).partition(".")[2]) == 6
    free = [line for line in lines if "provider" not in line]
    assert free and all(set(line) == {"step", "index", "units", "usd"} and line["usd"] == "0.000000" for line in free)
    assert Decimal(str(data["usd"])) == sum(Decimal(str(line["usd"])) for line in paid)
    assert len(str(data["usd"]).partition(".")[2]) == 6


def test_a_cached_step_is_estimated_at_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Answer(io.BytesIO):
        def __enter__(self) -> Answer:
            return self

    def opened(request: object, timeout: float = 0) -> Answer:
        sent = json.loads(getattr(request, "data", b"{}"))
        return Answer(_tone(sent["text"]))

    script, folder = _project(tmp_path, "xai-speech", "tts")
    home, shared = tmp_path / "home", tmp_path / "shared"
    quiet = {"AMBITIOUS_HOME": str(home)}
    before = engine.estimate_data(script, folder, quiet, shared)
    monkeypatch.setattr("urllib.request.urlopen", opened)
    db = _open(home)
    try:
        db.execute("BEGIN IMMEDIATE")
        engine.render_new(db, dict(script), folder, "job-one", FIRST, {**quiet, "XAI_API_KEY": SECRET}, shared)
        db.execute("COMMIT")
    finally:
        db.close()
    after = engine.estimate_data(script, folder, quiet, shared)
    elsewhere = engine.estimate_data(script, folder, quiet)
    assert Decimal(str(before["usd"])) > 0 and after["usd"] == "0.000000"
    assert all(line["cached"] is True and line["usd"] == "0.000000" for line in _paid(after))
    assert [line["stepKey"] for line in _paid(after)] == [line["stepKey"] for line in _paid(before)]
    assert elsewhere == before

    changed = json.loads(json.dumps(script))
    first = next(clip for track in changed["tracks"] for clip in track.get("clips", []) if clip["type"] == "speech")
    first["text"] = first["text"] + " Changed."
    partly = _paid(engine.estimate_data(changed, folder, quiet, shared))
    assert [line["cached"] for line in partly] == [False] + [True] * (len(partly) - 1)
    assert Decimal(str(partly[0]["usd"])) == Decimal(len(first["text"])) * Decimal("0.000015")


def test_every_price_has_a_unit_a_source_and_a_date() -> None:
    raw = json.loads(files("ambitious_video").joinpath("pricing.json").read_text(encoding="utf-8"))
    assert set(raw) == {"prices"}
    for entry in raw["prices"]:
        assert set(entry) == {"provider", "model", "unit", "usdPerUnit", "sourceUrl", "checkedAt"}
        assert entry["unit"] in UNITS and Decimal(entry["usdPerUnit"]) > 0
        assert entry["sourceUrl"].startswith("https://") and entry["checkedAt"] == "2026-11-16"
    table = price_table()
    found = {(item.provider, item.model): (item.unit, str(item.usd_per_unit), item.source_url) for item in table}
    assert found == LIST_PRICES


def test_lip_sync_is_priced_per_output_frame() -> None:
    per_second_at_25_fps = {"lipsync-2": "0.05", "lipsync-2-pro": "0.083", "sync-3": "0.133"}
    for model, listed in per_second_at_25_fps.items():
        price = price_of("sync-lipsync", model)
        assert price.unit == "frame" and price.usd_per_unit * 25 == Decimal(listed)
    assert price_of("sync-lipsync", "lipsync-2").usd_per_unit * 30 * 25 == Decimal("1.50")


def test_every_registered_provider_has_a_price_for_each_model() -> None:
    listed = providers.registered()
    assert [item.id for item in listed] == ["xai-image", "xai-speech", "xai-video"]
    for item in listed:
        assert item.kind in {"scene", "speech", "lipsync"} and item.secret.isupper() and item.adapter_version >= 1
        for model in item.models:
            price = item.price(model)
            assert (price.provider, price.model) == (item.id, model)
            assert item.quote({"model": model}, Decimal(1000)) == price.usd_per_unit * 1000


def test_a_missing_or_broken_price_is_refused() -> None:
    with pytest.raises(PriceMissing):
        price_of("xai-speech", "no-such-model")
    good = {
        "provider": "p",
        "model": "m",
        "unit": "second",
        "usdPerUnit": "0.5",
        "sourceUrl": "https://prices.example/p",
        "checkedAt": "2026-11-16",
    }
    assert parse_prices(json.dumps({"prices": [good]}))[0].usd_per_unit == Decimal("0.5")
    broken = [
        {**good, "unit": "minute"},
        {**good, "usdPerUnit": "free"},
        {**good, "usdPerUnit": "0"},
        {**good, "usdPerUnit": 0.5},
        {**good, "sourceUrl": "prices.example"},
        {**good, "checkedAt": "last week"},
        {**good, "note": "extra"},
        {name: value for name, value in good.items() if name != "checkedAt"},
    ]
    for entry in broken:
        with pytest.raises(PriceTableBroken):
            parse_prices(json.dumps({"prices": [entry]}))
    for text in ("", "[]", "{}", json.dumps({"prices": []}), json.dumps({"prices": [good, good]})):
        with pytest.raises(PriceTableBroken):
            parse_prices(text)


def test_the_registry_refuses_a_second_adapter_with_the_same_id(fake_speech: TestSpeech) -> None:
    found: Provider | None = providers.find("test-speech")
    assert found is fake_speech and providers.find("no-such-provider") is None
    with pytest.raises(ValueError):
        providers.register(TestSpeech())
    assert [item.id for item in providers.registered()] == ["test-speech", "xai-image", "xai-speech", "xai-video"]
    providers.unregister("test-speech")
    assert providers.find("test-speech") is None


def test_an_unknown_provider_or_model_is_refused_before_anything_runs(tmp_path: Path, no_network: None) -> None:
    def problems(script: Mapping[str, object], folder: Path) -> list[tuple[str, str]]:
        with pytest.raises(Failure) as caught:
            engine.plan_data(script, folder)
        rows = caught.value.data["problems"]
        assert isinstance(rows, list) and {error.code for error in caught.value.errors} == {"E_INPUT_SCHEMA"}
        return [(str(row["at"]), str(row["message"])) for row in rows]

    unknown, folder = _project(tmp_path, "test-speech", "large")
    found = problems(unknown, folder)
    assert found and all(at.endswith("/provider") and "No provider" in message for at, message in found)
    wrong_model = json.loads(json.dumps(unknown).replace("test-speech", "xai-speech"))
    found = problems(wrong_model, folder)
    assert found and all(at.endswith("/model") and "no such model" in message for at, message in found)
    for command in (engine.validate_data, engine.plan_data):
        with pytest.raises(Failure):
            command(unknown, folder)
    with pytest.raises(Failure):
        engine.estimate_data(unknown, folder, {"AMBITIOUS_HOME": str(tmp_path / "home")})
    assert not (tmp_path / "home").exists()


def test_a_provider_of_another_kind_cannot_speak(tmp_path: Path) -> None:
    class TestScene(TestSpeech):
        kind: Final = "scene"  # type: ignore[assignment,misc] # the same fake, as a scene provider
        id: Final = "test-scene"  # type: ignore[misc] # the same fake, as a scene provider

    script, folder = _project(tmp_path, "test-scene", "large")
    providers.register(TestScene())
    try:
        with pytest.raises(Failure) as caught:
            engine.plan_data(script, folder)
    finally:
        providers.unregister("test-scene")
    rows = caught.value.data["problems"]
    assert isinstance(rows, list) and rows and all("does not do this step" in row["message"] for row in rows)


class _Recorder(BaseHTTPRequestHandler):
    seen: list[tuple[str, str, str, bytes]] = []
    status = 200

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        kind = str(self.headers.get("Content-Type"))
        type(self).seen.append((self.path, str(self.headers.get("Authorization")), kind, body))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"spoken bytes" if type(self).status == 200 else body)

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def local_speech() -> Iterator[XaiSpeech]:
    _Recorder.seen = []
    _Recorder.status = 200
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield XaiSpeech(XaiSpeechConfig(endpoint=f"http://127.0.0.1:{server.server_port}/v1/tts", timeout_seconds=5))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


CLIP = Speech(0.0, "Hello there.", None, "xai-speech", "tts", "eve")


def test_xai_speech_sends_the_words_the_voice_and_the_key_in_a_header(tmp_path: Path, local_speech: XaiSpeech) -> None:
    params = local_speech.params(CLIP)
    assert params == {
        "model": "tts",
        "text": "Hello there.",
        "voice_id": "eve",
        "language": "auto",
        "output_format": {"codec": "mp3"},
    }
    result = local_speech.run(params, {}, SECRET, tmp_path / "out.bin")
    assert result.path == tmp_path / "out.bin" and result.path.read_bytes() == b"spoken bytes"
    ((path, auth, kind, body),) = _Recorder.seen
    assert (path, auth, kind) == ("/v1/tts", "Bearer " + SECRET, "application/json")
    assert json.loads(body) == {name: value for name, value in params.items() if name != "model"}
    assert SECRET.encode() not in body
    assert local_speech.quote(params, Decimal(1_000_000)) == Decimal(15)


def test_xai_speech_has_no_voice_of_its_own() -> None:
    adapter = XaiSpeech()
    assert (adapter.kind, adapter.id, adapter.models) == ("speech", "xai-speech", ("tts",))
    assert adapter.secret == "XAI_API_KEY"
    assert adapter.seedable is False and adapter.config.endpoint == "https://api.x.ai/v1/tts"
    for voice in ("eve", "default", "an-owner-voice"):
        assert adapter.params(Speech(0.0, "Hi", None, "xai-speech", "tts", voice))["voice_id"] == voice
    other = XaiSpeech(XaiSpeechConfig(language="en"))
    assert other.params(CLIP)["language"] == "en" and adapter.params(CLIP) != other.params(CLIP)


def _locals_of(error: BaseException) -> str:
    parts = [str(error), repr(error.args), str(error.__cause__), str(error.__context__)]
    frame = error.__traceback__
    while frame is not None:
        parts.extend(repr(value) for value in frame.tb_frame.f_locals.values())
        frame = frame.tb_next
    return " ".join(parts)


def test_a_refused_call_drops_the_key(tmp_path: Path, local_speech: XaiSpeech) -> None:
    _Recorder.status = 401
    out = tmp_path / "out.bin"
    with pytest.raises(ProviderError) as caught:
        local_speech.run(local_speech.params(CLIP), {}, SECRET, out)
    assert (caught.value.provider, caught.value.status) == ("xai-speech", 401)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert SECRET not in _locals_of(caught.value)
    assert not out.exists()
    failure = engine.provider_failure(caught.value)
    assert [error.code for error in failure.errors] == ["E_PROVIDER"]
    assert failure.data == {"provider": "xai-speech", "status": 401}
    assert SECRET not in json.dumps([[error.message, error.hint] for error in failure.errors])


def test_a_network_error_drops_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError(f"Bearer {SECRET}")

    monkeypatch.setattr("urllib.request.urlopen", failed)
    adapter = XaiSpeech()
    with pytest.raises(ProviderError) as caught:
        adapter.run(adapter.params(CLIP), {}, SECRET, tmp_path / "out.bin")
    assert caught.value.status == 0 and caught.value.__cause__ is None and caught.value.__context__ is None
    assert SECRET not in _locals_of(caught.value)
