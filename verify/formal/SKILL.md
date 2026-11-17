---
name: verify-formal-video
description: "Check the video module's render job rules (a retry never charges twice, a step key is paid for once across jobs, a verified job was rendered, one render per job) against the TLA+ model, and the video script schema against its examples. Use when a change touches the render, verify, jobs, ledger or cache commands, how a job changes state, how a ledger line is written, how a step key is built, how the step cache is read or written, how a script is read, or any file in spec/, schemas/, examples/ or src/ambitious_video/domain/."
---

# Formal verification for the video module

## What is verified

| Rule | In plain words | Harm if it breaks |
|---|---|---|
| `NoDoubleCharge` | The ledger never holds two lines for the same step key, however often a job is retried and however many jobs need that step. | The recorded cost is too high. |
| `OneCallPerStepKey` | The provider is called at most once for a step key, across retries and jobs. | The owner pays again for a stored result. |
| `RenderedIsFullyCharged` | Every paid step of a rendered or verified job has exactly one ledger line. | A cost goes missing from the ledger. |
| `VerifiedImpliesRendered` | A verified job was rendered to the end on its latest attempt. | A video is reported as checked when the checked file is stale or unfinished. |
| `OneActiveRenderPerJob` | At most one render works on a job, and only while the job is `rendering`. | Two renders write one output file. |

The same run checks that no job can get stuck, that every job ends `verified` or `failed` when steps that can happen do happen (`EveryJobSettles`), and that a job only moves along the declared arrows (`OnlyDeclaredMoves`).

Tool: TLA+ with the TLC model checker, because the question is whether any order of claims, paid steps, failures, crashes, recoveries, retries and verifications can reach a bad state. Tests only try the orders someone thought of.

Bounds: two jobs that need the same two step keys, two renders at the same time, three attempts per job; no limit on the number of steps. `spec/README.md` lists what is left out, including money spent at the provider when a render dies between a paid call and storing its result, and cache entries that are removed.

The same command also checks `schemas/video-script.v1.json`: the examples are accepted, every invalid example is rejected for the reason written next to it, and the schema, the model and `src/ambitious_video/domain/types.py` name the same clip types, rules, job states and moves.

## The one command

From the repo root:

```sh
bash spec/check.sh && python3 scripts/check_schemas.py
```

`spec/check.sh` needs [mise](https://mise.jdx.dev) and `curl`. It installs the Java and Python pinned in `.mise.toml`, downloads `tla2tools.jar` from the address pinned there into `~/.cache/ambitious/` and refuses it unless its SHA-256 matches the pin. It runs the models (must pass) and every variant in `spec/broken/` (each must break the rule named in `spec/broken/EXPECTED.json`). It takes a few seconds and ends with a line that starts `OK:`. `scripts/check_schemas.py` installs the pinned packages in `scripts/dev-requirements.txt` into `.venv/` on first run and ends with `check_schemas: ok`.

## Covered code

Last confirmed in step with the code at commit `db9f60754fc597e1d564a0aff4fd47d3b31f9a4a`. `domain/schedule.py` is the executable form of the model, `db/store.py` persists the same allowed moves and idempotent ledger lines, `domain/plan.py` builds step keys, `db/step_cache.py` is the step cache, and `cli/engine.py` is the render and verify path. The lock is `db/lock.py`.

| Model part | Code it describes |
|---|---|
| `States`, `Moves` | `JobState` and `JOB_TRANSITIONS` in `src/ambitious_video/domain/types.py`; the `state` column of the `jobs` table |
| `StepKeys` | `step_key` in `domain/plan.py`: a hash of what a paid step asks for, with no job, attempt, clock or path in it |
| `IdemKey`, `idemKeys` | `idem_key` in `types.py` (the step key alone); the `idem_key` column of the `ledger` table and its `UNIQUE` constraint |
| `ledgerLines` | the number of rows in `ledger` for one step key (`LedgerLine`) |
| `cached`, `calls` | the folders `<cache dir>/<step key>/` and the `step_cache` table; the provider calls made for a step key |
| `Claim` | the start of the `render` command: one transaction that reads the job's state and writes `rendering` |
| `RunPaidStep` | one paid step of a render, under a file lock named after the step key: look in the cache, call the provider only on a miss, store the result, and add the ledger line in one transaction |
| `Finish` | the end of `render`: one transaction that records the output and writes `rendered` |
| `RenderFails`, `Crash` | a render that stops on an error, and a render process that dies |
| `Recover` | marking a job `failed` when it is `rendering` and no render holds it |
| `Retry` | putting a `failed` job back in line |
| `VerifyPasses`, `VerifyRejects` | the `verify` command |
| `schemas/video-script.v1.json` | `Script` and the types it is made of, `ClipType`, `ActionKind`, `Transition`, `VoiceProvider`, `ScriptRule`, `TRACK_ID_PATTERN`, `HEX_COLOR_PATTERN` in `types.py` |

Run `git log db9f60754fc597e1d564a0aff4fd47d3b31f9a4a..HEAD -- src spec schemas examples scripts`. If it lists commits, read them against the table before you trust a pass. Move the commit above forward only after you have confirmed that every row still describes the code at the new commit.

One step in the model is one database transaction in the code. A render in the model holds its job from `Claim` until it finishes, fails or dies, and `Recover` acts only when no render holds the job; the code needs something the operating system releases when a process dies, such as a file lock, for that to be true. If a step is split across transactions, if a second command starts to change a job's state or write ledger lines, if a paid step stops holding its step key's lock from the cache look-up to the stored result, or if the idempotency key starts to depend on the job or the attempt, the model no longer describes the code; change the model before relying on a pass.

## Reading a result

If your agent setup has a `/statechart` skill for TLA+ models (pstack, a public plugin of agent skills, has one: it runs the checker, reports each broken rule as its shortest path in plain words, and draws the states as a chart when the model passes), read every result with it. Refer to it by name; do not copy its commands here. Without it, read TLC's output directly as follows.

When a check fails, `spec/check.sh` prints TLC's full output. Find the line `Error: Invariant <Rule> is violated.`; the rule it names is the one that broke. Below it TLC prints the shortest path to the bad state as numbered states. Each state starts with the step that led to it and lists every variable after that step:

```text
Error: Invariant NoDoubleCharge is violated.
Error: The behavior up to this point is:
State 1: <Initial predicate>
...
State 4: <RunPaidStep("renderer1",1) line 45, col 5 to line 53, col 63 of module AppendWithoutIdemKey>
/\ idemKeys = {<<1>>}
/\ state = [job1 |-> "rendering", job2 |-> "rendering"]
/\ rendering = [renderer1 |-> "job1", renderer2 |-> "job2"]
/\ done = [renderer1 |-> {1}, renderer2 |-> {}]
/\ ledgerLines = <<1, 0>>
/\ cached = {1}
/\ attempt = [job1 |-> 1, job2 |-> 1]
/\ calls = <<1, 0>>
/\ renderedAttempt = [job1 |-> 0, job2 |-> 0]

State 5: <RunPaidStep("renderer2",1) line 45, col 5 to line 53, col 63 of module AppendWithoutIdemKey>
/\ idemKeys = {<<1>>}
/\ state = [job1 |-> "rendering", job2 |-> "rendering"]
/\ rendering = [renderer1 |-> "job1", renderer2 |-> "job2"]
/\ done = [renderer1 |-> {1}, renderer2 |-> {1}]
/\ ledgerLines = <<2, 0>>
/\ cached = {1}
/\ attempt = [job1 |-> 1, job2 |-> 1]
/\ calls = <<1, 0>>
/\ renderedAttempt = [job1 |-> 0, job2 |-> 0]
```

Read the step names down the list and write the path as one line per step before deciding anything. Here: a render takes job1; another render takes job2; the first runs the paid step with step key 1, calls the provider and the ledger gets a line; the second needs the same step key, finds it in the cache and calls nothing, but the ledger gets a second line for the same step key.

To tell a path in the module's words:

- `state` is each job's state.
- `rendering` is the job each render process is working on, or `idle`. A job named here is held by that render.
- `done` is the step keys each render has dealt with in its current attempt.
- `idemKeys` is the idempotency keys in the ledger, each a step key.
- `cached` is the step keys with a stored result, and `calls` the number of provider calls for each step key. A `2` in `calls` is a result paid for twice.
- `ledgerLines` is the number of ledger lines, one number per step key. A `2` is a double charge; a `0` when a job is rendered is a missing charge.
- `attempt` is the number of the job's current attempt, and `renderedAttempt` the attempt whose render last finished (`0` for none). A verified job where the two differ was verified on a stale output.

Other failures look different. `Error: Deadlock reached.` means a job is stuck: read the last state for a job that is not settled and has no step left. `Error: Temporal properties were violated.` means `EveryJobSettles` broke: TLC prints a path that ends in a loop, and some job never settles on it. `Error: Action property OnlyDeclaredMoves is violated.` means the last step moved a job along an arrow that `Moves` does not have.

To run one model by hand and keep the output, from the repo root:

```sh
mise exec -- java -cp ~/.cache/ambitious/tla2tools-*.jar tlc2.TLC -workers 1 -metadir "$(mktemp -d)" -noGenerateSpecTE -config spec/broken/AppendWithoutIdemKey.cfg spec/broken/AppendWithoutIdemKey.tla
```

When a model passes, the `PASS` line gives the number of distinct states TLC checked. `spec/check.sh` fails if that number is too small for the model to be exploring anything.

A `FAIL:` line from `scripts/check_schemas.py` names the file and what to fix.

## Real bug or modeling error

1. Replay the path against the real code, one command or injected failure per step, as a test. If the code does the same, it is a real bug: report it with the path. Do not weaken the rule, and do not change product code unless asked.
2. If the code cannot take one of the steps, the model allows something the code forbids. Find the guard in the code, add it to the model, and say so in the pull request.
3. If a broken variant stops failing, or breaks a different rule, the model changed so that the rule can no longer catch that mistake. Treat that as a failure of the model.

## Changing things on purpose

- Change `spec/RenderJob.tla` first, then make the same change in each file in `spec/broken/`, keeping only its one changed line. `check.sh` fails if a variant drifts further from the model or uses another configuration.
- A new rule needs a new broken variant that breaks it, an entry in `spec/broken/EXPECTED.json` and a row in `spec/README.md`. `check.sh` fails if a rule has no variant.
- A new job state or move goes in `States` and `Moves` in the model and in `JobState` and `JOB_TRANSITIONS` in `types.py` together; `scripts/check_schemas.py` fails when they disagree.
- A new clip type, action, transition, provider or script rule goes in the schema (or in `RULES` in `scripts/check_schemas.py`) and in `types.py` together, with a valid example that uses it and an invalid example for it in `examples/invalid/` and its `EXPECTED.json`.
- A change that makes a valid script invalid belongs in a new schema file with a new version number.
- To move to another TLC build, change `TLA2TOOLS_URL` and `TLA2TOOLS_SHA256` in `.mise.toml` together. To move to other versions of the check's packages, change `scripts/dev-requirements.in` and write `scripts/dev-requirements.txt` again as `schemas/README.md` describes.
- Run the one command, then update the commit in "Covered code".
