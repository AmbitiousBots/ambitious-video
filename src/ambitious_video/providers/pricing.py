from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib.resources import files

from ambitious_video.domain.money import UNITS, Price

FIELDS = ("provider", "model", "unit", "usdPerUnit", "sourceUrl", "checkedAt")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOURCE = re.compile(r"^https://\S+$")


class PriceMissing(Exception):
    def __init__(self, provider: str, model: str) -> None:
        super().__init__(provider + "/" + model)
        self.provider = provider
        self.model = model


class PriceTableBroken(Exception):
    pass


def _price(raw: object) -> Price:
    if not isinstance(raw, dict) or set(raw) != set(FIELDS):
        raise PriceTableBroken
    texts = [raw[name] for name in FIELDS]
    if not all(isinstance(item, str) and item != "" for item in texts):
        raise PriceTableBroken
    provider, model, unit, rate, source, checked = (str(item) for item in texts)
    try:
        usd = Decimal(rate)
    except InvalidOperation:
        raise PriceTableBroken from None
    known = unit in UNITS and DAY.fullmatch(checked) is not None and SOURCE.fullmatch(source) is not None
    if not known or not usd.is_finite() or usd <= 0:
        raise PriceTableBroken
    return Price(provider, model, unit, usd, source, checked)


def parse_prices(text: str) -> tuple[Price, ...]:
    try:
        raw: object = json.loads(text)
    except ValueError:
        raise PriceTableBroken from None
    listed = raw.get("prices") if isinstance(raw, dict) else None
    if not isinstance(listed, list) or not listed:
        raise PriceTableBroken
    prices = tuple(_price(item) for item in listed)
    if len({(item.provider, item.model) for item in prices}) != len(prices):
        raise PriceTableBroken
    return prices


@cache
def price_table() -> tuple[Price, ...]:
    return parse_prices(files("ambitious_video").joinpath("pricing.json").read_text(encoding="utf-8"))


def price_of(provider: str, model: str) -> Price:
    for item in price_table():
        if item.provider == provider and item.model == model:
            return item
    raise PriceMissing(provider, model)
