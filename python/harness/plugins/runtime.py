"""Trusted backend loading and immutable client artifact publication."""

# ClientPublication and ClientArtifactRegistry form one internal ownership pair.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
import inspect
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType, ModuleType
from typing import Protocol, cast

from harness.cordis import Context, EffectHandle, Fiber, FiberState, PluginSpec, ServiceKey

from .revision import PluginRevision


class BackendActivationError(RuntimeError):
    """Raised when a backend revision cannot establish a PyCordis Fiber."""


@dataclass(frozen=True, slots=True)
class PluginRuntimeIdentity:
    """Manager-authoritative identity of one active backend contribution."""

    plugin_id: str
    revision: str


PLUGIN_RUNTIME_IDENTITY = ServiceKey[PluginRuntimeIdentity]("plugins.runtime-identity")


@dataclass(slots=True)
class BackendActivation:
    """Revision-qualified module and its owned backend Fiber."""

    module_name: str
    fiber: Fiber
    identity_effect: EffectHandle

    async def stop(self) -> None:
        """Dispose backend contributions and remove the import lookup entry."""
        try:
            await self.fiber.dispose()
        finally:
            try:
                await self.identity_effect.dispose()
            finally:
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
        identity_context = context.isolate(PLUGIN_RUNTIME_IDENTITY)
        identity_effect: EffectHandle | None = None
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
            identity_effect = await identity_context.provide(
                PLUGIN_RUNTIME_IDENTITY,
                PluginRuntimeIdentity(revision.manifest.plugin_id, revision.digest),
            )
            fiber = await identity_context.mount(spec, None)
            if fiber.state is FiberState.FAILED:
                failure = fiber.error
                await fiber.dispose()
                await identity_effect.dispose()
                raise BackendActivationError(
                    f"backend Fiber failed: {failure!r}"
                )
            return BackendActivation(module_name, fiber, identity_effect)
        except BaseException:
            try:
                if identity_effect is not None:
                    await identity_effect.dispose()
            finally:
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


@dataclass(frozen=True, slots=True)
class ClientArtifact:
    """Exact browser artifacts and activation metadata for one Revision."""

    bundle: bytes
    bundle_sha256: str
    protocol_schema: bytes | None
    activation_policy: str


class ClientArtifactRegistry:
    """Process-local source of client bundles for the Phase 4 bridge."""

    def __init__(self) -> None:
        self._current: dict[str, ClientPublication] = {}
        self._artifacts: dict[tuple[str, str], ClientArtifact] = {}
        self._watchers: list[Callable[[], None]] = []

    def publish(
        self,
        plugin_id: str,
        revision: str,
        bundle: bytes,
        *,
        protocol_schema: bytes | None = None,
        activation_policy: str = "required",
    ) -> ClientPublication:
        """Publish one current bundle and return its exact disposer."""
        if plugin_id in self._current:
            raise RuntimeError(f"client bundle for {plugin_id!r} is already published")
        if activation_policy not in ("required", "optional"):
            raise ValueError("client activation policy must be required or optional")
        immutable_bundle = bytes(bundle)
        publication = ClientPublication(self, plugin_id, revision)
        self._current[plugin_id] = publication
        self._artifacts[(plugin_id, revision)] = ClientArtifact(
            immutable_bundle,
            hashlib.sha256(immutable_bundle).hexdigest(),
            None if protocol_schema is None else bytes(protocol_schema),
            activation_policy,
        )
        try:
            self._notify()
        except BaseException:
            del self._current[plugin_id]
            del self._artifacts[(plugin_id, revision)]
            raise
        return publication

    def watch(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Observe publication changes and return an idempotent disposer."""
        self._watchers.append(callback)
        active = True

        def dispose() -> None:
            nonlocal active
            if active:
                active = False
                self._watchers.remove(callback)

        return dispose

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
        return self.artifact(plugin_id, revision).bundle_sha256

    def artifact(self, plugin_id: str, revision: str) -> ClientArtifact:
        """Return exact current artifact metadata without Revision fallback."""
        if self.current_revision(plugin_id) != revision:
            raise LookupError(f"client revision {plugin_id!r}/{revision!r} is not published")
        try:
            return self._artifacts[(plugin_id, revision)]
        except KeyError as error:
            raise LookupError(
                f"client revision {plugin_id!r}/{revision!r} is not published"
            ) from error

    def get(self, plugin_id: str, revision: str) -> bytes:
        """Return exact immutable bundle bytes for one published revision."""
        return self.artifact(plugin_id, revision).bundle

    def protocol_schema(self, plugin_id: str, revision: str) -> bytes:
        """Return the optional plugin wire Schema for one exact current Revision."""
        schema = self.artifact(plugin_id, revision).protocol_schema
        if schema is None:
            raise LookupError(f"client revision {plugin_id!r}/{revision!r} has no protocol Schema")
        return schema

    def _remove(self, publication: ClientPublication) -> None:
        if self._current.get(publication.plugin_id) is publication:
            del self._current[publication.plugin_id]
            self._artifacts.pop((publication.plugin_id, publication.revision), None)
            self._notify()

    def _notify(self) -> None:
        for watcher in tuple(self._watchers):
            watcher()
