"""Serialized aggregate lifecycle for backend and client plugin contributions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from harness.cordis import Context

from .client_activation import (
    ClientActivationAggregator,
    ClientActivationSnapshot,
    ClientActivationState,
    ClientQuorum,
)
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
    WAITING = "waiting"
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
    client_activation: ClientActivationSnapshot
    diagnostic: PluginDiagnostic | None


@dataclass(slots=True)
class _PluginRecord:
    revision: PluginRevision
    previous: PluginRevision | None = None
    desired_enabled: bool = False
    state: PluginState = PluginState.DISABLED
    backend: BackendActivation | None = None
    client: ClientPublication | None = None
    client_activation: ClientActivationSnapshot | None = None
    local_state: PluginState = PluginState.ACTIVE
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
        self._client_aggregator: ClientActivationAggregator | None = None
        self._default_client_quorum = ClientQuorum.ALL_CONNECTED
        self._client_quorums: dict[str, ClientQuorum] = {}

    def attach_client_aggregator(
        self,
        aggregator: ClientActivationAggregator,
    ) -> Callable[[], None]:
        """Attach the single Bridge-backed client readiness provider."""
        if self._client_aggregator is not None and self._client_aggregator is not aggregator:
            raise RuntimeError("a client activation aggregator is already attached")
        self._client_aggregator = aggregator
        for plugin_id, record in self._records.items():
            aggregator.configure(
                plugin_id,
                record.revision.manifest.client_policy,
                self._quorum(plugin_id),
            )
            if record.client is not None:
                aggregator.publish(plugin_id, record.client.revision)

        def dispose() -> None:
            if self._client_aggregator is aggregator:
                self._client_aggregator = None

        return dispose

    def configure_client_quorums(
        self,
        default: ClientQuorum,
        overrides: Mapping[str, ClientQuorum],
    ) -> None:
        """Validate deployment quorum choices against installed client plugins."""
        for plugin_id in overrides:
            record = self._records.get(plugin_id)
            if record is None:
                raise ValueError(f"client quorum override names unknown plugin {plugin_id!r}")
            if record.revision.manifest.client is None:
                raise ValueError(f"client quorum override names backend-only plugin {plugin_id!r}")
        self._default_client_quorum = default
        self._client_quorums = dict(overrides)
        if self._client_aggregator is not None:
            for plugin_id, record in self._records.items():
                self._client_aggregator.configure(
                    plugin_id,
                    record.revision.manifest.client_policy,
                    self._quorum(plugin_id),
                )

    def report_client_activation(self, snapshot: ClientActivationSnapshot) -> bool:
        """Accept one current Manager-qualified aggregate readiness report."""
        record = self._records.get(snapshot.plugin_id)
        if (
            record is None
            or snapshot.activation_policy is not record.revision.manifest.client_policy
        ):
            return False
        if record.revision.manifest.client is None:
            if snapshot.state is not ClientActivationState.NOT_APPLICABLE:
                return False
        elif record.desired_enabled:
            if record.client is None or snapshot.revision != record.client.revision:
                return False
            if snapshot.state in (
                ClientActivationState.DRAINING,
                ClientActivationState.NOT_PUBLISHED,
            ):
                return False
        else:
            if snapshot.state not in (
                ClientActivationState.DRAINING,
                ClientActivationState.NOT_PUBLISHED,
            ):
                return False
            if snapshot.revision not in (None, record.revision.digest):
                return False
        record.client_activation = snapshot
        if record.desired_enabled and record.client is not None:
            self._apply_client_state(record)
        return True

    def discover(self, directory: str | Path) -> tuple[PluginRevision | PluginDiagnostic, ...]:
        """Validate immediate plugin children in stable directory order."""
        root = Path(directory)
        results: list[PluginRevision | PluginDiagnostic] = []
        for child in sorted(
            (item for item in root.iterdir() if item.is_dir()), key=lambda p: p.name
        ):
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
            record = _PluginRecord(revision)
            self._records[revision.manifest.plugin_id] = record
            if self._client_aggregator is not None:
                self._client_aggregator.configure(
                    revision.manifest.plugin_id,
                    revision.manifest.client_policy,
                    self._quorum(revision.manifest.plugin_id),
                )
            return self._snapshot(record)

    async def enable(self, plugin_id: str) -> PluginSnapshot:
        """Idempotently activate the installed revision."""
        async with self._lock:
            record = self._record(plugin_id)
            if record.desired_enabled and record.local_state is not PluginState.FAILED:
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
            self._configure_client_target(record)
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
            self._configure_client_target(record)
            return await self._enable(record)

    async def uninstall(self, plugin_id: str) -> None:
        """Remove one disabled plugin from inventory."""
        async with self._lock:
            record = self._record(plugin_id)
            if record.state is not PluginState.DISABLED:
                raise RuntimeError("disable a plugin before uninstalling it")
            del self._records[plugin_id]
            if self._client_aggregator is not None:
                self._client_aggregator.remove(plugin_id)

    def snapshot(self) -> Mapping[str, PluginSnapshot]:
        """Return immutable snapshots ordered by Plugin ID."""
        return MappingProxyType(
            {key: self._snapshot(self._records[key]) for key in sorted(self._records)}
        )

    async def _enable(self, record: _PluginRecord) -> PluginSnapshot:
        record.desired_enabled = True
        record.state = PluginState.STARTING
        record.local_state = PluginState.ACTIVE
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
                    protocol_schema=record.revision.protocol_schema,
                    activation_policy=manifest.client_policy.value,
                )
            except BaseException as error:  # noqa: BLE001 -- contribution policy owns rollback
                failures.append(("client", error, manifest.client_policy))

        required = next(
            (failure for failure in failures if failure[2] is ActivationPolicy.REQUIRED),
            None,
        )
        if required is not None:
            await self._remove_contributions(record)
            record.local_state = PluginState.FAILED
            record.state = PluginState.FAILED
            record.diagnostic = PluginDiagnostic(required[0], str(required[1]))
        elif failures:
            first = failures[0]
            record.local_state = PluginState.DEGRADED
            record.state = PluginState.DEGRADED
            record.diagnostic = PluginDiagnostic(first[0], str(first[1]))
        else:
            record.local_state = PluginState.ACTIVE
            record.state = PluginState.ACTIVE
        if record.client is not None and self._client_aggregator is not None:
            self._client_aggregator.publish(
                manifest.plugin_id,
                record.client.revision,
            )
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
            publication = record.client
            try:
                publication.dispose()
            except BaseException as error:  # noqa: BLE001 -- backend cleanup must still run
                errors.append(error)
            record.client = None
            if self._client_aggregator is not None:
                self._client_aggregator.withdraw(
                    record.revision.manifest.plugin_id,
                    publication.revision,
                )
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
        client_activation = record.client_activation
        if client_activation is None:
            state = (
                ClientActivationState.NOT_APPLICABLE
                if revision.manifest.client is None
                else ClientActivationState.NOT_PUBLISHED
            )
            client_activation = ClientActivationSnapshot(
                revision.manifest.plugin_id,
                None,
                revision.manifest.client_policy,
                self._quorum(revision.manifest.plugin_id),
                state,
                0,
                0,
                0,
                0,
            )
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
            client_activation,
            record.diagnostic,
        )

    def _apply_client_state(self, record: _PluginRecord) -> None:
        snapshot = record.client_activation
        policy = record.revision.manifest.client_policy
        if snapshot is None or policy is None:
            record.state = record.local_state
            return
        if policy is ActivationPolicy.REQUIRED:
            if snapshot.state in (
                ClientActivationState.UNOBSERVED,
                ClientActivationState.RECONCILING,
            ):
                record.state = PluginState.WAITING
            elif snapshot.state is ClientActivationState.ACTIVE:
                record.state = record.local_state
            elif snapshot.state is ClientActivationState.DEGRADED:
                record.state = PluginState.DEGRADED
            elif snapshot.state in (
                ClientActivationState.FAILED,
                ClientActivationState.NOT_PUBLISHED,
            ):
                record.state = PluginState.FAILED
        elif snapshot.state in (
            ClientActivationState.DEGRADED,
            ClientActivationState.FAILED,
        ):
            record.state = PluginState.DEGRADED
        else:
            record.state = record.local_state

    def _quorum(self, plugin_id: str) -> ClientQuorum:
        return self._client_quorums.get(plugin_id, self._default_client_quorum)

    def _configure_client_target(self, record: _PluginRecord) -> None:
        if self._client_aggregator is None:
            record.client_activation = None
            return
        self._client_aggregator.configure(
            record.revision.manifest.plugin_id,
            record.revision.manifest.client_policy,
            self._quorum(record.revision.manifest.plugin_id),
        )
