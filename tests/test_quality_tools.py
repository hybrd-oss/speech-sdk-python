"""Regression checks for the failing complexity gate; no provider/network calls."""

import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check_complexity import check_file, main


class ComplexityTests(unittest.TestCase):
    def test_function_boundaries(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            for branches in (8, 9, 10):
                with self.subTest(complexity=branches + 1):
                    path.write_text(
                        "def sample(value):\n" + "    if value:\n        pass\n" * branches,
                        encoding="utf-8",
                    )
                    self.assertEqual(bool(check_file(path)), branches == 10)

    def test_methods_and_nested_functions(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            for prefix in (
                "class Example:\n    def sample(self, value):\n",
                "def outer():\n    def sample(value):\n",
            ):
                with self.subTest(prefix=prefix):
                    path.write_text(
                        prefix + "        if value:\n            pass\n" * 10,
                        encoding="utf-8",
                    )
                    self.assertTrue(check_file(path))

    def test_invalid_and_missing_source_fail(self) -> None:
        with TemporaryDirectory() as directory, redirect_stderr(StringIO()):
            path = Path(directory) / "sample.py"
            path.write_text("def broken(:\n", encoding="utf-8")
            self.assertEqual(main([str(path)]), 1)
            path.unlink()
            self.assertEqual(main([str(path)]), 1)

    def test_clean_directory_passes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text("def simple():\n    return 1\n", encoding="utf-8")
            self.assertEqual(main([directory]), 0)


if __name__ == "__main__":
    unittest.main()
