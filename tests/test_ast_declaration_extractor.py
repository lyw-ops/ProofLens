from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from lean_agent.project import scan_project


class AstDeclarationExtractorTests(unittest.TestCase):
    def test_scan_can_refine_declarations_from_lean_ast_markers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

theorem fromAst : True := by
  trivial

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )
            (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
            bin_dir = root / "bin"
            _write_fake_lake(bin_dir)
            with _path_prepend(bin_dir):
                analysis = scan_project(root, ast_declaration_timeout=5)

        self.assertIsNotNone(analysis.declaration_extraction)
        self.assertEqual(analysis.declaration_extraction.status, "ok")
        self.assertEqual(len(analysis.declaration_extraction.records), 1)
        declaration = analysis.declaration_map["Demo.fromAst"]
        self.assertEqual(declaration.line, 3)
        self.assertEqual(declaration.column, 1)
        self.assertEqual(declaration.end_line, 4)
        self.assertEqual(declaration.end_column, 10)
        self.assertEqual(declaration.source, "theorem fromAst : True := by\n  trivial")
        self.assertEqual(declaration.statement, "theorem fromAst : True")
        self.assertEqual([step.text for step in declaration.proof_steps], ["trivial"])

    def test_scan_can_disable_lake_ast_declaration_extraction(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                "theorem fromRegex : True := by\n  trivial\n",
                encoding="utf-8",
            )
            (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")

            analysis = scan_project(root, ast_declarations=False, ast_declaration_timeout=5)

        self.assertIsNone(analysis.declaration_extraction)
        self.assertIn("fromRegex", analysis.declaration_map)


def _write_fake_lake(bin_dir: Path) -> None:
    bin_dir.mkdir()
    lake = bin_dir / "lake"
    lake.write_text(
        """#!/usr/bin/env python3
import json
import sys

if sys.argv[1:4] == ["env", "lean", "--run"]:
    payload = {
        "file": "Main.lean",
        "kind": "theorem",
        "name": "Demo.fromAst",
        "short_name": "fromAst",
        "line": 3,
        "column": 1,
        "end_line": 4,
        "end_column": 10,
        "source": "theorem fromAst : True := by\\n  trivial",
    }
    print("PROOFLENS_AST_DECL\\t" + json.dumps(payload))
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
