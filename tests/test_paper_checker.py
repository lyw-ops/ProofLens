from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lean_agent.paper_checker import check_paper
from lean_agent.project import scan_project


class PaperCheckerTests(unittest.TestCase):
    def test_reports_missing_lean_reference(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                r"The theorem is \lean{Demo.ok}, but this one is stale: \lean{Demo.missing}.",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.references_checked, 2)
        messages = [finding.message for finding in report.findings]
        self.assertTrue(any("Demo.missing" in message for message in messages))

    def test_checks_explicit_leanstatement_against_semantic_type(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                r"The formal statement is \leanstatement{Demo.ok}{forall (n : Nat), n = n}.",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            analysis.declaration_map["Demo.ok"].semantic_type = "forall (n : Nat), n = n"
            report = check_paper(analysis, paper)

        self.assertEqual(report.statements_checked, 1)
        self.assertEqual(report.findings, [])

    def test_reports_mismatched_leanstatement(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                r"The formal statement is \leanstatement{Demo.ok}{forall (n : Nat), n = 0}.",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            analysis.declaration_map["Demo.ok"].semantic_type = "forall (n : Nat), n = n"
            report = check_paper(analysis, paper)

        self.assertEqual(report.statements_checked, 1)
        messages = [finding.message for finding in report.findings]
        self.assertTrue(any("does not match" in message for message in messages))
        self.assertEqual(
            report.findings[0].patch,
            r"\leanstatement{Demo.ok}{forall (n : Nat), n = n}",
        )
        self.assertEqual(report.patches_suggested, 1)
        self.assertEqual(report.patches_applied, 0)

    def test_check_paper_does_not_modify_file_without_permission(self) -> None:
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
            paper = root / "paper.tex"
            original = r"The formal statement is \leanstatement{Demo.ok}{forall (n : Nat), n = 0}."
            paper.write_text(original, encoding="utf-8")
            analysis = scan_project(root)
            analysis.declaration_map["Demo.ok"].semantic_type = "forall (n : Nat), n = n"
            report = check_paper(analysis, paper)
            current = paper.read_text(encoding="utf-8")

        self.assertEqual(current, original)
        self.assertEqual(report.patches_suggested, 1)
        self.assertEqual(report.patches_applied, 0)

    def test_check_paper_applies_statement_patch_with_permission(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                r"The formal statement is \leanstatement{Demo.ok}{forall (n : Nat), n = 0}.",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            analysis.declaration_map["Demo.ok"].semantic_type = "forall (n : Nat), n = n"
            report = check_paper(analysis, paper, apply_patches=True)

            patched = paper.read_text(encoding="utf-8")

        self.assertIn(r"\leanstatement{Demo.ok}{forall (n : Nat), n = n}", patched)
        self.assertEqual(report.findings, [])
        self.assertEqual(report.patches_suggested, 1)
        self.assertEqual(report.patches_applied, 1)
        self.assertEqual(report.applied_patches[0].kind, "replace_leanstatement")

    def test_reports_stale_lean_code_block_statement(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                r"""
\begin{lstlisting}[language=Lean]
theorem ok (n : Nat) : n = 0 := by
  sorry
\end{lstlisting}
""".strip(),
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.code_blocks_checked, 1)
        self.assertGreaterEqual(report.statements_checked, 1)
        messages = [finding.message for finding in report.findings]
        self.assertTrue(any("code block statement" in message for message in messages))

    def test_checks_conclusion_and_assumptions_commands(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                """
namespace Demo
theorem ok (n : Nat) (h : n = n) : n = n := by
  exact h
end Demo
""".strip()
                + "\n",
                encoding="utf-8",
            )
            paper = root / "paper.tex"
            paper.write_text(
                r"""
\leanconclusion{Demo.ok}{n = n}
\leanassumptions{Demo.ok}{h : n = n}
""".strip(),
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.formal_parts_checked, 2)
        self.assertEqual(report.findings, [])

    def test_reports_mismatched_conclusion(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(r"\leanconclusion{Demo.ok}{n = 0}", encoding="utf-8")
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.formal_parts_checked, 1)
        messages = [finding.message for finding in report.findings]
        self.assertTrue(any("conclusion" in message for message in messages))

    def test_resolves_paper_aliases_in_references_and_statements(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                r"""
\leanalias{Main theorem}{Demo.ok}
We cite \leantheorem{Main theorem}.
\leanstatement{Main theorem}{theorem ok (n : Nat) : n = n}
""".strip(),
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.references_checked, 2)
        self.assertEqual(report.statements_checked, 1)
        self.assertEqual(report.findings, [])

    def test_resolves_aliases_from_included_tex_files(self) -> None:
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
            (root / "aliases.tex").write_text(
                r"\leantheoremalias{Main theorem}{Demo.ok}",
                encoding="utf-8",
            )
            paper = root / "paper.tex"
            paper.write_text(
                r"\input{aliases} We cite \leantheorem{Main theorem}.",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.references_checked, 1)
        self.assertEqual(report.findings, [])

    def test_nested_latex_macro_reference_resolves_to_lean_name(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                r"We cite \leantheorem{\texttt{Demo.ok}}.",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.references_checked, 1)
        self.assertEqual(report.findings, [])

    def test_missing_reference_location_includes_nearest_section(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                "theorem ok : True := by\n  trivial\n",
                encoding="utf-8",
            )
            paper = root / "paper.tex"
            paper.write_text(
                r"\section{Formalization}\lean{missing}",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.references_checked, 1)
        self.assertIn("section `Formalization`", report.findings[0].location)
        self.assertIn("\\leanalias", report.findings[0].suggestion)

    def test_missing_reference_location_supports_starred_section(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Main.lean").write_text(
                "theorem ok : True := by\n  trivial\n",
                encoding="utf-8",
            )
            paper = root / "paper.tex"
            paper.write_text(
                r"\section*{Formalization}\lean{missing}",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.references_checked, 1)
        self.assertIn("section `Formalization`", report.findings[0].location)

    def test_missing_short_reference_includes_alias_patch_when_candidate_is_unique(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(r"\lean{Ok}", encoding="utf-8")
            analysis = scan_project(root)
            report = check_paper(analysis, paper)

        self.assertEqual(report.references_checked, 1)
        self.assertEqual(report.findings[0].patch, r"\leanalias{Ok}{Demo.ok}")

    def test_check_paper_inserts_unique_alias_patch_with_permission(self) -> None:
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
            paper = root / "paper.tex"
            paper.write_text(
                "\\documentclass{article}\n\\begin{document}\nWe cite \\lean{Ok}.\n\\end{document}\n",
                encoding="utf-8",
            )
            analysis = scan_project(root)
            report = check_paper(analysis, paper, apply_patches=True)
            patched = paper.read_text(encoding="utf-8")

        self.assertIn(r"\leanalias{Ok}{Demo.ok}", patched)
        self.assertLess(patched.index(r"\leanalias{Ok}{Demo.ok}"), patched.index(r"\begin{document}"))
        self.assertEqual(report.findings, [])
        self.assertEqual(report.patches_applied, 1)


if __name__ == "__main__":
    unittest.main()
