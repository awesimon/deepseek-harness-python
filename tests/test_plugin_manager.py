"""Runtime activation tests for the Dynamic Plugin Manager."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from harness.cordis import Cordis, ServiceKey
from harness.plugins import (
    ClientActivationState,
    ClientArtifactRegistry,
    PluginManager,
    PluginState,
)

DYNAMIC_VALUE = ServiceKey[str]("tests.dynamic-value")


def backend_source(value: str, *, fail: bool = False) -> str:
    """Return one standalone backend plugin module."""
    body = 'raise RuntimeError("backend failed")' if fail else f'await ctx.provide(KEY, "{value}")'
    return (
        "from harness.cordis import PluginSpec, ServiceKey\n"
        'KEY = ServiceKey[str]("tests.dynamic-value")\n'
        "async def apply(ctx, _config):\n"
        f"    {body}\n"
        'plugin = PluginSpec("dynamic-test", apply)\n'
    )


class PluginManagerTests(unittest.IsolatedAsyncioTestCase):
    """Exercise backend lifecycle, client publication, rollback, and update."""

    async def asyncSetUp(self) -> None:
        self.runtime = Cordis()
        self.clients = ClientArtifactRegistry()
        self.manager = PluginManager(self.runtime.root, clients=self.clients)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self) -> None:
        await self.runtime.close()
        self.temp.cleanup()

    def _write_plugin(
        self,
        value: str,
        *,
        version: str = "1.0.0",
        client: bool = False,
        fail: bool = False,
        backend_policy: str = "required",
    ) -> None:
        (self.root / "backend.py").write_text(
            backend_source(value, fail=fail),
            encoding="utf-8",
        )
        client_text = ""
        activation = f'\n[activation]\nbackend = "{backend_policy}"\n'
        if client:
            (self.root / "client.js").write_bytes(f"client-{value}".encode())
            client_text = '\n[client]\nbundle = "client.js"\nplatform = "web"\n'
            activation += 'client = "required"\n'
        (self.root / "plugin.toml").write_text(
            f'[plugin]\nid = "com.example.dynamic"\nversion = "{version}"\n'
            'runtime_api = "1"\n\n[backend]\nentrypoint = "backend.py:plugin"\n'
            f"{client_text}{activation}",
            encoding="utf-8",
        )

    async def test_backend_service_appears_and_disappears_with_enable(self) -> None:
        """Runtime loading mounts one Fiber and disable removes its Service."""
        self._write_plugin("v1")
        installed = await self.manager.install(self.root)
        self.assertIs(installed.state, PluginState.DISABLED)
        self.assertIs(
            installed.client_activation.state,
            ClientActivationState.NOT_APPLICABLE,
        )

        active = await self.manager.enable("com.example.dynamic")
        self.assertIs(active.state, PluginState.ACTIVE)
        self.assertEqual(self.runtime.root.lookup(DYNAMIC_VALUE), "v1")

        disabled = await self.manager.disable("com.example.dynamic")
        self.assertIs(disabled.state, PluginState.DISABLED)
        self.assertIsNone(self.runtime.root.lookup(DYNAMIC_VALUE))

    async def test_client_only_plugin_publishes_without_backend_fiber(self) -> None:
        """A browser-only contribution becomes active without Python code."""
        (self.root / "client.js").write_bytes(b"client-only")
        (self.root / "plugin.toml").write_text(
            '[plugin]\nid = "com.example.client"\nversion = "1.0.0"\n'
            'runtime_api = "1"\n\n[client]\nbundle = "client.js"\nplatform = "web"\n',
            encoding="utf-8",
        )
        await self.manager.install(self.root)
        active = await self.manager.enable("com.example.client")

        self.assertIs(active.state, PluginState.ACTIVE)
        self.assertIsNone(active.backend_module)
        self.assertEqual(self.runtime.fibers, ())
        self.assertEqual(self.clients.get("com.example.client", active.revision), b"client-only")

    async def test_full_stack_publication_and_required_failure_rollback(self) -> None:
        """Required backend failure leaves no published client contribution."""
        self._write_plugin("broken", client=True, fail=True)
        await self.manager.install(self.root)
        failed = await self.manager.enable("com.example.dynamic")

        self.assertIs(failed.state, PluginState.FAILED)
        self.assertIsNone(self.clients.current_revision("com.example.dynamic"))
        self.assertIsNone(self.runtime.root.lookup(DYNAMIC_VALUE))
        self.assertEqual(self.runtime.fibers, ())

    async def test_optional_backend_failure_keeps_client_degraded(self) -> None:
        """Optional backend failure retains successful client publication."""
        self._write_plugin("broken", client=True, fail=True, backend_policy="optional")
        await self.manager.install(self.root)
        degraded = await self.manager.enable("com.example.dynamic")

        self.assertIs(degraded.state, PluginState.DEGRADED)
        self.assertEqual(
            self.clients.get("com.example.dynamic", degraded.revision),
            b"client-broken",
        )
        await self.manager.disable("com.example.dynamic")
        self.assertIsNone(self.clients.current_revision("com.example.dynamic"))

    async def test_update_replaces_revision_and_retains_explicit_rollback(self) -> None:
        """Only one revision serves while previous metadata remains available."""
        self._write_plugin("v1")
        await self.manager.install(self.root)
        first = await self.manager.enable("com.example.dynamic")
        first_module = first.backend_module

        self._write_plugin("v2", version="1.1.0")
        second = await self.manager.update(self.root)
        self.assertIs(second.state, PluginState.ACTIVE)
        self.assertNotEqual(second.backend_module, first_module)
        self.assertEqual(second.previous_revision, first.revision)
        self.assertEqual(self.runtime.root.lookup(DYNAMIC_VALUE), "v2")

        rolled_back = await self.manager.rollback("com.example.dynamic")
        self.assertIs(rolled_back.state, PluginState.ACTIVE)
        self.assertEqual(self.runtime.root.lookup(DYNAMIC_VALUE), "v1")

    async def test_concurrent_enable_and_disable_are_idempotent(self) -> None:
        """Serialized duplicate mutations converge on one activation."""
        self._write_plugin("v1", client=True)
        await self.manager.install(self.root)
        enabled = await asyncio.gather(
            self.manager.enable("com.example.dynamic"),
            self.manager.enable("com.example.dynamic"),
        )
        self.assertEqual(enabled[0].revision, enabled[1].revision)
        self.assertEqual(len(self.runtime.fibers), 1)

        disabled = await asyncio.gather(
            self.manager.disable("com.example.dynamic"),
            self.manager.disable("com.example.dynamic"),
        )
        self.assertTrue(all(item.state is PluginState.DISABLED for item in disabled))
        self.assertEqual(self.runtime.fibers, ())
