"""Append-only Session events and deterministic projections."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .values import (
    JsonValue,
    Message,
    ModelChunk,
    ModelRequest,
    Role,
    SessionId,
    StepId,
    ToolCall,
    ToolOutcome,
    TurnId,
    freeze_json,
    thaw_json,
)

if TYPE_CHECKING:
    from .persistence import SessionStore


class UnknownSessionEventError(RuntimeError):
    """Raised when a projection encounters an unsupported required Event."""


@dataclass(frozen=True, slots=True)
class UserInputAccepted:
    """User Messages accepted into one Turn."""

    turn_id: TurnId
    messages: tuple[Message, ...]

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        if not messages or any(message.role is not Role.USER for message in messages):
            raise ValueError("accepted input requires one or more user messages")
        object.__setattr__(self, "messages", messages)


@dataclass(frozen=True, slots=True)
class ModelRequestRecorded:
    """Complete effective request captured before adapter execution."""

    request: ModelRequest


@dataclass(frozen=True, slots=True)
class ModelChunkRecorded:
    """Raw adapter chunk retained in arrival order."""

    step_id: StepId
    chunk: ModelChunk


@dataclass(frozen=True, slots=True)
class AssistantMessageCommitted:
    """Terminal Assistant Message used by subsequent model history."""

    step_id: StepId
    message: Message
    finish_reason: str

    def __post_init__(self) -> None:
        if self.message.role is not Role.ASSISTANT:
            raise ValueError("committed model output must be an assistant message")


@dataclass(frozen=True, slots=True)
class ToolExecutionStarted:
    """Exact Tool invocation captured before handler execution."""

    step_id: StepId
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolExecutionCompleted:
    """Tool result or structured failure captured after execution."""

    outcome: ToolOutcome


@dataclass(frozen=True, slots=True)
class StepFailed:
    """Step failure recorded without inventing an Assistant Message."""

    step_id: StepId
    code: str
    message: str


type SessionEvent = (
    UserInputAccepted
    | ModelRequestRecorded
    | ModelChunkRecorded
    | AssistantMessageCommitted
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | StepFailed
)


@dataclass(frozen=True, slots=True)
class SessionEnvelope:
    """One monotonically ordered Session Event."""

    sequence: int
    event: SessionEvent


class SessionLog:
    """Append-only Session Event authority with optional durable storage."""

    def __init__(self, session_id: SessionId, store: SessionStore | None = None) -> None:
        if not session_id:
            raise ValueError("session id must not be empty")
        self.session_id = session_id
        self._store = store
        self._events: list[SessionEnvelope] = [] if store is None else list(store.load(session_id))

    def append(self, event: SessionEvent) -> SessionEnvelope:
        """Append one immutable Event and assign its sequence number."""
        envelope = SessionEnvelope(len(self._events) + 1, event)
        if self._store is not None:
            self._store.append(self.session_id, envelope)
        self._events.append(envelope)
        return envelope

    def snapshot(self) -> tuple[SessionEnvelope, ...]:
        """Return an immutable snapshot of all current Events."""
        return tuple(self._events)

    def close(self) -> None:
        """Close optional durable storage."""
        if self._store is not None:
            self._store.close()


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One user-visible projection entry."""

    sequence: int
    kind: str
    content: JsonValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", freeze_json(self.content))


class SessionProjector:
    """Derive model history and transcript entries from one Session Log."""

    def __init__(self, log: SessionLog) -> None:
        self.log = log

    def model_history(self) -> tuple[Message, ...]:
        """Project the only Message history admitted to later model requests."""
        messages: list[Message] = []
        for envelope in self.log.snapshot():
            event = envelope.event
            if isinstance(event, UserInputAccepted):
                messages.extend(event.messages)
            elif isinstance(event, AssistantMessageCommitted):
                messages.append(event.message)
            elif isinstance(event, ToolExecutionCompleted):
                messages.append(_tool_message(event.outcome))
            elif isinstance(
                event,
                (ModelRequestRecorded, ModelChunkRecorded, ToolExecutionStarted, StepFailed),
            ):  # pyright: ignore[reportUnnecessaryIsInstance] -- runtime Event sets are extensible
                continue
            else:
                raise UnknownSessionEventError(
                    f"unsupported Session Event: {type(event).__name__}"
                )
        return tuple(messages)

    def transcript(self) -> tuple[TranscriptEntry, ...]:
        """Project user, Assistant, Tool, and Step failure entries."""
        entries: list[TranscriptEntry] = []
        for envelope in self.log.snapshot():
            event = envelope.event
            if isinstance(event, UserInputAccepted):
                entries.extend(
                    TranscriptEntry(envelope.sequence, "user", message.content)
                    for message in event.messages
                )
            elif isinstance(event, AssistantMessageCommitted):
                entries.append(
                    TranscriptEntry(envelope.sequence, "assistant", event.message.content)
                )
            elif isinstance(event, ToolExecutionCompleted):
                entries.append(
                    TranscriptEntry(
                        envelope.sequence,
                        "tool",
                        _tool_payload(event.outcome),
                    )
                )
            elif isinstance(event, StepFailed):
                entries.append(
                    TranscriptEntry(
                        envelope.sequence,
                        "step-error",
                        {"code": event.code, "message": event.message},
                    )
                )
            elif isinstance(
                event,
                (ModelRequestRecorded, ModelChunkRecorded, ToolExecutionStarted),
            ):  # pyright: ignore[reportUnnecessaryIsInstance] -- runtime Event sets are extensible
                continue
            else:
                raise UnknownSessionEventError(
                    f"unsupported Session Event: {type(event).__name__}"
                )
        return tuple(entries)


def _tool_payload(outcome: ToolOutcome) -> JsonValue:
    if outcome.error is None:
        return freeze_json({"ok": True, "result": outcome.result})
    return freeze_json(
        {
            "ok": False,
            "error": {"code": outcome.error.code, "message": outcome.error.message},
        }
    )


def _tool_message(outcome: ToolOutcome) -> Message:
    content = json.dumps(
        thaw_json(_tool_payload(outcome)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return Message(Role.TOOL, content, tool_call_id=outcome.call.id)
