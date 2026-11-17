# Contributing

ambitious-video is a command-line renderer. A script is data, so the same tool can make a video for any industry. Keep it that way: do not build a business, a brand, or an industry into the program. `test/test_name_scan.py` fails when a company's or a person's name appears in `src/`, `schemas/`, `templates/` or `examples/`, in a file at the top of the repository (`README.md`, `NOTICE`, this file and the others), in `.github/`, or in any other Markdown file outside `vendor/`. There is one exception: `NOTICE` carries the line `Copyright 2026 Ambitious Corporation`, which names the holder of the project's copyright. That name belongs in `NOTICE` only. Do not add it to `LICENSE`, to source file headers, to templates or examples, or to anything the program writes; what a rendered video says about its owner is up to the person who made it. Sample content is fictional and says SAMPLE DATA. Write documentation for an outside reader: the authors are "the project contributors".

## Set up

Install [mise](https://mise.jdx.dev) 2026.11.8, then from this repository:

```sh
mise trust && mise install
```

That installs the Java, Node 24.22.3 and Python 3.13.18 pinned in `.mise.toml`. The renderer also needs ffmpeg. Install the Python packages, including Pillow, from the pinned list:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary :all: --requirement scripts/dev-requirements.txt
```

## Checks

```sh
bash verify/run
node scripts/check-standalone.mjs
bash spec/check.sh && python3 scripts/check_schemas.py
```

`verify/run` renders `examples/minimal.yaml`, the script that `guide` prints, and each sample project from its starter template, which need no API key. After changing a starter template in `templates/`, copy it to `src/ambitious_video/templates/` and run `expand` again for the samples that use it; the checks fail until both are done. Do not point a check at a paid speech call. `verify/run` removes `XAI_API_KEY` and `ELEVENLABS_API_KEY` from the environment for that run.

## Secrets

Never put an API key in a file, a commit, an issue, a pull request, or a log. A paid step reads its provider's key from the environment (`XAI_API_KEY` for `xai-speech`, `ELEVENLABS_API_KEY` for `elevenlabs-speech`) for that one call. The key is not stored and is not printed: `test/test_secrets_offline.py` renders with a made-up key value and then searches the database, the cache, the output and everything printed for it. A new provider or command must keep that test passing. Tests never call a provider: they use a fake HTTP server on 127.0.0.1 or a stubbed `urlopen`.

## Pull requests

Open an issue to discuss a change, or a pull request that makes it. Use the pull request template. Before you push, run the checks above.

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) and [SECURITY.md](SECURITY.md).
