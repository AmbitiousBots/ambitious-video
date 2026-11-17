from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol, runtime_checkable

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.domain.money import Price
from ambitious_video.domain.types import GeneratedScene, LipSync, PaidClip, ProviderKind, SceneOutput


@dataclass(frozen=True, slots=True)
class RunResult:
    path: Path


@dataclass(frozen=True, slots=True)
class Patience:
    poll_seconds: float
    wait_seconds: float


class ProviderError(Exception):
    def __init__(self, provider: str, status: int) -> None:
        super().__init__(provider)
        self.provider = provider
        self.status = status


class RequestLost(ProviderError):
    pass


class Provider(Protocol):
    @property
    def kind(self) -> ProviderKind: ...

    @property
    def id(self) -> str: ...

    @property
    def models(self) -> tuple[str, ...]: ...

    @property
    def secret(self) -> str: ...

    @property
    def seedable(self) -> bool: ...

    @property
    def adapter_version(self) -> int: ...

    def params(self, step: PaidClip) -> JsonObject: ...

    def price(self, model: str) -> Price: ...

    def quote(self, params: JsonObject, units: Decimal) -> Decimal: ...

    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult: ...


@runtime_checkable
class SceneMaker(Protocol):
    @property
    def output(self) -> SceneOutput: ...

    def faults(self, step: GeneratedScene) -> tuple[tuple[str, str], ...]: ...


@runtime_checkable
class LipSyncer(Protocol):
    def lip_faults(self, step: LipSync) -> tuple[tuple[str, str], ...]: ...


@runtime_checkable
class Deferred(Protocol):
    @property
    def patience(self) -> Patience: ...

    def submit(self, params: JsonObject, inputs: Mapping[str, Path], secret: str) -> str: ...

    def collect(self, request_id: str, secret: str, out: Path) -> RunResult | None: ...


def await_result(provider: Deferred, request_id: str, secret: str, out: Path) -> RunResult | None:
    patience = provider.patience
    deadline = time.monotonic() + patience.wait_seconds
    try:
        while True:
            found = provider.collect(request_id, secret, out)
            if found is not None or time.monotonic() + patience.poll_seconds > deadline:
                return found
            time.sleep(patience.poll_seconds)
    except BaseException:
        del secret
        raise
