"""Manifest and revision tests for dynamic plugin artifacts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.plugins import ManifestError, build_revision, load_manifest


class PluginManifestTests(unittest.TestCase):
    """Exercise strict fields, contribution forms, paths, and revision identity."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, relative: str, content: str | bytes) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def test_backend_client_and_full_stack_forms_validate(self) -> None:
        """Each supported contribution combination has one root identity."""
        self._write("backend.py", "plugin = None\n")
        self._write("client.js", b"export default {}")
        forms = (
            '[backend]\nentrypoint = "backend.py:plugin"\n',
            '[client]\nbundle = "client.js"\nplatform = "web"\n',
            (
                '[backend]\nentrypoint = "backend.py:plugin"\n\n'
                '[client]\nbundle = "client.js"\nplatform = "web"\n'
            ),
        )
        for index, contribution in enumerate(forms):
            self._write(
                "plugin.toml",
                f'[plugin]\nid = "com.example.form{index}"\nversion = "1.0.0"\n'
                f'runtime_api = "1"\n\n{contribution}',
            )
            manifest = load_manifest(self.root).manifest
            self.assertEqual(manifest.plugin_id, f"com.example.form{index}")

    def test_unknown_fields_and_absent_contributions_fail(self) -> None:
        """Misspelled metadata and identity-only manifests fail loud."""
        self._write(
            "plugin.toml",
            '[plugin]\nid = "com.example.empty"\nversion = "1.0.0"\n'
            'runtime_api = "1"\nunknown = true\n',
        )
        with self.assertRaises(ManifestError):
            load_manifest(self.root)
        self._write(
            "plugin.toml",
            '[plugin]\nid = "com.example.empty"\nversion = "1.0.0"\n'
            'runtime_api = "1"\n',
        )
        with self.assertRaises(ManifestError):
            load_manifest(self.root)

    def test_activation_for_absent_contribution_fails(self) -> None:
        """Activation policy cannot imply a contribution that is not present."""
        self._write("client.js", b"client")
        self._write(
            "plugin.toml",
            '[plugin]\nid = "com.example.client"\nversion = "1.0.0"\n'
            'runtime_api = "1"\n\n[client]\nbundle = "client.js"\nplatform = "web"\n'
            '\n[activation]\nbackend = "optional"\n',
        )
        with self.assertRaises(ManifestError):
            load_manifest(self.root)

        self._write(
            "plugin.toml",
            '[plugin]\nid = "com.example.client"\nversion = "1.0.0"\n'
            'runtime_api = "1"\n\n[client]\nbundle = "client.js"\nplatform = "web"\n'
            '\n[activation]\nclient = "sometimes"\n',
        )
        with self.assertRaises(ManifestError):
            load_manifest(self.root)

    def test_artifact_path_cannot_escape_root(self) -> None:
        """Relative traversal cannot load code outside the plugin directory."""
        outside = self.root.parent / "outside-plugin.py"
        outside.write_text("plugin = None\n", encoding="utf-8")
        self._write(
            "plugin.toml",
            '[plugin]\nid = "com.example.escape"\nversion = "1.0.0"\n'
            'runtime_api = "1"\n\n[backend]\nentrypoint = "../outside-plugin.py:plugin"\n',
        )
        try:
            with self.assertRaises(ManifestError):
                load_manifest(self.root)
        finally:
            outside.unlink()

    def test_revision_changes_with_declared_content(self) -> None:
        """Backend, client, protocol, and manifest bytes participate in the digest."""
        self._write("backend.py", "plugin = None\n")
        self._write("client.js", b"client-v1")
        self._write("schema.json", b"{}")
        self._write(
            "plugin.toml",
            '[plugin]\nid = "com.example.full"\nversion = "1.0.0"\n'
            'runtime_api = "1"\n\n[backend]\nentrypoint = "backend.py:plugin"\n'
            '\n[client]\nbundle = "client.js"\nplatform = "web"\n'
            '\n[protocol]\nschema = "schema.json"\n',
        )
        first = build_revision(self.root)
        self.assertEqual(first.digest, build_revision(self.root).digest)
        for path, content in (
            ("backend.py", "plugin = 'changed'\n"),
            ("client.js", b"client-v2"),
            ("schema.json", b'{"changed":true}'),
        ):
            self._write(path, content)
            changed = build_revision(self.root)
            self.assertNotEqual(first.digest, changed.digest)
            first = changed
