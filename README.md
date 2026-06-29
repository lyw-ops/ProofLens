# Lean Formalization Assistant Agent

`lean-agent` is a research-assistant prototype for Lean formalization projects. It helps researchers inspect Lean project structure, check consistency between papers and source code, generate theorem and lemma explanations, export AI4Math benchmark items, and run basic reproducibility audits.

This version is intentionally implemented with the Python standard library only. It does not require network access or external APIs, making it a runnable local baseline that can later be extended with LLMs, Lean LSP support, GitHub integration, or paper-writing workflows.

## Features

- Scan Lean files and extract declarations such as `def`, `lemma`, `theorem`, `structure`, `class`, and `instance`.
- Generate proof-pipeline summaries, including imports, declarations, and approximate dependency relationships.
- Compare Lean references, GitHub links, commit hashes, and appendix snippets in LaTeX/Markdown papers against local source code.
- Generate natural-language explanations for Lean declarations for use in README files, paper appendices, and artifact documentation.
- Export Lean statements as benchmark items with descriptions, statements, dependencies, difficulty estimates, and verification commands.
- Audit project reproducibility by checking `lean-toolchain`, `lakefile`, README content, and optional `lake build` status.

## Quick Start

```bash
PYTHONPATH=src python3 -m lean_agent scan examples/sample_project
PYTHONPATH=src python3 -m lean_agent explain examples/sample_project --symbol Sample.add_zero_twice
PYTHONPATH=src python3 -m lean_agent check-paper --lean-root examples/sample_project --paper examples/sample_paper.tex
PYTHONPATH=src python3 -m lean_agent benchmark examples/sample_project --out benchmark.jsonl
PYTHONPATH=src python3 -m lean_agent audit examples/sample_project
```

Install it as a command-line tool:

```bash
python3 -m pip install -e .
lean-agent scan path/to/lean/project
```

## Commands

### `scan`

Scan a Lean project and output a structural summary.

```bash
lean-agent scan path/to/project --format markdown
lean-agent scan path/to/project --format json --out analysis.json
```

### `explain`

Generate a natural-language explanation for a theorem, lemma, or definition.

```bash
lean-agent explain path/to/project --symbol MyProject.Main.final_theorem
lean-agent explain path/to/project --symbol final_theorem --language en
```

### `check-paper`

Check whether Lean references and GitHub links in a paper are consistent with the local Lean project.

```bash
lean-agent check-paper --lean-root path/to/project --paper paper/main.tex
```

It checks:

- Whether declarations referenced by `\lean{...}`, `\leanref{...}`, `\leanname{...}`, and `\uses{...}` exist.
- Whether GitHub `blob/.../file.lean#Lx` links point to local files that exist.
- Whether GitHub links are pinned to 40-character commit hashes.
- Whether linked commit hashes match the current local `HEAD`.

### `benchmark`

Export AI4Math benchmark items.

```bash
lean-agent benchmark path/to/project --out benchmark.jsonl
lean-agent benchmark path/to/project --format json --out benchmark.json
```

Each item contains:

- `name`
- `kind`
- `file`
- `line`
- `natural_language_description`
- `lean_statement`
- `dependencies`
- `difficulty`
- `verification`

### `audit`

Check Lean project reproducibility.

```bash
lean-agent audit path/to/project
lean-agent audit path/to/project --run-build --timeout 120
```

`--run-build` invokes `lake build`. If Lean or Lake is not available on the machine, the report records the error instead of crashing the CLI.

## Design Scope

The goal of this agent is not to replace Lean. Instead, it connects the artifacts that researchers often have to synchronize manually:

- Formal statements in Lean source code.
- Mathematical exposition, theorem names, and source-code links in papers.
- README files, artifact instructions, and reproducibility notes.
- Structured items needed for AI4Math benchmarks.

In that sense, it is a Lean-aware research assistant: it first performs reliable static checks and structural extraction, then turns the language-generation parts into auditable, traceable context.
