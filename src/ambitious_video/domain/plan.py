from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ambitious_kit.domain.envelope import JsonValue

from ambitious_video.domain.types import Sha256Hex

LOCAL = "local"
LOCAL_VERSION = 1
SEED_SPAN = 2**32
UNHASHED = frozenset({"planSha256", "secretsNeeded"})


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sorted_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: sorted_json(value[key]) for key in sorted(value)}
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return [sorted_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class StepSpec:
    kind: str
    provider: str
    model: str | None
    adapter_version: int
    params: JsonValue
    seed: int | None
    input_hashes: tuple[str | None, ...]

    @property
    def paid(self) -> bool:
        return self.provider != LOCAL


def step_key(spec: StepSpec) -> Sha256Hex:
    body: dict[str, JsonValue] = {
        "kind": spec.kind,
        "provider": spec.provider,
        "model": spec.model,
        "adapterVersion": spec.adapter_version,
        "params": spec.params,
        "seed": spec.seed,
        "inputHashes": list(spec.input_hashes),
    }
    return Sha256Hex(digest_text(canonical(body)))


def provider_seed(seed: int, key: str) -> int:
    return int(digest_text(canonical({"seed": seed, "stepKey": key}))[:16], 16) % SEED_SPAN


def plan_sha(plan: Mapping[str, JsonValue]) -> Sha256Hex:
    body = {key: value for key, value in plan.items() if key not in UNHASHED}
    return Sha256Hex(digest_text(canonical(body)))
