"""Trusted backend loading and immutable client artifact publication."""

# ClientPublication and ClientArtifactRegistry form one internal ownership pair.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import inspect
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
from typing import Protocol, cast

from deepseek_harness.cordis import Context, Fiber, FiberState, PluginSpec

from .revision import PluginRevision


class BackendActivationError(RuntimeError):
    """Raised when a backend revision cannot establish a PyCordis Fiber."""


@dataclass(slots=True)
class BackendActivation:
    """Revision-qualified module and its owned backend Fiber."""

    module_name: str
    fiber: Fiber

    async def stop(self) -> None:
        """Dispose backend contributions and remove the import lookup entry."""
        await self.fiber.dispose()
        sys.modules.pop(self.module_name, None)


class BackendHost(Protocol):
    """Backend revision execution strategy used by PluginManager."""

    async def start(
        self,
        revision: PluginRevision,
        context: Context,
    ) -> BackendActivation:
        """Load and mount one backend revision."""
        ...


class InProcessBackendHost:
    """Trusted development Host using revision-qualified Python modules."""

    async def start(
        self,
        revision: PluginRevision,
        context: Context,
    ) -> BackendActivation:
        """Execute exact revision bytes and mount the exported PluginSpec."""
        backend = revision.manifest.backend
        source = revision.backend_source
        filename = revision.backend_filename
        if backend is None or source is None or filename is None:
            raise BackendActivationError("revision has no backend contribution")
        safe_id = revision.manifest.plugin_id.replace(".", "_").replace("-", "_")
        module_name = f"_dsh_plugin_{safe_id}_{revision.digest}"
        module = ModuleType(module_name)
        module.__file__ = filename
        module.__package__ = ""
        sys.modules[module_name] = module
        try:
            code = compile(source, filename, "exec")
            exec(code, module.__dict__)  # noqa: S102 -- trusted local plugin execution is the Host's purpose
            exported = getattr(module, backend.attribute)
            spec: PluginSpec[object]
            if isinstance(exported, PluginSpec):
                spec = cast(PluginSpec[object], exported)
            elif callable(exported):
                produced = exported()
                if inspect.isawaitable(produced):
                    produced = await produced
                if not isinstance(produced, PluginSpec):
                    raise TypeError("backend factory did not return PluginSpec")
                spec = cast(PluginSpec[object], produced)
            else:
                raise TypeError("backend entrypoint is not PluginSpec or a factory")
            fiber = await context.mount(spec, None)
            if fiber.state is FiberState.FAILED:
                failure = fiber.error
                await fiber.dispose()
                raise BackendActivationError(
                    f"backend Fiber failed: {failure!r}"
                )
            return BackendActivation(module_name, fiber)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise


@dataclass(slots=True)
class ClientPublication:
    """One currently published immutable client bundle."""

    registry: ClientArtifactRegistry
    plugin_id: str
    revision: str
    _active: bool = True

    def dispose(self) -> None:
        """Remove this exact publication once."""
        if not self._active:
            return
        self._active = False
        self.registry._remove(self)


class ClientArtifactRegistry:
    """Process-local source of client bundles for the Phase 4 bridge."""

    def __init__(self) -> None:
        self._current: dict[str, ClientPublication] = {}
        self._bundles: dict[tuple[str, str], bytes] = {}

    def publish(
        self,
        plugin_id: str,
        revision: str,
        bundle: bytes,
    ) -> ClientPublication:
        """Publish one current bundle and return its exact disposer."""
        if plugin_id in self._current:
            raise RuntimeError(f"client bundle for {plugin_id!r} is already published")
        publication = ClientPublication(self, plugin_id, revision)
        self._current[plugin_id] = publication
        self._bundles[(plugin_id, revision)] = bytes(bundle)
        return publication

    def current_revision(self, plugin_id: str) -> str | None:
        """Return the published revision without implying browser activation."""
        publication = self._current.get(plugin_id)
        return None if publication is None else publication.revision

    def snapshot(self) -> Mapping[str, str]:
        """Return current Plugin ID to Revision publication state."""
        return MappingProxyType(
            {plugin_id: self._current[plugin_id].revision for plugin_id in sorted(self._current)}
        )

    def bundle_digest(self, plugin_id: str, revision: str) -> str:
        """Return the SHA-256 digest of exact published bundle bytes."""
        return hashlib.sha256(self.get(plugin_id, revision)).hexdigest()

    def get(self, plugin_id: str, revision: str) -> bytes:
        """Return exact immutable bundle bytes for one published revision."""
        try:
            return self._bundles[(plugin_id, revision)]
        except KeyError as error:
            raise LookupError(f"client revision {plugin_id!r}/{revision!r} is not published") from error

    def _remove(self, publication: ClientPublication) -> None:
        if self._current.get(publication.plugin_id) is publication:
            del self._current[publication.plugin_id]
            self._bundles.pop((publication.plugin_id, publication.revision), None)
