from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lean_agent.explainer import resolve_symbol
from lean_agent.formal_type import normalize_formal_text
from lean_agent.lean_parser import extract_statement
from lean_agent.models import Finding, ProjectAnalysis


LEAN_REFERENCE_COMMANDS = (
    "lean",
    "leanref",
    "leanname",
    "leanstatement",
    "leanthm",
    "leantheorem",
    "leanlemma",
    "uses",
)
LATEX_INCLUDE_COMMANDS = ("input", "include")
LATEX_SECTION_COMMANDS = ("part", "chapter", "section", "subsection", "subsubsection")
LATEX_SYMBOL_WRAPPERS = {"texttt", "mathrm", "mathsf", "operatorname", "ensuremath", "protect"}
SNIPPET_DECLARATION_KINDS = {"theorem", "lemma", "def", "abbrev", "structure", "class", "inductive"}
SNIPPET_DECLARATION_MODIFIERS = {"private", "protected", "noncomputable"}
GITHUB_LINK_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/"
    r"(?P<kind>blob|tree)/(?P<ref>[^/\s#)]+)/(?P<path>[^#)\s]+)"
    r"(?:#L(?P<line_start>\d+)(?:-L(?P<line_end>\d+))?)?"
)
LEAN_BLOCK_RE = re.compile(
    r"\\begin\{(?P<env>lstlisting|minted)\}(?:\[[^\]]*\])?(?:\{Lean\})?"
    r"(?P<body>.*?)"
    r"\\end\{(?P=env)\}",
    flags=re.DOTALL | re.IGNORECASE,
)


@dataclass
class _LatexCommandMatch:
    command: str
    args: tuple[str, ...]
    start: int
    end: int


@dataclass
class _PatchEdit:
    start: int
    end: int
    replacement: str
    kind: str
    location: str
    target: str | None = None


@dataclass
class AppliedPaperPatch:
    kind: str
    location: str
    replacement: str
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaperCheckReport:
    paper: str
    lean_root: str
    findings: list[Finding] = field(default_factory=list)
    references_checked: int = 0
    statements_checked: int = 0
    formal_parts_checked: int = 0
    github_links_checked: int = 0
    code_blocks_checked: int = 0
    patches_suggested: int = 0
    patches_applied: int = 0
    applied_patches: list[AppliedPaperPatch] = field(default_factory=list)

    def ok(self) -> bool:
        return not any(finding.severity in {"error", "warning"} for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper": self.paper,
            "lean_root": self.lean_root,
            "ok": self.ok(),
            "references_checked": self.references_checked,
            "statements_checked": self.statements_checked,
            "formal_parts_checked": self.formal_parts_checked,
            "github_links_checked": self.github_links_checked,
            "code_blocks_checked": self.code_blocks_checked,
            "patches_suggested": self.patches_suggested,
            "patches_applied": self.patches_applied,
            "applied_patches": [patch.to_dict() for patch in self.applied_patches],
            "findings": [finding.to_dict() for finding in self.findings],
        }


def check_paper(
    analysis: ProjectAnalysis,
    paper_path: str | Path,
    apply_patches: bool = False,
) -> PaperCheckReport:
    path = Path(paper_path)
    text = path.read_text(encoding="utf-8")
    report = _check_paper_text(analysis, path, text)
    edits = _collect_safe_patch_edits(analysis, text, path.parent)
    report.patches_suggested = len(edits)
    if not apply_patches:
        return report

    patched_text, applied_patches = _apply_safe_patch_edits(text, edits)
    if patched_text != text:
        path.write_text(patched_text, encoding="utf-8")
        report = _check_paper_text(analysis, path, patched_text)
    report.patches_suggested = len(edits)
    report.patches_applied = len(applied_patches)
    report.applied_patches = applied_patches
    return report


def _check_paper_text(
    analysis: ProjectAnalysis,
    path: Path,
    text: str,
) -> PaperCheckReport:
    report = PaperCheckReport(paper=str(path), lean_root=analysis.root)
    aliases = _lean_aliases(text, path.parent)
    _check_lean_references(analysis, text, report, aliases)
    _check_lean_statement_commands(analysis, text, report, aliases)
    _check_formal_part_commands(analysis, text, report, aliases)
    _check_github_links(analysis, text, report)
    _check_lean_code_blocks(analysis, text, report)
    return report


def _collect_safe_patch_edits(
    analysis: ProjectAnalysis,
    text: str,
    base_dir: Path,
) -> list[_PatchEdit]:
    aliases = _lean_aliases(text, base_dir)
    edits: list[_PatchEdit] = []
    edits.extend(_statement_patch_edits(analysis, text, aliases))
    edits.extend(_formal_part_patch_edits(analysis, text, aliases))
    edits.extend(_alias_patch_edits(analysis, text, aliases))
    return _dedupe_patch_edits(edits)


def _statement_patch_edits(
    analysis: ProjectAnalysis,
    text: str,
    aliases: dict[str, str],
) -> list[_PatchEdit]:
    edits: list[_PatchEdit] = []
    for match in _iter_latex_command_matches(text, "leanstatement", arity=2):
        symbol, paper_statement = match.args
        declaration = resolve_symbol(analysis, aliases.get(symbol, symbol))
        if declaration is None or _statement_matches_declaration(paper_statement, declaration):
            continue
        expected = declaration.semantic_type or declaration.statement
        replacement = _replacement_command("leanstatement", symbol, expected)
        edits.append(
            _PatchEdit(
                start=match.start,
                end=match.end,
                replacement=replacement,
                kind="replace_leanstatement",
                location=_line_col(text, match.start),
                target=symbol,
            )
        )
    return edits


def _formal_part_patch_edits(
    analysis: ProjectAnalysis,
    text: str,
    aliases: dict[str, str],
) -> list[_PatchEdit]:
    edits: list[_PatchEdit] = []
    for match in _iter_latex_command_matches(text, "leanconclusion", arity=2):
        symbol, paper_conclusion = match.args
        declaration = resolve_symbol(analysis, aliases.get(symbol, symbol))
        if declaration is None:
            continue
        expected = declaration.formal_conclusion or _type_part_from_statement(declaration.statement) or ""
        if normalize_formal_text(paper_conclusion) == normalize_formal_text(expected):
            continue
        edits.append(
            _PatchEdit(
                start=match.start,
                end=match.end,
                replacement=_replacement_command("leanconclusion", symbol, expected),
                kind="replace_leanconclusion",
                location=_line_col(text, match.start),
                target=symbol,
            )
        )
    for match in _iter_latex_command_matches(text, "leanassumptions", arity=2):
        symbol, paper_assumptions = match.args
        declaration = resolve_symbol(analysis, aliases.get(symbol, symbol))
        if declaration is None:
            continue
        expected = "; ".join(
            f"{' '.join(parameter.names)} : {parameter.type}".strip()
            for parameter in declaration.formal_parameters
            if parameter.role == "assumption"
        )
        if normalize_formal_text(paper_assumptions) == normalize_formal_text(expected):
            continue
        edits.append(
            _PatchEdit(
                start=match.start,
                end=match.end,
                replacement=_replacement_command("leanassumptions", symbol, expected),
                kind="replace_leanassumptions",
                location=_line_col(text, match.start),
                target=symbol,
            )
        )
    return edits


def _alias_patch_edits(
    analysis: ProjectAnalysis,
    text: str,
    aliases: dict[str, str],
) -> list[_PatchEdit]:
    alias_lines: list[str] = []
    targets: list[str] = []
    for _command, raw_name, _offset in _iter_latex_commands(text, LEAN_REFERENCE_COMMANDS, arity=1):
        for name in _split_symbol_list(raw_name):
            resolved_name = aliases.get(name, name)
            if resolve_symbol(analysis, resolved_name) is not None:
                continue
            patch = _missing_reference_patch(analysis, name, resolved_name)
            if not patch or patch in text or patch in alias_lines:
                continue
            alias_lines.append(patch)
            targets.append(name)
    if not alias_lines:
        return []
    offset = _alias_insertion_offset(text)
    replacement = _format_alias_insertion(text, offset, alias_lines)
    return [
        _PatchEdit(
            start=offset,
            end=offset,
            replacement=replacement,
            kind="insert_leanalias",
            location=_line_col(text, offset),
            target=", ".join(targets),
        )
    ]


def _dedupe_patch_edits(edits: list[_PatchEdit]) -> list[_PatchEdit]:
    seen: set[tuple[int, int, str]] = set()
    unique: list[_PatchEdit] = []
    for edit in edits:
        key = (edit.start, edit.end, edit.replacement)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edit)
    return unique


def _apply_safe_patch_edits(
    text: str,
    edits: list[_PatchEdit],
) -> tuple[str, list[AppliedPaperPatch]]:
    patched = text
    applied: list[AppliedPaperPatch] = []
    next_start = len(text) + 1
    for edit in sorted(edits, key=lambda item: (item.start, item.end), reverse=True):
        if edit.end > next_start:
            continue
        patched = patched[: edit.start] + edit.replacement + patched[edit.end :]
        next_start = edit.start
        applied.append(
            AppliedPaperPatch(
                kind=edit.kind,
                location=edit.location,
                replacement=edit.replacement.strip(),
                target=edit.target,
            )
        )
    applied.reverse()
    return patched, applied


def _alias_insertion_offset(text: str) -> int:
    alias_matches = [
        *_iter_latex_command_matches(text, "leanalias", arity=2),
        *_iter_latex_command_matches(text, "leantheoremalias", arity=2),
    ]
    if alias_matches:
        return max(match.end for match in alias_matches)
    begin_document = text.find(r"\begin{document}")
    if begin_document != -1:
        return begin_document
    return 0


def _format_alias_insertion(text: str, offset: int, alias_lines: list[str]) -> str:
    insertion = "\n".join(alias_lines) + "\n"
    if offset > 0 and not text[:offset].endswith("\n"):
        insertion = "\n" + insertion
    if offset < len(text) and not text[offset:].startswith("\n"):
        insertion += "\n"
    return insertion


def report_to_markdown(report: PaperCheckReport) -> str:
    lines: list[str] = []
    lines.append("# Paper Consistency Report")
    lines.append("")
    lines.append(f"- Paper: `{report.paper}`")
    lines.append(f"- Lean root: `{report.lean_root}`")
    lines.append(f"- Lean references checked: {report.references_checked}")
    lines.append(f"- Lean statements checked: {report.statements_checked}")
    lines.append(f"- Formal parts checked: {report.formal_parts_checked}")
    lines.append(f"- GitHub links checked: {report.github_links_checked}")
    lines.append(f"- Lean code blocks checked: {report.code_blocks_checked}")
    lines.append(f"- Patch suggestions: {report.patches_suggested}")
    lines.append(f"- Patches applied: {report.patches_applied}")
    lines.append(f"- Status: {'OK' if report.ok() else 'Needs attention'}")
    lines.append("")
    if report.applied_patches:
        lines.append("## Applied Patches")
        lines.append("")
        for patch in report.applied_patches:
            target = f" `{patch.target}`" if patch.target else ""
            lines.append(f"- {patch.kind}{target} at `{patch.location}`: `{patch.replacement}`")
        lines.append("")
    if not report.findings:
        lines.append("No consistency issues found.")
        return "\n".join(lines) + "\n"
    lines.append("## Findings")
    lines.append("")
    for finding in report.findings:
        location = f" at `{finding.location}`" if finding.location else ""
        lines.append(f"- **{finding.severity.upper()}**{location}: {finding.message}")
        if finding.suggestion:
            lines.append(f"  Suggestion: {finding.suggestion}")
        if finding.patch:
            lines.append(f"  Patch: `{finding.patch}`")
    return "\n".join(lines) + "\n"


def report_to_json(report: PaperCheckReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def _check_lean_references(
    analysis: ProjectAnalysis,
    text: str,
    report: PaperCheckReport,
    aliases: dict[str, str],
) -> None:
    for command, raw_name, offset in _iter_latex_commands(text, LEAN_REFERENCE_COMMANDS, arity=1):
        names = _split_symbol_list(raw_name)
        for name in names:
            report.references_checked += 1
            resolved_name = aliases.get(name, name)
            if resolve_symbol(analysis, resolved_name) is None:
                patch = _missing_reference_patch(analysis, name, resolved_name)
                report.findings.append(
                    Finding(
                        severity="error",
                        message=f"Lean reference `{name}` from `\\{command}` was not found in scanned declarations.",
                        location=_line_col(text, offset),
                        suggestion=_missing_reference_suggestion(name, resolved_name, patch),
                        patch=patch,
                    )
                )


def _check_lean_statement_commands(
    analysis: ProjectAnalysis,
    text: str,
    report: PaperCheckReport,
    aliases: dict[str, str],
) -> None:
    for symbol, paper_statement, offset in _iter_latex_command_args(text, "leanstatement", arity=2):
        report.statements_checked += 1
        declaration = resolve_symbol(analysis, aliases.get(symbol, symbol))
        if declaration is None:
            continue
        if _statement_matches_declaration(paper_statement, declaration):
            continue
        expected = declaration.semantic_type or declaration.statement
        report.findings.append(
            Finding(
                severity="warning",
                message=f"Paper statement for `{symbol}` does not match the scanned Lean declaration.",
                location=_line_col(text, offset),
                suggestion=f"Update the paper statement or Lean source. Expected approximately: `{_shorten(expected)}`.",
                patch=_replacement_command("leanstatement", symbol, expected),
            )
        )


def _check_formal_part_commands(
    analysis: ProjectAnalysis,
    text: str,
    report: PaperCheckReport,
    aliases: dict[str, str],
) -> None:
    for symbol, paper_conclusion, offset in _iter_latex_command_args(text, "leanconclusion", arity=2):
        report.formal_parts_checked += 1
        declaration = resolve_symbol(analysis, aliases.get(symbol, symbol))
        if declaration is None:
            continue
        expected = declaration.formal_conclusion or _type_part_from_statement(declaration.statement) or ""
        if normalize_formal_text(paper_conclusion) == normalize_formal_text(expected):
            continue
        report.findings.append(
            Finding(
                severity="warning",
                message=f"Paper conclusion for `{symbol}` does not match the Lean declaration conclusion.",
                location=_line_col(text, offset),
                suggestion=f"Expected approximately: `{_shorten(expected)}`.",
                patch=_replacement_command("leanconclusion", symbol, expected),
            )
        )
    for symbol, paper_assumptions, offset in _iter_latex_command_args(text, "leanassumptions", arity=2):
        report.formal_parts_checked += 1
        declaration = resolve_symbol(analysis, aliases.get(symbol, symbol))
        if declaration is None:
            continue
        expected = "; ".join(
            f"{' '.join(parameter.names)} : {parameter.type}".strip()
            for parameter in declaration.formal_parameters
            if parameter.role == "assumption"
        )
        if normalize_formal_text(paper_assumptions) == normalize_formal_text(expected):
            continue
        report.findings.append(
            Finding(
                severity="warning",
                message=f"Paper assumptions for `{symbol}` do not match the Lean declaration assumptions.",
                location=_line_col(text, offset),
                suggestion=f"Expected approximately: `{_shorten(expected)}`.",
                patch=_replacement_command("leanassumptions", symbol, expected),
            )
        )


def _check_github_links(
    analysis: ProjectAnalysis,
    text: str,
    report: PaperCheckReport,
) -> None:
    head = _git_head(Path(analysis.root))
    for match in GITHUB_LINK_RE.finditer(text):
        report.github_links_checked += 1
        ref = match.group("ref")
        rel_path = match.group("path")
        linked_file = Path(analysis.root) / rel_path
        location = _line_col(text, match.start())

        if not _is_hex_commit_hash(ref):
            report.findings.append(
                Finding(
                    severity="warning",
                    message=f"GitHub link to `{rel_path}` is not pinned to a 40-character commit hash: `{ref}`.",
                    location=location,
                    suggestion="Use a fixed commit hash for camera-ready papers and artifact instructions.",
                )
            )
        elif head and ref.lower() != head.lower():
            report.findings.append(
                Finding(
                    severity="warning",
                    message=f"GitHub link commit `{ref}` differs from local HEAD `{head}`.",
                    location=location,
                    suggestion="Update the paper link or verify that the paper intentionally points to another artifact commit.",
                )
            )

        if match.group("kind") == "blob" and rel_path.endswith(".lean"):
            if not linked_file.exists():
                report.findings.append(
                    Finding(
                        severity="error",
                        message=f"GitHub link points to missing local Lean file `{rel_path}`.",
                        location=location,
                        suggestion="Check the repository path in the paper.",
                    )
                )
                continue
            line_start = match.group("line_start")
            if line_start:
                _check_link_line_has_nearby_declaration(
                    analysis,
                    rel_path,
                    int(line_start),
                    report,
                    location,
                )


def _check_link_line_has_nearby_declaration(
    analysis: ProjectAnalysis,
    rel_path: str,
    line_number: int,
    report: PaperCheckReport,
    location: str,
) -> None:
    declarations = [
        declaration
        for declaration in analysis.declarations
        if declaration.file == rel_path and declaration.line - 3 <= line_number <= declaration.end_line + 3
    ]
    if not declarations:
        report.findings.append(
            Finding(
                severity="info",
                message=f"GitHub link `{rel_path}#L{line_number}` does not point near a scanned declaration.",
                location=location,
                suggestion="If this is meant to cite a theorem, link to the theorem statement line.",
            )
        )


def _check_lean_code_blocks(
    analysis: ProjectAnalysis,
    text: str,
    report: PaperCheckReport,
) -> None:
    for match in LEAN_BLOCK_RE.finditer(text):
        body = match.group("body")
        report.code_blocks_checked += 1
        snippets = _declarations_in_code_block(body)
        for snippet in snippets:
            declaration = resolve_symbol(analysis, snippet.name)
            if declaration is None:
                report.findings.append(
                    Finding(
                        severity="warning",
                        message=f"Lean code block declares or mentions `{snippet.name}`, but it was not found in the scanned project.",
                        location=_line_col(text, match.start()),
                        suggestion="Check whether the appendix snippet is stale or intentionally pseudocode.",
                    )
                )
                continue
            report.statements_checked += 1
            if not snippet.statement or _normalized_statement(snippet.statement) == _normalized_statement(declaration.statement):
                continue
            report.findings.append(
                Finding(
                    severity="warning",
                    message=f"Lean code block statement for `{snippet.name}` differs from the scanned source declaration.",
                    location=_line_col(text, match.start()),
                    suggestion=f"Update the appendix snippet. Source statement is approximately: `{_shorten(declaration.statement)}`.",
                    patch=declaration.statement,
                )
            )


@dataclass
class LeanSnippetDeclaration:
    name: str
    statement: str


def _declarations_in_code_block(body: str) -> list[LeanSnippetDeclaration]:
    declarations: list[LeanSnippetDeclaration] = []
    lines = body.splitlines()
    for index, line in enumerate(lines):
        name = _snippet_declaration_name(line)
        if name:
            declarations.append(
                LeanSnippetDeclaration(
                    name=name,
                    statement=extract_statement("\n".join(lines[index:])),
                )
            )
    return declarations


def _snippet_declaration_name(line: str) -> str | None:
    words = _leading_words(line)
    index = 0
    while index < len(words) and words[index][0] in SNIPPET_DECLARATION_MODIFIERS:
        index += 1
    if index >= len(words) or words[index][0] not in SNIPPET_DECLARATION_KINDS:
        return None
    name_start = _skip_whitespace(line, words[index][2])
    name_end = name_start
    while name_end < len(line) and line[name_end] not in " \t\r\n:({[":
        name_end += 1
    name = line[name_start:name_end].strip()
    return name or None


def _leading_words(line: str) -> list[tuple[str, int, int]]:
    words: list[tuple[str, int, int]] = []
    index = _skip_whitespace(line, 0)
    while index < len(line):
        start = index
        while index < len(line) and (line[index].isalpha() or line[index] == "_"):
            index += 1
        if start == index:
            break
        words.append((line[start:index], start, index))
        index = _skip_whitespace(line, index)
    return words


def _statement_matches_declaration(paper_statement: str, declaration) -> bool:
    normalized = _normalized_statement(paper_statement)
    if not normalized:
        return False
    candidates = [
        declaration.semantic_type,
        declaration.statement,
        _type_part_from_statement(declaration.statement),
    ]
    return any(
        normalized == _normalized_statement(candidate)
        for candidate in candidates
        if candidate
    )


def _type_part_from_statement(statement: str) -> str | None:
    if ":=" in statement:
        statement = statement.split(":=", 1)[0]
    colon_index = _top_level_colon_index(statement)
    if colon_index is not None:
        return statement[colon_index + 1 :].strip()
    return None


def _normalized_statement(statement: str) -> str:
    lines = [_strip_line_comment(line) for line in statement.strip().splitlines()]
    return " ".join(" ".join(lines).split())


def _top_level_colon_index(text: str) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if char == '"' and not escaped:
                in_string = False
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char == '"':
            in_string = True
            escaped = False
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}" and depth:
            depth -= 1
            continue
        if char == ":" and depth == 0:
            return index
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
        if line.startswith("--", index):
            return line[:index]
        index += 1
    return line


def _shorten(text: str, limit: int = 180) -> str:
    normalized = _normalized_statement(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _lean_aliases(
    text: str,
    base_dir: Path | None = None,
    visited: set[Path] | None = None,
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for alias, target, _offset in _iter_latex_command_args(text, "leanalias", arity=2):
        alias = alias.strip()
        target = target.strip()
        if alias and target:
            aliases[alias] = target
    for alias, target, _offset in _iter_latex_command_args(text, "leantheoremalias", arity=2):
        alias = alias.strip()
        target = target.strip()
        if alias and target:
            aliases[alias] = target
    if base_dir is not None:
        visited = visited or set()
        for include_path in _included_latex_paths(text, base_dir):
            resolved = include_path.resolve()
            if resolved in visited or not resolved.exists():
                continue
            visited.add(resolved)
            aliases.update(_lean_aliases(resolved.read_text(encoding="utf-8"), resolved.parent, visited))
    return aliases


def _missing_reference_suggestion(name: str, resolved_name: str, patch: str | None = None) -> str:
    if name != resolved_name:
        return f"Alias `{name}` points to `{resolved_name}`, but that Lean declaration was not found. Check the alias target or Lean root."
    if patch:
        return "A similarly named Lean declaration was found. Add the alias patch below or update the reference spelling."
    return "Check theorem spelling, namespace qualification, add a `\\leanalias{paper name}{Lean.Name}`, or verify that the source file is included in the Lean root."


def _missing_reference_patch(
    analysis: ProjectAnalysis,
    name: str,
    resolved_name: str,
) -> str | None:
    if name != resolved_name:
        return None
    candidate = _candidate_declaration_name(analysis, name)
    if candidate is None or candidate == name:
        return None
    return _replacement_command("leanalias", name, candidate)


def _candidate_declaration_name(analysis: ProjectAnalysis, name: str) -> str | None:
    token = name.split(".")[-1].strip()
    if not token:
        return None
    names = analysis.declaration_index().names_for_token(token)
    if len(names) == 1:
        return names[0]
    lowered = name.lower()
    matches = [
        declaration.name
        for declaration in analysis.declarations
        if declaration.name.lower().endswith(lowered)
    ]
    return matches[0] if len(matches) == 1 else None


def _replacement_command(command: str, *args: str) -> str:
    return "\\" + command + "".join("{" + arg + "}" for arg in args)


def _included_latex_paths(text: str, base_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for _command, raw_path, _offset in _iter_latex_commands(text, LATEX_INCLUDE_COMMANDS, arity=1):
        raw_path = raw_path.strip()
        if not raw_path:
            continue
        path = base_dir / raw_path
        if path.suffix == "":
            path = path.with_suffix(".tex")
        paths.append(path)
    return paths


def _iter_latex_commands(
    text: str,
    commands: tuple[str, ...],
    arity: int,
) -> list[tuple[str, ...]]:
    matches: list[tuple[str, ...]] = []
    for command in commands:
        for match in _iter_latex_command_matches(text, command, arity=arity):
            matches.append((command, *match.args, match.start))
    return sorted(matches, key=lambda item: item[-1])


def _split_symbol_list(text: str) -> list[str]:
    symbols: list[str] = []
    start = 0
    depth = 0
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}" and depth:
            depth -= 1
            continue
        if depth == 0 and char in {",", ";"}:
            _push_symbol(symbols, text[start:index])
            start = index + 1
    _push_symbol(symbols, text[start:])
    return symbols


def _push_symbol(symbols: list[str], value: str) -> None:
    cleaned = _clean_latex_symbol(value)
    if cleaned:
        symbols.append(cleaned)


def _clean_latex_symbol(value: str) -> str:
    cleaned = value.strip()
    while True:
        unwrapped = _unwrap_latex_symbol_wrapper(cleaned)
        if unwrapped is None:
            break
        cleaned = unwrapped.strip()
    if cleaned.startswith("$") and cleaned.endswith("$") and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned.replace(r"\_", "_").strip()


def _unwrap_latex_symbol_wrapper(text: str) -> str | None:
    if not text.startswith("\\"):
        return None
    index = 1
    start = index
    while index < len(text) and text[index].isalpha():
        index += 1
    command = text[start:index]
    if command not in LATEX_SYMBOL_WRAPPERS:
        return None
    index = _skip_whitespace(text, index)
    if index >= len(text) or text[index] != "{":
        return None
    body, next_index = _read_braced_argument(text, index)
    if body is None:
        return None
    if _skip_whitespace(text, next_index) != len(text):
        return None
    return body


def _iter_latex_command_args(
    text: str,
    command: str,
    arity: int,
) -> list[tuple[str, ...]]:
    return [
        (*match.args, match.start)
        for match in _iter_latex_command_matches(text, command, arity=arity)
    ]


def _iter_latex_command_matches(
    text: str,
    command: str,
    arity: int,
) -> list[_LatexCommandMatch]:
    results: list[_LatexCommandMatch] = []
    pattern = "\\" + command
    offset = 0
    while True:
        start = text.find(pattern, offset)
        if start == -1:
            return results
        index = start + len(pattern)
        if not _latex_command_boundary(text, index):
            offset = index
            continue
        if index < len(text) and text[index] == "*":
            index += 1
        args: list[str] = []
        ok = True
        for _ in range(arity):
            index = _skip_whitespace(text, index)
            if index >= len(text) or text[index] != "{":
                ok = False
                break
            arg, index = _read_braced_argument(text, index)
            if arg is None:
                ok = False
                break
            args.append(arg)
        if ok:
            results.append(
                _LatexCommandMatch(
                    command=command,
                    args=tuple(args),
                    start=start,
                    end=index,
                )
            )
            offset = index
        else:
            offset = start + len(pattern)


def _latex_command_boundary(text: str, index: int) -> bool:
    return index >= len(text) or not text[index].isalpha()


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _read_braced_argument(text: str, start: int) -> tuple[str | None, int]:
    depth = 0
    index = start
    content_start = start + 1
    escaped = False
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[content_start:index], index + 1
        index += 1
    return None, start + 1


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _is_hex_commit_hash(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdefABCDEF" for char in value)


def _line_col(text: str, offset: int) -> str:
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset)
    column = offset + 1 if line_start == -1 else offset - line_start
    section = _section_at(text, offset)
    if section:
        return f"line {line}, column {column}, {section}"
    return f"line {line}, column {column}"


def _section_at(text: str, offset: int) -> str | None:
    current: tuple[str, str] | None = None
    for command, title, section_offset in _iter_latex_commands(text, LATEX_SECTION_COMMANDS, arity=1):
        if section_offset >= offset:
            break
        current = (command, _normalized_statement(title))
    if current is None:
        return None
    level, title = current
    return f"{level} `{title}`"
