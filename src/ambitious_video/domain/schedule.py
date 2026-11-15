from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ambitious_video.domain.jobs import allowed, charge
from ambitious_video.domain.types import IdemKey, JobId, JobState, LedgerLine, Sha256Hex, Usd, idem_key

IDLE = ""


def step_digest(step: int) -> Sha256Hex:
    return Sha256Hex(f"{step:064x}")


@dataclass
class World:
    jobs: tuple[str, ...]
    renderers: tuple[str, ...]
    paid: tuple[int, ...]
    max_attempts: int
    state: dict[str, JobState] = field(default_factory=dict)
    attempt: dict[str, int] = field(default_factory=dict)
    rendered_attempt: dict[str, int] = field(default_factory=dict)
    busy: dict[str, str] = field(default_factory=dict)
    done: dict[str, set[int]] = field(default_factory=dict)
    lines: dict[IdemKey, LedgerLine] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for job in self.jobs:
            self.state.setdefault(job, "queued")
            self.attempt.setdefault(job, 1)
            self.rendered_attempt.setdefault(job, 0)
        for renderer in self.renderers:
            self.busy.setdefault(renderer, IDLE)
            self.done.setdefault(renderer, set())

    def line_count(self, job: str, step: int) -> int:
        return sum(1 for line in self.lines.values() if line.job_id == job and line.step_index == step)

    def holders(self, job: str) -> int:
        return sum(1 for renderer in self.renderers if self.busy[renderer] == job)


def _move(world: World, job: str, wanted: JobState) -> bool:
    if not allowed(world.state[job], wanted):
        return False
    world.state[job] = wanted
    return True


def claim(world: World, renderer: str, job: str) -> bool:
    if world.busy[renderer] != IDLE or world.state[job] not in {"queued", "retried"}:
        return False
    if not _move(world, job, "rendering"):
        return False
    world.busy[renderer] = job
    world.done[renderer] = set()
    return True


def run_paid_step(world: World, renderer: str, step: int) -> bool:
    job = world.busy[renderer]
    if job == IDLE or step not in world.paid or step in world.done[renderer]:
        return False
    line = LedgerLine(
        JobId(job),
        step,
        "voiceover",
        Decimal(1),
        Usd(Decimal(1)),
        idem_key(JobId(job), step, step_digest(step)),
    )
    charge(world.lines, line)
    world.done[renderer].add(step)
    return True


def finish(world: World, renderer: str) -> bool:
    job = world.busy[renderer]
    if job == IDLE or world.done[renderer] != set(world.paid):
        return False
    if not _move(world, job, "rendered"):
        world.busy[renderer] = IDLE
        return False
    world.rendered_attempt[job] = world.attempt[job]
    world.busy[renderer] = IDLE
    return True


def render_fails(world: World, renderer: str) -> bool:
    job = world.busy[renderer]
    if job == IDLE or not _move(world, job, "failed"):
        return False
    world.busy[renderer] = IDLE
    return True


def crash(world: World, renderer: str) -> bool:
    if world.busy[renderer] == IDLE:
        return False
    world.busy[renderer] = IDLE
    return True


def recover(world: World, job: str) -> bool:
    if world.state[job] != "rendering" or world.holders(job) != 0:
        return False
    return _move(world, job, "failed")


def retry(world: World, job: str) -> bool:
    if world.state[job] != "failed" or world.attempt[job] >= world.max_attempts:
        return False
    if not _move(world, job, "retried"):
        return False
    world.attempt[job] += 1
    return True


def verify_passes(world: World, job: str) -> bool:
    return world.state[job] == "rendered" and _move(world, job, "verified")


def verify_rejects(world: World, job: str) -> bool:
    return world.state[job] == "rendered" and _move(world, job, "failed")


def invariants_hold(world: World) -> bool:
    for job in world.jobs:
        for step in world.paid:
            if world.line_count(job, step) > 1:
                return False
            if world.state[job] in {"rendered", "verified"} and world.line_count(job, step) != 1:
                return False
        if world.state[job] == "verified" and world.rendered_attempt[job] != world.attempt[job]:
            return False
        if world.holders(job) > 1:
            return False
        if world.holders(job) > 0 and world.state[job] != "rendering":
            return False
    return True
