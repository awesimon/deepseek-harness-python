"""Dependency-driven PyCordis service and fiber runtime."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar, cast

from .effects import EffectHandle, EffectScope
from .errors import (
    DuplicateServiceError,
    InactiveContextError,
    RuntimeClosedError,
    ServiceUnavailableError,
    UndeclaredDependencyError,
)
from .events import EventBus, EventKey, ResultT
from .types import CleanupResult, EffectSetup, PluginSpec, ServiceKey, ServiceT

ConfigT = TypeVar("ConfigT")


class FiberState(str, Enum):
    """Lifecycle states for one mounted plugin instance."""

    PENDING = "pending"
    LOADING = "loading"
    ACTIVE = "active"
    FAILED = "failed"
    UNLOADING = "unloading"
    DISPOSED = "disposed"


@dataclass(slots=True)
class _ServiceRegistration:
    key: ServiceKey[Any]
    realm: object
    value: Any
    provider: Fiber
    generation: int


class Fiber:
    """One mounted plugin instance and its current activation state."""

    def __init__(
        self,
        runtime: Cordis,
        uid: int,
        spec: PluginSpec[Any] | None,
        config: Any,
        parent: Fiber | None,
        realms: dict[ServiceKey[Any], object],
    ) -> None:
        self.runtime = runtime
        self.uid = uid
        self.spec = spec
        self.raw_config = config
        self.config: Any = None
        self.parent = parent
        self.children: set[Fiber] = set()
        self.state = FiberState.ACTIVE if spec is None else FiberState.PENDING
        self.error: BaseException | None = None
        self.cleanup_errors: list[BaseException] = []
        self.desired = True
        self.epoch: tuple[int, ...] | None = () if spec is None else None
        self.effects = EffectScope()
        self.context = Context(runtime, self, realms)

    @property
    def name(self) -> str:
        """Return the plugin's diagnostic name."""
        return "root" if self.spec is None else self.spec.name

    async def dispose(self) -> None:
        """Dispose this fiber and every child contribution."""
        await self.runtime.unmount(self)

    async def retry(self) -> None:
        """Retry a failed activation without waiting for dependency replacement."""
        await self.runtime.retry(self)

    @property
    def effect_labels(self) -> tuple[str, ...]:
        """Return labels for effects owned by the current activation."""
        return self.effects.labels


class Context:
    """A fiber-owned view over services, effects, events, and isolation realms."""

    def __init__(
        self,
        runtime: Cordis,
        fiber: Fiber,
        realms: dict[ServiceKey[Any], object],
    ) -> None:
        self.runtime = runtime
        self.fiber = fiber
        self._realms = realms

    def isolate(self, *keys: ServiceKey[Any], label: object | None = None) -> Context:
        """Return a child context with fresh or explicitly shared service realms."""
        if not keys:
            raise ValueError("isolate requires at least one service key")
        realm = object() if label is None else label
        realms = dict(self._realms)
        for key in keys:
            realms[key] = realm
        return Context(self.runtime, self.fiber, realms)

    def realm(self, key: ServiceKey[Any]) -> object:
        """Resolve the opaque realm token for a service key."""
        return self._realms.get(key, self.runtime._root_realm(key))

    def require(self, key: ServiceKey[ServiceT]) -> ServiceT:
        """Resolve a declared dependency or a service provided by this fiber."""
        if not self.runtime._is_declared_or_owned(self, key):
            raise UndeclaredDependencyError(
                f"plugin {self.fiber.name!r} did not declare service {key.name!r}"
            )
        registration = self.runtime._resolve(self, key)
        if registration is None:
            raise ServiceUnavailableError(
                f"service {key.name!r} is unavailable in plugin {self.fiber.name!r}"
            )
        return cast(ServiceT, registration.value)

    def lookup(self, key: ServiceKey[ServiceT]) -> ServiceT | None:
        """Reflectively resolve an active service without dependency enforcement."""
        registration = self.runtime._resolve(self, key)
        return None if registration is None else cast(ServiceT, registration.value)

    async def effect(self, setup: EffectSetup, label: str = "anonymous") -> EffectHandle:
        """Create an effect owned by the current fiber activation."""
        if self.fiber.state not in (FiberState.LOADING, FiberState.ACTIVE):
            raise InactiveContextError(
                f"cannot create effect in {self.fiber.name!r} while {self.fiber.state.value}"
            )
        return await self.fiber.effects.add(setup, label)

    async def provide(
        self,
        key: ServiceKey[ServiceT],
        value: ServiceT,
    ) -> EffectHandle:
        """Provide one service in this context's realm as an owned effect."""
        registration: _ServiceRegistration | None = None

        def setup() -> Callable[[], Any]:
            nonlocal registration
            registration = self.runtime._provide(self, key, value)

            async def cleanup() -> None:
                assert registration is not None
                self.runtime._remove_service(registration)
                await self.runtime._after_mutation()

            return cleanup

        handle = await self.effect(setup, f"provide:{key.name}")
        await self.runtime._after_mutation()
        return handle

    async def on(
        self,
        event: EventKey[Any],
        listener: Callable[..., Any],
        *,
        prepend: bool = False,
    ) -> EffectHandle:
        """Register an effect-owned event listener."""

        def setup() -> Callable[[], None]:
            return self.runtime.events.register(event, listener, prepend=prepend)

        return await self.effect(setup, f"on:{event.name}")

    async def mount(self, spec: PluginSpec[ConfigT], config: ConfigT) -> Fiber:
        """Mount a child plugin inheriting this context's service realms."""
        return await self.runtime.mount(spec, config, context=self)

    def emit(self, event: EventKey[None], *args: Any) -> None:
        """Dispatch a synchronous event."""
        self.runtime.events.emit(event, *args)

    async def parallel(self, event: EventKey[None], *args: Any) -> None:
        """Dispatch an awaited parallel event."""
        await self.runtime.events.parallel(event, *args)

    async def serial(self, event: EventKey[ResultT], *args: Any) -> ResultT | None:
        """Dispatch an awaited serial bail event."""
        return await self.runtime.events.serial(event, *args)

    async def waterfall(
        self,
        event: EventKey[ResultT],
        *args: Any,
        terminal: Callable[..., ResultT | Awaitable[ResultT]],
    ) -> ResultT:
        """Dispatch an around-middleware waterfall event."""
        return await self.runtime.events.waterfall(event, *args, terminal=terminal)


class Cordis:
    """Root PyCordis runtime and dependency graph owner."""

    def __init__(self) -> None:
        self.events = EventBus()
        self._realms: dict[ServiceKey[Any], object] = {}
        self._services: dict[tuple[ServiceKey[Any], object], _ServiceRegistration] = {}
        self._fibers: list[Fiber] = []
        self._next_uid = 1
        self._next_generation = 1
        self._closed = False
        self._dirty = False
        self._converge_lock = asyncio.Lock()
        self._inside_convergence = ContextVar(
            f"pycordis_inside_convergence_{id(self)}",
            default=False,
        )
        self._root = Fiber(self, 0, None, None, None, {})
        self.root = self._root.context

    @property
    def fibers(self) -> tuple[Fiber, ...]:
        """Return currently mounted non-root fibers in mount order."""
        return tuple(self._fibers)

    def _root_realm(self, key: ServiceKey[Any]) -> object:
        return self._realms.setdefault(key, object())

    async def mount(
        self,
        spec: PluginSpec[ConfigT],
        config: ConfigT,
        *,
        context: Context | None = None,
    ) -> Fiber:
        """Mount a plugin under a context and converge its dependencies."""
        if self._closed:
            raise RuntimeClosedError("cannot mount a plugin after runtime close")
        parent_context = self.root if context is None else context
        if parent_context.runtime is not self:
            raise ValueError("cannot mount a context from another runtime")
        parent = parent_context.fiber
        fiber = Fiber(
            self,
            self._next_uid,
            spec,
            config,
            parent,
            dict(parent_context._realms),
        )
        self._next_uid += 1
        parent.children.add(fiber)
        self._fibers.append(fiber)
        await self._after_mutation()
        return fiber

    async def unmount(self, fiber: Fiber) -> None:
        """Request recursive disposal and wait for graph convergence."""
        if fiber.runtime is not self:
            raise ValueError("cannot unmount a fiber from another runtime")
        if fiber is self._root:
            await self.close()
            return
        self._mark_undesired(fiber)
        await self._after_mutation()

    async def retry(self, fiber: Fiber) -> None:
        """Move a failed fiber back to pending and converge it."""
        if fiber.runtime is not self:
            raise ValueError("cannot retry a fiber from another runtime")
        if fiber.state is FiberState.FAILED and fiber.desired:
            fiber.state = FiberState.PENDING
            fiber.error = None
            fiber.epoch = None
            await self._after_mutation()

    async def close(self) -> None:
        """Dispose every plugin and root-owned effect."""
        if self._closed:
            return
        for child in tuple(self._root.children):
            self._mark_undesired(child)
        await self._after_mutation()
        try:
            await self._root.effects.close()
        except BaseException as error:
            self._root.cleanup_errors.append(error)
        self._closed = True

    def _mark_undesired(self, fiber: Fiber) -> None:
        fiber.desired = False
        for child in tuple(fiber.children):
            self._mark_undesired(child)
        self._dirty = True

    async def _after_mutation(self) -> None:
        self._dirty = True
        # Top-level effect disposal runs in child tasks. Those tasks inherit the
        # active convergence operation but cannot reacquire its lock; marking
        # the graph dirty is sufficient because the owner resumes after cleanup.
        if self._inside_convergence.get():
            return
        await self._converge()

    async def _converge(self) -> None:
        async with self._converge_lock:
            token = self._inside_convergence.set(True)
            try:
                while self._dirty:
                    self._dirty = False
                    changed = await self._deactivate_invalid_fibers()
                    changed = await self._dispose_undesired_fibers() or changed
                    changed = await self._activate_one_pending_fiber() or changed
                    if changed:
                        self._dirty = True
            finally:
                self._inside_convergence.reset(token)

    async def _deactivate_invalid_fibers(self) -> bool:
        changed = False
        for fiber in tuple(reversed(self._fibers)):
            if fiber.state is FiberState.ACTIVE and fiber.desired:
                epoch = self._dependency_epoch(fiber)
                if epoch != fiber.epoch:
                    if fiber.children:
                        for child in tuple(fiber.children):
                            self._mark_undesired(child)
                    else:
                        await self._deactivate(fiber, final=False)
                    changed = True
            elif fiber.state is FiberState.FAILED and fiber.desired:
                epoch = self._dependency_epoch(fiber)
                if epoch != fiber.epoch:
                    fiber.state = FiberState.PENDING
                    fiber.error = None
                    fiber.epoch = None
                    changed = True
        return changed

    async def _dispose_undesired_fibers(self) -> bool:
        changed = False
        for fiber in tuple(reversed(self._fibers)):
            if fiber.desired or fiber.state is FiberState.DISPOSED:
                continue
            if any(child.state is not FiberState.DISPOSED for child in fiber.children):
                continue
            if self._has_active_dependents(fiber):
                continue
            if fiber.state is FiberState.ACTIVE:
                await self._deactivate(fiber, final=True)
            else:
                if fiber.state is FiberState.LOADING:
                    continue
                if fiber.effect_labels:
                    await self._close_effects(fiber)
                fiber.state = FiberState.DISPOSED
            if fiber.parent is not None:
                fiber.parent.children.discard(fiber)
            try:
                self._fibers.remove(fiber)
            except ValueError:
                pass
            changed = True
        return changed

    async def _activate_one_pending_fiber(self) -> bool:
        for fiber in tuple(self._fibers):
            if not fiber.desired or fiber.state is not FiberState.PENDING:
                continue
            if fiber.parent is not None and fiber.parent.state is not FiberState.ACTIVE:
                continue
            epoch = self._dependency_epoch(fiber)
            if epoch is None:
                continue
            await self._activate(fiber, epoch)
            return True
        return False

    async def _activate(self, fiber: Fiber, epoch: tuple[int, ...]) -> None:
        assert fiber.spec is not None
        fiber.state = FiberState.LOADING
        fiber.effects = EffectScope()
        try:
            fiber.config = (
                fiber.spec.validate(fiber.raw_config)
                if fiber.spec.validate is not None
                else fiber.raw_config
            )
            result = fiber.spec.apply(fiber.context, fiber.config)
            if inspect.isawaitable(result):
                result = await result
            fiber.effects.add_result(cast(CleanupResult, result), f"plugin:{fiber.name}")
        except BaseException as error:
            fiber.error = error
            fiber.epoch = epoch
            for child in tuple(fiber.children):
                self._mark_undesired(child)
            await self._close_effects(fiber)
            fiber.state = FiberState.FAILED
            return
        fiber.error = None
        fiber.epoch = epoch
        fiber.state = FiberState.ACTIVE

    async def _deactivate(self, fiber: Fiber, *, final: bool) -> None:
        fiber.state = FiberState.UNLOADING
        await self._close_effects(fiber)
        fiber.epoch = None
        fiber.state = FiberState.DISPOSED if final else FiberState.PENDING

    async def _close_effects(self, fiber: Fiber) -> None:
        try:
            await fiber.effects.close()
        except BaseException as error:
            fiber.cleanup_errors.append(error)

    def _dependency_epoch(self, fiber: Fiber) -> tuple[int, ...] | None:
        assert fiber.spec is not None
        generations: list[int] = []
        for key in fiber.spec.requires:
            registration = self._resolve(fiber.context, key, include_self=False)
            if registration is None:
                return None
            generations.append(registration.generation)
        return tuple(generations)

    def _has_active_dependents(self, provider: Fiber) -> bool:
        registrations = {
            (registration.key, registration.realm)
            for registration in self._services.values()
            if registration.provider is provider
        }
        if not registrations:
            return False
        for fiber in self._fibers:
            if fiber is provider or fiber.state is not FiberState.ACTIVE:
                continue
            assert fiber.spec is not None
            if any((key, fiber.context.realm(key)) in registrations for key in fiber.spec.requires):
                return True
        return False

    def _resolve(
        self,
        context: Context,
        key: ServiceKey[ServiceT],
        *,
        include_self: bool = True,
    ) -> _ServiceRegistration | None:
        registration = self._services.get((key, context.realm(key)))
        if registration is None:
            return None
        if include_self and registration.provider is context.fiber:
            if context.fiber.state in (
                FiberState.LOADING,
                FiberState.ACTIVE,
                FiberState.UNLOADING,
            ):
                return registration
        if registration.provider.state is FiberState.ACTIVE and registration.provider.desired:
            return registration
        return None

    def _provide(
        self,
        context: Context,
        key: ServiceKey[ServiceT],
        value: ServiceT,
    ) -> _ServiceRegistration:
        address = (key, context.realm(key))
        existing = self._services.get(address)
        if existing is not None:
            raise DuplicateServiceError(
                f"service {key.name!r} is already provided by {existing.provider.name!r}"
            )
        registration = _ServiceRegistration(
            key,
            address[1],
            value,
            context.fiber,
            self._next_generation,
        )
        self._next_generation += 1
        self._services[address] = registration
        self._dirty = True
        return registration

    def _remove_service(self, registration: _ServiceRegistration) -> None:
        address = (registration.key, registration.realm)
        if self._services.get(address) is registration:
            del self._services[address]
            self._dirty = True

    def _is_declared_or_owned(self, context: Context, key: ServiceKey[Any]) -> bool:
        if context.fiber.spec is None or key in context.fiber.spec.requires:
            return True
        registration = self._services.get((key, context.realm(key)))
        return registration is not None and registration.provider is context.fiber
