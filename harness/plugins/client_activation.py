"""Derived multi-page readiness for published client contributions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .manifest import ActivationPolicy


class ClientQuorum(str, Enum):
    """Rule used to combine active browser pages."""

    ALL_CONNECTED = "all_connected"
    ANY_CONNECTED = "any_connected"


class ClientActivationState(str, Enum):
    """Revision-qualified readiness of one client contribution."""

    NOT_APPLICABLE = "not_applicable"
    NOT_PUBLISHED = "not_published"
    UNOBSERVED = "unobserved"
    RECONCILING = "reconciling"
    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILED = "failed"
    DRAINING = "draining"


@dataclass(frozen=True, slots=True)
class ClientPageObservation:
    """One page's current Bridge facts for a plugin entry."""

    page_id: str
    connection_generation: int
    operation_id: str | None
    plugin_id: str | None
    desired_revision: str | None
    revision: str | None
    state: str | None
    error: str | None
    operation_complete: bool
    operation_error: str | None
    inventory_only: bool = False


@dataclass(frozen=True, slots=True)
class ClientPageDiagnostic:
    """Structured current state for one eligible page."""

    error_code: str | None
    plugin_id: str
    target_revision: str
    page_id: str
    connection_generation: int
    operation_id: str | None
    page_state: str
    message: str | None


@dataclass(frozen=True, slots=True)
class ClientActivationSnapshot:
    """Immutable aggregate derived from one serialized Bridge snapshot."""

    plugin_id: str
    revision: str | None
    activation_policy: ActivationPolicy | None
    quorum: ClientQuorum
    state: ClientActivationState
    eligible_page_count: int
    active_page_count: int
    pending_page_count: int
    failed_page_count: int
    diagnostics: tuple[ClientPageDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class _Target:
    policy: ActivationPolicy | None
    quorum: ClientQuorum
    published_revision: str | None = None
    withdrawn_revision: str | None = None


type ClientActivationReporter = Callable[[ClientActivationSnapshot], bool]


class ClientActivationAggregator:
    """Own derived client snapshots without owning plugin or page lifecycle."""

    def __init__(self, reporter: ClientActivationReporter) -> None:
        self._reporter = reporter
        self._targets: dict[str, _Target] = {}
        self._pages: Mapping[str, tuple[ClientPageObservation, ...]] = MappingProxyType({})
        self._snapshots: dict[str, ClientActivationSnapshot] = {}

    def configure(
        self,
        plugin_id: str,
        policy: ActivationPolicy | None,
        quorum: ClientQuorum,
    ) -> ClientActivationSnapshot:
        """Register installed contribution metadata and recompute its readiness."""
        existing = self._targets.get(plugin_id)
        target = _Target(
            policy,
            quorum,
            None if existing is None else existing.published_revision,
            None if existing is None else existing.withdrawn_revision,
        )
        self._targets[plugin_id] = target
        return self._recompute(plugin_id)

    def publish(self, plugin_id: str, revision: str) -> ClientActivationSnapshot:
        """Begin one published Revision aggregation generation."""
        target = self._target(plugin_id)
        if target.policy is None:
            raise ValueError(f"plugin {plugin_id!r} has no client contribution")
        self._targets[plugin_id] = _Target(target.policy, target.quorum, revision, None)
        return self._recompute(plugin_id)

    def withdraw(self, plugin_id: str, revision: str) -> ClientActivationSnapshot:
        """Stop serving a Revision while observing connected-page drainage."""
        target = self._target(plugin_id)
        if target.published_revision not in (None, revision):
            raise ValueError("withdrawn client revision does not match the aggregate target")
        self._targets[plugin_id] = _Target(target.policy, target.quorum, None, revision)
        return self._recompute(plugin_id)

    def remove(self, plugin_id: str) -> None:
        """Remove one uninstalled plugin from aggregation inventory."""
        self._targets.pop(plugin_id, None)
        self._snapshots.pop(plugin_id, None)

    def observe(
        self,
        pages: Mapping[str, tuple[ClientPageObservation, ...]],
    ) -> None:
        """Replace all Bridge membership facts and recompute in stable order."""
        self._pages = MappingProxyType(
            {page_id: tuple(pages[page_id]) for page_id in sorted(pages)}
        )
        for plugin_id in sorted(self._targets):
            self._recompute(plugin_id)

    def snapshot(self) -> Mapping[str, ClientActivationSnapshot]:
        """Return current aggregates ordered by Plugin ID."""
        return MappingProxyType(
            {plugin_id: self._snapshots[plugin_id] for plugin_id in sorted(self._snapshots)}
        )

    def _recompute(self, plugin_id: str) -> ClientActivationSnapshot:
        target = self._target(plugin_id)
        if target.policy is None:
            snapshot = self._empty_snapshot(
                plugin_id,
                target,
                ClientActivationState.NOT_APPLICABLE,
            )
        elif target.published_revision is not None:
            snapshot = self._published_snapshot(plugin_id, target)
        elif target.withdrawn_revision is not None:
            snapshot = self._withdrawn_snapshot(plugin_id, target)
        else:
            snapshot = self._empty_snapshot(
                plugin_id,
                target,
                ClientActivationState.NOT_PUBLISHED,
            )
        previous = self._snapshots.get(plugin_id)
        self._snapshots[plugin_id] = snapshot
        if snapshot != previous:
            self._reporter(snapshot)
        return snapshot

    def _published_snapshot(
        self,
        plugin_id: str,
        target: _Target,
    ) -> ClientActivationSnapshot:
        revision = target.published_revision
        assert revision is not None
        diagnostics: list[ClientPageDiagnostic] = []
        active = 0
        pending = 0
        failed = 0
        for page_id in sorted(self._pages):
            observation = self._observation(page_id, plugin_id)
            category, diagnostic = self._classify(plugin_id, revision, observation)
            diagnostics.append(diagnostic)
            if category == "active":
                active += 1
            elif category == "pending":
                pending += 1
            else:
                failed += 1
        eligible = len(self._pages)
        quorum_satisfied = eligible > 0 and (
            active == eligible if target.quorum is ClientQuorum.ALL_CONNECTED else active > 0
        )
        if eligible == 0:
            state = ClientActivationState.UNOBSERVED
        elif quorum_satisfied and failed == 0:
            state = ClientActivationState.ACTIVE
        elif quorum_satisfied:
            state = ClientActivationState.DEGRADED
        elif pending > 0:
            state = ClientActivationState.RECONCILING
        else:
            state = ClientActivationState.FAILED
        return ClientActivationSnapshot(
            plugin_id,
            revision,
            target.policy,
            target.quorum,
            state,
            eligible,
            active,
            pending,
            failed,
            tuple(diagnostics),
        )

    def _withdrawn_snapshot(
        self,
        plugin_id: str,
        target: _Target,
    ) -> ClientActivationSnapshot:
        revision = target.withdrawn_revision
        assert revision is not None
        draining: list[ClientPageDiagnostic] = []
        for page_id in sorted(self._pages):
            observation = self._observation(page_id, plugin_id)
            if (
                observation.revision == revision
                and observation.state in ("active", "unloading")
                and not observation.inventory_only
            ):
                draining.append(
                    ClientPageDiagnostic(
                        None,
                        plugin_id,
                        revision,
                        page_id,
                        observation.connection_generation,
                        observation.operation_id,
                        observation.state,
                        None,
                    )
                )
        if not draining:
            return self._empty_snapshot(
                plugin_id,
                target,
                ClientActivationState.NOT_PUBLISHED,
                revision=revision,
            )
        return ClientActivationSnapshot(
            plugin_id,
            revision,
            target.policy,
            target.quorum,
            ClientActivationState.DRAINING,
            len(self._pages),
            0,
            len(draining),
            0,
            tuple(draining),
        )

    def _classify(
        self,
        plugin_id: str,
        revision: str,
        observation: ClientPageObservation,
    ) -> tuple[str, ClientPageDiagnostic]:
        page_state = observation.state or "absent"
        exact = observation.revision == revision
        if exact and page_state == "active" and not observation.inventory_only:
            category = "active"
            error_code = None
            message = None
        elif exact and page_state == "failed":
            category = "failed"
            error_code = "client_activation_failed"
            message = observation.error or "client activation failed"
        elif (
            observation.desired_revision == revision
            and observation.operation_complete
            and observation.operation_error is not None
        ):
            category = "failed"
            error_code = "reconcile_operation_failed"
            message = observation.operation_error
        elif exact and page_state in ("loading", "waiting", "unloading"):
            category = "pending"
            error_code = None
            message = None
        elif observation.desired_revision == revision and observation.operation_complete:
            category = "failed"
            error_code = (
                "reconcile_operation_failed"
                if observation.operation_error is not None
                else "client_revision_absent"
            )
            message = observation.operation_error or "reconciliation left target revision absent"
        else:
            category = "pending"
            error_code = None
            message = None
        return category, ClientPageDiagnostic(
            error_code,
            plugin_id,
            revision,
            observation.page_id,
            observation.connection_generation,
            observation.operation_id,
            page_state,
            message,
        )

    def _observation(self, page_id: str, plugin_id: str) -> ClientPageObservation:
        observations = self._pages[page_id]
        for observation in observations:
            if observation.plugin_id == plugin_id:
                return observation
        first = observations[0] if observations else None
        return ClientPageObservation(
            page_id,
            0 if first is None else first.connection_generation,
            None if first is None else first.operation_id,
            plugin_id,
            None,
            None,
            None,
            None,
            False if first is None else first.operation_complete,
            None if first is None else first.operation_error,
        )

    def _empty_snapshot(
        self,
        plugin_id: str,
        target: _Target,
        state: ClientActivationState,
        *,
        revision: str | None = None,
    ) -> ClientActivationSnapshot:
        return ClientActivationSnapshot(
            plugin_id,
            revision,
            target.policy,
            target.quorum,
            state,
            0 if target.policy is None else len(self._pages),
            0,
            0,
            0,
        )

    def _target(self, plugin_id: str) -> _Target:
        try:
            return self._targets[plugin_id]
        except KeyError as error:
            raise LookupError(f"plugin {plugin_id!r} is not configured for aggregation") from error
