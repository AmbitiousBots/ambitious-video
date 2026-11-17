from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message
from typing import IO

JSON_TYPE = "application/json"
MAX_BYTES = 256 * 1024 * 1024


class _Stay(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str
    ) -> urllib.request.Request | None:
        return None


def send(url: str, payload: bytes | None, secret: str | None, timeout: float) -> bytes | int:
    headers: dict[str, str] = {}
    if secret is not None:
        headers["Authorization"] = "Bearer " + secret
    if payload is not None:
        headers["Content-Type"] = JSON_TYPE
    method = "GET" if payload is None else "POST"
    request = urllib.request.Request(url, data=payload, method=method, headers=headers)
    opened = urllib.request.urlopen if secret is None else urllib.request.build_opener(_Stay).open
    del secret, headers
    status = 0
    try:
        with opened(request, timeout=timeout) as response:
            body = response.read(MAX_BYTES + 1)
        if isinstance(body, bytes) and len(body) <= MAX_BYTES:
            return body
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            error.read()
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
