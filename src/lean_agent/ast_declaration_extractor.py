from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from lean_agent.formal_type import decompose_formal_type
from lean_agent.lean_parser import extract_proof_steps, extract_statement
from lean_agent.models import (
    DeclarationExtractionRecord,
    DeclarationExtractionReport,
    LeanDeclaration,
    LeanFileAnalysis,
)


MARKER = "PROOFLENS_AST_DECL\t"

LEAN_AST_DECLARATION_EXTRACTOR = r"""
import Lean

open Lean
open Lean.Elab
open Lean.Language

def syntaxArgs (stx : Syntax) : Array Syntax :=
  match stx with
  | .node _ _ args => args
  | _ => #[]

partial def firstIdentName? (stx : Syntax) : Option Name :=
  match stx with
  | .ident _ _ value _ => some value
  | .node _ _ args => args.findSome? firstIdentName?
  | _ => none

partial def firstAtom? (stx : Syntax) : Option String :=
  match stx with
  | .atom _ value => some value
  | .node _ _ args => args.findSome? firstAtom?
  | _ => none

def declarationKeywords : List String :=
  ["theorem", "lemma", "def", "abbrev", "structure", "class", "inductive",
   "instance", "axiom", "constant", "opaque", "example"]

def commandKind (stx : Syntax) : String :=
  stx.getKind.toString

def isDeclaration (stx : Syntax) : Bool :=
  commandKind stx == "Lean.Parser.Command.declaration"

def declarationBody? (stx : Syntax) : Option Syntax :=
  (syntaxArgs stx)[1]?

def sourceSlice (source : String) (stx : Syntax) : String :=
  let start := stx.getPos?.getD 0
  let stop := stx.getTailPos?.getD start
  String.Pos.Raw.extract source start stop

def namespaces (scopes : List (Option String)) : List String :=
  scopes.reverse.filterMap id

def qualifyName (scopes : List (Option String)) (name : Name) : String :=
  let raw := name.toString
  if "_root_.".isPrefixOf raw then
    (raw.drop 7).toString
  else
    let ns := String.intercalate "." (namespaces scopes)
    if ns.isEmpty || (ns ++ ".").isPrefixOf raw then
      raw
    else
      ns ++ "." ++ raw

def scopeMatches (actual wanted : String) : Bool :=
  actual == wanted

def popScope (explicit? : Option String) (scopes : List (Option String)) : List (Option String) :=
  match explicit? with
  | none => scopes.drop 1
  | some wanted =>
      let rec go : List (Option String) -> List (Option String)
        | [] => []
        | none :: rest => go rest
        | some actual :: rest =>
            if scopeMatches actual wanted then rest else go rest
      go scopes

def recordJson? (source file : String) (scopes : List (Option String)) (stx : Syntax) : Option Json := do
  let body <- declarationBody? stx
  let keyword <- firstAtom? body
  if !declarationKeywords.contains keyword then
    none
  else
    let shortName <- firstIdentName? body
    let start := body.getPos?.getD 0
    let stop := body.getTailPos?.getD start
    let startPos := (FileMap.ofString source).toPosition start
    let stopPos := (FileMap.ofString source).toPosition stop
    some <| Json.mkObj [
      ("file", toJson file),
      ("kind", toJson keyword),
      ("name", toJson (qualifyName scopes shortName)),
      ("short_name", toJson shortName.toString),
      ("line", toJson startPos.line),
      ("column", toJson (startPos.column + 1)),
      ("end_line", toJson stopPos.line),
      ("end_column", toJson (stopPos.column + 1)),
      ("source", toJson (sourceSlice source body))
    ]

partial def collectDeclarations (source file : String) (scopes : List (Option String)) (stx : Syntax) :
    Array Json :=
  if isDeclaration stx then
    match recordJson? source file scopes stx with
    | some record => #[record]
    | none => #[]
  else
    (syntaxArgs stx).foldl (init := #[]) fun records child =>
      records ++ collectDeclarations source file scopes child

partial def walkCommands
    (source file : String)
    (scopes : List (Option String))
    (snap : Lean.Language.Lean.CommandParsedSnapshot) : IO (Array Json) := do
  let stx := snap.stx
  let mut records := #[]
  let mut nextScopes := scopes
  if !Parser.isTerminalCommand stx then
    match commandKind stx with
    | "Lean.Parser.Command.namespace" =>
        if let some name := firstIdentName? stx then
          nextScopes := some name.toString :: scopes
    | "Lean.Parser.Command.section" =>
        nextScopes := none :: scopes
    | "Lean.Parser.Command.end" =>
        nextScopes := popScope (firstIdentName? stx |>.map Name.toString) scopes
    | _ =>
        records := collectDeclarations source file scopes stx
  match snap.nextCmdSnap? with
  | none => pure records
  | some next => do
      let tail <- walkCommands source file nextScopes next.get
      pure (records ++ tail)

unsafe def runFile (root file : String) : IO UInt32 := do
  Lean.enableInitializersExecution
  let input <- IO.FS.readFile file
  let inputCtx := Parser.mkInputContext input file
  let opts := Lean.internal.cmdlineSnapshots.set {} false
  let opts := Elab.async.set opts false
  let mainModuleName <- try
    moduleNameOfFileName (System.FilePath.mk file) (some (System.FilePath.mk root))
  catch _ =>
    pure Name.anonymous
  let ctx := { inputCtx with }
  let setup stx := do
    return .ok {
      imports := stx.imports
      isModule := stx.isModule
      mainModuleName := mainModuleName
      opts := opts
    }
  let snap <- Lean.Language.Lean.process setup none ctx
  match snap.result? with
  | none => pure 1
  | some header =>
      let processed := header.processedSnap.get
      match processed.result? with
      | none => pure 1
      | some result => do
          let records <- walkCommands input file [] result.firstCmdSnap.get
          for record in records do
            IO.println s!"PROOFLENS_AST_DECL\t{record.compress}"
          pure 0

unsafe def main (args : List String) : IO UInt32 := do
  match args with
  | [root, file] => runFile root file
  | _ =>
      IO.eprintln "usage: prooflens AST declaration extractor <root> <file>"
      return 2
"""


def extract_ast_declarations(
    root: str | Path,
    files: list[LeanFileAnalysis],
    timeout: int = 120,
) -> DeclarationExtractionReport:
    root_path = Path(root).resolve()
    lean_files = sorted(file.path for file in files)
    command = _command_template(root_path)
    if not lean_files:
        return DeclarationExtractionReport(
            status="skipped",
            command=command,
            files=[],
            message="No Lean files were provided for AST declaration extraction.",
        )
    executable = command[0]
    if shutil.which(executable) is None:
        return DeclarationExtractionReport(
            status=f"missing_{executable}",
            command=command,
            files=lean_files,
            message=f"`{executable}` was not found on PATH.",
        )

    records: list[DeclarationExtractionRecord] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="prooflens-ast-decls-") as temp_dir:
        extractor_path = Path(temp_dir) / "ProofLensAstDeclarationExtractor.lean"
        extractor_path.write_text(LEAN_AST_DECLARATION_EXTRACTOR, encoding="utf-8")
        for file in lean_files:
            file_command = _command_for_file(root_path, file, extractor_path)
            try:
                result = _run(file_command, root_path, timeout)
            except subprocess.TimeoutExpired as exc:
                return DeclarationExtractionReport(
                    status="timeout",
                    command=file_command,
                    files=lean_files,
                    records=records,
                    exit_code=124,
                    stdout=_safe_text(exc.stdout),
                    stderr=_safe_text(exc.stderr),
                    message=f"AST declaration extraction timed out after {timeout} seconds.",
                )
            stdout_parts.append(result.stdout)
            stderr_parts.append(result.stderr)
            if result.returncode != 0:
                return DeclarationExtractionReport(
                    status="failed",
                    command=file_command,
                    files=lean_files,
                    records=records,
                    exit_code=result.returncode,
                    stdout="\n".join(stdout_parts),
                    stderr="\n".join(stderr_parts),
                    message=_first_output_line(result.stderr, result.stdout),
                )
            records.extend(_parse_records(result.stdout + "\n" + result.stderr))

    return DeclarationExtractionReport(
        status="ok",
        command=command,
        files=lean_files,
        records=records,
        exit_code=0,
        stdout="\n".join(stdout_parts),
        stderr="\n".join(stderr_parts),
    )


def apply_ast_declarations(
    files: list[LeanFileAnalysis],
    report: DeclarationExtractionReport | None,
) -> None:
    if report is None or report.status != "ok" or not report.records:
        return
    records_by_file: dict[str, list[DeclarationExtractionRecord]] = {}
    for record in report.records:
        records_by_file.setdefault(record.file, []).append(record)

    for file_analysis in files:
        records = records_by_file.get(file_analysis.path)
        if not records:
            continue
        static_by_name = {declaration.name: declaration for declaration in file_analysis.declarations}
        static_by_line = {
            (declaration.kind, declaration.line): declaration
            for declaration in file_analysis.declarations
        }
        merged: list[LeanDeclaration] = []
        used_static: set[int] = set()
        for record in records:
            declaration = static_by_name.get(record.name) or static_by_line.get((record.kind, record.line))
            if declaration is None:
                declaration = _declaration_from_record(record)
            else:
                used_static.add(id(declaration))
                _apply_record_to_declaration(declaration, record)
            merged.append(declaration)
        for declaration in file_analysis.declarations:
            if id(declaration) not in used_static:
                merged.append(declaration)
        file_analysis.declarations = sorted(merged, key=lambda item: (item.line, item.column, item.name))


def _declaration_from_record(record: DeclarationExtractionRecord) -> LeanDeclaration:
    declaration = LeanDeclaration(
        kind=record.kind,
        name=record.name,
        short_name=record.short_name,
        file=record.file,
        line=record.line,
        column=record.column,
        end_line=record.end_line,
        end_column=record.end_column,
        statement="",
        source=record.source.rstrip(),
        namespace=_namespace_for(record.name, record.short_name),
    )
    _refresh_derived_fields(declaration)
    return declaration


def _apply_record_to_declaration(
    declaration: LeanDeclaration,
    record: DeclarationExtractionRecord,
) -> None:
    declaration.kind = record.kind
    declaration.name = record.name
    declaration.short_name = record.short_name
    declaration.file = record.file
    declaration.line = record.line
    declaration.column = record.column
    declaration.end_line = record.end_line
    declaration.end_column = record.end_column
    declaration.source = record.source.rstrip()
    declaration.namespace = declaration.namespace or _namespace_for(record.name, record.short_name)
    _refresh_derived_fields(declaration)


def _refresh_derived_fields(declaration: LeanDeclaration) -> None:
    declaration.statement = extract_statement(declaration.source)
    formal_type = decompose_formal_type(declaration.statement)
    declaration.formal_parameters = formal_type.parameters
    declaration.formal_conclusion = formal_type.conclusion
    declaration.proof_steps = extract_proof_steps(declaration.source, declaration.line, declaration.kind)


def _namespace_for(name: str, short_name: str) -> str | None:
    suffix = "." + short_name
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return None


def _parse_records(output: str) -> list[DeclarationExtractionRecord]:
    records: list[DeclarationExtractionRecord] = []
    for line in output.splitlines():
        if not line.startswith(MARKER):
            continue
        try:
            payload = json.loads(line[len(MARKER):])
        except json.JSONDecodeError:
            continue
        record = _record_from_payload(payload)
        if record is not None:
            records.append(record)
    return records


def _record_from_payload(payload: object) -> DeclarationExtractionRecord | None:
    if not isinstance(payload, dict):
        return None
    try:
        return DeclarationExtractionRecord(
            file=_str(payload["file"]),
            kind=_str(payload["kind"]),
            name=_str(payload["name"]),
            short_name=_str(payload["short_name"]),
            line=_int(payload["line"]),
            column=_int(payload["column"]),
            end_line=_int(payload["end_line"]),
            end_column=_int(payload["end_column"]),
            source=_str(payload["source"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _command_template(root: Path) -> list[str]:
    if (root / "lakefile.lean").exists() or (root / "lakefile.toml").exists():
        return ["lake", "env", "lean", "--run", "<prooflens-extractor>", "<root>", "<file>"]
    return ["lean", "--run", "<prooflens-extractor>", "<root>", "<file>"]


def _command_for_file(root: Path, file: str, extractor: Path) -> list[str]:
    return [
        str(extractor) if part == "<prooflens-extractor>"
        else str(root) if part == "<root>"
        else file if part == "<file>"
        else part
        for part in _command_template(root)
    ]


def _run(command: list[str], root: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _safe_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _first_output_line(primary: str, fallback: str = "") -> str | None:
    text = primary.strip() or fallback.strip()
    if not text:
        return None
    return text.splitlines()[0]


def _int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("boolean is not an integer field")
    return int(value)


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected string field")
    return value
