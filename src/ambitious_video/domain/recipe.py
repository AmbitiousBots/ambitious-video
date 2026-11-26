from __future__ import annotations

import math
import re
from collections.abc import Mapping

from ambitious_video.domain.types import (
    MODEL_ID_PATTERN,
    PROVIDER_ID_PATTERN,
    SCHEMA_VERSION,
    SchemaProblem,
    ScriptProblem,
    UnknownStep,
)

DSL_VERSION = 1
MAX_RECIPE_STEPS = 32
MAX_RECIPE_TRIGGERS = 8
MAX_RECIPE_NEEDS = 16
MAX_RECIPE_DISCLOSURE = 160
MAX_RECIPE_SEED = 2_147_483_647
MAX_RECIPE_BUDGET_USD = 10_000
RECIPE_ID_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
STEP_ID_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
EVENT_NAME_PATTERN = r"^[a-z][a-z0-9._-]{0,95}$"
DURATION_TEXT_PATTERN = r"^[1-9][0-9]{0,4}[smhd]$"
RECIPE_ID = re.compile(RECIPE_ID_PATTERN)
STEP_ID = re.compile(STEP_ID_PATTERN)
EVENT_RE = re.compile(EVENT_NAME_PATTERN)
DURATION_RE = re.compile(DURATION_TEXT_PATTERN)
PROVIDER_RE = re.compile(PROVIDER_ID_PATTERN)
MODEL_RE = re.compile(MODEL_ID_PATTERN)
RESERVED_STEP_IDS = frozenset({"__proto__", "constructor", "prototype"})
ASSEMBLE_ACTION = "bot.ffmpeg_assemble"
TOP_KEYS = {
    "dslVersion",
    "kind",
    "id",
    "version",
    "title",
    "summary",
    "activation",
    "budget",
    "triggers",
    "steps",
}
AI_KEYS = {
    "id",
    "kind",
    "title",
    "needs",
    "next",
    "adapterVersion",
    "executor",
    "provider",
    "model",
    "cache",
    "disclosure",
    "seed",
    "params",
    "fallback",
}
TOOL_KEYS = {"id", "kind", "title", "needs", "next", "timeout", "action", "input"}
RETURN_KEYS = {"id", "kind", "title", "needs", "status", "output"}
ASSEMBLE_INPUT_KEYS = {"output", "brand", "cache", "disclosure", "template", "tracks", "cuts"}
TRIGGER_KINDS = ("manual", "event", "schedule", "signal")
MANUAL_TRIGGER_KEYS = {"kind", "input"}
EVENT_TRIGGER_KEYS = {"kind", "type", "where"}
SCHEDULE_TRIGGER_KEYS = {
    "kind",
    "rule",
    "subjectKind",
    "businessDays",
    "every",
    "start",
    "end",
    "date",
    "days",
    "dueDays",
    "leadDays",
}
SIGNAL_TRIGGER_KEYS = {"kind", "name", "subjectKind", "delayDays"}
SCHEDULE_RULES = frozenset({"recurring", "before_date", "on_date"})
SCHEDULE_EVERY = frozenset({"month", "quarter", "year"})
ACTIVATION_KINDS = ("auto", "person")
CACHE_KINDS = ("step_key",)
RETURN_STATUSES = ("completed", "blocked")
SCENE_PARAM_KEYS = frozenset({"prompt", "duration", "resolution", "aspect_ratio", "image", "kenburns"})
SPEECH_PARAM_KEYS = frozenset({"at", "text", "duration", "voice"})
LIP_PARAM_KEYS = frozenset({"duration", "video", "image", "voice"})


def _join(*parts: str) -> str:
    return "".join(parts)


class _Problems:
    def __init__(self) -> None:
        self.items: list[ScriptProblem] = []

    def schema(self, at: str, message: str) -> None:
        self.items.append(SchemaProblem(at, message))

    def unknown(self, at: str, step: str) -> None:
        self.items.append(UnknownStep(at, step))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _valid_step_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 64
        and STEP_ID.fullmatch(value) is not None
        and value not in RESERVED_STEP_IDS
    )


def _valid_kebab_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 64 and RECIPE_ID.fullmatch(value) is not None


def _check_keys(bag: _Problems, obj: Mapping[str, object], allowed: set[str], at: str) -> None:
    for key in obj:
        if key not in allowed:
            bag.schema(_join(at, "/", str(key)), "This field is not allowed.")


def _check_title(bag: _Problems, obj: Mapping[str, object], at: str, *, required: bool = False) -> None:
    if "title" not in obj:
        if required:
            bag.schema(_join(at, "/title"), "title must be non-empty text.")
        return
    title = obj["title"]
    if not isinstance(title, str) or not (1 <= len(title) <= 160):
        bag.schema(_join(at, "/title"), "title must be non-empty text.")


def _check_budget(bag: _Problems, raw: Mapping[str, object]) -> None:
    if "budget" not in raw:
        return
    budget = raw["budget"]
    if not isinstance(budget, dict):
        bag.schema("/budget", "budget must be an object.")
        return
    _check_keys(bag, budget, {"maxUsd"}, "/budget")
    cap = budget.get("maxUsd")
    if not _is_number(cap) or not (0 < float(str(cap)) <= MAX_RECIPE_BUDGET_USD):
        bag.schema("/budget/maxUsd", "Invalid budget maxUsd.")


def _check_trigger(bag: _Problems, trigger: object, at: str) -> None:
    if not isinstance(trigger, dict):
        bag.schema(at, "Trigger must be an object.")
        return
    kind = trigger.get("kind")
    if kind == "manual":
        _check_keys(bag, trigger, MANUAL_TRIGGER_KEYS, at)
        if "input" in trigger and not isinstance(trigger["input"], dict):
            bag.schema(_join(at, "/input"), "Manual input must be an object.")
    elif kind == "event":
        _check_keys(bag, trigger, EVENT_TRIGGER_KEYS, at)
        ev = trigger.get("type")
        if not isinstance(ev, str) or EVENT_RE.fullmatch(ev) is None:
            bag.schema(_join(at, "/type"), "Invalid event type.")
    elif kind == "schedule":
        _check_keys(bag, trigger, SCHEDULE_TRIGGER_KEYS, at)
        if trigger.get("rule") not in SCHEDULE_RULES:
            bag.schema(_join(at, "/rule"), "Invalid schedule rule.")
        if not _valid_kebab_id(trigger.get("subjectKind")):
            bag.schema(_join(at, "/subjectKind"), "Invalid subjectKind.")
        if "businessDays" in trigger and not isinstance(trigger["businessDays"], bool):
            bag.schema(_join(at, "/businessDays"), "businessDays must be boolean.")
        if "every" in trigger and trigger["every"] not in SCHEDULE_EVERY:
            bag.schema(_join(at, "/every"), "Invalid schedule every.")
    elif kind == "signal":
        _check_keys(bag, trigger, SIGNAL_TRIGGER_KEYS, at)
        name = trigger.get("name")
        if not isinstance(name, str) or EVENT_RE.fullmatch(name) is None:
            bag.schema(_join(at, "/name"), "Invalid signal name.")
        if "subjectKind" in trigger and not _valid_kebab_id(trigger["subjectKind"]):
            bag.schema(_join(at, "/subjectKind"), "Invalid subjectKind.")
    else:
        bag.schema(at, "Trigger kind is invalid.")


def _check_header(bag: _Problems, raw: Mapping[str, object]) -> None:
    _check_keys(bag, raw, TOP_KEYS, "")
    version_tag = raw.get("dslVersion")
    if not _is_int(version_tag) or version_tag != DSL_VERSION:
        bag.schema("/dslVersion", "dslVersion must be 1.")
    if raw.get("kind") != "recipe":
        bag.schema("/kind", "kind must be recipe.")
    if not _valid_kebab_id(raw.get("id")):
        bag.schema("/id", "id must be a kebab-case slug.")
    ver = raw.get("version")
    if not _is_int(ver) or not (1 <= int(str(ver)) <= 1000000):
        bag.schema("/version", "version must be a positive integer.")
    _check_title(bag, raw, "", required=True)
    if "summary" in raw:
        summary = raw["summary"]
        if not isinstance(summary, str) or not (1 <= len(summary) <= 400):
            bag.schema("/summary", "summary must be non-empty text.")
    if "activation" in raw and raw["activation"] not in ACTIVATION_KINDS:
        bag.schema("/activation", "activation must be auto or person.")
    _check_budget(bag, raw)
    triggers = raw.get("triggers")
    if not isinstance(triggers, list) or not (1 <= len(triggers) <= MAX_RECIPE_TRIGGERS):
        bag.schema("/triggers", "triggers must be a non-empty list.")
        return
    for index, trigger in enumerate(triggers):
        _check_trigger(bag, trigger, _join("/triggers/", str(index)))


def _parse_needs(bag: _Problems, step: Mapping[str, object], at: str) -> tuple[str, ...]:
    if "needs" not in step:
        return ()
    raw_needs = step["needs"]
    if not isinstance(raw_needs, list) or len(raw_needs) > MAX_RECIPE_NEEDS:
        bag.schema(_join(at, "/needs"), "needs must be a list of step ids.")
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_needs):
        if not _valid_step_id(item):
            bag.schema(_join(at, "/needs/", str(idx)), "Invalid step id in needs.")
        else:
            name = str(item)
            if name in seen:
                bag.schema(_join(at, "/needs/", str(idx)), "Duplicate step id in needs.")
            else:
                seen.add(name)
                out.append(name)
    return tuple(out)


def _parse_next(bag: _Problems, step: Mapping[str, object], at: str) -> str | None:
    if "next" not in step:
        return None
    target = step["next"]
    if not _valid_step_id(target):
        bag.schema(_join(at, "/next"), "Invalid step id in next.")
        return None
    return str(target)


def _check_graph(
    bag: _Problems,
    kinds: Mapping[str, str],
    step_at: Mapping[str, str],
    deps: Mapping[str, tuple[str, ...]],
    nexts: Mapping[str, str | None],
) -> None:
    edges: dict[str, list[str]] = {sid: [] for sid in kinds}
    for sid, needs in deps.items():
        for dep in needs:
            if dep not in kinds:
                bag.schema(_join(step_at[sid], "/needs"), _join("Unknown dependency ", dep, "."))
            elif kinds[dep] == "return":
                bag.schema(_join(step_at[sid], "/needs"), "Cannot depend on a return step.")
            else:
                edges[dep].append(sid)
    for sid, target in nexts.items():
        if target is None:
            continue
        if target not in kinds:
            bag.schema(_join(step_at[sid], "/next"), _join("Unknown next step ", target, "."))
        elif target == sid:
            bag.schema(_join(step_at[sid], "/next"), "Step cannot point to itself.")
        else:
            edges[sid].append(target)

    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        mark = state.get(node, 0)
        if mark == 1:
            return True
        if mark == 2:
            return False
        state[node] = 1
        for nxt in edges.get(node, ()):
            if visit(nxt):
                return True
        state[node] = 2
        return False

    for sid in kinds:
        if visit(sid):
            bag.schema("/steps", "Step dependencies contain a cycle.")
            return

    reverse: dict[str, list[str]] = {sid: [] for sid in kinds}
    for src, targets in edges.items():
        for dst in targets:
            reverse[dst].append(src)
    stack = [sid for sid, kind in kinds.items() if kind == "return"]
    reachable = set(stack)
    while stack:
        cur = stack.pop()
        for prev in reverse.get(cur, ()):
            if prev not in reachable:
                reachable.add(prev)
                stack.append(prev)
    for sid, kind in kinds.items():
        if kind != "return" and sid not in reachable:
            bag.schema(step_at[sid], "Step cannot reach a return step.")


def _ai_step_to_clip(
    bag: _Problems,
    step: Mapping[str, object],
    at: str,
) -> tuple[dict[str, object] | None, str | None]:
    _check_keys(bag, step, AI_KEYS, at)
    _check_title(bag, step, at)
    if "adapterVersion" in step:
        av = step["adapterVersion"]
        if not _is_int(av) or not (1 <= int(str(av)) <= 1000):
            bag.schema(_join(at, "/adapterVersion"), "Invalid adapterVersion.")
    if step.get("executor") != "bot":
        bag.schema(_join(at, "/executor"), "Video AI steps require executor bot.")
        return None, None
    provider = step.get("provider")
    model = step.get("model")
    if not isinstance(provider, str) or PROVIDER_RE.fullmatch(provider) is None:
        bag.schema(_join(at, "/provider"), "Invalid provider id.")
        return None, None
    if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
        bag.schema(_join(at, "/model"), "Invalid model id.")
        return None, None
    if "cache" in step and step["cache"] not in CACHE_KINDS:
        bag.schema(_join(at, "/cache"), "Video AI steps require step_key cache.")
        return None, None
    disclosure: str | None = None
    if "disclosure" in step:
        raw_disc = step["disclosure"]
        if not isinstance(raw_disc, str) or not (1 <= len(raw_disc) <= MAX_RECIPE_DISCLOSURE):
            bag.schema(_join(at, "/disclosure"), "Invalid disclosure text.")
            return None, None
        disclosure = raw_disc
    seed = step.get("seed")
    if "seed" in step and (not _is_int(seed) or not (0 <= int(str(seed)) <= MAX_RECIPE_SEED)):
        bag.schema(_join(at, "/seed"), "Invalid seed.")
        return None, None
    params = step.get("params")
    if not isinstance(params, dict):
        bag.schema(_join(at, "/params"), "params must be an object.")
        return None, None
    fallback = step.get("fallback")
    if "fallback" in step and not isinstance(fallback, dict):
        bag.schema(_join(at, "/fallback"), "fallback must be an object.")
        return None, None
    clip = _compile_ai_clip(bag, provider, model, params, fallback, seed, at)
    return clip, disclosure


def _compile_ai_clip(
    bag: _Problems,
    provider: str,
    model: str,
    params: Mapping[str, object],
    fallback: object,
    seed: object,
    at: str,
) -> dict[str, object] | None:
    is_scene = "prompt" in params
    is_speech = "text" in params
    is_lip = ("video" in params or "image" in params) and not is_scene
    matches = int(is_scene) + int(is_speech) + int(is_lip)
    if matches != 1:
        bag.schema(_join(at, "/params"), "Unsupported or ambiguous AI params.")
        return None
    if is_scene:
        _check_keys(bag, params, set(SCENE_PARAM_KEYS), _join(at, "/params"))
        clip: dict[str, object] = {"type": "generated_scene", "provider": provider, "model": model}
        for key in SCENE_PARAM_KEYS:
            if key in params:
                clip[key] = params[key]
        if seed is not None:
            clip["seed"] = seed
        if isinstance(fallback, dict):
            clip["fallback"] = dict(fallback)
        return clip
    if seed is not None:
        bag.schema(_join(at, "/seed"), "Only scene steps accept a seed.")
        return None
    if is_speech:
        _check_keys(bag, params, set(SPEECH_PARAM_KEYS), _join(at, "/params"))
        clip = {"type": "speech", "provider": provider, "model": model}
        for key in SPEECH_PARAM_KEYS:
            if key in params:
                clip[key] = params[key]
        if "at" not in clip:
            clip["at"] = 0.0
        if isinstance(fallback, dict):
            _check_keys(bag, fallback, {"type"}, _join(at, "/fallback"))
            fb_type = fallback.get("type")
            if fb_type not in ("captions", "captions_only"):
                bag.schema(_join(at, "/fallback/type"), "Speech fallback must be captions.")
                return None
        return clip
    _check_keys(bag, params, set(LIP_PARAM_KEYS), _join(at, "/params"))
    clip = {"type": "lip_sync", "provider": provider, "model": model}
    for key in LIP_PARAM_KEYS:
        if key in params:
            clip[key] = params[key]
    if isinstance(fallback, dict):
        clip["fallback"] = dict(fallback)
    return clip


def _resolve_tracks(
    bag: _Problems,
    raw_tracks: object,
    ai_clips: Mapping[str, dict[str, object]],
    assemble_needs: frozenset[str],
    at: str,
) -> tuple[list[object] | None, set[str]]:
    referenced: set[str] = set()
    if not isinstance(raw_tracks, list) or not (1 <= len(raw_tracks) <= 16):
        bag.schema(at, "tracks must be a non-empty list.")
        return None, referenced
    out_tracks: list[object] = []
    for t_idx, raw_track in enumerate(raw_tracks):
        t_at = _join(at, "/", str(t_idx))
        if not isinstance(raw_track, dict):
            out_tracks.append(raw_track)
            continue
        if "clips" not in raw_track or not isinstance(raw_track["clips"], list):
            out_tracks.append(dict(raw_track))
            continue
        resolved_clips: list[object] = []
        for c_idx, raw_clip in enumerate(raw_track["clips"]):
            c_at = _join(t_at, "/clips/", str(c_idx))
            if isinstance(raw_clip, dict) and "refStep" in raw_clip:
                if set(raw_clip) != {"refStep"} or not isinstance(raw_clip["refStep"], str):
                    bag.schema(c_at, "refStep must be a step id string.")
                    continue
                ref = raw_clip["refStep"]
                if ref not in ai_clips:
                    bag.schema(_join(c_at, "/refStep"), _join("Unknown AI step ", ref, "."))
                    continue
                if ref not in assemble_needs:
                    bag.schema(_join(c_at, "/refStep"), _join("Add ", ref, " to assemble needs."))
                    continue
                if ref in referenced:
                    bag.schema(_join(c_at, "/refStep"), "AI step is referenced twice.")
                    continue
                referenced.add(ref)
                resolved_clips.append(dict(ai_clips[ref]))
            else:
                resolved_clips.append(raw_clip)
        copied = dict(raw_track)
        copied["clips"] = resolved_clips
        out_tracks.append(copied)
    return out_tracks, referenced


def _merge_disclosure(
    bag: _Problems,
    disclosures: list[str],
    asm_input: Mapping[str, object],
    asm_at: str,
) -> str | None:
    found = list(disclosures)
    if "disclosure" in asm_input:
        raw_disc = asm_input["disclosure"]
        if not isinstance(raw_disc, str) or not (1 <= len(raw_disc) <= MAX_RECIPE_DISCLOSURE):
            bag.schema(_join(asm_at, "/input/disclosure"), "Invalid disclosure text.")
            return None
        found.append(raw_disc)
    unique = set(found)
    if len(unique) > 1:
        bag.schema("/steps", "Conflicting disclosure texts.")
        return None
    return found[0] if found else None


def compile_recipe(
    raw: Mapping[str, object],
) -> tuple[dict[str, object] | None, tuple[ScriptProblem, ...]]:
    bag = _Problems()
    _check_header(bag, raw)
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not (1 <= len(raw_steps) <= MAX_RECIPE_STEPS):
        bag.schema("/steps", "steps must be a non-empty list.")
        return None, tuple(bag.items)

    kinds: dict[str, str] = {}
    step_at: dict[str, str] = {}
    deps: dict[str, tuple[str, ...]] = {}
    nexts: dict[str, str | None] = {}
    ai_clips: dict[str, dict[str, object]] = {}
    disclosures: list[str] = []
    assemble_step: tuple[str, Mapping[str, object], tuple[str, ...]] | None = None
    has_return = False

    for index, entry in enumerate(raw_steps):
        at = _join("/steps/", str(index))
        if not isinstance(entry, dict):
            bag.schema(at, "Step must be an object.")
            continue
        step_id = entry.get("id")
        if not _valid_step_id(step_id):
            bag.schema(_join(at, "/id"), "Invalid step id.")
            continue
        sid = str(step_id)
        if sid in kinds:
            bag.schema(_join(at, "/id"), "Duplicate step id.")
            continue
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in ("ai", "tool", "return"):
            bag.unknown(_join(at, "/kind"), str(kind))
            continue
        kinds[sid] = kind
        step_at[sid] = at
        needs = _parse_needs(bag, entry, at)
        deps[sid] = needs
        if kind == "ai":
            nexts[sid] = _parse_next(bag, entry, at)
            clip, disc = _ai_step_to_clip(bag, entry, at)
            if clip is not None:
                ai_clips[sid] = clip
            if disc is not None:
                disclosures.append(disc)
        elif kind == "tool":
            _check_keys(bag, entry, TOOL_KEYS, at)
            _check_title(bag, entry, at)
            nexts[sid] = _parse_next(bag, entry, at)
            if "timeout" in entry:
                timeout = entry["timeout"]
                if not isinstance(timeout, str) or DURATION_RE.fullmatch(timeout) is None:
                    bag.schema(_join(at, "/timeout"), "Invalid timeout duration.")
            if entry.get("action") != ASSEMBLE_ACTION:
                bag.unknown(_join(at, "/action"), str(entry.get("action")))
            elif assemble_step is not None:
                bag.schema(at, "Only one assemble step is allowed.")
            else:
                assemble_step = (at, entry, needs)
        else:
            has_return = True
            _check_keys(bag, entry, RETURN_KEYS, at)
            _check_title(bag, entry, at)
            nexts[sid] = None
            if "status" in entry and entry["status"] not in RETURN_STATUSES:
                bag.schema(_join(at, "/status"), "Invalid return status.")
            if "output" in entry and not isinstance(entry["output"], dict):
                bag.schema(_join(at, "/output"), "Return output must be an object.")

    _check_graph(bag, kinds, step_at, deps, nexts)
    if not has_return:
        bag.schema("/steps", "Recipe requires a return step.")
    if assemble_step is None:
        bag.schema("/steps", "Missing bot.ffmpeg_assemble step.")
        return None, tuple(bag.items)

    asm_at, asm_entry, asm_needs = assemble_step
    asm_input = asm_entry.get("input")
    if not isinstance(asm_input, dict):
        bag.schema(_join(asm_at, "/input"), "Assemble input must be an object.")
        return None, tuple(bag.items)
    _check_keys(bag, asm_input, ASSEMBLE_INPUT_KEYS, _join(asm_at, "/input"))
    tracks, referenced = _resolve_tracks(
        bag,
        asm_input.get("tracks"),
        ai_clips,
        frozenset(asm_needs),
        _join(asm_at, "/input/tracks"),
    )
    for sid in ai_clips:
        if sid not in referenced:
            bag.schema(step_at[sid], "AI step is not referenced in tracks.")
    disclosure = _merge_disclosure(bag, disclosures, asm_input, asm_at)
    if bag.items or tracks is None:
        return None, tuple(bag.items)

    script: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "output": asm_input.get("output"),
        "tracks": tracks,
    }
    if "budget" in raw:
        script["budget"] = raw["budget"]
    if disclosure is not None:
        script["disclosure"] = disclosure
    for optional_key in ("brand", "cache", "cuts", "template"):
        if optional_key in asm_input:
            script[optional_key] = asm_input[optional_key]
    return script, ()
