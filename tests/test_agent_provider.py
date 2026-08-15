"""DeepSeek-compatible provider mapping and stream conformance tests."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from harness.agent import (
    DeepSeekHTTPAdapter,
    DeepSeekHTTPConfig,
    LLMProviderError,
    LLMRoute,
    Message,
    ModelRequest,
    ModelToolDefinition,
    Role,
    StepId,
    ToolCall,
    ToolCallId,
    TurnId,
)
from harness.agent.llm import collect_adapter_response


class DeepSeekProviderTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real HTTP request mapping and SSE assembly without credentials."""

    async def asyncSetUp(self) -> None:
        self.app = web.Application()
        self.server: TestServer | None = None
        self.session = ClientSession()

    async def asyncTearDown(self) -> None:
        await self.session.close()
        if self.server is not None:
            await self.server.close()

    async def _start(self, handler: object) -> DeepSeekHTTPAdapter:
        self.app.router.add_post("/chat/completions", handler)  # type: ignore[arg-type]
        self.server = TestServer(self.app)
        await self.server.start_server()
        config = DeepSeekHTTPConfig(
            "deepseek",
            "deepseek-chat",
            str(self.server.make_url("/")).rstrip("/"),
            "test-secret",
            request_timeout=1,
        )
        return DeepSeekHTTPAdapter(config, self.session)

    def _request(self) -> ModelRequest:
        return ModelRequest(
            TurnId("turn-1"),
            StepId("turn-1:step-1"),
            LLMRoute("deepseek", "deepseek-chat"),
            "System prompt",
            (
                Message(Role.USER, "Use the tool"),
                Message(
                    Role.ASSISTANT,
                    "",
                    (ToolCall(ToolCallId("old-call"), "lookup", {"key": "old"}),),
                ),
                Message(Role.TOOL, '{"ok":true}', tool_call_id=ToolCallId("old-call")),
            ),
            (
                ModelToolDefinition(
                    "lookup",
                    "Look up one key.",
                    {"type": "object", "properties": {"key": {"type": "string"}}},
                ),
            ),
        )

    async def test_maps_request_and_assembles_fragmented_content_and_tool_call(self) -> None:
        """Raw provider chunks precede one response with stable Tool fragments."""
        captured: dict[str, object] = {}

        async def handler(request: web.Request) -> web.StreamResponse:
            captured["authorization"] = request.headers.get("Authorization")
            captured["payload"] = await request.json()
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            events = (
                '{"choices":[{"index":0,"delta":{"content":"hello "},"finish_reason":null}]}',
                '{"choices":[{"index":0,"delta":{"content":"world","tool_calls":[{"index":0,"id":"call-1","function":{"name":"lookup","arguments":"{\\"key\\":"}}]},"finish_reason":null}]}',
                '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"value\\"}"}}]},"finish_reason":"tool_calls"}]}',
            )
            for event in events:
                await response.write(f"data: {event}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
            return response

        adapter = await self._start(handler)
        chunks = []
        result = await collect_adapter_response(adapter, self._request(), chunks.append)

        self.assertEqual(result.content, "hello world")
        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(result.tool_calls[0].id, "call-1")
        self.assertEqual(dict(result.tool_calls[0].arguments), {"key": "value"})
        self.assertEqual(len(chunks), 3)
        self.assertEqual(captured["authorization"], "Bearer test-secret")
        payload = captured["payload"]
        self.assertIsInstance(payload, Mapping)
        assert isinstance(payload, Mapping)
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertTrue(payload["stream"])
        messages = payload["messages"]
        self.assertIsInstance(messages, list)
        assert isinstance(messages, list)
        self.assertEqual(messages[0], {"role": "system", "content": "System prompt"})
        self.assertEqual(messages[-1]["tool_call_id"], "old-call")

    async def test_http_protocol_and_timeout_failures_are_terminal_and_secret_safe(self) -> None:
        """Operational failures expose stable metadata without leaking Authorization."""

        async def denied(_request: web.Request) -> web.Response:
            return web.Response(status=429, text="test-secret rate limited")

        adapter = await self._start(denied)
        with self.assertRaises(LLMProviderError) as caught:
            await collect_adapter_response(adapter, self._request(), lambda _chunk: None)
        self.assertEqual(caught.exception.failure.code, "provider_http")
        self.assertEqual(caught.exception.failure.http_status, 429)
        self.assertTrue(caught.exception.failure.retryable)
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertNotIn("test-secret", repr(adapter.config))

    async def test_invalid_stream_and_timeout_become_provider_failures(self) -> None:
        """Remote protocol and timing failures are distinct terminal results."""

        async def invalid(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: {not-json}\n\n")
            return response

        adapter = await self._start(invalid)
        with self.assertRaises(LLMProviderError) as protocol:
            await collect_adapter_response(adapter, self._request(), lambda _chunk: None)
        self.assertEqual(protocol.exception.failure.code, "provider_protocol")

        await self.server.close()
        self.server = None
        self.app = web.Application()

        async def slow(_request: web.Request) -> web.Response:
            await asyncio.sleep(0.1)
            return web.Response(text="late")

        self.app.router.add_post("/chat/completions", slow)
        self.server = TestServer(self.app)
        await self.server.start_server()
        timeout_adapter = DeepSeekHTTPAdapter(
            DeepSeekHTTPConfig(
                "deepseek",
                "model",
                str(self.server.make_url("/")).rstrip("/"),
                "secret",
                request_timeout=0.01,
            ),
            self.session,
        )
        with self.assertRaises(LLMProviderError) as timeout:
            await collect_adapter_response(
                timeout_adapter,
                self._request(),
                lambda _chunk: None,
            )
        self.assertEqual(timeout.exception.failure.code, "provider_timeout")

    async def test_nonfinite_provider_json_is_a_protocol_failure(self) -> None:
        """Provider-only numeric extensions never become frozen Agent values."""

        async def invalid(request: web.Request) -> web.StreamResponse:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'data: {"choices":[{"index":0,"delta":{"content":"ok"},'
                b'"finish_reason":"stop"}],"usage":NaN}\n\n'
            )
            return response

        adapter = await self._start(invalid)
        with self.assertRaises(LLMProviderError) as caught:
            await collect_adapter_response(adapter, self._request(), lambda _chunk: None)
        self.assertEqual(caught.exception.failure.code, "provider_protocol")

    def test_configuration_rejects_unsafe_urls_and_invalid_timeouts(self) -> None:
        """Credential-bearing URLs and non-positive timeouts fail before I/O."""
        with self.assertRaisesRegex(ValueError, "without credentials"):
            DeepSeekHTTPConfig("p", "m", "https://user:pass@example.test", "key")
        with self.assertRaisesRegex(ValueError, "timeouts"):
            DeepSeekHTTPConfig("p", "m", "https://example.test", "key", request_timeout=0)


if __name__ == "__main__":
    unittest.main()
