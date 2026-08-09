"""Production adapters for the continuous guarded-effect application."""

from __future__ import annotations

from collections.abc import Callable

from mechanical_librarian.domain.field_effect import EffectConsent, FieldEffectPlan


class PersistedEffectConsent:
    def __init__(self, repository) -> None:
        self._repository = repository

    def resolve(self, *, connection: str, library: str,
                destination: str) -> EffectConsent:
        return self._repository.current_field_effect_consent(
            connection, library, destination
        )

    def current(self, consent: EffectConsent) -> bool:
        return self._repository.field_effect_consent_is_current(consent)


class PersistedEffectDependencies:
    def __init__(self, repository) -> None:
        self._repository = repository

    def current(self, digests: tuple[str, ...]) -> bool:
        return self._repository.field_effect_dependencies_current(digests)


class DurableBackupReadiness:
    """Require a consistent exact-current application checkpoint before writes."""

    def __init__(self, repository) -> None:
        self._repository = repository

    def ready(self, plan: FieldEffectPlan) -> bool:
        del plan
        return self._repository.field_effect_backup_ready()


class PersistedForwardReevaluation:
    def __init__(self, request_scan: Callable[[str, str], object]) -> None:
        self._request_scan = request_scan

    def emit(self, plan: FieldEffectPlan) -> None:
        self._request_scan(plan.connection, plan.library)
