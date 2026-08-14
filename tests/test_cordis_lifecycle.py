"""Behavior tests for dependency-driven PyCordis lifecycle."""

from __future__ import annotations

import asyncio
import unittest

from harness.cordis import (
    Cordis,
    DuplicateServiceError,
    FiberState,
    PluginSpec,
    ServiceKey,
    UndeclaredDependencyError,
)


class CordisLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Exercise service activation, replacement, isolation, and rollback."""

    async def asyncSetUp(self) -> None:
        """Create one fresh runtime per test."""
        self.runtime = Cordis()

    async def asyncTearDown(self) -> None:
        """Release every test-owned contribution."""
        await self.runtime.close()

    async def test_dependency_waits_and_reactivates_for_new_provider(self) -> None:
        """A consumer follows provider identity and cleans up before provider removal."""
        service = ServiceKey[str]("example.message")
        trace: list[str] = []

        async def consume(ctx, _config):
            value = ctx.require(service)
            trace.append(f"consumer:start:{value}")
            await ctx.effect(
                lambda: lambda: trace.append(f"consumer:stop:{value}"),
                "consumer-resource",
            )

        consumer = await self.runtime.mount(
            PluginSpec("consumer", consume, requires=(service,)),
            None,
        )
        self.assertIs(consumer.state, FiberState.PENDING)

        def provider(version: str) -> PluginSpec[None]:
            async def apply(ctx, _config):
                await ctx.provide(service, version)
                return lambda: trace.append(f"provider:stop:{version}")

            return PluginSpec(f"provider-{version}", apply)

        first = await self.runtime.mount(provider("v1"), None)
        self.assertIs(first.state, FiberState.ACTIVE)
        self.assertIs(consumer.state, FiberState.ACTIVE)
        self.assertEqual(trace, ["consumer:start:v1"])

        await first.dispose()
        self.assertIs(first.state, FiberState.DISPOSED)
        self.assertIs(consumer.state, FiberState.PENDING)
        self.assertLess(trace.index("consumer:stop:v1"), trace.index("provider:stop:v1"))

        second = await self.runtime.mount(provider("v2"), None)
        self.assertIs(second.state, FiberState.ACTIVE)
        self.assertIs(consumer.state, FiberState.ACTIVE)
        self.assertEqual(trace[-1], "consumer:start:v2")

    async def test_failed_activation_rolls_back_effects_and_services(self) -> None:
        """A failed plugin leaves neither resources nor a published service."""
        service = ServiceKey[str]("example.failed")
        cleanup_order: list[str] = []

        async def fail(ctx, _config):
            await ctx.effect(
                lambda: (
                    lambda: cleanup_order.append("first"),
                    lambda: cleanup_order.append("second"),
                ),
                "ordered-group",
            )
            await ctx.provide(service, "must-not-leak")
            raise RuntimeError("activation failed")

        fiber = await self.runtime.mount(PluginSpec("failing", fail), None)

        self.assertIs(fiber.state, FiberState.FAILED)
        self.assertIsInstance(fiber.error, RuntimeError)
        self.assertEqual(cleanup_order, ["second", "first"])
        self.assertIsNone(self.runtime.root.lookup(service))

    async def test_duplicate_service_fails_second_provider(self) -> None:
        """One service realm admits exactly one provider."""
        service = ServiceKey[str]("example.unique")

        async def provide(value: str, ctx) -> None:
            await ctx.provide(service, value)

        first = await self.runtime.mount(
            PluginSpec("first", lambda ctx, _config: provide("first", ctx)),
            None,
        )
        second = await self.runtime.mount(
            PluginSpec("second", lambda ctx, _config: provide("second", ctx)),
            None,
        )

        self.assertIs(first.state, FiberState.ACTIVE)
        self.assertIs(second.state, FiberState.FAILED)
        self.assertIsInstance(second.error, DuplicateServiceError)
        self.assertEqual(self.runtime.root.lookup(service), "first")

        await first.dispose()
        self.assertIs(second.state, FiberState.FAILED)
        await second.retry()
        self.assertIs(second.state, FiberState.ACTIVE)
        self.assertEqual(self.runtime.root.lookup(service), "second")

    async def test_isolation_realms_hold_independent_providers(self) -> None:
        """The same service key can have independent isolated implementations."""
        service = ServiceKey[str]("example.isolated")
        left = self.runtime.root.isolate(service)
        right = self.runtime.root.isolate(service)
        observed: list[str] = []

        async def provider(ctx, value: str) -> None:
            await ctx.provide(service, value)

        async def consumer(ctx, _config) -> None:
            observed.append(ctx.require(service))

        await self.runtime.mount(PluginSpec("left-provider", provider), "left", context=left)
        await self.runtime.mount(PluginSpec("right-provider", provider), "right", context=right)
        left_consumer = await self.runtime.mount(
            PluginSpec("left-consumer", consumer, requires=(service,)),
            None,
            context=left,
        )
        right_consumer = await self.runtime.mount(
            PluginSpec("right-consumer", consumer, requires=(service,)),
            None,
            context=right,
        )

        self.assertIs(left_consumer.state, FiberState.ACTIVE)
        self.assertIs(right_consumer.state, FiberState.ACTIVE)
        self.assertEqual(observed, ["left", "right"])
        self.assertIsNone(self.runtime.root.lookup(service))

    async def test_require_rejects_undeclared_service_access(self) -> None:
        """Explicit service lookup does not hide an undeclared dependency."""
        service = ServiceKey[str]("example.declared")
        await self.runtime.root.provide(service, "root")

        async def apply(ctx, _config) -> None:
            ctx.require(service)

        fiber = await self.runtime.mount(PluginSpec("undeclared", apply), None)

        self.assertIs(fiber.state, FiberState.FAILED)
        self.assertIsInstance(fiber.error, UndeclaredDependencyError)

    async def test_parent_disposal_waits_for_child_cleanup(self) -> None:
        """A child contribution reaches quiescence before its parent cleans up."""
        trace: list[str] = []

        async def child_apply(_ctx, _config):
            trace.append("child:start")
            return lambda: trace.append("child:stop")

        child_spec = PluginSpec("child", child_apply)

        async def parent_apply(ctx, _config):
            trace.append("parent:start")
            await ctx.mount(child_spec, None)
            return lambda: trace.append("parent:stop")

        parent = await self.runtime.mount(PluginSpec("parent", parent_apply), None)
        self.assertEqual(trace, ["parent:start", "child:start"])

        await parent.dispose()

        self.assertEqual(trace, ["parent:start", "child:start", "child:stop", "parent:stop"])
        self.assertIs(parent.state, FiberState.DISPOSED)
        self.assertEqual(self.runtime.fibers, ())

    async def test_top_level_effects_cleanup_concurrently(self) -> None:
        """A fiber starts every top-level cleanup before waiting for completion."""
        both_started = asyncio.Event()
        started: list[str] = []

        async def cleanup(name: str) -> None:
            started.append(name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

        async def apply(ctx, _config) -> None:
            await ctx.effect(lambda: lambda: cleanup("first"), "first")
            await ctx.effect(lambda: lambda: cleanup("second"), "second")

        fiber = await self.runtime.mount(PluginSpec("concurrent-cleanup", apply), None)
        await fiber.dispose()

        self.assertCountEqual(started, ["first", "second"])

    async def test_effect_dispose_joins_concurrent_callers(self) -> None:
        """Concurrent disposal calls wait for one cleanup execution."""
        release = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_count = 0

        async def cleanup() -> None:
            nonlocal cleanup_count
            cleanup_count += 1
            cleanup_started.set()
            await release.wait()

        handle = await self.runtime.root.effect(lambda: cleanup, "shared-disposal")
        first = asyncio.create_task(handle.dispose())
        await cleanup_started.wait()
        second = asyncio.create_task(handle.dispose())
        release.set()
        await asyncio.gather(first, second)

        self.assertEqual(cleanup_count, 1)

    async def test_failed_parent_does_not_leave_child_fiber(self) -> None:
        """A child mounted during a failed activation is rolled back with its parent."""

        async def child_apply(_ctx, _config) -> None:
            self.fail("a child must not activate before its loading parent becomes active")

        async def parent_apply(ctx, _config) -> None:
            await ctx.mount(PluginSpec("unreached-child", child_apply), None)
            raise RuntimeError("parent failed")

        parent = await self.runtime.mount(PluginSpec("failed-parent", parent_apply), None)

        self.assertIs(parent.state, FiberState.FAILED)
        self.assertEqual(parent.children, set())
        self.assertEqual(self.runtime.fibers, (parent,))
