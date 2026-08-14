"""Cleanup-aware effects owned by plugin fibers."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable
from typing import Any

from .errors import InvalidEffectError
from .types import Cleanup, CleanupResult, EffectSetup


async def _await_if_needed(value: Any) -> Any:
    """Await an awaitable value and otherwise return it unchanged."""
    if inspect.isawaitable(value):
        return await value
    return value


def _collect_cleanups(result: CleanupResult) -> list[Cleanup]:
    """Normalize one effect result into validated cleanup callbacks."""
    if result is None:
        return []
    if callable(result):
        return [result]
    if not isinstance(result, Iterable):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise InvalidEffectError(f"unsupported effect result: {type(result).__name__}")
    cleanups = list(result)
    if any(not callable(cleanup) for cleanup in cleanups):
        raise InvalidEffectError("an effect cleanup iterable contains a non-callable value")
    return cleanups


class EffectHandle:
    """Single-shot disposer for one effect group."""

    def __init__(self, cleanups: list[Cleanup], label: str) -> None:
        self.label = label
        self._cleanups = cleanups
        self._disposal: asyncio.Task[None] | None = None

    @classmethod
    async def create(cls, setup: EffectSetup, label: str) -> EffectHandle:
        """Run setup and construct a handle for its cleanup callbacks."""
        result = await _await_if_needed(setup())
        return cls(_collect_cleanups(result), label)

    @classmethod
    def from_result(cls, result: CleanupResult, label: str) -> EffectHandle:
        """Construct a handle from an already-produced plugin result."""
        return cls(_collect_cleanups(result), label)

    async def dispose(self) -> None:
        """Run every cleanup in reverse order and join concurrent callers."""
        if self._disposal is None:
            self._disposal = asyncio.create_task(self._dispose_once())
        await asyncio.shield(self._disposal)

    async def _dispose_once(self) -> None:
        errors: list[BaseException] = []
        for cleanup in reversed(self._cleanups):
            try:
                await _await_if_needed(cleanup())
            except BaseException as error:  # noqa: BLE001 -- all cleanup attempts must run
                errors.append(error)
        self._cleanups.clear()
        if errors:
            raise BaseExceptionGroup(f"effect {self.label!r} cleanup failed", errors)


class EffectScope:
    """Top-level effects owned by one activation of a fiber."""

    def __init__(self) -> None:
        self._handles: list[EffectHandle] = []
        self._closed = False
        self._disposal: asyncio.Task[None] | None = None

    async def add(self, setup: EffectSetup, label: str) -> EffectHandle:
        """Run and retain one effect setup."""
        if self._closed:
            raise InvalidEffectError("cannot add an effect to a closed scope")
        handle = await EffectHandle.create(setup, label)
        self._handles.append(handle)
        return handle

    def add_result(self, result: CleanupResult, label: str) -> EffectHandle:
        """Retain cleanups returned by the plugin body itself."""
        if self._closed:
            raise InvalidEffectError("cannot add an effect to a closed scope")
        handle = EffectHandle.from_result(result, label)
        self._handles.append(handle)
        return handle

    async def close(self) -> None:
        """Dispose top-level effects concurrently and join racing callers."""
        if self._disposal is None:
            self._disposal = asyncio.create_task(self._close_once())
        await asyncio.shield(self._disposal)

    async def _close_once(self) -> None:
        """Run the scope's cleanup exactly once."""
        self._closed = True
        handles = self._handles
        self._handles = []
        results = await asyncio.gather(
            *(handle.dispose() for handle in handles),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise BaseExceptionGroup("fiber effect cleanup failed", errors)

    @property
    def labels(self) -> tuple[str, ...]:
        """Return labels for currently owned top-level effects."""
        return tuple(handle.label for handle in self._handles)
