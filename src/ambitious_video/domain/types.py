from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal, NewType

JobId = NewType("JobId", str)
TrackId = NewType("TrackId", str)
Sha256Hex = NewType("Sha256Hex", str)
IdemKey = NewType("IdemKey", str)
RelativePath = NewType("RelativePath", str)
WebAddress = NewType("WebAddress", str)
HexColor = NewType("HexColor", str)
Timestamp = NewType("Timestamp", str)
Usd = NewType("Usd", Decimal)

SCHEMA_VERSION: Final = 2
TEMPLATE_VERSION: Final = 1

TRACK_ID_PATTERN: Final = "^[a-z][a-z0-9_-]{0,31}$"
HEX_COLOR_PATTERN: Final = "^#[0-9A-Fa-f]{6}$"
PROVIDER_ID_PATTERN: Final = "^[a-z][a-z0-9-]{0,31}$"
MODEL_ID_PATTERN: Final = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
TEMPLATE_ID_PATTERN: Final = "^[a-z][a-z0-9-]{0,63}$"
INPUT_NAME_PATTERN: Final = "^[a-z][a-z0-9_]{0,31}$"
RESOLUTION_PATTERN: Final = "^[0-9]{1,4}[kp]$"
ASPECT_RATIO_PATTERN: Final = "^[0-9.]{1,4}:[0-9.]{1,4}$"
MAX_SEED: Final = 4294967295

ClipType = Literal[
    "title_card", "screen_recording", "video", "image", "voiceover", "speech", "audio", "generated_scene"
]
ActionKind = Literal["click", "wait", "scroll"]
Transition = Literal["crossfade"]
EncodeMode = Literal["web", "lossless"]
ProviderKind = Literal["scene", "speech", "lipsync"]
InputType = Literal["text", "image", "video", "audio", "color", "voice", "choice"]
SceneType = Literal["title_card", "image", "video", "generated_scene"]
FallbackType = Literal["title_card", "image", "video"]
SceneOutput = Literal["video", "image"]

ScriptRule = Literal[
    "track-ids-unique",
    "captions-from-a-voiceover-track",
    "duck-under-another-track",
    "video-out-after-in",
    "generated-scenes-need-disclosure",
]

JobState = Literal["queued", "rendering", "rendered", "verified", "failed", "retried"]

JOB_TRANSITIONS: Final[Mapping[JobState, frozenset[JobState]]] = MappingProxyType(
    {
        "queued": frozenset({"rendering"}),
        "rendering": frozenset({"rendered", "failed"}),
        "rendered": frozenset({"verified", "failed"}),
        "verified": frozenset(),
        "failed": frozenset({"retried"}),
        "retried": frozenset({"rendering"}),
    }
)

AssetKind = Literal["image", "video", "audio", "voiceover", "screen_recording"]

ErrorCode = Literal[
    "E_UNKNOWN_STEP",
    "E_JOB_STATE",
    "E_OVERLAP",
    "E_MEDIA",
    "E_RANGE",
    "E_TOOL",
    "E_PROVIDER",
    "E_VERIFY",
    "E_CACHE",
    "E_PENDING",
]


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E


type Result[T, E] = Ok[T] | Err[E]


@dataclass(frozen=True, slots=True)
class Output:
    path: RelativePath
    width: int
    height: int
    fps: int
    encode: EncodeMode


@dataclass(frozen=True, slots=True)
class BrandColors:
    bg: HexColor
    fg: HexColor
    accent: HexColor


@dataclass(frozen=True, slots=True)
class Brand:
    font: str | None
    colors: BrandColors | None
    logo: RelativePath | None


@dataclass(frozen=True, slots=True)
class Click:
    selector: str


@dataclass(frozen=True, slots=True)
class Wait:
    seconds: float


@dataclass(frozen=True, slots=True)
class Scroll:
    pixels: int


type Action = Click | Wait | Scroll


@dataclass(frozen=True, slots=True)
class TitleCard:
    duration: float
    text: str
    subtitle: str | None


@dataclass(frozen=True, slots=True)
class ScreenRecording:
    url: WebAddress
    duration: float
    actions: tuple[Action, ...]


@dataclass(frozen=True, slots=True)
class VideoClip:
    path: RelativePath
    start: float | None
    end: float | None


@dataclass(frozen=True, slots=True)
class KenBurns:
    zoom_from: float
    zoom_to: float


@dataclass(frozen=True, slots=True)
class ImageClip:
    path: RelativePath
    duration: float
    kenburns: KenBurns | None


@dataclass(frozen=True, slots=True)
class Voiceover:
    at: float
    text: str
    duration: float | None


@dataclass(frozen=True, slots=True)
class Speech:
    at: float
    text: str
    duration: float | None
    provider: str
    model: str
    voice: str


@dataclass(frozen=True, slots=True)
class AudioClip:
    path: RelativePath
    at: float
    gain_db: float | None
    duck_under: TrackId | None


type SceneFallback = TitleCard | ImageClip | VideoClip


@dataclass(frozen=True, slots=True)
class GeneratedScene:
    duration: float
    prompt: str
    provider: str
    model: str
    resolution: str | None
    aspect_ratio: str | None
    seed: int | None
    image: RelativePath | None
    kenburns: KenBurns | None
    fallback: SceneFallback


type Clip = TitleCard | ScreenRecording | VideoClip | ImageClip | Voiceover | Speech | AudioClip | GeneratedScene
PaidClip = Speech | GeneratedScene


@dataclass(frozen=True, slots=True)
class ClipTrack:
    id: TrackId
    clips: tuple[Clip, ...]


@dataclass(frozen=True, slots=True)
class CaptionsTrack:
    id: TrackId
    source: TrackId


type Track = ClipTrack | CaptionsTrack


@dataclass(frozen=True, slots=True)
class Cut:
    at: float
    transition: Transition
    duration: float


@dataclass(frozen=True, slots=True)
class TemplateRef:
    id: str
    version: int


@dataclass(frozen=True, slots=True)
class Script:
    output: Output
    brand: Brand | None
    tracks: tuple[Track, ...]
    cuts: tuple[Cut, ...]
    cache_dir: RelativePath | None = None
    template: TemplateRef | None = None
    budget_usd: Decimal | None = None
    disclosure: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaProblem:
    at: str
    message: str


@dataclass(frozen=True, slots=True)
class RuleProblem:
    rule: ScriptRule
    at: str
    message: str


@dataclass(frozen=True, slots=True)
class UnknownStep:
    at: str
    step: str


type ScriptProblem = SchemaProblem | RuleProblem | UnknownStep


@dataclass(frozen=True, slots=True)
class WaitingJob:
    id: JobId
    script_sha256: Sha256Hex
    created_at: Timestamp
    output_path: RelativePath
    state: Literal["queued", "retried"]


@dataclass(frozen=True, slots=True)
class RenderingJob:
    id: JobId
    script_sha256: Sha256Hex
    created_at: Timestamp
    output_path: RelativePath
    state: Literal["rendering"] = "rendering"


@dataclass(frozen=True, slots=True)
class RenderedJob:
    id: JobId
    script_sha256: Sha256Hex
    created_at: Timestamp
    output_path: RelativePath
    finished_at: Timestamp
    output_sha256: Sha256Hex
    state: Literal["rendered", "verified"]


@dataclass(frozen=True, slots=True)
class FailedJob:
    id: JobId
    script_sha256: Sha256Hex
    created_at: Timestamp
    output_path: RelativePath
    finished_at: Timestamp
    state: Literal["failed"] = "failed"


type Job = WaitingJob | RenderingJob | RenderedJob | FailedJob


@dataclass(frozen=True, slots=True)
class TransitionRefused:
    job_id: JobId
    state: JobState
    wanted: JobState


@dataclass(frozen=True, slots=True)
class LedgerLine:
    job_id: JobId
    step_index: int
    step: ClipType
    units: Decimal
    usd: Usd
    idem_key: IdemKey


@dataclass(frozen=True, slots=True)
class Asset:
    sha256: Sha256Hex
    path: RelativePath
    kind: AssetKind


def idem_key(step_key: Sha256Hex, call: int = 1) -> IdemKey:
    if call <= 1:
        return IdemKey(step_key)
    return IdemKey(step_key + "#" + str(call))
