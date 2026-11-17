from __future__ import annotations

import re
from collections.abc import Mapping

from ambitious_video.domain.types import (
    HEX_COLOR_PATTERN,
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
    HexColor,
    ImageClip,
    KenBurns,
    Output,
    RelativePath,
    RuleProblem,
    SchemaProblem,
    ScreenRecording,
    Script,
    ScriptProblem,
    ScriptRule,
    Scroll,
    TitleCard,
    Track,
    TrackId,
    UnknownStep,
    VideoClip,
    Voice,
    Voiceover,
    Wait,
    WebAddress,
)

RELATIVE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$)).+$")
WEB = re.compile(r"^https?://[^\s]+$")
TRACK = re.compile(TRACK_ID_PATTERN)
COLOR = re.compile(HEX_COLOR_PATTERN)
CLIP_TYPES = ("title_card", "screen_recording", "video", "image", "voiceover", "audio")
MAX_TEXT = 2000
MAX_PATH = 1024
MAX_URL = 2048


def say(*parts: str) -> str:
    return "".join(parts)


class _Bag:
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


def _object(bag: _Bag, value: object, at: str) -> dict[str, object] | None:
    if isinstance(value, dict):
        return value
    bag.schema(at, "This value must be an object.")
    return None


def _keys(bag: _Bag, value: Mapping[str, object], allowed: set[str], at: str) -> None:
    for key in value:
        if key not in allowed:
            bag.schema(say(at, "/", key), "This field is not allowed.")


def _need(bag: _Bag, value: Mapping[str, object], names: tuple[str, ...], at: str) -> bool:
    missing = [name for name in names if name not in value]
    for name in missing:
        bag.schema(at, say("Field ", name, " is required."))
    return not missing


def _text(
    bag: _Bag,
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


def _number(
    bag: _Bag,
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


def _list(bag: _Bag, value: object, at: str, *, low: int, high: int) -> list[object] | None:
    if not isinstance(value, list):
        bag.schema(at, "This value must be a list.")
        return None
    if not low <= len(value) <= high:
        bag.schema(at, "This list is the wrong length.")
        return None
    return value


def _path(bag: _Bag, value: object, at: str) -> RelativePath | None:
    text = _text(bag, value, at, pattern=RELATIVE, high=MAX_PATH)
    return None if text is None else RelativePath(text)


def _output(bag: _Bag, value: object) -> Output | None:
    raw = _object(bag, value, "/output")
    if raw is None:
        return None
    _keys(bag, raw, {"path", "width", "height", "fps"}, "/output")
    if not _need(bag, raw, ("path", "width", "height", "fps"), "/output"):
        return None
    path = _path(bag, raw.get("path"), "/output/path")
    width = _number(bag, raw.get("width"), "/output/width", whole=True, low=16, high=7680, multiple=2)
    height = _number(bag, raw.get("height"), "/output/height", whole=True, low=16, high=4320, multiple=2)
    fps = _number(bag, raw.get("fps"), "/output/fps", whole=True, low=1, high=120)
    if path is None or width is None or height is None or fps is None:
        return None
    return Output(path, int(width), int(height), int(fps))


def _brand(bag: _Bag, value: object) -> Brand | None:
    raw = _object(bag, value, "/brand")
    if raw is None:
        return None
    _keys(bag, raw, {"font", "colors", "logo"}, "/brand")
    font = _text(bag, raw["font"], "/brand/font", high=128) if "font" in raw else None
    logo = _path(bag, raw["logo"], "/brand/logo") if "logo" in raw else None
    colors: BrandColors | None = None
    if "colors" in raw:
        block = _object(bag, raw["colors"], "/brand/colors")
        if block is not None:
            _keys(bag, block, {"bg", "fg", "accent"}, "/brand/colors")
            if _need(bag, block, ("bg", "fg", "accent"), "/brand/colors"):
                paints = [
                    _text(bag, block[name], say("/brand/colors/", name), pattern=COLOR, low=7, high=7)
                    for name in ("bg", "fg", "accent")
                ]
                if all(item is not None for item in paints):
                    bg, fg, accent = paints
                    colors = BrandColors(HexColor(bg or ""), HexColor(fg or ""), HexColor(accent or ""))
    if bag.items:
        return None
    return Brand(font, colors, logo)


def _voice(bag: _Bag, value: object) -> Voice | None:
    raw = _object(bag, value, "/voice")
    if raw is None:
        return None
    _keys(bag, raw, {"provider", "voice"}, "/voice")
    if not _need(bag, raw, ("provider", "voice"), "/voice"):
        return None
    provider = raw.get("provider")
    if provider != "xai":
        bag.schema("/voice/provider", "The voice provider must be xai.")
        return None
    name = _text(bag, raw.get("voice"), "/voice/voice", high=128)
    if name is None:
        return None
    return Voice("xai", name)


def _cache(bag: _Bag, value: object) -> RelativePath | None:
    raw = _object(bag, value, "/cache")
    if raw is None:
        return None
    _keys(bag, raw, {"dir"}, "/cache")
    if not _need(bag, raw, ("dir",), "/cache"):
        return None
    return _path(bag, raw.get("dir"), "/cache/dir")


def _action(bag: _Bag, value: object, at: str) -> Action | None:
    raw = _object(bag, value, at)
    if raw is None:
        return None
    _keys(bag, raw, {"click", "wait", "scroll"}, at)
    if len(raw) != 1:
        bag.schema(at, "An action needs exactly one kind.")
        return None
    if set(raw) != {"click"} and set(raw) != {"wait"} and set(raw) != {"scroll"}:
        return None
    if "click" in raw:
        selector = _text(bag, raw["click"], say(at, "/click"), high=512)
        return None if selector is None else Click(selector)
    if "wait" in raw:
        seconds = _number(bag, raw["wait"], say(at, "/wait"), low=0, exclusive_low=True)
        return None if seconds is None else Wait(seconds)
    pixels = _number(bag, raw["scroll"], say(at, "/scroll"), whole=True)
    return None if pixels is None else Scroll(int(pixels))


def _clip(bag: _Bag, value: object, at: str) -> Clip | None:
    raw = _object(bag, value, at)
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
        "audio": {"type", "path", "at", "gain_db", "duck_under"},
    }[kind]
    required = {
        "title_card": ("duration", "text"),
        "screen_recording": ("url", "duration"),
        "video": ("path",),
        "image": ("path", "duration"),
        "voiceover": ("at", "text"),
        "audio": ("path", "at"),
    }[kind]
    _keys(bag, raw, allowed, at)
    if not _need(bag, raw, required, at):
        return None
    if kind == "title_card":
        duration = _number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        text = _text(bag, raw["text"], say(at, "/text"))
        subtitle = _text(bag, raw["subtitle"], say(at, "/subtitle")) if "subtitle" in raw else None
        if duration is None or text is None or ("subtitle" in raw and subtitle is None):
            return None
        return TitleCard(duration, text, subtitle)
    if kind == "screen_recording":
        url = _text(bag, raw["url"], say(at, "/url"), pattern=WEB, high=MAX_URL)
        duration = _number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        actions: tuple[Action, ...] = ()
        if "actions" in raw:
            listed = _list(bag, raw["actions"], say(at, "/actions"), low=0, high=64)
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
        start = _number(bag, raw["in"], say(at, "/in"), low=0) if "in" in raw else None
        end = _number(bag, raw["out"], say(at, "/out"), low=0, exclusive_low=True) if "out" in raw else None
        if path is None or ("in" in raw and start is None) or ("out" in raw and end is None):
            return None
        return VideoClip(path, start, end)
    if kind == "image":
        path = _path(bag, raw["path"], say(at, "/path"))
        duration = _number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        kenburns: KenBurns | None = None
        if "kenburns" in raw:
            block = _object(bag, raw["kenburns"], say(at, "/kenburns"))
            if block is None or path is None or duration is None:
                return None
            _keys(bag, block, {"from", "to"}, say(at, "/kenburns"))
            if not _need(bag, block, ("from", "to"), say(at, "/kenburns")):
                return None
            zoom_from = _number(bag, block["from"], say(at, "/kenburns/from"), low=1, high=4)
            zoom_to = _number(bag, block["to"], say(at, "/kenburns/to"), low=1, high=4)
            if zoom_from is None or zoom_to is None:
                return None
            kenburns = KenBurns(zoom_from, zoom_to)
        if path is None or duration is None:
            return None
        return ImageClip(path, duration, kenburns)
    if kind == "voiceover":
        at_time = _number(bag, raw["at"], say(at, "/at"), low=0)
        text = _text(bag, raw["text"], say(at, "/text"))
        duration = None
        if "duration" in raw:
            duration = _number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
        if at_time is None or text is None or ("duration" in raw and duration is None):
            return None
        return Voiceover(at_time, text, duration)
    path = _path(bag, raw["path"], say(at, "/path"))
    at_time = _number(bag, raw["at"], say(at, "/at"), low=0)
    gain = _number(bag, raw["gain_db"], say(at, "/gain_db"), low=-60, high=12) if "gain_db" in raw else None
    duck: TrackId | None = None
    if "duck_under" in raw:
        named = _text(bag, raw["duck_under"], say(at, "/duck_under"), pattern=TRACK, high=32)
        duck = None if named is None else TrackId(named)
    if path is None or at_time is None or ("gain_db" in raw and gain is None) or ("duck_under" in raw and duck is None):
        return None
    return AudioClip(path, at_time, gain, duck)


def _track(bag: _Bag, value: object, at: str) -> Track | None:
    raw = _object(bag, value, at)
    if raw is None:
        return None
    _keys(bag, raw, {"id", "clips", "from"}, at)
    if "id" not in raw:
        bag.schema(at, "Field id is required.")
        return None
    track_id = _text(bag, raw["id"], say(at, "/id"), pattern=TRACK, high=32)
    has_clips = "clips" in raw
    has_from = "from" in raw
    if has_clips == has_from:
        bag.schema(at, "A track needs clips or from.")
        return None
    if track_id is None:
        return None
    if has_from:
        source = _text(bag, raw["from"], say(at, "/from"), pattern=TRACK, high=32)
        return None if source is None else CaptionsTrack(TrackId(track_id), TrackId(source))
    listed = _list(bag, raw["clips"], say(at, "/clips"), low=1, high=512)
    if listed is None:
        return None
    clips = [_clip(bag, item, say(at, "/clips/", str(index))) for index, item in enumerate(listed)]
    if any(item is None for item in clips):
        return None
    return ClipTrack(TrackId(track_id), tuple(item for item in clips if item is not None))


def _cut(bag: _Bag, value: object, at: str) -> Cut | None:
    raw = _object(bag, value, at)
    if raw is None:
        return None
    _keys(bag, raw, {"at", "transition", "duration"}, at)
    if not _need(bag, raw, ("at", "transition", "duration"), at):
        return None
    when = _number(bag, raw["at"], say(at, "/at"), low=0)
    if raw.get("transition") != "crossfade":
        bag.schema(say(at, "/transition"), "The transition must be crossfade.")
    duration = _number(bag, raw["duration"], say(at, "/duration"), low=0, exclusive_low=True)
    if when is None or duration is None or raw.get("transition") != "crossfade":
        return None
    return Cut(when, "crossfade", duration)


def _rules(bag: _Bag, script: Script) -> None:
    ids = [track.id for track in script.tracks]
    if len(set(ids)) != len(ids):
        bag.rule("track-ids-unique", "/tracks", "Two tracks use the same id.")
    by_id = {track.id: track for track in script.tracks}
    for index, track in enumerate(script.tracks):
        if isinstance(track, CaptionsTrack):
            source = by_id.get(track.source)
            clips = source.clips if isinstance(source, ClipTrack) else ()
            if not any(isinstance(clip, Voiceover) for clip in clips):
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
                if isinstance(clip, VideoClip) and clip.end is not None and clip.end <= (clip.start or 0):
                    bag.rule(
                        "video-out-after-in",
                        say("/tracks/", str(index), "/clips/", str(clip_index)),
                        "out must be later than in.",
                    )


def load_script(raw: object) -> tuple[Script, tuple[ScriptProblem, ...]] | tuple[None, tuple[ScriptProblem, ...]]:
    bag = _Bag()
    document = _object(bag, raw, "")
    if document is None:
        return None, tuple(bag.items)
    _keys(bag, document, {"schemaVersion", "output", "brand", "voice", "cache", "tracks", "cuts"}, "")
    if "schemaVersion" not in document:
        bag.schema("", "Field schemaVersion is required.")
    elif document.get("schemaVersion") != 1:
        bag.schema("/schemaVersion", "schemaVersion must be 1.")
    if "output" not in document or "tracks" not in document:
        if "output" not in document:
            bag.schema("", "Field output is required.")
        if "tracks" not in document:
            bag.schema("", "Field tracks is required.")
        return None, tuple(bag.items)
    output = _output(bag, document["output"])
    brand = _brand(bag, document["brand"]) if "brand" in document else None
    voice = _voice(bag, document["voice"]) if "voice" in document else None
    cache_dir = _cache(bag, document["cache"]) if "cache" in document else None
    listed = _list(bag, document["tracks"], "/tracks", low=1, high=16)
    tracks: list[Track] = []
    if listed is not None:
        built = [_track(bag, item, say("/tracks/", str(index))) for index, item in enumerate(listed)]
        if any(item is None for item in built):
            tracks = []
        else:
            tracks = [item for item in built if item is not None]
    cuts: list[Cut] = []
    if "cuts" in document:
        listed_cuts = _list(bag, document["cuts"], "/cuts", low=0, high=256)
        if listed_cuts is not None:
            built_cuts = [_cut(bag, item, say("/cuts/", str(index))) for index, item in enumerate(listed_cuts)]
            if any(item is None for item in built_cuts):
                cuts = []
            else:
                cuts = [item for item in built_cuts if item is not None]
    if bag.items or output is None:
        return None, tuple(bag.items)
    script = Script(output, brand, voice, tuple(tracks), tuple(cuts), cache_dir)
    _rules(bag, script)
    if bag.items:
        return None, tuple(bag.items)
    return script, ()
