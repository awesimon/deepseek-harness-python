"""Serialized local control operations over the Dynamic Plugin Manager."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from harness.cordis import Context, PluginSpec, ServiceKey
from harness.plugins import PLUGIN_MANAGER, PluginManager, PluginSnapshot, PluginState


class PluginControlError(RuntimeError):
    """Base class for rejected control-plane intent."""


class PluginControlClosedError(PluginControlError):
    """Raised when shutdown rejects new control operations."""


class PluginControlConflictError(PluginControlError):
    """Raised when optimistic mutation preconditions are stale."""

    def __init__(self, message: str, current: ControlPluginSnapshot | None = None) -> None:
        super().__init__(message)
        self.current = current


class UnsafePluginRootError(PluginControlError):
    """Raised when a requested root is outside trusted immediate children."""


@dataclass(frozen=True, slots=True)
class WatcherDiagnostic:
    """Latest structured filesystem-watcher failure."""

    code: str
    message: str
    path: str | None = None
    plugin_id: str | None = None
    operation_id: str | None = None
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class WatcherSnapshot:
    """Current watcher activity included in control inventory."""

    enabled: bool
    catalogs: tuple[str, ...]
    create_policy: str | None = None
    delete_policy: str | None = None
    debounce_seconds: float | None = None
    pending_roots: tuple[str, ...] = ()
    dispatched_root: str | None = None
    diagnostic: WatcherDiagnostic | None = None


@dataclass(frozen=True, slots=True)
class ControlPluginSnapshot:
    """Manager-derived plugin state plus one optimistic mutation token."""

    plugin: PluginSnapshot
    mutation_version: int


@dataclass(frozen=True, slots=True)
class ControlInventorySnapshot:
    """One atomic ordered observation of control and watcher state."""

    inventory_version: int
    plugins: tuple[ControlPluginSnapshot, ...]
    watcher: WatcherSnapshot


@dataclass(frozen=True, slots=True)
class ControlOperation:
    """Accepted control mutation and its post-operation state."""

    operation_id: str
    outcome: Literal["succeeded", "failed"]
    snapshot: ControlPluginSnapshot


@dataclass(frozen=True, slots=True)
class ControlTombstone:
    """Final identity returned after an accepted uninstall."""

    operation_id: str
    outcome: Literal["succeeded"]
    plugin_id: str
    revision: str
    mutation_version: int


@dataclass(frozen=True, slots=True)
class PluginControlConfig:
    """Trusted catalog roots available to one control service."""

    catalogs: tuple[Path, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalogs", tuple(Path(path) for path in self.catalogs))


PLUGIN_CONTROL = ServiceKey["PluginControlService"]("plugins.control")


class PluginControlService:
    """Serialize operator and watcher intent against Manager-owned state."""

    def __init__(self, manager: PluginManager, catalogs: tuple[Path, ...]) -> None:
        self.manager = manager
        self.catalogs = catalogs
        self._lock = asyncio.Lock()
        self._versions: dict[str, int] = {}
        self._inventory_version = 0
        self._closing = False
        self._watcher = WatcherSnapshot(False, tuple(str(path) for path in catalogs))

    def inventory(self) -> ControlInventorySnapshot:
        """Return one ordered observation outside an active mutation."""
        snapshots = self.manager.snapshot()
        return ControlInventorySnapshot(
            self._inventory_version,
            tuple(self._snapshot(snapshots[plugin_id]) for plugin_id in snapshots),
            self._watcher,
        )

    def get(self, plugin_id: str) -> ControlPluginSnapshot:
        """Return one current plugin observation."""
        try:
            snapshot = self.manager.snapshot()[plugin_id]
        except KeyError as error:
            raise LookupError(f"plugin {plugin_id!r} is not installed") from error
        return self._snapshot(snapshot)

    async def install(
        self,
        plugin_root: str | Path,
        *,
        expected_absent: bool,
        enable: bool = False,
        source: str = "http",
    ) -> ControlOperation:
        """Install one trusted immediate child and optionally enable it."""
        del source
        root = self.resolve_plugin_root(plugin_root, strict=True)
        async with self._lock:
            self._assert_open()
            operation_id = _operation_id()
            plugin_id = _manifest_plugin_id(root)
            existing = self.manager.snapshot().get(plugin_id)
            if not expected_absent or existing is not None:
                current = None if existing is None else self._snapshot(existing)
                raise PluginControlConflictError(
                    f"plugin {plugin_id!r} is already installed or expectedAbsent is false",
                    current,
                )
            before = None
            installed = await self.manager.install(root)
            after = await self.manager.enable(plugin_id) if enable else installed
            self._record_change(plugin_id, before, after)
            return ControlOperation(operation_id, _outcome(after), self._snapshot(after))

    async def enable(
        self,
        plugin_id: str,
        *,
        expected_revision: str,
        expected_mutation_version: int,
    ) -> ControlOperation:
        """Enable one exact observed plugin revision."""
        return await self._mutate(
            plugin_id,
            expected_revision,
            expected_mutation_version,
            self.manager.enable,
        )

    async def disable(
        self,
        plugin_id: str,
        *,
        expected_revision: str,
        expected_mutation_version: int,
    ) -> ControlOperation:
        """Disable one exact observed plugin revision."""
        return await self._mutate(
            plugin_id,
            expected_revision,
            expected_mutation_version,
            self.manager.disable,
        )

    async def update(
        self,
        plugin_id: str,
        *,
        expected_revision: str,
        expected_mutation_version: int,
    ) -> ControlOperation:
        """Update from the installed root after checking caller preconditions."""

        async def operation(_plugin_id: str) -> PluginSnapshot:
            return await self.manager.update(self.manager.snapshot()[plugin_id].root)

        return await self._mutate(
            plugin_id,
            expected_revision,
            expected_mutation_version,
            operation,
        )

    async def rollback(
        self,
        plugin_id: str,
        *,
        expected_revision: str,
        expected_mutation_version: int,
        target_revision: str,
    ) -> ControlOperation:
        """Activate the exact retained revision selected by the caller."""
        async with self._lock:
            self._assert_open()
            before = self._checked(plugin_id, expected_revision, expected_mutation_version)
            if before.plugin.previous_revision != target_revision:
                raise PluginControlConflictError(
                    "targetRevision does not match the retained previous Revision",
                    before,
                )
            operation_id = _operation_id()
            after = await self.manager.rollback(plugin_id)
            self._record_change(plugin_id, before.plugin, after)
            return ControlOperation(operation_id, _outcome(after), self._snapshot(after))

    async def uninstall(
        self,
        plugin_id: str,
        *,
        expected_revision: str,
        expected_mutation_version: int,
    ) -> ControlTombstone:
        """Remove one exact disabled record and return its final token."""
        async with self._lock:
            self._assert_open()
            before = self._checked(plugin_id, expected_revision, expected_mutation_version)
            operation_id = _operation_id()
            await self.manager.uninstall(plugin_id)
            final_version = before.mutation_version + 1
            self._versions.pop(plugin_id, None)
            self._inventory_version += 1
            return ControlTombstone(
                operation_id,
                "succeeded",
                plugin_id,
                before.plugin.revision,
                final_version,
            )

    async def startup_install_enable(self, plugin_root: str | Path) -> ControlOperation:
        """Install and enable one startup candidate through the shared coordinator."""
        return await self.install(
            plugin_root,
            expected_absent=True,
            enable=True,
            source="startup",
        )

    async def watcher_reconcile(self, plugin_root: Path, *, enable_new: bool) -> None:
        """Apply one fresh watcher observation without stale HTTP preconditions."""
        root = self.resolve_plugin_root(plugin_root, strict=True)
        async with self._lock:
            self._assert_open()
            plugin_id = _manifest_plugin_id(root)
            current = self.manager.snapshot().get(plugin_id)
            if current is None:
                installed = await self.manager.install(root)
                after = await self.manager.enable(plugin_id) if enable_new else installed
                self._record_change(plugin_id, None, after)
                return
            if current.root != root:
                raise UnsafePluginRootError(
                    f"plugin {plugin_id!r} is installed from another trusted root"
                )
            after = await self.manager.update(root)
            self._record_change(plugin_id, current, after)

    async def watcher_remove(self, root: Path, *, uninstall: bool) -> None:
        """Apply a configured delete policy for one installed root."""
        resolved = self.resolve_plugin_root(root, strict=False)
        async with self._lock:
            self._assert_open()
            current = next(
                (
                    item
                    for item in self.manager.snapshot().values()
                    if item.root == resolved
                ),
                None,
            )
            if current is None:
                return
            disabled = await self.manager.disable(current.plugin_id)
            self._record_change(current.plugin_id, current, disabled)
            if uninstall and disabled.diagnostic is None:
                await self.manager.uninstall(current.plugin_id)
                self._versions.pop(current.plugin_id, None)
                self._inventory_version += 1

    def resolve_plugin_root(self, value: str | Path, *, strict: bool) -> Path:
        """Resolve one immediate catalog child without allowing symlink escape."""
        candidate = Path(value)
        try:
            resolved = candidate.resolve(strict=strict)
        except OSError as error:
            raise UnsafePluginRootError(f"cannot resolve plugin root {candidate}: {error}") from error
        if not any(resolved.parent == catalog for catalog in self.catalogs):
            raise UnsafePluginRootError(
                f"plugin root must be an immediate child of a trusted catalog: {resolved}"
            )
        if strict and not resolved.is_dir():
            raise UnsafePluginRootError(f"plugin root is not a directory: {resolved}")
        return resolved

    def update_watcher(self, snapshot: WatcherSnapshot) -> None:
        """Replace watcher status from its single lifecycle owner."""
        self._watcher = snapshot

    async def close(self) -> None:
        """Reject later work and join the active serialized mutation."""
        self.begin_close()
        async with self._lock:
            return

    def begin_close(self) -> None:
        """Reject newly dispatched intent before asynchronous teardown joins."""
        self._closing = True

    async def _mutate(
        self,
        plugin_id: str,
        expected_revision: str,
        expected_mutation_version: int,
        operation: Callable[[str], Awaitable[PluginSnapshot]],
    ) -> ControlOperation:
        async with self._lock:
            self._assert_open()
            before = self._checked(plugin_id, expected_revision, expected_mutation_version)
            operation_id = _operation_id()
            after = await operation(plugin_id)
            self._record_change(plugin_id, before.plugin, after)
            return ControlOperation(operation_id, _outcome(after), self._snapshot(after))

    def _checked(
        self,
        plugin_id: str,
        expected_revision: str,
        expected_mutation_version: int,
    ) -> ControlPluginSnapshot:
        current = self.get(plugin_id)
        if (
            current.plugin.revision != expected_revision
            or current.mutation_version != expected_mutation_version
        ):
            raise PluginControlConflictError(
                "plugin mutation preconditions are stale",
                current,
            )
        return current

    def _record_change(
        self,
        plugin_id: str,
        before: PluginSnapshot | None,
        after: PluginSnapshot,
    ) -> None:
        if before is not None and _mutation_state(before) == _mutation_state(after):
            return
        self._versions[plugin_id] = self._versions.get(plugin_id, 0) + 1
        self._inventory_version += 1

    def _snapshot(self, snapshot: PluginSnapshot) -> ControlPluginSnapshot:
        return ControlPluginSnapshot(snapshot, self._versions.get(snapshot.plugin_id, 0))

    def _assert_open(self) -> None:
        if self._closing:
            raise PluginControlClosedError("Plugin Control Plane is closing")


def plugin_control_plugin() -> PluginSpec[PluginControlConfig]:
    """Return the provider for serialized local plugin control."""

    async def apply(context: Context, config: PluginControlConfig) -> None:
        manager = context.require(PLUGIN_MANAGER)
        service = PluginControlService(manager, config.catalogs)
        await context.effect(lambda: service.close, "plugin-control-lifecycle")
        await context.provide(PLUGIN_CONTROL, service)

    return PluginSpec(
        "plugin-control",
        apply,
        requires=(PLUGIN_MANAGER,),
    )


def _manifest_plugin_id(root: Path) -> str:
    from harness.plugins import load_manifest

    return load_manifest(root).manifest.plugin_id


def _mutation_state(snapshot: PluginSnapshot) -> tuple[object, ...]:
    return (
        snapshot.version,
        snapshot.revision,
        snapshot.previous_revision,
        snapshot.root,
        snapshot.desired_enabled,
        snapshot.state,
        snapshot.backend_module,
        snapshot.client_revision,
        snapshot.diagnostic,
    )


def _outcome(snapshot: PluginSnapshot) -> Literal["succeeded", "failed"]:
    return "failed" if snapshot.state is PluginState.FAILED else "succeeded"


def _operation_id() -> str:
    return str(uuid.uuid4())
