from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
import unittest


class CliTests(unittest.TestCase):
    def test_benchmark_can_export_tactic_level_items(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo
theorem ok : True := by
  trivial
end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )
            output = root / "benchmark.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prooflens",
                    "benchmark",
                    str(root),
                    "--level",
                    "tactic",
                    "--format",
                    "json",
                    "--out",
                    str(output),
                ],
                text=True,
                capture_output=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["kind"], "tactic")
            self.assertEqual(payload[0]["tactic"], "trivial")

    def test_benchmark_can_export_proof_states_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo
theorem ok (n : Nat) : n = n := by
  rfl
end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )
            (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            lake = bin_dir / "lake"
            lake.write_text(
                """#!/usr/bin/env python3
import sys

trace = '''[Elab.info]
  \\u2022 [Command] @ \\u27e82, 0\\u27e9-\\u27e83, 5\\u27e9 @ Lean.Elab.Command.elabDeclaration
    \\u2022 [CustomInfo(Lean.Elab.Term.AsyncBodyInfo)]
      \\u2022 [CustomInfo(Lean.Elab.Term.BodyInfo)]
        \\u2022 [Tactic] @ \\u27e83, 2\\u27e9-\\u27e83, 5\\u27e9 @ Lean.Parser.Tactic._aux_Init_Tactics___macroRules_Lean_Parser_Tactic_tacticRfl_2
          (Tactic.tacticRfl "rfl")
          before
          n : Nat
          |- n = n
          after no goals
'''

if sys.argv[1:3] == ["env", "lean"] and "-Dtrace.Elab.info=true" in sys.argv:
    print(trace)
    sys.exit(0)
sys.exit(2)
""",
                encoding="utf-8",
            )
            lake.chmod(0o755)
            output = root / "benchmark.json"
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prooflens",
                    "benchmark",
                    str(root),
                    "--level",
                    "tactic",
                    "--proof-states",
                    "--format",
                    "json",
                    "--out",
                    str(output),
                ],
                text=True,
                capture_output=True,
                timeout=10,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload[0]["before_state"], "n : Nat\n|- n = n")
            self.assertEqual(payload[0]["after_state"], "no goals")

    def test_check_paper_semantic_statement_alignment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo
theorem ok (n : Nat) : n = n := by
  rfl
end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )
            (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
            paper = root / "paper.tex"
            paper.write_text(
                r"\leanstatement{Demo.ok}{forall (n : Nat), n = n}",
                encoding="utf-8",
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            lake = bin_dir / "lake"
            lake.write_text(
                """#!/bin/sh
if [ "$1" = "env" ] && [ "$2" = "lean" ]; then
  echo "PROOFLENS_DECL	theorem	Demo.ok	forall (n : Nat), n = n	"
  exit 0
fi
if [ "$1" = "--version" ]; then
  echo "Lake version 5.0.0"
  exit 0
fi
exit 2
""",
                encoding="utf-8",
            )
            lake.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prooflens",
                    "check-paper",
                    "--lean-root",
                    str(root),
                    "--paper",
                    str(paper),
                    "--semantic",
                    "--format",
                    "json",
                ],
                text=True,
                capture_output=True,
                timeout=10,
                env=env,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["statements_checked"], 1)
        self.assertEqual(payload["findings"], [])

    def test_env_command_does_not_require_lean_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prooflens",
                    "env",
                    str(root),
                    "--format",
                    "json",
                ],
                text=True,
                capture_output=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["root"], str(root.resolve()))
        self.assertIn("tools", payload)

    def test_scan_missing_path_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prooflens",
                    "scan",
                    str(missing),
                ],
                text=True,
                capture_output=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)

    def test_module_entrypoint_preserves_nonzero_exit_code(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                "theorem ok (n : Nat) : n = n := by\n  rfl\n",
                encoding="utf-8",
            )
            report = root / "audit.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prooflens",
                    "audit",
                    str(root),
                    "--format",
                    "json",
                    "--out",
                    str(report),
                ],
                text=True,
                capture_output=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
