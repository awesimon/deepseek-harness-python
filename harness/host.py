"""Runnable process assembly for the Python Harness plugin runtime."""

from __future__ import annotations

import argparse
import asyncio
import signal
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Self

from aiohttp import web

from harness.agent import AgentSpineConfig, agent_spine_plugin
from harness.bridge import (
    BROWSER_BRIDGE,
    BrowserBridge,
    BrowserBridgeTransport,
    browser_bridge_plugin,
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

    def __post_init__(self) -> None:
        """Normalize paths and reject values that cannot define a listener."""
        if not self.session_id:
            raise ValueError("session id must not be empty")
        if not self.bind_host:
            raise ValueError("bind host must not be empty")
        if self.port < 0 or self.port > 65535:
            raise ValueError("port must be between 0 and 65535")
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
        if self.browser_runtime is not None:
            object.__setattr__(self, "browser_runtime", Path(self.browser_runtime))


class HarnessHost:
    """Own one assembled runtime, listener, and installed plugin set."""

    def __init__(self, config: HarnessHostConfig) -> None:
        self.config = config
        self.state = HostState.NEW
        self.runtime = Cordis()
        self._manager: PluginManager | None = None
        self._bridge: BrowserBridge | None = None
        self._core_fibers: list[Fiber] = []
        self._enabled_plugins: list[str] = []
        self._runner: web.AppRunner | None = None
        self._base_url: str | None = None
        self._plugin_catalogs: tuple[Path, ...] = ()
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
            AgentSpineConfig(self.config.session_id),
        )
        await self._mount_core_plugin(plugin_manager_plugin(), None)
        await self._mount_core_plugin(browser_bridge_plugin(), None)
        self._manager = self.runtime.root.lookup(PLUGIN_MANAGER)
        self._bridge = self.runtime.root.lookup(BROWSER_BRIDGE)
        if self._manager is None or self._bridge is None:
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
        for revision in revisions:
            await self.manager.install(revision.root)
        try:
            self.manager.configure_client_quorums(
                self.config.client_quorum,
                dict(self.config.client_quorum_overrides),
            )
        except ValueError as error:
            raise HostStartupError(f"invalid client quorum configuration: {error}") from error
        for revision in revisions:
            snapshot = await self.manager.enable(revision.manifest.plugin_id)
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
    namespace = build_parser().parse_args(arguments)
    config = HarnessHostConfig(
        session_id=namespace.session_id,
        bind_host=namespace.host,
        port=namespace.port,
        plugin_catalogs=tuple(namespace.plugins),
        browser_runtime=namespace.browser_runtime,
        client_quorum=ClientQuorum(namespace.client_quorum),
        client_quorum_overrides=tuple(namespace.client_quorum_override),
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
