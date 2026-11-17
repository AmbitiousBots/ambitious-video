from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.domain.money import Price
from ambitious_video.domain.types import PaidClip, ProviderKind


@dataclass(frozen=True, slots=True)
class RunResult:
    path: Path


class ProviderError(Exception):
    def __init__(self, provider: str, status: int) -> None:
        super().__init__(provider)
        self.provider = provider
        self.status = status


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
