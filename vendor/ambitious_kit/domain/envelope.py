from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

type JsonValue = str | int | float | bool | None | Sequence[JsonValue] | Mapping[str, JsonValue]
type JsonObject = Mapping[str, JsonValue]

INTERNAL_ERROR_CODES = frozenset({"E_DB", "E_MIGRATION", "E_INTERNAL"})
MAX_SEED = 4294967295

MODULE_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
COMMAND_NAME = re.compile(r"^([a-z][a-z0-9-]*( [a-z][a-z0-9-]*)*)?$")
ERROR_CODE = re.compile(r"^E_[A-Z0-9]+(_[A-Z0-9]+)*$")
NOW = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class ErrorEntry:
    code: str
    message: str
    hint: str


@dataclass(frozen=True)
class Meta:
    now: str
    seed: int
    version: str


@dataclass(frozen=True)
class Identity:
    schema_version: int
    module: str
    command: str
    meta: Meta


@dataclass(frozen=True)
class Envelope:
    identity: Identity
    data: JsonObject
    errors: tuple[ErrorEntry, ...]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    @property
    def exit_code(self) -> int:
        if self.ok:
            return 0
        return 2 if any(error.code in INTERNAL_ERROR_CODES for error in self.errors) else 1

    def to_json(self) -> JsonObject:
        identity = self.identity
        return {
            "ok": self.ok,
            "schemaVersion": identity.schema_version,
            "module": identity.module,
            "command": identity.command,
            "data": self.data,
            "errors": [{"code": error.code, "message": error.message, "hint": error.hint} for error in self.errors],
            "meta": {"now": identity.meta.now, "seed": identity.meta.seed, "version": identity.meta.version},
        }

    def serialize(self) -> str:
        return json.dumps(self.to_json(), separators=(",", ":"), ensure_ascii=False) + "\n"


class Failure(Exception):
    def __init__(self, errors: Sequence[ErrorEntry], data: JsonObject | None = None) -> None:
        if len(errors) == 0:
            raise ValueError("A Failure needs at least one error.")
        super().__init__(" ".join(f"{error.code}: {error.message}" for error in errors))
        self.errors = tuple(errors)
        self.data: JsonObject = {} if data is None else data


def failure(code: str, message: str, hint: str, data: JsonObject | None = None) -> Failure:
    return Failure([ErrorEntry(code, message, hint)], data)


def ok(identity: Identity, data: JsonObject) -> Envelope:
    return Envelope(identity, data, ())


def err(identity: Identity, errors: Sequence[ErrorEntry], data: JsonObject | None = None) -> Envelope:
    if len(errors) == 0:
        raise ValueError("An error envelope needs at least one error.")
    return Envelope(identity, {} if data is None else data, tuple(errors))


def is_module_name(value: str) -> bool:
    return MODULE_NAME.fullmatch(value) is not None


def is_command_name(value: str) -> bool:
    return value != "" and COMMAND_NAME.fullmatch(value) is not None


def is_semver(value: str) -> bool:
    return VERSION.fullmatch(value) is not None


def _only_fields(value: Mapping[str, object], names: Sequence[str], where: str) -> list[str]:
    missing = [f'{where} is missing the field "{name}".' for name in names if name not in value]
    extra = [f'{where} has the unexpected field "{name}".' for name in value if name not in names]
    return missing + extra


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and len(value) > 0


def _error_problems(value: object, index: int) -> list[str]:
    where = f"errors[{index}]"
    if not isinstance(value, dict):
        return [f"{where} must be an object."]
    code = value.get("code")
    problems = _only_fields(value, ["code", "message", "hint"], where)
    if not (isinstance(code, str) and ERROR_CODE.fullmatch(code)):
        problems.append(f"{where}.code must look like E_NAME.")
    if not _non_empty_string(value.get("message")):
        problems.append(f"{where}.message must be a non-empty string.")
    if not _non_empty_string(value.get("hint")):
        problems.append(f"{where}.hint must be a non-empty string.")
    return problems


def _meta_problems(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["meta must be an object."]
    now, seed, version = value.get("now"), value.get("seed"), value.get("version")
    problems = _only_fields(value, ["now", "seed", "version"], "meta")
    if not (isinstance(now, str) and NOW.fullmatch(now)):
        problems.append("meta.now must be UTC with milliseconds, YYYY-MM-DDTHH:MM:SS.sssZ.")
    if not (isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= MAX_SEED):
        problems.append("meta.seed must be an integer from 0 to 4294967295.")
    if not (isinstance(version, str) and VERSION.fullmatch(version)):
        problems.append("meta.version must be a semantic version.")
    return problems


def envelope_problems(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["The envelope must be a JSON object."]
    is_ok, schema_version, module = value.get("ok"), value.get("schemaVersion"), value.get("module")
    command, errors = value.get("command"), value.get("errors")
    problems = _only_fields(
        value, ["ok", "schemaVersion", "module", "command", "data", "errors", "meta"], "The envelope"
    )
    if not isinstance(is_ok, bool):
        problems.append("ok must be true or false.")
    if not (isinstance(schema_version, int) and _is_integer(schema_version) and schema_version >= 1):
        problems.append("schemaVersion must be an integer, 1 or higher.")
    if not (isinstance(module, str) and MODULE_NAME.fullmatch(module)):
        problems.append("module must be the lower-case name after ambitious-.")
    if not (isinstance(command, str) and COMMAND_NAME.fullmatch(command)):
        problems.append("command must be lower-case words joined by one space, or empty.")
    if not isinstance(value.get("data"), dict):
        problems.append("data must be a JSON object.")
    if not isinstance(errors, list):
        problems.append("errors must be a list.")
    else:
        for index, entry in enumerate(errors):
            problems.extend(_error_problems(entry, index))
        if is_ok is True and len(errors) > 0:
            problems.append("ok is true, so errors must be empty.")
        if is_ok is False and len(errors) == 0:
            problems.append("ok is false, so errors must hold at least one error.")
    problems.extend(_meta_problems(value.get("meta")))
    return problems
