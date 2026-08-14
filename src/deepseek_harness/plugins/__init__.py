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
    BackendActivation,
    BackendActivationError,
    BackendHost,
    ClientArtifactRegistry,
    ClientPublication,
    InProcessBackendHost,
)

__all__ = [
    "CLIENT_ARTIFACTS",
    "PLUGIN_MANAGER",
    "ActivationPolicy",
    "BackendActivation",
    "BackendActivationError",
    "BackendHost",
    "BackendManifest",
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
    "PluginSnapshot",
    "PluginState",
    "build_revision",
    "load_manifest",
    "plugin_manager_plugin",
]
