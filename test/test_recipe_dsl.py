from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from test_render import ROOT, run, subprocess_verify

from ambitious_video.domain.load import load_script
from ambitious_video.domain.recipe import compile_recipe
from ambitious_video.domain.types import GeneratedScene, LipSync, Speech

RECIPE_PATH = ROOT / "examples" / "promo-video-recipe.json"
RECIPE_SCHEMA_PATH = ROOT / "schemas" / "video-recipe.v1.json"


def _example_recipe() -> dict[str, object]:
    loaded = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _recipe_validator() -> jsonschema.Draft202012Validator:
    schema = json.loads(RECIPE_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _data(payload: dict[str, object]) -> dict[str, object]:
    data = payload["data"]
    assert isinstance(data, dict)
    return data


def test_promo_video_recipe_matches_schema_and_compiles() -> None:
    raw = _example_recipe()
    _recipe_validator().validate(raw)
    compiled, compile_problems = compile_recipe(raw)
    assert compile_problems == () and compiled is not None
    script, load_problems = load_script(raw)
    assert load_problems == () and script is not None
    assert script.budget_usd == 5
    assert script.disclosure == "AI-generated scenes"
    clips = [clip for track in script.tracks if hasattr(track, "clips") for clip in track.clips]
    assert any(isinstance(clip, GeneratedScene) and clip.provider == "xai-video" for clip in clips)
    assert any(isinstance(clip, Speech) and clip.provider == "xai-speech" for clip in clips)


def test_recipe_supports_lip_sync_ai_steps() -> None:
    raw: dict[str, object] = {
        "dslVersion": 1,
        "kind": "recipe",
        "id": "presenter-recipe",
        "version": 1,
        "title": "Presenter recipe",
        "triggers": [{"kind": "manual"}],
        "steps": [
            {
                "id": "voice_line",
                "kind": "ai",
                "executor": "bot",
                "provider": "xai-speech",
                "model": "tts",
                "params": {"at": 0.2, "duration": 2.0, "text": "Hello from a recipe.", "voice": "eve"},
                "fallback": {"type": "captions"},
            },
            {
                "id": "presenter",
                "kind": "ai",
                "executor": "bot",
                "provider": "sync-lipsync",
                "model": "sync-3",
                "disclosure": "AI-generated scenes",
                "params": {"image": "assets/still.png", "voice": "voice", "duration": 3.0},
                "fallback": {"type": "none"},
            },
            {
                "id": "assemble",
                "kind": "tool",
                "needs": ["voice_line", "presenter"],
                "action": "bot.ffmpeg_assemble",
                "input": {
                    "output": {"path": "out/presenter.mp4", "width": 320, "height": 180, "fps": 10},
                    "tracks": [
                        {"id": "main", "clips": [{"refStep": "presenter"}]},
                        {"id": "voice", "clips": [{"refStep": "voice_line"}]},
                    ],
                },
            },
        ],
    }
    _recipe_validator().validate(raw)
    script, problems = load_script(raw)
    assert problems == () and script is not None
    clips = [clip for track in script.tracks if hasattr(track, "clips") for clip in track.clips]
    assert any(isinstance(clip, LipSync) and clip.provider == "sync-lipsync" for clip in clips)


@pytest.mark.parametrize(
    ("mutator", "expected_at"),
    [
        (lambda doc: doc.update({"dslVersion": 2}), "/dslVersion"),
        (lambda doc: doc.update({"kind": "view"}), "/kind"),
        (lambda doc: doc["steps"][0].update({"executor": "server"}), "/steps/0/executor"),
        (lambda doc: doc["steps"][2].update({"needs": ["scene"]}), "/steps/2/input/tracks/1/clips/0/refStep"),
        (
            lambda doc: doc["steps"][2]["input"]["tracks"][1]["clips"][0].update({"refStep": "missing"}),
            "/steps/2/input/tracks/1/clips/0/refStep",
        ),
        (
            lambda doc: doc["steps"][0].update({"needs": ["speech"]})
            or doc["steps"][1].update({"needs": ["scene"]}),
            "/steps",
        ),
        (lambda doc: doc["steps"][1].update({"disclosure": "Other label"}), "/steps"),
        (lambda doc: doc["steps"][2].update({"action": "bot.other_tool"}), "/steps/2/action"),
    ],
)
def test_invalid_recipes_are_refused(mutator: object, expected_at: str) -> None:
    raw = copy.deepcopy(_example_recipe())
    assert callable(mutator)
    mutator(raw)
    script, problems = load_script(raw)
    assert script is None and problems
    assert any(problem.at == expected_at for problem in problems), problems


def test_recipe_end_to_end_validate_plan_estimate_budget_offline_render_verify(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "assets").mkdir(parents=True)
    (work / "assets" / "still.png").write_bytes((ROOT / "examples" / "assets" / "still.png").read_bytes())
    raw = _example_recipe()
    assemble_input = raw["steps"][2]["input"]
    assert isinstance(assemble_input, dict)
    assemble_input["output"] = {"path": "out/promo.mp4", "width": 320, "height": 180, "fps": 10}
    recipe_file = work / "recipe.json"
    recipe_file.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    home = tmp_path / "home"
    checked = run(home, work, ["validate", "--in", str(recipe_file)])
    assert checked["exit"] == 0 and _data(checked) == {"ok": True, "durationSec": 8, "problems": []}

    plan_a = run(home, work, ["plan", "--in", str(recipe_file)])
    plan_b = run(home, work, ["plan", "--in", str(recipe_file)])
    assert plan_a["exit"] == 0 and plan_a["data"] == plan_b["data"]
    assert len(str(_data(plan_a)["planSha256"])) == 64

    estimated = run(home, work, ["estimate", "--in", str(recipe_file)])
    assert estimated["exit"] == 0, estimated
    est_data = _data(estimated)
    assert est_data["budget"] == {"cap": "5.000000", "source": "budget.maxUsd"}
    assert [entry["env"] for entry in est_data["secretsNeeded"]] == ["XAI_API_KEY"]

    over = run(home, work, ["estimate", "--in", str(recipe_file), "--max-usd", "0.01"])
    assert over["exit"] == 1 and over["errors"][0]["code"] == "E_BUDGET"

    refused = run(home, work, ["render", "--in", str(recipe_file)])
    assert refused["exit"] == 1 and refused["errors"][0]["code"] == "E_NO_KEY"

    rendered_a = run(home, work, ["render", "--offline", "--in", str(recipe_file)])
    rendered_b = run(home, work, ["render", "--offline", "--in", str(recipe_file)])
    assert rendered_a["exit"] == 0 and rendered_b["exit"] == 0
    data_a, data_b = _data(rendered_a), _data(rendered_b)
    assert data_a["costUsd"] == "0.000000" and data_a["sha256"] == data_b["sha256"]
    assert [step["fallback"] for step in data_a["steps"]] == ["image", "captions"]

    verified = subprocess_verify(home, work, str(data_a["jobId"]))
    assert verified["exit"] == 0 and _data(verified)["passed"] is True
