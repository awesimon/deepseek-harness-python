"""Immutable values shared by the Session, LLM, Tool, and Agent modules."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import NewType, cast

SessionId = NewType("SessionId", str)
TurnId = NewType("TurnId", str)
StepId = NewType("StepId", str)
ToolCallId = NewType("ToolCallId", str)

type JsonPrimitive = None | bool | int | float | str
type JsonValue = JsonPrimitive | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class InvalidJsonValueError(ValueError):
    """Raised when a cross-module value is not lossless JSON data."""


def freeze_json(value: object) -> JsonValue:
    """Validate and recursively freeze one JSON-compatible value."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidJsonValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        mapping = cast(Mapping[object, object], value)
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise InvalidJsonValueError("JSON object keys must be strings")
            frozen[key] = freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return tuple(freeze_json(item) for item in sequence)
    raise InvalidJsonValueError(f"unsupported JSON value: {type(value).__name__}")


def freeze_json_object(value: Mapping[str, object]) -> Mapping[str, JsonValue]:
    """Validate and freeze a JSON object."""
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise InvalidJsonValueError("expected a JSON object")
    return cast(Mapping[str, JsonValue], frozen)


def thaw_json(value: JsonValue) -> object:
    """Convert a frozen JSON value to ordinary dict and list containers."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


class Role(str, Enum):
    """Roles admitted to model history."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One model-requested Tool invocation."""

    id: ToolCallId
    name: str
    arguments: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("tool call id must not be empty")
        if not self.name:
            raise ValueError("tool name must not be empty")
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))


@dataclass(frozen=True, slots=True)
class Message:
    """One immutable model-history message."""

    role: Role
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: ToolCallId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        if self.tool_calls and self.role is not Role.ASSISTANT:
            raise ValueError("only assistant messages may contain tool calls")
        if self.role is Role.TOOL and self.tool_call_id is None:
            raise ValueError("tool messages require a tool call id")
        if self.role is not Role.TOOL and self.tool_call_id is not None:
            raise ValueError("only tool messages may carry a tool call id")


@dataclass(frozen=True, slots=True)
class ModelToolDefinition:
    """Model-visible Tool metadata captured for one Step."""

    name: str
    description: str
    parameters: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("tool name must not be empty")
        if not self.description:
            raise ValueError(f"tool {self.name!r} requires a description")
        object.__setattr__(self, "parameters", freeze_json_object(self.parameters))


@dataclass(frozen=True, slots=True)
class LLMRoute:
    """Explicit provider and model selection for one request."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise ValueError("LLM provider and model must not be empty")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Complete effective input captured before one adapter call."""

    turn_id: TurnId
    step_id: StepId
    route: LLMRoute
    system_prompt: str
    messages: tuple[Message, ...]
    tools: tuple[ModelToolDefinition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))


@dataclass(frozen=True, slots=True)
class ModelChunk:
    """One raw presentation or diagnostic chunk from an adapter."""

    data: JsonValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", freeze_json(self.data))


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Exactly one terminal response from an adapter."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        if not self.finish_reason:
            raise ValueError("finish reason must not be empty")

    @property
    def message(self) -> Message:
        """Return the Assistant Message committed to Session history."""
        return Message(Role.ASSISTANT, self.content, self.tool_calls)


@dataclass(frozen=True, slots=True)
class ModelProviderFailure:
    """One credential-free terminal failure from an LLM provider."""

    code: str
    message: str
    retryable: bool = False
    http_status: int | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("provider failures require a code and message")
        if self.http_status is not None and (
            isinstance(self.http_status, bool) or not 100 <= self.http_status <= 599
        ):
            raise ValueError("provider HTTP status must be between 100 and 599")


type AdapterOutput = ModelChunk | ModelResponse | ModelProviderFailure


@dataclass(frozen=True, slots=True)
class ToolError:
    """Structured model-visible Tool execution failure."""

    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("tool errors require a code and message")


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Resolved Tool Call about to begin execution."""

    step_id: StepId
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """One logged Tool result or failure."""

    step_id: StepId
    call: ToolCall
    result: JsonValue | None = None
    error: ToolError | None = None

    def __post_init__(self) -> None:
        if self.error is not None and self.result is not None:
            raise ValueError("failed tool outcomes cannot also contain a result")
        object.__setattr__(self, "result", freeze_json(self.result))
