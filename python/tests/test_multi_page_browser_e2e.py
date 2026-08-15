"""Real Chromium evidence for multi-page client activation aggregation."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from harness.host import HarnessHost, HarnessHostConfig
from harness.plugins import ClientActivationState, ClientQuorum, PluginState

ALL_PLUGIN = "com.example.all-pages"
ANY_PLUGIN = "com.example.any-page"


def client_source(label: str) -> str:
    """Return a page plugin that deliberately rejects the failing test page."""
    return f'''export function createPlugin(api) {{
  return () => {{
    if (new URL(location.href).searchParams.get("fail") === "1") {{
      throw new Error(`deliberate ${{api.pluginId}} failure`)
    }}
    const marker = document.createElement("div")
    marker.dataset.plugin = api.pluginId
    marker.dataset.label = "{label}"
    document.body.append(marker)
    return () => marker.remove()
  }}
}}
'''


class MultiPageBrowserEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Aggregate divergent real page Fibers under both quorum policies."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = Path(__file__).resolve().parents[2]
        subprocess.run(
            ["pnpm", "--dir", "frontend", "run", "build:browser"],
            cwd=cls.project,
            check=True,
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temporary.name) / "catalog"
        self.all_root = self.catalog / "all"
        self.any_root = self.catalog / "any"
        self.all_root.mkdir(parents=True)
        self.any_root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_plugin(self, root: Path, plugin_id: str, label: str, version: str) -> None:
        (root / "client.js").write_text(client_source(label), encoding="utf-8")
        (root / "plugin.toml").write_text(
            f'''[plugin]
id = "{plugin_id}"
version = "{version}"
runtime_api = "1"

[client]
bundle = "client.js"
platform = "web"

[activation]
client = "required"
''',
            encoding="utf-8",
        )

    async def _wait_state(
        self,
        host: HarnessHost,
        plugin_id: str,
        state: PluginState,
        client_state: ClientActivationState,
    ) -> None:
        for _attempt in range(200):
            snapshot = host.manager.snapshot()[plugin_id]
            if snapshot.state is state and snapshot.client_activation.state is client_state:
                return
            await asyncio.sleep(0.025)
        snapshot = host.manager.snapshot()[plugin_id]
        self.fail(
            f"{plugin_id} remained {snapshot.state.value}/{snapshot.client_activation.state.value}"
        )

    async def test_divergence_recovery_update_and_disable(self) -> None:
        """Two pages drive all/any quorum state and recover without republishing."""
        self._write_plugin(self.all_root, ALL_PLUGIN, "v1", "1.0.0")
        self._write_plugin(self.any_root, ANY_PLUGIN, "v1", "1.0.0")
        host = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.catalog,),
                browser_runtime=self.project / "frontend/dist/browser.js",
                client_quorum_overrides=((ANY_PLUGIN, ClientQuorum.ANY_CONNECTED),),
            )
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            healthy = await browser.new_page()
            failing = await browser.new_page()
            try:
                await host.start()
                await self._wait_state(
                    host,
                    ALL_PLUGIN,
                    PluginState.WAITING,
                    ClientActivationState.UNOBSERVED,
                )

                await healthy.goto(host.base_url)
                await healthy.locator(f'[data-plugin="{ALL_PLUGIN}"][data-label="v1"]').wait_for(
                    state="attached"
                )
                await healthy.locator(f'[data-plugin="{ANY_PLUGIN}"][data-label="v1"]').wait_for(
                    state="attached"
                )
                await self._wait_state(
                    host,
                    ALL_PLUGIN,
                    PluginState.ACTIVE,
                    ClientActivationState.ACTIVE,
                )

                await failing.goto(f"{host.base_url}/?fail=1")
                await self._wait_state(
                    host,
                    ALL_PLUGIN,
                    PluginState.FAILED,
                    ClientActivationState.FAILED,
                )
                await self._wait_state(
                    host,
                    ANY_PLUGIN,
                    PluginState.DEGRADED,
                    ClientActivationState.DEGRADED,
                )

                await failing.close()
                await self._wait_state(
                    host,
                    ALL_PLUGIN,
                    PluginState.ACTIVE,
                    ClientActivationState.ACTIVE,
                )
                await self._wait_state(
                    host,
                    ANY_PLUGIN,
                    PluginState.ACTIVE,
                    ClientActivationState.ACTIVE,
                )

                first_revision = host.manager.snapshot()[ALL_PLUGIN].revision
                self._write_plugin(self.all_root, ALL_PLUGIN, "v2", "1.1.0")
                updated = await host.manager.update(self.all_root)
                self.assertNotEqual(updated.revision, first_revision)
                await healthy.locator(f'[data-plugin="{ALL_PLUGIN}"][data-label="v2"]').wait_for(
                    state="attached"
                )
                await self._wait_state(
                    host,
                    ALL_PLUGIN,
                    PluginState.ACTIVE,
                    ClientActivationState.ACTIVE,
                )

                await host.manager.disable(ALL_PLUGIN)
                await host.manager.disable(ANY_PLUGIN)
                await healthy.locator(f'[data-plugin="{ALL_PLUGIN}"]').wait_for(state="detached")
                await healthy.locator(f'[data-plugin="{ANY_PLUGIN}"]').wait_for(state="detached")
                await self._wait_state(
                    host,
                    ALL_PLUGIN,
                    PluginState.DISABLED,
                    ClientActivationState.NOT_PUBLISHED,
                )
                await self._wait_state(
                    host,
                    ANY_PLUGIN,
                    PluginState.DISABLED,
                    ClientActivationState.NOT_PUBLISHED,
                )
                page_ids = host.bridge.page_ids()
                self.assertEqual(len(page_ids), 1)
                self.assertEqual(dict(host.bridge.page_snapshot(page_ids[0])), {})
            finally:
                await host.close()
                await browser.close()


if __name__ == "__main__":
    unittest.main()
