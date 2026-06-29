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
        self.assertEqual([step.text for step in final.proof_steps], ["exact base n"])
        self.assertEqual(final.proof_steps[0].tactic, "exact")
        self.assertEqual(final.proof_steps[0].line, 8)

    def test_ignores_declarations_and_dependencies_inside_comments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

lemma base (n : Nat) : n = n := by
  rfl

/-
theorem ghost (n : Nat) : n = n := by
  exact base n
-/

theorem final (n : Nat) : n = n := by
  -- base appears here only as comment text.
  /- Demo.base appears here only as block comment text. -/
  rfl

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        names = {declaration.name for declaration in analysis.declarations}
        self.assertNotIn("Demo.ghost", names)
        self.assertEqual(analysis.declaration_map["Demo.final"].dependencies, [])

    def test_clears_module_docstring_before_namespace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
/-- Module-level overview, not a declaration docstring. -/
namespace Demo

@[simp]
lemma base (n : Nat) : n = n := by
  rfl

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        base = analysis.declaration_map["Demo.base"]
        self.assertEqual(base.docstring, None)
        self.assertEqual(base.attributes, ["simp"])

    def test_extracts_multiline_statement_without_body(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

theorem multiline
    (n : Nat)
    (h : n = n) :
    n = n := by
  exact h

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        statement = analysis.declaration_map["Demo.multiline"].statement
        self.assertIn("(h : n = n) :", statement)
        self.assertTrue(statement.endswith("n = n"))
        self.assertNotIn("exact h", statement)
        declaration = analysis.declaration_map["Demo.multiline"]
        self.assertEqual(declaration.formal_conclusion, "n = n")
        self.assertEqual(
            [(parameter.names, parameter.role) for parameter in declaration.formal_parameters],
            [(["n"], "parameter"), (["h"], "assumption")],
        )
        steps = declaration.proof_steps
        self.assertEqual([step.text for step in steps], ["exact h"])

    def test_extracts_multiple_tactic_steps_with_bullets(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

theorem both_true : True ∧ True := by
  constructor
  · trivial
  · exact True.intro

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        steps = analysis.declaration_map["Demo.both_true"].proof_steps
        self.assertEqual([step.tactic for step in steps], ["constructor", "trivial", "exact"])
        self.assertEqual([step.text for step in steps], ["constructor", "trivial", "exact True.intro"])
        self.assertEqual([step.line for step in steps], [4, 5, 6])
        self.assertEqual([step.column for step in steps], [3, 5, 5])

    def test_inline_by_step_uses_tactic_column(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                "theorem ok : True := by trivial\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        step = analysis.declaration_map["ok"].proof_steps[0]
        self.assertEqual(step.text, "trivial")
        self.assertEqual(step.line, 1)
        self.assertEqual(step.column, 25)

    def test_extracts_multiple_tactics_on_one_line(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
theorem ok (h : True) : True := by
  intro
  trivial; exact h
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        steps = analysis.declaration_map["ok"].proof_steps
        self.assertEqual([step.text for step in steps], ["intro", "trivial", "exact h"])
        self.assertEqual([step.line for step in steps], [2, 3, 3])

    def test_extracts_tactics_split_by_all_goals_separator(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
theorem ok : True ∧ True := by
  constructor <;> trivial
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        steps = analysis.declaration_map["ok"].proof_steps
        self.assertEqual([step.text for step in steps], ["constructor", "trivial"])

    def test_extracts_inline_case_branch_tactic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
theorem ok : True := by
  case h => trivial
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        steps = analysis.declaration_map["ok"].proof_steps
        self.assertEqual([step.text for step in steps], ["trivial"])

    def test_keeps_calc_and_by_cases_as_proof_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
theorem ok (p : Prop) [Decidable p] : True := by
  by_cases h : p
  · trivial
  · calc
      True := by trivial
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        steps = analysis.declaration_map["ok"].proof_steps
        self.assertEqual(
            [step.tactic for step in steps],
            ["by_cases", "trivial", "calc", "trivial"],
        )

    def test_mutual_block_does_not_pop_namespace(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo

mutual
theorem left : True := by
  trivial
theorem right : True := by
  exact left
end

theorem after : True := by
  exact right

end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        self.assertIn("Demo.left", analysis.declaration_map)
        self.assertIn("Demo.right", analysis.declaration_map)
        self.assertIn("Demo.after", analysis.declaration_map)
        self.assertIn("Demo.right", analysis.declaration_map["Demo.after"].dependencies)

    def test_where_helper_proof_does_not_leak_into_parent_steps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
theorem ok : True := by
  exact helper
where
  helper : True := by
    trivial
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        steps = analysis.declaration_map["ok"].proof_steps
        self.assertEqual([step.text for step in steps], ["exact helper"])

    def test_extracts_match_branch_tactics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
theorem ok (b : Bool) : True := by
  match b with
  | true => trivial
  | false => trivial
""".strip()
                + "\n",
                encoding="utf-8",
            )

            analysis = scan_project(root)

        steps = analysis.declaration_map["ok"].proof_steps
        self.assertEqual([step.text for step in steps], ["match b with", "trivial", "trivial"])


if __name__ == "__main__":
    unittest.main()
