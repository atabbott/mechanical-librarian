"""Production adapters for the continuous guarded-effect application."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    PipelineProgress,
    ReviewPipelineError,
)
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

    def field_effect_matching_changed(self, digests: tuple[str, ...]) -> bool: ...


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

    def matching_changed(self, digests: tuple[str, ...]) -> bool:
        return self._repository.field_effect_matching_changed(digests)


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


class CoalescedScanRequest:
    """Ask the scan scheduler for one library scan, unless one is already owed.

    A verified cover write marks its work for fresh preparation; this asks for
    the scan that prepares it. A queued scan, or a running one already asked
    to scan again, will reach the work, so asking again would only repeat it:
    each library gets at most one request until its scan starts (#1363, Q17).
    The write has committed, so a refused request is not raised: the mark
    stays and the next scan of the library prepares the work.
    """

    def __init__(
        self,
        progress: Callable[[str, str], PipelineProgress | None],
        request: Callable[[str, str], object],
    ) -> None:
        self._progress = progress
        self._request = request

    def __call__(self, connection: str, library: str) -> None:
        try:
            current = self._progress(connection, library)
            if current is not None and (
                current.state == "queued" or current.rescan_requested
            ):
                return
            self._request(connection, library)
        except (MetadataReportError, ReviewPipelineError):
            return
