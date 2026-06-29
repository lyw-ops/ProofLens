from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lean_agent.explainer import resolve_symbol
from lean_agent.models import Finding, ProjectAnalysis


LEAN_COMMAND_RE = re.compile(
    r"\\(?P<command>lean|leanref|leanname|leanstatement|uses)\s*\{(?P<name>[^}]+)\}"
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


@dataclass
class PaperCheckReport:
    paper: str
    lean_root: str
    findings: list[Finding] = field(default_factory=list)
    references_checked: int = 0
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
    _check_lean_references(analysis, text, report)
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
) -> None:
    for match in LEAN_COMMAND_RE.finditer(text):
        names = [name.strip() for name in re.split(r"[,;]", match.group("name")) if name.strip()]
        for name in names:
            report.references_checked += 1
            if resolve_symbol(analysis, name) is None:
                report.findings.append(
                    Finding(
                        severity="error",
                        message=f"Lean reference `{name}` from `\\{match.group('command')}` was not found in scanned declarations.",
                        location=_line_col(text, match.start()),
                        suggestion="Check theorem spelling, namespace qualification, or whether the source file is included in the Lean root.",
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
        names = _declared_names_in_code_block(body)
        for name in names:
            if resolve_symbol(analysis, name) is None:
                report.findings.append(
                    Finding(
                        severity="warning",
                        message=f"Lean code block declares or mentions `{name}`, but it was not found in the scanned project.",
                        location=_line_col(text, match.start()),
                        suggestion="Check whether the appendix snippet is stale or intentionally pseudocode.",
                    )
                )


def _declared_names_in_code_block(body: str) -> list[str]:
    names: list[str] = []
    for line in body.splitlines():
        match = re.match(
            r"\s*(?:private\s+|protected\s+|noncomputable\s+)?"
            r"(?:theorem|lemma|def|abbrev|structure|class|inductive)\s+([^\s:({\[]+)",
            line,
        )
        if match:
            names.append(match.group(1))
    return names


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
    return f"line {line}, column {column}"
