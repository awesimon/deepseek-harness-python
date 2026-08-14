"""Behavior tests for PyCordis event dispatch modes."""

from __future__ import annotations

import asyncio
import unittest

from deepseek_harness.cordis import Cordis, EventKey, EventMode, InvalidEventModeError


class CordisEventTests(unittest.IsolatedAsyncioTestCase):
    """Exercise ordered, concurrent, and middleware event contracts."""

    async def asyncSetUp(self) -> None:
        """Create one fresh runtime per test."""
        self.runtime = Cordis()

    async def asyncTearDown(self) -> None:
        """Release root-owned listeners."""
        await self.runtime.close()

    async def test_waterfall_wraps_terminal_in_registration_order(self) -> None:
        """Calling next delegates and lets outer listeners transform the result."""
        event = EventKey[str]("request", EventMode.WATERFALL)
        trace: list[str] = []

        async def outer(value: str, next_call):
            trace.append("outer:before")
            result = await next_call()
            trace.append("outer:after")
            return f"outer({result})"

        async def inner(value: str, next_call):
            trace.append("inner:before")
            result = await next_call()
            trace.append("inner:after")
            return f"inner({result})"

        await self.runtime.root.on(event, outer)
        await self.runtime.root.on(event, inner)

        result = await self.runtime.root.waterfall(
            event,
            "payload",
            terminal=lambda value: f"terminal:{value}",
        )

        self.assertEqual(result, "outer(inner(terminal:payload))")
        self.assertEqual(
            trace,
            ["outer:before", "inner:before", "inner:after", "outer:after"],
        )

    async def test_waterfall_can_short_circuit_terminal(self) -> None:
        """Returning without next suppresses downstream and terminal behavior."""
        event = EventKey[str]("policy", EventMode.WATERFALL)
        called = False

        async def block(_value: str, _next_call):
            return "blocked"

        def terminal(_value: str) -> str:
            nonlocal called
            called = True
            return "allowed"

        await self.runtime.root.on(event, block)
        result = await self.runtime.root.waterfall(event, "input", terminal=terminal)

        self.assertEqual(result, "blocked")
        self.assertFalse(called)

    async def test_parallel_starts_all_listeners_before_completion(self) -> None:
        """Parallel dispatch does not serialize listener completion."""
        event = EventKey[None]("checkpoint", EventMode.PARALLEL)
        both_started = asyncio.Event()
        started: list[str] = []

        async def listener(name: str) -> None:
            started.append(name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        await self.runtime.root.on(event, lambda: listener("first"))
        await self.runtime.root.on(event, lambda: listener("second"))

        await self.runtime.root.parallel(event)
        self.assertEqual(started, ["first", "second"])

    async def test_serial_stops_at_first_bail_value(self) -> None:
        """Serial dispatch ignores None and False before returning a bail value."""
        event = EventKey[str]("select", EventMode.SERIAL)
        called: list[str] = []

        async def listener(name: str, result):
            called.append(name)
            return result

        await self.runtime.root.on(event, lambda: listener("none", None))
        await self.runtime.root.on(event, lambda: listener("false", False))
        await self.runtime.root.on(event, lambda: listener("selected", "value"))
        await self.runtime.root.on(event, lambda: listener("unreached", "other"))

        result = await self.runtime.root.serial(event)

        self.assertEqual(result, "value")
        self.assertEqual(called, ["none", "false", "selected"])

    async def test_listener_disappears_with_effect(self) -> None:
        """Disposing a listener effect removes that exact registration."""
        event = EventKey[None]("notice", EventMode.EMIT)
        seen: list[str] = []
        handle = await self.runtime.root.on(event, seen.append)

        self.runtime.root.emit(event, "first")
        await handle.dispose()
        self.runtime.root.emit(event, "second")

        self.assertEqual(seen, ["first"])
        self.assertEqual(self.runtime.events.listener_count(event), 0)

    async def test_wrong_dispatch_mode_fails_loudly(self) -> None:
        """An event key cannot drift between dispatch contracts."""
        event = EventKey[None]("strict", EventMode.EMIT)
        with self.assertRaises(InvalidEventModeError):
            await self.runtime.root.parallel(event)
