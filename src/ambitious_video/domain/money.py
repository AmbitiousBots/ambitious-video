from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ambitious_video.domain.timeline import Span, Timeline
from ambitious_video.domain.types import Script, Voiceover

SIX = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class Quote:
    step_index: int
    step: str
    units: Decimal
    usd: Decimal

    @property
    def paid(self) -> bool:
        return self.usd > 0


def quotes(script: Script, timeline: Timeline, rate: Decimal) -> tuple[Quote, ...]:
    spoken = script.voice is not None
    rows: list[Quote] = []
    for span in timeline.spans:
        if spoken and isinstance(span.clip, Voiceover):
            units = Decimal(len(span.clip.text))
            usd = units * rate
        else:
            units = Decimal(span.frames) / Decimal(timeline.fps)
            usd = Decimal(0)
        rows.append(Quote(span.step, span.kind, units, usd))
    return tuple(rows)


def money_text(value: Decimal) -> str:
    return format(value.quantize(SIX), "f")


def units_text(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.quantize(SIX), "f")


def total_usd(rows: tuple[Quote, ...]) -> Decimal:
    return sum((row.usd for row in rows), start=Decimal(0))


def paid_spans(script: Script, spans: tuple[Span, ...]) -> tuple[Span, ...]:
    if script.voice is None:
        return ()
    return tuple(span for span in spans if isinstance(span.clip, Voiceover))
