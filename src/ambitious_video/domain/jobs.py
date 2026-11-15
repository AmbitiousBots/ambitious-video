from __future__ import annotations

from ambitious_video.domain.types import JOB_TRANSITIONS, IdemKey, JobState, LedgerLine


def allowed(state: JobState, wanted: JobState) -> bool:
    return wanted in JOB_TRANSITIONS[state]


def charge(lines: dict[IdemKey, LedgerLine], line: LedgerLine) -> bool:
    if line.idem_key in lines:
        return False
    lines[line.idem_key] = line
    return True
