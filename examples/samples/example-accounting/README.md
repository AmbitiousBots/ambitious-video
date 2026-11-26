# Example Accounting Office (SAMPLE DATA)

Example Accounting Office is fictional sample data: the business, its words and its pictures are made up, and stand in for an owner's own.

- `inputs.json`: the words and pictures of this video, as values for the starter template `templates/short-explainer.yaml`.
- `brand.json`: the colors and the logo. The template reads it from this folder.
- `script.json`: the video script that `expand` writes from the template, the inputs and the brand. Do not edit it; change the inputs and run `expand` again. It needs no key and no network.
- `assets/`: placeholder pictures drawn by `scripts/make_sample_assets.py`.

```sh
ambitious-video expand --template templates/short-explainer.yaml --in examples/samples/example-accounting/inputs.json --out examples/samples/example-accounting/script.json
ambitious-video validate --in examples/samples/example-accounting/script.json
ambitious-video plan --in examples/samples/example-accounting/script.json
```
