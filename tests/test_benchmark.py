from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from lean_agent.benchmark import BENCHMARK_SCHEMA_VERSION, build_benchmark_items
from lean_agent.project import scan_project


class BenchmarkTests(unittest.TestCase):
    def test_exports_tactic_level_benchmark_items(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
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

            analysis = scan_project(root)

        tactic_items = build_benchmark_items(analysis, level="tactic")
        self.assertEqual(len(tactic_items), 1)
        item = tactic_items[0]
        self.assertEqual(item["kind"], "tactic")
        self.assertEqual(item["parent_name"], "Demo.final")
        self.assertEqual(item["tactic"], "rfl")
        self.assertEqual(item["tactic_text"], "rfl")
        self.assertEqual(item["schema_version"], "prooflens.ai4math.v1")
        self.assertIsNone(item["before_state"])
        self.assertIsNone(item["after_state"])
        self.assertEqual(item["field_status"]["before_state"], "missing")
        self.assertEqual(item["field_status"]["after_state"], "missing")

    def test_all_level_includes_theorem_and_tactic_items(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
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

            analysis = scan_project(root)

        items = build_benchmark_items(analysis, level="all")
        self.assertEqual([item["kind"] for item in items], ["theorem", "tactic"])

    def test_theorem_items_include_assumptions_and_conclusion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

theorem final (n : Nat) (h : n = n) : n = n := by
  exact h

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        item = build_benchmark_items(analysis)[0]
        self.assertEqual(item["schema_version"], "prooflens.ai4math.v1")
        self.assertEqual(item["schema_stability"], "stable")
        self.assertIsNone(item["missing_value"])
        self.assertEqual(item["line"], 3)
        self.assertEqual(item["column"], 1)
        self.assertEqual(item["end_line"], 6)
        self.assertEqual(item["end_column"], 9)
        self.assertEqual(item["conclusion"], "n = n")
        self.assertEqual(item["field_status"]["conclusion"], "available")
        self.assertEqual(item["assumptions"][0]["names"], ["h"])
        self.assertEqual(item["assumptions"][0]["type"], "n = n")

    def test_schema_document_matches_exported_items(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

theorem final (n : Nat) (h : n = n) : n = n := by
  exact h

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        schema_path = Path(__file__).resolve().parents[1] / "docs" / "benchmark_schema_v1.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], BENCHMARK_SCHEMA_VERSION)
        self.assertEqual(schema["properties"]["schema_stability"]["enum"], ["stable", "deprecated"])
        items = build_benchmark_items(analysis, level="all")
        self.assertEqual([item["kind"] for item in items], ["theorem", "tactic"])
        for item in items:
            _assert_schema_compatible(schema, item)


if __name__ == "__main__":
    unittest.main()


def _assert_schema_compatible(schema: dict, item: dict) -> None:
    for field in schema["required"]:
        assert field in item, field
    assert item["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert item["missing_value"] is None
    assert item["schema_stability"] in schema["properties"]["schema_stability"]["enum"]
    assert item["difficulty"] in schema["properties"]["difficulty"]["enum"]
    for status in item["field_status"].values():
        assert status in schema["properties"]["field_status"]["additionalProperties"]["enum"]

    variants = schema["oneOf"]
    if item["kind"] == "tactic":
        required = variants[1]["required"]
    else:
        required = variants[0]["required"]
    for field in required:
        assert field in item, field
    for field in item["field_status"]:
        assert field in item, field
