from __future__ import annotations

import json
import random
import sys
from collections.abc import Callable
from contextvars import ContextVar
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Protocol

import yaml
from ambitious_kit.cli.command import Context, DbContext, no_input, pure_command, read_command, write_command
from ambitious_kit.cli.runner import ModuleSpec, run_cli
from ambitious_kit.db.sqlite import load_migrations
from ambitious_kit.domain.envelope import JsonObject, failure

from ambitious_video.ai.speech import SpeechError
from ambitious_video.cli import engine
from ambitious_video.db.store import count_jobs
from ambitious_video.domain.need import NeedTool
from ambitious_video.install.tools import tool_report

VERSION = "0.1.0"
SCRIPT_DIR: ContextVar[Path] = ContextVar("script_dir", default=Path())


_json_loads = json.loads


def _loads(text: str) -> object:
    try:
        return _json_loads(text)
    except ValueError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ValueError(str(error)) from error


def _directory(argv: tuple[str, ...] | list[str]) -> Path:
    found: str | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--in" and index + 1 < len(argv):
            found = argv[index + 1]
        elif token.startswith("--in="):
            found = token[5:]
        index += 1
    if found is None:
        return Path.cwd()
    return Path(found).resolve().parent


def _rate() -> Decimal:
    raw = json.loads(files("ambitious_video").joinpath("pricing.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise failure("E_INTERNAL", "The price table is unreadable.", "Reinstall the video module.")
    value = raw.get("voiceoverPerCharacterUsd")
    if not isinstance(value, str):
        raise failure("E_INTERNAL", "The price table is unreadable.", "Reinstall the video module.")
    return Decimal(value)


def _job_id(rng: random.Random, count: int) -> str:
    return "j" + format(rng.randrange(16**8), "08x") + format(count, "08x")


def _script(raw: object) -> object:
    if not isinstance(raw, dict):
        raise failure("E_INPUT_SCHEMA", "The input must be an object.", "Send the video script as an object.")
    return raw


def _render_input(raw: object) -> tuple[str | None, object | None]:
    if not isinstance(raw, dict):
        raise failure("E_INPUT_SCHEMA", "The input must be an object.", "Send the video script as an object.")
    job = raw.get("jobId")
    if "jobId" in raw and not isinstance(job, str):
        raise failure("E_INPUT_SCHEMA", "jobId must be text.", "Send jobId as a string.")
    if set(raw) == {"jobId"} and isinstance(job, str):
        return job, None
    body = {key: value for key, value in raw.items() if key != "jobId"}
    return job if isinstance(job, str) else None, body


def _verify_input(raw: object) -> str:
    if not isinstance(raw, dict) or not isinstance(raw.get("jobId"), str) or raw.get("jobId") == "":
        raise failure("E_INPUT_SCHEMA", "jobId must be text.", 'Send { "jobId": "..." }.')
    job = raw["jobId"]
    if not isinstance(job, str):
        raise failure("E_INPUT_SCHEMA", "jobId must be text.", 'Send { "jobId": "..." }.')
    return job


def _guard(action: Callable[[], JsonObject]) -> JsonObject:
    try:
        return action()
    except NeedTool as error:
        raise engine.tool_failure(error) from None
    except SpeechError as error:
        raise engine.speech_failure(error) from None


def validate(context: Context[object]) -> JsonObject:
    return engine.validate_data(context.input, SCRIPT_DIR.get())


def plan(context: Context[object]) -> JsonObject:
    return engine.plan_data(context.input, SCRIPT_DIR.get())


def estimate(context: Context[object]) -> JsonObject:
    return engine.estimate_data(context.input, SCRIPT_DIR.get(), _rate())


def render(context: DbContext[tuple[str | None, object | None]]) -> JsonObject:
    job_id, body = context.input

    def run() -> JsonObject:
        if job_id is None:
            if body is None:
                raise failure("E_INPUT_SCHEMA", "The input must be a script.", "Send the video script.")
            return engine.render_new(
                context.db,
                body,
                SCRIPT_DIR.get(),
                _job_id(context.rng, count_jobs(context.db)),
                context.now,
                _rate(),
                context.env,
                engine.default_speaker,
            )
        return engine.render_again(context.db, job_id, body, context.now, _rate(), context.env, engine.default_speaker)

    return _guard(run)


def verify(context: DbContext[str]) -> JsonObject:
    return _guard(lambda: engine.verify_job(context.db, context.input, context.now))


def jobs(context: DbContext[None]) -> JsonObject:
    return engine.jobs_data(context.db)


def ledger(context: DbContext[None]) -> JsonObject:
    return engine.ledger_data(context.db)


class _Selfcheck(Protocol):
    def __call__(self, *, module: str, db_path: str, binary_path: str, migrations: object) -> JsonObject: ...


def _patched_selfcheck(original: _Selfcheck) -> _Selfcheck:
    def wrapped(*, module: str, db_path: str, binary_path: str, migrations: object) -> JsonObject:
        from ambitious_kit.domain.envelope import Failure

        try:
            data = original(module=module, db_path=db_path, binary_path=binary_path, migrations=migrations)
        except Failure as error:
            merged = dict(error.data)
            merged["tools"] = tool_report()
            raise Failure(error.errors, merged) from None
        merged = dict(data)
        merged["tools"] = tool_report()
        return merged

    return wrapped


def main() -> int:
    import ambitious_kit.cli.runner as runner

    runner.json.loads = _loads  # type: ignore[attr-defined] # YAML scripts use the JSON loader
    runner.selfcheck = _patched_selfcheck(runner.selfcheck)  # type: ignore[attr-defined,arg-type] # tools sit beside the kit report
    token = SCRIPT_DIR.set(_directory(sys.argv[1:]))
    try:
        return run_cli(
            ModuleSpec(
                module="video",
                version=VERSION,
                schema_version=1,
                migrations=load_migrations(files("ambitious_video").joinpath("db").joinpath("migrations")),
                commands={
                    "validate": pure_command(validate, _script),
                    "plan": pure_command(plan, _script),
                    "estimate": pure_command(estimate, _script),
                    "render": write_command(render, _render_input),
                    "verify": write_command(verify, _verify_input),
                    "jobs": read_command(jobs, no_input),
                    "ledger": read_command(ledger, no_input),
                },
            )
        )
    finally:
        SCRIPT_DIR.reset(token)


if __name__ == "__main__":
    raise SystemExit(main())

