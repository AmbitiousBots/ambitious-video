# ambitious-video

The video module is a generic renderer driven by a declarative video script (a JSON or YAML spec). Composable steps such as screen recordings, title cards, voiceover, captions, timed cuts and b-roll are assembled with ffmpeg, with a dry-run cost estimate and a cost ledger. No business or marketing script is built in; scripts are data, and example specs live under examples/. It is a command-line tool with no UI.

The module follows the shared module contract, [MODULE_CONTRACT.md](https://github.com/AmbitiousBots/ambitious-kit/blob/main/MODULE_CONTRACT.md) in ambitious-kit: one command-line binary, JSON in and a JSON envelope out, one SQLite file, deterministic with `--now` and `--seed`, no network except keyed AI calls.

## What is here so far

The commands are not written yet. This repository holds what they will be built on:

| Where | What |
|---|---|
| `schemas/video-script.v1.json` | The video script format. [schemas/README.md](schemas/README.md) explains it. |
| `examples/` | Two scripts as data: `minimal.yaml` needs no key, and `ambitious-marketing.yaml` is a short video about Ambitious. `examples/invalid/` holds scripts that must be rejected. |
| `spec/` | A TLA+ model of a render job: a retry never charges twice, a verified video was rendered, one render per job. [spec/README.md](spec/README.md) explains it. |
| `src/ambitious_video/domain/types.py` | The types the commands will share: the script, jobs and their states, the cost ledger and assets. |
| `verify/formal/SKILL.md` | For coding agents: when and how to rerun the checks, and how to read a failure. |

Check everything from the repo root (needs [mise](https://mise.jdx.dev) and `curl`):

```sh
bash spec/check.sh && python3 scripts/check_schemas.py
```

Part of [Ambitious](https://ambitiousbots.com). Licensed under the Apache License, Version 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
