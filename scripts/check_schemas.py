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

SCHEMA = REPO / "schemas" / "video-script.v2.json"
TEMPLATE_SCHEMA = REPO / "schemas" / "video-template.v1.json"
RECIPE_SCHEMA = REPO / "schemas" / "video-recipe.v1.json"
TEMPLATES = REPO / "templates"
PACKAGED_TEMPLATES = REPO / "src" / "ambitious_video" / "templates"
REQUIRED_TEMPLATES = ("announcement", "short-explainer", "welcome")
EXAMPLES = REPO / "examples"
INVALID = EXAMPLES / "invalid"
EXPECTED = INVALID / "EXPECTED.json"
MODEL = REPO / "spec" / "RenderJob.tla"
REQUIRED_EXAMPLES = (
    "minimal.json",
    "all-clip-types.json",
    "titles-and-captions.json",
    "generated-scenes.json",
    "lip-sync.json",
)
REQUIRED_RECIPES = ("promo-video-recipe.json",)
SAMPLES = EXAMPLES / "samples"
REQUIRED_SAMPLES = ("example-accounting", "example-bookshop", "example-fitness", "example-plumbing")
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
        not any(clip["type"] in ("voiceover", "speech") for clip in clips_of(script, track["from"]))
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


def generates_a_scene_without_disclosure(script: Document) -> bool:
    return "disclosure" not in script and any(
        clip["type"] in ("generated_scene", "lip_sync") for track in script["tracks"] for clip in track.get("clips", [])
    )


def lip_sync_speaks_no_sound_track(script: Document) -> bool:
    return any(
        clip["voice"] == track["id"]
        or not any(sound["type"] in ("speech", "audio") for sound in clips_of(script, clip["voice"]))
        for track in script["tracks"]
        for clip in track.get("clips", [])
        if clip["type"] == "lip_sync"
    )


RULES: dict[str, Callable[[Document], bool]] = {
    "track-ids-unique": repeats_a_track_id,
    "captions-from-a-voiceover-track": captions_from_no_voiceover,
    "duck-under-another-track": ducks_under_itself_or_nothing,
    "video-out-after-in": video_ends_before_it_starts,
    "generated-scenes-need-disclosure": generates_a_scene_without_disclosure,
    "lip-sync-voice-is-a-sound-track": lip_sync_speaks_no_sound_track,
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
    same("output encodings", defs["output"]["properties"]["encode"]["enum"], members(types.EncodeMode))
    same("pattern of a provider id", defs["providerId"]["pattern"], types.PROVIDER_ID_PATTERN)
    same("pattern of a model id", defs["modelId"]["pattern"], types.MODEL_ID_PATTERN)
    same("pattern of a track id", defs["trackId"]["pattern"], types.TRACK_ID_PATTERN)
    same("pattern of a color", defs["hexColor"]["pattern"], types.HEX_COLOR_PATTERN)
    scene = defs["generatedScene"]["properties"]
    same("pattern of a resolution", scene["resolution"]["pattern"], types.RESOLUTION_PATTERN)
    same("pattern of an aspect ratio", scene["aspect_ratio"]["pattern"], types.ASPECT_RATIO_PATTERN)
    same("largest seed", scene["seed"]["maximum"], types.MAX_SEED)
    same("what a fallback can show", defs["sceneFallback"]["properties"]["type"]["enum"], members(types.FallbackType))
    lip_fallback = defs["lipFallback"]["properties"]["type"]["enum"]
    same("what shows instead of a lip_sync clip", lip_fallback, members(types.LipFallbackType))
    same("pattern of a lip_sync provider id", defs["lipSync"]["properties"]["provider"]["pattern"], types.PROVIDER_ID_PATTERN)
    same("pattern of a lip_sync model id", defs["lipSync"]["properties"]["model"]["pattern"], types.MODEL_ID_PATTERN)
    same("pattern of a lip_sync voice track", defs["lipSync"]["properties"]["voice"]["pattern"], types.TRACK_ID_PATTERN)
    same("schema version", schema["properties"]["schemaVersion"]["const"], types.SCHEMA_VERSION)
    same("rules checked next to the schema", list(RULES), members(types.ScriptRule))
    return problems


def template_schema_matches_types(schema: Document, script_schema: Document, types: ModuleType) -> list[str]:
    problems = []

    def same(what: str, in_schema: object, in_types: object) -> None:
        if in_schema != in_types:
            problems.append(
                f"{what}: schemas/video-template.v1.json says {json.dumps(in_schema)} and "
                f"src/ambitious_video/domain/types.py says {json.dumps(in_types)}. Change both together"
            )

    defs = schema["$defs"]
    names = schema["properties"]["inputs"]["propertyNames"]["pattern"]
    in_script = script_schema["$defs"]["template"]["properties"]
    same("template version", schema["properties"]["templateVersion"]["const"], types.TEMPLATE_VERSION)
    same("template version in a script", in_script["templateVersion"]["const"], types.TEMPLATE_VERSION)
    same("pattern of a template id", defs["templateId"]["pattern"], types.TEMPLATE_ID_PATTERN)
    same("pattern of a template id in a script", in_script["id"]["pattern"], types.TEMPLATE_ID_PATTERN)
    same("pattern of an input name", names, types.INPUT_NAME_PATTERN)
    same("input types", defs["input"]["properties"]["type"]["enum"], members(types.InputType))
    same("what a scene can show", defs["shown"]["properties"]["type"]["enum"], members(types.SceneType))
    same("pattern of a scene id", defs["sceneId"]["pattern"], types.TRACK_ID_PATTERN)
    same("pattern of a color", defs["hexColor"]["pattern"], types.HEX_COLOR_PATTERN)
    same("pattern of a provider id", defs["speech"]["properties"]["provider"]["pattern"], types.PROVIDER_ID_PATTERN)
    same("pattern of a model id", defs["speech"]["properties"]["model"]["pattern"], types.MODEL_ID_PATTERN)
    same("a generated scene in a template", list(defs["generatedScene"]["properties"]), list(script_schema["$defs"]["generatedScene"]["properties"]))
    same("a lip_sync clip in a template", list(defs["lipSync"]["properties"]), list(script_schema["$defs"]["lipSync"]["properties"]))
    same("what shows instead of a lip_sync clip in a template", defs["lipFallback"]["properties"]["type"]["enum"], members(types.LipFallbackType))
    for name in ("path", "width", "height", "fps"):
        in_template = defs["output"]["properties"][name]
        same(f"output {name}", in_template, script_schema["$defs"]["output"]["properties"][name])
    encodings = script_schema["$defs"]["output"]["properties"]["encode"]["enum"]
    same("output encodings", defs["output"]["properties"]["encode"]["enum"], encodings)
    return problems


def check_templates(
    validator: Draft202012Validator, script_validator: Draft202012Validator, load: Callable[[Path], Document]
) -> tuple[list[str], int]:
    from ambitious_video.domain.template import expand, load_template

    problems = []
    names = sorted(path.stem for path in TEMPLATES.glob("*.json"))
    for name in REQUIRED_TEMPLATES:
        if name not in names:
            problems.append(f"templates/{name}.json is missing")
    loaded = {}
    for name in names:
        where = f"templates/{name}.json"
        template = load(TEMPLATES / f"{name}.json")
        found = rejections(validator, template)
        if found:
            problems.append(f"{where}: must be accepted, but the schema rejects it by {told(found)}")
            continue
        parsed, faults = load_template(template)
        if parsed is None:
            problems.append(f"{where}: the schema accepts it, but expand refuses it: {faults[0].at} {faults[0].message}")
            continue
        if template["id"] != name:
            problems.append(f"{where}: its id must be {name}, the name of the file")
        packaged = PACKAGED_TEMPLATES / f"{name}.json"
        if not packaged.is_file() or packaged.read_bytes() != (TEMPLATES / f"{name}.json").read_bytes():
            problems.append(f"{where} and src/ambitious_video/templates/{name}.json differ. Copy one over the other")
        loaded[name] = parsed
    if sorted(path.stem for path in PACKAGED_TEMPLATES.glob("*.json")) != names:
        problems.append("templates/ and src/ambitious_video/templates/ must hold the same files")
    for slug in sorted(path.name for path in SAMPLES.iterdir() if path.is_dir()):
        where = f"examples/samples/{slug}"
        if not (SAMPLES / slug / "inputs.json").is_file() or not (SAMPLES / slug / "script.json").is_file():
            problems.append(f"{where}: needs inputs.json")
            continue
        inputs = load(SAMPLES / slug / "inputs.json")
        if inputs.get("template") not in loaded:
            problems.append(f"{where}/inputs.json: template must name a starter template in templates/")
            continue
        made = expand(loaded[inputs["template"]], inputs, load(SAMPLES / slug / "brand.json"))
        if made.script is None:
            first = made.problems[0]
            problems.append(f"{where}/inputs.json: expand refuses it: {first.document} {first.at} {first.message}")
        elif rejections(script_validator, dict(made.script)):
            problems.append(f"{where}: the expanded script does not match schemas/video-script.v2.json")
        elif made.script != load(SAMPLES / slug / "script.json"):
            problems.append(f"{where}/script.json is not what expand writes from inputs.json. Run expand again")
    return problems, len(names)


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
    validator: Draft202012Validator,
    recipe_validator: Draft202012Validator,
    load: Callable[[Path], Document],
    types: ModuleType,
) -> tuple[list[str], int, int]:
    from ambitious_video.domain.recipe import compile_recipe

    problems = []
    names = sorted(path.name for path in EXAMPLES.glob("*.json"))
    used = set()
    for name in (*REQUIRED_EXAMPLES, *REQUIRED_RECIPES):
        if name not in names:
            problems.append(f"examples/{name} is missing")
    recipes = 0
    scripts = 0
    for name in names:
        doc = load(EXAMPLES / name)
        if isinstance(doc, dict) and "dslVersion" in doc:
            recipes += 1
            found = rejections(recipe_validator, doc)
            if found:
                problems.append(f"examples/{name}: must be accepted, but the recipe schema rejects it by {told(found)}")
                continue
            compiled, faults = compile_recipe(doc)
            if compiled is None:
                problems.append(f"examples/{name}: compile_recipe refuses it: {faults[0]}")
                continue
            script = compiled
        else:
            scripts += 1
            script = doc
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
    return problems, scripts, recipes


def check_samples(validator: Draft202012Validator, load: Callable[[Path], Document]) -> tuple[list[str], int]:
    problems = []
    slugs = sorted(path.name for path in SAMPLES.iterdir() if path.is_dir())
    for slug in REQUIRED_SAMPLES:
        if slug not in slugs:
            problems.append(f"examples/samples/{slug} is missing")
    brand_only = {"$schema": DRAFT, "$ref": "#/$defs/brand", "$defs": validator.schema["$defs"]}
    brand_validator = validator.evolve(schema=brand_only)
    for slug in slugs:
        where = f"examples/samples/{slug}"
        missing = [name for name in ("script.json", "brand.json", "README.md") if not (SAMPLES / slug / name).is_file()]
        if missing:
            problems.append(f"{where}: needs {', '.join(missing)}")
            continue
        script = load(SAMPLES / slug / "script.json")
        brand = load(SAMPLES / slug / "brand.json")
        found = rejections(validator, script)
        if found:
            problems.append(f"{where}/script.json: must be accepted, but the schema rejects it by {told(found)}")
            continue
        if broken_rules(script):
            problems.append(f"{where}/script.json: must be accepted, but breaks {', '.join(broken_rules(script))}")
        if not isinstance(brand, dict) or list(brand_validator.iter_errors(brand)):
            problems.append(f"{where}/brand.json: must be a brand as the schema describes one")
        elif script.get("brand") != brand:
            problems.append(f"{where}: the brand block of script.json and brand.json differ. Change both together")
        if "SAMPLE DATA" not in (SAMPLES / slug / "README.md").read_text(encoding="utf-8"):
            problems.append(f"{where}/README.md: must say that the project is SAMPLE DATA")
    return problems, len(slugs)


def check_invalid(
    validator: Draft202012Validator, load: Callable[[Path], Document], types: ModuleType
) -> tuple[list[str], int]:
    problems = []
    names = sorted(path.name for path in INVALID.glob("*.json") if path.name != EXPECTED.name)
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

    sys.path.insert(0, str(REPO / "src"))
    from ambitious_video.domain import types

    def load(path: Path) -> Document:
        return json.loads(path.read_text(encoding="utf-8"))

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if schema.get("$schema") != DRAFT:
        stop(f"schemas/video-script.v2.json must declare {DRAFT}")
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    template_schema = json.loads(TEMPLATE_SCHEMA.read_text(encoding="utf-8"))
    if template_schema.get("$schema") != DRAFT:
        stop(f"schemas/video-template.v1.json must declare {DRAFT}")
    jsonschema.Draft202012Validator.check_schema(template_schema)
    template_validator = jsonschema.Draft202012Validator(template_schema)

    recipe_schema = json.loads(RECIPE_SCHEMA.read_text(encoding="utf-8"))
    if recipe_schema.get("$schema") != DRAFT:
        stop(f"schemas/video-recipe.v1.json must declare {DRAFT}")
    jsonschema.Draft202012Validator.check_schema(recipe_schema)
    recipe_validator = jsonschema.Draft202012Validator(recipe_schema)

    valid_problems, valid, recipes = check_valid(validator, recipe_validator, load, types)
    sample_problems, samples = check_samples(validator, load)
    invalid_problems, invalid = check_invalid(validator, load, types)
    template_problems, templates = check_templates(template_validator, validator, load)
    agreement = schema_matches_types(schema, types) + model_matches_types(types)
    agreement += template_schema_matches_types(template_schema, schema, types)
    problems = agreement + valid_problems + sample_problems + template_problems + invalid_problems
    for problem in problems:
        sys.stderr.write(f"FAIL: {problem}\n")
    if problems:
        sys.exit(1)
    report = [
        f"python {sys.version.split()[0]}, jsonschema {version('jsonschema')}",
        "ok  schemas/video-script.v2.json is a valid JSON Schema (draft 2020-12)",
        "ok  schemas/video-recipe.v1.json is a valid JSON Schema (draft 2020-12)",
        "ok  the schema and src/ambitious_video/domain/types.py agree on clip types, actions, transitions, "
        "output encodings, patterns and rules",
        "ok  the job states and moves in spec/RenderJob.tla and types.py agree",
        f"ok  {valid} script examples and {recipes} DSL v1 recipe examples accepted, and together they use every clip type",
        f"ok  {samples} sample projects accepted, each with the brand its brand.json names",
        "ok  schemas/video-template.v1.json is a valid JSON Schema (draft 2020-12) and agrees with types.py",
        f"ok  {templates} starter templates accepted, each the same as its packaged copy, "
        "and every sample script is what expand writes from its inputs",
        f"ok  {invalid} invalid examples rejected, each for the reason in examples/invalid/EXPECTED.json",
        "check_schemas: ok",
    ]
    sys.stdout.write("\n".join(report) + "\n")


main()
