"""Runnable process assembly for the Python Harness plugin runtime."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import signal
import socket
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from aiohttp import ClientError, ClientSession, web

from harness.agent import (
    AGENT_INVOCATIONS,
    AgentInvocationService,
    AgentRuntimeConfig,
    AgentSpineConfig,
    DeepSeekHTTPConfig,
    DefaultLLMRouteUnavailableError,
    DuplicateInvocationIdError,
    InvocationCancelledError,
    InvocationServiceClosedError,
    LLMAdapterProtocolError,
    LLMProviderError,
    LLMRoute,
    LLMRouteNotFoundError,
    MaximumStepsExceededError,
    SessionProjector,
    agent_runtime_plugin,
    agent_spine_plugin,
    event_json,
)
from harness.bridge import (
    BROWSER_BRIDGE,
    BrowserBridge,
    BrowserBridgeTransport,
    browser_bridge_plugin,
)
from harness.control import (
    PLUGIN_CONTROL,
    ControlPluginSnapshot,
    PluginCatalogWatcher,
    PluginControlConfig,
    PluginControlHttpAdapter,
    PluginControlService,
    PluginWatcherConfig,
    WatchCreatePolicy,
    WatchDeletePolicy,
    build_plugin_parser,
    plugin_control_plugin,
    run_plugin_cli,
)
from harness.cordis import Cordis, Fiber, FiberState, PluginSpec
from harness.plugins import (
    PLUGIN_MANAGER,
    ClientQuorum,
    PluginDiagnostic,
    PluginManager,
    PluginRevision,
    PluginState,
    plugin_manager_plugin,
)

_BOOTSTRAP_HTML = b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DeepSeek Harness</title>
</head>
<body>
  <main id="harness-root"></main>
  <script type="module" src="/browser.js"></script>
</body>
</html>
"""


class HostState(str, Enum):
    """Lifecycle state of one process assembly."""

    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class HostStartupError(RuntimeError):
    """Raised when validated Host composition cannot reach a serving state."""


@dataclass(frozen=True, slots=True)
class HarnessHostConfig:
    """Trusted local configuration for one Harness Host."""

    session_id: str = "default"
    bind_host: str = "127.0.0.1"
    port: int = 8765
    plugin_catalogs: tuple[Path, ...] = ()
    browser_runtime: Path | None = None
    client_quorum: ClientQuorum = ClientQuorum.ALL_CONNECTED
    client_quorum_overrides: tuple[tuple[str, ClientQuorum], ...] = ()
    deepseek: DeepSeekHTTPConfig | None = None
    max_steps: int = 8
    session_db: Path | None = None
    control_enabled: bool = False
    watched_catalogs: tuple[Path, ...] = ()
    watcher: PluginWatcherConfig | None = None

    def __post_init__(self) -> None:
        """Normalize paths and reject values that cannot define a listener."""
        if not self.session_id:
            raise ValueError("session id must not be empty")
        if not self.bind_host:
            raise ValueError("bind host must not be empty")
        if self.port < 0 or self.port > 65535:
            raise ValueError("port must be between 0 and 65535")
        if isinstance(self.max_steps, bool) or self.max_steps <= 0:
            raise ValueError("maximum Steps must be positive")
        try:
            client_quorum = ClientQuorum(self.client_quorum)
        except ValueError as error:
            raise ValueError("client quorum must be all_connected or any_connected") from error
        object.__setattr__(self, "client_quorum", client_quorum)
        overrides: list[tuple[str, ClientQuorum]] = []
        seen: set[str] = set()
        for plugin_id, configured in self.client_quorum_overrides:
            if not plugin_id:
                raise ValueError("client quorum override Plugin ID must not be empty")
            if plugin_id in seen:
                raise ValueError(f"duplicate client quorum override for {plugin_id!r}")
            seen.add(plugin_id)
            try:
                quorum = ClientQuorum(configured)
            except ValueError as error:
                raise ValueError(f"unsupported client quorum override for {plugin_id!r}") from error
            overrides.append((plugin_id, quorum))
        object.__setattr__(self, "client_quorum_overrides", tuple(overrides))
        object.__setattr__(
            self,
            "plugin_catalogs",
            tuple(Path(path) for path in self.plugin_catalogs),
        )
        object.__setattr__(
            self,
            "watched_catalogs",
            tuple(Path(path) for path in self.watched_catalogs),
        )
        if self.browser_runtime is not None:
            object.__setattr__(self, "browser_runtime", Path(self.browser_runtime))
        if self.session_db is not None:
            object.__setattr__(self, "session_db", Path(self.session_db))


class HarnessHost:
    """Own one assembled runtime, listener, and installed plugin set."""

    def __init__(self, config: HarnessHostConfig) -> None:
        self.config = config
        self.state = HostState.NEW
        self.runtime = Cordis()
        self._manager: PluginManager | None = None
        self._bridge: BrowserBridge | None = None
        self._invocations: AgentInvocationService | None = None
        self._control: PluginControlService | None = None
        self._watcher: PluginCatalogWatcher | None = None
        self._core_fibers: list[Fiber] = []
        self._enabled_plugins: list[str] = []
        self._runner: web.AppRunner | None = None
        self._base_url: str | None = None
        self._plugin_catalogs: tuple[Path, ...] = ()
        self._watched_catalogs: tuple[Path, ...] = ()
        self._browser_bytes: bytes | None = None
        self._close_task: asyncio.Task[None] | None = None

    @property
    def manager(self) -> PluginManager:
        """Return the active Dynamic Plugin Manager."""
        if self._manager is None:
            raise RuntimeError("Harness Host has not established the Plugin Manager")
        return self._manager

    @property
    def bridge(self) -> BrowserBridge:
        """Return the active Browser Bridge."""
        if self._bridge is None:
            raise RuntimeError("Harness Host has not established the Browser Bridge")
        return self._bridge

    @property
    def invocations(self) -> AgentInvocationService:
        """Return the active Session-scoped Agent invocation service."""
        if self._invocations is None:
            raise RuntimeError("Harness Host has not established Agent invocations")
        return self._invocations

    @property
    def control(self) -> PluginControlService:
        """Return the active serialized Plugin Control Plane."""
        if self._control is None:
            raise RuntimeError("Harness Host has not established Plugin Control")
        return self._control

    @property
    def base_url(self) -> str:
        """Return the effective listener URL after successful startup."""
        if self._base_url is None:
            raise RuntimeError("Harness Host is not listening")
        return self._base_url

    async def start(self) -> None:
        """Validate configuration and establish the complete serving composition once."""
        if self.state is not HostState.NEW:
            raise RuntimeError("Harness Host start is single-shot")
        self.state = HostState.STARTING
        try:
            self._validate_paths()
            await self._mount_core()
            await self._activate_catalogs()
            await self._start_listener()
            await self._start_watcher()
        except BaseException as startup_error:
            self.state = HostState.FAILED
            try:
                await self._cleanup()
            except BaseException as cleanup_error:  # noqa: BLE001 -- preserve startup failure
                startup_error.add_note(f"Host cleanup also failed: {cleanup_error!r}")
            raise
        self.state = HostState.RUNNING

    async def close(self) -> None:
        """Join one teardown that removes listener and runtime contributions."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_once())
        await asyncio.shield(self._close_task)

    async def __aenter__(self) -> Self:
        """Start and return this Host."""
        await self.start()
        return self

    async def __aexit__(
        self,
        _error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close this Host after context exit."""
        await self.close()

    def _validate_paths(self) -> None:
        catalogs: list[Path] = []
        for configured in self.config.plugin_catalogs:
            try:
                catalog = configured.resolve(strict=True)
            except OSError as error:
                raise HostStartupError(
                    f"cannot resolve plugin catalog {configured}: {error}"
                ) from error
            if not catalog.is_dir():
                raise HostStartupError(f"plugin catalog is not a directory: {catalog}")
            catalogs.append(catalog)
        self._plugin_catalogs = tuple(catalogs)
        watched: list[Path] = []
        for configured in self.config.watched_catalogs:
            try:
                catalog = configured.resolve(strict=True)
            except OSError as error:
                raise HostStartupError(
                    f"cannot resolve watched plugin catalog {configured}: {error}"
                ) from error
            if catalog not in self._plugin_catalogs:
                raise HostStartupError(
                    f"watched plugin catalog is not a trusted plugin catalog: {catalog}"
                )
            watched.append(catalog)
        self._watched_catalogs = tuple(watched)
        if self.config.watcher is None and watched:
            raise HostStartupError("watched plugin catalogs require watcher configuration")
        if self.config.watcher is not None and not watched:
            raise HostStartupError("watcher configuration requires at least one watched catalog")
        if self.config.control_enabled and not _host_is_loopback(self.config.bind_host):
            raise HostStartupError("unauthenticated Plugin Control requires a loopback-only host")

        runtime = self.config.browser_runtime
        if runtime is None:
            return
        try:
            resolved = runtime.resolve(strict=True)
            self._browser_bytes = resolved.read_bytes()
        except OSError as error:
            raise HostStartupError(f"cannot read browser runtime {runtime}: {error}") from error
        if not resolved.is_file():
            raise HostStartupError(f"browser runtime is not a regular file: {resolved}")

    async def _mount_core(self) -> None:
        await self._mount_core_plugin(
            agent_spine_plugin(),
            AgentSpineConfig(self.config.session_id, self.config.session_db),
        )
        await self._mount_core_plugin(
            agent_runtime_plugin(),
            AgentRuntimeConfig(self.config.deepseek, self.config.max_steps),
        )
        await self._mount_core_plugin(plugin_manager_plugin(), None)
        await self._mount_core_plugin(browser_bridge_plugin(), None)
        await self._mount_core_plugin(
            plugin_control_plugin(),
            PluginControlConfig(self._plugin_catalogs),
        )
        self._manager = self.runtime.root.lookup(PLUGIN_MANAGER)
        self._bridge = self.runtime.root.lookup(BROWSER_BRIDGE)
        self._invocations = self.runtime.root.lookup(AGENT_INVOCATIONS)
        self._control = self.runtime.root.lookup(PLUGIN_CONTROL)
        if (
            self._manager is None
            or self._bridge is None
            or self._invocations is None
            or self._control is None
        ):
            raise HostStartupError("core composition did not publish required Services")

    async def _mount_core_plugin[ConfigT](
        self,
        specification: PluginSpec[ConfigT],
        config: ConfigT,
    ) -> None:
        fiber = await self.runtime.mount(specification, config)
        self._core_fibers.append(fiber)
        if fiber.state is not FiberState.ACTIVE:
            raise HostStartupError(f"core plugin {fiber.name!r} did not activate: {fiber.error!r}")

    async def _activate_catalogs(self) -> None:
        revisions: list[PluginRevision] = []
        for catalog in self._plugin_catalogs:
            discovered = self.manager.discover(catalog)
            diagnostics = [item for item in discovered if isinstance(item, PluginDiagnostic)]
            if diagnostics:
                detail = "; ".join(f"{item.contribution}: {item.message}" for item in diagnostics)
                raise HostStartupError(f"plugin discovery failed: {detail}")
            revisions.extend(item for item in discovered if not isinstance(item, PluginDiagnostic))
        installed: list[ControlPluginSnapshot] = []
        for revision in revisions:
            operation = await self.control.install(revision.root, expected_absent=True)
            installed.append(operation.snapshot)
        try:
            self.manager.configure_client_quorums(
                self.config.client_quorum,
                dict(self.config.client_quorum_overrides),
            )
        except ValueError as error:
            raise HostStartupError(f"invalid client quorum configuration: {error}") from error
        for current in installed:
            operation = await self.control.enable(
                current.plugin.plugin_id,
                expected_revision=current.plugin.revision,
                expected_mutation_version=current.mutation_version,
            )
            snapshot = operation.snapshot.plugin
            if snapshot.state is PluginState.FAILED:
                diagnostic = snapshot.diagnostic
                detail = "unknown activation failure" if diagnostic is None else diagnostic.message
                raise HostStartupError(
                    f"plugin {snapshot.plugin_id!r} failed to activate: {detail}"
                )
            self._enabled_plugins.append(snapshot.plugin_id)

    async def _start_listener(self) -> None:
        transport = BrowserBridgeTransport(self.bridge)
        app = transport.create_app()
        app.router.add_get("/health", self._health)
        app.router.add_post(
            "/api/v1/agent/invocations/{invocation_id}",
            self._invoke_agent,
        )
        app.router.add_delete(
            "/api/v1/agent/invocations/{invocation_id}",
            self._cancel_invocation,
        )
        app.router.add_get("/api/v1/sessions/{session_id}", self._session)
        if self.config.control_enabled:
            PluginControlHttpAdapter(self.control, lambda: self.base_url).register(app)
        if self._browser_bytes is not None:
            app.router.add_get("/", self._index)
            app.router.add_get("/browser.js", self._browser_runtime)
        runner = web.AppRunner(app, handle_signals=False)
        self._runner = runner
        await runner.setup()
        listener = socket.create_server((self.config.bind_host, self.config.port))
        listener.setblocking(False)
        site = web.SockSite(runner, listener)
        try:
            await site.start()
        except BaseException:
            listener.close()
            raise
        address = listener.getsockname()
        host = str(address[0])
        port = int(address[1])
        display_host = f"[{host}]" if ":" in host else host
        self._base_url = f"http://{display_host}:{port}"

    async def _start_watcher(self) -> None:
        config = self.config.watcher
        if config is None:
            return
        watcher = PluginCatalogWatcher(
            self.control,
            config,
            catalogs=self._watched_catalogs,
        )
        await watcher.start()
        self._watcher = watcher

    async def _close_once(self) -> None:
        if self.state is HostState.CLOSED:
            return
        self.state = HostState.CLOSING
        try:
            await self._cleanup()
        finally:
            self.state = HostState.CLOSED

    async def _cleanup(self) -> None:
        errors: list[BaseException] = []
        if self._control is not None:
            self._control.begin_close()
        if self._watcher is not None:
            try:
                await self._watcher.close()
            except BaseException as error:  # noqa: BLE001 -- later cleanup must still run
                errors.append(error)
            self._watcher = None
        if self._invocations is not None:
            try:
                await self._invocations.close()
            except BaseException as error:  # noqa: BLE001 -- listener cleanup must still run
                errors.append(error)
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except BaseException as error:  # noqa: BLE001 -- later cleanup must still run
                errors.append(error)
            self._runner = None
            self._base_url = None
        if self._manager is not None:
            for plugin_id in reversed(self._enabled_plugins):
                try:
                    await self._manager.disable(plugin_id)
                except BaseException as error:  # noqa: BLE001 -- all plugins must be attempted
                    errors.append(error)
            self._enabled_plugins.clear()
        try:
            await self.runtime.close()
        except BaseException as error:  # noqa: BLE001 -- listener and plugins were already attempted
            errors.append(error)
        if errors:
            raise BaseExceptionGroup("Harness Host cleanup failed", errors)

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _invoke_agent(self, request: web.Request) -> web.Response:
        identifier = request.match_info["invocation_id"]
        try:
            payload = await request.json()
        except (ValueError, TypeError):
            return _error_response(400, "invalid_request", "request body must be JSON")
        try:
            text, route = _parse_invocation_payload(payload)
            result = await self.invocations.invoke(identifier, text, route=route)
        except (TypeError, ValueError) as error:
            return _error_response(400, "invalid_request", str(error))
        except DuplicateInvocationIdError as error:
            return _error_response(409, "duplicate_invocation", str(error))
        except InvocationCancelledError as error:
            return _error_response(409, "invocation_cancelled", str(error))
        except MaximumStepsExceededError as error:
            return _error_response(409, "maximum_steps", str(error))
        except (DefaultLLMRouteUnavailableError, LLMRouteNotFoundError) as error:
            return _error_response(503, "route_unavailable", str(error))
        except InvocationServiceClosedError as error:
            return _error_response(503, "invocation_service_closed", str(error))
        except LLMProviderError as error:
            status = 504 if error.failure.code == "provider_timeout" else 502
            return _error_response(
                status,
                error.failure.code,
                str(error),
                retryable=error.failure.retryable,
                provider_status=error.failure.http_status,
            )
        except LLMAdapterProtocolError as error:
            return _error_response(502, "adapter_protocol", str(error))
        except Exception:  # noqa: BLE001 -- internal details never cross the HTTP API
            return _error_response(500, "invocation_failed", "Agent invocation failed")
        return web.json_response(
            {
                "invocation_id": identifier,
                "session_id": self.invocations.log.session_id,
                "turn_id": result.turn_id,
                "steps": result.steps,
                "message": {
                    "role": result.message.role.value,
                    "content": result.message.content,
                },
            }
        )

    async def _cancel_invocation(self, request: web.Request) -> web.Response:
        identifier = request.match_info["invocation_id"]
        if not await self.invocations.cancel(identifier):
            return _error_response(
                404,
                "invocation_not_found",
                f"Invocation ID {identifier!r} is not queued or active",
            )
        return web.json_response(
            {"invocation_id": identifier, "status": "cancellation_requested"},
            status=202,
        )

    async def _session(self, request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        if session_id != str(self.invocations.log.session_id):
            return _error_response(404, "session_not_found", "Session ID is not active")
        log = self.invocations.log

        return web.json_response(
            {
                "session_id": session_id,
                "events": [
                    {"sequence": envelope.sequence, **event_json(envelope.event)}
                    for envelope in log.snapshot()
                ],
                "transcript": [
                    {
                        "sequence": entry.sequence,
                        "kind": entry.kind,
                        "content": entry.content,
                    }
                    for entry in SessionProjector(log).transcript()
                ],
            }
        )

    async def _index(self, _request: web.Request) -> web.Response:
        return web.Response(body=_BOOTSTRAP_HTML, content_type="text/html")

    async def _browser_runtime(self, _request: web.Request) -> web.Response:
        assert self._browser_bytes is not None
        return web.Response(
            body=self._browser_bytes,
            content_type="text/javascript",
            headers={"Cache-Control": "no-cache"},
        )


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser shared by both executable entrypoints."""
    parser = argparse.ArgumentParser(prog="deepseek-harness-python")
    parser.add_argument("--session-id", default="default")
    parser.add_argument("--session-db", type=Path, metavar="FILE")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--plugins",
        action="append",
        type=Path,
        default=[],
        metavar="DIRECTORY",
        help="catalog containing immediate plugin directories; repeatable",
    )
    parser.add_argument("--browser-runtime", type=Path, metavar="FILE")
    parser.add_argument(
        "--client-quorum",
        choices=tuple(item.value for item in ClientQuorum),
        default=ClientQuorum.ALL_CONNECTED.value,
    )
    parser.add_argument(
        "--client-quorum-override",
        action="append",
        type=_parse_quorum_override,
        default=[],
        metavar="PLUGIN_ID=QUORUM",
    )
    parser.add_argument("--llm-provider")
    parser.add_argument("--llm-model")
    parser.add_argument("--llm-base-url", default="https://api.deepseek.com")
    parser.add_argument("--llm-api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--llm-connect-timeout", type=float, default=10.0)
    parser.add_argument("--llm-request-timeout", type=float, default=120.0)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--control", action="store_true")
    parser.add_argument(
        "--watch-plugins",
        action="append",
        type=Path,
        default=[],
        metavar="DIRECTORY",
    )
    parser.add_argument("--watch-debounce", type=float, default=0.25)
    parser.add_argument(
        "--watch-create",
        choices=tuple(item.value for item in WatchCreatePolicy),
        default=WatchCreatePolicy.IGNORE.value,
    )
    parser.add_argument(
        "--watch-delete",
        choices=tuple(item.value for item in WatchDeletePolicy),
        default=WatchDeletePolicy.IGNORE.value,
    )
    return parser


def build_invoke_parser() -> argparse.ArgumentParser:
    """Return the parser for the HTTP invocation client command."""
    parser = argparse.ArgumentParser(prog="deepseek-harness-python invoke")
    parser.add_argument("--url", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("text", metavar="TEXT")
    return parser


async def serve(config: HarnessHostConfig) -> None:
    """Run one Host until SIGINT or SIGTERM requests shutdown."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    registered: list[signal.Signals] = []
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, stop.set)
            registered.append(name)
        except NotImplementedError:
            pass
    try:
        async with HarnessHost(config) as host:
            print(host.base_url, flush=True)
            await stop.wait()
    finally:
        for name in registered:
            loop.remove_signal_handler(name)


def main(arguments: Sequence[str] | None = None) -> int:
    """Parse arguments and run the asynchronous Host lifecycle."""
    raw_arguments = list(sys.argv[1:] if arguments is None else arguments)
    if raw_arguments and raw_arguments[0] == "invoke":
        parser = build_invoke_parser()
        namespace = parser.parse_args(raw_arguments[1:])
        if (namespace.provider is None) != (namespace.model is None):
            parser.error("--provider and --model must be provided together")
        try:
            return asyncio.run(_invoke_cli(namespace))
        except KeyboardInterrupt:
            return 130

    if raw_arguments and raw_arguments[0] == "plugin":
        namespace = build_plugin_parser().parse_args(raw_arguments[1:])
        try:
            return asyncio.run(run_plugin_cli(namespace))
        except KeyboardInterrupt:
            return 130

    parser = build_parser()
    namespace = parser.parse_args(raw_arguments)
    try:
        deepseek = _provider_config(namespace)
    except ValueError as error:
        parser.error(str(error))
    config = HarnessHostConfig(
        session_id=namespace.session_id,
        bind_host=namespace.host,
        port=namespace.port,
        plugin_catalogs=tuple(namespace.plugins),
        browser_runtime=namespace.browser_runtime,
        client_quorum=ClientQuorum(namespace.client_quorum),
        client_quorum_overrides=tuple(namespace.client_quorum_override),
        deepseek=deepseek,
        max_steps=namespace.max_steps,
        session_db=namespace.session_db,
        control_enabled=namespace.control,
        watched_catalogs=tuple(namespace.watch_plugins),
        watcher=(
            None
            if not namespace.watch_plugins
            else PluginWatcherConfig(
                namespace.watch_debounce,
                WatchCreatePolicy(namespace.watch_create),
                WatchDeletePolicy(namespace.watch_delete),
            )
        ),
    )
    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        return 130
    return 0


def _parse_quorum_override(value: str) -> tuple[str, ClientQuorum]:
    try:
        plugin_id, quorum_text = value.split("=", 1)
        quorum = ClientQuorum(quorum_text)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError(
            "client quorum override must be PLUGIN_ID=all_connected|any_connected"
        ) from error
    if not plugin_id:
        raise argparse.ArgumentTypeError("client quorum override Plugin ID must not be empty")
    return plugin_id, quorum


def _provider_config(namespace: argparse.Namespace) -> DeepSeekHTTPConfig | None:
    provider = namespace.llm_provider
    model = namespace.llm_model
    if provider is None and model is None:
        return None
    if provider is None or model is None:
        raise ValueError("--llm-provider and --llm-model must be provided together")
    api_key = os.environ.get(namespace.llm_api_key_env)
    if not api_key:
        raise ValueError(
            f"configured LLM requires credential environment variable {namespace.llm_api_key_env!r}"
        )
    return DeepSeekHTTPConfig(
        provider,
        model,
        namespace.llm_base_url,
        api_key,
        namespace.llm_connect_timeout,
        namespace.llm_request_timeout,
    )


async def _invoke_cli(namespace: argparse.Namespace) -> int:
    identifier = str(uuid.uuid4())
    route = None
    if namespace.provider is not None:
        route = {"provider": namespace.provider, "model": namespace.model}
    body: dict[str, object] = {"input": namespace.text}
    if route is not None:
        body["route"] = route
    base_url = namespace.url.rstrip("/")
    endpoint = f"{base_url}/api/v1/agent/invocations/{identifier}"
    async with ClientSession() as client:
        try:
            response = await client.post(endpoint, json=body)
            async with response:
                payload = cast(object, await response.json())
        except asyncio.CancelledError:
            try:
                await asyncio.shield(_cancel_cli_invocation(client, endpoint))
            except (ClientError, TimeoutError):
                pass
            raise
        except (ClientError, TimeoutError) as error:
            print(f"invocation_transport: {error}", file=sys.stderr)
            return 1
    if response.status != 200:
        if isinstance(payload, Mapping):
            response_payload = cast(Mapping[object, object], payload)
            raw_code = response_payload.get("code", "invocation_failed")
            raw_message = response_payload.get(
                "message", f"Host returned HTTP {response.status}"
            )
            code = raw_code if isinstance(raw_code, str) else "invocation_failed"
            message = (
                raw_message
                if isinstance(raw_message, str)
                else f"Host returned HTTP {response.status}"
            )
        else:
            code = "invocation_failed"
            message = f"Host returned HTTP {response.status}"
        print(f"{code}: {message}", file=sys.stderr)
        return 1
    if not isinstance(payload, Mapping):
        print("invalid_response: Host returned a non-object response", file=sys.stderr)
        return 1
    response_payload = cast(Mapping[object, object], payload)
    message = response_payload.get("message")
    if not isinstance(message, Mapping):
        print("invalid_response: Host response omitted Assistant content", file=sys.stderr)
        return 1
    response_message = cast(Mapping[object, object], message)
    content = response_message.get("content")
    if not isinstance(content, str):
        print("invalid_response: Host response omitted Assistant content", file=sys.stderr)
        return 1
    print(content)
    return 0


async def _cancel_cli_invocation(client: ClientSession, endpoint: str) -> None:
    async with client.delete(endpoint) as response:
        await response.read()


def _parse_invocation_payload(payload: object) -> tuple[str, LLMRoute | None]:
    if not isinstance(payload, Mapping):
        raise TypeError("request body must be a JSON object")
    raw_body = cast(Mapping[object, object], payload)
    if any(not isinstance(key, str) for key in raw_body):
        raise TypeError("request body keys must be strings")
    body = cast(Mapping[str, object], raw_body)
    if set(body) - {"input", "route"}:
        raise ValueError("request body contains unsupported fields")
    text = body.get("input")
    if not isinstance(text, str) or not text:
        raise ValueError("input must be a non-empty string")
    raw_route = body.get("route")
    if raw_route is None:
        return text, None
    if not isinstance(raw_route, Mapping):
        raise TypeError("route must be a JSON object")
    raw_route_mapping = cast(Mapping[object, object], raw_route)
    if set(raw_route_mapping) != {"provider", "model"}:
        raise ValueError("route must contain exactly provider and model")
    provider = raw_route_mapping.get("provider")
    model = raw_route_mapping.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        raise TypeError("route provider and model must be strings")
    return text, LLMRoute(provider, model)


def _error_response(
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    provider_status: int | None = None,
) -> web.Response:
    payload: dict[str, object] = {"code": code, "message": message}
    if retryable is not None:
        payload["retryable"] = retryable
    if provider_status is not None:
        payload["provider_status"] = provider_status
    return web.json_response(payload, status=status)


def _host_is_loopback(host: str) -> bool:
    try:
        addresses = cast(
            list[
                tuple[
                    int,
                    int,
                    int,
                    str,
                    tuple[str, int] | tuple[str, int, int, int],
                ]
            ],
            socket.getaddrinfo(host, None, type=socket.SOCK_STREAM),
        )
    except socket.gaierror as error:
        raise HostStartupError(f"cannot resolve control bind host {host!r}: {error}") from error
    resolved = {item[4][0].split("%", 1)[0] for item in addresses}
    return bool(resolved) and all(ipaddress.ip_address(value).is_loopback for value in resolved)
