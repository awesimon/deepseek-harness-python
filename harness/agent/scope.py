"""Hierarchical Agent Scopes and reusable layered contribution storage."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TypeVar

ValueT = TypeVar("ValueT")


class DuplicateContributionError(RuntimeError):
    """Raised when one registry layer already owns a contribution name."""


@dataclass(frozen=True, slots=True, eq=False)
class AgentScope:
    """Opaque hierarchical identity used to select Agent contributions."""

    parent: AgentScope | None = None
    _identity: object = field(default_factory=object, repr=False)

    def lineage(self) -> tuple[AgentScope, ...]:
        """Return ancestors from farthest to nearest, including this Scope."""
        lineage: list[AgentScope] = []
        current: AgentScope | None = self
        seen: set[AgentScope] = set()
        while current is not None:
            if current in seen:
                raise RuntimeError("agent scope parent cycle detected")
            seen.add(current)
            lineage.append(current)
            current = current.parent
        lineage.reverse()
        return tuple(lineage)


class LayeredRegistry[ValueT]:
    """Global plus exact-Scope contributions with nearest-name precedence."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._global: dict[str, ValueT] = {}
        self._scoped: dict[AgentScope, dict[str, ValueT]] = {}

    def register(
        self,
        name: str,
        value: ValueT,
        *,
        scope: AgentScope | None = None,
    ) -> Callable[[], None]:
        """Register one exact-layer value and return an idempotent disposer."""
        if not name:
            raise ValueError(f"{self.kind} contribution name must not be empty")
        layer = self._global if scope is None else self._scoped.setdefault(scope, {})
        if name in layer:
            location = "global" if scope is None else "agent scope"
            raise DuplicateContributionError(
                f"{self.kind} {name!r} is already registered in the {location} layer"
            )
        layer[name] = value
        active = True

        def dispose() -> None:
            nonlocal active
            if not active:
                return
            active = False
            if layer.get(name) is value:
                del layer[name]
            if scope is not None and not layer:
                self._scoped.pop(scope, None)

        return dispose

    def snapshot(self, scope: AgentScope) -> Mapping[str, ValueT]:
        """Return a read-only merged snapshot for one Scope."""
        merged = dict(self._global)
        for item in scope.lineage():
            merged.update(self._scoped.get(item, {}))
        return MappingProxyType(merged)
