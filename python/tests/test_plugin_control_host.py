"""Real Host HTTP acceptance for local plugin control."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

from aiohttp import ClientSession

from harness.control import PluginWatcherConfig, build_plugin_parser, run_plugin_cli
from harness.cordis import ServiceKey
from harness.host import HarnessHost, HarnessHostConfig, HostStartupError

CONTROL_HTTP_VALUE = ServiceKey[str]("tests.control-http-value")


class PluginControlHostTests(unittest.IsolatedAsyncioTestCase):
    """Exercise strict HTTP admission and the full Manager lifecycle."""

    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temporary.name) / "catalog"
        self.catalog.mkdir()
        self.root = self.catalog / "plugin"
        self.host: HarnessHost | None = None

    async def asyncTearDown(self) -> None:
        if self.host is not None:
            await self.host.close()
        self.temporary.cleanup()

    def _write(self, value: str, version: str = "1.0.0") -> None:
        self.root.mkdir(exist_ok=True)
        (self.root / "backend.py").write_text(
            "from harness.cordis import PluginSpec, ServiceKey\n"
            'KEY = ServiceKey[str]("tests.control-http-value")\n'
            "async def apply(ctx, _config):\n"
            f'    await ctx.provide(KEY, "{value}")\n'
            'plugin = PluginSpec("control-http", apply)\n',
            encoding="utf-8",
        )
        (self.root / "plugin.toml").write_text(
            '[plugin]\nid = "com.example.control-http"\n'
            f'version = "{version}"\nruntime_api = "1"\n\n'
            '[backend]\nentrypoint = "backend.py:plugin"\n',
            encoding="utf-8",
        )

    async def _start(self) -> HarnessHost:
        self.host = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.catalog,),
                control_enabled=True,
            )
        )
        await self.host.start()
        return self.host

    async def test_http_lifecycle_and_stale_preconditions(self) -> None:
        """Every lifecycle command returns Manager state and requires fresh tokens."""
        host = await self._start()
        self._write("one")
        base = f"{host.base_url}/api/control/v1/plugins"
        async with ClientSession() as client:
            installed_response = await client.post(
                f"{base}/install",
                json={"pluginRoot": str(self.root), "expectedAbsent": True},
            )
            self.assertEqual(installed_response.status, 200)
            installed = (await installed_response.json())["plugin"]
            self.assertEqual(installed["mutationVersion"], 1)
            self.assertEqual(installed["state"], "disabled")

            enabled_response = await client.post(
                f"{base}/com.example.control-http/enable",
                json=_preconditions(installed),
            )
            enabled = (await enabled_response.json())["plugin"]
            self.assertEqual(enabled_response.status, 200)
            self.assertEqual(host.runtime.root.lookup(CONTROL_HTTP_VALUE), "one")

            stale = await client.post(
                f"{base}/com.example.control-http/disable",
                json=_preconditions(installed),
            )
            self.assertEqual(stale.status, 409)
            self.assertEqual((await stale.json())["code"], "mutation_conflict")

            self._write("two", "1.1.0")
            updated_response = await client.post(
                f"{base}/com.example.control-http/update",
                json=_preconditions(enabled),
            )
            updated = (await updated_response.json())["plugin"]
            self.assertEqual(host.runtime.root.lookup(CONTROL_HTTP_VALUE), "two")

            rollback_response = await client.post(
                f"{base}/com.example.control-http/rollback",
                json={
                    **_preconditions(updated),
                    "targetRevision": updated["previousRevision"],
                },
            )
            rolled_back = (await rollback_response.json())["plugin"]
            self.assertEqual(host.runtime.root.lookup(CONTROL_HTTP_VALUE), "one")

            disabled_response = await client.post(
                f"{base}/com.example.control-http/disable",
                json=_preconditions(rolled_back),
            )
            disabled = (await disabled_response.json())["plugin"]
            removed_response = await client.post(
                f"{base}/com.example.control-http/uninstall",
                json=_preconditions(disabled),
            )
            self.assertEqual(removed_response.status, 200)
            self.assertEqual((await removed_response.json())["tombstone"]["pluginId"],
                             "com.example.control-http")
            inventory = await client.get(base)
            self.assertEqual((await inventory.json())["plugins"], [])

    async def test_origin_content_type_and_fields_are_strict(self) -> None:
        """Browser origin and JSON constraints reject before Manager mutation."""
        host = await self._start()
        self._write("one")
        endpoint = f"{host.base_url}/api/control/v1/plugins/install"
        async with ClientSession() as client:
            wrong_origin = await client.post(
                endpoint,
                json={"pluginRoot": str(self.root), "expectedAbsent": True},
                headers={"Origin": "https://example.test"},
            )
            self.assertEqual(wrong_origin.status, 403)
            text_body = await client.post(endpoint, data="{}")
            self.assertEqual(text_body.status, 415)
            extra = await client.post(
                endpoint,
                json={
                    "pluginRoot": str(self.root),
                    "expectedAbsent": True,
                    "extra": True,
                },
            )
            self.assertEqual(extra.status, 400)
            accepted = await client.post(
                endpoint,
                json={"pluginRoot": str(self.root), "expectedAbsent": True},
                headers={"Origin": host.base_url},
            )
            self.assertEqual(accepted.status, 200)

    async def test_control_rejects_non_loopback_binding_before_mount(self) -> None:
        """Unauthenticated control never binds a wildcard listener."""
        host = HarnessHost(
            HarnessHostConfig(
                bind_host="0.0.0.0",
                port=0,
                plugin_catalogs=(self.catalog,),
                control_enabled=True,
            )
        )
        with self.assertRaisesRegex(HostStartupError, "loopback-only"):
            await host.start()
        self.assertEqual(host.runtime.fibers, ())

    async def test_cli_covers_commands_and_does_not_retry_conflict(self) -> None:
        """The JSON CLI uses HTTP snapshots and surfaces one stale mutation."""
        host = await self._start()
        self._write("one")
        installed = await _run_cli(
            host,
            ["install", str(self.root)],
        )
        installed_plugin = installed["plugin"]
        listed = await _run_cli(host, ["list"])
        self.assertEqual(len(listed["plugins"]), 1)
        shown = await _run_cli(host, ["show", "com.example.control-http"])
        self.assertEqual(shown["revision"], installed_plugin["revision"])

        stale_status, _stdout, stderr = await _run_cli_raw(
            host,
            [
                "enable",
                "com.example.control-http",
                "--revision",
                "stale",
                "--mutation-version",
                "0",
            ],
        )
        self.assertEqual(stale_status, 1)
        self.assertIn("mutation_conflict", stderr)
        self.assertEqual(host.control.get("com.example.control-http").mutation_version, 1)

        await _run_cli(host, ["enable", "com.example.control-http"])
        self._write("two", "1.1.0")
        updated = await _run_cli(host, ["update", "com.example.control-http"])
        self.assertIsNotNone(updated["plugin"]["previousRevision"])
        await _run_cli(host, ["rollback", "com.example.control-http"])
        await _run_cli(host, ["disable", "com.example.control-http"])
        removed = await _run_cli(host, ["uninstall", "com.example.control-http"])
        self.assertEqual(removed["tombstone"]["pluginId"], "com.example.control-http")

    async def test_real_filesystem_watch_hot_updates_installed_root(self) -> None:
        """watchfiles delivery reaches the same revisioned control lifecycle."""
        self._write("one")
        self.host = HarnessHost(
            HarnessHostConfig(
                port=0,
                plugin_catalogs=(self.catalog,),
                watched_catalogs=(self.catalog,),
                watcher=PluginWatcherConfig(debounce_seconds=0.05),
                control_enabled=True,
            )
        )
        await self.host.start()
        initial = self.host.control.get("com.example.control-http")
        self.assertEqual(self.host.runtime.root.lookup(CONTROL_HTTP_VALUE), "one")

        self._write("two", "1.1.0")
        await _wait_for(
            lambda: self.host is not None
            and self.host.control.get("com.example.control-http").plugin.revision
            != initial.plugin.revision,
        )
        self.assertEqual(self.host.runtime.root.lookup(CONTROL_HTTP_VALUE), "two")


def _preconditions(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        "expectedRevision": snapshot["revision"],
        "expectedMutationVersion": snapshot["mutationVersion"],
    }


async def _run_cli(host: HarnessHost, arguments: list[str]) -> dict[str, object]:
    status, stdout, stderr = await _run_cli_raw(host, arguments)
    if status != 0:
        raise AssertionError(f"CLI failed: {stderr}")
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise TypeError("CLI output is not an object")
    return payload


async def _run_cli_raw(
    host: HarnessHost,
    arguments: list[str],
) -> tuple[int, str, str]:
    namespace = build_plugin_parser().parse_args(["--url", host.base_url, *arguments])
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        status = await run_plugin_cli(namespace)
    return status, stdout.getvalue(), stderr.getvalue()


async def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
