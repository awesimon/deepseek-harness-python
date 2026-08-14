"""Immutable content revisions built from validated plugin artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .manifest import PluginManifest, load_manifest


@dataclass(frozen=True, slots=True)
class PluginRevision:
    """Exact manifest and contribution bytes for one plugin revision."""

    digest: str
    root: Path
    manifest: PluginManifest
    backend_source: bytes | None
    backend_filename: str | None
    client_bundle: bytes | None
    protocol_schema: bytes | None


def build_revision(plugin_root: str | Path) -> PluginRevision:
    """Read each declared artifact once and compute a deterministic digest."""
    loaded = load_manifest(plugin_root)
    backend = _read(loaded.backend_path, "backend")
    client = _read(loaded.client_path, "client")
    protocol = _read(loaded.protocol_path, "protocol")
    digest = hashlib.sha256()
    for label, content in (
        (b"manifest", loaded.source),
        (b"backend", backend),
        (b"client", client),
        (b"protocol", protocol),
    ):
        if content is None:
            continue
        digest.update(len(label).to_bytes(2, "big"))
        digest.update(label)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return PluginRevision(
        digest.hexdigest(),
        loaded.root,
        loaded.manifest,
        backend,
        None if loaded.backend_path is None else str(loaded.backend_path),
        client,
        protocol,
    )


def _read(path: Path | None, label: str) -> bytes | None:
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"cannot read {label} artifact {path}: {error}") from error
