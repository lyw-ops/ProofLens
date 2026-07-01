from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from lean_agent.models import (
    LeanDeclaration,
    LeanFileAnalysis,
    ProofStep,
    ProofStateExtractionReport,
    ProofStateRecord,
)


BULLET = "\u2022"
LEFT_ANGLE = "\u27e8"
RIGHT_ANGLE = "\u27e9"
DAGGER = "\u2020"
TACTIC_HEADER_RE = re.compile(
    rf"^(?P<indent>\s*){re.escape(BULLET)} \[Tactic\] @ "
    rf"{re.escape(LEFT_ANGLE)}(?P<line>\d+),\s*(?P<column>\d+){re.escape(RIGHT_ANGLE)}"
    rf"{re.escape(DAGGER)}?-"
    rf"{re.escape(LEFT_ANGLE)}(?P<end_line>\d+),\s*(?P<end_column>\d+){re.escape(RIGHT_ANGLE)}"
)
STRUCTURED_MARKER = "PROOFLENS_PROOF_STATE\t"
STRUCTURED_MODE = "lean_structured_json"
TRACE_MODE = "lean_json_trace"

LEAN_PROOF_STATE_EXTRACTOR = r"""
import Lean

open Lean
open Lean.Elab
open Lean.Language

def ppGoalsInCtx (ci : ContextInfo) (mctx : MetavarContext) (goals : List MVarId) : IO String := do
  let ctx : ContextInfo := { ci with mctx := mctx }
  let lines <- ctx.runMetaM {} do
    goals.mapM fun goal => do
      return toString (<- Lean.Meta.ppGoal goal)
  return if lines.isEmpty then "no goals" else String.intercalate "\n---\n" lines

def tacticSyntax (source : String) (ti : TacticInfo) : String :=
  let start := ti.stx.getPos?.getD 0
  let stop := ti.stx.getTailPos?.getD start
  (String.Pos.Raw.extract source start stop).trimAscii.toString

def recordJson (source : String) (ci : ContextInfo) (ti : TacticInfo) : IO Json := do
  let before <- ppGoalsInCtx ci ti.mctxBefore ti.goalsBefore
  let after <- ppGoalsInCtx ci ti.mctxAfter ti.goalsAfter
  let start := ti.stx.getPos?.getD 0
  let stop := ti.stx.getTailPos?.getD start
  let startPos := ci.fileMap.toPosition start
  let stopPos := ci.fileMap.toPosition stop
  return Json.mkObj [
    ("line", toJson startPos.line),
    ("column", toJson (startPos.column + 1)),
    ("end_line", toJson stopPos.line),
    ("end_column", toJson (stopPos.column + 1)),
    ("tactic_syntax", toJson (tacticSyntax source ti)),
    ("before_state", toJson before),
    ("after_state", toJson after)
  ]

partial def collectTactics
    (source : String) (tree : InfoTree) (ctx? : Option ContextInfo := none) :
    IO (Array Json) := do
  match tree with
  | .hole _ => pure #[]
  | .context info child => collectTactics source child (info.mergeIntoOuter? ctx?)
  | .node info children =>
      let mut out := #[]
      match ctx?, info with
      | some ci, .ofTacticInfo tacticInfo =>
          out := out.push (<- recordJson source ci tacticInfo)
      | _, _ => pure ()
      let ctx? := info.updateContext? ctx?
      for child in children do
        out := out ++ (<- collectTactics source child ctx?)
      return out

unsafe def runFile (root file : String) : IO UInt32 := do
  Lean.enableInitializersExecution
  let input <- IO.FS.readFile file
  let inputCtx := Parser.mkInputContext input file
  let opts := Lean.internal.cmdlineSnapshots.set {} true
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
  let tree := toSnapshotTree snap
  let snapshots := tree.getAll
  let mut hasErrors := false
  for snap in snapshots do
    for msg in snap.diagnostics.msgLog.toList do
      if msg.severity == MessageSeverity.error then
        hasErrors := true
      IO.eprintln (<- msg.toString)
  let mut records := #[]
  for snap in snapshots do
    if let some infoTree := snap.infoTree? then
      records := records ++ (<- collectTactics input infoTree none)
  for record in records do
    IO.println s!"PROOFLENS_PROOF_STATE\t{record.compress}"
  return if hasErrors then 1 else 0

unsafe def main (args : List String) : IO UInt32 := do
  match args with
  | [root, file] => runFile root file
  | _ =>
      IO.eprintln "usage: prooflens proof-state extractor <root> <file>"
      return 2
"""


def extract_proof_states(
    root: str | Path,
    files: list[LeanFileAnalysis],
    declarations: list[LeanDeclaration],
    timeout: int = 120,
) -> ProofStateExtractionReport:
    root_path = Path(root).resolve()
    tactic_files = sorted(
        {
            declaration.file
            for declaration in declarations
            if _can_have_tactic_state(declaration)
        }
    )
    command = _structured_command_template(root_path)
    if not tactic_files:
        return ProofStateExtractionReport(
            status="skipped",
            command=command,
            files=[],
            message="No tactic proof steps were found in the scanned declarations.",
        )
    executable = command[0]
    if shutil.which(executable) is None:
        return ProofStateExtractionReport(
            status=f"missing_{executable}",
            command=command,
            files=tactic_files,
            message=f"`{executable}` was not found on PATH.",
        )

    records: list[ProofStateRecord] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    modes: set[str] = set()
    had_partial_records = False
    partial_exit_code: int | None = None
    partial_message: str | None = None
    with tempfile.TemporaryDirectory(prefix="prooflens-proof-state-") as temp_dir:
        extractor_path = Path(temp_dir) / "ProofLensProofStateExtractor.lean"
        extractor_path.write_text(LEAN_PROOF_STATE_EXTRACTOR, encoding="utf-8")
        for file in tactic_files:
            structured_command = _structured_command_for_file(root_path, file, extractor_path)
            try:
                structured_result = _run(structured_command, root_path, timeout)
            except subprocess.TimeoutExpired as exc:
                return ProofStateExtractionReport(
                    status="timeout",
                    command=structured_command,
                    files=tactic_files,
                    records=records,
                    exit_code=124,
                    stdout=_safe_text(exc.stdout),
                    stderr=_safe_text(exc.stderr),
                    message=f"Proof-state extraction timed out after {timeout} seconds.",
                )
            stdout_parts.append(structured_result.stdout)
            stderr_parts.append(structured_result.stderr)
            structured_records, saw_structured_marker = _parse_structured_output(
                structured_result.stdout + "\n" + structured_result.stderr,
                file,
            )
            if structured_records:
                records.extend(structured_records)
                modes.add(STRUCTURED_MODE)
            if structured_result.returncode == 0 and saw_structured_marker:
                continue
            if structured_result.returncode != 0 and structured_records:
                had_partial_records = True
                partial_exit_code = structured_result.returncode
                partial_message = _partial_message(
                    structured_result.stderr,
                    structured_result.stdout,
                )

            trace_command = _trace_command_for_file(root_path, file)
            try:
                trace_result = _run(trace_command, root_path, timeout)
            except subprocess.TimeoutExpired as exc:
                return ProofStateExtractionReport(
                    status="timeout",
                    command=trace_command,
                    files=tactic_files,
                    records=records,
                    exit_code=124,
                    stdout=_safe_text(exc.stdout),
                    stderr=_safe_text(exc.stderr),
                    message=f"Proof-state extraction timed out after {timeout} seconds.",
                )
            stdout_parts.append(trace_result.stdout)
            stderr_parts.append(trace_result.stderr)
            trace_records = _parse_lean_output(trace_result.stdout + "\n" + trace_result.stderr, file)
            if trace_result.returncode != 0:
                if trace_records:
                    records.extend(trace_records)
                    modes.add(TRACE_MODE)
                    had_partial_records = True
                deduped_records = _dedupe_records(records)
                if deduped_records:
                    return ProofStateExtractionReport(
                        status="partial",
                        command=trace_command,
                        files=tactic_files,
                        records=deduped_records,
                        extraction_mode=_mode_summary(modes),
                        exit_code=trace_result.returncode,
                        stdout="\n".join(stdout_parts),
                        stderr="\n".join(stderr_parts),
                        message=_partial_message(trace_result.stderr, trace_result.stdout),
                    )
                return ProofStateExtractionReport(
                    status="failed",
                    command=trace_command,
                    files=tactic_files,
                    records=records,
                    extraction_mode=_mode_summary(modes),
                    exit_code=trace_result.returncode,
                    stdout="\n".join(stdout_parts),
                    stderr="\n".join(stderr_parts),
                    message=_first_output_line(trace_result.stderr, trace_result.stdout),
                )
            if trace_records:
                records.extend(trace_records)
                modes.add(TRACE_MODE)

    mode = _mode_summary(modes)
    status = "partial" if had_partial_records else "ok"
    return ProofStateExtractionReport(
        status=status,
        command=_command_template_for_mode(root_path, mode),
        files=tactic_files,
        records=_dedupe_records(records),
        extraction_mode=mode,
        exit_code=partial_exit_code if had_partial_records else 0,
        stdout="\n".join(stdout_parts),
        stderr="\n".join(stderr_parts),
        message=partial_message if had_partial_records else None,
    )


def attach_proof_states(
    declarations: list[LeanDeclaration],
    report: ProofStateExtractionReport,
) -> None:
    if report.extraction_mode in {STRUCTURED_MODE, "mixed"}:
        _merge_structured_proof_steps(declarations, report.records)
    records_by_location: dict[tuple[str, int, int], ProofStateRecord] = {}
    for record in report.records:
        records_by_location[(record.file, record.line, record.column)] = record
    for declaration in declarations:
        for step in declaration.proof_steps:
            record = records_by_location.get((declaration.file, step.line, step.column))
            if record is None:
                continue
            step.before_state = record.before_state
            step.after_state = record.after_state
            step.end_line = record.end_line


def _can_have_tactic_state(declaration: LeanDeclaration) -> bool:
    return declaration.kind in {"theorem", "lemma", "example"} or bool(declaration.proof_steps)


def _merge_structured_proof_steps(
    declarations: list[LeanDeclaration],
    records: list[ProofStateRecord],
) -> None:
    declarations_by_file: dict[str, list[LeanDeclaration]] = {}
    for declaration in declarations:
        if _can_have_tactic_state(declaration):
            declarations_by_file.setdefault(declaration.file, []).append(declaration)
    for file_declarations in declarations_by_file.values():
        file_declarations.sort(key=lambda item: (item.line, item.column, item.end_line, item.end_column))

    records_by_declaration: dict[int, list[ProofStateRecord]] = {}
    for record in records:
        declaration = _declaration_for_record(declarations_by_file.get(record.file, []), record)
        if declaration is None:
            continue
        records_by_declaration.setdefault(id(declaration), []).append(record)

    for declaration in declarations:
        declaration_records = records_by_declaration.get(id(declaration))
        if not declaration_records:
            continue
        declaration_records = sorted(
            _dedupe_records(declaration_records),
            key=lambda item: (item.line, item.column, item.end_line, item.end_column, item.tactic_syntax),
        )
        existing_by_location = {
            (step.line, step.column): step
            for step in declaration.proof_steps
        }
        merged: list[ProofStep] = []
        used_locations: set[tuple[int, int]] = set()
        for record in declaration_records:
            location = (record.line, record.column)
            step = existing_by_location.get(location)
            if step is None:
                step = _proof_step_from_record(record, len(merged) + 1)
            else:
                step.text = record.tactic_syntax
                step.tactic = _tactic_head(record.tactic_syntax)
                step.end_line = record.end_line
            step.before_state = record.before_state
            step.after_state = record.after_state
            used_locations.add(location)
            merged.append(step)
        for step in declaration.proof_steps:
            if (step.line, step.column) not in used_locations:
                merged.append(step)
        merged.sort(key=lambda item: (item.line, item.column, item.index))
        for index, step in enumerate(merged, start=1):
            step.index = index
        declaration.proof_steps = merged


def _declaration_for_record(
    declarations: list[LeanDeclaration],
    record: ProofStateRecord,
) -> LeanDeclaration | None:
    candidates = [
        declaration
        for declaration in declarations
        if _record_inside_declaration(record, declaration)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda declaration: (
            declaration.end_line - declaration.line,
            declaration.end_column - declaration.column,
        ),
    )


def _record_inside_declaration(record: ProofStateRecord, declaration: LeanDeclaration) -> bool:
    start = (declaration.line, declaration.column)
    end = (declaration.end_line, declaration.end_column)
    record_start = (record.line, record.column)
    return start <= record_start <= end


def _proof_step_from_record(record: ProofStateRecord, index: int) -> ProofStep:
    return ProofStep(
        index=index,
        tactic=_tactic_head(record.tactic_syntax),
        text=record.tactic_syntax,
        line=record.line,
        column=record.column,
        end_line=record.end_line,
        before_state=record.before_state,
        after_state=record.after_state,
    )


def _tactic_head(tactic_syntax: str) -> str:
    stripped = tactic_syntax.strip()
    head = _read_identifier(stripped)
    return head or stripped.split(maxsplit=1)[0]


def _read_identifier(text: str) -> str | None:
    if not text or not (text[0].isalpha() or text[0] == "_"):
        return None
    index = 1
    while index < len(text) and (text[index].isalnum() or text[index] in {"_", "'", "."}):
        index += 1
    return text[:index]


def _structured_command_template(root: Path) -> list[str]:
    if (root / "lakefile.lean").exists() or (root / "lakefile.toml").exists():
        return ["lake", "env", "lean", "--run", "<prooflens-extractor>", "<root>", "<file>"]
    return ["lean", "--run", "<prooflens-extractor>", "<root>", "<file>"]


def _trace_command_template(root: Path) -> list[str]:
    if (root / "lakefile.lean").exists() or (root / "lakefile.toml").exists():
        return ["lake", "env", "lean", "--json", "-Dtrace.Elab.info=true", "<file>"]
    return ["lean", "--json", "-Dtrace.Elab.info=true", "<file>"]


def _structured_command_for_file(root: Path, file: str, extractor: Path) -> list[str]:
    return [
        str(extractor) if part == "<prooflens-extractor>"
        else str(root) if part == "<root>"
        else file if part == "<file>"
        else part
        for part in _structured_command_template(root)
    ]


def _trace_command_for_file(root: Path, file: str) -> list[str]:
    return [file if part == "<file>" else part for part in _trace_command_template(root)]


def _command_template_for_mode(root: Path, mode: str | None) -> list[str]:
    if mode == TRACE_MODE:
        return _trace_command_template(root)
    return _structured_command_template(root)


def _mode_summary(modes: set[str]) -> str | None:
    if not modes:
        return None
    if len(modes) == 1:
        return next(iter(modes))
    return "mixed"


def _parse_lean_output(output: str, file: str) -> list[ProofStateRecord]:
    structured_records, saw_structured_marker = _parse_structured_output(output, file)
    if saw_structured_marker:
        return structured_records
    trace_payloads = _trace_payloads_from_json_messages(output)
    if trace_payloads:
        return _parse_trace_output("\n".join(trace_payloads), file)
    return _parse_trace_output(output, file)


def _parse_structured_output(output: str, file: str) -> tuple[list[ProofStateRecord], bool]:
    records: list[ProofStateRecord] = []
    saw_marker = False
    for line in output.splitlines():
        if not line.startswith(STRUCTURED_MARKER):
            continue
        saw_marker = True
        raw_payload = line[len(STRUCTURED_MARKER):]
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue
        record = _record_from_structured_payload(payload, file)
        if record is not None:
            records.append(record)
    return _dedupe_records(records), saw_marker


def _record_from_structured_payload(
    payload: object,
    file: str,
) -> ProofStateRecord | None:
    if not isinstance(payload, dict):
        return None
    tactic_syntax = _coerce_str(payload.get("tactic_syntax"))
    if not _is_actionable_tactic_syntax(tactic_syntax):
        return None
    try:
        line = _coerce_int(payload["line"])
        column = _coerce_int(payload["column"])
        end_line = _coerce_int(payload["end_line"])
        end_column = _coerce_int(payload["end_column"])
    except (KeyError, TypeError, ValueError):
        return None
    return ProofStateRecord(
        file=file,
        line=line,
        column=column,
        end_line=end_line,
        end_column=end_column,
        tactic_syntax=tactic_syntax,
        before_state=_coerce_str(payload.get("before_state")),
        after_state=_coerce_str(payload.get("after_state")),
    )


def _is_actionable_tactic_syntax(tactic_syntax: str) -> bool:
    stripped = tactic_syntax.strip()
    if not stripped:
        return False
    return stripped != "by" and not stripped.startswith(("by ", "by\n"))


def _dedupe_records(records: list[ProofStateRecord]) -> list[ProofStateRecord]:
    deduped: list[ProofStateRecord] = []
    seen: set[tuple[str, int, int, int, int, str, str, str]] = set()
    for record in records:
        key = (
            record.file,
            record.line,
            record.column,
            record.end_line,
            record.end_column,
            record.tactic_syntax,
            record.before_state,
            record.after_state,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("boolean is not a proof-state integer field")
    return int(value)


def _coerce_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _trace_payloads_from_json_messages(output: str) -> list[str]:
    payloads: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        data = message.get("data")
        if (
            message.get("kind") == "trace"
            and isinstance(data, str)
            and "[Elab.info]" in data
        ):
            payloads.append(data)
    return payloads


def _parse_trace_output(output: str, file: str) -> list[ProofStateRecord]:
    lines = output.splitlines()
    records: list[ProofStateRecord] = []
    index = 0
    while index < len(lines):
        match = TACTIC_HEADER_RE.match(lines[index])
        if not match:
            index += 1
            continue
        parsed = _parse_tactic_block(lines, index, match, file)
        if parsed is None:
            index += 1
            continue
        record, next_index = parsed
        records.append(record)
        index = max(index + 1, next_index)
    return records


def _parse_tactic_block(
    lines: list[str],
    header_index: int,
    match: re.Match[str],
    file: str,
) -> tuple[ProofStateRecord, int] | None:
    body_indent = len(match.group("indent")) + 2
    before_index = _find_state_marker(lines, header_index + 1, body_indent, "before")
    if before_index is None:
        return None
    tactic_syntax = _collect_syntax(lines, header_index + 1, before_index, body_indent)
    before_state, after_index = _collect_state(lines, before_index, body_indent, "before")
    if after_index >= len(lines):
        return None
    after_state, next_index = _collect_state(lines, after_index, body_indent, "after")
    return (
        ProofStateRecord(
            file=file,
            line=int(match.group("line")),
            column=int(match.group("column")) + 1,
            end_line=int(match.group("end_line")),
            end_column=int(match.group("end_column")) + 1,
            tactic_syntax=tactic_syntax,
            before_state=before_state,
            after_state=after_state,
        ),
        next_index,
    )


def _find_state_marker(
    lines: list[str],
    start: int,
    body_indent: int,
    marker: str,
) -> int | None:
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if _starts_new_tree(line, body_indent):
            return None
        if stripped == marker or stripped.startswith(marker + " "):
            return index
        index += 1
    return None


def _collect_syntax(lines: list[str], start: int, end: int, body_indent: int) -> str:
    syntax_lines = [
        _strip_body_indent(line, body_indent).rstrip()
        for line in lines[start:end]
        if line.strip()
    ]
    return "\n".join(syntax_lines).strip()


def _collect_state(
    lines: list[str],
    start: int,
    body_indent: int,
    marker: str,
) -> tuple[str, int]:
    first_line = lines[start].strip()
    state_lines: list[str] = []
    remainder = first_line[len(marker):].strip()
    if remainder:
        state_lines.append(remainder)
    index = start + 1
    while index < len(lines):
        stripped = lines[index].strip()
        if marker == "before" and stripped.startswith("after "):
            break
        if _starts_new_tree(lines[index], body_indent) or stripped.startswith("[Elab.info]"):
            break
        state_lines.append(_strip_body_indent(lines[index], body_indent).rstrip())
        index += 1
    return "\n".join(line for line in state_lines if line).strip(), index


def _starts_new_tree(line: str, body_indent: int) -> bool:
    stripped = line.strip()
    return _indent_width(line) <= body_indent and stripped.startswith(BULLET + " [")


def _strip_body_indent(line: str, body_indent: int) -> str:
    return line[body_indent:] if len(line) >= body_indent else line.lstrip()


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


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


def _partial_message(primary: str, fallback: str = "") -> str:
    line = _first_output_line(primary, fallback)
    if line:
        return f"Lean returned non-zero during proof-state extraction; preserved partial records. First diagnostic: {line}"
    return "Lean returned non-zero during proof-state extraction; preserved partial records."
