"""Small application-owned contracts for bounded external transport support."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ResolverFailureKind(StrEnum):
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class ResolverFailure(RuntimeError):
    """Value-free failure from the isolated address resolver."""

    def __init__(self, kind: ResolverFailureKind) -> None:
        if type(kind) is not ResolverFailureKind:
            raise ValueError("resolver failure is malformed")
        super().__init__("address resolution failed")
        self.kind = kind


@dataclass(frozen=True, repr=False, slots=True)
class ResolverBudget:
    """Trusted callbacks exposing only cancellation and remaining duration."""

    remaining_seconds: Callable[[], float]
    is_cancelled: Callable[[], bool]

    def __post_init__(self) -> None:
        if not callable(self.remaining_seconds) or not callable(self.is_cancelled):
            raise TypeError("resolver budget is malformed")

    def __repr__(self) -> str:
        return "ResolverBudget(<redacted>)"


class AddressResolver(Protocol):
    def __call__(self, host: str, port: int) -> tuple[str, ...]: ...


class AddressResolverFactory(Protocol):
    def __call__(self, budget: ResolverBudget) -> AddressResolver: ...


class TransportLogIsolationFactory(Protocol):
    def __call__(self) -> AbstractContextManager[None]: ...


__all__ = [
    "AddressResolver",
    "AddressResolverFactory",
    "ResolverBudget",
    "ResolverFailure",
    "ResolverFailureKind",
    "TransportLogIsolationFactory",
]
