from __future__ import annotations

from array import array
from dataclasses import dataclass

MAX_CELLS = 2_000_000


@dataclass(frozen=True)
class LineChange:
    start_line: int
    end_line: int
    before: str
    after: str


@dataclass(frozen=True)
class LineConflict:
    start_line: int
    end_line: int
    base: str | None
    current: str
    published: str


@dataclass(frozen=True)
class LineMerge:
    merged: str
    local_changes_kept: tuple[LineChange, ...]
    conflicts: tuple[LineConflict, ...]


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    lines: tuple[str, ...]
    side: str


def _lines(text: str) -> tuple[str, ...]:
    return tuple(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def _edits(base: tuple[str, ...], changed: tuple[str, ...], side: str) -> list[Edit]:
    start = 0
    while start < min(len(base), len(changed)) and base[start] == changed[start]:
        start += 1
    end, changed_end = len(base), len(changed)
    while end > start and changed_end > start and base[end - 1] == changed[changed_end - 1]:
        end -= 1
        changed_end -= 1
    before, after = base[start:end], changed[start:changed_end]
    width = len(after) + 1
    if (len(before) + 1) * width > MAX_CELLS:
        return [Edit(start, end, after, side)]
    common = array("I", [0]) * ((len(before) + 1) * width)
    for i in range(len(before) - 1, -1, -1):
        for j in range(len(after) - 1, -1, -1):
            common[i * width + j] = (
                common[(i + 1) * width + j + 1] + 1
                if before[i] == after[j]
                else max(common[(i + 1) * width + j], common[i * width + j + 1])
            )
    edits: list[Edit] = []
    i, j = 0, 0
    while i < len(before) or j < len(after):
        if i < len(before) and j < len(after) and before[i] == after[j]:
            i, j = i + 1, j + 1
            continue
        from_line, changed_from = i, j
        while (i < len(before) or j < len(after)) and not (
            i < len(before) and j < len(after) and before[i] == after[j]
        ):
            if j >= len(after) or (
                i < len(before) and common[(i + 1) * width + j] >= common[i * width + j + 1]
            ):
                i += 1
            else:
                j += 1
        edits.append(Edit(from_line + start, i + start, after[changed_from:j], side))
    return edits


def _changed_range(base: tuple[str, ...], start: int, end: int, edits: list[Edit]) -> list[str]:
    result: list[str] = []
    cursor = start
    for edit in edits:
        result.extend(base[cursor:edit.start])
        result.extend(edit.lines)
        cursor = edit.end
    result.extend(base[cursor:end])
    return result


def _marked_conflict(current: list[str], published: list[str], base: list[str] | None) -> list[str]:
    return (
        ["<<<<<<< current", *current]
        + ([] if base is None else ["||||||| base", *base])
        + ["=======", *published, ">>>>>>> published"]
    )


def unknown_base_merge(current: str, published: str) -> LineMerge:
    return LineMerge(
        "\n".join(_marked_conflict(list(_lines(current)), list(_lines(published)), None)) + "\n",
        (),
        (LineConflict(1, len(_lines(current)), None, current, published),),
    )


def merge_lines(base_text: str, current_text: str, published_text: str) -> LineMerge:
    base = _lines(base_text)
    edits = sorted(
        _edits(base, _lines(current_text), "current") + _edits(base, _lines(published_text), "published"),
        key=lambda edit: (edit.start, edit.end),
    )
    merged: list[str] = []
    local_changes_kept: list[LineChange] = []
    conflicts: list[LineConflict] = []
    cursor, next_edit = 0, 0
    while next_edit < len(edits):
        first = edits[next_edit]
        group, end = [first], first.end
        next_edit += 1
        while next_edit < len(edits):
            edit = edits[next_edit]
            if edit.start > end or (
                edit.start == end
                and edit.start != edit.end
                and not any(item.start == item.end == end for item in group)
            ):
                break
            group.append(edit)
            end = max(end, edit.end)
            next_edit += 1
        ours = [edit for edit in group if edit.side == "current"]
        theirs = [edit for edit in group if edit.side == "published"]
        original = list(base[first.start:end])
        current = _changed_range(base, first.start, end, ours)
        published = _changed_range(base, first.start, end, theirs)
        conflict = bool(ours and theirs and current != published)
        merged.extend(base[cursor:first.start])
        merged.extend(_marked_conflict(current, published, original) if conflict else current if ours else published)
        if conflict:
            conflicts.append(
                LineConflict(first.start + 1, end, "\n".join(original), "\n".join(current), "\n".join(published))
            )
        else:
            local_changes_kept.extend(
                LineChange(edit.start + 1, edit.end, "\n".join(base[edit.start:edit.end]), "\n".join(edit.lines))
                for edit in ours
            )
        cursor = end
    merged.extend(base[cursor:])
    return LineMerge("\n".join(merged), tuple(local_changes_kept), tuple(conflicts))
