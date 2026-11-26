from __future__ import annotations

import re
from collections.abc import Mapping

from ambitious_video.domain.recipe_shape import (
    Problems,
    check_keys,
    check_recipe_shape,
    parse_needs,
)
from ambitious_video.domain.types import (
    MODEL_ID_PATTERN,
    PROVIDER_ID_PATTERN,
    SCHEMA_VERSION,
    ScriptProblem,
)

MAX_RECIPE_DISCLOSURE = 160
PROVIDER_RE = re.compile(PROVIDER_ID_PATTERN)
MODEL_RE = re.compile(MODEL_ID_PATTERN)
PAID_CLIP_TYPES = frozenset({"speech", "generated_scene", "lip_sync"})
ASSEMBLE_ACTION = "bot.ffmpeg_assemble"
ASSEMBLE_INPUT_KEYS = {"output", "brand", "cache", "disclosure", "template", "tracks", "cuts"}
CACHE_KINDS = ("step_key",)
SCENE_PARAM_KEYS = frozenset({"prompt", "duration", "resolution", "aspect_ratio", "image", "kenburns"})
SPEECH_PARAM_KEYS = frozenset({"at", "text", "duration", "voice"})
LIP_PARAM_KEYS = frozenset({"duration", "video", "image", "voice"})


def _join(*parts: str) -> str:
    return "".join(parts)


def _ai_step_to_clip(
    bag: Problems,
    step: Mapping[str, object],
    at: str,
) -> tuple[dict[str, object] | None, str | None]:
    if step.get("executor") != "bot":
        bag.schema(_join(at, "/executor"), "Video AI steps require executor bot.")
        return None, None
    if "prompt" in step:
        bag.schema(_join(at, "/prompt"), "Video AI steps read params, not prompt.")
        return None, None
    if "budget" in step:
        bag.schema(_join(at, "/budget"), "Set the budget on the recipe instead.")
        return None, None
    provider = step.get("provider")
    model = step.get("model")
    if "provider" not in step:
        bag.schema(_join(at, "/provider"), "Video AI steps need a provider.")
        return None, None
    if not isinstance(provider, str) or PROVIDER_RE.fullmatch(provider) is None:
        bag.schema(_join(at, "/provider"), "Invalid provider id.")
        return None, None
    if not isinstance(model, str) or MODEL_RE.fullmatch(model) is None:
        bag.schema(_join(at, "/model"), "Invalid model id.")
        return None, None
    if "cache" in step and step["cache"] not in CACHE_KINDS:
        bag.schema(_join(at, "/cache"), "Video AI steps require step_key cache.")
        return None, None
    raw_disc = step.get("disclosure")
    disclosure = raw_disc if isinstance(raw_disc, str) else None
    seed = step.get("seed")
    params = step.get("params")
    if not isinstance(params, dict):
        bag.schema(_join(at, "/params"), "Video AI steps need params.")
        return None, None
    fallback = step.get("fallback")
    if "fallback" in step and not isinstance(fallback, dict):
        bag.schema(_join(at, "/fallback"), "fallback must be an object.")
        return None, None
    clip = _compile_ai_clip(bag, provider, model, params, fallback, seed, at)
    return clip, disclosure


def _compile_ai_clip(
    bag: Problems,
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
        check_keys(bag, params, set(SCENE_PARAM_KEYS), _join(at, "/params"))
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
        check_keys(bag, params, set(SPEECH_PARAM_KEYS), _join(at, "/params"))
        clip = {"type": "speech", "provider": provider, "model": model}
        for key in SPEECH_PARAM_KEYS:
            if key in params:
                clip[key] = params[key]
        if "at" not in clip:
            clip["at"] = 0.0
        if isinstance(fallback, dict):
            check_keys(bag, fallback, {"type"}, _join(at, "/fallback"))
            fb_type = fallback.get("type")
            if fb_type not in ("captions", "captions_only"):
                bag.schema(_join(at, "/fallback/type"), "Speech fallback must be captions.")
                return None
        return clip
    check_keys(bag, params, set(LIP_PARAM_KEYS), _join(at, "/params"))
    clip = {"type": "lip_sync", "provider": provider, "model": model}
    for key in LIP_PARAM_KEYS:
        if key in params:
            clip[key] = params[key]
    if isinstance(fallback, dict):
        clip["fallback"] = dict(fallback)
    return clip


def _resolve_tracks(
    bag: Problems,
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
            elif isinstance(raw_clip, dict) and raw_clip.get("type") in PAID_CLIP_TYPES:
                bag.schema(c_at, "Paid clips must use an ai step.")
            else:
                resolved_clips.append(raw_clip)
        copied = dict(raw_track)
        copied["clips"] = resolved_clips
        out_tracks.append(copied)
    return out_tracks, referenced


def _merge_disclosure(
    bag: Problems,
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
    shape_problems = check_recipe_shape(raw)
    raw_steps = raw.get("steps")
    if shape_problems or not isinstance(raw_steps, list):
        return None, shape_problems

    bag = Problems()
    step_at: dict[str, str] = {}
    ai_clips: dict[str, dict[str, object]] = {}
    disclosures: list[str] = []
    assemble_step: tuple[str, Mapping[str, object], tuple[str, ...]] | None = None

    for index, entry in enumerate(raw_steps):
        at = _join("/steps/", str(index))
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id"))
        kind = entry.get("kind")
        step_at[sid] = at
        if kind == "ai":
            clip, disc = _ai_step_to_clip(bag, entry, at)
            if clip is not None:
                ai_clips[sid] = clip
            if disc is not None:
                disclosures.append(disc)
        elif kind == "tool":
            if entry.get("action") != ASSEMBLE_ACTION:
                bag.unknown(_join(at, "/action"), str(entry.get("action")))
            elif assemble_step is not None:
                bag.schema(at, "Only one assemble step is allowed.")
            else:
                assemble_step = (at, entry, parse_needs(bag, entry, at))

    if assemble_step is None:
        bag.schema("/steps", "Missing bot.ffmpeg_assemble step.")
        return None, tuple(bag.items)

    asm_at, asm_entry, asm_needs = assemble_step
    asm_input = asm_entry.get("input")
    if not isinstance(asm_input, dict):
        bag.schema(_join(asm_at, "/input"), "Assemble input must be an object.")
        return None, tuple(bag.items)
    check_keys(bag, asm_input, ASSEMBLE_INPUT_KEYS, _join(asm_at, "/input"))
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
