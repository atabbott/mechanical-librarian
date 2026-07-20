"""Structural synthetic provider for the report-only Hardcover identity preview."""

from __future__ import annotations

from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptResult,
    IsbnIdentityQuery,
    allow_identity_send,
)
from mechanical_librarian.application.ports import OperationContext
from mechanical_librarian.domain.hardcover_identity import ProviderIdentityObservation


class SyntheticBookIdentityProvider:
    """Return one contributor-supplied observation tuple through the provider seam."""

    def __init__(
        self,
        expected_query: IsbnIdentityQuery,
        observations: tuple[ProviderIdentityObservation, ...],
    ) -> None:
        self._expected_query = expected_query
        self._observations = observations
        self._called = False

    def lookup(
        self,
        query: IsbnIdentityQuery,
        context: OperationContext,
        *,
        before_exchange=allow_identity_send,
    ) -> IdentityAttemptResult:
        if self._called or query != self._expected_query:
            raise ValueError("synthetic identity provider invocation is invalid")
        if type(context) is not OperationContext:
            raise TypeError("synthetic identity context is malformed")
        before_exchange()
        self._called = True
        return IdentityAttemptResult.success(self._observations)


__all__ = ["SyntheticBookIdentityProvider"]
