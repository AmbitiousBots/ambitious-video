from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

type Schema = Mapping[str, object]

ANNOTATIONS = frozenset({"$schema", "title", "description", "$defs"})


@dataclass(frozen=True)
class SchemaProblem:
    pointer: str
    keyword: str
    message: str


@dataclass(frozen=True)
class _Context:
    schema: Schema
    root: Schema
    pointer: str


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str)


def _type_of(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if _is_list(value):
        return "array"
    return "object"


def _text(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _same_json(a: object, b: object) -> bool:
    return _type_of(a) == _type_of(b) and _text(a) == _text(b)


def _problem(pointer: str, keyword: str, message: str) -> list[SchemaProblem]:
    return [SchemaProblem(pointer, keyword, message)]


def _subschema(rule: object, pointer: str) -> Schema:
    if not isinstance(rule, Mapping):
        raise ValueError(f'The schema at "{pointer}" must be an object.')
    return rule


def _resolve_ref(ref: object, root: Schema) -> Schema:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError(f'The $ref {_text(ref)} is not a local "#/..." reference.')
    node: object = root
    for key in ref[2:].split("/"):
        node = node.get(key) if isinstance(node, Mapping) else None
    if not isinstance(node, Mapping):
        raise ValueError(f'The $ref "{ref}" does not resolve.')
    return node


def _list(rule: object) -> Sequence[object]:
    if not isinstance(rule, Sequence) or isinstance(rule, str):
        raise ValueError("The schema keyword needs a list.")
    return rule


def _ref(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    return _check(_resolve_ref(rule, context.root), value, context.pointer, context.root)


def _type(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    actual = _type_of(value)
    if actual == rule or (rule == "number" and actual == "integer"):
        return []
    return _problem(context.pointer, "type", f"expected {rule}, got {actual}")


def _const(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    return [] if _same_json(rule, value) else _problem(context.pointer, "const", f"expected {_text(rule)}")


def _enum(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if any(_same_json(option, value) for option in _list(rule)):
        return []
    return _problem(context.pointer, "enum", f"expected one of {_text(rule)}")


def _required(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if not isinstance(value, Mapping):
        return []
    return [
        SchemaProblem(context.pointer, "required", f'missing required field "{name}"')
        for name in _list(rule)
        if isinstance(name, str) and name not in value
    ]


def _properties(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if not isinstance(value, Mapping) or not isinstance(rule, Mapping):
        return []
    found: list[SchemaProblem] = []
    for name, sub in rule.items():
        if name in value:
            found.extend(
                _check(_subschema(sub, context.pointer), value[name], f"{context.pointer}/{name}", context.root)
            )
    return found


def _additional_properties(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if not isinstance(value, Mapping):
        return []
    declared = context.schema.get("properties")
    known: Mapping[str, object] = declared if isinstance(declared, Mapping) else {}
    found: list[SchemaProblem] = []
    for name in value:
        if name in known:
            continue
        pointer = f"{context.pointer}/{name}"
        if rule is False:
            found.extend(_problem(pointer, "additionalProperties", f'unexpected field "{name}"'))
        else:
            found.extend(_check(_subschema(rule, context.pointer), value[name], pointer, context.root))
    return found


def _items(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    found: list[SchemaProblem] = []
    for index, item in enumerate(value):
        found.extend(_check(_subschema(rule, context.pointer), item, f"{context.pointer}/{index}", context.root))
    return found


def _any_of(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    for option in _list(rule):
        if not _check(_subschema(option, context.pointer), value, context.pointer, context.root):
            return []
    return _problem(context.pointer, "anyOf", "matches none of the allowed shapes")


def _min_items(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if isinstance(value, Sequence) and not isinstance(value, str) and isinstance(rule, int) and len(value) < rule:
        return _problem(context.pointer, "minItems", f"needs at least {rule} item(s)")
    return []


def _max_items(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if isinstance(value, Sequence) and not isinstance(value, str) and isinstance(rule, int) and len(value) > rule:
        return _problem(context.pointer, "maxItems", f"allows at most {rule} item(s)")
    return []


def _min_length(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if isinstance(value, str) and isinstance(rule, int) and len(value) < rule:
        return _problem(context.pointer, "minLength", f"needs at least {rule} character(s)")
    return []


def _pattern(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if isinstance(value, str) and isinstance(rule, str) and re.search(rule, value) is None:
        return _problem(context.pointer, "pattern", f'"{value}" does not match {rule}')
    return []


def _minimum(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isinstance(rule, int | float)
        and value < rule
    ):
        return _problem(context.pointer, "minimum", f"must be at least {rule}")
    return []


def _maximum(rule: object, value: object, context: _Context) -> list[SchemaProblem]:
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isinstance(rule, int | float)
        and value > rule
    ):
        return _problem(context.pointer, "maximum", f"must be at most {rule}")
    return []


KEYWORDS: Mapping[str, Callable[[object, object, _Context], list[SchemaProblem]]] = {
    "$ref": _ref,
    "type": _type,
    "const": _const,
    "enum": _enum,
    "required": _required,
    "properties": _properties,
    "additionalProperties": _additional_properties,
    "items": _items,
    "anyOf": _any_of,
    "minItems": _min_items,
    "maxItems": _max_items,
    "minLength": _min_length,
    "pattern": _pattern,
    "minimum": _minimum,
    "maximum": _maximum,
}


def _check(schema: Schema, value: object, pointer: str, root: Schema) -> list[SchemaProblem]:
    found: list[SchemaProblem] = []
    for keyword, rule in schema.items():
        if keyword in ANNOTATIONS:
            continue
        keyword_check = KEYWORDS.get(keyword)
        if keyword_check is None:
            raise ValueError(f'The schema keyword "{keyword}" is not supported. Extend the checker before using it.')
        found.extend(keyword_check(rule, value, _Context(schema, root, pointer)))
    return found


def schema_problems(schema: object, value: object) -> list[SchemaProblem]:
    root = _subschema(schema, "")
    return _check(root, value, "", root)


def describe_schema_problems(problems: Sequence[SchemaProblem]) -> str:
    return "; ".join(f"{found.pointer or '/'} {found.keyword}: {found.message}" for found in problems)
