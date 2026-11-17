# ambitious-video

The video module is a generic renderer driven by a declarative video script (a JSON or YAML spec). Composable steps such as screen recordings, title cards, voiceover, captions, timed cuts and b-roll are assembled with ffmpeg, with a dry-run cost estimate and a cost ledger. No business or marketing script is built in; scripts are data, and example specs live under examples/. It is a command-line tool with no UI.

The module follows the module contract of the small command-line kit it is built on, `ambitious_kit`: one command-line binary, JSON in and a JSON envelope out, one SQLite file, deterministic with `--now` and `--seed`, no network except keyed AI calls. The Python copy of that kit is in `vendor/`, and `vendor/KIT_VERSION` names the pinned version.

Version 0.1.0. One binary, `ambitious-video`, reads a script from `--in <file>`, or from stdin with `--in -`, and prints one JSON envelope. `--help` and `--version` print the usage and the version as envelope data and exit 0. With `--in <file>` stdin is never read, and ffmpeg and ffprobe never see it, so a caller that leaves stdin open (a tool runner, for example) does not make a command hang. Without `--in`, a script piped on stdin is still read: the command waits at most two seconds for the first byte, and once any byte has arrived it reads to the end of stdin, so a slow writer never has its script cut short. A program that generates a script should send it with `--in -`, which waits for the end of stdin however long the first byte takes; on Windows the two-second wait is not available for pipes, so `--in -` is the only way to send a script on stdin there. Paths in a script are relative to the directory that holds the script. The database is `$AMBITIOUS_HOME/video/video.sqlite` and the step cache is `$AMBITIOUS_HOME/video/cache` (`AMBITIOUS_HOME` defaults to `/workspace/ambitious`).

| Command | Access | What it returns |
|---|---|---|
| `guide` | none | A short example script to copy from `data.example.script`, and the steps to render it. |
| `validate` | none | Whether the script is usable, its duration, and any problems. |
| `plan` | none | The ordered steps with a `stepKey` each, an ffmpeg graph, the caption segments, the encoding, the assets the script names with their hashes, and `planSha256`. |
| `estimate` | none | A dry-run price from `pricing.json`. No provider is called. |
| `render` | write | Renders the script. With `jobId` alone, retries that failed or abandoned job. Lists each paid step as cached or billed. |
| `verify` | write | Checks duration, frame count, resolution, audio, encoding, and title and cut frames of a rendered job. |
| `jobs` | read | The jobs in the database. |
| `ledger` | read | The cost lines, exactly one per provider call. A line's idempotency key is the step key (`<stepKey>#2`, `#3`, ... when a removed result had to be called for again), so a retry or a new job with the same inputs does not add a line. |
| `cache list` | read | The entries of the step cache: step key, folder, provider, size, when it was made and last used. |
| `cache prune` | write | Removes entries last used more than `--older-than <days>` ago. Nothing is ever removed without this command. |
| `selfcheck` | write | Runtime, binary checksum, database integrity, and ffmpeg, Pillow, and Playwright, with a line in `data.warnings` for each missing tool. |
| `commands` | none | Every command, including this one. |
| `help` | none | The usage, the commands and the flags. `--help` anywhere on the command line does the same. |
| `version` | none | The version, also in `meta.version`. `--version` does the same. |

## A first video

`guide` prints a short example script and the steps to render it. The script is also `examples/titles-and-captions.yaml`: two title cards, captions and one crossfade. It needs no media files and no key.

```sh
ambitious-video guide                         # copy data.example.script into video.yaml
ambitious-video validate --in video.yaml
ambitious-video estimate --in video.yaml      # 0, because nothing is spoken
ambitious-video render --in video.yaml        # writes out/titles-and-captions.mp4 next to video.yaml
```

Leave `voice` unset unless you want spoken voiceover and have set the provider key in the environment.

## What a render does

`render` of a script whose `voice` is set needs `XAI_API_KEY` only when a line has to be spoken that is not in the step cache yet. The key is read from the environment for that call and is not stored. Without `voice`, voiceover clips are captions only and no key is needed. Speech is priced at the public text-to-speech rate in `pricing.json` (15 USD per 1 million characters). A voice named `default` uses the provider's default voice. The spoken language is detected.

## Plans, step keys and the step cache

`plan` prints canonical JSON: keys are sorted, and there is no time, no absolute path and no environment value in it. The same script and files give the same bytes in every process, whatever `--now` and `--seed` are. `planSha256` is the SHA-256 of that plan, and `scriptSchemaVersion` is the version of the script format it was made from. `render` returns the same `planSha256`.

Every step has a `stepKey`: the SHA-256 of its kind, provider, model, adapter version, parameters, seed and input hashes, written as canonical JSON. A file the step reads goes in as the hash of its bytes. The key of a paid step names only what is sent to the provider (for speech: the words and the voice), not the job, the clock or where the line sits in the video. `output.stepKey` is the key of the finished video; `output.encode`, the bytes of the `brand.font` file and the key of every step are among its inputs, so changing one step changes that step's key and the output's key and leaves the other steps' keys alone. For a provider that takes a seed, `provider_seed` derives it from `--seed` and the step key. Speech takes none, so `--seed` does not change a speech key, and no provider that takes a seed ships yet.

The result of a paid step is kept in `<cache dir>/<stepKey>/` (`output.bin` and a `meta.json` with its hash) and listed in the `step_cache` table. Before a paid call, `render` looks there. A hit calls nothing, needs no key, and adds no ledger line; a miss calls the provider once, stores the result and adds one ledger line whose idempotency key is the step key. When the chosen cache folder has no result but another cache folder this database has used does, the verified result is copied in instead: no call, no key and no line. Renders that use one database take turns on a step key, also when they run at the same time with different cache folders, so the second one copies the result of the first. A second job with the same words and voice therefore costs nothing, and two renders with every paid step cached write the same file on the same machine and toolchain. `render` lists each paid step with `cached` and the `usd` this call was charged, and `costUsd` is their sum.

The cache folder is `$AMBITIOUS_HOME/video/cache`. A script can name another with `cache: { dir: <folder below the script> }`, and `--cache <dir>` on `render`, `cache list` and `cache prune` overrides both. Entries are never removed automatically. `cache prune --older-than 90` removes the entries last used more than 90 days ago; an entry that a running render holds is left alone, and `--older-than` takes whole days from 0 to 36500. The ledger holds one line per provider call, and a step key is called again only if its result was removed: by `cache prune`, or by deleting its folder by hand. That next call is paid for and gets its own line, with the idempotency key `<stepKey>#2` (then `#3`, and so on), so the ledger total stays equal to what `render` reported as `costUsd`. An entry whose file no longer matches its recorded hash is refused with `E_CACHE`.

A picture is scaled to cover the output size. A video clip of another size is scaled to fit inside the output size and centered on the brand background color, so every clip fills its scheduled time whatever the size of its source. A logo keeps its transparency: the background shows through it.

Captions are drawn only by a track with `from: <id of a track with voiceover clips>`. Each voiceover clip is shown during its own time and not before or after. A clip with more than one sentence shows one sentence at a time, each for a share of the clip that matches its length in characters. A script with no such track draws no captions. `plan` lists the segments in `data.captions`.

`brand.font` is the path of a font file next to the script, such as `fonts/name.ttf`, and is used for title cards and captions. Fonts installed on the computer are never searched, so the same script draws the same frames on every computer. `validate` and `render` fail with `E_MEDIA` when `brand.font` is a font name, a path outside the script folder, a missing file, or a file that is not a font. Without `brand.font`, the font built into Pillow is used.

`output.encode` chooses how the video is stored:

| `output.encode` | Video | Use it for |
|---|---|---|
| `web` (the default) | H.264 High profile, `yuv420p` pixels, BT.709 colors, with the index at the start of the file (`+faststart`). | Playing in browsers and common players. |
| `lossless` | H.264 as exact RGB (`libx264rgb`, `-qp 0`). Many browsers and players cannot show it. | Comparing a video pixel by pixel, and files whose bytes repeat. |

Frames that the module draws (title cards, still images, captions, and cuts between them) depend on the script and the assets, not on the clock. The same script with the same `--now` and `--seed` gives the same plan and the same drawn frames. A `lossless` file also has the same bytes each time. A `web` file has the same bytes with the same ffmpeg build, and can differ slightly between builds. Screen recordings follow the page named in the script, so they can change between runs.

`verify` draws the title and cut frames again and compares them with the same frames of the video. For `lossless` every pixel must be equal. For `web` the drawn frame is first converted to `yuv420p` and back in the same way as the video, and then two limits apply, on a scale of 0 to 255: after averaging each 8 by 8 block of pixels the mean difference per color channel is at most 2, and after averaging each 4 by 4 block of pixels no block differs by more than 32. Averaging first lets encoder noise on grain and fine texture cancel inside a block, while an overall shift in color or brightness still counts in full. `verify` also checks that the file is stored the way `output.encode` says.

Playwright is needed only for `screen_recording` clips. When it is missing, `selfcheck` still succeeds, and says so in `data.warnings` and in `data.tools.playwright.hint`.

A render holds a lock that the operating system releases if the process dies. The next retry of that job id takes the lock, marks the abandoned render failed, and continues. The state moves are the ones in `JOB_TRANSITIONS`.

## Module error codes

Shared codes (`E_USAGE`, `E_INPUT_SCHEMA`, `E_FORBIDDEN_PATH`, `E_NO_KEY`, `E_DB`, `E_MIGRATION`, `E_INTERNAL`) are defined by the module contract. These codes are this module's:

| Code | Exit | Meaning | Typical hint |
|---|---|---|---|
| `E_UNKNOWN_STEP` | 1 | A clip `type` is not one of the six known steps. | Use one of the types in the script schema. |
| `E_JOB_STATE` | 1 | The job is not in a state that allows the requested move. | Read `jobs` and retry only a failed or abandoned job. |
| `E_OVERLAP` | 1 | Two clips on one track overlap. | Move a clip or shorten it. |
| `E_MEDIA` | 1 | A file is missing, `in` / `out` falls outside it, or `brand.font` is not a font file next to the script. | Put the file next to the script, or fix the times. |
| `E_RANGE` | 1 | A cut is not on a clip boundary, or the video is longer than 1800 seconds. | Place the cut where one clip ends and the next begins. |
| `E_TOOL` | 1 | ffmpeg, ffprobe, Pillow or Playwright is missing, or ffmpeg failed while rendering. | Install the missing tool and run the command again. |
| `E_SPEECH` | 1 | The speech service rejected the call. | Check the voice name and the key. |
| `E_VERIFY` | 1 | A rendered file failed a check. There is one error for each failed check, and its message says what differs: the duration or the frame count against the script's, how the video is stored against `output.encode`, a frame that is missing or that differs, or a file that is not a video. The job moves to `failed`. | Read `data.checks`: each check has `ok`, and `expected` and `actual` where they apply. |
| `E_CACHE` | 1 | A step cache entry does not match its recorded hash. | Delete the folder named in `data.stepKey` and render again. |

## Layout

| Where | What |
|---|---|
| `schemas/video-script.v1.json` | The video script format. [schemas/README.md](schemas/README.md) explains it. |
| `examples/` | Three scripts as data: `minimal.yaml` and `titles-and-captions.yaml` need no key, and `all-clip-types.yaml` uses every clip type and a voice. `examples/invalid/` holds scripts that must be rejected. |
| `examples/samples/` | Four sample projects for made-up businesses (a plumber, an accounting office, a fitness studio, a bookshop), all SAMPLE DATA. Each has a `brand.yaml`, a `script.yaml` and placeholder pictures drawn by `scripts/make_sample_assets.py`, and needs no key. |
| `src/ambitious_video/steps/` | One module per clip type. Each `compile` is pure. Register a new type in `steps/__init__.py`. |
| `spec/` | A TLA+ model of a render job: a retry never charges twice, the ledger holds one line per provider call, a step key is called again only if its result was removed, a verified video was rendered, one render per job. [spec/README.md](spec/README.md) explains it. |
| `src/ambitious_video/domain/plan.py` | Canonical JSON, `step_key` and `planSha256`. |
| `src/ambitious_video/db/step_cache.py` | The step cache: the folders and the `step_cache` table. |
| `src/ambitious_video/domain/types.py` | The types the commands share: the script, jobs and their states, the cost ledger and assets. |
| `verify/formal/SKILL.md` | For coding agents: when and how to rerun the checks, and how to read a failure. |
| `pricing.json` | The dry-run price table, inside the package. |
| `guide.yaml`, `help.json` | What `guide` and `help` print, inside the package. `guide.yaml` is a copy of `examples/titles-and-captions.yaml`, and a test keeps the two the same. |

Build `dist/ambitious-video-0.1.0.pyz` with `python scripts/build.py`. It runs on Python 3.13 with Pillow and ffmpeg installed. PyYAML is bundled. The check from a fresh tree:

```sh
bash verify/run
```

The model check (needs [mise](https://mise.jdx.dev) and `curl`):

```sh
bash spec/check.sh && python3 scripts/check_schemas.py
```

Maintained by the project contributors. Licensed under the Apache License, Version 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). To contribute, read [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and [SECURITY.md](SECURITY.md).
