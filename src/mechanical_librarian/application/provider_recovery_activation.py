"""Drive one provider-recovery member and drain the durable cutover outbox.

`ScopedProviderRecoveryRunner` owns the lease and the backoff; this owns what
happens inside one claimed member: proving the package is still exact-current,
choosing between an already-staged successor and a freshly generated one,
recovering the safe target-hold taxonomy from a currentness failure, and
confirming the package actually cut over afterwards.

Every adapter this needs arrives as an explicit dependency. The composition root
still constructs the repository, the target reader, the media inspector, and the
provider bindings; nothing here opens a connection, reads a secret, or knows what
an Audiobookshelf endpoint is.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.configuration import ApplicationConfiguration
from mechanical_librarian.application.metadata_report import (
    CurrentnessError,
    EpubInspector,
    MetadataReportError,
    ReportRepository,
    TargetReader,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    TargetConnectionHeld,
    provider_recovery_jobs,
)
from mechanical_librarian.application.provider_retry_successor import (
    ConfiguredProviderRetry,
    GenerateProviderRetrySuccessor,
    ProviderRetrySuccessorError,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.metadata_report import MetadataReport

if TYPE_CHECKING:
    from mechanical_librarian.application.configuration import (
        LibraryConfiguration,
        MediaMountConfiguration,
        TargetConnectionConfiguration,
    )
    from mechanical_librarian.domain.provider_recovery import (
        ProviderRecovery,
        ProviderRecoveryJob,
    )

__all__ = [
    "ProviderRecoveryActivation",
    "ProviderRecoveryRepository",
    "ProviderRetryReceipt",
    "RetrySuccessorObservers",
]


class ProviderRecoveryRepository(ReportRepository, Protocol):
    """``ReportRepository`` plus the two retry-staging reads recovery performs."""

    def completed_provider_retry_successor(
        self, source_report_id: str
    ) -> str | None: ...

    def provider_retry_work_id(self, report_id: str) -> str: ...


class ProviderRetryReceipt(Protocol):
    """The staging write the concrete repository adds for a published successor."""

    def stage_provider_retry_successor(
        self, source_report_id: str, report: MetadataReport, staged_at: int
    ) -> str: ...


class RetrySuccessorObservers(Protocol):
    """Build the reader and inspector one successor observation requires.

    The composition root implements this so every credential, client, and
    artifact store stays behind the boundary that already owns them.
    """

    def __call__(
        self,
        connection: TargetConnectionConfiguration,
        library: LibraryConfiguration,
        mount: MediaMountConfiguration,
    ) -> tuple[TargetReader, EpubInspector]: ...


class ProviderRecoveryActivation:
    """One claimed recovery member, and the outbox that creates recovery work."""

    def __init__(
        self,
        *,
        configuration: ApplicationConfiguration,
        repository_factory: Callable[[], ProviderRecoveryRepository],
        observers: RetrySuccessorObservers,
        provider_resolver: Callable[
            [str, str, str, str, int], ConfiguredProviderRetry | None
        ],
        pipeline: Callable[[], ReviewPreparationPipeline | None],
        enqueue: Callable[[tuple[ProviderRecoveryJob, ...]], int],
        clock: Callable[[], int],
    ) -> None:
        self._configuration = configuration
        self._repository_factory = repository_factory
        self._observers = observers
        self._provider_resolver = provider_resolver
        self._pipeline = pipeline
        self._enqueue = enqueue
        self._clock = clock

    def retry_member(
        self,
        recovery_state: ProviderRecovery,
        job: ProviderRecoveryJob,
        heartbeat: Callable[[], None],
    ) -> None:
        owed, staged_report_id = self._admit(job)
        if not owed:
            return
        pipeline = self._pipeline()
        report_id = (
            staged_report_id
            if staged_report_id is not None
            else self._generate_successor(pipeline, recovery_state, job, heartbeat)
        )
        self._complete_successor(pipeline, job, report_id, heartbeat)

    def _admit(self, job: ProviderRecoveryJob) -> tuple[bool, str | None]:
        """Prove the claimed package is still exact-current before observing.

        Returns ``(False, None)`` when the successor already completed and
        nothing is owed, ``(True, report_id)`` when a later report has already
        been staged, and ``(True, None)`` when a fresh observation is owed.
        """
        repository = self._repository_factory()
        try:
            completed_report_id = repository.completed_provider_retry_successor(
                job.source_report_id
            )
            if completed_report_id is not None:
                return False, None
            package = repository.current_review_package(
                job.target_connection, job.library, job.item
            )
            if package != (
                job.package_proposal_id,
                job.package_report_id,
                job.package_case_id,
                job.package_checkpoint,
                job.package_outcome,
            ):
                raise CurrentnessError("target or media changed")
            current_document = repository.current_optional(
                job.target_connection,
                job.library,
                job.item,
                job.file_identity,
            )
            if current_document is None:
                return True, None
            current_report_id = MetadataReport.from_json(current_document).report_id
            if current_report_id == job.source_report_id:
                return True, None
            try:
                repository.provider_retry_work_id(current_report_id)
            except MetadataReportError:
                raise CurrentnessError("target or media changed") from None
            return True, current_report_id
        finally:
            repository.close()

    def _generate_successor(
        self,
        pipeline: ReviewPreparationPipeline | None,
        recovery_state: ProviderRecovery,
        job: ProviderRecoveryJob,
        heartbeat: Callable[[], None],
    ) -> str:
        connection, library, mount, _ = self._configuration.select(
            job.target_connection, job.library
        )
        if mount is None:
            raise CurrentnessError("target or media changed")
        target, inspector = self._observers(connection, library, mount)
        try:
            serialized = GenerateProviderRetrySuccessor(
                target=target,
                inspector=inspector,
                repository_factory=cast(
                    "Callable[[], ReportRepository]", self._repository_factory
                ),
                provider_resolver=self._provider_resolver,
                mount=mount,
                limits=self._configuration.limits,
                # The staged work id is deliberately discarded here; the seam
                # types the hook as returning nothing.
                after_publication=cast(
                    "Callable[[ReportRepository, MetadataReport], None]",
                    lambda bound, report: cast(
                        "ProviderRetryReceipt", bound
                    ).stage_provider_retry_successor(
                        job.source_report_id, report, self._clock()
                    ),
                ),
                heartbeat=heartbeat,
            ).run(
                job.source_report_id,
                tuple(json.loads(job.lane_ids)),
                provider_name=job.connection,
                adapter=job.adapter,
                generation=job.generation,
                generation_woken=recovery_state.attempt == 0,
            )
        except TargetConnectionError as error:
            if pipeline is not None:
                pipeline.hold_connection(job.target_connection, error.code)
            # This is a target hold, not provider evidence: retain the
            # completed provider observation and stop successor backoff.
            raise TargetConnectionHeld("target connection is held") from None
        except CurrentnessError as error:
            # Currentness deliberately hides target details at the report
            # boundary, but retains the closed target condition as its cause.
            # Recover that safe taxonomy here so this successor cannot be
            # mistaken for a provider transient/backoff condition.
            cause = error.__cause__
            while cause is not None and not isinstance(cause, TargetConnectionError):
                cause = cause.__cause__
            if isinstance(cause, TargetConnectionError):
                if pipeline is not None:
                    pipeline.hold_connection(job.target_connection, cause.code)
                raise TargetConnectionHeld("target connection is held") from None
            raise
        return MetadataReport.from_json(serialized).report_id

    def _complete_successor(
        self,
        pipeline: ReviewPreparationPipeline | None,
        job: ProviderRecoveryJob,
        report_id: str,
        heartbeat: Callable[[], None],
    ) -> None:
        if pipeline is None or not pipeline.run_recovery_successor(
            report_id, heartbeat
        ):
            raise ProviderRetrySuccessorError("provider retry package incomplete")
        repository = self._repository_factory()
        try:
            package = repository.current_review_package(
                job.target_connection, job.library, job.item
            )
            if package is None or package[1] != report_id:
                raise ProviderRetrySuccessorError("provider retry package incomplete")
        finally:
            repository.close()

    def drain_outbox(
        self, repository: ProviderRecoveryRepository | None = None
    ) -> None:
        """Deliver exact package cutovers without letting one row stop boot."""
        owns_repository = repository is None
        if repository is None:
            repository = self._repository_factory()
        try:
            failed: set[str] = set()
            while rows := repository.pending_provider_recovery_outbox(limit=100):
                delivered = False
                for row in rows:
                    typed = cast(
                        "tuple[str, str, str, str, str | None, str, str | None, int | None, str, int]",
                        row,
                    )
                    if typed[0] in failed:
                        continue
                    try:
                        self._deliver(repository, typed)
                        delivered = True
                    except Exception:
                        # The row remains pending for a later bounded retry.
                        # Never let a bad oldest row make startup crash-loop or
                        # prevent a younger independently deliverable cutover.
                        failed.add(typed[0])
                if not delivered:
                    return
        finally:
            if owns_repository:
                repository.close()

    def _deliver(
        self,
        repository: ProviderRecoveryRepository,
        # The outbox query projects exactly this column order and nullability;
        # see ``pending_provider_recovery_outbox``.
        row: tuple[
            str, str, str, str, str | None, str, str | None, int | None, str, int
        ],
    ) -> None:
        (
            outbox_id,
            target_connection,
            library_name,
            item_id,
            proposal_id,
            report_id,
            case_id,
            checkpoint,
            outcome,
            created_at,
        ) = row
        package = (proposal_id, report_id, case_id, checkpoint, outcome)
        current = repository.current_review_package(
            target_connection,
            library_name,
            item_id,
        )
        if current == package:
            report = MetadataReport.from_json(repository.get(report_id))
            if (
                report.target.connection != target_connection
                or report.target.library != library_name
                or report.target.item != item_id
            ):
                raise ReviewPipelineError("projection")
            jobs = provider_recovery_jobs(
                report,
                package=package,
                configuration=self._configuration,
                scope=report.target.library,
            )
            if jobs:
                try:
                    self._enqueue(jobs)
                except Exception as error:
                    raise ReviewPipelineError("recovery") from error
        repository.complete_provider_recovery_outbox(
            outbox_id,
            delivered_at=max(self._clock(), created_at),
        )
