"""Public transport-independent Browser Bridge API."""

from .codec import (
    BRIDGE_SCHEMA,
    BridgeProtocolError,
    decode_frame,
    encode_frame,
    validate_wire_frame,
)
from .events import BridgeEventRegistry
from .host import (
    BridgeRpcRegistry,
    BrowserBridge,
    PagePluginSnapshot,
    StaleBridgeMessageError,
)
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
from .transport import BrowserBridgeTransport, create_bridge_app

__all__ = [
    "BRIDGE_SCHEMA",
    "PROTOCOL_VERSION",
    "BridgeEvent",
    "BridgeEventRegistry",
    "BridgeFrame",
    "BridgeProtocolError",
    "BridgeRpcRegistry",
    "BrowserBridge",
    "BrowserBridgeTransport",
    "DesiredClient",
    "Hello",
    "PagePluginSnapshot",
    "PagePluginState",
    "PluginLoadResult",
    "ReconcileCommand",
    "ReconcileComplete",
    "RpcCall",
    "RpcCancel",
    "RpcResult",
    "StaleBridgeMessageError",
    "create_bridge_app",
    "decode_frame",
    "encode_frame",
    "validate_wire_frame",
]
