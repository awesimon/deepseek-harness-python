"""In-memory lifecycle harnesses for plugins built with the public SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from types import TracebackType
from typing import Any, Self

from harness.agent.values import JsonValue, freeze_json
from harness.bridge import (
    BRIDGE_EVENT_REGISTRY,
    BRIDGE_RPC_REGISTRY,
    BROWSER_BRIDGE,
    PROTOCOL_VERSION,
    BridgeEvent,
    BridgeEventRegistry,
    BridgeRpcRegistry,
    BrowserBridge,
    RpcCall,
    RpcResult,
)
from harness.cordis import Cordis, EffectHandle, Fiber, PluginSpec, ServiceKey
from harness.plugins import (
    PLUGIN_RUNTIME_IDENTITY,
    ClientArtifactRegistry,
    PluginRuntimeIdentity,
)

from ._protocol import ClientEvent, RpcMethod

__all__ = ["BackendPluginHarness", "FullStackPluginHarness"]

# Fixture Services are heterogeneous; each value type is enforced when plugin code calls require().
type TestServices = Mapping[ServiceKey[Any], Any]

type _RpcHandler = Callable[
    [Mapping[str, JsonValue]],
    JsonValue | Awaitable[JsonValue],
]
type _EventHandler = Callable[[str, JsonValue], None | Awaitable[None]]


class _ObservedRpcRegistry(BridgeRpcRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.active: set[tuple[str, str, str]] = set()

    def register(
        self,
        plugin_id: str,
        revision: str,
        method: str,
        handler: _RpcHandler,
    ) -> Callable[[], None]:
        key = (plugin_id, revision, method)
        dispose_registration = super().register(plugin_id, revision, method, handler)
        self.active.add(key)
        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            try:
                dispose_registration()
            finally:
                self.active.discard(key)

        return dispose


class _ObservedEventRegistry(BridgeEventRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.active: set[tuple[str, str, str]] = set()

    def register(
        self,
        plugin_id: str,
        revision: str,
        name: str,
        handler: _EventHandler,
    ) -> Callable[[], None]:
        key = (plugin_id, revision, name)
        dispose_registration = super().register(plugin_id, revision, name, handler)
        self.active.add(key)
        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            try:
                dispose_registration()
            finally:
                self.active.discard(key)

        return dispose


class BackendPluginHarness:
    """Mount one SDK plugin with synthetic Manager identity and test Services."""

    def __init__(
        self,
        plugin: PluginSpec[None],
        *,
        plugin_id: str,
        revision: str,
        services: TestServices | None = None,
    ) -> None:
        """Create an unstarted test runtime.

        @param plugin: Plugin returned by one public SDK factory.
        @param plugin_id: Synthetic Manager-owned fixture identity.
        @param revision: Synthetic Manager-owned fixture Revision.
        @param services: Values for explicitly declared application Services.
        """
        if not plugin_id or not revision:
            raise ValueError("test plugin identity must not be empty")
        self.runtime = Cordis()
        self.plugin = plugin
        self.plugin_id = plugin_id
        self.revision = revision
        self.services = dict(services or {})
        if PLUGIN_RUNTIME_IDENTITY in self.services:
            raise ValueError("test services must not replace the synthetic plugin identity")
        self._fiber: Fiber | None = None
        self._identity_effect: EffectHandle | None = None
        self._started = False
        self._disposal: asyncio.Task[None] | None = None

    @property
    def fiber(self) -> Fiber:
        """Return the mounted backend Fiber after start."""
        if self._fiber is None:
            raise RuntimeError("plugin harness has not started")
        return self._fiber

    async def start(self) -> Fiber:
        """Provide fixtures and mount the real public PluginSpec once.

        @returns: Mounted Fiber, including its failure diagnostics when setup fails.
        """
        if self._started:
            raise RuntimeError("plugin harness can only start once")
        self._started = True
        try:
            for key, value in self.services.items():
                await self.runtime.root.provide(key, value)
            identity_context = self.runtime.root.isolate(PLUGIN_RUNTIME_IDENTITY)
            self._identity_effect = await identity_context.provide(
                PLUGIN_RUNTIME_IDENTITY,
                PluginRuntimeIdentity(self.plugin_id, self.revision),
            )
            self._fiber = await identity_context.mount(self.plugin, None)
            return self._fiber
        except BaseException as error:
            cleanup_errors = await self._close_runtime()
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "plugin harness start and cleanup failed",
                    [error, *cleanup_errors],
                )
            raise

    async def dispose(self) -> None:
        """Dispose every owned object once and report cleanup diagnostics."""
        if self._disposal is None:
            self._disposal = asyncio.create_task(self._dispose_once())
        await asyncio.shield(self._disposal)

    async def _dispose_once(self) -> None:
        errors: list[BaseException] = []
        if self._fiber is not None:
            try:
                await self._fiber.dispose()
            except BaseException as error:  # noqa: BLE001 -- teardown continues
                errors.append(error)
        try:
            await self._after_plugin_disposed()
        except BaseException as error:  # noqa: BLE001 -- teardown continues
            errors.append(error)
        if self._identity_effect is not None:
            try:
                await self._identity_effect.dispose()
            except BaseException as error:  # noqa: BLE001 -- teardown continues
                errors.append(error)
        errors.extend(await self._close_runtime())
        errors.extend(self._diagnostic_cleanup_errors())
        if errors:
            raise BaseExceptionGroup("plugin harness cleanup failed", errors)

    async def _after_plugin_disposed(self) -> None:
        return None

    async def _close_runtime(self) -> list[BaseException]:
        errors: list[BaseException] = []
        try:
            await self.runtime.close()
        except BaseException as error:  # noqa: BLE001 -- return every teardown failure
            errors.append(error)
        return errors

    def _diagnostic_cleanup_errors(self) -> list[BaseException]:
        errors: list[BaseException] = []
        if self._fiber is not None:
            errors.extend(self._fiber.cleanup_errors)
        errors.extend(self.runtime.root.fiber.cleanup_errors)
        return errors

    async def __aenter__(self) -> Self:
        """Start the harness for an async context manager."""
        await self.start()
        return self

    async def __aexit__(
        self,
        _error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Dispose the harness when leaving an async context manager."""
        await self.dispose()


class FullStackPluginHarness(BackendPluginHarness):
    """Backend harness with one active in-memory browser page and Bridge."""

    def __init__(
        self,
        plugin: PluginSpec[None],
        *,
        plugin_id: str,
        revision: str,
        services: TestServices | None = None,
        page_id: str = "test-page",
    ) -> None:
        """Create a revision-bound full-stack test environment.

        @param plugin: Bridge backend plugin returned by the public SDK factory.
        @param plugin_id: Synthetic reconciliation-owned fixture identity.
        @param revision: Synthetic reconciliation-owned fixture Revision.
        @param services: Values for explicitly declared application Services.
        @param page_id: Synthetic connected browser Page ID.
        """
        if not plugin_id or not revision:
            raise ValueError("test plugin identity must not be empty")
        provided = dict(services or {})
        reserved = (BROWSER_BRIDGE, BRIDGE_RPC_REGISTRY, BRIDGE_EVENT_REGISTRY)
        overlap = tuple(key.name for key in reserved if key in provided)
        if overlap:
            raise ValueError(f"test services replace Bridge fixture(s): {', '.join(overlap)}")
        self.rpc = _ObservedRpcRegistry()
        self.events = _ObservedEventRegistry()
        self.bridge = BrowserBridge(ClientArtifactRegistry(), self.rpc, self.events)
        self.page_id = page_id
        self._emitted_events: list[BridgeEvent] = []
        self._next_call_id = 1
        self.bridge.connect(page_id, {plugin_id: revision})
        self.bridge.attach_page_events(page_id, self._emitted_events.append)
        provided[BROWSER_BRIDGE] = self.bridge
        provided[BRIDGE_RPC_REGISTRY] = self.rpc
        provided[BRIDGE_EVENT_REGISTRY] = self.events
        super().__init__(
            plugin,
            plugin_id=plugin_id,
            revision=revision,
            services=provided,
        )

    @property
    def emitted_events(self) -> tuple[BridgeEvent, ...]:
        """Return backend-to-client Events captured in delivery order."""
        return tuple(self._emitted_events)

    async def call_rpc[ArgumentsT: Mapping[str, JsonValue], ResultT: JsonValue](
        self,
        method: RpcMethod[ArgumentsT, ResultT],
        arguments: ArgumentsT,
        *,
        call_id: str | None = None,
    ) -> RpcResult:
        """Invoke one registered method through BrowserBridge authorization.

        @param method: RPC descriptor used by plugin code.
        @param arguments: JSON-compatible RPC arguments.
        @param call_id: Optional deterministic fixture call identifier.
        @returns: Structured Bridge RPC success or failure.
        """
        if call_id is None:
            call_id = f"test-call-{self._next_call_id}"
            self._next_call_id += 1
        return await self.bridge.call(
            RpcCall(
                PROTOCOL_VERSION,
                self.page_id,
                call_id,
                self.plugin_id,
                self.revision,
                method.name,
                arguments,
            )
        )

    def cancel_rpc(self, call_id: str) -> bool:
        """Request cancellation of one active fixture RPC call.

        @param call_id: Call identifier passed to ``call_rpc``.
        @returns: Whether an active call was found.
        """
        return self.bridge.cancel(self.page_id, call_id)

    async def send_client_event[PayloadT: JsonValue](
        self,
        event: ClientEvent[PayloadT],
        payload: PayloadT,
    ) -> None:
        """Send one client Event through BrowserBridge authorization.

        @param event: Client-to-backend Event descriptor.
        @param payload: JSON-compatible Event payload.
        """
        await self.bridge.receive_event(
            BridgeEvent(
                PROTOCOL_VERSION,
                self.page_id,
                self.plugin_id,
                self.revision,
                event.name,
                freeze_json(payload),
            )
        )

    def assert_no_registrations(self) -> None:
        """Raise when any SDK RPC or Event registration remains active."""
        active = sorted((*self.rpc.active, *self.events.active))
        if active:
            raise AssertionError(f"Bridge registrations remain active: {active!r}")

    async def _after_plugin_disposed(self) -> None:
        self.bridge.disconnect(self.page_id)
