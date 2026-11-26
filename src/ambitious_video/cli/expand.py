from __future__ import annotations

import json
import re
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

from ambitious_kit.domain.envelope import ErrorEntry, Failure, JsonObject, JsonValue, failure
from ambitious_kit.paths import refuse_agent_data

from ambitious_video.cli.engine import as_json
from ambitious_video.domain.plan import canonical, digest_text, sorted_json
from ambitious_video.domain.template import IN_TEMPLATE, Expansion, Located, Template, expand, load_template
from ambitious_video.domain.types import TEMPLATE_ID_PATTERN, TEMPLATE_VERSION

STARTER_SUFFIX = ".json"
MAX_ERRORS = 20
HINT = "Fix the field named in data.problems."

Loads = Callable[[str], object]


def starter_ids() -> tuple[str, ...]:
    folder = files("ambitious_video").joinpath("templates")
    names = [item.name for item in folder.iterdir() if item.name.endswith(STARTER_SUFFIX)]
    return tuple(sorted(name.removesuffix(STARTER_SUFFIX) for name in names))


def starter_text(name: str) -> str:
    return files("ambitious_video").joinpath("templates").joinpath(name + STARTER_SUFFIX).read_text(encoding="utf-8")


def refuse(problems: tuple[Located, ...]) -> Failure:
    rows: list[JsonValue] = [
        {"code": "E_INPUT_SCHEMA", "in": item.document, "at": item.at, "message": item.message} for item in problems
    ]
    errors = [ErrorEntry("E_INPUT_SCHEMA", item.message, HINT) for item in problems[:MAX_ERRORS]]
    return Failure(errors, {"ok": False, "problems": rows})


def _is_starter(named: str) -> bool:
    return re.fullmatch(TEMPLATE_ID_PATTERN, named) is not None


def _template_text(named: str) -> str:
    if _is_starter(named):
        if named not in starter_ids():
            listed = ", ".join(starter_ids())
            raise failure("E_USAGE", f'No starter template is named "{named}".', f"Use a file, or one of: {listed}.")
        return starter_text(named)
    try:
        return Path(refuse_agent_data(named)).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise failure(
            "E_INPUT_SCHEMA",
            f"The template file {named} could not be read.",
            "Pass --template a file or a starter id.",
        ) from None


def read_template(named: str, loads: Loads) -> Template:
    try:
        raw = loads(_template_text(named))
    except ValueError:
        raise refuse((Located(IN_TEMPLATE, "", "The template is not valid JSON."),)) from None
    template, problems = load_template(raw)
    if template is None:
        raise refuse(problems)
    return template


def _brand(template: Template, root: Path, loads: Loads) -> object | None:
    if template.brand_file is None:
        return None
    unreadable = refuse((Located(IN_TEMPLATE, "/brand/file", "The brand file could not be read."),))
    try:
        path = Path(refuse_agent_data(str((root / template.brand_file).resolve())))
        return loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise unreadable from None


def script_text(script: object) -> str:
    return json.dumps(script, indent=2, ensure_ascii=False) + "\n"


def _written(script: object, out: Path) -> None:
    target = Path(refuse_agent_data(str(out)))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script_text(script), encoding="utf-8")
    except OSError:
        raise failure("E_USAGE", f"The file {out} could not be written.", "Pass --out a writable path.") from None


def expanded(document: object, root: Path, named: str | None, loads: Loads) -> tuple[Template, Expansion]:
    if named is None:
        raise failure(
            "E_USAGE",
            "expand needs --template <file or id>.",
            "Run guide and read data.templates.",
        )
    template = read_template(named, loads)
    made = expand(template, document, _brand(template, root, loads))
    if made.script is None:
        raise refuse(made.problems)
    return template, made


def expand_data(document: object, root: Path, named: str | None, out: Path | None, loads: Loads) -> JsonObject:
    template, made = expanded(document, root, named, loads)
    script = sorted_json(as_json(made.script))
    data: dict[str, JsonValue] = {
        "script": script,
        "scriptSha256": digest_text(canonical(script)),
        "template": {"id": template.id, "templateVersion": TEMPLATE_VERSION, "title": template.title},
        "fallbacks": [
            {"step": note.step, "scene": note.scene, "fallback": note.fallback, "missing": list(note.missing)}
            for note in made.notes
        ],
    }
    if out is not None:
        _written(made.script, out)
        data["written"] = out.as_posix()
    return {name: sorted_json(data[name]) for name in sorted(data)}


def starters_data(loads: Loads) -> list[JsonValue]:
    listed: list[JsonValue] = []
    for name in starter_ids():
        text = starter_text(name)
        template = read_template(name, loads)
        inputs: list[JsonValue] = []
        for spec in template.inputs.values():
            entry: dict[str, JsonValue] = {"name": spec.name, "type": spec.kind, "required": spec.required}
            if spec.default is not None:
                entry["default"] = spec.default
            if spec.options:
                entry["options"] = list(spec.options)
            if spec.description is not None:
                entry["description"] = spec.description
            inputs.append(entry)
        listed.append(
            {
                "id": template.id,
                "title": template.title,
                "description": template.description,
                "inputs": inputs,
                "text": text,
            }
        )
    return listed
