from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ambitious_kit.domain.envelope import INTERNAL_ERROR_CODES, envelope_problems
from ambitious_kit.paths import AGENT_DATA_ROOT

NOW = "2026-01-01T00:00:00Z"
NOW_ECHO = "2026-01-01T00:00:00.000Z"
FIXED = ("--now", NOW, "--seed", "7")
RUN_TIMEOUT_SECONDS = 60
GARBAGE = "this file is not a database\n" * 64

USAGE = """Usage: python -m ambitious_kit.contract <path to the built module binary>

Runs the module contract tests against a built binary (.pyz, .mjs or any
executable) and prints one row per check. Each check uses its own fresh,
temporary AMBITIOUS_HOME. Exit code 0: every check passed. 1: a check failed.
2: the tests could not run.
"""


@dataclass(frozen=True)
class ContractResult:
    id: str
    title: str
    problems: tuple[str, ...]


@dataclass(frozen=True)
class Outcome:
    label: str
    status: int | None
    stdout: str
    envelope: Mapping[str, object] | None
    problems: tuple[str, ...]


@dataclass(frozen=True)
class Listed:
    name: str
    access: str
    input: bool


def _launcher(binary: str) -> list[str]:
    if binary.endswith(".pyz"):
        return [sys.executable, binary]
    if binary.endswith((".mjs", ".js")):
        return ["node", binary]
    return [binary]


def _error_codes(envelope: Mapping[str, object]) -> list[str]:
    errors = envelope.get("errors")
    if not isinstance(errors, list):
        return []
    return [entry["code"] for entry in errors if isinstance(entry, dict) and isinstance(entry.get("code"), str)]


def _expected_exit(envelope: Mapping[str, object]) -> int:
    if envelope.get("ok") is True:
        return 0
    return 2 if any(code in INTERNAL_ERROR_CODES for code in _error_codes(envelope)) else 1


def _parse_stdout(stdout: str) -> tuple[Mapping[str, object] | None, list[str]]:
    if not stdout.endswith("\n") or "\n" in stdout[:-1]:
        return None, [f"stdout must be exactly one line ended by one newline, but was {json.dumps(stdout[:200])}."]
    try:
        value: object = json.loads(stdout)
    except ValueError:
        return None, [f"stdout must be one JSON envelope, but was {json.dumps(stdout[:200])}."]
    return (value if isinstance(value, dict) else None), envelope_problems(value)


def _field(value: object, *path: str) -> object:
    node = value
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _files_under(directory: str) -> list[str]:
    root = Path(directory)
    return sorted(str(path.relative_to(root)) for path in root.rglob("*")) if root.exists() else []


@dataclass
class Bench:
    binary: str
    homes: list[str] = field(default_factory=list)

    def fresh_home(self) -> str:
        home = tempfile.mkdtemp(prefix="ambitious-contract-")
        self.homes.append(home)
        return home

    def run(self, args: Sequence[str], home: str, *, env: Mapping[str, str] | None = None, stdin: str = "") -> Outcome:
        label = f"`{' '.join(args)}`"
        try:
            result = subprocess.run(
                [*_launcher(self.binary), *args],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=RUN_TIMEOUT_SECONDS,
                env={**os.environ, "AMBITIOUS_HOME": home, **(env or {})},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return Outcome(label, None, "", None, (f"{label} could not be run: {error}.",))
        envelope, problems = _parse_stdout(result.stdout)
        if envelope is not None and result.returncode != _expected_exit(envelope):
            problems.append(
                f"exited {result.returncode}, but its envelope calls for exit {_expected_exit(envelope)} "
                "(0 for ok, 2 for E_DB, E_MIGRATION or E_INTERNAL, otherwise 1)."
            )
        return Outcome(
            label, result.returncode, result.stdout, envelope, tuple(f"{label}: {problem}" for problem in problems)
        )

    def commands(self) -> list[Listed]:
        listed = _field(self.run(["commands", *FIXED], self.fresh_home()).envelope, "data", "commands")
        if not isinstance(listed, list):
            return []
        return [
            Listed(entry["name"], entry["access"], entry["input"])
            for entry in listed
            if isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and isinstance(entry.get("access"), str)
            and isinstance(entry.get("input"), bool)
        ]


def _must_fail(outcome: Outcome, code: str, exit_code: int) -> list[str]:
    if outcome.envelope is None:
        return list(outcome.problems)
    codes = _error_codes(outcome.envelope)
    problems = list(outcome.problems)
    if not (outcome.envelope.get("ok") is False and code in codes):
        answered = "ok" if outcome.envelope.get("ok") is True else ", ".join(codes)
        problems.append(f"{outcome.label}: must fail with {code}, but answered {answered}.")
    if outcome.status != exit_code:
        problems.append(f"{outcome.label}: must exit {exit_code}, but exited {outcome.status}.")
    return problems


def _must_succeed(outcome: Outcome) -> list[str]:
    if outcome.envelope is None or outcome.envelope.get("ok") is True:
        return list(outcome.problems)
    return [
        *outcome.problems,
        f"{outcome.label}: must succeed, but failed with {', '.join(_error_codes(outcome.envelope))}.",
    ]


def _is_whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _envelope_shape(bench: Bench) -> list[str]:
    home = bench.fresh_home()
    selfcheck = bench.run(["selfcheck", *FIXED], home)
    data = _field(selfcheck.envelope, "data")
    expectations = [
        ('command must be "selfcheck"', _field(selfcheck.envelope, "command") == "selfcheck"),
        (f"meta.now must echo --now as {NOW_ECHO}", _field(selfcheck.envelope, "meta", "now") == NOW_ECHO),
        ("meta.seed must echo --seed 7", _field(selfcheck.envelope, "meta", "seed") == 7),
        (
            "data.runtime.name and data.runtime.version must be strings",
            isinstance(_field(data, "runtime", "name"), str) and isinstance(_field(data, "runtime", "version"), str),
        ),
        (
            "data.binary.sha256 must be the SHA-256 of the binary that ran",
            _field(data, "binary", "sha256") == _sha256(bench.binary),
        ),
        ("data.binary.path must be a string", isinstance(_field(data, "binary", "path"), str)),
        ("data.db.path must be under AMBITIOUS_HOME", str(_field(data, "db", "path")).startswith(home)),
        ('data.db.integrityCheck must be "ok" on a fresh database', _field(data, "db", "integrityCheck") == "ok"),
        ("data.db.userVersion must be a whole number", _is_whole_number(_field(data, "db", "userVersion"))),
        ("data.db.migrationsApplied must be a list", isinstance(_field(data, "db", "migrationsApplied"), list)),
        ("data.problems must be an empty list when ok is true", _field(data, "problems") == []),
    ]
    listing = bench.run(["commands", *FIXED], home)
    listed = _field(listing.envelope, "data", "commands")
    names = [_field(entry, "name") for entry in listed] if isinstance(listed, list) else []
    problems = _must_succeed(selfcheck)
    if selfcheck.envelope is not None:
        problems.extend(f"{selfcheck.label}: {rule}." for rule, holds in expectations if not holds)
    problems.extend(_must_succeed(listing))
    if not ("selfcheck" in names and "commands" in names):
        problems.append(
            f"{listing.label}: data.commands must list every command, including selfcheck and commands, "
            "each as { name, access, input }."
        )
    return problems


def _garbage_db(bench: Bench) -> str:
    garbage = os.path.join(bench.fresh_home(), "garbage.sqlite")
    Path(garbage).write_text(GARBAGE, encoding="utf-8")
    return garbage


def _exit_codes(bench: Bench) -> list[str]:
    home = bench.fresh_home()
    garbage = _garbage_db(bench)
    usage: list[list[str]] = [
        [],
        ["no-such-command"],
        ["selfcheck", "--no-such-flag"],
        ["selfcheck", "--now", "2026-01-01T00:00:00"],
        ["selfcheck", "--now", "yesterday"],
        ["selfcheck", "--now", "2026-02-30T00:00:00Z"],
        ["selfcheck", "--seed", "-1"],
        ["selfcheck", "--seed", "4294967296"],
        ["selfcheck", "--seed", "1.5"],
        ["selfcheck", "--db"],
    ]
    problems = [problem for args in usage for problem in _must_fail(bench.run(args, home), "E_USAGE", 1)]
    unknown = bench.run(["no-such-command", *FIXED], home)
    if unknown.envelope is not None and unknown.envelope.get("command") != "":
        problems.append(f'{unknown.label}: command must be "" when no known command was given.')
    problems.extend(_must_succeed(bench.run(["selfcheck", *FIXED], home)))
    broken = bench.run(["selfcheck", "--db", garbage, *FIXED], home)
    problems.extend(_must_fail(broken, "E_DB", 2))
    listed_problems = _field(broken.envelope, "data", "problems")
    if broken.envelope is not None and not (isinstance(listed_problems, list) and len(listed_problems) > 0):
        problems.append(f"{broken.label}: selfcheck must say what is wrong in data.problems.")
    return problems


def _now_determinism(bench: Bench) -> list[str]:
    home = bench.fresh_home()
    offset = ["--now", "2026-03-05T09:30:00+01:00", "--seed", "42"]
    first = bench.run(["selfcheck", *offset], home)
    second = bench.run(["selfcheck", *offset], home)
    third = bench.run(["selfcheck", *offset, "--json"], home)
    problems = _must_succeed(first)
    if _field(first.envelope, "meta", "now") != "2026-03-05T08:30:00.000Z":
        problems.append(f"{first.label}: meta.now must be the --now instant in UTC, 2026-03-05T08:30:00.000Z.")
    if _field(first.envelope, "meta", "seed") != 42:
        problems.append(f"{first.label}: meta.seed must echo --seed 42.")
    if second.stdout != third.stdout:
        problems.append(
            f"{third.label}: --json must change nothing, and two runs with the same --now and --seed "
            "must print the same bytes."
        )
    for listed in bench.commands():
        if listed.access == "write" or listed.input:
            continue
        args = [*listed.name.split(" "), *offset]
        once, again = bench.run(args, home), bench.run(args, home)
        problems.extend(once.problems)
        if once.stdout != again.stdout:
            problems.append(f"{once.label}: two runs with the same --now and --seed must print the same bytes.")
    return problems


def _agent_data_refusal(bench: Bench) -> list[str]:
    home = bench.fresh_home()
    linked = bench.fresh_home()
    os.symlink(AGENT_DATA_ROOT, os.path.join(linked, "link"))
    inside = f"{AGENT_DATA_ROOT}/module.sqlite"
    problems: list[str] = []
    for listed in bench.commands():
        outcome = bench.run([*listed.name.split(" "), "--db", inside, *FIXED], home, stdin="{}" if listed.input else "")
        problems.extend(_must_fail(outcome, "E_FORBIDDEN_PATH", 1))
    variants: list[tuple[list[str], dict[str, str]]] = [
        (["selfcheck", "--db", f"{AGENT_DATA_ROOT}/../agent-data/module.sqlite", *FIXED], {}),
        (["selfcheck", "--db", os.path.join(linked, "link", "module.sqlite"), *FIXED], {}),
        (["selfcheck", "--in", f"{AGENT_DATA_ROOT}/input.json", *FIXED], {}),
        (["selfcheck", *FIXED], {"AMBITIOUS_HOME": AGENT_DATA_ROOT}),
        (["selfcheck", *FIXED], {"AMBITIOUS_HOME": os.path.join(linked, "link", "home")}),
    ]
    for args, env in variants:
        problems.extend(_must_fail(bench.run(args, home, env=env), "E_FORBIDDEN_PATH", 1))
    created = _files_under(home)
    if created:
        problems.append(
            f"A refused command must not create anything, but AMBITIOUS_HOME now holds {', '.join(created)}."
        )
    return problems


def _read_only_analysis(bench: Bench) -> list[str]:
    problems: list[str] = []
    for listed in bench.commands():
        if listed.access != "read":
            continue
        args = [*listed.name.split(" "), *FIXED]
        stdin = "{}" if listed.input else ""
        empty = bench.fresh_home()
        missing = bench.run(args, empty, stdin=stdin)
        created_when_missing = _files_under(empty)
        home = bench.fresh_home()
        prepared = bench.run(["selfcheck", *FIXED], home)
        db_path = _field(prepared.envelope, "data", "db", "path")
        if not isinstance(db_path, str) or not os.path.exists(db_path):
            problems.extend(prepared.problems)
            problems.append(
                f"{prepared.label}: selfcheck must create the database and report its path in data.db.path."
            )
            continue
        before = _sha256(db_path)
        analysis = bench.run(args, home, stdin=stdin)
        problems.extend(_must_fail(missing, "E_MIGRATION", 2))
        if created_when_missing:
            problems.append(
                f"{missing.label}: a read command must not create the database, "
                f"but AMBITIOUS_HOME now holds {', '.join(created_when_missing)}."
            )
        problems.extend(analysis.problems)
        if _sha256(db_path) != before:
            problems.append(
                f"{analysis.label}: a read command must open the database with mode=ro, but the database file changed."
            )
    return problems


def _no_stdout_noise(bench: Bench) -> list[str]:
    home = bench.fresh_home()
    garbage = _garbage_db(bench)
    invocations: list[tuple[list[str], str]] = [
        (["selfcheck"], ""),
        ([], ""),
        (["--help"], ""),
        (["selfcheck", "--db", garbage], ""),
    ]
    for listed in bench.commands():
        words = listed.name.split(" ")
        invocations.append((words, "{}" if listed.input else ""))
        if listed.input:
            invocations.extend([(words, "this is not JSON"), (words, "")])
    return [problem for args, stdin in invocations for problem in bench.run(args, home, stdin=stdin).problems]


CHECKS: tuple[tuple[str, str, Callable[[Bench], list[str]]], ...] = (
    ("envelope-shape", "Every command prints one envelope that matches the schema", _envelope_shape),
    ("exit-codes", "Exit code is 0 for ok, 1 for a caller error, 2 for an internal error", _exit_codes),
    ("now-determinism", "--now and --seed are echoed, and the same run prints the same bytes", _now_determinism),
    ("agent-data-refusal", "Every path under the bot's private data directory is refused", _agent_data_refusal),
    ("read-only-analysis", "Read commands never create or change the database", _read_only_analysis),
    ("no-stdout-noise", "Nothing but the envelope is ever written to stdout", _no_stdout_noise),
)


def run_contract(bin_path: str) -> list[ContractResult]:
    binary = os.path.abspath(bin_path)
    if not os.path.exists(binary):
        return [ContractResult("binary", "The binary exists", (f"{binary} does not exist. Build the module first.",))]
    results: list[ContractResult] = []
    for check_id, title, check in CHECKS:
        bench = Bench(binary)
        try:
            results.append(ContractResult(check_id, title, tuple(check(bench))))
        finally:
            for home in bench.homes:
                shutil.rmtree(home, ignore_errors=True)
    return results


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1 or argv[0].startswith("--"):
        asked_for_help = list(argv) == ["--help"]
        (sys.stdout if asked_for_help else sys.stderr).write(USAGE)
        return 0 if asked_for_help else 2
    results = run_contract(argv[0])
    width = max(len(result.id) for result in results)
    for result in results:
        sys.stdout.write(f"{'FAIL' if result.problems else 'ok  '}  {result.id.ljust(width)}  {result.title}\n")
        for problem in result.problems:
            sys.stdout.write(f"      {problem}\n")
    failed = sum(1 for result in results if result.problems)
    if failed == 0:
        sys.stdout.write(f"contract: ok. {len(results)} of {len(results)} checks passed for {argv[0]}.\n")
    else:
        sys.stdout.write(f"contract: {failed} of {len(results)} checks failed for {argv[0]}.\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
