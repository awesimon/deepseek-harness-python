"""Immutable protocol descriptors for full-stack plugins."""

# Descriptor factory result types are supplied by the assignment target.
# pyright: reportInvalidTypeVarUse=false

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from harness.agent.values import JsonValue


def _validate_wire_name(name: str) -> None:
    if not name:
        raise ValueError("protocol descriptor name must not be empty")


@dataclass(frozen=True, slots=True)
class RpcMethod[ArgumentsT: Mapping[str, JsonValue], ResultT: JsonValue]:
    """One revision-bound backend RPC name with typed arguments and result.

    @param name: Non-empty name carried on the Browser Bridge wire.
    """

    name: str
    direction: Literal["rpc"] = field(default="rpc", init=False)

    def __post_init__(self) -> None:
        """Reject an empty wire name."""
        _validate_wire_name(self.name)


@dataclass(frozen=True, slots=True)
class ClientEvent[PayloadT: JsonValue]:
    """One revision-bound client-to-backend Event name.

    @param name: Non-empty name carried on the Browser Bridge wire.
    """

    name: str
    direction: Literal["client"] = field(default="client", init=False)

    def __post_init__(self) -> None:
        """Reject an empty wire name."""
        _validate_wire_name(self.name)


@dataclass(frozen=True, slots=True)
class ServerEvent[PayloadT: JsonValue]:
    """One revision-bound backend-to-client Event name.

    @param name: Non-empty name carried on the Browser Bridge wire.
    """

    name: str
    direction: Literal["server"] = field(default="server", init=False)

    def __post_init__(self) -> None:
        """Reject an empty wire name."""
        _validate_wire_name(self.name)


def rpc_method[ArgumentsT: Mapping[str, JsonValue], ResultT: JsonValue](
    name: str,
) -> RpcMethod[ArgumentsT, ResultT]:
    """Create an immutable backend RPC descriptor.

    @param name: Non-empty name carried on the Browser Bridge wire.
    @returns: Descriptor with no plugin or Revision identity.
    """
    return RpcMethod(name)


def client_event[PayloadT: JsonValue](
    name: str,
) -> ClientEvent[PayloadT]:
    """Create an immutable client-to-backend Event descriptor.

    @param name: Non-empty name carried on the Browser Bridge wire.
    @returns: Descriptor with no plugin or Revision identity.
    """
    return ClientEvent(name)


def server_event[PayloadT: JsonValue](
    name: str,
) -> ServerEvent[PayloadT]:
    """Create an immutable backend-to-client Event descriptor.

    @param name: Non-empty name carried on the Browser Bridge wire.
    @returns: Descriptor with no plugin or Revision identity.
    """
    return ServerEvent(name)
