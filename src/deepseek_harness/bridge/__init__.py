"""Public transport-independent Browser Bridge API."""

from .host import (
    BridgeRpcRegistry,
    BrowserBridge,
    PagePluginSnapshot,
    StaleBridgeMessageError,
)
from .protocol import (
    PROTOCOL_VERSION,
    DesiredClient,
    PagePluginState,
    PluginLoadResult,
    ReconcileCommand,
    RpcCall,
    RpcResult,
)

__all__ = [
    "PROTOCOL_VERSION",
    "BridgeRpcRegistry",
    "BrowserBridge",
    "DesiredClient",
    "PagePluginSnapshot",
    "PagePluginState",
    "PluginLoadResult",
    "ReconcileCommand",
    "RpcCall",
    "RpcResult",
    "StaleBridgeMessageError",
]
