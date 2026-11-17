# Example Plumbing Co. (SAMPLE DATA)

Example Plumbing Co. is fictional sample data: the business, its words and its pictures are made up, and stand in for an owner's own.

- `brand.yaml`: the colors and the logo. `script.yaml` repeats them in its `brand` block.
- `script.yaml`: a version 1 video script. It needs no key and no network.
- `assets/`: placeholder pictures drawn by `scripts/make_sample_assets.py`.

```sh
ambitious-video validate --in examples/samples/example-plumbing/script.yaml
ambitious-video plan --in examples/samples/example-plumbing/script.yaml
```
