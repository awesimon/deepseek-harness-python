"""Keyless full-stack plugin lifecycle across Manager and Browser Bridge."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harness.bridge import (
    BRIDGE_EVENT_REGISTRY,
    BRIDGE_RPC_REGISTRY,
    PROTOCOL_VERSION,
    BridgeEvent,
    BridgeEventRegistry,
    BridgeRpcRegistry,
    BrowserBridge,
    PagePluginState,
    PluginLoadResult,
    ReconcileComplete,
    RpcCall,
)
from harness.cordis import Cordis, ServiceKey
from harness.plugins import (
    ClientArtifactRegistry,
    PluginManager,
)

PLUGIN_ID = "com.example.full-stack"
RECEIVED_SERVICE = ServiceKey[list[object]]("tests.bridge.received")


def backend_source(revision_label: str) -> str:
    """Return a backend contribution with Effect-owned Bridge handlers."""
    return f'''from harness.bridge import BRIDGE_EVENT_REGISTRY, BRIDGE_RPC_REGISTRY
from harness.cordis import PluginSpec, ServiceKey
from harness.plugins import PLUGIN_RUNTIME_IDENTITY

RECEIVED = ServiceKey[list[object]]("tests.bridge.received")
PLUGIN_ID = "{PLUGIN_ID}"
LABEL = "{revision_label}"

async def apply(ctx, config):
    rpc = ctx.require(BRIDGE_RPC_REGISTRY)
    events = ctx.require(BRIDGE_EVENT_REGISTRY)
    received = ctx.require(RECEIVED)
    identity = ctx.require(PLUGIN_RUNTIME_IDENTITY)

    def echo(arguments):
        return {{"revision": LABEL, "value": arguments["value"]}}

    def receive(page_id, payload):
        received.append({{"revision": LABEL, "pageId": page_id, "payload": payload}})

    await ctx.effect(
        lambda: rpc.register(identity.plugin_id, identity.revision, "echo", echo),
        "bridge-rpc:echo",
    )
    await ctx.effect(
        lambda: events.register(identity.plugin_id, identity.revision, "from-client", receive),
        "bridge-event:from-client",
    )

plugin = PluginSpec(
    "full-stack-test",
    apply,
    requires=(BRIDGE_RPC_REGISTRY, BRIDGE_EVENT_REGISTRY, RECEIVED, PLUGIN_RUNTIME_IDENTITY),
)
'''


class FullStackLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Exercise one logical plugin through both contribution lifecycles."""

    async def asyncSetUp(self) -> None:
        self.runtime = Cordis()
        self.clients = ClientArtifactRegistry()
        self.rpc = BridgeRpcRegistry()
        self.events = BridgeEventRegistry()
        self.received: list[object] = []
        await self.runtime.root.provide(BRIDGE_RPC_REGISTRY, self.rpc)
        await self.runtime.root.provide(BRIDGE_EVENT_REGISTRY, self.events)
        await self.runtime.root.provide(RECEIVED_SERVICE, self.received)
        self.manager = PluginManager(self.runtime.root, clients=self.clients)
        self.bridge = BrowserBridge(self.clients, self.rpc, self.events)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self) -> None:
        await self.runtime.close()
        self.temp.cleanup()

    def _write_plugin(self, label: str, version: str) -> None:
        (self.root / "backend.py").write_text(backend_source(label), encoding="utf-8")
        (self.root / "client.js").write_text(
            "export default (ctx) => ctx.effect(() => () => undefined)\n"
            f"export const revisionLabel = {label!r}\n",
            encoding="utf-8",
        )
        (self.root / "plugin.toml").write_text(
            f'[plugin]\nid = "{PLUGIN_ID}"\nversion = "{version}"\nruntime_api = "1"\n\n'
            '[backend]\nentrypoint = "backend.py:plugin"\n\n'
            '[client]\nbundle = "client.js"\nplatform = "web"\n\n'
            '[activation]\nbackend = "required"\nclient = "required"\n',
            encoding="utf-8",
        )

    def _activate_page(self, operation_id: str, revision: str) -> None:
        self.bridge.report(
            "page-1",
            PluginLoadResult(
                PROTOCOL_VERSION,
                operation_id,
                PLUGIN_ID,
                revision,
                PagePluginState.ACTIVE,
            ),
        )
        self.bridge.complete(
            "page-1",
            ReconcileComplete(PROTOCOL_VERSION, operation_id, True),
        )

    async def _call(self, revision: str, call_id: str) -> object:
        return await self.bridge.call(
            RpcCall(
                PROTOCOL_VERSION,
                "page-1",
                call_id,
                PLUGIN_ID,
                revision,
                "echo",
                {"value": call_id},
            )
        )

    async def test_enable_update_and_disable_remove_both_contributions(self) -> None:
        """Both runtimes follow one Revision while stale identities lose authority."""
        self._write_plugin("v1", "1.0.0")
        await self.manager.install(self.root)
        first = await self.manager.enable(PLUGIN_ID)

        command = self.bridge.connect("page-1", {})
        self.assertEqual([item.revision for item in command.desired], [first.revision])
        self._activate_page(command.operation_id, first.revision)
        outbound: list[BridgeEvent] = []
        self.bridge.attach_page_events("page-1", outbound.append)

        result = await self._call(first.revision, "call-v1")
        self.assertEqual(result.result, {"revision": "v1", "value": "call-v1"})
        await self.bridge.receive_event(
            BridgeEvent(
                PROTOCOL_VERSION,
                "page-1",
                PLUGIN_ID,
                first.revision,
                "from-client",
                {"value": 1},
            )
        )
        self.assertEqual(
            self.received[0],
            {"revision": "v1", "pageId": "page-1", "payload": {"value": 1}},
        )
        self.assertEqual(
            await self.bridge.emit_event(
                PLUGIN_ID,
                first.revision,
                "from-backend",
                {"value": 2},
            ),
            1,
        )
        self.assertEqual(outbound[-1].payload, {"value": 2})

        self._write_plugin("v2", "1.1.0")
        second = await self.manager.update(self.root)
        self.assertNotEqual(second.revision, first.revision)
        command = self.bridge.reconcile("page-1")
        self.assertEqual([item.revision for item in command.desired], [second.revision])

        unavailable = await self._call(first.revision, "call-old-handler")
        self.assertEqual(unavailable.error_code, "method_unavailable")
        self._activate_page(command.operation_id, second.revision)
        stale = await self._call(first.revision, "call-stale-page")
        self.assertEqual(stale.error_code, "stale_client")
        current = await self._call(second.revision, "call-v2")
        self.assertEqual(current.result, {"revision": "v2", "value": "call-v2"})

        await self.manager.disable(PLUGIN_ID)
        command = self.bridge.reconcile("page-1")
        self.assertEqual(command.desired, ())
        self.bridge.report(
            "page-1",
            PluginLoadResult(
                PROTOCOL_VERSION,
                command.operation_id,
                PLUGIN_ID,
                second.revision,
                PagePluginState.ABSENT,
            ),
        )
        self.bridge.complete(
            "page-1",
            ReconcileComplete(PROTOCOL_VERSION, command.operation_id, True),
        )

        self.assertEqual(self.runtime.fibers, ())
        self.assertEqual(dict(self.clients.snapshot()), {})
        self.assertEqual(dict(self.bridge.page_snapshot("page-1")), {})
        disabled = await self._call(second.revision, "call-disabled")
        self.assertEqual(disabled.error_code, "stale_client")


if __name__ == "__main__":
    unittest.main()
