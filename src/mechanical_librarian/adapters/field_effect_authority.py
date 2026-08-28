"""Exact persisted decision/current-case authority adapter for field effects."""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict, cast

from mechanical_librarian.application.field_effects import AuthoritySnapshot
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.field_effect import EffectAuthority
from mechanical_librarian.domain.human_decision import HumanDecision


class _AuthorityFields(TypedDict):
    """Exact keyword shape the persistence layer builds for AuthoritySnapshot."""

    connection: str
    library: str
    work_id: str
    destination: str
    review_case_id: str
    target_fingerprint: str
    target_version: str
    decision_value: object
    source: Literal["persisted_review_case", "fake_service"]
    pre_value: object
    dependency_digests: tuple[str, ...]


class _AuthorityRepository(Protocol):
    """Persistence seam the guarded authority adapter reads."""

    def field_effect_authority_source(
        self, decision: HumanDecision | AutomaticDecision | CoverAutomaticDecision
    ) -> dict[str, object]: ...
    def field_effect_authority_is_current(self, authority: EffectAuthority) -> bool: ...


class PersistedFieldEffectAuthority:
    def __init__(self, repository: _AuthorityRepository) -> None:
        self._repository = repository

    def resolve(
        self, decision: HumanDecision | AutomaticDecision | CoverAutomaticDecision
    ) -> AuthoritySnapshot:
        # The repository builds exactly AuthoritySnapshot's keyword shape; see
        # _AuthorityFields above.
        return AuthoritySnapshot(
            **cast(
                "_AuthorityFields",
                self._repository.field_effect_authority_source(decision),
            )
        )

    def current(self, authority: EffectAuthority) -> bool:
        return self._repository.field_effect_authority_is_current(authority)
