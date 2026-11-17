from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = os.pathsep.join([str(ROOT / "src"), str(ROOT / "vendor")])


def run(home: Path, cwd: Path, args: list[str], script: str | None = None) -> dict[str, object]:
    path = cwd / "input.yaml"
    if script is not None:
        path.write_text(script, encoding="utf-8")
        args = [*args, "--in", str(path)]
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = ENV_PATH
    env["AMBITIOUS_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "ambitious_video.cli.main", *args, "--now", "2026-01-01T00:00:00Z", "--seed", "1"],
        cwd=cwd,
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


def test_minimal_render_is_repeatable(tmp_path: Path) -> None:
    source = ROOT / "examples"
    work = tmp_path / "work"
    work.mkdir()
    (work / "assets").mkdir()
    (work / "assets" / "still.png").write_bytes((source / "assets" / "still.png").read_bytes())
    script = (source / "minimal.yaml").read_text(encoding="utf-8")
    first = run(tmp_path / "home-a", work, ["render"], script.replace("out/minimal.mp4", "one.mp4"))
    second = run(tmp_path / "home-b", work, ["render"], script.replace("out/minimal.mp4", "two.mp4"))
    assert first["exit"] == 0 and second["exit"] == 0
    data_a = first["data"]
    data_b = second["data"]
    assert isinstance(data_a, dict) and isinstance(data_b, dict)
    assert data_a["sha256"] == data_b["sha256"]
    assert data_a["durationSec"] == 7
    assert data_a["costUsd"] == "0.000000"
    checked = subprocess_verify(tmp_path / "home-a", work, str(data_a["jobId"]))
    assert checked["exit"] == 0
    body = checked["data"]
    assert isinstance(body, dict) and body["passed"] is True


def subprocess_verify(home: Path, cwd: Path, job_id: str) -> dict[str, object]:
    target = cwd / "job.json"
    target.write_text(json.dumps({"jobId": job_id}), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = ENV_PATH
    env["AMBITIOUS_HOME"] = str(home)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ambitious_video.cli.main",
            "verify",
            "--in",
            str(target),
            "--now",
            "2026-01-01T00:00:00Z",
            "--seed",
            "1",
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    payload["exit"] = result.returncode
    return payload


def test_a_cut_between_title_cards_matches(tmp_path: Path) -> None:
    script = "\n".join(
        [
            "schemaVersion: 2",
            "output: { path: cut.mp4, width: 320, height: 180, fps: 10 }",
            "tracks:",
            "  - id: main",
            "    clips:",
            '      - { type: title_card, duration: 1.0, text: "One" }',
            '      - { type: title_card, duration: 1.0, text: "Two" }',
            "cuts:",
            "  - { at: 1.0, transition: crossfade, duration: 0.2 }",
            "",
        ]
    )
    rendered = run(tmp_path / "home", tmp_path, ["render"], script)
    assert rendered["exit"] == 0, rendered
    data = rendered["data"]
    assert isinstance(data, dict)
    checked = subprocess_verify(tmp_path / "home", tmp_path, str(data["jobId"]))
    assert checked["exit"] == 0, checked
    body = checked["data"]
    assert isinstance(body, dict)
    checks = body["checks"]
    assert isinstance(checks, list)
    names = [item["name"] for item in checks if isinstance(item, dict)]
    assert "cut-10" in names
    assert all(isinstance(item, dict) and item["ok"] is True for item in checks)


def test_speech_without_a_key_is_refused(tmp_path: Path) -> None:
    script = "\n".join(
        [
            "schemaVersion: 2",
            "output: { path: talk.mp4, width: 320, height: 180, fps: 10 }",
            "tracks:",
            "  - id: main",
            "    clips:",
            '      - { type: title_card, duration: 1.0, text: "Hi" }',
            "  - id: voice",
            "    clips:",
            "      - type: speech",
            "        at: 0",
            "        duration: 0.5",
            '        text: "Hi"',
            "        provider: xai-speech",
            "        model: tts",
            "        voice: eve",
            "",
        ]
    )
    rendered = run(tmp_path / "home", tmp_path, ["render"], script)
    assert rendered["exit"] == 1
    errors = rendered["errors"]
    assert isinstance(errors, list) and isinstance(errors[0], dict)
    assert errors[0]["code"] == "E_NO_KEY" and "XAI_API_KEY" in errors[0]["hint"]
    assert rendered["data"] == {"env": "XAI_API_KEY", "provider": "xai-speech"}


def test_all_clip_types_estimate_matches_the_price_table(tmp_path: Path) -> None:
    script = (ROOT / "examples" / "all-clip-types.yaml").read_text(encoding="utf-8")
    estimated = run(tmp_path / "home", tmp_path, ["estimate"], script)
    assert estimated["exit"] == 0, estimated
    data = estimated["data"]
    assert isinstance(data, dict)
    assert data["usd"] == "0.003585"
    assert data["durationSec"] == 33
