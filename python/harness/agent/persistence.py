"""SQLite persistence for append-only Agent Session events."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from .session import (
    AssistantMessageCommitted,
    ModelChunkRecorded,
    ModelRequestRecorded,
    SessionEnvelope,
    SessionEvent,
    StepFailed,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    UserInputAccepted,
)
from .values import (
    LLMRoute,
    Message,
    ModelChunk,
    ModelRequest,
    ModelToolDefinition,
    Role,
    SessionId,
    StepId,
    ToolCall,
    ToolCallId,
    ToolError,
    ToolOutcome,
    TurnId,
    freeze_json,
    freeze_json_object,
    thaw_json,
)

SESSION_FORMAT_VERSION = 0
SCHEMA_VERSION = 1


class SessionPersistenceError(RuntimeError):
    """Raised when durable Session state cannot be opened, decoded, or appended."""


class SessionStore(Protocol):
    """Persistence operations required by :class:`SessionLog`."""

    def load(self, session_id: SessionId) -> tuple[SessionEnvelope, ...]:
        """Load one complete, ordered Session."""
        ...

    def append(self, session_id: SessionId, envelope: SessionEnvelope) -> None:
        """Durably append one envelope at its expected sequence."""

    def close(self) -> None:
        """Close the underlying storage."""


class SQLiteSessionStore:
    """Single-process SQLite store for immutable Session Event envelopes."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        connection: sqlite3.Connection | None = None
        try:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection = connection
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._initialize()
        except SessionPersistenceError:
            if connection is not None:
                connection.close()
            raise
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            raise SessionPersistenceError(f"cannot open Session database {self.path!r}") from error

    def _initialize(self) -> None:
        current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise SessionPersistenceError(
                f"Session database schema {current} is newer than supported {SCHEMA_VERSION}"
            )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                format_version INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (session_id, sequence)
            )
            """
        )
        if current != SCHEMA_VERSION:
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def load(self, session_id: SessionId) -> tuple[SessionEnvelope, ...]:
        """Load and validate every event for one Session ID."""
        with self._lock:
            try:
                rows = self._connection.execute(
                    """
                    SELECT sequence, format_version, event_type, payload
                    FROM session_events
                    WHERE session_id = ?
                    ORDER BY sequence
                    """,
                    (str(session_id),),
                ).fetchall()
            except sqlite3.Error as error:
                raise SessionPersistenceError("cannot read Session events") from error
        envelopes: list[SessionEnvelope] = []
        for expected, row in enumerate(rows, start=1):
            sequence, format_version, event_type, payload = row
            if sequence != expected:
                raise SessionPersistenceError("Session events contain a sequence gap")
            if format_version != SESSION_FORMAT_VERSION:
                raise SessionPersistenceError(
                    f"unsupported Session format version {format_version!r}"
                )
            if not isinstance(event_type, str) or not isinstance(payload, str):
                raise SessionPersistenceError("Session event row has invalid storage types")
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError as error:
                raise SessionPersistenceError("Session event payload is not valid JSON") from error
            try:
                event = decode_event(event_type, decoded)
            except (TypeError, ValueError, KeyError) as error:
                raise SessionPersistenceError(
                    f"Session event {expected} cannot be decoded"
                ) from error
            envelopes.append(SessionEnvelope(expected, event))
        return tuple(envelopes)

    def append(self, session_id: SessionId, envelope: SessionEnvelope) -> None:
        """Append one event only when its sequence is the next durable sequence."""
        event_type, payload = encode_event(envelope.event)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM session_events WHERE session_id = ?",
                    (str(session_id),),
                ).fetchone()
                expected = int(row[0]) + 1
                if envelope.sequence != expected:
                    raise SessionPersistenceError(
                        f"Session append expected sequence {expected}, got {envelope.sequence}"
                    )
                self._connection.execute(
                    """
                    INSERT INTO session_events
                        (session_id, sequence, format_version, event_type, payload)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(session_id),
                        envelope.sequence,
                        SESSION_FORMAT_VERSION,
                        event_type,
                        payload,
                    ),
                )
                self._connection.commit()
            except SessionPersistenceError:
                self._connection.rollback()
                raise
            except sqlite3.Error as error:
                self._connection.rollback()
                raise SessionPersistenceError("cannot append Session event") from error

    def close(self) -> None:
        """Close the SQLite connection once."""
        with self._lock:
            self._connection.close()


def encode_event(event: SessionEvent) -> tuple[str, str]:
    """Encode one event as a stable tagged JSON payload."""
    if isinstance(event, UserInputAccepted):
        event_type = "user_input_accepted"
        payload: object = {
            "turn_id": str(event.turn_id),
            "messages": [_message_json(message) for message in event.messages],
        }
    elif isinstance(event, ModelRequestRecorded):
        event_type = "model_request_recorded"
        payload = {"request": _model_request_json(event.request)}
    elif isinstance(event, ModelChunkRecorded):
        event_type = "model_chunk_recorded"
        payload = {"step_id": str(event.step_id), "chunk": thaw_json(event.chunk.data)}
    elif isinstance(event, AssistantMessageCommitted):
        event_type = "assistant_message_committed"
        payload = {
            "step_id": str(event.step_id),
            "message": _message_json(event.message),
            "finish_reason": event.finish_reason,
        }
    elif isinstance(event, ToolExecutionStarted):
        event_type = "tool_execution_started"
        payload = {"step_id": str(event.step_id), "call": _tool_call_json(event.call)}
    elif isinstance(event, ToolExecutionCompleted):
        event_type = "tool_execution_completed"
        payload = {"outcome": _tool_outcome_json(event.outcome)}
    elif isinstance(event, StepFailed):  # pyright: ignore[reportUnnecessaryIsInstance] -- extensible Event union
        event_type = "step_failed"
        payload = {"step_id": str(event.step_id), "code": event.code, "message": event.message}
    else:
        raise SessionPersistenceError(f"unsupported Session event: {type(event).__name__}")
    try:
        return event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise SessionPersistenceError("Session event is not strict JSON") from error


def event_json(event: SessionEvent) -> Mapping[str, object]:
    """Return one event as a tagged JSON-compatible object for read APIs."""
    event_type, payload = encode_event(event)
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise SessionPersistenceError("encoded Session event is not an object")
    return {"type": event_type, "payload": cast(dict[str, object], decoded)}


def decode_event(event_type: str, value: object) -> SessionEvent:
    """Decode one tagged JSON object into an immutable Session Event."""
    payload = _object(value)
    if event_type == "user_input_accepted":
        return UserInputAccepted(
            TurnId(_string(payload, "turn_id")),
            tuple(_message(item) for item in _array(payload, "messages")),
        )
    if event_type == "model_request_recorded":
        return ModelRequestRecorded(_model_request(payload.get("request")))
    if event_type == "model_chunk_recorded":
        return ModelChunkRecorded(
            StepId(_string(payload, "step_id")), ModelChunk(freeze_json(payload.get("chunk")))
        )
    if event_type == "assistant_message_committed":
        return AssistantMessageCommitted(
            StepId(_string(payload, "step_id")),
            _message(payload.get("message")),
            _string(payload, "finish_reason"),
        )
    if event_type == "tool_execution_started":
        return ToolExecutionStarted(
            StepId(_string(payload, "step_id")), _tool_call(payload.get("call"))
        )
    if event_type == "tool_execution_completed":
        return ToolExecutionCompleted(_tool_outcome(payload.get("outcome")))
    if event_type == "step_failed":
        return StepFailed(
            StepId(_string(payload, "step_id")),
            _string(payload, "code"),
            _string(payload, "message"),
        )
    raise ValueError(f"unknown Session event type {event_type!r}")


def _message_json(message: Message) -> Mapping[str, object]:
    return {
        "role": message.role.value,
        "content": message.content,
        "tool_calls": [_tool_call_json(call) for call in message.tool_calls],
        "tool_call_id": message.tool_call_id,
    }


def _message(value: object) -> Message:
    payload = _object(value)
    raw_id = payload.get("tool_call_id")
    return Message(
        Role(_string(payload, "role")),
        _string(payload, "content"),
        tuple(_tool_call(item) for item in _array(payload, "tool_calls")),
        None if raw_id is None else ToolCallId(_as_string(raw_id)),
    )


def _tool_call_json(call: ToolCall) -> Mapping[str, object]:
    return {"id": str(call.id), "name": call.name, "arguments": thaw_json(call.arguments)}


def _tool_call(value: object) -> ToolCall:
    payload = _object(value)
    return ToolCall(
        ToolCallId(_string(payload, "id")),
        _string(payload, "name"),
        freeze_json_object(_object(payload.get("arguments"))),
    )


def _model_request_json(request: ModelRequest) -> Mapping[str, object]:
    return {
        "turn_id": str(request.turn_id),
        "step_id": str(request.step_id),
        "route": {"provider": request.route.provider, "model": request.route.model},
        "system_prompt": request.system_prompt,
        "messages": [_message_json(message) for message in request.messages],
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": thaw_json(tool.parameters),
            }
            for tool in request.tools
        ],
    }


def _model_request(value: object) -> ModelRequest:
    payload = _object(value)
    route = _object(payload.get("route"))
    tools = tuple(_tool_definition(item) for item in _array(payload, "tools"))
    return ModelRequest(
        TurnId(_string(payload, "turn_id")),
        StepId(_string(payload, "step_id")),
        LLMRoute(_string(route, "provider"), _string(route, "model")),
        _string(payload, "system_prompt"),
        tuple(_message(item) for item in _array(payload, "messages")),
        tools,
    )


def _tool_definition(value: object) -> ModelToolDefinition:
    payload = _object(value)
    return ModelToolDefinition(
        _string(payload, "name"),
        _string(payload, "description"),
        freeze_json_object(_object(payload.get("parameters"))),
    )


def _tool_outcome_json(outcome: ToolOutcome) -> Mapping[str, object]:
    error = None
    if outcome.error is not None:
        error = {"code": outcome.error.code, "message": outcome.error.message}
    return {
        "step_id": str(outcome.step_id),
        "call": _tool_call_json(outcome.call),
        "result": thaw_json(outcome.result),
        "error": error,
    }


def _tool_outcome(value: object) -> ToolOutcome:
    payload = _object(value)
    raw_error = payload.get("error")
    error = None
    if raw_error is not None:
        error_payload = _object(raw_error)
        error = ToolError(_string(error_payload, "code"), _string(error_payload, "message"))
    return ToolOutcome(
        StepId(_string(payload, "step_id")),
        _tool_call(payload.get("call")),
        freeze_json(payload.get("result")),
        error,
    )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("expected a JSON object")
    result: dict[str, object] = {}
    for key, item in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise TypeError("JSON object keys must be strings")
        result[key] = item
    return result


def _array(payload: Mapping[str, object], key: str) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise TypeError(f"field {key!r} must be an array")
    return cast(list[object], value)


def _string(payload: Mapping[str, object], key: str) -> str:
    return _as_string(payload.get(key))


def _as_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value
