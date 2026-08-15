"""Overwrite-safe rendering for the built-in plugin templates."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import tempfile
import tomllib
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from harness.plugins import load_manifest
from harness.plugins.manifest import PLUGIN_ID, VERSION
from harness.sdk import (
    BROWSER_SDK_PACKAGE,
    BROWSER_SDK_VERSION,
    PYTHON_SDK_VERSION,
    RUNTIME_API,
)


class PluginKind(str, Enum):
    """Supported logical plugin contribution forms."""

    BACKEND = "backend"
    CLIENT = "client"
    FULL_STACK = "full-stack"


def create_plugin(
    kind: PluginKind,
    plugin_id: str,
    destination: str | Path,
    *,
    version: str = "0.1.0",
) -> Path:
    """Render one complete plugin project without replacing any destination."""
    _validate_identity(plugin_id, version)
    target = Path(destination)
    parent = target.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError(f"destination parent is not a directory: {parent}")
    if target.name in ("", ".", ".."):
        raise ValueError("destination must name a new child directory")
    resolved = parent / target.name
    if os.path.lexists(resolved):
        raise FileExistsError(f"destination already exists: {resolved}")

    files = _render(kind, plugin_id, version)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=parent))
    try:
        for relative in sorted(files):
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(files[relative], encoding="utf-8", newline="\n")
            path.chmod(0o644)
        _validate_source_tree(temporary, kind, require_artifacts=False)
        _rename_exclusive(temporary, resolved)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return resolved


def validate_plugin(plugin_root: str | Path) -> PluginKind:
    """Validate one generated project and every declared runtime artifact."""
    root = Path(plugin_root).resolve(strict=True)
    kind = _kind_from_manifest(root)
    _validate_source_tree(root, kind, require_artifacts=True)
    load_manifest(root)
    return kind


def _validate_identity(plugin_id: str, version: str) -> None:
    if not PLUGIN_ID.fullmatch(plugin_id):
        raise ValueError(f"invalid plugin id: {plugin_id!r}")
    if not VERSION.fullmatch(version):
        raise ValueError(f"invalid plugin version: {version!r}")
    tooling = plugin_id.replace(".", "-")
    if not tooling or "/" in tooling or "\\" in tooling:
        raise ValueError("plugin id cannot produce a safe tooling name")


def _kind_from_manifest(root: Path) -> PluginKind:
    try:
        raw = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"invalid plugin.toml: {error}") from error
    backend = isinstance(raw.get("backend"), dict)
    client = isinstance(raw.get("client"), dict)
    if backend and client:
        return PluginKind.FULL_STACK
    if backend:
        return PluginKind.BACKEND
    if client:
        return PluginKind.CLIENT
    raise ValueError("plugin requires a backend or client contribution")


def _validate_source_tree(root: Path, kind: PluginKind, *, require_artifacts: bool) -> None:
    required = {".gitignore", "README.md", "plugin.toml"}
    if kind in (PluginKind.BACKEND, PluginKind.FULL_STACK):
        required.update({"pyproject.toml", "backend/plugin.py", "tests/test_backend.py"})
    if kind in (PluginKind.CLIENT, PluginKind.FULL_STACK):
        required.update(
            {
                "frontend/package.json",
                "frontend/pnpm-lock.yaml",
                "frontend/tsconfig.json",
                "frontend/src/plugin.ts",
                "frontend/tests/plugin.test.ts",
            }
        )
        bundle = root / "frontend/dist/client.js"
        if require_artifacts and not bundle.is_file():
            raise ValueError("missing frontend/dist/client.js; run 'pnpm --dir frontend run build'")
    if kind is PluginKind.FULL_STACK:
        required.add("protocol/api.schema.json")
    missing = sorted(relative for relative in required if not (root / relative).is_file())
    if missing:
        raise ValueError(f"missing template file(s): {', '.join(missing)}")


def _rename_exclusive(source: Path, destination: Path) -> None:
    system = platform.system()
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if system == "Darwin" and hasattr(libc, "renamex_np"):
        result = libc.renamex_np(source_bytes, destination_bytes, 0x00000004)
    elif system == "Linux" and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, source_bytes, -100, destination_bytes, 1)
    else:
        if os.path.lexists(destination):
            raise FileExistsError(f"destination already exists: {destination}")
        os.rename(source, destination)
        return
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == 17:
        raise FileExistsError(f"destination already exists: {destination}")
    raise OSError(error, os.strerror(error), destination)


def _render(kind: PluginKind, plugin_id: str, version: str) -> MappingProxyType[str, str]:
    files: dict[str, str] = {
        ".gitignore": _GITIGNORE,
        "README.md": _readme(kind),
        "plugin.toml": _manifest(kind, plugin_id, version),
    }
    if kind in (PluginKind.BACKEND, PluginKind.FULL_STACK):
        files["pyproject.toml"] = _python_project(plugin_id)
        files["backend/plugin.py"] = (
            _FULL_STACK_BACKEND if kind is PluginKind.FULL_STACK else _BACKEND
        )
        files["tests/test_backend.py"] = (
            _FULL_STACK_BACKEND_TEST if kind is PluginKind.FULL_STACK else _BACKEND_TEST
        )
    if kind in (PluginKind.CLIENT, PluginKind.FULL_STACK):
        files["frontend/package.json"] = _frontend_package(plugin_id)
        files["frontend/pnpm-lock.yaml"] = _frontend_lock()
        files["frontend/tsconfig.json"] = _TSCONFIG
        files["frontend/src/plugin.ts"] = (
            _FULL_STACK_CLIENT if kind is PluginKind.FULL_STACK else _CLIENT
        )
        files["frontend/tests/plugin.test.ts"] = (
            _FULL_STACK_CLIENT_TEST if kind is PluginKind.FULL_STACK else _CLIENT_TEST
        )
    if kind is PluginKind.FULL_STACK:
        files["protocol/api.schema.json"] = _PROTOCOL_SCHEMA
    return MappingProxyType(files)


def _manifest(kind: PluginKind, plugin_id: str, version: str) -> str:
    lines = [
        "[plugin]",
        f'id = "{plugin_id}"',
        f'version = "{version}"',
        f'runtime_api = "{RUNTIME_API}"',
    ]
    if kind in (PluginKind.BACKEND, PluginKind.FULL_STACK):
        lines.extend(["", "[backend]", 'entrypoint = "backend/plugin.py:plugin"'])
    if kind in (PluginKind.CLIENT, PluginKind.FULL_STACK):
        lines.extend(
            [
                "",
                "[client]",
                'bundle = "frontend/dist/client.js"',
                'platform = "web"',
            ]
        )
    if kind is PluginKind.FULL_STACK:
        lines.extend(["", "[protocol]", 'schema = "protocol/api.schema.json"'])
    lines.append("")
    lines.append("[activation]")
    if kind in (PluginKind.BACKEND, PluginKind.FULL_STACK):
        lines.append('backend = "required"')
    if kind in (PluginKind.CLIENT, PluginKind.FULL_STACK):
        lines.append('client = "required"')
    return "\n".join(lines) + "\n"


def _python_project(plugin_id: str) -> str:
    name = plugin_id.replace(".", "-")
    return f'''[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}-backend"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["deepseek-harness-python=={PYTHON_SDK_VERSION}"]
'''


def _frontend_package(plugin_id: str) -> str:
    name = plugin_id.replace(".", "-")
    return f'''{{
  "name": "{name}-client",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {{
    "build": "esbuild src/plugin.ts --bundle --format=esm --platform=browser --outfile=dist/client.js",
    "test": "vitest run",
    "typecheck": "tsc --noEmit"
  }},
  "dependencies": {{
    "{BROWSER_SDK_PACKAGE}": "{BROWSER_SDK_VERSION}"
  }},
  "devDependencies": {{
    "esbuild": "^0.25.0",
    "typescript": "^5.9.3",
    "vitest": "^3.2.4"
  }}
}}
'''


def _frontend_lock() -> str:
    return f"""lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      '{BROWSER_SDK_PACKAGE}':
        specifier: {BROWSER_SDK_VERSION}
        version: {BROWSER_SDK_VERSION}
    devDependencies:
      esbuild:
        specifier: ^0.25.0
        version: 0.25.12
      typescript:
        specifier: ^5.9.3
        version: 5.9.3
      vitest:
        specifier: ^3.2.4
        version: 3.2.7
"""


def _readme(kind: PluginKind) -> str:
    frontend = kind in (PluginKind.CLIENT, PluginKind.FULL_STACK)
    backend = kind in (PluginKind.BACKEND, PluginKind.FULL_STACK)
    commands: list[str] = []
    if backend:
        commands.append("python -m unittest discover -s tests -v")
    if frontend:
        commands.extend(
            [
                "pnpm --dir frontend install --frozen-lockfile",
                "pnpm --dir frontend run typecheck",
                "pnpm --dir frontend run test",
                "pnpm --dir frontend run build",
            ]
        )
    commands.extend(
        [
            "deepseek-harness-plugin validate .",
            "deepseek-harness-python --plugins .. --browser-runtime /path/to/browser.js",
        ]
    )
    return "# DeepSeek Harness Plugin\n\n" + "```sh\n" + "\n".join(commands) + "\n```\n"


_GITIGNORE = """__pycache__/
.venv/
frontend/node_modules/
frontend/dist/
"""

_BACKEND = """from harness.cordis import ServiceKey
from harness.sdk import define_backend_plugin

EXAMPLE_STATE = ServiceKey[str]("example.state")


async def setup(ctx):
    await ctx.cordis.provide(EXAMPLE_STATE, "active")


plugin = define_backend_plugin(setup)
"""

_BACKEND_TEST = """import unittest

from harness.sdk.testing import BackendPluginHarness

from backend.plugin import EXAMPLE_STATE, plugin


class BackendPluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle(self):
        harness = BackendPluginHarness(
            plugin,
            plugin_id="com.example.fixture",
            revision="fixture-revision",
        )
        await harness.start()
        self.assertEqual(harness.runtime.root.lookup(EXAMPLE_STATE), "active")
        await harness.dispose()
        self.assertIsNone(harness.runtime.root.lookup(EXAMPLE_STATE))


if __name__ == "__main__":
    unittest.main()
"""

_FULL_STACK_BACKEND = """from collections.abc import Mapping

from harness.sdk import (
    ClientEvent,
    JsonValue,
    RpcMethod,
    ServerEvent,
    client_event,
    define_bridge_backend_plugin,
    rpc_method,
    server_event,
)

DESCRIBE: RpcMethod[Mapping[str, JsonValue], JsonValue] = rpc_method("describe")
CHANGED: ClientEvent[JsonValue] = client_event("changed")
RENDER: ServerEvent[JsonValue] = server_event("render")


async def setup(ctx):
    async def describe(arguments):
        return {"value": arguments.get("value")}

    async def changed(page_id, payload):
        await ctx.channel.emit_client_event(RENDER, payload, page_id=page_id)

    await ctx.channel.register_rpc(DESCRIBE, describe)
    await ctx.channel.on_client_event(CHANGED, changed)


plugin = define_bridge_backend_plugin(setup)
"""

_FULL_STACK_BACKEND_TEST = """import unittest

from harness.sdk.testing import FullStackPluginHarness

from backend.plugin import CHANGED, DESCRIBE, plugin


class BackendPluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpc_and_events(self):
        harness = FullStackPluginHarness(
            plugin,
            plugin_id="com.example.fixture",
            revision="fixture-revision",
        )
        await harness.start()
        result = await harness.call_rpc(DESCRIBE, {"value": "hello"})
        self.assertEqual(result.result, {"value": "hello"})
        await harness.send_client_event(CHANGED, {"value": "event"})
        self.assertEqual(harness.emitted_events[-1].payload, {"value": "event"})
        await harness.dispose()
        harness.assert_no_registrations()


if __name__ == "__main__":
    unittest.main()
"""

_CLIENT = """import { defineClientPlugin } from '@deepseek-harness/browser-bridge-client'

export const createPlugin = defineClientPlugin((ctx) => {
  const state = globalThis as typeof globalThis & { __examplePlugin?: string }
  ctx.effect(() => {
    state.__examplePlugin = `active:${ctx.pluginId}`
    return () => { delete state.__examplePlugin }
  })
})
"""

_CLIENT_TEST = """import { describe, expect, it } from 'vitest'
import { createClientPluginHarness } from '@deepseek-harness/browser-bridge-client/testing'

import { createPlugin } from '../src/plugin.js'

describe('client plugin', () => {
  it('mounts and disposes', async () => {
    const harness = await createClientPluginHarness(createPlugin)
    await harness.dispose()
    expect(harness.activeListenerCount).toBe(0)
  })
})
"""

_FULL_STACK_CLIENT = """import {
  clientEvent,
  defineClientPlugin,
  rpcMethod,
  serverEvent,
} from '@deepseek-harness/browser-bridge-client'

const describe = rpcMethod<{ value: string }, { value: string }>('describe')
const changed = clientEvent<{ value: string }>('changed')
const render = serverEvent<{ value: string }>('render')

export const createPlugin = defineClientPlugin((ctx) => {
  const state = globalThis as typeof globalThis & { __examplePlugin?: string }
  state.__examplePlugin = 'mounting'
  ctx.on(render, (payload) => {
    state.__examplePlugin = payload.value
  })
  ctx.effect(() => {
    const timer = setTimeout(async () => {
      const result = await ctx.call(describe, { value: 'ready' })
      state.__examplePlugin = result.value
      ctx.emit(changed, { value: 'mounted' })
    }, 0)
    return () => clearTimeout(timer)
  })
  return () => { delete state.__examplePlugin }
})
"""

_FULL_STACK_CLIENT_TEST = """import { describe, expect, it } from 'vitest'
import { createClientPluginHarness } from '@deepseek-harness/browser-bridge-client/testing'

import { createPlugin } from '../src/plugin.js'

describe('full-stack client plugin', () => {
  it('uses the revision-bound channel', async () => {
    const harness = await createClientPluginHarness(createPlugin, {
      call: (_method, arguments_) => arguments_,
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(harness.calls[0]?.method).toBe('describe')
    expect(harness.emitted[0]?.name).toBe('changed')
    await harness.dispose()
  })
})
"""

_TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "lib": ["ES2022", "DOM"],
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts", "tests/**/*.ts"]
}
"""

_PROTOCOL_SCHEMA = """{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Plugin protocol",
  "type": "object",
  "additionalProperties": true
}
"""
