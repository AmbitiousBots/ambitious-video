# Schemas

`video-script.v2.json` is the JSON Schema (draft 2020-12) for a video script: the JSON document that describes a video as data. `video-recipe.v1.json` is the JSON Schema (draft 2020-12) for a shared JSON DSL v1 recipe (`dslVersion: 1`, `kind: "recipe"`, see `examples/promo-video-recipe.json`); a recipe this tool can render compiles into a validated video script. The section "The shared recipe" below explains it. `examples/minimal.json`, `examples/titles-and-captions.json`, `examples/all-clip-types.json`, `examples/generated-scenes.json` and `examples/lip-sync.json` are video scripts, and each folder under `examples/samples/` holds one more. `titles-and-captions.json` is the one to copy first: it needs no media files and no key, and the `guide` command prints it.

```json
{
  "schemaVersion": 2,
  "template": { "id": "welcome", "templateVersion": 1 },
  "budget": { "maxUsd": 5 },
  "output": { "path": "out/demo.mp4", "width": 1920, "height": 1080, "fps": 30, "encode": "web" },
  "brand": { "font": "assets/font.ttf", "colors": { "bg": "#0B0B0B", "fg": "#FFFFFF", "accent": "#3B82F6" }, "logo": "assets/logo.png" },
  "cache": { "dir": ".video-cache" },
  "tracks": [
    {
      "id": "main",
      "clips": [
        { "type": "title_card", "duration": 3.0, "text": "Hello", "subtitle": "optional" },
        { "type": "screen_recording", "url": "https://example.com", "duration": 8.0, "actions": [{ "click": "#start" }, { "wait": 1.5 }, { "scroll": 600 }] },
        { "type": "video", "path": "assets/broll.mp4", "in": 2.0, "out": 6.0 },
        { "type": "image", "path": "assets/shot.png", "duration": 4.0, "kenburns": { "from": 1.0, "to": 1.1 } }
      ]
    },
    {
      "id": "voice",
      "clips": [
        { "type": "speech", "at": 0.5, "text": "Narration line", "provider": "xai-speech", "model": "tts", "voice": "eve" },
        { "type": "voiceover", "at": 4.0, "text": "A caption that is not spoken" }
      ]
    },
    { "id": "captions", "from": "voice" },
    { "id": "music", "clips": [{ "type": "audio", "path": "assets/bed.mp3", "at": 0, "gain_db": -18, "duck_under": "voice" }] }
  ],
  "cuts": [{ "at": 11.0, "transition": "crossfade", "duration": 0.4 }]
}
```

## What the schema says

- **`template`**, **`budget`**, **`disclosure`** (all optional) are written by the `expand` command when it makes a script from a template, described under [Templates](#templates). `template: { id, templateVersion }` names that template, and `plan` repeats it. `budget: { maxUsd }` is the most one run may cost in US dollars: when the paid steps that are not in the step cache yet cost more, `estimate` and `render` stop with `E_BUDGET` before any provider is called. `--max-usd` on those two commands can lower the cap for one run and cannot raise it. A script without `budget` has no cap. `disclosure` is the text drawn on every frame of a generated scene; a script with a `generated_scene` clip needs it, also when it was written by hand. None of the three changes the key of a paid step.
- **`output`**: where the video is written, its size in pixels (even numbers, as common video encoders need) and its frames per second. `encode` (optional) is `web` or `lossless`. `web`, the default, is H.264 with `yuv420p` pixels, which browsers and common players show. `lossless` is exact RGB, for comparing a video pixel by pixel; many browsers cannot show it.
- **`brand`** (optional): a font file, three colors as `#RRGGBB`, and a logo file. Nothing about a business is built in: without `brand.colors`, title cards are drawn in neutral colors (background `#111111`, text `#FFFFFF`, accent `#336699`), text is set in the drawing library's built-in font, and no logo is shown. `font` must be the path of a font file, such as `fonts/name.ttf`. A font name such as `Sans` is not enough, because fonts installed on one computer are not on another and are never searched. A logo with transparency keeps it: the background shows through.
- **`cache`** (optional): `cache: { dir: <folder> }` names the folder, below the script's folder, where results of paid steps are kept so that the same step is not paid for twice. Without it the cache is `$AMBITIOUS_HOME/video/cache`; `--cache <dir>` on the command line overrides both. It does not change the plan or any step key.
- **`tracks`**: each track has an `id` and either `clips` or `from`. A track with `from: <track id>` draws captions from the `voiceover` and `speech` clips of that track: each clip is shown during its own time, and a clip with more than one sentence shows one sentence at a time, each for a share of the clip that matches its length in characters. A sentence ends at `.`, `!` or `?` followed by a space. A script with no `from` track draws no captions.
- **Clips.** Eight types. An unknown `type` is rejected.

  | `type` | Needs | May have |
  |---|---|---|
  | `title_card` | `duration`, `text` | `subtitle` |
  | `screen_recording` | `url` (http or https), `duration` | `actions`: a list where each entry is one of `click: <CSS selector>`, `wait: <seconds>`, `scroll: <pixels>` |
  | `video` | `path` | `in`, `out`: the part of the file to use, in seconds. A file of another size is scaled to fit and centered on the background color. |
  | `image` | `path`, `duration` | `kenburns: { from, to }`: a slow zoom between two scales from 1 to 4 |
  | `voiceover` | `at`, `text` | `duration`: how long the line is shown. Nothing is spoken and no key is needed; the line gives captions their words and timing |
  | `speech` | `at`, `text`, `provider`, `model`, `voice` | `duration`: how long the line lasts when it should not depend on the spoken audio |
  | `audio` | `path`, `at` | `gain_db`; `duck_under: <track id>` turns this clip down while that track is heard |
  | `lip_sync` | `provider`, `model`, `duration`, `voice`, and one of `video` or `image` | `fallback`: `{ type: none }` or `{ type: mouth, sprite, x, y, width, height, color }` |
  | `generated_scene` | `provider`, `model`, `prompt`, `duration`, `fallback` | `resolution` (such as `720p` or `1k`), `aspect_ratio` (such as `16:9`), `seed`, `image`: a reference picture, `kenburns: { from, to }` for a still picture |

- **`speech`** is a paid step: the line is spoken by the `provider` (an adapter id such as `xai-speech` or `elevenlabs-speech`), with that `model` and that `voice`, all three written out in the script. What `voice` holds is up to the provider: a voice name for `xai-speech`, and a voice id from the owner's own account, which can be a cloned voice, for `elevenlabs-speech`. There is no script-wide voice setting and no built-in voice. The schema checks only the shape of the three names; the `validate` command refuses a provider or model that no adapter is registered for. Speaking needs the provider's key in the environment when the video is rendered; a key never goes in a script, and the schema rejects one. With `--offline` on `plan`, `estimate` or `render`, a `speech` clip takes its free fallback, `captions`: nothing is spoken and no key is needed, and the line keeps its words and its time, as a `voiceover` clip with the same `at`, `text` and `duration` would. The script itself does not change.
- **`generated_scene`** is a paid step: the `provider` makes a scene from the `prompt` with that `model`, and it is shown for `duration` seconds on the track, like a picture or a video clip. `fallback` is what is shown for the same time when the provider is not called (`--offline`): `{ type: image, path, kenburns }`, `{ type: video, path, in }` or `{ type: title_card, text, subtitle }`. It has no `duration` or `out` of its own. The schema checks the shapes only; the `validate` command refuses what the named provider does not offer, for example a `seed` for a provider that takes none or `kenburns` on a moving scene. A key never goes in a script, and the schema rejects one.
- **`lip_sync`** is a paid step: the `provider` moves the lips of a presenter to the sound of the track that `voice` names, and the result is shown for `duration` seconds. The presenter is the owner's own file: a `video` at least as long as the clip, or an `image` for a model that takes a picture. The sound is every `speech` and `audio` clip on that track that sounds while the clip is shown, including one that starts earlier and runs into it; a clip with no sound in it is refused before anything is sent. It is priced per frame of the clip at the output frame rate. `fallback` is what is shown for the same time when the provider is not called (`--offline`): `none` (also the meaning of no `fallback`) shows the presenter file as it is, and `mouth` draws a mouth on the picture `sprite` and opens it with the loudness of the `audio` files on that track; `x`, `y`, `width` and `height` are parts of the frame from 0 to 1, with `x` and `y` at the middle of the mouth. A key never goes in a script, and the schema rejects one.
- **`cuts`** (optional): at time `at`, blend from one clip into the next with a `transition` (`crossfade`) over `duration` seconds.
- **Paths** are relative to the folder that holds the script. A path that starts with `/` or has a `..` part is rejected, so a script can be moved with its folder and cannot read or write outside it.
- **Times** are seconds. A start time (`at`, `in`) is zero or more; a length (`duration`, `out`, `wait`) is more than zero.
- **No unknown properties** anywhere, and every list and text has a maximum size.

## What the schema cannot say

JSON Schema cannot compare one part of a document with another. These rules are checked next to the schema, by `scripts/check_schemas.py` for the examples here, and they are named in `ScriptRule` in `src/ambitious_video/domain/types.py` for the module's `validate` command:

| Rule | In plain words |
|---|---|
| `track-ids-unique` | No two tracks have the same `id`. |
| `captions-from-a-voiceover-track` | `from` names a track that has at least one `voiceover` or `speech` clip. |
| `duck-under-another-track` | `duck_under` names a track in the script other than the clip's own. |
| `video-out-after-in` | A `video` clip's `out` is later than its `in`. |
| `generated-scenes-need-disclosure` | A script with a `generated_scene` or `lip_sync` clip has `disclosure` text. |
| `lip-sync-voice-is-a-sound-track` | The `voice` of a `lip_sync` clip names another track that has at least one `speech` or `audio` clip. |

Rules that need the media files or the computed timeline (clips on a track do not overlap, every `at`, `in` and `out` falls inside its media, every cut falls inside the video, `brand.font` is a readable font file below the script's folder) belong to the `validate` command and are not checked here.

## Templates

`video-template.v1.json` is the JSON Schema (draft 2020-12) for a video template: a video with named inputs. The `expand` command fills a template with the values of an inputs file and writes a video script as described above. The format belongs to this command-line tool alone; nothing else has to read it. `templates/` holds three starter templates, and each sample project under `examples/samples/` is made from one of them.

```json
{
  "templateVersion": 1,
  "id": "shop-news",
  "title": "Shop news",
  "description": "Optional. Says what the template is for.",
  "inputs": {
    "name": { "type": "text", "required": true, "maxLength": 40, "description": "Shown on the first card." },
    "mood": { "type": "choice", "options": ["calm", "bright"], "default": "calm" },
    "paper": { "type": "color", "default": "#101010" },
    "clip": { "type": "video" },
    "voice": { "type": "voice" },
    "tune": { "type": "audio" }
  },
  "brand": { "colors": { "bg": "{{paper}}", "fg": "#FFFFFF", "accent": "#3366AA" } },
  "output": { "path": "out/news.mp4", "width": 1920, "height": 1080, "fps": 30 },
  "budget": { "maxUsd": 5 },
  "speech": { "provider": "xai-speech", "model": "tts", "voice": "{{voice}}" },
  "music": { "path": "{{tune}}", "gain_db": -18 },
  "fallbacks": { "speech": "captions", "music": "none" },
  "disclosure": "AI-generated scenes",
  "scenes": [
    {
      "id": "hello",
      "show": { "type": "title_card", "duration": 2.0, "text": "{{name}}", "subtitle": "A {{mood}} day" },
      "say": [{ "at": 0.5, "text": "Hello from {{name}}." }],
      "cut": { "transition": "crossfade", "duration": 0.2 }
    },
    {
      "id": "moving",
      "show": { "type": "video", "path": "{{clip}}", "in": 1.0, "out": 2.5 },
      "fallback": { "type": "title_card", "duration": 1.5, "text": "{{name}} in pictures" }
    }
  ],
  "endCard": { "duration": 2.0, "text": "See you at {{name}}", "say": [{ "at": 0.25, "text": "Goodbye." }] }
}
```

- **`templateVersion`** is 1. **`id`** is lower-case letters, digits and hyphens; **`title`** and the optional **`description`** are for people.
- **`inputs`**: the named values. A name is lower-case letters, digits and underscores. Each input has a `type`, and may have `required: true`, a `default`, and a `description`.

  | `type` | A value is | It may fill |
  |---|---|---|
  | `text` | Text, at most `maxLength` characters (2000 without it). | `text`, `subtitle`, the `prompt` of a generated scene and the lines in `say`, mixed with other words. |
  | `choice` | One of the `options` listed in the template. | The same places as `text`. |
  | `image` | The path of a picture below the folder of the inputs file. | The `path` of an `image` clip, the reference `image` of a generated scene, and `brand.logo`. |
  | `video` | The path of a video file below that folder. | The `path` of a `video` clip. |
  | `audio` | The path of an audio file below that folder. | `music.path`. |
  | `color` | `#RRGGBB`. | A color of a `brand` written in the template. |
  | `voice` | The name of a voice of the speech provider. | `speech.voice`. |

- **Slots.** `{{name}}` in a text is replaced by the value of the input `name`. Text places may mix slots and words. A path, a color or a voice is either written out or is exactly one slot. Substitution happens once and is plain: there are no conditions, loops, filters or arithmetic, `{{ name | upper }}` is rejected, and a value that itself holds `{{...}}` is not expanded again.
- **`brand`** is either `{ "file": "brand.json" }`, the owner's brand file read from the folder of the inputs file, or a brand written in the template as in a script, where a color or the logo may be a slot. A brand file holds what a script's `brand` block holds: `colors`, `logo` and `font`.
- **`output`** is the output of the script. An inputs file can replace any part of it.
- **`budget.maxUsd`** is the most one video from this template may cost, in US dollars. The starter templates set 5. `expand` copies it into the script, where `estimate` and `render` refuse to go above it.
- **`speech`** says who speaks the lines: a `provider` and a `model`, both written out, and a `voice` that is written out or a slot. A template without `speech` makes captions only.
- **`music`** is one audio file played under the whole video, turned down while a line is heard.
- **`scenes`** are shown in order. `show` is a `title_card`, an `image`, a `video`, a `generated_scene` or a `lip_sync` clip written as in a script; a `video` needs `out`, so that the length of every scene is known without opening a file. A `generated_scene` names its provider and model in the template, so a template chooses, for example, between `grok-imagine-video`, `grok-imagine-video-1.5` and `grok-imagine-video-1.5-lite`; its `prompt` may hold slots, and its own `fallback` (shown when the provider is not called) may take its picture or video from a slot. A `lip_sync` clip is there only when the template asks for it; its `video` or `image` and the `sprite` of its fallback may come from a slot, and its `voice` is `voice`, the track `expand` puts the lines of `say` on. `say` lists lines, each with `text` and optional `at` (seconds from the start of the scene, 0 without it) and `duration`. `cut` is a crossfade into the next scene, so the last scene cannot have one. Scene ids are unique.
- **`fallback`** on a scene is the clip shown instead when a slot in `show` has no value. **`fallbacks`** names what the other steps become when a slot they need has no value: `speech: captions` turns every line into a caption that is not spoken, and `music: none` leaves the music out. Without a fallback, a slot with no value is an error.
- **`endCard`** is a last title card after the scenes: `duration`, `text`, optional `subtitle` and `say`.
- **`disclosure`** is the text drawn on every frame of a generated scene. It is plain text from the template, and `expand` copies it into the script. A template whose scene shows a `generated_scene` or a `lip_sync` clip must have it. The starter templates set "AI-generated scenes"; it marks nothing in a video without such a scene.

An inputs file is `{ template, inputs, output }`: `inputs` maps input names to values, the optional `template` is the id the values were written for (`expand` refuses another template), and the optional `output` replaces parts of the template's output. Every value is text; an input with no value, or with `null`, is left out.

What the template schema cannot say is checked by `expand`: a slot names an input of the template, the input has a type that fits the place, a `default` is a valid value for its type, scene ids are unique, and the last scene has no `cut`. `test/test_templates.py` holds one template for each of these mistakes.

## Examples

- `examples/*.json` must be accepted and break no rule. Together they must use every clip type. `all-clip-types.json` uses every free type and `speech`, `generated-scenes.json` adds `generated_scene`, `lip-sync.json` adds `lip_sync`, and `promo-video-recipe.json` validates against `schemas/video-recipe.v1.json` and compiles into a validated script (`test/fixtures/promo-render.json`, a recipe another tool runs, must validate against the same schema and be refused only for what cannot be rendered here); their paid clips name a provider, so they show a price in `estimate`.
- `examples/samples/<name>/` holds four sample projects for four made-up businesses (a plumber, an accounting office, a fitness studio and a bookshop). Everything in them is SAMPLE DATA. Each has an `inputs.json` with the values for one starter template, a `brand.json`, the `script.json` that `expand` writes from the two (it must be accepted, break no rule, and be exactly what `expand` writes), a `README.md`, and placeholder pictures in `assets/`. They need no key and no network. `python3 scripts/make_sample_assets.py` draws the pictures again; it uses only the Python standard library.
- `templates/*.json` are the three starter templates. Each must be accepted by the template schema and by `expand`, have its file name as its `id`, and be the same as its copy in `src/ambitious_video/templates/`.
- `examples/invalid/*.json` must each be rejected for the reason listed for it in `examples/invalid/EXPECTED.json`: `{ "keyword", "at" }` names the schema keyword that rejects it and where, and `{ "rule" }` names the one rule above that it breaks while passing the schema.

## Check

```sh
python3 scripts/check_schemas.py
```

The script itself uses only the Python standard library. On first run it creates `.venv/` and installs the pinned dev packages (including `jsonschema`, with no YAML dependency) from `scripts/dev-requirements.txt`, where every file is pinned by SHA-256 (`scripts/dev-requirements.in` lists the versions; `uv pip compile --generate-hashes --universal` writes the pinned file). It needs Python 3.13 or newer and starts itself again with the Python pinned in `.mise.toml` when it is run by an older one. It also checks that the schema, the model in `spec/` and `src/ambitious_video/domain/types.py` name the same clip types, actions, transitions, output encodings, patterns, rules, job states and moves, that `schemas/video-recipe.v1.json` is valid and names the same fields, kinds, patterns and limits as `src/ambitious_video/domain/recipe_shape.py`, and that the template schema and `types.py` name the same input types, scene clips, patterns and template version. It ends with `check_schemas: ok`.

A change that makes a valid script invalid belongs in a new schema file with a new version number. Version 2 replaced the script-wide `voice` block of version 1 with the `speech` clip; a version 1 script is rejected.

## The shared recipe

`video-recipe.v1.json` describes the recipe format that this tool shares with other tools that read recipes. A recipe built from `ai`, `tool` and `return` steps that one of them accepts is accepted by the schema here, and the other way round. `src/ambitious_video/domain/recipe_shape.py` checks the same shape when the program runs, and `scripts/check_schemas.py` fails when the two name different fields, kinds, patterns or limits.

| Part | What it holds |
|---|---|
| Top level | `dslVersion` (1), `kind` (`recipe`), `id`, `version`, `triggers`, `steps`. Optional: `title`, `summary`, `activation` (`auto` or `person`), `budget.maxUsd`, `subject`, `source`. |
| `subject` | The kind of thing each run is about, such as `lead`: lowercase letters, digits and `_`, at most 32 characters. |
| `source` | The document version the recipe was written from: `doc:sha256:` and 64 lowercase hex characters. |
| A `manual` trigger | Optional `input`: each field has a `type`, and `currency` (for `money`), `values` (for `enum`) or `refKind` (for `ref`) only where the type calls for it. |
| An `event` trigger | `type` is one of `document.ingested`, `fact.written`, `fact.verified`, `subject.created`, `entity.upserted`, `entity.confirmed`. Optional `where` expression. |
| A `schedule` trigger | Exactly one of `every` (`day`, `week`, `month`, `quarter`, `year`, a count of hours or days such as `12h` or `10d`, or an expression) or `at` (a date, a date and time with a zone, or an expression). `start` is allowed only with `every`. |
| A `signal` trigger | `name`, two lowercase words joined by a dot. |
| Every step | `id`, `kind`, optional `title`, `needs` and `from`. `from` lists the facts the step was written from, such as `payment.net_days`. |
| An `ai` step | `model`. Optional: `executor` (`server` or `bot`), `provider`, `prompt`, `params`, `output`, `fallback`, `cache` (`step_key` or `none`), `seed`, `disclosure`, `budget`, `adapterVersion`, `next`. `output` names each returned field with a type, `file`, or the list of values it may take. |
| A `tool` step | `action`: `documents.ingest`, `facts.upsert`, `views.query`, `signals.emit`, or `bot.` or `sor.` and a task name. Optional: `input`, `outputSchema`, `timeout`, `next`. |
| A `return` step | Optional `status` (`completed` or `blocked`) and `output`. |
| An expression | Text, a number, `true`, `false`, `null`, or an object with one operator: `$` (a path), `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `contains`, `exists`, `and`, `or`, `not`, `addDays`, `addBusinessDays`, `diffDays`, `coalesce`. |
| A path | At most 6 lowercase segments joined by dots, such as `steps.scene.video`, or `facts.` and a fact name. A fact path may end in `verified`, `verified_by` or `verified_at` to read whether a person checked the fact, who, and when; a fact name itself may not end in one of those three words. |

A step with no `next` runs into the step written after it, unless another step lists it in `needs`. Every step that is not a `return` must lead to one, and the steps must not form a loop.

Shapes from earlier drafts are refused: a `schedule` trigger with `rule`, `subjectKind`, `days`, `dueDays`, `leadDays`, `date`, `end` or `businessDays`; a `signal` trigger with `delayDays` or `subjectKind`; `outputSchema` on an `ai` step (it is now `output`); the actions `facts.write` and `facts.verify`; the event `fact.confirmed`.

### Rules only the program checks

A JSON Schema cannot say these, so `validate` checks them:

- No text or field name holds something that looks like an API key or a private key, a null character, or broken Unicode.
- A recipe that lists `from` on a step, or reads a fact anywhere in its steps or triggers (`{ "$": "facts.<name>" }` or `{{facts.<name>}}` in text), names its `subject`. The schema checks only the `from` half.
- `needs` and `next` name steps that exist, step ids are used once, there is a `return` step, and the steps form no loop.
- The recipe is at most 64 KiB, values nest at most 16 deep, and no field named `__proto__`, `constructor` or `prototype` appears at any depth.

### What this tool renders

This tool renders on the machine it runs on. It keeps no facts, asks no person and sets no timers. So:

- The step kinds `eval`, `human`, `wait` and `recipe`, and a top-level `entity`, are refused by the schema and by the program, each with a message that names it.
- A recipe with the shared shape renders only when each `ai` step has `"executor": "bot"`, a `provider` and `model` this tool knows, `params` for one scene, one spoken line or one lip-sync clip, and `cache` left at `step_key`; when exactly one `tool` step has the action `bot.ffmpeg_assemble`; and when that step lists each `ai` step in `needs` and places it once in `tracks` with `{ "refStep": "<step id>" }`. Otherwise `validate` names the step and the reason, for example `/steps/0/executor: Video AI steps require executor bot.`
- An `ai` step here takes its prompt in `params.prompt`, not `prompt`, and has no `budget` of its own: set `budget.maxUsd` on the recipe, above 0.
- `subject`, `source`, `from`, `title`, `output` and `outputSchema` are read and checked, and change nothing: the compiled script, every step key and `planSha256` are the same with or without them.
