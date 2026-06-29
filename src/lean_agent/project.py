from __future__ import annotations

import json
from pathlib import Path

from lean_agent.declaration_index import DeclarationIndex
from lean_agent.lean_parser import parse_lean_file, tokenize_lean_source
from lean_agent.models import LeanDeclaration, LeanFileAnalysis, ProjectAnalysis
from lean_agent.proof_state_extractor import attach_proof_states, extract_proof_states
from lean_agent.semantic_extractor import attach_semantics, extract_semantics


IGNORED_DIRS = {
    ".git",
    ".lake",
    ".elan",
    "build",
    "dist",
    "__pycache__",
}
IGNORED_LEAN_FILES = {
    "lakefile.lean",
}


def find_lean_files(root: str | Path) -> list[Path]:
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Lean path does not exist: {root_path}")
    if root_path.is_file():
        if root_path.suffix != ".lean":
            raise ValueError(f"Expected a .lean file or directory, got: {root_path}")
        return [root_path]
    if not root_path.is_dir():
        raise NotADirectoryError(f"Lean path is not a directory: {root_path}")
    files: list[Path] = []
    for path in root_path.rglob("*.lean"):
        if path.name in IGNORED_LEAN_FILES:
            continue
        if _is_ignored_path(path, root_path):
            continue
        files.append(path)
    if not files:
        raise FileNotFoundError(f"No .lean files found under: {root_path}")
    return sorted(files)


def _is_ignored_path(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        relative_parts = path.parts
    return any(part in IGNORED_DIRS for part in relative_parts)


def scan_project(
    root: str | Path,
    semantic: bool = False,
    semantic_build: bool = False,
    semantic_timeout: int = 120,
    proof_states: bool = False,
    proof_state_timeout: int = 120,
) -> ProjectAnalysis:
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
    analysis = ProjectAnalysis(
        root=str(scan_root),
        files=file_analyses,
        declarations=declarations,
    )
    if semantic:
        analysis.semantic = extract_semantics(
            scan_root,
            file_analyses,
            declarations,
            run_build=semantic_build,
            timeout=semantic_timeout,
        )
        attach_semantics(declarations, analysis.semantic)
    if proof_states:
        analysis.proof_states = extract_proof_states(
            scan_root,
            file_analyses,
            declarations,
            timeout=proof_state_timeout,
        )
        attach_proof_states(declarations, analysis.proof_states)
    return analysis


def _attach_dependencies(declarations: list[LeanDeclaration]) -> None:
    index = DeclarationIndex(declarations)
    for declaration in declarations:
        tokens = tokenize_lean_source(declaration.source)
        dependencies: set[str] = set()
        for token in tokens:
            for candidate in index.names_for_token(token):
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
    if analysis.semantic:
        lines.append(f"- Semantic extraction: {analysis.semantic.status}")
        lines.append(f"- Semantic declarations: {len(analysis.semantic.declarations)}")
    if analysis.proof_states:
        lines.append(f"- Proof-state extraction: {analysis.proof_states.status}")
        lines.append(f"- Proof-state records: {len(analysis.proof_states.records)}")
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
                    dep_count = len(analysis.direct_dependencies(declaration))
                    semantic = f", semantic {declaration.semantic_kind}" if declaration.semantic_kind else ""
                    lines.append(
                        f"  - `{declaration.name}` ({declaration.kind}, line {declaration.line}, deps {dep_count}{semantic})"
                    )
            lines.append("")

    important = _important_declarations(analysis.declarations)
    if important:
        lines.append("## Proof Pipeline Candidates")
        lines.append("")
        for declaration in important:
            deps = ", ".join(f"`{name}`" for name in analysis.direct_dependencies(declaration)) or "none"
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
