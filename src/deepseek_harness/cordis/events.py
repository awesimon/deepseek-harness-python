"""Effect-owned event listeners and Cordis dispatch modes."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

from .errors import InvalidEventModeError

ResultT = TypeVar("ResultT")
type Listener = Callable[..., Any]


class EventMode(str, Enum):
    """Supported event dispatch strategies."""

    EMIT = "emit"
    PARALLEL = "parallel"
    SERIAL = "serial"
    WATERFALL = "waterfall"


@dataclass(frozen=True, slots=True)
class EventKey(Generic[ResultT]):
    """Stable event name paired with its only legal dispatch mode."""

    name: str
    mode: EventMode

    def __post_init__(self) -> None:
        """Reject an empty event name."""
        if not self.name:
            raise ValueError("event name must not be empty")


@dataclass(slots=True)
class _ListenerRecord:
    callback: Listener


async def _invoke(callback: Listener, *args: Any) -> Any:
    """Invoke a listener and await its result when needed."""
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


class EventBus:
    """Runtime event registry; contexts attach ownership through effects."""

    def __init__(self) -> None:
        self._listeners: dict[EventKey[Any], list[_ListenerRecord]] = {}

    def register(
        self,
        event: EventKey[Any],
        listener: Listener,
        *,
        prepend: bool = False,
    ) -> Callable[[], None]:
        """Register a listener and return an idempotent exact-registration undo."""
        records = self._listeners.setdefault(event, [])
        record = _ListenerRecord(listener)
        if prepend:
            records.insert(0, record)
        else:
            records.append(record)
        active = True

        def remove() -> None:
            nonlocal active
            if not active:
                return
            active = False
            try:
                records.remove(record)
            except ValueError:
                return
            if not records:
                self._listeners.pop(event, None)

        return remove

    def _for(self, event: EventKey[Any], mode: EventMode) -> tuple[_ListenerRecord, ...]:
        if event.mode is not mode:
            raise InvalidEventModeError(
                f"event {event.name!r} uses {event.mode.value}, not {mode.value}"
            )
        return tuple(self._listeners.get(event, ()))

    def emit(self, event: EventKey[None], *args: Any) -> None:
        """Invoke synchronous observers in registration order."""
        for record in self._for(event, EventMode.EMIT):
            result = record.callback(*args)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError(f"emit listener for {event.name!r} returned an awaitable")

    async def parallel(self, event: EventKey[None], *args: Any) -> None:
        """Start every listener and wait for all of them."""
        records = self._for(event, EventMode.PARALLEL)
        results = await asyncio.gather(
            *(_invoke(record.callback, *args) for record in records),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise BaseExceptionGroup(f"parallel event {event.name!r} failed", errors)

    async def serial(self, event: EventKey[ResultT], *args: Any) -> ResultT | None:
        """Invoke listeners in order until one returns a bail value."""
        for record in self._for(event, EventMode.SERIAL):
            result = await _invoke(record.callback, *args)
            if result is not None and result is not False:
                return result
        return None

    async def waterfall(
        self,
        event: EventKey[ResultT],
        *args: Any,
        terminal: Callable[..., ResultT | Awaitable[ResultT]],
    ) -> ResultT:
        """Compose listeners around a terminal operation."""
        records = self._for(event, EventMode.WATERFALL)
        index = 0

        async def call_next() -> ResultT:
            nonlocal index
            if index == len(records):
                return await _invoke(terminal, *args)
            record = records[index]
            index += 1
            return await _invoke(record.callback, *args, call_next)

        return await call_next()

    def listener_count(self, event: EventKey[Any]) -> int:
        """Return the current number of listeners for diagnostics and tests."""
        return len(self._listeners.get(event, ()))
