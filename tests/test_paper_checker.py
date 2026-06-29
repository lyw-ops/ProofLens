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


if __name__ == "__main__":
    unittest.main()

