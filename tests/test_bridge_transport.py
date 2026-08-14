"""HTTP/WebSocket Browser Bridge adapter tests."""

from __future__ import annotations

import asyncio
import unittest

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from harness.bridge import (
    BridgeRpcRegistry,
    BrowserBridge,
    create_bridge_app,
)
from harness.plugins import ClientArtifactRegistry


class BridgeTransportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise exact artifacts, frame routing, replacement, and live reconciliation."""

    async def asyncSetUp(self) -> None:
        self.clients = ClientArtifactRegistry()
        self.rpc = BridgeRpcRegistry()
        self.bridge = BrowserBridge(self.clients, self.rpc)
        self.server = TestServer(create_bridge_app(self.bridge))
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_http_serves_only_exact_current_artifacts(self) -> None:
        """Bundle and protocol responses carry immutable metadata without fallback."""
        publication = self.clients.publish(
            "com.example.client",
            "rev-1",
            b"export default {}",
            protocol_schema=b'{"type":"object"}',
        )
        bundle = await self.client.get("/plugins/com.example.client/rev-1/client.js")
        self.assertEqual(bundle.status, 200)
        self.assertEqual(await bundle.read(), b"export default {}")
        self.assertIn("immutable", bundle.headers["Cache-Control"])
        self.assertEqual(bundle.headers["X-Content-SHA256"], self.clients.bundle_digest("com.example.client", "rev-1"))

        schema = await self.client.get("/plugins/com.example.client/rev-1/protocol.json")
        self.assertEqual(schema.status, 200)
        self.assertEqual(await schema.read(), b'{"type":"object"}')

        publication.dispose()
        stale = await self.client.get("/plugins/com.example.client/rev-1/client.js")
        self.assertEqual(stale.status, 404)

    async def test_websocket_routes_rpc_and_pushes_publication_changes(self) -> None:
        """Hello establishes state while RPC and live graph updates share the connection."""
        self.clients.publish("com.example.client", "rev-1", b"bundle")
        self.rpc.register(
            "com.example.client",
            "rev-1",
            "echo",
            lambda arguments: {"echo": arguments["value"]},
        )
        socket = await self.client.ws_connect("/bridge")
        await socket.send_json(
            {"protocol": "1", "type": "hello", "pageId": "page-1", "loaded": {}}
        )
        reconcile = await socket.receive_json()
        self.assertEqual(reconcile["type"], "reconcile")
        desired = reconcile["desired"][0]
        await socket.send_json(
            {
                "protocol": "1",
                "type": "plugin-result",
                "operationId": reconcile["operationId"],
                "pluginId": desired["pluginId"],
                "revision": desired["revision"],
                "state": "active",
                "error": None,
            }
        )
        await socket.send_json(
            {
                "protocol": "1",
                "type": "reconcile-complete",
                "operationId": reconcile["operationId"],
                "success": True,
                "error": None,
            }
        )
        await socket.send_json(
            {
                "protocol": "1",
                "type": "rpc-call",
                "pageId": "page-1",
                "callId": "call-1",
                "pluginId": "com.example.client",
                "revision": "rev-1",
                "method": "echo",
                "arguments": {"value": "hello"},
            }
        )
        result = await socket.receive_json()
        self.assertEqual(result["result"], {"echo": "hello"})

        self.clients.publish("com.example.second", "rev-2", b"second")
        updated = await socket.receive_json()
        self.assertEqual(updated["type"], "reconcile")
        self.assertEqual(
            [item["pluginId"] for item in updated["desired"]],
            ["com.example.client", "com.example.second"],
        )
        await socket.close()

    async def test_replacement_connection_owns_page_cleanup(self) -> None:
        """Closing a replaced connection cannot remove the replacement page state."""
        first = await self.client.ws_connect("/bridge")
        await first.send_json(
            {"protocol": "1", "type": "hello", "pageId": "page-1", "loaded": {}}
        )
        await first.receive_json()
        second = await self.client.ws_connect("/bridge")
        await second.send_json(
            {"protocol": "1", "type": "hello", "pageId": "page-1", "loaded": {}}
        )
        await second.receive_json()

        closed = await first.receive(timeout=1)
        self.assertIn(closed.type, (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING))
        await asyncio.sleep(0)
        self.assertEqual(dict(self.bridge.page_snapshot("page-1")), {})
        await second.close()
