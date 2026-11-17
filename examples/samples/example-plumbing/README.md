# Example Plumbing Co. (SAMPLE DATA)

Example Plumbing Co. is fictional sample data: the business, its words and its pictures are made up, and stand in for an owner's own.

- `inputs.yaml`: the words and pictures of this video, as values for the starter template `templates/welcome.yaml`.
- `brand.yaml`: the colors and the logo. The template reads it from this folder.
- `script.yaml`: the video script that `expand` writes from the template, the inputs and the brand. Do not edit it; change the inputs and run `expand` again. It needs no key and no network.
- `assets/`: placeholder pictures drawn by `scripts/make_sample_assets.py`.

```sh
ambitious-video expand --template templates/welcome.yaml --in examples/samples/example-plumbing/inputs.yaml --out examples/samples/example-plumbing/script.yaml
ambitious-video validate --in examples/samples/example-plumbing/script.yaml
ambitious-video plan --in examples/samples/example-plumbing/script.yaml
```
