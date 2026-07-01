from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lean_agent.formal_type import FormalParameter


@dataclass
class ProofStep:
    index: int
    tactic: str
    text: str
    line: int
    column: int
    end_line: int | None = None
    before_state: str | None = None
    after_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LeanDeclaration:
    kind: str
    name: str
    short_name: str
    file: str
    line: int
    column: int
    end_line: int
    end_column: int
    statement: str
    docstring: str | None = None
    attributes: list[str] = field(default_factory=list)
    namespace: str | None = None
    source: str = ""
    dependencies: list[str] = field(default_factory=list)
    canonical_name: str | None = None
    semantic_kind: str | None = None
    semantic_type: str | None = None
    semantic_dependencies: list[str] = field(default_factory=list)
    formal_parameters: list[FormalParameter] = field(default_factory=list)
    formal_conclusion: str | None = None
    proof_steps: list[ProofStep] = field(default_factory=list)

    def to_dict(self, include_source: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_source:
            data.pop("source", None)
        return data


@dataclass
class LeanFileAnalysis:
    path: str
    imports: list[str]
    declarations: list[LeanDeclaration]

    def to_dict(self, include_source: bool = False) -> dict[str, Any]:
        return {
            "path": self.path,
            "imports": self.imports,
            "declarations": [
                declaration.to_dict(include_source=include_source)
                for declaration in self.declarations
            ],
        }


@dataclass
class DeclarationExtractionRecord:
    file: str
    kind: str
    name: str
    short_name: str
    line: int
    column: int
    end_line: int
    end_column: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeclarationExtractionReport:
    status: str
    command: list[str]
    files: list[str]
    records: list[DeclarationExtractionRecord] = field(default_factory=list)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "command": self.command,
            "files": self.files,
            "records": [record.to_dict() for record in self.records],
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "message": self.message,
        }


@dataclass
class ProjectAnalysis:
    root: str
    files: list[LeanFileAnalysis]
    declarations: list[LeanDeclaration]
    declaration_extraction: DeclarationExtractionReport | None = None
    semantic: SemanticExtractionReport | None = None
    proof_states: ProofStateExtractionReport | None = None

    def declaration_index(self):
        from lean_agent.declaration_index import DeclarationIndex

        return DeclarationIndex(self.declarations)

    @property
    def declaration_map(self) -> dict[str, LeanDeclaration]:
        return self.declaration_index().compatible_declaration_map()

    def dependency_graph(self, prefer_semantic: bool = True) -> dict[str, list[str]]:
        return {
            declaration.name: self.direct_dependencies(declaration, prefer_semantic=prefer_semantic)
            for declaration in self.declarations
        }

    def direct_dependencies(
        self,
        declaration: LeanDeclaration,
        prefer_semantic: bool = True,
    ) -> list[str]:
        if prefer_semantic and declaration.semantic_dependencies:
            return declaration.semantic_dependencies
        return declaration.dependencies

    def transitive_dependency_graph(self, prefer_semantic: bool = True) -> dict[str, list[str]]:
        graph = self.dependency_graph(prefer_semantic=prefer_semantic)
        return {
            name: sorted(_transitive_dependencies(name, graph))
            for name in graph
        }

    def relative_file(self, path: str | Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(Path(self.root).resolve()))
        except ValueError:
            return str(path)

    def to_dict(self, include_source: bool = False) -> dict[str, Any]:
        return {
            "root": self.root,
            "files": [
                file_analysis.to_dict(include_source=include_source)
                for file_analysis in self.files
            ],
            "declarations": [
                declaration.to_dict(include_source=include_source)
                for declaration in self.declarations
            ],
            "dependency_graph": self.dependency_graph(),
            "static_dependency_graph": self.dependency_graph(prefer_semantic=False),
            "transitive_dependency_graph": self.transitive_dependency_graph(),
            "declaration_index": self.declaration_index().to_dict(),
            "declaration_extraction": (
                self.declaration_extraction.to_dict() if self.declaration_extraction else None
            ),
            "semantic": self.semantic.to_dict() if self.semantic else None,
            "proof_states": self.proof_states.to_dict() if self.proof_states else None,
        }


@dataclass
class Finding:
    severity: str
    message: str
    location: str | None = None
    suggestion: str | None = None
    patch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticDeclaration:
    name: str
    kind: str
    type: str
    dependencies: list[str] = field(default_factory=list)
    file: str | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    docstring: str | None = None
    attributes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticExtractionReport:
    status: str
    command: list[str]
    modules: list[str]
    declarations: list[SemanticDeclaration] = field(default_factory=list)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "command": self.command,
            "modules": self.modules,
            "declarations": [declaration.to_dict() for declaration in self.declarations],
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "message": self.message,
        }


@dataclass
class ProofStateRecord:
    file: str
    line: int
    column: int
    end_line: int
    end_column: int
    tactic_syntax: str
    before_state: str
    after_state: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProofStateExtractionReport:
    status: str
    command: list[str]
    files: list[str]
    records: list[ProofStateRecord] = field(default_factory=list)
    extraction_mode: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "command": self.command,
            "files": self.files,
            "records": [record.to_dict() for record in self.records],
            "extraction_mode": self.extraction_mode,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "message": self.message,
        }


def _transitive_dependencies(name: str, graph: dict[str, list[str]]) -> set[str]:
    visited: set[str] = set()
    stack = list(graph.get(name, []))
    while stack:
        dependency = stack.pop()
        if dependency in visited:
            continue
        visited.add(dependency)
        stack.extend(graph.get(dependency, []))
    visited.discard(name)
    return visited
