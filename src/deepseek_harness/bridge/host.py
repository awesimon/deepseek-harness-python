"""Transport-independent page reconciliation and package-private RPC."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from deepseek_harness.agent.values import JsonValue, freeze_json
from deepseek_harness.plugins import ClientArtifactRegistry

from .protocol import (
    PROTOCOL_VERSION,
    DesiredClient,
    PagePluginState,
    PluginLoadResult,
    ReconcileCommand,
    RpcCall,
    RpcResult,
)


class StaleBridgeMessageError(RuntimeError):
    """Raised when a page reports an old operation or revision."""


type RpcHandler = Callable[[Mapping[str, JsonValue]], JsonValue | Awaitable[JsonValue]]


@dataclass(frozen=True, slots=True)
class PagePluginSnapshot:
    """Host-observed state for one page plugin revision."""

    revision: str
    state: PagePluginState
    error: str | None


@dataclass(slots=True)
class _Page:
    operation: int
    plugins: dict[str, PagePluginSnapshot]


class BridgeRpcRegistry:
    """Effect-compatible handlers addressed by Plugin ID and Revision."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str, str], RpcHandler] = {}

    def register(
        self,
        plugin_id: str,
        revision: str,
        method: str,
        handler: RpcHandler,
    ) -> Callable[[], None]:
        """Register one exact method and return an idempotent disposer."""
        key = (plugin_id, revision, method)
        if key in self._handlers:
            raise RuntimeError(f"bridge RPC method is already registered: {key!r}")
        self._handlers[key] = handler
        active = True

        def dispose() -> None:
            nonlocal active
            if active and self._handlers.get(key) is handler:
                del self._handlers[key]
            active = False

        return dispose

    def resolve(self, call: RpcCall) -> RpcHandler:
        """Resolve one exact same-plugin Revision method."""
        try:
            return self._handlers[(call.plugin_id, call.revision, call.method)]
        except KeyError as error:
            raise LookupError("bridge RPC method is unavailable or stale") from error


class BrowserBridge:
    """Host state machine independent of WebSocket or HTTP frameworks."""

    def __init__(
        self,
        clients: ClientArtifactRegistry,
        rpc: BridgeRpcRegistry | None = None,
    ) -> None:
        self.clients = clients
        self.rpc = rpc or BridgeRpcRegistry()
        self._pages: dict[str, _Page] = {}
        self._calls: dict[tuple[str, str], asyncio.Task[RpcResult]] = {}

    def connect(
        self,
        page_id: str,
        loaded: Mapping[str, str],
    ) -> ReconcileCommand:
        """Replace a page connection and return its complete desired graph."""
        if not page_id:
            raise ValueError("page id must not be empty")
        plugins = {
            plugin_id: PagePluginSnapshot(revision, PagePluginState.ACTIVE, None)
            for plugin_id, revision in loaded.items()
        }
        self._pages[page_id] = _Page(0, plugins)
        return self.reconcile(page_id)

    def reconcile(self, page_id: str) -> ReconcileCommand:
        """Supersede prior work and return the complete desired graph."""
        page = self._page(page_id)
        page.operation += 1
        desired = tuple(
            DesiredClient(
                plugin_id,
                revision,
                f"/plugins/{plugin_id}/{revision}/client.js",
                self.clients.bundle_digest(plugin_id, revision),
            )
            for plugin_id, revision in self.clients.snapshot().items()
        )
        return ReconcileCommand(PROTOCOL_VERSION, str(page.operation), desired)

    def report(self, page_id: str, result: PluginLoadResult) -> None:
        """Apply a result only for the page's current operation and desired revision."""
        page = self._page(page_id)
        if result.protocol != PROTOCOL_VERSION or result.operation_id != str(page.operation):
            raise StaleBridgeMessageError("stale reconciliation operation")
        desired = self.clients.current_revision(result.plugin_id)
        if result.state is not PagePluginState.ABSENT and desired != result.revision:
            raise StaleBridgeMessageError("stale or unpublished client revision")
        if result.state is PagePluginState.ABSENT:
            page.plugins.pop(result.plugin_id, None)
        else:
            page.plugins[result.plugin_id] = PagePluginSnapshot(
                result.revision,
                result.state,
                result.error,
            )

    def page_snapshot(self, page_id: str) -> Mapping[str, PagePluginSnapshot]:
        """Return immutable page-local plugin state."""
        return MappingProxyType(dict(self._page(page_id).plugins))

    def bundle(self, plugin_id: str, revision: str) -> bytes:
        """Return exact currently published bundle bytes."""
        if self.clients.current_revision(plugin_id) != revision:
            raise LookupError("client revision is not currently published")
        return self.clients.get(plugin_id, revision)

    async def call(self, call: RpcCall) -> RpcResult:
        """Execute one authorized page-local RPC with cancellation tracking."""
        page = self._page(call.page_id)
        state = page.plugins.get(call.plugin_id)
        if state is None or state.state is not PagePluginState.ACTIVE or state.revision != call.revision:
            return RpcResult(PROTOCOL_VERSION, call.call_id, error_code="stale_client", error_message="page revision is not active")
        try:
            handler = self.rpc.resolve(call)
        except LookupError as error:
            return RpcResult(PROTOCOL_VERSION, call.call_id, error_code="method_unavailable", error_message=str(error))

        async def invoke() -> RpcResult:
            try:
                value = handler(call.arguments)
                if inspect.isawaitable(value):
                    value = await value
                return RpcResult(PROTOCOL_VERSION, call.call_id, result=freeze_json(value))
            except asyncio.CancelledError:
                return RpcResult(PROTOCOL_VERSION, call.call_id, error_code="cancelled", error_message="RPC call cancelled")
            except Exception as error:  # noqa: BLE001 -- handler failures cross a wire boundary
                return RpcResult(PROTOCOL_VERSION, call.call_id, error_code="handler_error", error_message=str(error))

        key = (call.page_id, call.call_id)
        if key in self._calls:
            raise RuntimeError("duplicate active RPC call id")
        task = asyncio.create_task(invoke())
        self._calls[key] = task
        try:
            return await task
        finally:
            self._calls.pop(key, None)

    def cancel(self, page_id: str, call_id: str) -> bool:
        """Best-effort cancel one active RPC task."""
        task = self._calls.get((page_id, call_id))
        if task is None:
            return False
        task.cancel()
        return True

    def disconnect(self, page_id: str) -> None:
        """Remove ephemeral page state and cancel its outstanding calls."""
        self._pages.pop(page_id, None)
        for (owner, _call_id), task in tuple(self._calls.items()):
            if owner == page_id:
                task.cancel()

    def _page(self, page_id: str) -> _Page:
        try:
            return self._pages[page_id]
        except KeyError as error:
            raise LookupError(f"page {page_id!r} is not connected") from error
