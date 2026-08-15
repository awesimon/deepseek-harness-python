"""Real Chromium lifecycle coverage for the assembled Harness Host."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from harness.host import HarnessHost, HarnessHostConfig, HostState
from harness.plugins import PluginState

PLUGIN_ID = "com.example.browser-e2e"


def backend_source(label: str) -> str:
    """Return a revision-bound backend that echoes RPC and Events."""
    return f'''from harness.bridge import (
    BRIDGE_EVENT_REGISTRY,
    BRIDGE_RPC_REGISTRY,
    BROWSER_BRIDGE,
)
from harness.cordis import PluginSpec
from harness.plugins import PLUGIN_RUNTIME_IDENTITY

LABEL = "{label}"

async def apply(ctx, _config):
    bridge = ctx.require(BROWSER_BRIDGE)
    rpc = ctx.require(BRIDGE_RPC_REGISTRY)
    events = ctx.require(BRIDGE_EVENT_REGISTRY)
    identity = ctx.require(PLUGIN_RUNTIME_IDENTITY)
    received = {{"label": None}}

    def describe(arguments):
        return {{
            "revision": LABEL,
            "client": received["label"],
            "value": arguments["value"],
        }}

    async def receive(page_id, payload):
        received["label"] = payload["label"]
        await bridge.emit_event(
            identity.plugin_id,
            identity.revision,
            "from-backend",
            {{"revision": LABEL}},
            page_id=page_id,
        )

    await ctx.effect(
        lambda: rpc.register(identity.plugin_id, identity.revision, "describe", describe),
        "browser-e2e-rpc",
    )
    await ctx.effect(
        lambda: events.register(
            identity.plugin_id,
            identity.revision,
            "from-client",
            receive,
        ),
        "browser-e2e-event",
    )

plugin = PluginSpec(
    "browser-e2e-{label}",
    apply,
    requires=(
        BROWSER_BRIDGE,
        BRIDGE_RPC_REGISTRY,
        BRIDGE_EVENT_REGISTRY,
        PLUGIN_RUNTIME_IDENTITY,
    ),
)
'''


def client_source(label: str) -> str:
    """Return one browser contribution with visible lifecycle evidence."""
    return f'''export function createPlugin(api) {{
  return () => {{
    const lifecycle = window.__harnessLifecycle ??= []
    lifecycle.push("start:{label}")
    const element = document.createElement("div")
    element.id = "full-stack"
    element.dataset.version = "{label}"
    document.querySelector("#harness-root").append(element)

    const stopEvent = api.on("from-backend", (payload) => {{
      element.dataset.event = payload.revision
    }})
    const previous = window.__harnessPluginApi
    window.__harnessPluginApi = api
    const timer = window.setTimeout(async () => {{
      if (previous) {{
        try {{
          await previous.call("describe", {{ value: "stale" }})
          element.dataset.stale = "accepted"
        }} catch (error) {{
          element.dataset.stale = error.code ?? error.name
        }}
      }}
      api.emit("from-client", {{ label: "{label}" }})
      const result = await api.call("describe", {{ value: "{label}" }})
      element.dataset.rpc = `${{result.revision}}:${{result.client}}:${{result.value}}`
    }}, 25)

    return () => {{
      window.clearTimeout(timer)
      stopEvent()
      lifecycle.push("stop:{label}")
      element.remove()
    }}
  }}
}}
'''


class HostBrowserEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Exercise Browser Bridge traffic through a real Cordis TS runtime."""

    @classmethod
    def setUpClass(cls) -> None:
        """Build the browser entrypoint consumed by the Host."""
        cls.project = Path(__file__).resolve().parents[1]
        subprocess.run(
            ["pnpm", "--dir", "frontend", "run", "build:browser"],
            cwd=cls.project,
            check=True,
        )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temp.name) / "catalog"
        self.plugin_root = self.catalog / "full-stack"
        self.plugin_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_plugin(self, label: str, version: str) -> None:
        (self.plugin_root / "backend.py").write_text(
            backend_source(label),
            encoding="utf-8",
        )
        (self.plugin_root / "client.js").write_text(
            client_source(label),
            encoding="utf-8",
        )
        (self.plugin_root / "plugin.toml").write_text(
            f'''[plugin]
id = "{PLUGIN_ID}"
version = "{version}"
runtime_api = "1"

[backend]
entrypoint = "backend.py:plugin"

[client]
bundle = "client.js"
platform = "web"

[activation]
backend = "required"
client = "required"
''',
            encoding="utf-8",
        )

    async def test_update_stale_rejection_disable_and_host_teardown(self) -> None:
        """Chromium observes both runtimes following one plugin Revision."""
        self._write_plugin("v1", "1.0.0")
        host = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.catalog,),
                browser_runtime=self.project / "frontend" / "dist" / "browser.js",
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
                await page.locator(
                    '#full-stack[data-version="v1"]'
                    '[data-rpc="v1:v1:v1"][data-event="v1"]'
                ).wait_for(state="attached")
                first = host.manager.snapshot()[PLUGIN_ID]

                self._write_plugin("v2", "1.1.0")
                second = await host.manager.update(self.plugin_root)
                self.assertNotEqual(second.revision, first.revision)
                await page.locator(
                    '#full-stack[data-version="v2"]'
                    '[data-rpc="v2:v2:v2"][data-event="v2"]'
                    '[data-stale="stale_client"]'
                ).wait_for(state="attached")
                self.assertEqual(
                    await page.evaluate("window.__harnessLifecycle"),
                    ["start:v1", "stop:v1", "start:v2"],
                )

                disabled = await host.manager.disable(PLUGIN_ID)
                self.assertIs(disabled.state, PluginState.DISABLED)
                await page.locator("#full-stack").wait_for(state="detached")
                self.assertEqual(
                    await page.evaluate("window.__harnessLifecycle"),
                    ["start:v1", "stop:v1", "start:v2", "stop:v2"],
                )
                self.assertEqual(dict(host.bridge.clients.snapshot()), {})

                await host.close()
                await page.locator('#harness-root[data-bridge="closed"]').wait_for(
                    state="attached"
                )
                self.assertIs(host.state, HostState.CLOSED)
                self.assertEqual(host.runtime.fibers, ())
                self.assertEqual(page_errors, [])
            finally:
                await host.close()
                await browser.close()


if __name__ == "__main__":
    unittest.main()
