"""Exact persisted decision/current-case authority adapter for field effects."""

from __future__ import annotations

from mechanical_librarian.application.field_effects import AuthoritySnapshot
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import EffectAuthority
from mechanical_librarian.domain.human_decision import HumanDecision


class PersistedFieldEffectAuthority:
    def __init__(self, repository) -> None:
        self._repository = repository

    def resolve(self, decision: HumanDecision | AutomaticDecision) -> AuthoritySnapshot:
        return AuthoritySnapshot(**self._repository.field_effect_authority_source(decision))

    def current(self, authority: EffectAuthority) -> bool:
        return self._repository.field_effect_authority_is_current(authority)
