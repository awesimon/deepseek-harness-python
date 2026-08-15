"""Durable Session Store and event codec tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from harness.agent import (
    AssistantMessageCommitted,
    LLMRoute,
    Message,
    ModelChunk,
    ModelChunkRecorded,
    ModelRequest,
    ModelRequestRecorded,
    ModelToolDefinition,
    Role,
    SessionEnvelope,
    SessionId,
    SessionLog,
    SessionPersistenceError,
    SessionProjector,
    SQLiteSessionStore,
    StepFailed,
    StepId,
    ToolCall,
    ToolCallId,
    ToolError,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    ToolOutcome,
    TurnId,
    UserInputAccepted,
    encode_event,
)


class SessionPersistenceTests(unittest.TestCase):
    """Exercise restart recovery, strict decoding, and append atomicity."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "state" / "sessions.sqlite"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_all_event_variants_round_trip_with_equal_projection(self) -> None:
        """A reopened store reconstructs every supported immutable event."""
        call = ToolCall(ToolCallId("call-1"), "echo", {"value": "hello"})
        request = ModelRequest(
            TurnId("turn-1"),
            StepId("step-1"),
            LLMRoute("deepseek", "deepseek-chat"),
            "system",
            (Message(Role.USER, "hello"),),
            (ModelToolDefinition("echo", "Echo a value", {"type": "object"}),),
        )
        events = (
            UserInputAccepted(TurnId("turn-1"), (Message(Role.USER, "hello"),)),
            ModelRequestRecorded(request),
            ModelChunkRecorded(StepId("step-1"), ModelChunk({"delta": "h"})),
            AssistantMessageCommitted(
                StepId("step-1"), Message(Role.ASSISTANT, "", (call,)), "tool_calls"
            ),
            ToolExecutionStarted(StepId("step-1"), call),
            ToolExecutionCompleted(
                ToolOutcome(StepId("step-1"), call, error=ToolError("failed", "no result"))
            ),
            StepFailed(StepId("step-1"), "provider_http", "upstream failed"),
        )
        store = SQLiteSessionStore(self.path)
        try:
            log = SessionLog(SessionId("session-1"), store)
            for event in events:
                log.append(event)
            first_encoding = tuple(encode_event(item.event) for item in log.snapshot())
            first_transcript = tuple(
                (entry.kind, entry.content) for entry in SessionProjector(log).transcript()
            )
        finally:
            store.close()
        reopened = SQLiteSessionStore(self.path)
        try:
            restored = SessionLog(SessionId("session-1"), reopened)
            self.assertEqual(
                first_encoding,
                tuple(encode_event(item.event) for item in restored.snapshot()),
            )
            self.assertEqual(
                first_transcript,
                tuple((entry.kind, entry.content) for entry in SessionProjector(restored).transcript()),
            )
        finally:
            reopened.close()

    def test_sequence_conflict_does_not_add_memory_state(self) -> None:
        """A rejected durable sequence leaves the in-memory log unchanged."""
        store = SQLiteSessionStore(self.path)
        try:
            log = SessionLog(SessionId("session-1"), store)
            log.append(UserInputAccepted(TurnId("turn-1"), (Message(Role.USER, "hello"),)))
            with self.assertRaises(SessionPersistenceError):
                store.append(
                    SessionId("session-1"),
                    # The store already expects sequence two, not four.
                    SessionEnvelope(4, StepFailed(StepId("step-1"), "bad", "bad")),
                )
            self.assertEqual(len(log.snapshot()), 1)
        finally:
            store.close()

    def test_unknown_event_and_schema_version_fail_on_open(self) -> None:
        """Corrupt tagged rows never become partially usable Session state."""
        store = SQLiteSessionStore(self.path)
        store.close()
        connection = sqlite3.connect(self.path)
        connection.execute(
            "INSERT INTO session_events VALUES (?, ?, ?, ?, ?)",
            ("session-1", 1, 0, "future_event", "{}"),
        )
        connection.commit()
        connection.close()
        corrupt = SQLiteSessionStore(self.path)
        try:
            with self.assertRaises(SessionPersistenceError):
                corrupt.load(SessionId("session-1"))
        finally:
            corrupt.close()


if __name__ == "__main__":
    unittest.main()
