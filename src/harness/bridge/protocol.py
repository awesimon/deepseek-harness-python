"""Immutable version 1 Browser Bridge protocol values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

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
            object.__setattr__(self, "result", freeze_json(self.result))
        elif self.result is not None or not self.error_message:
            raise ValueError("failed RPC result requires only code and message")
