"""Local Browser SDK asset and scaffold distribution tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.scaffold import PluginKind, create_plugin
from harness.scaffold.sdk_asset import (
    BROWSER_SDK_SHA256,
    BROWSER_SDK_TARBALL,
    browser_sdk_bytes,
    export_browser_sdk,
)


class SdkDistributionTests(unittest.TestCase):
    """Prove the wheel-bundled tarball is verified and self-contained."""

    def test_export_is_digest_idempotent_and_rejects_different_bytes(self) -> None:
        """Export does not overwrite a user-owned path."""
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / BROWSER_SDK_TARBALL
            first = export_browser_sdk(target)
            second = export_browser_sdk(target)
            self.assertEqual(first, second)
            target.write_bytes(b"different")
            with self.assertRaises(FileExistsError):
                export_browser_sdk(target)

    def test_client_scaffold_vendors_verified_sdk_and_file_dependency(self) -> None:
        """Generated clients need no workspace symlink to resolve the SDK."""
        with tempfile.TemporaryDirectory() as temporary:
            destination = create_plugin(
                PluginKind.CLIENT,
                "com.example.distributed",
                Path(temporary) / "plugin",
            )
            vendor = destination / "frontend" / "vendor" / BROWSER_SDK_TARBALL
            self.assertEqual(vendor.read_bytes(), browser_sdk_bytes())
            package = (destination / "frontend" / "package.json").read_text(encoding="utf-8")
            lockfile = (destination / "frontend" / "pnpm-lock.yaml").read_text(encoding="utf-8")
            self.assertIn(f"file:vendor/{BROWSER_SDK_TARBALL}", package)
            self.assertIn(f"file:vendor/{BROWSER_SDK_TARBALL}", lockfile)
            self.assertEqual(len(BROWSER_SDK_SHA256), 64)


if __name__ == "__main__":
    unittest.main()
