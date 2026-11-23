from __future__ import annotations

import unicodedata

from ambitious_kit.domain.envelope import failure

ACTOR_MAX_CHARACTERS = 200
ACTOR_RULE = (
    f"actor is 1 to {ACTOR_MAX_CHARACTERS} Unicode characters, with no control characters or line breaks "
    "and no leading or trailing whitespace. Internal spaces are allowed. Nothing is trimmed."
)
REFUSED_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})


def actor_problem(raw: object) -> str | None:
    if not isinstance(raw, str):
        return "is not a string"
    if raw == "":
        return "is empty"
    if any(unicodedata.category(character) in REFUSED_CATEGORIES for character in raw):
        return "has a control character, a line break or half of a surrogate pair"
    if raw[0].isspace() or raw[-1].isspace():
        return "starts or ends with whitespace"
    return f"has {len(raw)} characters" if len(raw) > ACTOR_MAX_CHARACTERS else None


def parse_actor(raw: object) -> str:
    problem = actor_problem(raw)
    if problem is not None or not isinstance(raw, str):
        raise failure(
            "E_INPUT_SCHEMA",
            f'The input field "actor" {problem or "is not a string"}.',
            f"Send an actor that fits this rule: {ACTOR_RULE}",
        )
    return raw
