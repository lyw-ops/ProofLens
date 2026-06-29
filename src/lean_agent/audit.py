from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lean_agent.models import Finding, ProjectAnalysis


@dataclass
class AuditReport:
    root: str
    findings: list[Finding] = field(default_factory=list)
    build_ran: bool = False
    build_exit_code: int | None = None
    build_stdout: str = ""
    build_stderr: str = ""

    def ok(self) -> bool:
        return self.build_exit_code in {None, 0} and not any(
            finding.severity in {"error", "warning"} for finding in self.findings
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "ok": self.ok(),
            "findings": [finding.to_dict() for finding in self.findings],
            "build": {
                "ran": self.build_ran,
                "exit_code": self.build_exit_code,
                "stdout": self.build_stdout,
                "stderr": self.build_stderr,
            },
        }


def audit_project(
    analysis: ProjectAnalysis,
    run_build: bool = False,
    timeout: int = 120,
) -> AuditReport:
    root = Path(analysis.root)
    report = AuditReport(root=str(root))
    _check_project_files(root, report)
    _check_readme(root, report)
    if run_build:
        _run_lake_build(root, report, timeout)
    return report


def audit_to_markdown(report: AuditReport) -> str:
    lines: list[str] = []
    lines.append("# Lean Project Audit")
    lines.append("")
    lines.append(f"- Root: `{report.root}`")
    lines.append(f"- Status: {'OK' if report.ok() else 'Needs attention'}")
    lines.append(f"- Lake build: {'ran' if report.build_ran else 'not run'}")
    if report.build_ran:
        lines.append(f"- Build exit code: {report.build_exit_code}")
    lines.append("")
    if report.findings:
        lines.append("## Findings")
        lines.append("")
        for finding in report.findings:
            location = f" at `{finding.location}`" if finding.location else ""
            lines.append(f"- **{finding.severity.upper()}**{location}: {finding.message}")
            if finding.suggestion:
                lines.append(f"  Suggestion: {finding.suggestion}")
        lines.append("")
    if report.build_ran and report.build_exit_code != 0:
        lines.append("## Build stderr")
        lines.append("")
        lines.append("```text")
        lines.append(report.build_stderr.strip()[-4000:])
        lines.append("```")
    if not report.findings and (not report.build_ran or report.build_exit_code == 0):
        lines.append("No reproducibility issues found by the static audit.")
    return "\n".join(lines).rstrip() + "\n"


def audit_to_json(report: AuditReport) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def _check_project_files(root: Path, report: AuditReport) -> None:
    if not (root / "lean-toolchain").exists():
        report.findings.append(
            Finding(
                severity="warning",
                message="Missing `lean-toolchain`.",
                suggestion="Commit `lean-toolchain` so readers can reproduce the exact Lean version.",
            )
        )
    if not ((root / "lakefile.lean").exists() or (root / "lakefile.toml").exists()):
        report.findings.append(
            Finding(
                severity="warning",
                message="Missing `lakefile.lean` or `lakefile.toml`.",
                suggestion="Add a Lake configuration or run this command on the actual Lean project root.",
            )
        )
    if not (root / "lake-manifest.json").exists():
        report.findings.append(
            Finding(
                severity="info",
                message="Missing `lake-manifest.json`.",
                suggestion="For Mathlib-based artifacts, commit the manifest used for the paper artifact.",
            )
        )


def _check_readme(root: Path, report: AuditReport) -> None:
    readme = _find_readme(root)
    if readme is None:
        report.findings.append(
            Finding(
                severity="warning",
                message="Missing README.",
                suggestion="Add artifact instructions with Lean version, build command, expected output, and theorem map.",
            )
        )
        return
    text = readme.read_text(encoding="utf-8", errors="replace").lower()
    required_terms = {
        "lean version or toolchain": ["lean-toolchain", "lean version", "lean4", "elan"],
        "build instructions": ["lake build", "build"],
        "reproducibility or artifact note": ["artifact", "reproduc", "camera-ready"],
        "commit hash": ["commit", "hash", "revision"],
    }
    for label, terms in required_terms.items():
        if not any(term in text for term in terms):
            report.findings.append(
                Finding(
                    severity="info",
                    message=f"README may be missing {label}.",
                    location=str(readme.name),
                    suggestion="Add a short reproducibility section for paper reviewers and artifact evaluators.",
                )
            )


def _find_readme(root: Path) -> Path | None:
    for name in ("README.md", "README.rst", "README.txt", "Readme.md"):
        path = root / name
        if path.exists():
            return path
    return None


def _run_lake_build(root: Path, report: AuditReport, timeout: int) -> None:
    report.build_ran = True
    try:
        result = subprocess.run(
            ["lake", "build"],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        report.build_exit_code = 127
        report.build_stderr = "`lake` executable was not found on PATH."
        report.findings.append(
            Finding(
                severity="error",
                message="Cannot run `lake build` because `lake` is not installed or not on PATH.",
                suggestion="Install Lean via elan, then rerun the audit with `--run-build`.",
            )
        )
        return
    except subprocess.TimeoutExpired as exc:
        report.build_exit_code = 124
        report.build_stdout = exc.stdout or ""
        report.build_stderr = exc.stderr or ""
        report.findings.append(
            Finding(
                severity="error",
                message=f"`lake build` timed out after {timeout} seconds.",
                suggestion="Increase `--timeout` or inspect whether dependency downloads/builds are still running.",
            )
        )
        return
    report.build_exit_code = result.returncode
    report.build_stdout = result.stdout
    report.build_stderr = result.stderr
    if result.returncode != 0:
        report.findings.append(
            Finding(
                severity="error",
                message="`lake build` failed.",
                suggestion="Inspect build stderr and fix the first Lean error before rerunning.",
            )
        )

