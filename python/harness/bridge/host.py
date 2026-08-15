"""Transport-independent page reconciliation and package-private RPC."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from harness.agent.values import JsonValue, freeze_json
from harness.plugins import ClientArtifactRegistry
from harness.plugins.client_activation import (
    ClientActivationAggregator,
    ClientPageObservation,
)

from .events import BridgeEventRegistry, EventSink
from .protocol import (
    PROTOCOL_VERSION,
    BridgeEvent,
    DesiredClient,
    PagePluginState,
    PluginLoadResult,
    ReconcileCommand,
    ReconcileComplete,
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
    inventory_only: bool = False


@dataclass(slots=True)
class _Page:
    generation: int
    operation: int
    plugins: dict[str, PagePluginSnapshot]
    desired: dict[str, str]
    completed_operation: str | None = None
    completion_error: str | None = None


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
        events: BridgeEventRegistry | None = None,
        aggregation: ClientActivationAggregator | None = None,
    ) -> None:
        self.clients = clients
        self.rpc = rpc or BridgeRpcRegistry()
        self.events = events or BridgeEventRegistry()
        self.aggregation = aggregation
        self._pages: dict[str, _Page] = {}
        self._calls: dict[tuple[str, str], asyncio.Task[RpcResult]] = {}
        self._next_generation = 0

    def connect(
        self,
        page_id: str,
        loaded: Mapping[str, str],
    ) -> ReconcileCommand:
        """Replace a page connection and return its complete desired graph."""
        if not page_id:
            raise ValueError("page id must not be empty")
        if page_id in self._pages:
            self._disconnect(page_id, notify=False)
        self._next_generation += 1
        published = self.clients.snapshot()
        plugins = {
            plugin_id: PagePluginSnapshot(
                revision,
                PagePluginState.ACTIVE,
                None,
                inventory_only=published.get(plugin_id) != revision,
            )
            for plugin_id, revision in loaded.items()
        }
        page = _Page(self._next_generation, 0, plugins, {})
        self._pages[page_id] = page
        command = self._reconcile(page)
        self._notify_aggregation()
        return command

    def reconcile(
        self,
        page_id: str,
        *,
        generation: int | None = None,
    ) -> ReconcileCommand:
        """Supersede prior work and return the complete desired graph."""
        page = self._page(page_id, generation)
        command = self._reconcile(page)
        self._notify_aggregation()
        return command

    def _reconcile(self, page: _Page) -> ReconcileCommand:
        page.operation += 1
        page.completed_operation = None
        page.completion_error = None
        desired_items: list[DesiredClient] = []
        for plugin_id, revision in self.clients.snapshot().items():
            artifact = self.clients.artifact(plugin_id, revision)
            schema_url = (
                None
                if artifact.protocol_schema is None
                else f"/plugins/{plugin_id}/{revision}/protocol.json"
            )
            desired_items.append(
                DesiredClient(
                    plugin_id,
                    revision,
                    f"/plugins/{plugin_id}/{revision}/client.js",
                    artifact.bundle_sha256,
                    schema_url,
                    artifact.activation_policy,
                )
            )
        desired = tuple(desired_items)
        page.desired = {item.plugin_id: item.revision for item in desired}
        return ReconcileCommand(PROTOCOL_VERSION, str(page.operation), desired)

    def report(
        self,
        page_id: str,
        result: PluginLoadResult,
        *,
        generation: int | None = None,
    ) -> None:
        """Apply a result only for the page's current operation and desired revision."""
        page = self._page(page_id, generation)
        if result.protocol != PROTOCOL_VERSION or result.operation_id != str(page.operation):
            raise StaleBridgeMessageError("stale reconciliation operation")
        if result.state in (PagePluginState.ABSENT, PagePluginState.UNLOADING):
            current = page.plugins.get(result.plugin_id)
            expected = None if current is None else current.revision
        else:
            expected = page.desired.get(result.plugin_id)
        if expected != result.revision:
            raise StaleBridgeMessageError("revision is not authorized by this operation")
        if result.state is PagePluginState.ABSENT:
            page.plugins.pop(result.plugin_id, None)
        else:
            page.plugins[result.plugin_id] = PagePluginSnapshot(
                result.revision,
                result.state,
                result.error,
                inventory_only=False,
            )
        self._notify_aggregation()

    def complete(
        self,
        page_id: str,
        result: ReconcileComplete,
        *,
        generation: int | None = None,
    ) -> None:
        """Record completion only for the page's current reconciliation operation."""
        page = self._page(page_id, generation)
        if result.protocol != PROTOCOL_VERSION or result.operation_id != str(page.operation):
            raise StaleBridgeMessageError("stale reconciliation completion")
        page.completed_operation = result.operation_id
        page.completion_error = result.error
        self._notify_aggregation()

    def page_snapshot(
        self,
        page_id: str,
        *,
        generation: int | None = None,
    ) -> Mapping[str, PagePluginSnapshot]:
        """Return immutable page-local plugin state."""
        return MappingProxyType(dict(self._page(page_id, generation).plugins))

    def page_generation(self, page_id: str) -> int:
        """Return the accepted opaque connection generation for a page."""
        return self._page(page_id).generation

    def page_ids(self) -> tuple[str, ...]:
        """Return connected Page IDs in deterministic order."""
        return tuple(sorted(self._pages))

    def bundle(self, plugin_id: str, revision: str) -> bytes:
        """Return exact currently published bundle bytes."""
        if self.clients.current_revision(plugin_id) != revision:
            raise LookupError("client revision is not currently published")
        return self.clients.get(plugin_id, revision)

    def protocol_schema(self, plugin_id: str, revision: str) -> bytes:
        """Return exact plugin protocol Schema bytes for a current Revision."""
        return self.clients.protocol_schema(plugin_id, revision)

    def attach_page_events(
        self,
        page_id: str,
        sender: EventSink,
        *,
        generation: int | None = None,
    ) -> Callable[[], None]:
        """Attach a connection-owned outbound Event sink for a connected page."""
        self._page(page_id, generation)
        return self.events.attach_page(page_id, sender)

    async def receive_event(self, event: BridgeEvent) -> None:
        """Authorize and dispatch one client Event to its backend handler."""
        state = self._active_revision(event.page_id, event.plugin_id)
        if state != event.revision:
            raise StaleBridgeMessageError("page Event revision is not active")
        await self.events.dispatch_backend(event)

    async def emit_event(
        self,
        plugin_id: str,
        revision: str,
        name: str,
        payload: JsonValue,
        *,
        page_id: str | None = None,
    ) -> int:
        """Emit one backend Event to matching active pages and return delivery count."""
        targets = (page_id,) if page_id is not None else tuple(self._pages)
        delivered = 0
        for target in targets:
            if self._active_revision(target, plugin_id) != revision:
                if page_id is not None:
                    raise StaleBridgeMessageError("target page revision is not active")
                continue
            await self.events.dispatch_client(
                BridgeEvent(PROTOCOL_VERSION, target, plugin_id, revision, name, payload)
            )
            delivered += 1
        return delivered

    async def call(self, call: RpcCall) -> RpcResult:
        """Execute one authorized page-local RPC with cancellation tracking."""
        page = self._page(call.page_id)
        state = page.plugins.get(call.plugin_id)
        if (
            state is None
            or state.state is not PagePluginState.ACTIVE
            or state.revision != call.revision
        ):
            return RpcResult(
                PROTOCOL_VERSION,
                call.call_id,
                error_code="stale_client",
                error_message="page revision is not active",
            )
        try:
            handler = self.rpc.resolve(call)
        except LookupError as error:
            return RpcResult(
                PROTOCOL_VERSION,
                call.call_id,
                error_code="method_unavailable",
                error_message=str(error),
            )

        async def invoke() -> RpcResult:
            try:
                value = handler(call.arguments)
                if inspect.isawaitable(value):
                    value = await value
                return RpcResult(PROTOCOL_VERSION, call.call_id, result=freeze_json(value))
            except asyncio.CancelledError:
                return RpcResult(
                    PROTOCOL_VERSION,
                    call.call_id,
                    error_code="cancelled",
                    error_message="RPC call cancelled",
                )
            except Exception as error:  # noqa: BLE001 -- handler failures cross a wire boundary
                return RpcResult(
                    PROTOCOL_VERSION,
                    call.call_id,
                    error_code="handler_error",
                    error_message=str(error),
                )

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

    def disconnect(self, page_id: str, *, generation: int | None = None) -> None:
        """Remove ephemeral page state and cancel its outstanding calls."""
        if generation is not None:
            self._page(page_id, generation)
        self._disconnect(page_id, notify=True)

    def _disconnect(self, page_id: str, *, notify: bool) -> None:
        self._pages.pop(page_id, None)
        self.events.detach_page(page_id)
        for (owner, _call_id), task in tuple(self._calls.items()):
            if owner == page_id:
                task.cancel()
        if notify:
            self._notify_aggregation()

    def _active_revision(self, page_id: str, plugin_id: str) -> str | None:
        state = self._page(page_id).plugins.get(plugin_id)
        if state is None or state.state is not PagePluginState.ACTIVE:
            return None
        return state.revision

    def _page(self, page_id: str, generation: int | None = None) -> _Page:
        try:
            page = self._pages[page_id]
        except KeyError as error:
            raise LookupError(f"page {page_id!r} is not connected") from error
        if generation is not None and page.generation != generation:
            raise StaleBridgeMessageError("page connection generation was replaced")
        return page

    def _notify_aggregation(self) -> None:
        if self.aggregation is None:
            return
        pages: dict[str, tuple[ClientPageObservation, ...]] = {}
        for page_id in sorted(self._pages):
            page = self._pages[page_id]
            plugin_ids = sorted(set(page.plugins) | set(page.desired))
            observations: list[ClientPageObservation] = []
            for plugin_id in plugin_ids:
                plugin = page.plugins.get(plugin_id)
                observations.append(
                    ClientPageObservation(
                        page_id,
                        page.generation,
                        str(page.operation),
                        plugin_id,
                        page.desired.get(plugin_id),
                        None if plugin is None else plugin.revision,
                        None if plugin is None else plugin.state.value,
                        None if plugin is None else plugin.error,
                        page.completed_operation == str(page.operation),
                        page.completion_error,
                        False if plugin is None else plugin.inventory_only,
                    )
                )
            if not observations:
                observations.append(
                    ClientPageObservation(
                        page_id,
                        page.generation,
                        str(page.operation),
                        None,
                        None,
                        None,
                        None,
                        None,
                        page.completed_operation == str(page.operation),
                        page.completion_error,
                    )
                )
            pages[page_id] = tuple(observations)
        self.aggregation.observe(MappingProxyType(pages))
