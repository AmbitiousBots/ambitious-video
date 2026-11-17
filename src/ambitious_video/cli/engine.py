from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path

from ambitious_kit.domain.clock import format_instant
from ambitious_kit.domain.envelope import ErrorEntry, Failure, JsonObject, JsonValue, failure
from ambitious_kit.paths import ambitious_home, refuse_agent_data

from ambitious_video.ai import speech
from ambitious_video.ai.speech import SpeechError, speak
from ambitious_video.db import step_cache
from ambitious_video.db.lock import Hold
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
from ambitious_video.domain.load import RELATIVE, load_script, say
from ambitious_video.domain.money import Quote, money_text, quotes, total_usd, units_text
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
    EncodeMode,
    ImageClip,
    Script,
    ScriptProblem,
    Sha256Hex,
    UnknownStep,
    VideoClip,
    Voiceover,
    idem_key,
)
from ambitious_video.media.audio import Piece, write_wav
from ambitious_video.media.film import STORED_AS, TO_RGB, canvas_for, encode, frame_at, matches, prepare
from ambitious_video.media.probe import duration_of, inspect, stored_as
from ambitious_video.steps import compile_clip
from ambitious_video.steps.common import readable_font

Speaker = Callable[[str, str, str], bytes]
FONT_INPUT = "brand.font"


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


def study(raw: object, root: Path, *, require_files: bool) -> tuple[Script, Timeline]:
    script, problems = load_script(raw)
    faults = _faults_of(problems)
    if script is None:
        raise_faults(faults, 0)
        raise failure("E_INPUT_SCHEMA", "The script is not valid.", "Fix the script field in data.problems.")
    media = media_map(script, root) if require_files else None
    timeline = timeline_for(script, media, require_files=require_files)
    found = (*font_faults(script, root), *timeline.faults) if require_files else timeline.faults
    if found:
        raise_faults(found, seconds_json(timeline.total, timeline.fps))
    return script, timeline


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


def step_spec(script: Script, clip: Clip, hashes: Mapping[str, str | None]) -> StepSpec:
    if isinstance(clip, Voiceover) and script.voice is not None:
        params = speech.request_params(clip.text, script.voice.voice)
        return StepSpec("speech", speech.PROVIDER, speech.MODEL, speech.ADAPTER_VERSION, params, None, ())
    inputs: tuple[str | None, ...] = ()
    if isinstance(clip, VideoClip | ImageClip | AudioClip):
        inputs = (hashes.get(clip.path),)
    return StepSpec(kind_of(clip), LOCAL, None, LOCAL_VERSION, as_json(compile_clip(clip)), None, inputs)


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
    spec: StepSpec
    key: str
    quote: Quote


def paid_steps(script: Script, timeline: Timeline, rate: Decimal) -> tuple[Paid, ...]:
    priced = {item.step_index: item for item in quotes(script, timeline, rate)}
    found: list[Paid] = []
    for span in timeline.spans:
        spec = step_spec(script, span.clip, {})
        quote = priced[span.step]
        if spec.paid and quote.paid:
            found.append(Paid(span, spec, step_key(spec), quote))
    return tuple(found)


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


def _no_key() -> Failure:
    return failure(
        "E_NO_KEY",
        say("This command needs an AI call ", "and no key was set."),
        say("Set ", speech.KEY_ENV, " in the environment", " for this call."),
    )


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


def _require_key(db: sqlite3.Connection, paid: tuple[Paid, ...], store: Path, key: str) -> None:
    if key == "" and not all(_stored(db, store, item.key) for item in paid):
        raise _no_key()


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


def _pieces(
    script: Script,
    timeline: Timeline,
    root: Path,
    key: str,
    speaker: Speaker,
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
    for item in paid:
        clip = item.span.clip
        if not isinstance(clip, Voiceover) or script.voice is None:
            continue
        cached = item.key in seen
        if not cached:
            if key == "" and not _stored(db, store, item.key):
                raise _no_key()
            call = partial(speaker, clip.text, script.voice.voice, key)
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
        pieces.append(Piece(seen[item.key], item.span.start, 0.0, ()))
        ranges.append((item.span.start, item.span.end))
    for span in timeline.spans:
        if isinstance(span.clip, AudioClip):
            ducks = tuple(ranges) if span.clip.duck_under is not None else ()
            gain = 0.0 if span.clip.gain_db is None else span.clip.gain_db
            pieces.append(Piece(root / str(span.clip.path), span.start, gain, ducks))
    return Spoken(tuple(pieces), tuple(steps), billed)


def _rendered(
    job_id: str, output: Path, timeline: Timeline, spoken: Spoken, plan: JsonObject, store: Path
) -> JsonObject:
    return {
        "jobId": job_id,
        "output": str(output),
        "sha256": file_sha(output),
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "costUsd": money_text(spoken.billed),
        "planSha256": plan["planSha256"],
        "cacheDir": str(store),
        "steps": list(spoken.steps),
    }


def _produce(script: Script, timeline: Timeline, root: Path, dest: Path, pieces: tuple[Piece, ...]) -> str:
    work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
    partial = work / "video.mp4"
    try:
        wav = work / "audio.wav"
        write_wav(wav, timeline.total, timeline.fps, pieces)
        canvas = canvas_for(timeline, root)
        prepare(timeline, canvas, work, root)
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
    rate: Decimal,
    env: Mapping[str, str],
    speaker: Speaker,
    cache_flag: Path | None = None,
) -> JsonObject:
    script, timeline = study(raw, root, require_files=True)
    key = env.get(speech.KEY_ENV, "")
    store = cache_root(env, cache_flag, script, root)
    paid = paid_steps(script, timeline, rate)
    _require_key(db, paid, store, key)
    plan = plan_data(raw, root)
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
            spoken = _pieces(script, timeline, root, key, speaker, db, job_id, paid, store, stamp)
            digest = _produce(script, timeline, root, output, spoken.pieces)
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
    rate: Decimal,
    env: Mapping[str, str],
    speaker: Speaker,
    cache_flag: Path | None = None,
) -> JsonObject:
    row = read_job(db, job_id)
    if row is None:
        raise failure("E_INPUT_SCHEMA", "No job has that id.", "Use an id from the jobs command.")
    stored = json.loads(row.script_json)
    if raw is not None and canonical(raw) != row.script_json and digest_text(canonical(raw)) != row.script_sha256:
        raise failure("E_INPUT_SCHEMA", "The script does not match the job.", "Retry with the same script.")
    root = Path(row.script_dir)
    script, timeline = study(stored, root, require_files=True)
    key = env.get(speech.KEY_ENV, "")
    store = cache_root(env, cache_flag, script, root)
    paid = paid_steps(script, timeline, rate)
    _require_key(db, paid, store, key)
    plan = plan_data(stored, root)
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
            spoken = _pieces(script, timeline, root, key, speaker, db, job_id, paid, store, stamp)
            digest = _produce(script, timeline, root, output, spoken.pieces)
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
    return tuple(chosen)


def _grab(video: str, index: int, dest: Path, mode: EncodeMode) -> None:
    import subprocess

    picked = "select=eq(n\\," + str(index) + ")"
    result = subprocess.run(
        [
            "ffmpeg",
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
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not dest.is_file():
        raise NeedTool("ffmpeg")


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
        checks.append({"name": "duration", "ok": abs(duration - expected) <= 0.05})
        checks.append({"name": "resolution", "ok": width == row.width and height == row.height})
        checks.append({"name": "audio", "ok": audio})
        mode = script.output.encode
        checks.append({"name": "encoding", "ok": stored_as(str(output)) == STORED_AS[mode]})
        work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
        try:
            canvas = canvas_for(timeline, Path(row.script_dir))
            prepare(timeline, canvas, work, Path(row.script_dir))
            for name, index in _samples(timeline):
                painted = frame_at(timeline, index, canvas)
                shot = work / (name + ".png")
                _grab(str(output), index, shot, mode)
                checks.append({"name": name, "ok": matches(painted, shot, mode)})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    passed = all(item.get("ok") is True for item in checks)
    begin(db)
    move_job(db, job_id, "verified" if passed else "failed", format_instant(when))
    if passed:
        return {"jobId": job_id, "passed": True, "checks": as_json(checks)}
    commit(db)
    raise Failure(
        [ErrorEntry("E_VERIFY", "The rendered video failed a check.", "Read data.checks for the failed check.")],
        {"jobId": job_id, "passed": False, "checks": as_json(checks)},
    )


def plan_data(raw: object, root: Path) -> JsonObject:
    script, timeline = study(raw, root, require_files=False)
    hashes = _hashes(script, root)
    steps: list[JsonValue] = []
    keys: list[str] = []
    for span in timeline.spans:
        spec = step_spec(script, span.clip, hashes)
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


def estimate_data(raw: object, root: Path, rate: Decimal) -> JsonObject:
    script, timeline = study(raw, root, require_files=False)
    rows = quotes(script, timeline, rate)
    return {
        "usd": money_text(total_usd(rows)),
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "breakdown": as_json(
            [
                {"step": row.step, "index": row.step_index, "units": units_text(row.units), "usd": money_text(row.usd)}
                for row in rows
            ]
        ),
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


def default_speaker(text: str, voice: str, key: str) -> bytes:
    return speak(text, voice, key)


def tool_failure(error: NeedTool) -> Failure:
    return failure(
        "E_TOOL",
        say("The tool ", error.tool, " is not available."),
        "Install it and run the command again.",
    )


def speech_failure(_error: SpeechError) -> Failure:
    return failure(
        "E_SPEECH",
        "The speech service rejected the call.",
        "Check the voice name and the key.",
    )

