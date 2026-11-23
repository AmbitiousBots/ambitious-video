from __future__ import annotations

import random
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ambitious_kit.domain.envelope import JsonObject
from ambitious_kit.domain.input_shape import InputShape

type Access = Literal["read", "write", "none"]


@dataclass(frozen=True)
class Context[Input]:
    input: Input
    now: datetime
    rng: random.Random
    env: Mapping[str, str]


@dataclass(frozen=True)
class DbContext[Input](Context[Input]):
    db: sqlite3.Connection


@dataclass(frozen=True)
class Runtime:
    now: datetime
    rng: random.Random
    env: Mapping[str, str]
    open_db: Callable[[], sqlite3.Connection]


@dataclass(frozen=True)
class Command:
    access: Access
    takes_input: bool
    parse: Callable[[object], Callable[[Runtime], JsonObject]]
    shape: InputShape | None = None


def no_input(_raw: object) -> None:
    return None


def _with_db[Input](
    access: Access,
    run: Callable[[DbContext[Input]], JsonObject],
    parse_input: Callable[[object], Input],
    shape: InputShape | None,
) -> Command:
    def parse(raw: object) -> Callable[[Runtime], JsonObject]:
        parsed = parse_input(raw)
        return lambda runtime: run(DbContext(parsed, runtime.now, runtime.rng, runtime.env, runtime.open_db()))

    return Command(access, parse_input is not no_input, parse, shape)


def read_command[Input](
    run: Callable[[DbContext[Input]], JsonObject],
    parse_input: Callable[[object], Input],
    shape: InputShape | None = None,
) -> Command:
    return _with_db("read", run, parse_input, shape)


def write_command[Input](
    run: Callable[[DbContext[Input]], JsonObject],
    parse_input: Callable[[object], Input],
    shape: InputShape | None = None,
) -> Command:
    return _with_db("write", run, parse_input, shape)


def pure_command[Input](
    run: Callable[[Context[Input]], JsonObject],
    parse_input: Callable[[object], Input],
    shape: InputShape | None = None,
) -> Command:
    def parse(raw: object) -> Callable[[Runtime], JsonObject]:
        parsed = parse_input(raw)
        return lambda runtime: run(Context(parsed, runtime.now, runtime.rng, runtime.env))

    return Command("none", parse_input is not no_input, parse, shape)
