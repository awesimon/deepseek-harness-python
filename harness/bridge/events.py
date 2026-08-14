"""Effect-compatible explicit Event forwarding for Browser Bridge plugins."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from harness.agent.values import JsonValue

from .protocol import BridgeEvent

type EventHandler = Callable[[str, JsonValue], None | Awaitable[None]]
type EventSink = Callable[[BridgeEvent], None | Awaitable[None]]


@dataclass(slots=True)
class _PageSink:
    sender: EventSink
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BridgeEventRegistry:
    """Explicit backend handlers and connection-owned browser Event sinks."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str, str], EventHandler] = {}
        self._pages: dict[str, _PageSink] = {}

    def register(
        self,
        plugin_id: str,
        revision: str,
        name: str,
        handler: EventHandler,
    ) -> Callable[[], None]:
        """Register one exact client-to-backend Event and return its disposer."""
        key = (plugin_id, revision, name)
        if key in self._handlers:
            raise RuntimeError(f"bridge Event handler is already registered: {key!r}")
        self._handlers[key] = handler
        active = True

        def dispose() -> None:
            nonlocal active
            if active and self._handlers.get(key) is handler:
                del self._handlers[key]
            active = False

        return dispose

    def attach_page(self, page_id: str, sender: EventSink) -> Callable[[], None]:
        """Attach one connection sink and return an identity-safe disposer."""
        if page_id in self._pages:
            raise RuntimeError(f"page Event sink is already attached: {page_id!r}")
        sink = _PageSink(sender)
        self._pages[page_id] = sink
        active = True

        def dispose() -> None:
            nonlocal active
            if active and self._pages.get(page_id) is sink:
                del self._pages[page_id]
            active = False

        return dispose

    def detach_page(self, page_id: str) -> None:
        """Remove any Event sink owned by a disconnected logical page."""
        self._pages.pop(page_id, None)

    async def dispatch_backend(self, event: BridgeEvent) -> None:
        """Deliver one authorized client Event to its exact backend handler."""
        try:
            handler = self._handlers[(event.plugin_id, event.revision, event.name)]
        except KeyError as error:
            raise LookupError("bridge Event handler is unavailable or stale") from error
        result = handler(event.page_id, event.payload)
        if inspect.isawaitable(result):
            await result

    async def dispatch_client(self, event: BridgeEvent) -> None:
        """Deliver one backend Event in connection order to its target page."""
        try:
            sink = self._pages[event.page_id]
        except KeyError as error:
            raise LookupError(f"page {event.page_id!r} has no Event sink") from error
        async with sink.lock:
            result = sink.sender(event)
            if inspect.isawaitable(result):
                await result
