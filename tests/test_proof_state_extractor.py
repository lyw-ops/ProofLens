from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from lean_agent.project import scan_project


class ProofStateExtractorTests(unittest.TestCase):
    def test_scan_can_attach_tactic_before_and_after_states(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_project(root)
            bin_dir = root / "bin"
            _write_fake_lake(bin_dir, structured=True)
            with _path_prepend(bin_dir):
                analysis = scan_project(root, proof_states=True, proof_state_timeout=5)

        self.assertIsNotNone(analysis.proof_states)
        self.assertEqual(analysis.proof_states.status, "ok")
        self.assertEqual(analysis.proof_states.extraction_mode, "lean_structured_json")
        self.assertIn("--run", analysis.proof_states.command)
        declaration = analysis.declaration_map["Demo.final"]
        self.assertEqual(len(declaration.proof_steps), 1)
        step = declaration.proof_steps[0]
        self.assertEqual(step.before_state, "n : Nat\n|- n = n")
        self.assertEqual(step.after_state, "no goals")

    def test_scan_falls_back_to_trace_when_structured_output_is_unavailable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_project(root)
            bin_dir = root / "bin"
            _write_fake_lake(bin_dir, structured=False)
            with _path_prepend(bin_dir):
                analysis = scan_project(root, proof_states=True, proof_state_timeout=5)

        self.assertIsNotNone(analysis.proof_states)
        self.assertEqual(analysis.proof_states.status, "ok")
        self.assertEqual(analysis.proof_states.extraction_mode, "lean_json_trace")
        self.assertIn("--json", analysis.proof_states.command)
        step = analysis.declaration_map["Demo.final"].proof_steps[0]
        self.assertEqual(step.before_state, "n : Nat\n|- n = n")
        self.assertEqual(step.after_state, "no goals")

    def test_non_tactic_projects_skip_proof_state_extraction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text("def value : Nat := 1\n", encoding="utf-8")

            analysis = scan_project(root, proof_states=True, proof_state_timeout=5)

        self.assertIsNotNone(analysis.proof_states)
        self.assertEqual(analysis.proof_states.status, "skipped")


def _write_project(root: Path) -> None:
    (root / "Main.lean").write_text(
        """
namespace Demo

theorem final (n : Nat) : n = n := by
  rfl

end Demo
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")


def _write_fake_lake(bin_dir: Path, structured: bool) -> None:
    bin_dir.mkdir()
    lake = bin_dir / "lake"
    structured_block = ""
    if structured:
        structured_block = """
if sys.argv[1:4] == ["env", "lean", "--run"]:
    print('PROOFLENS_PROOF_STATE\\t{"line":4,"column":3,"end_line":4,"end_column":6,"tactic_syntax":"rfl","before_state":"n : Nat\\\\n|- n = n","after_state":"no goals"}')
    sys.exit(0)
"""
    lake.write_text(
        f"""#!/usr/bin/env python3
import sys

trace = '''[Elab.info]
  \\u2022 [Command] @ \\u27e83, 0\\u27e9-\\u27e84, 5\\u27e9 @ Lean.Elab.Command.elabDeclaration
    \\u2022 [CustomInfo(Lean.Elab.Term.AsyncBodyInfo)]
      \\u2022 [CustomInfo(Lean.Elab.Term.BodyInfo)]
        \\u2022 [Tactic] @ \\u27e84, 2\\u27e9-\\u27e84, 5\\u27e9 @ Lean.Parser.Tactic._aux_Init_Tactics___macroRules_Lean_Parser_Tactic_tacticRfl_2
          (Tactic.tacticRfl "rfl")
          before
          n : Nat
          |- n = n
          after no goals
'''

{structured_block}
if sys.argv[1:3] == ["env", "lean"] and "-Dtrace.Elab.info=true" in sys.argv:
    print(trace)
    sys.exit(0)
sys.exit(2)
""",
        encoding="utf-8",
    )
    lake.chmod(0o755)


class _path_prepend:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.original = ""

    def __enter__(self) -> None:
        self.original = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.path) + os.pathsep + self.original

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        os.environ["PATH"] = self.original


if __name__ == "__main__":
    unittest.main()
