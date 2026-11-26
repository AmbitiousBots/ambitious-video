from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from test_render import ENV_PATH, ROOT, run, subprocess_verify

EXAMPLE = ROOT / "examples" / "titles-and-captions.json"


def data_of(payload: dict[str, object]) -> dict[str, object]:
    data = payload["data"]
    assert isinstance(data, dict)
    return data


def names_in(listed: object) -> list[str]:
    assert isinstance(listed, list)
    return sorted(str(item["name"]) for item in listed if isinstance(item, dict))


def selfcheck_with(home: Path, stub: Path, body: str) -> dict[str, object]:
    package = stub / "playwright"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(body, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = os.pathsep.join([str(stub), ENV_PATH])
    env["AMBITIOUS_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "ambitious_video.cli.main", "selfcheck", "--now", "2026-01-01T00:00:00Z", "--seed", "1"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.endswith("\n") and "\n" not in result.stdout[:-1], result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    payload["exit"] = result.returncode
    return payload


def test_guide_prints_the_example_script(tmp_path: Path) -> None:
    guided = run(tmp_path / "home", tmp_path, ["guide"])
    assert guided["exit"] == 0 and guided["ok"] is True and guided["command"] == "guide"
    data = data_of(guided)
    example = data["example"]
    assert isinstance(example, dict)
    assert example["script"] == EXAMPLE.read_text(encoding="utf-8")
    assert example["repositoryPath"] == "examples/titles-and-captions.json"
    notes = data["notes"]
    assert isinstance(notes, list) and any("use a speech clip" in str(note) for note in notes)
    assert isinstance(data["steps"], list) and data["steps"]


def test_the_example_is_title_cards_captions_and_one_crossfade() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    script = json.loads(text)
    assert "voice" not in script and "brand" not in script
    kinds = {clip["type"] for track in script["tracks"] for clip in track.get("clips", [])}
    assert kinds == {"title_card", "voiceover"}
    assert [track["from"] for track in script["tracks"] if "from" in track] == ["voice"]
    assert len(script["cuts"]) == 1 and script["cuts"][0]["transition"] == "crossfade"


def test_the_guide_script_validates_and_renders_without_a_key(tmp_path: Path) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    example = data_of(run(home, work, ["guide"]))["example"]
    assert isinstance(example, dict)
    script = str(example["script"])
    assert sorted(path.name for path in work.iterdir()) == []
    validated = run(home, work, ["validate"], script)
    assert validated["exit"] == 0, validated
    assert data_of(validated) == {"ok": True, "durationSec": 6, "problems": []}
    estimated = run(home, work, ["estimate"], script)
    assert estimated["exit"] == 0 and data_of(estimated)["usd"] == "0.000000"
    rendered = run(home, work, ["render"], script)
    assert rendered["exit"] == 0, rendered
    made = data_of(rendered)
    assert made["costUsd"] == "0.000000" and made["durationSec"] == 6
    assert (work / "out" / "titles-and-captions.mp4").is_file()
    checked = subprocess_verify(home, work, str(made["jobId"]))
    assert checked["exit"] == 0, checked
    checks = data_of(checked)["checks"]
    assert isinstance(checks, list)
    assert "cut-90" in [item["name"] for item in checks if isinstance(item, dict)]


def test_help_flag_prints_one_ok_envelope(tmp_path: Path) -> None:
    home = tmp_path / "home"
    listed = data_of(run(home, tmp_path, ["commands"]))["commands"]
    names = names_in(listed)
    for args in (["-h"], ["help"], ["-h", "--no-such-flag"]):
        shown = run(home, tmp_path, args)
        assert shown["exit"] == 0 and shown["ok"] is True and shown["errors"] == [], args
        assert shown["command"] == "help"
        data = data_of(shown)
        assert str(data["usage"]).startswith("ambitious-video <command>")
        assert names_in(data["commands"]) == names
        flags = data["flags"]
        assert isinstance(flags, list)
        assert {"--help", "--version"} <= {str(item["flag"]) for item in flags if isinstance(item, dict)}
    bare = run(home, tmp_path, ["--help"])
    assert bare["exit"] == 0 and bare["ok"] is True and bare["errors"] == []
    assert bare["command"] == "commands" and data_of(bare)["commands"] == listed
    focused = run(home, tmp_path, ["selfcheck", "--help"])
    assert focused["exit"] == 0 and focused["ok"] is True and focused["errors"] == []
    assert focused["command"] == "selfcheck"
    assert isinstance(listed, list)
    only = [entry for entry in listed if isinstance(entry, dict) and entry["name"] == "selfcheck"]
    assert data_of(focused)["commands"] == only
    refused = run(home, tmp_path, ["--help", "--no-such-flag"])
    assert refused["exit"] == 1 and refused["ok"] is False
    errors = refused["errors"]
    assert isinstance(errors, list) and isinstance(errors[0], dict)
    assert errors[0]["code"] == "E_USAGE"
    assert not home.exists()


def test_version_flag_reports_the_version(tmp_path: Path) -> None:
    for args in (["--version"], ["version"], ["render", "--version"]):
        shown = run(tmp_path / "home", tmp_path, args)
        assert shown["exit"] == 0 and shown["ok"] is True and shown["errors"] == [], args
        assert shown["command"] == "version"
        assert data_of(shown) == {"version": "0.1.1"}
        meta = shown["meta"]
        assert isinstance(meta, dict)
        assert meta == {"now": "2026-01-01T00:00:00.000Z", "seed": 1, "version": "0.1.1"}


def test_usage_errors_are_still_usage_errors(tmp_path: Path) -> None:
    for args in ([], ["no-such-command"], ["selfcheck", "--no-such-flag"]):
        refused = run(tmp_path / "home", tmp_path, args)
        assert refused["exit"] == 1 and refused["ok"] is False, args
        errors = refused["errors"]
        assert isinstance(errors, list) and isinstance(errors[0], dict)
        assert errors[0]["code"] == "E_USAGE"


def test_selfcheck_warns_when_playwright_is_missing(tmp_path: Path) -> None:
    checked = selfcheck_with(tmp_path / "home", tmp_path / "stub", 'raise ImportError("not installed")\n')
    assert checked["exit"] == 0 and checked["ok"] is True and checked["errors"] == []
    data = data_of(checked)
    assert data["problems"] == []
    warnings = data["warnings"]
    assert isinstance(warnings, list) and len(warnings) == 1
    assert "Playwright is not installed" in str(warnings[0]) and "screen_recording" in str(warnings[0])
    tools = data["tools"]
    assert isinstance(tools, dict) and isinstance(tools["playwright"], dict)
    assert tools["playwright"]["ok"] is False
    assert "screen_recording" in str(tools["playwright"]["hint"])


def test_selfcheck_has_no_warning_when_playwright_is_present(tmp_path: Path) -> None:
    checked = selfcheck_with(tmp_path / "home", tmp_path / "stub", '__version__ = "9.9.9"\n')
    assert checked["exit"] == 0 and checked["ok"] is True
    data = data_of(checked)
    assert data["warnings"] == [] and data["problems"] == []
    tools = data["tools"]
    assert isinstance(tools, dict)
    assert tools["playwright"] == {"version": "9.9.9", "ok": True}
