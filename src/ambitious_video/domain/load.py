from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal

from ambitious_video.domain.types import (
    ASPECT_RATIO_PATTERN,
    HEX_COLOR_PATTERN,
    MAX_SEED,
    MODEL_ID_PATTERN,
    MOUTH_COLOR,
    PROVIDER_ID_PATTERN,
    RESOLUTION_PATTERN,
    SCHEMA_VERSION,
    TEMPLATE_ID_PATTERN,
    TEMPLATE_VERSION,
    TRACK_ID_PATTERN,
    Action,
    AudioClip,
    Brand,
    BrandColors,
    CaptionsTrack,
    Click,
    Clip,
    ClipTrack,
    Cut,
    EncodeMode,
    GeneratedScene,
    HexColor,
    ImageClip,
    KenBurns,
    LipSync,
    MouthShape,
    Output,
    RelativePath,
    RuleProblem,
    SceneFallback,
    SchemaProblem,
    ScreenRecording,
    Script,
    ScriptProblem,
    ScriptRule,
    Scroll,
    Speech,
    TemplateRef,
    TitleCard,
    Track,
    TrackId,
    UnknownStep,
    VideoClip,
    Voiceover,
    Wait,
    WebAddress,
)

RELATIVE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).+$")
WEB = re.compile(r"^https?://[^\s]+$")
TRACK = re.compile(TRACK_ID_PATTERN)
COLOR = re.compile(HEX_COLOR_PATTERN)
PROVIDER = re.compile(PROVIDER_ID_PATTERN)
MODEL = re.compile(MODEL_ID_PATTERN)
TEMPLATE = re.compile(TEMPLATE_ID_PATTERN)
RESOLUTION = re.compile(RESOLUTION_PATTERN)
ASPECT_RATIO = re.compile(ASPECT_RATIO_PATTERN)
CLIP_TYPES = (
    "title_card",
    "screen_recording",
    "video",
    "image",
    "voiceover",
    "speech",
    "audio",
    "generated_scene",
    "lip_sync",
)
FALLBACK_KEYS = {
    "title_card": {"type", "text", "subtitle"},
    "image": {"type", "path", "kenburns"},
    "video": {"type", "path", "in"},
}
FALLBACK_NEEDS = {"title_card": ("text",), "image": ("path",), "video": ("path",)}
SCENE_KEYS = {
    "type",
    "provider",
    "model",
    "prompt",
    "duration",
    "resolution",
    "aspect_ratio",
    "seed",
    "image",
    "kenburns",
    "fallback",
}
LIP_KEYS = {"type", "provider", "model", "duration", "video", "image", "voice", "fallback"}
MOUTH_KEYS = {"type", "sprite", "x", "y", "width", "height", "color"}
MOUTH_NEEDS = ("sprite", "x", "y", "width", "height")
SPOKEN = frozenset({"voiceover", "speech"})
MAX_TEXT = 2000
MAX_PATH = 1024
MAX_URL = 2048
MAX_LABEL = 200
MAX_BUDGET_USD = 1000000
TOP_LEVEL = {"schemaVersion", "template", "budget", "disclosure", "output", "brand", "cache", "tracks", "cuts"}


def say(*parts: str) -> str:
    return "".join(parts)


class Bag:
    def __init__(self) -> None:
        self.items: list[ScriptProblem] = []

    def add(self, problem: ScriptProblem) -> None:
        self.items.append(problem)

    def schema(self, at: str, message: str) -> None:
        self.add(SchemaProblem(at, message))

    def unknown(self, at: str, step: str) -> None:
        self.add(UnknownStep(at, step))

    def rule(self, rule: ScriptRule, at: str, message: str) -> None:
        self.add(RuleProblem(rule, at, message))


def as_object(bag: Bag, value: object, at: str) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    bag.schema(at, "This value must be an object.")
    return None


def only_keys(bag: Bag, value: Mapping[str, object], allowed: set[str], at: str) -> None:
    for key in value:
        if key not in allowed:
            bag.schema(say(at, "/", str(key)), "This field is not allowed.")


def need(bag: Bag, value: Mapping[str, object], names: tuple[str, ...], at: str) -> bool:
    missing = [name for name in names if name not in value]
    for name in missing:
        bag.schema(at, say("Field ", name, " is required."))
    return not missing


def as_text(
    bag: Bag,
    value: object,
    at: str,
    *,
    pattern: re.Pattern[str] | None = None,
    low: int = 1,
    high: int = MAX_TEXT,
) -> str | None:
    if not isinstance(value, str):
        bag.schema(at, "This value must be text.")
        return None
    if not low <= len(value) <= high:
        bag.schema(at, "This text is empty or too long.")
        return None
    if pattern is not None and pattern.fullmatch(value) is None:
        bag.schema(at, "This text has the wrong shape.")
        return None
    return value


def as_number(
    bag: Bag,
    value: object,
    at: str,
    *,
    whole: bool = False,
    low: float | None = None,
    high: float | None = None,
    exclusive_low: bool = False,
    multiple: int | None = None,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        bag.schema(at, "This value must be a number.")
        return None
    if whole and not isinstance(value, int):
        bag.schema(at, "This value must be a whole number.")
        return None
    if low is not None and (value < low or (exclusive_low and value <= low)):
        bag.schema(at, "This number is too small.")
        return None
    if high is not None and value > high:
        bag.schema(at, "This number is too large.")
        return None
    if multiple is not None and int(value) % multiple != 0:
        bag.schema(at, "This number must be even.")
        return None
    return float(value)


def as_list(bag: Bag, value: object, at: str, *, low: int, high: int) -> list[object] | None:
    if not isinstance(value, list):
        bag.schema(at, "This value must be a list.")
        return None
    if not low <= len(value) <= high:
        bag.schema(at, "This list is the wrong length.")
        return None
    return value


def _path(bag: Bag, value: object, at: str) -> RelativePath | None:
    text = as_text(bag, value, at, pattern=RELATIVE, high=MAX_PATH)
    return None if text is None else RelativePath(text)


def as_output(bag: Bag, value: object) -> Output | None:
    raw = as_object(bag, value, "/output")
    if raw is None:
        return None
    only_keys(bag, raw, {"path", "width", "height", "fps", "encode"}, "/output")
    if not need(bag, raw, ("path", "width", "height", "fps"), "/output"):
        return None
    path = _path(bag, raw.get("path"), "/output/path")
    width = as_number(bag, raw.get("width"), "/output/width", whole=True, low=16, high=7680, multiple=2)
    height = as_number(bag, raw.get("height"), "/output/height", whole=True, low=16, high=4320, multiple=2)
    fps = as_number(bag, raw.get("fps"), "/output/fps", whole=True, low=1, high=120)
    encode: EncodeMode = "web"
    if "encode" in raw:
        chosen = raw["encode"]
        if chosen == "lossless":
            encode = "lossless"
        elif chosen != "web":
            bag.schema("/output/encode", "encode must be web or lossless.")
            return None
    if path is None or width is None or height is None or fps is None:
        return None
    return Output(path, int(width), int(height), int(fps), encode)


def as_brand(bag: Bag, value: object) -> Brand | None:
    raw = as_object(bag, value, "/brand")
    if raw is None:
        return None
    only_keys(bag, raw, {"font", "colors", "logo"}, "/brand")
    font = as_text(bag, raw["font"], "/brand/font", high=128) if "font" in raw else None
    logo = _path(bag, raw["logo"], "/brand/logo") if "logo" in raw else None
    colors: BrandColors | None = None
    if "colors" in raw:
        block = as_object(bag, raw["colors"], "/brand/colors")
        if block is not None:
            only_keys(bag, block, {"bg", "fg", "accent"}, "/brand/colors")
            if need(bag, block, ("bg", "fg", "accent"), "/brand/colors"):
                paints = [
                    as_text(bag, block[name], say("/brand/colors/", name), pattern=COLOR, low=7, high=7)
                    for name in ("bg", "fg", "accent")
                ]
                if all(item is not None for item in paints):
                    bg, fg, accent = paints
                    colors = BrandColors(HexColor(bg or ""), HexColor(fg or ""), HexColor(accent or ""))
    if bag.items:
        return None
    return Brand(font, colors, logo)


def _cache(bag: Bag, value: object) -> RelativePath | None:
    raw = as_object(bag, value, "/cache")
    if raw is None:
        return None
    only_keys(bag, raw, {"dir"}, "/cache")
    if not need(bag, raw, ("dir",), "/cache"):
        return None
    return _path(bag, raw.get("dir"), "/cache/dir")


def as_template(bag: Bag, value: object, at: str) -> TemplateRef | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"id", "templateVersion"}, at)
    if not need(bag, raw, ("id", "templateVersion"), at):
        return None
    name = as_text(bag, raw["id"], say(at, "/id"), pattern=TEMPLATE, high=64)
    if raw["templateVersion"] != TEMPLATE_VERSION or isinstance(raw["templateVersion"], bool):
        bag.schema(say(at, "/templateVersion"), "templateVersion must be 1.")
        return None
    return None if name is None else TemplateRef(name, TEMPLATE_VERSION)


def as_budget(bag: Bag, value: object, at: str) -> Decimal | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"maxUsd"}, at)
    if not need(bag, raw, ("maxUsd",), at):
        return None
    limit = raw["maxUsd"]
    if as_number(bag, limit, say(at, "/maxUsd"), low=0, high=MAX_BUDGET_USD, exclusive_low=True) is None:
        return None
    return Decimal(str(limit))


def _action(bag: Bag, value: object, at: str) -> Action | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"click", "wait", "scroll"}, at)
    if len(raw) != 1:
        bag.schema(at, "An action needs exactly one kind.")
        return None
    if set(raw) != {"click"} and set(raw) != {"wait"} and set(raw) != {"scroll"}:
        return None
    if "click" in raw:
        selector = as_text(bag, raw["click"], say(at, "/click"), high=512)
        return None if selector is None else Click(selector)
    if "wait" in raw:
        seconds = as_number(bag, raw["wait"], say(at, "/wait"), low=0, exclusive_low=True)
        return None if seconds is None else Wait(seconds)
    pixels = as_number(bag, raw["scroll"], say(at, "/scroll"), whole=True)
    return None if pixels is None else Scroll(int(pixels))


def _kenburns(bag: Bag, value: object, at: str) -> KenBurns | None:
    block = as_object(bag, value, at)
    if block is None:
        return None
    only_keys(bag, block, {"from", "to"}, at)
    if not need(bag, block, ("from", "to"), at):
        return None
    zoom_from = as_number(bag, block["from"], say(at, "/from"), low=1, high=4)
    zoom_to = as_number(bag, block["to"], say(at, "/to"), low=1, high=4)
    if zoom_from is None or zoom_to is None:
        return None
    return KenBurns(zoom_from, zoom_to)


def _scene_fallback(bag: Bag, value: object, at: str, duration: float) -> SceneFallback | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    kind = raw.get("type")
    if not isinstance(kind, str) or kind not in FALLBACK_KEYS:
        bag.schema(say(at, "/type"), "Use title_card, image or video.")
        return None
    before = len(bag.items)
    only_keys(bag, raw, FALLBACK_KEYS[kind], at)
    if not need(bag, raw, FALLBACK_NEEDS[kind], at):
        return None
    if kind == "title_card":
        text = as_text(bag, raw["text"], say(at, "/text"))
        subtitle = as_text(bag, raw["subtitle"], say(at, "/subtitle")) if "subtitle" in raw else None
        if text is None or len(bag.items) != before:
            return None
        return TitleCard(duration, text, subtitle)
    path = _path(bag, raw["path"], say(at, "/path"))
    if kind == "image":
        zoom = _kenburns(bag, raw["kenburns"], say(at, "/kenburns")) if "kenburns" in raw else None
        if path is None or len(bag.items) != before:
            return None
        return ImageClip(path, duration, zoom)
    start = as_number(bag, raw["in"], say(at, "/in"), low=0) if "in" in raw else None
    if path is None or len(bag.items) != before:
        return None
    return VideoClip(path, start, (start or 0.0) + duration)


def _generated(bag: Bag, raw: Mapping[str, object], at: str) -> GeneratedScene | None:
    before = len(bag.items)
    provider = as_text(bag, raw["provider"], say(at, "/provider"), pattern=PROVIDER, high=32)
    model = as_text(bag, raw["model"], say(at, "/model"), pattern=MODEL, high=64)
    prompt = as_text(bag, raw["prompt"], say(at, "/prompt"))
    duration = as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
    resolution = None
    if "resolution" in raw:
        resolution = as_text(bag, raw["resolution"], say(at, "/resolution"), pattern=RESOLUTION, high=5)
    aspect = None
    if "aspect_ratio" in raw:
        aspect = as_text(bag, raw["aspect_ratio"], say(at, "/aspect_ratio"), pattern=ASPECT_RATIO, high=9)
    seed = as_number(bag, raw["seed"], say(at, "/seed"), whole=True, low=0, high=MAX_SEED) if "seed" in raw else None
    image = _path(bag, raw["image"], say(at, "/image")) if "image" in raw else None
    zoom = _kenburns(bag, raw["kenburns"], say(at, "/kenburns")) if "kenburns" in raw else None
    if provider is None or model is None or prompt is None or duration is None:
        return None
    fallback = _scene_fallback(bag, raw["fallback"], say(at, "/fallback"), duration)
    if fallback is None or len(bag.items) != before:
        return None
    return GeneratedScene(
        duration,
        prompt,
        provider,
        model,
        resolution,
        aspect,
        None if seed is None else int(seed),
        image,
        zoom,
        fallback,
    )


def _mouth(bag: Bag, value: object, at: str) -> MouthShape | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    kind = raw.get("type")
    if kind != "none" and kind != "mouth":
        bag.schema(say(at, "/type"), "Use none or mouth.")
        return None
    before = len(bag.items)
    only_keys(bag, raw, MOUTH_KEYS if kind == "mouth" else {"type"}, at)
    if kind == "none" or not need(bag, raw, MOUTH_NEEDS, at):
        return None
    sprite = _path(bag, raw["sprite"], say(at, "/sprite"))
    middle = [as_number(bag, raw[name], say(at, "/", name), low=0, high=1) for name in ("x", "y")]
    size = [
        as_number(bag, raw[name], say(at, "/", name), low=0, high=1, exclusive_low=True)
        for name in ("width", "height")
    ]
    color: str | None = MOUTH_COLOR
    if "color" in raw:
        color = as_text(bag, raw["color"], say(at, "/color"), pattern=COLOR, low=7, high=7)
    if sprite is None or color is None or len(bag.items) != before:
        return None
    return MouthShape(sprite, middle[0] or 0.0, middle[1] or 0.0, size[0] or 0.0, size[1] or 0.0, HexColor(color))


def _lip_sync(bag: Bag, raw: Mapping[str, object], at: str) -> LipSync | None:
    before = len(bag.items)
    provider = as_text(bag, raw["provider"], say(at, "/provider"), pattern=PROVIDER, high=32)
    model = as_text(bag, raw["model"], say(at, "/model"), pattern=MODEL, high=64)
    duration = as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
    voice = as_text(bag, raw["voice"], say(at, "/voice"), pattern=TRACK, high=32)
    if ("video" in raw) == ("image" in raw):
        bag.schema(at, "Give one of video or image.")
    video = _path(bag, raw["video"], say(at, "/video")) if "video" in raw else None
    image = _path(bag, raw["image"], say(at, "/image")) if "image" in raw else None
    mouth = _mouth(bag, raw["fallback"], say(at, "/fallback")) if "fallback" in raw else None
    if provider is None or model is None or duration is None or voice is None or len(bag.items) != before:
        return None
    return LipSync(duration, provider, model, video, image, TrackId(voice), mouth)


def as_clip(bag: Bag, value: object, at: str) -> Clip | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    if "type" not in raw:
        bag.schema(at, "Field type is required.")
        return None
    kind = raw["type"]
    if not isinstance(kind, str):
        bag.schema(say(at, "/type"), "This value must be text.")
        return None
    if kind not in CLIP_TYPES:
        bag.unknown(say(at, "/type"), kind)
        return None
    allowed = {
        "title_card": {"type", "duration", "text", "subtitle"},
        "screen_recording": {"type", "url", "duration", "actions"},
        "video": {"type", "path", "in", "out"},
        "image": {"type", "path", "duration", "kenburns"},
        "voiceover": {"type", "at", "text", "duration"},
        "speech": {"type", "at", "text", "duration", "provider", "model", "voice"},
        "audio": {"type", "path", "at", "gain_db", "duck_under"},
        "generated_scene": SCENE_KEYS,
        "lip_sync": LIP_KEYS,
    }[kind]
    required = {
        "title_card": ("duration", "text"),
        "screen_recording": ("url", "duration"),
        "video": ("path",),
        "image": ("path", "duration"),
        "voiceover": ("at", "text"),
        "speech": ("at", "text", "provider", "model", "voice"),
        "audio": ("path", "at"),
        "generated_scene": ("provider", "model", "prompt", "duration", "fallback"),
        "lip_sync": ("provider", "model", "duration", "voice"),
    }[kind]
    only_keys(bag, raw, allowed, at)
    if not need(bag, raw, required, at):
        return None
    if kind == "generated_scene":
        return _generated(bag, raw, at)
    if kind == "lip_sync":
        return _lip_sync(bag, raw, at)
    if kind == "title_card":
        duration = as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        text = as_text(bag, raw["text"], say(at, "/text"))
        subtitle = as_text(bag, raw["subtitle"], say(at, "/subtitle")) if "subtitle" in raw else None
        if duration is None or text is None or ("subtitle" in raw and subtitle is None):
            return None
        return TitleCard(duration, text, subtitle)
    if kind == "screen_recording":
        url = as_text(bag, raw["url"], say(at, "/url"), pattern=WEB, high=MAX_URL)
        duration = as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        actions: tuple[Action, ...] = ()
        if "actions" in raw:
            listed = as_list(bag, raw["actions"], say(at, "/actions"), low=0, high=64)
            if listed is None or url is None or duration is None:
                return None
            built = [_action(bag, item, say(at, "/actions/", str(index))) for index, item in enumerate(listed)]
            if any(item is None for item in built):
                return None
            actions = tuple(item for item in built if item is not None)
        if url is None or duration is None:
            return None
        return ScreenRecording(WebAddress(url), duration, actions)
    if kind == "video":
        path = _path(bag, raw["path"], say(at, "/path"))
        start = as_number(bag, raw["in"], say(at, "/in"), low=0) if "in" in raw else None
        end = as_number(bag, raw["out"], say(at, "/out"), low=0, exclusive_low=True) if "out" in raw else None
        if path is None or ("in" in raw and start is None) or ("out" in raw and end is None):
            return None
        return VideoClip(path, start, end)
    if kind == "image":
        path = _path(bag, raw["path"], say(at, "/path"))
        duration = as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        kenburns: KenBurns | None = None
        if "kenburns" in raw:
            block = as_object(bag, raw["kenburns"], say(at, "/kenburns"))
            if block is None or path is None or duration is None:
                return None
            only_keys(bag, block, {"from", "to"}, say(at, "/kenburns"))
            if not need(bag, block, ("from", "to"), say(at, "/kenburns")):
                return None
            zoom_from = as_number(bag, block["from"], say(at, "/kenburns/from"), low=1, high=4)
            zoom_to = as_number(bag, block["to"], say(at, "/kenburns/to"), low=1, high=4)
            if zoom_from is None or zoom_to is None:
                return None
            kenburns = KenBurns(zoom_from, zoom_to)
        if path is None or duration is None:
            return None
        return ImageClip(path, duration, kenburns)
    if kind in SPOKEN:
        at_time = as_number(bag, raw["at"], say(at, "/at"), low=0)
        text = as_text(bag, raw["text"], say(at, "/text"))
        duration = None
        if "duration" in raw:
            duration = as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        if at_time is None or text is None or ("duration" in raw and duration is None):
            return None
        if kind == "voiceover":
            return Voiceover(at_time, text, duration)
        provider = as_text(bag, raw["provider"], say(at, "/provider"), pattern=PROVIDER, high=32)
        model = as_text(bag, raw["model"], say(at, "/model"), pattern=MODEL, high=64)
        voice = as_text(bag, raw["voice"], say(at, "/voice"), high=128)
        if provider is None or model is None or voice is None:
            return None
        return Speech(at_time, text, duration, provider, model, voice)
    path = _path(bag, raw["path"], say(at, "/path"))
    at_time = as_number(bag, raw["at"], say(at, "/at"), low=0)
    gain = as_number(bag, raw["gain_db"], say(at, "/gain_db"), low=-60, high=12) if "gain_db" in raw else None
    duck: TrackId | None = None
    if "duck_under" in raw:
        named = as_text(bag, raw["duck_under"], say(at, "/duck_under"), pattern=TRACK, high=32)
        duck = None if named is None else TrackId(named)
    if path is None or at_time is None or ("gain_db" in raw and gain is None) or ("duck_under" in raw and duck is None):
        return None
    return AudioClip(path, at_time, gain, duck)


def _track(bag: Bag, value: object, at: str) -> Track | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"id", "clips", "from"}, at)
    if "id" not in raw:
        bag.schema(at, "Field id is required.")
        return None
    track_id = as_text(bag, raw["id"], say(at, "/id"), pattern=TRACK, high=32)
    has_clips = "clips" in raw
    has_from = "from" in raw
    if has_clips == has_from:
        bag.schema(at, "A track needs clips or from.")
        return None
    if track_id is None:
        return None
    if has_from:
        source = as_text(bag, raw["from"], say(at, "/from"), pattern=TRACK, high=32)
        return None if source is None else CaptionsTrack(TrackId(track_id), TrackId(source))
    listed = as_list(bag, raw["clips"], say(at, "/clips"), low=1, high=512)
    if listed is None:
        return None
    clips = [as_clip(bag, item, say(at, "/clips/", str(index))) for index, item in enumerate(listed)]
    if any(item is None for item in clips):
        return None
    return ClipTrack(TrackId(track_id), tuple(item for item in clips if item is not None))


def _cut(bag: Bag, value: object, at: str) -> Cut | None:
    raw = as_object(bag, value, at)
    if raw is None:
        return None
    only_keys(bag, raw, {"at", "transition", "duration"}, at)
    if not need(bag, raw, ("at", "transition", "duration"), at):
        return None
    when = as_number(bag, raw["at"], say(at, "/at"), low=0)
    if raw.get("transition") != "crossfade":
        bag.schema(say(at, "/transition"), "The transition must be crossfade.")
    duration = as_number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
    if when is None or duration is None or raw.get("transition") != "crossfade":
        return None
    return Cut(when, "crossfade", duration)


def _rules(bag: Bag, script: Script) -> None:
    every = [clip for track in script.tracks if isinstance(track, ClipTrack) for clip in track.clips]
    if script.disclosure is None and any(isinstance(clip, GeneratedScene | LipSync) for clip in every):
        bag.rule(
            "generated-scenes-need-disclosure",
            "",
            "A generated scene needs disclosure text.",
        )
    ids = [track.id for track in script.tracks]
    if len(set(ids)) != len(ids):
        bag.rule("track-ids-unique", "/tracks", "Two tracks use the same id.")
    by_id = {track.id: track for track in script.tracks}
    for index, track in enumerate(script.tracks):
        if isinstance(track, CaptionsTrack):
            source = by_id.get(track.source)
            clips = source.clips if isinstance(source, ClipTrack) else ()
            if not any(isinstance(clip, Voiceover | Speech) for clip in clips):
                bag.rule(
                    "captions-from-a-voiceover-track",
                    say("/tracks/", str(index), "/from"),
                    "Captions must come from a voice track.",
                )
        if isinstance(track, ClipTrack):
            for clip_index, clip in enumerate(track.clips):
                if isinstance(clip, AudioClip) and clip.duck_under is not None:
                    if clip.duck_under == track.id or clip.duck_under not in by_id:
                        bag.rule(
                            "duck-under-another-track",
                            say("/tracks/", str(index), "/clips/", str(clip_index)),
                            "duck_under must name another track.",
                        )
                if isinstance(clip, LipSync):
                    heard = by_id.get(clip.voice)
                    sounds = heard.clips if isinstance(heard, ClipTrack) and heard.id != track.id else ()
                    if not any(isinstance(sound, Speech | AudioClip) for sound in sounds):
                        bag.rule(
                            "lip-sync-voice-is-a-sound-track",
                            say("/tracks/", str(index), "/clips/", str(clip_index), "/voice"),
                            "voice must name a track with sound.",
                        )
                if isinstance(clip, VideoClip) and clip.end is not None and clip.end <= (clip.start or 0):
                    bag.rule(
                        "video-out-after-in",
                        say("/tracks/", str(index), "/clips/", str(clip_index)),
                        "out must be later than in.",
                    )


def load_script(raw: object) -> tuple[Script, tuple[ScriptProblem, ...]] | tuple[None, tuple[ScriptProblem, ...]]:
    bag = Bag()
    document = as_object(bag, raw, "")
    if document is None:
        return None, tuple(bag.items)
    only_keys(bag, document, TOP_LEVEL, "")
    if "schemaVersion" not in document:
        bag.schema("", "Field schemaVersion is required.")
    elif document.get("schemaVersion") != SCHEMA_VERSION:
        bag.schema("/schemaVersion", "schemaVersion must be 2.")
    if "output" not in document or "tracks" not in document:
        if "output" not in document:
            bag.schema("", "Field output is required.")
        if "tracks" not in document:
            bag.schema("", "Field tracks is required.")
        return None, tuple(bag.items)
    output = as_output(bag, document["output"])
    brand = as_brand(bag, document["brand"]) if "brand" in document else None
    cache_dir = _cache(bag, document["cache"]) if "cache" in document else None
    template = as_template(bag, document["template"], "/template") if "template" in document else None
    budget = as_budget(bag, document["budget"], "/budget") if "budget" in document else None
    disclosure = None
    if "disclosure" in document:
        disclosure = as_text(bag, document["disclosure"], "/disclosure", high=MAX_LABEL)
    listed = as_list(bag, document["tracks"], "/tracks", low=1, high=16)
    tracks: list[Track] = []
    if listed is not None:
        built = [_track(bag, item, say("/tracks/", str(index))) for index, item in enumerate(listed)]
        if any(item is None for item in built):
            tracks = []
        else:
            tracks = [item for item in built if item is not None]
    cuts: list[Cut] = []
    if "cuts" in document:
        listed_cuts = as_list(bag, document["cuts"], "/cuts", low=0, high=256)
        if listed_cuts is not None:
            built_cuts = [_cut(bag, item, say("/cuts/", str(index))) for index, item in enumerate(listed_cuts)]
            if any(item is None for item in built_cuts):
                cuts = []
            else:
                cuts = [item for item in built_cuts if item is not None]
    if bag.items or output is None:
        return None, tuple(bag.items)
    script = Script(output, brand, tuple(tracks), tuple(cuts), cache_dir, template, budget, disclosure)
    _rules(bag, script)
    if bag.items:
        return None, tuple(bag.items)
    return script, ()
