from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from test_render import ROOT, run, subprocess_verify

from ambitious_video.domain.load import load_script
from ambitious_video.domain.recipe import compile_recipe
from ambitious_video.domain.recipe_shape import check_recipe_shape
from ambitious_video.domain.types import GeneratedScene, LipSync, Speech

RECIPE_PATH = ROOT / "examples" / "promo-video-recipe.json"
RECIPE_SCHEMA_PATH = ROOT / "schemas" / "video-recipe.v1.json"
SHARED_FIXTURE_PATH = ROOT / "test" / "fixtures" / "promo-render.json"
DOC_ID = "doc:sha256:" + "ab" * 32


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
        "summary": "Combines generated speech with lip sync.",
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
            {
                "id": "done",
                "kind": "return",
                "needs": ["assemble"],
                "status": "completed",
                "output": {"videoPath": "out/presenter.mp4"},
            },
        ],
    }
    _recipe_validator().validate(raw)
    script, problems = load_script(raw)
    assert problems == () and script is not None
    clips = [clip for track in script.tracks if hasattr(track, "clips") for clip in track.clips]
    assert any(isinstance(clip, LipSync) and clip.provider == "sync-lipsync" for clip in clips)


def _dup_ref_step(doc: dict[str, object]) -> None:
    tracks = doc["steps"][2]["input"]["tracks"]
    tracks[0]["clips"].append({"refStep": "scene"})


def _unreferenced_ai_step(doc: dict[str, object]) -> None:
    doc["steps"].insert(
        2,
        {
            "id": "unused_scene",
            "kind": "ai",
            "executor": "bot",
            "provider": "xai-video",
            "model": "grok-imagine-video",
            "params": {"prompt": "Unused shot", "duration": 2.0},
            "next": "done",
        },
    )


def _drop_return(doc: dict[str, object]) -> None:
    doc["steps"] = [step for step in doc["steps"] if step["kind"] != "return"]


def _inline_paid_clip(doc: dict[str, object]) -> None:
    doc["steps"][2]["input"]["tracks"][1]["clips"].append(
        {"type": "speech", "provider": "xai-speech", "model": "tts", "text": "Sneaky", "voice": "eve"}
    )


def _only_trigger(trigger: dict[str, object]) -> object:
    return lambda doc: doc.update({"triggers": [trigger]})


@pytest.mark.parametrize(
    ("mutator", "expected_at"),
    [
        (lambda doc: doc.update({"dslVersion": 2}), "/dslVersion"),
        (lambda doc: doc.update({"dslVersion": 1.0}), "/dslVersion"),
        (lambda doc: doc.update({"kind": "view"}), "/kind"),
        (lambda doc: doc.update({"id": "promo_video"}), "/id"),
        (lambda doc: doc.update({"budget": {"maxUsd": 20000}}), "/budget/maxUsd"),
        (lambda doc: doc.update({"budget": {"maxUsd": 10**400}}), "/budget/maxUsd"),
        (lambda doc: doc.update({"budget": {"maxUsd": 0}}), "/budget/maxUsd"),
        (lambda doc: doc.update({"triggers": [{"kind": "manual", "extra": 1}]}), "/triggers/0/extra"),
        (lambda doc: doc.update({"triggers": [{"kind": "manual", "input": {"foo": 123}}]}), "/triggers/0/input/foo"),
        (lambda doc: doc.update({"triggers": [{"kind": "event"}]}), "/triggers/0/type"),
        (lambda doc: doc.update({"triggers": [{"kind": "event", "type": "video-ready"}]}), "/triggers/0/type"),
        (lambda doc: doc.update({"triggers": [{"kind": "event", "type": "video.ready"}]}), "/triggers/0/type"),
        (_only_trigger({"kind": "schedule"}), "/triggers/0"),
        (_only_trigger({"kind": "schedule", "every": "month", "at": "2026-01-01"}), "/triggers/0"),
        (_only_trigger({"kind": "schedule", "at": "2026-01-01", "start": "2026-01-01"}), "/triggers/0"),
        (_only_trigger({"kind": "schedule", "every": "fortnight"}), "/triggers/0/every"),
        (_only_trigger({"kind": "schedule", "every": 7}), "/triggers/0/every"),
        (_only_trigger({"kind": "schedule", "at": "next week"}), "/triggers/0/at"),
        (_only_trigger({"kind": "schedule", "at": {"$": "facts"}}), "/triggers/0/at"),
        (_only_trigger({"kind": "signal", "name": "a.b", "delayDays": 1}), "/triggers/0/delayDays"),
        (_only_trigger({"kind": "signal", "name": "a.b", "subjectKind": "x"}), "/triggers/0/subjectKind"),
        (lambda doc: doc.update({"title": ""}), "/title"),
        (lambda doc: doc.update({"subject": "Lead"}), "/subject"),
        (lambda doc: doc.update({"source": "doc:sha256:abc"}), "/source"),
        (lambda doc: doc.update({"entity": {"kind": "lead", "trust": "direct", "fields": {}}}), "/entity"),
        (lambda doc: doc["steps"][0].update({"from": ["scene.length"]}), "/subject"),
        (lambda doc: doc["steps"][0].update({"from": []}) or doc.update({"subject": "lead"}), "/steps/0/from"),
        (
            lambda doc: doc["steps"][0].update({"from": ["scene.verified"]}) or doc.update({"subject": "lead"}),
            "/steps/0/from/0",
        ),
        (
            lambda doc: doc["steps"][0].update({"from": ["a.b", "a.b"]}) or doc.update({"subject": "lead"}),
            "/steps/0/from/1",
        ),
        (lambda doc: doc["steps"][0].update({"outputSchema": {"video": "string"}}), "/steps/0/outputSchema"),
        (lambda doc: doc["steps"][0].update({"output": {"video": "movie"}}), "/steps/0/output/video"),
        (lambda doc: doc["steps"][0].update({"output": {"fit": ["high", "high"]}}), "/steps/0/output/fit"),
        (lambda doc: doc["steps"][0].update({"output": {"fit": []}}), "/steps/0/output/fit"),
        (lambda doc: doc["steps"][0].pop("provider"), "/steps/0/provider"),
        (lambda doc: doc["steps"][0].update({"prompt": "A town at sunrise"}), "/steps/0/prompt"),
        (lambda doc: doc["steps"][0].update({"budget": {"maxUsd": 1}}), "/steps/0/budget"),
        (lambda doc: doc["steps"][0].pop("params"), "/steps/0/params"),
        (lambda doc: doc["steps"][2].update({"outputSchema": {"file": "movie"}}), "/steps/2/outputSchema/file"),
        (lambda doc: doc["steps"][0].update({"id": "bad-step-id"}), "/steps/0/id"),
        (lambda doc: doc["steps"][0].update({"id": "constructor"}), "/steps/0/id"),
        (lambda doc: doc["steps"][0].update({"executor": "server"}), "/steps/0/executor"),
        (lambda doc: doc["steps"][0].update({"cache": "none"}), "/steps/0/cache"),
        (lambda doc: doc["steps"][0].update({"title": ""}), "/steps/0/title"),
        (lambda doc: doc["steps"][0]["params"].update({"api_key": "secret"}), "/steps/0/params/api_key"),
        (lambda doc: doc["steps"][0]["params"].update({"text": "both"}), "/steps/0/params"),
        (lambda doc: doc["steps"][1].update({"seed": 7}), "/steps/1/seed"),
        (lambda doc: doc["steps"][1]["fallback"].update({"extra": "no"}), "/steps/1/fallback/extra"),
        (lambda doc: doc["steps"][0].update({"next": "missing"}), "/steps/0/next"),
        (lambda doc: doc["steps"][0].update({"next": "scene"}), "/steps/0/next"),
        (
            lambda doc: doc["steps"][0].update({"next": "assemble"})
            or doc["steps"][2].update({"next": "scene"}),
            "/steps",
        ),
        (lambda doc: doc["steps"][2].update({"timeout": "10000s"}), "/steps/2/timeout"),
        (lambda doc: doc["steps"][2].update({"needs": ["scene"]}), "/steps/2/input/tracks/1/clips/0/refStep"),
        (lambda doc: doc["steps"][2].update({"needs": ["scene", "scene", "speech"]}), "/steps/2/needs/1"),
        (lambda doc: doc["steps"][0].update({"needs": ["done"]}), "/steps"),
        (lambda doc: doc["steps"][0].update({"needs": ["scene"]}), "/steps/0/needs"),
        (lambda doc: doc["steps"][0].update({"needs": ["missing"]}), "/steps/0/needs"),
        (
            lambda doc: doc["steps"][2]["input"]["tracks"][1]["clips"][0].update({"refStep": "missing"}),
            "/steps/2/input/tracks/1/clips/0/refStep",
        ),
        (_dup_ref_step, "/steps/2/input/tracks/0/clips/2/refStep"),
        (_inline_paid_clip, "/steps/2/input/tracks/1/clips/1"),
        (_unreferenced_ai_step, "/steps/2"),
        (_drop_return, "/steps"),
        (
            lambda doc: doc["steps"][0].update({"needs": ["speech"]})
            or doc["steps"][1].update({"needs": ["scene"]}),
            "/steps",
        ),
        (lambda doc: doc["steps"][1].update({"disclosure": "Other label"}), "/steps"),
        (lambda doc: doc["steps"][2]["input"].update({"disclosure": 42}), "/steps/2/input/disclosure"),
        (lambda doc: doc["steps"][2].update({"action": "bot.other_tool"}), "/steps/2/action"),
        (lambda doc: doc["steps"][3].update({"output": "bad"}), "/steps/3/output"),
        (lambda doc: doc["steps"][3].update({"output": {"constructor": 1}}), "/steps/3/output/constructor"),
    ],
)
def test_invalid_recipes_are_refused(mutator: object, expected_at: str) -> None:
    raw = copy.deepcopy(_example_recipe())
    assert callable(mutator)
    mutator(raw)
    script, problems = load_script(raw)
    assert script is None and problems
    assert any(problem.at == expected_at for problem in problems), problems


def _shared_fixture() -> dict[str, object]:
    loaded = json.loads(SHARED_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_shared_promo_render_fixture_has_the_shared_shape_and_names_what_cannot_render_here() -> None:
    raw = _shared_fixture()
    _recipe_validator().validate(raw)
    assert check_recipe_shape(raw) == ()
    script, problems = load_script(raw)
    assert script is None
    assert [problem.at for problem in problems] == [
        "/steps/0/executor",
        "/steps/1/executor",
        "/steps/2/executor",
        "/steps/3/action",
        "/steps",
    ]


def _lead_recipe() -> dict[str, object]:
    return {
        "dslVersion": 1,
        "kind": "recipe",
        "id": "lead-triage",
        "version": 3,
        "subject": "lead",
        "source": DOC_ID,
        "budget": {"maxUsd": 0},
        "triggers": [
            {"kind": "manual", "input": {"budget": {"type": "money", "currency": "USD", "required": True}}},
            {"kind": "event", "type": "fact.verified", "where": {"eq": [{"$": "facts.fit.verified"}, True]}},
            {"kind": "schedule", "every": {"$": "facts.frequency"}, "start": "2026-01-01"},
            {"kind": "schedule", "every": "10d"},
            {"kind": "schedule", "at": "2026-03-01T09:00:00Z"},
            {"kind": "schedule", "at": {"addDays": [{"$": "facts.renewal.date"}, -30]}},
            {"kind": "signal", "name": "lead.replied"},
        ],
        "steps": [
            {
                "id": "classify",
                "kind": "ai",
                "from": ["company.size", "verified.source"],
                "model": "provider/some-model",
                "prompt": "Rate {{facts.company.size}}",
                "output": {"fit": ["high", "medium", "low"], "reason": "text", "deck": "file"},
                "cache": "none",
                "budget": {"maxUsd": 0.5},
            },
            {
                "id": "apply",
                "kind": "tool",
                "action": "facts.upsert",
                "input": {"facts": [{"key": "fit", "value": {"$": "steps.classify.fit"}}]},
                "outputSchema": {"written": "int"},
                "timeout": "30s",
            },
            {"id": "done", "kind": "return", "output": {"fit": {"$": "steps.classify.fit"}}},
        ],
    }


def test_a_recipe_that_only_another_tool_can_run_still_has_the_shared_shape() -> None:
    raw = _lead_recipe()
    _recipe_validator().validate(raw)
    assert check_recipe_shape(raw) == ()
    script, problems = load_script(raw)
    assert script is None and problems[0].at == "/steps/0/executor"


def _set(path: list[object], value: object) -> object:
    def apply(doc: dict[str, object]) -> None:
        node: object = doc
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value

    return apply


def _drop(path: list[object]) -> object:
    def apply(doc: dict[str, object]) -> None:
        node: object = doc
        for part in path[:-1]:
            node = node[part]
        del node[path[-1]]

    return apply


@pytest.mark.parametrize(
    "mutator",
    [
        _set(["title"], ""),
        _set(["subject"], "lead-kind"),
        _drop(["subject"]),
        _set(["source"], "contract.pdf"),
        _set(["source"], DOC_ID.upper()),
        _set(["entity"], {"kind": "lead", "trust": "direct", "fields": {}}),
        _set(["budget"], {"maxUsd": -1}),
        _set(["triggers", 0, "input", "budget"], {"type": "money"}),
        _set(["triggers", 0, "input", "budget"], {"type": "string", "currency": "USD"}),
        _set(["triggers", 0, "input", "budget"], {"type": "text", "unique": True}),
        _set(["triggers", 0, "input", "budget"], {"type": "enum", "values": ["a", "a"]}),
        _set(["triggers", 0, "input", "budget"], {"type": "ref"}),
        _set(["triggers", 0, "input", "budget"], {"type": "string", "label": "Budget"}),
        _set(["triggers", 1, "type"], "fact.confirmed"),
        _set(["triggers", 1, "type"], "lead.created"),
        _set(["triggers", 1, "where"], {"eq": [1]}),
        _set(["triggers", 1, "where"], {"eq": [1, 2], "ne": [1, 2]}),
        _set(["triggers", 1, "where"], {"matches": [1, 2]}),
        _set(["triggers", 1, "where"], {"$": "facts"}),
        _set(["triggers", 1, "where"], {"$": "facts.verified"}),
        _set(["triggers", 1, "where"], {"$": "facts.fit.verified.verified_by"}),
        _set(["triggers", 1, "where"], {"$": "a.b.c.d.e.f.g"}),
        _set(["triggers", 1, "where"], {"$": "input.constructor"}),
        _set(["triggers", 1, "where"], {"in": [1, []]}),
        _set(["triggers", 1, "where"], {"and": []}),
        _set(["triggers", 1, "where"], [1]),
        _set(["triggers", 2], {"kind": "schedule", "rule": "recurring", "subjectKind": "lead", "every": "month"}),
        _set(["triggers", 2], {"kind": "schedule", "rule": "on_date", "subjectKind": "lead", "date": "2026-01-01"}),
        _set(["triggers", 2], {"kind": "schedule", "every": "month", "days": 3}),
        _set(["triggers", 2], {"kind": "schedule", "every": "month", "businessDays": True}),
        _set(["triggers", 2], {"kind": "schedule", "every": "month", "end": "2027-01-01"}),
        _set(["triggers", 2], {"kind": "schedule", "every": "month", "dueDays": 3}),
        _set(["triggers", 2], {"kind": "schedule", "every": "month", "leadDays": 3}),
        _set(["triggers", 2], {"kind": "schedule"}),
        _set(["triggers", 2], {"kind": "schedule", "every": "month", "at": "2026-01-01"}),
        _set(["triggers", 2], {"kind": "schedule", "at": "2026-01-01", "start": "2026-01-01"}),
        _set(["triggers", 2], {"kind": "schedule", "every": "0d"}),
        _set(["triggers", 2], {"kind": "schedule", "every": 30}),
        _set(["triggers", 2], {"kind": "schedule", "at": "2026-03-01 09:00"}),
        _set(["triggers", 2], {"kind": "schedule", "at": None}),
        _set(["triggers", 6, "delayDays"], 2),
        _set(["triggers", 6, "subjectKind"], "lead"),
        _set(["steps", 0, "from"], []),
        _set(["steps", 0, "from"], ["Company.Size"]),
        _set(["steps", 0, "from"], ["fit.verified_at"]),
        _set(["steps", 0, "from"], ["a.b", "a.b"]),
        _set(["steps", 0, "outputSchema"], {"fit": "string"}),
        _set(["steps", 0, "output"], {"fit": "movie"}),
        _set(["steps", 0, "output"], {"fit": []}),
        _set(["steps", 0, "output"], {"Fit": "text"}),
        _drop(["steps", 0, "model"]),
        _set(["steps", 0, "provider"], ""),
        _set(["steps", 0, "executor"], "person"),
        _set(["steps", 0, "cache"], "always"),
        _set(["steps", 0, "kind"], "eval"),
        _set(["steps", 0, "kind"], "human"),
        _set(["steps", 0, "kind"], "wait"),
        _set(["steps", 0, "kind"], "recipe"),
        _set(["steps", 1, "action"], "facts.write"),
        _set(["steps", 1, "action"], "facts.verify"),
        _set(["steps", 1, "action"], "shell.run"),
        _set(["steps", 1, "outputSchema"], {"written": "file"}),
        _set(["steps", 1, "output"], {"written": "int"}),
        _set(["steps", 1, "timeout"], "2w"),
        _set(["steps", 2, "status"], "failed"),
    ],
)
def test_the_schema_file_and_the_program_refuse_the_same_shapes(mutator: object) -> None:
    raw = _lead_recipe()
    assert callable(mutator)
    mutator(raw)
    assert list(_recipe_validator().iter_errors(raw)) != []
    assert check_recipe_shape(raw) != ()


@pytest.mark.parametrize("kind", ["eval", "human", "wait", "recipe"])
def test_a_step_kind_this_tool_has_no_use_for_is_refused_by_name(kind: str) -> None:
    raw = _example_recipe()
    raw["steps"].insert(0, {"id": "first", "kind": kind})
    script, problems = load_script(raw)
    assert script is None
    assert [(problem.at, problem.message) for problem in problems] == [
        ("/steps/0/kind", f"This tool cannot run {kind} steps.")
    ]


@pytest.mark.parametrize(
    ("mutator", "expected_at"),
    [
        (_set(["steps", 0, "params", "prompt"], "Use sk_live_" + "a1" * 8), "/steps/0/params/prompt"),
        (_set(["steps", 3, "output", "note"], "ghp\u200b_" + "b2" * 8), "/steps/3/output/note"),
        (_set(["steps", 3, "output", "note"], "bad\x00text"), "/steps/3/output/note"),
        (_set(["steps", 3, "output", "note"], "\ud800"), "/steps/3/output/note"),
        (_set(["steps", 3, "output", "note"], {"a": {"prototype": 1}}), "/steps/3/output/note/a/prototype"),
        (_set(["steps", 3, "output", "note"], {"$": "facts.brand.name"}), "/subject"),
        (_set(["steps", 0, "params", "prompt"], "Show {{ facts.brand.name }}"), "/subject"),
        (_set(["triggers"], [{"kind": "schedule", "every": {"$": "facts.frequency"}}]), "/subject"),
    ],
)
def test_rules_a_schema_file_cannot_say_are_checked_by_the_program(mutator: object, expected_at: str) -> None:
    raw = _example_recipe()
    assert callable(mutator)
    mutator(raw)
    _recipe_validator().validate(raw)
    assert [problem.at for problem in check_recipe_shape(raw)] == [expected_at]


def test_a_step_falls_through_to_the_one_after_it_unless_another_step_needs_it() -> None:
    raw = _example_recipe()
    del raw["steps"][3]["needs"]
    assert check_recipe_shape(raw) == ()
    raw["steps"].append({"id": "after", "kind": "tool", "action": "bot.notify", "needs": ["done"]})
    assert [problem.at for problem in check_recipe_shape(raw)] == ["/steps/4"]


def _with_records(raw: dict[str, object]) -> dict[str, object]:
    marked = copy.deepcopy(raw)
    marked["subject"] = "campaign"
    marked["source"] = DOC_ID
    del marked["title"]
    for step in marked["steps"]:
        step["from"] = ["launch.date", "brand.name"]
    return marked


def test_subject_source_and_from_do_not_change_what_is_rendered_or_any_step_key(tmp_path: Path) -> None:
    plain = _example_recipe()
    marked = _with_records(plain)
    _recipe_validator().validate(marked)
    assert compile_recipe(marked) == compile_recipe(plain)

    work = tmp_path / "work"
    (work / "assets").mkdir(parents=True)
    (work / "assets" / "still.png").write_bytes((ROOT / "examples" / "assets" / "still.png").read_bytes())
    (work / "plain.json").write_text(json.dumps(plain), encoding="utf-8")
    (work / "marked.json").write_text(json.dumps(marked), encoding="utf-8")
    home = tmp_path / "home"
    plan_plain = run(home, work, ["plan", "--in", str(work / "plain.json")])
    plan_marked = run(home, work, ["plan", "--in", str(work / "marked.json")])
    assert plan_plain["exit"] == 0 and plan_marked["exit"] == 0
    assert _data(plan_marked)["steps"] == _data(plan_plain)["steps"]
    assert _data(plan_marked)["planSha256"] == _data(plan_plain)["planSha256"]


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
