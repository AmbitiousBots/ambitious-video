# ambitious-video

The video module is a generic renderer driven by a declarative video script (a JSON or YAML spec). Composable steps such as screen recordings, title cards, speech, generated scenes, lip-sync, captions, timed cuts and b-roll are assembled with ffmpeg, with a dry-run cost estimate and a cost ledger. No business or marketing script is built in; scripts are data, and example specs live under examples/. It is a command-line tool with no UI.

The module follows the module contract of the small command-line kit it is built on, `ambitious_kit`: one command-line binary, JSON in and a JSON envelope out, one SQLite file, deterministic with `--now` and `--seed`, no network except keyed AI calls. The Python copy of that kit is in `vendor/`, and `vendor/KIT_VERSION` names the pinned version.

Version 0.1.0. One binary, `ambitious-video`, reads a script from `--in <file>`, or from stdin with `--in -`, and prints one JSON envelope. `--help` and `--version` print the usage and the version as envelope data and exit 0. With `--in <file>` stdin is never read, and ffmpeg and ffprobe never see it, so a caller that leaves stdin open (a tool runner, for example) does not make a command hang. Without `--in`, a script piped on stdin is still read: the command waits at most two seconds for the first byte, and once any byte has arrived it reads to the end of stdin, so a slow writer never has its script cut short. A program that generates a script should send it with `--in -`, which waits for the end of stdin however long the first byte takes; on Windows the two-second wait is not available for pipes, so `--in -` is the only way to send a script on stdin there. Paths in a script are relative to the directory that holds the script. The database is `$AMBITIOUS_HOME/video/video.sqlite` and the step cache is `$AMBITIOUS_HOME/video/cache` (`AMBITIOUS_HOME` defaults to `/workspace/ambitious`).

| Command | Access | What it returns |
|---|---|---|
| `guide` | none | A short example script to copy from `data.example.script`, the starter templates with their inputs in `data.templates`, and the steps to render. |
| `expand` | none | The video script made from a template (`--template <file or starter id>`) and an inputs file, in `data.script`, with each fallback that was taken. `--out <file>` also writes it. |
| `validate` | none | Whether the script is usable, its duration, and any problems. |
| `plan` | none | The ordered steps with a `stepKey` each, an ffmpeg graph, the caption segments, the encoding, the assets the script names with their hashes, `planSha256`, and `secretsNeeded`: the keys an uncached paid step still needs. With `--offline`, the plan in which every paid step takes its free fallback. |
| `estimate` | none | A dry-run price from `pricing.json`. No provider is called. Each paid line names its provider, model, units, price and price source; a step already in the step cache is priced at 0. `secretsNeeded` lists the keys that are still needed. Stops with `E_BUDGET` when the uncached steps cost more than the spending cap (`budget.maxUsd`, or a lower `--max-usd`). With `--offline` the price is 0. |
| `render` | write | Renders the script and writes a provenance manifest next to the video. With `jobId` alone, retries that failed or abandoned job. Lists each paid step as cached or billed. Stops before any call with `E_BUDGET` when the uncached steps cost more than the spending cap, and with `E_NO_KEY` when a key is missing, and with `E_PENDING` when a provider has not finished in time (the retry asks for the same request again and sends nothing new). With `--offline`, calls no provider and marks each replaced step with `fallback`. |
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

A `voiceover` clip is a caption only. Use a `speech` clip, described below, only when you want a line spoken and have set the provider key in the environment. `estimate` lists the keys a script still needs, and `--offline` renders it without any.

## Templates

A template is a video with named inputs. It is data that its owner keeps and changes: the same template makes a video for any business once the inputs are filled in. `expand` fills a template and returns an ordinary video script, so everything after it (`validate`, `plan`, `estimate`, `render`, `verify`) works as for a script written by hand.

```sh
ambitious-video expand --template welcome --in inputs.yaml --out script.yaml
ambitious-video validate --in script.yaml
ambitious-video estimate --in script.yaml     # show the price before rendering
ambitious-video render --in script.yaml
```

`--template` takes a template file, or the id of one of the three starter templates that ship with the binary. `guide` lists them in `data.templates.starters`, each with its inputs and its full text to save and change:

| Starter | What it makes | Default size |
|---|---|---|
| `welcome` | The name, three pictures, one highlight and a closing card, about 20 seconds. | 1920 by 1080 |
| `short-explainer` | One idea in three points, each with a picture or, without one, a card. About 20 seconds. | 1920 by 1080 |
| `announcement` | One piece of news with three pictures, upright for a phone screen. About 16 seconds. | 1080 by 1920 |

The starters name no business. Their words, pictures, colors and logo all come from the inputs and the brand file.

An inputs file holds the values, and may replace the template's output:

```yaml
template: welcome                      # optional: expand refuses another template
inputs:
  business_name: "Example Plumbing Co."
  picture_1: assets/still-1.png        # a file in the folder of the inputs file
  # ... one value for each required input
output: { path: out/plumbing.mp4, width: 1280, height: 720 }   # optional
```

A template looks like this (`schemas/video-template.v1.json` is its JSON Schema, and [schemas/README.md](schemas/README.md) explains every field):

```yaml
templateVersion: 1
id: welcome
title: Welcome video
inputs:
  business_name: { type: text, required: true, maxLength: 60 }
  picture_1: { type: image, required: true }
  voice: { type: voice }
brand: { file: brand.yaml }             # the owner's brand file, next to the inputs
output: { path: out/welcome.mp4, width: 1920, height: 1080, fps: 30 }
budget: { maxUsd: 5 }
speech: { provider: xai-speech, model: tts, voice: "{{voice}}" }
fallbacks: { speech: captions }
scenes:
  - id: opening
    show: { type: title_card, duration: 3.0, text: "{{business_name}}" }
    say: [{ at: 0.5, duration: 2.0, text: "Welcome to {{business_name}}." }]
    cut: { transition: crossfade, duration: 0.4 }
  - id: picture-1
    show: { type: image, path: "{{picture_1}}", duration: 4.0 }
endCard: { duration: 3.0, text: "{{business_name}}" }
```

- **Inputs** have a type: `text`, `image`, `video`, `audio`, `color`, `voice` or `choice`. An input is `required`, or has a `default`, or may be left out.
- **Substitution is plain.** `{{name}}` is replaced by the value of that input, once. There is no expression language: no conditions, loops, filters or arithmetic, and a value that itself holds `{{...}}` is not expanded again.
- **Scenes** are shown in order. Each shows a title card, a picture or a part of a video file, may say lines (timed from the start of the scene), and may cut into the next scene with a crossfade. `expand` works out every time in the script.
- **Fallbacks.** When a slot has no value, the step takes the fallback the template names: a scene shows its `fallback` clip, lines become captions only (`fallbacks.speech: captions`), and music is left out (`fallbacks.music: none`). `data.fallbacks` lists each one taken. Without a named fallback the missing value is an error.
- **Provider and model are written in the template.** Nothing chooses one at render time. The starters speak the lines with `xai-speech` only when the `voice` input is given; without it the lines are captions and the video costs nothing.
- **`budget.maxUsd`** is the most one video may cost, in US dollars. The starters set 5. `expand` copies it into the script, and `estimate` and `render` refuse to go above it: see [Spending caps](#spending-caps).

`expand` is pure: it opens no database and calls no provider, and the same template, inputs and brand file give the same script byte for byte, whatever `--now` and `--seed` are. It prints the script in `data.script` with `data.scriptSha256`, and `--out` also writes it to a file, as JSON when the name ends in `.json` and as YAML otherwise. Write the script into the folder of the inputs file, because paths in a script are relative to the script. The script names its template in `template: { id, templateVersion }`, and `plan` repeats that.

An input the template does not have, a missing required input, a value of the wrong type, a slot that names no input and a template that does not match the schema are all refused with `E_INPUT_SCHEMA`. `data.problems` has one entry for each, with the document it is in (`template`, `inputs`, `brand` or `script`), the place (`at`) and a message:

```json
{ "code": "E_INPUT_SCHEMA", "in": "inputs", "at": "/inputs", "message": "Input tagline is required." }
```

## Templates and providers

The short version of what the sections below explain in full.

- **Templates belong to their owner.** A template is data: typed inputs, a brand, scenes with `{{input}}` slots, a spending cap and the provider and model of every paid step. The three starters and the four sample projects in `examples/samples/` (made-up businesses, SAMPLE DATA) are there to copy and change. See [Templates](#templates).
- **Recommended providers.** Nothing is chosen for you: a script or template names the provider and model of each paid step. The adapters that ship are a reasonable place to start:

  | Step | Start with | Alternatives |
  |---|---|---|
  | `speech` | `xai-speech`, model `tts` (`XAI_API_KEY`) | `elevenlabs-speech` (`ELEVENLABS_API_KEY`), which can speak with a voice from the owner's own account, such as a cloned one |
  | `generated_scene` | `xai-video`, model `grok-imagine-video` (`XAI_API_KEY`) | `grok-imagine-video-1.5` and `grok-imagine-video-1.5-lite`, or `xai-image` for one still picture with a slow zoom |
  | `lip_sync` | `sync-lipsync`, model `lipsync-2` (`SYNC_API_KEY`) | `lipsync-2-pro`, `sync-3`; offline, a mouth drawn on this computer |

  Lip-sync runs only where a script has a `lip_sync` clip; no starter template has one. There is no adapter for other scene models such as Google Veo; a clip made elsewhere is an ordinary `video` clip, and [Adding a provider](#adding-a-provider) says how to write an adapter. See [Paid steps and providers](#paid-steps-and-providers).
- **Bring your own key.** Each provider reads its key from one environment variable, only for a call that is not in the step cache. The owner pays the provider directly. A key value is never written to the database, the cache, the plan, the manifest, the ledger, a log or any output. `estimate` and `plan` list the variables still needed in `secretsNeeded`. See [Keys asked on demand](#keys-asked-on-demand).
- **Estimates before spending.** `estimate` calls no provider. It prices each paid step from `pricing.json` with six decimal places and names the provider, model, units and the source of the price. `render` is the only command that spends, so show the estimate and get a go-ahead before every render.
- **Spending caps.** `budget.maxUsd` in the script (5 in the starters) and `--max-usd` on the command line cap what one run may cost, and the lower of the two applies. Above the cap, `estimate` and `render` stop with `E_BUDGET` before any provider is called. See [Spending caps](#spending-caps).
- **The step cache.** A paid result is stored under its step key and is never paid for twice: the next render that needs the same step takes it from the cache, calls nothing and counts it as 0. The cache is `$AMBITIOUS_HOME/video/cache` unless `cache.dir` or `--cache` names another folder, and nothing is removed from it except by `cache prune --older-than <days>`. See [Plans, step keys and the step cache](#plans-step-keys-and-the-step-cache).
- **Offline.** `--offline` on `plan`, `estimate` and `render` calls no provider and needs no key. Every paid step takes its free fallback and the cost is 0.000000. See [Rendering offline](#rendering-offline).

## What a render does

## Paid steps and providers

A paid step is a clip that names a provider and one of its models. There are three: `speech`, a line spoken by a text-to-speech provider, `generated_scene`, a scene a provider makes from a prompt ([Generated scenes](#generated-scenes)), and `lip_sync`, a presenter whose lips a provider moves to the sound of a track ([Lip-sync](#lip-sync)).

```yaml
- { type: speech, at: 0.5, text: "Narration line", provider: xai-speech, model: tts, voice: eve }
```

The provider, the model and the voice are always written in the script. Nothing picks one for you at render time and no voice is built in. A `voiceover` clip is the free form of the same line: it gives captions their words and timing, nothing is spoken and no key is needed.

Keys are bring-your-own. Each provider reads its key from one environment variable (`XAI_API_KEY` for the three xAI adapters, `ELEVENLABS_API_KEY` for `elevenlabs-speech`, `SYNC_API_KEY` for `sync-lipsync`), only when a step has to be called that is not in the step cache yet. The key is used for that call and is never written to the database, the cache, the plan, the ledger, a log or any output. Without it, `render` stops with `E_NO_KEY` before anything is called or charged. [Keys asked on demand](#keys-asked-on-demand) says how to find out which keys a script needs, and [Rendering offline](#rendering-offline) how to render without any.

| Provider id | Step | Models | Key | Unit |
|---|---|---|---|---|
| `xai-speech` | `speech` | `tts` | `XAI_API_KEY` | character |
| `elevenlabs-speech` | `speech` | `eleven-v4`, `eleven-flash` | `ELEVENLABS_API_KEY` | character |
| `xai-video` | `generated_scene` | `grok-imagine-video`, `grok-imagine-video-1.5`, `grok-imagine-video-1.5-lite` | `XAI_API_KEY` | second |
| `xai-image` | `generated_scene` (a still picture) | `grok-imagine-image`, `grok-imagine-image-2.0` | `XAI_API_KEY` | image |
| `sync-lipsync` | `lip_sync` | `lipsync-2`, `lipsync-2-pro`, `sync-3` | `SYNC_API_KEY` | frame |

`pricing.json` holds one entry per provider and model: `unit`, `usdPerUnit`, `sourceUrl` (the provider's public price page) and `checkedAt` (the day that page was read). These are list prices, not measured spend; check the source before relying on them. Lip-sync is priced per frame of the clip at the output frame rate; a plan fee that a provider charges your account each month is not in the table.

`estimate` prices a script from that table without calling anyone. A paid line looks like this:

```json
{ "step": "speech", "index": 8, "units": "40", "usd": "0.000600", "provider": "xai-speech", "model": "tts",
  "unit": "character", "usdPerUnit": "0.000015", "sourceUrl": "https://docs.x.ai/docs/models",
  "checkedAt": "2026-11-16", "stepKey": "...", "cached": false }
```

A step whose result is already in the step cache has `"cached": true` and `"usd": "0.000000"`, and `usd` at the top is the sum of what a render would still be charged. `--cache <dir>` tells `estimate` which cache to look in, as it does for `render`. `estimate` reads only that folder and not the database, so a result that `render` would copy from another cache folder is still shown at its price: a render can cost less than the estimate, never more.

### Speech with ElevenLabs

`elevenlabs-speech` speaks a line with a voice from your own ElevenLabs account:

```yaml
- { type: speech, at: 0.5, text: "Narration line", provider: elevenlabs-speech, model: eleven-v4, voice: "<voice id>" }
```

- **`voice` is a voice id**, the identifier ElevenLabs shows for a voice, not its display name. It can be one of the stock voices or a voice you cloned from your own recordings; the adapter sends whatever id the script names and has no voice of its own. In a template, leave it as the `voice` input so each owner supplies their own.
- **Models.** `eleven-v4` is the expressive model (sent as `eleven_v4`) and `eleven-flash` the faster, cheaper one (sent as `eleven_flash_v2_5`). Both are priced per character of the line's text in `pricing.json`.
- **The key is yours.** The adapter reads `ELEVENLABS_API_KEY` from the environment for the call and sends it only to `api.elevenlabs.io`, in the `xi-api-key` header. You pay ElevenLabs directly, on your own plan; this project is not part of that payment. The ledger records the list price of each call, which can differ from what your plan charges.
- **Only that key is asked for.** A script can speak with ElevenLabs and make its scenes with another provider. `secretsNeeded` then lists `ELEVENLABS_API_KEY` for the speech steps alone, and nothing once those lines are in the step cache.

The adapter follows the provider's public API documentation, and the tests run it against a local stand-in server. No test calls the provider, so check a first paid render with one short line. The request shape is kept in `providers/elevenlabs_speech.py`.

### Generated scenes

A `generated_scene` clip asks a provider for a scene and shows it for `duration` seconds:

```yaml
disclosure: "AI-generated scenes"
tracks:
  - id: main
    clips:
      - type: generated_scene
        provider: xai-video
        model: grok-imagine-video        # or grok-imagine-video-1.5, grok-imagine-video-1.5-lite
        prompt: "A slow aerial shot of a small town at sunrise, no people, no text"
        duration: 8
        resolution: 720p                 # optional
        aspect_ratio: "16:9"             # optional
        image: assets/reference.png      # optional: a picture the scene starts from
        fallback: { type: image, path: assets/town.png, kenburns: { from: 1.0, to: 1.08 } }
```

- **Provider and model** are written in the clip, as for speech. `xai-video` returns a moving scene, priced per second of `duration` (whole seconds from 1 to 15). `xai-image` returns one still picture, priced per picture, which is shown for `duration` with the clip's optional `kenburns: { from, to }` zoom. `validate` refuses a length, a size, a shape or a reference picture the named provider does not offer, and a `seed` for a provider that takes none.
- **`fallback`** is required: `{ type: image, path, kenburns }`, `{ type: video, path, in }` or `{ type: title_card, text, subtitle }`. It has no length of its own; it is shown for the `duration` of the scene, so the rest of the video keeps its timing. `validate` checks its file, and that a video file is long enough.
- **Disclosure.** Every frame that shows a generated scene, including the frames of a crossfade into or out of one, carries a small tag in the top right corner with the script's `disclosure` text. The text comes from the script (a template copies its own into it; the starter templates say "AI-generated scenes"), no wording is built in, and a script with a `generated_scene` clip and no `disclosure` is refused. Frames of other clips, and of a fallback, carry no tag.
- **Only the picture is used.** A moving scene is cut to the frames of its time on the track; its last frame is held if it is shorter, and any sound it has is left out.
- **The result is cached** under its step key like any paid step: a second job with the same prompt, model, length and reference picture calls nothing and costs nothing.
- **`verify` does not compare the frames of a generated scene.** It skips every sample frame in which such a scene is visible, so an online and an offline render of one script get the same answer. A video made only of generated scenes is checked for duration, frame count, size, sound and encoding, and for no frame.

A moving scene takes the provider a while. `render` sends the request once, writes the provider's request id to the database under the step key before it asks for the result, and then polls for a bounded time (every 5 seconds for up to 10 minutes with `xai-video`). If the scene is not ready by then, `render` stops with `E_PENDING` and the job is `failed`. The ledger has no line for the scene yet, but the provider may still charge for it when it finishes, and the ledger records that only when a later render collects the result, so retry the job. A video with several scenes sends every one of them before it waits for the first, so they are made at the same time and one retry can collect them all; if a later step then fails, the scenes already sent are still made, and the retry collects them. Retrying that job, or rendering the same step in a new job, asks for the stored request id again and never sends the prompt a second time. When the result arrives it is stored and the ledger gets its one line, whose idempotency key is the step key. The stored request id is dropped only when the result is stored, when the provider itself reports that the request failed or expired, when it answers the stored id with HTTP 404 or 410 (it no longer knows the request), or when the bytes it returns are not a video. Each of these ends with `E_PROVIDER`, and only then does the next render send the step again. In the last case the provider has usually charged for the scene, so real spend can be higher than the ledger shows. Any other failed poll (a refused key, a rate limit, an outage) keeps the request, and the retry asks for it again. If such retries keep failing, `render --resend` forgets the stored request of every unfinished scene of that video and sends it again; the provider may charge for the earlier request as well, so use it only then. One gap remains: a process that is killed after the provider accepted the request and before the id was written cannot know the id, and its retry sends the step again.

The two adapters follow the provider's public API documentation, and the tests run them against a local stand-in server. No test calls the provider, so check a first paid render with a short scene. The request and response shapes are kept in `providers/xai_video.py` and `providers/xai_image.py`, apart from everything else.

### Lip-sync

A `lip_sync` clip shows a presenter whose lips a provider moves to the sound of another track. It is never added for you: a video has lip-sync only when its script, or the template it came from, has such a clip.

```yaml
disclosure: "AI-generated lip movement"
tracks:
  - id: main
    clips:
      - type: lip_sync
        provider: sync-lipsync
        model: lipsync-2
        video: assets/presenter.mp4
        voice: voice
        duration: 6
        fallback: { type: mouth, sprite: assets/presenter.png, x: 0.5, y: 0.62, width: 0.12, height: 0.08 }
  - id: voice
    clips:
      - { type: speech, at: 0.2, text: "Hello, and welcome.", provider: xai-speech, model: tts, voice: eve }
```

- **The presenter is your own file.** `video` is a video of the presenter at least as long as the clip. `image` is a picture instead, which only `sync-3` takes. Give exactly one of the two.
- **`voice` names the track the presenter speaks.** The clip takes every `speech` and `audio` clip on that track that sounds while it is shown, mixes them into one sound file as long as the clip, and sends that file and the presenter to the provider. A sound that starts before the clip and runs into it is heard from the point the clip begins (the planned length of a `speech` line is used to tell). A `lip_sync` clip with no sound in it is refused with `E_INPUT_SCHEMA` at its `voice`, in `validate`, `plan`, `estimate` and `render`, before anything is sent or paid for. The plan lists those steps in the clip's `hears`. A `speech` step is made (or read from the step cache) first, so its key is still needed.
- **The models** of `sync-lipsync` are `lipsync-2`, `lipsync-2-pro` and `sync-3`, and the script always names one.
- **The price is per frame.** `estimate` counts the frames of the clip at the output frame rate: 30 seconds at 25 fps is 750 frames, which is $1.50 with `lipsync-2`, and the same 30 seconds at 30 fps is 900 frames. The provider counts the frames of the video it returns, which follow the frame rate of the presenter file, so a presenter recorded at another frame rate is billed a little differently from the estimate. A monthly plan fee that the provider charges your account is separate and is not in the estimate or the ledger.
- **The key is yours.** The adapter reads `SYNC_API_KEY` from the environment for the call and sends it only to `api.sync.so`, in the `x-api-key` header. The result is downloaded without the key. The files are uploaded in the request itself, so nothing has to be hosted anywhere.
- **Step key.** The key of a `lip_sync` step is made from the model, the hash of the presenter file, and the sound it hears: the step key of each `speech` clip, the hash of each `audio` file, and where each one starts in the clip. New words, another voice or another presenter file make a new key and a new charge. A change elsewhere in the video does not.
- **Later answers.** Lip-sync takes the provider a while, and it works like a moving [generated scene](#generated-scenes): the request id is stored under the step key before the result is asked for, a render that runs out of time stops with `E_PENDING`, and a retry asks for the stored id and never uploads the files a second time. `render --resend` forgets the stored request and sends it again. A request the provider reports as failed or rejected is forgotten and gets no ledger line.
- **Disclosure.** A script with a `lip_sync` clip needs `disclosure` text, and every frame of the clip carries it, as for a generated scene. `verify` skips those frames in the same way.
- **Offline.** With `--offline` the provider is not called and the clip's `fallback` is shown for the same time. `{ type: none }`, which is also what a clip without `fallback` gets, shows the presenter file as it is. `{ type: mouth, sprite, x, y, width, height }` draws a mouth on a picture of the presenter and opens it with the loudness of the sound: `x` and `y` are the middle of the mouth and `width` and `height` its size when fully open, as parts of the frame from 0 to 1, and `color` is optional. The mouth is drawn on this computer from the `audio` files on the voice track, and the same files give the same frames every time. A `speech` clip is not spoken offline, so the mouth stays closed during it. No disclosure text is drawn on a fallback.

The adapter follows the provider's public API documentation, and the tests run it against a local stand-in server. No test calls the provider, so check a first paid render with a short clip. The request and response shapes are kept in `providers/syncso_lipsync.py`.

### Keys asked on demand

`estimate` and `plan` list the keys a render would still need, in `data.secretsNeeded`:

```json
{ "secretsNeeded": [ { "env": "XAI_API_KEY", "provider": "xai-speech", "steps": [8, 9, 10, 11], "usd": "0.003585" } ] }
```

There is one entry for each environment variable that is not set and that at least one uncached paid step needs. `env` is the variable to set, `provider` is the provider that reads it (ids separated by a comma and a space when several providers read the same variable), `steps` holds the `index` of each step that waits for it, and `usd` is what those steps would cost. A step whose result is in the step cache needs no key, so the list is empty when every key is set or every paid step is cached. It holds names only, never a key value. Like the prices, it reads only the chosen cache folder.

`render` makes the same check across all paid steps before the first call. When a key is missing it stops with `E_NO_KEY`, one error for each variable, and `data.missing` holds the same entries. No provider has been called, no job has been created and nothing has been charged, also when the key of another provider in the same script is set.

A program that renders for someone else, such as an assistant, should work in this order:

1. Run `estimate`, show the price, and wait for the owner to agree to it. `render` is the only command that spends.
2. Ask the owner once for each `env` in `secretsNeeded`, through the host's secure secret or environment mechanism. Do not ask for a key that is not listed.
3. Set the variable only in the environment of the `render` command. Never write a key to a script, a file, a message, a page, a log or a command line.
4. If the owner declines, or nothing may leave the computer, render with `--offline`.

`guide` prints the same rules in `data.keys`.

### Spending caps

A script can say the most one run may cost, and so can the command line:

```yaml
budget: { maxUsd: 5 }
```

```sh
ambitious-video estimate --in script.yaml --max-usd 2.50
ambitious-video render --in script.yaml --max-usd 2.50
```

The lower of `budget.maxUsd` and `--max-usd` applies, so the flag can tighten the cap of a script for one run and cannot raise it. Without either there is no cap. `--max-usd` takes a plain amount in US dollars from 0 to 1000000 with at most six decimal places.

`estimate` and `render` add up the paid steps that are not in the step cache yet. A cached step counts as 0, and so does every step with `--offline`. When the sum is more than the cap, the command stops with `E_BUDGET`:

```json
{ "estimate": "6.400000", "cap": "5.000000", "source": "budget.maxUsd" }
```

`estimate` is the uncached sum, `cap` the cap that applied and `source` where it came from: `budget.maxUsd` or `--max-usd`. `estimate` also returns its usual `breakdown`, so the lines that cost the most can be seen. A sum equal to the cap passes. A passing `estimate` reports the cap it was checked against in `data.budget` as `{ cap, source }`.

`render` makes this check before the key check and before the first call, for a new job and for the retry of a job id. When it stops, no provider has been called, nothing has been charged, no job has been created and no file has been written. Because cached steps count as 0, a render that stopped half way can be run again under the same cap: only the steps that are still missing are counted.

The cap is checked against list prices from `pricing.json`, not against a provider's invoice, and a monthly plan fee that a provider charges the owner's account is not part of it. Raising a cap is the owner's decision.

### Rendering offline

`--offline` on `plan`, `estimate` and `render` calls no provider and needs no key. Every paid step takes its free fallback instead:

| Paid step | Fallback | What changes |
|---|---|---|
| `speech` | `captions` | Nothing is spoken. The line keeps its words and its time, so a captions track still shows it, exactly as a `voiceover` clip would. |
| `generated_scene` | `image`, `video` or `title_card` | The clip's own `fallback` is shown for the same time: the owner's picture (with a slow zoom if it names one), a part of the owner's video file, or a title card in the brand colors. No disclosure text is drawn, because nothing was generated. |
| `lip_sync` | `none` or `mouth` | `none` shows the presenter file as it is. `mouth` draws a mouth on the owner's picture of the presenter and opens it with the loudness of the `audio` files on the voice track. No disclosure text is drawn. |

A replaced step is listed as the free step that stands in for it, with `"fallback": "captions"` (or `image`, `video`, `title_card` for a scene, `none` or `mouth` for lip-sync) and `"fallbackFor": { "type": "speech", "provider": "...", "model": "..." }`, in the steps of `plan` and `render` and in the lines of `estimate`. The offline plan has no paid step, so its estimate and its `costUsd` are `0.000000`, `secretsNeeded` is empty, the ledger gets no line and the step cache is not read or written. It is a different plan from the online one, with its own `planSha256`, and it is as repeatable as any free render: the same script and files give the same plan in every process and the same video file on the same machine and toolchain. `--offline` applies to the whole command, also when a key is set or a result is cached. With `{ "jobId": "..." }` it applies to that retry.

`--offline` replaces paid steps only. A `screen_recording` clip still opens the page it names.

### Adding a provider

A provider is one module in `src/ambitious_video/providers/` with a class that follows `Provider` in `providers/base.py`, registered in `providers/__init__.py`:

| Member | What it is |
|---|---|
| `kind` | `speech`, `scene` or `lipsync`: the step it can do. |
| `id`, `models` | The names a script uses in `provider` and `model`. |
| `secret` | The name of the environment variable that holds the key. |
| `seedable`, `adapter_version` | Whether the provider takes a seed, and a number to raise when the adapter's output changes, so old cache entries are not reused. |
| `params(step)` | Pure. The canonical parameters that are sent; they are hashed into the step key. |
| `price(model)`, `quote(params, units)` | The price entry and the price of that many units, from `pricing.json` only. |
| `run(params, inputs, secret, out)` | The call. Writes the result to `out`. Uses only the standard library, and must not keep, log or raise the key. |

A `scene` provider also follows `SceneMaker`: `output` says whether it returns a `video` or an `image` (a still picture, shown with the clip's `kenburns` zoom), and `faults(step)` names the fields of a clip it cannot make, such as a length or a size it does not offer, so `validate` refuses them before anything is sent. A provider that answers later follows `Deferred` instead of doing the work in `run`: `submit(params, inputs, secret)` starts the work and returns the provider's request id, `collect(request_id, secret, out)` returns the result or `None` while it is not ready, and `patience` gives the seconds between two polls and the longest wait. It raises `RequestLost` when the provider says the request failed, which is the only case in which a step is sent again. A `lipsync` provider follows `Deferred` and `LipSyncer`: `lip_faults(step)` names the fields of a `lip_sync` clip it cannot make, and `submit` finds the presenter file in `inputs` under `video` or `image` and the mixed sound under `audio`.

The commands find providers only through that registry, so an adapter needs no change to `plan`, `estimate`, the step cache or `render`. `test/test_providers.py` registers one inside a test and runs it through all four, and `test/test_generated_scenes.py` does the same with one that answers later. A script that names a provider id, a model or a kind of step the registry does not have is refused by `validate`.

## Plans, step keys and the step cache

`plan` prints canonical JSON: keys are sorted, and there is no time, no absolute path and no environment value in it. The same script and files give the same bytes in every process, whatever `--now` and `--seed` are. `planSha256` is the SHA-256 of that plan, and `scriptSchemaVersion` is the version of the script format it was made from. A script that `expand` made also has `template: { id, templateVersion }` in its plan. `render` returns the same `planSha256`. `secretsNeeded` is printed next to the plan and is not part of `planSha256`, because it depends on which keys are set and what the step cache holds; everything else in `data` is hashed.

Every step has a `stepKey`: the SHA-256 of its kind, provider, model, adapter version, parameters, seed and input hashes, written as canonical JSON. A file the step reads goes in as the hash of its bytes. The key of a paid step names only what is sent to the provider (for speech: the model, the words, the voice and the adapter's language and format settings), not the job, the clock or where the line sits in the video. `output.stepKey` is the key of the finished video; `output.encode`, the bytes of the `brand.font` file and the key of every step are among its inputs, so changing one step changes that step's key and the output's key and leaves the other steps' keys alone. The key of a generated scene names the model, the prompt, the length, the size and shape when given, and the hash of the reference picture. Its `fallback`, its `kenburns` zoom and the disclosure text are not sent to the provider, so they change only the output's key and never cause a second charge. A `generated_scene` clip can carry a `seed` for a provider that takes one, and it is then part of the key. None of the adapters that ship takes a seed, and `--seed` never changes a step key. The key of a `lip_sync` step chains to the steps it hears: it holds the hash of the presenter file and, for the sound, the step key of each `speech` clip and the hash of each `audio` file, so changing a spoken line changes the key of the line and of the lip-sync that follows it, and nothing else.

The result of a paid step is kept in `<cache dir>/<stepKey>/` (`output.bin` and a `meta.json` with its hash) and listed in the `step_cache` table. Before a paid call, `render` looks there. A hit calls nothing, needs no key, and adds no ledger line; a miss calls the provider once, stores the result and adds one ledger line whose idempotency key is the step key. When the chosen cache folder has no result but another cache folder this database has used does, the verified result is copied in instead: no call, no key and no line. Renders that use one database take turns on a step key, also when they run at the same time with different cache folders, so the second one copies the result of the first. A second job with the same words and voice therefore costs nothing, and two renders with every paid step cached write the same file on the same machine and toolchain. `render` lists each paid step with `cached` and the `usd` this call was charged, and `costUsd` is their sum.

Every render writes a provenance manifest next to the video, as `<output file>.manifest.json` (`render` returns its path in `data.manifest`). It says where each step of that file came from:

```json
{ "manifestVersion": 1, "jobId": "...", "template": { "id": "welcome", "templateVersion": 1 }, "planSha256": "...",
  "offline": false, "disclosure": "AI-generated scenes", "costUsd": "0.400000",
  "output": { "file": "welcome.mp4", "sha256": "...", "stepKey": "..." },
  "steps": [ { "index": 1, "type": "generated_scene", "provider": "xai-video", "model": "grok-imagine-video",
               "stepKey": "...", "paid": true, "cached": false, "usd": "0.400000" } ] }
```

`template` is `null` for a script that was not made from a template. `steps` lists every step of the plan, free ones with the provider `local`; `cached` and `usd` say whether this render took a paid result from the step cache and what it was charged (a step that an earlier attempt of the same job paid for is `cached` with `usd` 0 in the final manifest; the ledger is the record of what was spent); a step that took its fallback also has `fallback` and `fallbackFor`. The manifest holds no key, no absolute path and no time.

The cache folder is `$AMBITIOUS_HOME/video/cache`. A script can name another with `cache: { dir: <folder below the script> }`, and `--cache <dir>` on `plan`, `estimate`, `render`, `cache list` and `cache prune` overrides both. Entries are never removed automatically. `cache prune --older-than 90` removes the entries last used more than 90 days ago; an entry that a running render holds is left alone, and `--older-than` takes whole days from 0 to 36500. The ledger holds one line per provider call, and a step key is called again only if its result was removed: by `cache prune`, or by deleting its folder by hand. That next call is paid for and gets its own line, with the idempotency key `<stepKey>#2` (then `#3`, and so on), so the ledger total stays equal to what `render` reported as `costUsd`. An entry whose file no longer matches its recorded hash is refused with `E_CACHE`.

A picture is scaled to cover the output size. A video clip of another size is scaled to fit inside the output size and centered on the brand background color, so every clip fills its scheduled time whatever the size of its source. A logo keeps its transparency: the background shows through it.

Captions are drawn only by a track with `from: <id of a track with voiceover or speech clips>`. Each such clip is shown during its own time and not before or after. A clip with more than one sentence shows one sentence at a time, each for a share of the clip that matches its length in characters. A script with no such track draws no captions. `plan` lists the segments in `data.captions`.

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

Shared codes (`E_USAGE`, `E_INPUT_SCHEMA`, `E_FORBIDDEN_PATH`, `E_NO_KEY`, `E_DB`, `E_MIGRATION`, `E_INTERNAL`) are defined by the module contract. For `E_NO_KEY`, `data.missing` lists each missing variable as `{ env, provider, steps, usd }`, and the hint names the variable. These codes are this module's:

| Code | Exit | Meaning | Typical hint |
|---|---|---|---|
| `E_UNKNOWN_STEP` | 1 | A clip `type` is not one of the eight known steps. | Use one of the types in the script schema. |
| `E_JOB_STATE` | 1 | The job is not in a state that allows the requested move. | Read `jobs` and retry only a failed or abandoned job. |
| `E_OVERLAP` | 1 | Two clips on one track overlap. | Move a clip or shorten it. |
| `E_MEDIA` | 1 | A file is missing, `in` / `out` falls outside it, or `brand.font` is not a font file next to the script. | Put the file next to the script, or fix the times. |
| `E_RANGE` | 1 | A cut is not on a clip boundary, or the video is longer than 1800 seconds. | Place the cut where one clip ends and the next begins. |
| `E_TOOL` | 1 | ffmpeg, ffprobe, Pillow or Playwright is missing, or ffmpeg failed while rendering. | Install the missing tool and run the command again. |
| `E_PROVIDER` | 1 | A provider rejected the call. `data.provider` names it and `data.status` is the HTTP status (0 when there was no answer). For a scene the provider gave up on or no longer knows, or returned bytes that are not a video or a picture, `data.stepKey` names the step, nothing is stored, the ledger gets no line, and the next render sends the step again. When a poll for a stored request fails for another reason, `data.requestId` names the request and it is kept. | Check the model, the voice and the key. For a kept request, retry the job id; if that keeps failing, render with `--resend`. |
| `E_PENDING` | 1 | A provider that answers later had not finished within the wait. The job moves to `failed`, the request id is kept, and the ledger has no line yet (the provider may still charge when the scene finishes). `data` holds `jobId`, `index`, `stepKey`, `provider` and `requestId`. | Retry with `{ "jobId": "..." }` later. The same request is asked for again and nothing is sent twice. |
| `E_VERIFY` | 1 | A rendered file failed a check. There is one error for each failed check, and its message says what differs: the duration or the frame count against the script's, how the video is stored against `output.encode`, a frame that is missing or that differs, or a file that is not a video. The job moves to `failed`. | Read `data.checks`: each check has `ok`, and `expected` and `actual` where they apply. |
| `E_BUDGET` | 1 | The paid steps that are not in the step cache cost more than the spending cap. Nothing was called or charged. `data` holds `estimate`, `cap` and `source` (`budget.maxUsd` or `--max-usd`); `estimate` adds `breakdown`. | Make the paid steps cheaper, render with `--offline`, or have the owner raise the cap. |
| `E_CACHE` | 1 | A step cache entry does not match its recorded hash. | Delete the folder named in `data.stepKey` and render again. |

## Layout

| Where | What |
|---|---|
| `schemas/video-script.v2.json` | The video script format. [schemas/README.md](schemas/README.md) explains it. |
| `schemas/video-template.v1.json` | The template format that `expand` reads. The same README explains it. |
| `templates/` | The three starter templates: `welcome.yaml`, `short-explainer.yaml` and `announcement.yaml`. A copy of each is inside the package, in `src/ambitious_video/templates/`, so the binary has them; a test keeps the copies the same. |
| `examples/` | Five scripts as data: `minimal.yaml` and `titles-and-captions.yaml` need no key, `all-clip-types.yaml` uses every free clip type and paid `speech`, `generated-scenes.yaml` has two `generated_scene` clips with their fallbacks, and `lip-sync.yaml` has a `lip_sync` clip that speaks a `speech` line. `examples/invalid/` holds scripts that must be rejected. |
| `examples/samples/` | Four sample projects for made-up businesses (a plumber, an accounting office, a fitness studio, a bookshop), all SAMPLE DATA. Each has an `inputs.yaml` for one starter template, a `brand.yaml`, the `script.yaml` that `expand` writes from them, and placeholder pictures drawn by `scripts/make_sample_assets.py`, and needs no key. |
| `src/ambitious_video/steps/` | One module per clip type. Each `compile` is pure. Register a new type in `steps/__init__.py`. |
| `src/ambitious_video/providers/` | The `Provider` protocol, the registry, the price table reader, and one module per provider adapter. |
| `spec/` | A TLA+ model of a render job: a retry never charges twice, the ledger holds one line per provider call, a step key is called again only if its result was removed, a verified video was rendered, one render per job. [spec/README.md](spec/README.md) explains it. |
| `src/ambitious_video/domain/plan.py` | Canonical JSON, `step_key` and `planSha256`. |
| `src/ambitious_video/domain/template.py` | Reading a template and filling it: pure, with no file or network access. |
| `src/ambitious_video/db/step_cache.py` | The step cache: the folders and the `step_cache` table. |
| `src/ambitious_video/domain/types.py` | The types the commands share: the script, jobs and their states, the cost ledger and assets. |
| `verify/formal/SKILL.md` | For coding agents: when and how to rerun the checks, and how to read a failure. |
| `pricing.json` | The price table, inside the package: one entry per provider and model, with its source and the day it was checked. |
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
