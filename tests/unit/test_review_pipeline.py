from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import call, patch

import httpx
import pytest

import mechanical_librarian.bootstrap.composition as composition
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.preparation_presentation import (
    refresh_preparation_presentation,
    refresh_preparation_run_state,
    refresh_preparation_work,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    _FAILURE_CODES,
)
from mechanical_librarian.adapters.persistence.schema import _REVIEW_WORK_SQL
from mechanical_librarian.application.catalogue import (
    CatalogueEntry,
    CatalogueReader,
    TargetConnectionError,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    AudioItemInspectionError,
    CredentialError,
    ItemInspectionError,
    MetadataReportError,
    TargetNotFoundError,
    UnsupportedMediaError,
)
from mechanical_librarian.application.operations import (
    StageTimer,
    operational_incidents,
)
from mechanical_librarian.application.proposal_sets import (
    CreateProposalSet,
    ProposalCurrentnessError,
    ProposalEvaluationError,
    ProposalInternalError,
    ProposalMissingInputError,
    ProposalPolicyError,
    ProposalPublicationError,
    ProposalRetainedDataError,
    ProposalSetError,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    ProviderFailureObserver,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    ReviewPipelineError,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
    audio_inspection_failure_code,
)
from mechanical_librarian.application.review_pipeline_stages import ReviewPipelineStages
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.domain.audio_inspection import AudioInspectionOutcome
from mechanical_librarian.domain.epub_inspection import EpubInspectionOutcome
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.preparation_presentation import (
    PreparationRunState,
    PreparationTrigger,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.provider_recovery import MAX_TIMESTAMP
from mechanical_librarian.domain.review_browse import browse_report
from tests.support.review_cases import _report

_PRODUCER = "f" * 64


def _proposal_stages(
    create_proposal_set: Callable[[str, ReviewPipelineRepository], str],
) -> ReviewPipelineStages:
    def unreachable(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError

    return ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unreachable,
        catalogue_reader=cast("CatalogueReader", unreachable),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=unreachable,
        generate_report=unreachable,
        create_proposal_set=create_proposal_set,
        admit_review_case=unreachable,
        cover_considerations=unreachable,
        recovery_observer=cast("ProviderFailureObserver", unreachable),
        drain_outbox=unreachable,
        discovery_lock=threading.Lock(),
        clock=lambda: 7,
    )


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        (UnsupportedMediaError("unsupported"), "unsupported_media"),
        (
            ItemInspectionError(EpubInspectionOutcome.UNAVAILABLE_SOURCE),
            "unreadable_media",
        ),
        (ItemInspectionError(EpubInspectionOutcome.RESOURCE_LIMIT), "parser_refused"),
        (
            ItemInspectionError(EpubInspectionOutcome.INTERNAL_FAILURE),
            "internal_failure",
        ),
        (ItemInspectionError(), "unknown"),
    ),
    ids=("unsupported", "unreadable", "parser-refused", "internal", "unknown"),
)
def test_extraction_stage_projects_only_declared_failure_boundaries(
    failure: Exception, code: str
) -> None:
    def unreachable(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError

    stages = ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unreachable,
        catalogue_reader=cast("CatalogueReader", unreachable),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=lambda: None,
        generate_report=lambda *_: (_ for _ in ()).throw(failure),
        create_proposal_set=unreachable,
        admit_review_case=unreachable,
        cover_considerations=unreachable,
        recovery_observer=cast("ProviderFailureObserver", unreachable),
        drain_outbox=unreachable,
        discovery_lock=threading.Lock(),
        clock=lambda: 7,
    )

    with (
        patch.object(stages, "_require_eligible_entry"),
        pytest.raises(ReviewPipelineError) as raised,
    ):
        stages.extract(
            cast("ReviewPipelineRepository", object()),
            cast("PipelineAction", object()),
            False,
        )
    assert raised.value.code == code
    assert raised.value.__cause__ is failure


@pytest.mark.parametrize(
    ("failure", "code"),
    (
        (ProposalMissingInputError("missing"), "missing_required_input"),
        (ProposalRetainedDataError("retained"), "malformed_retained_data"),
        (ProposalCurrentnessError("stale"), "stale"),
        (ProposalPolicyError("policy"), "internal_failure"),
        (ProposalEvaluationError("evaluation"), "internal_failure"),
        (ProposalPublicationError("publication"), "internal_failure"),
        (ProposalInternalError("internal"), "internal_failure"),
        (ProposalSetError("undeclared closed type"), "unknown"),
        (RuntimeError("undeclared stage exception"), "unknown"),
    ),
)
def test_proposal_stage_projects_only_closed_failure_codes(
    failure: Exception, code: str
) -> None:
    stages = _proposal_stages(
        lambda _report_id, _repository: (_ for _ in ()).throw(failure)
    )

    with pytest.raises(ReviewPipelineError) as raised:
        stages.propose(cast("ReviewPipelineRepository", object()), "report-id")
    assert raised.value.code == code
    assert raised.value.__cause__ is failure


def test_proposal_stage_preserves_target_connection_for_recovery() -> None:
    failure = TargetConnectionError("unavailable")
    stages = _proposal_stages(
        lambda _report_id, _repository: (_ for _ in ()).throw(failure)
    )

    with pytest.raises(TargetConnectionError) as raised:
        stages.propose(cast("ReviewPipelineRepository", object()), "report-id")
    assert raised.value is failure


@pytest.mark.parametrize(
    ("prepare", "expected_failure", "code", "proposal_count"),
    (
        (
            lambda repository, report: None,
            ProposalMissingInputError,
            "missing_required_input",
            0,
        ),
        (
            lambda repository, report: (
                repository.publish(report, lambda: True),
                repository._connection.execute(
                    "DROP TRIGGER metadata_reports_no_update"
                ),
                repository._connection.execute(
                    "UPDATE metadata_reports SET connection_name='other' WHERE report_id=?",
                    (report.report_id,),
                ),
            ),
            ProposalRetainedDataError,
            "malformed_retained_data",
            0,
        ),
        (
            lambda repository, report: (
                repository.publish(report, lambda: True),
                repository._connection.execute(
                    "DROP TRIGGER metadata_reports_no_update"
                ),
                repository._connection.execute(
                    "UPDATE metadata_reports SET document_digest=? WHERE report_id=?",
                    ("0" * 64, report.report_id),
                ),
            ),
            ProposalRetainedDataError,
            "malformed_retained_data",
            0,
        ),
        (
            lambda repository, report: (
                repository.publish(report, lambda: True),
                repository._connection.set_authorizer(
                    lambda action, table, *_: (
                        sqlite3.SQLITE_DENY
                        if action == sqlite3.SQLITE_READ and table == "metadata_reports"
                        else sqlite3.SQLITE_OK
                    )
                ),
            ),
            ProposalInternalError,
            "internal_failure",
            0,
        ),
        (
            lambda repository, report: repository.publish(report, lambda: True),
            None,
            None,
            1,
        ),
    ),
    ids=("absent", "envelope", "digest", "persistence-fault", "valid"),
)
def test_sqlite_proposal_stage_keeps_input_failure_types_distinct(
    application_path: Path,
    prepare: Callable[[object, MetadataReport], object],
    expected_failure: type[Exception] | None,
    code: str | None,
    proposal_count: int,
) -> None:
    """The live adapter classifies retained input before proposal publication."""
    repository = open_repository(application_path)
    report = _report()
    prepare(repository, report)
    stages = _proposal_stages(
        lambda value, store: CreateProposalSet(store, lambda _: True).run(value)
    )

    if expected_failure is None:
        assert (
            stages.propose(repository, report.report_id)
            == evaluate_report(report).proposal_set_id
        )
    else:
        with pytest.raises(ReviewPipelineError) as raised:
            stages.propose(repository, report.report_id)
        assert raised.value.code == code
        assert isinstance(raised.value.__cause__, expected_failure)

    assert repository._connection.execute(
        "SELECT COUNT(*) FROM proposal_sets"
    ).fetchone() == (proposal_count,)
    repository.close()


@pytest.fixture
def application_path() -> Iterator[Path]:
    """A created, closed application database in a directory of its own."""

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        open_repository(path).close()
        yield path


def test_only_accepted_owner_trigger_creates_or_replaces_visible_run(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    ordinary = repository.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 10
    )
    assert repository.preparation_presentation(10) is None

    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        11,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    first = repository.preparation_presentation(11)
    assert first is not None
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        12,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    extended = repository.preparation_presentation(12)
    assert extended is not None
    assert extended.run_id == first.run_id
    assert [item.trigger for item in extended.triggers] == [
        PreparationTrigger.MANUAL_SCAN
    ]
    assert extended.triggers[0].accepted_count == 1

    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed',updated_at=13 WHERE scan_id=?",
        (ordinary.scan_id,),
    )
    refresh_preparation_presentation(repository._connection, 13)
    repository._connection.commit()
    terminal = repository.preparation_presentation(13)
    assert terminal is not None and terminal.dismissible
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 14)
    triggerless = repository.preparation_presentation(14)
    assert triggerless is not None
    assert (
        triggerless.run_id,
        triggerless.state,
        triggerless.passage,
        triggerless.completed_at,
    ) == (terminal.run_id, terminal.state, terminal.passage, terminal.completed_at)
    repository.dismiss_preparation_presentation(terminal.run_id, 15)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 16)
    dismissed = repository.preparation_presentation(16)
    assert dismissed is not None and dismissed.state.value == "dismissed"

    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        17,
        trigger=PreparationTrigger.LIBRARY_ADDED,
    )
    replacement = repository.preparation_presentation(17)
    repository.close()
    assert replacement is not None
    assert replacement.run_id != terminal.run_id
    assert [item.trigger for item in replacement.triggers] == [
        PreparationTrigger.LIBRARY_ADDED
    ]


def test_terminal_trigger_replacement_starts_queued_and_reopens_current(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    first = repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed',updated_at=101 "
        "WHERE scan_id=?",
        (first.scan_id,),
    )
    refresh_preparation_presentation(repository._connection, 101)
    terminal = repository.preparation_presentation(101)
    assert terminal is not None and terminal.dismissible

    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        102,
        trigger=PreparationTrigger.LIBRARY_ADDED,
    )
    replacement = repository.preparation_presentation(102)
    repository.close()

    assert replacement is not None
    assert replacement.run_id != terminal.run_id
    assert replacement.state is PreparationRunState.STARTING
    assert replacement.completed_at is None
    reopened = open_repository(application_path, create=False)
    assert reopened.preparation_presentation(102) == replacement
    reopened.close()


def test_first_visible_run_restarts_completed_triggerless_scan_as_queued(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    ordinary = repository.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 100
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed',updated_at=101 "
        "WHERE scan_id=?",
        (ordinary.scan_id,),
    )
    repository._connection.commit()
    assert repository.preparation_presentation(101) is None

    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        102,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    presentation = repository.preparation_presentation(102)
    enrollment_state = repository._connection.execute(
        "SELECT cycle_state FROM review_preparation_presentation "
        "WHERE row_kind='enrollment'"
    ).fetchone()
    repository.close()

    assert presentation is not None
    assert presentation.state is PreparationRunState.STARTING
    assert presentation.completed_at is None
    assert enrollment_state == ("queued",)
    reopened = open_repository(application_path, create=False)
    assert reopened.preparation_presentation(102) == presentation
    reopened.close()


def test_work_order_uses_unbounded_enrollment_ordinal(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    libraries = tuple((f"library-{index}", f"lib-{index}") for index in range(4))
    for index, (library, library_id) in enumerate(libraries):
        repository.request_review_scan(
            "primary",
            library,
            library_id,
            _PRODUCER,
            100 + index,
            trigger=PreparationTrigger.MANUAL_SCAN,
        )
    for index, (library, library_id) in enumerate(libraries):
        discovery = repository.claim_review_pipeline_action(110 + index * 2)
        assert discovery is not None
        assert discovery.library == library
        repository.complete_review_discovery(
            discovery,
            1,
            f"{index + 1:064x}",
            (
                replace(
                    _entry(f"item-{index}", f"{index + 11:064x}"),
                    library=library,
                    library_id=library_id,
                    title=f"Enrollment {index}",
                ),
            ),
            111 + index * 2,
        )
    repository._connection.execute(
        "UPDATE review_preparation_work SET state='running',updated_at=120"
    )
    refresh_preparation_presentation(repository._connection, 120)
    presentation = repository.preparation_presentation(120)
    work_ordinals = repository._connection.execute(
        "SELECT enrollment_ordinal,trigger_ordinal FROM review_preparation_presentation "
        "WHERE row_kind='work' ORDER BY enrollment_ordinal"
    ).fetchall()
    repository.close()

    assert presentation is not None
    assert presentation.triggers[0].accepted_count == 4
    assert work_ordinals == [(0, None), (1, None), (2, None), (3, None)]
    assert [book.name for book in presentation.active_books] == [
        f"Enrollment {index}" for index in range(4)
    ]


@pytest.mark.parametrize(
    "terminal_failure", (False, True), ids=("rollover", "terminal-cancellation")
)
def test_accepted_successor_cycle_settles_once_and_reopens_exact_current(
    application_path: Path, terminal_failure: bool
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("item", "1" * 64),), 102
    )

    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        103,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        104,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    before_rollover = repository.preparation_presentation(104)
    assert before_rollover is not None
    assert before_rollover.triggers[0].accepted_count == 2
    assert before_rollover.updated_at == 103

    work = repository.claim_review_pipeline_action(105)
    assert work is not None and work.stage == "extraction"
    if terminal_failure:
        repository.fail_review_pipeline_action(work, "failed", False, 106)
    else:
        repository._connection.execute(
            "UPDATE review_preparation_work SET stage='complete',state='complete',updated_at=106 "
            "WHERE work_id=?",
            (work.action_id,),
        )
        refresh_preparation_work(repository._connection, work.action_id, 106)
    assert repository.claim_review_pipeline_action(107) is None
    cycles = repository._connection.execute(
        "SELECT enrollment.cycle_id,enrollment.pending_cycle_id,work.cycle_id "
        "FROM review_preparation_presentation AS enrollment "
        "JOIN review_preparation_presentation AS work ON work.row_kind='work' "
        "AND work.scan_id=enrollment.scan_id "
        "WHERE enrollment.row_kind='enrollment'"
    ).fetchone()
    expected = repository.preparation_presentation(107)
    repository.close()

    assert cycles is not None
    assert cycles[1] is None
    assert cycles[0] == cycles[2]
    reopened = open_repository(application_path, create=False)
    assert reopened.preparation_presentation(107) == expected
    if not terminal_failure:
        successor_discovery = reopened.claim_review_pipeline_action(108)
        assert successor_discovery is not None
        reopened.complete_review_discovery(
            successor_discovery,
            2,
            "b" * 64,
            (_entry("item", "2" * 64),),
            109,
        )
        running = reopened.claim_review_pipeline_action(110)
        assert running is not None and running.stage == "extraction"
        active = reopened.preparation_presentation(110)
        assert active is not None
        assert [book.name for book in active.active_books] == ["Synthetic work"]
        assert active.passage.ready == 1
        assert active.recent_completions[0].outcome == "ready"
    reopened.close()


def test_terminal_enrollment_is_excluded_from_triggerless_full_reconciliation(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    scan_a = repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    repository.request_review_scan(
        "primary",
        "history",
        "lib-2",
        _PRODUCER,
        101,
        trigger=PreparationTrigger.LIBRARY_ADDED,
    )
    discovery_a = repository.claim_review_pipeline_action(102)
    assert discovery_a is not None and discovery_a.library == "fiction"
    repository.complete_review_discovery(
        discovery_a, 1, "a" * 64, (_entry("a", "1" * 64),), 103
    )
    discovery_b = repository.claim_review_pipeline_action(104)
    assert discovery_b is not None and discovery_b.library == "history"
    repository.complete_review_discovery(
        discovery_b,
        1,
        "b" * 64,
        (replace(_entry("b", "2" * 64), library="history", library_id="lib-2"),),
        105,
    )
    work_a = repository._connection.execute(
        "SELECT work_id FROM review_preparation_work WHERE scan_id=?",
        (scan_a.scan_id,),
    ).fetchone()[0]
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='complete',state='complete',updated_at=106 "
        "WHERE work_id=?",
        (work_a,),
    )
    refresh_preparation_work(repository._connection, work_a, 106)
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed',updated_at=107 WHERE scan_id=?",
        (scan_a.scan_id,),
    )
    refresh_preparation_run_state(repository._connection, 107)
    before = repository.preparation_presentation(107)
    before_work = repository._connection.execute(
        "SELECT work_stage,current_work_state,work_outcome,updated_at "
        "FROM review_preparation_presentation WHERE row_id=?",
        ("work:" + work_a,),
    ).fetchone()
    assert before is not None and before.state is PreparationRunState.RUNNING

    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 108)
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='extraction',state='running',updated_at=109 "
        "WHERE work_id=?",
        (work_a,),
    )
    refresh_preparation_presentation(repository._connection, 109)
    after = repository.preparation_presentation(109)
    after_work = repository._connection.execute(
        "SELECT work_stage,current_work_state,work_outcome,updated_at "
        "FROM review_preparation_presentation WHERE row_id=?",
        ("work:" + work_a,),
    ).fetchone()
    repository.close()

    assert after is not None
    assert after_work == before_work
    assert after.passage == before.passage
    assert after.recent_completions == before.recent_completions


@pytest.mark.parametrize("ineligible", (False, True), ids=("removed", "ineligible"))
def test_successor_discovery_removes_orphaned_presentation_work(
    application_path: Path, ineligible: bool
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    entry = _entry("item", "1" * 64)
    repository.complete_review_discovery(discovery, 1, "a" * 64, (entry,), 102)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        103,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    work_id = repository._connection.execute(
        "SELECT work_id FROM review_preparation_work"
    ).fetchone()[0]
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='complete',state='complete',updated_at=104 "
        "WHERE work_id=?",
        (work_id,),
    )
    refresh_preparation_work(repository._connection, work_id, 104)
    assert repository.claim_review_pipeline_action(105) is None
    successor = repository.claim_review_pipeline_action(106)
    assert successor is not None and successor.stage == "discovery"
    successor_entries = (
        (replace(entry, classification="ambiguous"),) if ineligible else ()
    )
    repository.complete_review_discovery(
        successor, 2, "b" * 64, successor_entries, 107
    )
    assert repository.claim_review_pipeline_action(108) is None
    presentation = repository.preparation_presentation(108)
    sidecar_work = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='work'"
    ).fetchone()[0]
    repository.close()

    assert presentation is not None
    assert presentation.passage.ready == 1
    assert presentation.total == 1
    assert presentation.active_books == ()
    assert sidecar_work == 0
    reopened = open_repository(application_path, create=False)
    assert reopened.preparation_presentation(108) == presentation
    reopened.close()


def test_multibyte_observed_title_survives_presentation_commit_and_reopen(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    title = "界" * 400
    repository.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        (replace(_entry("item", "1" * 64), title=title),),
        102,
    )
    expected = repository.preparation_presentation(102)
    persisted = repository._connection.execute(
        "SELECT display_name FROM review_preparation_presentation WHERE row_kind='work'"
    ).fetchone()
    repository.close()

    assert expected is not None
    assert persisted == (title,)
    reopened = open_repository(application_path, create=False)
    actual = reopened.preparation_presentation(102)
    reopened_title = reopened._connection.execute(
        "SELECT display_name FROM review_preparation_presentation WHERE row_kind='work'"
    ).fetchone()
    reopened.close()
    assert actual == expected
    assert reopened_title == (title,)


def test_preparation_projection_bounds_private_names_and_preserves_outcomes(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    entries = tuple(
        CatalogueEntry(
            "primary",
            "fiction",
            "lib-1",
            f"item-{index:02}",
            1,
            f"Private book {index:02}",
            ("Example Author",),
            True,
            False,
            "singular-supported",
            f"{index + 1:064x}",
            f"/book-{index:02}.epub",
            "epub",
            "books",
        )
        for index in range(10)
    )
    repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 102)
    work = repository._connection.execute(
        "SELECT work_id FROM review_preparation_work ORDER BY ordinal"
    ).fetchall()
    for index, (work_id,) in enumerate(work):
        repository._connection.execute(
            "UPDATE review_preparation_work SET stage='complete',state=?,updated_at=? WHERE work_id=?",
            ("complete" if index % 2 == 0 else "failed", 103 + index, work_id),
        )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed_with_failures',updated_at=113"
    )
    refresh_preparation_presentation(repository._connection, 113)
    repository._connection.commit()

    presentation = repository.preparation_presentation(113)
    repository.close()
    assert presentation is not None
    assert presentation.passage.ready == 5
    assert presentation.passage.failed == 5
    assert presentation.passage.checked == 10
    assert presentation.active_books == ()
    assert len(presentation.recent_completions) == 8
    assert {item.outcome for item in presentation.recent_completions} == {
        "ready",
        "failed",
    }
    assert presentation.recent_completions[0].name == "Private book 09"
    assert presentation.throughput_per_minute == 46.2


def test_preparation_projection_exposes_five_active_names_and_overflow(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.INITIAL_SETUP,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    entries = tuple(
        CatalogueEntry(
            "primary",
            "fiction",
            "lib-1",
            f"item-{index:02}",
            1,
            f"Private book {index:02}",
            ("Example Author",),
            True,
            False,
            "singular-supported",
            f"{index + 1:064x}",
            f"/book-{index:02}.epub",
            "epub",
            "books",
        )
        for index in range(9)
    )
    repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 102)
    work = repository._connection.execute(
        "SELECT work_id FROM review_preparation_work ORDER BY ordinal"
    ).fetchall()
    repository._connection.execute(
        "UPDATE review_preparation_work SET state='delayed',retry_not_before=999 "
        "WHERE work_id=?",
        (work[1][0],),
    )
    repository._connection.executemany(
        "UPDATE review_preparation_work SET stage='provider',state='running',updated_at=103 "
        "WHERE work_id=?",
        work[2:],
    )
    refresh_preparation_presentation(repository._connection, 103)
    presentation = repository.preparation_presentation(102)
    repository.close()
    assert presentation is not None
    assert [item.name for item in presentation.active_books] == [
        f"Private book {index:02}" for index in range(2, 7)
    ]
    assert presentation.active_overflow == 2


def test_preparation_failures_preserve_actual_passage_and_first_completion_time(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    entries = tuple(
        CatalogueEntry(
            "primary",
            "fiction",
            "lib-1",
            f"item-{index}",
            1,
            f"Private failure {index}",
            (),
            True,
            False,
            "singular-supported",
            f"{index + 1:064x}",
            f"/failure-{index}.epub",
            "epub",
            "books",
        )
        for index in range(2)
    )
    repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 102)
    work = repository._connection.execute(
        "SELECT work_id FROM review_preparation_work ORDER BY ordinal"
    ).fetchall()
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='extraction',state='failed',updated_at=103 WHERE work_id=?",
        (work[0][0],),
    )
    repository._connection.execute(
        "UPDATE review_preparation_work SET stage='provider',state='failed',updated_at=104 WHERE work_id=?",
        (work[1][0],),
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed_with_failures',updated_at=105"
    )
    refresh_preparation_presentation(repository._connection, 105)
    first = repository.preparation_presentation(105)
    refresh_preparation_presentation(repository._connection, 120)
    later = repository.preparation_presentation(120)
    repository.close()

    assert first is not None and later is not None
    assert first.passage.to_data() == {
        "read": 1,
        "searches_finished": 0,
        "compared": 0,
        "checked": 0,
        "ready": 0,
        "failed": 2,
    }
    assert first.completed_at == 105
    assert later.completed_at == 105


def test_preparation_blocking_is_scoped_to_enrolled_connections(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    repository._connection.execute(
        "INSERT INTO review_preparation_connection_conditions VALUES("
        "'secondary','unavailable','paused',1,1,101)"
    )
    refresh_preparation_run_state(repository._connection, 101)
    unrelated = repository.preparation_presentation(101)
    repository._connection.execute(
        "INSERT INTO review_preparation_connection_conditions VALUES("
        "'primary','unavailable','paused',1,1,102)"
    )
    refresh_preparation_run_state(repository._connection, 102)
    enrolled = repository.preparation_presentation(102)
    repository.close()

    assert unrelated is not None
    assert unrelated.state is PreparationRunState.STARTING
    assert enrolled is not None
    assert enrolled.state is PreparationRunState.BLOCKED


def test_completed_enrollment_connection_condition_does_not_block_active_peer(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    first = repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    second = repository.request_review_scan(
        "secondary",
        "history",
        "lib-2",
        _PRODUCER,
        101,
        trigger=PreparationTrigger.LIBRARY_ADDED,
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed',updated_at=102 WHERE scan_id=?",
        (first.scan_id,),
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='discovering',updated_at=102 WHERE scan_id=?",
        (second.scan_id,),
    )
    repository._connection.execute(
        "UPDATE review_preparation_presentation SET cycle_state='completed',updated_at=102 "
        "WHERE row_kind='enrollment' AND scan_id=?",
        (first.scan_id,),
    )
    repository._connection.execute(
        "UPDATE review_preparation_presentation SET cycle_state='discovering',updated_at=102 "
        "WHERE row_kind='enrollment' AND scan_id=?",
        (second.scan_id,),
    )
    repository._connection.execute(
        "INSERT INTO review_preparation_connection_conditions VALUES("
        "'primary','unavailable','paused',1,1,103)"
    )
    refresh_preparation_run_state(repository._connection, 103)
    presentation = repository.preparation_presentation(103)
    repository.close()

    assert presentation is not None
    assert presentation.state is PreparationRunState.RUNNING


def test_per_work_presentation_query_count_does_not_grow_with_run_population(
    tmp_path: Path,
) -> None:
    def measured(population: int) -> tuple[int, tuple[str, ...], int, tuple[str, ...]]:
        repository = open_repository(tmp_path / f"population-{population}.sqlite3")
        repository.request_review_scan(
            "primary",
            "fiction",
            "lib-1",
            _PRODUCER,
            100,
            trigger=PreparationTrigger.MANUAL_SCAN,
        )
        discovery = repository.claim_review_pipeline_action(101)
        assert discovery is not None
        entries = tuple(
            CatalogueEntry(
                "primary",
                "fiction",
                "lib-1",
                f"item-{index}",
                1,
                f"Synthetic book {index}",
                (),
                True,
                False,
                "singular-supported",
                f"{index + 1:064x}",
                f"/book-{index}.epub",
                "epub",
                "books",
            )
            for index in range(population)
        )
        repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 102)
        work_id = repository._connection.execute(
            "SELECT work_id FROM review_preparation_work ORDER BY ordinal LIMIT 1"
        ).fetchone()[0]
        repository._connection.execute(
            "UPDATE review_preparation_work SET state='running',updated_at=103 WHERE work_id=?",
            (work_id,),
        )
        statements: list[str] = []
        repository._connection.set_trace_callback(statements.append)
        refresh_preparation_work(repository._connection, work_id, 103)
        repository._connection.set_trace_callback(None)
        exact_work_plan = tuple(
            row[3]
            for row in repository._connection.execute(
                "EXPLAIN QUERY PLAN SELECT run_id,work_stage,work_outcome "
                "FROM review_preparation_presentation WHERE row_id=? AND row_kind='work'",
                ("work:" + work_id,),
            )
        )
        trigger_plan = tuple(
            row[3]
            for row in repository._connection.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) "
                "FROM review_preparation_presentation AS trigger "
                "JOIN review_preparation_scans AS scan ON scan.scan_id=trigger.scan_id "
                "WHERE trigger.row_kind='trigger' AND trigger.run_id=?",
                (repository.preparation_presentation(103).run_id,),
            )
        )
        snapshot_plans = tuple(
            row[3]
            for sql in (
                "SELECT display_name,work_stage FROM review_preparation_presentation WHERE row_kind='work' AND run_id=? AND current_work_state='running' ORDER BY enrollment_ordinal,ordinal,work_id LIMIT 5",
                "SELECT display_name,work_outcome,updated_at FROM review_preparation_presentation WHERE row_kind='work' AND run_id=? AND work_outcome IS NOT NULL ORDER BY updated_at DESC,enrollment_ordinal DESC,ordinal DESC,work_id DESC LIMIT 8",
                "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='work' AND run_id=? AND work_outcome IS NOT NULL AND updated_at>=?",
            )
            for row in repository._connection.execute(
                "EXPLAIN QUERY PLAN " + sql,
                (repository.preparation_presentation(103).run_id, 0)
                if sql.endswith("updated_at>=?")
                else (repository.preparation_presentation(103).run_id,),
            )
        )
        vm_steps = 0

        def count_step() -> int:
            nonlocal vm_steps
            vm_steps += 1
            return 0

        repository._connection.set_progress_handler(count_step, 1)
        repository.preparation_presentation(103)
        repository._connection.set_progress_handler(None, 0)
        repository.close()
        return len(statements), exact_work_plan + trigger_plan, vm_steps, snapshot_plans

    small_count, small_plan, small_vm, small_snapshot_plan = measured(1)
    large_count, large_plan, large_vm, large_snapshot_plan = measured(2_000)
    assert large_count == small_count
    assert large_count <= 10
    assert any(
        "sqlite_autoindex_review_preparation_presentation_1" in row
        for row in small_plan
    )
    assert any("review_preparation_presentation_run" in row for row in small_plan)
    assert large_plan == small_plan
    assert large_vm <= small_vm + 40
    assert large_snapshot_plan == small_snapshot_plan
    assert all("USE TEMP B-TREE" not in row for row in large_snapshot_plan)
    assert any(
        "review_preparation_presentation_active" in row for row in large_snapshot_plan
    )
    assert any(
        "review_preparation_presentation_recent" in row for row in large_snapshot_plan
    )
    assert any(
        "review_preparation_presentation_throughput" in row
        for row in large_snapshot_plan
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "UPDATE review_preparation_presentation SET first_accepted_at=123 WHERE row_kind='run'",
        "UPDATE review_preparation_presentation SET ready=1 WHERE row_kind='trigger'",
        "UPDATE review_preparation_presentation SET searches_finished=1 WHERE row_kind='trigger'",
        "UPDATE review_preparation_presentation SET started_at=1 WHERE row_kind='trigger'",
        "UPDATE review_preparation_presentation SET ready=1 WHERE row_kind='enrollment'",
        "UPDATE review_preparation_presentation SET started_at=1 WHERE row_kind='enrollment'",
        "UPDATE review_preparation_presentation SET ready=1 WHERE row_kind='work'",
        "UPDATE review_preparation_presentation SET started_at=1 WHERE row_kind='work'",
        "UPDATE review_preparation_presentation SET row_id='work:invented' WHERE row_kind='work'",
        "UPDATE review_preparation_presentation SET connection_name='other' WHERE row_kind='enrollment'",
        "UPDATE review_preparation_presentation SET pending_cycle_id=cycle_id WHERE row_kind='enrollment' AND ordinal=0",
        "UPDATE review_preparation_presentation SET ordinal=2 "
        "WHERE row_kind='enrollment'",
        "UPDATE review_preparation_presentation SET run_id='preparation_run_' || "
        "lower(hex(randomblob(32))) WHERE row_kind='trigger'",
        "UPDATE review_preparation_presentation SET enrollment_ordinal=9 "
        "WHERE row_kind='work'",
        "UPDATE review_preparation_presentation SET cycle_id=(SELECT cycle_id FROM "
        "review_preparation_presentation WHERE row_kind='enrollment' AND ordinal=0) "
        "WHERE row_kind='enrollment' AND ordinal=1",
    ),
    ids=(
        "run-trigger-column",
        "trigger-passage-column",
        "trigger-search-column",
        "trigger-run-column",
        "enrollment-passage-column",
        "enrollment-run-column",
        "work-passage-column",
        "work-run-column",
        "work-row-identity",
        "enrollment-target",
        "equal-current-pending-cycle",
        "noncanonical-ordinal",
        "foreign-run",
        "work-enrollment-ordinal",
        "duplicate-enrollment-cycle",
    ),
)
def test_exact_current_presentation_rows_fail_closed(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "invalid-presentation.sqlite3"
    repository = open_repository(path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("item", "1" * 64),), 102
    )
    repository.request_review_scan(
        "primary",
        "history",
        "lib-2",
        _PRODUCER,
        103,
        trigger=PreparationTrigger.LIBRARY_ADDED,
    )
    repository.close()

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(mutation)
    connection.commit()
    connection.close()

    with pytest.raises(
        MetadataReportError, match="database is unavailable or incompatible"
    ):
        open_repository(path, create=False)


@pytest.mark.parametrize("column", ("cycle_id", "pending_cycle_id"))
def test_nonhex_presentation_cycle_identity_fails_closed(
    tmp_path: Path, column: str
) -> None:
    path = tmp_path / f"invalid-{column}.sqlite3"
    repository = open_repository(path)
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        100,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    discovery = repository.claim_review_pipeline_action(101)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("item", "1" * 64),), 102
    )
    repository.request_review_scan(
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        103,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    repository.close()

    invalid = "preparation_cycle_" + "a" * 63 + "g"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(
        f"UPDATE review_preparation_presentation SET {column}=? "
        "WHERE row_kind='enrollment'",
        (invalid,),
    )
    if column == "cycle_id":
        connection.execute(
            "UPDATE review_preparation_presentation SET cycle_id=? WHERE row_kind='work'",
            (invalid,),
        )
    connection.commit()
    connection.close()

    with pytest.raises(
        MetadataReportError, match="database is unavailable or incompatible"
    ):
        open_repository(path, create=False)


def test_pause_state_is_durable_and_does_not_remove_committed_work(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    repository.set_review_pipeline_paused(True, 11)
    repository.close()

    reopened = open_repository(application_path)
    assert reopened.review_pipeline_paused()
    action = reopened.claim_review_pipeline_action(12)
    reopened.set_review_pipeline_paused(False, 13)
    assert not reopened.review_pipeline_paused()
    reopened.close()
    assert action is not None
    assert action.stage == "discovery"


def test_pause_atomically_rejects_new_scan_admission(application_path: Path) -> None:
    repository = open_repository(application_path)
    repository.set_review_pipeline_paused(True, 10)
    with pytest.raises(ReviewPipelineError, match="paused"):
        repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 11)
    count = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_scans"
    ).fetchone()
    repository.close()
    assert count == (0,)


def test_scan_qualification_survives_restart_and_resets_for_new_cycle(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    action = repository.claim_review_pipeline_action(2)
    assert action is not None
    assert repository.discovery_requires_qualification(action)
    assert repository.qualify_review_discovery(action, 3)
    repository.close()

    repository = open_repository(application_path)
    repository.recover_review_pipeline((("primary", "fiction", "lib-1", _PRODUCER),))
    action = repository.claim_review_pipeline_action(4)
    assert action is not None
    assert not repository.discovery_requires_qualification(action)
    repository.defer_for_target_connection(action, "unavailable", 5)
    condition = repository.begin_target_connection_check("primary", 6)
    assert condition is not None
    assert repository.finish_target_connection_check(
        "primary", condition.epoch, None, 7
    )
    action = repository.claim_review_pipeline_action(8)
    assert action is not None
    assert not repository.discovery_requires_qualification(action)
    repository.complete_review_discovery(action, 1, "a" * 64, (), 9)
    assert repository.claim_review_pipeline_action(10) is None
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 11)
    action = repository.claim_review_pipeline_action(12)
    assert action is not None
    assert repository.discovery_requires_qualification(action)
    repository.close()


def test_recovery_rebinds_the_latest_scan_after_settings_change(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    first_boot = repository.request_review_scan(
        "primary", "books", "books-v1", _PRODUCER, 1, first_boot=True
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed' WHERE scan_id=?",
        (first_boot.scan_id,),
    )
    ordinary = repository.request_review_scan(
        "primary", "books", "books-v1", _PRODUCER, 2
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='processing' WHERE scan_id=?",
        (ordinary.scan_id,),
    )
    repository.close()

    changed_producer = "e" * 64
    reopened = open_repository(database, create=False)
    try:
        reopened.recover_review_pipeline(
            (("primary", "books", "books-v2", changed_producer),)
        )
        rows = reopened._connection.execute(
            "SELECT scan_id,state,library_id,producer_fingerprint "
            "FROM review_preparation_scans ORDER BY requested_at,scan_id"
        ).fetchall()
    finally:
        reopened.close()

    assert rows == [
        (first_boot.scan_id, "completed", "books-v1", _PRODUCER),
        (ordinary.scan_id, "queued", "books-v2", changed_producer),
    ]


def test_pipeline_preflight_is_once_per_cycle_and_recovery_retains_qualification(
    application_path: Path,
) -> None:
    checks: list[str] = []
    discoveries = 0

    def discover(_connection: str, _library: str):
        nonlocal discoveries
        discoveries += 1
        if discoveries == 1:
            raise KeyboardInterrupt()
        return 1, "a" * 64, ()

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=discover,
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (),
        propose=lambda *_: "",
        admit=lambda *_: ("", None),
        project=lambda *_: True,
        check_connection=checks.append,
        clock=lambda: 10,
    )
    pipeline.request("primary", "fiction")
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_next()
    assert checks == ["primary"]

    pipeline.recover()
    assert pipeline.run_next()
    assert checks == ["primary"]
    assert not pipeline.run_next()
    pipeline.request("primary", "fiction")
    assert pipeline.run_next()
    assert checks == ["primary", "primary"]


def test_preopened_observation_survives_delete_journal_writer(
    application_path: Path,
) -> None:
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        observation_repository_factory=lambda: open_repository(
            application_path, cross_thread=True
        ),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: (_ for _ in ()).throw(AssertionError()),
    )
    pipeline.open_observation()
    writer = sqlite3.connect(application_path, isolation_level=None)
    try:
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "UPDATE review_preparation_control SET updated_at=1 WHERE singleton=1"
            )
            if sys.platform.startswith("linux"):
                for operation in (
                    pipeline.status,
                    pipeline.connection_conditions,
                    lambda: pipeline.request("primary", "fiction"),
                    lambda: pipeline.progress("primary", "fiction"),
                ):
                    with pytest.raises(ReviewPipelineError, match="persistence"):
                        operation()
            else:
                assert pipeline.status() == ((None,), False)
                assert pipeline.connection_conditions() == ()
                with pytest.raises(ReviewPipelineError, match="persistence"):
                    pipeline.request("primary", "fiction")
                assert pipeline.progress("primary", "fiction") is None
            started = time.monotonic()
            with pytest.raises(MetadataReportError):
                open_repository(application_path)
            if sys.platform.startswith("linux"):
                assert time.monotonic() - started < 1
        finally:
            writer.rollback()
            writer.close()

        assert pipeline.status() == ((None,), False)
        assert pipeline.connection_conditions() == ()
        requested = pipeline.request("primary", "fiction")
        assert requested.state == "queued"
        assert pipeline.progress("primary", "fiction") == requested
    finally:
        pipeline.close_observation()

    reopened = open_repository(application_path)
    try:
        assert reopened.review_pipeline_progress("primary", "fiction") == requested
    finally:
        reopened.close()


def test_lifecycle_foreground_request_uses_one_clean_atomic_pause_boundary(
    application_path: Path,
) -> None:
    owner = open_repository(application_path)
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(
            application_path, create=False, authority=owner.authority
        ),
        observation_repository_factory=lambda: open_repository(
            application_path,
            create=False,
            cross_thread=True,
            authority=owner.authority,
        ),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: (_ for _ in ()).throw(AssertionError()),
        clock=lambda: 10,
    )
    pipeline.open_observation()
    try:
        requested = pipeline.request("primary", "fiction")
        assert requested.state == "queued"
        assert pipeline.progress("primary", "fiction") == requested
        pipeline.set_paused(True)
        with pytest.raises(ReviewPipelineError, match="paused"):
            pipeline.request("primary", "fiction")
    finally:
        pipeline.close_observation()
        owner.close()

    reopened = open_repository(application_path)
    try:
        assert reopened.review_pipeline_progress("primary", "fiction") == requested
        assert reopened.review_pipeline_paused()
    finally:
        reopened.close()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_lifecycle_foreground_rejects_replaced_linux_binding(
    application_path: Path,
) -> None:
    with tempfile.TemporaryDirectory() as replacement_directory:
        replacement_path = Path(replacement_directory) / "replacement.sqlite"
        replacement = open_repository(replacement_path)
        replacement.close()
        owner = open_repository(application_path)
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(
                application_path, create=False, authority=owner.authority
            ),
            observation_repository_factory=lambda: open_repository(
                application_path,
                create=False,
                cross_thread=True,
                authority=owner.authority,
            ),
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
            providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
            propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
            admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
            project=lambda *_: (_ for _ in ()).throw(AssertionError()),
            clock=lambda: 10,
        )
        pipeline.open_observation()
        obsolete_path = application_path.with_name("obsolete.sqlite")
        application_path.rename(obsolete_path)
        replacement_path.rename(application_path)
        try:
            for operation in (
                lambda: pipeline.progress("primary", "fiction"),
                pipeline.status,
                pipeline.connection_conditions,
                lambda: pipeline.request("primary", "fiction"),
            ):
                with pytest.raises(ReviewPipelineError, match="persistence"):
                    operation()
        finally:
            pipeline.close_observation()
            owner.close()

        replacement = open_repository(application_path)
        try:
            assert replacement.review_pipeline_progress("primary", "fiction") is None
        finally:
            replacement.close()
        obsolete = open_repository(obsolete_path)
        try:
            assert obsolete.review_pipeline_progress("primary", "fiction") is None
        finally:
            obsolete.close()


def test_composed_currentness_preserves_target_connection_errors(
    application_path: Path,
) -> None:
    configuration = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/token"
            ),
        ),
        (LibraryConfiguration("ebooks", "primary", "library-id", "book", "media", ()),),
        (MediaMountConfiguration("media", "/media", "/library", True),),
        (),
    )
    report = _report()
    proposal = evaluate_report(report)
    repository = open_repository(application_path)
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    repository.close()

    def current(target, *_args, **_keywords):
        target.read("item-1")
        return True

    for status, code in ((401, "authentication"), (403, "authorization")):
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(composition, "CoverArtifactStore", return_value=object())
            )
            stack.enter_context(
                patch.object(
                    composition,
                    "target_client",
                    side_effect=lambda *_args: httpx.Client(
                        transport=httpx.MockTransport(
                            lambda _request: httpx.Response(status)  # noqa: B023 - closure is created and consumed within this iteration
                        )
                    ),
                )
            )
            stack.enter_context(
                patch.object(composition, "require_report_current", side_effect=current)
            )
            with pytest.raises(TargetConnectionError) as raised:
                composition.create_proposal_set_loaded(
                    configuration, application_path, Path("ignored"), report.report_id
                )
            assert raised.value.code == code
            with pytest.raises(TargetConnectionError) as raised:
                composition.admit_review_case_loaded(
                    configuration,
                    application_path,
                    Path("ignored"),
                    proposal.proposal_set_id,
                )
            assert raised.value.code == code

    with (
        patch.object(composition, "CoverArtifactStore", return_value=object()),
        patch.object(
            composition,
            "target_client",
            side_effect=lambda *_args: httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: (_ for _ in ()).throw(
                        httpx.ConnectError("offline")
                    )
                )
            ),
        ),
        patch.object(composition, "require_report_current", side_effect=current),
    ):
        with pytest.raises(TargetConnectionError) as raised:
            composition.create_proposal_set_loaded(
                configuration, application_path, Path("ignored"), report.report_id
            )
        assert raised.value.code == "unavailable"
        with pytest.raises(TargetConnectionError) as raised:
            composition.admit_review_case_loaded(
                configuration,
                application_path,
                Path("ignored"),
                proposal.proposal_set_id,
            )
        assert raised.value.code == "unavailable"

    with (
        patch.object(composition, "CoverArtifactStore", return_value=object()),
        patch.object(
            composition,
            "target_client",
            side_effect=lambda *_args: httpx.Client(
                transport=httpx.MockTransport(lambda _request: httpx.Response(404))
            ),
        ),
        patch.object(composition, "require_report_current", side_effect=current),
    ):
        with pytest.raises(ProposalCurrentnessError):
            composition.create_proposal_set_loaded(
                configuration, application_path, Path("ignored"), report.report_id
            )
        result = json.loads(
            composition.admit_review_case_loaded(
                configuration,
                application_path,
                Path("ignored"),
                proposal.proposal_set_id,
            )
        )
    assert result["status"] == "stale"


def test_store_credentials_reach_proposal_and_admission_currentness(
    application_path: Path,
) -> None:
    """A provider-empty first run retains its private ABS credential authority."""
    configuration = ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "https://example.invalid", None),),
        (
            LibraryConfiguration(
                "ebooks", "primary", "library-id", "ebook", "media", ()
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/library", True),),
        (),
    )
    report = _report()
    repository = open_repository(application_path)
    repository.publish(report, lambda: True)
    repository.close()
    client = object()

    class _Artifacts:
        def require_current(self) -> None:
            return None

    class _Target:
        def __init__(self, *, client_factory, **_arguments) -> None:
            self.client_factory = client_factory

    def current(target, *_arguments, **_keywords) -> bool:
        assert target.client_factory() is client
        return True

    with (
        patch.object(composition, "CoverArtifactStore", return_value=_Artifacts()),
        patch.object(composition, "AudiobookshelfItemReader", _Target),
        patch.object(composition, "target_client", side_effect=AssertionError),
        patch.object(composition, "target_client_token", return_value=client) as token,
        patch.object(composition, "require_report_current", side_effect=current),
    ):
        proposal = ProposalSet.from_json(
            composition.create_proposal_set_loaded(
                configuration,
                application_path,
                Path("ignored"),
                report.report_id,
                credentials={"target:primary": "stored-key"},
            )
        )
        admitted = json.loads(
            composition.admit_review_case_loaded(
                configuration,
                application_path,
                Path("ignored"),
                proposal.proposal_set_id,
                credentials={"target:primary": "stored-key"},
            )
        )

    assert admitted["status"] == "admitted"
    assert token.call_args_list == [
        call("https://example.invalid", "stored-key", 30),
        call("https://example.invalid", "stored-key", 30),
    ]


def test_late_target_connection_errors_rollback_and_defer_retained_stage() -> None:
    class Repository:
        def __init__(self) -> None:
            self.begun = 0
            self.rolled_back = 0
            self.deferred: list[tuple[PipelineAction, str]] = []

        def guard_target_connection(self, _action, _checked_at):
            return True

        def begin_review_pipeline_output(self) -> None:
            self.begun += 1

        def rollback_review_pipeline_output(self) -> None:
            self.rolled_back += 1

        def defer_for_target_connection(self, action, code, _updated_at) -> None:
            self.deferred.append((action, code))

        def fail_review_pipeline_action(self, *_args) -> None:
            raise AssertionError("target connection must not create item failure")

    callbacks = {"proposal": 0, "admission": 0, "projection": 0}

    def failing(stage: str):
        def callback(*_args):
            callbacks[stage] += 1
            raise TargetConnectionError("tls")

        return callback

    for stage, identifiers in (
        ("proposal", {"report_id": "report-1"}),
        ("admission", {"report_id": "report-1", "proposal_set_id": "proposal-1"}),
        (
            "projection",
            {
                "report_id": "report-1",
                "proposal_set_id": "proposal-1",
                "review_case_id": "case-1",
            },
        ),
    ):
        repository = Repository()
        action = PipelineAction(
            "action-1",
            "scan-1",
            stage,
            1,
            "primary",
            "ebooks",
            "library-id",
            _PRODUCER,
            1,
            "a" * 64,
            "item-1",
            "b" * 64,
            0,
            source_fingerprint="c" * 64,
            **identifiers,
        )
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: cast(ReviewPipelineRepository, repository),  # noqa: B023 - closure is created and consumed within this iteration
            libraries=(("primary", "ebooks", "library-id", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
            providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
            propose=failing("proposal"),
            admit=failing("admission"),
            project=failing("projection"),
            clock=lambda: 1,
        )
        assert (
            pipeline._run_claimed_action(repository, action, claim_next=False) is None
        )
        assert repository.begun == 1
        assert repository.rolled_back == 1
        assert repository.deferred == [(action, "tls")]
    assert callbacks == {"proposal": 1, "admission": 1, "projection": 1}


def test_post_cutover_delivery_failure_requeues_the_claimed_successor() -> None:
    class Repository:
        def __init__(self, successor: PipelineAction) -> None:
            self.successor = successor
            self.failures: list[tuple[PipelineAction, str, bool, int]] = []

        def advance_review_pipeline_action(self, *_args, **_kwargs):
            return self.successor

        def fail_review_pipeline_action(
            self, action, code, retryable, completed_at, _failure_root
        ):
            self.failures.append((action, code, retryable, completed_at))

    action = PipelineAction(
        "action-1",
        "scan-1",
        "projection",
        1,
        "primary",
        "ebooks",
        "library-id",
        _PRODUCER,
        1,
        "a" * 64,
        "item-1",
        "b" * 64,
        0,
        "report-1",
        review_case_id="case-1",
        source_fingerprint="c" * 64,
    )
    successor = replace(action, action_id="action-2", stage="proposal")
    repository = Repository(successor)
    announcements: list[tuple[str, str | None]] = []
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, repository),
        libraries=(("primary", "ebooks", "library-id", _PRODUCER),),
        discover=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        after_advance=lambda *_args: (_ for _ in ()).throw(RuntimeError()),
        clock=lambda: 9,
        announce=lambda topic, subject: announcements.append((topic, subject)),
    )
    pauses: list[None] = []
    pipeline._cooperative_wait = lambda: pauses.append(None)

    assert pipeline._advance(repository, action, "complete", claim_next=True) is None
    assert [(successor, "persistence", True, 9)] == repository.failures
    assert pauses == []
    assert [
        ("preparation", None),
        ("library", "case-1"),
        ("case", "case-1"),
        ("preparation", None),
        ("library", None),
    ] == announcements


def test_committed_advance_pauses_before_processing_claimed_successor() -> None:
    class Repository:
        def __init__(self, successor: PipelineAction) -> None:
            self.successor = successor

        def advance_review_pipeline_action(self, *_args, **_kwargs):
            return self.successor

    action = PipelineAction(
        "action-1",
        "scan-1",
        "projection",
        1,
        "primary",
        "ebooks",
        "library-id",
        _PRODUCER,
        1,
        "a" * 64,
        "item-1",
        "b" * 64,
        0,
        "report-1",
        review_case_id="case-1",
        source_fingerprint="c" * 64,
    )
    successor = replace(action, action_id="action-2", stage="proposal")
    repository = Repository(successor)
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, repository),
        libraries=(("primary", "ebooks", "library-id", _PRODUCER),),
        discover=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        clock=lambda: 9,
    )
    pauses: list[None] = []
    pipeline._cooperative_wait = lambda: pauses.append(None)

    assert (
        pipeline._advance(repository, action, "complete", claim_next=True) == successor
    )
    assert pauses == [None]


def test_committed_extraction_checkpoint_pauses_only_for_claimed_successor() -> None:
    class Repository:
        def __init__(self, successor: PipelineAction | None) -> None:
            self.successor = successor

        def take_review_pipeline_claim(self):
            return self.successor

    action = PipelineAction(
        "action-1",
        "scan-1",
        "extraction",
        1,
        "primary",
        "ebooks",
        "library-id",
        _PRODUCER,
        1,
        "a" * 64,
        "item-1",
        "b" * 64,
        0,
        source_fingerprint="c" * 64,
    )
    successor = replace(action, action_id="action-2", stage="provider")
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, Repository(None)),
        libraries=(("primary", "ebooks", "library-id", _PRODUCER),),
        discover=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_args: ("report-1", True),
        providers=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        clock=lambda: 9,
    )
    pauses: list[None] = []
    pipeline._cooperative_wait = lambda: pauses.append(None)

    assert (
        pipeline._execute_claimed_action(Repository(successor), action, claim_next=True)
        == successor
    )
    assert pauses == [None]
    assert (
        pipeline._execute_claimed_action(Repository(None), action, claim_next=True)
        is None
    )
    assert pauses == [None]


def test_post_cutover_delivery_failure_announces_committed_package_without_successor() -> (
    None
):
    class Repository:
        def advance_review_pipeline_action(self, *_args, **_kwargs):
            return None

    action = PipelineAction(
        "action-1",
        "scan-1",
        "projection",
        1,
        "primary",
        "ebooks",
        "library-id",
        _PRODUCER,
        1,
        "a" * 64,
        "item-1",
        "b" * 64,
        0,
        "report-1",
        review_case_id="case-1",
        source_fingerprint="c" * 64,
    )
    announcements: list[tuple[str, str | None]] = []
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, Repository()),
        libraries=(("primary", "ebooks", "library-id", _PRODUCER),),
        discover=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        after_advance=lambda *_args: (_ for _ in ()).throw(RuntimeError()),
        clock=lambda: 9,
        announce=lambda topic, subject: announcements.append((topic, subject)),
    )

    assert pipeline._advance(Repository(), action, "complete", claim_next=True) is None
    assert [("preparation", None), ("library", "case-1"), ("case", "case-1")] == (
        announcements
    )


def test_post_cutover_settlement_failure_propagates_closed_persistence_error() -> None:
    class Repository:
        def advance_review_pipeline_action(self, *_args, **_kwargs):
            return successor

        def fail_review_pipeline_action(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("injected settlement failure")

    action = PipelineAction(
        "action-1",
        "scan-1",
        "provider",
        1,
        "primary",
        "ebooks",
        "library-id",
        _PRODUCER,
        1,
        "a" * 64,
        "item-1",
        "b" * 64,
        0,
        "report-1",
        source_fingerprint="c" * 64,
    )
    successor = replace(action, action_id="action-2", stage="proposal")
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, Repository()),
        libraries=(("primary", "ebooks", "library-id", _PRODUCER),),
        discover=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        after_advance=lambda *_args: (_ for _ in ()).throw(RuntimeError()),
        clock=lambda: 9,
    )

    with pytest.raises(ReviewPipelineError) as raised:
        pipeline._advance(Repository(), action, None, claim_next=True)
    assert "persistence" == raised.value.code


def test_connection_hold_guards_claims_and_stale_checkers_fail_closed(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    repository.request_review_scan("primary", "history", "lib-2", _PRODUCER, 2)
    repository.request_review_scan("secondary", "books", "lib-3", _PRODUCER, 3)
    first = repository.claim_review_pipeline_action(4)
    second = repository.claim_review_pipeline_action(5)
    assert first is not None and second is not None
    assert (first.connection, second.connection) == ("primary", "primary")
    repository.defer_for_target_connection(first, "unavailable", 6)
    assert not repository.guard_target_connection(second, 7)
    states = repository._connection.execute(
        "SELECT state FROM review_preparation_scans WHERE connection_name='primary'"
    ).fetchall()
    assert states == [("queued",), ("queued",)]
    unrelated = repository.claim_review_pipeline_action(8)
    assert unrelated is not None
    assert unrelated.connection == "secondary"
    check = repository.begin_target_connection_check("primary", 9)
    assert check is not None
    assert not repository.finish_target_connection_check(
        "primary", check.epoch - 1, None, 10
    )
    repository.close()

    reopened = open_repository(application_path)
    reopened.recover_review_pipeline((("primary", "fiction", "lib-1", _PRODUCER),))
    condition = reopened.target_connection_conditions()[0]
    reopened.close()
    assert (condition.connection, condition.state) == ("primary", "paused")


def test_restart_retires_removed_active_scope_before_any_orphan_claim(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    repository.request_review_scan("removed", "old-library", "lib-2", _PRODUCER, 2)
    repository.recover_review_pipeline((("primary", "fiction", "lib-1", _PRODUCER),))
    first = repository.claim_review_pipeline_action(3)
    assert first is not None
    assert first.connection == "primary"
    assert repository.claim_review_pipeline_action(4) is None
    retired = repository._connection.execute(
        "SELECT state FROM review_preparation_scans WHERE connection_name='removed'"
    ).fetchone()
    repository.close()
    assert retired == ("completed_with_failures",)


def test_delayed_work_remains_visible_recovers_when_due_and_retires_with_removed_scope(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    discovery = repository.claim_review_pipeline_action(2)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("delayed", "b" * 64),), 3
    )
    work = repository.claim_review_pipeline_action(4)
    assert work is not None
    repository.fail_review_pipeline_action(work, "transient", True, 5)
    progress = repository.review_pipeline_progress("primary", "fiction")
    assert progress is not None
    assert dict(progress.stages)["queued"] == 1
    repository.hold_target_connection("primary", "unavailable", 6)
    assert repository.target_connection_conditions()[0].affected == 1
    check = repository.begin_target_connection_check("primary", 7)
    assert check is not None
    assert repository.finish_target_connection_check("primary", check.epoch, None, 8)
    repository.recover_review_pipeline((("primary", "fiction", "lib-1", _PRODUCER),))
    assert repository.claim_review_pipeline_action(14) is None
    recovered = repository.claim_review_pipeline_action(15)
    assert recovered is not None
    assert recovered.item_id == "delayed"
    repository.fail_review_pipeline_action(recovered, "transient", True, 16)
    repository.recover_review_pipeline(())
    retired = repository._connection.execute(
        "SELECT state FROM review_preparation_work WHERE work_id=?",
        (recovered.action_id,),
    ).fetchone()
    assert not any(
        "diagnostic_reference" in signal
        for signal in repository.operational_snapshot()["signals"]
    )
    repository.close()
    assert retired == ("stale",)


def test_unexpected_owner_probe_durably_returns_to_paused(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    action = repository.claim_review_pipeline_action(2)
    assert action is not None
    repository.defer_for_target_connection(action, "tls", 3)
    repository.close()

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError),
        providers=lambda *_: (),
        propose=lambda *_: "",
        admit=lambda *_: ("", None),
        project=lambda *_: True,
        check_connection=lambda _: (_ for _ in ()).throw(RuntimeError()),
        clock=lambda: 4,
    )
    assert pipeline.check_connection("primary")
    condition = pipeline.connection_conditions()[0]
    assert (condition.code, condition.state) == ("unavailable", "paused")


def test_owner_probe_releases_its_repository_before_network_and_refreshes_affected(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    action = repository.claim_review_pipeline_action(2)
    assert action is not None
    repository.defer_for_target_connection(action, "unavailable", 3)
    repository.request_review_scan("primary", "history", "lib-2", _PRODUCER, 4)
    assert repository.target_connection_conditions()[0].affected == 2
    repository.close()

    opened: list[object] = []

    def factory():
        value = open_repository(application_path)
        opened.append(value)
        return value

    def probe(_connection: str) -> None:
        # Begin committed before the external observation: an unrelated
        # repository read can proceed while this probe is deliberately slow.
        observer = open_repository(application_path)
        try:
            assert observer.target_connection_conditions()[0].state == "checking"
        finally:
            observer.close()

    pipeline = ReviewPreparationPipeline(
        repository_factory=factory,
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError),
        providers=lambda *_: (),
        propose=lambda *_: "",
        admit=lambda *_: ("", None),
        project=lambda *_: True,
        check_connection=probe,
        clock=lambda: 5,
    )
    assert pipeline.check_connection("primary")
    assert pipeline.connection_conditions()[0].state == "recovered"


def test_unreadable_connection_conditions_reach_the_caller_as_pipeline_error() -> None:
    """The condition read owes its caller this layer's error type.

    The read was a bare `try/finally`, so the bound repository's own
    `MetadataReportError` escaped untyped. Its caller is the recovery
    runner's blocked-target callback on the supervisor's lane, and that
    lane survives only a `ReviewPipelineError`: an unconverted fault ended
    the lane thread outright, stopping recovery and ordinary preparation
    with it (#698).
    """
    closed: list[bool] = []

    class _UnreadableConditions:
        def target_connection_conditions(self) -> tuple[object, ...]:
            raise MetadataReportError("target connection condition is unavailable")

        def close(self) -> None:
            closed.append(True)

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(
            "ReviewPipelineRepository", _UnreadableConditions()
        ),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError),
        providers=lambda *_: (),
        propose=lambda *_: "",
        admit=lambda *_: ("", None),
        project=lambda *_: True,
        check_connection=lambda _: None,
        clock=lambda: 6,
    )

    with pytest.raises(ReviewPipelineError) as raised:
        pipeline.connection_conditions()

    assert raised.value.code == "persistence"
    assert isinstance(raised.value.__cause__, MetadataReportError)
    assert closed == [True]


@pytest.mark.parametrize(
    ("seam", "run"),
    (
        (
            "begin_target_connection_check",
            lambda pipeline: pipeline.check_connection("primary"),
        ),
        (
            "hold_target_connection",
            lambda pipeline: pipeline.hold_connection("primary", "transient"),
        ),
        (
            "provider_retry_work_id",
            lambda pipeline: pipeline.run_recovery_successor("report_1"),
        ),
    ),
    ids=("check_connection", "hold_connection", "run_recovery_successor"),
)
def test_every_remaining_bare_call_site_converts_and_still_releases(
    seam: str, run: Callable[[ReviewPreparationPipeline], object]
) -> None:
    """The seam's shape is universal, not one exception and three gaps.

    `check_connection`'s two durable transitions, `hold_connection` and
    `run_recovery_successor` released the handle on every path but converted
    nothing, so the bound repository's own error reached their callers
    untyped -- the recovery classifier and the web boundary both, neither of
    which this layer had promised a raw adapter fault. Each converts now,
    and the `finally` still runs (#698).
    """
    closed: list[str] = []
    fault = MetadataReportError("durable transition is unavailable")

    class _UnwritableRepository:
        def __init__(self, seam: str) -> None:
            self._seam = seam

        def __getattr__(self, name: str):
            def call(*_arguments: object, **_keywords: object):
                if name == self._seam:
                    raise fault
                return None

            return call

        def close(self) -> None:
            closed.append(self._seam)

    def pipeline_for(seam: str) -> ReviewPreparationPipeline:
        return ReviewPreparationPipeline(
            repository_factory=lambda: cast(
                "ReviewPipelineRepository", _UnwritableRepository(seam)
            ),
            libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError),
            extract=lambda *_: (_ for _ in ()).throw(AssertionError),
            providers=lambda *_: (),
            propose=lambda *_: "",
            admit=lambda *_: ("", None),
            project=lambda *_: True,
            check_connection=lambda _: None,
            clock=lambda: 6,
        )

    closed.clear()
    with pytest.raises(ReviewPipelineError) as raised:
        run(pipeline_for(seam))
    assert raised.value.code == "persistence"
    assert raised.value.__cause__ is fault
    assert closed == [seam]


def test_cover_consideration_factory_fault_is_recorded_as_persistence() -> None:
    """The factory is not separable from the rest of the cover stage.

    The bound factory is `composition._compose_cover_considerations`, which
    writes twice through the repository while it builds the service --
    `bind_current_cover_policy` before construction and
    `bind_current_cover_replay` after it. So a store failure can reach
    `prepare_cover` from the factory call, and `persistence` is the identity
    the stage answers with for every part of itself it cannot attribute
    (#698).
    """
    fault = MetadataReportError("binding the current cover policy failed")

    class _StagedRepository:
        def read_staged_cover_preparation(
            self, _action: PipelineAction
        ) -> tuple[str, str, bool]:
            return ("report_" + "1" * 64, "a" * 64, False)

    def unreachable(*_arguments: object, **_keywords: object):
        raise AssertionError

    stages = ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unreachable,
        catalogue_reader=cast("CatalogueReader", unreachable),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=unreachable,
        generate_report=unreachable,
        create_proposal_set=unreachable,
        admit_review_case=unreachable,
        cover_considerations=lambda _repository: (_ for _ in ()).throw(fault),
        recovery_observer=cast("ProviderFailureObserver", unreachable),
        drain_outbox=unreachable,
        discovery_lock=threading.Lock(),
        clock=lambda: 7,
    )
    action = PipelineAction(
        "review_work_" + "a" * 64,
        "review_scan_" + "b" * 64,
        "projection",
        1,
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        1,
        "c" * 64,
        "item-1",
        "d" * 64,
        1,
        review_case_id="review_case_" + "e" * 64,
        source_fingerprint="f" * 64,
    )

    with pytest.raises(ReviewPipelineError) as raised:
        stages.prepare_cover(
            cast("ReviewPipelineRepository", _StagedRepository()), action
        )
    assert raised.value.code == "persistence"
    assert raised.value.__cause__ is fault


def test_stages_outbox_failure_settles_the_claimed_successor() -> None:
    class Repository:
        def __init__(self, successor: PipelineAction) -> None:
            self.successor = successor
            self.failures: list[tuple[PipelineAction, str, bool]] = []

        def advance_review_pipeline_action(self, *_args, **_kwargs):
            return self.successor

        def fail_review_pipeline_action(self, action, code, retryable, *_args):
            self.failures.append((action, code, retryable))

    def unreachable(*_arguments: object, **_keywords: object):
        raise AssertionError

    stages = ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unreachable,
        catalogue_reader=cast("CatalogueReader", unreachable),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=unreachable,
        generate_report=unreachable,
        create_proposal_set=unreachable,
        admit_review_case=unreachable,
        cover_considerations=unreachable,
        recovery_observer=cast("ProviderFailureObserver", unreachable),
        drain_outbox=lambda _repository: (_ for _ in ()).throw(
            ReviewPipelineError("persistence")
        ),
        discovery_lock=threading.Lock(),
        clock=lambda: 7,
    )
    action = PipelineAction(
        "action-1",
        "scan-1",
        "projection",
        1,
        "primary",
        "ebooks",
        "library-id",
        _PRODUCER,
        1,
        "a" * 64,
        "item-1",
        "b" * 64,
        0,
        "report-1",
        review_case_id="case-1",
        source_fingerprint="c" * 64,
    )
    successor = replace(action, action_id="action-2", stage="proposal")
    repository = Repository(successor)
    pipeline = ReviewPreparationPipeline(
        repository_factory=unreachable,
        libraries=(("primary", "ebooks", "library-id", _PRODUCER),),
        discover=unreachable,
        extract=unreachable,
        providers=unreachable,
        propose=unreachable,
        admit=unreachable,
        project=unreachable,
        after_advance=stages.after_advance,
        clock=lambda: 7,
    )

    assert pipeline._advance(repository, action, "complete", claim_next=True) is None
    assert [(successor, "persistence", True)] == repository.failures


def test_checking_condition_allows_admission_replay_and_rejects_stale_finish_epoch(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
    action = repository.claim_review_pipeline_action(2)
    assert action is not None
    repository.defer_for_target_connection(action, "unavailable", 3)
    first = repository.begin_target_connection_check("primary", 4)
    assert first is not None
    assert "checking" == first.state
    repository.close()

    concurrent = open_repository(application_path)
    concurrent.request_review_scan("primary", "history", "lib-2", _PRODUCER, 5)
    concurrent.request_review_scan("primary", "history", "lib-2", _PRODUCER, 5)
    assert "checking" == concurrent.target_connection_conditions()[0].state
    assert concurrent.finish_target_connection_check(
        "primary", first.epoch, "unavailable", 6
    )
    paused = concurrent.target_connection_conditions()[0]
    assert ("paused", 2) == (paused.state, paused.affected)
    second = concurrent.begin_target_connection_check("primary", 7)
    assert second is not None
    assert not concurrent.finish_target_connection_check(
        "primary", first.epoch, None, 8
    )
    assert ("checking", second.epoch) == (
        concurrent.target_connection_conditions()[0].state,
        concurrent.target_connection_conditions()[0].epoch,
    )
    assert concurrent.finish_target_connection_check(
        "primary", second.epoch, "unavailable", 9
    )
    assert ("paused", 2) == (
        concurrent.target_connection_conditions()[0].state,
        concurrent.target_connection_conditions()[0].affected,
    )
    concurrent.close()


def test_status_reads_all_latest_library_progress_and_pause_in_one_snapshot(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    first = repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    second = repository.request_review_scan(
        "primary", "history", "lib-2", _PRODUCER, 11
    )
    repository.set_review_pipeline_paused(True, 12)
    progress, paused = repository.review_pipeline_status(
        (("primary", "fiction"), ("primary", "history"), ("primary", "missing"))
    )
    repository.close()

    assert paused
    assert progress == (first, second, None)
    repository = open_repository(application_path)
    with pytest.raises(MetadataReportError):
        repository.review_pipeline_status((("x" * 65, "fiction"),))
    repository.close()


def test_actual_provider_credential_failure_projects_once_at_provider_scope(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("item", "1" * 64),), 12
    )
    repository.close()
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: (_ for _ in ()).throw(
            CredentialError("credential unavailable", provider="hardcover-main")
        ),
        providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: (_ for _ in ()).throw(AssertionError()),
        clock=lambda: 16,
    )
    assert pipeline.run_next()
    repository = open_repository(application_path)
    signals = cast(
        "list[dict[str, object]]", repository.operational_snapshot()["signals"]
    )
    repository.close()
    assert {
        "kind": "authentication_failure",
        "cause": "provider_authentication",
        "scope": "connection",
        "connection": "hardcover-main",
        "library": None,
        "category": None,
        "work_id": None,
        "field": None,
    } in signals


def test_observed_provider_authentication_remains_in_complete_report(
    application_path: Path,
) -> None:
    base = _report()
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(
            ProviderObservation("google-main", ProviderStatus.AUTHORIZATION),
            ProviderObservation("hardcover-main", ProviderStatus.AUTHENTICATION),
        ),
    )
    entry = _entry("item", report.file.identity)
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, 1, "a" * 64, (entry,), 12)
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None
    repository.publish(
        report,
        lambda: True,
        None,
        lambda value, published: value.complete_review_extraction_publication(
            extraction,
            published.report_id,
            14,
        ),
    )
    work = repository._connection.execute(
        "SELECT stage,state,failure_code,failure_root FROM review_preparation_work"
    ).fetchone()
    retained = MetadataReport.from_json(repository.get(report.report_id))
    repository.close()
    assert work == ("proposal", "queued", None, None)
    assert tuple(
        (provider.provider, provider.status) for provider in retained.providers
    ) == (
        ("google-main", ProviderStatus.AUTHORIZATION),
        ("hardcover-main", ProviderStatus.AUTHENTICATION),
    )


def test_the_failure_code_allow_list_and_its_normalisation_stay_one_vocabulary() -> (
    None
):
    """The CHECK and the set that normalises against it are held equal.

    `sqlite_master` stores the DDL verbatim, so the CHECK cannot be generated
    from `_FAILURE_CODES` and the two lists are written twice. They drifted
    three times, and each drift is silent: a code the application deliberately
    chose collapses to the anonymous `failed`, and the owner is told the work
    failed rather than which side failed. Reading the allow-list back out of
    the DDL is what makes the next divergence fail here instead (#734).
    """
    allow_list = re.search(
        r"failure_code IS NULL OR failure_code IN \(([^)]+)\)", _REVIEW_WORK_SQL
    )
    assert allow_list is not None
    assert _FAILURE_CODES == frozenset(
        value.strip().strip("'") for value in allow_list.group(1).split(",")
    )
    assert "persistence" in _FAILURE_CODES


def test_thousand_work_three_library_backlog_is_bounded_and_fair(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    libraries = (("fiction", 334), ("history", 333), ("science", 333))
    for offset, (library, _count) in enumerate(libraries):
        repository.request_review_scan(
            "primary", library, f"lib-{offset}", _PRODUCER, 10 + offset
        )
    for offset, (library, count) in enumerate(libraries):
        discovery = repository.claim_review_pipeline_action(20 + offset)
        assert discovery is not None
        assert discovery.library == library
        entries = tuple(
            replace(
                _entry(f"{library}-{index}", f"{index:064x}"),
                library=library,
                library_id=f"lib-{offset}",
                updated_at=1000 + index,
            )
            for index in range(count)
        )
        repository.complete_review_discovery(
            discovery, offset + 1, f"{offset + 1:064x}", entries, 30 + offset
        )
    total = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_work"
    ).fetchone()
    claimed: list[tuple[str, str]] = []
    for timestamp in range(40, 49):
        action = repository.claim_review_pipeline_action(timestamp)
        assert action is not None
        assert action.item_id is not None
        claimed.append((action.library, action.item_id))
        repository.fail_review_pipeline_action(action, "failed", False, timestamp)
    repository.close()
    assert total == (1000,)
    for start in range(0, 9, 3):
        assert {library for library, _ in claimed[start : start + 3]} == {
            item[0] for item in libraries
        }
    for library, count in libraries:
        assert [
            item for claimed_library, item in claimed if claimed_library == library
        ] == sorted(f"{library}-{index}" for index in range(count))[:3]


def test_ordinal_claim_index_keeps_first_ready_cost_flat_across_cohort_sizes(
    application_path: Path,
) -> None:
    """#826: the first eligible ordinal does not pay to sort its whole cohort."""

    def first_ready_steps(count: int) -> tuple[int, tuple[tuple[object, ...], ...]]:
        repository = open_repository(application_path.with_name(f"{count}.sqlite3"))
        repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
        discovery = repository.claim_review_pipeline_action(2)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            tuple(
                _entry(f"item-{index:04d}", f"{index:064x}") for index in range(count)
            ),
            3,
        )
        sql = (
            "SELECT work_id FROM review_preparation_work WHERE state='queued' "
            "AND library_name=? ORDER BY ordinal LIMIT 1"
        )
        plan = tuple(
            repository._connection.execute("EXPLAIN QUERY PLAN " + sql, ("fiction",))
        )
        steps = 0

        def count_steps() -> int:
            nonlocal steps
            steps += 1
            return 0

        repository._connection.set_progress_handler(count_steps, 1)
        row = repository._connection.execute(sql, ("fiction",)).fetchone()
        repository._connection.set_progress_handler(None, 0)
        repository.close()
        assert row is not None
        return steps, plan

    ten_steps, ten_plan = first_ready_steps(10)
    thousand_steps, thousand_plan = first_ready_steps(1_000)

    for plan in (ten_plan, thousand_plan):
        assert any("review_preparation_work_aged_claim" in str(row) for row in plan)
        assert not any("TEMP B-TREE" in str(row) for row in plan)
    # A fixed first row has a fixed index walk. The generous bound leaves room
    # for SQLite's version-specific setup without permitting cohort-size work.
    assert thousand_steps <= ten_steps + 30


def test_due_promotion_skips_a_large_delayed_prefix_with_bounded_vm_steps(
    application_path: Path,
) -> None:
    """#826: future backoff rows never become a linear claim prefix."""

    def claim_steps(count: int) -> int:
        repository = open_repository(
            application_path.with_name(f"delayed-{count}.sqlite3")
        )
        repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 1)
        discovery = repository.claim_review_pipeline_action(2)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            tuple(
                _entry(f"item-{index:04d}", f"{index:064x}") for index in range(count)
            ),
            3,
        )
        repository._connection.execute(
            "UPDATE review_preparation_work SET state='delayed',retry_not_before=100"
        )
        repository._connection.execute(
            "UPDATE review_preparation_work SET state='queued',retry_not_before=0 "
            "WHERE ordinal=?",
            (count - 1,),
        )
        repository._connection.commit()
        steps = 0

        def count_steps() -> int:
            nonlocal steps
            steps += 1
            return 0

        repository._connection.set_progress_handler(count_steps, 1)
        action = repository.claim_review_pipeline_action(4)
        repository._connection.set_progress_handler(None, 0)
        repository.close()
        assert action is not None
        assert action.item_id == f"item-{count - 1:04d}"
        return steps

    ten_steps = claim_steps(10)
    thousand_steps = claim_steps(1_000)
    assert thousand_steps <= ten_steps + 80


def test_former_updated_time_claim_index_requires_a_clean_rebuild(
    application_path: Path,
) -> None:
    """#826: the exact-current schema rejects the predecessor without rewriting it."""

    repository = open_repository(application_path)
    repository.close()
    connection = sqlite3.connect(application_path)
    connection.execute("DROP INDEX review_preparation_work_aged_claim")
    connection.execute(
        "CREATE INDEX review_preparation_work_aged_claim "
        "ON review_preparation_work(state,library_name,updated_at,ordinal)"
    )
    connection.commit()
    connection.close()
    before = application_path.read_bytes()

    with pytest.raises(
        MetadataReportError, match="database is unavailable or incompatible"
    ):
        open_repository(application_path, create=False)

    assert application_path.read_bytes() == before


def test_oversized_fairness_cursor_fails_closed(application_path: Path) -> None:
    repository = open_repository(application_path)
    repository._connection.execute("PRAGMA ignore_check_constraints=ON")
    repository._connection.execute(
        "UPDATE review_preparation_control SET last_library=? WHERE singleton=1",
        ("x" * 65,),
    )
    repository._connection.commit()
    repository._connection.execute("PRAGMA ignore_check_constraints=OFF")

    with pytest.raises(
        MetadataReportError, match="review preparation control is invalid"
    ):
        repository.claim_review_pipeline_action(10)
    repository.close()


def test_claims_keep_discovery_order_across_normal_successor_stages(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        (
            _entry("first", "1" * 64),
            _entry("second", "2" * 64),
            _entry("third", "3" * 64),
        ),
        12,
    )
    action = repository.claim_review_pipeline_action(13)
    assert action is not None
    observed = []
    for timestamp in range(14, 18):
        assert action is not None
        observed.append((action.stage, action.item_id))
        action = repository.advance_review_pipeline_action(
            action, None, timestamp, True
        )
    repository.close()
    assert observed == [
        ("extraction", "first"),
        ("provider", "first"),
        ("proposal", "first"),
        ("admission", "first"),
    ]


def test_advance_atomically_claims_recoverable_next_action(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    repository.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        (replace(_entry("item-1", report.file.identity), library="ebooks"),),
        12,
    )
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None
    provider = repository.advance_review_pipeline_action(
        extraction, report.report_id, 14, True
    )
    assert provider is not None
    assert provider.stage == "provider"
    repository.close()

    reopened = open_repository(application_path)
    reopened.recover_review_pipeline((("primary", "ebooks", "lib-1", _PRODUCER),))
    recovered = reopened.claim_review_pipeline_action(15)
    reopened.close()
    assert recovered is not None
    assert (recovered.action_id, recovered.stage, recovered.attempt) == (
        provider.action_id,
        "provider",
        2,
    )


def test_admission_target_not_found_is_typed_stale(application_path: Path) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.close()

    with (
        patch.object(composition, "CoverArtifactStore", return_value=object()),
        patch.object(
            composition,
            "_require_report_current_loaded",
            side_effect=TargetNotFoundError("target item is unavailable"),
        ),
    ):
        result = json.loads(
            composition.admit_review_case_loaded(
                object(), application_path, Path("ignored"), proposal.proposal_set_id
            )
        )

    repository = open_repository(application_path)
    cases = repository._connection.execute(
        "SELECT COUNT(*) FROM review_cases"
    ).fetchone()
    repository.close()
    assert result["status"] == "stale"
    assert result["review_case_id"] is None
    assert cases == (0,)


def test_request_discovery_claim_and_restart_are_persisted(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    first = repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    duplicate = repository.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 11
    )
    assert duplicate.scan_id == first.scan_id
    discovery = repository.claim_review_pipeline_action(12)
    assert discovery is not None
    assert discovery is not None
    assert discovery.stage == "discovery"
    entries = (
        CatalogueEntry(
            "primary",
            "fiction",
            "lib-1",
            "slow",
            1,
            "Slow synthetic work",
            ("Example Author",),
            True,
            False,
            "singular-supported",
            "1" * 64,
            "/slow.epub",
            "epub",
            "books",
        ),
        CatalogueEntry(
            "primary",
            "fiction",
            "lib-1",
            "fast",
            1,
            "Fast synthetic work",
            ("Example Author",),
            True,
            False,
            "singular-supported",
            "2" * 64,
            "/fast.epub",
            "epub",
            "books",
        ),
    )
    repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 13)
    one = repository.claim_review_pipeline_action(14)
    two = repository.claim_review_pipeline_action(14)
    assert {
        cast(PipelineAction, one).item_id,
        cast(PipelineAction, two).item_id,
    } == {"slow", "fast"}
    repository.close()

    reopened = open_repository(application_path)
    reopened.recover_review_pipeline((("primary", "fiction", "lib-1", _PRODUCER),))
    recovered = {
        cast(PipelineAction, reopened.claim_review_pipeline_action(15)).item_id,
        cast(PipelineAction, reopened.claim_review_pipeline_action(15)).item_id,
    }
    assert recovered == {"slow", "fast"}
    progress = reopened.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 16
    )
    assert progress.rescan_requested
    reopened.close()


def test_restart_requeues_claims_across_scopes_without_creating_recovery_effects(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    producers = (
        ("primary", "ebooks", "library-1", _PRODUCER),
        ("secondary", "audio", "library-2", _PRODUCER),
        ("tertiary", "other", "library-3", _PRODUCER),
    )
    for index, (connection, library, library_id, producer) in enumerate(producers):
        repository.request_review_scan(connection, library, library_id, producer, index)
    discoveries = (
        repository.claim_review_pipeline_action(10),
        repository.claim_review_pipeline_action(11),
    )
    assert [("primary", "ebooks"), ("secondary", "audio")] == [
        (action.connection, action.library)
        for action in discoveries
        if action is not None
    ]
    for index, action in enumerate(discoveries):
        assert action is not None
        repository.complete_review_discovery(
            action,
            1,
            f"{index + 1:064x}",
            (
                replace(
                    _entry(f"running-{index}", f"{index + 1:064x}"),
                    connection=action.connection,
                    library=action.library,
                    library_id=action.library_id,
                ),
            ),
            12 + index,
        )
    third_discovery = repository.claim_review_pipeline_action(14)
    assert third_discovery is not None
    assert "discovery" == third_discovery.stage
    repository.complete_review_discovery(
        third_discovery,
        1,
        "3" * 64,
        (
            replace(
                _entry("unrelated", "3" * 64),
                connection=third_discovery.connection,
                library=third_discovery.library,
                library_id=third_discovery.library_id,
            ),
        ),
        15,
    )
    running = (
        repository.claim_review_pipeline_action(16),
        repository.claim_review_pipeline_action(17),
    )
    assert all(
        action is not None and action.stage == "extraction" for action in running
    )
    running_ids = {cast(PipelineAction, action).action_id for action in running}
    assert (
        2
        == repository._connection.execute(
            "SELECT count(*) FROM review_preparation_work WHERE state='running'"
        ).fetchone()[0]
    )
    repository.close()

    def provider_spy(*_arguments: object) -> tuple[ProviderStatus, ...]:
        raise AssertionError("restart recovery must not request a provider")

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=producers,
        discover=lambda *_arguments: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_arguments: (_ for _ in ()).throw(AssertionError()),
        providers=provider_spy,
        propose=lambda *_arguments: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_arguments: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_arguments: (_ for _ in ()).throw(AssertionError()),
    )
    pipeline.recover()

    reopened = open_repository(application_path)
    states = reopened._connection.execute(
        "SELECT work_id,state FROM review_preparation_work WHERE work_id IN (?,?) "
        "ORDER BY work_id",
        tuple(sorted(running_ids)),
    ).fetchall()
    assert [(work_id, "queued") for work_id in sorted(running_ids)] == states
    assert (0, 0) == reopened._connection.execute(
        "SELECT (SELECT count(*) FROM metadata_reports),"
        "(SELECT count(*) FROM provider_recovery_outbox)"
    ).fetchone()
    resumed = reopened.claim_review_pipeline_action(18)
    assert resumed is not None and resumed.action_id not in running_ids
    reopened.fail_review_pipeline_action(resumed, "failed", False, 19)
    recovered = reopened.claim_review_pipeline_action(20)
    assert recovered is not None and recovered.action_id in running_ids
    reopened.close()


@pytest.mark.parametrize(
    "cursor",
    (
        (True, "recovery_outbox_" + "a" * 64),
        (-1, "recovery_outbox_" + "a" * 64),
        (MAX_TIMESTAMP + 1, "recovery_outbox_" + "a" * 64),
        (0, "not-an-outbox-id"),
        [0, "recovery_outbox_" + "a" * 64],
    ),
)
def test_outbox_cursor_rejects_malformed_or_out_of_range_values(
    application_path: Path, cursor: object
) -> None:
    repository = open_repository(application_path)
    with pytest.raises(MetadataReportError, match="outbox query is invalid"):
        repository.pending_provider_recovery_outbox(after=cursor)
    repository.close()


def test_outbox_cursor_accepts_the_persisted_timestamp_and_id_boundaries(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    assert () == repository.pending_provider_recovery_outbox(
        after=(MAX_TIMESTAMP, "recovery_outbox_" + "f" * 64)
    )
    repository.close()


def test_outbox_acknowledgement_failure_keeps_the_real_row_pending_for_replay(
    application_path: Path,
) -> None:
    class CommitFailingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection
            self.fail_commit = True

        @property
        def in_transaction(self) -> bool:
            return self._connection.in_transaction

        def execute(self, statement: str, parameters: tuple[object, ...] = ()):
            return self._connection.execute(statement, parameters)

        def commit(self) -> None:
            if self.fail_commit:
                raise sqlite3.OperationalError("injected acknowledgement failure")
            self._connection.commit()

        def rollback(self) -> None:
            self._connection.rollback()

    repository = open_repository(application_path)
    outbox = repository._recovery_outbox
    report = _report()
    repository.publish(report, lambda: True)
    outbox.enqueue(
        report.target.connection,
        report.target.library,
        report.target.item,
        (None, report.report_id, None, None, "report_only"),
        7,
    )
    pending = repository.pending_provider_recovery_outbox()
    assert 1 == len(pending)
    proxy = CommitFailingConnection(repository._connection)
    with patch.object(outbox, "_connection", cast(sqlite3.Connection, proxy)):
        with pytest.raises(MetadataReportError, match="outbox is unavailable"):
            outbox.complete_provider_recovery_outbox(pending[0][0], delivered_at=7)
    assert pending == repository.pending_provider_recovery_outbox()
    outbox.complete_provider_recovery_outbox(pending[0][0], delivered_at=7)
    outbox.complete_provider_recovery_outbox(pending[0][0], delivered_at=7)
    assert () == repository.pending_provider_recovery_outbox()
    repository.close()


def test_repeated_queued_request_advances_its_freshness_anchor(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    first = repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)

    repeated = repository.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 11
    )

    assert repeated.scan_id == first.scan_id
    assert repeated.state == "queued"
    assert repeated.updated_at == 11
    timing = repository._connection.execute(
        "SELECT requested_at,updated_at FROM review_preparation_scans WHERE scan_id=?",
        (first.scan_id,),
    ).fetchone()
    repository.close()
    assert timing == (11, 11)


def test_request_during_discovery_forces_a_successor_scan(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None

    requested = repository.request_review_scan(
        "primary", "fiction", "lib-1", _PRODUCER, 12
    )

    assert requested.state == "discovering"
    assert requested.rescan_requested
    assert requested.updated_at == 12
    repository.complete_review_discovery(discovery, 1, "a" * 64, (), 13)
    processing = repository.review_pipeline_progress("primary", "fiction")
    assert processing is not None
    assert processing.rescan_requested
    assert repository.claim_review_pipeline_action(14) is None
    queued = repository.review_pipeline_progress("primary", "fiction")
    assert queued is not None
    assert queued.state == "queued"
    repository.close()


def test_failed_discovery_preserves_the_requested_successor(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 12)

    with patch.object(
        repository,
        "_has_pending_field_effect_manual_return",
        return_value=True,
    ):
        repository.fail_review_pipeline_action(discovery, "failed", False, 13)

    progress = repository.review_pipeline_progress("primary", "fiction")
    assert progress is not None
    assert progress.state == "queued"
    assert not progress.rescan_requested
    successor = repository.claim_review_pipeline_action(14)
    assert successor is not None
    assert successor.stage == "discovery"
    repository.close()


def test_failed_processing_preserves_the_requested_successor(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("item-1", "1" * 64),), 12
    )
    work = repository.claim_review_pipeline_action(13)
    assert work is not None
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 14)
    repository.fail_review_pipeline_action(work, "failed", False, 15)

    with patch.object(
        repository,
        "_has_pending_field_effect_manual_return",
        return_value=True,
    ):
        assert repository.claim_review_pipeline_action(16) is None
    progress = repository.review_pipeline_progress("primary", "fiction")
    assert progress is not None
    assert progress.state == "queued"
    successor = repository.claim_review_pipeline_action(17)
    assert successor is not None
    assert successor.stage == "discovery"
    repository.close()


def test_complete_empty_discovery_reaches_a_bounded_summary(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    action = repository.claim_review_pipeline_action(11)
    assert action is not None
    repository.complete_review_discovery(action, 1, "b" * 64, (), 12)
    assert repository.claim_review_pipeline_action(13) is None
    progress = repository.review_pipeline_progress("primary", "fiction")
    assert progress is not None
    assert progress is not None
    assert (progress.state, progress.total) == ("completed", 0)
    changed_producer = "d" * 64
    repository.recover_review_pipeline(
        (("primary", "fiction", "lib-2", changed_producer),)
    )
    assert repository.claim_review_pipeline_action(14) is None
    unchanged = repository.review_pipeline_progress("primary", "fiction")
    assert unchanged is not None
    assert unchanged.state == "completed"
    repository.request_review_scan("primary", "fiction", "lib-2", changed_producer, 15)
    refreshed = repository.claim_review_pipeline_action(16)
    assert refreshed is not None
    assert refreshed.library_id == "lib-2"
    assert refreshed.producer_fingerprint == changed_producer
    repository.close()


def test_failed_rediscovery_cannot_claim_queued_children(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("queued", "9" * 64),), 12
    )
    changed_producer = "d" * 64
    repository.recover_review_pipeline(
        (("primary", "fiction", "lib-2", changed_producer),)
    )
    rediscovery = repository.claim_review_pipeline_action(13)
    assert rediscovery is not None
    assert rediscovery.stage == "discovery"
    repository.fail_review_pipeline_action(rediscovery, "transient", True, 14)

    assert repository.claim_review_pipeline_action(15) is None
    progress = repository.review_pipeline_progress("primary", "fiction")
    child = repository._connection.execute(
        "SELECT state FROM review_preparation_work"
    ).fetchone()
    repository.close()

    assert progress is not None
    assert progress.state == "completed_with_failures"
    assert child == ("queued",)


def test_third_crashed_stage_is_terminal_instead_of_overflowing_claim(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("crashed", "9" * 64),), 12
    )
    action = repository.claim_review_pipeline_action(13)
    assert action is not None
    repository._connection.execute(
        "UPDATE review_preparation_work SET attempt=3 WHERE work_id=?",
        (action.action_id,),
    )
    repository._connection.commit()
    repository.recover_review_pipeline((("primary", "fiction", "lib-1", _PRODUCER),))
    assert repository.claim_review_pipeline_action(14) is None
    progress = repository.review_pipeline_progress("primary", "fiction")
    assert progress is not None
    assert (progress.state, progress.checked, progress.blocked) == (
        "completed_with_failures",
        1,
        1,
    )
    repository.close()


def test_complete_scan_tombstones_only_confirmed_absence(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        10,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, 1, "c" * 64, (), 12)
    assert repository.claim_review_pipeline_action(13) is None
    confirmation = repository.claim_review_pipeline_action(14)
    assert confirmation is not None
    assert confirmation.stage == "discovery"
    repository.complete_review_discovery(confirmation, 2, "c" * 64, (), 15)
    assert repository.claim_review_pipeline_action(16) is None
    repository.close()

    connection = sqlite3.connect(application_path)
    pointer = connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    transition = connection.execute(
        "SELECT actor,kind,reason FROM review_case_transitions "
        "ORDER BY checkpoint DESC LIMIT 1"
    ).fetchone()
    connection.close()
    assert pointer == (None, "tombstoned")
    assert transition == ("review-preparation-v1", "tombstoned", "absent")

    repository = open_repository(application_path)
    assert repository._connection.execute(
        "SELECT COUNT(*) FROM current_review_packages"
    ).fetchone() == (0,)
    assert repository.count_current_review_cases(None) == (0, 0)
    assert repository.read_review_attention(None, 10, 0).total == 0
    repository.close()

    repository = open_repository(application_path)
    reappeared = json.loads(repository.admit_review_case(proposal))
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    repository.close()
    assert reappeared["status"] == "admitted"
    assert pointer == (reappeared["review_case_id"], "admitted")


def test_complete_scan_retires_present_item_that_became_ineligible(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        10,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    ineligible = replace(
        _entry(report.target.item, report.file.identity),
        library=report.target.library,
        classification="singular-unmounted",
        mount=None,
    )
    repository.complete_review_discovery(discovery, 1, "c" * 64, (ineligible,), 12)
    assert repository.claim_review_pipeline_action(13) is None
    confirmation = repository.claim_review_pipeline_action(14)
    assert confirmation is not None
    repository.complete_review_discovery(confirmation, 2, "c" * 64, (ineligible,), 15)
    assert repository.claim_review_pipeline_action(16) is None
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    transition = repository._connection.execute(
        "SELECT actor,kind,reason FROM review_case_transitions "
        "ORDER BY checkpoint DESC LIMIT 1"
    ).fetchone()
    package_count = repository._connection.execute(
        "SELECT COUNT(*) FROM current_review_packages"
    ).fetchone()
    library_count = repository.count_current_review_cases(None)
    attention_count = repository.read_review_attention(None, 10, 0).total
    reopened = json.loads(repository.admit_review_case(proposal))
    repository.close()

    assert pointer == (None, "retired")
    assert transition == ("review-preparation-v1", "retired", "ineligible")
    assert package_count == (0,)
    assert library_count == (0, 0)
    assert attention_count == 0
    assert reopened["status"] == "admitted"
    assert reopened["review_case_id"] == admitted["review_case_id"]


def test_confirmation_discovery_cancels_absence_after_reappearance(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        10,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, 1, "c" * 64, (), 12)
    assert repository.claim_review_pipeline_action(13) is None
    confirmation = repository.claim_review_pipeline_action(14)
    assert confirmation is not None
    reappeared = replace(
        _entry(report.target.item, report.file.identity),
        library=report.target.library,
    )
    repository.complete_review_discovery(confirmation, 2, "c" * 64, (reappeared,), 15)
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    staged = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_absences"
    ).fetchone()
    repository.close()

    assert pointer == (admitted["review_case_id"], "admitted")
    assert staged == (0,)


def test_changed_retirement_candidate_is_discarded_without_third_scan(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        10,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, 1, "c" * 64, (), 12)
    assert repository.claim_review_pipeline_action(13) is None
    confirmation = repository.claim_review_pipeline_action(14)
    assert confirmation is not None
    ineligible = replace(
        _entry(report.target.item, report.file.identity),
        library=report.target.library,
        classification="multiple-files",
    )
    repository.complete_review_discovery(confirmation, 2, "d" * 64, (ineligible,), 15)

    assert repository.claim_review_pipeline_action(16) is None
    assert repository.claim_review_pipeline_action(17) is None
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    staged = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_absences"
    ).fetchone()
    repository.close()
    assert pointer == (admitted["review_case_id"], "admitted")
    assert staged == (0,)


def test_later_rediscovery_confirms_new_absence_before_tombstone(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        10,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    present = replace(
        _entry(report.target.item, report.file.identity),
        library=report.target.library,
    )
    repository.complete_review_discovery(discovery, 1, "c" * 64, (present,), 12)
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        13,
    )
    work = repository.claim_review_pipeline_action(14)
    while work is not None:
        repository.advance_review_pipeline_action(work, None, 15)
        work = repository.claim_review_pipeline_action(16)
    later = repository.claim_review_pipeline_action(17)
    assert later is not None
    repository.complete_review_discovery(later, 2, "d" * 64, (), 18)

    assert repository.claim_review_pipeline_action(19) is None
    confirmation = repository.claim_review_pipeline_action(20)
    assert confirmation is not None
    assert confirmation.stage == "discovery"
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    assert pointer == (admitted["review_case_id"], "admitted")

    repository.complete_review_discovery(confirmation, 3, "e" * 64, (), 21)
    assert repository.claim_review_pipeline_action(22) is None
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    repository.close()
    assert pointer == (None, "tombstoned")


def test_failed_discovery_cannot_tombstone_current_lineage(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    repository.close()
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic")),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: True,
        clock=lambda: 20,
    )
    pipeline.request("primary", "ebooks")
    assert pipeline.run_next(1)
    repository = open_repository(application_path)
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    repository.close()
    assert pointer == (admitted["review_case_id"], "admitted")


def test_later_work_failure_discards_staged_ineligible_retirement(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        10,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        1,
        "d" * 64,
        (
            replace(
                _entry(report.target.item, report.file.identity),
                library=report.target.library,
                classification="singular-unmounted",
                mount=None,
            ),
            replace(
                _entry("later-failure", "9" * 64),
                library=report.target.library,
            ),
        ),
        12,
    )
    work = repository.claim_review_pipeline_action(13)
    assert work is not None
    repository.fail_review_pipeline_action(work, "failed", False, 14)
    assert repository.claim_review_pipeline_action(15) is None
    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    staged = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_absences"
    ).fetchone()
    progress = repository.review_pipeline_progress(
        report.target.connection, report.target.library
    )
    repository.close()
    assert progress is not None
    assert pointer == (admitted["review_case_id"], "admitted")
    assert staged == (0,)
    assert progress.state == "completed_with_failures"


def test_manual_return_successor_discards_failed_scan_absence_candidates(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        10,
    )
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery,
        1,
        "d" * 64,
        (
            replace(
                _entry(report.target.item, report.file.identity),
                library=report.target.library,
                classification="singular-unmounted",
                mount=None,
            ),
            replace(
                _entry("later-failure", "9" * 64),
                library=report.target.library,
            ),
        ),
        12,
    )
    work = repository.claim_review_pipeline_action(13)
    assert work is not None
    repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "lib-1",
        _PRODUCER,
        14,
    )
    repository.fail_review_pipeline_action(work, "failed", False, 15)

    with patch.object(
        repository,
        "_has_pending_field_effect_manual_return",
        return_value=True,
    ):
        assert repository.claim_review_pipeline_action(16) is None

    pointer = repository._connection.execute(
        "SELECT review_case_id,outcome FROM current_review_case_lineages"
    ).fetchone()
    staged = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_absences"
    ).fetchone()
    progress = repository.review_pipeline_progress(
        report.target.connection, report.target.library
    )
    repository.close()
    assert progress is not None
    assert pointer == (admitted["review_case_id"], "admitted")
    assert staged == (0,)
    assert progress.state == "queued"


def test_one_work_progresses_through_every_persisted_stage(
    application_path: Path,
) -> None:
    report = _report()
    proposal = evaluate_report(report)
    entries = [
        CatalogueEntry(
            "primary",
            "ebooks",
            "lib-1",
            "item-1",
            1,
            "Synthetic work",
            ("Example Author",),
            True,
            False,
            "singular-supported",
            report.file.identity,
            "/item-1.epub",
            "epub",
            "books",
        )
    ]
    extracted: list[str] = []
    prepared: list[str] = []

    def publish_report(repository, __: PipelineAction, ___: bool):
        extracted.append(report.report_id)
        repository.publish(report, lambda: True)
        return report.report_id, False

    def publish_proposal(repository, __: str) -> str:
        repository.publish_proposal(proposal)
        return proposal.proposal_set_id

    def admit(repository, __: str) -> tuple[str, str | None]:
        result = json.loads(repository.admit_review_case(proposal))
        return result["status"], result.get("review_case_id")

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (1, "d" * 64, tuple(entries)),
        extract=publish_report,
        providers=lambda *_: (ProviderStatus.DISABLED,),
        propose=publish_proposal,
        admit=admit,
        project=lambda _, action: (action.review_case_id or "").startswith(
            "review_case_"
        ),
        before_projection=lambda _, action: prepared.append(
            action.review_case_id or ""
        ),
        clock=lambda: 20,
    )
    pipeline.request("primary", "ebooks")
    assert sum(pipeline.run_next(1) for _ in range(6)) == 6
    assert not pipeline.run_next(1)
    progress = pipeline.progress("primary", "ebooks")
    assert progress is not None
    assert (progress.state, progress.checked, progress.total, progress.ready) == (
        "completed",
        1,
        1,
        1,
    )
    assert len(extracted) == 1
    assert len(prepared) == 1
    assert prepared[0].startswith("review_case_")

    pipeline.request("primary", "ebooks")
    assert pipeline.run_next(1)
    assert not pipeline.run_next(1)
    assert len(extracted) == 1

    entries[0] = replace(entries[0], updated_at=2, source_fingerprint="9" * 64)
    pipeline.request("primary", "ebooks")
    assert pipeline.run_next(1)
    assert pipeline.run_next(1)
    assert len(extracted) == 2

    changed_producer = "e" * 64
    repository = open_repository(application_path)
    repository.recover_review_pipeline(
        (("primary", "ebooks", "lib-1", changed_producer),)
    )
    discovery = repository.claim_review_pipeline_action(40)
    assert discovery is not None
    assert discovery.producer_fingerprint == changed_producer
    repository.complete_review_discovery(discovery, 2, "e" * 64, tuple(entries), 41)
    restarted = repository.claim_review_pipeline_action(42)
    assert restarted is not None
    repository.close()
    assert restarted.stage == "extraction"
    assert restarted.producer_fingerprint == changed_producer
    assert restarted.report_id is None


def test_terminal_provider_failure_continues_to_proposal(
    application_path: Path,
) -> None:
    proposed: list[str] = []
    extracted: list[str] = []
    clock = [30]
    provider_statuses = [
        (ProviderStatus.CANDIDATES, ProviderStatus.INCOMPLETE),
        (ProviderStatus.CANDIDATES,),
    ]
    report = _report()

    def publish_report(repository, __: PipelineAction, ___: bool) -> tuple[str, bool]:
        extracted.append(report.report_id)
        repository.publish(report, lambda: True)
        return report.report_id, False

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (
            1,
            "e" * 64,
            (
                CatalogueEntry(
                    "primary",
                    "ebooks",
                    "lib-1",
                    "item-1",
                    1,
                    "Blocked synthetic work",
                    (),
                    True,
                    False,
                    "singular-supported",
                    report.file.identity,
                    "/item-1.epub",
                    "epub",
                    "books",
                ),
            ),
        ),
        extract=publish_report,
        providers=lambda *_: provider_statuses.pop(0),
        # `append` returns `None`, so the `or` always yields the proposal id.
        propose=lambda _, report_id: proposed.append(report_id) or "unreachable",  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        admit=lambda *_: ("no_case", None),
        project=lambda *_: True,
        clock=lambda: clock[0],
    )
    pipeline.request("primary", "ebooks")
    assert pipeline.run_next(1)
    assert pipeline.run_next(1)
    assert pipeline.run_next(1)
    assert pipeline.run_next(1)
    assert proposed == [report.report_id]
    repository = open_repository(application_path)
    control = repository._connection.execute(
        "SELECT circuit_failures,circuit_open_until FROM review_preparation_control"
    ).fetchone()
    repository.close()
    assert control == (0, 0)


def test_terminal_unavailable_provider_report_continues_to_proposal(
    application_path: Path,
) -> None:
    report = _report()
    clock = [30]
    extracted: list[str] = []
    provider_calls = 0
    proposed: list[str] = []

    def publish_report(repository, __: PipelineAction, ___: bool) -> tuple[str, bool]:
        extracted.append(report.report_id)
        repository.publish(report, lambda: True)
        return report.report_id, False

    def unavailable(_, __: str) -> tuple[ProviderStatus, ...]:
        nonlocal provider_calls
        provider_calls += 1
        return (ProviderStatus.UNAVAILABLE,)

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (
            1,
            "e" * 64,
            (replace(_entry("item-1", report.file.identity), library="ebooks"),),
        ),
        extract=publish_report,
        providers=unavailable,
        # `append` returns `None`, so the `or` always yields the proposal id.
        propose=lambda _, report_id: proposed.append(report_id) or "proposal-set",  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: True,
        clock=lambda: clock[0],
    )
    pipeline.request("primary", "ebooks")
    assert pipeline.run_next(1)
    assert pipeline.run_next(1)
    assert pipeline.run_next(1)
    assert pipeline.run_next(1)
    assert proposed == [report.report_id]
    assert (len(extracted), provider_calls) == (1, 1)


def test_first_generation_and_refresh_publish_only_after_projection(
    application_path: Path,
) -> None:
    reports = [
        _report(title="Published old", content_digest="a", file_identity="b"),
        _report(title="Staged successor", content_digest="c", file_identity="d"),
        _report(title="Failed successor", content_digest="e", file_identity="f"),
        _report(title="Recovered successor", content_digest="1", file_identity="2"),
        _report(title="Absent successor", content_digest="3", file_identity="4"),
        _report(title="Retired successor", content_digest="5", file_identity="6"),
    ]
    repository = open_repository(application_path)
    repository.enter_shadow_preview(1)
    repository.close()

    def pipeline_for(
        report: MetadataReport,
        *,
        projection: bool = True,
        now: int = 20,
        database: Path | None = None,
    ) -> ReviewPreparationPipeline:
        proposal = evaluate_report(report)
        selected_database = application_path if database is None else database

        def extract(repository, action: PipelineAction, claim_next: bool):
            repository.publish(
                report,
                lambda: True,
                after_publication=lambda bound, current: (
                    bound.complete_review_extraction_publication(
                        action, current.report_id, now, 0, None, claim_next, None
                    )
                ),
            )
            return report.report_id, True

        def propose(repository, _: str) -> str:
            repository.publish_proposal(proposal)
            return proposal.proposal_set_id

        def admit(repository, _: str) -> tuple[str, str | None]:
            value = json.loads(repository.admit_review_case(proposal))
            return value["status"], value.get("review_case_id")

        def project(repository, action: PipelineAction) -> str:
            if not projection:
                raise RuntimeError("synthetic projection failure")
            outcome = repository.persist_shadow_outcome(
                action.review_case_id or "", now, staged_action=action
            )
            return "stale" if outcome == "stale" else "complete"

        return ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(selected_database),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (
                1,
                report.file.content_digest,
                (replace(_entry("item-1", report.file.identity), library="ebooks"),),
            ),
            extract=extract,
            providers=lambda *_: (),
            propose=propose,
            admit=admit,
            project=project,
            clock=lambda: now,
        )

    def titles() -> tuple[str, ...]:
        repository = open_repository(application_path)
        rows = repository.read_review_library(None, 10, 0, None).rows
        repository.close()
        return tuple(json.loads(row[1])["target"]["fields"][0]["value"] for row in rows)

    first = pipeline_for(reports[0])
    first.request("primary", "ebooks")
    assert titles() == ()
    for _ in range(5):
        assert first.run_next(1)
        if _ < 4:
            assert titles() == ()
    assert titles() == ("Published old",)
    repository = open_repository(application_path)
    old_case = repository._connection.execute(
        "SELECT review_case_id FROM current_review_packages"
    ).fetchone()[0]
    assert json.loads(repository.get_review_case(old_case))["current"]
    repository.close()
    assert not first.run_next(1)

    refresh = pipeline_for(reports[1], now=30)
    refresh.request("primary", "ebooks")
    for _ in range(5):
        assert refresh.run_next(1)
        if _ < 4:
            assert titles() == ("Published old",)
            repository = open_repository(application_path)
            assert json.loads(repository.get_review_case(old_case))["current"]
            staged = repository._connection.execute(
                "SELECT review_case_id FROM current_review_case_lineages"
            ).fetchone()
            if staged is not None and staged[0] not in {None, old_case}:
                assert not json.loads(repository.get_review_case(staged[0]))["current"]
            repository.close()
    assert titles() == ("Staged successor",)
    repository = open_repository(application_path)
    assert not json.loads(repository.get_review_case(old_case))["current"]
    repository.close()
    assert not refresh.run_next(1)

    failed_refresh = pipeline_for(reports[2], projection=False, now=40)
    failed_refresh.request("primary", "ebooks")
    for _ in range(5):
        assert failed_refresh.run_next(1)
        assert titles() == ("Staged successor",)
    assert not failed_refresh.run_next(1)
    repository = open_repository(application_path)
    staged_case = repository._connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages"
    ).fetchone()[0]
    published_case = repository._connection.execute(
        "SELECT review_case_id FROM current_review_packages"
    ).fetchone()[0]
    assert staged_case != published_case
    assert json.loads(repository.get_review_case(published_case))["current"]
    assert not json.loads(repository.get_review_case(staged_case))["current"]
    prior_attention = repository.read_review_attention(None, 10, 0).total
    repository.close()

    reopened = open_repository(application_path)
    page = BrowseReviewLibrary(reopened).library(
        lens="all", search="Staged successor", limit=1, cursor=None
    )
    assert page["counts"]["all"] == 1
    assert page["next_cursor"] is None
    assert len(page["items"]) == 1
    assert page["items"][0]["preparation"] is not None
    assert reopened.read_review_attention(None, 10, 0).total == prior_attention
    reopened.close()

    reused_report = open_repository(application_path)
    assert (
        reused_report._connection.execute(
            "UPDATE review_preparation_work SET report_id=? WHERE state='failed'",
            (reports[1].report_id,),
        ).rowcount
        == 1
    )
    reused_report_page = BrowseReviewLibrary(reused_report).library(
        lens="all", search="Staged successor", limit=1, cursor=None
    )
    assert reused_report_page["counts"]["all"] == 1
    assert reused_report_page["items"][0]["preparation"] is not None
    assert reused_report._connection.execute(
        "SELECT report_id FROM current_review_packages"
    ).fetchone() == (reports[1].report_id,)
    assert (
        reused_report._connection.execute(
            "UPDATE review_preparation_work SET report_id=? WHERE state='failed'",
            (reports[2].report_id,),
        ).rowcount
        == 1
    )
    reused_report.close()

    recovery = pipeline_for(reports[3], now=50)
    recovery.request("primary", "ebooks")
    recovery_steps = 0
    while recovery.run_next(1):
        recovery_steps += 1
        assert recovery_steps <= 5
    assert recovery_steps > 0
    recovered = open_repository(application_path)
    recovered_page = BrowseReviewLibrary(recovered).library(
        lens="all", search="Recovered successor", limit=1, cursor=None
    )
    recovered_case = recovered._connection.execute(
        "SELECT review_case_id FROM current_review_packages"
    ).fetchone()[0]
    assert recovered_page["items"][0]["preparation"] is None
    assert not json.loads(recovered.get_review_case(published_case))["current"]
    assert json.loads(recovered.get_review_case(recovered_case))["current"]
    recovered.close()

    def fail_successor(report: MetadataReport, now: int) -> None:
        pipeline = pipeline_for(report, projection=False, now=now)
        pipeline.request("primary", "ebooks")
        for _ in range(5):
            assert pipeline.run_next(1)
        assert not pipeline.run_next(1)

    def confirm_removal(
        entries: tuple[CatalogueEntry, ...],
        now: int,
        database: Path | None = None,
    ) -> None:
        repository = open_repository(application_path if database is None else database)
        repository.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, now)
        discovery = repository.claim_review_pipeline_action(now + 1)
        assert discovery is not None
        repository.complete_review_discovery(discovery, 1, "9" * 64, entries, now + 2)
        assert repository.claim_review_pipeline_action(now + 3) is None
        confirmation = repository.claim_review_pipeline_action(now + 4)
        assert confirmation is not None
        repository.complete_review_discovery(
            confirmation, 2, "9" * 64, entries, now + 5
        )
        assert repository.claim_review_pipeline_action(now + 6) is None
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM current_review_packages"
        ).fetchone() == (0,)
        assert repository.count_current_review_cases(None) == (0, 0)
        repository.close()

    fail_successor(reports[4], 60)
    confirm_removal((), 70)

    repository = open_repository(application_path)
    recovered_proposal = evaluate_report(reports[3])
    repository.publish(reports[3], lambda: True)
    repository.publish_proposal(recovered_proposal)
    repository.admit_review_case(recovered_proposal)
    repository.close()
    fail_successor(reports[5], 80)
    ineligible = replace(
        _entry(reports[5].target.item, reports[5].file.identity),
        library="ebooks",
        classification="singular-unmounted",
        mount=None,
    )
    confirm_removal((ineligible,), 90)

    for index, entries in enumerate(
        (
            (),
            (
                replace(
                    _entry(reports[4].target.item, reports[4].file.identity),
                    library="ebooks",
                    classification="singular-unmounted",
                    mount=None,
                ),
            ),
        )
    ):
        database = application_path.parent / f"first-{index}.sqlite3"
        open_repository(database).close()
        first_failure = pipeline_for(
            reports[4],
            projection=False,
            now=100 + index * 20,
            database=database,
        )
        first_failure.request("primary", "ebooks")
        for _ in range(5):
            assert first_failure.run_next(1)
        assert not first_failure.run_next(1)
        confirm_removal(entries, 110 + index * 20, database)


def test_first_generation_real_shadow_projection_precedes_package_cutover(
    application_path: Path,
) -> None:
    report = _report(
        title="First staged projection", content_digest="a", file_identity="b"
    )
    proposal = evaluate_report(report)

    def extract(repository, action: PipelineAction, claim_next: bool):
        repository.publish(
            report,
            lambda: True,
            after_publication=lambda bound, current: (
                bound.complete_review_extraction_publication(
                    action, current.report_id, 10, 0, None, claim_next, None
                )
            ),
        )
        return report.report_id, True

    def project(repository, action: PipelineAction) -> str:
        result = repository.persist_shadow_outcome(
            action.review_case_id or "", 10, staged_action=action
        )
        observer = sqlite3.connect(application_path)
        try:
            assert (0,) == observer.execute(
                "SELECT COUNT(*) FROM shadow_decision_outcomes"
            ).fetchone()
            assert (0, 0) == observer.execute(
                "SELECT (SELECT COUNT(*) FROM current_review_packages),"
                "(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone()
        finally:
            observer.close()
        return "complete" if result != "stale" else "stale"

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (
            1,
            report.file.content_digest,
            (replace(_entry("item-1", report.file.identity), library="ebooks"),),
        ),
        extract=extract,
        providers=lambda *_: (),
        propose=lambda repository, _: (
            repository.publish_proposal(proposal),
            proposal.proposal_set_id,
        )[1],
        admit=lambda repository, _: (
            lambda value: (value["status"], value.get("review_case_id"))
        )(json.loads(repository.admit_review_case(proposal))),
        project=project,
        clock=lambda: 10,
    )
    pipeline.request("primary", "ebooks")
    # Admission owns the staged lineage, but package publication remains
    # gated on the real projection transaction.  In particular, automatic
    # decision-only handling must not make the staged case visible early.
    for _ in range(4):
        assert pipeline.run_next(1)
    repository = open_repository(application_path)
    assert ("projection", "queued") == repository._connection.execute(
        "SELECT stage,state FROM review_preparation_work"
    ).fetchone()
    assert (0,) == repository._connection.execute(
        "SELECT COUNT(*) FROM current_review_packages"
    ).fetchone()
    assert (0,) == repository._connection.execute(
        "SELECT COUNT(*) FROM automatic_decisions"
    ).fetchone()
    staged_case = repository._connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages"
    ).fetchone()[0]
    assert not json.loads(repository.get_review_case(staged_case))["current"]
    assert not repository._review_case_accepts_authority(staged_case)
    repository.close()

    assert pipeline.run_next(1)
    repository = open_repository(application_path)
    assert ("complete",) == repository._connection.execute(
        "SELECT state FROM review_preparation_work"
    ).fetchone()
    assert (1,) == repository._connection.execute(
        "SELECT COUNT(*) FROM current_review_packages"
    ).fetchone()
    case_id = repository._connection.execute(
        "SELECT review_case_id FROM current_review_packages"
    ).fetchone()[0]
    repository.close()
    # A restarted worker replays the same immutable outcome and never
    # manufactures another decision, package checkpoint, or completion.
    restarted = open_repository(application_path)
    restarted.enter_shadow_preview(11)
    replay = restarted.persist_shadow_outcome(case_id, 11)
    assert replay.startswith('{"authority":"none"')
    assert (1, 1, 1) == restarted._connection.execute(
        "SELECT (SELECT COUNT(*) FROM shadow_decision_outcomes),"
        "(SELECT COUNT(*) FROM current_review_packages),"
        "(SELECT COUNT(*) FROM review_preparation_work WHERE state='complete')"
    ).fetchone()
    restarted.close()


def test_projection_crash_rolls_back_staged_shadow_outcome_before_requeue(
    application_path: Path,
) -> None:
    report = _report(
        title="Rollback staged projection", content_digest="c", file_identity="d"
    )
    proposal = evaluate_report(report)

    def extract(repository, action: PipelineAction, claim_next: bool):
        repository.publish(
            report,
            lambda: True,
            after_publication=lambda bound, current: (
                bound.complete_review_extraction_publication(
                    action, current.report_id, 10, 0, None, claim_next, None
                )
            ),
        )
        return report.report_id, True

    def project(repository, action: PipelineAction) -> str:
        assert "stale" != repository.persist_shadow_outcome(
            action.review_case_id or "", 10, staged_action=action
        )
        observer = sqlite3.connect(application_path)
        try:
            assert (0,) == observer.execute(
                "SELECT COUNT(*) FROM shadow_decision_outcomes"
            ).fetchone()
            assert (0, 0) == observer.execute(
                "SELECT (SELECT COUNT(*) FROM current_review_packages),"
                "(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone()
        finally:
            observer.close()
        raise RuntimeError("injected projection crash")

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (
            1,
            report.file.content_digest,
            (replace(_entry("item-1", report.file.identity), library="ebooks"),),
        ),
        extract=extract,
        providers=lambda *_: (),
        propose=lambda repository, _: (
            repository.publish_proposal(proposal),
            proposal.proposal_set_id,
        )[1],
        admit=lambda repository, _: (
            lambda value: (value["status"], value.get("review_case_id"))
        )(json.loads(repository.admit_review_case(proposal))),
        project=project,
        clock=lambda: 10,
    )
    pipeline.request("primary", "ebooks")
    for _ in range(5):
        assert pipeline.run_next(1)

    observer = open_repository(application_path)
    try:
        assert () == observer.list_shadow_outcomes(50)
        assert (0, 0) == observer._connection.execute(
            "SELECT (SELECT COUNT(*) FROM current_review_packages),"
            "(SELECT COUNT(*) FROM automatic_decisions)"
        ).fetchone()
        assert ("failed", "unknown") == observer._connection.execute(
            "SELECT state,failure_code FROM review_preparation_work"
        ).fetchone()
        incident = next(
            signal
            for signal in cast(
                "list[dict[str, object]]", observer.operational_snapshot()["signals"]
            )
            if signal["cause"] == "preparation_failure"
        )
        assert {
            "attempt": 1,
            "code": "unknown",
            "stage": "projection",
        }.items() <= incident.items()
        assert str(incident["diagnostic_reference"]).startswith("prep-")
    finally:
        observer.close()


def test_real_projection_stale_lane_does_not_block_mixed_fifty_work_cohort(
    application_path: Path,
) -> None:
    reports: dict[str, MetadataReport] = {}
    report_for_item: dict[str, MetadataReport] = {}
    proposals = {}
    entries = []
    for index in range(50):
        base = _report(
            title=f"Synthetic {index}",
            content_digest=f"{index % 16:x}",
            file_identity=f"{(index + 5) % 16:x}",
            actionable=index % 2 == 0,
        )
        report = MetadataReport.create(
            target=replace(base.target, item=f"item-{index}"),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
        )
        reports[report.report_id] = report
        report_for_item[f"item-{index}"] = report
        entries.append(
            replace(_entry(f"item-{index}", report.file.identity), library="ebooks")
        )
    first = report_for_item["item-0"]
    replacement = MetadataReport.create(
        target=first.target,
        file=replace(first.file, content_digest="f" * 64),
        local_claims=first.local_claims,
        local_reasons=first.local_reasons,
        providers=first.providers,
    )
    projected: list[str] = []
    projection_outcomes: list[str] = []
    stale_projection_items: list[str] = []

    def extract(repository, action: PipelineAction, _: bool):
        report = report_for_item[action.item_id or ""]
        repository.publish(report, lambda: True)
        return report.report_id, False

    def propose(repository, report_id: str) -> str:
        report = reports[report_id]
        proposal = evaluate_report(report)
        proposals[proposal.proposal_set_id] = proposal
        repository.publish_proposal(proposal)
        return proposal.proposal_set_id

    def admit(repository, proposal_id: str) -> tuple[str, str | None]:
        value = json.loads(repository.admit_review_case(proposals[proposal_id]))
        return value["status"], value.get("review_case_id")

    def project(repository, action: PipelineAction) -> str:
        projected.append(action.item_id or "")
        outcome = repository.persist_shadow_outcome(
            action.review_case_id or "", 20, staged_action=action
        )
        projection_outcomes.append(outcome)
        if outcome == "stale":
            stale_projection_items.append(action.item_id or "")
        return "stale" if outcome == "stale" else "complete"

    repository = open_repository(application_path)
    repository.enter_shadow_preview(1)
    repository.close()
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (1, "a" * 64, tuple(entries)),
        extract=extract,
        providers=lambda *_: (),
        propose=propose,
        admit=admit,
        project=project,
        clock=lambda: 20,
    )
    pipeline.request("primary", "ebooks")
    for _ in range(512):
        repository = open_repository(application_path)
        state = repository._connection.execute(
            "SELECT stage,state,report_id FROM review_preparation_work "
            "WHERE item_id='item-0'"
        ).fetchone()
        repository.close()
        if state == ("projection", "queued", first.report_id):
            break
        assert pipeline.run_next(1)
    else:
        pytest.fail("item-0 never reached staged projection")
    # A supported foreground refresh wins the lineage immediately before
    # the worker claims projection.  The worker must discover that exact
    # currentness drift through real staged shadow persistence, not a test
    # projector shortcut.
    repository = open_repository(application_path)
    repository.publish(
        replacement,
        lambda: True,
        first.report_id,
        after_publication=lambda *_: None,
    )
    repository.close()
    while pipeline.run_next(1):
        pass
    repository = open_repository(application_path)
    states = repository._connection.execute(
        "SELECT item_id,state,failure_code FROM review_preparation_work ORDER BY item_id"
    ).fetchall()
    repository.close()
    assert "stale" in projection_outcomes
    assert {"item-0"} == set(stale_projection_items)
    assert "item-2" in projected
    assert 50 == len(states)
    assert all(state in {"complete", "failed", "stale"} for _, state, _ in states)
    assert ("complete", None) == {
        item_id: (state, failure_code) for item_id, state, failure_code in states
    }["item-2"]
    assert 1 == [(state, failure_code) for _, state, failure_code in states].count(
        ("stale", "stale")
    )


def test_unrelated_provider_success_does_not_shorten_open_circuit(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("item-1", "b" * 64),), 12
    )
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None
    repository.advance_review_pipeline_action(extraction, None, 14)
    provider = repository.claim_review_pipeline_action(15)
    assert provider is not None
    repository._connection.execute(
        "UPDATE review_preparation_control SET circuit_failures=2,"
        "circuit_open_until=100 WHERE singleton=1"
    )
    repository.advance_review_pipeline_action(provider, None, 20)
    control = repository._connection.execute(
        "SELECT circuit_failures,circuit_open_until "
        "FROM review_preparation_control WHERE singleton=1"
    ).fetchone()
    repository.close()
    assert control == (2, 100)


def test_terminal_provider_failure_does_not_open_shared_circuit(
    application_path: Path,
) -> None:
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_entry("item-1", "b" * 64),), 12
    )
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None
    repository.advance_review_pipeline_action(extraction, None, 14)
    provider = repository.claim_review_pipeline_action(15)
    assert provider is not None
    repository.fail_review_pipeline_action(provider, "provider", False, 16)
    control = repository._connection.execute(
        "SELECT circuit_failures,circuit_open_until "
        "FROM review_preparation_control WHERE singleton=1"
    ).fetchone()
    work = repository._connection.execute(
        "SELECT state,failure_code FROM review_preparation_work"
    ).fetchone()
    repository.close()
    assert control == (0, 0)
    assert work == ("failed", "provider")


def test_repeated_extraction_currentness_drift_requests_rediscovery(
    application_path: Path,
) -> None:
    entry = replace(
        _entry("item-1", "b" * 64),
        library="ebooks",
    )
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(application_path),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (1, "e" * 64, (entry,)),
        extract=lambda *_: (_ for _ in ()).throw(ReviewPipelineError("stale")),
        providers=lambda *_: (),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: True,
        clock=lambda: 30,
    )
    pipeline.request("primary", "ebooks")
    assert pipeline.run_next(1)
    assert sum(pipeline.run_next(1) for _ in range(3)) == 3

    repository = open_repository(application_path)
    progress = repository.review_pipeline_progress("primary", "ebooks")
    reports = repository._connection.execute(
        "SELECT COUNT(*) FROM metadata_reports"
    ).fetchone()
    cases = repository._connection.execute(
        "SELECT COUNT(*) FROM review_cases"
    ).fetchone()
    repository.close()

    assert progress is not None
    assert progress.rescan_requested
    assert reports == (0,)
    assert cases == (0,)


def test_report_and_extraction_checkpoint_commit_atomically(
    application_path: Path,
) -> None:
    report = _report()
    entry = CatalogueEntry(
        "primary",
        "ebooks",
        "lib-1",
        "item-1",
        1,
        "Synthetic work",
        (),
        True,
        False,
        "singular-supported",
        report.file.identity,
        "/item-1.epub",
        "epub",
        "books",
    )
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, 1, "a" * 64, (entry,), 12)
    action = repository.claim_review_pipeline_action(13)
    assert action is not None

    def fail_after_checkpoint(value, published) -> None:
        value.complete_review_extraction_publication(action, published.report_id, 14)
        raise RuntimeError("synthetic rollback")

    with pytest.raises(MetadataReportError):
        repository.publish(report, lambda: True, None, fail_after_checkpoint)
    assert repository._connection.execute(
        "SELECT COUNT(*) FROM metadata_reports"
    ).fetchone() == (0,)
    assert repository._connection.execute(
        "SELECT stage,state,report_id FROM review_preparation_work"
    ).fetchone() == ("extraction", "running", None)
    repository.publish(
        report,
        lambda: True,
        None,
        lambda value, published: value.complete_review_extraction_publication(
            action, published.report_id, 15, 30
        ),
    )
    repository.close()

    reopened = open_repository(application_path)
    reopened.recover_review_pipeline((("primary", "ebooks", "lib-1", _PRODUCER),))
    resumed = reopened.claim_review_pipeline_action(16)
    assert resumed is not None
    retry_not_before = reopened._connection.execute(
        "SELECT retry_not_before FROM review_preparation_work"
    ).fetchone()
    reopened.close()
    assert resumed.stage == "proposal"
    assert resumed.report_id == report.report_id
    assert retry_not_before == (0,)


def _entry(item_id: str, file_identity: str) -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        item_id,
        1,
        "Synthetic work",
        ("Example Author",),
        True,
        False,
        "singular-supported",
        file_identity,
        f"/{item_id}.epub",
        "epub",
        "books",
    )


class _ConcurrentRepository:
    def __init__(self, actions: list[PipelineAction]) -> None:
        self.actions = actions
        self.lock = threading.Lock()
        self.completed: list[str] = []

    def request_review_scan(self, *args):  # pragma: no cover - not used here
        raise AssertionError

    def review_pipeline_progress(self, *args):  # pragma: no cover - not used here
        return None

    def review_pipeline_paused(self) -> bool:
        return False

    def set_review_pipeline_paused(self, paused: bool, updated_at: int) -> None:
        return None

    def recover_review_pipeline(self, *args) -> None:
        return None

    def claim_review_pipeline_action(self, claimed_at: int):
        with self.lock:
            return None if not self.actions else self.actions.pop(0)

    def complete_review_discovery(self, *args):  # pragma: no cover - not used here
        raise AssertionError

    def begin_review_pipeline_output(self):
        pass

    def take_review_pipeline_claim(self):
        return None

    def rollback_review_pipeline_output(self):
        pass

    def advance_review_pipeline_action(
        self, action, output_id, completed_at, claim_next=False
    ):
        with self.lock:
            self.completed.append(action.item_id)

    def fail_review_pipeline_action(self, *args):
        raise AssertionError

    def close(self) -> None:
        return None


class _ScheduledPipeline:
    libraries = (
        ("primary", "fiction", "lib-1"),
        ("primary", "non-fiction", "lib-2"),
    )

    def __init__(self) -> None:
        self.progresses: dict[tuple[str, str], PipelineProgress] = {}
        self.requests: list[tuple[str, str]] = []
        self.is_paused = False
        self.status_calls = 0

    def paused(self):
        return self.is_paused

    def progress(self, connection, library):
        return self.progresses.get((connection, library))

    def status(self):
        self.status_calls += 1
        return (
            tuple(
                self.progresses.get((connection, library))
                for connection, library, _ in self.libraries
            ),
            self.is_paused,
        )

    def request(self, connection, library, *, trigger=None):
        self.requests.append((connection, library))
        return PipelineProgress(
            "review_scan_" + "a" * 64, connection, library, "queued", 0, 0, 0, 0, False
        )

    def set_paused(self, paused):
        self.is_paused = paused


class _LifecyclePipeline:
    """Small supervisor double that records only its lifecycle contract."""

    libraries: tuple[tuple[str, str, str], ...] = ()

    def __init__(self, *, fail_recover: bool = False, hold_run: bool = False) -> None:
        self.events: list[str] = []
        self.maximum_actions: list[int] = []
        self.fail_recover = fail_recover
        self.run_started = threading.Event()
        self.release_run = threading.Event()
        if not hold_run:
            self.release_run.set()

    def open_observation(self) -> None:
        self.events.append("open")

    def close_observation(self) -> None:
        self.events.append("close")

    def resume_claiming(self) -> None:
        self.events.append("resume")

    def recover(self) -> None:
        self.events.append("recover")
        if self.fail_recover:
            raise ReviewPipelineError("persistence")

    def run_next(self, maximum_actions: int = 64) -> bool:
        self.events.append("run")
        self.maximum_actions.append(maximum_actions)
        self.run_started.set()
        self.release_run.wait(3)
        return False

    def begin_drain(self) -> None:
        self.events.append("drain")

    def paused(self) -> bool:
        return False

    def status(self) -> tuple[tuple[PipelineProgress | None, ...], bool]:
        return (), False


def test_supervisor_opens_observation_before_recovery_and_workers() -> None:
    pipeline = _LifecyclePipeline()
    supervisor = ReviewPreparationSupervisor(pipeline)
    supervisor.start()
    try:
        assert pipeline.events[:3] == ["open", "resume", "recover"]
        assert pipeline.run_started.wait(1)
        assert pipeline.maximum_actions
        assert set(pipeline.maximum_actions) == {64}
    finally:
        supervisor.stop()


def test_supervisor_closes_preopened_observation_when_start_fails() -> None:
    pipeline = _LifecyclePipeline(fail_recover=True)
    supervisor = ReviewPreparationSupervisor(pipeline)

    with pytest.raises(ReviewPipelineError, match="persistence"):
        supervisor.start()

    assert pipeline.events == ["open", "resume", "recover", "close"]
    assert supervisor._threads == ()


def test_supervisor_cleans_only_started_lane_after_second_thread_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _LifecyclePipeline()
    joined: list[str] = []
    original_start = threading.Thread.start
    original_join = threading.Thread.join

    def start(thread: threading.Thread) -> None:
        if thread.name == "review-preparation-1":
            raise RuntimeError("synthetic second start failure")
        original_start(thread)

    def join(thread: threading.Thread, *args: object, **kwargs: object) -> None:
        joined.append(thread.name)
        original_join(thread, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(threading.Thread, "join", join)
    supervisor = ReviewPreparationSupervisor(
        pipeline, on_stop=lambda: pipeline.events.append("on_stop")
    )

    with pytest.raises(RuntimeError, match="synthetic second start failure"):
        supervisor.start()

    assert joined == ["review-preparation-0"]
    assert pipeline.events[-3:] == ["drain", "on_stop", "close"]
    assert supervisor._threads == ()


def test_supervisor_stops_then_closes_observation_in_existing_callback_order() -> None:
    pipeline = _LifecyclePipeline()
    supervisor = ReviewPreparationSupervisor(
        pipeline, on_stop=lambda: pipeline.events.append("on_stop")
    )
    supervisor.start()
    supervisor.stop()

    assert pipeline.events[-3:] == ["drain", "on_stop", "close"]


def test_supervisor_closes_observation_when_stop_callback_fails() -> None:
    pipeline = _LifecyclePipeline()
    error = RuntimeError("synthetic stop callback failure")

    def fail_stop() -> None:
        pipeline.events.append("on_stop")
        raise error

    supervisor = ReviewPreparationSupervisor(pipeline, on_stop=fail_stop)
    supervisor.start()

    with pytest.raises(RuntimeError) as raised:
        supervisor.stop()

    assert raised.value is error
    assert pipeline.events[-3:] == ["drain", "on_stop", "close"]
    assert supervisor._threads == ()


def test_supervisor_timeout_retains_observation_until_workers_stop() -> None:
    pipeline = _LifecyclePipeline(hold_run=True)
    supervisor = ReviewPreparationSupervisor(pipeline, graceful_shutdown_seconds=1)
    supervisor.start()
    assert pipeline.run_started.wait(1)

    with pytest.raises(ReviewPipelineError, match="shutdown_timeout"):
        supervisor.stop(deadline=time.monotonic())
    assert "close" not in pipeline.events

    pipeline.release_run.set()
    supervisor.stop()
    assert pipeline.events[-1] == "close"


def test_supervisor_restart_opens_a_fresh_observation() -> None:
    pipeline = _LifecyclePipeline()
    supervisor = ReviewPreparationSupervisor(pipeline)
    supervisor.start()
    supervisor.stop()
    supervisor.start()
    supervisor.stop()

    assert [event for event in pipeline.events if event == "open"] == ["open", "open"]
    assert [event for event in pipeline.events if event == "close"] == [
        "close",
        "close",
    ]


def test_lifecycle_foreground_controls_share_one_reentrant_lock_and_workers_keep_their_factory() -> (
    None
):
    status_entered = threading.Event()
    release_status = threading.Event()
    conditions_called = threading.Event()
    request_called = threading.Event()
    regular_opens = 0
    observation_opens = 0
    progress = PipelineProgress(
        "review_scan_" + "a" * 64,
        "primary",
        "fiction",
        "queued",
        0,
        0,
        0,
        0,
        False,
        1,
    )

    class ObservationRepository:
        def require_current(self) -> None:
            return None

        def review_pipeline_status(self, *_):
            status_entered.set()
            assert release_status.wait(2)
            return (), False

        def target_connection_conditions(self):
            conditions_called.set()
            return ()

        def request_review_scan(self, *_):
            request_called.set()
            return progress

        def review_pipeline_progress(self, *_):
            return progress

        def close(self) -> None:
            return None

    observation = ObservationRepository()
    action_repository = _ConcurrentRepository([])

    def regular_factory() -> ReviewPipelineRepository:
        nonlocal regular_opens
        regular_opens += 1
        return cast(ReviewPipelineRepository, action_repository)

    def observation_factory() -> ReviewPipelineRepository:
        nonlocal observation_opens
        observation_opens += 1
        return cast(ReviewPipelineRepository, observation)

    pipeline = ReviewPreparationPipeline(
        repository_factory=regular_factory,
        observation_repository_factory=observation_factory,
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: (_ for _ in ()).throw(AssertionError()),
    )
    pipeline.open_observation()
    status_thread = threading.Thread(target=pipeline.status)
    conditions_thread = threading.Thread(target=pipeline.connection_conditions)
    request_thread = threading.Thread(
        target=lambda: pipeline.request("primary", "fiction")
    )
    status_thread.start()
    assert status_entered.wait(1)
    conditions_thread.start()
    request_thread.start()
    assert not conditions_called.wait(0.1)
    assert not request_called.wait(0.1)
    release_status.set()
    status_thread.join(timeout=1)
    conditions_thread.join(timeout=1)
    request_thread.join(timeout=1)
    assert not status_thread.is_alive()
    assert not conditions_thread.is_alive()
    assert not request_thread.is_alive()
    assert conditions_called.is_set()
    assert request_called.is_set()
    assert pipeline.progress("primary", "fiction") == progress
    assert pipeline.run_next() is False
    pipeline.close_observation()

    assert observation_opens == 1
    assert regular_opens == 1


def test_lifecycle_foreground_operations_validate_before_each_repository_call() -> None:
    calls: list[str] = []
    progress = PipelineProgress(
        "review_scan_" + "c" * 64,
        "primary",
        "fiction",
        "queued",
        0,
        0,
        0,
        0,
        False,
        1,
    )

    class ObservationRepository:
        def require_current(self) -> None:
            calls.append("validate")

        def request_review_scan(self, *_):
            calls.append("request")
            return progress

        def review_pipeline_progress(self, *_):
            calls.append("progress")
            return progress

        def review_pipeline_status(self, *_):
            calls.append("status")
            return (), False

        def target_connection_conditions(self):
            calls.append("conditions")
            return ()

        def close(self) -> None:
            return None

    observation = ObservationRepository()
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, observation),
        observation_repository_factory=lambda: cast(
            ReviewPipelineRepository, observation
        ),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: (_ for _ in ()).throw(AssertionError()),
    )
    pipeline.open_observation()
    try:
        assert pipeline.request("primary", "fiction") == progress
        assert pipeline.progress("primary", "fiction") == progress
        assert pipeline.status() == ((), False)
        assert pipeline.connection_conditions() == ()
    finally:
        pipeline.close_observation()

    assert calls == [
        "validate",
        "request",
        "validate",
        "progress",
        "validate",
        "status",
        "validate",
        "conditions",
    ]


def test_foreground_control_holds_observation_through_close_then_falls_back() -> None:
    request_started = threading.Event()
    release_request = threading.Event()
    close_started = threading.Event()
    regular_opens = 0
    progress = PipelineProgress(
        "review_scan_" + "b" * 64,
        "primary",
        "fiction",
        "queued",
        0,
        0,
        0,
        0,
        False,
        1,
    )

    class ObservationRepository:
        closed = 0

        def require_current(self) -> None:
            return None

        def request_review_scan(self, *_):
            request_started.set()
            assert release_request.wait(2)
            return progress

        def review_pipeline_progress(self, *_):
            return progress

        def close(self) -> None:
            self.closed += 1
            close_started.set()

    observation = ObservationRepository()
    fallback = _ConcurrentRepository([])

    def regular_factory() -> ReviewPipelineRepository:
        nonlocal regular_opens
        regular_opens += 1
        return cast(ReviewPipelineRepository, fallback)

    pipeline = ReviewPreparationPipeline(
        repository_factory=regular_factory,
        observation_repository_factory=lambda: cast(
            ReviewPipelineRepository, observation
        ),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: (_ for _ in ()).throw(AssertionError()),
    )
    pipeline.open_observation()
    request = threading.Thread(target=lambda: pipeline.request("primary", "fiction"))
    request.start()
    assert request_started.wait(1)
    closing = threading.Thread(target=pipeline.close_observation)
    closing.start()
    assert not close_started.wait(0.1)
    release_request.set()
    request.join(timeout=1)
    closing.join(timeout=1)
    assert not request.is_alive()
    assert not closing.is_alive()
    assert observation.closed == 1

    assert pipeline.progress("primary", "fiction") is None
    pipeline.close_observation()
    assert observation.closed == 1
    assert regular_opens == 1


@pytest.fixture
def reports_path() -> Iterator[Path]:
    """An uncreated report database pathname in a directory of its own."""

    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory) / "reports.sqlite3"


def test_recovery_successor_uses_normal_projection_and_atomic_package_cutover(
    reports_path: Path,
) -> None:
    predecessor = _report(
        title="Visible predecessor", content_digest="a", file_identity="b"
    )
    successor = _report(
        title="Recovered successor", content_digest="a", file_identity="b"
    )
    repository = open_repository(reports_path)
    repository.enter_shadow_preview(1)
    repository.close()

    def pipeline_for(report: MetadataReport, now: int) -> ReviewPreparationPipeline:
        proposal = evaluate_report(report)

        def extract(bound, action: PipelineAction, claim_next: bool):
            bound.publish(
                report,
                lambda: True,
                after_publication=lambda current, published: (
                    current.complete_review_extraction_publication(
                        action, published.report_id, now, 0, None, claim_next, None
                    )
                ),
            )
            return report.report_id, True

        def propose(bound, _: str) -> str:
            bound.publish_proposal(proposal)
            return proposal.proposal_set_id

        def admit(bound, _: str) -> tuple[str, str | None]:
            value = json.loads(bound.admit_review_case(proposal))
            return value["status"], value.get("review_case_id")

        def project(bound, action: PipelineAction) -> str:
            outcome = bound.persist_shadow_outcome(
                action.review_case_id or "", now, staged_action=action
            )
            return "stale" if outcome == "stale" else "complete"

        return ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(reports_path),
            libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
            discover=lambda *_: (
                1,
                report.file.content_digest,
                (replace(_entry("item-1", report.file.identity), library="ebooks"),),
            ),
            extract=extract,
            providers=lambda *_: (),
            propose=propose,
            admit=admit,
            project=project,
            clock=lambda: now,
        )

    initial = pipeline_for(predecessor, 20)
    initial.request("primary", "ebooks")
    for _ in range(5):
        assert initial.run_next(1)
    repository = open_repository(reports_path)
    predecessor_package = repository.current_review_package(
        "primary", "ebooks", "item-1"
    )
    assert predecessor_package is not None
    current_document = repository.current_optional(
        "primary", "ebooks", "item-1", predecessor.file.identity
    )
    assert current_document is not None
    assert current_document is not None
    expected_current = MetadataReport.from_json(current_document).report_id
    assert predecessor.report_id == expected_current
    repository.publish(
        successor,
        lambda: True,
        expected_current,
        after_publication=lambda bound, report: bound.stage_provider_retry_successor(
            predecessor.report_id, report, 30
        ),
        expected_package=predecessor_package,
    )
    assert predecessor_package == repository.current_review_package(
        "primary", "ebooks", "item-1"
    )
    assert repository.completed_provider_retry_successor(predecessor.report_id) is None
    repository.close()

    recovery = pipeline_for(successor, 30)
    assert recovery.run_recovery_successor(successor.report_id)
    repository = open_repository(reports_path)
    current = repository.current_review_package("primary", "ebooks", "item-1")
    assert current is not None
    assert current is not None
    assert successor.report_id == current[1]
    assert predecessor_package != current
    assert successor.report_id == repository.completed_provider_retry_successor(
        predecessor.report_id
    )
    pending = repository.pending_provider_recovery_outbox()
    assert 2 == len(pending)
    assert successor.report_id == pending[-1][5]
    repository.close()

    restarted = open_repository(reports_path)
    replayed = restarted.pending_provider_recovery_outbox()
    assert pending == replayed
    for row in replayed:
        restarted.complete_provider_recovery_outbox(
            row[0],
            delivered_at=row[9],
        )
        restarted.complete_provider_recovery_outbox(
            row[0],
            delivered_at=row[9],
        )
    assert () == restarted.pending_provider_recovery_outbox()
    restarted.close()


def test_startup_interval_and_pause_admission_are_bounded() -> None:
    clock = [100]
    pipeline = _ScheduledPipeline()
    supervisor = ReviewPreparationSupervisor(
        pipeline, polling_interval_seconds=60, clock=lambda: clock[0]
    )
    supervisor._schedule_due()
    assert pipeline.requests == [("primary", "fiction"), ("primary", "non-fiction")]
    assert pipeline.status_calls == 1
    pipeline.requests.clear()
    for connection, library, _ in pipeline.libraries:
        pipeline.progresses[(connection, library)] = PipelineProgress(
            "review_scan_" + "b" * 64,
            connection,
            library,
            "completed",
            1,
            1,
            1,
            0,
            False,
            100,
        )
    clock[0] = 159
    supervisor._schedule_due()
    assert pipeline.requests == []
    assert pipeline.status_calls == 2
    supervisor._schedule_due()
    assert pipeline.status_calls == 2
    clock[0] = 160
    supervisor._schedule_due()
    assert len(pipeline.requests) == 2
    assert pipeline.status_calls == 3
    pipeline.requests.clear()
    pipeline.is_paused = True
    clock[0] = 220
    supervisor._schedule_due()
    assert pipeline.requests == []
    assert pipeline.status_calls == 4
    supervisor._schedule_due()
    clock[0] = 1_000_000
    supervisor._schedule_due()
    assert pipeline.status_calls == 4


def test_terminal_failures_wait_for_owner_action_without_idle_status_reads() -> None:
    clock = [100]
    pipeline = _ScheduledPipeline()
    for connection, library, _ in pipeline.libraries:
        pipeline.progresses[(connection, library)] = PipelineProgress(
            "review_scan_" + "c" * 64,
            connection,
            library,
            "completed_with_failures",
            1,
            1,
            0,
            1,
            False,
            100,
        )
    supervisor = ReviewPreparationSupervisor(
        pipeline, polling_interval_seconds=60, clock=lambda: clock[0]
    )

    supervisor._schedule_due()
    clock[0] = 1_000_000
    supervisor._schedule_due()
    supervisor._schedule_due()
    assert pipeline.status_calls == 1

    supervisor.retry("primary", "fiction")
    supervisor._schedule_due()
    assert pipeline.status_calls == 2


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_two_idle_lanes_borrow_startup_schema_validation(
    application_path: Path,
) -> None:
    import mechanical_librarian.adapters.persistence.metadata_reports as reports
    import mechanical_librarian.adapters.persistence.schema as schema

    owner = open_repository(application_path)
    condition = threading.Condition()
    opens = 0

    def repository_factory() -> ReviewPipelineRepository:
        nonlocal opens
        repository = open_repository(
            application_path,
            create=False,
            authority=owner.authority,
        )
        with condition:
            opens += 1
            condition.notify_all()
        return repository

    pipeline = ReviewPreparationPipeline(
        repository_factory=repository_factory,
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
        providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
        propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
        admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
        project=lambda *_: (_ for _ in ()).throw(AssertionError()),
    )
    supervisor = ReviewPreparationSupervisor(pipeline)
    validate = schema._validate_schema
    try:
        with (
            patch.object(schema, "_validate_schema", wraps=validate) as read,
            patch.object(reports, "_validate_schema", wraps=validate) as write,
        ):
            supervisor.start()
            with condition:
                assert condition.wait_for(lambda: opens >= 4, timeout=3)
            supervisor.stop()
        assert read.call_count + write.call_count == 0
    finally:
        if supervisor._threads:
            supervisor.stop()
        owner.close()


def test_lane_zero_rechecks_due_deadline_between_bounded_work_batches() -> None:
    clock = [159]
    pipeline = _ScheduledPipeline()
    for connection, library, _ in pipeline.libraries:
        pipeline.progresses[(connection, library)] = PipelineProgress(
            "review_scan_" + "d" * 64,
            connection,
            library,
            "completed",
            1,
            1,
            1,
            0,
            False,
            100,
        )
    supervisor = ReviewPreparationSupervisor(
        pipeline, polling_interval_seconds=60, clock=lambda: clock[0]
    )
    supervisor._schedule_due()
    assert pipeline.requests == []

    def run_next(maximum_actions: int = 64) -> bool:
        clock[0] = 160
        supervisor._stop.set()
        return True

    pipeline.run_next = run_next
    supervisor._wake.set()
    supervisor._run(0)
    assert pipeline.requests == [("primary", "fiction"), ("primary", "non-fiction")]


def test_lane_zero_wakes_recovery_with_the_supervisor_clock() -> None:
    clock = [41]
    pipeline = _ScheduledPipeline()
    woken: list[int] = []
    supervisor = ReviewPreparationSupervisor(
        pipeline, clock=lambda: clock[0], recovery_wake=woken.append
    )

    def run_next(maximum_actions: int = 64) -> bool:
        supervisor._stop.set()
        return False

    pipeline.run_next = run_next
    supervisor._wake.set()
    supervisor._run(0)
    assert [41] == woken


def test_lane_zero_contains_recovery_store_failure_and_continues() -> None:
    pipeline = _ScheduledPipeline()
    wakes: list[int] = []
    runs = 0

    def recovery(now: int) -> None:
        wakes.append(now)
        raise ReviewPipelineError("persistence")

    supervisor = ReviewPreparationSupervisor(
        pipeline,
        polling_interval_seconds=60,
        clock=lambda: 41,
        recovery_wake=recovery,
    )

    def run_next(maximum_actions: int = 64) -> bool:
        nonlocal runs
        runs += 1
        if runs == 3:
            supervisor._stop.set()
        else:
            # The next wake belongs to ordinary preparation, not recovery.
            supervisor._wake.set()
        return False

    pipeline.run_next = run_next
    supervisor._wake.set()
    supervisor._run(0)
    assert [41, 41, 41] == wakes
    assert 3 == runs
    assert pipeline.status_calls >= 1


def test_retry_is_limited_to_terminal_actionable_failure() -> None:
    pipeline = _ScheduledPipeline()
    supervisor = ReviewPreparationSupervisor(pipeline)
    pipeline.progresses[("primary", "fiction")] = PipelineProgress(
        "review_scan_" + "c" * 64,
        "primary",
        "fiction",
        "completed_with_failures",
        1,
        1,
        0,
        1,
        False,
        100,
    )
    supervisor.retry("primary", "fiction")
    assert pipeline.requests == [("primary", "fiction")]
    pipeline.progresses[("primary", "fiction")] = replace(
        pipeline.progresses[("primary", "fiction")], state="completed"
    )
    with pytest.raises(ReviewPipelineError, match="retry"):
        supervisor.retry("primary", "fiction")


def test_preparation_stages_emit_exact_advisory_timing_classes() -> None:
    class Timings:
        def __init__(self):
            self.values = []

        def append(self, timing):
            self.values.append(timing)

        def list(self):
            return tuple(self.values)

    def action(stage: str, **values) -> PipelineAction:
        return PipelineAction(
            "review_work_" + stage[0] * 64,
            "review_scan_" + "a" * 64,
            stage,
            1,
            "primary",
            "fiction",
            "lib-1",
            _PRODUCER,
            1,
            "b" * 64,
            "item",
            "1" * 64,
            1,
            source_fingerprint="c" * 64,
            **values,
        )

    timings = Timings()
    ticks = iter(range(0, 100_000_000, 1_000_000))
    repository = _ConcurrentRepository([])
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, repository),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: ("report_" + "1" * 64, False),
        providers=lambda *_: (ProviderStatus.DISABLED,),
        propose=lambda *_: "proposal_set_" + "2" * 64,
        admit=lambda *_: ("admitted", "review_case_" + "3" * 64),
        project=lambda *_: True,
        clock=lambda: 20,
        timing=StageTimer(timings, monotonic_ns=lambda: next(ticks), clock=lambda: 21),
    )
    pipeline._run_claimed_action(repository, action("extraction"), claim_next=False)
    pipeline._run_claimed_action(
        repository,
        action("provider", report_id="report_" + "1" * 64),
        claim_next=False,
    )
    pipeline._run_claimed_action(
        repository,
        action("proposal", report_id="report_" + "1" * 64),
        claim_next=False,
    )
    pipeline._run_claimed_action(
        repository,
        action("admission", proposal_set_id="proposal_set_" + "2" * 64),
        claim_next=False,
    )
    pipeline._run_claimed_action(
        repository,
        action("projection", review_case_id="review_case_" + "3" * 64),
        claim_next=False,
    )
    assert [(value.stage, value.wait_class) for value in timings.values] == [
        ("policy_evaluation", "none"),
        ("policy_evaluation", "none"),
        ("policy_evaluation", "none"),
    ]


def test_drain_rejects_initial_claim_and_persisted_claim_boundary_is_serialized() -> (
    None
):
    action = PipelineAction(
        "review_work_" + "1" * 64,
        "review_scan_" + "a" * 64,
        "extraction",
        1,
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        1,
        "b" * 64,
        "item",
        "1" * 64,
        1,
        source_fingerprint="c" * 64,
    )
    repository = _ConcurrentRepository([action])
    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, repository),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=lambda *_: ("report_" + "1" * 64, False),
        providers=lambda *_: (),
        propose=lambda *_: "proposal_set_" + "2" * 64,
        admit=lambda *_: ("admitted", None),
        project=lambda *_: True,
    )
    pipeline.begin_drain()
    assert not pipeline.run_next()
    assert repository.actions == [action]


def test_slow_work_does_not_hold_a_library_wide_stage_barrier() -> None:
    slow_started = threading.Event()
    release_slow = threading.Event()
    fast_completed = threading.Event()
    actions = [
        PipelineAction(
            "review_work_" + "1" * 64,
            "review_scan_" + "a" * 64,
            "extraction",
            1,
            "primary",
            "fiction",
            "lib-1",
            _PRODUCER,
            1,
            "b" * 64,
            "slow",
            "1" * 64,
            1,
            source_fingerprint="c" * 64,
        ),
        PipelineAction(
            "review_work_" + "2" * 64,
            "review_scan_" + "a" * 64,
            "extraction",
            1,
            "primary",
            "fiction",
            "lib-1",
            _PRODUCER,
            1,
            "b" * 64,
            "fast",
            "2" * 64,
            1,
            source_fingerprint="d" * 64,
        ),
    ]
    repository = _ConcurrentRepository(actions)

    def extract(_, action: PipelineAction, __: bool):
        if action.item_id == "slow":
            slow_started.set()
            assert release_slow.wait(2)
        else:
            fast_completed.set()
        return "report_" + "3" * 64, False

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, repository),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=extract,
        providers=lambda *_: (ProviderStatus.DISABLED,),
        propose=lambda *_: "proposal_set_" + "4" * 64,
        admit=lambda *_: ("admitted", "review_case_" + "5" * 64),
        project=lambda *_: True,
        clock=lambda: 20,
    )
    supervisor = ReviewPreparationSupervisor(pipeline)
    supervisor.start()
    try:
        assert slow_started.wait(1)
        assert fast_completed.wait(1)
    finally:
        release_slow.set()
        supervisor.stop()


def test_shutdown_deadline_retains_active_worker_and_durable_claim() -> None:
    started = threading.Event()
    release = threading.Event()
    action = PipelineAction(
        "review_work_" + "8" * 64,
        "review_scan_" + "a" * 64,
        "extraction",
        1,
        "primary",
        "fiction",
        "lib-1",
        _PRODUCER,
        1,
        "b" * 64,
        "slow",
        "8" * 64,
        1,
        source_fingerprint="c" * 64,
    )
    repository = _ConcurrentRepository([action])

    def extract(*_):
        started.set()
        assert release.wait(3)
        return "report_" + "3" * 64, False

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: cast(ReviewPipelineRepository, repository),
        libraries=(("primary", "fiction", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=extract,
        providers=lambda *_: (),
        propose=lambda *_: "proposal_set_" + "4" * 64,
        admit=lambda *_: ("admitted", None),
        project=lambda *_: True,
    )
    supervisor = ReviewPreparationSupervisor(pipeline, graceful_shutdown_seconds=1)
    supervisor.start()
    assert started.wait(1)
    with pytest.raises(ReviewPipelineError, match="shutdown_timeout"):
        supervisor.stop()
    assert supervisor._threads
    assert repository.completed == []
    release.set()
    supervisor.stop()
    assert repository.completed == ["slow"]
    assert supervisor._threads == ()


@pytest.mark.parametrize(
    ("outcome", "code"),
    (
        (AudioInspectionOutcome.SUCCESS, "success"),
        (AudioInspectionOutcome.NO_APPLICABLE_EVIDENCE, "success"),
        (AudioInspectionOutcome.UNSUPPORTED_FORMAT, "unsupported_media"),
        (AudioInspectionOutcome.UNAVAILABLE_SOURCE, "unreadable_media"),
        (AudioInspectionOutcome.MALFORMED, "parser_refused"),
        (AudioInspectionOutcome.PROBE_FAILURE, "parser_refused"),
        (AudioInspectionOutcome.RESOURCE_LIMIT, "parser_refused"),
        (AudioInspectionOutcome.TIMEOUT, "parser_refused"),
        (AudioInspectionOutcome.CANCELLATION, "parser_refused"),
        (AudioInspectionOutcome.INVALID_CONFIGURATION, "internal_failure"),
        (AudioInspectionOutcome.CHILD_FAILURE, "internal_failure"),
        (AudioInspectionOutcome.INTERNAL_FAILURE, "internal_failure"),
    ),
)
def test_every_audio_inspection_outcome_reaches_the_owner_with_a_name(
    outcome: AudioInspectionOutcome, code: str
) -> None:
    """No audiobook failure may fall through to the unnamed `unknown` code.

    A missing probe binary, a refused container, and an unreadable file are
    three different things to an owner, and each has to say which it was.
    """
    assert audio_inspection_failure_code(outcome) == code


def test_the_audio_outcome_vocabulary_is_exactly_what_the_mapping_answers() -> None:
    assert {
        audio_inspection_failure_code(value) for value in AudioInspectionOutcome
    } == {
        "success",
        "unsupported_media",
        "unreadable_media",
        "parser_refused",
        "internal_failure",
    }


def test_an_audiobook_inspection_failure_is_named_at_the_extraction_stage() -> None:
    """The stage translates the audio vocabulary, as it already does for EPUB."""

    def unreachable(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError

    def refuse(*_arguments: object, **_keywords: object) -> str:
        raise AudioItemInspectionError(AudioInspectionOutcome.INVALID_CONFIGURATION)

    stages = ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unreachable,
        catalogue_reader=cast("CatalogueReader", unreachable),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=lambda: None,
        generate_report=refuse,
        create_proposal_set=unreachable,
        admit_review_case=unreachable,
        cover_considerations=unreachable,
        recovery_observer=cast("ProviderFailureObserver", unreachable),
        drain_outbox=unreachable,
        discovery_lock=threading.Lock(),
        clock=lambda: 7,
    )

    with (
        patch.object(stages, "_require_eligible_entry", lambda _action: None),
        pytest.raises(ReviewPipelineError) as raised,
    ):
        stages.extract(
            cast("ReviewPipelineRepository", object()),
            cast("PipelineAction", object()),
            False,
        )

    assert raised.value.code == "internal_failure"


def test_a_terminal_stop_that_registers_no_package_refuses_to_pass_silently(
    application_path: Path,
) -> None:
    """The unreachable-evidence failure must not become a quiet no-op.

    A foreign key already keeps a report the pipeline never published out of
    this call, so the guard sits behind it.  Lifting that key is the only way
    to reach the state it exists for, and the guard is what stops that state
    from producing a work with no package and no complaint.
    """
    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_audio_entry("item", "1" * 64),), 12
    )
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None

    repository._connection.execute("PRAGMA foreign_keys=OFF")
    repository._connection.execute("BEGIN")
    with pytest.raises(MetadataReportError):
        repository.complete_review_extraction_publication(
            extraction, "report_" + "c" * 64, 14, 0, "local_evidence_only"
        )
    packages = repository._connection.execute(
        "SELECT COUNT(*) FROM current_review_packages"
    ).fetchone()
    repository._connection.execute("ROLLBACK")
    repository.close()

    assert packages == (0,)


def test_the_provider_stage_refuses_an_audiobook_report_it_should_never_see() -> None:
    def unreachable(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError

    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item-1", default_target_fields("Recorded title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(),
        local_reasons=("no_local_audio_claims",),
        providers=(),
        audio=AudioFacts(),
    )

    class _Repository:
        def get(self, report_id: str) -> str:
            assert report_id == report.report_id
            return report.to_json()

    stages = ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unreachable,
        catalogue_reader=cast("CatalogueReader", unreachable),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=unreachable,
        generate_report=unreachable,
        create_proposal_set=unreachable,
        admit_review_case=unreachable,
        cover_considerations=unreachable,
        recovery_observer=cast("ProviderFailureObserver", unreachable),
        drain_outbox=unreachable,
        discovery_lock=threading.Lock(),
        clock=lambda: 7,
    )

    with pytest.raises(ReviewPipelineError) as raised:
        stages.providers(
            cast("ReviewPipelineRepository", _Repository()), report.report_id
        )

    assert raised.value.code == "local_evidence_only"


def test_an_ebook_report_still_reaches_the_provider_stage() -> None:
    def unreachable(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError

    report = _report()

    class _Repository:
        def get(self, _report_id: str) -> str:
            return report.to_json()

    observations: list[object] = []
    stages = ReviewPipelineStages(
        configuration=ApplicationConfiguration((), (), (), ()),
        catalogue=unreachable,
        catalogue_reader=cast("CatalogueReader", unreachable),
        probe_connection=unreachable,
        connections=frozenset(),
        repository_factory=unreachable,
        recover_artifacts=unreachable,
        generate_report=unreachable,
        create_proposal_set=unreachable,
        admit_review_case=unreachable,
        cover_considerations=unreachable,
        recovery_observer=cast("ProviderFailureObserver", observations.append),
        drain_outbox=unreachable,
        discovery_lock=threading.Lock(),
        clock=lambda: 7,
    )

    assert (
        stages.providers(
            cast("ReviewPipelineRepository", _Repository()), report.report_id
        )
        == ()
    )


def _audio_entry(item_id: str, file_identity: str) -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        item_id,
        1,
        "Recorded work",
        ("Example Author",),
        False,
        False,
        "singular-supported",
        file_identity,
        f"/{item_id}.m4b",
        "m4b",
        "audiobooks",
    )


def test_a_published_audiobook_report_ends_terminally_at_its_local_evidence(
    application_path: Path,
) -> None:
    """Preparation stops in the transaction that published the report.

    The ordinary ladder runs extraction straight to `proposal`; the `provider`
    stage is only reached by a terminal extraction disposition or a retry
    successor. An audiobook has neither a provider comparison nor a proposal to
    evaluate, so its work ends here rather than queueing stages that cannot
    complete.
    """
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "fiction", "item", default_target_fields("Recorded work")
        ),
        file=FileFacts(0, "1" * 64, "b" * 64, "m4b"),
        local_claims=(),
        local_reasons=("no_local_audio_claims",),
        providers=(),
        audio=AudioFacts(),
    )

    repository = open_repository(application_path)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_audio_entry("item", report.file.identity),), 12
    )
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None
    repository.publish(
        report,
        lambda: True,
        None,
        lambda value, published: value.complete_review_extraction_publication(
            extraction, published.report_id, 14, 0, "local_evidence_only"
        ),
    )
    work = repository._connection.execute(
        "SELECT stage,state,failure_code,failure_root FROM review_preparation_work"
    ).fetchone()
    retained = MetadataReport.from_json(repository.get(report.report_id))
    browsed = repository.list_current_review_case_documents(None, 10, 0, None)
    found = repository.count_current_review_cases("Recorded work")
    incidents = operational_incidents(repository.operational_snapshot())
    remaining = repository.claim_review_pipeline_action(15)
    repository.close()

    assert work == ("provider", "failed", "local_evidence_only", None)
    assert remaining is None
    assert retained.schema_version == "metadata-report-v31"
    assert retained.audio is not None
    assert retained.providers == ()
    # The evidence is what the owner browses: a report, not a terminal card.
    assert len(browsed) == 1
    case_document, report_document = browsed[0]
    assert case_document is None
    assert browse_report(report_document)["audio"] is not None
    assert found == (1, 0)
    # And a stop that is not a fault raises no incident, so the status surface
    # keeps answering for every other library.
    assert [value for value in incidents if value.kind == "owner_action_required"] == []
    assert not any(
        value.kind not in {"healthy_activity", "automatic_recovery"}
        for value in incidents
    )
