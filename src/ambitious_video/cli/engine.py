from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ambitious_kit.domain.clock import format_instant
from ambitious_kit.domain.envelope import ErrorEntry, Failure, JsonObject, JsonValue, failure
from ambitious_kit.paths import refuse_agent_data

from ambitious_video.ai.speech import SpeechError, speak
from ambitious_video.db.lock import Hold
from ambitious_video.db.store import (
    JobRow,
    insert_asset,
    insert_job,
    insert_ledger,
    list_ledger,
    mark_rendered,
    move_job,
    read_job,
)
from ambitious_video.db.tx import begin, commit
from ambitious_video.domain.load import load_script, say
from ambitious_video.domain.money import money_text, quotes, total_usd, units_text
from ambitious_video.domain.need import NeedTool
from ambitious_video.domain.timeline import Fault, Timeline, asset_paths, graph_of, seconds_json, timeline_for
from ambitious_video.domain.types import (
    AudioClip,
    ClipTrack,
    JobId,
    Script,
    ScriptProblem,
    Sha256Hex,
    UnknownStep,
    Voiceover,
    idem_key,
)
from ambitious_video.media.audio import Piece, write_wav
from ambitious_video.media.film import canvas_for, encode, frame_at, prepare
from ambitious_video.media.probe import duration_of, frame_count, inspect
from ambitious_video.steps import compile_clip

Speaker = Callable[[str, str, str], bytes]


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


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def study(raw: object, root: Path, *, require_files: bool) -> tuple[Script, Timeline]:
    script, problems = load_script(raw)
    faults = _faults_of(problems)
    if script is None:
        raise_faults(faults, 0)
        raise failure("E_INPUT_SCHEMA", "The script is not valid.", "Fix the script field in data.problems.")
    media = media_map(script, root) if require_files else None
    timeline = timeline_for(script, media, require_files=require_files)
    if timeline.faults:
        raise_faults(timeline.faults, seconds_json(timeline.total, timeline.fps))
    return script, timeline


def _db_path(db: sqlite3.Connection) -> Path:
    row = db.execute("PRAGMA database_list").fetchone()
    if not isinstance(row, tuple) or not isinstance(row[2], str) or row[2] == "":
        raise failure("E_DB", "The database path is missing.", "Run selfcheck and read data.problems.")
    return Path(row[2])


def _needs_key(script: Script) -> bool:
    if script.voice is None:
        return False
    return any(
        isinstance(clip, Voiceover)
        for track in script.tracks
        if isinstance(track, ClipTrack)
        for clip in track.clips
    )


def _pieces(
    script: Script,
    timeline: Timeline,
    root: Path,
    key: str,
    speaker: Speaker,
    db: sqlite3.Connection,
    job_id: str,
    rate: Decimal,
) -> tuple[Piece, ...]:
    pieces: list[Piece] = []
    ranges: list[tuple[int, int]] = []
    priced = {item.step_index: item for item in quotes(script, timeline, rate)}
    for span in timeline.spans:
        if not isinstance(span.clip, Voiceover) or script.voice is None:
            continue
        quote = priced[span.step]
        if not quote.paid:
            continue
        body = canonical({"step": span.step, "text": span.clip.text})
        token = idem_key(JobId(job_id), span.step, Sha256Hex(digest_text(body)))
        cache = _db_path(db).parent / "speech" / (str(token) + ".bin")
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.is_file():
            cache.write_bytes(speaker(span.clip.text, script.voice.voice, key))
        begin(db)
        insert_ledger(
            db,
            job_id=job_id,
            step=span.kind,
            step_index=span.step,
            units=units_text(quote.units),
            usd=money_text(quote.usd),
            idem_key=str(token),
        )
        commit(db)
        pieces.append(Piece(cache, span.start, 0.0, ()))
        ranges.append((span.start, span.end))
    for span in timeline.spans:
        if isinstance(span.clip, AudioClip):
            ducks = tuple(ranges) if span.clip.duck_under is not None else ()
            gain = 0.0 if span.clip.gain_db is None else span.clip.gain_db
            pieces.append(Piece(root / str(span.clip.path), span.start, gain, ducks))
    return tuple(pieces)


def _produce(script: Script, timeline: Timeline, root: Path, dest: Path, pieces: tuple[Piece, ...]) -> str:
    work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
    partial = work / "video.mp4"
    try:
        wav = work / "audio.wav"
        write_wav(wav, timeline.total, timeline.fps, pieces)
        canvas = canvas_for(timeline, root)
        prepare(timeline, canvas, work, root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        encode(timeline, canvas, wav, partial)
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
) -> JsonObject:
    script, timeline = study(raw, root, require_files=True)
    key = env.get("XAI_API_KEY", "")
    if _needs_key(script) and key == "":
        raise failure(
            "E_NO_KEY",
            say("This command needs an AI call ", "and no key was set."),
            say("Set XAI_API_KEY in the environment", " for this call."),
        )
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
            pieces = _pieces(script, timeline, root, key, speaker, db, job_id, rate)
            digest = _produce(script, timeline, root, output, pieces)
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
    cost = total_usd(quotes(script, timeline, rate))
    return {
        "jobId": job_id,
        "output": str(output),
        "sha256": file_sha(output),
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "costUsd": money_text(cost),
    }


def render_again(
    db: sqlite3.Connection,
    job_id: str,
    raw: object | None,
    when: datetime,
    rate: Decimal,
    env: Mapping[str, str],
    speaker: Speaker,
) -> JsonObject:
    row = read_job(db, job_id)
    if row is None:
        raise failure("E_INPUT_SCHEMA", "No job has that id.", "Use an id from the jobs command.")
    stored = json.loads(row.script_json)
    if raw is not None and canonical(raw) != row.script_json and digest_text(canonical(raw)) != row.script_sha256:
        raise failure("E_INPUT_SCHEMA", "The script does not match the job.", "Retry with the same script.")
    root = Path(row.script_dir)
    script, timeline = study(stored, root, require_files=True)
    key = env.get("XAI_API_KEY", "")
    if _needs_key(script) and key == "":
        raise failure(
            "E_NO_KEY",
            say("This command needs an AI call ", "and no key was set."),
            say("Set XAI_API_KEY in the environment", " for this call."),
        )
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
            pieces = _pieces(script, timeline, root, key, speaker, db, job_id, rate)
            digest = _produce(script, timeline, root, output, pieces)
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
    return {
        "jobId": job_id,
        "output": row.output_path,
        "sha256": file_sha(Path(row.output_path)),
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "costUsd": money_text(total_usd(quotes(script, timeline, rate))),
    }


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


def _grab(video: str, index: int, dest: Path) -> None:
    import subprocess

    if shutil.which("ffmpeg") is None:
        raise NeedTool("ffmpeg")
    subprocess.run(
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
            "select=eq(n\\," + str(index) + ")",
            "-vframes",
            "1",
            str(dest),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


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
    if name == "frames":
        return say("The video has ", str(actual), " frames, not ", str(expected), ".")
    if name == "resolution":
        return say("The video is ", str(actual), ", not ", str(expected), ".")
    if name == "audio":
        return "The video has no audio track."
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
        checks.append({"name": "readable", "ok": duration >= 0 and width > 0})
        checks.append(
            {"name": "duration", "ok": abs(duration - expected) <= 0.05, "expected": expected, "actual": duration}
        )
        total = timeline.total
        checks.append({"name": "frames", "ok": counted == total, "expected": total, "actual": counted})
        checks.append({"name": "resolution", "ok": size == wanted, "expected": wanted, "actual": size})
        checks.append({"name": "audio", "ok": audio})
        work = Path(tempfile.mkdtemp(prefix="ambitious-video-"))
        try:
            canvas = canvas_for(timeline, Path(row.script_dir))
            prepare(timeline, canvas, work, Path(row.script_dir))
            for name, index in _samples(timeline):
                painted = frame_at(timeline, index, canvas).tobytes()
                shot = work / (name + ".png")
                _grab(str(output), index, shot)
                from ambitious_video.media.film import pixels

                found = shot.is_file()
                same = found and pixels(shot) == painted
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


def plan_data(raw: object, root: Path) -> JsonObject:
    _script, timeline = study(raw, root, require_files=False)
    steps = []
    for span in timeline.spans:
        step = compile_clip(span.clip)
        step["index"] = span.step
        step["atFrame"] = span.start
        step["frames"] = span.frames
        step["track"] = span.track
        steps.append(step)
    return {
        "durationSec": seconds_json(timeline.total, timeline.fps),
        "steps": as_json(steps),
        "graph": graph_of(timeline),
        "assets": as_json([{"path": path, "kind": kind} for path, kind in asset_paths(_script)]),
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

