from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lean_agent.explainer import resolve_symbol
from lean_agent.formal_type import normalize_formal_text
from lean_agent.lean_parser import extract_statement
from lean_agent.models import Finding, ProjectAnalysis


LEAN_COMMAND_RE = re.compile(
    r"\\(?P<command>lean|leanref|leanname|leanstatement|leanthm|leantheorem|leanlemma|uses)\s*\{(?P<name>[^}]+)\}"
)
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
LABEL_RE = re.compile(r"\\label\{(?P<label>[^}]+)\}")
SECTION_RE = re.compile(r"\\(?P<level>part|chapter|section|subsection|subsubsection)\*?\{(?P<title>[^}]+)\}")


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
            "findings": [finding.to_dict() for finding in self.findings],
        }


def check_paper(
    analysis: ProjectAnalysis,
    paper_path: str | Path,
) -> PaperCheckReport:
    path = Path(paper_path)
    text = path.read_text(encoding="utf-8")
    report = PaperCheckReport(paper=str(path), lean_root=analysis.root)
    aliases = _lean_aliases(text)
    _check_lean_references(analysis, text, report, aliases)
    _check_lean_statement_commands(analysis, text, report, aliases)
    _check_formal_part_commands(analysis, text, report, aliases)
    _check_github_links(analysis, text, report)
    _check_lean_code_blocks(analysis, text, report)
    return report


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
    lines.append(f"- Status: {'OK' if report.ok() else 'Needs attention'}")
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
    return "\n".join(lines) + "\n"


def report_to_json(report: PaperCheckReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def _check_lean_references(
    analysis: ProjectAnalysis,
    text: str,
    report: PaperCheckReport,
    aliases: dict[str, str],
) -> None:
    for match in LEAN_COMMAND_RE.finditer(text):
        names = [name.strip() for name in re.split(r"[,;]", match.group("name")) if name.strip()]
        for name in names:
            report.references_checked += 1
            resolved_name = aliases.get(name, name)
            if resolve_symbol(analysis, resolved_name) is None:
                report.findings.append(
                    Finding(
                        severity="error",
                        message=f"Lean reference `{name}` from `\\{match.group('command')}` was not found in scanned declarations.",
                        location=_line_col(text, match.start()),
                        suggestion=_missing_reference_suggestion(name, resolved_name),
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

        if not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
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
        match = re.match(
            r"\s*(?:private\s+|protected\s+|noncomputable\s+)?"
            r"(?:theorem|lemma|def|abbrev|structure|class|inductive)\s+([^\s:({\[]+)",
            line,
        )
        if match:
            declarations.append(
                LeanSnippetDeclaration(
                    name=match.group(1),
                    statement=extract_statement("\n".join(lines[index:])),
                )
            )
    return declarations


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
    match = re.search(r"\)\s*:\s*(.+)$", statement, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"\s:\s*(.+)$", statement, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _normalized_statement(statement: str) -> str:
    statement = statement.strip()
    statement = re.sub(r"--.*", "", statement)
    statement = re.sub(r"\s+", " ", statement)
    return statement.strip()


def _shorten(text: str, limit: int = 180) -> str:
    normalized = _normalized_statement(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _lean_aliases(text: str) -> dict[str, str]:
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
    return aliases


def _missing_reference_suggestion(name: str, resolved_name: str) -> str:
    if name != resolved_name:
        return f"Alias `{name}` points to `{resolved_name}`, but that Lean declaration was not found. Check the alias target or Lean root."
    return "Check theorem spelling, namespace qualification, add a `\\leanalias{paper name}{Lean.Name}`, or verify that the source file is included in the Lean root."


def _iter_latex_command_args(
    text: str,
    command: str,
    arity: int,
) -> list[tuple[str, ...]]:
    results: list[tuple[str, ...]] = []
    pattern = "\\" + command
    offset = 0
    while True:
        start = text.find(pattern, offset)
        if start == -1:
            return results
        index = start + len(pattern)
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
            results.append((*args, start))
            offset = index
        else:
            offset = start + len(pattern)


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
    for match in SECTION_RE.finditer(text, 0, offset):
        current = (match.group("level"), _normalized_statement(match.group("title")))
    if current is None:
        return None
    level, title = current
    return f"{level} `{title}`"
