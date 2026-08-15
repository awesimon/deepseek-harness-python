"""Plugin Control Plane serialization and optimistic concurrency tests."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from harness.control import (
    PluginCatalogWatcher,
    PluginControlConflictError,
    PluginControlService,
    PluginWatcherConfig,
    UnsafePluginRootError,
    WatchCreatePolicy,
    WatchDeletePolicy,
)
from harness.cordis import Cordis, ServiceKey
from harness.plugins import PluginManager, PluginState

CONTROL_VALUE = ServiceKey[str]("tests.control-value")


class PluginControlServiceTests(unittest.IsolatedAsyncioTestCase):
    """Exercise trusted roots, lifecycle commands, tokens, and tombstones."""

    async def asyncSetUp(self) -> None:
        self.runtime = Cordis()
        self.manager = PluginManager(self.runtime.root)
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temporary.name) / "catalog"
        self.catalog.mkdir()
        self.root = self.catalog / "plugin"
        self.root.mkdir()
        self.control = PluginControlService(self.manager, (self.catalog.resolve(),))

    async def asyncTearDown(self) -> None:
        await self.control.close()
        await self.runtime.close()
        self.temporary.cleanup()

    def _write(self, value: str, version: str = "1.0.0") -> None:
        (self.root / "backend.py").write_text(
            "from harness.cordis import PluginSpec, ServiceKey\n"
            'KEY = ServiceKey[str]("tests.control-value")\n'
            "async def apply(ctx, _config):\n"
            f'    await ctx.provide(KEY, "{value}")\n'
            'plugin = PluginSpec("control-test", apply)\n',
            encoding="utf-8",
        )
        (self.root / "plugin.toml").write_text(
            '[plugin]\nid = "com.example.control"\n'
            f'version = "{version}"\nruntime_api = "1"\n\n'
            '[backend]\nentrypoint = "backend.py:plugin"\n',
            encoding="utf-8",
        )

    async def test_complete_lifecycle_uses_monotonic_preconditions(self) -> None:
        """Every changed operation returns the exact next concurrency token."""
        self._write("one")
        installed = await self.control.install(self.root, expected_absent=True)
        self.assertEqual(installed.snapshot.mutation_version, 1)
        self.assertIs(installed.snapshot.plugin.state, PluginState.DISABLED)

        enabled = await self.control.enable(
            "com.example.control",
            expected_revision=installed.snapshot.plugin.revision,
            expected_mutation_version=1,
        )
        self.assertEqual(enabled.snapshot.mutation_version, 2)
        self.assertEqual(self.runtime.root.lookup(CONTROL_VALUE), "one")

        repeated = await self.control.enable(
            "com.example.control",
            expected_revision=enabled.snapshot.plugin.revision,
            expected_mutation_version=2,
        )
        self.assertEqual(repeated.snapshot.mutation_version, 2)

        self._write("two", "1.1.0")
        updated = await self.control.update(
            "com.example.control",
            expected_revision=enabled.snapshot.plugin.revision,
            expected_mutation_version=2,
        )
        self.assertEqual(updated.snapshot.mutation_version, 3)
        self.assertEqual(self.runtime.root.lookup(CONTROL_VALUE), "two")

        rolled_back = await self.control.rollback(
            "com.example.control",
            expected_revision=updated.snapshot.plugin.revision,
            expected_mutation_version=3,
            target_revision=updated.snapshot.plugin.previous_revision or "",
        )
        self.assertEqual(rolled_back.snapshot.mutation_version, 4)
        self.assertEqual(self.runtime.root.lookup(CONTROL_VALUE), "one")

        disabled = await self.control.disable(
            "com.example.control",
            expected_revision=rolled_back.snapshot.plugin.revision,
            expected_mutation_version=4,
        )
        tombstone = await self.control.uninstall(
            "com.example.control",
            expected_revision=disabled.snapshot.plugin.revision,
            expected_mutation_version=5,
        )
        self.assertEqual(tombstone.mutation_version, 6)
        self.assertEqual(self.control.inventory().plugins, ())

    async def test_stale_and_unsafe_intent_never_mutates_manager(self) -> None:
        """Revision, mutation version, and catalog containment reject before effects."""
        self._write("one")
        installed = await self.control.install(self.root, expected_absent=True)
        with self.assertRaises(PluginControlConflictError) as caught:
            await self.control.enable(
                "com.example.control",
                expected_revision="stale",
                expected_mutation_version=1,
            )
        self.assertEqual(caught.exception.current, installed.snapshot)
        self.assertIs(self.manager.snapshot()["com.example.control"].state, PluginState.DISABLED)

        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        with self.assertRaises(UnsafePluginRootError):
            await self.control.install(outside, expected_absent=True)

    async def test_inventory_is_ordered_and_install_requires_absence(self) -> None:
        """Collection snapshots and install admission remain deterministic."""
        self._write("one")
        first = await self.control.install(self.root, expected_absent=True)
        with self.assertRaises(PluginControlConflictError):
            await self.control.install(self.root, expected_absent=True)
        inventory = self.control.inventory()
        self.assertEqual(inventory.inventory_version, 1)
        self.assertEqual(inventory.plugins, (first.snapshot,))

    async def test_watcher_coalesces_update_and_preserves_invalid_candidate(self) -> None:
        """Debounced rescans apply latest valid bytes and retain serving invalid state."""
        self._write("one")
        await self.control.install(self.root, expected_absent=True, enable=True)
        watcher = PluginCatalogWatcher(
            self.control,
            PluginWatcherConfig(debounce_seconds=0.01),
        )
        self._write("two", "1.1.0")
        watcher.notify(self.root)
        watcher.notify(self.root)
        await _wait_for(
            lambda: self.control.get("com.example.control").mutation_version == 2
        )
        current = self.control.get("com.example.control")
        self.assertEqual(self.runtime.root.lookup(CONTROL_VALUE), "two")

        (self.root / "plugin.toml").write_text("invalid", encoding="utf-8")
        watcher.notify(self.root)
        await _wait_for(lambda: self.control.inventory().watcher.diagnostic is not None)
        self.assertEqual(
            self.control.get("com.example.control").plugin.revision,
            current.plugin.revision,
        )
        self.assertEqual(self.runtime.root.lookup(CONTROL_VALUE), "two")
        await watcher.close()

    async def test_watcher_create_and_delete_policies_use_control_lifecycle(self) -> None:
        """Configured root creation and removal never bypass Manager teardown."""
        self._write("one")
        watcher = PluginCatalogWatcher(
            self.control,
            PluginWatcherConfig(
                debounce_seconds=0.01,
                create_policy=WatchCreatePolicy.INSTALL_ENABLED,
                delete_policy=WatchDeletePolicy.UNINSTALL,
            ),
        )
        watcher.notify(self.root)
        await _wait_for(lambda: bool(self.control.inventory().plugins))
        self.assertEqual(self.runtime.root.lookup(CONTROL_VALUE), "one")

        (self.root / "plugin.toml").unlink()
        watcher.notify(self.root)
        await _wait_for(lambda: not self.control.inventory().plugins)
        self.assertIsNone(self.runtime.root.lookup(CONTROL_VALUE))
        await watcher.close()


async def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.005)


if __name__ == "__main__":
    unittest.main()
