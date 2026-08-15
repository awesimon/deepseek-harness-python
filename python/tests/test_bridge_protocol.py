"""Normative Browser Bridge Schema and Codec tests."""

from __future__ import annotations

import json
import unittest
from importlib.resources import files
from typing import cast

from harness.bridge import (
    PROTOCOL_VERSION,
    BridgeEvent,
    BridgeProtocolError,
    DesiredClient,
    Hello,
    PagePluginState,
    PluginLoadResult,
    ReconcileCommand,
    ReconcileComplete,
    RpcCall,
    RpcCancel,
    RpcResult,
    decode_frame,
    encode_frame,
)


class BridgeProtocolTests(unittest.TestCase):
    """Every supported frame round-trips through the normative Schema."""

    def test_supported_frames_round_trip(self) -> None:
        """Typed values encode and decode without losing wire data."""
        frames = (
            Hello(PROTOCOL_VERSION, "page-1", {"com.example.client": "rev-1"}),
            ReconcileCommand(
                PROTOCOL_VERSION,
                "operation-1",
                (
                    DesiredClient(
                        "com.example.client",
                        "rev-1",
                        "/plugins/com.example.client/rev-1/client.js",
                        "a" * 64,
                        "/plugins/com.example.client/rev-1/protocol.json",
                        "optional",
                    ),
                ),
            ),
            PluginLoadResult(
                PROTOCOL_VERSION,
                "operation-1",
                "com.example.client",
                "rev-1",
                PagePluginState.ACTIVE,
            ),
            ReconcileComplete(PROTOCOL_VERSION, "operation-1", True),
            RpcCall(
                PROTOCOL_VERSION,
                "page-1",
                "call-1",
                "com.example.client",
                "rev-1",
                "echo",
                {"value": "hello"},
            ),
            RpcResult(PROTOCOL_VERSION, "call-1", result={"echo": "hello"}),
            RpcResult(
                PROTOCOL_VERSION,
                "call-2",
                error_code="handler_error",
                error_message="failed",
            ),
            RpcCancel(PROTOCOL_VERSION, "page-1", "call-1"),
            BridgeEvent(
                PROTOCOL_VERSION,
                "page-1",
                "com.example.client",
                "rev-1",
                "changed",
                {"value": 1},
            ),
        )

        for frame in frames:
            with self.subTest(frame=type(frame).__name__):
                self.assertEqual(encode_frame(decode_frame(encode_frame(frame))), encode_frame(frame))

    def test_schema_rejects_unknown_fields_versions_types_and_result_forms(self) -> None:
        """Invalid frames fail before a protocol value can be constructed."""
        invalid = (
            {"protocol": "2", "type": "hello", "pageId": "page-1", "loaded": {}},
            {"protocol": "1", "type": "unknown"},
            {
                "protocol": "1",
                "type": "hello",
                "pageId": "page-1",
                "loaded": {},
                "extra": True,
            },
            {
                "protocol": "1",
                "type": "rpc-result",
                "callId": "call-1",
                "result": None,
                "errorCode": "failed",
                "errorMessage": "failed",
            },
        )

        for frame in invalid:
            with self.subTest(frame=frame), self.assertRaises(BridgeProtocolError):
                decode_frame(frame)

    def test_shared_typescript_fixtures_match_the_normative_schema(self) -> None:
        """Shared server fixtures have identical Python and TypeScript outcomes."""
        resource = files("harness.protocol").joinpath("bridge-v1.fixtures.json")
        loaded = cast(dict[str, list[object]], json.loads(resource.read_text(encoding="utf-8")))

        for frame in loaded["serverValid"]:
            with self.subTest(valid=frame):
                decode_frame(frame)
        for frame in loaded["serverInvalid"]:
            with self.subTest(invalid=frame), self.assertRaises(BridgeProtocolError):
                decode_frame(frame)
