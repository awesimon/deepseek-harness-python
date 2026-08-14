"""Schema-validated Browser Bridge frame encoding and decoding."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from importlib.resources import files
from typing import cast

from jsonschema import Draft202012Validator, ValidationError

from harness.agent.values import freeze_json, freeze_json_object, thaw_json

from .protocol import (
    PROTOCOL_VERSION,
    BridgeEvent,
    BridgeFrame,
    DesiredClient,
    Hello,
    PagePluginState,
    PluginLoadResult,
    ReconcileCommand,
    ReconcileComplete,
    RpcCall,
    RpcCancel,
    RpcResult,
)

type WireJson = None | bool | int | float | str | list["WireJson"] | dict[str, "WireJson"]


class BridgeProtocolError(ValueError):
    """Structured validation failure for one untrusted wire frame."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_schema() -> Mapping[str, object]:
    resource = files("harness.protocol").joinpath("bridge-v1.schema.json")
    raw: object = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("Browser Bridge Schema root must be an object")
    return cast(dict[str, object], raw)


BRIDGE_SCHEMA = _load_schema()
_VALIDATOR = Draft202012Validator(BRIDGE_SCHEMA)


def validate_wire_frame(value: object) -> None:
    """Validate one frame against the bundled normative Schema."""
    validated = cast(WireJson, thaw_json(freeze_json(value)))
    # jsonschema's overload exposes an unknown generator type despite a JSON-compatible input.
    errors = cast(
        Iterator[ValidationError],
        _VALIDATOR.iter_errors(validated),  # pyright: ignore[reportUnknownMemberType]
    )
    error = next(errors, None)
    if error is not None:
        location = error.json_path or "$"
        raise BridgeProtocolError("schema_error", f"{location}: {error.message}")


def decode_frame(value: object) -> BridgeFrame:
    """Validate untrusted JSON data and return one immutable frame value."""
    frame = _object(value, "frame")
    protocol = frame.get("protocol")
    if protocol != PROTOCOL_VERSION:
        raise BridgeProtocolError("unsupported_protocol", f"unsupported protocol: {protocol!r}")
    frame_type = frame.get("type")
    supported = {
        "hello",
        "reconcile",
        "plugin-result",
        "reconcile-complete",
        "rpc-call",
        "rpc-result",
        "rpc-cancel",
        "event",
    }
    if frame_type not in supported:
        raise BridgeProtocolError("unknown_frame", f"unknown frame type: {frame_type!r}")
    validate_wire_frame(frame)

    if frame_type == "hello":
        loaded = _object(frame["loaded"], "loaded")
        return Hello(PROTOCOL_VERSION, _string(frame["pageId"]), _string_map(loaded))
    if frame_type == "reconcile":
        desired = tuple(_decode_desired(item) for item in _array(frame["desired"], "desired"))
        return ReconcileCommand(PROTOCOL_VERSION, _string(frame["operationId"]), desired)
    if frame_type == "plugin-result":
        return PluginLoadResult(
            PROTOCOL_VERSION,
            _string(frame["operationId"]),
            _string(frame["pluginId"]),
            _string(frame["revision"]),
            PagePluginState(_string(frame["state"])),
            _nullable_string(frame["error"]),
        )
    if frame_type == "reconcile-complete":
        return ReconcileComplete(
            PROTOCOL_VERSION,
            _string(frame["operationId"]),
            _boolean(frame["success"]),
            _nullable_string(frame["error"]),
        )
    if frame_type == "rpc-call":
        arguments = freeze_json_object(_object(frame["arguments"], "arguments"))
        return RpcCall(
            PROTOCOL_VERSION,
            _string(frame["pageId"]),
            _string(frame["callId"]),
            _string(frame["pluginId"]),
            _string(frame["revision"]),
            _string(frame["method"]),
            arguments,
        )
    if frame_type == "rpc-result":
        if "errorCode" in frame:
            return RpcResult(
                PROTOCOL_VERSION,
                _string(frame["callId"]),
                error_code=_string(frame["errorCode"]),
                error_message=_string(frame["errorMessage"]),
            )
        return RpcResult(
            PROTOCOL_VERSION,
            _string(frame["callId"]),
            result=freeze_json(frame["result"]),
        )
    if frame_type == "rpc-cancel":
        return RpcCancel(
            PROTOCOL_VERSION,
            _string(frame["pageId"]),
            _string(frame["callId"]),
        )
    if frame_type == "event":
        return BridgeEvent(
            PROTOCOL_VERSION,
            _string(frame["pageId"]),
            _string(frame["pluginId"]),
            _string(frame["revision"]),
            _string(frame["name"]),
            freeze_json(frame["payload"]),
        )
    raise AssertionError(f"unhandled validated frame type: {frame_type}")


def encode_frame(frame: BridgeFrame) -> dict[str, object]:
    """Encode one typed frame and mechanically validate the result."""
    encoded: dict[str, object]
    if isinstance(frame, Hello):
        encoded = {
            "protocol": frame.protocol,
            "type": "hello",
            "pageId": frame.page_id,
            "loaded": dict(frame.loaded),
        }
    elif isinstance(frame, ReconcileCommand):
        encoded = {
            "protocol": frame.protocol,
            "type": "reconcile",
            "operationId": frame.operation_id,
            "desired": [_encode_desired(item) for item in frame.desired],
        }
    elif isinstance(frame, PluginLoadResult):
        encoded = {
            "protocol": frame.protocol,
            "type": "plugin-result",
            "operationId": frame.operation_id,
            "pluginId": frame.plugin_id,
            "revision": frame.revision,
            "state": frame.state.value,
            "error": frame.error,
        }
    elif isinstance(frame, ReconcileComplete):
        encoded = {
            "protocol": frame.protocol,
            "type": "reconcile-complete",
            "operationId": frame.operation_id,
            "success": frame.success,
            "error": frame.error,
        }
    elif isinstance(frame, RpcCall):
        encoded = {
            "protocol": frame.protocol,
            "type": "rpc-call",
            "pageId": frame.page_id,
            "callId": frame.call_id,
            "pluginId": frame.plugin_id,
            "revision": frame.revision,
            "method": frame.method,
            "arguments": thaw_json(frame.arguments),
        }
    elif isinstance(frame, RpcResult):
        encoded = {
            "protocol": frame.protocol,
            "type": "rpc-result",
            "callId": frame.call_id,
        }
        if frame.error_code is None:
            encoded["result"] = thaw_json(frame.result)
        else:
            encoded["errorCode"] = frame.error_code
            encoded["errorMessage"] = frame.error_message
    elif isinstance(frame, RpcCancel):
        encoded = {
            "protocol": frame.protocol,
            "type": "rpc-cancel",
            "pageId": frame.page_id,
            "callId": frame.call_id,
        }
    else:
        encoded = {
            "protocol": frame.protocol,
            "type": "event",
            "pageId": frame.page_id,
            "pluginId": frame.plugin_id,
            "revision": frame.revision,
            "name": frame.name,
            "payload": thaw_json(frame.payload),
        }
    validate_wire_frame(encoded)
    return encoded


def _decode_desired(value: object) -> DesiredClient:
    item = _object(value, "desired item")
    return DesiredClient(
        _string(item["pluginId"]),
        _string(item["revision"]),
        _string(item["bundleUrl"]),
        _string(item["bundleSha256"]),
        _nullable_string(item["protocolSchemaUrl"]),
        _string(item["activationPolicy"]),
    )


def _encode_desired(item: DesiredClient) -> dict[str, object]:
    return {
        "pluginId": item.plugin_id,
        "revision": item.revision,
        "bundleUrl": item.bundle_url,
        "bundleSha256": item.bundle_sha256,
        "protocolSchemaUrl": item.protocol_schema_url,
        "activationPolicy": item.activation_policy,
    }


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BridgeProtocolError("schema_error", f"{name} must be a JSON object")
    candidate = cast(dict[object, object], value)
    if any(not isinstance(key, str) for key in candidate):
        raise BridgeProtocolError("schema_error", f"{name} must be a JSON object")
    return cast(dict[str, object], candidate)


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise BridgeProtocolError("schema_error", f"{name} must be a JSON array")
    return cast(list[object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise BridgeProtocolError("schema_error", "expected a string")
    return value


def _nullable_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise BridgeProtocolError("schema_error", "expected a boolean")
    return value


def _string_map(value: Mapping[str, object]) -> dict[str, str]:
    return {key: _string(item) for key, item in value.items()}
