from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from ambitious_kit.domain.envelope import JsonObject, JsonValue

FIELD_TYPES = ("string", "number", "integer", "boolean", "object", "array")
ALLOWED = ", ".join(FIELD_TYPES)


@dataclass(frozen=True)
class InputField:
    name: str
    type: str
    required: bool


@dataclass(frozen=True)
class InputShape:
    fields: Sequence[InputField]
    example: JsonObject | None = None


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_integer(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


MATCHES: Mapping[str, Callable[[object], bool]] = {
    "string": lambda value: isinstance(value, str),
    "number": _is_number,
    "integer": _is_integer,
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
}

ARTICLE: Mapping[str, str] = {
    "string": "a string",
    "number": "a number",
    "integer": "an integer",
    "boolean": "a boolean",
    "object": "an object",
    "array": "an array",
}


def _kind_of(value: object) -> str:
    if isinstance(value, list):
        return "an array"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int | float):
        return "a number"
    return "an object" if isinstance(value, dict) else "not a JSON value"


def _name_of(field: object) -> object:
    return getattr(field, "name", None)


def _is_field_type(value: object) -> bool:
    return isinstance(value, str) and value in FIELD_TYPES


def _field_problems(field: object, position: int, seen: Sequence[object]) -> list[str]:
    name = _name_of(field)
    if not isinstance(name, str) or name == "":
        return [f"field {position} must have a name, a string that is not empty"]
    problems: list[str] = []
    if name in seen:
        problems.append(f'field "{name}" is listed more than once')
    declared = getattr(field, "type", None)
    if not isinstance(declared, str):
        problems.append(f'field "{name}" has a type that is not a string; use one of: {ALLOWED}')
    elif declared not in FIELD_TYPES:
        problems.append(f'field "{name}" has the unknown type "{declared}"; use one of: {ALLOWED}')
    if not isinstance(getattr(field, "required", None), bool):
        problems.append(f'field "{name}" must say required as true or false')
    return problems


def _example_problems(example: object, fields: Sequence[object]) -> list[str]:
    if not isinstance(example, dict):
        return ["the example must be a JSON object"]
    listed = [field for field in fields if isinstance(_name_of(field), str)]
    names = [_name_of(field) for field in listed]
    problems = [
        f'the example has the field "{key}", which is not a listed field' for key in example if key not in names
    ]
    for field in listed:
        key = str(_name_of(field))
        declared = getattr(field, "type", None)
        if key not in example:
            if getattr(field, "required", None) is True:
                problems.append(f'the example leaves out the required field "{key}"')
            continue
        if not isinstance(declared, str) or declared not in FIELD_TYPES:
            continue
        value = example[key]
        if value is None:
            problems.append(
                f'the example field "{key}" is null, but its type is {declared}; '
                f"null is never an example value, so give {ARTICLE[declared]} or leave the field out"
            )
        elif not MATCHES[declared](value):
            problems.append(f'the example field "{key}" must be {ARTICLE[declared]}, but is {_kind_of(value)}')
    return problems


def shape_problems(command: str, takes_input: bool, shape: object) -> list[str]:
    fields = getattr(shape, "fields", None)
    if not isinstance(fields, list | tuple):
        problems = ["the input shape must have a list of fields"]
    else:
        problems = [] if takes_input else ["an input shape is declared, but the command takes no input"]
        names = [_name_of(field) for field in fields]
        for index, field in enumerate(fields):
            problems.extend(_field_problems(field, index + 1, names[:index]))
        example = getattr(shape, "example", None)
        if example is not None:
            problems.extend(_example_problems(example, fields))
    return [f'command "{command}": {problem}' for problem in problems]


def listed_shape(shape: InputShape) -> JsonObject:
    fields: Sequence[JsonValue] = [
        {"name": field.name, "type": field.type, "required": field.required} for field in shape.fields
    ]
    return {"fields": fields} if shape.example is None else {"fields": fields, "example": shape.example}
