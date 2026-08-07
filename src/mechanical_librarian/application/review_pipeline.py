from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.domain.metadata_report import ProviderStatus

__all__ = [
    "PipelineAction",
    "PipelineProgress",
    "ReviewPipelineError",
    "ReviewPipelineRepository",
    "ReviewPreparationPipeline",
    "ReviewPreparationSupervisor",
]


_STAGES = (
    "discovery",
    "extraction",
    "provider",
    "proposal",
    "admission",
    "projection",
)
_TECHNICAL_PROVIDER_FAILURES = frozenset(
    {
        ProviderStatus.AUTHENTICATION,
        ProviderStatus.AUTHORIZATION,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.INCOMPLETE,
        ProviderStatus.MALFORMED,
        ProviderStatus.OVERSIZED,
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
        ProviderStatus.QUERY_LIMIT_EXCEEDED,
    }
)
_TRANSIENT_PROVIDER_FAILURES = frozenset(
    {ProviderStatus.RATE_LIMITED, ProviderStatus.UNAVAILABLE}
)
_CONFIGURATION_PROVIDER_FAILURES = frozenset(
    {ProviderStatus.AUTHENTICATION, ProviderStatus.AUTHORIZATION}
)


def _provider_failure_disposition(
    statuses: tuple[ProviderStatus, ...],
) -> tuple[str, bool] | None:
    failures = _TECHNICAL_PROVIDER_FAILURES.intersection(statuses)
    if _CONFIGURATION_PROVIDER_FAILURES.intersection(failures):
        return "configuration", False
    if _TRANSIENT_PROVIDER_FAILURES.intersection(failures):
        return "transient", True
    if failures:
        return "provider", False
    return None


class ReviewPipelineError(RuntimeError):
    """A fixed, value-free review-preparation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PipelineProgress:
    scan_id: str
    connection: str
    library: str
    state: str
    checked: int
    total: int
    ready: int
    blocked: int
    rescan_requested: bool
    updated_at: int = 0


@dataclass(frozen=True, slots=True)
class PipelineAction:
    action_id: str
    scan_id: str
    stage: str
    attempt: int
    connection: str
    library: str
    library_id: str
    producer_fingerprint: str
    catalogue_generation: int | None = None
    catalogue_fingerprint: str | None = None
    item_id: str | None = None
    file_identity: str | None = None
    source_revision: int | None = None
    report_id: str | None = None
    proposal_set_id: str | None = None
    review_case_id: str | None = None
    source_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if (
            self.stage not in _STAGES
            or type(self.attempt) is not int
            or self.attempt < 1
            or type(self.producer_fingerprint) is not str
            or len(self.producer_fingerprint) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.producer_fingerprint
            )
        ):
            raise ReviewPipelineError("state")
        if self.stage != "discovery" and (
            self.item_id is None
            or self.file_identity is None
            or self.source_revision is None
            or type(self.source_revision) is not int
            or self.source_revision < 0
            or self.catalogue_generation is None
            or self.catalogue_fingerprint is None
            or type(self.source_fingerprint) is not str
            or len(self.source_fingerprint) != 64
            or any(value not in "0123456789abcdef" for value in self.source_fingerprint)
        ):
            raise ReviewPipelineError("state")


class ReviewPipelineRepository(Protocol):
    def request_review_scan(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
    ) -> PipelineProgress: ...

    def review_pipeline_progress(
        self, connection: str, library: str
    ) -> PipelineProgress | None: ...

    def review_pipeline_status(
        self, libraries: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[PipelineProgress | None, ...], bool]: ...

    def recover_review_pipeline(
        self, producers: tuple[tuple[str, str, str, str], ...]
    ) -> None: ...

    def review_pipeline_paused(self) -> bool: ...

    def set_review_pipeline_paused(self, paused: bool, updated_at: int) -> None: ...

    def claim_review_pipeline_action(self, claimed_at: int) -> PipelineAction | None: ...

    def begin_review_pipeline_output(self) -> None: ...

    def rollback_review_pipeline_output(self) -> None: ...

    def complete_review_discovery(
        self,
        action: PipelineAction,
        generation: int,
        fingerprint: str,
        entries: tuple[CatalogueEntry, ...],
        completed_at: int,
    ) -> None: ...

    def advance_review_pipeline_action(
        self,
        action: PipelineAction,
        output_id: str | None,
        completed_at: int,
        claim_next: bool = False,
    ) -> PipelineAction | None: ...

    def fail_review_pipeline_action(
        self,
        action: PipelineAction,
        code: str,
        retryable: bool,
        completed_at: int,
    ) -> None: ...

    def close(self) -> None: ...


class ReviewPreparationPipeline:
    """Advance one persisted, exact-current review-preparation action at a time."""

    def __init__(
        self,
        *,
        repository_factory: Callable[[], ReviewPipelineRepository],
        libraries: tuple[tuple[str, str, str, str], ...],
        discover: Callable[
            [str, str], tuple[int, str, tuple[CatalogueEntry, ...]]
        ],
        extract: Callable[
            [ReviewPipelineRepository, PipelineAction], tuple[str, bool]
        ],
        providers: Callable[
            [ReviewPipelineRepository, str], tuple[ProviderStatus, ...]
        ],
        propose: Callable[[ReviewPipelineRepository, str], str],
        admit: Callable[
            [ReviewPipelineRepository, str], tuple[str, str | None]
        ],
        project: Callable[[ReviewPipelineRepository, str], bool],
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        if not libraries or len(libraries) != len(set(libraries)):
            raise ReviewPipelineError("configuration")
        self._repository_factory = repository_factory
        self._libraries = {
            (connection, library): (library_id, producer_fingerprint)
            for connection, library, library_id, producer_fingerprint in libraries
        }
        self._discover = discover
        self._extract = extract
        self._providers = providers
        self._propose = propose
        self._admit = admit
        self._project = project
        self._clock = clock

    @property
    def libraries(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (connection, library, values[0])
            for (connection, library), values in sorted(self._libraries.items())
        )

    def _open_repository(self) -> ReviewPipelineRepository:
        try:
            return self._repository_factory()
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error

    def request(self, connection: str, library: str) -> PipelineProgress:
        selected = self._libraries.get((connection, library))
        if selected is None:
            raise ReviewPipelineError("library")
        library_id, producer_fingerprint = selected
        repository = self._open_repository()
        try:
            try:
                return repository.request_review_scan(
                    connection,
                    library,
                    library_id,
                    producer_fingerprint,
                    self._clock(),
                )
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def progress(self, connection: str, library: str) -> PipelineProgress | None:
        if (connection, library) not in self._libraries:
            raise ReviewPipelineError("library")
        repository = self._open_repository()
        try:
            try:
                return repository.review_pipeline_progress(connection, library)
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def status(self) -> tuple[tuple[PipelineProgress | None, ...], bool]:
        libraries = tuple(sorted(self._libraries))
        repository = self._open_repository()
        try:
            try:
                return repository.review_pipeline_status(libraries)
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def recover(self) -> None:
        repository = self._open_repository()
        try:
            try:
                repository.recover_review_pipeline(
                    tuple(
                        (connection, library, values[0], values[1])
                        for (connection, library), values in sorted(
                            self._libraries.items()
                        )
                    )
                )
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def paused(self) -> bool:
        repository = self._open_repository()
        try:
            try:
                return repository.review_pipeline_paused()
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def set_paused(self, paused: bool) -> None:
        repository = self._open_repository()
        try:
            try:
                repository.set_review_pipeline_paused(paused, self._clock())
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def run_next(self, maximum_actions: int = 64) -> bool:
        if type(maximum_actions) is not int or not 1 <= maximum_actions <= 64:
            raise ReviewPipelineError("configuration")
        repository = self._open_repository()
        try:
            try:
                action = repository.claim_review_pipeline_action(self._clock())
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
            if action is None:
                return False
            for index in range(maximum_actions):
                next_action = self._run_claimed_action(
                    repository,
                    action,
                    claim_next=index + 1 < maximum_actions,
                )
                if next_action is None:
                    return True
                action = next_action
            return True
        finally:
            repository.close()

    def _run_claimed_action(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        *,
        claim_next: bool,
    ) -> PipelineAction | None:
        output_transaction = False
        try:
            output_id: str | None = None
            if action.stage == "discovery":
                generation, fingerprint, entries = self._discover(
                    action.connection, action.library
                )
                self._complete_discovery(
                    repository, action, generation, fingerprint, entries
                )
                return None
            if action.stage == "extraction":
                output_id, checkpoint_committed = self._extract(
                    repository, action
                )
                if checkpoint_committed:
                    return None
            elif action.stage == "provider":
                if action.report_id is None:
                    raise ReviewPipelineError("state")
                statuses = self._providers(repository, action.report_id)
                disposition = _provider_failure_disposition(statuses)
                if disposition is not None:
                    self._fail(repository, action, *disposition)
                    return None
            elif action.stage == "proposal":
                if action.report_id is None:
                    raise ReviewPipelineError("state")
                repository.begin_review_pipeline_output()
                output_transaction = True
                output_id = self._propose(repository, action.report_id)
            elif action.stage == "admission":
                if action.proposal_set_id is None:
                    raise ReviewPipelineError("state")
                repository.begin_review_pipeline_output()
                output_transaction = True
                _, output_id = self._admit(repository, action.proposal_set_id)
            elif action.stage == "projection":
                if action.review_case_id is not None and not self._project(
                    repository, action.review_case_id
                ):
                    raise ReviewPipelineError("projection")
            return self._advance(
                repository,
                action,
                output_id,
                claim_next=claim_next,
            )
        except ReviewPipelineError as error:
            if output_transaction:
                repository.rollback_review_pipeline_output()
            self._fail(
                repository,
                action,
                error.code,
                error.code in {"stale", "transient"},
            )
        except Exception:
            if output_transaction:
                repository.rollback_review_pipeline_output()
            self._fail(repository, action, "failed", False)
        return None

    def _complete_discovery(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        generation: int,
        fingerprint: str,
        entries: tuple[CatalogueEntry, ...],
    ) -> None:
        try:
            repository.complete_review_discovery(
                action, generation, fingerprint, entries, self._clock()
            )
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error

    def _advance(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        output_id: str | None,
        *,
        claim_next: bool,
    ) -> PipelineAction | None:
        try:
            return repository.advance_review_pipeline_action(
                action, output_id, self._clock(), claim_next
            )
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error

    def _fail(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        code: str,
        retryable: bool,
    ) -> None:
        try:
            repository.fail_review_pipeline_action(
                action, code, retryable, self._clock()
            )
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error


class ReviewPreparationSupervisor:
    """Run the fixed two-lane persisted pipeline and wake it after owner requests."""

    def __init__(
        self,
        pipeline: ReviewPreparationPipeline,
        *,
        polling_interval_seconds: int | None = None,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        if polling_interval_seconds is not None and (
            type(polling_interval_seconds) is not int
            or not 60 <= polling_interval_seconds <= 86_400
        ):
            raise ReviewPipelineError("configuration")
        self._pipeline = pipeline
        self._polling_interval_seconds = polling_interval_seconds
        self._clock = clock
        self._schedule_lock = threading.Lock()
        self._next_schedule_at: int | None = None
        self._schedule_paused = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: tuple[threading.Thread, ...] = ()

    @property
    def libraries(self) -> tuple[tuple[str, str, str], ...]:
        return self._pipeline.libraries

    def start(self) -> None:
        if self._threads:
            return
        self._pipeline.recover()
        self._schedule_due()
        self._threads = tuple(
            threading.Thread(
                target=self._run,
                args=(lane,),
                name=f"review-preparation-{lane}",
                daemon=True,
            )
            for lane in range(2)
        )
        for thread in self._threads:
            thread.start()
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = ()

    def request(self, connection: str, library: str) -> PipelineProgress:
        if self._pipeline.paused():
            raise ReviewPipelineError("paused")
        progress = self._pipeline.request(connection, library)
        self._wake.set()
        return progress

    def progress(self, connection: str, library: str) -> PipelineProgress | None:
        return self._pipeline.progress(connection, library)

    def status(self) -> tuple[tuple[PipelineProgress | None, ...], bool]:
        return self._pipeline.status()

    def paused(self) -> bool:
        return self._pipeline.paused()

    def set_paused(self, paused: bool) -> None:
        self._pipeline.set_paused(paused)
        with self._schedule_lock:
            self._schedule_paused = paused
            if not paused:
                self._next_schedule_at = None
        if not paused:
            self._schedule_due()
            self._wake.set()

    def retry(self, connection: str, library: str) -> PipelineProgress:
        progress = self.progress(connection, library)
        if progress is None or progress.state != "completed_with_failures":
            raise ReviewPipelineError("retry")
        return self.request(connection, library)

    def _schedule_due(self) -> None:
        with self._schedule_lock:
            if self._polling_interval_seconds is None:
                return
            if self._schedule_paused:
                return
            now = self._clock()
            if self._next_schedule_at is not None and now < self._next_schedule_at:
                return
            progresses, paused = self._pipeline.status()
            if paused:
                self._schedule_paused = True
                self._next_schedule_at = None
                return
            next_schedule_at: int | None = None
            for (connection, library, _), progress in zip(
                self.libraries, progresses, strict=True
            ):
                if progress is None or (
                    progress.state == "completed"
                    and now - progress.updated_at >= self._polling_interval_seconds
                ):
                    self._pipeline.request(connection, library)
                    next_schedule_at = now + 1
                elif progress.state in {"queued", "discovering", "processing"}:
                    next_schedule_at = now + 1
                elif progress.state == "completed":
                    due_at = progress.updated_at + self._polling_interval_seconds
                    next_schedule_at = (
                        due_at
                        if next_schedule_at is None
                        else min(next_schedule_at, due_at)
                    )
            self._next_schedule_at = next_schedule_at

    def _run(self, lane: int) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1)
            self._wake.clear()
            try:
                if lane == 0:
                    self._schedule_due()
                while not self._stop.is_set() and self._pipeline.run_next():
                    pass
            except ReviewPipelineError:
                continue


def pipeline_scan_id(connection: str, library: str) -> str:
    material = f"review-preparation-v1\0{connection}\0{library}"
    return "review_scan_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
