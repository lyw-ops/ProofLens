from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class LakeDependency:
    name: str
    source: str
    kind: str | None = None
    url: str | None = None
    rev: str | None = None
    input_rev: str | None = None
    scope: str | None = None

    @property
    def is_mathlib(self) -> bool:
        return self.name.lower() == "mathlib" or "mathlib" in (self.url or "").lower()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_lake_dependencies(root: str | Path) -> list[LakeDependency]:
    root_path = Path(root)
    dependencies: list[LakeDependency] = []
    dependencies.extend(_dependencies_from_manifest(root_path / "lake-manifest.json"))
    dependencies.extend(_dependencies_from_lakefile_lean(root_path / "lakefile.lean"))
    dependencies.extend(_dependencies_from_lakefile_toml(root_path / "lakefile.toml"))
    return _dedupe_dependencies(dependencies)


def mathlib_dependencies(root: str | Path) -> list[LakeDependency]:
    return [
        dependency
        for dependency in detect_lake_dependencies(root)
        if dependency.is_mathlib
    ]


def _dependencies_from_manifest(path: Path) -> list[LakeDependency]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    dependencies: list[LakeDependency] = []
    for package in _manifest_packages(data):
        if not isinstance(package, dict):
            continue
        name = _string_or_none(package.get("name"))
        if not name:
            continue
        dependencies.append(
            LakeDependency(
                name=name,
                source="lake-manifest.json",
                kind=_string_or_none(package.get("type")),
                url=_string_or_none(package.get("url")),
                rev=_string_or_none(package.get("rev")),
                input_rev=_string_or_none(package.get("inputRev") or package.get("input_rev")),
                scope=_string_or_none(package.get("scope")),
            )
        )
    return dependencies


def _manifest_packages(data: Any) -> list[Any]:
    if isinstance(data, dict):
        packages = data.get("packages")
        if isinstance(packages, list):
            return packages
        package_entries = data.get("packageEntries")
        if isinstance(package_entries, list):
            return package_entries
    return []


def _dependencies_from_lakefile_lean(path: Path) -> list[LakeDependency]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    dependencies: list[LakeDependency] = []
    for line in text.splitlines():
        dependency = _parse_lakefile_lean_require(line)
        if dependency:
            dependencies.append(dependency)
    return dependencies


def _parse_lakefile_lean_require(line: str) -> LakeDependency | None:
    stripped = _strip_line_comment(line).strip()
    index = 0
    keyword, index = _read_word(stripped, index)
    if keyword != "require":
        return None
    index = _skip_spaces(stripped, index)
    name, index = _read_dependency_name(stripped, index)
    if not name:
        return None

    kind: str | None = None
    url: str | None = None
    input_rev: str | None = None
    while index < len(stripped):
        index = _skip_spaces(stripped, index)
        if index >= len(stripped):
            break
        if stripped[index] == "@":
            index = _skip_spaces(stripped, index + 1)
            input_rev, index = _read_quoted_string(stripped, index)
            continue
        word, next_index = _read_word(stripped, index)
        if word == "from":
            kind, next_index = _read_word(stripped, _skip_spaces(stripped, next_index))
            kind = _normalize_kind(kind)
            next_index = _skip_spaces(stripped, next_index)
            if next_index < len(stripped) and stripped[next_index] == '"':
                url, next_index = _read_quoted_string(stripped, next_index)
            index = next_index
            continue
        index = next_index + 1 if next_index == index else next_index

    return LakeDependency(
        name=name,
        source="lakefile.lean",
        kind=kind,
        url=url,
        input_rev=input_rev,
    )


def _dependencies_from_lakefile_toml(path: Path) -> list[LakeDependency]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    dependencies: list[LakeDependency] = []
    for block in _toml_require_blocks(text):
        name = _toml_string(block, "name")
        if not name:
            continue
        dependencies.append(
            LakeDependency(
                name=name,
                source="lakefile.toml",
                kind="git" if _toml_string(block, "git") else None,
                url=_toml_string(block, "git") or _toml_string(block, "path"),
                rev=_toml_string(block, "rev"),
                input_rev=_toml_string(block, "rev"),
            )
        )
    return dependencies


def _toml_require_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_require = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[["):
            if in_require and current:
                blocks.append("\n".join(current))
            in_require = stripped == "[[require]]"
            current = [line] if in_require else []
            continue
        if in_require:
            current.append(line)
    if in_require and current:
        blocks.append("\n".join(current))
    return blocks


def _toml_string(block: str, key: str) -> str | None:
    for line in block.splitlines():
        stripped = _strip_line_comment(line).strip()
        if not stripped.startswith(key):
            continue
        index = _skip_spaces(stripped, len(key))
        if index >= len(stripped) or stripped[index] != "=":
            continue
        index = _skip_spaces(stripped, index + 1)
        if index >= len(stripped) or stripped[index] != '"':
            continue
        value, _next_index = _read_quoted_string(stripped, index)
        return value
    return None


def _strip_line_comment(line: str) -> str:
    in_string = False
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if in_string:
            if char == '"' and not escaped:
                in_string = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue
        if char == '"':
            in_string = True
            escaped = False
            index += 1
            continue
        if line.startswith("--", index) or line.startswith("#", index):
            return line[:index]
        index += 1
    return line


def _read_word(text: str, start: int) -> tuple[str, int]:
    index = start
    while index < len(text) and (text[index].isalpha() or text[index] == "_"):
        index += 1
    return text[start:index], index


def _read_dependency_name(text: str, start: int) -> tuple[str, int]:
    index = start
    while index < len(text) and not text[index].isspace():
        if text[index] in {'"', "@"}:
            break
        index += 1
    return text[start:index], index


def _read_quoted_string(text: str, start: int) -> tuple[str | None, int]:
    if start >= len(text) or text[start] != '"':
        return None, start
    chars: list[str] = []
    escaped = False
    index = start + 1
    while index < len(text):
        char = text[index]
        if escaped:
            chars.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == '"':
            return "".join(chars), index + 1
        chars.append(char)
        index += 1
    return None, start


def _skip_spaces(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _dedupe_dependencies(dependencies: list[LakeDependency]) -> list[LakeDependency]:
    merged: dict[str, LakeDependency] = {}
    for dependency in dependencies:
        key = dependency.name.lower()
        existing = merged.get(key)
        if existing is None:
            merged[key] = dependency
            continue
        existing.kind = existing.kind or dependency.kind
        existing.url = existing.url or dependency.url
        existing.rev = existing.rev or dependency.rev
        existing.input_rev = existing.input_rev or dependency.input_rev
        existing.scope = existing.scope or dependency.scope
        if dependency.source not in existing.source.split(", "):
            existing.source += ", " + dependency.source
    return sorted(merged.values(), key=lambda item: item.name.lower())


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_kind(value: str | None) -> str | None:
    return value.lower() if value else None
