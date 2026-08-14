"""Public Dynamic Plugin Manager API."""

from .manager import PluginDiagnostic, PluginManager, PluginSnapshot, PluginState
from .manifest import (
    ActivationPolicy,
    BackendManifest,
    ClientManifest,
    LoadedManifest,
    ManifestError,
    PluginManifest,
    load_manifest,
)
from .plugin import CLIENT_ARTIFACTS, PLUGIN_MANAGER, plugin_manager_plugin
from .revision import PluginRevision, build_revision
from .runtime import (
    PLUGIN_RUNTIME_IDENTITY,
    BackendActivation,
    BackendActivationError,
    BackendHost,
    ClientArtifact,
    ClientArtifactRegistry,
    ClientPublication,
    InProcessBackendHost,
    PluginRuntimeIdentity,
)

__all__ = [
    "CLIENT_ARTIFACTS",
    "PLUGIN_MANAGER",
    "PLUGIN_RUNTIME_IDENTITY",
    "ActivationPolicy",
    "BackendActivation",
    "BackendActivationError",
    "BackendHost",
    "BackendManifest",
    "ClientArtifact",
    "ClientArtifactRegistry",
    "ClientManifest",
    "ClientPublication",
    "InProcessBackendHost",
    "LoadedManifest",
    "ManifestError",
    "PluginDiagnostic",
    "PluginManager",
    "PluginManifest",
    "PluginRevision",
    "PluginRuntimeIdentity",
    "PluginSnapshot",
    "PluginState",
    "build_revision",
    "load_manifest",
    "plugin_manager_plugin",
]
