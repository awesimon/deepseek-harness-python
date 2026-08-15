"""Public Plugin Control Plane API."""

from .cli import build_plugin_parser, run_plugin_cli
from .http import PluginControlHttpAdapter
from .service import (
    PLUGIN_CONTROL,
    ControlInventorySnapshot,
    ControlOperation,
    ControlPluginSnapshot,
    ControlTombstone,
    PluginControlClosedError,
    PluginControlConfig,
    PluginControlConflictError,
    PluginControlError,
    PluginControlService,
    UnsafePluginRootError,
    WatcherDiagnostic,
    WatcherSnapshot,
    plugin_control_plugin,
)
from .watcher import (
    PluginCatalogWatcher,
    PluginWatcherConfig,
    WatchCreatePolicy,
    WatchDeletePolicy,
)

__all__ = [
    "PLUGIN_CONTROL",
    "ControlInventorySnapshot",
    "ControlOperation",
    "ControlPluginSnapshot",
    "ControlTombstone",
    "PluginCatalogWatcher",
    "PluginControlClosedError",
    "PluginControlConfig",
    "PluginControlConflictError",
    "PluginControlError",
    "PluginControlHttpAdapter",
    "PluginControlService",
    "PluginWatcherConfig",
    "UnsafePluginRootError",
    "WatchCreatePolicy",
    "WatchDeletePolicy",
    "WatcherDiagnostic",
    "WatcherSnapshot",
    "build_plugin_parser",
    "plugin_control_plugin",
    "run_plugin_cli",
]
