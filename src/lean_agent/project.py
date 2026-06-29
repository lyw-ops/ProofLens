from __future__ import annotations

import json
from pathlib import Path

from lean_agent.lean_parser import parse_lean_file, tokenize_lean_source
from lean_agent.models import LeanDeclaration, LeanFileAnalysis, ProjectAnalysis


IGNORED_DIRS = {
    ".git",
    ".lake",
    ".elan",
    "build",
    "dist",
    "__pycache__",
}


def find_lean_files(root: str | Path) -> list[Path]:
    root_path = Path(root)
    if root_path.is_file():
        return [root_path] if root_path.suffix == ".lean" else []
    files: list[Path] = []
    for path in root_path.rglob("*.lean"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def scan_project(root: str | Path) -> ProjectAnalysis:
    root_path = Path(root).resolve()
    scan_root = root_path.parent if root_path.is_file() else root_path
    lean_files = find_lean_files(root_path)
    file_analyses: list[LeanFileAnalysis] = [
        parse_lean_file(path, scan_root)
        for path in lean_files
    ]
    declarations = [
        declaration
        for file_analysis in file_analyses
        for declaration in file_analysis.declarations
    ]
    _attach_dependencies(declarations)
    return ProjectAnalysis(
        root=str(scan_root),
        files=file_analyses,
        declarations=declarations,
    )


def _attach_dependencies(declarations: list[LeanDeclaration]) -> None:
    token_to_names: dict[str, set[str]] = {}
    for declaration in declarations:
        token_to_names.setdefault(declaration.name, set()).add(declaration.name)
        token_to_names.setdefault(declaration.short_name, set()).add(declaration.name)
        token_to_names.setdefault(declaration.name.split(".")[-1], set()).add(declaration.name)

    for declaration in declarations:
        tokens = tokenize_lean_source(declaration.source)
        dependencies: set[str] = set()
        for token in tokens:
            for candidate in token_to_names.get(token, set()):
                if candidate != declaration.name:
                    dependencies.add(candidate)
        declaration.dependencies = sorted(dependencies)


def project_to_markdown(analysis: ProjectAnalysis) -> str:
    lines: list[str] = []
    lines.append(f"# Lean Project Analysis")
    lines.append("")
    lines.append(f"- Root: `{analysis.root}`")
    lines.append(f"- Lean files: {len(analysis.files)}")
    lines.append(f"- Declarations: {len(analysis.declarations)}")
    lines.append("")

    if analysis.files:
        lines.append("## Files")
        lines.append("")
        for file_analysis in analysis.files:
            lines.append(f"### `{file_analysis.path}`")
            if file_analysis.imports:
                lines.append(f"- Imports: {', '.join(f'`{item}`' for item in file_analysis.imports)}")
            else:
                lines.append("- Imports: none")
            if not file_analysis.declarations:
                lines.append("- Declarations: none")
            else:
                lines.append("- Declarations:")
                for declaration in file_analysis.declarations:
                    dep_count = len(declaration.dependencies)
                    lines.append(
                        f"  - `{declaration.name}` ({declaration.kind}, line {declaration.line}, deps {dep_count})"
                    )
            lines.append("")

    important = _important_declarations(analysis.declarations)
    if important:
        lines.append("## Proof Pipeline Candidates")
        lines.append("")
        for declaration in important:
            deps = ", ".join(f"`{name}`" for name in declaration.dependencies) or "none"
            lines.append(f"- `{declaration.name}` ({declaration.kind}): depends on {deps}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def project_to_json(analysis: ProjectAnalysis, include_source: bool = False) -> str:
    return json.dumps(
        analysis.to_dict(include_source=include_source),
        ensure_ascii=False,
        indent=2,
    )


def _important_declarations(declarations: list[LeanDeclaration]) -> list[LeanDeclaration]:
    theorem_like = [
        declaration
        for declaration in declarations
        if declaration.kind in {"theorem", "lemma", "def", "structure", "class"}
    ]
    return sorted(
        theorem_like,
        key=lambda item: (
            item.kind not in {"theorem", "lemma"},
            -len(item.dependencies),
            item.file,
            item.line,
        ),
    )[:20]
