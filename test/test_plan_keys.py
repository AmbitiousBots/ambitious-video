from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ambitious_video.cli import engine
from ambitious_video.domain.plan import StepSpec, canonical, plan_sha, provider_seed, step_key

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ENV_PATH = os.pathsep.join([str(ROOT / "src"), str(ROOT / "vendor")])
PROJECTS = {
    "minimal": EXAMPLES / "minimal.json",
    "example-plumbing": EXAMPLES / "samples" / "example-plumbing" / "script.json",
    "example-accounting": EXAMPLES / "samples" / "example-accounting" / "script.json",
    "example-fitness": EXAMPLES / "samples" / "example-fitness" / "script.json",
    "example-bookshop": EXAMPLES / "samples" / "example-bookshop" / "script.json",
}
NAMES = sorted(PROJECTS)
SPOKEN = {"type": "speech", "provider": "xai-speech", "model": "tts", "voice": "eve"}


def _script(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _plan_line(script: Path, home: Path, now: str, seed: str) -> str:
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = ENV_PATH
    env["AMBITIOUS_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "ambitious_video.cli.main", "plan", "--in", str(script), "--now", now, "--seed", seed],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _data_bytes(line: str) -> str:
    start = line.index('"data":') + len('"data":')
    end = line.rindex(',"errors":')
    return line[start:end]


def _copy(name: str, dest: Path) -> tuple[dict[str, object], Path]:
    source = PROJECTS[name]
    folder = dest / name
    if name == "minimal":
        (folder / "assets").mkdir(parents=True)
        shutil.copy(EXAMPLES / "assets" / "still.png", folder / "assets" / "still.png")
    else:
        shutil.copytree(source.parent, folder)
    script = _script(source)
    tracks = script["tracks"]
    assert isinstance(tracks, list)
    for track in tracks:
        for clip in track.get("clips", []):
            if clip["type"] == "voiceover":
                clip.update(SPOKEN)
    return script, folder


def _keys(plan: dict[str, object]) -> list[str]:
    steps = plan["steps"]
    assert isinstance(steps, list)
    return [step["stepKey"] for step in steps]


def _plan(script: dict[str, object], folder: Path) -> dict[str, object]:
    loaded = json.loads(canonical(engine.plan_data(script, folder)))
    assert isinstance(loaded, dict)
    return loaded


def _first(script: dict[str, object], kind: str) -> dict[str, object]:
    tracks = script["tracks"]
    assert isinstance(tracks, list)
    for track in tracks:
        for clip in track.get("clips", []):
            if clip["type"] == kind:
                assert isinstance(clip, dict)
                return clip
    raise AssertionError(kind)


@pytest.mark.parametrize("name", NAMES)
def test_plan_bytes_do_not_depend_on_the_process_or_the_clock(name: str, tmp_path: Path) -> None:
    first = _plan_line(PROJECTS[name], tmp_path / "home-a", "2026-01-01T00:00:00Z", "1")
    second = _plan_line(PROJECTS[name], tmp_path / "home-b", "2031-07-09T12:34:56Z", "1")
    other_seed = _plan_line(PROJECTS[name], tmp_path / "home-c", "2026-01-01T00:00:00Z", "99")
    assert _data_bytes(first) == _data_bytes(second) == _data_bytes(other_seed)
    data = json.loads(_data_bytes(first))
    assert data["planSha256"] == plan_sha(data)
    assert len(data["planSha256"]) == 64
    assert data["scriptSchemaVersion"] == 2
    assert _data_bytes(first) == canonical(data)
    assert str(ROOT) not in first.replace(str(PROJECTS[name]), "")
    assert "2026-01-01" not in _data_bytes(first) and str(tmp_path) not in first


@pytest.mark.parametrize("name", NAMES)
def test_plan_in_one_process_is_the_same_twice(name: str) -> None:
    folder = PROJECTS[name].parent
    first = canonical(engine.plan_data(_script(PROJECTS[name]), folder))
    second = canonical(engine.plan_data(_script(PROJECTS[name]), folder))
    assert first == second
    assert all(len(key) == 64 for key in _keys(json.loads(first)))


@pytest.mark.parametrize("name", NAMES)
def test_changed_text_changes_only_downstream_step_keys(name: str, tmp_path: Path) -> None:
    script, folder = _copy(name, tmp_path)
    before = _plan(script, folder)
    line = _first(script, "speech")
    line["text"] = str(line["text"]) + " Changed."
    after = _plan(script, folder)
    changed = [index for index, (old, new) in enumerate(zip(_keys(before), _keys(after), strict=True)) if old != new]
    steps = after["steps"]
    assert isinstance(steps, list)
    assert len(changed) == 1 and steps[changed[0]]["text"].endswith(" Changed.")
    assert steps[changed[0]]["paid"] is True
    assert (steps[changed[0]]["provider"], steps[changed[0]]["model"]) == ("xai-speech", "tts")
    assert before["output"] != after["output"]
    assert before["planSha256"] != after["planSha256"]
    assert before["assets"] == after["assets"]


@pytest.mark.parametrize("name", NAMES)
def test_changed_picture_changes_only_downstream_step_keys(name: str, tmp_path: Path) -> None:
    script, folder = _copy(name, tmp_path)
    before = _plan(script, folder)
    picture = str(_first(script, "image")["path"])
    with (folder / picture).open("ab") as handle:
        handle.write(b"one more byte")
    after = _plan(script, folder)
    steps = after["steps"]
    assert isinstance(steps, list)
    changed = [index for index, (old, new) in enumerate(zip(_keys(before), _keys(after), strict=True)) if old != new]
    assert changed and all(steps[index].get("path") == picture for index in changed)
    assert before["output"] != after["output"]
    assert before["planSha256"] != after["planSha256"]


def test_encoding_and_font_file_change_only_the_output_key(tmp_path: Path) -> None:
    script, folder = _copy("minimal", tmp_path)
    (folder / "font.ttf").write_bytes(b"first font bytes")
    script["brand"] = {"font": "font.ttf"}
    before = _plan(script, folder)
    output = script["output"]
    assert isinstance(output, dict)
    output["encode"] = "lossless"
    stored = _plan(script, folder)
    (folder / "font.ttf").write_bytes(b"other font bytes")
    drawn = _plan(script, folder)
    assert _keys(before) == _keys(stored) == _keys(drawn)
    assert before["encode"] == "web" and stored["encode"] == "lossless"
    assert len({canonical(plan["output"]) for plan in (before, stored, drawn)}) == 3


def test_paid_step_key_names_the_content_not_the_job_or_the_place() -> None:
    def speech_keys(script: dict[str, object], folder: Path) -> dict[str, str]:
        steps = _plan(script, folder)["steps"]
        assert isinstance(steps, list)
        return {step["text"]: step["stepKey"] for step in steps if step["paid"]}

    line = "The same words, spoken once."
    base = {"schemaVersion": 2}
    one = {
        **base,
        "output": {"path": "one.mp4", "width": 320, "height": 180, "fps": 10},
        "tracks": [{"id": "voice", "clips": [{**SPOKEN, "at": 0, "text": line}]}],
    }
    two = {
        **base,
        "output": {"path": "two.mp4", "width": 640, "height": 360, "fps": 30},
        "tracks": [
            {"id": "main", "clips": [{"type": "title_card", "duration": 9.0, "text": "Other"}]},
            {"id": "voice", "clips": [{**SPOKEN, "at": 4.5, "duration": 2.0, "text": line}]},
        ],
    }
    assert speech_keys(one, ROOT)[line] == speech_keys(two, ROOT / "examples")[line]
    other_voice = {
        **one,
        "tracks": [{"id": "voice", "clips": [{**SPOKEN, "voice": "another", "at": 0, "text": line}]}],
    }
    assert speech_keys(one, ROOT)[line] != speech_keys(other_voice, ROOT)[line]


def test_step_key_covers_every_field_and_seeds_come_from_seed_and_key() -> None:
    base = StepSpec("speech", "xai", "tts", 1, {"text": "hello"}, None, ())
    variants = [
        StepSpec("scene", "xai", "tts", 1, {"text": "hello"}, None, ()),
        StepSpec("speech", "other", "tts", 1, {"text": "hello"}, None, ()),
        StepSpec("speech", "xai", "tts-2", 1, {"text": "hello"}, None, ()),
        StepSpec("speech", "xai", "tts", 2, {"text": "hello"}, None, ()),
        StepSpec("speech", "xai", "tts", 1, {"text": "hello!"}, None, ()),
        StepSpec("speech", "xai", "tts", 1, {"text": "hello"}, 7, ()),
        StepSpec("speech", "xai", "tts", 1, {"text": "hello"}, None, ("ab" * 32,)),
    ]
    keys = {step_key(spec) for spec in [base, *variants]}
    assert len(keys) == len(variants) + 1
    assert step_key(base) == step_key(StepSpec("speech", "xai", "tts", 1, {"text": "hello"}, None, ()))
    key = step_key(base)
    assert provider_seed(1, key) == provider_seed(1, key)
    assert provider_seed(1, key) != provider_seed(2, key)
    assert provider_seed(1, key) != provider_seed(1, step_key(variants[0]))
    assert 0 <= provider_seed(1, key) < 2**32
