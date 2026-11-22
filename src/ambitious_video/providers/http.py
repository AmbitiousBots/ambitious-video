from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import IO, final

from ambitious_kit.domain.envelope import JsonObject

from ambitious_video.providers.base import Patience, ProviderError, RunResult, await_result

JSON_TYPE = "application/json"
MAX_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class KeyHeader:
    name: str
    prefix: str


BEARER = KeyHeader("Authorization", "Bearer ")


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str
    ) -> urllib.request.Request | None:
        return None


def send(
    url: str,
    payload: bytes | None,
    secret: str | None,
    timeout: float,
    carried: KeyHeader = BEARER,
    content_type: str = JSON_TYPE,
) -> bytes | int:
    status = 0
    try:
        request = urllib.request.Request(url, data=payload)
        if payload is not None:
            request.add_header("Content-Type", content_type)
        if secret is not None:
            request.add_unredirected_header(carried.name, carried.prefix + secret)
        opened = urllib.request.urlopen if secret is None else urllib.request.build_opener(_Stay).open
        del secret
        with opened(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
        if isinstance(body, bytes) and len(body) <= MAX_BYTES:
            return body
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            error.close()
        except Exception:
            pass
    except Exception:
        status = 0
    return status


def as_object(body: bytes) -> dict[str, object] | None:
    try:
        found: object = json.loads(body)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


@dataclass(frozen=True, slots=True)
class Request:
    url: str
    payload: bytes | None
    timeout: float
    header: KeyHeader = BEARER
    content_type: str = JSON_TYPE


class Sender(AbstractContextManager["Sender"]):
    def __init__(self, secret: str) -> None:
        self.__secret = secret

    def __call__(self, request: Request) -> bytes | int:
        return send(request.url, request.payload, self.__secret, request.timeout, request.header, request.content_type)

    def __exit__(self, *_exc: object) -> None:
        del self.__secret


class MediaProvider(ABC):
    @abstractmethod
    def render(self, params: JsonObject, inputs: Mapping[str, Path], exchange: Sender, out: Path) -> RunResult: ...

    @final
    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult:
        with Sender(secret) as exchange:
            del secret
            return self.render(params, inputs, exchange, out)


class DeferredProvider(ABC):
    patience: Patience

    @property
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    def start(self, params: JsonObject, inputs: Mapping[str, Path], exchange: Sender) -> str: ...

    @abstractmethod
    def poll(self, request_id: str, exchange: Sender, out: Path) -> RunResult | None: ...

    @final
    def submit(self, params: JsonObject, inputs: Mapping[str, Path], secret: str) -> str:
        with Sender(secret) as exchange:
            del secret
            return self.start(params, inputs, exchange)

    @final
    def collect(self, request_id: str, secret: str, out: Path) -> RunResult | None:
        with Sender(secret) as exchange:
            del secret
            return self.poll(request_id, exchange, out)

    @final
    def run(self, params: JsonObject, inputs: Mapping[str, Path], secret: str, out: Path) -> RunResult:
        try:
            request_id = self.submit(params, inputs, secret)
            found = await_result(self, request_id, secret, out)
        finally:
            del secret
        if found is None:
            raise ProviderError(self.id, 0)
        return found
