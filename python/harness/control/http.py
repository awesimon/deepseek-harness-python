"""Loopback HTTP adapter for the Plugin Control Plane."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from typing import cast

from aiohttp import web

from harness.plugins import ClientActivationSnapshot, ClientPageDiagnostic

from .service import (
    ControlInventorySnapshot,
    ControlOperation,
    ControlPluginSnapshot,
    ControlTombstone,
    PluginControlClosedError,
    PluginControlConflictError,
    PluginControlService,
    UnsafePluginRootError,
    WatcherDiagnostic,
    WatcherSnapshot,
)


class PluginControlHttpAdapter:
    """Expose strict JSON resources over one trusted loopback listener."""

    def __init__(
        self,
        control: PluginControlService,
        origin: Callable[[], str],
    ) -> None:
        self.control = control
        self.origin = origin

    def register(self, app: web.Application) -> None:
        """Register the complete versioned control resource set."""
        app.router.add_get("/api/control/v1/plugins", self._list)
        app.router.add_get("/api/control/v1/plugins/{plugin_id}", self._show)
        app.router.add_post("/api/control/v1/plugins/install", self._install)
        for action in ("enable", "disable", "update", "rollback", "uninstall"):
            app.router.add_post(
                f"/api/control/v1/plugins/{{plugin_id}}/{action}",
                getattr(self, f"_{action}"),
            )

    async def _list(self, request: web.Request) -> web.Response:
        rejected = self._origin_error(request)
        if rejected is not None:
            return rejected
        return web.json_response(_inventory_json(self.control.inventory()))

    async def _show(self, request: web.Request) -> web.Response:
        rejected = self._origin_error(request)
        if rejected is not None:
            return rejected
        try:
            snapshot = self.control.get(request.match_info["plugin_id"])
        except LookupError as error:
            return _error(404, "plugin_not_found", str(error))
        return web.json_response(_control_snapshot_json(snapshot))

    async def _install(self, request: web.Request) -> web.Response:
        payload, rejected = await self._mutation_payload(
            request,
            {"pluginRoot", "expectedAbsent"},
        )
        if rejected is not None:
            return rejected
        assert payload is not None
        root = payload.get("pluginRoot")
        expected_absent = payload.get("expectedAbsent")
        if not isinstance(root, str) or not root or expected_absent is not True:
            return _error(
                400,
                "invalid_request",
                "install requires non-empty pluginRoot and expectedAbsent true",
            )
        return await self._run(
            self.control.install(root, expected_absent=True),
        )

    async def _enable(self, request: web.Request) -> web.Response:
        return await self._standard_mutation(request, "enable")

    async def _disable(self, request: web.Request) -> web.Response:
        return await self._standard_mutation(request, "disable")

    async def _update(self, request: web.Request) -> web.Response:
        return await self._standard_mutation(request, "update")

    async def _rollback(self, request: web.Request) -> web.Response:
        payload, rejected = await self._mutation_payload(
            request,
            {"expectedRevision", "expectedMutationVersion", "targetRevision"},
        )
        if rejected is not None:
            return rejected
        assert payload is not None
        parsed = _preconditions(payload)
        target = payload.get("targetRevision")
        if isinstance(parsed, web.Response):
            return parsed
        if not isinstance(target, str) or not target:
            return _error(400, "invalid_request", "targetRevision must be non-empty")
        revision, version = parsed
        return await self._run(
            self.control.rollback(
                request.match_info["plugin_id"],
                expected_revision=revision,
                expected_mutation_version=version,
                target_revision=target,
            )
        )

    async def _uninstall(self, request: web.Request) -> web.Response:
        payload, rejected = await self._mutation_payload(
            request,
            {"expectedRevision", "expectedMutationVersion"},
        )
        if rejected is not None:
            return rejected
        assert payload is not None
        parsed = _preconditions(payload)
        if isinstance(parsed, web.Response):
            return parsed
        revision, version = parsed
        return await self._run(
            self.control.uninstall(
                request.match_info["plugin_id"],
                expected_revision=revision,
                expected_mutation_version=version,
            )
        )

    async def _standard_mutation(self, request: web.Request, action: str) -> web.Response:
        payload, rejected = await self._mutation_payload(
            request,
            {"expectedRevision", "expectedMutationVersion"},
        )
        if rejected is not None:
            return rejected
        assert payload is not None
        parsed = _preconditions(payload)
        if isinstance(parsed, web.Response):
            return parsed
        revision, version = parsed
        methods = {
            "enable": self.control.enable,
            "disable": self.control.disable,
            "update": self.control.update,
        }
        method = methods[action]
        return await self._run(
            method(
                request.match_info["plugin_id"],
                expected_revision=revision,
                expected_mutation_version=version,
            )
        )

    async def _mutation_payload(
        self,
        request: web.Request,
        fields: set[str],
    ) -> tuple[dict[str, object] | None, web.Response | None]:
        rejected = self._origin_error(request)
        if rejected is not None:
            return None, rejected
        if request.content_type != "application/json":
            return None, _error(
                415,
                "unsupported_content_type",
                "mutation requests require application/json",
            )
        try:
            payload = await request.json()
        except (TypeError, ValueError):
            return None, _error(400, "invalid_request", "request body must be JSON")
        if not isinstance(payload, Mapping):
            return None, _error(400, "invalid_request", "request body must be an object")
        raw = cast(Mapping[object, object], payload)
        if any(not isinstance(key, str) for key in raw):
            return None, _error(400, "invalid_request", "request keys must be strings")
        body = cast(Mapping[str, object], raw)
        if set(body) != fields:
            return None, _error(
                400,
                "invalid_request",
                f"request must contain exactly {', '.join(sorted(fields))}",
            )
        return dict(body), None

    async def _run(
        self,
        operation: Awaitable[ControlOperation | ControlTombstone],
    ) -> web.Response:
        try:
            result = await operation
        except PluginControlConflictError as error:
            current = None if error.current is None else _control_snapshot_json(error.current)
            return _error(409, "mutation_conflict", str(error), current=current)
        except LookupError as error:
            return _error(404, "plugin_not_found", str(error))
        except UnsafePluginRootError as error:
            return _error(400, "unsafe_plugin_root", str(error))
        except PluginControlClosedError as error:
            return _error(503, "control_closing", str(error))
        except (RuntimeError, ValueError) as error:
            return _error(409, "operation_rejected", str(error))
        except Exception:  # noqa: BLE001 -- internal details never cross the local HTTP API
            return _error(500, "control_failure", "Plugin control operation failed")
        if isinstance(result, ControlOperation):
            return web.json_response(_operation_json(result))
        assert isinstance(result, ControlTombstone)
        return web.json_response(_tombstone_json(result))

    def _origin_error(self, request: web.Request) -> web.Response | None:
        origin = request.headers.get("Origin")
        if origin is not None and origin.rstrip("/") != self.origin().rstrip("/"):
            return _error(403, "origin_rejected", "browser Origin does not match control origin")
        return None


def _preconditions(payload: Mapping[str, object]) -> tuple[str, int] | web.Response:
    revision = payload.get("expectedRevision")
    version = payload.get("expectedMutationVersion")
    if not isinstance(revision, str) or not revision:
        return _error(400, "invalid_request", "expectedRevision must be non-empty")
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        return _error(
            400,
            "invalid_request",
            "expectedMutationVersion must be a non-negative integer",
        )
    return revision, version


def _inventory_json(snapshot: ControlInventorySnapshot) -> dict[str, object]:
    return {
        "inventoryVersion": snapshot.inventory_version,
        "plugins": [_control_snapshot_json(item) for item in snapshot.plugins],
        "watcher": _watcher_json(snapshot.watcher),
    }


def _control_snapshot_json(snapshot: ControlPluginSnapshot) -> dict[str, object]:
    plugin = snapshot.plugin
    return {
        "pluginId": plugin.plugin_id,
        "version": plugin.version,
        "revision": plugin.revision,
        "previousRevision": plugin.previous_revision,
        "root": str(plugin.root),
        "desiredEnabled": plugin.desired_enabled,
        "state": plugin.state.value,
        "backendModule": plugin.backend_module,
        "clientRevision": plugin.client_revision,
        "clientActivation": _client_activation_json(plugin.client_activation),
        "diagnostic": (
            None
            if plugin.diagnostic is None
            else {
                "code": f"plugin_{plugin.diagnostic.contribution}",
                "message": plugin.diagnostic.message,
                "contribution": plugin.diagnostic.contribution,
            }
        ),
        "mutationVersion": snapshot.mutation_version,
    }


def _client_activation_json(snapshot: ClientActivationSnapshot) -> dict[str, object]:
    return {
        "pluginId": snapshot.plugin_id,
        "revision": snapshot.revision,
        "activationPolicy": (
            None if snapshot.activation_policy is None else snapshot.activation_policy.value
        ),
        "quorum": snapshot.quorum.value,
        "state": snapshot.state.value,
        "eligiblePageCount": snapshot.eligible_page_count,
        "activePageCount": snapshot.active_page_count,
        "pendingPageCount": snapshot.pending_page_count,
        "failedPageCount": snapshot.failed_page_count,
        "diagnostics": [_page_diagnostic_json(item) for item in snapshot.diagnostics],
    }


def _page_diagnostic_json(item: ClientPageDiagnostic) -> dict[str, object]:
    return {
        "code": item.error_code,
        "message": item.message,
        "pluginId": item.plugin_id,
        "targetRevision": item.target_revision,
        "pageId": item.page_id,
        "connectionGeneration": item.connection_generation,
        "operationId": item.operation_id,
        "pageState": item.page_state,
    }


def _watcher_json(snapshot: WatcherSnapshot) -> dict[str, object]:
    diagnostic = snapshot.diagnostic
    return {
        "enabled": snapshot.enabled,
        "catalogs": list(snapshot.catalogs),
        "createPolicy": snapshot.create_policy,
        "deletePolicy": snapshot.delete_policy,
        "debounceSeconds": snapshot.debounce_seconds,
        "pendingRoots": list(snapshot.pending_roots),
        "dispatchedRoot": snapshot.dispatched_root,
        "diagnostic": None if diagnostic is None else _watcher_diagnostic_json(diagnostic),
    }


def _watcher_diagnostic_json(diagnostic: WatcherDiagnostic) -> dict[str, object]:
    return asdict(diagnostic)


def _operation_json(operation: ControlOperation) -> dict[str, object]:
    return {
        "operationId": operation.operation_id,
        "outcome": operation.outcome,
        "plugin": _control_snapshot_json(operation.snapshot),
    }


def _tombstone_json(tombstone: ControlTombstone) -> dict[str, object]:
    return {
        "operationId": tombstone.operation_id,
        "outcome": tombstone.outcome,
        "tombstone": {
            "pluginId": tombstone.plugin_id,
            "revision": tombstone.revision,
            "mutationVersion": tombstone.mutation_version,
        },
    }


def _error(status: int, code: str, message: str, **extra: object) -> web.Response:
    return web.json_response({"code": code, "message": message, **extra}, status=status)
