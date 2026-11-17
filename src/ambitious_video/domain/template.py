from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import get_args

from ambitious_video.domain.load import (
    COLOR,
    MAX_LABEL,
    MAX_PATH,
    MAX_TEXT,
    MODEL,
    PROVIDER,
    RELATIVE,
    TEMPLATE,
    TRACK,
    Bag,
    as_brand,
    as_budget,
    as_clip,
    as_list,
    as_number,
    as_object,
    as_output,
    as_text,
    load_script,
    need,
    only_keys,
    say,
)
from ambitious_video.domain.types import (
    INPUT_NAME_PATTERN,
    SCHEMA_VERSION,
    TEMPLATE_VERSION,
    InputType,
    SceneType,
    ScriptProblem,
    UnknownStep,
)

SLOT = re.compile(r"\{\{\s*([a-z][a-z0-9_]{0,31})\s*\}\}")
BRACES = re.compile(r"\{\{|\}\}")
NAME = re.compile(INPUT_NAME_PATTERN)
INPUT_TYPES: tuple[str, ...] = get_args(InputType)
SCENE_TYPES: tuple[str, ...] = get_args(SceneType)
WORDS = frozenset({"text", "choice"})
PATHS = frozenset({"image", "video", "audio"})
NO_INPUT: frozenset[str] = frozenset()
FALLBACKS = {"speech": "captions", "music": "none"}
OUTPUT_KEYS = {"path", "width", "height", "fps", "encode"}
INPUT_KEYS = {"type", "required", "default", "options", "maxLength", "description"}
TOP_LEVEL = {
    "templateVersion",
    "id",
    "title",
    "description",
    "inputs",
    "brand",
    "output",
    "budget",
    "speech",
    "fallbacks",
    "music",
    "scenes",
    "endCard",
    "disclosure",
}
REQUIRED = ("templateVersion", "id", "title", "output", "scenes")
END_CARD = "end-card"
MAX_INPUTS = 64
MAX_SCENES = 64
MAX_LINES = 16
MAX_OPTIONS = 32
MAX_VOICE = 128
MAX_DESCRIPTION = 500
IN_TEMPLATE = "template"
IN_INPUTS = "inputs"
IN_BRAND = "brand"
IN_SCRIPT = "script"


@dataclass(frozen=True, slots=True)
class Input:
    name: str
    kind: str
    required: bool
    default: str | None
    options: tuple[str, ...]
    max_length: int
    description: str | None


@dataclass(frozen=True, slots=True)
class Line:
    at: float
    duration: float | None
    text: str


@dataclass(frozen=True, slots=True)
class Scene:
    id: str
    at: str
    show: Mapping[str, object]
    fallback: Mapping[str, object] | None
    lines: tuple[Line, ...]
    cut: float | None

    @property
    def show_at(self) -> str:
        return self.at if self.id == END_CARD else say(self.at, "/show")


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    title: str
    description: str | None
    inputs: Mapping[str, Input]
    brand_file: str | None
    brand: Mapping[str, object] | None
    output: Mapping[str, object]
    budget: Mapping[str, object] | None
    speech: Mapping[str, object] | None
    fallbacks: Mapping[str, str]
    music: Mapping[str, object] | None
    scenes: tuple[Scene, ...]
    disclosure: str | None


@dataclass(frozen=True, slots=True)
class Located:
    document: str
    at: str
    message: str


@dataclass(frozen=True, slots=True)
class Note:
    step: str
    scene: str | None
    fallback: str
    missing: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Expansion:
    script: Mapping[str, object] | None
    problems: tuple[Located, ...]
    notes: tuple[Note, ...]


def _message(problem: ScriptProblem) -> str:
    if isinstance(problem, UnknownStep):
        return say("Unknown step ", problem.step[:40], ".")
    return problem.message


def located(document: str, bag: Bag, strip: str = "") -> tuple[Located, ...]:
    return tuple(Located(document, item.at.removeprefix(strip), _message(item)) for item in bag.items)


def _value(bag: Bag, spec: Input, value: object, at: str) -> str | None:
    if spec.kind == "text":
        return as_text(bag, value, at, high=spec.max_length)
    if spec.kind in PATHS:
        return as_text(bag, value, at, pattern=RELATIVE, high=MAX_PATH)
    if spec.kind == "color":
        return as_text(bag, value, at, pattern=COLOR, high=MAX_LABEL)
    if spec.kind == "voice":
        return as_text(bag, value, at, high=MAX_VOICE)
    chosen = as_text(bag, value, at, high=MAX_LABEL)
    if chosen is not None and chosen not in spec.options:
        bag.schema(at, "This value is not one of the options.")
        return None
    return chosen


def _options(bag: Bag, value: object, at: str) -> tuple[str, ...]:
    listed = as_list(bag, value, at, low=1, high=MAX_OPTIONS)
    if listed is None:
        return ()
    texts = [as_text(bag, item, say(at, "/", str(index)), high=MAX_LABEL) for index, item in enumerate(listed)]
    options = tuple(item for item in texts if item is not None)
    if len(set(options)) != len(options):
        bag.schema(at, "Two options are the same.")
    return options


def _input(bag: Bag, name: str, value: object, at: str) -> Input | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, INPUT_KEYS, at)
    if not need(bag, raw, ("type",), at):
        return None
    kind = raw["type"]
    if not isinstance(kind, str) or kind not in INPUT_TYPES:
        bag.schema(say(at, "/type"), "This is not an input type.")
        return None
    required = raw.get("required", False)
    if not isinstance(required, bool):
        bag.schema(say(at, "/required"), "This value must be true or false.")
    options: tuple[str, ...] = ()
    if kind == "choice":
        if "options" in raw:
            options = _options(bag, raw["options"], say(at, "/options"))
        else:
            bag.schema(at, "A choice input needs options.")
    elif "options" in raw:
        bag.schema(say(at, "/options"), "Only a choice input has options.")
    max_length = MAX_TEXT
    if "maxLength" in raw and kind != "text":
        bag.schema(say(at, "/maxLength"), "Only a text input has maxLength.")
    elif "maxLength" in raw:
        counted = as_number(bag, raw["maxLength"], say(at, "/maxLength"), whole=True, low=1, high=MAX_TEXT)
        max_length = MAX_TEXT if counted is None else int(counted)
    description = None
    if "description" in raw:
        description = as_text(bag, raw["description"], say(at, "/description"), high=MAX_DESCRIPTION)
    spec = Input(name, kind, required is True, None, options, max_length, description)
    default = _value(bag, spec, raw["default"], say(at, "/default")) if "default" in raw else None
    return replace(spec, default=default)


def _inputs(bag: Bag, value: object, at: str) -> dict[str, Input]:
    found: dict[str, Input] = {}
    listed = as_object(bag, value, at)
    if listed is None:
        return found
    if len(listed) > MAX_INPUTS:
        bag.schema(at, "There are too many inputs.")
        return found
    for key, item in listed.items():
        where = say(at, "/", str(key))
        if not isinstance(key, str) or NAME.fullmatch(key) is None:
            bag.schema(where, "This name has the wrong shape.")
            continue
        spec = _input(bag, key, item, where)
        if spec is not None:
            found[key] = spec
    return found


def _slots(
    bag: Bag, text: object, at: str, inputs: Mapping[str, Input], kinds: frozenset[str], *, whole: bool = False
) -> None:
    if not isinstance(text, str):
        return
    names: list[str] = SLOT.findall(text)
    if BRACES.search(SLOT.sub("", text)) is not None:
        bag.schema(at, "This slot has the wrong shape.")
        return
    for name in dict.fromkeys(names):
        spec = inputs.get(name)
        if spec is None:
            bag.schema(at, say("Slot ", name, " names no input."))
        elif spec.kind not in kinds:
            bag.schema(at, say("Slot ", name, " has the wrong type."))
    if whole and names and SLOT.fullmatch(text) is None:
        bag.schema(at, "Use one whole slot or a plain value.")


def _shown(bag: Bag, value: object, at: str, inputs: Mapping[str, Input]) -> dict[str, object] | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    before = len(bag.items)
    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in SCENE_TYPES:
        bag.schema(say(at, "/type"), "Use title_card, image or video.")
        return None
    as_clip(bag, raw, at)
    if kind == "video" and "out" not in raw:
        bag.schema(at, "Field out is required.")
    _slots(bag, raw.get("text"), say(at, "/text"), inputs, WORDS)
    _slots(bag, raw.get("subtitle"), say(at, "/subtitle"), inputs, WORDS)
    _slots(bag, raw.get("path"), say(at, "/path"), inputs, frozenset({kind}), whole=True)
    return raw if len(bag.items) == before else None


def _lines(bag: Bag, value: object, at: str, inputs: Mapping[str, Input]) -> tuple[Line, ...]:
    listed = as_list(bag, value, at, low=1, high=MAX_LINES)
    found: list[Line] = []
    for index, item in enumerate(listed or []):
        where = say(at, "/", str(index))
        raw = as_object(bag, item, where)
        if raw is None:
            continue
        only_keys(bag, raw, {"at", "duration", "text"}, where)
        if not need(bag, raw, ("text",), where):
            continue
        text = as_text(bag, raw["text"], say(where, "/text"))
        _slots(bag, raw["text"], say(where, "/text"), inputs, WORDS)
        start = as_number(bag, raw["at"], say(where, "/at"), low=0) if "at" in raw else 0.0
        length = None
        if "duration" in raw:
            length = as_number(bag, raw["duration"], say(where, "/duration"), low=0, exclusive_low=True)
        if text is not None and start is not None:
            found.append(Line(start, length, text))
    return tuple(found)


def _cut(bag: Bag, value: object, at: str) -> float | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"transition", "duration"}, at)
    if not need(bag, raw, ("transition", "duration"), at):
        return None
    if raw["transition"] != "crossfade":
        bag.schema(say(at, "/transition"), "The transition must be crossfade.")
    return as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)


def _scene(bag: Bag, value: object, at: str, inputs: Mapping[str, Input]) -> Scene | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    before = len(bag.items)
    only_keys(bag, raw, {"id", "show", "fallback", "say", "cut"}, at)
    if not need(bag, raw, ("id", "show"), at):
        return None
    name = as_text(bag, raw["id"], say(at, "/id"), pattern=TRACK, high=32)
    show = _shown(bag, raw["show"], say(at, "/show"), inputs)
    fallback = _shown(bag, raw["fallback"], say(at, "/fallback"), inputs) if "fallback" in raw else None
    lines = _lines(bag, raw["say"], say(at, "/say"), inputs) if "say" in raw else ()
    cut = _cut(bag, raw["cut"], say(at, "/cut")) if "cut" in raw else None
    if name is None or show is None or len(bag.items) != before:
        return None
    return Scene(name, at, show, fallback, lines, cut)


def _end_card(bag: Bag, value: object, at: str, inputs: Mapping[str, Input]) -> Scene | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    before = len(bag.items)
    only_keys(bag, raw, {"duration", "text", "subtitle", "say"}, at)
    card: dict[str, object] = {"type": "title_card"}
    card.update({key: raw[key] for key in ("duration", "text", "subtitle") if key in raw})
    show = _shown(bag, card, at, inputs)
    lines = _lines(bag, raw["say"], say(at, "/say"), inputs) if "say" in raw else ()
    if show is None or len(bag.items) != before:
        return None
    return Scene(END_CARD, at, show, None, lines, None)


def _brand(
    bag: Bag, value: object, at: str, inputs: Mapping[str, Input]
) -> tuple[str | None, Mapping[str, object] | None]:
    raw = as_object(bag, value, at)
    if raw is None:
        return None, None
    if "file" in raw:
        only_keys(bag, raw, {"file"}, at)
        return as_text(bag, raw["file"], say(at, "/file"), pattern=RELATIVE, high=MAX_PATH), None
    only_keys(bag, raw, {"font", "colors", "logo"}, at)
    _slots(bag, raw.get("logo"), say(at, "/logo"), inputs, frozenset({"image"}), whole=True)
    _slots(bag, raw.get("font"), say(at, "/font"), inputs, NO_INPUT, whole=True)
    colors = raw.get("colors")
    if isinstance(colors, dict):
        for name, paint in colors.items():
            _slots(bag, paint, say(at, "/colors/", str(name)), inputs, frozenset({"color"}), whole=True)
    return None, raw


def _speech(bag: Bag, value: object, at: str, inputs: Mapping[str, Input]) -> Mapping[str, object] | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"provider", "model", "voice"}, at)
    if not need(bag, raw, ("provider", "model", "voice"), at):
        return None
    as_text(bag, raw["provider"], say(at, "/provider"), pattern=PROVIDER, high=32)
    as_text(bag, raw["model"], say(at, "/model"), pattern=MODEL, high=64)
    as_text(bag, raw["voice"], say(at, "/voice"), high=MAX_VOICE)
    _slots(bag, raw["voice"], say(at, "/voice"), inputs, frozenset({"voice"}), whole=True)
    return raw


def _fallbacks(bag: Bag, value: object, at: str) -> Mapping[str, str]:
    raw = as_object(bag, value, at)
    if raw is None:
        return {}
    only_keys(bag, raw, set(FALLBACKS), at)
    for name, wanted in FALLBACKS.items():
        if name in raw and raw[name] != wanted:
            bag.schema(say(at, "/", name), say("This fallback must be ", wanted, "."))
    return {name: wanted for name, wanted in FALLBACKS.items() if raw.get(name) == wanted}


def _music(bag: Bag, value: object, at: str, inputs: Mapping[str, Input]) -> Mapping[str, object] | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"path", "gain_db"}, at)
    if not need(bag, raw, ("path",), at):
        return None
    as_text(bag, raw["path"], say(at, "/path"), pattern=RELATIVE, high=MAX_PATH)
    _slots(bag, raw["path"], say(at, "/path"), inputs, frozenset({"audio"}), whole=True)
    if "gain_db" in raw:
        as_number(bag, raw["gain_db"], say(at, "/gain_db"), low=-60, high=12)
    return raw


def _scenes(bag: Bag, document: Mapping[str, object], inputs: Mapping[str, Input]) -> tuple[Scene, ...]:
    listed = as_list(bag, document["scenes"], "/scenes", low=1, high=MAX_SCENES)
    built = [_scene(bag, item, say("/scenes/", str(index)), inputs) for index, item in enumerate(listed or [])]
    if "endCard" in document:
        built.append(_end_card(bag, document["endCard"], "/endCard", inputs))
    scenes = tuple(item for item in built if item is not None)
    if len(scenes) != len(built):
        return scenes
    names = [scene.id for scene in scenes]
    if len(set(names)) != len(names):
        bag.schema("/scenes", "Two scenes use the same id.")
    if scenes and scenes[-1].cut is not None:
        bag.schema(say(scenes[-1].at, "/cut"), "The last scene cannot have a cut.")
    return scenes


def load_template(raw: object) -> tuple[Template | None, tuple[Located, ...]]:
    bag = Bag()
    document = as_object(bag, raw, "")
    if document is None:
        return None, located(IN_TEMPLATE, bag)
    only_keys(bag, document, TOP_LEVEL, "")
    version = document.get("templateVersion")
    if "templateVersion" in document and (version != TEMPLATE_VERSION or isinstance(version, bool)):
        bag.schema("/templateVersion", "templateVersion must be 1.")
    if not need(bag, document, REQUIRED, ""):
        return None, located(IN_TEMPLATE, bag)
    name = as_text(bag, document["id"], "/id", pattern=TEMPLATE, high=64)
    title = as_text(bag, document["title"], "/title", high=MAX_LABEL)
    description = None
    if "description" in document:
        description = as_text(bag, document["description"], "/description", high=MAX_DESCRIPTION)
    inputs = _inputs(bag, document["inputs"], "/inputs") if "inputs" in document else {}
    output = as_object(bag, document["output"], "/output")
    if output is not None:
        as_output(bag, output)
    budget = None
    if "budget" in document and as_budget(bag, document["budget"], "/budget") is not None:
        budget = as_object(bag, document["budget"], "/budget")
    brand_file, brand = _brand(bag, document["brand"], "/brand", inputs) if "brand" in document else (None, None)
    speech = _speech(bag, document["speech"], "/speech", inputs) if "speech" in document else None
    fallbacks = _fallbacks(bag, document["fallbacks"], "/fallbacks") if "fallbacks" in document else {}
    music = _music(bag, document["music"], "/music", inputs) if "music" in document else None
    disclosure = None
    if "disclosure" in document:
        disclosure = as_text(bag, document["disclosure"], "/disclosure", high=MAX_LABEL)
    scenes = _scenes(bag, document, inputs)
    if bag.items or name is None or title is None or output is None:
        return None, located(IN_TEMPLATE, bag)
    return (
        Template(
            name,
            title,
            description,
            inputs,
            brand_file,
            brand,
            output,
            budget,
            speech,
            fallbacks,
            music,
            scenes,
            disclosure,
        ),
        (),
    )


def _filled(value: object, values: Mapping[str, str], missing: list[str]) -> object:
    if isinstance(value, str):

        def swap(found: re.Match[str]) -> str:
            name = found.group(1)
            if name not in values and name not in missing:
                missing.append(name)
            return values.get(name, "")

        return SLOT.sub(swap, value)
    if isinstance(value, Mapping):
        return {key: _filled(item, values, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [_filled(item, values, missing) for item in value]
    return value


def _unfilled(bag: Bag, missing: list[str], at: str) -> None:
    for name in missing:
        bag.schema(at, say("Input ", name, " has no value."))


def _values(bag: Bag, template: Template, given: Mapping[str, object]) -> dict[str, str]:
    values = {name: spec.default for name, spec in template.inputs.items() if spec.default is not None}
    for name, value in given.items():
        where = say("/inputs/", str(name))
        spec = template.inputs.get(name) if isinstance(name, str) else None
        if spec is None:
            bag.schema(where, "This input is not in the template.")
            continue
        if value is None:
            continue
        checked = _value(bag, spec, value, where)
        if checked is not None:
            values[name] = checked
    for name, spec in template.inputs.items():
        if spec.required and name not in values and given.get(name) is None:
            bag.schema("/inputs", say("Input ", name, " is required."))
    return values


def _seconds(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _length(clip: object) -> float:
    if not isinstance(clip, Mapping):
        return 0.0
    if clip.get("type") == "video":
        return _seconds(clip.get("out")) - _seconds(clip.get("in"))
    return _seconds(clip.get("duration"))


def _spoken(start: float, line: Line, text: object) -> dict[str, object]:
    clip: dict[str, object] = {"at": round(start + line.at, 3)}
    if line.duration is not None:
        clip["duration"] = line.duration
    clip["text"] = text
    return clip


def _tracks(template: Template, values: Mapping[str, str], bag: Bag, notes: list[Note]) -> dict[str, object]:
    clips: list[object] = []
    spoken: list[dict[str, object]] = []
    cuts: list[object] = []
    start = 0.0
    for scene in template.scenes:
        missing: list[str] = []
        clip = _filled(scene.show, values, missing)
        where = scene.show_at
        if missing and scene.fallback is not None:
            lost = tuple(missing)
            missing = []
            clip = _filled(scene.fallback, values, missing)
            where = say(scene.at, "/fallback")
            if not missing:
                notes.append(Note("scene", scene.id, str(scene.fallback.get("type")), lost))
        _unfilled(bag, missing, where)
        clips.append(clip)
        for index, line in enumerate(scene.lines):
            missing = []
            spoken.append(_spoken(start, line, _filled(line.text, values, missing)))
            _unfilled(bag, missing, say(scene.at, "/say/", str(index), "/text"))
        start = round(start + _length(clip), 3)
        if scene.cut is not None:
            cuts.append({"at": start, "transition": "crossfade", "duration": scene.cut})
    tracks: list[object] = [{"id": "main", "clips": clips}]
    if spoken:
        voice: dict[str, object] = {"type": "voiceover"}
        if template.speech is not None:
            missing = []
            named = _filled(template.speech, values, missing)
            if not missing and isinstance(named, Mapping):
                voice = {"type": "speech", **named}
            elif template.fallbacks.get("speech") is not None:
                notes.append(Note("speech", None, template.fallbacks["speech"], tuple(missing)))
            else:
                _unfilled(bag, missing, "/speech/voice")
        kind = {"type": voice["type"]}
        rest = {key: item for key, item in voice.items() if key != "type"}
        tracks.append({"id": "voice", "clips": [{**kind, **line, **rest} for line in spoken]})
        tracks.append({"id": "captions", "from": "voice"})
    if template.music is not None:
        missing = []
        bed = _filled(template.music, values, missing)
        if not missing and isinstance(bed, Mapping):
            under = {"duck_under": "voice"} if spoken else {}
            tracks.append({"id": "music", "clips": [{"type": "audio", "at": 0, **bed, **under}]})
        elif template.fallbacks.get("music") is not None:
            notes.append(Note("music", None, template.fallbacks["music"], tuple(missing)))
        else:
            _unfilled(bag, missing, "/music/path")
    built: dict[str, object] = {"tracks": tracks}
    if cuts:
        built["cuts"] = cuts
    return built


def expand(template: Template, document: object, brand: object | None) -> Expansion:
    asked = Bag()
    raw = as_object(asked, document, "")
    if raw is None:
        return Expansion(None, located(IN_INPUTS, asked), ())
    only_keys(asked, raw, {"template", "inputs", "output"}, "")
    if "template" in raw and raw["template"] != template.id:
        asked.schema("/template", "The inputs name another template.")
    given = as_object(asked, raw["inputs"], "/inputs") if "inputs" in raw else {}
    values = _values(asked, template, given or {})
    output = dict(template.output)
    if "output" in raw:
        chosen = as_object(asked, raw["output"], "/output")
        if chosen is not None:
            only_keys(asked, chosen, OUTPUT_KEYS, "/output")
            output.update(chosen)
    if asked.items:
        return Expansion(None, located(IN_INPUTS, asked), ())
    wanted = Bag()
    branded = Bag()
    notes: list[Note] = []
    script: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "template": {"id": template.id, "templateVersion": TEMPLATE_VERSION},
    }
    if template.budget is not None:
        script["budget"] = dict(template.budget)
    if template.disclosure is not None:
        script["disclosure"] = template.disclosure
    script["output"] = output
    if template.brand_file is not None:
        as_brand(branded, brand)
        script["brand"] = brand
    elif template.brand is not None:
        missing: list[str] = []
        script["brand"] = _filled(template.brand, values, missing)
        _unfilled(wanted, missing, "/brand")
    script.update(_tracks(template, values, wanted, notes))
    problems = (*located(IN_TEMPLATE, wanted), *located(IN_BRAND, branded, "/brand"))
    if problems:
        return Expansion(None, problems, ())
    _loaded, faults = load_script(script)
    if faults:
        checked = Bag()
        checked.items.extend(faults)
        return Expansion(None, located(IN_SCRIPT, checked), ())
    return Expansion(script, (), tuple(notes))
