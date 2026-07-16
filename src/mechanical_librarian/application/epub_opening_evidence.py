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
    PUBLISHABLE_EPUB_OUTCOMES,
    ResolvedEpubSource,
    epub_work_key,
)
from mechanical_librarian.domain.local_evidence import LocalEvidenceOutcome


class EpubOpeningEvidenceRepository(Protocol):
    def resolve(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> ResolvedEpubSource | None: ...

    def accept(
        self,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
        accepted_at: datetime,
    ) -> tuple[str, EpubEvidenceResult | None] | None: ...

    def claim(
        self,
        work_id: str,
        worker_id: str,
        claimed_at: datetime,
        lease_duration: timedelta,
    ) -> EpubEvidenceClaim | None: ...

    def publish(
        self,
        claim: EpubEvidenceClaim,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
        report: EpubOpeningReport,
        published_at: datetime,
    ) -> EpubEvidenceResult | None: ...

    def fail(
        self,
        claim: EpubEvidenceClaim,
        failure: EpubEvidenceFailure,
        failed_at: datetime,
        *,
        retryable: bool,
    ) -> EpubEvidenceResult: ...


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
        lease_duration: timedelta = timedelta(seconds=120),
    ) -> EpubEvidenceResult:
        try:
            source = self._repository.resolve(locator, mount_configuration_digest)
            if source is None:
                return _failed(EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE)
            source_fingerprint = fingerprint(source)
            work_key = epub_work_key(source, source_fingerprint, governing_digest)
            accepted = self._repository.accept(
                source, source_fingerprint, governing_digest, now()
            )
            if accepted is None:
                return _failed(EpubEvidenceFailure.PERSISTENCE_FAILURE)
            work_id, reused = accepted
            if reused is not None:
                return reused
            claim = self._repository.claim(
                work_id, worker_id, now(), lease_duration
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
                )
            if fingerprint(source) != source_fingerprint:
                return self._repository.fail(
                    claim,
                    EpubEvidenceFailure.SOURCE_CHANGED,
                    now(),
                    retryable=True,
                )
            if report.outcome not in PUBLISHABLE_EPUB_OUTCOMES:
                failure, retryable = _operational_failure(report.outcome)
                return self._repository.fail(
                    claim, failure, now(), retryable=retryable
                )
            published = self._repository.publish(
                claim,
                source,
                source_fingerprint,
                governing_digest,
                report,
                now(),
            )
            return (
                published
                if published is not None
                else _failed(EpubEvidenceFailure.SOURCE_CHANGED)
            )
        except (OSError, ValueError):
            return _failed(EpubEvidenceFailure.INTEGRITY_FAILURE)
        except Exception:
            return _failed(EpubEvidenceFailure.INTERNAL_FAILURE)


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
