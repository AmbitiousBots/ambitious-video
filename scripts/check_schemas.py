#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import venv
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from types import ModuleType

    from jsonschema import Draft202012Validator, ValidationError

Document = dict
Rejection = tuple[str, str]

REPO = Path(__file__).resolve().parent.parent
SCRIPT = Path(__file__).resolve()
REQUIREMENTS = REPO / "scripts" / "dev-requirements.txt"
VENV = REPO / ".venv"
STAMP = VENV / "dev-requirements.sha256"
RESTARTED = "AMBITIOUS_VIDEO_CHECK_RESTARTED"
MINIMUM_PYTHON = (3, 13)

SCHEMA = REPO / "schemas" / "video-script.v1.json"
EXAMPLES = REPO / "examples"
INVALID = EXAMPLES / "invalid"
EXPECTED = INVALID / "EXPECTED.json"
MODEL = REPO / "spec" / "RenderJob.tla"
REQUIRED_EXAMPLES = ("minimal.yaml", "ambitious-marketing.yaml")
DRAFT = "https://json-schema.org/draft/2020-12/schema"


def stop(message: str) -> NoReturn:
    sys.stderr.write(f"FAIL: {message}\n")
    sys.exit(1)


def restart(command: list[str]) -> NoReturn:
    if os.environ.get(RESTARTED) == command[0]:
        stop(f"starting again with {command[0]} did not help; run the check with Python 3.13 or newer")
    os.chdir(REPO)
    os.execvpe(command[0], command, {**os.environ, RESTARTED: command[0]})


def use_supported_python() -> None:
    if sys.version_info >= MINIMUM_PYTHON:
        return
    if shutil.which("mise") is None:
        stop("this check needs Python 3.13 or newer, or mise (https://mise.jdx.dev) to install the pinned one")
    restart(["mise", "exec", "--", "python3", str(SCRIPT)])


def use_dev_venv() -> None:
    wanted = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    python = VENV / "bin" / "python"
    installed = STAMP.exists() and STAMP.read_text(encoding="utf-8").strip() == wanted
    if installed and Path(sys.prefix).resolve() == VENV.resolve():
        return
    if not installed:
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
        install = [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
        pinned = ["--require-hashes", "--only-binary", ":all:", "--requirement", str(REQUIREMENTS)]
        if subprocess.run(install + pinned, check=False).returncode != 0:
            stop("could not install the pinned packages in scripts/dev-requirements.txt into .venv")
        STAMP.write_text(wanted + "\n", encoding="utf-8")
    restart([str(python), str(SCRIPT)])


def members(literal: object) -> list[object]:
    from typing import get_args

    return list(get_args(literal))


def track_ids(script: Document) -> list[str]:
    return [track["id"] for track in script["tracks"]]


def clips_of(script: Document, track_id: str) -> list[Document]:
    return [clip for track in script["tracks"] if track["id"] == track_id for clip in track.get("clips", [])]


def repeats_a_track_id(script: Document) -> bool:
    ids = track_ids(script)
    return len(set(ids)) != len(ids)


def captions_from_no_voiceover(script: Document) -> bool:
    return any(
        not any(clip["type"] == "voiceover" for clip in clips_of(script, track["from"]))
        for track in script["tracks"]
        if "from" in track
    )


def ducks_under_itself_or_nothing(script: Document) -> bool:
    return any(
        clip["duck_under"] == track["id"] or clip["duck_under"] not in track_ids(script)
        for track in script["tracks"]
        for clip in track.get("clips", [])
        if "duck_under" in clip
    )


def video_ends_before_it_starts(script: Document) -> bool:
    return any(
        clip["out"] <= clip.get("in", 0)
        for track in script["tracks"]
        for clip in track.get("clips", [])
        if clip["type"] == "video" and "out" in clip
    )


RULES: dict[str, Callable[[Document], bool]] = {
    "track-ids-unique": repeats_a_track_id,
    "captions-from-a-voiceover-track": captions_from_no_voiceover,
    "duck-under-another-track": ducks_under_itself_or_nothing,
    "video-out-after-in": video_ends_before_it_starts,
}


def broken_rules(script: Document) -> list[str]:
    return [rule for rule, breaks in RULES.items() if breaks(script)]


def rejections(validator: Draft202012Validator, document: Document) -> list[Rejection]:
    def spread(error: ValidationError) -> Iterator[Rejection]:
        yield error.validator, "".join(f"/{part}" for part in error.absolute_path)
        for inner in error.context or []:
            yield from spread(inner)

    return sorted({found for error in validator.iter_errors(document) for found in spread(error)})


def told(found: list[Rejection]) -> str:
    return ", ".join(f'"{keyword}" at "{at}"' for keyword, at in found)


def schema_matches_types(schema: Document, types: ModuleType) -> list[str]:
    problems = []

    def same(what: str, in_schema: object, in_types: object) -> None:
        if in_schema != in_types:
            problems.append(
                f"{what}: the schema says {json.dumps(in_schema)} and src/ambitious_video/domain/types.py says "
                f"{json.dumps(in_types)}. Change both together"
            )

    defs = schema["$defs"]
    clip_types = members(types.ClipType)
    same("clip types", defs["clip"]["properties"]["type"]["enum"], clip_types)
    shaped = [each["if"]["properties"]["type"]["const"] for each in defs["clip"]["allOf"]]
    same("clip types with a shape", shaped, clip_types)
    same("recording actions", list(defs["action"]["properties"]), members(types.ActionKind))
    same("cut transitions", defs["cut"]["properties"]["transition"]["enum"], members(types.Transition))
    same("voice providers", defs["voice"]["properties"]["provider"]["enum"], members(types.VoiceProvider))
    same("pattern of a track id", defs["trackId"]["pattern"], types.TRACK_ID_PATTERN)
    same("pattern of a color", defs["hexColor"]["pattern"], types.HEX_COLOR_PATTERN)
    same("schema version", schema["properties"]["schemaVersion"]["const"], types.SCHEMA_VERSION)
    same("rules checked next to the schema", list(RULES), members(types.ScriptRule))
    return problems


def model_matches_types(types: ModuleType) -> list[str]:
    text = MODEL.read_text(encoding="utf-8")
    states = re.search(r"^States == \{([^}]*)\}$", text, re.MULTILINE)
    moves = re.search(r"^Moves ==\n((?:    .*\n)+)", text, re.MULTILINE)
    if states is None or moves is None:
        return ["spec/RenderJob.tla must define States and Moves, to compare with the job states in types.py"]
    in_model = sorted(re.findall(r'<<"(\w+)", "(\w+)">>', moves.group(1)))
    in_types = sorted((start, end) for start, ends in types.JOB_TRANSITIONS.items() for end in ends)
    problems = []
    if sorted(re.findall(r'"(\w+)"', states.group(1))) != sorted(members(types.JobState)):
        problems.append("States in spec/RenderJob.tla and JobState in types.py differ. Change both together")
    if in_model != in_types:
        problems.append("Moves in spec/RenderJob.tla and JOB_TRANSITIONS in types.py differ. Change both together")
    return problems


def check_valid(
    validator: Draft202012Validator, load: Callable[[Path], Document], types: ModuleType
) -> tuple[list[str], int]:
    problems = []
    names = sorted(path.name for path in EXAMPLES.glob("*.yaml"))
    used = set()
    for name in REQUIRED_EXAMPLES:
        if name not in names:
            problems.append(f"examples/{name} is missing")
    for name in names:
        script = load(EXAMPLES / name)
        found = rejections(validator, script)
        if found:
            problems.append(f"examples/{name}: must be accepted, but the schema rejects it by {told(found)}")
            continue
        if broken_rules(script):
            problems.append(f"examples/{name}: must be accepted, but breaks {', '.join(broken_rules(script))}")
        used.update(clip["type"] for track in script["tracks"] for clip in track.get("clips", []))
    for clip_type in members(types.ClipType):
        if clip_type not in used:
            problems.append(f"no example in examples/ uses a {clip_type} clip, so nothing shows the schema accepts one")
    return problems, len(names)


def check_invalid(
    validator: Draft202012Validator, load: Callable[[Path], Document], types: ModuleType
) -> tuple[list[str], int]:
    problems = []
    names = sorted(path.name for path in INVALID.glob("*.yaml"))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    for name in expected:
        if name not in names:
            problems.append(f"EXPECTED.json lists {name}, but examples/invalid has no such file")
    for name in names:
        want = expected.get(name)
        script = load(INVALID / name)
        found = rejections(validator, script)
        if not isinstance(want, dict):
            problems.append(f"invalid/{name}: add it to examples/invalid/EXPECTED.json with what must reject it")
        elif isinstance(want.get("rule"), str):
            if found:
                problems.append(
                    f"invalid/{name}: must pass the schema and break only {want['rule']}, "
                    f"but the schema rejects it by {told(found)}"
                )
            elif broken_rules(script) != [want["rule"]]:
                broke = ", ".join(broken_rules(script)) or "nothing"
                problems.append(f"invalid/{name}: must break only {want['rule']}, but breaks {broke}")
        elif isinstance(want.get("keyword"), str) and isinstance(want.get("at"), str):
            if not found:
                problems.append(f"invalid/{name}: must be rejected, but the schema accepts it")
            elif (want["keyword"], want["at"]) not in found:
                problems.append(
                    f'invalid/{name}: rejected, but not by "{want["keyword"]}" at "{want["at"]}". '
                    f"It was rejected by {told(found)}"
                )
        else:
            shapes = '{ "keyword", "at" } for the schema or { "rule" } for a rule'
            problems.append(f"invalid/{name}: its EXPECTED.json entry must be {shapes}")
    wanted = [want for want in expected.values() if isinstance(want, dict)]
    for rule in members(types.ScriptRule):
        if not any(want.get("rule") == rule for want in wanted):
            problems.append(f"no invalid example breaks the rule {rule}, so nothing shows the check can catch it")
    unknown_clip = re.compile(r"^/tracks/\d+/clips/\d+/type$")
    if not any(want.get("keyword") == "enum" and unknown_clip.match(want.get("at", "")) for want in wanted):
        problems.append("no invalid example has an unknown clip type, so nothing shows the schema rejects one")
    return problems, len(names)


def main() -> None:
    use_supported_python()
    use_dev_venv()
    from importlib.metadata import version

    import jsonschema
    import yaml

    sys.path.insert(0, str(REPO / "src"))
    from ambitious_video.domain import types

    def load(path: Path) -> Document:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if schema.get("$schema") != DRAFT:
        stop(f"schemas/video-script.v1.json must declare {DRAFT}")
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    valid_problems, valid = check_valid(validator, load, types)
    invalid_problems, invalid = check_invalid(validator, load, types)
    problems = schema_matches_types(schema, types) + model_matches_types(types) + valid_problems + invalid_problems
    for problem in problems:
        sys.stderr.write(f"FAIL: {problem}\n")
    if problems:
        sys.exit(1)
    report = [
        f"python {sys.version.split()[0]}, jsonschema {version('jsonschema')}, PyYAML {version('PyYAML')}",
        "ok  schemas/video-script.v1.json is a valid JSON Schema (draft 2020-12)",
        "ok  the schema and src/ambitious_video/domain/types.py agree on clip types, actions, transitions, "
        "providers, patterns and rules",
        "ok  the job states and moves in spec/RenderJob.tla and types.py agree",
        f"ok  {valid} examples accepted, and together they use every clip type",
        f"ok  {invalid} invalid examples rejected, each for the reason in examples/invalid/EXPECTED.json",
        "check_schemas: ok",
    ]
    sys.stdout.write("\n".join(report) + "\n")


main()
