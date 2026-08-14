"""Behavior tests for immutable Agent values and Session projection."""

from __future__ import annotations

import unittest
from typing import Any, cast

from harness.agent import (
    AssistantMessageCommitted,
    Message,
    ModelChunk,
    ModelChunkRecorded,
    Role,
    SessionId,
    SessionLog,
    SessionProjector,
    StepId,
    ToolCall,
    ToolCallId,
    ToolExecutionCompleted,
    ToolOutcome,
    TurnId,
    UnknownSessionEventError,
    UserInputAccepted,
)


class AgentSessionTests(unittest.TestCase):
    """Exercise append ordering, frozen values, and deterministic history."""

    def test_append_assigns_monotonic_sequences_and_snapshot_is_immutable(self) -> None:
        """Snapshots retain ordered immutable Envelopes after later appends."""
        log = SessionLog(SessionId("session-1"))
        first = log.append(
            UserInputAccepted(TurnId("turn-1"), (Message(Role.USER, "hello"),))
        )
        snapshot = log.snapshot()
        second = log.append(ModelChunkRecorded(StepId("step-1"), ModelChunk({"text": "h"})))

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(len(snapshot), 1)
        chunk = cast(ModelChunkRecorded, second.event).chunk
        with self.assertRaises(TypeError):
            cast(Any, chunk.data)["text"] = "changed"

    def test_model_history_uses_only_committed_messages_and_tool_outcomes(self) -> None:
        """Raw chunks stay durable without entering later model history."""
        log = SessionLog(SessionId("session-1"))
        turn = TurnId("turn-1")
        step = StepId("step-1")
        call = ToolCall(ToolCallId("call-1"), "echo", {"value": "hello"})
        log.append(UserInputAccepted(turn, (Message(Role.USER, "hello"),)))
        log.append(ModelChunkRecorded(step, ModelChunk("partial")))
        log.append(
            AssistantMessageCommitted(
                step,
                Message(Role.ASSISTANT, "", (call,)),
                "tool_calls",
            )
        )
        log.append(ToolExecutionCompleted(ToolOutcome(step, call, result={"value": "hello"})))

        history = SessionProjector(log).model_history()

        self.assertEqual([message.role for message in history], [Role.USER, Role.ASSISTANT, Role.TOOL])
        self.assertEqual(history[-1].tool_call_id, call.id)
        self.assertEqual(history[-1].content, '{"ok":true,"result":{"value":"hello"}}')

    def test_projection_rejects_an_unknown_required_event(self) -> None:
        """A new required Event cannot disappear from history silently."""
        log = SessionLog(SessionId("session-1"))
        log.append(cast(Any, object()))

        with self.assertRaises(UnknownSessionEventError):
            SessionProjector(log).model_history()
