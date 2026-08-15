"""Runnable Host composition and lifecycle tests."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientConnectorError, ClientSession

from harness.agent import AGENT_LOOP
from harness.bridge import (
    BRIDGE_EVENT_REGISTRY,
    BRIDGE_RPC_REGISTRY,
    BROWSER_BRIDGE,
    CLIENT_ACTIVATION,
)
from harness.host import (
    HarnessHost,
    HarnessHostConfig,
    HostStartupError,
    HostState,
    build_parser,
)
from harness.plugins import CLIENT_ARTIFACTS, PLUGIN_MANAGER, ClientQuorum, PluginState


def backend_source(*, fail: bool = False) -> str:
    """Return one minimal dynamic backend plugin."""
    body = 'raise RuntimeError("activation failed")' if fail else "return None"
    return (
        "from harness.cordis import PluginSpec\n"
        "async def apply(_context, _config):\n"
        f"    {body}\n"
        'plugin = PluginSpec("host-test", apply)\n'
    )


class HarnessHostTests(unittest.IsolatedAsyncioTestCase):
    """Exercise core composition, catalog startup, serving, and teardown."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    def _write_plugin(
        self,
        directory: str,
        plugin_id: str,
        *,
        client: bool = False,
        fail: bool = False,
    ) -> Path:
        root = self.root / "catalog" / directory
        root.mkdir(parents=True, exist_ok=True)
        (root / "backend.py").write_text(backend_source(fail=fail), encoding="utf-8")
        client_table = ""
        activation = '[activation]\nbackend = "required"\n'
        if client:
            (root / "client.js").write_text("export default () => undefined\n", encoding="utf-8")
            client_table = '\n[client]\nbundle = "client.js"\nplatform = "web"\n'
            activation += 'client = "required"\n'
        (root / "plugin.toml").write_text(
            f'[plugin]\nid = "{plugin_id}"\nversion = "1.0.0"\nruntime_api = "1"\n\n'
            '[backend]\nentrypoint = "backend.py:plugin"\n'
            f"{client_table}\n{activation}",
            encoding="utf-8",
        )
        return root

    async def test_core_services_listener_and_browser_runtime_are_assembled(self) -> None:
        """One Host obtains every subsystem from active provider Fibers."""
        browser = self.root / "browser.js"
        browser.write_text("document.body.dataset.runtime = 'loaded'\n", encoding="utf-8")
        host = HarnessHost(HarnessHostConfig(port=0, browser_runtime=browser))

        await host.start()

        self.assertIs(host.state, HostState.RUNNING)
        for key in (
            AGENT_LOOP,
            PLUGIN_MANAGER,
            CLIENT_ARTIFACTS,
            BROWSER_BRIDGE,
            CLIENT_ACTIVATION,
            BRIDGE_RPC_REGISTRY,
            BRIDGE_EVENT_REGISTRY,
        ):
            with self.subTest(service=key.name):
                self.assertIsNotNone(host.runtime.root.lookup(key))
        async with ClientSession() as client:
            health = await client.get(f"{host.base_url}/health")
            self.assertEqual(await health.json(), {"status": "ok"})
            index = await client.get(f"{host.base_url}/")
            self.assertIn("/browser.js", await index.text())
            runtime = await client.get(f"{host.base_url}/browser.js")
            self.assertEqual(await runtime.read(), browser.read_bytes())

        old_url = host.base_url
        await asyncio.gather(host.close(), host.close())
        self.assertIs(host.state, HostState.CLOSED)
        self.assertEqual(host.runtime.fibers, ())
        with self.assertRaises(RuntimeError):
            _ = host.base_url
        async with ClientSession() as client:
            with self.assertRaises(ClientConnectorError):
                await client.get(f"{old_url}/health")

    async def test_catalog_plugins_activate_in_stable_identity_order(self) -> None:
        """All valid immediate children are enabled before startup returns."""
        self._write_plugin("z-last", "com.example.z-last", client=True)
        self._write_plugin("a-first", "com.example.a-first")
        host = HarnessHost(HarnessHostConfig(port=0, plugin_catalogs=(self.root / "catalog",)))

        await host.start()

        snapshots = host.manager.snapshot()
        self.assertEqual(tuple(snapshots), ("com.example.a-first", "com.example.z-last"))
        self.assertIs(snapshots["com.example.a-first"].state, PluginState.ACTIVE)
        self.assertIs(snapshots["com.example.z-last"].state, PluginState.WAITING)
        self.assertEqual(
            host.bridge.clients.current_revision("com.example.z-last"),
            snapshots["com.example.z-last"].revision,
        )
        await host.close()
        self.assertEqual(host.runtime.fibers, ())
        self.assertEqual(dict(host.bridge.clients.snapshot()), {})

    async def test_discovery_and_activation_failures_roll_back_startup(self) -> None:
        """Invalid candidates and required failures leave no active Fiber."""
        malformed = self.root / "catalog" / "malformed"
        malformed.mkdir(parents=True)
        (malformed / "plugin.toml").write_text("[plugin]\nid = 'bad'\n", encoding="utf-8")
        invalid = HarnessHost(HarnessHostConfig(port=0, plugin_catalogs=(self.root / "catalog",)))
        with self.assertRaisesRegex(HostStartupError, "plugin discovery failed"):
            await invalid.start()
        self.assertIs(invalid.state, HostState.FAILED)
        self.assertEqual(invalid.runtime.fibers, ())

        malformed.rename(self.root / "ignored")
        self._write_plugin("broken", "com.example.broken", fail=True)
        failed = HarnessHost(HarnessHostConfig(port=0, plugin_catalogs=(self.root / "catalog",)))
        with self.assertRaisesRegex(HostStartupError, "failed to activate"):
            await failed.start()
        self.assertIs(failed.state, HostState.FAILED)
        self.assertEqual(failed.runtime.fibers, ())

    async def test_invalid_paths_fail_before_core_mount(self) -> None:
        """Missing trusted inputs never establish the core composition."""
        host = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.root / "missing",),
                browser_runtime=self.root / "missing.js",
            )
        )
        with self.assertRaisesRegex(HostStartupError, "cannot resolve plugin catalog"):
            await host.start()
        self.assertEqual(host.runtime.fibers, ())
        with self.assertRaises(RuntimeError):
            await host.start()

    async def test_client_quorum_overrides_are_validated_before_serving(self) -> None:
        """Unknown and backend-only override targets reject Host startup."""
        unknown = HarnessHost(
            HarnessHostConfig(
                port=0,
                client_quorum_overrides=(("com.example.missing", ClientQuorum.ANY_CONNECTED),),
            )
        )
        with self.assertRaisesRegex(HostStartupError, "unknown plugin"):
            await unknown.start()

        self._write_plugin("backend", "com.example.backend")
        backend_only = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.root / "catalog",),
                client_quorum_overrides=(("com.example.backend", ClientQuorum.ANY_CONNECTED),),
            )
        )
        with self.assertRaisesRegex(HostStartupError, "backend-only plugin"):
            await backend_only.start()

    async def test_client_quorum_override_reaches_the_installed_aggregate(self) -> None:
        """A valid per-plugin deployment choice replaces the Host default."""
        self._write_plugin("client", "com.example.client", client=True)
        host = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.root / "catalog",),
                client_quorum_overrides=(("com.example.client", ClientQuorum.ANY_CONNECTED),),
            )
        )
        await host.start()
        snapshot = host.manager.snapshot()["com.example.client"]
        self.assertIs(snapshot.client_activation.quorum, ClientQuorum.ANY_CONNECTED)
        self.assertIs(snapshot.state, PluginState.WAITING)
        await host.close()

    def test_client_quorum_values_and_duplicate_overrides_are_rejected(self) -> None:
        """Unsupported quorum names and duplicate identities fail configuration."""
        with self.assertRaisesRegex(ValueError, "client quorum"):
            HarnessHostConfig(client_quorum="majority")  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "duplicate client quorum override"):
            HarnessHostConfig(
                client_quorum_overrides=(
                    ("com.example.client", ClientQuorum.ALL_CONNECTED),
                    ("com.example.client", ClientQuorum.ANY_CONNECTED),
                )
            )

    def test_cli_parser_builds_the_shared_configuration_inputs(self) -> None:
        """Both executable entrypoints use the exported parser inputs."""
        namespace = build_parser().parse_args(
            [
                "--session-id",
                "session-test",
                "--host",
                "localhost",
                "--port",
                "0",
                "--plugins",
                "one",
                "--plugins",
                "two",
                "--browser-runtime",
                "browser.js",
            ]
        )
        self.assertEqual(namespace.session_id, "session-test")
        self.assertEqual(namespace.host, "localhost")
        self.assertEqual(namespace.port, 0)
        self.assertEqual(namespace.plugins, [Path("one"), Path("two")])
        self.assertEqual(namespace.browser_runtime, Path("browser.js"))


if __name__ == "__main__":
    unittest.main()
