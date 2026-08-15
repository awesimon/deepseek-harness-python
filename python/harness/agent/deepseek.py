"""DeepSeek-compatible Chat Completions streaming adapter."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, StreamReader

from .values import (
    AdapterOutput,
    InvalidJsonValueError,
    JsonValue,
    Message,
    ModelChunk,
    ModelProviderFailure,
    ModelRequest,
    ModelResponse,
    Role,
    ToolCall,
    ToolCallId,
    freeze_json_object,
    thaw_json,
)

_MAX_ERROR_BODY_BYTES = 8192


class _ProviderProtocolError(RuntimeError):
    """Internal validated-provider response failure."""


@dataclass(frozen=True, slots=True)
class DeepSeekHTTPConfig:
    """Validated configuration for one DeepSeek-compatible exact route."""

    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    connect_timeout: float = 10.0
    request_timeout: float = 120.0

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("LLM provider and model must not be empty")
        if not self.api_key:
            raise ValueError("LLM API key must not be empty")
        if (
            isinstance(self.connect_timeout, bool)
            or isinstance(self.request_timeout, bool)
            or not math.isfinite(self.connect_timeout)
            or not math.isfinite(self.request_timeout)
            or self.connect_timeout <= 0
            or self.request_timeout <= 0
        ):
            raise ValueError("LLM timeouts must be positive")
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "LLM base URL must be an HTTP(S) origin or path without credentials, query, or fragment"
            )
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @property
    def endpoint(self) -> str:
        """Return the fixed Chat Completions endpoint."""
        return f"{self.base_url}/chat/completions"


@dataclass(slots=True)
class _ToolCallAssembly:
    identifier: str | None = None
    name: str | None = None
    arguments: list[str] = field(default_factory=list[str])


class DeepSeekHTTPAdapter:
    """Map Agent Spine requests to DeepSeek-compatible streaming HTTP."""

    def __init__(self, config: DeepSeekHTTPConfig, session: ClientSession) -> None:
        self.config = config
        self.session = session

    async def stream(self, request: ModelRequest) -> AsyncIterator[AdapterOutput]:
        """Yield raw chunks followed by one assembled response or provider failure."""
        timeout = ClientTimeout(
            total=self.config.request_timeout,
            connect=self.config.connect_timeout,
        )
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        try:
            async with self.session.post(
                self.config.endpoint,
                headers=headers,
                json=_request_payload(request),
                timeout=timeout,
            ) as response:
                if response.status < 200 or response.status >= 300:
                    body = await response.content.read(_MAX_ERROR_BODY_BYTES + 1)
                    truncated = len(body) > _MAX_ERROR_BODY_BYTES
                    detail = body[:_MAX_ERROR_BODY_BYTES].decode("utf-8", errors="replace").strip()
                    detail = _redact_credentials(detail, self.config.api_key)
                    suffix = " (truncated)" if truncated else ""
                    message = f"LLM provider returned HTTP {response.status}"
                    if detail:
                        message = f"{message}: {detail}{suffix}"
                    yield ModelProviderFailure(
                        "provider_http",
                        message,
                        retryable=_retryable_status(response.status),
                        http_status=response.status,
                    )
                    return
                if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                    yield ModelProviderFailure(
                        "provider_protocol",
                        "LLM provider response is not an event stream",
                    )
                    return
                try:
                    async for output in _decode_stream(response.content):
                        yield output
                except _ProviderProtocolError as error:
                    yield ModelProviderFailure("provider_protocol", str(error))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            yield ModelProviderFailure(
                "provider_timeout",
                "LLM provider request timed out",
                retryable=True,
            )
        except ClientError as error:
            yield ModelProviderFailure(
                "provider_network",
                f"LLM provider request failed: {type(error).__name__}",
                retryable=True,
            )


async def _decode_stream(content: StreamReader) -> AsyncIterator[AdapterOutput]:
    assembled_content: list[str] = []
    tool_calls: dict[int, _ToolCallAssembly] = {}
    finish_reason: str | None = None
    saw_data = False
    async for data in _iter_sse_data(content):
        if data == "[DONE]":
            break
        saw_data = True
        if finish_reason is not None:
            raise _ProviderProtocolError("provider emitted data after its terminal choice")
        try:
            decoded = json.loads(data)
        except json.JSONDecodeError as error:
            raise _ProviderProtocolError("provider emitted invalid stream JSON") from error
        if not isinstance(decoded, Mapping):
            raise _ProviderProtocolError("provider stream data must be a JSON object")
        raw = cast(Mapping[str, object], decoded)
        try:
            chunk = ModelChunk(cast(JsonValue, raw))
        except InvalidJsonValueError as error:
            raise _ProviderProtocolError("provider stream data is not valid JSON") from error
        yield chunk
        choice = _single_choice(raw)
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            raise _ProviderProtocolError("provider choice requires an object delta")
        _apply_delta(cast(Mapping[str, object], delta), assembled_content, tool_calls)
        raw_finish = choice.get("finish_reason")
        if raw_finish is not None:
            if not isinstance(raw_finish, str) or not raw_finish:
                raise _ProviderProtocolError("provider finish reason must be a non-empty string")
            finish_reason = raw_finish
    if not saw_data:
        raise _ProviderProtocolError("provider stream completed without data")
    if finish_reason is None:
        raise _ProviderProtocolError("provider stream completed without a terminal choice")
    try:
        finished_calls = _finish_tool_calls(tool_calls)
    except InvalidJsonValueError as error:
        raise _ProviderProtocolError("provider Tool arguments are not valid JSON") from error
    yield ModelResponse("".join(assembled_content), finished_calls, finish_reason)


async def _iter_sse_data(content: StreamReader) -> AsyncIterator[str]:
    data_lines: list[str] = []
    while True:
        try:
            raw_line = await content.readline()
        except ValueError as error:
            raise _ProviderProtocolError("provider stream contains an oversized line") from error
        if not raw_line:
            if data_lines:
                yield "\n".join(data_lines)
            return
        try:
            line = raw_line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as error:
            raise _ProviderProtocolError("provider stream is not UTF-8") from error
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        field_name, separator, value = line.partition(":")
        if field_name != "data":
            continue
        if separator and value.startswith(" "):
            value = value[1:]
        data_lines.append(value)


def _request_payload(request: ModelRequest) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.extend(_message_payload(message) for message in request.messages)
    payload: dict[str, object] = {
        "model": request.route.model,
        "messages": messages,
        "stream": True,
    }
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": thaw_json(tool.parameters),
                },
            }
            for tool in request.tools
        ]
    return payload


def _message_payload(message: Message) -> dict[str, object]:
    payload: dict[str, object] = {"role": message.role.value, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        thaw_json(call.arguments),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            }
            for call in message.tool_calls
        ]
    if message.role is Role.TOOL:
        assert message.tool_call_id is not None
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _single_choice(payload: Mapping[str, object]) -> Mapping[str, object]:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        raise _ProviderProtocolError("provider stream data requires exactly one choice")
    typed_choices = cast(list[object], choices)
    if len(typed_choices) != 1:
        raise _ProviderProtocolError("provider stream data requires exactly one choice")
    choice = typed_choices[0]
    if not isinstance(choice, Mapping):
        raise _ProviderProtocolError("provider choice must be a JSON object")
    raw_choice = cast(Mapping[str, object], choice)
    index = raw_choice.get("index")
    if index not in (None, 0):
        raise _ProviderProtocolError("provider stream returned an unsupported choice index")
    return raw_choice


def _apply_delta(
    delta: Mapping[str, object],
    content: list[str],
    tool_calls: dict[int, _ToolCallAssembly],
) -> None:
    raw_content = delta.get("content")
    if raw_content is not None:
        if not isinstance(raw_content, str):
            raise _ProviderProtocolError("provider content delta must be a string")
        content.append(raw_content)
    raw_calls = delta.get("tool_calls")
    if raw_calls is None:
        return
    if not isinstance(raw_calls, list):
        raise _ProviderProtocolError("provider Tool Call delta must be an array")
    for raw_call in cast(list[object], raw_calls):
        if not isinstance(raw_call, Mapping):
            raise _ProviderProtocolError("provider Tool Call delta must be an object")
        call = cast(Mapping[str, object], raw_call)
        index = call.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise _ProviderProtocolError("provider Tool Call index must be non-negative")
        assembly = tool_calls.setdefault(index, _ToolCallAssembly())
        _set_consistent(assembly, "identifier", call.get("id"), "Tool Call ID")
        function = call.get("function")
        if function is None:
            continue
        if not isinstance(function, Mapping):
            raise _ProviderProtocolError("provider Tool Call function must be an object")
        raw_function = cast(Mapping[str, object], function)
        _set_consistent(assembly, "name", raw_function.get("name"), "Tool name")
        arguments = raw_function.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, str):
                raise _ProviderProtocolError("provider Tool arguments delta must be a string")
            assembly.arguments.append(arguments)


def _set_consistent(
    assembly: _ToolCallAssembly,
    field_name: str,
    value: object,
    label: str,
) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise _ProviderProtocolError(f"provider {label} must be a non-empty string")
    current = getattr(assembly, field_name)
    if current is not None and current != value:
        raise _ProviderProtocolError(f"provider changed {label} between fragments")
    setattr(assembly, field_name, value)


def _finish_tool_calls(assemblies: Mapping[int, _ToolCallAssembly]) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for index in sorted(assemblies):
        assembly = assemblies[index]
        if assembly.identifier is None or assembly.name is None:
            raise _ProviderProtocolError("provider returned an incomplete Tool Call")
        raw_arguments = "".join(assembly.arguments)
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as error:
            raise _ProviderProtocolError("provider returned invalid Tool arguments JSON") from error
        if not isinstance(arguments, Mapping):
            raise _ProviderProtocolError("provider Tool arguments must decode to an object")
        calls.append(
            ToolCall(
                ToolCallId(assembly.identifier),
                assembly.name,
                freeze_json_object(cast(Mapping[str, object], arguments)),
            )
        )
    return tuple(calls)


def _retryable_status(status: int) -> bool:
    return status in {408, 409, 425, 429} or status >= 500


def _redact_credentials(detail: str, api_key: str) -> str:
    return detail.replace(f"Bearer {api_key}", "Bearer [REDACTED]").replace(
        api_key,
        "[REDACTED]",
    )
