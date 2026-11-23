from __future__ import annotations

import json
import os
import random
import secrets
import sqlite3
import sys
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ambitious_kit.cli.command import Command, Context, Runtime, no_input, pure_command
from ambitious_kit.cli.selfcheck import selfcheck
from ambitious_kit.db.sqlite import migrate, open_db, require_current_schema
from ambitious_kit.domain.args import FLAG_USAGE, ParsedArgs, parse_args
from ambitious_kit.domain.clock import format_instant, parse_now, parse_seed
from ambitious_kit.domain.envelope import (
    MAX_SEED,
    Envelope,
    ErrorEntry,
    Failure,
    Identity,
    JsonObject,
    Meta,
    err,
    failure,
    is_command_name,
    is_module_name,
    is_semver,
    ok,
)
from ambitious_kit.domain.input_shape import listed_shape, shape_problems
from ambitious_kit.domain.migrations import Migration
from ambitious_kit.paths import ambitious_home, refuse_agent_data
from ambitious_kit.runtime import pinned_runtime

BUILT_IN = ("selfcheck", "commands")


@dataclass(frozen=True)
class ModuleSpec:
    module: str
    version: str
    schema_version: int
    migrations: Sequence[Migration]
    commands: Mapping[str, Command]
    runtime_lock: str | None = None


@dataclass(frozen=True)
class Io:
    argv: Sequence[str]
    env: Mapping[str, str]
    binary_path: str
    read_stdin: Callable[[], str]
    system_now: Callable[[], datetime]
    system_seed: Callable[[], int]
    write_stderr: Callable[[str], object]


def _usage(message: str, hint: str) -> ErrorEntry:
    return ErrorEntry("E_USAGE", message, hint)


def _checked_spec(spec: ModuleSpec) -> None:
    wrong: list[str] = []
    if not is_module_name(spec.module):
        wrong.append(f'module "{spec.module}" must be the lower-case name after ambitious-')
    if not is_semver(spec.version):
        wrong.append(f'version "{spec.version}" must be a semantic version')
    if spec.schema_version < 1:
        wrong.append("schema_version must be an integer, 1 or higher")
    wrong.extend(
        f'command "{name}" must be lower-case words joined by one space'
        for name in spec.commands
        if not is_command_name(name)
    )
    wrong.extend(f'command "{name}" is provided by the kit' for name in spec.commands if name in BUILT_IN)
    for name, declared in spec.commands.items():
        if declared.shape is not None:
            wrong.extend(shape_problems(name, declared.takes_input, declared.shape))
    if wrong:
        raise ValueError(f"The module spec is not valid: {'; '.join(wrong)}.")


def _read_input(command: str, takes_input: bool, args: ParsedArgs, io: Io) -> object:
    if not takes_input:
        if args.input_file is not None:
            raise Failure([_usage(f"The command {command} takes no input, but --in was given.", "Remove --in.")])
        return None
    piped = io.read_stdin()
    if args.input_file is not None and piped.strip() != "":
        raise Failure(
            [
                _usage(
                    "Input was given both on stdin and with --in.",
                    "Give the input JSON one way: on stdin or as a file named by --in.",
                )
            ]
        )
    text = piped
    if args.input_file is not None:
        try:
            text = Path(args.input_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise failure(
                "E_INPUT_SCHEMA",
                f"The input file {args.input_file} could not be read.",
                "Pass --in the path of a readable JSON file.",
            ) from error
    if text.strip() == "":
        raise failure(
            "E_INPUT_SCHEMA",
            f"The command {command} needs input and got none.",
            "Send a JSON object on stdin or with --in.",
        )
    try:
        parsed: object = json.loads(text)
    except ValueError as error:
        raise failure(
            "E_INPUT_SCHEMA", "The input is not valid JSON.", "Send one JSON object on stdin or with --in."
        ) from error
    return parsed


def _errors_of(error: Exception, io: Io) -> tuple[Sequence[ErrorEntry], JsonObject]:
    if isinstance(error, Failure):
        return error.errors, error.data
    io.write_stderr("".join(traceback.format_exception(error)))
    if isinstance(error, sqlite3.Error):
        return [
            ErrorEntry(
                "E_DB",
                f"The database could not be read or written: {error}.",
                "Run selfcheck and read data.problems.",
            )
        ], {}
    return [ErrorEntry("E_INTERNAL", "The module hit an unexpected error.", "Report it with the stderr log.")], {}


def cli_envelope(spec: ModuleSpec, io: Io) -> Envelope:
    _checked_spec(spec)
    args = parse_args(io.argv)
    now = io.system_now() if args.now is None else parse_now(args.now)
    seed = io.system_seed() if args.seed is None else parse_seed(args.seed)
    instant = now if now is not None else io.system_now()
    used_seed = seed if seed is not None else io.system_seed()
    meta = Meta(format_instant(instant), used_seed, spec.version)

    def identity(command: str) -> Identity:
        return Identity(spec.schema_version, spec.module, command, meta)

    commands: dict[str, Command] = dict(spec.commands)

    def describe_commands(only: str | None) -> Callable[[Context[None]], JsonObject]:
        return lambda _context: {
            "commands": [
                {
                    "name": name,
                    "access": listed.access,
                    "input": listed.takes_input,
                    **({} if listed.shape is None else listed_shape(listed.shape)),
                }
                for name, listed in sorted(commands.items())
                if only is None or name == only
            ]
        }

    commands["selfcheck"] = Command("write", False, lambda _raw: lambda _runtime: {})
    commands["commands"] = pure_command(describe_commands(None), no_input)
    asked = " ".join(args.words)
    name = "commands" if args.help and len(args.words) == 0 else asked
    command = commands.get(name)
    if args.help and command is not None and len(args.words) > 0:
        command = pure_command(describe_commands(name), no_input)
    valid = ", ".join(sorted(commands))
    usage_errors: list[ErrorEntry] = []
    if len(args.words) == 0 and not args.help:
        usage_errors.append(_usage("No command was given.", f"Use one of: {valid}."))
    elif command is None:
        usage_errors.append(_usage(f'Unknown command "{name}".', f"Use one of: {valid}."))
    usage_errors.extend(_usage(problem, f"Use only these flags: {FLAG_USAGE}.") for problem in args.problems)
    if now is None:
        usage_errors.append(
            _usage(
                f'The --now value "{args.now}" is not a time with a time zone.',
                "Use ISO 8601 with Z or an offset, for example 2026-01-01T00:00:00Z.",
            )
        )
    if seed is None:
        usage_errors.append(
            _usage(
                f'The --seed value "{args.seed}" is not an integer from 0 to {MAX_SEED}.',
                f"Use a whole number from 0 to {MAX_SEED}.",
            )
        )
    if command is None or usage_errors:
        return err(identity("" if command is None else asked), usage_errors)

    connections: list[sqlite3.Connection] = []
    try:
        home = ambitious_home(io.env)
        db_path = refuse_agent_data(args.db or os.path.join(home, spec.module, f"{spec.module}.sqlite"))
        if args.input_file is not None:
            refuse_agent_data(args.input_file)
        run = command.parse(None if args.help else _read_input(name, command.takes_input, args, io))
        if name == "selfcheck" and not args.help:
            return ok(
                identity(name),
                selfcheck(
                    module=spec.module,
                    db_path=db_path,
                    binary_path=io.binary_path,
                    migrations=spec.migrations,
                    runtime=None
                    if spec.runtime_lock is None
                    else pinned_runtime(io.env, spec.module, spec.runtime_lock),
                ),
            )
        readonly = command.access == "read"

        def open_for_command() -> sqlite3.Connection:
            if connections:
                return connections[0]
            db = open_db(db_path, readonly=readonly)
            connections.append(db)
            if readonly:
                require_current_schema(db, spec.migrations)
            else:
                migrate(db, spec.migrations)
            return db

        runtime = Runtime(instant, random.Random(used_seed), io.env, open_for_command)
        if command.access != "write":
            return ok(identity(name), run(runtime))
        db = open_for_command()
        db.execute("BEGIN IMMEDIATE")
        try:
            data = run(runtime)
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        return ok(identity(name), data)
    except Exception as error:
        errors, data = _errors_of(error, io)
        return err(identity(name), errors, data)
    finally:
        for connection in connections:
            connection.close()


def _read_stdin() -> str:
    return "" if sys.stdin.isatty() else sys.stdin.buffer.read().decode("utf-8", errors="replace")


def run_cli(spec: ModuleSpec) -> int:
    io = Io(
        argv=sys.argv[1:],
        env=os.environ,
        binary_path=sys.argv[0],
        read_stdin=_read_stdin,
        system_now=lambda: datetime.now(UTC),
        system_seed=lambda: secrets.randbelow(MAX_SEED + 1),
        write_stderr=sys.stderr.write,
    )
    try:
        result = cli_envelope(spec, io)
    except Exception as error:
        sys.stderr.write("".join(traceback.format_exception(error)))
        result = err(
            Identity(
                1,
                spec.module if is_module_name(spec.module) else "unknown",
                "",
                Meta(format_instant(datetime.now(UTC)), 0, spec.version if is_semver(spec.version) else "0.0.0"),
            ),
            [ErrorEntry("E_INTERNAL", "The module hit an unexpected error.", "Report it with the stderr log.")],
        )
    sys.stdout.write(result.serialize())
    sys.stdout.flush()
    return result.exit_code
