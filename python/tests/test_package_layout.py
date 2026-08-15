"""Package import layout tests."""

from __future__ import annotations

import importlib
import unittest


class PackageLayoutTests(unittest.TestCase):
    """The distribution exposes only the public harness import root."""

    def test_harness_is_the_only_import_root(self) -> None:
        """The public root resolves while the unsupported name stays absent."""
        self.assertEqual(importlib.import_module("harness").__name__, "harness")

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("deepseek_harness")


if __name__ == "__main__":
    unittest.main()
