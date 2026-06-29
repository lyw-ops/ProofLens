from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LeanDeclaration:
    kind: str
    name: str
    short_name: str
    file: str
    line: int
    end_line: int
    statement: str
    docstring: str | None = None
    attributes: list[str] = field(default_factory=list)
    namespace: str | None = None
    source: str = ""
    dependencies: list[str] = field(default_factory=list)

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
class ProjectAnalysis:
    root: str
    files: list[LeanFileAnalysis]
    declarations: list[LeanDeclaration]

    @property
    def declaration_map(self) -> dict[str, LeanDeclaration]:
        result: dict[str, LeanDeclaration] = {}
        for declaration in self.declarations:
            result[declaration.name] = declaration
            result.setdefault(declaration.short_name, declaration)
        return result

    def dependency_graph(self) -> dict[str, list[str]]:
        return {
            declaration.name: declaration.dependencies
            for declaration in self.declarations
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
        }


@dataclass
class Finding:
    severity: str
    message: str
    location: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

