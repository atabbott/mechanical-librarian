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
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol, cast

from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.configuration import ApplicationConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspector,
    CurrentnessError,
    EpubInspector,
    MetadataReportError,
    ReportRepository,
    TargetReader,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    ProviderRecoveryAttemptFailure,
    TargetConnectionHeld,
    provider_recovery_jobs,
)
from mechanical_librarian.application.provider_recovery_policy import (
    provider_recovery_decision,
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
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.provider_recovery import (
    DEFERRED_RECOVERY_FAILURES,
    TRANSIENT_RECOVERY_FAILURES,
    RecoveryAttemptEvidence,
)

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

    def get_proposal(self, proposal_set_id: str) -> str: ...


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
    ) -> tuple[TargetReader, EpubInspector, AudioInspector]: ...


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
        enqueue: Callable[
            [tuple[ProviderRecoveryJob, ...], int, Mapping[ProviderRecoveryJob, str]],
            int,
        ],
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
    ) -> RecoveryAttemptEvidence | None:
        owed, staged_report_id = self._admit(job)
        if not owed:
            return None
        evidence: list[RecoveryAttemptEvidence] = []
        pipeline = self._pipeline()
        report_id = (
            staged_report_id
            if staged_report_id is not None
            else self._generate_successor(
                pipeline, recovery_state, job, heartbeat, evidence.append
            )
        )
        try:
            self._complete_successor(pipeline, job, report_id, heartbeat)
        except CurrentnessError:
            raise
        except Exception as error:
            raise ProviderRecoveryAttemptFailure(
                evidence[0] if evidence else RecoveryAttemptEvidence("terminal_failure")
            ) from error
        return evidence[0] if evidence else None

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
        operational_evidence: Callable[[RecoveryAttemptEvidence], None],
    ) -> str:
        connection, library, mount, _ = self._configuration.select(
            job.target_connection, job.library
        )
        if mount is None:
            raise CurrentnessError("target or media changed")
        target, inspector, audio_inspector = self._observers(connection, library, mount)
        try:
            serialized = GenerateProviderRetrySuccessor(
                target=target,
                inspector=inspector,
                audio_inspector=audio_inspector,
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
                operational_evidence=operational_evidence,
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
    ) -> tuple[str, ...]:
        """Deliver exact package cutovers without letting one row stop boot."""
        owns_repository = repository is None
        if repository is None:
            repository = self._repository_factory()
        try:
            failures: set[str] = set()
            cursor: tuple[int, str] | None = None
            while rows := repository.pending_provider_recovery_outbox(
                limit=100, after=cursor
            ):
                for row in rows:
                    typed = cast(
                        "tuple[str, str, str, str, str | None, str, str | None, int | None, str, int]",
                        row,
                    )
                    cursor = (typed[9], typed[0])
                    try:
                        self._deliver(repository, typed)
                    except ReviewPipelineError as error:
                        # The row remains pending for a later bounded retry.
                        # Never let a bad oldest row make startup crash-loop or
                        # prevent a younger independently deliverable cutover.
                        failures.add(
                            error.code
                            if error.code
                            in {"configuration", "projection", "persistence"}
                            else "persistence"
                        )
                    except Exception:
                        failures.add("persistence")
            return tuple(sorted(failures))
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
            proposal = (
                None
                if proposal_id is None
                else ProposalSet.from_json(repository.get_proposal(proposal_id))
            )
            if proposal is not None and (
                proposal.proposal_set_id != proposal_id
                or proposal.report_id != report.report_id
            ):
                raise ReviewPipelineError("projection")
            jobs = provider_recovery_jobs(
                report,
                package=package,
                proposal=proposal,
                configuration=self._configuration,
                scope=report.target.library,
            )
            # An ebook's non-terminal lane failure reaches recovery only here,
            # so its typed classification names the episode (#1367). Audiobook
            # and credential failures are already observed where they occur.
            failures = (
                {}
                if report.audio is not None
                else {
                    observation.provider: decision[0]
                    for observation in report.providers
                    if (
                        decision := provider_recovery_decision(
                            observation, now=created_at
                        )
                    )
                    is not None
                    and decision[0]
                    in TRANSIENT_RECOVERY_FAILURES | DEFERRED_RECOVERY_FAILURES
                }
            )
            outcomes = {
                job: failures[job.connection]
                for job in jobs
                if job.connection in failures
            }
            if jobs:
                try:
                    self._enqueue(jobs, created_at, outcomes)
                except Exception as error:
                    raise ReviewPipelineError("persistence") from error
        repository.complete_provider_recovery_outbox(
            outbox_id,
            delivered_at=max(self._clock(), created_at),
        )
