"""aiohttp HTTP and WebSocket adapter for the transport-independent Browser Bridge."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import cast

from aiohttp import WSMsgType, web

from .codec import BridgeProtocolError, decode_frame, encode_frame
from .host import BrowserBridge, StaleBridgeMessageError
from .protocol import (
    BridgeEvent,
    BridgeFrame,
    Hello,
    PluginLoadResult,
    ReconcileComplete,
    RpcCall,
    RpcCancel,
)


@dataclass(slots=True)
class _Connection:
    page_id: str
    generation: int
    socket: web.WebSocketResponse
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reconcile_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reconcile_pending: bool = False
    reconcile_dirty: bool = False
    tasks: set[asyncio.Task[None]] = field(default_factory=lambda: set[asyncio.Task[None]]())
    dispose_events: Callable[[], None] | None = None

    async def send(self, frame: BridgeFrame) -> None:
        """Serialize outbound frames for this logical connection."""
        async with self.send_lock:
            await self.socket.send_json(encode_frame(frame))


class BrowserBridgeTransport:
    """Application-scoped HTTP/WebSocket exposure for one BrowserBridge."""

    def __init__(self, bridge: BrowserBridge) -> None:
        self.bridge = bridge
        self._connections: dict[str, _Connection] = {}
        self._dispose_watch = bridge.clients.watch(self._publication_changed)

    def create_app(self) -> web.Application:
        """Create an aiohttp application with artifact and Bridge routes."""
        app = web.Application()
        app.router.add_get(
            "/plugins/{plugin_id}/{revision}/client.js",
            self._bundle,
        )
        app.router.add_get(
            "/plugins/{plugin_id}/{revision}/protocol.json",
            self._protocol_schema,
        )
        app.router.add_get("/bridge", self._websocket)
        app.on_shutdown.append(self._shutdown)
        return app

    async def _bundle(self, request: web.Request) -> web.Response:
        plugin_id = request.match_info["plugin_id"]
        revision = request.match_info["revision"]
        try:
            content = self.bridge.bundle(plugin_id, revision)
            digest = self.bridge.clients.bundle_digest(plugin_id, revision)
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        return web.Response(
            body=content,
            content_type="text/javascript",
            headers=self._artifact_headers(digest),
        )

    async def _protocol_schema(self, request: web.Request) -> web.Response:
        plugin_id = request.match_info["plugin_id"]
        revision = request.match_info["revision"]
        try:
            content = self.bridge.protocol_schema(plugin_id, revision)
        except LookupError as error:
            raise web.HTTPNotFound(text=str(error)) from error
        return web.Response(
            body=content,
            content_type="application/schema+json",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        connection: _Connection | None = None
        try:
            async for message in socket:
                if message.type is not WSMsgType.TEXT:
                    if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                    await socket.close(code=1003, message=b"Bridge frames must be JSON text")
                    break
                try:
                    raw: object = json.loads(cast(str, message.data))
                    frame = decode_frame(raw)
                    if connection is None:
                        if not isinstance(frame, Hello):
                            raise BridgeProtocolError("hello_required", "first frame must be hello")
                        connection = await self._connect(socket, frame)
                    else:
                        await self._receive(connection, frame)
                except (BridgeProtocolError, StaleBridgeMessageError, LookupError) as error:
                    await socket.close(code=1008, message=str(error).encode("utf-8")[:120])
                    break
        finally:
            if connection is not None and self._connections.get(connection.page_id) is connection:
                del self._connections[connection.page_id]
                if connection.dispose_events is not None:
                    connection.dispose_events()
                self.bridge.disconnect(
                    connection.page_id,
                    generation=connection.generation,
                )
                if connection.tasks:
                    await asyncio.gather(*connection.tasks, return_exceptions=True)
        return socket

    async def _connect(self, socket: web.WebSocketResponse, hello: Hello) -> _Connection:
        previous = self._connections.get(hello.page_id)
        if previous is not None and previous.dispose_events is not None:
            previous.dispose_events()
        command = self.bridge.connect(hello.page_id, hello.loaded)
        generation = self.bridge.page_generation(hello.page_id)
        connection = _Connection(
            hello.page_id,
            generation,
            socket,
            reconcile_pending=True,
        )
        self._connections[hello.page_id] = connection
        connection.dispose_events = self.bridge.attach_page_events(
            hello.page_id,
            connection.send,
            generation=generation,
        )
        if previous is not None:
            await previous.socket.close(code=1000, message=b"page connection replaced")
        await connection.send(command)
        return connection

    async def _receive(self, connection: _Connection, frame: BridgeFrame) -> None:
        self._require_current(connection)
        if isinstance(frame, PluginLoadResult):
            self.bridge.report(
                connection.page_id,
                frame,
                generation=connection.generation,
            )
            return
        if isinstance(frame, ReconcileComplete):
            await self._complete_reconcile(connection, frame)
            return
        if isinstance(frame, RpcCall):
            self._require_page(connection, frame.page_id)
            self._start_task(connection, self._run_rpc(connection, frame))
            return
        if isinstance(frame, RpcCancel):
            self._require_page(connection, frame.page_id)
            self.bridge.cancel(frame.page_id, frame.call_id)
            return
        if isinstance(frame, BridgeEvent):
            self._require_page(connection, frame.page_id)
            await self.bridge.receive_event(frame)
            return
        raise BridgeProtocolError("unexpected_frame", "frame is not valid from a browser page")

    async def _run_rpc(self, connection: _Connection, call: RpcCall) -> None:
        result = await self.bridge.call(call)
        if self._connections.get(connection.page_id) is connection and not connection.socket.closed:
            await connection.send(result)

    def _publication_changed(self) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Publication before the application loop starts is included in the first hello graph.
            return
        for connection in tuple(self._connections.values()):
            self._start_task(connection, self._request_reconcile(connection))

    async def _request_reconcile(self, connection: _Connection) -> None:
        async with connection.reconcile_lock:
            if self._connections.get(connection.page_id) is not connection:
                return
            if connection.reconcile_pending:
                connection.reconcile_dirty = True
                return
            connection.reconcile_pending = True
            await connection.send(
                self.bridge.reconcile(
                    connection.page_id,
                    generation=connection.generation,
                )
            )

    async def _complete_reconcile(
        self,
        connection: _Connection,
        frame: ReconcileComplete,
    ) -> None:
        self.bridge.complete(
            connection.page_id,
            frame,
            generation=connection.generation,
        )
        async with connection.reconcile_lock:
            connection.reconcile_pending = False
            if (
                not connection.reconcile_dirty
                or self._connections.get(connection.page_id) is not connection
            ):
                return
            connection.reconcile_dirty = False
            connection.reconcile_pending = True
            await connection.send(
                self.bridge.reconcile(
                    connection.page_id,
                    generation=connection.generation,
                )
            )

    def _start_task(
        self,
        connection: _Connection,
        operation: Coroutine[object, object, None],
    ) -> None:
        task = asyncio.create_task(operation)
        connection.tasks.add(task)
        task.add_done_callback(connection.tasks.discard)

    @staticmethod
    def _require_page(connection: _Connection, page_id: str) -> None:
        if page_id != connection.page_id:
            raise StaleBridgeMessageError("frame Page ID does not own this connection")

    def _require_current(self, connection: _Connection) -> None:
        if self._connections.get(connection.page_id) is not connection:
            raise StaleBridgeMessageError("page connection generation was replaced")
        if self.bridge.page_generation(connection.page_id) != connection.generation:
            raise StaleBridgeMessageError("page connection generation was replaced")

    @staticmethod
    def _artifact_headers(digest: str) -> dict[str, str]:
        return {
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"sha256-{digest}"',
            "X-Content-SHA256": digest,
        }

    async def _shutdown(self, _app: web.Application) -> None:
        self._dispose_watch()
        sockets = tuple(connection.socket for connection in self._connections.values())
        if sockets:
            await asyncio.gather(
                *(
                    socket.close(code=1001, message=b"Bridge transport stopping")
                    for socket in sockets
                )
            )


def create_bridge_app(bridge: BrowserBridge) -> web.Application:
    """Create the default aiohttp application for one BrowserBridge."""
    return BrowserBridgeTransport(bridge).create_app()
