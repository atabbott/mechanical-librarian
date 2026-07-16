"""Durable, restart-safe orchestration for EPUB opening evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from mechanical_librarian.domain.epub_opening import EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceClaim,
    EpubEvidenceFailure,
    EpubEvidenceLocator,
    EpubEvidenceMode,
    EpubEvidenceResult,
    EpubRecoverableWork,
    EpubSourceResolution,
    PUBLISHABLE_EPUB_OUTCOMES,
    ResolvedEpubSource,
    epub_work_key,
)
from mechanical_librarian.domain.local_evidence import LocalEvidenceOutcome


class EpubOpeningEvidenceRepository(Protocol):
    def next_recoverable(
        self, mount_configuration_digests: frozenset[str], governing_digest: str, now: datetime
    ) -> EpubRecoverableWork | None: ...

    def resolve(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> EpubSourceResolution: ...

    def accept(
        self,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
        accepted_at: datetime,
        initiating_authority: str,
    ) -> tuple[str, EpubEvidenceResult | None] | None: ...

    def claim(
        self,
        work_id: str,
        worker_id: str,
        claimed_at: datetime,
        lease_duration: timedelta,
        capacity: int,
    ) -> EpubEvidenceClaim | None: ...

    def publish(
        self,
        claim: EpubEvidenceClaim,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
        report: EpubOpeningReport,
        published_at: datetime,
        source_is_current: Callable[[], bool],
        publication_clock: Callable[[], datetime] | None = None,
    ) -> EpubEvidenceResult | None: ...

    def fail(
        self,
        claim: EpubEvidenceClaim,
        failure: EpubEvidenceFailure,
        failed_at: datetime,
        *,
        retryable: bool,
        failure_clock: Callable[[], datetime] | None = None,
    ) -> EpubEvidenceResult: ...

    def cancel(
        self, work_id: str, authority: str, cancelled_at: datetime
    ) -> bool: ...


class RunEpubOpeningEvidence:
    def __init__(self, repository: EpubOpeningEvidenceRepository) -> None:
        self._repository = repository

    def run(
        self,
        locator: EpubEvidenceLocator,
        mount_configuration_digest: str,
        governing_digest: str,
        worker_id: str,
        now: Callable[[], datetime],
        fingerprint: Callable[[ResolvedEpubSource], str],
        execute: Callable[[ResolvedEpubSource], EpubOpeningReport],
        revalidate: Callable[[ResolvedEpubSource, str], bool] = lambda _source, _digest: True,
        lease_duration: timedelta = timedelta(seconds=120),
        capacity: int = 1,
        initiating_authority: str = "local_operator",
    ) -> EpubEvidenceResult:
        try:
            resolution = self._repository.resolve(locator, mount_configuration_digest)
            if resolution.source is None:
                return _failed(resolution.failure or EpubEvidenceFailure.CORRUPT_STATE)
            source = resolution.source
            try:
                source_fingerprint = fingerprint(source)
            except OSError:
                return _failed(EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE)
            except ValueError:
                return _failed(EpubEvidenceFailure.BINDING_INTEGRITY_FAILURE)
            work_key = epub_work_key(source, source_fingerprint, governing_digest)
            accepted = self._repository.accept(
                source, source_fingerprint, governing_digest, now(), initiating_authority
            )
            if accepted is None:
                return _failed(EpubEvidenceFailure.PERSISTENCE_FAILURE)
            work_id, reused = accepted
            if reused is not None:
                return reused
            claim = self._repository.claim(
                work_id, worker_id, now(), lease_duration, capacity
            )
            if claim is None or claim.work_key != work_key:
                return _failed(EpubEvidenceFailure.BUSY)
            report = execute(source)
            if type(report) is not EpubOpeningReport:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.INTERNAL_FAILURE,
                    now(),
                    retryable=False,
                    failure_clock=now,
                )
            if fingerprint(source) != source_fingerprint or not revalidate(
                source, source_fingerprint
            ):
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.SOURCE_CHANGED,
                    now(),
                    retryable=True,
                    failure_clock=now,
                )
            if report.outcome not in PUBLISHABLE_EPUB_OUTCOMES:
                failure, retryable = _operational_failure(report.outcome)
                return self._repository.fail(
                    claim, failure, now(), retryable=retryable, failure_clock=now
                )
            published = self._repository.publish(
                claim,
                source,
                source_fingerprint,
                governing_digest,
                report,
                now(),
                lambda: revalidate(source, source_fingerprint),
                now,
            )
            if published is None:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.PERSISTENCE_FAILURE,
                    now(),
                    retryable=True,
                    failure_clock=now,
                )
            if not published.succeeded:
                return self._repository.fail(
                    claim,
                    published.failure or EpubEvidenceFailure.CORRUPT_STATE,
                    now(),
                    retryable=published.failure is EpubEvidenceFailure.SOURCE_CHANGED,
                    failure_clock=now,
                )
            return published
        except (OSError, ValueError):
            if "claim" in locals() and claim is not None:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.SOURCE_CHANGED,
                    now(),
                    retryable=True,
                    failure_clock=now,
                )
            return _failed(EpubEvidenceFailure.INTEGRITY_FAILURE)
        except Exception:
            if "claim" in locals() and claim is not None:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.INTERNAL_FAILURE,
                    now(),
                    retryable=True,
                    failure_clock=now,
                )
            return _failed(EpubEvidenceFailure.INTERNAL_FAILURE)

    def resume(
        self,
        mount_configuration_digests: frozenset[str],
        governing_digest: str,
        worker_id: str,
        now: Callable[[], datetime],
        fingerprint: Callable[[ResolvedEpubSource], str],
        execute: Callable[[ResolvedEpubSource], EpubOpeningReport],
        revalidate: Callable[[ResolvedEpubSource, str], bool],
        lease_duration: timedelta = timedelta(seconds=120),
        capacity: int = 1,
        initiating_authority: str = "system_recovery",
    ) -> EpubEvidenceResult | None:
        work = self._repository.next_recoverable(
            mount_configuration_digests, governing_digest, now()
        )
        if work is None:
            return None
        claim = self._repository.claim(
            work.work_id, worker_id, now(), lease_duration, capacity
        )
        if claim is None or claim.work_key != work.work_key:
            return _failed(EpubEvidenceFailure.BUSY)
        resolution = self._repository.resolve(
            work.locator, work.mount_configuration_digest
        )
        if resolution.source is None:
            return self._repository.fail(
                claim,
                resolution.failure or EpubEvidenceFailure.CORRUPT_STATE,
                now(),
                retryable=False,
                failure_clock=now,
            )
        source = resolution.source
        try:
            source_fingerprint = fingerprint(source)
            if source_fingerprint != work.source_fingerprint:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.SOURCE_CHANGED,
                    now(),
                    retryable=False,
                    failure_clock=now,
                )
            if epub_work_key(
                source, source_fingerprint, governing_digest
            ) != work.work_key:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.CORRUPT_STATE,
                    now(),
                    retryable=False,
                    failure_clock=now,
                )
            report = execute(source)
            if type(report) is not EpubOpeningReport:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.INTERNAL_FAILURE,
                    now(),
                    retryable=False,
                    failure_clock=now,
                )
            if fingerprint(source) != source_fingerprint or not revalidate(
                source, work.source_fingerprint
            ):
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.SOURCE_CHANGED,
                    now(),
                    retryable=False,
                    failure_clock=now,
                )
            if report.outcome not in PUBLISHABLE_EPUB_OUTCOMES:
                failure, retryable = _operational_failure(report.outcome)
                return self._repository.fail(
                    claim,
                    failure,
                    now(),
                    retryable=retryable,
                    failure_clock=now,
                )
            published = self._repository.publish(
                claim,
                source,
                work.source_fingerprint,
                governing_digest,
                report,
                now(),
                lambda: revalidate(source, work.source_fingerprint),
                now,
            )
            if published is None:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.PERSISTENCE_FAILURE,
                    now(),
                    retryable=True,
                    failure_clock=now,
                )
            if not published.succeeded:
                return self._repository.fail(
                    claim,
                    published.failure or EpubEvidenceFailure.CORRUPT_STATE,
                    now(),
                    retryable=False,
                    failure_clock=now,
                )
            return published
        except (OSError, ValueError):
            return self._repository.fail(
                claim,
                EpubEvidenceFailure.SOURCE_CHANGED,
                now(),
                retryable=True,
                failure_clock=now,
            )
        except Exception:
            return self._repository.fail(
                claim,
                EpubEvidenceFailure.INTERNAL_FAILURE,
                now(),
                retryable=True,
                failure_clock=now,
            )


def _operational_failure(
    outcome: LocalEvidenceOutcome,
) -> tuple[EpubEvidenceFailure, bool]:
    return {
        LocalEvidenceOutcome.INVALID_INVOCATION: (
            EpubEvidenceFailure.INVALID_INVOCATION,
            False,
        ),
        LocalEvidenceOutcome.INVALID_CONFIGURATION: (
            EpubEvidenceFailure.INVALID_CONFIGURATION,
            False,
        ),
        LocalEvidenceOutcome.INVALID_REFERENCE: (
            EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE,
            False,
        ),
        LocalEvidenceOutcome.UNAVAILABLE_SOURCE: (
            EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE,
            True,
        ),
        LocalEvidenceOutcome.RESOURCE_LIMIT: (
            EpubEvidenceFailure.RESOURCE_LIMIT,
            False,
        ),
        LocalEvidenceOutcome.TIMEOUT: (EpubEvidenceFailure.TIMEOUT, True),
        LocalEvidenceOutcome.CANCELLATION: (
            EpubEvidenceFailure.CANCELLED,
            False,
        ),
        LocalEvidenceOutcome.UNSTABLE_SOURCE: (
            EpubEvidenceFailure.SOURCE_CHANGED,
            True,
        ),
        LocalEvidenceOutcome.CHILD_FAILURE: (
            EpubEvidenceFailure.INTERNAL_FAILURE,
            True,
        ),
        LocalEvidenceOutcome.INTERNAL_FAILURE: (
            EpubEvidenceFailure.INTERNAL_FAILURE,
            True,
        ),
    }.get(outcome, (EpubEvidenceFailure.INTERNAL_FAILURE, False))


def _failed(failure: EpubEvidenceFailure) -> EpubEvidenceResult:
    return EpubEvidenceResult(EpubEvidenceMode.FAILED, failure=failure)


EpubOpeningEvidenceRunner = Callable[
    [Path, Path, str, str, int], EpubEvidenceResult
]


__all__ = [
    "EpubOpeningEvidenceRepository",
    "EpubOpeningEvidenceRunner",
    "RunEpubOpeningEvidence",
]
