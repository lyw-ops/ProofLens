from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest

from lean_agent.benchmark import build_benchmark_items
from lean_agent.project import scan_project
from lean_agent.semantic_extractor import _parse_declarations


class SemanticExtractorTests(unittest.TestCase):
    def test_scan_can_attach_semantic_declaration_types_from_lake(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_project(root)
            bin_dir = root / "bin"
            _write_fake_lake(bin_dir)
            with _path_prepend(bin_dir):
                analysis = scan_project(root, semantic=True, semantic_timeout=5)

        self.assertIsNotNone(analysis.semantic)
        self.assertEqual(analysis.semantic.status, "ok")
        self.assertEqual(len(analysis.semantic.declarations), 2)
        semantic_final = {
            declaration.name: declaration
            for declaration in analysis.semantic.declarations
        }["Demo.final"]
        self.assertEqual(semantic_final.file, "Main.lean")
        self.assertEqual(semantic_final.line, 8)
        self.assertEqual(semantic_final.column, 1)
        self.assertEqual(semantic_final.end_line, 11)
        self.assertEqual(semantic_final.end_column, 9)
        self.assertEqual(semantic_final.docstring, "Final theorem.")
        self.assertEqual(semantic_final.attributes, ["simp"])
        declaration = analysis.declaration_map["Demo.final"]
        self.assertEqual(declaration.canonical_name, "Demo.final")
        self.assertEqual(declaration.semantic_kind, "theorem")
        self.assertEqual(declaration.semantic_type, "forall (n : Nat), n = n")
        self.assertEqual(declaration.semantic_dependencies, ["Demo.ok"])
        self.assertEqual(analysis.dependency_graph()["Demo.final"], ["Demo.ok"])
        self.assertEqual(analysis.transitive_dependency_graph()["Demo.final"], ["Demo.ok"])

        items = {
            item["name"]: item
            for item in build_benchmark_items(analysis)
        }
        self.assertEqual(items["Demo.final"]["lean_statement"], "forall (n : Nat), n = n")
        self.assertEqual(items["Demo.final"]["dependencies"], ["Demo.ok"])

    def test_semantic_scan_skips_non_lake_project(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                "theorem ok : True := by\n  trivial\n",
                encoding="utf-8",
            )

            analysis = scan_project(root, semantic=True, semantic_timeout=5)

        self.assertIsNotNone(analysis.semantic)
        self.assertEqual(analysis.semantic.status, "skipped")
        self.assertIn("No lakefile", analysis.semantic.message)

    def test_semantic_json_marker_handles_tabs_and_newlines(self) -> None:
        declarations = _parse_declarations(
            'PROOFLENS_DECL_JSON {"kind":"theorem","name":"Demo.final",'
            '"type":"forall (n : Nat),\\n\\tn = n","dependencies":["Demo.ok"]}\n'
        )

        self.assertEqual(len(declarations), 1)
        self.assertEqual(declarations[0].name, "Demo.final")
        self.assertEqual(declarations[0].type, "forall (n : Nat),\n\tn = n")
        self.assertEqual(declarations[0].dependencies, ["Demo.ok"])


def _write_project(root: Path) -> None:
    (root / "Main.lean").write_text(
        """
namespace Demo

theorem ok (n : Nat) : n = n := by
  rfl

/-- Final theorem. -/
@[simp]
theorem final (n : Nat) : n = n := by
  exact ok n

end Demo
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")


def _write_fake_lake(bin_dir: Path) -> None:
    bin_dir.mkdir()
    lake = bin_dir / "lake"
    lake.write_text(
        """#!/bin/sh
if [ "$1" = "env" ] && [ "$2" = "lean" ]; then
  printf '%s\n' 'PROOFLENS_DECL_JSON {"kind":"theorem","name":"Demo.ok","type":"forall (n : Nat), n = n","dependencies":[]}'
  printf '%s\n' 'PROOFLENS_DECL_JSON {"kind":"theorem","name":"Demo.final","type":"forall (n : Nat), n = n","dependencies":["Demo.ok"]}'
  exit 0
fi
if [ "$1" = "build" ]; then
  echo "Build completed successfully"
  exit 0
fi
echo "unexpected lake command" >&2
exit 2
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
