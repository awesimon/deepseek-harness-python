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
from .plugin import (
    BRIDGE_EVENT_REGISTRY,
    BRIDGE_RPC_REGISTRY,
    BROWSER_BRIDGE,
    browser_bridge_plugin,
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
    "BRIDGE_EVENT_REGISTRY",
    "BRIDGE_RPC_REGISTRY",
    "BRIDGE_SCHEMA",
    "BROWSER_BRIDGE",
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
    "browser_bridge_plugin",
    "create_bridge_app",
    "decode_frame",
    "encode_frame",
    "validate_wire_frame",
]
