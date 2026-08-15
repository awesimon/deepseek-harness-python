"""Immutable version 1 Browser Bridge protocol values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from harness.agent.values import JsonValue, freeze_json, freeze_json_object

PROTOCOL_VERSION = "1"


class PagePluginState(str, Enum):
    """Page-local state for one exact client revision."""

    ABSENT = "absent"
    LOADING = "loading"
    ACTIVE = "active"
    WAITING = "waiting"
    FAILED = "failed"
    UNLOADING = "unloading"


@dataclass(frozen=True, slots=True)
class DesiredClient:
    """One client revision desired by the Host."""

    plugin_id: str
    revision: str
    bundle_url: str
    bundle_sha256: str
    protocol_schema_url: str | None = None
    activation_policy: str = "required"

    def __post_init__(self) -> None:
        """Validate contribution metadata carried to the browser."""
        if self.activation_policy not in ("required", "optional"):
            raise ValueError("client activation policy must be required or optional")


@dataclass(frozen=True, slots=True)
class Hello:
    """Initial page inventory for one logical connection."""

    protocol: str
    page_id: str
    loaded: Mapping[str, str]

    def __post_init__(self) -> None:
        """Validate the protocol and freeze the reported inventory."""
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")
        object.__setattr__(self, "loaded", MappingProxyType(dict(self.loaded)))


@dataclass(frozen=True, slots=True)
class ReconcileCommand:
    """Complete desired client graph for one page operation."""

    protocol: str
    operation_id: str
    desired: tuple[DesiredClient, ...]

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")
        object.__setattr__(self, "desired", tuple(self.desired))


@dataclass(frozen=True, slots=True)
class PluginLoadResult:
    """Page result for one plugin in a reconciliation operation."""

    protocol: str
    operation_id: str
    plugin_id: str
    revision: str
    state: PagePluginState
    error: str | None = None

    def __post_init__(self) -> None:
        """Validate the protocol and required failure diagnostic."""
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")
        if self.state is PagePluginState.FAILED and not self.error:
            raise ValueError("failed plugin result requires an error")


@dataclass(frozen=True, slots=True)
class ReconcileComplete:
    """Terminal page result for one reconciliation operation."""

    protocol: str
    operation_id: str
    success: bool
    error: str | None = None

    def __post_init__(self) -> None:
        """Require exactly one successful or failed completion form."""
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")
        if self.success == (self.error is not None):
            raise ValueError("reconciliation completion requires success or one error")


@dataclass(frozen=True, slots=True)
class RpcCall:
    """Package-private browser-to-backend call."""

    protocol: str
    page_id: str
    call_id: str
    plugin_id: str
    revision: str
    method: str
    arguments: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")
        object.__setattr__(self, "arguments", freeze_json_object(self.arguments))


@dataclass(frozen=True, slots=True)
class RpcResult:
    """Structured RPC success or failure response."""

    protocol: str
    call_id: str
    result: JsonValue | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")
        if self.error_code is None:
            if self.error_message is not None:
                raise ValueError("successful RPC result cannot contain an error message")
            object.__setattr__(self, "result", freeze_json(self.result))
        elif self.result is not None or not self.error_message:
            raise ValueError("failed RPC result requires only code and message")


@dataclass(frozen=True, slots=True)
class RpcCancel:
    """Best-effort cancellation request for one page-owned RPC call."""

    protocol: str
    page_id: str
    call_id: str

    def __post_init__(self) -> None:
        """Reject an unsupported protocol version."""
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")


@dataclass(frozen=True, slots=True)
class BridgeEvent:
    """Explicitly named JSON Event crossing one plugin Revision."""

    protocol: str
    page_id: str
    plugin_id: str
    revision: str
    name: str
    payload: JsonValue

    def __post_init__(self) -> None:
        """Validate the protocol and freeze the Event payload."""
        if self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported Browser Bridge protocol")
        object.__setattr__(self, "payload", freeze_json(self.payload))


type BridgeFrame = (
    Hello
    | ReconcileCommand
    | PluginLoadResult
    | ReconcileComplete
    | RpcCall
    | RpcResult
    | RpcCancel
    | BridgeEvent
)
