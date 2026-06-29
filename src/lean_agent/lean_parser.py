from __future__ import annotations

import re
from pathlib import Path

from lean_agent.models import LeanDeclaration, LeanFileAnalysis


DECLARATION_KINDS = (
    "theorem",
    "lemma",
    "def",
    "abbrev",
    "structure",
    "class",
    "inductive",
    "instance",
    "axiom",
    "constant",
    "opaque",
    "example",
)

DECLARATION_RE = re.compile(
    r"^\s*"
    r"(?:(?:private|protected|noncomputable|unsafe|partial)\s+)*"
    r"(?P<kind>" + "|".join(DECLARATION_KINDS) + r")\b"
    r"(?:\s+(?P<name>[^\s:({\[]+))?"
)
IMPORT_RE = re.compile(r"^\s*import\s+(.+?)\s*$")
NAMESPACE_RE = re.compile(r"^\s*namespace\s+([A-Za-z0-9_'.]+)\s*$")
SECTION_RE = re.compile(r"^\s*section(?:\s+[A-Za-z0-9_'.]+)?\s*$")
END_RE = re.compile(r"^\s*end(?:\s+([A-Za-z0-9_'.]+))?\s*$")
ATTRIBUTE_RE = re.compile(r"^\s*@\[(.+)\]\s*$")

KEYWORDS = {
    "by",
    "where",
    "from",
    "fun",
    "match",
    "with",
    "let",
    "have",
    "show",
    "exact",
    "simp",
    "rw",
    "intro",
    "intros",
    "apply",
    "import",
    "namespace",
    "section",
    "end",
}


def parse_lean_file(path: str | Path, root: str | Path | None = None) -> LeanFileAnalysis:
    file_path = Path(path)
    root_path = Path(root).resolve() if root else file_path.parent.resolve()
    text = file_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    imports = _extract_imports(lines)
    declarations = _find_declarations(lines, file_path, root_path)
    _attach_source_and_statements(lines, declarations)
    return LeanFileAnalysis(
        path=_relative_path(file_path, root_path),
        imports=imports,
        declarations=declarations,
    )


def _extract_imports(lines: list[str]) -> list[str]:
    imports: list[str] = []
    for line in lines:
        match = IMPORT_RE.match(line)
        if match:
            imports.extend(part.strip() for part in match.group(1).split() if part.strip())
    return imports


def _find_declarations(
    lines: list[str],
    file_path: Path,
    root_path: Path,
) -> list[LeanDeclaration]:
    declarations: list[LeanDeclaration] = []
    pending_docstring: str | None = None
    pending_attributes: list[str] = []
    namespace_stack: list[str] = []
    scope_stack: list[tuple[str, str | None]] = []
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        stripped = line.strip()

        if stripped.startswith("/--"):
            pending_docstring, line_index = _collect_docstring(lines, line_index)
            continue

        attribute_match = ATTRIBUTE_RE.match(line)
        if attribute_match:
            pending_attributes.append(attribute_match.group(1).strip())
            line_index += 1
            continue

        namespace_match = NAMESPACE_RE.match(line)
        if namespace_match:
            name = namespace_match.group(1)
            namespace_stack.append(name)
            scope_stack.append(("namespace", name))
            _clear_pending_if_needed(stripped, pending_attributes)
            line_index += 1
            continue

        if SECTION_RE.match(line):
            scope_stack.append(("section", None))
            line_index += 1
            continue

        end_match = END_RE.match(line)
        if end_match:
            _pop_scope(scope_stack, namespace_stack, end_match.group(1))
            line_index += 1
            continue

        declaration_match = DECLARATION_RE.match(line)
        if declaration_match and not _is_comment_line(line):
            kind = declaration_match.group("kind")
            raw_name = declaration_match.group("name")
            short_name = _normalize_name(raw_name, kind, line_index + 1)
            namespace = ".".join(namespace_stack) if namespace_stack else None
            full_name = _qualify_name(short_name, namespace)
            declarations.append(
                LeanDeclaration(
                    kind=kind,
                    name=full_name,
                    short_name=short_name,
                    file=_relative_path(file_path, root_path),
                    line=line_index + 1,
                    end_line=line_index + 1,
                    statement="",
                    docstring=pending_docstring,
                    attributes=pending_attributes,
                    namespace=namespace,
                )
            )
            pending_docstring = None
            pending_attributes = []
            line_index += 1
            continue

        if stripped and not stripped.startswith("--"):
            pending_docstring = None
            pending_attributes = []

        line_index += 1

    return declarations


def _clear_pending_if_needed(stripped: str, pending_attributes: list[str]) -> None:
    if stripped and not stripped.startswith("--"):
        pending_attributes.clear()


def _collect_docstring(lines: list[str], start: int) -> tuple[str, int]:
    collected: list[str] = []
    line_index = start
    while line_index < len(lines):
        collected.append(lines[line_index])
        if "-/" in lines[line_index]:
            break
        line_index += 1
    raw = "\n".join(collected)
    raw = re.sub(r"^\s*/--\s?", "", raw)
    raw = re.sub(r"\s?-/\s*$", "", raw)
    cleaned = "\n".join(_clean_doc_line(line) for line in raw.splitlines()).strip()
    return cleaned or None, line_index + 1


def _clean_doc_line(line: str) -> str:
    return re.sub(r"^\s*\*\s?", "", line).rstrip()


def _pop_scope(
    scope_stack: list[tuple[str, str | None]],
    namespace_stack: list[str],
    explicit_name: str | None,
) -> None:
    if not scope_stack:
        return
    if explicit_name:
        for index in range(len(scope_stack) - 1, -1, -1):
            scope_type, scope_name = scope_stack[index]
            if scope_name == explicit_name:
                del scope_stack[index:]
                if scope_type == "namespace":
                    while namespace_stack and namespace_stack[-1] != explicit_name:
                        namespace_stack.pop()
                    if namespace_stack:
                        namespace_stack.pop()
                return
    scope_type, scope_name = scope_stack.pop()
    if scope_type == "namespace" and namespace_stack:
        if scope_name is None or namespace_stack[-1] == scope_name:
            namespace_stack.pop()


def _normalize_name(raw_name: str | None, kind: str, line_number: int) -> str:
    if not raw_name or raw_name.startswith((":","[","(","{")):
        return f"anonymous_{kind}_{line_number}"
    if raw_name == "_":
        return f"anonymous_{kind}_{line_number}"
    return raw_name.strip()


def _qualify_name(short_name: str, namespace: str | None) -> str:
    if not namespace or short_name.startswith("_root_."):
        return short_name
    if short_name.startswith(namespace + "."):
        return short_name
    return f"{namespace}.{short_name}"


def _attach_source_and_statements(
    lines: list[str],
    declarations: list[LeanDeclaration],
) -> None:
    for index, declaration in enumerate(declarations):
        start = declaration.line - 1
        end = declarations[index + 1].line - 2 if index + 1 < len(declarations) else len(lines) - 1
        end = max(start, end)
        source_lines = lines[start : end + 1]
        declaration.end_line = end + 1
        declaration.source = "\n".join(source_lines).rstrip()
        declaration.statement = extract_statement(declaration.source)


def extract_statement(source: str) -> str:
    statement_lines: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            if statement_lines:
                break
            continue
        if stripped.startswith("/--") or stripped.startswith("*") or stripped == "-/":
            continue
        line_without_comment = _remove_line_comment(line).rstrip()
        if ":=" in line_without_comment:
            before_body = line_without_comment.split(":=", 1)[0].rstrip()
            if before_body:
                statement_lines.append(before_body)
            break
        if re.match(r"^\s*(by|where)\b", line_without_comment):
            break
        statement_lines.append(line_without_comment.rstrip())
        if _statement_seems_complete(statement_lines):
            break
    return "\n".join(statement_lines).strip()


def _statement_seems_complete(lines: list[str]) -> bool:
    if not lines:
        return False
    joined = "\n".join(lines)
    balance = 0
    for char in joined:
        if char in "([{":
            balance += 1
        elif char in ")]}":
            balance -= 1
    last = lines[-1].strip()
    return balance <= 0 and last.endswith(("Prop", "Type", "Sort", "True", "False"))


def _remove_line_comment(line: str) -> str:
    in_string = False
    previous = ""
    for index, char in enumerate(line):
        if char == '"' and previous != "\\":
            in_string = not in_string
        if not in_string and line[index : index + 2] == "--":
            return line[:index]
        previous = char
    return line


def _is_comment_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("--") or stripped.startswith("/-")


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def tokenize_lean_source(source: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_'.]*", source))
    return {token for token in tokens if token not in KEYWORDS}

