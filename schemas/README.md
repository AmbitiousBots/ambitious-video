# Schemas

`video-script.v1.json` is the JSON Schema (draft 2020-12) for a video script: the YAML or JSON document that describes a video as data. `examples/minimal.yaml`, `examples/titles-and-captions.yaml` and `examples/all-clip-types.yaml` are such documents, and each folder under `examples/samples/` holds one more. `titles-and-captions.yaml` is the one to copy first: it needs no media files and no key, and the `guide` command prints it.

```yaml
schemaVersion: 1
output: { path: out/demo.mp4, width: 1920, height: 1080, fps: 30, encode: web }
brand: { font: assets/font.ttf, colors: { bg: "#0B0B0B", fg: "#FFFFFF", accent: "#3B82F6" }, logo: assets/logo.png }
voice: { provider: xai, voice: default }
cache: { dir: .video-cache }
tracks:
  - id: main
    clips:
      - { type: title_card, duration: 3.0, text: "Hello", subtitle: "optional" }
      - { type: screen_recording, url: "https://example.com", duration: 8.0, actions: [{ click: "#start" }, { wait: 1.5 }, { scroll: 600 }] }
      - { type: video, path: assets/broll.mp4, in: 2.0, out: 6.0 }
      - { type: image, path: assets/shot.png, duration: 4.0, kenburns: { from: 1.0, to: 1.1 } }
  - id: voice
    clips: [{ type: voiceover, at: 0.5, text: "Narration line" }]
  - id: captions
    from: voice
  - id: music
    clips: [{ type: audio, path: assets/bed.mp3, at: 0, gain_db: -18, duck_under: voice }]
cuts: [{ at: 11.0, transition: crossfade, duration: 0.4 }]
```

## What the schema says

- **`output`**: where the video is written, its size in pixels (even numbers, as common video encoders need) and its frames per second. `encode` (optional) is `web` or `lossless`. `web`, the default, is H.264 with `yuv420p` pixels, which browsers and common players show. `lossless` is exact RGB, for comparing a video pixel by pixel; many browsers cannot show it.
- **`brand`** (optional): a font file, three colors as `#RRGGBB`, and a logo file. Nothing about a business is built in: without `brand.colors`, title cards are drawn in neutral colors (background `#111111`, text `#FFFFFF`, accent `#336699`), text is set in the drawing library's built-in font, and no logo is shown. `font` must be the path of a font file, such as `fonts/name.ttf`. A font name such as `Sans` is not enough, because fonts installed on one computer are not on another and are never searched. A logo with transparency keeps it: the background shows through.
- **`voice`** (optional): the provider and voice that speak `voiceover` clips. Speaking needs the provider's key in the environment when the video is rendered; a key never goes in a script, and the schema rejects one. Without `voice`, nothing is spoken and no key is needed; `voiceover` clips still give captions their words and timing. Leave `voice` unset unless spoken voiceover is wanted and the key is set.
- **`cache`** (optional): `cache: { dir: <folder> }` names the folder, below the script's folder, where results of paid steps are kept so that the same step is not paid for twice. Without it the cache is `$AMBITIOUS_HOME/video/cache`; `--cache <dir>` on the command line overrides both. It does not change the plan or any step key.
- **`tracks`**: each track has an `id` and either `clips` or `from`. A track with `from: <track id>` draws captions from the `voiceover` clips of that track: each clip is shown during its own time, and a clip with more than one sentence shows one sentence at a time, each for a share of the clip that matches its length in characters. A sentence ends at `.`, `!` or `?` followed by a space. A script with no `from` track draws no captions.
- **Clips.** Six types. An unknown `type` is rejected.

  | `type` | Needs | May have |
  |---|---|---|
  | `title_card` | `duration`, `text` | `subtitle` |
  | `screen_recording` | `url` (http or https), `duration` | `actions`: a list where each entry is one of `click: <CSS selector>`, `wait: <seconds>`, `scroll: <pixels>` |
  | `video` | `path` | `in`, `out`: the part of the file to use, in seconds. A file of another size is scaled to fit and centered on the background color. |
  | `image` | `path`, `duration` | `kenburns: { from, to }`: a slow zoom between two scales from 1 to 4 |
  | `voiceover` | `at`, `text` | `duration`: how long the line lasts when it should not depend on the spoken audio |
  | `audio` | `path`, `at` | `gain_db`; `duck_under: <track id>` turns this clip down while that track is heard |

- **`cuts`** (optional): at time `at`, blend from one clip into the next with a `transition` (`crossfade`) over `duration` seconds.
- **Paths** are relative to the folder that holds the script. A path that starts with `/` or has a `..` part is rejected, so a script can be moved with its folder and cannot read or write outside it.
- **Times** are seconds. A start time (`at`, `in`) is zero or more; a length (`duration`, `out`, `wait`) is more than zero.
- **No unknown properties** anywhere, and every list and text has a maximum size.

## What the schema cannot say

JSON Schema cannot compare one part of a document with another. These rules are checked next to the schema, by `scripts/check_schemas.py` for the examples here, and they are named in `ScriptRule` in `src/ambitious_video/domain/types.py` for the module's `validate` command:

| Rule | In plain words |
|---|---|
| `track-ids-unique` | No two tracks have the same `id`. |
| `captions-from-a-voiceover-track` | `from` names a track that has at least one `voiceover` clip. |
| `duck-under-another-track` | `duck_under` names a track in the script other than the clip's own. |
| `video-out-after-in` | A `video` clip's `out` is later than its `in`. |

Rules that need the media files or the computed timeline (clips on a track do not overlap, every `at`, `in` and `out` falls inside its media, every cut falls inside the video, `brand.font` is a readable font file below the script's folder) belong to the `validate` command and are not checked here.

## Examples

- `examples/*.yaml` must be accepted and break no rule. Together they must use every clip type. `all-clip-types.yaml` does so by itself; it names a voice, so it shows a price in `estimate`.
- `examples/samples/<name>/` holds four sample projects for four made-up businesses (a plumber, an accounting office, a fitness studio and a bookshop). Everything in them is SAMPLE DATA. Each has a `script.yaml` that must be accepted and break no rule, a `brand.yaml` with the same colors and logo as the script's `brand` block, a `README.md`, and placeholder pictures in `assets/`. They need no key and no network. `python3 scripts/make_sample_assets.py` draws the pictures again; it uses only the Python standard library.
- `examples/invalid/*.yaml` must each be rejected for the reason listed for it in `examples/invalid/EXPECTED.json`: `{ "keyword", "at" }` names the schema keyword that rejects it and where, and `{ "rule" }` names the one rule above that it breaks while passing the schema.

## Check

```sh
python3 scripts/check_schemas.py
```

The script itself uses only the Python standard library. On first run it creates `.venv/` and installs the two packages it checks with, `jsonschema` and `PyYAML`, from `scripts/dev-requirements.txt`, where every file is pinned by SHA-256 (`scripts/dev-requirements.in` lists the two versions; `uv pip compile --generate-hashes --universal` writes the pinned file). It needs Python 3.13 or newer and starts itself again with the Python pinned in `.mise.toml` when it is run by an older one. It also checks that the schema, the model in `spec/` and `src/ambitious_video/domain/types.py` name the same clip types, actions, transitions, providers, output encodings, patterns, rules, job states and moves. It ends with `check_schemas: ok`.

A change that makes a valid script invalid belongs in a new schema file with a new version number.
