from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lean_agent.models import LeanDeclaration, ProjectAnalysis


BENCHMARK_KINDS = {"theorem", "lemma", "def", "abbrev", "structure", "class", "inductive"}


def build_benchmark_items(analysis: ProjectAnalysis) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for declaration in analysis.declarations:
        if declaration.kind not in BENCHMARK_KINDS:
            continue
        items.append(_item_for_declaration(analysis, declaration))
    return items


def write_benchmark(
    analysis: ProjectAnalysis,
    output_path: str | Path,
    output_format: str = "jsonl",
) -> None:
    items = build_benchmark_items(analysis)
    path = Path(output_path)
    if output_format == "json":
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def _item_for_declaration(
    analysis: ProjectAnalysis,
    declaration: LeanDeclaration,
) -> dict[str, Any]:
    dependency_files = sorted(
        {
            analysis.declaration_map[dependency].file
            for dependency in declaration.dependencies
            if dependency in analysis.declaration_map
        }
    )
    return {
        "id": _stable_id(declaration),
        "name": declaration.name,
        "kind": declaration.kind,
        "file": declaration.file,
        "line": declaration.line,
        "natural_language_description": _description(declaration),
        "lean_statement": declaration.statement,
        "dependencies": declaration.dependencies,
        "dependency_files": dependency_files,
        "difficulty": _difficulty(declaration),
        "verification": {
            "type": "lake",
            "command": f"lake env lean {declaration.file}",
            "note": "Run from the Lean project root. Use `lake build` for whole-project verification.",
        },
    }


def _stable_id(declaration: LeanDeclaration) -> str:
    safe_name = declaration.name.replace(".", "__").replace("'", "_prime")
    return f"{safe_name}__L{declaration.line}"


def _description(declaration: LeanDeclaration) -> str:
    if declaration.docstring:
        return declaration.docstring
    if declaration.kind in {"theorem", "lemma"}:
        return f"Prove the Lean {declaration.kind} `{declaration.name}`."
    if declaration.kind in {"def", "abbrev"}:
        return f"Formalize the definition `{declaration.name}`."
    return f"Formalize the Lean {declaration.kind} `{declaration.name}`."


def _difficulty(declaration: LeanDeclaration) -> str:
    statement_lines = max(1, declaration.statement.count("\n") + 1)
    dep_count = len(declaration.dependencies)
    if declaration.kind in {"structure", "class", "inductive"}:
        return "medium" if statement_lines <= 6 else "hard"
    if dep_count <= 2 and statement_lines <= 2:
        return "easy"
    if dep_count <= 6 and statement_lines <= 6:
        return "medium"
    return "hard"

