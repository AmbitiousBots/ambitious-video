from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import NoReturn

from ambitious_kit.domain.clock import format_instant
from ambitious_kit.domain.envelope import ErrorEntry, Failure, JsonObject, JsonValue, failure
from ambitious_kit.paths import ambitious_home, refuse_agent_data

from ambitious_video import providers
from ambitious_video.db import step_cache
from ambitious_video.db.lock import Hold
from ambitious_video.db.requests import forget_request, store_request, stored_request
from ambitious_video.db.store import (
    JobRow,
    count_calls,
    insert_asset,
    insert_job,
    insert_ledger,
    list_ledger,
    mark_rendered,
    move_job,
    read_job,
)
from ambitious_video.db.tx import begin, commit
from ambitious_video.domain.clips import kind_of
from ambitious_video.domain.fallback import Fallback, with_fallbacks
from ambitious_video.domain.load import RELATIVE, load_script, say
from ambitious_video.domain.money import Quote, money_text, paid_units, rate_text, total_usd, units_text
from ambitious_video.domain.need import NeedTool
from ambitious_video.domain.plan import (
    LOCAL,
    LOCAL_VERSION,
    StepSpec,
    canonical,
    digest_text,
    plan_sha,
    sorted_json,
    step_key,
)
from ambitious_video.domain.timeline import Fault, Span, Timeline, asset_paths, graph_of, seconds_json, timeline_for
from ambitious_video.domain.types import (
    SCHEMA_VERSION,
    AudioClip,
    CaptionsTrack,
    Clip,
    ClipTrack,
    EncodeMode,
    GeneratedScene,
    ImageClip,
    PaidClip,
    Script,
    ScriptProblem,
    Sha256Hex,
    Speech,
    UnknownStep,
    VideoClip,
    idem_key,
)
from ambitious_video.media.audio import Piece, write_wav
from ambitious_video.media.film import STORED_AS, TO_RGB, Made, canvas_for, encode, frame_at, matches, prepare
from ambitious_video.media.probe import duration_of, frame_count, inspect, stored_as
from ambitious_video.providers.base import (
    Deferred,
    Provider,
    ProviderError,
    RequestLost,
    SceneMaker,
    await_result,
)
from ambitious_video.providers.pricing import PriceMissing, PriceTableBroken
from ambitious_video.steps import compile_clip
from ambitious_video.steps.common import readable_font, readable_image

FONT_INPUT = "brand.font"
PAID_KINDS: dict[type, str] = {Speech: "speech", GeneratedScene: "scene"}
MANIFEST_SUFFIX = ".manifest.json"
MANIFEST_VERSION = 1


class Waiting(Failure):
    pass


def as_json(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [as_json(item) for item in value]
    if isinstance(value, dict):
        built: dict[str, JsonValue] = {}
        for key, item in value.items():
            built[str(key)] = as_json(item)
        return built
    return str(value)


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _faults_of(problems: tuple[ScriptProblem, ...]) -> tuple[Fault, ...]:
    rows: list[Fault] = []
    for problem in problems:
        if isinstance(problem, UnknownStep):
            rows.append(Fault("E_UNKNOWN_STEP", problem.at, say("Unknown step ", problem.step[:40], ".")))
        else:
            rows.append(Fault("E_INPUT_SCHEMA", problem.at, problem.message))
    return tuple(rows)


def raise_faults(faults: tuple[Fault, ...], duration: int | float) -> None:
    if not faults:
        return
    data: JsonObject = {
        "ok": False,
        "durationSec": duration,
        "problems": [{"code": item.code, "at": item.at, "message": item.message} for item in faults],
    }
    hint = "Fix the script field in data.problems."
    raise Failure([ErrorEntry(item.code, item.message, hint) for item in faults[:20]], data)


def _checked(root: Path, relative: str) -> Path:
    return Path(refuse_agent_data(str((root / relative).resolve())))


def media_map(script: Script, root: Path) -> dict[str, float | None]:
    found: dict[str, float | None] = {}
    for path, kind in asset_paths(script):
        full = _checked(root, path)
        if not full.is_file():
            found[path] = None
        elif kind == "image":
            found[path] = 0.0
        else:
            found[path] = duration_of(str(full))
    return found


def font_faults(script: Script, root: Path) -> tuple[Fault, ...]:
    font = None if script.brand is None else script.brand.font
    if font is None:
        return ()
    at = "/brand/font"
    if RELATIVE.fullmatch(font) is None:
        inside = say("brand.font must be a font file ", "in the folder that holds the script.")
        return (Fault("E_MEDIA", at, inside),)
    full = _checked(root, font)
    if not full.is_file():
        missing = say(
            "The font file is missing. ",
            "brand.font must be the path ",
            "of a font file, such as fonts/name.ttf, ",
            "not the name of a font.",
        )
        return (Fault("E_MEDIA", at, missing),)
    if not readable_font(str(full)):
        unread = say("The font file cannot be read as a font. ", "Use a TrueType or OpenType file.")
        return (Fault("E_MEDIA", at, unread),)
    return ()


def _scene_faults(clip: GeneratedScene, found: Provider, at: str) -> list[Fault]:
    if not isinstance(found, SceneMaker):
        return [Fault("E_INPUT_SCHEMA", say(at, "/provider"), "That provider does not do this step.")]
    rows = [Fault("E_INPUT_SCHEMA", say(at, "/", name), message) for name, message in found.faults(clip)]
    if clip.seed is not None and not found.seedable:
        rows.append(Fault("E_INPUT_SCHEMA", say(at, "/seed"), "This provider takes no seed."))
    if clip.kenburns is not None and found.output != "image":
        rows.append(Fault("E_INPUT_SCHEMA", say(at, "/kenburns"), "Only a still picture takes kenburns."))
    return rows


def _provider_faults(script: Script) -> tuple[Fault, ...]:
    rows: list[Fault] = []
    for track_index, track in enumerate(script.tracks):
        if not isinstance(track, ClipTrack):
            continue
        for clip_index, clip in enumerate(track.clips):
            if not isinstance(clip, PaidClip):
                continue
            at = say("/tracks/", str(track_index), "/clips/", str(clip_index))
            found = providers.find(clip.provider)
            if found is None:
                rows.append(Fault("E_INPUT_SCHEMA", say(at, "/provider"), "No provider has that id."))
            elif found.kind != PAID_KINDS[type(clip)]:
                rows.append(Fault("E_INPUT_SCHEMA", say(at, "/provider"), "That provider does not do this step."))
            elif clip.model not in found.models:
                rows.append(Fault("E_INPUT_SCHEMA", say(at, "/model"), "The provider has no such model."))
            elif isinstance(clip, GeneratedScene):
                rows.extend(_scene_faults(clip, found, at))
    return tuple(rows)


def _provider_of(clip: PaidClip) -> Provider:
    found = providers.find(clip.provider)
    if found is None:
        raise failure("E_INPUT_SCHEMA", "No provider has that id.", "Fix the provider of the step.")
    return found


def _no_price() -> NoReturn:
    raise failure("E_INTERNAL", "The price table is unreadable.", "Reinstall the video module.")


def _declined(_clip: PaidClip) -> bool:
    return True


def _is_scene(clip: PaidClip) -> bool:
    return isinstance(clip, GeneratedScene)


def _fallback_faults(script: Script, media: Mapping[str, float | None], known: tuple[Fault, ...]) -> tuple[Fault, ...]:
    spare, placed = with_fallbacks(script, _is_scene)
    if not placed:
        return ()
    faults = timeline_for(spare, media, require_files=True).faults
    return tuple(item for item in faults if item not in known)


def studied(
    raw: object, root: Path, *, require_files: bool, offline: bool
) -> tuple[Script, Timeline, Mapping[int, Fallback]]:
    script, problems = load_script(raw)
    faults = _faults_of(problems)
    if script is None:
        raise_faults(faults, 0)
        raise failure("E_INPUT_SCHEMA", "The script is not valid.", "Fix the script field in data.problems.")
    raise_faults(_provider_faults(script), 0)
    placed: Mapping[tuple[str, int], Fallback] = {}
    if offline:
        script, placed = with_fallbacks(script, _declined)
    media = media_map(script, root) if require_files else None
    timeline = timeline_for(script, media, require_files=require_files)
    found = (*font_faults(script, root), *timeline.faults) if require_files else timeline.faults
    if media is not None and not offline:
        found = (*found, *_fallback_faults(script, media, timeline.faults))
    if found:
        raise_faults(found, seconds_json(timeline.total, timeline.fps))
    spots = {(span.track, span.index): span.step for span in timeline.spans}
    taken = {spots[spot]: found for spot, found in placed.items() if spot in spots}
    return script, timeline, taken


def study(raw: object, root: Path, *, require_files: bool) -> tuple[Script, Timeline]:
    script, timeline, _taken = studied(raw, root, require_files=require_files, offline=False)
    return script, timeline


def _fallback_mark(found: Fallback) -> dict[str, JsonValue]:
    lost = found.instead_of
    return {
        "fallback": found.name,
        "fallbackFor": {"type": kind_of(lost), "provider": lost.provider, "model": lost.model},
    }


def _db_path(db: sqlite3.Connection) -> Path:
    row = db.execute("PRAGMA database_list").fetchone()
    if not isinstance(row, tuple) or not isinstance(row[2], str) or row[2] == "":
        raise failure("E_DB", "The database path is missing.", "Run selfcheck and read data.problems.")
    return Path(row[2])


def _hashes(script: Script, root: Path) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for path, _kind in asset_paths(script):
        full = _checked(root, path)
        found[path] = file_sha(full) if full.is_file() else None
    font = None if script.brand is None else script.brand.font
    if font is not None:
        named = _checked(root, font) if RELATIVE.fullmatch(font) is not None else None
        found[FONT_INPUT] = file_sha(named) if named is not None and named.is_file() else None
    return found


def step_spec(clip: Clip, hashes: Mapping[str, str | None]) -> StepSpec:
    if isinstance(clip, PaidClip):
        found = _provider_of(clip)
        params = sorted_json(found.params(clip))
        if isinstance(clip, GeneratedScene):
            given: tuple[str | None, ...] = () if clip.image is None else (hashes.get(clip.image),)
            return StepSpec(found.kind, found.id, clip.model, found.adapter_version, params, clip.seed, given)
        return StepSpec(found.kind, found.id, clip.model, found.adapter_version, params, None, ())
    inputs: tuple[str | None, ...] = ()
    if isinstance(clip, VideoClip | ImageClip | AudioClip):
        inputs = (hashes.get(clip.path),)
    return StepSpec(kind_of(clip), LOCAL, None, LOCAL_VERSION, as_json(compile_clip(clip)), None, inputs)


def _zoom_json(clip: Clip) -> JsonValue:
    if not isinstance(clip, GeneratedScene) or clip.kenburns is None:
        return None
    return {"from": clip.kenburns.zoom_from, "to": clip.kenburns.zoom_to}


def _output_spec(script: Script, timeline: Timeline, hashes: Mapping[str, str | None], keys: list[str]) -> StepSpec:
    brand = script.brand
    colors: JsonValue = None
    if brand is not None and brand.colors is not None:
        colors = {"bg": brand.colors.bg, "fg": brand.colors.fg, "accent": brand.colors.accent}
    params: JsonValue = {
        "width": timeline.width,
        "height": timeline.height,
        "fps": timeline.fps,
        "frames": timeline.total,
        "encode": script.output.encode,
        "font": None if brand is None else brand.font,
        "colors": colors,
        "cuts": [{"atFrame": mark.at, "frames": mark.frames} for mark in timeline.cuts],
        "captions": [
            {"id": track.id, "from": track.source} for track in script.tracks if isinstance(track, CaptionsTrack)
        ],
        "placements": [
            {"index": span.step, "track": span.track, "atFrame": span.start, "frames": span.frames}
            for span in timeline.spans
        ],
    }
    made = [span for span in timeline.spans if isinstance(span.clip, GeneratedScene)]
    if made and isinstance(params, dict):
        params["aiTag"] = timeline.disclosure
        params["generated"] = [{"index": span.step, "kenburns": _zoom_json(span.clip)} for span in made]
    logo: tuple[str | None, ...] = ()
    if brand is not None and brand.logo is not None:
        logo = (hashes.get(brand.logo),)
    font: tuple[str | None, ...] = ()
    if brand is not None and brand.font is not None:
        font = (hashes.get(FONT_INPUT),)
    return StepSpec("render", LOCAL, None, LOCAL_VERSION, params, None, (*logo, *font, *keys))


def cache_root(env: Mapping[str, str], flag: Path | None, script: Script | None, root: Path) -> Path:
    if flag is not None:
        chosen = flag
    elif script is not None and script.cache_dir is not None:
        chosen = root / script.cache_dir
    else:
        chosen = Path(ambitious_home(env)) / "video" / "cache"
    return Path(refuse_agent_data(str(chosen.resolve())))


@dataclass(frozen=True, slots=True)
class Paid:
    span: Span
    clip: PaidClip
    provider: Provider
    spec: StepSpec
    key: str
    quote: Quote
    inputs: Mapping[str, Path]


def _paid(span: Span, clip: PaidClip, hashes: Mapping[str, str | None], root: Path) -> Paid:
    found = _provider_of(clip)
    spec = step_spec(clip, hashes)
    inputs: dict[str, Path] = {}
    if isinstance(clip, GeneratedScene) and clip.image is not None:
        inputs["image"] = _checked(root, clip.image)
    try:
        price = found.price(clip.model)
        units = paid_units(clip, price.unit)
        usd = found.quote(spec.params if isinstance(spec.params, Mapping) else {}, units)
    except (PriceMissing, PriceTableBroken):
        _no_price()
    return Paid(span, clip, found, spec, step_key(spec), Quote(span.step, span.kind, units, usd, price), inputs)


def paid_steps(timeline: Timeline, root: Path) -> tuple[Paid, ...]:
    hashes: dict[str, str | None] = {}
    for span in timeline.spans:
        if isinstance(span.clip, GeneratedScene) and span.clip.image is not None:
            full = _checked(root, span.clip.image)
            hashes[span.clip.image] = file_sha(full) if full.is_file() else None
    return tuple(_paid(span, span.clip, hashes, root) for span in timeline.spans if isinstance(span.clip, PaidClip))


def _damaged(error: step_cache.CacheDamaged) -> Failure:
    return failure(
        "E_CACHE",
        "A cache entry does not match its hash.",
        say("Delete the cache folder named ", error.step_key[:12], "..."),
        {"stepKey": error.step_key},
    )


def _entry(store: Path, key: str) -> step_cache.Entry | None:
    try:
        return step_cache.read_entry(store, key)
    except step_cache.CacheDamaged as error:
        raise _damaged(error) from None


def secrets_needed(owed: Iterable[Paid], env: Mapping[str, str]) -> list[JsonObject]:
    waiting: dict[str, list[Paid]] = {}
    for item in owed:
        if env.get(item.provider.secret, "") == "":
            waiting.setdefault(item.provider.secret, []).append(item)
    return [
        {
            "env": name,
            "provider": ", ".join(sorted({item.provider.id for item in waiting[name]})),
            "steps": sorted(item.span.step for item in waiting[name]),
            "usd": money_text(sum((item.quote.usd for item in waiting[name]), start=Decimal(0))),
        }
        for name in sorted(waiting)
    ]


def _no_key(missing: list[JsonObject]) -> Failure:
    message = say("This command needs an AI call ", "and no key was set.")
    errors = [
        ErrorEntry(
            "E_NO_KEY",
            message,
            say("Set ", str(entry["env"])[:64], " in the environment", ", or run again with --offline."),
        )
        for entry in missing[:20]
    ]
    return Failure(errors, {"missing": missing})


def _elsewhere(db: sqlite3.Connection, store: Path, key: str) -> step_cache.Entry | None:
    for folder, digest in step_cache.ready_elsewhere(db, key, str(store)):
        try:
            found = step_cache.read_entry(Path(refuse_agent_data(folder)), key)
        except (step_cache.CacheDamaged, Failure, OSError):
            continue
        if found is not None and found.sha256 == digest:
            return found
    return None


def _stored(db: sqlite3.Connection, store: Path, key: str) -> bool:
    return _entry(store, key) is not None or _elsewhere(db, store, key) is not None


def _keyless(db: sqlite3.Connection, store: Path, item: Paid, env: Mapping[str, str]) -> bool:
    return env.get(item.provider.secret, "") == "" and not _stored(db, store, item.key)


def _require_keys(db: sqlite3.Connection, paid: tuple[Paid, ...], store: Path, env: Mapping[str, str]) -> None:
    missing = secrets_needed((item for item in paid if not _stored(db, store, item.key)), env)
    if missing:
        raise _no_key(missing)


def _waiting(item: Paid, job_id: str, request_id: str) -> Waiting:
    entry = ErrorEntry(
        "E_PENDING",
        "The provider has not finished the step.",
        say("Retry the job id later. ", "The request is kept ", "and is not sent again."),
    )
    data: JsonObject = {
        "jobId": job_id,
        "index": item.span.step,
        "stepKey": item.key,
        "provider": item.provider.id,
        "requestId": request_id,
    }
    return Waiting([entry], data)


def _lost(item: Paid) -> Failure:
    return failure(
        "E_PROVIDER",
        "The provider could not make the step.",
        say("Render again to send the step again, ", "or run with --offline."),
        {"provider": item.provider.id, "stepKey": item.key},
    )


def _usable(item: Paid, path: Path) -> bool:
    if not isinstance(item.provider, SceneMaker):
        return True
    if item.provider.output != "image":
        return duration_of(str(path)) is not None
    return readable_image(path)


def _later(
    db: sqlite3.Connection, item: Paid, maker: Deferred, env: Mapping[str, str], job_id: str, stamp: str
) -> bytes:
    work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
    try:
        params = item.spec.params if isinstance(item.spec.params, Mapping) else {}
        request_id = stored_request(db, item.key)
        if request_id is None:
            request_id = maker.submit(params, item.inputs, env.get(item.provider.secret, ""))
            begin(db)
            store_request(db, item.key, item.provider.id, item.spec.model or "", request_id, stamp)
            commit(db)
        try:
            result = await_result(maker, request_id, env.get(item.provider.secret, ""), work / step_cache.BODY)
        except RequestLost:
            begin(db)
            forget_request(db, item.key)
            commit(db)
            raise _lost(item) from None
        if result is None:
            raise _waiting(item, job_id, request_id)
        if not _usable(item, result.path):
            begin(db)
            forget_request(db, item.key)
            commit(db)
            raise _lost(item)
        return result.path.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(db: sqlite3.Connection, item: Paid, env: Mapping[str, str], job_id: str, stamp: str) -> bytes:
    if isinstance(item.provider, Deferred):
        return _later(db, item, item.provider, env, job_id, stamp)
    work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
    try:
        params = item.spec.params if isinstance(item.spec.params, Mapping) else {}
        result = item.provider.run(params, item.inputs, env.get(item.provider.secret, ""), work / step_cache.BODY)
        if not _usable(item, result.path):
            raise _lost(item)
        return result.path.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _brought(db: sqlite3.Connection, store: Path, key: str, label: step_cache.Label) -> step_cache.Entry | None:
    other = _elsewhere(db, store, key)
    if other is None:
        return None
    try:
        return step_cache.copy_entry(other, store, label)
    except (step_cache.CacheDamaged, OSError):
        return None


def _add_line(db: sqlite3.Connection, item: Paid, job_id: str) -> None:
    call = count_calls(db, item.key) + 1
    while not insert_ledger(
        db,
        job_id=job_id,
        step=item.span.kind,
        step_index=item.span.step,
        units=units_text(item.quote.units),
        usd=money_text(item.quote.usd),
        idem_key=str(idem_key(Sha256Hex(item.key), call)),
    ):
        call += 1


def _fetch(
    db: sqlite3.Connection,
    store: Path,
    item: Paid,
    job_id: str,
    stamp: str,
    call: Callable[[], bytes],
) -> tuple[Path, bool]:
    label = step_cache.Label(item.spec.kind, item.spec.provider, item.spec.model or "", item.spec.adapter_version)
    where = str(store)
    across = Hold(str(_db_path(db).parent / "locks" / ("step-" + item.key + ".lock")))
    hold: Hold | None = None
    try:
        across.lock()
        hold = Hold(str(step_cache.lock_path(store, item.key)))
        hold.lock()
        entry = _entry(store, item.key)
        owed = False
        if entry is None:
            entry = _brought(db, store, item.key, label)
        else:
            owed = step_cache.row_state(db, item.key, where) == "calling"
        if entry is None:
            begin(db)
            step_cache.mark_calling(db, item.key, where, label, stamp)
            commit(db)
            try:
                body = call()
            except Waiting:
                raise
            except Exception:
                begin(db)
                step_cache.delete_row(db, item.key, where)
                commit(db)
                raise
            entry = step_cache.write_entry(store, item.key, label, body)
            owed = True
        begin(db)
        if owed:
            _add_line(db, item, job_id)
        forget_request(db, item.key)
        step_cache.mark_ready(db, entry, where, label, stamp)
        commit(db)
        return entry.path, not owed
    finally:
        if hold is not None:
            hold.close()
        across.close()


@dataclass(frozen=True, slots=True)
class Spoken:
    pieces: tuple[Piece, ...]
    steps: tuple[JsonObject, ...]
    billed: Decimal
    media: Mapping[int, Made]


def _pieces(
    timeline: Timeline,
    root: Path,
    env: Mapping[str, str],
    db: sqlite3.Connection,
    job_id: str,
    paid: tuple[Paid, ...],
    store: Path,
    stamp: str,
) -> Spoken:
    pieces: list[Piece] = []
    ranges: list[tuple[int, int]] = []
    steps: list[JsonObject] = []
    billed = Decimal(0)
    seen: dict[str, Path] = {}
    media: dict[int, Made] = {}
    for item in paid:
        cached = item.key in seen
        if not cached:
            if _keyless(db, store, item, env):
                raise _no_key(secrets_needed((item,), env))
            call = partial(_run, db, item, env, job_id, stamp)
            seen[item.key], cached = _fetch(db, store, item, job_id, stamp, call)
        usd = Decimal(0) if cached else item.quote.usd
        billed += usd
        steps.append(
            {
                "index": item.span.step,
                "step": item.span.kind,
                "stepKey": item.key,
                "provider": item.spec.provider,
                "model": item.spec.model,
                "cached": cached,
                "usd": money_text(usd),
            }
        )
        if isinstance(item.clip, Speech):
            pieces.append(Piece(seen[item.key], item.span.start, 0.0, ()))
            ranges.append((item.span.start, item.span.end))
        elif isinstance(item.provider, SceneMaker):
            media[item.span.step] = Made(seen[item.key], item.provider.output == "image")
    for span in timeline.spans:
        if isinstance(span.clip, AudioClip):
            ducks = tuple(ranges) if span.clip.duck_under is not None else ()
            gain = 0.0 if span.clip.gain_db is None else span.clip.gain_db
            pieces.append(Piece(root / str(span.clip.path), span.start, gain, ducks))
    return Spoken(tuple(pieces), tuple(steps), billed, media)


def _standins(plan: JsonObject) -> list[JsonObject]:
    listed = plan["steps"]
    found: list[JsonObject] = []
    for step in listed if isinstance(listed, list) else []:
        if isinstance(step, Mapping) and "fallback" in step:
            found.append(
                {
                    "index": step["index"],
                    "step": step["type"],
                    "stepKey": step["stepKey"],
                    "provider": step["provider"],
                    "model": step["model"],
                    "cached": False,
                    "usd": money_text(Decimal(0)),
                    "fallback": step["fallback"],
                    "fallbackFor": step["fallbackFor"],
                }
            )
    return found


def _step_index(step: JsonObject) -> int:
    index = step["index"]
    return index if isinstance(index, int) else 0


def _manifest(job_id: str, output: Path, script: Script, spoken: Spoken, plan: JsonObject, offline: bool) -> JsonObject:
    billed = {_step_index(step): step for step in spoken.steps}
    listed = plan["steps"]
    steps: list[JsonValue] = []
    for step in listed if isinstance(listed, list) else []:
        if not isinstance(step, Mapping):
            continue
        paid = billed.get(_step_index(step))
        row: dict[str, JsonValue] = {
            "index": step["index"],
            "type": step["type"],
            "stepKey": step["stepKey"],
            "provider": step["provider"],
            "model": step["model"],
            "paid": step["paid"],
            "cached": False if paid is None else paid["cached"],
            "usd": money_text(Decimal(0)) if paid is None else paid["usd"],
        }
        for name in ("fallback", "fallbackFor"):
            if name in step:
                row[name] = step[name]
        steps.append(row)
    final = plan["output"]
    return {
        "manifestVersion": MANIFEST_VERSION,
        "jobId": job_id,
        "template": plan.get("template"),
        "planSha256": plan["planSha256"],
        "offline": offline,
        "disclosure": script.disclosure,
        "costUsd": money_text(spoken.billed),
        "output": {
            "file": output.name,
            "sha256": file_sha(output),
            "stepKey": final["stepKey"] if isinstance(final, Mapping) else None,
        },
        "steps": steps,
    }


def manifest_path(output: Path) -> Path:
    return output.with_name(output.name + MANIFEST_SUFFIX)


def _write_manifest(output: Path, manifest: JsonObject) -> None:
    target = manifest_path(output)
    draft = target.with_name(target.name + ".partial")
    draft.write_text(json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(draft, target)


def _rendered(
    job_id: str, output: Path, timeline: Timeline, spoken: Spoken, plan: JsonObject, store: Path
) -> JsonObject:
    steps = sorted([*spoken.steps, *_standins(plan)], key=_step_index)
    return {
        "jobId": job_id,
        "output": str(output),
        "manifest": str(manifest_path(output)),
        "sha256": file_sha(output),
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "costUsd": money_text(spoken.billed),
        "planSha256": plan["planSha256"],
        "cacheDir": str(store),
        "steps": steps,
    }


def _produce(script: Script, timeline: Timeline, root: Path, dest: Path, spoken: Spoken) -> str:
    work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
    partial = work / "video.mp4"
    try:
        wav = work / "audio.wav"
        write_wav(wav, timeline.total, timeline.fps, spoken.pieces)
        canvas = canvas_for(timeline, root)
        prepare(timeline, canvas, work, root, spoken.media)
        dest.parent.mkdir(parents=True, exist_ok=True)
        encode(timeline, canvas, wav, partial, script.output.encode)
        os.replace(partial, dest)
        return file_sha(dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _remember_assets(db: sqlite3.Connection, script: Script, root: Path) -> None:
    for path, kind in asset_paths(script):
        full = root / path
        if full.is_file() and kind in {"image", "video", "audio", "voiceover", "screen_recording"}:
            insert_asset(db, file_sha(full), path, kind)


def render_new(
    db: sqlite3.Connection,
    raw: object,
    root: Path,
    job_id: str,
    when: datetime,
    env: Mapping[str, str],
    cache_flag: Path | None = None,
    *,
    offline: bool = False,
) -> JsonObject:
    script, timeline, _taken = studied(raw, root, require_files=True, offline=offline)
    store = cache_root(env, cache_flag, script, root)
    paid = paid_steps(timeline, root)
    _require_keys(db, paid, store, env)
    plan = plan_data(raw, root, offline=offline)
    stamp = format_instant(when)
    output = _checked(root, str(script.output.path))
    body = canonical(raw if isinstance(raw, dict) else {})
    row = JobRow(
        job_id,
        digest_text(body),
        "queued",
        stamp,
        None,
        str(output),
        None,
        body,
        str(root),
        str(seconds_json(timeline.total, timeline.fps)),
        timeline.width,
        timeline.height,
        timeline.fps,
    )
    insert_job(db, row)
    if not move_job(db, job_id, "rendering", None):
        raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
    commit(db)
    hold = Hold(str(_db_path(db).parent / "locks" / (job_id + ".lock")))
    try:
        if not hold.try_lock():
            raise failure("E_JOB_STATE", "A render already holds this job.", "Wait for it, or retry the job id.")
        try:
            spoken = _pieces(timeline, root, env, db, job_id, paid, store, stamp)
            digest = _produce(script, timeline, root, output, spoken)
            _write_manifest(output, _manifest(job_id, output, script, spoken, plan, offline))
            begin(db)
            _remember_assets(db, script, root)
            if not mark_rendered(db, job_id, stamp, digest):
                raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
            commit(db)
        except Exception:
            begin(db)
            move_job(db, job_id, "failed", stamp)
            commit(db)
            raise
    finally:
        hold.close()
    begin(db)
    return _rendered(job_id, output, timeline, spoken, plan, store)


def render_again(
    db: sqlite3.Connection,
    job_id: str,
    raw: object | None,
    when: datetime,
    env: Mapping[str, str],
    cache_flag: Path | None = None,
    *,
    offline: bool = False,
) -> JsonObject:
    row = read_job(db, job_id)
    if row is None:
        raise failure("E_INPUT_SCHEMA", "No job has that id.", "Use an id from the jobs command.")
    stored = json.loads(row.script_json)
    if raw is not None and canonical(raw) != row.script_json and digest_text(canonical(raw)) != row.script_sha256:
        raise failure("E_INPUT_SCHEMA", "The script does not match the job.", "Retry with the same script.")
    root = Path(row.script_dir)
    script, timeline, _taken = studied(stored, root, require_files=True, offline=offline)
    store = cache_root(env, cache_flag, script, root)
    paid = paid_steps(timeline, root)
    _require_keys(db, paid, store, env)
    plan = plan_data(stored, root, offline=offline)
    stamp = format_instant(when)
    commit(db)
    hold = Hold(str(_db_path(db).parent / "locks" / (job_id + ".lock")))
    try:
        if row.state == "rendering":
            if not hold.try_lock():
                raise failure("E_JOB_STATE", "A render already holds this job.", "Wait for it, or retry the job id.")
            begin(db)
            if not move_job(db, job_id, "failed", stamp):
                commit(db)
                raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
            if not move_job(db, job_id, "retried", None):
                commit(db)
                raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
            if not move_job(db, job_id, "rendering", None):
                commit(db)
                raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
            commit(db)
        elif row.state == "failed":
            if not hold.try_lock():
                raise failure("E_JOB_STATE", "A render already holds this job.", "Wait for it, or retry the job id.")
            begin(db)
            if not move_job(db, job_id, "retried", None) or not move_job(db, job_id, "rendering", None):
                commit(db)
                raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
            commit(db)
        else:
            raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
        try:
            output = Path(row.output_path)
            spoken = _pieces(timeline, root, env, db, job_id, paid, store, stamp)
            digest = _produce(script, timeline, root, output, spoken)
            _write_manifest(output, _manifest(job_id, output, script, spoken, plan, offline))
            begin(db)
            _remember_assets(db, script, root)
            if not mark_rendered(db, job_id, stamp, digest):
                raise failure("E_JOB_STATE", "The job cannot make that move.", "Read the job state with jobs.")
            commit(db)
        except Exception:
            begin(db)
            move_job(db, job_id, "failed", stamp)
            commit(db)
            raise
    finally:
        hold.close()
    begin(db)
    return _rendered(job_id, Path(row.output_path), timeline, spoken, plan, store)


def _made_at(timeline: Timeline, index: int) -> bool:
    made = [span for span in timeline.spans if isinstance(span.clip, GeneratedScene)]
    if any(span.start <= index < span.end for span in made):
        return True
    edges = {edge for span in made for edge in (span.start, span.end)}
    windows = [mark for mark in timeline.cuts if mark.at - mark.half <= index < mark.at - mark.half + mark.frames]
    return any(mark.at in edges for mark in windows)


def _samples(timeline: Timeline) -> tuple[tuple[str, int], ...]:
    windows = [(mark.at - mark.half, mark.at - mark.half + mark.frames) for mark in timeline.cuts]
    chosen: list[tuple[str, int]] = []
    for span in timeline.spans:
        if span.kind != "title_card":
            continue
        chosen.append((say("title-", str(span.step)), span.start + span.frames // 2))
    visual = [span for span in timeline.spans if span.kind in {"title_card", "image"}]
    for mark in timeline.cuts:
        left = next((span for span in visual if span.end == mark.at), None)
        right = next((span for span in visual if span.start == mark.at), None)
        if left is not None and right is not None:
            if not any(start <= mark.at < end for start, end in windows):
                pass
            chosen.append((say("cut-", str(mark.at)), mark.at))
    return tuple(item for item in chosen if not _made_at(timeline, item[1]))


def _grab(video: str, index: int, dest: Path, counted: int, mode: EncodeMode) -> None:
    import subprocess

    if shutil.which("ffmpeg") is None:
        raise NeedTool("ffmpeg")
    picked = "select=eq(n\\," + str(index) + ")"
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video,
            "-vf",
            picked if mode == "lossless" else picked + "," + TO_RGB,
            "-vframes",
            "1",
            str(dest),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and index < counted:
        raise NeedTool("ffmpeg")


def _seconds_text(value: float) -> str:
    return format(value, ".3f").rstrip("0").rstrip(".")


def _why(check: Mapping[str, object]) -> str:
    name = str(check.get("name"))
    if name == "file":
        return "The rendered file is missing."
    if name == "readable":
        return "The rendered file is not a video."
    expected, actual = check.get("expected"), check.get("actual")
    if name == "duration" and isinstance(expected, float) and isinstance(actual, float):
        return say("The video lasts ", _seconds_text(actual), " s, not ", _seconds_text(expected), " s.")
    if name == "frames" and actual == -1:
        return "The video frames could not be counted."
    if name == "frames":
        return say("The video has ", str(actual), " frames, not ", str(expected), ".")
    if name == "resolution":
        return say("The video is ", str(actual), ", not ", str(expected), ".")
    if name == "audio":
        return "The video has no audio track."
    if name == "encoding":
        return say("The video is stored as ", str(actual), ", not ", str(expected), ".")
    if check.get("found") is False:
        return say("Frame ", str(check.get("frame")), " (", name, ") is missing from the video.")
    return say("Frame ", str(check.get("frame")), " (", name, ") differs from the script.")


def verify_job(db: sqlite3.Connection, job_id: str, when: datetime) -> JsonObject:
    row = read_job(db, job_id)
    if row is None:
        raise failure("E_INPUT_SCHEMA", "No job has that id.", "Use an id from the jobs command.")
    if row.state != "rendered":
        raise failure("E_JOB_STATE", "The job cannot make that move.", "Verify only a rendered job.")
    stored = json.loads(row.script_json)
    script, timeline = study(stored, Path(row.script_dir), require_files=True)
    commit(db)
    checks: list[dict[str, object]] = []
    output = Path(row.output_path)
    if not output.is_file():
        checks.append({"name": "file", "ok": False})
    else:
        width, height, duration, audio = inspect(str(output))
        expected = float(row.duration_sec)
        counted = frame_count(str(output))
        size, wanted = say(str(width), "x", str(height)), say(str(row.width), "x", str(row.height))
        readable = duration >= 0 and width > 0
        checks.append({"name": "readable", "ok": readable})
        checks.append(
            {"name": "duration", "ok": abs(duration - expected) <= 0.05, "expected": expected, "actual": duration}
        )
        total = timeline.total
        checks.append({"name": "frames", "ok": counted == total, "expected": total, "actual": counted})
        checks.append({"name": "resolution", "ok": size == wanted, "expected": wanted, "actual": size})
        checks.append({"name": "audio", "ok": audio})
        mode = script.output.encode
        kept = " ".join(stored_as(str(output)) if readable else ("", "")).strip()
        asked = " ".join(STORED_AS[mode])
        checks.append({"name": "encoding", "ok": kept == asked, "expected": asked, "actual": kept})
        work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
        try:
            canvas = canvas_for(timeline, Path(row.script_dir))
            prepare(timeline, canvas, work, Path(row.script_dir))
            for name, index in _samples(timeline):
                painted = frame_at(timeline, index, canvas)
                shot = work / (name + ".png")
                _grab(str(output), index, shot, counted, mode)
                found = shot.is_file()
                same = found and matches(painted, shot, mode)
                checks.append({"name": name, "ok": same, "frame": index, "found": found})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    passed = all(item.get("ok") is True for item in checks)
    begin(db)
    move_job(db, job_id, "verified" if passed else "failed", format_instant(when))
    if passed:
        return {"jobId": job_id, "passed": True, "checks": as_json(checks)}
    commit(db)
    hint = "Read data.checks for the failed check."
    failed = [item for item in checks if item.get("ok") is not True]
    if failed[0].get("name") == "readable":
        failed = failed[:1]
    raise Failure(
        [ErrorEntry("E_VERIFY", _why(item), hint) for item in failed[:20]],
        {"jobId": job_id, "passed": False, "checks": as_json(checks)},
    )


def plan_data(raw: object, root: Path, *, offline: bool = False) -> JsonObject:
    script, timeline, taken = studied(raw, root, require_files=False, offline=offline)
    hashes = _hashes(script, root)
    steps: list[JsonValue] = []
    keys: list[str] = []
    for span in timeline.spans:
        spec = step_spec(span.clip, hashes)
        key = step_key(spec)
        keys.append(key)
        step = compile_clip(span.clip)
        step["index"] = span.step
        step["atFrame"] = span.start
        step["frames"] = span.frames
        step["track"] = span.track
        step["stepKey"] = key
        step["provider"] = spec.provider
        step["model"] = spec.model
        step["paid"] = spec.paid
        if span.step in taken:
            step.update(_fallback_mark(taken[span.step]))
        steps.append(as_json(step))
    out = script.output
    plan: dict[str, JsonValue] = {
        "scriptSchemaVersion": SCHEMA_VERSION,
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "steps": steps,
        "graph": graph_of(timeline),
        "assets": [{"path": path, "kind": kind, "sha256": hashes.get(path)} for path, kind in asset_paths(script)],
        "output": {
            "path": out.path,
            "width": out.width,
            "height": out.height,
            "fps": out.fps,
            "stepKey": step_key(_output_spec(script, timeline, hashes, keys)),
        },
        "captions": as_json(
            [
                {"track": item.track, "atFrame": item.start, "frames": item.frames, "text": item.text}
                for item in timeline.captions
            ]
        ),
        "encode": out.encode,
    }
    if script.template is not None:
        plan["template"] = {"id": script.template.id, "templateVersion": script.template.version}
    plan["planSha256"] = plan_sha(plan)
    return {name: sorted_json(plan[name]) for name in sorted(plan)}


def _cache_rows(db: sqlite3.Connection, only: Path | None) -> tuple[step_cache.CacheRow, ...]:
    rows = step_cache.list_rows(db)
    if only is None:
        return rows
    wanted = str(Path(refuse_agent_data(str(only.resolve()))))
    return tuple(row for row in rows if row.dir == wanted)


def _cache_line(row: step_cache.CacheRow) -> JsonObject:
    present = (
        step_cache.STEP_KEY.fullmatch(row.step_key) is not None
        and (Path(row.dir) / row.step_key / step_cache.BODY).is_file()
    )
    return {
        "stepKey": row.step_key,
        "dir": row.dir,
        "kind": row.kind,
        "provider": row.provider,
        "model": row.model,
        "state": row.state,
        "sha256": row.sha256,
        "bytes": row.size,
        "createdAt": row.created_at,
        "usedAt": row.used_at,
        "present": present,
    }


def cache_list(db: sqlite3.Connection, only: Path | None) -> JsonObject:
    rows = _cache_rows(db, only)
    return {
        "entries": [_cache_line(row) for row in rows],
        "count": len(rows),
        "bytes": sum(row.size or 0 for row in rows),
    }


def _pruned(db: sqlite3.Connection, row: step_cache.CacheRow) -> bool:
    root = Path(refuse_agent_data(row.dir))
    if not root.is_dir():
        step_cache.delete_row(db, row.step_key, row.dir)
        return True
    hold = Hold(str(step_cache.lock_path(root, row.step_key)))
    try:
        if not hold.try_lock():
            return False
        step_cache.remove_entry(root, row.step_key)
        step_cache.delete_row(db, row.step_key, row.dir)
        return True
    finally:
        hold.close()


def cache_prune(db: sqlite3.Connection, only: Path | None, days: int, when: datetime) -> JsonObject:
    cutoff = format_instant(when - timedelta(days=days))
    rows = _cache_rows(db, only)
    removed: list[JsonObject] = []
    freed = 0
    for row in rows:
        if row.used_at >= cutoff:
            continue
        try:
            if not _pruned(db, row):
                continue
        except step_cache.CacheDamaged as error:
            raise _damaged(error) from None
        removed.append({"stepKey": row.step_key, "dir": row.dir, "bytes": row.size})
        freed += row.size or 0
    return {
        "olderThanDays": days,
        "cutoff": cutoff,
        "removed": removed,
        "kept": len(rows) - len(removed),
        "freedBytes": freed,
    }


def _estimate_line(span: Span, timeline: Timeline, item: Paid | None, cached: bool) -> tuple[Quote, JsonObject]:
    if item is None:
        free = Quote(span.step, span.kind, Decimal(span.frames) / Decimal(timeline.fps), Decimal(0))
        return free, {"step": free.step, "index": free.step_index, "units": units_text(free.units), "usd": "0.000000"}
    price = item.quote.price
    if price is None:
        _no_price()
    quote = Quote(span.step, span.kind, item.quote.units, Decimal(0) if cached else item.quote.usd, price)
    return quote, {
        "step": quote.step,
        "index": quote.step_index,
        "units": units_text(quote.units),
        "usd": money_text(quote.usd),
        "provider": item.provider.id,
        "model": item.clip.model,
        "unit": price.unit,
        "usdPerUnit": rate_text(price.usd_per_unit),
        "sourceUrl": price.source_url,
        "checkedAt": price.checked_at,
        "stepKey": item.key,
        "cached": cached,
    }


def _owed(paid: tuple[Paid, ...], store: Path) -> tuple[Paid, ...]:
    return tuple(item for item in paid if _entry(store, item.key) is None)


def plan_view(
    raw: object, root: Path, env: Mapping[str, str], cache_flag: Path | None = None, *, offline: bool = False
) -> JsonObject:
    plan = dict(plan_data(raw, root, offline=offline))
    script, timeline, _taken = studied(raw, root, require_files=False, offline=offline)
    store = cache_root(env, cache_flag, script, root)
    plan["secretsNeeded"] = secrets_needed(_owed(paid_steps(timeline, root), store), env)
    return {name: plan[name] for name in sorted(plan)}


def estimate_data(
    raw: object, root: Path, env: Mapping[str, str], cache_flag: Path | None = None, *, offline: bool = False
) -> JsonObject:
    script, timeline, taken = studied(raw, root, require_files=False, offline=offline)
    store = cache_root(env, cache_flag, script, root)
    every = paid_steps(timeline, root)
    owed = _owed(every, store)
    waiting = {item.span.step for item in owed}
    paid = {item.span.step: item for item in every}
    rows: list[Quote] = []
    lines: list[JsonObject] = []
    for span in timeline.spans:
        item = paid.get(span.step)
        cached = item is not None and span.step not in waiting
        quote, line = _estimate_line(span, timeline, item, cached)
        rows.append(quote)
        lines.append({**line, **_fallback_mark(taken[span.step])} if span.step in taken else line)
    return {
        "usd": money_text(total_usd(tuple(rows))),
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "breakdown": lines,
        "secretsNeeded": secrets_needed(owed, env),
    }


def validate_data(raw: object, root: Path) -> JsonObject:
    _script, timeline = study(raw, root, require_files=True)
    return {"ok": True, "durationSec": seconds_json(timeline.total, timeline.fps), "problems": []}


def jobs_data(db: sqlite3.Connection) -> JsonObject:
    from ambitious_video.db.store import list_jobs

    return {
        "jobs": [
            {
                "id": row.id,
                "state": row.state,
                "scriptSha256": row.script_sha256,
                "createdAt": row.created_at,
                "finishedAt": row.finished_at,
                "outputPath": row.output_path,
                "outputSha256": row.output_sha256,
            }
            for row in list_jobs(db)
        ]
    }


def ledger_data(db: sqlite3.Connection) -> JsonObject:
    return {
        "lines": [
            {
                "jobId": row.job_id,
                "step": row.step,
                "index": row.step_index,
                "units": row.units,
                "usd": row.usd,
                "idemKey": row.idem_key,
            }
            for row in list_ledger(db)
        ]
    }


def tool_failure(error: NeedTool) -> Failure:
    return failure(
        "E_TOOL",
        say("The tool ", error.tool, " is not available."),
        "Install it and run the command again.",
    )


def provider_failure(error: ProviderError) -> Failure:
    return failure(
        "E_PROVIDER",
        "The provider rejected the call.",
        "Check the model, the voice and the key.",
        {"provider": error.provider, "status": error.status},
    )

