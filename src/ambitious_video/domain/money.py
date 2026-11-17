from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ambitious_video.domain.types import PaidClip, Speech

SIX = Decimal("0.000001")
UNITS = frozenset({"character", "second", "image", "frame"})


@dataclass(frozen=True, slots=True)
class Price:
    provider: str
    model: str
    unit: str
    usd_per_unit: Decimal
    source_url: str
    checked_at: str


@dataclass(frozen=True, slots=True)
class Quote:
    step_index: int
    step: str
    units: Decimal
    usd: Decimal
    price: Price | None = None

    @property
    def paid(self) -> bool:
        return self.price is not None


def paid_units(clip: PaidClip, unit: str, frames: int) -> Decimal:
    if isinstance(clip, Speech):
        return Decimal(len(clip.text))
    if unit == "frame":
        return Decimal(frames)
    if unit == "second":
        return Decimal(str(clip.duration))
    return Decimal(1)


def money_text(value: Decimal) -> str:
    return format(value.quantize(SIX), "f")


def units_text(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.quantize(SIX), "f")


def rate_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def total_usd(rows: tuple[Quote, ...]) -> Decimal:
    return sum((row.usd for row in rows), start=Decimal(0))
