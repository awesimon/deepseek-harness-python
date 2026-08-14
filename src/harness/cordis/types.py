"""Public value types for the PyCordis kernel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from .runtime import Context

ServiceT = TypeVar("ServiceT")
ConfigT = TypeVar("ConfigT")

type Cleanup = Callable[[], None | Awaitable[None]]
type CleanupResult = None | Cleanup | Iterable[Cleanup]
type EffectSetup = Callable[[], CleanupResult | Awaitable[CleanupResult]]
type PluginApply[ConfigT] = Callable[
    ["Context", ConfigT],
    CleanupResult | Awaitable[CleanupResult],
]


@dataclass(frozen=True, slots=True)
class ServiceKey[ServiceT]:
    """Stable name-based identity for one service API.

    @param name: Globally stable service name.
    """

    name: str

    def __post_init__(self) -> None:
        """Reject an empty service name."""
        if not self.name:
            raise ValueError("service name must not be empty")

    def __repr__(self) -> str:
        """Return a concise diagnostic representation."""
        return f"ServiceKey({self.name!r})"


@dataclass(frozen=True, slots=True)
class PluginSpec[ConfigT]:
    """One mountable plugin definition.

    @param name: Stable diagnostic plugin name.
    @param apply: Plugin lifecycle body.
    @param requires: Services required before activation.
    @param validate: Optional configuration validator and normalizer.
    """

    name: str
    apply: PluginApply[ConfigT]
    requires: tuple[ServiceKey[Any], ...] = ()
    validate: Callable[[Any], ConfigT] | None = None

    def __post_init__(self) -> None:
        """Validate the name and reject duplicate dependency declarations."""
        if not self.name:
            raise ValueError("plugin name must not be empty")
        if len(set(self.requires)) != len(self.requires):
            raise ValueError(f"plugin {self.name!r} declares a service more than once")
