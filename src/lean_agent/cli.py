from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lean_agent.audit import audit_project, audit_to_json, audit_to_markdown
from lean_agent.benchmark import build_benchmark_items, write_benchmark
from lean_agent.environment import environment_to_json, environment_to_markdown, inspect_environment
from lean_agent.explainer import explain_symbol
from lean_agent.paper_checker import check_paper, report_to_json, report_to_markdown
from lean_agent.project import project_to_json, project_to_markdown, scan_project


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 1
    except Exception as exc:
        print(f"prooflens: error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prooflens",
        description="Prooflens: a Lean-aware assistant for formalization projects, papers, and AI4Math benchmarks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan a Lean project and summarize declarations.")
    scan.add_argument("path", help="Lean project root or a .lean file")
    scan.add_argument("--format", choices=("markdown", "json"), default="markdown")
    scan.add_argument("--include-source", action="store_true", help="Include full source blocks in JSON output.")
    scan.add_argument(
        "--ast-declarations",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use Lean AST extraction to refine declarations; defaults to on for Lake projects.",
    )
    scan.add_argument("--ast-declaration-timeout", type=int, default=120, help="AST declaration extraction timeout in seconds.")
    scan.add_argument("--semantic", action="store_true", help="Run the optional Lean/Lake semantic extractor.")
    scan.add_argument("--semantic-build", action="store_true", help="Run `lake build` before semantic extraction.")
    scan.add_argument("--semantic-timeout", type=int, default=120, help="Semantic extraction timeout in seconds.")
    scan.add_argument("--proof-states", action="store_true", help="Extract tactic before/after states with Lean.")
    scan.add_argument("--proof-state-timeout", type=int, default=120, help="Proof-state extraction timeout in seconds.")
    scan.add_argument("--out", help="Write output to a file instead of stdout.")
    scan.set_defaults(func=cmd_scan)

    explain = subparsers.add_parser("explain", help="Explain one Lean declaration in context.")
    explain.add_argument("path", help="Lean project root or a .lean file")
    explain.add_argument("--symbol", required=True, help="Declaration name, short name, or qualified name.")
    explain.add_argument("--language", choices=("zh", "en"), default="zh")
    explain.add_argument("--out", help="Write output to a file instead of stdout.")
    explain.set_defaults(func=cmd_explain)

    check = subparsers.add_parser("check-paper", help="Check paper references against Lean source.")
    check.add_argument("--lean-root", required=True, help="Lean project root")
    check.add_argument("--paper", required=True, help="LaTeX or Markdown paper path")
    check.add_argument("--format", choices=("markdown", "json"), default="markdown")
    check.add_argument(
        "--ast-declarations",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use Lean AST extraction to refine declarations; defaults to on for Lake projects.",
    )
    check.add_argument("--ast-declaration-timeout", type=int, default=120, help="AST declaration extraction timeout in seconds.")
    check.add_argument("--semantic", action="store_true", help="Run the optional Lean/Lake semantic extractor.")
    check.add_argument("--semantic-build", action="store_true", help="Run `lake build` before semantic extraction.")
    check.add_argument("--semantic-timeout", type=int, default=120, help="Semantic extraction timeout in seconds.")
    check.add_argument(
        "--apply-patches",
        action="store_true",
        help="Apply safe paper patch suggestions to the paper file. Without this flag, files are not modified.",
    )
    check.add_argument("--out", help="Write output to a file instead of stdout.")
    check.set_defaults(func=cmd_check_paper)

    benchmark = subparsers.add_parser("benchmark", help="Export AI4Math benchmark items.")
    benchmark.add_argument("path", help="Lean project root or a .lean file")
    benchmark.add_argument("--format", choices=("jsonl", "json"), default="jsonl")
    benchmark.add_argument("--level", choices=("theorem", "tactic", "all"), default="theorem")
    benchmark.add_argument("--out", required=True, help="Output .jsonl or .json path")
    benchmark.add_argument(
        "--ast-declarations",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Use Lean AST extraction to refine declarations; defaults to on for Lake projects.",
    )
    benchmark.add_argument("--ast-declaration-timeout", type=int, default=120, help="AST declaration extraction timeout in seconds.")
    benchmark.add_argument("--semantic", action="store_true", help="Run the optional Lean/Lake semantic extractor.")
    benchmark.add_argument("--semantic-build", action="store_true", help="Run `lake build` before semantic extraction.")
    benchmark.add_argument("--semantic-timeout", type=int, default=120, help="Semantic extraction timeout in seconds.")
    benchmark.add_argument("--proof-states", action="store_true", help="Extract tactic before/after states with Lean.")
    benchmark.add_argument("--proof-state-timeout", type=int, default=120, help="Proof-state extraction timeout in seconds.")
    benchmark.set_defaults(func=cmd_benchmark)

    audit = subparsers.add_parser("audit", help="Audit reproducibility metadata and optionally run lake build.")
    audit.add_argument("path", help="Lean project root")
    audit.add_argument("--run-build", action="store_true", help="Run `lake build`.")
    audit.add_argument("--timeout", type=int, default=120, help="Build timeout in seconds.")
    audit.add_argument("--format", choices=("markdown", "json"), default="markdown")
    audit.add_argument("--out", help="Write output to a file instead of stdout.")
    audit.set_defaults(func=cmd_audit)

    env = subparsers.add_parser("env", help="Inspect local Lean, Lake, elan, and Git environment.")
    env.add_argument("path", nargs="?", default=".", help="Lean project root or working directory.")
    env.add_argument("--timeout", type=int, default=10, help="Environment command timeout in seconds.")
    env.add_argument("--format", choices=("markdown", "json"), default="markdown")
    env.add_argument("--out", help="Write output to a file instead of stdout.")
    env.set_defaults(func=cmd_env)

    return parser


def cmd_scan(args: argparse.Namespace) -> int:
    analysis = scan_project(
        args.path,
        ast_declarations=args.ast_declarations,
        ast_declaration_timeout=args.ast_declaration_timeout,
        semantic=args.semantic or args.semantic_build,
        semantic_build=args.semantic_build,
        semantic_timeout=args.semantic_timeout,
        proof_states=args.proof_states,
        proof_state_timeout=args.proof_state_timeout,
    )
    if args.format == "json":
        output = project_to_json(analysis, include_source=args.include_source)
    else:
        output = project_to_markdown(analysis)
    _emit(output, args.out)
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    analysis = scan_project(args.path)
    output = explain_symbol(analysis, args.symbol, language=args.language)
    _emit(output + "\n", args.out)
    return 0


def cmd_check_paper(args: argparse.Namespace) -> int:
    analysis = scan_project(
        args.lean_root,
        ast_declarations=args.ast_declarations,
        ast_declaration_timeout=args.ast_declaration_timeout,
        semantic=args.semantic or args.semantic_build,
        semantic_build=args.semantic_build,
        semantic_timeout=args.semantic_timeout,
    )
    report = check_paper(analysis, args.paper, apply_patches=args.apply_patches)
    output = report_to_json(report) if args.format == "json" else report_to_markdown(report)
    _emit(output, args.out)
    return 0 if report.ok() else 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    analysis = scan_project(
        args.path,
        ast_declarations=args.ast_declarations,
        ast_declaration_timeout=args.ast_declaration_timeout,
        semantic=args.semantic or args.semantic_build,
        semantic_build=args.semantic_build,
        semantic_timeout=args.semantic_timeout,
        proof_states=args.proof_states,
        proof_state_timeout=args.proof_state_timeout,
    )
    write_benchmark(analysis, args.out, output_format=args.format, level=args.level)
    count = len(build_benchmark_items(analysis, level=args.level))
    print(f"Wrote {count} benchmark items to {args.out}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    analysis = scan_project(args.path)
    report = audit_project(analysis, run_build=args.run_build, timeout=args.timeout)
    output = audit_to_json(report) if args.format == "json" else audit_to_markdown(report)
    _emit(output, args.out)
    return 0 if report.ok() else 1


def cmd_env(args: argparse.Namespace) -> int:
    report = inspect_environment(args.path, timeout=args.timeout)
    output = environment_to_json(report) if args.format == "json" else environment_to_markdown(report)
    _emit(output, args.out)
    return 0


def _emit(output: str, output_path: str | None) -> None:
    if output_path:
        Path(output_path).write_text(output, encoding="utf-8")
        return
    print(output, end="" if output.endswith("\n") else "\n")


if __name__ == "__main__":
    raise SystemExit(main())
