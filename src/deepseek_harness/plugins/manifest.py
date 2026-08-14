"""Strict root-manifest parsing and contained artifact resolution."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

PLUGIN_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class ManifestError(ValueError):
    """Raised when a plugin manifest or declared artifact is invalid."""


class ActivationPolicy(str, Enum):
    """Whether one contribution failure rejects aggregate activation."""

    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class BackendManifest:
    """Python backend entrypoint declaration."""

    path: str
    attribute: str


@dataclass(frozen=True, slots=True)
class ClientManifest:
    """Browser bundle declaration."""

    bundle: str
    platform: str


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Validated root authority for one logical plugin."""

    plugin_id: str
    version: str
    runtime_api: str
    backend: BackendManifest | None
    client: ClientManifest | None
    protocol_schema: str | None
    backend_policy: ActivationPolicy | None
    client_policy: ActivationPolicy | None


@dataclass(frozen=True, slots=True)
class LoadedManifest:
    """Manifest plus exact source bytes and resolved artifact paths."""

    root: Path
    manifest: PluginManifest
    source: bytes
    backend_path: Path | None
    client_path: Path | None
    protocol_path: Path | None


def load_manifest(plugin_root: str | Path) -> LoadedManifest:
    """Read and strictly validate one plugin root without importing code."""
    root = Path(plugin_root).resolve(strict=True)
    if not root.is_dir():
        raise ManifestError(f"plugin root is not a directory: {root}")
    manifest_path = root / "plugin.toml"
    try:
        source = manifest_path.read_bytes()
    except OSError as error:
        raise ManifestError(f"cannot read {manifest_path}: {error}") from error
    try:
        raw = tomllib.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ManifestError(f"invalid plugin.toml: {error}") from error

    _fields(raw, {"plugin", "backend", "client", "protocol", "activation"}, "root")
    plugin = _table(raw, "plugin", required=True)
    assert plugin is not None
    _fields(plugin, {"id", "version", "runtime_api"}, "plugin")
    plugin_id = _string(plugin, "id")
    version = _string(plugin, "version")
    runtime_api = _string(plugin, "runtime_api")
    if not PLUGIN_ID.fullmatch(plugin_id):
        raise ManifestError(f"invalid plugin id: {plugin_id!r}")
    if not VERSION.fullmatch(version):
        raise ManifestError(f"invalid plugin version: {version!r}")
    if runtime_api != "1":
        raise ManifestError(f"unsupported runtime_api: {runtime_api!r}")

    backend_table = _table(raw, "backend")
    client_table = _table(raw, "client")
    if backend_table is None and client_table is None:
        raise ManifestError("plugin requires a backend or client contribution")

    backend: BackendManifest | None = None
    backend_path: Path | None = None
    if backend_table is not None:
        _fields(backend_table, {"entrypoint"}, "backend")
        entrypoint = _string(backend_table, "entrypoint")
        try:
            path_text, attribute = entrypoint.rsplit(":", 1)
        except ValueError as error:
            raise ManifestError("backend.entrypoint must be <python-file>:<attribute>") from error
        if not attribute.isidentifier() or not path_text.endswith(".py"):
            raise ManifestError("backend.entrypoint must be <python-file>:<attribute>")
        backend_path = _contained_file(root, path_text, "backend.entrypoint")
        backend = BackendManifest(path_text, attribute)

    client: ClientManifest | None = None
    client_path: Path | None = None
    if client_table is not None:
        _fields(client_table, {"bundle", "platform"}, "client")
        bundle = _string(client_table, "bundle")
        platform = _string(client_table, "platform")
        if platform != "web":
            raise ManifestError(f"unsupported client platform: {platform!r}")
        client_path = _contained_file(root, bundle, "client.bundle")
        client = ClientManifest(bundle, platform)

    protocol_table = _table(raw, "protocol")
    protocol_schema: str | None = None
    protocol_path: Path | None = None
    if protocol_table is not None:
        _fields(protocol_table, {"schema"}, "protocol")
        protocol_schema = _string(protocol_table, "schema")
        protocol_path = _contained_file(root, protocol_schema, "protocol.schema")

    activation = _table(raw, "activation") or {}
    _fields(activation, {"backend", "client"}, "activation")
    backend_policy = _policy(activation, "backend", backend is not None)
    client_policy = _policy(activation, "client", client is not None)
    manifest = PluginManifest(
        plugin_id,
        version,
        runtime_api,
        backend,
        client,
        protocol_schema,
        backend_policy,
        client_policy,
    )
    return LoadedManifest(
        root,
        manifest,
        source,
        backend_path,
        client_path,
        protocol_path,
    )


def _fields(table: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ManifestError(f"unknown {location} field(s): {', '.join(unknown)}")


def _table(
    root: dict[str, Any],
    name: str,
    *,
    required: bool = False,
) -> dict[str, Any] | None:
    value = root.get(name)
    if value is None:
        if required:
            raise ManifestError(f"missing [{name}] table")
        return None
    if not isinstance(value, dict):
        raise ManifestError(f"[{name}] must be a table")
    # tomllib guarantees string keys; its nested table type is not preserved by narrowing.
    return cast(dict[str, Any], value)


def _string(table: dict[str, Any], name: str) -> str:
    value = table.get(name)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{name} must be a non-empty string")
    return value


def _policy(
    activation: dict[str, Any],
    name: str,
    contribution_present: bool,
) -> ActivationPolicy | None:
    value = activation.get(name)
    if not contribution_present:
        if value is not None:
            raise ManifestError(f"activation.{name} requires a [{name}] contribution")
        return None
    if value is None:
        return ActivationPolicy.REQUIRED
    try:
        return ActivationPolicy(value)
    except (TypeError, ValueError) as error:
        raise ManifestError(f"activation.{name} must be 'required' or 'optional'") from error


def _contained_file(root: Path, relative: str, field: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        raise ManifestError(f"{field} must be relative to the plugin root")
    try:
        resolved = (root / path).resolve(strict=True)
    except OSError as error:
        raise ManifestError(f"cannot resolve {field} {relative!r}: {error}") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ManifestError(f"{field} escapes the plugin root or is not a file")
    return resolved
