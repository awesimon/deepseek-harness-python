"""Lifecycle-owned backend plugin authoring API."""

# SDK factories are the sole callers of identity-bearing private constructors.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, cast

from harness.agent.values import JsonValue, freeze_json, freeze_json_object
from harness.bridge import (
    BRIDGE_EVENT_REGISTRY,
    BRIDGE_RPC_REGISTRY,
    BROWSER_BRIDGE,
    BridgeEventRegistry,
    BridgeRpcRegistry,
    BrowserBridge,
)
from harness.cordis import Context, FiberState, InactiveContextError, PluginSpec, ServiceKey
from harness.cordis.types import CleanupResult
from harness.plugins import PLUGIN_RUNTIME_IDENTITY, PluginRuntimeIdentity

from ._protocol import ClientEvent, RpcMethod, ServerEvent

type BackendSetup = Callable[
    [BackendPluginContext],
    CleanupResult | Awaitable[CleanupResult],
]
type BridgeBackendSetup = Callable[
    [BridgeBackendPluginContext],
    CleanupResult | Awaitable[CleanupResult],
]
type RpcHandler[ArgumentsT, ResultT: JsonValue] = Callable[
    [ArgumentsT],
    ResultT | Awaitable[ResultT],
]
type ClientEventHandler[PayloadT: JsonValue] = Callable[
    [str, PayloadT],
    None | Awaitable[None],
]

# Dependency tuples are heterogeneous, so Python cannot preserve each ServiceKey value type.
type ServiceDependency = ServiceKey[Any]


class BackendPluginContext:
    """Read-only author view of one Manager-owned backend activation."""

    __slots__ = ("_cordis", "_identity")
    _cordis: Context
    _identity: PluginRuntimeIdentity

    def __init__(self) -> None:
        """Prevent plugins from constructing an identity-bearing context."""
        raise TypeError("BackendPluginContext is created by define_backend_plugin")

    @classmethod
    def _create(cls, cordis: Context, identity: PluginRuntimeIdentity) -> BackendPluginContext:
        instance = object.__new__(cls)
        instance._cordis = cordis
        instance._identity = identity
        return instance

    @property
    def cordis(self) -> Context:
        """Return the active PyCordis Context for declared Services and Effects."""
        return self._cordis

    @property
    def plugin_id(self) -> str:
        """Return the Manager-injected Plugin ID for diagnostics and application data."""
        return self._identity.plugin_id

    @property
    def revision(self) -> str:
        """Return the Manager-computed content Revision."""
        return self._identity.revision


class BridgeBackendPluginContext(BackendPluginContext):
    """Read-only backend author view with one revision-bound Bridge channel."""

    __slots__ = ("_channel",)
    _channel: BackendPluginChannel

    def __init__(self) -> None:
        """Prevent plugins from constructing an identity-bearing context."""
        raise TypeError("BridgeBackendPluginContext is created by define_bridge_backend_plugin")

    @classmethod
    def _create_bridge(
        cls,
        cordis: Context,
        identity: PluginRuntimeIdentity,
        channel: BackendPluginChannel,
    ) -> BridgeBackendPluginContext:
        instance = object.__new__(cls)
        instance._cordis = cordis
        instance._identity = identity
        instance._channel = channel
        return instance

    @property
    def channel(self) -> BackendPluginChannel:
        """Return the Bridge channel bound to this activation's identity."""
        return self._channel


class BackendPluginChannel:
    """Effect-owned RPC and Event operations for one injected plugin identity."""

    __slots__ = ("_bridge", "_cordis", "_events", "_identity", "_rpc")
    _bridge: BrowserBridge
    _cordis: Context
    _events: BridgeEventRegistry
    _identity: PluginRuntimeIdentity
    _rpc: BridgeRpcRegistry

    def __init__(self) -> None:
        """Prevent plugins from constructing or retargeting a channel."""
        raise TypeError("BackendPluginChannel is created by define_bridge_backend_plugin")

    @classmethod
    def _create(
        cls,
        cordis: Context,
        identity: PluginRuntimeIdentity,
        bridge: BrowserBridge,
        rpc: BridgeRpcRegistry,
        events: BridgeEventRegistry,
    ) -> BackendPluginChannel:
        instance = object.__new__(cls)
        instance._cordis = cordis
        instance._identity = identity
        instance._bridge = bridge
        instance._rpc = rpc
        instance._events = events
        return instance

    async def register_rpc[ArgumentsT: Mapping[str, JsonValue], ResultT: JsonValue](
        self,
        method: RpcMethod[ArgumentsT, ResultT],
        handler: RpcHandler[ArgumentsT, ResultT],
    ) -> None:
        """Register one RPC method as an Effect of the active backend Fiber.

        @param method: Direction-safe RPC descriptor.
        @param handler: Callback receiving immutable JSON-compatible arguments.
        """

        async def invoke(arguments: Mapping[str, JsonValue]) -> JsonValue:
            immutable = freeze_json_object(arguments)
            result = handler(cast(ArgumentsT, immutable))
            if inspect.isawaitable(result):
                result = await result
            return freeze_json(result)

        identity = self._identity
        await self._cordis.effect(
            lambda: self._rpc.register(identity.plugin_id, identity.revision, method.name, invoke),
            f"sdk:bridge-rpc:{method.name}",
        )

    async def on_client_event[PayloadT: JsonValue](
        self,
        event: ClientEvent[PayloadT],
        handler: ClientEventHandler[PayloadT],
    ) -> None:
        """Register one client Event handler as an Effect of the backend Fiber.

        @param event: Client-to-backend Event descriptor.
        @param handler: Callback receiving source Page ID and immutable payload.
        """

        async def invoke(page_id: str, payload: JsonValue) -> None:
            immutable = freeze_json(payload)
            result = handler(page_id, cast(PayloadT, immutable))
            if inspect.isawaitable(result):
                await result

        identity = self._identity
        await self._cordis.effect(
            lambda: self._events.register(
                identity.plugin_id,
                identity.revision,
                event.name,
                invoke,
            ),
            f"sdk:bridge-event:{event.name}",
        )

    async def emit_client_event[PayloadT: JsonValue](
        self,
        event: ServerEvent[PayloadT],
        payload: PayloadT,
        *,
        page_id: str | None = None,
    ) -> int:
        """Send one validated Event to active matching client Revisions.

        @param event: Backend-to-client Event descriptor.
        @param payload: JSON-compatible Event payload.
        @param page_id: Optional exact target Page ID.
        @returns: Number of matching pages that received the Event.
        """
        self._ensure_active()
        identity = self._identity
        return await self._bridge.emit_event(
            identity.plugin_id,
            identity.revision,
            event.name,
            freeze_json(payload),
            page_id=page_id,
        )

    def _ensure_active(self) -> None:
        state = self._cordis.fiber.state
        if state not in (FiberState.LOADING, FiberState.ACTIVE):
            raise InactiveContextError(
                f"cannot use Bridge channel in {self._cordis.fiber.name!r} while {state.value}"
            )


def _resolve_dependencies(
    name: str,
    requires: Iterable[ServiceDependency],
    implicit: tuple[ServiceDependency, ...],
) -> tuple[ServiceDependency, ...]:
    author = tuple(requires)
    if len(set(author)) != len(author):
        raise ValueError(f"plugin {name!r} declares an author service more than once")
    overlap = tuple(key for key in author if key in implicit)
    if overlap:
        names = ", ".join(repr(key.name) for key in overlap)
        raise ValueError(f"plugin {name!r} explicitly declares SDK-owned service(s): {names}")
    return author + implicit


def define_backend_plugin(
    setup: BackendSetup,
    *,
    requires: Iterable[ServiceDependency] = (),
    name: str = "plugin-backend",
) -> PluginSpec[None]:
    """Define a backend-only plugin using Manager-injected identity.

    @param setup: Author lifecycle callback receiving a read-only SDK context.
    @param requires: Explicit application Service dependencies.
    @param name: Diagnostic Fiber name with no package or wire meaning.
    @returns: Mountable PyCordis plugin specification.
    """
    dependencies = _resolve_dependencies(name, requires, (PLUGIN_RUNTIME_IDENTITY,))

    async def apply(cordis: Context, _config: None) -> CleanupResult:
        identity = cordis.require(PLUGIN_RUNTIME_IDENTITY)
        result = setup(BackendPluginContext._create(cordis, identity))
        if inspect.isawaitable(result):
            result = await result
        return cast(CleanupResult, result)

    return PluginSpec(name, apply, requires=dependencies)


def define_bridge_backend_plugin(
    setup: BridgeBackendSetup,
    *,
    requires: Iterable[ServiceDependency] = (),
    name: str = "plugin-backend",
) -> PluginSpec[None]:
    """Define a backend plugin with revision-bound Browser Bridge operations.

    @param setup: Author lifecycle callback receiving a read-only Bridge SDK context.
    @param requires: Explicit application Service dependencies.
    @param name: Diagnostic Fiber name with no package or wire meaning.
    @returns: Mountable PyCordis plugin specification.
    """
    implicit = (
        PLUGIN_RUNTIME_IDENTITY,
        BROWSER_BRIDGE,
        BRIDGE_RPC_REGISTRY,
        BRIDGE_EVENT_REGISTRY,
    )
    dependencies = _resolve_dependencies(name, requires, implicit)

    async def apply(cordis: Context, _config: None) -> CleanupResult:
        identity = cordis.require(PLUGIN_RUNTIME_IDENTITY)
        channel = BackendPluginChannel._create(
            cordis,
            identity,
            cordis.require(BROWSER_BRIDGE),
            cordis.require(BRIDGE_RPC_REGISTRY),
            cordis.require(BRIDGE_EVENT_REGISTRY),
        )
        context = BridgeBackendPluginContext._create_bridge(cordis, identity, channel)
        result = setup(context)
        if inspect.isawaitable(result):
            result = await result
        return cast(CleanupResult, result)

    return PluginSpec(name, apply, requires=dependencies)
