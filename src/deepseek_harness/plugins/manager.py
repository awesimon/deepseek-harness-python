"""Serialized aggregate lifecycle for backend and client plugin contributions."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from deepseek_harness.cordis import Context

from .manifest import ActivationPolicy
from .revision import PluginRevision, build_revision
from .runtime import (
    BackendActivation,
    BackendHost,
    ClientArtifactRegistry,
    ClientPublication,
    InProcessBackendHost,
)


class PluginState(str, Enum):
    """Aggregate state of one installed logical plugin."""

    DISABLED = "disabled"
    STARTING = "starting"
    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLING = "disabling"


@dataclass(frozen=True, slots=True)
class PluginDiagnostic:
    """Most recent activation or cleanup failure."""

    contribution: str
    message: str


@dataclass(frozen=True, slots=True)
class PluginSnapshot:
    """Immutable public view of one installed plugin record."""

    plugin_id: str
    version: str
    revision: str
    previous_revision: str | None
    root: Path
    desired_enabled: bool
    state: PluginState
    backend_module: str | None
    client_revision: str | None
    diagnostic: PluginDiagnostic | None


@dataclass(slots=True)
class _PluginRecord:
    revision: PluginRevision
    previous: PluginRevision | None = None
    desired_enabled: bool = False
    state: PluginState = PluginState.DISABLED
    backend: BackendActivation | None = None
    client: ClientPublication | None = None
    diagnostic: PluginDiagnostic | None = None


class PluginManager:
    """Install and coordinate trusted local plugin revisions."""

    def __init__(
        self,
        context: Context,
        *,
        backend_host: BackendHost | None = None,
        clients: ClientArtifactRegistry | None = None,
    ) -> None:
        self.context = context
        self.backend_host = backend_host or InProcessBackendHost()
        self.clients = clients or ClientArtifactRegistry()
        self._records: dict[str, _PluginRecord] = {}
        self._lock = asyncio.Lock()

    def discover(self, directory: str | Path) -> tuple[PluginRevision | PluginDiagnostic, ...]:
        """Validate immediate plugin children in stable directory order."""
        root = Path(directory)
        results: list[PluginRevision | PluginDiagnostic] = []
        for child in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda p: p.name):
            if not (child / "plugin.toml").is_file():
                continue
            try:
                results.append(build_revision(child))
            except Exception as error:  # noqa: BLE001 -- discovery returns per-candidate diagnostics
                results.append(PluginDiagnostic(str(child), str(error)))
        return tuple(results)

    async def install(self, plugin_root: str | Path) -> PluginSnapshot:
        """Validate and install one disabled revision."""
        revision = build_revision(plugin_root)
        async with self._lock:
            existing = self._records.get(revision.manifest.plugin_id)
            if existing is not None:
                if existing.state is not PluginState.DISABLED:
                    raise RuntimeError("cannot replace an enabled plugin installation")
                if existing.revision.root != revision.root:
                    raise RuntimeError(
                        f"plugin id {revision.manifest.plugin_id!r} belongs to another root"
                    )
            self._records[revision.manifest.plugin_id] = _PluginRecord(revision)
            return self._snapshot(self._records[revision.manifest.plugin_id])

    async def enable(self, plugin_id: str) -> PluginSnapshot:
        """Idempotently activate the installed revision."""
        async with self._lock:
            record = self._record(plugin_id)
            if record.state in (PluginState.ACTIVE, PluginState.DEGRADED):
                return self._snapshot(record)
            return await self._enable(record)

    async def disable(self, plugin_id: str) -> PluginSnapshot:
        """Idempotently remove every active contribution."""
        async with self._lock:
            record = self._record(plugin_id)
            await self._disable(record)
            return self._snapshot(record)

    async def update(self, plugin_root: str | Path) -> PluginSnapshot:
        """Validate a distinct candidate, stop current, and activate candidate."""
        candidate = build_revision(plugin_root)
        async with self._lock:
            record = self._record(candidate.manifest.plugin_id)
            if record.revision.root != candidate.root:
                raise RuntimeError("update root differs from installed plugin root")
            if record.revision.digest == candidate.digest:
                return self._snapshot(record)
            was_enabled = record.desired_enabled
            await self._disable(record)
            record.previous = record.revision
            record.revision = candidate
            if was_enabled:
                return await self._enable(record)
            return self._snapshot(record)

    async def rollback(self, plugin_id: str) -> PluginSnapshot:
        """Swap to the retained previous revision and explicitly activate it."""
        async with self._lock:
            record = self._record(plugin_id)
            if record.previous is None:
                raise RuntimeError(f"plugin {plugin_id!r} has no previous revision")
            await self._disable(record)
            record.revision, record.previous = record.previous, record.revision
            return await self._enable(record)

    async def uninstall(self, plugin_id: str) -> None:
        """Remove one disabled plugin from inventory."""
        async with self._lock:
            record = self._record(plugin_id)
            if record.state is not PluginState.DISABLED:
                raise RuntimeError("disable a plugin before uninstalling it")
            del self._records[plugin_id]

    def snapshot(self) -> Mapping[str, PluginSnapshot]:
        """Return immutable snapshots ordered by Plugin ID."""
        return MappingProxyType(
            {key: self._snapshot(self._records[key]) for key in sorted(self._records)}
        )

    async def _enable(self, record: _PluginRecord) -> PluginSnapshot:
        record.desired_enabled = True
        record.state = PluginState.STARTING
        record.diagnostic = None
        failures: list[tuple[str, BaseException, ActivationPolicy]] = []
        manifest = record.revision.manifest
        if manifest.backend is not None:
            assert manifest.backend_policy is not None
            try:
                record.backend = await self.backend_host.start(record.revision, self.context)
            except BaseException as error:  # noqa: BLE001 -- contribution policy owns rollback
                failures.append(("backend", error, manifest.backend_policy))
        if manifest.client is not None:
            assert manifest.client_policy is not None
            try:
                assert record.revision.client_bundle is not None
                record.client = self.clients.publish(
                    manifest.plugin_id,
                    record.revision.digest,
                    record.revision.client_bundle,
                )
            except BaseException as error:  # noqa: BLE001 -- contribution policy owns rollback
                failures.append(("client", error, manifest.client_policy))

        required = next(
            (failure for failure in failures if failure[2] is ActivationPolicy.REQUIRED),
            None,
        )
        if required is not None:
            await self._remove_contributions(record)
            record.state = PluginState.FAILED
            record.diagnostic = PluginDiagnostic(required[0], str(required[1]))
        elif failures:
            first = failures[0]
            record.state = PluginState.DEGRADED
            record.diagnostic = PluginDiagnostic(first[0], str(first[1]))
        else:
            record.state = PluginState.ACTIVE
        return self._snapshot(record)

    async def _disable(self, record: _PluginRecord) -> None:
        if record.state is PluginState.DISABLED:
            record.desired_enabled = False
            return
        record.desired_enabled = False
        record.state = PluginState.DISABLING
        errors = await self._remove_contributions(record)
        record.state = PluginState.DISABLED
        record.diagnostic = (
            None if not errors else PluginDiagnostic("cleanup", "; ".join(map(str, errors)))
        )

    async def _remove_contributions(self, record: _PluginRecord) -> list[BaseException]:
        errors: list[BaseException] = []
        if record.client is not None:
            try:
                record.client.dispose()
            except BaseException as error:  # noqa: BLE001 -- backend cleanup must still run
                errors.append(error)
            record.client = None
        if record.backend is not None:
            try:
                await record.backend.stop()
            except BaseException as error:  # noqa: BLE001 -- aggregate cleanup retains diagnostics
                errors.append(error)
            record.backend = None
        return errors

    def _record(self, plugin_id: str) -> _PluginRecord:
        try:
            return self._records[plugin_id]
        except KeyError as error:
            raise LookupError(f"plugin {plugin_id!r} is not installed") from error

    def _snapshot(self, record: _PluginRecord) -> PluginSnapshot:
        revision = record.revision
        return PluginSnapshot(
            revision.manifest.plugin_id,
            revision.manifest.version,
            revision.digest,
            None if record.previous is None else record.previous.digest,
            revision.root,
            record.desired_enabled,
            record.state,
            None if record.backend is None else record.backend.module_name,
            None if record.client is None else record.client.revision,
            record.diagnostic,
        )
