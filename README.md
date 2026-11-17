# ambitious-video

The video module is a generic renderer driven by a declarative video script (a JSON or YAML spec). Composable steps such as screen recordings, title cards, voiceover, captions, timed cuts and b-roll are assembled with ffmpeg, with a dry-run cost estimate and a cost ledger. No business or marketing script is built in; scripts are data, and example specs live under examples/. It is a command-line tool with no UI.

The module follows the shared module contract, [MODULE_CONTRACT.md](https://github.com/AmbitiousBots/ambitious-kit/blob/kit-v0.1.1/MODULE_CONTRACT.md) in ambitious-kit at kit-v0.1.1: one command-line binary, JSON in and a JSON envelope out, one SQLite file, deterministic with `--now` and `--seed`, no network except keyed AI calls. The Python copy of that kit is in `vendor/`.

Version 0.1.0. One binary, `ambitious-video`, reads a script on stdin or from `--in` and prints one JSON envelope. Paths in a script are relative to the directory that holds the script. The database is `$AMBITIOUS_HOME/video/video.sqlite` and the step cache is `$AMBITIOUS_HOME/video/cache` (`AMBITIOUS_HOME` defaults to `/workspace/ambitious`).

| Command | Access | What it returns |
|---|---|---|
| `validate` | none | Whether the script is usable, its duration, and any problems. |
| `plan` | none | The ordered steps with a `stepKey` each, an ffmpeg graph, the assets the script names with their hashes, and `planSha256`. |
| `estimate` | none | A dry-run price from `pricing.json`. No provider is called. |
| `render` | write | Renders the script. With `jobId` alone, retries that failed or abandoned job. Lists each paid step as cached or billed. |
| `verify` | write | Checks duration, resolution, audio, and title and cut frames of a rendered job. |
| `jobs` | read | The jobs in the database. |
| `ledger` | read | The cost lines, one per provider call. A line's idempotency key is the step key, so a retry or a new job with the same inputs does not add a second line. |
| `cache list` | read | The entries of the step cache: step key, folder, provider, size, when it was made and last used. |
| `cache prune` | write | Removes entries last used more than `--older-than <days>` ago. Nothing is ever removed without this command. |
| `selfcheck` | write | Runtime, binary checksum, database integrity, and ffmpeg, Pillow, and Playwright. |
| `commands` | none | Every command, including this one. |

`render` of a script whose `voice` is set needs `XAI_API_KEY` only when a line has to be spoken that is not in the step cache yet. The key is read from the environment for that call and is not stored. Without `voice`, voiceover clips are captions only and no key is needed. Speech is priced at the public text-to-speech rate in `pricing.json` (15 USD per 1 million characters). A voice named `default` uses the provider's default voice. The spoken language is detected.

## Plans, step keys and the step cache

`plan` prints canonical JSON: keys are sorted, and there is no time, no absolute path and no environment value in it. The same script and files give the same bytes in every process, whatever `--now` and `--seed` are. `planSha256` is the SHA-256 of that plan, and `scriptSchemaVersion` is the version of the script format it was made from. `render` returns the same `planSha256`.

Every step has a `stepKey`: the SHA-256 of its kind, provider, model, adapter version, parameters, seed and input hashes, written as canonical JSON. A file the step reads goes in as the hash of its bytes. The key of a paid step names only what is sent to the provider (for speech: the words and the voice), not the job, the clock or where the line sits in the video. `output.stepKey` is the key of the finished video; the key of every step is one of its inputs, so changing one step changes that step's key and the output's key and leaves the other steps' keys alone. For a provider that takes a seed, `provider_seed` derives it from `--seed` and the step key. Speech takes none, so `--seed` does not change a speech key, and no provider that takes a seed ships yet.

The result of a paid step is kept in `<cache dir>/<stepKey>/` (`output.bin` and a `meta.json` with its hash) and listed in the `step_cache` table. Before a paid call, `render` looks there. A hit calls nothing, needs no key, and adds no ledger line; a miss calls the provider once, stores the result and adds one ledger line whose idempotency key is the step key. A second job with the same words and voice therefore costs nothing, and two renders with every paid step cached write the same file on the same machine and toolchain. `render` lists each paid step with `cached` and the `usd` this call was charged, and `costUsd` is their sum.

The cache folder is `$AMBITIOUS_HOME/video/cache`. A script can name another with `cache: { dir: <folder below the script> }`, and `--cache <dir>` on `render`, `cache list` and `cache prune` overrides both. Entries are never removed automatically. `cache prune --older-than 90` removes the entries last used more than 90 days ago; a removed step is called and paid for again the next time a render needs it, and the ledger keeps its one line for that step key. An entry whose file no longer matches its recorded hash is refused with `E_CACHE`.

Frames that the module draws (title cards, still images, captions, and cuts between them) depend on the script and the assets, not on the clock. Screen recordings follow the page named in the script, so they can change between runs. `verify` hashes the drawn frames against the same drawing and checks that a screen or video frame was not required for that hash.

A render holds a lock that the operating system releases if the process dies. The next retry of that job id takes the lock, marks the abandoned render failed, and continues. The state moves are the ones in `JOB_TRANSITIONS`.

## Module error codes

Shared codes (`E_USAGE`, `E_INPUT_SCHEMA`, `E_FORBIDDEN_PATH`, `E_NO_KEY`, `E_DB`, `E_MIGRATION`, `E_INTERNAL`) are defined by the module contract. These codes are this module's:

| Code | Exit | Meaning | Typical hint |
|---|---|---|---|
| `E_UNKNOWN_STEP` | 1 | A clip `type` is not one of the six known steps. | Use one of the types in the script schema. |
| `E_JOB_STATE` | 1 | The job is not in a state that allows the requested move. | Read `jobs` and retry only a failed or abandoned job. |
| `E_OVERLAP` | 1 | Two clips on one track overlap. | Move a clip or shorten it. |
| `E_MEDIA` | 1 | A file is missing, or `in` / `out` falls outside it. | Put the file next to the script, or fix the times. |
| `E_RANGE` | 1 | A cut is not on a clip boundary, or the video is longer than 1800 seconds. | Place the cut where one clip ends and the next begins. |
| `E_TOOL` | 1 | ffmpeg or Pillow is not available. | Install the missing tool and run the command again. |
| `E_SPEECH` | 1 | The speech service rejected the call. | Check the voice name and the key. |
| `E_VERIFY` | 1 | A rendered file failed a check. The job moves to `failed`. | Read `data.checks`. |
| `E_CACHE` | 1 | A step cache entry does not match its recorded hash. | Delete the folder named in `data.stepKey` and render again. |

## Layout

| Where | What |
|---|---|
| `schemas/video-script.v1.json` | The video script format. [schemas/README.md](schemas/README.md) explains it. |
| `examples/` | Two scripts as data: `minimal.yaml` needs no key, and `all-clip-types.yaml` uses every clip type and a voice. `examples/invalid/` holds scripts that must be rejected. |
| `examples/samples/` | Four sample projects for made-up businesses (a plumber, an accounting office, a fitness studio, a bookshop), all SAMPLE DATA. Each has a `brand.yaml`, a `script.yaml` and placeholder pictures drawn by `scripts/make_sample_assets.py`, and needs no key. |
| `src/ambitious_video/steps/` | One module per clip type. Each `compile` is pure. Register a new type in `steps/__init__.py`. |
| `spec/` | A TLA+ model of a render job: a retry never charges twice, a step key is called and charged at most once across jobs, a verified video was rendered, one render per job. [spec/README.md](spec/README.md) explains it. |
| `src/ambitious_video/domain/plan.py` | Canonical JSON, `step_key` and `planSha256`. |
| `src/ambitious_video/db/step_cache.py` | The step cache: the folders and the `step_cache` table. |
| `src/ambitious_video/domain/types.py` | The types the commands share: the script, jobs and their states, the cost ledger and assets. |
| `verify/formal/SKILL.md` | For coding agents: when and how to rerun the checks, and how to read a failure. |
| `pricing.json` | The dry-run price table, inside the package. |

Build `dist/ambitious-video-0.1.0.pyz` with `python scripts/build.py`. It runs on Python 3.13 with Pillow and ffmpeg installed. PyYAML is bundled. The check from a fresh tree:

```sh
bash verify/run
```

The model check (needs [mise](https://mise.jdx.dev) and `curl`):

```sh
bash spec/check.sh && python3 scripts/check_schemas.py
```

Part of [Ambitious](https://ambitiousbots.com). Licensed under the Apache License, Version 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). To contribute, read [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and [SECURITY.md](SECURITY.md).
