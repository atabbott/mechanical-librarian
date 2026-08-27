"""Production adapters for the continuous guarded-effect application."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.domain.field_effect import EffectConsent, FieldEffectPlan


class _ConsentRepository(Protocol):
    """Persistence seam the guarded consent adapter reads."""

    def current_field_effect_consent(
        self,
        connection: str,
        library: str,
        destination: str,
    ) -> EffectConsent: ...
    def field_effect_consent_is_current(self, consent: EffectConsent) -> bool: ...


class _DependencyRepository(Protocol):
    """Persistence seam the guarded dependency adapter reads."""

    def field_effect_dependencies_current(self, digests: tuple[str, ...]) -> bool: ...


class _BackupRepository(Protocol):
    """Persistence seam the durable backup readiness adapter reads."""

    def field_effect_backup_ready(self, *, fresh: bool = False) -> bool: ...


class PersistedEffectConsent:
    def __init__(self, repository: _ConsentRepository) -> None:
        self._repository = repository

    def resolve(
        self, *, connection: str, library: str, destination: str
    ) -> EffectConsent:
        return self._repository.current_field_effect_consent(
            connection, library, destination
        )

    def current(self, consent: EffectConsent) -> bool:
        return self._repository.field_effect_consent_is_current(consent)


class PersistedEffectDependencies:
    def __init__(self, repository: _DependencyRepository) -> None:
        self._repository = repository

    def current(self, digests: tuple[str, ...]) -> bool:
        return self._repository.field_effect_dependencies_current(digests)


class DurableBackupReadiness:
    """Require a consistent exact-current application checkpoint before writes."""

    def __init__(self, repository: _BackupRepository) -> None:
        self._repository = repository

    def ready(self, plan: FieldEffectPlan) -> bool:
        del plan
        return self._repository.field_effect_backup_ready(fresh=True)


class PersistedForwardReevaluation:
    def __init__(self, request_scan: Callable[[str, str], object]) -> None:
        self._request_scan = request_scan

    def emit(self, plan: FieldEffectPlan) -> None:
        self._request_scan(plan.connection, plan.library)
