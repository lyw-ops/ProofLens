import unittest

from lean_agent.declaration_index import DeclarationIndex
from lean_agent.models import LeanDeclaration, ProjectAnalysis


class DeclarationIndexTests(unittest.TestCase):
    def test_resolves_full_names_suffixes_and_ambiguous_short_names(self) -> None:
        first = _declaration("A.Demo.shared", "shared", "A.lean", 1)
        second = _declaration("B.Demo.shared", "shared", "B.lean", 2)
        final = _declaration("B.Demo.final", "final", "B.lean", 5)
        index = DeclarationIndex([first, second, final])

        self.assertIs(index.resolve("B.Demo.final"), final)
        self.assertIs(index.resolve("Demo.final"), final)
        self.assertIsNone(index.resolve("shared"))
        self.assertTrue(index.lookup("shared").ambiguous)
        self.assertEqual(index.names_for_token("shared"), ["A.Demo.shared", "B.Demo.shared"])
        self.assertEqual(index.ambiguous_symbols()["shared"], ["A.Demo.shared", "B.Demo.shared"])

    def test_project_analysis_computes_transitive_dependencies(self) -> None:
        base = _declaration("Demo.base", "base", "Main.lean", 1)
        mid = _declaration("Demo.mid", "mid", "Main.lean", 4)
        final = _declaration("Demo.final", "final", "Main.lean", 8)
        mid.dependencies = ["Demo.base"]
        final.dependencies = ["Demo.mid"]
        analysis = ProjectAnalysis(root="/tmp/demo", files=[], declarations=[base, mid, final])

        self.assertEqual(analysis.dependency_graph()["Demo.final"], ["Demo.mid"])
        self.assertEqual(analysis.transitive_dependency_graph()["Demo.final"], ["Demo.base", "Demo.mid"])


def _declaration(name: str, short_name: str, file: str, line: int) -> LeanDeclaration:
    return LeanDeclaration(
        kind="theorem",
        name=name,
        short_name=short_name,
        file=file,
        line=line,
        column=1,
        end_line=line,
        end_column=1,
        statement=f"theorem {short_name} : True",
    )


if __name__ == "__main__":
    unittest.main()
