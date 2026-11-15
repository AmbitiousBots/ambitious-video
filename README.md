# ambitious-video

The video module is a generic renderer driven by a declarative video script (a JSON or YAML spec). Composable steps such as screen recordings, title cards, voiceover, captions, timed cuts and b-roll are assembled with ffmpeg, with a dry-run cost estimate and a cost ledger. No business or marketing script is built in; scripts are data, and example specs live under examples/. It is a command-line tool with no UI.

The module follows the shared module contract, [MODULE_CONTRACT.md](https://github.com/AmbitiousBots/ambitious-kit/blob/main/MODULE_CONTRACT.md) in ambitious-kit: one command-line binary, JSON in and a JSON envelope out, one SQLite file, deterministic with `--now` and `--seed`, no network except keyed AI calls.

Version 0.1.0. One binary, `ambitious-video`, reads a script on stdin or from `--in` and prints one JSON envelope. Paths in a script are relative to the directory that holds the script. The database is `$AMBITIOUS_HOME/video/video.sqlite` (`AMBITIOUS_HOME` defaults to `/workspace/ambitious`).

| Command | Access | What it returns |
|---|---|---|
| `validate` | none | Whether the script is usable, its duration, and any problems. |
| `plan` | none | The ordered steps, an ffmpeg graph, and the assets the script names. |
| `estimate` | none | A dry-run price from `pricing.json`. No provider is called. |
| `render` | write | Renders the script. With `jobId` alone, retries that failed or abandoned job. |
| `verify` | write | Checks duration, resolution, audio, and title and cut frames of a rendered job. |
| `jobs` | read | The jobs in the database. |
| `ledger` | read | The cost lines. A line's idempotency key does not include the attempt, so a retry does not add a second line. |
| `selfcheck` | write | Runtime, binary checksum, database integrity, and ffmpeg, Pillow, and Playwright. |
| `commands` | none | Every command, including this one. |

`render` of a script whose `voice` is set needs `XAI_API_KEY` for that call. The key is not stored. Without `voice`, voiceover clips are captions only and no key is needed. Speech is priced at the public text-to-speech rate in `pricing.json` (15 USD per 1 million characters). A voice named `default` uses the provider's default voice. The spoken language is detected.

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

## Layout

| Where | What |
|---|---|
| `schemas/video-script.v1.json` | The video script format. [schemas/README.md](schemas/README.md) explains it. |
| `examples/` | Two scripts as data: `minimal.yaml` needs no key, and `ambitious-marketing.yaml` is a short video about Ambitious. `examples/invalid/` holds scripts that must be rejected. |
| `src/ambitious_video/steps/` | One module per clip type. Each `compile` is pure. Register a new type in `steps/__init__.py`. |
| `spec/` | A TLA+ model of a render job: a retry never charges twice, a verified video was rendered, one render per job. [spec/README.md](spec/README.md) explains it. |
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

Part of [Ambitious](https://ambitiousbots.com). Licensed under the Apache License, Version 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
