"""Assembled Host evidence for one generated full-stack plugin."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from harness.host import HarnessHost, HarnessHostConfig
from harness.plugins import ClientActivationState, PluginState
from harness.scaffold import PluginKind, create_plugin, validate_plugin

PLUGIN_ID = "com.example.generated-full-stack"


class GeneratedFullStackEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Build a generated project and activate both contributions unchanged."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = Path(__file__).resolve().parents[1]
        subprocess.run(
            ["pnpm", "--dir", "frontend", "run", "build"],
            cwd=cls.project,
            check=True,
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temporary.name) / "catalog"
        self.catalog.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _build_client(self, plugin_root: Path) -> None:
        frontend = self.project / "frontend"
        modules = plugin_root / "frontend/node_modules"
        package_scope = modules / "@deepseek-harness"
        package_scope.mkdir(parents=True)
        (package_scope / "browser-bridge-client").symlink_to(frontend)
        subprocess.run(
            [
                str(frontend / "node_modules/.bin/esbuild"),
                str(plugin_root / "frontend/src/plugin.ts"),
                "--bundle",
                "--format=esm",
                "--platform=browser",
                f"--outfile={plugin_root / 'frontend/dist/client.js'}",
            ],
            cwd=plugin_root,
            check=True,
        )

    async def test_generated_plugin_rpc_events_and_disable(self) -> None:
        """Generated source reaches active, exchanges traffic, and fully unloads."""
        plugin_root = create_plugin(
            PluginKind.FULL_STACK,
            PLUGIN_ID,
            self.catalog / "generated",
        )
        self._build_client(plugin_root)
        self.assertIs(validate_plugin(plugin_root), PluginKind.FULL_STACK)
        host = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.catalog,),
                browser_runtime=self.project / "frontend/dist/browser.js",
            )
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page()
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            try:
                await host.start()
                await page.goto(host.base_url)
                await page.wait_for_function("globalThis.__examplePlugin === 'mounted'")
                active = host.manager.snapshot()[PLUGIN_ID]
                self.assertIs(active.state, PluginState.ACTIVE)
                self.assertIs(active.client_activation.state, ClientActivationState.ACTIVE)
                self.assertIsNotNone(active.backend_module)

                disabled = await host.manager.disable(PLUGIN_ID)
                await page.wait_for_function("!('__examplePlugin' in globalThis)")
                self.assertIs(disabled.state, PluginState.DISABLED)
                self.assertIsNone(disabled.backend_module)
                for page_id in host.bridge.page_ids():
                    self.assertEqual(dict(host.bridge.page_snapshot(page_id)), {})
                self.assertEqual(page_errors, [])
            finally:
                await host.close()
                await browser.close()


if __name__ == "__main__":
    unittest.main()
