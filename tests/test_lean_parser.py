from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lean_agent.project import scan_project


class LeanParserTests(unittest.TestCase):
    def test_parses_namespaced_declarations_and_dependencies(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

/-- Base helper. -/
lemma base (n : Nat) : n = n := by
  rfl

theorem final (n : Nat) : n = n := by
  exact base n

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        names = {declaration.name for declaration in analysis.declarations}
        self.assertIn("Demo.base", names)
        self.assertIn("Demo.final", names)
        final = analysis.declaration_map["Demo.final"]
        self.assertEqual(final.kind, "theorem")
        self.assertIn("Demo.base", final.dependencies)
        self.assertEqual(final.docstring, None)
        base = analysis.declaration_map["Demo.base"]
        self.assertEqual(base.docstring, "Base helper.")


if __name__ == "__main__":
    unittest.main()

