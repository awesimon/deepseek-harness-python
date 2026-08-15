"""Stable PyCordis lifecycle and registration errors."""


class CordisError(RuntimeError):
    """Base class for PyCordis failures."""


class RuntimeClosedError(CordisError):
    """Raised when a caller mutates a closed runtime."""


class InactiveContextError(CordisError):
    """Raised when an inactive fiber attempts to create an effect."""


class ServiceUnavailableError(CordisError):
    """Raised when no active service registration satisfies a lookup."""


class UndeclaredDependencyError(CordisError):
    """Raised when a plugin requires a service absent from its declaration."""


class DuplicateServiceError(CordisError):
    """Raised when two providers occupy the same service realm."""


class InvalidEventModeError(CordisError):
    """Raised when an event is dispatched through the wrong mode."""


class InvalidEffectError(CordisError):
    """Raised when effect setup returns an unsupported cleanup value."""
