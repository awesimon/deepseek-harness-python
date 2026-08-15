"""Serialized Agent invocation and cancellation lifecycle tests."""

from __future__ import annotations

import asyncio
import unittest

from harness.agent import (
    AGENT_LOOP,
    LLM_REGISTRY,
    SESSION_LOG,
    AgentInvocationService,
    AgentSpineConfig,
    DefaultLLMRouteUnavailableError,
    DuplicateInvocationIdError,
    InvocationCancelledError,
    LLMRoute,
    ModelProviderFailure,
    ModelResponse,
    StepFailed,
    UserInputAccepted,
    agent_spine_plugin,
)
from harness.cordis import Cordis


class AgentInvocationTests(unittest.IsolatedAsyncioTestCase):
    """Prove FIFO Session ownership, admission, cancellation, and shutdown."""

    async def asyncSetUp(self) -> None:
        self.runtime = Cordis()
        await self.runtime.mount(agent_spine_plugin(), AgentSpineConfig("session-runtime"))
        self.loop = self.runtime.root.lookup(AGENT_LOOP)
        self.llms = self.runtime.root.lookup(LLM_REGISTRY)
        self.log = self.runtime.root.lookup(SESSION_LOG)
        assert self.loop is not None
        assert self.llms is not None
        assert self.log is not None
        self.route = LLMRoute("fake", "model")

    async def asyncTearDown(self) -> None:
        await self.runtime.close()

    async def test_fifo_execution_reuses_completed_session_history(self) -> None:
        """A queued Turn starts only after the prior Assistant result is durable."""
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        requests = []

        class Adapter:
            calls = 0

            async def stream(adapter_self, request):
                adapter_self.calls += 1
                requests.append(request)
                if adapter_self.calls == 1:
                    first_started.set()
                    await release_first.wait()
                    yield ModelResponse("first result")
                else:
                    yield ModelResponse("second result")

        self.llms.register(self.route, Adapter())
        service = AgentInvocationService(
            self.loop,
            self.llms,
            self.log,
            default_route=self.route,
            max_steps=4,
        )
        first = asyncio.create_task(service.invoke("one", "first"))
        await first_started.wait()
        second = asyncio.create_task(service.invoke("two", "second"))
        await asyncio.sleep(0)
        self.assertEqual(len(requests), 1)
        release_first.set()
        first_result, second_result = await asyncio.gather(first, second)
        self.assertEqual(first_result.message.content, "first result")
        self.assertEqual(second_result.message.content, "second result")
        self.assertEqual([message.content for message in requests[1].messages], [
            "first",
            "first result",
            "second",
        ])
        await service.close()

    async def test_route_admission_precedes_user_input(self) -> None:
        """Missing default and explicit routes leave the Session untouched."""
        service = AgentInvocationService(
            self.loop,
            self.llms,
            self.log,
            default_route=None,
            max_steps=4,
        )
        with self.assertRaises(DefaultLLMRouteUnavailableError):
            await service.invoke("missing-default", "hello")
        with self.assertRaises(LookupError):
            await service.invoke("missing-route", "hello", route=self.route)
        self.assertEqual(self.log.snapshot(), ())
        await service.close()

    async def test_route_removed_after_admission_records_step_failure(self) -> None:
        """An admitted queued Turn records the Step when its Route disappears."""
        started = asyncio.Event()
        release = asyncio.Event()

        class Adapter:
            async def stream(_self, _request):
                started.set()
                await release.wait()
                yield ModelResponse("first result")

        dispose = self.llms.register(self.route, Adapter())
        service = AgentInvocationService(
            self.loop,
            self.llms,
            self.log,
            default_route=self.route,
            max_steps=4,
        )
        first = asyncio.create_task(service.invoke("first", "first"))
        await started.wait()
        second = asyncio.create_task(service.invoke("second", "second"))
        await asyncio.sleep(0)
        dispose()
        release.set()
        await first
        with self.assertRaises(LookupError):
            await second
        failure = self.log.snapshot()[-1].event
        self.assertIsInstance(failure, StepFailed)
        assert isinstance(failure, StepFailed)
        self.assertEqual(failure.code, "route_unavailable")
        await service.close()

    async def test_queued_and_active_invocations_cancel_without_success(self) -> None:
        """Cancellation removes queued input and propagates into the active adapter."""
        started = asyncio.Event()
        adapter_cancelled = asyncio.Event()

        class Adapter:
            async def stream(_self, _request):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    adapter_cancelled.set()
                if False:
                    yield ModelResponse("unreachable")

        self.llms.register(self.route, Adapter())
        service = AgentInvocationService(
            self.loop,
            self.llms,
            self.log,
            default_route=self.route,
            max_steps=4,
        )
        active = asyncio.create_task(service.invoke("active", "first"))
        await started.wait()
        queued = asyncio.create_task(service.invoke("queued", "second"))
        await asyncio.sleep(0)
        duplicate = asyncio.create_task(service.invoke("queued", "duplicate"))
        with self.assertRaises(DuplicateInvocationIdError):
            await duplicate
        self.assertTrue(await service.cancel("queued"))
        with self.assertRaises(InvocationCancelledError):
            await queued
        self.assertTrue(await service.cancel("active"))
        with self.assertRaises(InvocationCancelledError):
            await active
        await adapter_cancelled.wait()
        self.assertFalse(await service.cancel("active"))
        failures = [
            envelope.event
            for envelope in self.log.snapshot()
            if isinstance(envelope.event, StepFailed)
        ]
        self.assertEqual(failures[-1].code, "cancelled")
        accepted_text = [
            message.content
            for envelope in self.log.snapshot()
            if isinstance(envelope.event, UserInputAccepted)
            for message in envelope.event.messages
        ]
        self.assertEqual(accepted_text, ["first"])
        await service.close()

    async def test_provider_failure_is_logged_and_close_joins_active_work(self) -> None:
        """Provider failure metadata and shutdown cancellation survive orchestration."""

        class FailedAdapter:
            async def stream(_self, _request):
                yield ModelProviderFailure("provider_http", "HTTP 503", True, 503)

        dispose_failed = self.llms.register(self.route, FailedAdapter())
        service = AgentInvocationService(
            self.loop,
            self.llms,
            self.log,
            default_route=self.route,
            max_steps=4,
        )
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            await service.invoke("failure", "hello")
        failure = self.log.snapshot()[-1].event
        self.assertIsInstance(failure, StepFailed)
        assert isinstance(failure, StepFailed)
        self.assertEqual(failure.code, "provider_http")
        dispose_failed()

        started = asyncio.Event()
        cancelled = asyncio.Event()

        class BlockingAdapter:
            async def stream(_self, request):
                del request
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
                if False:
                    yield ModelResponse("unreachable")

        self.llms.register(self.route, BlockingAdapter())
        active = asyncio.create_task(service.invoke("shutdown", "wait"))
        await started.wait()
        await service.close()
        with self.assertRaises(InvocationCancelledError):
            await active
        await cancelled.wait()


if __name__ == "__main__":
    unittest.main()
