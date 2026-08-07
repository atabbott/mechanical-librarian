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

    def recover_review_pipeline(
        self, producers: tuple[tuple[str, str, str, str], ...]
    ) -> None: ...

    def claim_review_pipeline_action(self, claimed_at: int) -> PipelineAction | None: ...

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
    ) -> None: ...

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
        extract: Callable[[PipelineAction], tuple[str, bool]],
        providers: Callable[[str], tuple[ProviderStatus, ...]],
        propose: Callable[[str], str],
        admit: Callable[[str], tuple[str, str | None]],
        project: Callable[[str], bool],
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

    def run_next(self) -> bool:
        repository = self._open_repository()
        try:
            try:
                action = repository.claim_review_pipeline_action(self._clock())
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()
        if action is None:
            return False
        try:
            output_id: str | None = None
            if action.stage == "discovery":
                generation, fingerprint, entries = self._discover(
                    action.connection, action.library
                )
                self._complete_discovery(action, generation, fingerprint, entries)
                return True
            if action.stage == "extraction":
                output_id, checkpoint_committed = self._extract(action)
                if checkpoint_committed:
                    return True
            elif action.stage == "provider":
                if action.report_id is None:
                    raise ReviewPipelineError("state")
                statuses = self._providers(action.report_id)
                failures = _TECHNICAL_PROVIDER_FAILURES.intersection(statuses)
                if failures:
                    self._fail(action, "provider", False)
                    return True
            elif action.stage == "proposal":
                if action.report_id is None:
                    raise ReviewPipelineError("state")
                output_id = self._propose(action.report_id)
            elif action.stage == "admission":
                if action.proposal_set_id is None:
                    raise ReviewPipelineError("state")
                _, output_id = self._admit(action.proposal_set_id)
            elif action.stage == "projection":
                if action.review_case_id is not None and not self._project(
                    action.review_case_id
                ):
                    raise ReviewPipelineError("projection")
            self._advance(action, output_id)
        except ReviewPipelineError as error:
            self._fail(action, error.code, error.code in {"stale", "transient"})
        except Exception:
            self._fail(action, "failed", False)
        return True

    def _complete_discovery(
        self,
        action: PipelineAction,
        generation: int,
        fingerprint: str,
        entries: tuple[CatalogueEntry, ...],
    ) -> None:
        repository = self._open_repository()
        try:
            try:
                repository.complete_review_discovery(
                    action, generation, fingerprint, entries, self._clock()
                )
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def _advance(self, action: PipelineAction, output_id: str | None) -> None:
        repository = self._open_repository()
        try:
            try:
                repository.advance_review_pipeline_action(
                    action, output_id, self._clock()
                )
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def _fail(self, action: PipelineAction, code: str, retryable: bool) -> None:
        repository = self._open_repository()
        try:
            try:
                repository.fail_review_pipeline_action(
                    action, code, retryable, self._clock()
                )
            except ReviewPipelineError:
                raise
            except Exception as error:
                raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()


class ReviewPreparationSupervisor:
    """Run the fixed two-lane persisted pipeline and wake it after owner requests."""

    def __init__(self, pipeline: ReviewPreparationPipeline) -> None:
        self._pipeline = pipeline
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
        self._threads = tuple(
            threading.Thread(
                target=self._run,
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
        progress = self._pipeline.request(connection, library)
        self._wake.set()
        return progress

    def progress(self, connection: str, library: str) -> PipelineProgress | None:
        return self._pipeline.progress(connection, library)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1)
            self._wake.clear()
            try:
                while not self._stop.is_set() and self._pipeline.run_next():
                    pass
            except ReviewPipelineError:
                continue


def pipeline_scan_id(connection: str, library: str) -> str:
    material = f"review-preparation-v1\0{connection}\0{library}"
    return "review_scan_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
