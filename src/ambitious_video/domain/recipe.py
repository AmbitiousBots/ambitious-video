from __future__ import annotations

import re
from collections.abc import Mapping

from ambitious_video.domain.types import (
    MAX_SEED,
    MODEL_ID_PATTERN,
    PROVIDER_ID_PATTERN,
    SCHEMA_VERSION,
    SchemaProblem,
    ScriptProblem,
    UnknownStep,
)

DSL_VERSION = 1
RECIPE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
STEP_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PROVIDER_RE = re.compile(PROVIDER_ID_PATTERN)
MODEL_RE = re.compile(MODEL_ID_PATTERN)
ASSEMBLE_ACTION = "bot.ffmpeg_assemble"
TOP_KEYS = {
    "dslVersion",
    "kind",
    "id",
    "version",
    "title",
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
    "executor",
    "provider",
    "model",
    "cache",
    "disclosure",
    "seed",
    "params",
    "fallback",
}
TOOL_KEYS = {"id", "kind", "title", "needs", "next", "action", "input"}
RETURN_KEYS = {"id", "kind", "title", "needs", "status", "output"}
ASSEMBLE_INPUT_KEYS = {"output", "brand", "cache", "disclosure", "template", "tracks", "cuts"}
TRIGGER_KINDS = frozenset({"manual", "event", "schedule", "signal"})
ACTIVATION_KINDS = frozenset({"auto", "person"})
CACHE_KINDS = frozenset({"step_key", "none"})
RETURN_STATUSES = frozenset({"completed", "blocked"})
SCENE_PARAM_KEYS = ("prompt", "duration", "resolution", "aspect_ratio", "image", "kenburns")
SPEECH_PARAM_KEYS = ("at", "text", "duration", "voice")
LIP_PARAM_KEYS = ("duration", "video", "image", "voice")


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


def _check_keys(bag: _Problems, obj: Mapping[str, object], allowed: set[str], at: str) -> None:
    for key in obj:
        if key not in allowed:
            bag.schema(_join(at, "/", str(key)), "This field is not allowed.")


def _check_header(bag: _Problems, raw: Mapping[str, object]) -> None:
    _check_keys(bag, raw, TOP_KEYS, "")
    if raw.get("dslVersion") != DSL_VERSION or isinstance(raw.get("dslVersion"), bool):
        bag.schema("/dslVersion", "dslVersion must be 1.")
    if raw.get("kind") != "recipe":
        bag.schema("/kind", "kind must be recipe.")
    rid = raw.get("id")
    if not isinstance(rid, str) or RECIPE_ID.fullmatch(rid) is None:
        bag.schema("/id", "id must be a kebab-case slug.")
    ver = raw.get("version")
    if not _is_int(ver) or not (1 <= int(str(ver)) <= 1000000):
        bag.schema("/version", "version must be a positive integer.")
    title = raw.get("title")
    if not isinstance(title, str) or not (1 <= len(title) <= 160):
        bag.schema("/title", "title must be non-empty text.")
    if "activation" in raw and raw["activation"] not in ACTIVATION_KINDS:
        bag.schema("/activation", "activation must be auto or person.")
    triggers = raw.get("triggers")
    if not isinstance(triggers, list) or not (1 <= len(triggers) <= 16):
        bag.schema("/triggers", "triggers must be a non-empty list.")
        return
    for index, trigger in enumerate(triggers):
        at = _join("/triggers/", str(index))
        if not isinstance(trigger, dict) or trigger.get("kind") not in TRIGGER_KINDS:
            bag.schema(at, "Trigger kind is invalid.")


def _parse_needs(bag: _Problems, step: Mapping[str, object], at: str) -> tuple[str, ...]:
    if "needs" not in step:
        return ()
    raw_needs = step["needs"]
    if not isinstance(raw_needs, list) or len(raw_needs) > 32:
        bag.schema(_join(at, "/needs"), "needs must be a list of step ids.")
        return ()
    out: list[str] = []
    for idx, item in enumerate(raw_needs):
        if not isinstance(item, str) or STEP_ID.fullmatch(item) is None:
            bag.schema(_join(at, "/needs/", str(idx)), "Invalid step id in needs.")
        else:
            out.append(item)
    return tuple(out)


def _check_dag(bag: _Problems, deps: Mapping[str, tuple[str, ...]]) -> None:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        mark = state.get(node, 0)
        if mark == 1:
            return True
        if mark == 2:
            return False
        state[node] = 1
        for dep in deps.get(node, ()):
            if dep in deps and visit(dep):
                return True
        state[node] = 2
        return False

    for step_id, needs in deps.items():
        for dep in needs:
            if dep not in deps:
                bag.schema("/steps", _join("Unknown dependency ", dep, "."))
        if visit(step_id):
            bag.schema("/steps", "Step dependencies contain a cycle.")
            return


def _ai_step_to_clip(
    bag: _Problems,
    step: Mapping[str, object],
    at: str,
) -> tuple[dict[str, object] | None, str | None]:
    _check_keys(bag, step, AI_KEYS, at)
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
        bag.schema(_join(at, "/cache"), "cache must be step_key or none.")
        return None, None
    disclosure: str | None = None
    if "disclosure" in step:
        raw_disc = step["disclosure"]
        if not isinstance(raw_disc, str) or not (1 <= len(raw_disc) <= 200):
            bag.schema(_join(at, "/disclosure"), "Invalid disclosure text.")
            return None, None
        disclosure = raw_disc
    seed = step.get("seed")
    if "seed" in step and (not _is_int(seed) or not (0 <= int(str(seed)) <= MAX_SEED)):
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
    if "prompt" in params:
        clip: dict[str, object] = {"type": "generated_scene", "provider": provider, "model": model}
        for key in SCENE_PARAM_KEYS:
            if key in params:
                clip[key] = params[key]
        if seed is not None:
            clip["seed"] = seed
        if isinstance(fallback, dict):
            clip["fallback"] = dict(fallback)
        return clip
    if "text" in params and "voice" in params:
        clip = {"type": "speech", "provider": provider, "model": model}
        for key in SPEECH_PARAM_KEYS:
            if key in params:
                clip[key] = params[key]
        if "at" not in clip:
            clip["at"] = 0.0
        if isinstance(fallback, dict):
            fb_type = fallback.get("type")
            if fb_type not in ("captions", "captions_only"):
                bag.schema(_join(at, "/fallback/type"), "Speech fallback must be captions.")
                return None
        return clip
    if "voice" in params and ("video" in params or "image" in params):
        clip = {"type": "lip_sync", "provider": provider, "model": model}
        for key in LIP_PARAM_KEYS:
            if key in params:
                clip[key] = params[key]
        if isinstance(fallback, dict):
            clip["fallback"] = dict(fallback)
        return clip
    bag.schema(_join(at, "/params"), "Unsupported AI step params.")
    return None


def _resolve_tracks(
    bag: _Problems,
    raw_tracks: object,
    ai_clips: Mapping[str, dict[str, object]],
    assemble_needs: frozenset[str],
    at: str,
) -> list[object] | None:
    if not isinstance(raw_tracks, list) or not (1 <= len(raw_tracks) <= 16):
        bag.schema(at, "tracks must be a non-empty list.")
        return None
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
                resolved_clips.append(dict(ai_clips[ref]))
            else:
                resolved_clips.append(raw_clip)
        copied = dict(raw_track)
        copied["clips"] = resolved_clips
        out_tracks.append(copied)
    return out_tracks


def _merge_disclosure(
    bag: _Problems,
    disclosures: list[str],
    tool_disclosure: object,
) -> str | None:
    found = list(disclosures)
    if isinstance(tool_disclosure, str):
        found.append(tool_disclosure)
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
    if not isinstance(raw_steps, list) or not (1 <= len(raw_steps) <= 64):
        bag.schema("/steps", "steps must be a non-empty list.")
        return None, tuple(bag.items)

    deps: dict[str, tuple[str, ...]] = {}
    ai_clips: dict[str, dict[str, object]] = {}
    disclosures: list[str] = []
    assemble_step: tuple[str, Mapping[str, object], tuple[str, ...]] | None = None

    for index, entry in enumerate(raw_steps):
        at = _join("/steps/", str(index))
        if not isinstance(entry, dict):
            bag.schema(at, "Step must be an object.")
            continue
        step_id = entry.get("id")
        if not isinstance(step_id, str) or STEP_ID.fullmatch(step_id) is None:
            bag.schema(_join(at, "/id"), "Invalid step id.")
            continue
        if step_id in deps:
            bag.schema(_join(at, "/id"), "Duplicate step id.")
            continue
        needs = _parse_needs(bag, entry, at)
        deps[step_id] = needs
        kind = entry.get("kind")
        if kind == "ai":
            clip, disc = _ai_step_to_clip(bag, entry, at)
            if clip is not None:
                ai_clips[step_id] = clip
            if disc is not None:
                disclosures.append(disc)
        elif kind == "tool":
            _check_keys(bag, entry, TOOL_KEYS, at)
            if entry.get("action") != ASSEMBLE_ACTION:
                bag.unknown(_join(at, "/action"), str(entry.get("action")))
            elif assemble_step is not None:
                bag.schema(at, "Only one assemble step is allowed.")
            else:
                assemble_step = (at, entry, needs)
        elif kind == "return":
            _check_keys(bag, entry, RETURN_KEYS, at)
            if "status" in entry and entry["status"] not in RETURN_STATUSES:
                bag.schema(_join(at, "/status"), "Invalid return status.")
        else:
            bag.unknown(_join(at, "/kind"), str(kind))

    _check_dag(bag, deps)
    if assemble_step is None:
        bag.schema("/steps", "Missing bot.ffmpeg_assemble step.")
        return None, tuple(bag.items)

    asm_at, asm_entry, asm_needs = assemble_step
    asm_input = asm_entry.get("input")
    if not isinstance(asm_input, dict):
        bag.schema(_join(asm_at, "/input"), "Assemble input must be an object.")
        return None, tuple(bag.items)
    _check_keys(bag, asm_input, ASSEMBLE_INPUT_KEYS, _join(asm_at, "/input"))
    tracks = _resolve_tracks(
        bag,
        asm_input.get("tracks"),
        ai_clips,
        frozenset(asm_needs),
        _join(asm_at, "/input/tracks"),
    )
    disclosure = _merge_disclosure(bag, disclosures, asm_input.get("disclosure"))
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
