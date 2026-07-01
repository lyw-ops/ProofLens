from __future__ import annotations

import re
from pathlib import Path

from lean_agent.formal_type import decompose_formal_type
from lean_agent.models import LeanDeclaration, LeanFileAnalysis, ProofStep


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
MUTUAL_RE = re.compile(r"^\s*mutual\s*$")
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
    block_comment_depth = 0
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        stripped = line.strip()

        if block_comment_depth:
            block_comment_depth = _block_comment_depth_after_line(line, block_comment_depth)
            line_index += 1
            continue

        if stripped.startswith("/--"):
            pending_docstring, line_index = _collect_docstring(lines, line_index)
            continue

        if stripped.startswith("/-"):
            block_comment_depth = _block_comment_depth_after_line(line, 0)
            pending_docstring = None
            pending_attributes = []
            line_index += 1
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
            pending_docstring = None
            pending_attributes = []
            line_index += 1
            continue

        if SECTION_RE.match(line):
            scope_stack.append(("section", None))
            pending_docstring = None
            pending_attributes = []
            line_index += 1
            continue

        if MUTUAL_RE.match(line):
            scope_stack.append(("mutual", None))
            pending_docstring = None
            pending_attributes = []
            line_index += 1
            continue

        end_match = END_RE.match(line)
        if end_match:
            _pop_scope(scope_stack, namespace_stack, end_match.group(1))
            pending_docstring = None
            pending_attributes = []
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
                    column=_first_nonspace_column(line),
                    end_line=line_index + 1,
                    end_column=len(line) + 1,
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


def _collect_docstring(lines: list[str], start: int) -> tuple[str, int]:
    collected: list[str] = []
    line_index = start
    while line_index < len(lines):
        collected.append(lines[line_index])
        if "-/" in lines[line_index]:
            break
        line_index += 1
    raw = "\n".join(collected)
    raw = _strip_docstring_markers(raw)
    cleaned = "\n".join(_clean_doc_line(line) for line in raw.splitlines()).strip()
    return cleaned or None, line_index + 1


def _strip_docstring_markers(raw: str) -> str:
    text = raw.lstrip()
    if text.startswith("/--"):
        text = text[3:]
        if text.startswith(" "):
            text = text[1:]
    text = text.rstrip()
    if text.endswith("-/"):
        text = text[:-2]
        if text.endswith(" "):
            text = text[:-1]
    return text


def _clean_doc_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("*"):
        stripped = stripped[1:]
        if stripped.startswith(" "):
            stripped = stripped[1:]
        return stripped.rstrip()
    return line.rstrip()


def _block_comment_depth_after_line(line: str, depth: int) -> int:
    index = 0
    while index < len(line) - 1:
        marker = line[index : index + 2]
        if marker == "/-":
            depth += 1
            index += 2
            continue
        if marker == "-/" and depth:
            depth -= 1
            index += 2
            continue
        index += 1
    return depth


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
        declaration.end_column = len(lines[end]) + 1 if 0 <= end < len(lines) else 1
        declaration.source = "\n".join(source_lines).rstrip()
        declaration.statement = extract_statement(declaration.source)
        formal_type = decompose_formal_type(declaration.statement)
        declaration.formal_parameters = formal_type.parameters
        declaration.formal_conclusion = formal_type.conclusion
        declaration.proof_steps = extract_proof_steps(declaration.source, declaration.line, declaration.kind)


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
        if _starts_with_keyword(line_without_comment, {"by", "where"}):
            break
        statement_lines.append(line_without_comment.rstrip())
        if _statement_seems_complete(statement_lines):
            break
    return "\n".join(statement_lines).strip()


PROOF_KINDS = {"theorem", "lemma", "example"}


def extract_proof_steps(
    source: str,
    start_line: int = 1,
    kind: str | None = None,
) -> list[ProofStep]:
    if kind is not None and kind not in PROOF_KINDS:
        return []
    lines = source.splitlines()
    proof_start = _proof_start_index(lines)
    if proof_start is None:
        return []
    steps: list[ProofStep] = []
    for relative_index in range(proof_start, len(lines)):
        raw_line = lines[relative_index]
        line_without_comment = _remove_line_comment(raw_line)
        if _is_proof_terminator(line_without_comment):
            break
        normalized = _normalize_tactic_line(line_without_comment)
        if not normalized:
            continue
        for step_text in _tactic_segments(normalized):
            if not step_text or step_text in {"by", "where"}:
                continue
            if step_text.startswith(("case ", "| ")) and "=>" not in step_text:
                continue
            step_text = _inline_branch_tactic(step_text)
            tactic = _tactic_head(step_text)
            if not tactic:
                continue
            steps.append(
                ProofStep(
                    index=len(steps) + 1,
                    tactic=tactic,
                    text=step_text,
                    line=start_line + relative_index,
                    column=_tactic_column(raw_line, step_text),
                    end_line=start_line + relative_index,
                )
            )
    return steps


def _proof_start_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        stripped = _remove_line_comment(line).strip()
        if ":= by" in stripped or stripped == "by" or stripped.startswith("by "):
            return index
    return None


def _normalize_tactic_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    if ":= by" in stripped:
        after_by = stripped.split(":= by", 1)[1].strip()
        return after_by
    if stripped.startswith("by "):
        return stripped[3:].strip()
    while stripped.startswith(("·", "*", "-")):
        stripped = stripped[1:].strip()
    return stripped


def _tactic_segments(line: str) -> list[str]:
    segments: list[str] = []
    start = 0
    index = 0
    depth = 0
    in_string = False
    escaped = False
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
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        if depth == 0 and line.startswith("<;>", index):
            _push_segment(segments, line[start:index])
            index += 3
            start = index
            continue
        if depth == 0 and char == ";":
            _push_segment(segments, line[start:index])
            index += 1
            start = index
            continue
        index += 1
    _push_segment(segments, line[start:])
    return segments


def _push_segment(segments: list[str], segment: str) -> None:
    cleaned = segment.strip()
    if cleaned:
        segments.append(cleaned)


def _inline_branch_tactic(step_text: str) -> str:
    if step_text.startswith(("case ", "| ")) and "=>" in step_text:
        return step_text.split("=>", 1)[1].strip()
    return step_text


def _is_proof_terminator(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped == "where"
        or stripped.startswith("where ")
        or END_RE.match(stripped)
        or NAMESPACE_RE.match(stripped)
        or SECTION_RE.match(stripped)
        or MUTUAL_RE.match(stripped)
    )


def _tactic_head(step_text: str) -> str:
    return _read_identifier(step_text, 0) or ""


def _first_nonspace_column(line: str) -> int:
    for index, char in enumerate(line):
        if not char.isspace():
            return index + 1
    return 1


def _tactic_column(line: str, step_text: str) -> int:
    if step_text:
        index = line.find(step_text)
        if index >= 0:
            return index + 1
    index = 0
    while index < len(line) and line[index].isspace():
        index += 1
    while index < len(line) and line[index] in {"·", "*", "-"}:
        index += 1
        while index < len(line) and line[index].isspace():
            index += 1
    return index + 1 if index < len(line) else _first_nonspace_column(line)


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
    tokens = _identifier_tokens(_strip_comments_and_strings(source))
    return {token for token in tokens if token not in KEYWORDS}


def _identifier_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    index = 0
    while index < len(text):
        token = _read_identifier(text, index)
        if token:
            tokens.add(token)
            index += len(token)
            continue
        index += 1
    return tokens


def _read_identifier(text: str, start: int) -> str | None:
    if start >= len(text) or not _is_identifier_start(text[start]):
        return None
    index = start + 1
    while index < len(text) and _is_identifier_char(text[index]):
        index += 1
    return text[start:index]


def _is_identifier_start(char: str) -> bool:
    return char.isalpha() or char == "_"


def _is_identifier_char(char: str) -> bool:
    return char.isalnum() or char in {"_", "'", "."}


def _starts_with_keyword(text: str, keywords: set[str]) -> bool:
    stripped = text.lstrip()
    for keyword in keywords:
        if stripped == keyword:
            return True
        if stripped.startswith(keyword) and len(stripped) > len(keyword):
            return not _is_identifier_char(stripped[len(keyword)])
    return False


def _strip_comments_and_strings(source: str) -> str:
    cleaned: list[str] = []
    index = 0
    block_depth = 0
    in_string = False
    escaped = False

    while index < len(source):
        char = source[index]
        marker = source[index : index + 2]

        if block_depth:
            if marker == "/-":
                block_depth += 1
                cleaned.append("  ")
                index += 2
                continue
            if marker == "-/":
                block_depth -= 1
                cleaned.append("  ")
                index += 2
                continue
            cleaned.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if in_string:
            cleaned.append("\n" if char == "\n" else " ")
            if char == '"' and not escaped:
                in_string = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            index += 1
            continue

        if marker == "--":
            while index < len(source) and source[index] != "\n":
                cleaned.append(" ")
                index += 1
            continue
        if marker == "/-":
            block_depth = 1
            cleaned.append("  ")
            index += 2
            continue
        if char == '"':
            in_string = True
            escaped = False
            cleaned.append(" ")
            index += 1
            continue

        cleaned.append(char)
        index += 1

    return "".join(cleaned)
