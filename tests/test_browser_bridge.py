"""Transport-independent Browser Bridge state and RPC tests."""

from __future__ import annotations

import asyncio
import unittest

from deepseek_harness.bridge import (
    PROTOCOL_VERSION,
    BridgeRpcRegistry,
    BrowserBridge,
    PagePluginState,
    PluginLoadResult,
    RpcCall,
    StaleBridgeMessageError,
)
from deepseek_harness.plugins import ClientArtifactRegistry


class BrowserBridgeTests(unittest.IsolatedAsyncioTestCase):
    """Exercise reconciliation identity, exact bundles, RPC, and cancellation."""

    async def asyncSetUp(self) -> None:
        self.clients = ClientArtifactRegistry()
        self.rpc = BridgeRpcRegistry()
        self.bridge = BrowserBridge(self.clients, self.rpc)

    async def test_reconcile_reports_complete_graph_and_rejects_stale_result(self) -> None:
        """A newer operation makes older page results observational only."""
        publication = self.clients.publish("com.example.client", "rev-1", b"bundle")
        first = self.bridge.connect("page-1", {})
        self.assertEqual([item.plugin_id for item in first.desired], ["com.example.client"])
        second = self.bridge.reconcile("page-1")

        with self.assertRaises(StaleBridgeMessageError):
            self.bridge.report(
                "page-1",
                PluginLoadResult(
                    PROTOCOL_VERSION,
                    first.operation_id,
                    "com.example.client",
                    "rev-1",
                    PagePluginState.ACTIVE,
                ),
            )
        self.bridge.report(
            "page-1",
            PluginLoadResult(
                PROTOCOL_VERSION,
                second.operation_id,
                "com.example.client",
                "rev-1",
                PagePluginState.ACTIVE,
            ),
        )
        self.assertIs(
            self.bridge.page_snapshot("page-1")["com.example.client"].state,
            PagePluginState.ACTIVE,
        )
        publication.dispose()

    async def test_bundle_requires_exact_current_revision(self) -> None:
        """Disable makes the old content-addressed bundle unavailable."""
        publication = self.clients.publish("com.example.client", "rev-1", b"bundle")
        self.assertEqual(self.bridge.bundle("com.example.client", "rev-1"), b"bundle")
        publication.dispose()
        with self.assertRaises(LookupError):
            self.bridge.bundle("com.example.client", "rev-1")

    async def test_rpc_requires_active_same_revision_and_disposable_handler(self) -> None:
        """Page state and handler identity jointly authorize package-private RPC."""
        self.clients.publish("com.example.client", "rev-1", b"bundle")
        operation = self.bridge.connect("page-1", {"com.example.client": "rev-1"})
        self.bridge.report(
            "page-1",
            PluginLoadResult(
                PROTOCOL_VERSION,
                operation.operation_id,
                "com.example.client",
                "rev-1",
                PagePluginState.ACTIVE,
            ),
        )
        dispose = self.rpc.register(
            "com.example.client",
            "rev-1",
            "echo",
            lambda arguments: {"echo": arguments["value"]},
        )
        call = RpcCall(
            PROTOCOL_VERSION,
            "page-1",
            "call-1",
            "com.example.client",
            "rev-1",
            "echo",
            {"value": "hello"},
        )
        result = await self.bridge.call(call)
        self.assertIsNone(result.error_code)
        self.assertEqual(result.result["echo"], "hello")

        dispose()
        unavailable = await self.bridge.call(call)
        self.assertEqual(unavailable.error_code, "method_unavailable")

    async def test_disconnect_cancels_active_rpc_and_removes_page(self) -> None:
        """Disconnect cancels work owned by the logical page connection."""
        self.clients.publish("com.example.client", "rev-1", b"bundle")
        self.bridge.connect("page-1", {"com.example.client": "rev-1"})
        started = asyncio.Event()

        async def wait(_arguments):
            started.set()
            await asyncio.Event().wait()

        self.rpc.register("com.example.client", "rev-1", "wait", wait)
        call = RpcCall(
            PROTOCOL_VERSION,
            "page-1",
            "call-1",
            "com.example.client",
            "rev-1",
            "wait",
            {},
        )
        task = asyncio.create_task(self.bridge.call(call))
        await started.wait()
        self.bridge.disconnect("page-1")
        result = await task
        self.assertEqual(result.error_code, "cancelled")
        with self.assertRaises(LookupError):
            self.bridge.page_snapshot("page-1")
