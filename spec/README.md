# Formal model of a render job

Rendering a video can cost money: a step such as a voiceover is a paid call to an AI provider. A render can also fail or be killed half way, and then it is tried again. This folder holds a small model of the life of a render job and checks, for every order of steps, that a retry never charges twice, that the ledger holds one line per provider call, that a step key is called again only if its result was removed, that a job is never marked verified on an output it did not just render, and that two renders never work on one job at once. The model was written before the `render` and `verify` code, so the code is written against rules that are already checked.

Run it from the repo root:

```sh
bash spec/check.sh
```

The script needs [mise](https://mise.jdx.dev) (it installs the Java and Python pinned in `.mise.toml`) and `curl`. It downloads TLC, the model checker for the TLA+ language, as `tla2tools.jar` (a build of the 1.8.0 line, the same one the vendored kit, `ambitious_kit`, pins) from the address pinned in `.mise.toml`, keeps it in `~/.cache/ambitious/` and refuses a file whose SHA-256 differs from the pin. A run takes a few seconds.

## `RenderJob.tla`: the model

A job moves between six states:

```text
queued -> rendering -> rendered -> verified
              |            |
              v            v
           failed  <-------+
              |
              v
           retried -> rendering
```

| Step in the model | What it stands for |
|---|---|
| `Claim` | A render takes a waiting job (`queued` or `retried`) and marks it `rendering`. |
| `RunPaidStep` | The render needs one paid step. If the cache holds no result for its step key, the provider is called once and the result is stored. The line of the latest call is added to the cost ledger, unless the ledger already holds that call's idempotency key. The key is the step key plus the number of the call: the step key is a hash of what the step asks the provider for, and the number is 1 unless the result was removed and called for again. It does not name the job or the attempt, so it is the same on every attempt and in every job with the same inputs. |
| `Remove` | The stored result of a step key is removed, by `cache prune` or by deleting its folder by hand, at any moment. |
| `Finish` | The render has run every step. It records the output and marks the job `rendered`. |
| `RenderFails` | The render hits an error at any point and marks the job `failed`. |
| `Crash` | The render process dies at any point and writes nothing. The job is left `rendering`. |
| `Recover` | A job left `rendering` by a render that is no longer running is marked `failed`. |
| `Retry` | A `failed` job is put back in line as `retried`. |
| `VerifyPasses`, `VerifyRejects` | `verify` checks the output of a `rendered` job and marks it `verified` or `failed`. |

The model keeps four things that are only there to state the rules: `ledgerLines`, the number of ledger lines for each step key, `calls`, the number of provider calls for each step key, `removals`, the number of times its result was removed, and `renderedAttempt`, the attempt whose render last finished. The code needs no column for any of them; it reads the number of the call from the ledger lines the step key already has. `cached` is the set of step keys with a stored result: the folders of the step cache.

The rules, checked in every reachable state (189,355 distinct states):

| Rule | In plain words | Harm if it breaks |
|---|---|---|
| `NoDoubleCharge` | The ledger never holds more lines for a step key than provider calls were made for it, however often a job is retried and however many jobs need that step. | The recorded cost is higher than what was paid. |
| `CalledAgainOnlyAfterRemoval` | The provider is called once for a step key, across retries and across jobs, and once more only each time its stored result was removed. | The owner pays the provider again for a result that is already stored. |
| `RenderedIsFullyCharged` | Once a job is `rendered` or `verified`, every paid step was called for and has exactly one ledger line per provider call, written by the job that made the call. | A cost goes missing from the ledger. |
| `VerifiedImpliesRendered` | A `verified` job was rendered to the end on its latest attempt, and that output is the one that was checked. | A video is reported as checked when the file that was checked is stale, or the render never finished. |
| `OneActiveRenderPerJob` | At most one render works on a job, and only while the job is `rendering`. | Two renders write the same output file, or a job changes state under a render that is still running. |

`TypeOK` is also checked: every value stays in its allowed set.

Three more checks run on the same model:

- **No job is stuck.** TLC reports an error if it reaches a state with no next step, other than all jobs being settled (`verified`, or `failed` with no attempts left). Without `Recover`, a crashed render leaves its job `rendering` forever, and this check fails.
- **`EveryJobSettles`.** If steps that can happen do happen, every job ends `verified` or `failed` for good.
- **`OnlyDeclaredMoves`** (`RenderJobMoves.cfg`). A job only ever moves along the arrows in the picture above, the set `Moves` in the model. `python3 scripts/check_schemas.py` fails when `Moves` differs from `JOB_TRANSITIONS` in `src/ambitious_video/domain/types.py`, so the table the code uses is the one that was checked.

### Bounds

Two jobs, two renders that can run at the same time, two step keys, three attempts per job, and one removal per step key. Both jobs need both step keys: two jobs with the same inputs is the case where sharing matters. Steps that cost nothing do not touch the ledger and are left out. The limits on attempts and removals keep the model finite; nothing in the rules depends on the numbers.

## `broken/`: showing that each rule can fail

Each file in `spec/broken/` is the model with one line changed to a plausible mistake. `check.sh` requires each to break the rule listed for it in `broken/EXPECTED.json` and no other rule first, to use the same configuration as the model, and to differ from the model in at most two lines. It also requires every rule in `RenderJob.cfg` to have a variant that breaks it.

| Variant | The mistake | Rule it must break |
|---|---|---|
| `AppendWithoutIdemKey` | A paid step adds its ledger line without looking for its idempotency key. | `NoDoubleCharge` |
| `IdemKeyWithoutStep` | The idempotency key does not name the step key. | `RenderedIsFullyCharged` |
| `IdemKeyWithJob` | The idempotency key names the job as well as the step key and the call, so a second job adds a line for a step served from the cache. | `NoDoubleCharge` |
| `IdemKeyWithoutCall` | The idempotency key names the step key but not the call, so the call made after a result was removed is never recorded. | `RenderedIsFullyCharged` |
| `CallWithoutCacheCheck` | A paid step calls the provider without looking in the cache for its step key. | `CalledAgainOnlyAfterRemoval` |
| `VerifyWithoutStateCheck` | `verify` accepts any job that has an output file, without checking that the job is `rendered`. | `VerifiedImpliesRendered` |
| `ClaimWithoutStateCheck` | A render takes a job without checking that it is waiting. | `OneActiveRenderPerJob` |
| `RecoverWithoutLockCheck` | Recovery marks a `rendering` job `failed` without checking that its render has stopped. | `OneActiveRenderPerJob` |

`EXPECTED.json` has one entry per variant: `{ "<variant>": { "invariant": "<rule>", "because": "<plain sentence>" } }`. This is the shape the shared TLC runner of the kit, `ambitious_kit`, reads, and each variant sits next to a `.cfg` of the same name as that runner expects, so it can replace `check.sh` without changes here.

The three extra checks have no file in `broken/`, because the shared runner only expects broken invariants. Each was seen to fail once by hand: dropping `Recover` gives a stuck state, dropping the fairness of `Retry` breaks `EveryJobSettles`, and dropping a pair from `Moves` breaks `OnlyDeclaredMoves`.

## Which code this constrains

The commands live in `src/ambitious_video/cli/engine.py`. The moves and the idempotent ledger are `domain/jobs.py`, `domain/schedule.py` and `db/store.py`. The lock is `db/lock.py`.

| Code | Constrained by |
|---|---|
| `JOB_TRANSITIONS` in `src/ambitious_video/domain/types.py` | `Moves`; the two are compared on every check |
| `step_key` in `src/ambitious_video/domain/plan.py` | The members of `StepKeys`: a SHA-256 of the step's kind, provider, model, adapter version, parameters, seed and input hashes. Nothing about the job, the attempt, the clock or the machine goes in. |
| `idem_key` in `types.py` | `IdemKey`: the step key and the number of the call (`<step key>` for the first call, `<step key>#2` for the next), never the job or the attempt |
| A paid step in `cli/engine.py` | One `RunPaidStep`. The render holds two file locks named after the step key while it looks in the cache, calls the provider and stores the result: first one beside the database, then one in the cache folder. Two renders on one database therefore never call for one step key at the same time, whichever cache folders they use, and neither do two databases that share a cache folder. |
| Adding a ledger line | Count the lines the step key already has and insert the line of the next call, in one database transaction. The `UNIQUE` constraint on `idem_key` is what makes this hold when two writers race. The code adds the line in the render that made the call and adds nothing on a cache hit; in the model a cached step key always has the line of its latest call already, so both say the same. |
| `step_cache` table and `<cache dir>/<step key>/` | `cached`. A row is written as `calling` before the provider call and as `ready` in the transaction that adds the ledger line. A later render that finds a stored result whose row is still `calling` adds the missing line. |
| `cache prune` and a deleted cache folder | `Remove`. `cache prune` takes the step key's file lock in the cache folder first and skips an entry that a render holds. |
| Taking a job | One `Claim`: read the state and write `rendering` in one transaction, so that only one render wins |
| A running render | Holds something for the whole render that the operating system releases when the process dies, such as a file lock. `Recover` must take the same thing before it marks the job `failed`. |
| `verify` | Refuses a job that is not `rendered`, even when an output file exists |

## What is not covered

- The model checks the design, not the Python. Tests tie the code to it: `test/domain/test_schedule.py` is the property test that drives the job functions with generated orders of claim, step, remove, fail, crash, recover, retry and verify, and checks the same rules.
- Money spent at the provider. If a render dies after a paid call returns and before its result is stored, the next attempt calls the provider again. No line is written for that lost call, so the ledger can be lower than the provider's bill by that one call. Closing this needs the provider to accept an idempotency key, which is outside this module.
- Two databases that share one cache folder, or one database used with several cache folders. The model has one ledger and one cache. In the code, a paid step holds a lock beside the database for its step key, so renders on one database take turns on a step key even when their cache folders differ, and a render that misses in its own folder copies a verified result from another folder the same database has used instead of calling. `test/test_step_cache.py` covers both, renders one after the other and renders at the same time; the model does not. Two databases that share a folder each keep their own ledger: the second finds the stored result and adds no line.
- More than one removal per step key.
- More than two jobs, renders or step keys, and more than three attempts.
- The content of a render: what `verify` checks, the ffmpeg graph and the script are not modeled. `verify` is a step that passes or rejects.
- Crashes in the middle of a database write; each step is one transaction.
