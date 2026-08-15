"""Session-scoped Agent invocation queue and provider composition."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from aiohttp import ClientSession

from harness.cordis import Context, PluginSpec, ServiceKey

from .deepseek import DeepSeekHTTPAdapter, DeepSeekHTTPConfig
from .llm import LLMRegistry
from .loop import AgentLoop, AgentRunResult
from .plugin import AGENT_LOOP, LLM_REGISTRY, SESSION_LOG
from .scope import AgentScope
from .session import SessionLog
from .values import LLMRoute


class AgentInvocationError(RuntimeError):
    """Base class for invocation admission and lifecycle failures."""


class DuplicateInvocationIdError(AgentInvocationError):
    """Raised when an Invocation ID is already queued or active."""


class InvocationCancelledError(AgentInvocationError):
    """Raised when an invocation is explicitly cancelled."""


class InvocationServiceClosedError(AgentInvocationError):
    """Raised when a closing invocation service rejects new work."""


class DefaultLLMRouteUnavailableError(AgentInvocationError):
    """Raised when an invocation omits a route and no default exists."""


@dataclass(frozen=True, slots=True)
class AgentRuntimeConfig:
    """Provider and Turn defaults for one Agent Runtime assembly."""

    deepseek: DeepSeekHTTPConfig | None = None
    max_steps: int = 8

    def __post_init__(self) -> None:
        if isinstance(self.max_steps, bool) or self.max_steps <= 0:
            raise ValueError("maximum Steps must be positive")


@dataclass(slots=True)
class _Invocation:
    identifier: str
    text: str
    route: LLMRoute
    future: asyncio.Future[AgentRunResult]


AGENT_INVOCATIONS = ServiceKey["AgentInvocationService"]("agent.invocations")
DEFAULT_LLM_ROUTE = ServiceKey[LLMRoute]("agent.default-llm-route")


class AgentInvocationService:
    """Serialize cancellable Turn invocations for one Session."""

    def __init__(
        self,
        loop: AgentLoop,
        llms: LLMRegistry,
        log: SessionLog,
        *,
        default_route: LLMRoute | None,
        max_steps: int,
    ) -> None:
        self.loop = loop
        self.llms = llms
        self.log = log
        self.default_route = default_route
        self.max_steps = max_steps
        self.scope = AgentScope()
        self._lock = asyncio.Lock()
        self._queue: deque[_Invocation] = deque()
        self._live: dict[str, _Invocation] = {}
        self._active: _Invocation | None = None
        self._active_turn: asyncio.Task[AgentRunResult] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    async def invoke(
        self,
        identifier: str,
        text: str,
        *,
        route: LLMRoute | None = None,
    ) -> AgentRunResult:
        """Queue one Turn and wait for its terminal result without interleaving history."""
        if not identifier:
            raise ValueError("Invocation ID must not be empty")
        if not text:
            raise ValueError("Invocation input must not be empty")
        effective_route = route if route is not None else self.default_route
        if effective_route is None:
            raise DefaultLLMRouteUnavailableError("no default LLM route is configured")
        self.llms.resolve(effective_route)
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(_consume_unobserved_exception)
        invocation = _Invocation(identifier, text, effective_route, future)
        async with self._lock:
            if self._closed:
                raise InvocationServiceClosedError("Agent invocation service is closing")
            if identifier in self._live:
                raise DuplicateInvocationIdError(
                    f"Invocation ID {identifier!r} is already queued or active"
                )
            self._live[identifier] = invocation
            self._queue.append(invocation)
            if self._worker is None:
                self._worker = asyncio.create_task(self._run_queue())
        return await asyncio.shield(future)

    async def cancel(self, identifier: str) -> bool:
        """Cancel one queued or active invocation without affecting another Turn."""
        async with self._lock:
            invocation = self._live.get(identifier)
            if invocation is None:
                return False
            if invocation is self._active:
                if self._active_turn is not None:
                    self._active_turn.cancel()
                return True
            self._queue.remove(invocation)
            del self._live[identifier]
            _set_exception(
                invocation.future,
                InvocationCancelledError("Agent invocation cancelled before execution"),
            )
            return True

    async def close(self) -> None:
        """Reject new work, cancel all live invocations, and join the queue worker."""
        async with self._lock:
            if not self._closed:
                self._closed = True
                while self._queue:
                    invocation = self._queue.popleft()
                    self._live.pop(invocation.identifier, None)
                    _set_exception(
                        invocation.future,
                        InvocationCancelledError("Agent invocation cancelled during shutdown"),
                    )
                if self._active_turn is not None:
                    self._active_turn.cancel()
            worker = self._worker
        if worker is not None:
            await asyncio.shield(worker)

    async def _run_queue(self) -> None:
        while True:
            async with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                invocation = self._queue.popleft()
                self._active = invocation
                turn = asyncio.create_task(
                    self.loop.run_text(
                        invocation.text,
                        route=invocation.route,
                        scope=self.scope,
                        max_steps=self.max_steps,
                    )
                )
                self._active_turn = turn
            try:
                result = await turn
            except asyncio.CancelledError:
                _set_exception(
                    invocation.future,
                    InvocationCancelledError("Agent invocation cancelled"),
                )
            except Exception as error:  # noqa: BLE001 -- invocation failures belong to its Future
                _set_exception(invocation.future, error)
            else:
                if not invocation.future.done():
                    invocation.future.set_result(result)
            finally:
                async with self._lock:
                    self._live.pop(invocation.identifier, None)
                    self._active = None
                    self._active_turn = None


def agent_runtime_plugin() -> PluginSpec[AgentRuntimeConfig]:
    """Return the provider for HTTP LLM routing and serialized invocations."""

    async def apply(context: Context, config: AgentRuntimeConfig) -> None:
        loop = context.require(AGENT_LOOP)
        llms = context.require(LLM_REGISTRY)
        log = context.require(SESSION_LOG)
        route: LLMRoute | None = None
        session: ClientSession | None = None
        dispose_route = None
        if config.deepseek is not None:
            route = LLMRoute(config.deepseek.provider, config.deepseek.model)
            session = ClientSession()
            try:
                dispose_route = llms.register(route, DeepSeekHTTPAdapter(config.deepseek, session))
            except BaseException:
                await session.close()
                raise
        invocations = AgentInvocationService(
            loop,
            llms,
            log,
            default_route=route,
            max_steps=config.max_steps,
        )

        async def cleanup() -> None:
            await invocations.close()
            if dispose_route is not None:
                dispose_route()
            if session is not None:
                await session.close()

        await context.effect(lambda: cleanup, "agent-runtime-lifecycle")
        await context.provide(AGENT_INVOCATIONS, invocations)
        if route is not None:
            await context.provide(DEFAULT_LLM_ROUTE, route)

    return PluginSpec(
        "agent-runtime",
        apply,
        requires=(AGENT_LOOP, LLM_REGISTRY, SESSION_LOG),
    )


def _set_exception(future: asyncio.Future[AgentRunResult], error: BaseException) -> None:
    if not future.done():
        future.set_exception(error)


def _consume_unobserved_exception(future: asyncio.Future[AgentRunResult]) -> None:
    if not future.cancelled():
        future.exception()
