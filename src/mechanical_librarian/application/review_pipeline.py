from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from mechanical_librarian.application.catalogue import (
    CatalogueEntry,
    TargetConnectionError,
)
from mechanical_librarian.application.metadata_report import CredentialError
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.domain.epub_inspection import EpubInspectionOutcome
from mechanical_librarian.domain.metadata_report import ProviderStatus

__all__ = [
    "PipelineAction",
    "PipelineProgress",
    "ReviewPipelineError",
    "ReviewPipelineRepository",
    "ReviewPreparationPipeline",
    "ReviewPreparationSupervisor",
    "TargetConnectionCondition",
    "preparation_failure_reference",
    "inspection_failure_code",
]


_STAGES = (
    "discovery",
    "extraction",
    "provider",
    "proposal",
    "admission",
    "projection",
)


_ForegroundResult = TypeVar("_ForegroundResult")


def _unconfigured_connection_check(_connection: str) -> None:
    # Direct pipeline construction remains a pure test/application seam. The
    # composed product always supplies the adapter-owned probe.
    return None


class ReviewPipelineError(RuntimeError):
    """A fixed, value-free review-preparation failure."""

    def __init__(self, code: str, *, root: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.root = root


def preparation_failure_reference(
    action_id: str, stage: str, attempt: int, recovery_attempt: int, code: str
) -> str:
    """Return a durable, value-free terminal preparation reference."""
    if (
        type(action_id) is not str
        or type(stage) is not str
        or type(code) is not str
        or type(attempt) is not int
        or type(recovery_attempt) is not int
    ):
        raise ReviewPipelineError("state")
    material = "\0".join(
        (
            "review-preparation-failure-v1",
            action_id,
            stage,
            str(attempt),
            str(recovery_attempt),
            code,
        )
    )
    return "prep-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def inspection_failure_code(outcome: EpubInspectionOutcome) -> str:
    if outcome in {
        EpubInspectionOutcome.SUCCESS,
        EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
    }:
        return "success"
    if outcome in {
        EpubInspectionOutcome.UNSUPPORTED_FORMAT,
        EpubInspectionOutcome.UNSUPPORTED_VERSION,
        EpubInspectionOutcome.UNSUPPORTED_COMPRESSION,
        EpubInspectionOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS,
        EpubInspectionOutcome.ENCRYPTED,
    }:
        return "unsupported_media"
    if outcome in {
        EpubInspectionOutcome.INTEGRITY_FAILURE,
        EpubInspectionOutcome.UNSTABLE_SOURCE,
        EpubInspectionOutcome.UNAVAILABLE_SOURCE,
    }:
        return "unreadable_media"
    if outcome in {
        EpubInspectionOutcome.INVALID_REFERENCE,
        EpubInspectionOutcome.MALFORMED,
        EpubInspectionOutcome.RESOURCE_LIMIT,
        EpubInspectionOutcome.TIMEOUT,
        EpubInspectionOutcome.CANCELLATION,
    }:
        return "parser_refused"
    if outcome in {
        EpubInspectionOutcome.INVALID_INVOCATION,
        EpubInspectionOutcome.INVALID_CONFIGURATION,
        EpubInspectionOutcome.CHILD_FAILURE,
        EpubInspectionOutcome.INTERNAL_FAILURE,
    }:
        return "internal_failure"
    raise ReviewPipelineError("state")


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
    stages: tuple[tuple[str, int], ...] = ()
    """How many works stand at each preparation stage, in owner order.

    `checked`, `ready` and `blocked` answer "how far through", which is what a
    progress bar needs and all an owner had. They cannot answer "what is it
    doing", which is what an owner watching a library prepare for days actually
    asks, and answering it is #641's first acceptance. The stage names are the
    persisted pipeline's own; the owner-facing wording is chosen at the surface,
    because the two vocabularies change for different reasons.
    """


@dataclass(frozen=True, slots=True)
class TargetConnectionCondition:
    """The value-free durable condition for one configured target connection."""

    connection: str
    code: str
    state: str
    epoch: int
    affected: int
    updated_at: int


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
    lineage_checkpoint: int | None = None
    policy_generation: int | None = None

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
        if self.lineage_checkpoint is not None and (
            type(self.lineage_checkpoint) is not int or self.lineage_checkpoint < 1
        ):
            raise ReviewPipelineError("state")
        if self.policy_generation is not None and (
            type(self.policy_generation) is not int or self.policy_generation < 1
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

    def target_connection_conditions(self) -> tuple[TargetConnectionCondition, ...]: ...
    def guard_target_connection(
        self, action: PipelineAction, checked_at: int
    ) -> bool: ...
    def discovery_requires_qualification(self, action: PipelineAction) -> bool: ...
    def qualify_review_discovery(
        self, action: PipelineAction, qualified_at: int
    ) -> bool: ...
    def defer_for_target_connection(
        self, action: PipelineAction, code: str, updated_at: int
    ) -> None: ...
    def hold_target_connection(
        self, connection: str, code: str, updated_at: int
    ) -> None: ...
    def begin_target_connection_check(
        self, connection: str, checked_at: int
    ) -> TargetConnectionCondition | None: ...
    def finish_target_connection_check(
        self, connection: str, epoch: int, code: str | None, checked_at: int
    ) -> bool: ...

    def claim_review_pipeline_action(
        self, claimed_at: int
    ) -> PipelineAction | None: ...
    def claim_review_pipeline_work(
        self, work_id: str, claimed_at: int
    ) -> PipelineAction | None: ...
    def provider_retry_work_id(self, report_id: str) -> str: ...
    def provider_retry_complete(self, report_id: str) -> bool: ...
    def completed_provider_retry_successor(
        self, source_report_id: str
    ) -> str | None: ...

    def take_review_pipeline_claim(self) -> PipelineAction | None: ...

    def begin_review_pipeline_output(self) -> None: ...

    def rollback_review_pipeline_output(self) -> None: ...

    def persist_shadow_outcome(
        self,
        review_case_id: str,
        evaluated_at: int,
        *,
        staged_action: PipelineAction | None = None,
    ) -> str: ...

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
        exact_next: bool = False,
    ) -> PipelineAction | None: ...

    def fail_review_pipeline_action(
        self,
        action: PipelineAction,
        code: str,
        retryable: bool,
        completed_at: int,
        failure_root: str | None = None,
    ) -> None: ...

    def close(self) -> None: ...


class ReviewPreparationPipeline:
    """Advance one persisted, exact-current review-preparation action at a time.

    ``ReviewPipelineRepository`` is a protocol with no declared error type, and
    the composition root binds it to a repository this layer never names, so no
    call on it has an enumerable raisable set. Where a repository call site
    below converts at all it follows one shape: re-raise ``ReviewPipelineError``
    untouched, so a code the repository or a stage already chose is never
    overwritten, then convert the remainder to
    ``ReviewPipelineError("persistence")`` with the cause chained. The two
    handlers that do something other than convert -- the connection probe and
    the claimed-action epilogue -- record their own invariant where they stand.

    The shape is now universal. ``check_connection``'s two durable transitions,
    ``hold_connection`` and ``run_recovery_successor`` held the repository open
    under a bare ``try/finally``: they released the handle on every path but
    converted nothing, so the bound repository's own error reached their caller
    untyped. Each now converts like the rest, and the ``finally`` still releases
    the handle. None of the three needed it to keep a lane alive -- the two
    recovery callers run inside a handler that classifies every failure, and the
    check is owner-triggered -- so the conversion is owed to the seam's contract
    rather than to a thread, and no caller's outcome changes: the recovery
    classifier already treated a raw fault and a ``ReviewPipelineError`` alike,
    and the web boundary already answered ``persistence`` for both.
    """

    def __init__(
        self,
        *,
        repository_factory: Callable[[], ReviewPipelineRepository],
        observation_repository_factory: Callable[[], ReviewPipelineRepository]
        | None = None,
        libraries: tuple[tuple[str, str, str, str], ...],
        discover: Callable[[str, str], tuple[int, str, tuple[CatalogueEntry, ...]]],
        extract: Callable[
            [ReviewPipelineRepository, PipelineAction, bool], tuple[str, bool]
        ],
        providers: Callable[
            [ReviewPipelineRepository, str], tuple[ProviderStatus, ...]
        ],
        propose: Callable[[ReviewPipelineRepository, str], str],
        admit: Callable[[ReviewPipelineRepository, str], tuple[str, str | None]],
        project: Callable[[ReviewPipelineRepository, PipelineAction], bool | str],
        before_projection: Callable[
            [ReviewPipelineRepository, PipelineAction], None
        ] = (lambda *_args: None),
        after_advance: Callable[
            [ReviewPipelineRepository, PipelineAction, str | None], None
        ] = lambda *_args: None,
        check_connection: Callable[[str], None] = _unconfigured_connection_check,
        clock: Callable[[], int] = lambda: int(time.time()),
        timing: StageTimer | None = None,
        announce: Callable[[str, str | None], object] = lambda *_args: None,
    ) -> None:
        if not libraries or len(libraries) != len(set(libraries)):
            raise ReviewPipelineError("configuration")
        self._repository_factory = repository_factory
        self._observation_repository_factory = (
            repository_factory
            if observation_repository_factory is None
            else observation_repository_factory
        )
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
        self._before_projection = before_projection
        self._after_advance = after_advance
        self._check_connection = check_connection
        self._clock = clock
        self._announce = announce
        self._timing = timing or StageTimer(None)
        self._draining = threading.Event()
        self._claim_lock = threading.Lock()
        self._observation_lock = threading.RLock()
        self._observation_repository: ReviewPipelineRepository | None = None

    @property
    def libraries(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (connection, library, values[0])
            for (connection, library), values in sorted(self._libraries.items())
        )

    def attach_updates(self, announce: Callable[[str, str | None], object]) -> None:
        """Bind the invalidation channel this pipeline announces stages on."""
        self._announce = announce

    def _open_repository(self) -> ReviewPipelineRepository:
        try:
            return self._repository_factory()
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error

    def _with_foreground_repository(
        self, call: Callable[[ReviewPipelineRepository], _ForegroundResult]
    ) -> _ForegroundResult:
        """Serialize one foreground snapshot or control on the lifecycle handle."""
        with self._observation_lock:
            repository = self._observation_repository
            if repository is not None:
                try:
                    return call(repository)
                except ReviewPipelineError:
                    raise
                except Exception as error:
                    raise ReviewPipelineError("persistence") from error
            repository = self._open_repository()
            try:
                try:
                    return call(repository)
                except ReviewPipelineError:
                    raise
                except Exception as error:
                    raise ReviewPipelineError("persistence") from error
            finally:
                repository.close()

    def request(self, connection: str, library: str) -> PipelineProgress:
        selected = self._libraries.get((connection, library))
        if selected is None:
            raise ReviewPipelineError("library")
        library_id, producer_fingerprint = selected
        progress = self._with_foreground_repository(
            lambda repository: repository.request_review_scan(
                connection,
                library,
                library_id,
                producer_fingerprint,
                self._clock(),
            )
        )
        self._announce("preparation", None)
        return progress

    def progress(self, connection: str, library: str) -> PipelineProgress | None:
        if (connection, library) not in self._libraries:
            raise ReviewPipelineError("library")
        return self._with_foreground_repository(
            lambda repository: repository.review_pipeline_progress(connection, library)
        )

    def status(self) -> tuple[tuple[PipelineProgress | None, ...], bool]:
        libraries = tuple(sorted(self._libraries))
        return self._with_foreground_repository(
            lambda repository: repository.review_pipeline_status(libraries)
        )

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
        self._announce("preparation", None)

    def connection_conditions(self) -> tuple[TargetConnectionCondition, ...]:
        return self._with_foreground_repository(
            lambda repository: repository.target_connection_conditions()
        )

    def open_observation(self) -> None:
        """Validate the foreground preparation reader before workers start."""
        with self._observation_lock:
            if self._observation_repository is None:
                try:
                    self._observation_repository = (
                        self._observation_repository_factory()
                    )
                except ReviewPipelineError:
                    raise
                except Exception as error:
                    raise ReviewPipelineError("persistence") from error

    def close_observation(self) -> None:
        """Release the foreground reader after the preparation lanes stop."""
        with self._observation_lock:
            if self._observation_repository is not None:
                self._observation_repository.close()
                self._observation_repository = None

    def check_connection(self, connection: str) -> bool:
        """One owner-triggered, epoch-bound connection check.

        A duplicate click while checking is intentionally a no-op.  A stale
        checker cannot clear a later failure because finish compares its epoch.
        """
        # The durable begin/finish transitions are deliberately separate from
        # the external probe.  Holding a SQLite connection (or the web
        # application's serialization gate) across network I/O would make an
        # owner check globally disruptive.
        repository = self._open_repository()
        try:
            condition = repository.begin_target_connection_check(
                connection, self._clock()
            )
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()
        if condition is None:
            return False
        code: str | None = None
        try:
            self._check_connection(connection)
        except TargetConnectionError as error:
            code = error.code
        except Exception:
            # A probe that did not produce a closed successful observation
            # must never strand this connection in checking.
            code = "unavailable"
        repository = self._open_repository()
        try:
            repository.finish_target_connection_check(
                connection, condition.epoch, code, self._clock()
            )
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()
        self._announce("preparation", None)
        return True

    def hold_connection(self, connection: str, code: str) -> None:
        """Record a retry-successor target observation as a connection hold."""
        repository = self._open_repository()
        try:
            repository.hold_target_connection(connection, code, self._clock())
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()
        self._announce("preparation", None)

    def begin_drain(self) -> None:
        """Finish the current claim without admitting another one."""
        with self._claim_lock:
            self._draining.set()

    def resume_claiming(self) -> None:
        self._draining.clear()

    def run_next(self, maximum_actions: int = 64) -> bool:
        if type(maximum_actions) is not int or not 1 <= maximum_actions <= 64:
            raise ReviewPipelineError("configuration")
        repository = self._open_repository()
        try:
            with self._claim_lock:
                if self._draining.is_set():
                    return False
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
                    claim_next=(
                        index + 1 < maximum_actions and not self._draining.is_set()
                    ),
                )
                if next_action is None:
                    return True
                action = next_action
            return True
        finally:
            repository.close()

    def run_recovery_successor(
        self, report_id: str, heartbeat: Callable[[], None] = lambda: None
    ) -> bool:
        """Complete one staged successor through ordinary F22 stages."""
        if not callable(heartbeat):
            raise ReviewPipelineError("state")
        heartbeat()
        repository = self._open_repository()
        try:
            work_id = repository.provider_retry_work_id(report_id)
            action = repository.claim_review_pipeline_work(work_id, self._clock())
            if action is None:
                return repository.provider_retry_complete(report_id)
            for _index in range(4):
                if action is None:
                    return False
                heartbeat()
                action = self._run_claimed_action(
                    repository,
                    action,
                    claim_next=False,
                    exact_next=True,
                )
                heartbeat()
                if action is None:
                    return repository.provider_retry_complete(report_id)
            return False
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error
        finally:
            repository.close()

    def _run_claimed_action(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        *,
        claim_next: bool,
        exact_next: bool = False,
    ) -> PipelineAction | None:
        timing = {
            "proposal": ("policy_evaluation", "none"),
            "admission": ("policy_evaluation", "none"),
            "projection": ("policy_evaluation", "none"),
        }.get(action.stage)
        if timing is None:
            return self._execute_claimed_action(
                repository,
                action,
                claim_next=claim_next,
                exact_next=exact_next,
            )
        with self._timing.measure(*timing):
            return self._execute_claimed_action(
                repository,
                action,
                claim_next=claim_next,
                exact_next=exact_next,
            )

    def _execute_claimed_action(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        *,
        claim_next: bool,
        exact_next: bool = False,
    ) -> PipelineAction | None:
        output_transaction = False
        try:
            # A connection can become paused after this worker obtained its
            # durable claim. Re-check before every target-dependent stage so
            # the claim returns to its retained checkpoint without an item
            # failure.
            guard = getattr(repository, "guard_target_connection", None)
            if guard is not None and not guard(action, self._clock()):
                return None
            output_id: str | None = None
            if action.stage == "discovery":
                requires_qualification = getattr(
                    repository, "discovery_requires_qualification", None
                )
                if requires_qualification is not None and requires_qualification(
                    action
                ):
                    self._check_connection(action.connection)
                    if not repository.qualify_review_discovery(action, self._clock()):
                        return None
                generation, fingerprint, entries = self._discover(
                    action.connection, action.library
                )
                self._complete_discovery(
                    repository, action, generation, fingerprint, entries
                )
                return None
            if action.stage == "extraction":
                output_id, checkpoint_committed = self._extract(
                    repository, action, claim_next
                )
                if checkpoint_committed:
                    return repository.take_review_pipeline_claim()
            elif action.stage == "provider":
                if action.report_id is None:
                    raise ReviewPipelineError("state")
                # The report already contains one bounded terminal observation
                # for every configured provider lane. Proposal admission, not a
                # sibling technical status, decides whether the complete report
                # can form a coherent package.
                self._providers(repository, action.report_id)
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
                if action.review_case_id is not None:
                    # Advisory cover review settles before F22's projection
                    # transaction, so a stale or persistence failure cannot
                    # publish a package without the required comparison.
                    self._before_projection(repository, action)
                    repository.begin_review_pipeline_output()
                    output_transaction = True
                    projection = self._project(repository, action)
                    if projection == "stale":
                        raise ReviewPipelineError("stale")
                    if projection is not True and projection != "complete":
                        raise ReviewPipelineError("projection")
            return self._advance(
                repository,
                action,
                output_id,
                claim_next=claim_next,
                exact_next=exact_next,
            )
        except ReviewPipelineError as error:
            if output_transaction:
                repository.rollback_review_pipeline_output()
            self._fail(
                repository,
                action,
                error.code,
                error.code in {"stale", "transient"},
                error.root,
            )
        except TargetConnectionError as error:
            # The action remains at its first uncommitted target-dependent
            # checkpoint.  This is deliberately distinct from provider and
            # item-currentness failures.
            if output_transaction:
                repository.rollback_review_pipeline_output()
            repository.defer_for_target_connection(action, error.code, self._clock())
        except CredentialError as error:
            if output_transaction:
                repository.rollback_review_pipeline_output()
            self._fail(
                repository,
                action,
                "authentication",
                False,
                error.provider,
            )
        except Exception:
            # Terminal epilogue for one claimed action. The stages above are
            # injected callables spanning the target, the providers, the media,
            # and the repository, so nothing here can enumerate what they
            # raise, and an action left claimed by an escaping fault would stall
            # its lane for every later work. ``failed`` is the honest code for
            # a cause this layer cannot name: it is not retryable, it does not
            # borrow a provider or currentness meaning, and the durable record
            # keeps the action visible for owner attention rather than
            # discarding it. The rollback runs first so no partial output
            # survives the failure.
            if output_transaction:
                repository.rollback_review_pipeline_output()
            self._fail(repository, action, "unknown", False)
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
        self._announce("preparation", None)

    def _advance(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        output_id: str | None,
        *,
        claim_next: bool,
        exact_next: bool = False,
    ) -> PipelineAction | None:
        try:
            if claim_next:
                with self._claim_lock:
                    advanced = repository.advance_review_pipeline_action(
                        action,
                        output_id,
                        self._clock(),
                        not self._draining.is_set(),
                    )
            elif exact_next:
                advanced = repository.advance_review_pipeline_action(
                    action,
                    output_id,
                    self._clock(),
                    False,
                    True,
                )
            else:
                advanced = repository.advance_review_pipeline_action(
                    action, output_id, self._clock(), False
                )
            try:
                self._after_advance(repository, action, output_id)
            except Exception:
                # The cutover is already durable even when its recovery outbox
                # delivery is not. Readers must refetch the normal committed
                # state before any claimed successor is settled.
                try:
                    self._announce_stage(action, output_id)
                finally:
                    # ``advance_review_pipeline_action`` can atomically claim
                    # the successor before the post-cutover outbox is delivered.
                    # A delivery fault must settle that successor now; leaving it
                    # running would consume one of the fixed lanes until restart.
                    if advanced is not None:
                        self._fail(repository, advanced, "persistence", True)
                return None
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error
        self._announce_stage(action, output_id)
        return advanced

    def _announce_stage(self, action: PipelineAction, output_id: str | None) -> None:
        """Name what a committed stage change invalidated, and nothing more.

        This runs after the durable transition, never inside it: a browser
        convenience must not be able to fail a stage. The announcement carries a
        topic and, for a published work, its review case identity -- never a
        count, a stage name, or any part of the package that just committed.

        `projection` reaching `complete`, and a non-actionable `admission`, are
        the two shapes of #597's atomic current-package cutover, which is the
        moment a work becomes browseable.
        """
        published = action.stage == "projection" or (
            action.stage == "admission" and output_id is None
        )
        self._announce("preparation", None)
        if not published:
            return
        review_case_id = (
            action.review_case_id
            if action.stage == "projection"
            else (output_id if output_id is not None else None)
        )
        self._announce("library", review_case_id)
        if review_case_id is not None:
            self._announce("case", review_case_id)

    def _fail(
        self,
        repository: ReviewPipelineRepository,
        action: PipelineAction,
        code: str,
        retryable: bool,
        failure_root: str | None = None,
    ) -> None:
        try:
            repository.fail_review_pipeline_action(
                action, code, retryable, self._clock(), failure_root
            )
        except ReviewPipelineError:
            raise
        except Exception as error:
            raise ReviewPipelineError("persistence") from error
        # A terminal failure is preparation state an owner acts on, and a work
        # that becomes visible as "could not finish", so both are announced.
        self._announce("preparation", None)
        self._announce("library", None)


class ReviewPreparationSupervisor:
    """Run the fixed two-lane persisted pipeline and wake it after owner requests."""

    def __init__(
        self,
        pipeline: ReviewPreparationPipeline,
        *,
        polling_interval_seconds: int | None = None,
        graceful_shutdown_seconds: int = 30,
        clock: Callable[[], int] = lambda: int(time.time()),
        recovery_wake: Callable[[int], None] | None = None,
        recovery_drain: Callable[[], None] | None = None,
        recovery_resume: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        if polling_interval_seconds is not None and (
            type(polling_interval_seconds) is not int
            or not 60 <= polling_interval_seconds <= 86_400
        ):
            raise ReviewPipelineError("configuration")
        if (
            type(graceful_shutdown_seconds) is not int
            or not 1 <= graceful_shutdown_seconds <= 300
        ):
            raise ReviewPipelineError("configuration")
        self._pipeline = pipeline
        self._polling_interval_seconds = polling_interval_seconds
        self._graceful_shutdown_seconds = graceful_shutdown_seconds
        self._clock = clock
        self._recovery_wake = recovery_wake
        self._recovery_drain = recovery_drain
        self._recovery_resume = recovery_resume
        self._schedule_lock = threading.Lock()
        self._next_schedule_at: int | None = None
        self._schedule_paused = False
        self._schedule_waiting_for_action = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: tuple[threading.Thread, ...] = ()
        self._on_stop = on_stop

    @property
    def libraries(self) -> tuple[tuple[str, str, str], ...]:
        return self._pipeline.libraries

    def attach_updates(self, announce: Callable[[str, str | None], object]) -> None:
        """Bind the invalidation channel the pipeline announces stages on."""
        self._pipeline.attach_updates(announce)

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        self._pipeline.open_observation()
        try:
            if self._recovery_resume is not None:
                self._recovery_resume()
            resume = getattr(self._pipeline, "resume_claiming", None)
            if resume is not None:
                resume()
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
        except Exception:
            self._pipeline.close_observation()
            raise

    def begin_drain(self) -> None:
        """Close claim admission and wake workers without waiting."""
        drain = getattr(self._pipeline, "begin_drain", None)
        if drain is not None:
            drain()
        if self._recovery_drain is not None:
            self._recovery_drain()
        self._stop.set()
        self._wake.set()

    def stop(self, deadline: float | None = None) -> None:
        self.begin_drain()
        if deadline is None:
            deadline = time.monotonic() + self._graceful_shutdown_seconds
        for thread in self._threads:
            thread.join(timeout=max(deadline - time.monotonic(), 0))
        active = tuple(thread for thread in self._threads if thread.is_alive())
        self._threads = active
        if active:
            raise ReviewPipelineError("shutdown_timeout")
        if self._on_stop is not None:
            self._on_stop()
            self._on_stop = None
        self._pipeline.close_observation()

    def request(self, connection: str, library: str) -> PipelineProgress:
        progress = self._pipeline.request(connection, library)
        with self._schedule_lock:
            self._schedule_waiting_for_action = False
            self._next_schedule_at = None
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
                self._schedule_waiting_for_action = False
                self._next_schedule_at = None
        if not paused:
            self._schedule_due()
            self._wake.set()

    def retry(self, connection: str, library: str) -> PipelineProgress:
        progress = self.progress(connection, library)
        if progress is None or progress.state != "completed_with_failures":
            raise ReviewPipelineError("retry")
        return self.request(connection, library)

    def connection_conditions(self) -> tuple[TargetConnectionCondition, ...]:
        return self._pipeline.connection_conditions()

    def check_connection(self, connection: str) -> bool:
        changed = self._pipeline.check_connection(connection)
        if changed:
            self._wake.set()
        return changed

    def hold_connection(self, connection: str, code: str) -> None:
        self._pipeline.hold_connection(connection, code)
        self._wake.set()

    def recover_provider_once(self) -> None:
        """Use the same durable claim path for an explicit owner invocation."""
        if self._recovery_wake is None:
            raise ReviewPipelineError("recovery")
        self._recovery_wake(self._clock())

    def _schedule_due(self) -> None:
        with self._schedule_lock:
            if self._polling_interval_seconds is None:
                return
            if self._schedule_paused:
                return
            if self._schedule_waiting_for_action:
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
            only_actionable_failures = bool(progresses)
            for (connection, library, _), progress in zip(
                self.libraries, progresses, strict=True
            ):
                only_actionable_failures = only_actionable_failures and (
                    progress is not None and progress.state == "completed_with_failures"
                )
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
            self._schedule_waiting_for_action = only_actionable_failures

    def _run(self, lane: int) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1)
            self._wake.clear()
            try:
                if lane == 0:
                    # Recovery has no scheduler of its own.  It shares the
                    # supervisor's clock, wake, drain and shutdown lifecycle.
                    # A failed recovery observation is already persisted by
                    # its runner, so it must not prevent ordinary preparation.
                    if self._recovery_wake is not None:
                        try:
                            self._recovery_wake(self._clock())
                        except ReviewPipelineError:
                            # Explicit owner recovery reports its closed
                            # failure. The background lane instead preserves
                            # preparation capacity until the next normal wake.
                            pass
                    self._schedule_due()
                while not self._stop.is_set():
                    advanced = self._pipeline.run_next()
                    if lane == 0:
                        self._schedule_due()
                    if not advanced:
                        break
            except ReviewPipelineError:
                continue


def pipeline_scan_id(connection: str, library: str) -> str:
    material = f"review-preparation-v1\0{connection}\0{library}"
    return "review_scan_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
