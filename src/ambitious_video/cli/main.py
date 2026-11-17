from __future__ import annotations

import json
import os
import random
import select
import stat
import sys
from collections.abc import Callable
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path
from typing import Protocol

import yaml
from ambitious_kit.cli.command import Context, DbContext, no_input, pure_command, read_command, write_command
from ambitious_kit.cli.runner import ModuleSpec, run_cli
from ambitious_kit.db.sqlite import load_migrations
from ambitious_kit.domain.envelope import JsonObject, failure

from ambitious_video.cli import engine, guide
from ambitious_video.db.store import count_jobs
from ambitious_video.domain.need import NeedTool
from ambitious_video.install.tools import tool_report, tool_warnings
from ambitious_video.providers.base import ProviderError

VERSION = "0.1.0"
SCRIPT_DIR: ContextVar[Path] = ContextVar("script_dir", default=Path())
CACHE_DIR: ContextVar[Path | None] = ContextVar("cache_dir", default=None)
OLDER_THAN: ContextVar[str | None] = ContextVar("older_than", default=None)
KIT_VALUE_FLAGS = ("--in", "--db", "--now", "--seed")
MAX_PRUNE_DAYS = 36500
OWN_FLAGS = {
    "--cache": ("estimate", "render", "cache list", "cache prune"),
    "--older-than": ("cache prune",),
}
STDIN_WAIT_SECONDS = 2.0


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


def _groups(argv: list[str]) -> list[tuple[str, list[str], str]]:
    groups: list[tuple[str, list[str], str]] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        index += 1
        if not token.startswith("--"):
            groups.append(("", [token], token))
            continue
        name, equals, value = token.partition("=")
        tokens = [token]
        takes_value = name in KIT_VALUE_FLAGS or name in OWN_FLAGS
        if takes_value and equals == "" and index < len(argv) and not argv[index].startswith("--"):
            value = argv[index]
            tokens.append(value)
            index += 1
        groups.append((name, tokens, value))
    return groups


def _own_flags(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    groups = _groups(argv)
    command = " ".join(value for name, _tokens, value in groups if name == "")
    kept: list[str] = []
    taken: dict[str, str] = {}
    for name, tokens, value in groups:
        if name not in OWN_FLAGS:
            kept.extend(tokens)
        elif command in OWN_FLAGS[name] and value != "" and name not in taken:
            taken[name] = value
        else:
            kept.append(name)
    return kept, taken


def _input_source(argv: list[str]) -> tuple[list[str], str]:
    kept: list[str] = []
    source = "implicit"
    index = 0
    while index < len(argv):
        token = argv[index]
        value = argv[index + 1] if index + 1 < len(argv) else None
        index += 1
        if token == "--in=-" or (token == "--in" and value == "-"):
            source = "stdin"
            index += 1 if token == "--in" else 0
            continue
        if token.startswith("--in=") or (token == "--in" and value is not None):
            source = "file"
        kept.append(token)
    return kept, source


def _waiting_bytes(descriptor: int) -> bytes:
    if stat.S_ISREG(os.fstat(descriptor).st_mode):
        return sys.stdin.buffer.read()
    if not select.select([descriptor], [], [], STDIN_WAIT_SECONDS)[0]:
        return b""
    held = bytearray()
    while chunk := os.read(descriptor, 65536):
        held += chunk
    return bytes(held)


def _stdin_reader(source: str) -> Callable[[], str]:
    def read() -> str:
        if source == "file" or sys.stdin is None or sys.stdin.closed:
            return ""
        if source == "stdin":
            return sys.stdin.buffer.read().decode("utf-8", errors="replace")
        if sys.stdin.isatty():
            return ""
        try:
            return _waiting_bytes(sys.stdin.fileno()).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    return read


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
    except ProviderError as error:
        raise engine.provider_failure(error) from None


def validate(context: Context[object]) -> JsonObject:
    return _guard(lambda: engine.validate_data(context.input, SCRIPT_DIR.get()))


def plan(context: Context[object]) -> JsonObject:
    return engine.plan_data(context.input, SCRIPT_DIR.get())


def estimate(context: Context[object]) -> JsonObject:
    return engine.estimate_data(context.input, SCRIPT_DIR.get(), context.env, CACHE_DIR.get())


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
                context.env,
                CACHE_DIR.get(),
            )
        return engine.render_again(context.db, job_id, body, context.now, context.env, CACHE_DIR.get())

    return _guard(run)


def verify(context: DbContext[str]) -> JsonObject:
    return _guard(lambda: engine.verify_job(context.db, context.input, context.now))


def jobs(context: DbContext[None]) -> JsonObject:
    return engine.jobs_data(context.db)


def ledger(context: DbContext[None]) -> JsonObject:
    return engine.ledger_data(context.db)


def cache_list(context: DbContext[None]) -> JsonObject:
    return engine.cache_list(context.db, CACHE_DIR.get())


def cache_prune(context: DbContext[None]) -> JsonObject:
    asked = OLDER_THAN.get()
    valid = asked is not None and asked.isascii() and asked.isdigit() and len(asked) <= 6
    if asked is None or not valid or int(asked) > MAX_PRUNE_DAYS:
        raise failure(
            "E_USAGE",
            "cache prune needs --older-than <days>.",
            "Give whole days, 0 to 36500, such as 90.",
        )
    return engine.cache_prune(context.db, CACHE_DIR.get(), int(asked), context.now)


def show_guide(_context: Context[None]) -> JsonObject:
    return guide.guide_data()


def show_help(_context: Context[None]) -> JsonObject:
    return guide.help_data(VERSION)


def show_version(_context: Context[None]) -> JsonObject:
    return guide.version_data(VERSION)


def _with_tools(data: JsonObject) -> JsonObject:
    merged = dict(data)
    report = tool_report()
    merged["tools"] = report
    merged["warnings"] = tool_warnings(report)
    return merged


class _Selfcheck(Protocol):
    def __call__(self, *, module: str, db_path: str, binary_path: str, migrations: object) -> JsonObject: ...


def _patched_selfcheck(original: _Selfcheck) -> _Selfcheck:
    def wrapped(*, module: str, db_path: str, binary_path: str, migrations: object) -> JsonObject:
        from ambitious_kit.domain.envelope import Failure

        try:
            data = original(module=module, db_path=db_path, binary_path=binary_path, migrations=migrations)
        except Failure as error:
            raise Failure(error.errors, _with_tools(error.data)) from None
        return _with_tools(data)

    return wrapped


def main() -> int:
    import ambitious_kit.cli.runner as runner

    runner.json.loads = _loads  # type: ignore[attr-defined] # YAML scripts use the JSON loader
    runner.selfcheck = _patched_selfcheck(runner.selfcheck)  # type: ignore[attr-defined,arg-type] # tools sit beside the kit report
    original = sys.argv
    kept, own = _own_flags(guide.builtin_command(sys.argv[1:]))
    kept, source = _input_source(kept)
    runner._read_stdin = _stdin_reader(source)
    token = SCRIPT_DIR.set(_directory(kept))
    chosen = own.get("--cache")
    cache_token = CACHE_DIR.set(None if chosen is None else Path(chosen))
    days_token = OLDER_THAN.set(own.get("--older-than"))
    sys.argv = [original[0], *kept]
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
                    "cache list": read_command(cache_list, no_input),
                    "cache prune": write_command(cache_prune, no_input),
                    "guide": pure_command(show_guide, no_input),
                    "help": pure_command(show_help, no_input),
                    "version": pure_command(show_version, no_input),
                },
            )
        )
    finally:
        sys.argv = original
        OLDER_THAN.reset(days_token)
        CACHE_DIR.reset(cache_token)
        SCRIPT_DIR.reset(token)


if __name__ == "__main__":
    raise SystemExit(main())

