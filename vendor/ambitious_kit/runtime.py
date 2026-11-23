from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from ambitious_kit.domain.envelope import Failure, JsonObject, failure
from ambitious_kit.paths import ambitious_home

RUNTIME_MARKER = "ambitious-runtime.json"

type RuntimeState = Literal["ready", "missing", "damaged"]


@dataclass(frozen=True)
class PinnedRuntime:
    module: str
    lock_sha256: str
    path: str
    state: RuntimeState
    site_packages: str
    problem: str | None

    def to_json(self) -> JsonObject:
        return {"state": self.state, "path": self.path, "lockSha256": self.lock_sha256}


def lock_sha256(lock_text: str) -> str:
    return hashlib.sha256(lock_text.encode("utf-8")).hexdigest()


def _marker_matches(path: str, expected: str) -> bool:
    try:
        with open(os.path.join(path, RUNTIME_MARKER), encoding="utf-8") as marker:
            parsed: object = json.load(marker)
    except (OSError, ValueError):
        return False
    return isinstance(parsed, dict) and parsed.get("lockSha256") == expected


def pinned_runtime(
    environment: Mapping[str, str], module: str, lock_text: str, python: tuple[int, int] | None = None
) -> PinnedRuntime:
    major, minor = python if python is not None else (sys.version_info.major, sys.version_info.minor)
    sha = lock_sha256(lock_text)
    path = os.path.join(ambitious_home(environment), module, "runtime", sha)
    site_packages = os.path.join(path, "lib", f"python{major}.{minor}", "site-packages")
    how = f"Install it with the installer's --runtime flag for ambitious-{module}."
    if not _marker_matches(path, sha):
        present = os.path.isdir(path)
        return PinnedRuntime(
            module,
            sha,
            path,
            "damaged" if present else "missing",
            site_packages,
            f"The pinned runtime at {path} is incomplete. {how}"
            if present
            else f"The pinned runtime is not installed at {path}. {how}",
        )
    if not os.path.isdir(site_packages):
        return PinnedRuntime(
            module,
            sha,
            path,
            "damaged",
            site_packages,
            f"The pinned runtime at {path} was not built for Python {major}.{minor}. "
            f"Remove it and install it again with the python3 that runs this module.",
        )
    return PinnedRuntime(module, sha, path, "ready", site_packages, None)


def runtime_failure(runtime: PinnedRuntime) -> Failure:
    return failure(
        "E_RUNTIME",
        runtime.problem or "The pinned runtime is not ready.",
        f"Run the kit installer for {runtime.module} with --runtime, then run selfcheck. "
        "Commands that do not need the runtime keep working without it.",
        {"pinnedRuntime": runtime.to_json()},
    )


def activate(runtime: PinnedRuntime) -> None:
    if runtime.state != "ready":
        raise runtime_failure(runtime)
    if runtime.site_packages not in sys.path:
        sys.path.insert(0, runtime.site_packages)
