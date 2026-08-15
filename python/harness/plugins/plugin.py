"""PyCordis Service composition for dynamic plugin management."""

from __future__ import annotations

from harness.cordis import Context, PluginSpec, ServiceKey

from .manager import PluginManager
from .runtime import ClientArtifactRegistry

PLUGIN_MANAGER = ServiceKey[PluginManager]("plugins.manager")
CLIENT_ARTIFACTS = ServiceKey[ClientArtifactRegistry]("plugins.client-artifacts")


def plugin_manager_plugin() -> PluginSpec[None]:
    """Return the plugin that provides Manager and client artifact Services."""

    async def apply(context: Context, _config: None) -> None:
        clients = ClientArtifactRegistry()
        manager = PluginManager(context, clients=clients)
        await context.provide(CLIENT_ARTIFACTS, clients)
        await context.provide(PLUGIN_MANAGER, manager)

    return PluginSpec("plugin-manager", apply)
