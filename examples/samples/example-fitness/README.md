# Example Fitness Studio (SAMPLE DATA)

Example Fitness Studio is fictional sample data: the business, its words and its pictures are made up, and stand in for an owner's own.

- `inputs.json`: the words and pictures of this video, as values for the starter template `templates/announcement.json`.
- `brand.json`: the colors and the logo. The template reads it from this folder.
- `script.json`: the video script that `expand` writes from the template, the inputs and the brand. Do not edit it; change the inputs and run `expand` again. It needs no key and no network.
- `assets/`: placeholder pictures drawn by `scripts/make_sample_assets.py`.

```sh
ambitious-video expand --template templates/announcement.json --in examples/samples/example-fitness/inputs.json --out examples/samples/example-fitness/script.json
ambitious-video validate --in examples/samples/example-fitness/script.json
ambitious-video plan --in examples/samples/example-fitness/script.json
```
