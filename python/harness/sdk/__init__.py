"""Stable authoring API for lifecycle-owned Python backend plugins."""

from importlib.metadata import version

from harness.agent.values import JsonValue

from ._backend import (
    BackendPluginChannel,
    BackendPluginContext,
    BridgeBackendPluginContext,
    define_backend_plugin,
    define_bridge_backend_plugin,
)
from ._protocol import (
    ClientEvent,
    RpcMethod,
    ServerEvent,
    client_event,
    rpc_method,
    server_event,
)

RUNTIME_API = "1"
PYTHON_SDK_VERSION = version("deepseek-harness-python")
BROWSER_SDK_PACKAGE = "@deepseek-harness/browser-bridge-client"
BROWSER_SDK_VERSION = "0.1.0-dev.0"

__all__ = [
    "BROWSER_SDK_PACKAGE",
    "BROWSER_SDK_VERSION",
    "PYTHON_SDK_VERSION",
    "RUNTIME_API",
    "BackendPluginChannel",
    "BackendPluginContext",
    "BridgeBackendPluginContext",
    "ClientEvent",
    "JsonValue",
    "RpcMethod",
    "ServerEvent",
    "client_event",
    "define_backend_plugin",
    "define_bridge_backend_plugin",
    "rpc_method",
    "server_event",
]
