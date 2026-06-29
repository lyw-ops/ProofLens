from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from lean_agent.formal_type import decompose_formal_type
from lean_agent.models import (
    LeanDeclaration,
    LeanFileAnalysis,
    SemanticDeclaration,
    SemanticExtractionReport,
)


MARKER = "PROOFLENS_DECL\t"


def extract_semantics(
    root: str | Path,
    files: list[LeanFileAnalysis],
    declarations: list[LeanDeclaration],
    run_build: bool = False,
    timeout: int = 120,
) -> SemanticExtractionReport:
    root_path = Path(root).resolve()
    modules = _module_names(files)
    command: list[str] = ["lake", "env", "lean", "<extractor>"]
    if not ((root_path / "lakefile.lean").exists() or (root_path / "lakefile.toml").exists()):
        return SemanticExtractionReport(
            status="skipped",
            command=command,
            modules=modules,
            message="No lakefile was found in the project root.",
        )
    if shutil.which("lake") is None:
        return SemanticExtractionReport(
            status="missing_lake",
            command=command,
            modules=modules,
            message="`lake` was not found on PATH.",
        )
    if not modules:
        return SemanticExtractionReport(
            status="skipped",
            command=command,
            modules=modules,
            message="No importable Lean modules were derived from scanned files.",
        )
    if run_build:
        build = _run(["lake", "build"], root_path, timeout)
        if build.returncode != 0:
            return SemanticExtractionReport(
                status="build_failed",
                command=["lake", "build"],
                modules=modules,
                exit_code=build.returncode,
                stdout=build.stdout,
                stderr=build.stderr,
                message="`lake build` failed before semantic extraction.",
            )

    extractor_path = _write_extractor(modules, _target_names(declarations))
    command = ["lake", "env", "lean", str(extractor_path)]
    try:
        result = _run(command, root_path, timeout)
    except subprocess.TimeoutExpired as exc:
        return SemanticExtractionReport(
            status="timeout",
            command=command,
            modules=modules,
            exit_code=124,
            stdout=_safe_text(exc.stdout),
            stderr=_safe_text(exc.stderr),
            message=f"Semantic extraction timed out after {timeout} seconds.",
        )
    finally:
        _unlink_quietly(extractor_path)

    semantic_declarations = _parse_declarations(result.stdout)
    return SemanticExtractionReport(
        status="ok" if result.returncode == 0 else "failed",
        command=command,
        modules=modules,
        declarations=semantic_declarations,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        message=None if result.returncode == 0 else _first_output_line(result.stderr, result.stdout),
    )


def attach_semantics(
    declarations: list[LeanDeclaration],
    report: SemanticExtractionReport,
) -> None:
    semantic_by_name = {
        _normalize_name(declaration.name): declaration
        for declaration in report.declarations
    }
    for declaration in declarations:
        semantic = semantic_by_name.get(_normalize_name(declaration.name))
        if semantic is None:
            continue
        declaration.canonical_name = semantic.name
        declaration.semantic_kind = semantic.kind
        declaration.semantic_type = semantic.type
        declaration.semantic_dependencies = semantic.dependencies
        semantic.file = declaration.file
        semantic.line = declaration.line
        semantic.column = declaration.column
        semantic.end_line = declaration.end_line
        semantic.end_column = declaration.end_column
        semantic.docstring = declaration.docstring
        semantic.attributes = list(declaration.attributes)
        formal_type = decompose_formal_type(semantic.type)
        declaration.formal_parameters = formal_type.parameters
        declaration.formal_conclusion = formal_type.conclusion


def _write_extractor(modules: list[str], target_names: list[str]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".lean",
        prefix="prooflens_semantic_",
        delete=False,
    )
    with handle:
        handle.write(_extractor_source(modules, target_names))
    return Path(handle.name)


def _extractor_source(modules: list[str], target_names: list[str]) -> str:
    imports = "\n".join(f"import {module}" for module in modules)
    targets = ", ".join(_lean_string(name) for name in target_names)
    return f"""import Lean
{imports}

open Lean

def prooflensTargets : List String := [{targets}]

def prooflensKind (info : ConstantInfo) : String :=
  match info with
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

partial def prooflensCollectConsts (expr : Expr) (acc : NameSet := {{}}) : NameSet :=
  match expr with
  | .const name _ => acc.insert name
  | .app fn arg => prooflensCollectConsts arg (prooflensCollectConsts fn acc)
  | .lam _ type body _ => prooflensCollectConsts body (prooflensCollectConsts type acc)
  | .forallE _ type body _ => prooflensCollectConsts body (prooflensCollectConsts type acc)
  | .letE _ type value body _ =>
      prooflensCollectConsts body (prooflensCollectConsts value (prooflensCollectConsts type acc))
  | .mdata _ body => prooflensCollectConsts body acc
  | .proj _ _ body => prooflensCollectConsts body acc
  | _ => acc

def prooflensValue? (info : ConstantInfo) : Option Expr :=
  match info with
  | .defnInfo value => some value.value
  | .thmInfo value => some value.value
  | .opaqueInfo value => some value.value
  | _ => none

def prooflensDeps (target : Name) (info : ConstantInfo) : String :=
  let depsFromType := prooflensCollectConsts info.type
  let deps :=
    match prooflensValue? info with
    | some value => prooflensCollectConsts value depsFromType
    | none => depsFromType
  let names :=
    deps.toList.map Name.toString |>.filter (fun name =>
      prooflensTargets.contains name && name != target.toString
    )
  String.intercalate "," names

#eval show CoreM Unit from do
  let env ← getEnv
  for (name, info) in env.constants.toList do
    if prooflensTargets.contains name.toString then
      IO.println s!"{MARKER}{{prooflensKind info}}\t{{name}}\t{{info.type}}\t{{prooflensDeps name info}}"
"""


def _module_names(files: list[LeanFileAnalysis]) -> list[str]:
    modules: set[str] = set()
    for file_analysis in files:
        path = Path(file_analysis.path)
        if path.suffix != ".lean":
            continue
        parts = list(path.with_suffix("").parts)
        if not parts:
            continue
        if all(_valid_module_part(part) for part in parts):
            modules.add(".".join(parts))
    return sorted(modules)


def _target_names(declarations: list[LeanDeclaration]) -> list[str]:
    return sorted(
        {
            declaration.name
            for declaration in declarations
            if declaration.name and not declaration.short_name.startswith("anonymous_")
        }
    )


def _parse_declarations(stdout: str) -> list[SemanticDeclaration]:
    declarations: list[SemanticDeclaration] = []
    for line in stdout.splitlines():
        if not line.startswith(MARKER):
            continue
        payload = line[len(MARKER):]
        parts = payload.split("\t", 3)
        if len(parts) not in {3, 4}:
            continue
        kind, name, type_text = parts[:3]
        dependencies = _parse_dependencies(parts[3]) if len(parts) == 4 else []
        declarations.append(
            SemanticDeclaration(
                name=name,
                kind=kind,
                type=type_text,
                dependencies=dependencies,
            )
        )
    return declarations


def _parse_dependencies(text: str) -> list[str]:
    return sorted({_normalize_name(item.strip()) for item in text.split(",") if item.strip()})


def _normalize_name(name: str) -> str:
    return name.removeprefix("_root_.")


def _run(command: list[str], root: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _valid_module_part(part: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", part) is not None


def _lean_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _first_output_line(primary: str, fallback: str = "") -> str | None:
    text = primary.strip() or fallback.strip()
    if not text:
        return None
    return text.splitlines()[0]


def _safe_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
