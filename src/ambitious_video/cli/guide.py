from __future__ import annotations

import json
from importlib.resources import files

from ambitious_kit.domain.envelope import JsonObject, JsonValue, failure

from ambitious_video.cli.engine import as_json

SHORT_HELP = "-h"
VERSION_FLAG = "--version"
KEPT_VALUE_FLAGS = ("--now", "--seed")
KEPT_PLAIN_FLAGS = ("--json",)


def builtin_command(argv: list[str]) -> list[str]:
    if SHORT_HELP in argv:
        word = "help"
    elif VERSION_FLAG in argv and "--help" not in argv:
        word = "version"
    else:
        return argv
    kept = [word]
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in KEPT_VALUE_FLAGS and index + 1 < len(argv):
            kept.extend(argv[index : index + 2])
            index += 2
            continue
        name, equals, _value = token.partition("=")
        if token in KEPT_PLAIN_FLAGS or (equals == "=" and name in KEPT_VALUE_FLAGS):
            kept.append(token)
        index += 1
    return kept


def _section(name: str) -> dict[str, JsonValue]:
    raw = json.loads(files("ambitious_video").joinpath("help.json").read_text(encoding="utf-8"))
    found = as_json(raw.get(name)) if isinstance(raw, dict) else None
    if not isinstance(found, dict):
        raise failure("E_INTERNAL", "The help text is unreadable.", "Reinstall the video module.")
    return dict(found)


def help_data(version: str) -> JsonObject:
    data = _section("help")
    data["version"] = version
    return data


def version_data(version: str) -> JsonObject:
    return {"version": version}


def guide_data(starters: list[JsonValue]) -> JsonObject:
    data = _section("guide")
    templates = data.get("templates")
    data["templates"] = {**templates, "starters": starters} if isinstance(templates, dict) else {"starters": starters}
    example = data.get("example")
    script = files("ambitious_video").joinpath("guide.yaml").read_text(encoding="utf-8")
    data["example"] = {**example, "script": script} if isinstance(example, dict) else {"script": script}
    return data
