from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from ambitious_kit.domain.envelope import failure

AGENT_DATA_ROOT = "/home/box/agent-data"
DEFAULT_AMBITIOUS_HOME = "/workspace/ambitious"
MAX_LINKS = 40


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _link_target(path: str) -> str | None:
    try:
        return os.readlink(path) if os.path.islink(path) else None
    except OSError:
        return None


def _parts(path: str) -> list[str]:
    return [part for part in path.split(os.sep) if part != ""]


def _with_links_resolved(path: str, forbidden: Callable[[str], bool]) -> str:
    current = os.sep
    pending = _parts(path)
    links = 0
    while pending:
        if forbidden(current):
            return current
        following = os.path.join(current, pending.pop(0))
        if forbidden(following):
            return following
        target = _link_target(following)
        if target is None:
            current = following
            continue
        if links >= MAX_LINKS:
            return following
        links += 1
        absolute = os.path.normpath(
            target if os.path.isabs(target) else os.path.join(os.path.dirname(following), target)
        )
        current = os.sep
        pending = _parts(absolute) + pending
    return current


def refuse_agent_data(path: str) -> str:
    absolute = os.path.abspath(path)
    refusal = failure(
        "E_FORBIDDEN_PATH",
        f"The path {path} is under {AGENT_DATA_ROOT}/, which modules never read or write.",
        "Use a path under AMBITIOUS_HOME, or omit --db to use the default.",
    )
    if _is_under(absolute, AGENT_DATA_ROOT):
        raise refusal
    resolved_parent = _with_links_resolved(os.path.dirname(AGENT_DATA_ROOT), lambda _resolved: False)
    roots = (AGENT_DATA_ROOT, os.path.join(resolved_parent, "agent-data"))

    def forbidden(resolved: str) -> bool:
        return any(_is_under(resolved, root) for root in roots)

    if forbidden(_with_links_resolved(absolute, forbidden)):
        raise refusal
    return absolute


def ambitious_home(environment: Mapping[str, str]) -> str:
    return refuse_agent_data(environment.get("AMBITIOUS_HOME") or DEFAULT_AMBITIOUS_HOME)
