"""Structural synthetic provider for the report-only Hardcover identity preview."""

from __future__ import annotations

from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptResult,
    IsbnIdentityQuery,
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
        self._prepared = False
        self._called = False

    def prepare(
        self,
        query: IsbnIdentityQuery,
        context: OperationContext,
    ) -> SyntheticBookIdentityProvider:
        if self._prepared or query != self._expected_query:
            raise ValueError("synthetic identity provider invocation is invalid")
        if type(context) is not OperationContext:
            raise TypeError("synthetic identity context is malformed")
        self._prepared = True
        return self

    def exchange(
        self,
        context: OperationContext,
        *,
        begin_transport,
    ) -> IdentityAttemptResult:
        if self._called or not self._prepared or type(context) is not OperationContext:
            raise ValueError("synthetic identity provider invocation is invalid")
        if not callable(begin_transport):
            raise TypeError("synthetic transport authority is malformed")
        transport_context = begin_transport()
        if type(transport_context) is not OperationContext:
            raise TypeError("synthetic transport context is malformed")
        self._called = True
        return IdentityAttemptResult.success(self._observations)


__all__ = ["SyntheticBookIdentityProvider"]
