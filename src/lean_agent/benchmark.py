from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lean_agent.models import LeanDeclaration, ProjectAnalysis


BENCHMARK_KINDS = {"theorem", "lemma", "def", "abbrev", "structure", "class", "inductive"}
BENCHMARK_SCHEMA_VERSION = "prooflens.ai4math.v1"
BENCHMARK_SCHEMA_STABILITY = "experimental"


def build_benchmark_items(
    analysis: ProjectAnalysis,
    level: str = "theorem",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if level in {"theorem", "all"}:
        for declaration in analysis.declarations:
            if declaration.kind not in BENCHMARK_KINDS:
                continue
            items.append(_item_for_declaration(analysis, declaration))
    if level in {"tactic", "all"}:
        for declaration in analysis.declarations:
            items.extend(_tactic_items_for_declaration(analysis, declaration))
    return items


def write_benchmark(
    analysis: ProjectAnalysis,
    output_path: str | Path,
    output_format: str = "jsonl",
    level: str = "theorem",
) -> None:
    items = build_benchmark_items(analysis, level=level)
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
    dependencies = analysis.direct_dependencies(declaration)
    dependency_files = sorted(
        {
            analysis.declaration_map[dependency].file
            for dependency in dependencies
            if dependency in analysis.declaration_map
        }
    )
    lean_statement = declaration.semantic_type or declaration.statement
    return _with_schema(
        {
        "id": _stable_id(declaration),
        "name": declaration.name,
        "kind": declaration.kind,
        "canonical_name": declaration.canonical_name or declaration.name,
        "semantic_kind": declaration.semantic_kind,
        "file": declaration.file,
        "line": declaration.line,
        "column": declaration.column,
        "end_line": declaration.end_line,
        "end_column": declaration.end_column,
        "natural_language_description": _description(declaration),
        "lean_statement": lean_statement,
        "formal_parameters": [parameter.to_dict() for parameter in declaration.formal_parameters],
        "assumptions": [
            parameter.to_dict()
            for parameter in declaration.formal_parameters
            if parameter.role == "assumption"
        ],
        "conclusion": declaration.formal_conclusion,
        "dependencies": dependencies,
        "static_dependencies": declaration.dependencies,
        "semantic_dependencies": declaration.semantic_dependencies,
        "proof_steps": [step.to_dict() for step in declaration.proof_steps],
        "dependency_files": dependency_files,
        "difficulty": _difficulty(declaration, dependencies, lean_statement),
        "verification": {
            "type": "lake",
            "command": f"lake env lean {declaration.file}",
            "note": "Run from the Lean project root. Use `lake build` for whole-project verification.",
        },
        },
        optional_fields=[
            "semantic_kind",
            "conclusion",
            "semantic_dependencies",
        ],
    )


def _tactic_items_for_declaration(
    analysis: ProjectAnalysis,
    declaration: LeanDeclaration,
) -> list[dict[str, Any]]:
    if not declaration.proof_steps:
        return []
    parent_dependencies = analysis.direct_dependencies(declaration)
    parent_statement = declaration.semantic_type or declaration.statement
    items: list[dict[str, Any]] = []
    for step in declaration.proof_steps:
        items.append(
            _with_schema(
                {
                "id": f"{_stable_id(declaration)}__step_{step.index}",
                "name": f"{declaration.name}::step_{step.index}",
                "kind": "tactic",
                "parent_name": declaration.name,
                "parent_canonical_name": declaration.canonical_name or declaration.name,
                "parent_kind": declaration.kind,
                "file": declaration.file,
                "line": step.line,
                "column": step.column,
                "natural_language_description": (
                    f"Fill tactic step {step.index} of `{declaration.name}` using `{step.tactic}`."
                ),
                "lean_statement": parent_statement,
                "conclusion": declaration.formal_conclusion,
                "tactic": step.tactic,
                "tactic_text": step.text,
                "before_state": step.before_state,
                "after_state": step.after_state,
                "dependencies": parent_dependencies,
                "verification": {
                    "type": "lake",
                    "command": f"lake env lean {declaration.file}",
                    "note": "Run from the Lean project root. Tactic-state fields are populated when proof-state extraction is available.",
                },
                "difficulty": _tactic_difficulty(step.text),
                },
                optional_fields=[
                    "conclusion",
                    "before_state",
                    "after_state",
                ],
            )
        )
    return items


def _with_schema(item: dict[str, Any], optional_fields: list[str]) -> dict[str, Any]:
    field_status = {
        field: _field_status(item.get(field))
        for field in optional_fields
    }
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "schema_stability": BENCHMARK_SCHEMA_STABILITY,
        "missing_value": None,
        "field_status": field_status,
        **item,
    }


def _field_status(value: Any) -> str:
    if value is None:
        return "missing"
    if isinstance(value, list) and not value:
        return "missing"
    return "available"


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


def _difficulty(
    declaration: LeanDeclaration,
    dependencies: list[str],
    lean_statement: str,
) -> str:
    statement_lines = max(1, lean_statement.count("\n") + 1)
    dep_count = len(dependencies)
    if declaration.kind in {"structure", "class", "inductive"}:
        return "medium" if statement_lines <= 6 else "hard"
    if dep_count <= 2 and statement_lines <= 2:
        return "easy"
    if dep_count <= 6 and statement_lines <= 6:
        return "medium"
    return "hard"


def _tactic_difficulty(tactic_text: str) -> str:
    if tactic_text.startswith(("rfl", "trivial", "simp", "exact")):
        return "easy"
    if tactic_text.startswith(("rw", "apply", "constructor", "intro", "intros")):
        return "medium"
    return "hard"
