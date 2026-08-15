"""Host Agent invocation API and HTTP client command tests."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import unittest

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from harness.agent import AGENT_INVOCATIONS, SESSION_LOG, DeepSeekHTTPConfig, StepFailed
from harness.host import HarnessHost, HarnessHostConfig, _invoke_cli


class AgentHostTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the assembled Host against a keyless local provider."""

    async def asyncSetUp(self) -> None:
        self.provider_app = web.Application()
        self.provider: TestServer | None = None
        self.host: HarnessHost | None = None

    async def asyncTearDown(self) -> None:
        if self.host is not None:
            await self.host.close()
        if self.provider is not None:
            await self.provider.close()

    async def _start_provider(self, handler: object) -> DeepSeekHTTPConfig:
        self.provider_app.router.add_post("/chat/completions", handler)  # type: ignore[arg-type]
        self.provider = TestServer(self.provider_app)
        await self.provider.start_server()
        return DeepSeekHTTPConfig(
            "deepseek",
            "deepseek-chat",
            str(self.provider.make_url("/")).rstrip("/"),
            "test-key",
            request_timeout=1,
        )

    async def _start_host(self, config: DeepSeekHTTPConfig | None) -> HarnessHost:
        self.host = HarnessHost(HarnessHostConfig(port=0, deepseek=config))
        await self.host.start()
        return self.host

    async def test_http_invocation_returns_terminal_assistant_message(self) -> None:
        """The real Host records a streamed provider result and returns one JSON body."""

        async def provider(request: web.Request) -> web.StreamResponse:
            payload = await request.json()
            self.assertEqual(payload["messages"][-1]["content"], "hello")
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'data: {"choices":[{"index":0,"delta":{"content":"world"},"finish_reason":"stop"}]}\n\n'
            )
            await response.write(b"data: [DONE]\n\n")
            return response

        host = await self._start_host(await self._start_provider(provider))
        self.assertIsNotNone(host.runtime.root.lookup(AGENT_INVOCATIONS))
        async with ClientSession() as client:
            response = await client.post(
                f"{host.base_url}/api/v1/agent/invocations/invoke-1",
                json={"input": "hello"},
            )
            self.assertEqual(response.status, 200)
            payload = await response.json()
        self.assertEqual(payload["invocation_id"], "invoke-1")
        self.assertEqual(payload["session_id"], "default")
        self.assertEqual(payload["message"], {"role": "assistant", "content": "world"})

    async def test_validation_route_and_provider_failures_are_structured(self) -> None:
        """Bad input, missing routes, and upstream failures retain distinct HTTP codes."""
        host = await self._start_host(None)
        endpoint = f"{host.base_url}/api/v1/agent/invocations/test"
        async with ClientSession() as client:
            invalid = await client.post(endpoint, json={"input": ""})
            self.assertEqual(invalid.status, 400)
            invalid_route = await client.post(
                endpoint,
                json={"input": "hello", "route": []},
            )
            self.assertEqual(invalid_route.status, 400)
            unavailable = await client.post(endpoint, json={"input": "hello"})
            self.assertEqual(unavailable.status, 503)
            self.assertEqual((await unavailable.json())["code"], "route_unavailable")
        namespace = argparse.Namespace(
            url=host.base_url,
            provider=None,
            model=None,
            text="hello",
        )
        error_output = io.StringIO()
        with contextlib.redirect_stderr(error_output):
            status = await _invoke_cli(namespace)
        self.assertEqual(status, 1)
        self.assertIn("route_unavailable", error_output.getvalue())

    async def test_provider_failure_maps_to_safe_gateway_response(self) -> None:
        """Provider metadata reaches callers without exposing the configured key."""

        async def provider(_request: web.Request) -> web.Response:
            return web.Response(status=503, text="test-key unavailable")

        host = await self._start_host(await self._start_provider(provider))
        async with ClientSession() as client:
            response = await client.post(
                f"{host.base_url}/api/v1/agent/invocations/failure",
                json={"input": "hello"},
            )
            payload = await response.json()
        self.assertEqual(response.status, 502)
        self.assertEqual(payload["code"], "provider_http")
        self.assertEqual(payload["provider_status"], 503)
        self.assertTrue(payload["retryable"])
        self.assertNotIn("test-key", payload["message"])

    async def test_delete_cancels_an_active_provider_request(self) -> None:
        """The cancellation route propagates into the active SSE response."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def provider(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            started.set()
            await release.wait()
            return response

        host = await self._start_host(await self._start_provider(provider))
        endpoint = f"{host.base_url}/api/v1/agent/invocations/cancel-me"
        async with ClientSession() as client:
            pending = asyncio.create_task(client.post(endpoint, json={"input": "wait"}))
            await started.wait()
            cancelled = await client.delete(endpoint)
            self.assertEqual(cancelled.status, 202)
            result = await pending
            self.assertEqual(result.status, 409)
            self.assertEqual((await result.json())["code"], "invocation_cancelled")
        release.set()

    async def test_host_close_cancels_invocation_before_waiting_for_http_cleanup(self) -> None:
        """Shutdown does not wait for a blocked provider handler before cancellation."""
        started = asyncio.Event()

        async def provider(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            started.set()
            await asyncio.Event().wait()
            return response

        host = await self._start_host(await self._start_provider(provider))
        endpoint = f"{host.base_url}/api/v1/agent/invocations/close-me"
        async with ClientSession() as client:
            pending = asyncio.create_task(client.post(endpoint, json={"input": "wait"}))
            await started.wait()
            await asyncio.wait_for(host.close(), timeout=1)
            response = await asyncio.wait_for(pending, timeout=1)
            self.assertEqual(response.status, 409)
            self.assertEqual((await response.json())["code"], "invocation_cancelled")

    async def test_invoke_cli_prints_only_terminal_content(self) -> None:
        """The client command consumes the public Host API rather than provider secrets."""

        async def provider(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'data: {"choices":[{"index":0,"delta":{"content":"cli result"},"finish_reason":"stop"}]}\n\n'
            )
            return response

        host = await self._start_host(await self._start_provider(provider))
        namespace = argparse.Namespace(
            url=host.base_url,
            provider=None,
            model=None,
            text="hello",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = await _invoke_cli(namespace)
        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "cli result\n")

    async def test_invoke_cli_interrupt_cancels_the_host_invocation(self) -> None:
        """Client task cancellation sends DELETE before propagating the interrupt."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def provider(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            started.set()
            await release.wait()
            return response

        host = await self._start_host(await self._start_provider(provider))
        namespace = argparse.Namespace(
            url=host.base_url,
            provider=None,
            model=None,
            text="wait",
        )
        invocation = asyncio.create_task(_invoke_cli(namespace))
        await started.wait()
        invocation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await invocation
        log = host.runtime.root.lookup(SESSION_LOG)
        assert log is not None
        failure = log.snapshot()[-1].event
        self.assertIsInstance(failure, StepFailed)
        assert isinstance(failure, StepFailed)
        self.assertEqual(failure.code, "cancelled")
        release.set()


if __name__ == "__main__":
    unittest.main()
