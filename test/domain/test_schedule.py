from __future__ import annotations

from ambitious_video.domain.schedule import (
    World,
    claim,
    crash,
    finish,
    invariants_hold,
    recover,
    remove,
    render_fails,
    retry,
    run_paid_step,
    verify_passes,
    verify_rejects,
)


def world() -> World:
    return World(("job-a", "job-b"), ("red", "blue"), (0, 1), 3)


def test_retry_does_not_charge_twice() -> None:
    state = world()
    assert claim(state, "red", "job-a")
    assert run_paid_step(state, "red", 0)
    assert render_fails(state, "red")
    assert retry(state, "job-a")
    assert claim(state, "red", "job-a")
    assert run_paid_step(state, "red", 0)
    assert state.line_count(0) == 1
    assert state.call_count(0) == 1
    assert invariants_hold(state)


def test_a_second_job_with_the_same_step_key_is_not_charged() -> None:
    state = world()
    assert claim(state, "red", "job-a")
    assert run_paid_step(state, "red", 0)
    assert claim(state, "blue", "job-b")
    assert run_paid_step(state, "blue", 0)
    assert state.line_count(0) == 1
    assert state.call_count(0) == 1
    assert invariants_hold(state)


def test_a_removed_result_is_called_again_and_charged_once_per_call() -> None:
    state = world()
    assert claim(state, "red", "job-a")
    assert run_paid_step(state, "red", 0)
    assert run_paid_step(state, "red", 1)
    assert finish(state, "red")
    assert remove(state, 0)
    assert not remove(state, 0)
    assert claim(state, "blue", "job-b")
    assert run_paid_step(state, "blue", 0)
    assert run_paid_step(state, "blue", 1)
    assert (state.call_count(0), state.line_count(0)) == (2, 2)
    assert (state.call_count(1), state.line_count(1)) == (1, 1)
    assert len({line.idem_key for line in state.lines.values()}) == 3
    assert invariants_hold(state)


def test_verify_refuses_a_job_that_is_not_rendered() -> None:
    state = world()
    assert not verify_passes(state, "job-a")
    assert state.state["job-a"] == "queued"


def test_random_orders_keep_the_rules() -> None:
    actions = (
        "claim",
        "pay0",
        "pay1",
        "finish",
        "fail",
        "crash",
        "recover",
        "retry",
        "pass",
        "reject",
        "remove0",
        "remove1",
    )
    seed = 1
    for _case in range(80):
        state = world()
        for _step in range(40):
            seed = (seed * 1664525 + 1013904223) % (2**32)
            action = actions[seed % len(actions)]
            seed = (seed * 1664525 + 1013904223) % (2**32)
            job = ("job-a", "job-b")[seed % 2]
            seed = (seed * 1664525 + 1013904223) % (2**32)
            renderer = ("red", "blue")[seed % 2]
            if action == "claim":
                claim(state, renderer, job)
            elif action == "pay0":
                run_paid_step(state, renderer, 0)
            elif action == "pay1":
                run_paid_step(state, renderer, 1)
            elif action == "finish":
                finish(state, renderer)
            elif action == "fail":
                render_fails(state, renderer)
            elif action == "crash":
                crash(state, renderer)
            elif action == "recover":
                recover(state, job)
            elif action == "retry":
                retry(state, job)
            elif action == "pass":
                verify_passes(state, job)
            elif action == "remove0":
                remove(state, 0)
            elif action == "remove1":
                remove(state, 1)
            else:
                verify_rejects(state, job)
            assert invariants_hold(state)
