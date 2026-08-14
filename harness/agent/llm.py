"""Explicit LLM adapter routing and stream protocol enforcement."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from .values import AdapterOutput, LLMRoute, ModelChunk, ModelRequest, ModelResponse


class DuplicateLLMRouteError(RuntimeError):
    """Raised when two adapters register the same provider and model Route."""


class LLMRouteNotFoundError(LookupError):
    """Raised when no adapter owns an explicitly requested Route."""


class LLMAdapterProtocolError(RuntimeError):
    """Raised when an adapter violates terminal response ordering."""


class LLMAdapter(Protocol):
    """One provider implementation yielding chunks then one response."""

    def stream(self, request: ModelRequest) -> AsyncIterator[AdapterOutput]:
        """Yield zero or more chunks followed by exactly one response."""
        ...


class LLMRegistry:
    """Effect-compatible registry for explicit LLM Routes."""

    def __init__(self) -> None:
        self._adapters: dict[LLMRoute, LLMAdapter] = {}

    def register(self, route: LLMRoute, adapter: LLMAdapter) -> Callable[[], None]:
        """Register one exact Route and return an idempotent disposer."""
        if route in self._adapters:
            raise DuplicateLLMRouteError(
                f"LLM route {route.provider!r}/{route.model!r} is already registered"
            )
        self._adapters[route] = adapter
        active = True

        def dispose() -> None:
            nonlocal active
            if not active:
                return
            active = False
            if self._adapters.get(route) is adapter:
                del self._adapters[route]

        return dispose

    def resolve(self, route: LLMRoute) -> LLMAdapter:
        """Resolve one explicit Route without hidden fallback."""
        try:
            return self._adapters[route]
        except KeyError as error:
            raise LLMRouteNotFoundError(
                f"no LLM adapter for {route.provider!r}/{route.model!r}"
            ) from error


async def collect_adapter_response(
    adapter: LLMAdapter,
    request: ModelRequest,
    on_chunk: Callable[[ModelChunk], None | Awaitable[None]],
) -> ModelResponse:
    """Consume an adapter stream while enforcing one terminal response."""
    response: ModelResponse | None = None
    async for item in adapter.stream(request):
        if response is not None:
            raise LLMAdapterProtocolError("adapter yielded output after its terminal response")
        if isinstance(item, ModelResponse):
            response = item
            continue
        if not isinstance(item, ModelChunk):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise LLMAdapterProtocolError(
                f"adapter yielded unsupported output {type(item).__name__}"
            )
        result = on_chunk(item)
        if isinstance(result, Awaitable):
            await result
    if response is None:
        raise LLMAdapterProtocolError("adapter completed without a terminal response")
    return response
