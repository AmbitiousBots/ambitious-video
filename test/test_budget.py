from __future__ import annotations

import array
import io
import json
import math
import shutil
import sys
import threading
import wave
from collections.abc import Iterator
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import ambitious_kit.cli.runner as runner
import pytest
from test_name_scan import hits

from ambitious_video import providers
from ambitious_video.cli import engine, main
from ambitious_video.providers.xai_speech import XaiSpeech, XaiSpeechConfig

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "samples"
SLUGS = ("example-accounting", "example-bookshop", "example-fitness", "example-plumbing")
NOW = "2026-01-01T00:00:00Z"
KEY = "not-a-real-key-5d2f80c1"
SMALL = {"width": 320, "height": 180, "fps": 10}
UPRIGHT = {"width": 180, "height": 320, "fps": 10}
STARTER_CAP = {"cap": "5.000000", "source": "budget.maxUsd"}


def _tone(text: str) -> bytes:
    tone = array.array("h", [int(8000 * math.sin(index * (200 + len(text)) / 4800)) for index in range(4800)])
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(tone.tobytes())
    return buffer.getvalue()


class _Speaker(BaseHTTPRequestHandler):
    seen: list[str] = []
    status = 200

    def do_POST(self) -> None:
        sent = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        text = json.loads(sent)["text"]
        type(self).seen.append(text)
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(_tone(text) if type(self).status == 200 else b"refused")

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def fake_speech(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[type[_Speaker]]:
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
    monkeypatch.setenv("AMBITIOUS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XAI_API_KEY", KEY)
    try:
        yield _Speaker
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

    def __call__(self, *args: str) -> dict[str, object]:
        self.monkeypatch.setattr(sys, "argv", ["ambitious-video", *args, "--now", NOW, "--seed", "1"])
        code = main.main()
        captured = self.capsys.readouterr()
        assert KEY not in captured.out and KEY not in captured.err
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


def _spoken_sample(dest: Path, slug: str = "example-bookshop") -> tuple[dict[str, object], Path]:
    folder = dest / slug
    shutil.copytree(SAMPLES / slug, folder)
    script = json.loads((folder / "script.json").read_text(encoding="utf-8"))
    assert isinstance(script, dict)
    script["output"] = {"path": "out/video.mp4", **SMALL}
    for track in script["tracks"]:
        for clip in track.get("clips", []):
            if clip["type"] == "voiceover":
                clip.update({"type": "speech", "provider": "xai-speech", "model": "tts", "voice": "narrator"})
    return script, folder


def _saved(folder: Path, script: dict[str, object], name: str = "video.json") -> str:
    path = folder / name
    path.write_text(json.dumps(script), encoding="utf-8")
    return str(path)


def _with(script: dict[str, object], cap: object | None, line: str | None = None) -> dict[str, object]:
    changed = json.loads(json.dumps(script))
    assert isinstance(changed, dict)
    if cap is None:
        changed.pop("budget", None)
    else:
        changed["budget"] = {"maxUsd": cap}
    if line is not None:
        changed["tracks"][1]["clips"][0]["text"] = line
    return changed


def _codes(payload: dict[str, object]) -> list[str]:
    errors = payload["errors"]
    assert isinstance(errors, list)
    return [error["code"] for error in errors]


def _data(payload: dict[str, object]) -> dict[str, object]:
    data = payload["data"]
    assert isinstance(data, dict)
    return data


def _list(data: dict[str, object], name: str) -> list[dict[str, object]]:
    rows = data[name]
    assert isinstance(rows, list)
    return rows


def _uncached_usd(data: dict[str, object]) -> Decimal:
    lines = [line for line in _list(data, "breakdown") if line.get("cached") is False]
    return sum((Decimal(str(line["usd"])) for line in lines), start=Decimal(0))


def _price(text: str) -> str:
    return format(Decimal(len(text)) * Decimal("0.000015"), ".6f")


def test_an_estimate_above_the_cap_stops_before_any_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_speech: type[_Speaker], shell: _Shell
) -> None:
    script, folder = _spoken_sample(tmp_path)
    free = _data(shell("estimate", "--in", _saved(folder, _with(script, None), "free.json")))
    total = str(free["usd"])
    assert Decimal(total) > Decimal("0.001") and "budget" not in free
    source = _saved(folder, _with(script, 0.001))

    priced = shell("estimate", "--in", source)
    assert priced["exit"] == 1 and _codes(priced) == ["E_BUDGET"]
    over = _data(priced)
    assert sorted(over) == ["breakdown", "cap", "estimate", "source"]
    assert (over["estimate"], over["cap"], over["source"]) == (total, "0.001000", "budget.maxUsd")
    assert over["breakdown"] == free["breakdown"] and _uncached_usd(over) == Decimal(total)

    refused = shell("render", "--in", source)
    assert refused["exit"] == 1 and _codes(refused) == ["E_BUDGET"]
    assert refused["data"] == {"estimate": total, "cap": "0.001000", "source": "budget.maxUsd"}
    monkeypatch.delenv("XAI_API_KEY")
    assert _codes(shell("render", "--in", source)) == ["E_BUDGET"]

    assert fake_speech.seen == []
    assert _data(shell("ledger"))["lines"] == [] and _data(shell("jobs"))["jobs"] == []
    assert _data(shell("cache", "list"))["entries"] == []
    assert not (folder / "out").exists()


def test_non_json_yaml_script_is_rejected_before_any_provider_call(
    tmp_path: Path, fake_speech: type[_Speaker], shell: _Shell
) -> None:
    folder = tmp_path / "yaml"
    folder.mkdir()
    yaml_path = folder / "legacy.yaml"
    yaml_path.write_text("schemaVersion: 2\nbudget:\n  maxUsd: 5\n", encoding="utf-8")
    for command in ("validate", "estimate", "render"):
        stopped = shell(command, "--in", str(yaml_path))
        assert stopped["exit"] == 1 and _codes(stopped) == ["E_INPUT_SCHEMA"], stopped
    assert fake_speech.seen == []
    assert not (tmp_path / "home").exists()


def test_a_cached_rerun_passes_under_a_cap_that_the_first_run_would_not(
    tmp_path: Path, fake_speech: type[_Speaker], shell: _Shell
) -> None:
    script, folder = _spoken_sample(tmp_path)
    first = shell("render", "--in", _saved(folder, _with(script, 5)))
    assert first["exit"] == 0, first
    billed = str(_data(first)["costUsd"])
    calls = len(fake_speech.seen)
    lines = _data(shell("ledger"))["lines"]
    assert Decimal(billed) > Decimal("0.001") and calls == 5

    tight = _saved(folder, _with(script, 0.001), "tight.json")
    priced = shell("estimate", "--in", tight)
    assert priced["exit"] == 0 and _data(priced)["usd"] == "0.000000"
    assert _data(priced)["budget"] == {"cap": "0.001000", "source": "budget.maxUsd"}
    again = shell("render", "--in", tight, "--max-usd", "0")
    assert again["exit"] == 0, again
    assert _data(again)["costUsd"] == "0.000000" and _data(again)["jobId"] != _data(first)["jobId"]
    assert all(step["cached"] is True for step in _list(_data(again), "steps"))
    assert len(fake_speech.seen) == calls and _data(shell("ledger"))["lines"] == lines

    new_line = "A sentence nobody has paid for yet."
    one_new = _saved(folder, _with(script, 0.001, new_line), "one-new.json")
    partly = shell("estimate", "--in", one_new)
    assert partly["exit"] == 0 and _data(partly)["usd"] == _price(new_line)
    stopped = shell("render", "--in", one_new, "--max-usd", "0.0001")
    assert _codes(stopped) == ["E_BUDGET"]
    assert stopped["data"] == {"estimate": _price(new_line), "cap": "0.000100", "source": "--max-usd"}
    assert len(fake_speech.seen) == calls
    paid = shell("render", "--in", one_new)
    assert paid["exit"] == 0 and _data(paid)["costUsd"] == _price(new_line)
    assert fake_speech.seen[calls:] == [new_line]


@pytest.mark.parametrize(
    ("in_script", "flag", "cap", "source"),
    [
        (0.001, None, "0.001000", "budget.maxUsd"),
        (None, "0.001", "0.001000", "--max-usd"),
        (0.002, "0.001", "0.001000", "--max-usd"),
        (0.001, "0.002", "0.001000", "budget.maxUsd"),
        (0.001, "5", "0.001000", "budget.maxUsd"),
        (0.001, "0.001", "0.001000", "budget.maxUsd"),
        (5, "0", "0.000000", "--max-usd"),
    ],
)
def test_the_lower_of_the_script_cap_and_the_flag_applies(
    in_script: float | None, flag: str | None, cap: str, source: str, tmp_path: Path, fake_speech: type[_Speaker]
) -> None:
    script, folder = _spoken_sample(tmp_path)
    limit = None if flag is None else Decimal(flag)
    with pytest.raises(engine.Failure) as stopped:
        engine.estimate_data(_with(script, in_script), folder, {}, tmp_path / "cache", max_usd=limit)
    assert [error.code for error in stopped.value.errors] == ["E_BUDGET"]
    assert (stopped.value.data["cap"], stopped.value.data["source"]) == (cap, source)
    assert source in stopped.value.errors[0].hint and fake_speech.seen == []


def test_an_estimate_equal_to_the_cap_passes_and_one_step_more_does_not(
    tmp_path: Path, fake_speech: type[_Speaker]
) -> None:
    script, folder = _spoken_sample(tmp_path)
    store = tmp_path / "cache"
    total = Decimal(str(engine.estimate_data(_with(script, None), folder, {}, store)["usd"]))
    exact = engine.estimate_data(_with(script, None), folder, {}, store, max_usd=total)
    assert exact["usd"] == format(total, ".6f")
    assert exact["budget"] == {"cap": format(total, ".6f"), "source": "--max-usd"}
    with pytest.raises(engine.Failure) as stopped:
        engine.estimate_data(_with(script, None), folder, {}, store, max_usd=total - Decimal("0.000001"))
    assert [error.code for error in stopped.value.errors] == ["E_BUDGET"] and fake_speech.seen == []


def test_a_script_without_a_cap_and_no_flag_is_not_limited(tmp_path: Path, fake_speech: type[_Speaker]) -> None:
    script, folder = _spoken_sample(tmp_path)
    priced = engine.estimate_data(_with(script, None), folder, {}, tmp_path / "cache")
    assert Decimal(str(priced["usd"])) > 0 and "budget" not in priced


def test_the_retry_of_a_job_is_held_to_the_cap(tmp_path: Path, fake_speech: type[_Speaker], shell: _Shell) -> None:
    script, folder = _spoken_sample(tmp_path)
    source = _saved(folder, _with(script, 5))
    fake_speech.status = 500
    failed = shell("render", "--in", source)
    assert _codes(failed) == ["E_PROVIDER"] and len(fake_speech.seen) == 1
    jobs = _list(_data(shell("jobs")), "jobs")
    assert [job["state"] for job in jobs] == ["failed"]
    retry = _saved(folder, {"jobId": jobs[0]["id"]}, "job.json")

    fake_speech.status = 200
    stopped = shell("render", "--in", retry, "--max-usd", "0.001")
    assert _codes(stopped) == ["E_BUDGET"] and _data(stopped)["source"] == "--max-usd"
    assert len(fake_speech.seen) == 1 and _data(shell("ledger"))["lines"] == []
    assert [job["state"] for job in _list(_data(shell("jobs")), "jobs")] == ["failed"]
    done = shell("render", "--in", retry, "--max-usd", "1")
    assert done["exit"] == 0 and len(fake_speech.seen) == 6


def test_offline_costs_nothing_and_passes_any_cap(tmp_path: Path, fake_speech: type[_Speaker], shell: _Shell) -> None:
    script, folder = _spoken_sample(tmp_path)
    source = _saved(folder, _with(script, 0.001))
    priced = shell("estimate", "--in", source, "--offline", "--max-usd", "0")
    assert priced["exit"] == 0 and _data(priced)["usd"] == "0.000000"
    assert _data(priced)["budget"] == {"cap": "0.000000", "source": "--max-usd"}
    rendered = shell("render", "--in", source, "--offline", "--max-usd", "0")
    assert rendered["exit"] == 0 and _data(rendered)["costUsd"] == "0.000000" and fake_speech.seen == []


@pytest.mark.parametrize("amount", ["abc", "-1", "1e3", "1.2345678", "2000000", "$5", "5.", ".5", "٥"])
def test_max_usd_must_be_a_plain_amount(
    amount: str, tmp_path: Path, fake_speech: type[_Speaker], shell: _Shell
) -> None:
    script, folder = _spoken_sample(tmp_path)
    source = _saved(folder, script)
    for command in ("estimate", "render"):
        refused = shell(command, "--in", source, "--max-usd", amount)
        assert refused["exit"] != 0 and _codes(refused) == ["E_USAGE"], refused
    assert fake_speech.seen == [] and _data(shell("jobs"))["jobs"] == []


def test_max_usd_belongs_to_estimate_and_render_alone(
    tmp_path: Path, fake_speech: type[_Speaker], shell: _Shell
) -> None:
    script, folder = _spoken_sample(tmp_path)
    source = _saved(folder, script)
    for command in (["plan"], ["validate"], ["cache", "list"]):
        assert _codes(shell(*command, "--in", source, "--max-usd", "1")) == ["E_USAGE"]
    assert _codes(shell("estimate", "--in", source, "--max-usd")) == ["E_USAGE"]
    assert _codes(shell("estimate", "--in", source, "--max-usd", "1", "--max-usd", "2")) == ["E_USAGE"]
    assert shell("estimate", "--in", source, "--max-usd=1")["exit"] == 0
    plain = _data(shell("plan", "--in", source))
    capped = _data(shell("plan", "--in", _saved(folder, _with(script, 0.001), "capped.json")))
    assert plain["planSha256"] == capped["planSha256"] and fake_speech.seen == []


def test_help_and_the_guide_explain_the_cap(shell: _Shell) -> None:
    listed = _data(shell("help"))
    flags = {str(entry["flag"]): str(entry["meaning"]) for entry in _list(listed, "flags")}
    assert "--max-usd" in str(listed["usage"]) and "E_BUDGET" in flags["--max-usd amount"]
    templates = _data(shell("guide"))["templates"]
    assert isinstance(templates, dict)
    notes = " ".join(templates["notes"])
    for phrase in ("E_BUDGET", "budget.maxUsd", "--max-usd", "before any provider is called", "the lower"):
        assert phrase in notes
    assert "go-ahead" in " ".join(templates["steps"])


@pytest.mark.parametrize("slug", SLUGS)
def test_each_sample_business_goes_from_its_template_to_a_paid_verified_video(
    slug: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_speech: type[_Speaker], shell: _Shell
) -> None:
    folder = tmp_path / slug
    shutil.copytree(SAMPLES / slug, folder)
    (folder / "script.json").unlink()
    document = json.loads((folder / "inputs.json").read_text(encoding="utf-8"))
    starter = document["template"]
    document["inputs"]["voice"] = "narrator"
    size = UPRIGHT if starter == "announcement" else SMALL
    document["output"] = {"path": "out/video.mp4", **size, "encode": "lossless"}
    inputs = _saved(folder, document, "inputs.json")
    script = folder / "video.json"
    source = ("--in", str(script))
    monkeypatch.delenv("XAI_API_KEY")

    made = shell("expand", "--template", starter, "--in", inputs, "--out", str(script))
    assert made["exit"] == 0, made
    written = json.loads(script.read_text(encoding="utf-8"))
    assert written["budget"] == {"maxUsd": 5} and written["template"] == {"id": starter, "templateVersion": 1}
    assert written["brand"] == json.loads((folder / "brand.json").read_text(encoding="utf-8"))

    checked = shell("validate", *source)
    assert checked["exit"] == 0 and _data(checked)["problems"] == []
    planned = shell("plan", *source)
    plan = _data(planned)
    paid = [step for step in _list(plan, "steps") if step["paid"]]
    assert planned["exit"] == 0 and len(paid) == 5 and shell("plan", *source)["data"] == plan
    assert all((step["provider"], step["model"]) == ("xai-speech", "tts") for step in paid)

    priced = shell("estimate", *source)
    estimate = _data(priced)
    assert priced["exit"] == 0 and estimate["budget"] == STARTER_CAP
    assert 0 < Decimal(str(estimate["usd"])) < 5 and len(str(estimate["usd"]).partition(".")[2]) == 6
    assert [entry["env"] for entry in _list(estimate, "secretsNeeded")] == ["XAI_API_KEY"]
    assert _codes(shell("estimate", *source, "--max-usd", "0.001")) == ["E_BUDGET"]
    assert _codes(shell("render", *source, "--max-usd", "0.001")) == ["E_BUDGET"]
    assert _codes(shell("render", *source)) == ["E_NO_KEY"]
    assert fake_speech.seen == [] and _data(shell("jobs"))["jobs"] == []

    monkeypatch.setenv("XAI_API_KEY", KEY)
    rendered = shell("render", *source)
    assert rendered["exit"] == 0, rendered
    render = _data(rendered)
    spoken = [step for step in _list(render, "steps") if step["provider"] == "xai-speech"]
    assert len(spoken) == 5 == len(fake_speech.seen) and all(step["cached"] is False for step in spoken)
    assert render["costUsd"] == estimate["usd"] and render["planSha256"] == plan["planSha256"]
    assert (folder / "out" / "video.mp4").is_file()

    verified = shell("verify", "--in", _saved(folder, {"jobId": render["jobId"]}, "job.json"))
    assert verified["exit"] == 0 and _data(verified)["passed"] is True
    lines = _list(_data(shell("ledger")), "lines")
    assert len(lines) == 5 and format(sum(Decimal(str(line["usd"])) for line in lines), ".6f") == estimate["usd"]
    manifest = json.loads(Path(str(render["manifest"])).read_text(encoding="utf-8"))
    assert manifest["template"] == {"id": starter, "templateVersion": 1}
    assert manifest["costUsd"] == estimate["usd"] and manifest["planSha256"] == plan["planSha256"]

    assert _data(shell("estimate", *source))["usd"] == "0.000000"
    again = shell("render", *source, "--max-usd", "0")
    assert again["exit"] == 0 and _data(again)["costUsd"] == "0.000000" and len(fake_speech.seen) == 5
    assert all(step["cached"] is True for step in _list(_data(again), "steps"))
    assert len(_list(_data(shell("ledger")), "lines")) == 5

    for text in (script.read_text(encoding="utf-8"), json.dumps(plan), json.dumps(manifest)):
        assert hits(text) == []
    held = b"".join(path.read_bytes() for path in sorted(tmp_path.rglob("*")) if path.is_file())
    assert KEY.encode() not in held
