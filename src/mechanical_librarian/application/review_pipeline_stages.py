"""Assemble the review-preparation pipeline from explicit collaborators.

`ReviewPreparationPipeline` advances persisted actions; this decides what each
stage means. Which catalogue entry still qualifies an extraction, which
credential failure is a provider health observation rather than a bare
authentication stop, which admission result is stale, and which advancement owes
an outbox drain are all application decisions that previously lived as closures
inside the composition root.

Everything adapter-shaped arrives as a dependency. The composition root still
opens the catalogue and the repository, constructs the target reader, reads the
secrets, and builds the cover-consideration service; nothing here does.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from mechanical_librarian.application.batch import (
    BatchSelection,
    batch_fingerprint,
)
from mechanical_librarian.application.batch import (
    catalogue_fingerprint as batch_catalogue_fingerprint,
)
from mechanical_librarian.application.catalogue import (
    CatalogueError,
    CatalogueReader,
    CatalogueRepository,
    RefreshCurrentCatalogue,
    TargetConnectionError,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    CurrentnessError,
    TargetNotFoundError,
)
from mechanical_librarian.application.proposal_sets import ProposalCurrentnessError
from mechanical_librarian.application.provider_recovery_evidence import (
    ProviderFailureObserver,
    provider_statuses,
    record_provider_recovery_observations,
)
from mechanical_librarian.application.review_application import (
    ReviewApplicationError,
    prepare_cover_consideration,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.metadata_report import MetadataReport, ProviderStatus
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionResult,
    ReviewAdmissionStaleError,
)

if TYPE_CHECKING:
    import threading

    from mechanical_librarian.application.catalogue import CatalogueEntry
    from mechanical_librarian.application.configuration import (
        ApplicationConfiguration,
        LibraryConfiguration,
    )
    from mechanical_librarian.application.cover_consideration import (
        CoverConsiderationService,
    )
    from mechanical_librarian.application.operations import StageTimer
    from mechanical_librarian.application.review_application import SerializedGate

__all__ = ["ReviewPipelineStages", "ReviewStageRepository"]


class ReviewStageRepository(ReviewPipelineRepository, Protocol):
    """``ReviewPipelineRepository`` plus the three reads the stages also perform.

    The pipeline hands every stage callback back exactly the repository the
    composition root's own factory created, so this states the capabilities that
    object always carried rather than restricting it.
    """

    def get(self, report_id: str) -> str: ...

    def read_staged_cover_preparation(
        self, action: PipelineAction
    ) -> tuple[str, str, bool] | None: ...

    def atomic_cover_preparation(self, call: Callable[[], object]) -> object: ...


class ReviewPipelineStages:
    """The nine stage decisions, and the pipeline they are assembled into."""

    def __init__(
        self,
        *,
        configuration: ApplicationConfiguration,
        catalogue: Callable[[bool], CatalogueRepository],
        catalogue_reader: CatalogueReader,
        probe_connection: Callable[[str, tuple[LibraryConfiguration, ...]], None],
        connections: frozenset[str],
        repository_factory: Callable[[], ReviewPipelineRepository],
        recover_artifacts: Callable[[], None],
        generate_report: Callable[
            [PipelineAction, bool, ReviewPipelineRepository], str
        ],
        create_proposal_set: Callable[[str, ReviewPipelineRepository], str],
        admit_review_case: Callable[[str, ReviewPipelineRepository], str],
        cover_considerations: Callable[
            [ReviewPipelineRepository], CoverConsiderationService
        ],
        recovery_observer: ProviderFailureObserver,
        drain_outbox: Callable[[ReviewPipelineRepository], None],
        discovery_lock: threading.Lock,
        clock: Callable[[], int],
    ) -> None:
        self._configuration = configuration
        self._catalogue = catalogue
        self._catalogue_reader = catalogue_reader
        self._probe_connection = probe_connection
        self._connections = connections
        self._repository_factory = repository_factory
        self._recover_artifacts = recover_artifacts
        self._generate_report = generate_report
        self._create_proposal_set = create_proposal_set
        self._admit_review_case = admit_review_case
        self._cover_considerations = cover_considerations
        self._recovery_observer = recovery_observer
        self._drain_outbox = drain_outbox
        self._discovery_lock = discovery_lock
        self._clock = clock

    def pipeline(self, timing: StageTimer | None = None) -> ReviewPreparationPipeline:
        """Assemble the one pipeline these stages advance."""
        return ReviewPreparationPipeline(
            repository_factory=self._repository_factory,
            libraries=tuple(
                (
                    library.connection,
                    library.name,
                    library.library_id,
                    batch_fingerprint(
                        self._configuration,
                        (
                            BatchSelection(
                                library.connection,
                                library.name,
                                library.library_id,
                                "producer",
                                None,
                                "producer",
                                False,
                            ),
                        ),
                    ),
                )
                for library in self._configuration.libraries
            ),
            discover=self.discover,
            extract=self.extract,
            providers=self.providers,
            propose=self.propose,
            admit=self.admit,
            project=self.project,
            before_projection=self.prepare_cover,
            after_advance=self.after_advance,
            check_connection=self.check_connection,
            timing=timing,
        )

    def discover(
        self, connection: str, library: str
    ) -> tuple[int, str, tuple[CatalogueEntry, ...]]:
        with self._discovery_lock:
            repository = self._catalogue(True)
            try:
                service = RefreshCurrentCatalogue(self._catalogue_reader, repository)
                service.refresh_library(self._configuration, connection, library)
                snapshot = repository.snapshot(
                    batch_catalogue_fingerprint(self._configuration)
                )
                return snapshot.generation, snapshot.fingerprint, snapshot.entries
            except TargetConnectionError:
                raise
            except CatalogueError as error:
                raise ReviewPipelineError("transient") from error
            finally:
                repository.close()

    def extract(
        self, pipeline_repository: ReviewPipelineRepository, action: PipelineAction,
        claim_next: bool,
    ) -> tuple[str, bool]:
        self._require_eligible_entry(action)
        self._recover_artifacts()
        try:
            serialized = self._generate_report(action, claim_next, pipeline_repository)
        except CredentialError as error:
            raise self._credential_failure(error, action) from error
        except (CurrentnessError, TargetNotFoundError) as error:
            raise ReviewPipelineError("stale") from error
        return MetadataReport.from_json(serialized).report_id, True

    def _require_eligible_entry(self, action: PipelineAction) -> None:
        catalogue = self._catalogue(False)
        try:
            entry = catalogue.latest_entry(
                action.catalogue_fingerprint or "",
                (
                    action.connection,
                    action.library,
                    action.library_id,
                    action.item_id or "",
                ),
            )
        except CatalogueError as error:
            raise ReviewPipelineError("stale") from error
        finally:
            catalogue.close()
        if (
            entry is None
            or entry.file_identity != action.file_identity
            or entry.source_fingerprint != action.source_fingerprint
            or not entry.eligible
        ):
            raise ReviewPipelineError("stale")

    def _credential_failure(
        self, error: CredentialError, action: PipelineAction
    ) -> ReviewPipelineError:
        """Record a named provider's credential failure as durable health first."""
        if error.provider is None:
            return ReviewPipelineError("authentication")
        provider = next(
            (
                value for value in self._configuration.providers
                if value.name == error.provider
            ),
            None,
        )
        if provider is None:
            return ReviewPipelineError("configuration")
        self._recovery_observer.observe_failure(
            connection=provider.name, adapter=provider.adapter,
            scope=action.library,
            generation=self._configuration.provider_recovery_generation,
            failure="authentication", now=self._clock(),
        )
        return ReviewPipelineError("authentication", root=error.provider)

    def providers(
        self, pipeline_repository: ReviewPipelineRepository, report_id: str
    ) -> tuple[ProviderStatus, ...]:
        value = MetadataReport.from_json(
            cast("ReviewStageRepository", pipeline_repository).get(report_id)
        )
        record_provider_recovery_observations(
            self._recovery_observer, value,
            adapters={
                provider.name: provider.adapter
                for provider in self._configuration.providers
            },
            scope=value.target.library,
            generation=self._configuration.provider_recovery_generation,
            now=self._clock(),
        )
        return provider_statuses(value)

    def propose(
        self, pipeline_repository: ReviewPipelineRepository, report_id: str
    ) -> str:
        try:
            serialized = self._create_proposal_set(report_id, pipeline_repository)
        except ProposalCurrentnessError as error:
            raise ReviewPipelineError("stale") from error
        return ProposalSet.from_json(serialized).proposal_set_id

    def admit(
        self, pipeline_repository: ReviewPipelineRepository, proposal_set_id: str
    ) -> tuple[str, str | None]:
        result = ReviewAdmissionResult.from_json(
            self._admit_review_case(proposal_set_id, pipeline_repository)
        )
        if result.status == "stale":
            raise ReviewPipelineError("stale")
        return result.status, result.review_case_id

    def project(
        self, pipeline_repository: ReviewPipelineRepository,
        action: PipelineAction | str,
    ) -> str:
        try:
            if type(action) is str:
                outcome = pipeline_repository.persist_shadow_outcome(
                    action, self._clock()
                )
                return "stale" if outcome == "stale" else "complete"
            # The branch above already returned for an exact ``str``; mypy keeps
            # ``str`` in the union here only because a ``str`` subclass would
            # fail that exact test, which this seam never supplies.
            staged = cast("PipelineAction", action)
            if staged.review_case_id is None:
                raise ReviewPipelineError("state")
            outcome = pipeline_repository.persist_shadow_outcome(
                staged.review_case_id, self._clock(), staged_action=staged
            )
            if outcome == "stale":
                return "stale"
            return "complete"
        except Exception as error:
            # Projection spans the untyped pipeline repository, the shadow
            # outcome write, and the staged-action narrowing above, none of
            # which declares what it raises. Every one of them means the same
            # thing to the pipeline -- this package was not projected -- and
            # ``projection`` is not retryable, so nothing is retried on a fault
            # this layer cannot name.
            raise ReviewPipelineError("projection") from error

    def prepare_cover(
        self, pipeline_repository: ReviewPipelineRepository, action: PipelineAction
    ) -> None:
        if action.review_case_id is None:
            return
        try:
            def snapshot() -> tuple[str, str, bool] | None:
                return cast(
                    "ReviewStageRepository", pipeline_repository
                ).read_staged_cover_preparation(action)

            if snapshot() is None:
                return
            considerations = self._cover_considerations(pipeline_repository)
            prepare_cover_consideration(
                None,
                considerations,
                action.review_case_id,
                serialized=lambda call: call(),
                snapshot=snapshot,
                # The adapter's gate is not written generically, so it cannot
                # structurally satisfy the return-type-preserving protocol.
                finalize=cast(
                    "SerializedGate",
                    cast(
                        "ReviewStageRepository", pipeline_repository
                    ).atomic_cover_preparation,
                ),
            )
        except ReviewAdmissionStaleError as error:
            raise ReviewPipelineError("stale") from error
        except ReviewApplicationError as error:
            raise ReviewPipelineError(error.code) from error
        except Exception as error:
            # Last resort over the whole cover stage. Staleness and the
            # application's own codes are carried through above, so what
            # remains is a cover preparation that failed for a reason nothing
            # here can name -- from the untyped repository seam, from the
            # injected consideration factory, or from the application's own
            # preparation between them, none of which this handler can tell
            # apart. ``persistence`` is the code this stage answers with in
            # that case rather than a finding about which of the three failed.
            # It must still raise: an unprepared cover cannot be advanced past.
            raise ReviewPipelineError("persistence") from error

    def after_advance(
        self, pipeline_repository: ReviewPipelineRepository, action: PipelineAction,
        output_id: str | None,
    ) -> None:
        """Drain the durable cutover outbox after every final advancement."""
        if not (
            action.stage == "projection"
            or (action.stage == "admission" and output_id is None)
        ) or action.report_id is None:
            return
        self._drain_outbox(pipeline_repository)

    def check_connection(self, connection: str) -> None:
        if connection not in self._connections:
            raise ReviewPipelineError("library")
        self._probe_connection(
            connection,
            tuple(
                value for value in self._configuration.libraries
                if value.connection == connection
            ),
        )
