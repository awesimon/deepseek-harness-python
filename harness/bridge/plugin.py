"""PyCordis Service composition for the Browser Bridge."""

from __future__ import annotations

from harness.cordis import Context, PluginSpec, ServiceKey
from harness.plugins import CLIENT_ARTIFACTS, PLUGIN_MANAGER
from harness.plugins.client_activation import ClientActivationAggregator

from .events import BridgeEventRegistry
from .host import BridgeRpcRegistry, BrowserBridge

BROWSER_BRIDGE = ServiceKey[BrowserBridge]("bridge.host")
BRIDGE_RPC_REGISTRY = ServiceKey[BridgeRpcRegistry]("bridge.rpc")
BRIDGE_EVENT_REGISTRY = ServiceKey[BridgeEventRegistry]("bridge.events")
CLIENT_ACTIVATION = ServiceKey[ClientActivationAggregator]("bridge.client-activation")


def browser_bridge_plugin() -> PluginSpec[None]:
    """Return the provider for Bridge state, RPC, and Event Services."""

    async def apply(context: Context, _config: None) -> None:
        clients = context.require(CLIENT_ARTIFACTS)
        manager = context.require(PLUGIN_MANAGER)
        rpc = BridgeRpcRegistry()
        events = BridgeEventRegistry()
        aggregation = ClientActivationAggregator(manager.report_client_activation)
        await context.effect(
            lambda: manager.attach_client_aggregator(aggregation),
            "attach-client-activation",
        )
        bridge = BrowserBridge(clients, rpc, events, aggregation)
        await context.provide(BRIDGE_RPC_REGISTRY, rpc)
        await context.provide(BRIDGE_EVENT_REGISTRY, events)
        await context.provide(CLIENT_ACTIVATION, aggregation)
        await context.provide(BROWSER_BRIDGE, bridge)

    return PluginSpec(
        "browser-bridge",
        apply,
        requires=(CLIENT_ARTIFACTS, PLUGIN_MANAGER),
    )
