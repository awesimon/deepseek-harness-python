"""PyCordis Service composition for the Browser Bridge."""

from __future__ import annotations

from harness.cordis import Context, PluginSpec, ServiceKey
from harness.plugins import CLIENT_ARTIFACTS

from .events import BridgeEventRegistry
from .host import BridgeRpcRegistry, BrowserBridge

BROWSER_BRIDGE = ServiceKey[BrowserBridge]("bridge.host")
BRIDGE_RPC_REGISTRY = ServiceKey[BridgeRpcRegistry]("bridge.rpc")
BRIDGE_EVENT_REGISTRY = ServiceKey[BridgeEventRegistry]("bridge.events")


def browser_bridge_plugin() -> PluginSpec[None]:
    """Return the provider for Bridge state, RPC, and Event Services."""

    async def apply(context: Context, _config: None) -> None:
        clients = context.require(CLIENT_ARTIFACTS)
        rpc = BridgeRpcRegistry()
        events = BridgeEventRegistry()
        bridge = BrowserBridge(clients, rpc, events)
        await context.provide(BRIDGE_RPC_REGISTRY, rpc)
        await context.provide(BRIDGE_EVENT_REGISTRY, events)
        await context.provide(BROWSER_BRIDGE, bridge)

    return PluginSpec(
        "browser-bridge",
        apply,
        requires=(CLIENT_ARTIFACTS,),
    )
