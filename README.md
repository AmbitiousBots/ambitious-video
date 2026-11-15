# ambitious-video

The video module is a generic renderer driven by a declarative video script (a JSON or YAML spec). Composable steps such as screen recordings, title cards, voiceover, captions, timed cuts and b-roll are assembled with ffmpeg, with a dry-run cost estimate and a cost ledger. No business or marketing script is built in; scripts are data, and example specs live under examples/. It is a command-line tool with no UI.

The module follows the shared module contract, [MODULE_CONTRACT.md](https://github.com/AmbitiousBots/ambitious-kit/blob/main/MODULE_CONTRACT.md) in ambitious-kit: one command-line binary, JSON in and a JSON envelope out, one SQLite file, deterministic with `--now` and `--seed`, no network except keyed AI calls.

Part of [Ambitious](https://ambitiousbots.com). Licensed under the Apache License, Version 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE).
