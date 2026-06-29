from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import unittest


class CliTests(unittest.TestCase):
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
