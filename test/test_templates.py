from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

from ambitious_video.cli import engine
from ambitious_video.cli import expand as expander
from ambitious_video.domain.load import load_script
from ambitious_video.domain.plan import canonical, plan_sha
from ambitious_video.domain.template import Expansion, expand, load_template

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
PACKAGED = ROOT / "src" / "ambitious_video" / "templates"
SAMPLES = ROOT / "examples" / "samples"
ENV_PATH = os.pathsep.join([str(ROOT / "src"), str(ROOT / "vendor")])
STARTERS = ["announcement", "short-explainer", "welcome"]
USES = {
    "example-accounting": "short-explainer",
    "example-bookshop": "welcome",
    "example-fitness": "announcement",
    "example-plumbing": "welcome",
}
SLUGS = sorted(USES)
FIRST = ("2026-01-01T00:00:00Z", "1")
LATER = ("2031-07-09T12:34:56Z", "99")
SMALL = {"width": 320, "height": 180, "fps": 10}
UPRIGHT = {"width": 180, "height": 320, "fps": 10}


def _yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _schema(name: str) -> jsonschema.Draft202012Validator:
    loaded = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(loaded)
    return jsonschema.Draft202012Validator(loaded)


def _cli(home: Path, args: list[str], clock: tuple[str, str] = FIRST) -> tuple[dict[str, object], str]:
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = ENV_PATH
    env["AMBITIOUS_HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "ambitious_video.cli.main", *args, "--now", clock[0], "--seed", clock[1]],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.endswith("\n") and "\n" not in result.stdout[:-1], result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict) and result.stderr == ""
    payload["exit"] = result.returncode
    return payload, result.stdout


def _data_bytes(line: str) -> str:
    start = line.index('"data":') + len('"data":')
    end = line.rindex(',"errors":')
    return line[start:end]


def _data(payload: dict[str, object]) -> dict[str, object]:
    data = payload["data"]
    assert isinstance(data, dict)
    return data


def _problems(payload: dict[str, object]) -> list[tuple[str, str, str]]:
    listed = _data(payload)["problems"]
    assert isinstance(listed, list)
    return [(item["in"], item["at"], item["message"]) for item in listed]


def _copied(slug: str, dest: Path) -> Path:
    folder = dest / slug
    shutil.copytree(SAMPLES / slug, folder)
    (folder / "script.yaml").unlink()
    return folder


def _starter(name: str) -> dict[str, object]:
    return _yaml(TEMPLATES / f"{name}.yaml")


def _expanded(template: dict[str, object], inputs: dict[str, object], brand: object | None = None) -> Expansion:
    loaded, problems = load_template(template)
    assert loaded is not None, problems
    return expand(loaded, inputs, brand)


def _sample_script(slug: str, change: dict[str, object] | None = None) -> dict[str, object]:
    document = _yaml(SAMPLES / slug / "inputs.yaml")
    inputs = document["inputs"]
    assert isinstance(inputs, dict)
    inputs.update(change or {})
    made = _expanded(_starter(USES[slug]), document, _yaml(SAMPLES / slug / "brand.yaml"))
    assert made.script is not None, made.problems
    return dict(made.script)


def _keys(plan: dict[str, object]) -> list[str]:
    steps = plan["steps"]
    assert isinstance(steps, list)
    return [step["stepKey"] for step in steps]


def _plan(script: dict[str, object], folder: Path) -> dict[str, object]:
    loaded = json.loads(canonical(engine.plan_data(script, folder)))
    assert isinstance(loaded, dict)
    return loaded


CUSTOM: dict[str, object] = {
    "templateVersion": 1,
    "id": "shop-news",
    "title": "Shop news",
    "inputs": {
        "name": {"type": "text", "required": True, "maxLength": 20},
        "mood": {"type": "choice", "options": ["calm", "bright"], "default": "calm"},
        "paper": {"type": "color", "default": "#101010"},
        "mark": {"type": "image"},
        "clip": {"type": "video"},
        "voice": {"type": "voice"},
        "tune": {"type": "audio"},
    },
    "brand": {"colors": {"bg": "{{paper}}", "fg": "#FFFFFF", "accent": "#3366AA"}},
    "output": {"path": "out/news.mp4", "width": 320, "height": 180, "fps": 10},
    "budget": {"maxUsd": 2.5},
    "speech": {"provider": "xai-speech", "model": "tts", "voice": "{{voice}}"},
    "music": {"path": "{{tune}}", "gain_db": -20},
    "fallbacks": {"speech": "captions", "music": "none"},
    "disclosure": "AI-generated scenes",
    "scenes": [
        {
            "id": "hello",
            "show": {"type": "title_card", "duration": 2, "text": "{{ name }}", "subtitle": "A {{mood}} day"},
            "say": [{"at": 0.5, "text": "Hello from {{name}}."}],
            "cut": {"transition": "crossfade", "duration": 0.2},
        },
        {
            "id": "moving",
            "show": {"type": "video", "path": "{{clip}}", "in": 1.0, "out": 2.5},
            "fallback": {"type": "title_card", "duration": 1.5, "text": "{{name}} in pictures"},
            "say": [{"text": "Take a look.", "duration": 1.0}],
        },
    ],
    "endCard": {"duration": 2.0, "text": "See you at {{name}}", "say": [{"at": 0.25, "text": "Goodbye."}]},
}
FILLED = {"name": "Corner Shop", "mood": "bright", "clip": "assets/walk.mp4", "voice": "narrator"}
FILLED["tune"] = "assets/bed.mp3"


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("expand must not open a socket")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("socket.create_connection", refuse)
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def test_the_three_starter_templates_are_present_and_packaged() -> None:
    assert sorted(path.stem for path in TEMPLATES.glob("*.yaml")) == STARTERS
    assert list(expander.starter_ids()) == STARTERS
    for name in STARTERS:
        text = (TEMPLATES / f"{name}.yaml").read_text(encoding="utf-8")
        assert (PACKAGED / f"{name}.yaml").read_text(encoding="utf-8") == text
        assert expander.starter_text(name) == text


@pytest.mark.parametrize("name", STARTERS)
def test_starter_template_matches_the_schema_and_caps_spend_at_five_dollars(name: str) -> None:
    template = _starter(name)
    _schema("video-template.v1.json").validate(template)
    loaded, problems = load_template(template)
    assert loaded is not None and problems == ()
    assert (loaded.id, template["templateVersion"]) == (name, 1)
    assert template["budget"] == {"maxUsd": 5}
    assert template["brand"] == {"file": "brand.yaml"}
    assert loaded.fallbacks == {"speech": "captions", "music": "none"}
    assert loaded.speech == {"provider": "xai-speech", "model": "tts", "voice": "{{voice}}"}
    assert all(spec.description for spec in loaded.inputs.values())
    assert "example" not in (TEMPLATES / f"{name}.yaml").read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("slug", SLUGS)
def test_expand_and_plan_bytes_do_not_depend_on_the_process_or_the_clock(slug: str, tmp_path: Path) -> None:
    template = str(TEMPLATES / f"{USES[slug]}.yaml")
    folders = [_copied(slug, tmp_path / "a"), _copied(slug, tmp_path / "b")]
    lines: list[str] = []
    plans: list[str] = []
    for folder, clock, home in zip(folders, (FIRST, LATER), ("home-a", "home-b"), strict=True):
        source = ["--template", template, "--in", str(folder / "inputs.yaml"), "--out", str(folder / "script.yaml")]
        made, line = _cli(tmp_path / home, ["expand", *source], clock)
        assert made["exit"] == 0, made
        lines.append(_data_bytes(line).replace(str(folder), ""))
        planned, plan_line = _cli(tmp_path / home, ["plan", "--in", str(folder / "script.yaml")], clock)
        assert planned["exit"] == 0, planned
        plans.append(_data_bytes(plan_line))
        assert not (tmp_path / home).exists()
    assert lines[0] == lines[1] and plans[0] == plans[1]
    written = [(folder / "script.yaml").read_bytes() for folder in folders]
    assert written[0] == written[1] == (SAMPLES / slug / "script.yaml").read_bytes()

    data = json.loads(lines[0])
    assert lines[0] == canonical(data)
    assert data["template"]["id"] == USES[slug] and data["template"]["templateVersion"] == 1
    assert data["script"] == yaml.safe_load(written[0])
    assert data["scriptSha256"] == expander.digest_text(canonical(data["script"]))
    assert "2026-01-01" not in lines[0] and str(tmp_path) not in lines[0] and str(ROOT) not in lines[0]

    plan = json.loads(plans[0])
    assert plan["planSha256"] == plan_sha(plan) and len(plan["planSha256"]) == 64
    assert plan["template"] == {"id": USES[slug], "templateVersion": 1}
    assert plans[0] == canonical(plan) and str(tmp_path) not in plans[0]


@pytest.mark.parametrize("slug", SLUGS)
def test_changing_one_input_changes_only_downstream_step_keys(slug: str, no_network: None) -> None:
    folder = SAMPLES / slug
    before = _plan(_sample_script(slug), folder)
    assert before == _plan(_sample_script(slug), folder)

    spoken = _plan(_sample_script(slug, {"closing_line": "A new last line."}), folder)
    changed = [index for index, (old, new) in enumerate(zip(_keys(before), _keys(spoken), strict=True)) if old != new]
    steps = spoken["steps"]
    assert isinstance(steps, list)
    assert len(changed) == 1 and steps[changed[0]]["text"] == "A new last line."
    assert before["output"] != spoken["output"] and before["planSha256"] != spoken["planSha256"]
    assert before["assets"] == spoken["assets"]

    shown = _plan(_sample_script(slug, {"call_to_action": "A new last card"}), folder)
    changed = [index for index, (old, new) in enumerate(zip(_keys(before), _keys(shown), strict=True)) if old != new]
    cards = shown["steps"]
    assert isinstance(cards, list)
    assert len(changed) == 1 and cards[changed[0]]["type"] == "title_card"


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_goes_from_its_template_and_brand_to_a_verified_video(slug: str, tmp_path: Path) -> None:
    folder = _copied(slug, tmp_path)
    home = tmp_path / "home"
    document = _yaml(folder / "inputs.yaml")
    inputs = document["inputs"]
    assert isinstance(inputs, dict)
    inputs["voice"] = "narrator"
    size = UPRIGHT if USES[slug] == "announcement" else SMALL
    document["output"] = {"path": "out/video.mp4", **size, "encode": "lossless"}
    (folder / "inputs.json").write_text(json.dumps(document), encoding="utf-8")
    script = folder / "video.yaml"
    source = ["--in", str(script)]

    filled = ["--template", USES[slug], "--in", str(folder / "inputs.json"), "--out", str(script)]
    made, _line = _cli(home, ["expand", *filled])
    assert made["exit"] == 0, made
    expanded = _data(made)
    assert expanded["fallbacks"] == [{"step": "music", "scene": None, "fallback": "none", "missing": ["music"]}]
    assert expanded["written"] == str(script)
    assert _yaml(script)["brand"] == _yaml(folder / "brand.yaml")

    checked, _line = _cli(home, ["validate", *source])
    assert checked["exit"] == 0 and _data(checked)["problems"] == []
    planned, _line = _cli(home, ["plan", *source])
    plan = _data(planned)
    steps = plan["steps"]
    assert isinstance(steps, list)
    paid = [step for step in steps if step["paid"]]
    assert planned["exit"] == 0 and len(paid) == 5
    assert all((step["provider"], step["model"], step["voice"]) == ("xai-speech", "tts", "narrator") for step in paid)
    priced, _line = _cli(home, ["estimate", *source])
    estimate = _data(priced)
    needed = estimate["secretsNeeded"]
    assert isinstance(needed, list)
    assert [entry["env"] for entry in needed] == ["XAI_API_KEY"] == [entry["env"] for entry in plan["secretsNeeded"]]
    assert 0 < float(str(estimate["usd"])) < 5 and str(estimate["usd"])[::-1].index(".") == 6

    offline, _line = _cli(home, ["estimate", "--offline", *source])
    assert _data(offline)["usd"] == "0.000000" and _data(offline)["secretsNeeded"] == []
    assert not home.exists()
    rendered, _line = _cli(home, ["render", "--offline", *source])
    render = _data(rendered)
    assert rendered["exit"] == 0 and render["costUsd"] == "0.000000"
    fallen = render["steps"]
    assert isinstance(fallen, list)
    assert len(fallen) == 5 and all(step["fallback"] == "captions" for step in fallen)
    assert (folder / "out" / "video.mp4").is_file()
    (folder / "job.json").write_text(json.dumps({"jobId": render["jobId"]}), encoding="utf-8")
    verified, _line = _cli(home, ["verify", "--in", str(folder / "job.json")])
    assert verified["exit"] == 0 and _data(verified)["passed"] is True
    ledger, _line = _cli(home, ["ledger"])
    assert _data(ledger)["lines"] == []


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_script_is_what_expand_writes(slug: str, no_network: None) -> None:
    script = _sample_script(slug)
    kept = SAMPLES / slug / "script.yaml"
    assert expander.script_text(script, ".yaml") == kept.read_text(encoding="utf-8")
    assert kept.read_text(encoding="utf-8").startswith("# Written by the expand command.")
    assert _yaml(SAMPLES / slug / "inputs.yaml")["template"] == USES[slug]
    assert script["template"] == {"id": USES[slug], "templateVersion": 1} and script["budget"] == {"maxUsd": 5}
    _schema("video-script.v2.json").validate(script)
    types = {clip["type"] for track in script["tracks"] for clip in track.get("clips", [])}
    assert types == {"title_card", "image", "voiceover"}


def test_every_input_type_fills_its_slot(no_network: None) -> None:
    _schema("video-template.v1.json").validate(CUSTOM)
    made = _expanded(CUSTOM, {"template": "shop-news", "inputs": FILLED, "output": {"fps": 12}})
    assert made.problems == () and made.notes == () and made.script is not None
    script = made.script
    _schema("video-script.v2.json").validate(script)
    assert list(script) == ["schemaVersion", "template", "budget", "disclosure", "output", "brand", "tracks", "cuts"]
    assert script["template"] == {"id": "shop-news", "templateVersion": 1}
    assert script["budget"] == {"maxUsd": 2.5} and script["disclosure"] == "AI-generated scenes"
    assert script["output"] == {"path": "out/news.mp4", "width": 320, "height": 180, "fps": 12}
    assert script["brand"] == {"colors": {"bg": "#101010", "fg": "#FFFFFF", "accent": "#3366AA"}}
    spoken = {"provider": "xai-speech", "model": "tts", "voice": "narrator"}
    assert script["tracks"] == [
        {
            "id": "main",
            "clips": [
                {"type": "title_card", "duration": 2, "text": "Corner Shop", "subtitle": "A bright day"},
                {"type": "video", "path": "assets/walk.mp4", "in": 1.0, "out": 2.5},
                {"type": "title_card", "duration": 2.0, "text": "See you at Corner Shop"},
            ],
        },
        {
            "id": "voice",
            "clips": [
                {"type": "speech", "at": 0.5, "text": "Hello from Corner Shop.", **spoken},
                {"type": "speech", "at": 2.0, "duration": 1.0, "text": "Take a look.", **spoken},
                {"type": "speech", "at": 3.75, "text": "Goodbye.", **spoken},
            ],
        },
        {"id": "captions", "from": "voice"},
        {
            "id": "music",
            "clips": [{"type": "audio", "at": 0, "path": "assets/bed.mp3", "gain_db": -20, "duck_under": "voice"}],
        },
    ]
    assert script["cuts"] == [{"at": 2.0, "transition": "crossfade", "duration": 0.2}]
    loaded, problems = load_script(dict(script))
    assert loaded is not None and problems == ()
    assert loaded.template is not None and (loaded.template.id, loaded.template.version) == ("shop-news", 1)
    assert str(loaded.budget_usd) == "2.5" and loaded.disclosure == "AI-generated scenes"


def test_a_step_without_its_value_takes_the_fallback_the_template_names(no_network: None) -> None:
    made = _expanded(CUSTOM, {"inputs": {"name": "Corner Shop", "voice": None}})
    assert made.script is not None and made.problems == ()
    assert [(note.step, note.scene, note.fallback, note.missing) for note in made.notes] == [
        ("scene", "moving", "title_card", ("clip",)),
        ("speech", None, "captions", ("voice",)),
        ("music", None, "none", ("tune",)),
    ]
    tracks = made.script["tracks"]
    assert isinstance(tracks, list) and [track["id"] for track in tracks] == ["main", "voice", "captions"]
    assert tracks[0]["clips"][1] == {"type": "title_card", "duration": 1.5, "text": "Corner Shop in pictures"}
    assert tracks[0]["clips"][0]["subtitle"] == "A calm day"
    assert tracks[1]["clips"] == [
        {"type": "voiceover", "at": 0.5, "text": "Hello from Corner Shop."},
        {"type": "voiceover", "at": 2.0, "duration": 1.0, "text": "Take a look."},
        {"type": "voiceover", "at": 3.75, "text": "Goodbye."},
    ]
    plan = engine.plan_view(dict(made.script), ROOT, {"AMBITIOUS_HOME": str(ROOT / "nowhere")})
    assert plan["secretsNeeded"] == [] and not any(step["paid"] for step in plan["steps"])


def test_a_missing_value_without_a_fallback_is_an_error(no_network: None) -> None:
    strict = copy.deepcopy(CUSTOM)
    del strict["fallbacks"]
    scenes = strict["scenes"]
    assert isinstance(scenes, list)
    del scenes[1]["fallback"]
    made = _expanded(strict, {"inputs": {"name": "Corner Shop"}})
    assert made.script is None
    assert [(item.document, item.at, item.message) for item in made.problems] == [
        ("template", "/scenes/1/show", "Input clip has no value."),
        ("template", "/speech/voice", "Input voice has no value."),
        ("template", "/music/path", "Input tune has no value."),
    ]


def test_substitution_is_plain_and_a_value_is_never_expanded_again(no_network: None) -> None:
    made = _expanded(CUSTOM, {"inputs": {**FILLED, "name": "{{mood}} {{ 1 + 1 }}"}})
    assert made.script is not None
    tracks = made.script["tracks"]
    assert isinstance(tracks, list)
    assert tracks[0]["clips"][0]["text"] == "{{mood}} {{ 1 + 1 }}"
    assert tracks[1]["clips"][0]["text"] == "Hello from {{mood}} {{ 1 + 1 }}."


INPUT_PROBLEMS = [
    ({"inputs": {**FILLED, "extra": "x"}}, ("inputs", "/inputs/extra", "This input is not in the template.")),
    ({"inputs": {"mood": "calm"}}, ("inputs", "/inputs", "Input name is required.")),
    ({"inputs": {"name": None}}, ("inputs", "/inputs", "Input name is required.")),
    ({"inputs": {"name": 42}}, ("inputs", "/inputs/name", "This value must be text.")),
    ({"inputs": {"name": "x" * 21}}, ("inputs", "/inputs/name", "This text is empty or too long.")),
    ({"inputs": {"name": "A", "mood": "loud"}}, ("inputs", "/inputs/mood", "This value is not one of the options.")),
    ({"inputs": {"name": "A", "paper": "red"}}, ("inputs", "/inputs/paper", "This text has the wrong shape.")),
    ({"inputs": {"name": "A", "clip": "../walk.mp4"}}, ("inputs", "/inputs/clip", "This text has the wrong shape.")),
    ({"inputs": {"name": "A", "mark": "/etc/logo.png"}}, ("inputs", "/inputs/mark", "This text has the wrong shape.")),
    ({"inputs": {"name": "A"}, "template": "welcome"}, ("inputs", "/template", "The inputs name another template.")),
    ({"inputs": {"name": "A"}, "output": {"size": 4}}, ("inputs", "/output/size", "This field is not allowed.")),
    ({"inputs": {"name": "A"}, "output": {"width": 321}}, ("script", "/output/width", "This number must be even.")),
    ({"inputs": {"name": "A"}, "voice": "narrator"}, ("inputs", "/voice", "This field is not allowed.")),
    ({"inputs": ["name"]}, ("inputs", "/inputs", "This value must be an object.")),
]


@pytest.mark.parametrize(("document", "problem"), INPUT_PROBLEMS)
def test_an_unknown_missing_or_wrong_input_is_refused(
    document: dict[str, object], problem: tuple[str, str, str], no_network: None
) -> None:
    made = _expanded(CUSTOM, document)
    assert made.script is None
    assert problem in [(item.document, item.at, item.message) for item in made.problems]


def _changed(path: list[str | int], value: object) -> dict[str, object]:
    template = copy.deepcopy(CUSTOM)
    target: object = template
    for part in path[:-1]:
        target = target[part]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return template


FIRST_TEXT: list[str | int] = ["scenes", 0, "show", "text"]
SECOND_SHOW: list[str | int] = ["scenes", 1, "show"]
TEMPLATE_PROBLEMS = [
    (["templateVersion"], 2, "/templateVersion", "templateVersion must be 1.", True),
    (["id"], "Shop News", "/id", "This text has the wrong shape.", True),
    (["title"], None, "", "Field title is required.", True),
    (["scenes"], [], "/scenes", "This list is the wrong length.", True),
    (["extra"], 1, "/extra", "This field is not allowed.", True),
    (["inputs", "Name"], {"type": "text"}, "/inputs/Name", "This name has the wrong shape.", True),
    (["inputs", "mood", "type"], "number", "/inputs/mood/type", "This is not an input type.", True),
    (["inputs", "mood", "options"], None, "/inputs/mood", "A choice input needs options.", True),
    (["inputs", "name", "options"], ["a"], "/inputs/name/options", "Only a choice input has options.", True),
    (["inputs", "paper", "maxLength"], 7, "/inputs/paper/maxLength", "Only a text input has maxLength.", True),
    (["inputs", "paper", "default"], "red", "/inputs/paper/default", "This text has the wrong shape.", False),
    (["inputs", "mood", "default"], "loud", "/inputs/mood/default", "This value is not one of the options.", False),
    (FIRST_TEXT, "{{ name | upper }}", "/scenes/0/show/text", "This slot has the wrong shape.", False),
    (FIRST_TEXT, "{{name}", "/scenes/0/show/text", "This slot has the wrong shape.", False),
    (FIRST_TEXT, "{{owner}}", "/scenes/0/show/text", "Slot owner names no input.", False),
    (FIRST_TEXT, "{{mark}}", "/scenes/0/show/text", "Slot mark has the wrong type.", False),
    ([*SECOND_SHOW, "path"], "{{mark}}", "/scenes/1/show/path", "Slot mark has the wrong type.", False),
    ([*SECOND_SHOW, "path"], "clips/{{clip}}", "/scenes/1/show/path", "Use one whole slot or a plain value.", False),
    ([*SECOND_SHOW, "out"], None, "/scenes/1/show", "Field out is required.", True),
    ([*SECOND_SHOW, "type"], "screen_recording", "/scenes/1/show/type", "Use title_card, image or video.", True),
    (["scenes", 1, "id"], "hello", "/scenes", "Two scenes use the same id.", False),
    (["scenes", 0, "cut", "transition"], "wipe", "/scenes/0/cut/transition", "The transition must be crossfade.", True),
    (["scenes", 0, "say", 0, "words"], "x", "/scenes/0/say/0/words", "This field is not allowed.", True),
    (["speech", "voice"], "{{name}}", "/speech/voice", "Slot name has the wrong type.", False),
    (["speech", "model"], None, "/speech", "Field model is required.", True),
    (["fallbacks", "speech"], "silence", "/fallbacks/speech", "This fallback must be captions.", True),
    (["music", "path"], "{{clip}}", "/music/path", "Slot clip has the wrong type.", False),
    (["brand", "colors", "bg"], "{{name}}", "/brand/colors/bg", "Slot name has the wrong type.", False),
    (["brand", "file"], "../brand.yaml", "/brand/file", "This text has the wrong shape.", True),
    (["budget", "maxUsd"], 0, "/budget/maxUsd", "This number is too small.", True),
    (["output", "fps"], 0, "/output/fps", "This number is too small.", True),
    (["endCard", "duration"], None, "/endCard", "Field duration is required.", True),
]


@pytest.mark.parametrize(("path", "value", "at", "message", "in_schema"), TEMPLATE_PROBLEMS)
def test_a_template_with_a_mistake_is_refused(
    path: list[str | int], value: object, at: str, message: str, in_schema: bool
) -> None:
    template = _changed(path, value)
    loaded, problems = load_template(template)
    assert loaded is None
    assert ("template", at, message) in [(item.document, item.at, item.message) for item in problems]
    refused_by_schema = list(_schema("video-template.v1.json").iter_errors(template)) != []
    assert refused_by_schema is in_schema


def test_the_last_scene_cannot_cut_to_nothing() -> None:
    template = copy.deepcopy(CUSTOM)
    del template["endCard"]
    scenes = template["scenes"]
    assert isinstance(scenes, list)
    scenes[1]["cut"] = {"transition": "crossfade", "duration": 0.2}
    loaded, problems = load_template(template)
    assert loaded is None
    assert [(item.at, item.message) for item in problems] == [("/scenes/1/cut", "The last scene cannot have a cut.")]


def test_expand_reports_each_problem_with_its_document_and_place(tmp_path: Path) -> None:
    folder = _copied("example-plumbing", tmp_path)
    document = _yaml(folder / "inputs.yaml")
    inputs = document["inputs"]
    assert isinstance(inputs, dict)
    del inputs["tagline"]
    inputs["slogan"] = "Not an input"
    inputs["picture_1"] = "../still-1.png"
    (folder / "inputs.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    source = ["--template", "welcome", "--in", str(folder / "inputs.yaml"), "--out", str(folder / "script.yaml")]
    refused, _line = _cli(tmp_path / "home", ["expand", *source])
    assert refused["exit"] == 1 and refused["ok"] is False
    assert sorted(_problems(refused)) == [
        ("inputs", "/inputs", "Input tagline is required."),
        ("inputs", "/inputs/picture_1", "This text has the wrong shape."),
        ("inputs", "/inputs/slogan", "This input is not in the template."),
    ]
    errors = refused["errors"]
    assert isinstance(errors, list) and {error["code"] for error in errors} == {"E_INPUT_SCHEMA"}
    assert all(error["hint"] == "Fix the field named in data.problems." for error in errors)
    assert not (folder / "script.yaml").exists() and not (tmp_path / "home").exists()


def test_expand_refuses_a_broken_template_or_brand_file(tmp_path: Path) -> None:
    folder = _copied("example-fitness", tmp_path)
    source = ["--in", str(folder / "inputs.yaml")]
    broken = folder / "broken.yaml"
    template = _starter("announcement")
    scenes = template["scenes"]
    assert isinstance(scenes, list)
    scenes[0]["show"]["text"] = "{{ business_name + tagline }}"
    broken.write_text(yaml.safe_dump(template), encoding="utf-8")
    refused, _line = _cli(tmp_path / "home", ["expand", "--template", str(broken), *source])
    assert refused["exit"] == 1
    assert _problems(refused) == [("template", "/scenes/0/show/text", "This slot has the wrong shape.")]

    broken.write_text("scenes: [unclosed", encoding="utf-8")
    refused, _line = _cli(tmp_path / "home", ["expand", "--template", str(broken), *source])
    assert _problems(refused) == [("template", "", "The template is not YAML or JSON.")]

    wrong = 'colors: { bg: "#GGGGGG", fg: "#FFFFFF", accent: "#000000" }\n'
    (folder / "brand.yaml").write_text(wrong, encoding="utf-8")
    refused, _line = _cli(tmp_path / "home", ["expand", "--template", "announcement", *source])
    assert _problems(refused) == [("brand", "/colors/bg", "This text has the wrong shape.")]
    (folder / "brand.yaml").unlink()
    refused, _line = _cli(tmp_path / "home", ["expand", "--template", "announcement", *source])
    assert _problems(refused) == [("template", "/brand/file", "The brand file could not be read.")]


def test_expand_needs_a_template_and_its_flags_belong_to_it_alone(tmp_path: Path) -> None:
    source = ["--in", str(SAMPLES / "example-bookshop" / "inputs.yaml")]
    missing, _line = _cli(tmp_path / "home", ["expand", *source])
    errors = missing["errors"]
    assert isinstance(errors, list)
    assert missing["exit"] == 1 and [error["code"] for error in errors] == ["E_USAGE"]
    assert errors[0]["message"] == "expand needs --template <file or id>."
    unknown, _line = _cli(tmp_path / "home", ["expand", "--template", "birthday", *source])
    errors = unknown["errors"]
    assert isinstance(errors, list) and [error["code"] for error in errors] == ["E_USAGE"]
    assert errors[0]["hint"] == "Use a file, or one of: announcement, short-explainer, welcome."
    unread, _line = _cli(tmp_path / "home", ["expand", "--template", str(tmp_path / "none.yaml"), *source])
    errors = unread["errors"]
    assert isinstance(errors, list) and [error["code"] for error in errors] == ["E_INPUT_SCHEMA"]
    script = ["--in", str(SAMPLES / "example-bookshop" / "script.yaml")]
    for command, flag in (("plan", "--template"), ("validate", "--out"), ("estimate", "--out")):
        refused, _line = _cli(tmp_path / "home", [command, flag, "welcome", *script])
        errors = refused["errors"]
        assert isinstance(errors, list) and [error["code"] for error in errors] == ["E_USAGE"], command
    assert not (tmp_path / "home").exists()


def test_expand_writes_json_when_the_file_name_ends_in_json(tmp_path: Path) -> None:
    source = ["--template", "welcome", "--in", str(SAMPLES / "example-bookshop" / "inputs.yaml")]
    shown, line = _cli(tmp_path / "home", ["expand", *source])
    assert shown["exit"] == 0 and "written" not in _data(shown)
    target = tmp_path / "deep" / "folder" / "script.json"
    made, _line = _cli(tmp_path / "home", ["expand", *source, "--out", str(target)], LATER)
    assert made["exit"] == 0 and _data(made)["written"] == str(target)
    text = target.read_text(encoding="utf-8")
    assert text == canonical(_data(shown)["script"]) + "\n"
    assert _data(made)["script"] == _data(shown)["script"] == _yaml(SAMPLES / "example-bookshop" / "script.yaml")
    assert _data(made)["scriptSha256"] == _data(shown)["scriptSha256"]
    assert _data_bytes(line) == canonical(_data(shown))


def test_guide_and_help_describe_the_starter_templates(tmp_path: Path) -> None:
    guided, _line = _cli(tmp_path / "home", ["guide"])
    templates = _data(guided)["templates"]
    assert isinstance(templates, dict)
    starters = templates["starters"]
    assert [starter["id"] for starter in starters] == STARTERS
    for starter in starters:
        assert starter["text"] == (TEMPLATES / f"{starter['id']}.yaml").read_text(encoding="utf-8")
        assert starter["title"] and starter["description"]
        names = [entry["name"] for entry in starter["inputs"]]
        assert names == list(_starter(starter["id"])["inputs"])
        required = [entry["name"] for entry in starter["inputs"] if entry["required"]]
        assert "business_name" in required and "voice" not in required and "music" not in required
        assert all(entry["type"] and entry["description"] for entry in starter["inputs"])
    steps = " ".join(templates["steps"])
    assert "expand --template" in steps and "estimate" in steps and "budget" in " ".join(templates["notes"])
    helped, _line = _cli(tmp_path / "home", ["help"])
    help_data = _data(helped)
    commands = help_data["commands"]
    flags = help_data["flags"]
    assert isinstance(commands, list) and isinstance(flags, list)
    assert "expand" in [command["name"] for command in commands]
    assert {"--template file", "--out file"} <= {flag["flag"] for flag in flags}
    listed, _line = _cli(tmp_path / "home", ["commands"])
    named = {command["name"]: command for command in _data(listed)["commands"]}
    assert named["expand"] == {"name": "expand", "access": "none", "input": True}


def test_a_script_without_a_template_has_none_in_its_plan() -> None:
    script = _yaml(ROOT / "examples" / "minimal.yaml")
    assert "template" not in engine.plan_data(script, ROOT / "examples")
    script["template"] = {"id": "welcome", "templateVersion": 2}
    loaded, problems = load_script(script)
    assert loaded is None and [(item.at, item.message) for item in problems] == [
        ("/template/templateVersion", "templateVersion must be 1.")
    ]
    assert list(_schema("video-script.v2.json").iter_errors(script)) != []
