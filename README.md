# Prooflens

Prooflens is a research-assistant prototype for Lean formalization projects. It helps researchers inspect Lean project structure, check consistency between papers and source code, generate theorem and lemma explanations, export AI4Math benchmark items, and run basic reproducibility audits.

This version is intentionally implemented with the Python standard library only. It does not require network access or external APIs, making it a runnable local baseline that can later be extended with LLMs, Lean LSP support, GitHub integration, or paper-writing workflows.

## Run From This Checkout

Prooflens uses a `src/` layout. From the repository root, either install it in editable mode:

```sh
python3 -m pip install -e .
prooflens scan examples/sample_project
```

Or run it without installation by setting `PYTHONPATH=src`:

```sh
PYTHONPATH=src python3 -m prooflens scan examples/sample_project
PYTHONPATH=src python3 -m prooflens check-paper --lean-root examples/sample_project --paper examples/sample_paper.tex
```

## Sample Project

`examples/sample_project` is a minimal Lake project with `Sample.lean` and `lakefile.lean`.
With Lean and Lake on `PATH`, it can be used for semantic and proof-state demos:

```sh
cd examples/sample_project
lake build
cd ../..

PYTHONPATH=src python3 -m prooflens scan examples/sample_project --semantic-build --proof-states --format json
```

## Semantic Extraction

Static scanning works on ordinary `.lean` files and does not require Lake. The optional semantic extractor does require a Lake project because it runs Lean through:

```sh
lake env lean <generated-extractor>
```

Use `--semantic` to collect elaborated declaration kinds, canonical names, pretty-printed Lean types, and dependencies between scanned declarations when the Lake project has already been built. Use `--semantic-build` when you want Prooflens to run `lake build` before semantic extraction:

```sh
PYTHONPATH=src python3 -m prooflens scan examples/sample_project --semantic
PYTHONPATH=src python3 -m prooflens scan examples/sample_project --semantic-build
```

`--semantic-build` implies `--semantic`. If `lake build` fails, semantic extraction stops and reports `build_failed`.

The current "semantic" layer is Lean elaboration metadata. It is not yet a natural-language semantic matcher between paper prose and Lean theorems, and it does not use embeddings, an LLM, or Lean LSP. Those can be added later as optional matchers while keeping this pure local baseline runnable.

## Proof States

Use `--proof-states` to attach tactic before/after states to declarations and tactic-level benchmark items:

```sh
PYTHONPATH=src python3 -m prooflens scan examples/sample_project --proof-states --format json
PYTHONPATH=src python3 -m prooflens benchmark examples/sample_project --level tactic --proof-states --format json --out benchmark.json
```

Proof-state extraction needs Lean to elaborate the scanned file. In a Lake project, Prooflens runs through `lake env lean`; outside a Lake project it falls back to `lean` directly. If Lean returns a non-zero exit after producing some proof states, Prooflens reports `partial` and keeps the records it could parse.

## Other Commands

```sh
PYTHONPATH=src python3 -m prooflens explain examples/sample_project --symbol Sample.add_zero_twice
PYTHONPATH=src python3 -m prooflens env examples/sample_project
PYTHONPATH=src python3 -m prooflens audit examples/sample_project --run-build
```
