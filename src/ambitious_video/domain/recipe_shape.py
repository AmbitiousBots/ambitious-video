from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping

from ambitious_video.domain.types import SchemaProblem, ScriptProblem, UnknownStep

DSL_VERSION = 1
MAX_RECIPE_STEPS = 32
MAX_RECIPE_TRIGGERS = 8
MAX_RECIPE_NEEDS = 16
MAX_RECIPE_FROM = 16
MAX_RECIPE_DISCLOSURE = 160
MAX_RECIPE_SEED = 2_147_483_647
MAX_RECIPE_BUDGET_USD = 10_000
MAX_RECIPE_BYTES = 65_536
MAX_RECIPE_VALUES = 10_000
MAX_SAFE_RECORD_KEYS = 40
MAX_SAFE_DEPTH = 16
MAX_SAFE_NODES = 2000
MAX_TITLE = 160
MAX_SUMMARY = 400
MAX_MODEL = 120
MAX_PROVIDER = 64
MAX_PROMPT = 8000
MAX_ACTION = 96
MAX_EXPR_TEXT = 2048
MAX_PATH_TEXT = 320
MAX_PATH_SEGMENTS = 6
MAX_ENUM_VALUES = 64
MAX_ENUM_TEXT = 64
MAX_ADAPTER_VERSION = 1000
MAX_VERSION = 1_000_000
RECIPE_ID_PATTERN = r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
STEP_ID_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
_EVENT_SEG = r"[a-z][a-z0-9_]{0,31}"
EVENT_NAME_PATTERN = f"^{_EVENT_SEG}\\.{_EVENT_SEG}$"
DURATION_TEXT_PATTERN = r"^[1-9][0-9]{0,3}[smhd]$"
SUBJECT_KIND_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
SOURCE_PATTERN = r"^doc:sha256:[0-9a-f]{64}$"
CURRENCY_PATTERN = r"^[A-Z]{3}$"
_UNITS = "day|week|month|quarter|year"
RECURRENCE_PATTERN = f"^(?:{_UNITS}|[1-9][0-9]{{0,3}}[hd])$"
_DAY = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
_CLOCK = r"T[0-9]{2}:[0-9]{2}:[0-9]{2}"
_FRACTION = r"(?:\.[0-9]{1,6})?"
_ZONE = r"(?:Z|[+-][0-9]{2}:[0-9]{2})"
INSTANT_PATTERN = f"^{_DAY}(?:{_CLOCK}{_FRACTION}{_ZONE})?$"
_PATH_SEG = r"[a-z][a-z0-9_]*"
DOTTED_PATH_PATTERN = f"^{_PATH_SEG}(?:\\.{_PATH_SEG}){{0,9}}$"
FACT_KEY_PATTERN = f"^{_EVENT_SEG}(?:\\.{_EVENT_SEG}){{0,7}}$"
_REVIEW = r"verified(?:_by|_at)?"
_RECORD_ACTIONS = r"documents\.ingest|facts\.upsert"
_QUERY_ACTIONS = r"views\.query|signals\.emit"
_TASK_ACTIONS = r"(?:sor|bot)\.[a-z][a-z0-9_]{0,63}"
ACTION_PATTERN = f"^(?:{_RECORD_ACTIONS}|{_QUERY_ACTIONS}|{_TASK_ACTIONS})$"
_KEY_BLOCK = "-----BEGIN [A-Z ]*PRIVATE KEY-----"
_EDGE = "(?<![a-z0-9])"
_PREFIXES = "|".join(
    (
        "sk_(?:live|test)_",
        "rk_(?:live|test)_",
        "whsec_",
        "amb_",
        "ghp_",
        "gho_",
        "ghs_",
        "github_pat_",
        "sk-proj-",
        "sk-ant-",
        "xai-",
    )
)
_CLOUD_KEY = "AKIA[0-9A-Z]{16}(?![0-9A-Z])"
_TOKEN = f"{_EDGE}(?:{_PREFIXES})[a-z0-9_-]{{12,}}"
RECIPE_ID = re.compile(RECIPE_ID_PATTERN)
STEP_ID = re.compile(STEP_ID_PATTERN)
EVENT_RE = re.compile(EVENT_NAME_PATTERN)
DURATION_RE = re.compile(DURATION_TEXT_PATTERN)
SUBJECT_RE = re.compile(SUBJECT_KIND_PATTERN)
SOURCE_RE = re.compile(SOURCE_PATTERN)
CURRENCY_RE = re.compile(CURRENCY_PATTERN)
RECURRENCE_RE = re.compile(RECURRENCE_PATTERN)
INSTANT_RE = re.compile(INSTANT_PATTERN)
DOTTED_PATH_RE = re.compile(DOTTED_PATH_PATTERN)
FACT_KEY_RE = re.compile(FACT_KEY_PATTERN)
ACTION_RE = re.compile(ACTION_PATTERN)
REVIEW_END_RE = re.compile(f"(?:^|\\.){_REVIEW}\\Z")
REVIEW_SUFFIX_RE = re.compile(f"\\.{_REVIEW}\\Z")
FACT_TEMPLATE_RE = re.compile(r"\{\{\s*facts\.")
SECRET_RE = re.compile(f"{_KEY_BLOCK}|{_TOKEN}|{_EDGE}{_CLOUD_KEY}", re.IGNORECASE)
RESERVED_STEP_IDS = frozenset({"__proto__", "constructor", "prototype"})
STEP_KINDS = ("ai", "tool", "return")
REFUSED_STEP_KINDS = ("eval", "human", "wait", "recipe")
TRIGGER_KINDS = ("manual", "event", "schedule", "signal")
EVENT_TYPES = (
    "document.ingested",
    "fact.written",
    "fact.verified",
    "subject.created",
    "entity.upserted",
    "entity.confirmed",
)
FIELD_TYPES = (
    "string",
    "text",
    "int",
    "decimal",
    "money",
    "date",
    "datetime",
    "bool",
    "enum",
    "ref",
    "list",
    "object",
)
AI_OUTPUT_TYPES = (*FIELD_TYPES, "file")
UNIQUE_FIELD_TYPES = frozenset({"string", "int", "date", "datetime", "ref"})
ACTIVATION_KINDS = ("auto", "person")
SHARED_CACHE_KINDS = ("step_key", "none")
EXECUTORS = ("server", "bot")
RETURN_STATUSES = ("completed", "blocked")
PAIR_OPS = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "addDays", "addBusinessDays", "diffDays"}
)
ONE_OPS = frozenset({"exists", "not"})
LIST_OPS: Mapping[str, int] = {"and": 16, "or": 16, "coalesce": 8}
MAX_IN_VALUES = 64
EXPR_OPS = frozenset({"$", "in"}) | PAIR_OPS | ONE_OPS | frozenset(LIST_OPS)
TOP_KEYS = {
    "dslVersion",
    "kind",
    "id",
    "version",
    "title",
    "summary",
    "activation",
    "budget",
    "subject",
    "source",
    "triggers",
    "steps",
}
BASE_KEYS = {"id", "kind", "title", "needs", "from"}
AI_KEYS = BASE_KEYS | {
    "next",
    "adapterVersion",
    "budget",
    "executor",
    "provider",
    "model",
    "cache",
    "disclosure",
    "seed",
    "prompt",
    "output",
    "params",
    "fallback",
}
TOOL_KEYS = BASE_KEYS | {"next", "timeout", "action", "input", "outputSchema"}
RETURN_KEYS = BASE_KEYS | {"status", "output"}
MANUAL_TRIGGER_KEYS = {"kind", "input"}
EVENT_TRIGGER_KEYS = {"kind", "type", "where"}
SCHEDULE_TRIGGER_KEYS = {"kind", "every", "at", "start"}
SIGNAL_TRIGGER_KEYS = {"kind", "name"}
FIELD_SPEC_KEYS = {"currency", "refKind", "required", "type", "unique", "values"}


def _join(*parts: str) -> str:
    return "".join(parts)


class Problems:
    def __init__(self) -> None:
        self.items: list[ScriptProblem] = []

    def schema(self, at: str, message: str) -> None:
        self.items.append(SchemaProblem(at, message))

    def unknown(self, at: str, step: str) -> None:
        self.items.append(UnknownStep(at, step))


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _int_between(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _text_between(value: object, low: int, high: int) -> bool:
    return isinstance(value, str) and low <= len(value) <= high


def valid_step_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 64
        and STEP_ID.fullmatch(value) is not None
        and value not in RESERVED_STEP_IDS
    )


def _valid_kebab_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 64 and RECIPE_ID.fullmatch(value) is not None


def is_fact_key(value: object) -> bool:
    return isinstance(value, str) and FACT_KEY_RE.fullmatch(value) is not None and REVIEW_END_RE.search(value) is None


def _valid_path(value: object) -> bool:
    if not isinstance(value, str) or not (1 <= len(value) <= MAX_PATH_TEXT):
        return False
    if DOTTED_PATH_RE.fullmatch(value) is None or value == "facts":
        return False
    parts = value.split(".")
    if any(part in RESERVED_STEP_IDS for part in parts):
        return False
    if parts[0] != "facts":
        return len(parts) <= MAX_PATH_SEGMENTS
    return is_fact_key(REVIEW_SUFFIX_RE.sub("", value[len("facts.") :], count=1))


def has_secret(text: str) -> bool:
    shown = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return SECRET_RE.search(unicodedata.normalize("NFKC", shown)) is not None


def _well_formed(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _text_fault(text: str) -> str | None:
    if "\x00" in text:
        return "Null bytes are not allowed."
    if not _well_formed(text):
        return "Text must be well-formed Unicode."
    if has_secret(text):
        return "Remove secrets from the recipe."
    return None


def check_keys(bag: Problems, obj: Mapping[str, object], allowed: set[str], at: str) -> None:
    for key in obj:
        if key not in allowed:
            bag.schema(_join(at, "/", str(key)), "This field is not allowed.")


def _check_all_text(bag: Problems, raw: object) -> None:
    stack: list[tuple[object, str]] = [(raw, "")]
    seen = 0
    while stack:
        value, at = stack.pop()
        seen += 1
        if seen > MAX_RECIPE_VALUES:
            bag.schema("", "Recipe has too many values.")
            return
        if isinstance(value, str):
            fault = _text_fault(value)
            if fault is not None:
                bag.schema(at, fault)
        elif isinstance(value, list):
            stack.extend((item, _join(at, "/", str(idx))) for idx, item in enumerate(value))
        elif isinstance(value, dict):
            for key, item in value.items():
                fault = _text_fault(key) if isinstance(key, str) else "Unsafe object key."
                if fault is not None:
                    bag.schema(at, fault)
                stack.append((item, _join(at, "/", str(key))))


def _check_safe_value(bag: Problems, value: object, at: str) -> bool:
    stack: list[tuple[object, str, int]] = [(value, at, 0)]
    nodes = 0
    clean = True
    while stack:
        item, where, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SAFE_NODES:
            bag.schema(at, "Value has too many entries.")
            return False
        if depth > MAX_SAFE_DEPTH:
            bag.schema(where, "Value is nested too deeply.")
            return False
        if isinstance(item, list):
            stack.extend((child, _join(where, "/", str(idx)), depth + 1) for idx, child in enumerate(item))
        elif isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or key in RESERVED_STEP_IDS:
                    bag.schema(_join(where, "/", str(key)), "Unsafe object key.")
                    clean = False
                    continue
                stack.append((child, _join(where, "/", key), depth + 1))
        elif isinstance(item, float) and not math.isfinite(item):
            bag.schema(where, "Numbers must be finite.")
            clean = False
    return clean


def check_safe_record(bag: Problems, value: object, at: str) -> None:
    if not isinstance(value, dict):
        bag.schema(at, "Value must be an object.")
        return
    if len(value) > MAX_SAFE_RECORD_KEYS:
        bag.schema(at, "Too many entries in object.")
        return
    for key, item in value.items():
        where = _join(at, "/", str(key))
        if not isinstance(key, str) or not (1 <= len(key) <= 64) or key in RESERVED_STEP_IDS:
            bag.schema(where, "Unsafe object key.")
            continue
        _check_safe_value(bag, item, where)


def _expr_children(value: Mapping[str, object]) -> list[object] | None:
    if len(value) != 1:
        return None
    ((name, body),) = value.items()
    if name == "$":
        return [] if _valid_path(body) else None
    if name in ONE_OPS:
        return [body]
    if not isinstance(body, list):
        return None
    if name in PAIR_OPS:
        return list(body) if len(body) == 2 else None
    if name in LIST_OPS:
        return list(body) if 1 <= len(body) <= LIST_OPS[name] else None
    if name == "in" and len(body) == 2 and isinstance(body[1], list) and 1 <= len(body[1]) <= MAX_IN_VALUES:
        return [body[0], *body[1]]
    return None


def _check_expr(bag: Problems, value: object, at: str) -> None:
    if not _check_safe_value(bag, value, at):
        return
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, bool) or _is_number(item):
            continue
        if isinstance(item, str) and len(item) <= MAX_EXPR_TEXT:
            continue
        children = _expr_children(item) if isinstance(item, dict) else None
        if children is None:
            bag.schema(at, "Invalid expression.")
            return
        stack.extend(children)


def _check_title(bag: Problems, obj: Mapping[str, object], at: str) -> None:
    if "title" in obj and not _text_between(obj["title"], 1, MAX_TITLE):
        bag.schema(_join(at, "/title"), "title must be non-empty text.")


def _check_budget(bag: Problems, raw: Mapping[str, object], at: str) -> None:
    if "budget" not in raw:
        return
    budget = raw["budget"]
    where = _join(at, "/budget")
    if not isinstance(budget, dict):
        bag.schema(where, "budget must be an object.")
        return
    check_keys(bag, budget, {"maxUsd"}, where)
    cap = budget.get("maxUsd")
    if not _is_number(cap) or not isinstance(cap, (int, float)) or not (0 <= cap <= MAX_RECIPE_BUDGET_USD):
        bag.schema(_join(where, "/maxUsd"), "Invalid budget maxUsd.")


def _unique_texts(value: object, low: int, high: int) -> bool:
    if not isinstance(value, list) or not (low <= len(value) <= high):
        return False
    if not all(_text_between(item, 1, MAX_ENUM_TEXT) for item in value):
        return False
    return len(set(value)) == len(value)


def _field_spec_ok(spec: Mapping[str, object]) -> bool:
    if any(key not in FIELD_SPEC_KEYS for key in spec):
        return False
    kind = spec.get("type")
    if kind not in FIELD_TYPES:
        return False
    if any(name in spec and not isinstance(spec[name], bool) for name in ("required", "unique")):
        return False
    currency = spec.get("currency")
    if "currency" in spec and (not isinstance(currency, str) or CURRENCY_RE.fullmatch(currency) is None):
        return False
    if "refKind" in spec and not _valid_kebab_id(spec["refKind"]):
        return False
    if "values" in spec and not _unique_texts(spec["values"], 1, MAX_ENUM_VALUES):
        return False
    if (kind == "money") != ("currency" in spec) or (kind == "enum") != ("values" in spec):
        return False
    if (kind == "ref") != ("refKind" in spec):
        return False
    return spec.get("unique") is not True or kind in UNIQUE_FIELD_TYPES


def _check_manual_trigger(bag: Problems, trigger: Mapping[str, object], at: str) -> None:
    check_keys(bag, trigger, MANUAL_TRIGGER_KEYS, at)
    if "input" not in trigger:
        return
    raw_in = trigger["input"]
    if not isinstance(raw_in, dict) or len(raw_in) > MAX_SAFE_RECORD_KEYS:
        bag.schema(_join(at, "/input"), "Manual input must be an object.")
        return
    for key, val in raw_in.items():
        if not valid_step_id(key) or not isinstance(val, dict) or not _field_spec_ok(val):
            bag.schema(_join(at, "/input/", str(key)), "Invalid manual input field.")


def _check_moment(bag: Problems, value: object, at: str, pattern: re.Pattern[str], message: str) -> None:
    if isinstance(value, dict):
        _check_expr(bag, value, at)
    elif not isinstance(value, str) or pattern.fullmatch(value) is None:
        bag.schema(at, message)


def _check_schedule_trigger(bag: Problems, trigger: Mapping[str, object], at: str) -> None:
    check_keys(bag, trigger, SCHEDULE_TRIGGER_KEYS, at)
    if ("every" in trigger) == ("at" in trigger):
        bag.schema(at, "Schedule needs exactly one of every, at.")
    if "start" in trigger and "every" not in trigger:
        bag.schema(at, "start applies only with every.")
    if "every" in trigger:
        _check_moment(bag, trigger["every"], _join(at, "/every"), RECURRENCE_RE, "Invalid schedule every.")
    for name in ("at", "start"):
        if name in trigger:
            _check_moment(bag, trigger[name], _join(at, "/", name), INSTANT_RE, "Invalid schedule date.")


def _check_trigger(bag: Problems, trigger: object, at: str) -> None:
    if not isinstance(trigger, dict):
        bag.schema(at, "Trigger must be an object.")
        return
    kind = trigger.get("kind")
    if kind == "manual":
        _check_manual_trigger(bag, trigger, at)
    elif kind == "event":
        check_keys(bag, trigger, EVENT_TRIGGER_KEYS, at)
        if trigger.get("type") not in EVENT_TYPES:
            bag.schema(_join(at, "/type"), "Invalid event type.")
        if "where" in trigger:
            _check_expr(bag, trigger["where"], _join(at, "/where"))
    elif kind == "schedule":
        _check_schedule_trigger(bag, trigger, at)
    elif kind == "signal":
        check_keys(bag, trigger, SIGNAL_TRIGGER_KEYS, at)
        name = trigger.get("name")
        if not isinstance(name, str) or EVENT_RE.fullmatch(name) is None:
            bag.schema(_join(at, "/name"), "Invalid signal name.")
    else:
        bag.schema(at, "Trigger kind is invalid.")


def _recipe_size(raw: Mapping[str, object]) -> int:
    try:
        text = json.dumps(raw, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return 0
    return len(text.encode("utf-8", "surrogatepass"))


def _check_header(bag: Problems, raw: Mapping[str, object]) -> None:
    if _recipe_size(raw) > MAX_RECIPE_BYTES:
        bag.schema("", "Recipe exceeds 64 KiB limit.")
    if "entity" in raw:
        bag.schema("/entity", "This tool keeps no entities.")
    check_keys(bag, raw, TOP_KEYS | {"entity"}, "")
    version_tag = raw.get("dslVersion")
    if not is_int(version_tag) or version_tag != DSL_VERSION:
        bag.schema("/dslVersion", "dslVersion must be 1.")
    if raw.get("kind") != "recipe":
        bag.schema("/kind", "kind must be recipe.")
    if not _valid_kebab_id(raw.get("id")):
        bag.schema("/id", "id must be a kebab-case slug.")
    if not _int_between(raw.get("version"), 1, MAX_VERSION):
        bag.schema("/version", "version must be a positive integer.")
    _check_title(bag, raw, "")
    if "summary" in raw and not _text_between(raw["summary"], 1, MAX_SUMMARY):
        bag.schema("/summary", "summary must be non-empty text.")
    if "activation" in raw and raw["activation"] not in ACTIVATION_KINDS:
        bag.schema("/activation", "activation must be auto or person.")
    subject = raw.get("subject")
    if "subject" in raw and (not isinstance(subject, str) or SUBJECT_RE.fullmatch(subject) is None):
        bag.schema("/subject", "subject must be a short lowercase kind.")
    source = raw.get("source")
    if "source" in raw and (not isinstance(source, str) or SOURCE_RE.fullmatch(source) is None):
        bag.schema("/source", "source must be doc:sha256:<64 hex>.")
    _check_budget(bag, raw, "")
    triggers = raw.get("triggers")
    if not isinstance(triggers, list) or not (1 <= len(triggers) <= MAX_RECIPE_TRIGGERS):
        bag.schema("/triggers", "triggers must be a non-empty list.")
        return
    for index, trigger in enumerate(triggers):
        _check_trigger(bag, trigger, _join("/triggers/", str(index)))


def parse_needs(bag: Problems, step: Mapping[str, object], at: str) -> tuple[str, ...]:
    if "needs" not in step:
        return ()
    raw_needs = step["needs"]
    if not isinstance(raw_needs, list) or len(raw_needs) > MAX_RECIPE_NEEDS:
        bag.schema(_join(at, "/needs"), "needs must be a list of step ids.")
        return ()
    out: list[str] = []
    for idx, item in enumerate(raw_needs):
        if not valid_step_id(item):
            bag.schema(_join(at, "/needs/", str(idx)), "Invalid step id in needs.")
        elif str(item) in out:
            bag.schema(_join(at, "/needs/", str(idx)), "Duplicate step id in needs.")
        else:
            out.append(str(item))
    return tuple(out)


def _check_from(bag: Problems, step: Mapping[str, object], at: str) -> None:
    if "from" not in step:
        return
    keys = step["from"]
    where = _join(at, "/from")
    if not isinstance(keys, list) or not (1 <= len(keys) <= MAX_RECIPE_FROM):
        bag.schema(where, "from must be a list of fact keys.")
        return
    for idx, key in enumerate(keys):
        if not is_fact_key(key):
            bag.schema(_join(where, "/", str(idx)), "Invalid fact key in from.")
        elif key in keys[:idx]:
            bag.schema(_join(where, "/", str(idx)), "Duplicate fact key in from.")


def _parse_next(bag: Problems, step: Mapping[str, object], at: str) -> str | None:
    if "next" not in step:
        return None
    target = step["next"]
    if not valid_step_id(target):
        bag.schema(_join(at, "/next"), "Invalid step id in next.")
        return None
    return str(target)


def _check_types(
    bag: Problems, step: Mapping[str, object], name: str, at: str, *, lists: bool
) -> None:
    if name not in step:
        return
    fields = step[name]
    where = _join(at, "/", name)
    if not isinstance(fields, dict) or len(fields) > MAX_SAFE_RECORD_KEYS:
        bag.schema(where, "Value must be a map of field types.")
        return
    allowed = AI_OUTPUT_TYPES if lists else FIELD_TYPES
    for key, kind in fields.items():
        named = isinstance(kind, str) and kind in allowed
        listed = lists and _unique_texts(kind, 1, MAX_ENUM_VALUES)
        if not valid_step_id(key) or not (named or listed):
            bag.schema(_join(where, "/", str(key)), "Invalid output field.")


def _check_ai_step(bag: Problems, step: Mapping[str, object], at: str) -> None:
    check_keys(bag, step, AI_KEYS, at)
    if "adapterVersion" in step and not _int_between(step["adapterVersion"], 1, MAX_ADAPTER_VERSION):
        bag.schema(_join(at, "/adapterVersion"), "Invalid adapterVersion.")
    _check_budget(bag, step, at)
    if "cache" in step and step["cache"] not in SHARED_CACHE_KINDS:
        bag.schema(_join(at, "/cache"), "cache must be step_key or none.")
    if "disclosure" in step and not _text_between(step["disclosure"], 1, MAX_RECIPE_DISCLOSURE):
        bag.schema(_join(at, "/disclosure"), "Invalid disclosure text.")
    if "executor" in step and step["executor"] not in EXECUTORS:
        bag.schema(_join(at, "/executor"), "executor must be server or bot.")
    if "fallback" in step:
        _check_safe_value(bag, step["fallback"], _join(at, "/fallback"))
    if not _text_between(step.get("model"), 1, MAX_MODEL):
        bag.schema(_join(at, "/model"), "model must be non-empty text.")
    _check_types(bag, step, "output", at, lists=True)
    if "params" in step:
        check_safe_record(bag, step["params"], _join(at, "/params"))
    if "prompt" in step and not _text_between(step["prompt"], 1, MAX_PROMPT):
        bag.schema(_join(at, "/prompt"), "prompt must be non-empty text.")
    if "provider" in step and not _text_between(step["provider"], 1, MAX_PROVIDER):
        bag.schema(_join(at, "/provider"), "provider must be non-empty text.")
    if "seed" in step and not _int_between(step["seed"], 0, MAX_RECIPE_SEED):
        bag.schema(_join(at, "/seed"), "Invalid seed.")


def _check_tool_step(bag: Problems, step: Mapping[str, object], at: str) -> None:
    check_keys(bag, step, TOOL_KEYS, at)
    action = step.get("action")
    if not _text_between(action, 1, MAX_ACTION) or ACTION_RE.fullmatch(str(action)) is None:
        bag.schema(_join(at, "/action"), "Invalid tool action.")
    if "input" in step:
        check_safe_record(bag, step["input"], _join(at, "/input"))
    _check_types(bag, step, "outputSchema", at, lists=False)
    timeout = step.get("timeout")
    if "timeout" in step and (not isinstance(timeout, str) or DURATION_RE.fullmatch(timeout) is None):
        bag.schema(_join(at, "/timeout"), "Invalid timeout duration.")


def _check_return_step(bag: Problems, step: Mapping[str, object], at: str) -> None:
    check_keys(bag, step, RETURN_KEYS, at)
    if "status" in step and step["status"] not in RETURN_STATUSES:
        bag.schema(_join(at, "/status"), "Invalid return status.")
    if "output" in step:
        check_safe_record(bag, step["output"], _join(at, "/output"))


def _has_cycle(edges: Mapping[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        mark = state.get(node, 0)
        if mark != 0:
            return mark == 1
        state[node] = 1
        if any(visit(nxt) for nxt in edges.get(node, ())):
            return True
        state[node] = 2
        return False

    return any(visit(node) for node in edges)


def _check_graph(
    bag: Problems,
    order: list[str],
    kinds: Mapping[str, str],
    deps: Mapping[str, tuple[str, ...]],
    nexts: Mapping[str, str | None],
) -> None:
    place = {sid: _join("/steps/", str(index)) for index, sid in enumerate(order)}
    edges: dict[str, list[str]] = {sid: [] for sid in order}
    needed = {dep for needs in deps.values() for dep in needs}
    for index, sid in enumerate(order):
        for dep in deps[sid]:
            if dep not in kinds or dep == sid:
                bag.schema(_join(place[sid], "/needs"), _join("Unknown dependency ", dep, "."))
            else:
                edges[dep].append(sid)
        if kinds[sid] == "return":
            continue
        target = nexts[sid]
        if target is None and sid not in needed and index + 1 < len(order):
            target = order[index + 1]
        elif target is not None and target not in kinds:
            bag.schema(_join(place[sid], "/next"), _join("Unknown next step ", target, "."))
            continue
        elif target == sid:
            bag.schema(_join(place[sid], "/next"), "Step cannot point to itself.")
            continue
        if target is not None:
            edges[sid].append(target)
    if "return" not in kinds.values():
        bag.schema("/steps", "Recipe requires a return step.")
    if _has_cycle(edges):
        bag.schema("/steps", "Step dependencies contain a cycle.")
        return
    for sid in order:
        if kinds[sid] != "return" and not edges[sid]:
            bag.schema(place[sid], "Step cannot reach a return step.")


def _reads_facts(value: object) -> bool:
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if FACT_TEMPLATE_RE.search(item) is not None:
                return True
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            ref = item.get("$")
            if isinstance(ref, str) and ref.startswith("facts."):
                return True
            stack.extend(item.values())
    return False


def _check_steps(bag: Problems, raw_steps: list[object]) -> None:
    order: list[str] = []
    kinds: dict[str, str] = {}
    deps: dict[str, tuple[str, ...]] = {}
    nexts: dict[str, str | None] = {}
    whole = True
    for index, entry in enumerate(raw_steps):
        at = _join("/steps/", str(index))
        if not isinstance(entry, dict):
            bag.schema(at, "Step must be an object.")
            whole = False
            continue
        step_id = entry.get("id")
        if not valid_step_id(step_id) or str(step_id) in kinds:
            bag.schema(_join(at, "/id"), "Invalid or duplicate step id.")
            whole = False
            continue
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in STEP_KINDS:
            if kind in REFUSED_STEP_KINDS:
                bag.schema(_join(at, "/kind"), _join("This tool cannot run ", str(kind), " steps."))
            else:
                bag.unknown(_join(at, "/kind"), str(kind))
            whole = False
            continue
        sid = str(step_id)
        order.append(sid)
        kinds[sid] = kind
        _check_title(bag, entry, at)
        deps[sid] = parse_needs(bag, entry, at)
        _check_from(bag, entry, at)
        nexts[sid] = None if kind == "return" else _parse_next(bag, entry, at)
        if kind == "ai":
            _check_ai_step(bag, entry, at)
        elif kind == "tool":
            _check_tool_step(bag, entry, at)
        else:
            _check_return_step(bag, entry, at)
    if whole:
        _check_graph(bag, order, kinds, deps, nexts)


def check_recipe_shape(raw: Mapping[str, object]) -> tuple[ScriptProblem, ...]:
    bag = Problems()
    _check_all_text(bag, raw)
    _check_header(bag, raw)
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not (1 <= len(raw_steps) <= MAX_RECIPE_STEPS):
        bag.schema("/steps", "steps must be a non-empty list.")
        return tuple(bag.items)
    _check_steps(bag, raw_steps)
    lists_from = any(isinstance(step, dict) and "from" in step for step in raw_steps)
    if "subject" not in raw and (lists_from or _reads_facts([raw_steps, raw.get("triggers")])):
        bag.schema("/subject", "Recipes that read facts need a subject.")
    return tuple(bag.items)
