"""Debounced filesystem intent for trusted plugin catalogs."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from watchfiles import awatch  # pyright: ignore[reportUnknownVariableType] -- upstream Event union

from harness.plugins import load_manifest

from .service import (
    PluginControlClosedError,
    PluginControlService,
    WatcherDiagnostic,
    WatcherSnapshot,
)


class WatchCreatePolicy(str, Enum):
    """Action for one newly valid immediate plugin child."""

    IGNORE = "ignore"
    INSTALL_DISABLED = "install_disabled"
    INSTALL_ENABLED = "install_enabled"


class WatchDeletePolicy(str, Enum):
    """Action after one installed plugin root remains absent."""

    IGNORE = "ignore"
    DISABLE = "disable"
    UNINSTALL = "uninstall"


@dataclass(frozen=True, slots=True)
class PluginWatcherConfig:
    """Deployment policy for trusted catalog filesystem changes."""

    debounce_seconds: float = 0.25
    create_policy: WatchCreatePolicy = WatchCreatePolicy.IGNORE
    delete_policy: WatchDeletePolicy = WatchDeletePolicy.IGNORE

    def __post_init__(self) -> None:
        if (
            isinstance(self.debounce_seconds, bool)
            or not math.isfinite(self.debounce_seconds)
            or self.debounce_seconds <= 0
        ):
            raise ValueError("watcher debounce must be positive")
        object.__setattr__(self, "create_policy", WatchCreatePolicy(self.create_policy))
        object.__setattr__(self, "delete_policy", WatchDeletePolicy(self.delete_policy))


@dataclass(slots=True)
class _RootWork:
    task: asyncio.Task[None]
    dirty: bool = False
    dispatching: bool = False


class PluginCatalogWatcher:
    """Coalesce relevant file changes into serialized Manager operations."""

    def __init__(
        self,
        control: PluginControlService,
        config: PluginWatcherConfig,
        *,
        catalogs: tuple[Path, ...] | None = None,
    ) -> None:
        self.control = control
        self.config = config
        self.catalogs = control.catalogs if catalogs is None else catalogs
        self._work: dict[Path, _RootWork] = {}
        self._runner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._closing = False
        self._diagnostic: WatcherDiagnostic | None = None
        self._dispatched: Path | None = None

    async def start(self) -> None:
        """Start one watchfiles task after Host startup validation."""
        if self._runner is not None:
            raise RuntimeError("Plugin Catalog Watcher is already started")
        self._sync_status()
        self._runner = asyncio.create_task(self._watch())

    def notify(self, plugin_root: str | Path) -> None:
        """Coalesce one already-qualified plugin root for tests and watch delivery."""
        if self._closing:
            return
        root = self.control.resolve_plugin_root(plugin_root, strict=False)
        existing = self._work.get(root)
        if existing is not None:
            existing.dirty = True
        else:
            task = asyncio.create_task(self._drive(root))
            self._work[root] = _RootWork(task)
        self._sync_status()

    async def close(self) -> None:
        """Stop events, discard pending debounce, and join dispatched mutations."""
        if self._closing:
            runner = self._runner
            if runner is not None:
                await asyncio.shield(runner)
            return
        self._closing = True
        self._stop.set()
        runner = self._runner
        if runner is not None:
            await asyncio.shield(runner)
        tasks: list[asyncio.Task[None]] = []
        for work in tuple(self._work.values()):
            tasks.append(work.task)
            if not work.dispatching:
                work.task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._work.clear()
        self._dispatched = None
        self._sync_status()

    async def _watch(self) -> None:
        try:
            async for changes in awatch(
                *self.catalogs,
                stop_event=self._stop,
                recursive=True,
            ):
                for _change, changed in changes:
                    root = self._relevant_root(Path(changed))
                    if root is not None:
                        self.notify(root)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- watcher failure becomes status
            self._diagnostic = WatcherDiagnostic(
                "watcher_backend_failed",
                f"filesystem watcher failed: {type(error).__name__}",
            )
            self._sync_status()

    def _relevant_root(self, changed: Path) -> Path | None:
        absolute = changed.absolute()
        for catalog in self.catalogs:
            try:
                relative = absolute.relative_to(catalog)
            except ValueError:
                continue
            if len(relative.parts) < 2:
                return None
            root = catalog / relative.parts[0]
            artifact = Path(*relative.parts[1:]).as_posix()
            if artifact == "plugin.toml":
                return root
            current = next(
                (
                    item
                    for item in self.control.manager.snapshot().values()
                    if item.root == root
                ),
                None,
            )
            if current is None:
                return None
            try:
                record_manifest = load_manifest(root).manifest
            except Exception:  # noqa: BLE001 -- plugin.toml events own invalid candidate reports
                return None
            observed = {"plugin.toml"}
            if record_manifest.backend is not None:
                observed.add(record_manifest.backend.path)
            if record_manifest.client is not None:
                observed.add(record_manifest.client.bundle)
            if record_manifest.protocol_schema is not None:
                observed.add(record_manifest.protocol_schema)
            return root if artifact in observed else None
        return None

    async def _drive(self, root: Path) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.debounce_seconds)
                work = self._work[root]
                work.dirty = False
                work.dispatching = True
                self._dispatched = root
                self._sync_status()
                try:
                    await self._reconcile(root)
                except PluginControlClosedError:
                    self._work.pop(root, None)
                    self._dispatched = None
                    self._sync_status()
                    return
                except Exception as error:  # noqa: BLE001 -- preserve the serving revision
                    self._diagnostic = WatcherDiagnostic(
                        "watcher_candidate_invalid",
                        str(error),
                        path=str(root),
                    )
                work.dispatching = False
                self._dispatched = None
                if not work.dirty:
                    del self._work[root]
                    self._sync_status()
                    return
                self._sync_status()
        except asyncio.CancelledError:
            self._work.pop(root, None)
            if self._dispatched == root:
                self._dispatched = None
            self._sync_status()
            raise

    async def _reconcile(self, root: Path) -> None:
        if not (root / "plugin.toml").is_file():
            if self.config.delete_policy is WatchDeletePolicy.IGNORE:
                return
            await self.control.watcher_remove(
                root,
                uninstall=self.config.delete_policy is WatchDeletePolicy.UNINSTALL,
            )
            return
        installed = any(
            item.root == root for item in self.control.manager.snapshot().values()
        )
        if not installed and self.config.create_policy is WatchCreatePolicy.IGNORE:
            return
        await self.control.watcher_reconcile(
            root,
            enable_new=self.config.create_policy is WatchCreatePolicy.INSTALL_ENABLED,
        )

    def _sync_status(self) -> None:
        self.control.update_watcher(
            WatcherSnapshot(
                not self._closing,
                tuple(str(path) for path in self.catalogs),
                self.config.create_policy.value,
                self.config.delete_policy.value,
                self.config.debounce_seconds,
                tuple(str(path) for path in sorted(self._work)),
                None if self._dispatched is None else str(self._dispatched),
                self._diagnostic,
            )
        )
