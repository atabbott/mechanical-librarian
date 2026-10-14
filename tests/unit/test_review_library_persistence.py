"""Library and Attention persistence: current-work projection,
bounded browse windows, total ordering, and the cover state inside
the current case snapshot.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeAuthorityCurrentness,
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_cases import ReviewLibraryPage
from mechanical_librarian.adapters.persistence.review_preparation import _FAILURE_CODES
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    CapabilityObservation,
    GuardedFieldApplication,
)
from mechanical_librarian.application.operation_admission import OperationAdmission
from mechanical_librarian.application.review_application import (
    ReviewApplication,
    ReviewApplicationError,
)
from mechanical_librarian.application.review_web import (
    _TERMINAL_PREPARATION_GUIDANCE,
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
    RecordHumanDecision,
    _library_item,
    _provider_comparison_notices,
    _provider_outcomes,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_browse import provider_lane_facts
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.proposal_evidence import (
    _claim as _proposal_claim,
)
from tests.support.proposal_evidence import (
    _fields as _proposal_fields,
)
from tests.support.proposal_evidence import (
    _provider as _proposal_provider,
)
from tests.support.proposal_evidence import (
    _report as _proposal_report,
)
from tests.support.review_cases import _report
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)


def test_cover_currentness_sql_binding_fails_closed_for_unknown_and_failure(
    tmp_path: Path,
) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")

    repository.bind_cover_consideration_currentness(
        lambda _document, _replay: "unknown"
    )
    assert repository._connection.execute(
        "SELECT ml_cover_consideration_current('document','replay')"
    ).fetchone() == (0,)

    def unavailable(_document: str, _replay: str) -> str:
        raise RuntimeError("synthetic currentness failure")

    repository.bind_cover_consideration_currentness(unavailable)
    assert repository._connection.execute(
        "SELECT ml_cover_consideration_current('document','replay')"
    ).fetchone() == (0,)
    repository.close()


def _field_effect_application(
    repository: SQLiteMetadataReportRepository, directory: Path
) -> tuple[
    GuardedFieldApplication,
    FakeTargetService,
    FakeAuthorityCurrentness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
]:
    target = FakeTargetService()
    authority = FakeAuthorityCurrentness()
    capabilities = FakeCapabilityObserver("c" * 64)
    consent = FakeConsentCurrentness(repository.field_effect_consent_envelope_identity)
    return (
        GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            capabilities,
            authority,
            FakeDependencyCurrentness(),
            consent,
            PrivateCoverRecoveryStore(directory / "recovery", lambda _: b"cover"),
            FakeForwardReevaluation(),
            lambda: 10,
            FakePreclaimHook(),
            FakePreclaimHook(),
            FakePreclaimHook(),
        ),
        target,
        authority,
        capabilities,
        consent,
    )


def test_terminal_provider_taxonomy_projects_closed_safe_outcomes() -> None:
    statuses = tuple(
        status for status in ProviderStatus if status is not ProviderStatus.CANDIDATES
    )
    source = _report()
    report = MetadataReport.create(
        target=source.target,
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=tuple(
            sorted(
                (
                    ProviderObservation(f"provider-{index}", status)
                    for index, status in enumerate(statuses)
                ),
                key=lambda provider: provider.provider,
            )
        ),
    )
    outcomes = _provider_outcomes(provider_lane_facts(report))
    assert {outcome["status"] for outcome in outcomes} == {
        status.value for status in statuses
    }
    assert all(
        set(outcome)
        == {"lane_kind", "provider", "provider_requests", "reason", "status"}
        and outcome["provider_requests"] == 0
        for outcome in outcomes
    )
    assert "diagnostic" not in json.dumps(outcomes)
    many = MetadataReport.create(
        target=source.target,
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=tuple(
            ProviderObservation(f"lane-{index:02d}", ProviderStatus.NO_MATCH)
            for index in range(33)
        ),
    )
    assert len(_provider_outcomes(provider_lane_facts(many))) == 33


def test_every_lane_is_reported_but_only_limiting_ones_become_notices() -> None:
    """The source ledger and the comparison notices read the same lanes.

    The source disclosure reports every lane so an owner can distinguish a
    match from a source that was never asked. No terminal lane becomes a
    permanent case-level comparison banner.
    """

    source = _report()
    report = MetadataReport.create(
        target=source.target,
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(
            _proposal_provider(
                "google-books-main", "gb-1", (), authority="google_books"
            ),
            ProviderObservation(
                "hardcover-main", ProviderStatus.DISABLED, authority="hardcover"
            ),
            ProviderObservation(
                "open-library-main",
                ProviderStatus.INSUFFICIENT_INPUT,
                authority="open_library",
            ),
            ProviderObservation(
                "private-connection", ProviderStatus.NO_MATCH, authority="hardcover"
            ),
        ),
    )
    assert [
        outcome["status"] for outcome in _provider_outcomes(provider_lane_facts(report))
    ] == [
        "candidates",
        "disabled",
        "insufficient_input",
        "no_match",
    ]
    assert _provider_comparison_notices(report) == []


def test_a_work_with_no_review_case_projects_its_current_values_and_reason() -> None:
    report = _report()
    item = _library_item(None, report.to_json(), ())
    summary = item["summary"]
    assert type(summary) is dict
    assert summary["state"] == "no_decision_recorded"
    assert "No review decision is recorded" in str(summary["reason"])
    assert summary["fields"] == report.target.to_data()["fields"]
    # A work that does have a case is opened through its decision surface, so
    # the row carries no second projection of the same values.
    case = ReviewCase.create(evaluate_report(report), report)
    assert _library_item(case.to_json(), report.to_json(), ())["summary"] is None


def test_bounded_browse_names_the_page_and_walks_it_in_both_directions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "review.sqlite")
        for index in range(5):
            source = _report(title=f"Work {index:02d}")
            repository.publish(
                MetadataReport.create(
                    target=TargetFacts(
                        "primary", "ebooks", f"work-{index:02d}", source.target.fields
                    ),
                    file=source.file,
                    local_claims=source.local_claims,
                    local_reasons=source.local_reasons,
                    providers=source.providers,
                ),
                lambda: True,
            )
        browse = BrowseReviewLibrary(repository)
        first = browse.library(lens="all", search=None, limit=2, cursor=None)
        assert (first["page"], first["previous_cursor"]) == (1, None)
        assert first["next_cursor"] is not None
        second = browse.library(
            lens="all", search=None, limit=2, cursor=str(first["next_cursor"])
        )
        assert second["page"] == 2
        assert second["previous_cursor"] is not None
        third = browse.library(
            lens="all", search=None, limit=2, cursor=str(second["next_cursor"])
        )
        assert (third["page"], third["next_cursor"]) == (3, None)
        # The backward step is the same bounded window, and the first page has
        # nothing before it to name.
        back = browse.library(
            lens="all", search=None, limit=2, cursor=str(second["previous_cursor"])
        )
        assert (back["page"], back["previous_cursor"]) == (1, None)
        assert back["items"] == first["items"]
        repository.close()


def test_fifty_mixed_packages_and_failures_share_one_bounded_browse_window() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "review.sqlite")
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        from mechanical_librarian.application.catalogue import CatalogueEntry

        entries = tuple(
            CatalogueEntry(
                "primary",
                "fiction",
                "lib-1",
                f"failed-{index:02d}",
                index,
                f"Failed {index:02d}",
                (),
                True,
                False,
                "singular-supported",
                f"{index + 1:064x}",
                f"/safe-{index}.epub",
                "epub",
                "/media",
            )
            for index in range(25)
        )
        repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 12)
        now = 13
        while action := repository.claim_review_pipeline_action(now):
            if action.stage == "extraction":
                repository.advance_review_pipeline_action(action, None, now + 1)
            elif action.stage == "provider":
                repository.fail_review_pipeline_action(
                    action, "authentication", False, now + 1, "hardcover-main"
                )
            else:
                pytest.fail(f"unexpected stage {action.stage}")
            now += 2
        for index in range(25):
            source = _report(
                title=f"Published {index:02d}",
                content_digest=f"{index + 30:064x}"[-1],
                file_identity=f"{index + 60:064x}"[-1],
            )
            report = MetadataReport.create(
                target=TargetFacts(
                    "primary", "ebooks", f"published-{index:02d}", source.target.fields
                ),
                file=source.file,
                local_claims=source.local_claims,
                local_reasons=source.local_reasons,
                providers=source.providers,
            )
            repository.publish(report, lambda: True)
        total, attention = repository.count_current_review_cases(None)
        rows = tuple(
            row
            for offset in range(0, 50, 7)
            for row in repository.list_current_review_case_documents(
                None, 7, offset, None
            )
        )
        failed_total = repository.count_current_review_cases("fiction")[0]
        published_total = repository.count_current_review_cases("ebooks")[0]
        repository.close()
    assert (total, attention) == (50, 0)
    assert (failed_total, published_total) == (25, 25)
    assert len(rows) == 50
    assert len(set(rows)) == 50


def test_terminal_failed_work_is_library_only_and_safe() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "review.sqlite")
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        from mechanical_librarian.application.catalogue import CatalogueEntry

        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            (
                CatalogueEntry(
                    "primary",
                    "fiction",
                    "lib-1",
                    "item-secret",
                    1,
                    "Title",
                    (),
                    True,
                    False,
                    "singular-supported",
                    "b" * 64,
                    "/safe.epub",
                    "epub",
                    "/media",
                ),
            ),
            12,
        )
        extraction = repository.claim_review_pipeline_action(13)
        assert extraction is not None
        repository.advance_review_pipeline_action(extraction, None, 14)
        provider = repository.claim_review_pipeline_action(15)
        assert provider is not None
        repository.fail_review_pipeline_action(
            provider, "authentication", False, 16, "hardcover-main"
        )
        repository.publish(_report(), lambda: True)
        first_page = repository.list_current_review_case_documents(None, 1, 0, None)
        second_page = repository.list_current_review_case_documents(None, 1, 1, None)
        title_rows = repository.list_current_review_case_documents(
            "Preparation unavailable", 10, 0, None
        )
        media_rows = repository.list_current_review_case_documents(
            "unknown", 10, 0, None
        )
        snapshot = repository.read_review_library(None, 10, 0, None)
        title_total = repository.count_current_review_cases("Preparation unavailable")
        media_total = repository.count_current_review_cases("unknown")
        attention = repository.read_review_attention(None, 10, 0)
        repository.close()
    assert len(first_page) == 1
    assert len(second_page) == 1
    assert first_page != second_page
    assert snapshot.total == 2
    assert snapshot.attention_total == 0
    assert len(snapshot.rows) == 2
    assert (len(title_rows), title_total) == (1, (1, 0))
    assert (len(media_rows), media_total) == (1, (1, 0))
    assert attention.total == 0
    from mechanical_librarian.application.review_web import _library_item

    item = next(
        _library_item(*row[:3])
        for row in snapshot.rows
        if _library_item(*row[:3])["preparation"] is not None
    )
    assert item["preparation"] == {
        "attempt": 1,
        "diagnostic_reference": "prep-adcd2c66d60d4c88",
        # A provider-credential terminal says nothing about the media.
        "media_health": [],
        "provider": "hardcover-main",
        "reason": "Provider credentials need owner attention.",
        "recovery": "Update the configured provider credentials, then scan this library again.",
        "recovery_attempt": 0,
        "stage": "provider",
        "status": "authentication",
    }
    assert item["preparation_outcomes"] == []
    # A work whose preparation failed has no values to show, so its summary
    # names the condition instead of presenting an empty record.
    assert item["summary"] == {
        "fields": [],
        "reason": "Provider credentials need owner attention.",
        "state": "preparation_failed",
    }
    assert "credential unavailable" not in json.dumps(item)


def test_real_sqlite_progress_reconciles_mixed_terminal_and_active_work() -> None:
    """A stale item is terminally blocked beside failed, ready, and active work.

    The persisted state is deliberately mixed in one current scan: progress is
    a projection over the durable work rows, not a story reconstructed from a
    single pipeline transition.  Stale stays in the failed bucket and names no
    separate retry or recovery work.
    """
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "review.sqlite"
        repository = open_repository(database)
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        from mechanical_librarian.application.catalogue import CatalogueEntry

        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            tuple(
                CatalogueEntry(
                    "primary",
                    "fiction",
                    "lib-1",
                    f"item-{ordinal}",
                    ordinal,
                    f"Synthetic {ordinal}",
                    (),
                    True,
                    False,
                    "singular-supported",
                    f"{ordinal + 1:064x}",
                    f"/synthetic-{ordinal}.epub",
                    "epub",
                    "/media",
                )
                for ordinal in range(4)
            ),
            12,
        )
        active = repository.claim_review_pipeline_action(13)
        assert active is not None
        assert active.stage == "extraction"
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        review_case_id = json.loads(repository.admit_review_case(proposal))[
            "review_case_id"
        ]

        with sqlite3.connect(database) as connection:
            work_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT work_id FROM review_preparation_work "
                    "WHERE scan_id=? ORDER BY ordinal",
                    (active.scan_id,),
                )
            ]
            assert len(work_ids) == 4
            active_state = connection.execute(
                "SELECT state FROM review_preparation_work WHERE work_id=?",
                (work_ids[0],),
            ).fetchone()
            connection.executemany(
                "UPDATE review_preparation_work "
                "SET stage=?,state=?,failure_code=?,review_case_id=?,outcome=?,"
                "updated_at=? WHERE work_id=?",
                (
                    ("provider", "stale", "stale", None, None, 14, work_ids[1]),
                    ("proposal", "failed", "failed", None, None, 14, work_ids[2]),
                    (
                        "complete",
                        "complete",
                        None,
                        review_case_id,
                        "case",
                        14,
                        work_ids[3],
                    ),
                ),
            )
            stale = connection.execute(
                "SELECT state,recovery_attempt FROM review_preparation_work WHERE work_id=?",
                (work_ids[1],),
            ).fetchone()

        progress = repository.review_pipeline_progress("primary", "fiction")
        repository.close()

    assert progress is not None
    assert active_state == ("running",)
    assert (
        progress.total,
        progress.checked,
        progress.ready,
        progress.recorded,
        progress.blocked,
    ) == (
        4,
        3,
        1,
        0,
        2,
    )
    assert dict(progress.stages) == {
        "queued": 0,
        "extracting": 1,
        "providers": 0,
        "proposals": 0,
        "admission": 0,
        "projection": 0,
        "ready": 1,
        "recorded": 0,
        "failed": 2,
    }
    assert progress.checked == progress.ready + progress.recorded + progress.blocked
    assert progress.total == sum(count for _stage, count in progress.stages)
    # Stale is terminally blocked: it remains in place, has no retry count,
    # and this read path creates no manual retry or recovery work.
    assert stale == ("stale", 0)


@pytest.mark.parametrize(
    ("status", "reason", "recovery"),
    (
        (
            "provider",
            "A provider ended without a usable result.",
            "No owner action is currently available.",
        ),
        (
            "configuration",
            "Provider configuration needs owner attention.",
            "Update the configured provider, then scan this library again.",
        ),
        (
            "authentication",
            "Provider credentials need owner attention.",
            "Update the configured provider credentials, then scan this library again.",
        ),
        (
            "stale",
            "The item changed while preparation was in progress.",
            "Automatic recovery is pending for the current item.",
        ),
        (
            "transient",
            "Bounded automatic recovery attempts were exhausted.",
            "When this library finishes with failures, open Background activity and choose Retry.",
        ),
        (
            "projection",
            "Review projection could not be completed.",
            "No owner action is currently available.",
        ),
        (
            "failed",
            "Preparation failed; no safe detail is available.",
            "No owner action is currently available.",
        ),
        (
            "currentness",
            "The source changed before preparation could be completed.",
            "Automatic recovery is pending for the current source.",
        ),
        (
            "persistence",
            "Preparation state could not be durably recorded.",
            "No owner action is currently available.",
        ),
        (
            "unsupported_media",
            "This media type is not supported for review preparation.",
            "Use a supported media file, then scan this library again.",
        ),
        (
            "unreadable_media",
            "The local media file could not be read.",
            "Restore a readable media file in the library, then scan this library again.",
        ),
        (
            "parser_refused",
            "The media file could not be safely read within supported parsing limits.",
            "Use a media file that meets the supported parsing limits, then scan this library again.",
        ),
        (
            "missing_required_input",
            "Required preparation input is unavailable.",
            "Provide the required supported item input, then scan this library again.",
        ),
        (
            "malformed_retained_data",
            "Retained preparation data could not be safely reopened.",
            "No owner action is currently available.",
        ),
        (
            "internal_failure",
            "Preparation stopped before a usable review package was produced.",
            "No owner action is currently available.",
        ),
        (
            "unknown",
            "Preparation failed; no safe detail is available.",
            "No owner action is currently available.",
        ),
    ),
)
def test_terminal_preparation_guidance_is_total_and_closed(
    status: str, reason: str, recovery: str
) -> None:
    assert set(_TERMINAL_PREPARATION_GUIDANCE) == _FAILURE_CODES
    item = _library_item(
        None,
        json.dumps(
            {
                "attempt": 1,
                "failure_root": None,
                "item": "synthetic-item",
                "lane": "extraction",
                "library": "Synthetic library",
                "recovery_attempt": 0,
                "schema_version": "review-terminal-work-v1",
                "status": status,
                "work_id": "review_work_" + "a" * 64,
            }
        ),
        (),
    )

    preparation = item["preparation"]
    assert isinstance(preparation, dict)
    assert preparation["reason"] == reason
    assert preparation["recovery"] == recovery
    assert "reported condition" not in recovery


def _terminal_document(status: str, failure_root: str | None) -> str:
    return json.dumps(
        {
            "attempt": 1,
            "failure_root": failure_root,
            "item": "synthetic-item",
            "lane": "extraction",
            "library": "Synthetic library",
            "recovery_attempt": 0,
            "schema_version": "review-terminal-work-v1",
            "status": status,
            "work_id": "review_work_" + "a" * 64,
        }
    )


def test_a_composed_item_names_the_track_that_refused_it() -> None:
    """The ordinal is the whole difference for a multi-file audiobook.

    "The local media file could not be read" is not something an owner
    holding three hundred files can act on; the track number is the file they
    can open. It reaches the reason and never the provider field, which names
    a provider and nothing else.
    """
    item = _library_item(None, _terminal_document("unreadable_media", "track_3"), ())

    preparation = item["preparation"]
    summary = item["summary"]
    assert isinstance(preparation, dict)
    assert isinstance(summary, dict)
    assert preparation["reason"] == (
        "The local media file could not be read. "
        "The refusing file is track 3 of this item's audio files."
    )
    assert preparation["provider"] == "provider"
    assert summary["reason"] == preparation["reason"]


def test_a_terminal_without_a_recorded_track_reads_exactly_as_before() -> None:
    """A single-file item records no root, and its owner copy is unchanged."""
    item = _library_item(None, _terminal_document("unreadable_media", None), ())

    preparation = item["preparation"]
    assert isinstance(preparation, dict)
    assert preparation["reason"] == "The local media file could not be read."
    assert preparation["provider"] == "provider"


def test_cover_state_is_inside_the_current_case_read_snapshot() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        reader = open_repository(database)
        report = _report()
        reader.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        reader.publish_proposal(proposal_set)
        case_id = json.loads(reader.admit_review_case(proposal_set))["review_case_id"]
        replay_digest = "b" * 64
        consideration_id = "cover_consideration_" + "c" * 64
        document = json.dumps(
            {
                "consideration_id": consideration_id,
                "replay_digest": replay_digest,
                "report_id": report.report_id,
                "report_document": report.to_json(),
                "review_case_id": case_id,
                "schema_version": "cover-consideration-v4",
                "supersedes_id": None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        inserted = threading.Event()
        finished = threading.Event()
        writer_errors: list[BaseException] = []
        writer_path = (
            database if reader._binding is None else reader._binding.alias_path
        )

        def write_consideration() -> None:
            writer = None
            try:
                connection = sqlite3.connect(writer_path, isolation_level=None)
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                writer = SQLiteMetadataReportRepository(connection)
                writer._connection.set_trace_callback(
                    lambda statement: (
                        inserted.set()
                        if statement.startswith("INSERT INTO cover_considerations")
                        else None
                    )
                )
                writer.record_cover_consideration(
                    document,
                    review_case_id=case_id,
                    report_id=report.report_id,
                    replay_digest=replay_digest,
                    supersedes_id=None,
                    occurred_at=1_000,
                )
            except BaseException as error:
                writer_errors.append(error)
            finally:
                if writer is not None:
                    writer.close()
                finished.set()

        original = reader.get_review_case
        started = False

        def interleave(review_case_id: str) -> str:
            nonlocal started
            value = original(review_case_id)
            if not started:
                started = True
                threading.Thread(target=write_consideration, daemon=True).start()
                assert inserted.wait(5)
            return value

        with patch.object(reader, "get_review_case", side_effect=interleave):
            before = reader.read_current_review_case(case_id, 50)
        assert before.cover_consideration_document is None
        assert () == before.cover_history
        assert finished.wait(5)
        assert [] == writer_errors
        after = reader.read_current_review_case(case_id, 50)
        assert document == after.cover_consideration_document
        assert (
            json.loads(after.cover_consideration_document)["report_document"]
            == report.to_json()
        )
        assert "automatic_prepared" == after.cover_history[0]["kind"]
        reader.close()


def test_cover_retry_stops_after_an_owner_outcome() -> None:
    application = object.__new__(ReviewApplication)
    application._cover_considerations = Mock()
    application._cases = Mock()
    application._cases._read.return_value = (
        {
            "fields": [
                {
                    "destination": "cover",
                    "human_decision": {"outcome": "accept"},
                    "proposal_id": "review_proposal_" + "d" * 64,
                }
            ]
        },
        _report(),
        None,
    )
    # A deliberate patch of the bound method on a bare instance.
    application.serialized = lambda call: call()  # type: ignore[method-assign]  # reason: deliberate per-test monkeypatch of a bound method

    with pytest.raises(ReviewApplicationError, match="already_decided"):
        application.retry_cover_consideration("review_case_" + "e" * 64)
    application._cover_considerations.ensure.assert_not_called()


def test_cover_observer_runs_outside_the_global_application_gate() -> None:
    application = object.__new__(ReviewApplication)
    application._gate = threading.RLock()
    application._admission = OperationAdmission()
    # A deliberate patch of the bound method on a bare instance.
    application.serialized = ReviewApplication.serialized.__get__(application)  # type: ignore[method-assign]  # reason: deliberate per-test monkeypatch of a bound method
    application._cases = Mock()
    application._cases._read.return_value = (
        {
            "fields": [
                {
                    "destination": "cover",
                    "human_decision": None,
                    "proposal_id": "review_proposal_" + "d" * 64,
                }
            ]
        },
        _report(),
        None,
    )
    service = Mock()
    service.current.return_value = None
    service.existing.return_value = None
    service.latest_id.return_value = None
    service.prepare_observation.return_value = object()
    expected = {"consideration_id": "cover_consideration_" + "f" * 64}
    service.ensure.return_value = expected

    def observe(*_args):
        acquired: list[bool] = []

        def probe() -> None:
            held = application._gate.acquire(blocking=False)
            acquired.append(held)
            if held:
                application._gate.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(5)
        assert [True] == acquired
        return object()

    service.observe.side_effect = observe
    application._cover_considerations = service

    assert expected == application.prepare_cover_consideration(
        "review_case_" + "e" * 64
    )
    service.ensure.assert_called_once()


def test_cover_retry_allows_a_successor_after_stale_retirement() -> None:
    application = object.__new__(ReviewApplication)
    application._cover_considerations = Mock()
    application._cases = Mock()
    application._cases._read.return_value = (
        {
            "fields": [
                {
                    "destination": "cover",
                    "human_decision": {"outcome": "accept"},
                    "proposal_id": "review_proposal_" + "d" * 64,
                }
            ]
        },
        _report(),
        None,
    )
    application._cover_considerations.current.return_value = None
    application._cover_considerations.latest_id.return_value = (
        "cover_consideration_" + "a" * 64
    )
    successor = {"consideration_id": "cover_consideration_" + "b" * 64}
    application._cover_considerations.ensure.return_value = successor
    application._cover_considerations.prepare_observation.return_value = object()
    application._cover_considerations.observe.return_value = object()
    # A deliberate patch of the bound method on a bare instance.
    application.serialized = lambda call: call()  # type: ignore[method-assign]  # reason: deliberate per-test monkeypatch of a bound method

    assert successor == application.retry_cover_consideration("review_case_" + "e" * 64)


def test_attention_search_matches_once_per_package_before_field_expansion(tmp_path):
    repository = open_repository(tmp_path / "application.sqlite3")
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    assert (
        repository._connection.execute(
            "SELECT count(*) FROM review_case_field_facts"
        ).fetchone()[0]
        > 10
    )
    item_calls = 0

    def casefold(value: str) -> str:
        nonlocal item_calls
        if value == report.target.item:
            item_calls += 1
        return value.casefold()

    repository._connection.create_function(
        "ml_casefold", 1, casefold, deterministic=True
    )
    browser = BrowseReviewLibrary(repository)
    try:
        negative = browser.attention(search="absent-search-term", limit=10, cursor=None)
        assert negative["items"] == []
        assert negative["total"] == 0
        assert item_calls == 1
        positive = browser.attention(search=report.target.item, limit=10, cursor=None)
        assert positive["total"] == 1
        assert len(positive["items"]) == 1
        assert item_calls == 2
        cleared = browser.attention(search=None, limit=10, cursor=None)
        assert cleared["items"] == positive["items"]
        assert cleared["total"] == positive["total"]
        assert cleared["query"] is None
    finally:
        repository.close()


def test_attention_search_membership_uses_full_package_key(tmp_path):
    repository = open_repository(tmp_path / "application.sqlite3")
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    browser = BrowseReviewLibrary(repository)
    try:
        expected = browser.attention(search="alias-only", limit=10, cursor=None)
        assert expected["total"] == 0
        repository._connection.execute(
            "INSERT INTO current_review_packages "
            "SELECT connection_name,library_name,'alias-only',proposal_set_id,"
            "report_id,review_case_id,checkpoint,outcome FROM current_review_packages"
        )
        repository._connection.commit()
        matched = browser.attention(search="alias-only", limit=10, cursor=None)
        assert matched["total"] == 1
        assert len(matched["items"]) == 1
        assert (
            repository.read_current_attention_page("alias-only", 10, 0).rows[0][3] == 1
        )
        assert (
            browser.attention(search=report.target.item, limit=10, cursor=None)["total"]
            == 1
        )
    finally:
        repository.close()


@pytest.mark.parametrize("pointer", ["report_id", "proposal_set_id"])
def test_attention_search_preserves_malformed_pointer_refusal(tmp_path, pointer):
    from mechanical_librarian.application.metadata_report import MetadataReportError
    from mechanical_librarian.application.review_web import ReviewWebError
    from mechanical_librarian.domain.review_browse import ReviewBrowseValueError

    repository = open_repository(tmp_path / "application.sqlite3")
    first = _report()
    second = _report(title="Replacement", content_digest="c")
    for report in (first, second):
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
    first_proposal = evaluate_report(first)
    replacement = (
        first.report_id if pointer == "report_id" else first_proposal.proposal_set_id
    )
    repository._connection.execute(
        f"UPDATE current_review_packages SET {pointer}=?", (replacement,)
    )
    repository._connection.commit()
    try:
        with pytest.raises(ReviewWebError) as caught:
            BrowseReviewLibrary(repository).attention(
                search="Replacement", limit=10, cursor=None
            )
        assert caught.value.code == "unavailable"
        cause = caught.value.__cause__
        assert isinstance(cause, MetadataReportError)
        assert str(cause) == "review attention is unavailable"
        assert isinstance(cause.__cause__, ReviewBrowseValueError)
        assert str(cause.__cause__) == "review browse lineage disagrees"
    finally:
        repository.close()


def test_sqlite_library_and_attention_queries_are_current_and_bounded() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        repository = open_repository(database)
        source_report = _report(title="Straße ÅNGSTRÖM")
        unresolved_report = MetadataReport.create(
            target=TargetFacts(
                source_report.target.connection,
                source_report.target.library,
                source_report.target.item,
                tuple(
                    TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.VALUE,
                        ("Ada Hostile Search",),
                    )
                    if field.destination is TargetDestination.AUTHORS
                    else field
                    for field in source_report.target.fields
                ),
            ),
            file=source_report.file,
            local_claims=source_report.local_claims,
            local_reasons=source_report.local_reasons,
            providers=source_report.providers,
            cover_sources=source_report.cover_sources,
        )
        repository.publish(unresolved_report, lambda: True)
        unresolved_proposal = evaluate_report(unresolved_report)
        repository.publish_proposal(unresolved_proposal)
        unresolved_case_id = json.loads(
            repository.admit_review_case(unresolved_proposal)
        )["review_case_id"]

        providers = tuple(
            _proposal_provider(
                name,
                f"{name}-edition",
                (
                    _proposal_claim(
                        name,
                        f"{name}-edition",
                        ClaimKind.TITLE,
                        "Corrected title",
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority=authority,
            )
            for name, authority in (
                ("hardcover_primary", "hardcover"),
                ("open_library_primary", "open_library"),
            )
        )
        source = _proposal_report(providers=providers)
        automatic_report = MetadataReport.create(
            target=TargetFacts("primary", "novels", "ÉDITION-2", source.target.fields),
            file=FileFacts(1, "c" * 64, "d" * 64),
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=source.providers,
            cover_sources=source.cover_sources,
        )
        repository.publish(automatic_report, lambda: True)
        automatic_proposal = evaluate_report(automatic_report)
        repository.publish_proposal(automatic_proposal)
        repository.admit_review_case(automatic_proposal)

        assert repository.count_current_review_cases(None) == (2, 1)
        for search in (
            None,
            "ångström",
            "STRASSE",
            "NOVELS",
            "ada hostile",
            "EPUB",
            "%",
            "_",
            "\\",
        ):
            totals = repository.count_current_review_cases(search)
            attention_total = repository.count_current_attention_fields(search)
            for lens in (None, True, False):
                complete = repository.read_current_library_page(search, 50, 0, lens)
                assert (complete.total, complete.attention_total) == totals
                for offset in (0, 1, 50):
                    page = repository.read_current_library_page(search, 1, offset, lens)
                    assert page.rows == complete.rows[offset : offset + 1]
                    assert (page.total, page.attention_total) == totals
            complete_attention = repository.read_current_attention_page(search, 50, 0)
            assert complete_attention.total == attention_total
            for offset in (0, 1, 50):
                fields = repository.read_current_attention_page(search, 1, offset)
                assert fields.rows == complete_attention.rows[offset : offset + 1]
                assert fields.total == attention_total
        attention_rows = repository.list_current_review_case_documents(
            None, 10, 0, True
        )
        assert len(attention_rows) == 1
        assert (
            unresolved_case_id
            == ReviewCase.from_json(attention_rows[0][0]).review_case_id
        )
        automatic_rows = repository.list_current_review_case_documents(
            "édition-2", 10, 0, False
        )
        assert len(automatic_rows) == 1
        assert repository.count_current_review_cases("ångström") == (1, 1)
        assert repository.count_current_review_cases("STRASSE") == (1, 1)
        assert repository.count_current_review_cases("NOVELS") == (1, 0)
        assert repository.count_current_review_cases("ada hostile") == (1, 1)
        assert repository.count_current_review_cases("EPUB") == (2, 1)
        assert repository.count_current_review_cases("%") == (0, 0)
        assert repository.count_current_review_cases("_") == (0, 0)
        assert repository.count_current_review_cases("\\") == (0, 0)
        queue = repository.list_current_attention_documents(None, 10, 0)
        assert len(queue) == 1
        assert queue[0][2] == "title"
        assert repository.count_current_attention_fields(None) == 1
        assert repository.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="title",
            reason="field_unavailable",
            changed_at=1,
        ) == (unresolved_case_id,)
        assert repository.count_current_review_cases(None) == (2, 0)
        assert repository.count_current_attention_fields(None) == 0
        assert repository.list_current_attention_documents(None, 10, 0) == ()
        repository.clear_field_effect_capability_suspension(
            scope="destination",
            connection="primary",
            destination="title",
            reason="field_unavailable",
            observed_at=2,
        )
        assert repository.count_current_review_cases(None) == (2, 1)
        assert repository.suspend_field_effect_capability(
            scope="connection",
            connection="primary",
            destination="title",
            reason="connection_unavailable",
            changed_at=2,
        ) == (unresolved_case_id,)
        assert repository.count_current_review_cases(None) == (2, 0)
        assert repository.count_current_attention_fields(None) == 0
        repository.clear_field_effect_capability_suspension(
            scope="connection",
            connection="primary",
            destination="",
            reason="connection_unavailable",
            observed_at=3,
        )
        assert repository.count_current_review_cases(None) == (2, 1)
        assert repository.count_current_attention_fields("STRASSE") == 1
        assert len(repository.list_current_attention_documents("ångström", 10, 0)) == 1
        assert repository.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="title",
            reason="insufficient_permission",
            changed_at=4,
        ) == (unresolved_case_id,)
        assert (
            repository.suspend_field_effect_capability(
                scope="destination",
                connection="primary",
                destination="title",
                reason="insufficient_permission",
                changed_at=5,
            )
            == ()
        )
        repository.close()
        reopened = open_repository(database, create=False)
        assert (
            reopened.field_effect_capability_suspension("primary", "title")
            == "insufficient_permission"
        )
        assert reopened.count_current_review_cases("ångström") == (1, 0)
        assert reopened.count_current_review_cases("STRASSE") == (1, 0)
        assert reopened.count_current_review_cases("NOVELS") == (1, 0)
        reopened.close()


@pytest.mark.parametrize(
    (
        "scope",
        "decide_sibling",
        "via_reconcile",
        "successful",
        "interleave",
        "inline_ambiguous",
    ),
    (
        ("destination", False, False, False, False, False),
        ("destination", True, False, False, False, False),
        ("connection", False, False, False, False, False),
        ("destination", False, True, False, False, False),
        ("destination", True, False, True, False, False),
        ("connection", False, False, False, True, False),
        ("destination", False, False, False, False, True),
    ),
)
def test_suspension_announces_only_changed_persisted_library_cases(
    scope: str,
    decide_sibling: bool,
    via_reconcile: bool,
    successful: bool,
    interleave: bool,
    inline_ambiguous: bool,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository = open_repository(root / "application.sqlite3")
        trigger_source = _bibliographic_report(
            current_title="Suspension trigger",
            providers=(
                _bibliographic_source(
                    "catalogue", "edition-1", titles=(), series="A series"
                ),
            ),
        )
        source = MetadataReport.create(
            target=TargetFacts(
                trigger_source.target.connection,
                trigger_source.target.library,
                "suspension-trigger",
                trigger_source.target.fields,
            ),
            file=FileFacts(
                2,
                "e" * 64,
                "f" * 64,
                trigger_source.file.format,
            ),
            local_claims=trigger_source.local_claims,
            local_reasons=trigger_source.local_reasons,
            providers=trigger_source.providers,
            cover_sources=trigger_source.cover_sources,
        )
        if scope == "destination":
            sibling = _bibliographic_report(
                current_title="Mistborn Secret History",
                providers=(
                    _bibliographic_source(
                        "catalogue", "edition-1", titles=(), series="Mistborn"
                    ),
                ),
            )
            sibling_destination = "series"
        else:
            sibling_source = _report(title="Connection sibling", content_digest="c")
            sibling = MetadataReport.create(
                target=TargetFacts(
                    source.target.connection,
                    source.target.library,
                    "connection-sibling",
                    tuple(
                        TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.VALUE,
                            ("Connection sibling",),
                        )
                        if field.destination is TargetDestination.AUTHORS
                        else field
                        for field in sibling_source.target.fields
                    ),
                ),
                file=FileFacts(3, "c" * 64, "d" * 64),
                local_claims=sibling_source.local_claims,
                local_reasons=sibling_source.local_reasons,
                providers=sibling_source.providers,
                cover_sources=sibling_source.cover_sources,
            )
            sibling_destination = "title"

        def admit_and_decide(
            report: MetadataReport, idempotency_key: str
        ) -> tuple[str, HumanDecision, dict[str, object]]:
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            review_case_id = json.loads(repository.admit_review_case(proposal))[
                "review_case_id"
            ]
            assert type(review_case_id) is str
            fields = InspectCurrentReviewCase(repository).run(review_case_id)["fields"]
            field = next(
                cast("dict[str, object]", value)
                for value in cast("list[object]", fields)
                if cast("dict[str, object]", value)["destination"] == "series"
            )
            selected = cast("list[object]", field["proposed_values"])[0]
            acknowledgement = RecordHumanDecision(repository, clock=lambda: 1).run(
                review_case_id,
                proposal_id=str(field["proposal_id"]),
                target_fingerprint=str(field["target_fingerprint"]),
                destination="series",
                actor="owner",
                outcome="accept",
                selected_value=cast("dict[str, object]", selected),
                idempotency_key=idempotency_key,
            )
            document = repository.find_human_decision(
                HumanDecision.idempotency_digest_for(idempotency_key)
            )
            assert document is not None
            return review_case_id, HumanDecision.from_json(document), acknowledgement

        trigger_case_id, trigger_decision, acknowledgement = admit_and_decide(
            source, "suspension-trigger-decision-0001"
        )
        repository.publish(sibling, lambda: True)
        sibling_proposal = evaluate_report(sibling)
        repository.publish_proposal(sibling_proposal)
        sibling_case_id: str | None = None
        if not interleave:
            sibling_case_id = json.loads(
                repository.admit_review_case(sibling_proposal)
            )["review_case_id"]
            assert type(sibling_case_id) is str
            assert sibling_case_id != trigger_case_id
        if decide_sibling:
            assert sibling_case_id is not None
            fields = InspectCurrentReviewCase(repository).run(sibling_case_id)["fields"]
            field = next(
                cast("dict[str, object]", value)
                for value in cast("list[object]", fields)
                if cast("dict[str, object]", value)["destination"]
                == sibling_destination
            )
            RecordHumanDecision(repository, clock=lambda: 1).run(
                sibling_case_id,
                proposal_id=str(field["proposal_id"]),
                target_fingerprint=str(field["target_fingerprint"]),
                destination=sibling_destination,
                actor="owner",
                outcome="accept",
                selected_value=cast("dict[str, object]", field["proposed_values"][0]),
                idempotency_key="suspension-sibling-decision-0001",
            )

        application, target, authority, capabilities, consent = (
            _field_effect_application(repository, root)
        )
        if interleave:
            observe = capabilities.observe

            def admit_sibling_during_capability_observation(
                plan: FieldEffectPlan,
            ) -> object:
                nonlocal sibling_case_id
                sibling_case_id = json.loads(
                    repository.admit_review_case(sibling_proposal)
                )["review_case_id"]
                assert type(sibling_case_id) is str
                return observe(plan)

        authority.bind(
            trigger_decision,
            connection=source.target.connection,
            library=source.target.library,
            work_id=source.target.item,
            target_version="version-1",
            source="persisted_review_case",
        )
        consent.allow(
            connection=source.target.connection,
            library=source.target.library,
            destination="series",
        )
        operation = application.plan(
            trigger_decision,
            connection=source.target.connection,
            library=source.target.library,
            work_id=source.target.item,
            destination="series",
            pre_value=None,
            desired_value=trigger_decision.selected_value,
            target_fingerprint=trigger_decision.target_fingerprint,
            target_version="version-1",
            dependency_digests=("d" * 64,),
            capability_digest="c" * 64,
        )
        assert operation is not None
        target.seed(operation.plan)
        if via_reconcile:

            def lose_verification(_: object) -> None:
                target.unavailable_scope_next = "destination"
                target.unavailable_reason_next = "field_unavailable"

            target.after_apply = lose_verification
            assert application.apply(operation.plan.plan_id).state == "reconciling"
            repository.clear_field_effect_capability_suspension(
                scope="destination",
                connection=source.target.connection,
                destination="series",
                reason="field_unavailable",
                observed_at=11,
            )
        if not successful and not inline_ambiguous:
            target.unavailable_scope_next = (
                "connection" if scope == "connection" else "destination"
            )
            target.unavailable_reason_next = (
                "connection_unavailable"
                if scope == "connection"
                else "field_unavailable"
            )
        service = ContinuousGuardedApplication(
            repository,
            {(source.target.connection, source.target.library): application},
            {(source.target.connection, source.target.library): capabilities},
            authority,
            ((source.target.connection, source.target.library),),
            lambda: 2,
            lambda *_: None,
        )
        announcements: list[tuple[str, str | None]] = []
        service.attach_updates(
            lambda topic, subject: announcements.append((topic, subject))
        )
        browse = BrowseReviewLibrary(repository)
        expected_before = () if decide_sibling or interleave else (sibling_case_id,)
        assert browse.attention(search=None, limit=10, cursor=None)["total"] == len(
            expected_before
        )
        assert (
            repository._field_effects._current_attention_review_case_ids(
                source.target.connection,
                None if scope == "connection" else "series",
            )
            == expected_before
        )
        if inline_ambiguous:
            target.ambiguous_next = "pre"

            def suspend_targeted_reconciliation(_: FieldEffectPlan) -> None:
                target.unavailable_scope_next = "destination"
                target.unavailable_reason_next = "field_unavailable"

            # The recovery hook sits immediately after the ambiguous apply has
            # persisted its targeted reconciliation and before the inline
            # action claims it. It proves that this retryable suspension came
            # from reconciliation rather than either of the ordinary reads.
            application._recovery_hook = suspend_targeted_reconciliation
            completed = service.apply(operation.plan.plan_id)
        elif interleave:
            with patch.object(
                capabilities,
                "observe",
                side_effect=admit_sibling_during_capability_observation,
            ):
                completed = service.apply(operation.plan.plan_id)
        elif via_reconcile:
            completed = service.reconcile(operation.plan.plan_id)
        else:
            completed = service.apply(operation.plan.plan_id)

        assert sibling_case_id is not None
        expected_affected = () if decide_sibling else (sibling_case_id,)
        assert completed.affected_review_case_ids == expected_affected
        if inline_ambiguous:
            assert target.ambiguous_next is None
            assert completed.state == "retryable"
            assert completed.affected_review_case_ids == (sibling_case_id,)
            assert announcements == [
                ("activity", None),
                ("library", sibling_case_id),
            ]

        if successful:
            assert completed.state == "verified"
            assert (
                repository.field_effect_capability_suspension(
                    source.target.connection, "series"
                )
                is None
            )
        else:
            assert repository.field_effect_capability_suspension(
                source.target.connection, "" if scope == "connection" else "series"
            ) == (
                "connection_unavailable"
                if scope == "connection"
                else "field_unavailable"
            )
        assert (
            repository._field_effects._current_attention_review_case_ids(
                source.target.connection,
                None if scope == "connection" else "series",
            )
            == ()
        )
        after_all = browse.library(lens="all", search=None, limit=10, cursor=None)
        assert sibling_case_id in {
            item["review_case_id"]
            for item in cast("list[dict[str, object]]", after_all["items"])
        }
        assert announcements == [
            ("activity", None),
            *((("library", sibling_case_id),) if not decide_sibling else ()),
        ]
        assert acknowledgement["review_case_id"] == trigger_case_id
        repository.close()


def test_sqlite_library_groups_attention_by_current_provider_completeness() -> None:
    """Current report lanes rank attention without creating a second status."""

    limited_statuses = {
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
    complete_statuses = {
        ProviderStatus.DISABLED,
        ProviderStatus.INSUFFICIENT_INPUT,
        ProviderStatus.NO_MATCH,
    }

    def report_for(
        title: str,
        item: str,
        index: int,
        status: ProviderStatus,
        *,
        actionable: bool = True,
    ) -> MetadataReport:
        source = _report(
            title,
            f"{index % 16:x}",
            f"{(index + 10) % 16:x}",
            actionable=actionable,
        )
        return MetadataReport.create(
            target=TargetFacts(
                source.target.connection,
                source.target.library,
                item,
                source.target.fields,
            ),
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=(ProviderObservation(f"provider-{index}", status),),
        )

    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        reports = tuple(
            report_for(
                f"Alpha limited {status.value}",
                f"limited-{index}",
                index,
                status,
            )
            for index, status in enumerate(sorted(limited_statuses, key=str))
        ) + tuple(
            report_for(
                f"Zulu complete {status.value}",
                f"complete-{index}",
                index + len(limited_statuses),
                status,
            )
            for index, status in enumerate(sorted(complete_statuses, key=str))
        )
        for report in reports:
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)

        rows = repository.list_current_review_case_documents(None, 20, 0, True)
        titles = [
            cast(str, MetadataReport.from_json(report_document).target.fields[0].value)
            for _, report_document in rows
        ]
        assert titles == sorted(
            (title for title in titles if title.startswith("Zulu complete")),
            key=str.casefold,
        ) + sorted(
            (title for title in titles if title.startswith("Alpha limited")),
            key=str.casefold,
        )

        limited = report_for(
            "Alpha reclassified", "reclassified", 30, ProviderStatus.UNAVAILABLE
        )
        complete = report_for("Zulu stable", "stable", 31, ProviderStatus.NO_MATCH)
        for report in (limited, complete):
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
        before = repository.list_current_review_case_documents(None, 20, 0, True)
        assert MetadataReport.from_json(before[-1][1]).target.item == "reclassified"

        successor = report_for(
            "Alpha reclassified", "reclassified", 32, ProviderStatus.NO_MATCH
        )
        repository.publish(successor, lambda: True)
        successor_proposal = evaluate_report(successor)
        repository.publish_proposal(successor_proposal)
        repository.admit_review_case(successor_proposal)
        after = repository.list_current_review_case_documents(None, 20, 0, True)
        assert MetadataReport.from_json(after[0][1]).target.item == "reclassified"

        for report in (
            report_for(
                "Zulu no attention",
                "no-attention-limited",
                33,
                ProviderStatus.UNAVAILABLE,
                actionable=False,
            ),
            report_for(
                "Alpha no attention",
                "no-attention-complete",
                34,
                ProviderStatus.NO_MATCH,
                actionable=False,
            ),
        ):
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
        no_attention = repository.list_current_review_case_documents(
            "no attention", 20, 0, False
        )
        assert [
            MetadataReport.from_json(report_document).target.fields[0].value
            for _, report_document in no_attention
        ] == ["Alpha no attention", "Zulu no attention"]
        repository.close()


def test_sqlite_attention_keeps_manual_bibliographic_membership_field_local() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        report = _bibliographic_report(
            current_title="Mistborn Secret History",
            providers=(
                _bibliographic_source(
                    "catalogue",
                    "edition-1",
                    titles=(),
                    series="Mistborn",
                ),
            ),
        )
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
        first = repository.list_current_attention_documents(None, 1, 0)
        assert len(first) == 1
        assert first[0][3] == 1
        rows = repository.list_current_attention_documents(None, 10, 0)
        assert [row[2] for row in rows] == ["series"]
        assert repository.count_current_attention_fields(None) == 1
        assert repository.count_current_review_cases(None) == (1, 1)
        repository.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="series",
            reason="field_unavailable",
            changed_at=1,
        )
        suspended = InspectCurrentReviewCase(repository).run(
            json.loads(repository.admit_review_case(proposal))["review_case_id"]
        )
        field = next(
            value for value in suspended["fields"] if value["destination"] == "series"
        )
        assert (
            "This field is waiting for an external service recovery."
            == field["classification_reason"]
        )
        assert [
            row[2] for row in repository.list_current_attention_documents(None, 1, 0)
        ] == []
        assert repository.count_current_attention_fields(None) == 0
        repository.clear_field_effect_capability_suspension(
            scope="destination",
            connection="primary",
            destination="series",
            reason="field_unavailable",
            observed_at=2,
        )
        repository.close()


def test_current_pointer_excludes_current_only_successors_from_owner_attention() -> (
    None
):
    """One work alternates between context-only and owner-actionable packets."""

    def report(revision: str, *, actionable: bool) -> MetadataReport:
        source = _report(
            "Lifecycle work",
            content_digest=revision,
            file_identity=revision,
            actionable=actionable,
        )
        return MetadataReport.create(
            target=source.target,
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=source.providers,
            cover_sources=source.cover_sources,
        )

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        repository = open_repository(path)

        def admit(revision: str, *, actionable: bool) -> dict[str, object]:
            source = report(revision, actionable=actionable)
            repository.publish(source, lambda: True)
            proposal = evaluate_report(source)
            repository.publish_proposal(proposal)
            return json.loads(repository.admit_review_case(proposal))

        initial = admit("a", actionable=False)
        assert initial["status"] == "no_case"
        assert repository.count_current_review_cases(None) == (1, 0)
        assert repository.count_current_attention_fields(None) == 0
        assert repository.read_review_attention(None, 10, 0).total == 0

        actionable = admit("b", actionable=True)
        case_id = str(actionable["review_case_id"])
        assert repository.count_current_review_cases(None) == (1, 1)
        assert repository.count_current_attention_fields(None) == 1
        assert [
            ReviewCase.from_json(row[0]).review_case_id
            for row in repository.read_review_attention(None, 10, 0).rows
        ] == [case_id]
        detail = InspectCurrentReviewCase(repository).run(case_id)
        title = next(
            field for field in detail["fields"] if field["destination"] == "title"
        )
        assert title["decision_actions"] == ["keep_current", "accept"]
        assert title["needs_attention"] is True
        assert (
            ReviewCase.from_json(
                repository.list_current_review_case_documents(None, 10, 0, True)[0][0]
            ).review_case_id
            == case_id
        )

        later = admit("c", actionable=False)
        assert later["status"] in {"no_case", "retired"}
        assert repository.count_current_review_cases(None) == (1, 0)
        assert repository.count_current_attention_fields(None) == 0
        assert repository.read_review_attention(None, 10, 0).rows == ()
        repository.close()
        reopened = open_repository(path, create=False)
        assert reopened.count_current_review_cases(None) == (1, 0)
        assert reopened.count_current_attention_fields(None) == 0
        assert reopened.read_review_attention(None, 10, 0).rows == ()
        reopened.close()


def test_admitted_preservation_only_sibling_enters_attention_only_as_successor() -> (
    None
):
    """An admitted case can retain readable non-owner sibling context."""

    def revised(titles: tuple[str, ...], revision: str) -> MetadataReport:
        providers = (
            ()
            if not titles
            else (
                _bibliographic_source(
                    "catalogue-a", "edition-1", titles=(), series="One"
                ),
                _bibliographic_source(
                    "catalogue-b", "edition-2", titles=(), series="Two"
                ),
            )
        )
        source = _bibliographic_report(
            current_title="Old title",
            providers=providers,
            local_claims=_report().local_claims,
        )
        return MetadataReport.create(
            target=source.target,
            file=FileFacts(
                1,
                revision * 64,
                ("b" if revision == "a" else "c") * 64,
                source.file.format,
            ),
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=source.providers,
            cover_sources=source.cover_sources,
        )

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "application.sqlite3"
        repository = open_repository(path)

        def publish(report: MetadataReport) -> str:
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            return str(
                json.loads(repository.admit_review_case(proposal))["review_case_id"]
            )

        first_case_id = publish(revised((), "a"))
        first = InspectCurrentReviewCase(repository).run(first_case_id)
        title = next(
            field for field in first["fields"] if field["destination"] == "series"
        )
        assert title["decision_actions"] == []
        assert title["needs_attention"] is False
        assert [
            row[2] for row in repository.list_current_attention_documents(None, 10, 0)
        ] == ["title"]
        assert repository.count_current_attention_fields(None) == 1

        successor_id = publish(revised(("series now contested",), "b"))
        assert successor_id != first_case_id
        assert repository.current_review_case_successor(first_case_id) == successor_id
        assert [
            row[2] for row in repository.list_current_attention_documents(None, 10, 0)
        ] == [
            "series",
            "title",
        ]
        successor = InspectCurrentReviewCase(repository).run(successor_id)
        successor_title = next(
            field for field in successor["fields"] if field["destination"] == "series"
        )
        assert successor_title["decision_actions"] == ["keep_current", "accept"]
        assert successor_title["needs_attention"] is True
        repository.close()

        reopened = open_repository(path, create=False)
        assert reopened.current_review_case_successor(first_case_id) == successor_id
        assert [
            row[2] for row in reopened.list_current_attention_documents(None, 10, 0)
        ] == [
            "series",
            "title",
        ]
        reopened.close()


def test_sqlite_attention_excludes_epub_inapplicable_destinations_from_membership() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        report = _bibliographic_report(
            current_title="Mistborn Secret History",
            providers=(
                _bibliographic_source(
                    "catalogue", "edition-1", titles=(), series="Mistborn"
                ),
            ),
        )
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        review_case_id = json.loads(repository.admit_review_case(proposal))[
            "review_case_id"
        ]
        repository._connection.execute(
            "UPDATE review_case_field_facts SET unresolved_choice=1 "
            "WHERE review_case_id=? AND destination IN ('narrators','explicit')",
            (review_case_id,),
        )

        assert report.file.format == "epub"
        assert repository._connection.execute(
            "SELECT destination,unresolved_choice FROM review_case_field_facts "
            "WHERE review_case_id=? AND destination IN ('narrators','explicit') "
            "ORDER BY destination",
            (review_case_id,),
        ).fetchall() == [("explicit", 1), ("narrators", 1)]
        assert [
            row[2] for row in repository.list_current_attention_documents(None, 10, 0)
        ] == ["series"]
        assert repository.count_current_attention_fields(None) == 1
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=1, cursor=None
        )
        assert attention["total"] == 1
        assert attention["next_cursor"] is None
        assert [
            (item["destination"], item["work_attention_count"])
            for item in attention["items"]
        ] == [("series", 1)]
        repository.close()


def test_sqlite_library_excludes_epub_conflicting_narrators_from_attention() -> None:
    def with_item(source: MetadataReport, item: str) -> MetadataReport:
        return MetadataReport.create(
            target=TargetFacts(
                source.target.connection,
                source.target.library,
                item,
                source.target.fields,
            ),
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=source.providers,
            cover_sources=source.cover_sources,
        )

    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        narrator_report = with_item(
            _proposal_report(
                title="Narrator conflict",
                fields=_proposal_fields(
                    narrators=TargetField(
                        TargetDestination.NARRATORS, TargetFieldState.MISSING
                    )
                ),
                providers=(
                    _proposal_provider(
                        "catalogue-a",
                        "edition-a",
                        (
                            _proposal_claim(
                                "catalogue-a",
                                "edition-a",
                                ClaimKind.CONTRIBUTOR,
                                "Nora Narrator",
                                scope=ClaimScope.EDITION,
                                role="narrator",
                            ),
                        ),
                    ),
                    _proposal_provider(
                        "catalogue-b",
                        "edition-b",
                        (
                            _proposal_claim(
                                "catalogue-b",
                                "edition-b",
                                ClaimKind.CONTRIBUTOR,
                                "Pia Narrator",
                                scope=ClaimScope.EDITION,
                                role="narrator",
                            ),
                        ),
                    ),
                ),
            ),
            "narrator-conflict",
        )
        series_report = with_item(
            _bibliographic_report(
                current_title="Mistborn Secret History",
                providers=(
                    _bibliographic_source(
                        "catalogue", "edition-1", titles=(), series="Mistborn"
                    ),
                ),
            ),
            "series-attention",
        )
        for report in (narrator_report, series_report):
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)

        assert narrator_report.file.format == "epub"
        assert repository.count_current_review_cases(None) == (2, 1)
        assert repository.count_current_review_cases("narrator-conflict") == (1, 0)
        assert (
            repository.list_current_attention_documents("narrator-conflict", 10, 0)
            == ()
        )
        browse = BrowseReviewLibrary(repository)
        first = browse.library(lens="all", search=None, limit=1, cursor=None)
        assert first["counts"] == {
            "all": 2,
            "needs_attention": 1,
            "no_attention_needed": 1,
        }
        assert [item["title"] for item in first["items"]] == ["Mistborn Secret History"]
        assert first["next_cursor"] is not None
        second = browse.library(
            lens="all", search=None, limit=1, cursor=first["next_cursor"]
        )
        assert [
            (item["title"], item["attention_count"], item["attention_state"])
            for item in second["items"]
        ] == [("Current title", 0, "no_attention_needed")]
        assert second["next_cursor"] is None
        assert [
            item["title"]
            for item in browse.library(
                lens="needs_attention", search=None, limit=1, cursor=None
            )["items"]
        ] == ["Mistborn Secret History"]
        assert [
            item["title"]
            for item in browse.library(
                lens="no_attention_needed",
                search="narrator-conflict",
                limit=1,
                cursor=None,
            )["items"]
        ] == ["Current title"]
        repository.close()


def test_sqlite_complete_library_keeps_current_no_case_and_retired_work() -> None:
    def for_item(source: MetadataReport, item: str) -> MetadataReport:
        return MetadataReport.create(
            target=TargetFacts(
                source.target.connection,
                source.target.library,
                item,
                source.target.fields,
            ),
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=source.providers,
            cover_sources=source.cover_sources,
        )

    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        no_case = for_item(
            _report("No-case work", "1", "2", actionable=False),
            "no-case-item",
        )
        repository.publish(no_case, lambda: True)
        no_case_proposal = evaluate_report(no_case)
        repository.publish_proposal(no_case_proposal)
        assert (
            json.loads(repository.admit_review_case(no_case_proposal))["status"]
            == "no_case"
        )

        actionable = for_item(_report("Former case", "3", "4"), "retired-item")
        repository.publish(actionable, lambda: True)
        actionable_proposal = evaluate_report(actionable)
        repository.publish_proposal(actionable_proposal)
        repository.admit_review_case(actionable_proposal)
        retired = for_item(
            _report("Retired work", "5", "6", actionable=False),
            "retired-item",
        )
        repository.publish(retired, lambda: True)
        retired_proposal = evaluate_report(retired)
        repository.publish_proposal(retired_proposal)
        assert (
            json.loads(repository.admit_review_case(retired_proposal))["status"]
            == "retired"
        )
        in_progress = for_item(
            _report("In-progress report", "7", "8"),
            "in-progress-item",
        )
        repository.publish(in_progress, lambda: True)

        assert repository.count_current_review_cases(None) == (3, 0)
        assert repository.count_current_review_cases("No-case") == (1, 0)
        assert repository.count_current_review_cases("In-progress") == (1, 0)
        rows = repository.list_current_review_case_documents(None, 10, 0, False)
        assert len(rows) == 3
        assert all(case_document is None for case_document, _ in rows)
        assert repository.list_current_attention_documents(None, 10, 0) == ()
        repository.close()


def test_sqlite_library_order_is_total_across_duplicate_work_identity() -> None:
    def for_connection(source: MetadataReport, connection: str) -> MetadataReport:
        return MetadataReport.create(
            target=TargetFacts(
                connection,
                source.target.library,
                source.target.item,
                source.target.fields,
            ),
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=source.providers,
            cover_sources=source.cover_sources,
        )

    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        source = _report("Duplicate title", "1", "2", actionable=False)
        for connection in ("zeta", "alpha"):
            repository.publish(for_connection(source, connection), lambda: True)

        pages = [
            repository.list_current_review_case_documents(None, 1, offset, None)
            for offset in (0, 1)
        ]
        connections = [
            MetadataReport.from_json(page[0][1]).target.connection for page in pages
        ]
        assert connections == ["alpha", "zeta"]
        assert len(set(connections)) == 2
        assert (
            repository.list_current_review_case_documents(None, 1, 0, None) == pages[0]
        )
        repository.close()


def _scheduled_suspension_fixture(
    directory: Path,
) -> tuple[
    SQLiteMetadataReportRepository,
    ContinuousGuardedApplication,
    FakeCapabilityObserver,
    str,
    str,
]:
    """Build one decided trigger beside the still-unresolved Library sibling."""
    repository = open_repository(directory / "application.sqlite3")
    trigger_source = _bibliographic_report(
        current_title="Scheduled suspension trigger",
        providers=(
            _bibliographic_source("catalogue", "edition-1", titles=(), series="A"),
        ),
    )
    trigger = MetadataReport.create(
        target=TargetFacts(
            trigger_source.target.connection,
            trigger_source.target.library,
            "scheduled-suspension-trigger",
            trigger_source.target.fields,
        ),
        file=FileFacts(8, "c" * 64, "d" * 64, trigger_source.file.format),
        local_claims=trigger_source.local_claims,
        local_reasons=trigger_source.local_reasons,
        providers=trigger_source.providers,
        cover_sources=trigger_source.cover_sources,
    )
    repository.publish(trigger, lambda: True)
    proposal = evaluate_report(trigger)
    repository.publish_proposal(proposal)
    trigger_case_id = json.loads(repository.admit_review_case(proposal))[
        "review_case_id"
    ]
    assert type(trigger_case_id) is str
    fields = InspectCurrentReviewCase(repository).run(trigger_case_id)["fields"]
    trigger_field = next(
        cast("dict[str, object]", field)
        for field in cast("list[object]", fields)
        if cast("dict[str, object]", field)["destination"] == "series"
    )
    RecordHumanDecision(repository, clock=lambda: 1).run(
        trigger_case_id,
        proposal_id=str(trigger_field["proposal_id"]),
        target_fingerprint=str(trigger_field["target_fingerprint"]),
        destination="series",
        actor="owner",
        outcome="accept",
        selected_value=cast("dict[str, object]", trigger_field["proposed_values"])[0],
        idempotency_key="scheduled-suspension-trigger-0001",
    )
    document = repository.find_human_decision(
        HumanDecision.idempotency_digest_for("scheduled-suspension-trigger-0001")
    )
    assert document is not None
    decision = HumanDecision.from_json(document)
    sibling_source = _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(
            _bibliographic_source(
                "catalogue", "edition-1", titles=(), series="Mistborn"
            ),
        ),
    )
    sibling = sibling_source
    repository.publish(sibling, lambda: True)
    sibling_proposal = evaluate_report(sibling)
    repository.publish_proposal(sibling_proposal)
    sibling_case_id = json.loads(repository.admit_review_case(sibling_proposal))[
        "review_case_id"
    ]
    assert type(sibling_case_id) is str
    application, _target, authority, capabilities, consent = _field_effect_application(
        repository, directory
    )
    authority.bind(
        decision,
        connection=trigger.target.connection,
        library=trigger.target.library,
        work_id=trigger.target.item,
        target_version="version-1",
        source="persisted_review_case",
    )
    consent.allow(
        connection=trigger.target.connection,
        library=trigger.target.library,
        destination="series",
    )
    service = ContinuousGuardedApplication(
        repository,
        {(trigger.target.connection, trigger.target.library): application},
        {(trigger.target.connection, trigger.target.library): capabilities},
        authority,
        ((trigger.target.connection, trigger.target.library),),
        lambda: 2,
        lambda *_: None,
    )
    return repository, service, capabilities, sibling_case_id, document


@pytest.mark.parametrize("entrypoint", ("schedule", "enable"))
def test_real_sqlite_suspension_refreshes_library_and_attention_for_public_callers(
    entrypoint: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        (
            repository,
            service,
            capabilities,
            sibling_case_id,
            decision_document,
        ) = _scheduled_suspension_fixture(Path(directory))
        capabilities.category = "field_unavailable"
        announcements: list[tuple[str, str | None]] = []
        service.attach_updates(
            lambda topic, subject: announcements.append((topic, subject))
        )
        if entrypoint == "schedule":
            connection, library = service._libraries[0]
            repository.grant_field_effect_consent_matrix(
                connection, ((library, ("series",)),), 1
            )
            assert any(
                row["review_case_id"] == sibling_case_id
                and row["destination"] == "series"
                for row in BrowseReviewLibrary(repository).attention(
                    search=None, limit=10, cursor=None
                )["items"]
            )
            with patch.object(
                repository,
                "pending_field_effect_decision_documents",
                return_value=(decision_document,),
            ):
                assert service.schedule() == 0
            assert (
                repository.field_effect_capability_suspension(connection, "series")
                == "field_unavailable"
            )
        else:
            with (
                patch.object(
                    repository,
                    "pending_field_effect_decision_documents",
                    return_value=(decision_document,),
                ),
                pytest.raises(Exception, match="capability"),
            ):
                service.enable(((service._libraries[0][1], ("series",)),))
        browse = BrowseReviewLibrary(repository)
        all_rows = browse.library(lens="all", search=None, limit=10, cursor=None)[
            "items"
        ]
        sibling = next(
            row for row in all_rows if row["review_case_id"] == sibling_case_id
        )
        expected_count = 0
        assert sibling["attention_count"] == expected_count
        assert sibling["attention_state"] == (
            "needs_attention" if expected_count else "no_attention_needed"
        )
        needs_attention = browse.library(
            lens="needs_attention", search=None, limit=10, cursor=None
        )["items"]
        assert needs_attention == []
        no_attention = {
            row["review_case_id"]
            for row in browse.library(
                lens="no_attention_needed", search=None, limit=10, cursor=None
            )["items"]
        }
        assert sibling_case_id in no_attention
        attention = browse.attention(search=None, limit=10, cursor=None)
        assert attention["total"] == 0
        assert attention["items"] == []
        assert announcements == [("activity", None), ("library", sibling_case_id)]
        repository.close()


def test_scheduler_flushes_committed_suspension_before_later_repository_failure() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        (
            repository,
            service,
            capabilities,
            sibling_case_id,
            decision_document,
        ) = _scheduled_suspension_fixture(Path(directory))
        connection, library = service._libraries[0]
        repository.grant_field_effect_consent_matrix(
            connection, ((library, ("series",)),), 1
        )
        persisted_consent = repository.current_field_effect_consent(
            connection, library, "series"
        )
        announcements: list[tuple[str, str | None]] = []
        service.attach_updates(
            lambda topic, subject: announcements.append((topic, subject))
        )
        with (
            patch.object(
                repository,
                "pending_field_effect_decision_documents",
                return_value=(decision_document, decision_document),
            ),
            patch.object(
                capabilities,
                "observe",
                side_effect=(
                    CapabilityObservation("field_unavailable", "c" * 64),
                    CapabilityObservation("allowed", "c" * 64),
                ),
            ),
            patch.object(
                repository,
                "current_field_effect_consent",
                side_effect=(persisted_consent, FieldEffectError("persistence")),
            ),
            pytest.raises(FieldEffectError, match="persistence"),
        ):
            service.schedule()
        assert announcements == [("activity", None), ("library", sibling_case_id)]
        repository.close()


def test_enable_flushes_committed_suspension_before_later_repository_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        (
            repository,
            service,
            capabilities,
            sibling_case_id,
            decision_document,
        ) = _scheduled_suspension_fixture(Path(directory))
        connection, library = service._libraries[0]
        del connection
        announcements: list[tuple[str, str | None]] = []
        service.attach_updates(
            lambda topic, subject: announcements.append((topic, subject))
        )
        observed_series = False

        def observe_after_series(plan: FieldEffectPlan) -> CapabilityObservation:
            nonlocal observed_series
            if observed_series:
                raise FieldEffectError("persistence")
            if plan.destination == "series":
                observed_series = True
                return CapabilityObservation("field_unavailable", "c" * 64)
            return CapabilityObservation("allowed", "c" * 64)

        with (
            patch.object(
                repository,
                "pending_field_effect_decision_documents",
                return_value=(decision_document,),
            ),
            patch.object(
                capabilities,
                "observe",
                side_effect=observe_after_series,
            ),
            pytest.raises(FieldEffectError, match="persistence"),
        ):
            service.enable(((library, ("series",)),))
        assert announcements == [("activity", None), ("library", sibling_case_id)]
        repository.close()


@pytest.mark.parametrize("entrypoint", ("schedule", "enable"))
def test_preexisting_suspension_does_not_reannounce_from_public_callers(
    entrypoint: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        (
            repository,
            service,
            capabilities,
            _sibling_case_id,
            decision_document,
        ) = _scheduled_suspension_fixture(Path(directory))
        connection, library = service._libraries[0]
        capabilities.category = "field_unavailable"
        if entrypoint == "schedule":
            repository.grant_field_effect_consent_matrix(
                connection, ((library, ("series",)),), 1
            )
        with patch.object(
            repository,
            "pending_field_effect_decision_documents",
            return_value=(decision_document,),
        ):
            if entrypoint == "schedule":
                service.schedule()
            else:
                with pytest.raises(Exception, match="capability"):
                    service.enable(((library, ("series",)),))
        announcements: list[tuple[str, str | None]] = []
        service.attach_updates(
            lambda topic, subject: announcements.append((topic, subject))
        )
        with patch.object(
            repository,
            "pending_field_effect_decision_documents",
            return_value=(decision_document,),
        ):
            if entrypoint == "schedule":
                service.schedule()
            else:
                with pytest.raises(Exception, match="capability"):
                    service.enable(((library, ("series",)),))
        assert announcements == []
        assert (
            BrowseReviewLibrary(repository).attention(
                search=None, limit=10, cursor=None
            )["total"]
            == 0
        )
        repository.close()


def test_scheduler_success_keeps_a_different_unresolved_sibling_attention_only() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        (
            repository,
            service,
            _capabilities,
            sibling_case_id,
            decision_document,
        ) = _scheduled_suspension_fixture(Path(directory))
        connection, library = service._libraries[0]
        repository.grant_field_effect_consent_matrix(
            connection, ((library, ("series",)),), 1
        )
        browse = BrowseReviewLibrary(repository)
        before = browse.library(lens="all", search=None, limit=10, cursor=None)["items"]
        sibling_before = next(
            row for row in before if row["review_case_id"] == sibling_case_id
        )
        announcements: list[tuple[str, str | None]] = []
        service.attach_updates(
            lambda topic, subject: announcements.append((topic, subject))
        )
        with patch.object(
            repository,
            "pending_field_effect_decision_documents",
            return_value=(decision_document,),
        ):
            assert service.schedule() == 1
        after = browse.library(lens="all", search=None, limit=10, cursor=None)["items"]
        sibling_after = next(
            row for row in after if row["review_case_id"] == sibling_case_id
        )
        assert sibling_after["attention_count"] == sibling_before["attention_count"]
        assert sibling_after["attention_state"] == sibling_before["attention_state"]
        assert sibling_case_id in {
            row["review_case_id"]
            for row in browse.library(
                lens="needs_attention", search=None, limit=10, cursor=None
            )["items"]
        }
        assert sibling_case_id in {
            row["review_case_id"]
            for row in browse.attention(search=None, limit=10, cursor=None)["items"]
        }
        assert announcements == [("activity", None)]
        repository.close()


def test_a_recorded_owner_decision_is_blocked_only_once_its_effect_advances() -> None:
    """The override boundary reads the owner's decisions, not only automatic ones.

    `record_human_decision` refuses a replacement exactly when an effect for the
    previous decision has left the reversible states. This projection is what
    tells the surface the same thing before the owner tries.
    """
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "override.sqlite3")
        report = _bibliographic_report(
            current_title="Mistborn Secret History",
            providers=(
                _bibliographic_source(
                    "catalogue", "edition-1", titles=(), series="Mistborn"
                ),
            ),
        )
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        review_case_id = json.loads(repository.admit_review_case(proposal))[
            "review_case_id"
        ]
        cases = InspectCurrentReviewCase(repository)

        def series_field() -> dict[str, object]:
            return next(
                cast("dict[str, object]", item)
                for item in cast("list[object]", cases.run(review_case_id)["fields"])
                if cast("dict[str, object]", item)["destination"] == "series"
            )

        field = series_field()
        selected = cast("list[object]", field["proposed_values"])[0]
        decision = RecordHumanDecision(repository, clock=lambda: 10).run(
            review_case_id,
            proposal_id=str(field["proposal_id"]),
            target_fingerprint=str(field["target_fingerprint"]),
            destination="series",
            actor="owner",
            outcome="accept",
            selected_value=cast("dict[str, object]", selected),
            idempotency_key="override-boundary-0001",
        )
        recorded = series_field()
        assert recorded["classification"] == "human_recorded"
        assert recorded["editable"] is True

        consent = repository.grant_field_effect_consents(
            "primary", ("ebooks",), ("series",), 11
        )[0]
        plan = FieldEffectPlan.create(
            connection=report.target.connection,
            library=report.target.library,
            work_id=report.target.item,
            destination="series",
            pre_value=None,
            desired_value=cast("dict[str, object]", selected)["value"],
            target_fingerprint=str(field["target_fingerprint"]),
            target_version="version-before-write",
            dependency_digests=("d" * 64,),
            authority=EffectAuthority(
                str(decision["decision_id"]),
                review_case_id,
                str(field["target_fingerprint"]),
                "persisted_review_case",
                "human",
                "candidate",
                None,
                None,
            ),
            consent=consent,
            capability_digest="c" * 64,
            created_at=12,
        )
        repository.record_field_effect(FieldEffectOperation(plan, updated_at=12))
        # `planned` is still reversible, so the owner may still change their mind.
        assert series_field()["editable"] is True

        repository.claim_field_effect(plan.plan_id, "planned", "a" * 64, 13)
        assert repository.read_current_review_case(
            review_case_id, None
        ).override_blocked_destinations == ("series",)
        advanced = series_field()
        assert advanced["classification"] == "human_recorded"
        assert advanced["editable"] is False
        repository.close()


@pytest.mark.parametrize("component", ("subtitle", "series"))
def test_retained_component_ask_agrees_across_detail_library_and_attention(
    tmp_path, component
):
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from tests.support.proposal_evidence import retained_decoration_report

    report = retained_decoration_report(component)
    destinations = {
        name: "unsupported" if value == "unsupported" else "ask"
        for name, value in MetadataPolicyProfile.builtin().destinations
    }
    profile = MetadataPolicyProfile.create(
        "Retained Ask", dict(MetadataPolicyProfile.builtin().options), destinations
    )
    resolved = resolve_policy(profile, report.target.library)
    repository = open_repository(tmp_path / "review.sqlite")
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    assert len(proposal.bibliographic_combinations) == 1
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    browser = BrowseReviewLibrary(repository)
    for recorded in (False, True):
        if recorded:
            repository.persist_shadow_outcome(case_id, 3)
        detail = InspectCurrentReviewCase(repository).run(case_id)
        affected = {
            field["destination"]: field
            for field in detail["fields"]
            if field["destination"] in ("title", component)
        }
        assert all(
            field["classification"] == "needs_attention" and field["editable"]
            for field in affected.values()
        )
        library = browser.library(
            lens="needs_attention", search=None, limit=10, cursor=None
        )
        attention = browser.attention(search=None, limit=10, cursor=None)
        all_works = browser.library(lens="all", search=None, limit=10, cursor=None)
        assert len(library["items"]) == 1
        assert (
            all_works["counts"]
            == library["counts"]
            == {"all": 1, "needs_attention": 1, "no_attention_needed": 0}
        )
        assert {"title", component} <= {
            item["destination"] for item in attention["items"]
        }
        assert library["items"][0]["attention_count"] == attention["total"]
        assert all_works["items"][0]["attention_count"] == attention["total"]
    field = affected[component]
    RecordHumanDecision(repository, clock=lambda: 4).run(
        case_id,
        proposal_id=field["proposal_id"],
        target_fingerprint=field["target_fingerprint"],
        destination=component,
        actor="owner",
        outcome="accept",
        selected_value=field["proposed_values"][0],
        idempotency_key="retained-component-owner-choice",
    )
    assert component not in {
        item["destination"]
        for item in browser.attention(search=None, limit=10, cursor=None)["items"]
    }
    repository.close()
    repository = open_repository(tmp_path / "review.sqlite")
    field = next(
        field
        for field in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        if field["destination"] == component
    )
    assert field["classification"] == "human_recorded"
    repository.close()


@pytest.mark.parametrize(
    ("title_layout", "subtitle", "expected"),
    (
        ("ask", "ask", True),
        ("ignore", "ask", True),
        ("ask", "ignore", True),
        ("ask", "automatic", False),
        ("automatic", "ask", False),
        ("unsupported", "unsupported", False),
        ("ignore", "ignore", False),
    ),
)
def test_retained_subtitle_attention_uses_original_mixed_policy(
    tmp_path, title_layout, subtitle, expected
):
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from tests.support.proposal_evidence import retained_decoration_report

    report = retained_decoration_report("subtitle")
    builtin = MetadataPolicyProfile.builtin()
    destinations = dict(builtin.destinations)
    destinations.update(title_layout=title_layout, subtitle=subtitle)
    profile = MetadataPolicyProfile.create(
        "Mixed retained subtitle", dict(builtin.options), destinations
    )
    resolved = resolve_policy(profile, report.target.library)
    repository = open_repository(tmp_path / "mixed.sqlite")
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        builtin.policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    if title_layout == subtitle == "ignore":
        assert case_id is None
        assert (
            BrowseReviewLibrary(repository).attention(
                search=None, limit=20, cursor=None
            )["items"]
            == []
        )
        repository.close()
        return
    snapshot = repository.read_current_review_case(case_id, None)
    assert ("subtitle" in snapshot.policy_attention_destinations) is expected
    field = next(
        field
        for field in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        if field["destination"] == "subtitle"
    )
    assert (field["classification"] == "needs_attention") is expected
    attention = BrowseReviewLibrary(repository).attention(
        search=None, limit=20, cursor=None
    )
    assert (
        "subtitle" in {item["destination"] for item in attention["items"]}
    ) is expected
    repository.close()


@pytest.mark.parametrize(
    "contributing,automatic,expected",
    ((True, True, True), (False, True, False), (True, False, False)),
)
def test_provider_withheld_reason_requires_exact_contributing_automatic_evidence(
    tmp_path, contributing, automatic, expected
):
    from tests.support.provider_recovery_rail import publish_provider_failure_case

    repository = open_repository(tmp_path / "review.sqlite")
    case_id, _, _, _ = publish_provider_failure_case(
        repository, contributing=contributing, automatic=automatic
    )
    before = tuple(repository._connection.iterdump())
    changes = repository._connection.total_changes
    detail = InspectCurrentReviewCase(repository).run(case_id)
    series = next(
        field for field in detail["fields"] if field["destination"] == "series"
    )
    assert (series["automatic_withheld_reason"] is not None) is expected
    if expected:
        assert series["needs_attention"] is True
        assert "contributing ISBN lookup" in series["automatic_withheld_reason"]
    assert tuple(repository._connection.iterdump()) == before
    assert repository._connection.total_changes == changes
    repository.close()


@pytest.mark.parametrize(
    "mode",
    (
        "admission_only",
        "absent",
        "mismatched",
        "human",
        "malformed",
        "outcome_malformed",
    ),
)
def test_provider_withheld_reason_never_invents_policy_authority(tmp_path, mode):
    from dataclasses import replace

    from mechanical_librarian.application.review_web import ReviewWebError
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )
    from tests.support.provider_recovery_rail import publish_provider_failure_case

    repository = open_repository(tmp_path / "review.sqlite")
    case_id, report, policy, proposal = publish_provider_failure_case(
        repository, project=mode != "admission_only"
    )
    if mode == "human":
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "series"
        )
        RecordHumanDecision(repository, clock=lambda: 5).run(
            review_case_id=case_id,
            proposal_id=field["proposal_id"],
            target_fingerprint=field["target_fingerprint"],
            destination="series",
            outcome="keep_current",
            selected_value=None,
            actor="owner",
            idempotency_key="a" * 32,
        )
    snapshot = repository.read_current_review_case(case_id, None)
    assert snapshot.resolved_policy_document == policy.canonical_json
    assert (
        json.loads(snapshot.case_document)["case"]["proposal_set_id"]
        == proposal.proposal_set_id
    )
    assert dict(policy.destinations)["description_fill"] == "automatic"
    from mechanical_librarian.domain.rich_metadata_preview import (
        compile_rich_metadata_preview,
    )

    description = next(
        f
        for f in compile_rich_metadata_preview(policy, report, proposal).fields
        if f.field == "description_fill"
    )
    assert (description.outcome, description.reason) == ("abstain", "no_candidate")
    if mode in {"absent", "mismatched", "malformed"}:
        document = (
            None
            if mode == "absent"
            else "{}"
            if mode == "malformed"
            else resolve_policy(
                MetadataPolicyProfile.builtin(), report.target.library
            ).canonical_json
        )
        snapshot = replace(snapshot, resolved_policy_document=document)
    if mode == "outcome_malformed":
        snapshot = replace(snapshot, final_outcome_document="{}")
    # A snapshot collaborator isolates absent/unmatched authority from production
    # policy persistence, whose validator correctly refuses corrupt later state.
    store = Mock()
    store.read_current_review_case.return_value = snapshot
    if mode in {"malformed", "mismatched", "outcome_malformed"}:
        with pytest.raises(ReviewWebError, match="unavailable"):
            InspectCurrentReviewCase(store).run(case_id)
    else:
        detail = InspectCurrentReviewCase(store).run(case_id)
        reasons = {
            f["destination"]: f["automatic_withheld_reason"]
            for f in detail["fields"]
            if f["automatic_withheld_reason"] is not None
        }
        if mode == "absent":
            assert reasons == {}
        elif mode == "admission_only":
            assert (
                reasons["series"]
                == "No recorded automatic evaluation is available for this field."
            )
            assert all(value == reasons["series"] for value in reasons.values())
        else:
            assert "series" not in reasons
            assert (
                reasons["description"]
                == "No eligible value was found in the retained evidence."
            )
        assert all(isinstance(reason, str) and reason for reason in reasons.values())
        if mode == "human":
            assert (
                reasons["title"]
                == "The recorded evaluation did not select an automatic action; no more specific reason was recorded."
            )
    repository.close()


def test_provider_withheld_read_preserves_settled_database_bytes(tmp_path):
    from tests.support.provider_recovery_rail import publish_provider_failure_case

    database = tmp_path / "review.sqlite"
    repository = open_repository(database)
    case_id, _, _, _ = publish_provider_failure_case(repository)
    repository._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = database.read_bytes()
    InspectCurrentReviewCase(repository).run(case_id)
    assert database.read_bytes() == before
    repository.close()


def test_provider_policy_and_report_replacement_stay_inside_current_snapshot(tmp_path):
    from mechanical_librarian.application.review_web import ReviewWebError
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )
    from tests.support.provider_recovery_rail import (
        provider_failure_report,
        publish_provider_failure_case,
    )

    database = tmp_path / "review.sqlite"
    reader = open_repository(database)
    case_id, original_report, policy, _ = publish_provider_failure_case(reader)
    profile = MetadataPolicyProfile.create(
        "Successor policy", dict(policy.options), dict(policy.destinations)
    )
    next_policy = resolve_policy(profile, policy.library)
    reader.create_metadata_policy_draft(profile.canonical_json)
    writer_path = database if reader._binding is None else reader._binding.alias_path
    inserted = threading.Event()
    finished = threading.Event()
    errors = []
    successor_ids = []

    def publish_successor():
        writer = None
        try:
            connection = sqlite3.connect(writer_path, isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            writer = SQLiteMetadataReportRepository(connection)
            connection.set_trace_callback(
                lambda statement: (
                    inserted.set()
                    if statement.startswith("INSERT INTO metadata_reports")
                    else None
                )
            )
            connection.set_trace_callback(
                lambda statement: (
                    inserted.set()
                    if statement.startswith("UPDATE metadata_policy_state")
                    else None
                )
            )
            writer.activate_metadata_policy(
                profile.policy_id,
                policy.profile_id,
                (
                    (
                        policy.library,
                        next_policy.resolved_id,
                        next_policy.canonical_json,
                    ),
                ),
                4,
                expected_active_generation=2,
            )
            report = provider_failure_report(contributing=False)
            writer.publish(
                report, lambda: True, expected_current=original_report.report_id
            )
            proposal = evaluate_report(
                report, next_policy.resolved_id, dict(next_policy.destinations)
            )
            writer.publish_proposal(proposal)
            successor_ids.append(
                json.loads(writer.admit_review_case(proposal))["review_case_id"]
            )
            writer.persist_shadow_outcome(successor_ids[0], 4)
        except BaseException as error:
            errors.append(error)
        finally:
            if writer is not None:
                writer.close()
            finished.set()

    original = reader.get_review_case
    started = False
    writer_thread = threading.Thread(target=publish_successor, daemon=True)

    def interleave(selected_id):
        nonlocal started
        value = original(selected_id)
        if not started:
            started = True
            writer_thread.start()
            assert inserted.wait(5)
        return value

    try:
        with patch.object(reader, "get_review_case", side_effect=interleave):
            detail = InspectCurrentReviewCase(reader).run(case_id)
        reasons = {
            f["destination"]: f["automatic_withheld_reason"]
            for f in detail["fields"]
            if f["automatic_withheld_reason"] is not None
        }
        assert (
            reasons["series"]
            == "A contributing ISBN lookup did not supply usable evidence."
        )
        assert (
            reasons["description"]
            == "No eligible value was found in the retained evidence."
        )
        assert all(isinstance(reason, str) and reason for reason in reasons.values())
        writer_thread.join(timeout=5)
        assert not writer_thread.is_alive()
        assert errors == []
        assert len(successor_ids) == 1
        with pytest.raises(ReviewWebError, match="stale"):
            InspectCurrentReviewCase(reader).run(case_id)
        current = BrowseReviewLibrary(reader).library(
            lens="all", search=None, limit=10, cursor=None
        )
        assert len(current["items"]) == 1
        assert current["items"][0]["review_case_id"] == successor_ids[0]
        reports = reader.read_review_library(None, 10, 0, None).rows
        assert (
            MetadataReport.from_json(reports[0][1]).report_id
            == provider_failure_report(contributing=False).report_id
        )
        after = InspectCurrentReviewCase(reader).run(successor_ids[0])
        reasons = {
            f["destination"]: f["automatic_withheld_reason"]
            for f in after["fields"]
            if f["automatic_withheld_reason"] is not None
        }
        assert "series" not in reasons
        assert (
            reasons["description"]
            == "No eligible value was found in the retained evidence."
        )
        assert all(isinstance(reason, str) and reason for reason in reasons.values())
    finally:
        if started:
            writer_thread.join(timeout=5)
        reader.close()


@pytest.mark.parametrize(
    "status,reason",
    (
        (ProviderStatus.CANDIDATES, "Returned metadata candidates for comparison."),
        (ProviderStatus.UNAVAILABLE, "Could not be reached."),
        (ProviderStatus.RATE_LIMITED, "Limited the request rate."),
    ),
)
def test_provider_snapshot_copy_does_not_invent_match_or_retry(status, reason):
    from dataclasses import replace

    from tests.support.provider_recovery_rail import provider_failure_report

    base = provider_failure_report()
    provider = base.providers[0]
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(
            replace(
                provider,
                status=status,
                lanes=(),
                candidates=provider.candidates
                if status is ProviderStatus.CANDIDATES
                else (),
            ),
        ),
    )
    outcomes = _provider_outcomes(provider_lane_facts(report))
    assert outcomes[0]["status"] == status.value
    assert outcomes[0]["reason"] == reason


def test_existing_automatic_outcome_never_gets_provider_withheld_claim(tmp_path):
    from tests.support.provider_recovery_rail import publish_provider_failure_case

    repository = open_repository(tmp_path / "review.sqlite")
    case_id, _, _, _ = publish_provider_failure_case(
        repository, contributing=False, project=False
    )
    repository.activate_qualified_automatic(3)
    repository.persist_shadow_outcome(case_id, 4)
    detail = InspectCurrentReviewCase(repository).run(case_id)
    series = next(f for f in detail["fields"] if f["destination"] == "series")
    assert series["automatic_decision"] is not None
    assert series["automatic_withheld_reason"] is None
    repository.close()


def test_non_provider_abstention_keeps_review_without_provider_claim(tmp_path):
    from mechanical_librarian.domain.rich_metadata_preview import (
        compile_rich_metadata_preview,
    )
    from tests.support.provider_recovery_rail import (
        _automatic_series_report,
        _series_claims,
        _series_provider,
        publish_provider_failure_case,
    )

    repository = open_repository(tmp_path / "review.sqlite")
    _, original, policy, _ = publish_provider_failure_case(repository)
    report = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            _series_claims("search", "edition-2", sequence="3"),
        ),
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    preview = next(
        field
        for field in compile_rich_metadata_preview(policy, report, proposal).fields
        if field.field == "series"
    )
    assert (preview.outcome, preview.reason) == (
        "abstain",
        "series_conflicting_membership",
    )
    repository.publish(report, lambda: True, expected_current=original.report_id)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    repository.persist_shadow_outcome(case_id, 4)
    field = next(
        field
        for field in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        if field["destination"] == "series"
    )
    assert field["needs_attention"] is True
    assert (
        field["automatic_withheld_reason"]
        == "The retained evidence does not resolve series membership for automatic handling."
    )
    repository.close()


@pytest.mark.parametrize("destination", ("ask", "ignore", "automatic"))
def test_generated_author_policy_routes_owner_attention_without_automatic_leak(
    tmp_path, destination
):
    from tests.support.author_evidence import author_report, publish_author_case

    repository = open_repository(tmp_path / "authors.sqlite")
    try:
        case_id, _ = publish_author_case(
            repository, author_report(tmp_path / "media"), destination
        )
        inspector = InspectCurrentReviewCase(repository)
        detail = inspector.run(case_id)
        field = next(
            item for item in detail["fields"] if item["destination"] == "authors"
        )
        browser = BrowseReviewLibrary(repository)
        attention = browser.attention(search=None, limit=10, cursor=None)
        library = browser.library(lens="all", search=None, limit=10, cursor=None)
        assert library["items"][0]["attention_count"] == attention["total"]
        assert ("authors" in {item["destination"] for item in attention["items"]}) == (
            destination == "ask"
        )
        assert (field["automatic_decision"] is not None) == (destination == "automatic")
        assert field["needs_attention"] == (destination == "ask")
        if destination == "ignore":
            assert field["decision_actions"] == []
            assert field["proposed_values"] == []
            assert field["editable"] is False
        elif destination == "ask":
            assert field["editable"] is True
            assert field["decision_actions"] == ["keep_current", "accept"]
            RecordHumanDecision(repository, clock=lambda: 5).run(
                case_id,
                proposal_id=field["proposal_id"],
                target_fingerprint=field["target_fingerprint"],
                destination="authors",
                actor="owner",
                outcome="accept",
                selected_value={"kind": "list", "value": ["Ada Example"]},
                idempotency_key="authors-ask-owner-final-0001",
            )
            settled = next(
                item
                for item in inspector.run(case_id)["fields"]
                if item["destination"] == "authors"
            )
            assert settled["human_decision"]["outcome"] == "accept"
            assert settled["automatic_decision"] is None
            assert settled["needs_attention"] is False
            after = browser.attention(search=None, limit=10, cursor=None)
            assert "authors" not in {item["destination"] for item in after["items"]}
            assert type(attention["total"]) is int
            assert after["total"] == attention["total"] - 1
    finally:
        repository.close()


@pytest.mark.parametrize("suspension", ("pause", "category"))
def test_recorded_cause_and_current_suspension_share_one_snapshot(tmp_path, suspension):
    from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
    from tests.support.provider_recovery_rail import publish_provider_failure_case

    reader = open_repository(tmp_path / "snapshot.sqlite")
    case_id, _, _, _ = publish_provider_failure_case(reader)
    before = reader.read_current_review_case(case_id, None).final_outcome_document
    writer_path = (
        reader._binding.alias_path if reader._binding else tmp_path / "snapshot.sqlite"
    )
    attempted = threading.Event()
    errors = []

    def suspend():
        connection = sqlite3.connect(writer_path, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.set_trace_callback(
            lambda sql: (
                attempted.set()
                if sql.startswith("UPDATE automatic_safety_state")
                else None
            )
        )
        writer = SQLiteMetadataReportRepository(connection)
        try:
            if suspension == "pause":
                writer.pause_automatic_safety(True, 5)
            else:
                writer._decisions._set_automatic_safety_state(
                    AutomaticSafetyState(
                        False, None, (("series", "authority_binding"),)
                    ),
                    5,
                )
        except BaseException as error:
            errors.append(error)
        finally:
            writer.close()
            attempted.set()

    original = reader.get_review_case
    thread = threading.Thread(target=suspend)
    started = False

    def interleave(identity):
        nonlocal started
        document = original(identity)
        if not started:
            started = True
            thread.start()
            assert attempted.wait(5)
        return document

    try:
        with patch.object(reader, "get_review_case", side_effect=interleave):
            detail = InspectCurrentReviewCase(reader).run(case_id)
        thread.join(5)
        assert not thread.is_alive()
        assert errors == []
        series = next(
            field for field in detail["fields"] if field["destination"] == "series"
        )
        assert (
            series["automatic_withheld_reason"]
            == "A contributing ISBN lookup did not supply usable evidence."
        )
        successor = InspectCurrentReviewCase(reader).run(case_id)
        series = next(
            field for field in successor["fields"] if field["destination"] == "series"
        )
        assert (
            series["automatic_withheld_reason"]
            == "Automatic handling is currently suspended. Recorded evaluation: A contributing ISBN lookup did not supply usable evidence."
        )
        assert (
            reader.read_current_review_case(case_id, None).final_outcome_document
            == before
        )
    finally:
        if thread.ident is not None:
            thread.join(5)
        reader.close()


def test_terminal_catalogue_identity_uses_current_full_key_before_page_and_count() -> (
    None
):
    from dataclasses import replace

    from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
    from mechanical_librarian.application.catalogue import CatalogueEntry

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        catalogue = open_catalogue(root / "catalogue.sqlite", create=True)
        entries = tuple(
            CatalogueEntry(
                "primary",
                "fiction",
                "lib-1",
                f"item-{index}",
                1,
                "Shared 100%_ title",
                (f"Author {index}",),
                True,
                False,
                "singular-supported",
                f"{index + 1:064x}",
                "/synthetic.epub",
                "epub",
                "/media",
            )
            for index in range(3)
        )
        catalogue.acquire_refresh()
        generation = catalogue.publish("a" * 64, entries)
        repository = open_repository(root / "review.sqlite")
        repository.bind_library_catalogue(root / "catalogue.sqlite", "a" * 64)
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, generation, "a" * 64, entries, 12
        )
        tick = 13
        while action := repository.claim_review_pipeline_action(tick):
            repository.fail_review_pipeline_action(
                action, "unreadable_media", False, tick + 1, "track_1"
            )
            tick += 2
        browser = BrowseReviewLibrary(repository)
        first = browser.library(lens="all", search="100%_", limit=2, cursor=None)
        assert first["counts"] == {
            "all": 3,
            "needs_attention": 0,
            "no_attention_needed": 3,
        }
        items = cast(list[dict[str, object]], first["items"])
        assert [item["title"] for item in items] == ["Shared 100%_ title"] * 2
        assert [item["authors"] for item in items] == [["Author 0"], ["Author 1"]]
        assert all(
            item["media_kind"] == "epub" and item["detail_url"] is None
            for item in items
        )
        assert all(
            len(row) == 8
            for row in repository.read_review_library(None, 10, 0, None).rows
        )
        second = browser.library(
            lens="all", search="100%_", limit=2, cursor=cast(str, first["next_cursor"])
        )
        assert len(cast(list[object], second["items"])) == 1
        assert (
            browser.library(lens="all", search="100XX", limit=2, cursor=None)["items"]
            == []
        )
        assert (
            len(
                cast(
                    list[object],
                    browser.library(
                        lens="all", search="Author 2", limit=2, cursor=None
                    )["items"],
                )
            )
            == 1
        )
        assert repository.read_review_attention(None, 10, 0).total == 0
        from mechanical_librarian.application.metadata_report import MetadataReportError

        with (
            patch.object(
                repository,
                "read_current_library_page",
                side_effect=sqlite3.OperationalError("injected combined page failure"),
            ) as failed_page,
            pytest.raises(MetadataReportError, match="review snapshot is unavailable"),
        ):
            repository.read_review_library(None, 10, 0, None)
        failed_page.assert_called_once()
        assert not repository._connection.in_transaction
        assert "retained_catalogue" not in {
            row[1] for row in repository._connection.execute("PRAGMA database_list")
        }
        assert repository.read_review_library(None, 10, 0, None).total == 3
        # Refresh after the combined query must not split this request's snapshot.
        original_list = repository.read_current_library_page
        refreshed = tuple(replace(entry, title="Refreshed") for entry in entries)

        def publish_after_page(
            search: str | None,
            limit: int,
            offset: int,
            attention: bool | None,
            library_labels=(),
        ) -> ReviewLibraryPage:
            rows = original_list(search, limit, offset, attention, library_labels)
            catalogue.acquire_refresh()
            catalogue.publish("a" * 64, refreshed)
            return rows

        with patch.object(
            repository,
            "read_current_library_page",
            side_effect=publish_after_page,
        ) as interleaved:
            snapshot = repository.read_review_library("100%_", 10, 0, None)
        interleaved.assert_called_once()
        assert len(snapshot.rows) == snapshot.total == 3
        assert repository.read_review_library("100%_", 10, 0, None).total == 0
        assert repository.read_review_library("Refreshed", 10, 0, None).total == 3
        # Another library's key and a stale configuration must never lend their identity.
        catalogue.acquire_refresh()
        catalogue.publish(
            "a" * 64, tuple(replace(entry, library_id="other") for entry in refreshed)
        )
        assert repository.read_review_library("Refreshed", 10, 0, None).total == 0
        assert (
            repository.read_review_library("Preparation unavailable", 10, 0, None).total
            == 3
        )
        catalogue.acquire_refresh()
        catalogue.publish("b" * 64, refreshed)
        assert repository.read_review_library("Refreshed", 10, 0, None).total == 0
        assert not repository._connection.in_transaction
        assert "retained_catalogue" not in {
            row[1] for row in repository._connection.execute("PRAGMA database_list")
        }
        repository.close()
        catalogue.close()


@pytest.mark.parametrize("file_backed", (False, True))
def test_active_labels_filter_current_and_terminal_before_count_and_page(
    tmp_path, file_backed
):
    from mechanical_librarian.adapters.persistence.review_sessions import (
        SQLiteReviewSessionRepository,
    )
    from mechanical_librarian.application.catalogue import CatalogueEntry
    from mechanical_librarian.application.owner_settings import (
        FileOwnerSettingsRepository,
        OwnerSettingsOperations,
    )
    from mechanical_librarian.application.review_application import (
        compose_review_application,
    )
    from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument

    repository = open_repository(tmp_path / "review.sqlite")
    report = _report()
    library, connection = report.target.library, report.target.connection
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    repository.request_review_scan(connection, library, "library-id", "f" * 64, 10)
    action = repository.claim_review_pipeline_action(11)
    entry = CatalogueEntry(
        connection,
        library,
        "library-id",
        "terminal-only",
        1,
        "Terminal",
        (),
        True,
        False,
        "singular-supported",
        "a" * 64,
        "/synthetic.epub",
        "epub",
        "/media",
    )
    repository.complete_review_discovery(action, "generation", "b" * 64, (entry,), 12)
    action = repository.claim_review_pipeline_action(13)
    repository.fail_review_pipeline_action(action, "unreadable_media", False, 14)
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v4",
            "revision": 1,
            "connections": [
                {"name": connection, "endpoint": "https://example.invalid"}
            ],
            "libraries": [
                {
                    "name": library,
                    "connection": connection,
                    "library_id": "library-id",
                    "media_kind": "ebook",
                    "display_name": "Stale Store Label",
                }
            ],
        }
    )
    repository._connection.execute(
        "INSERT INTO owner_settings VALUES(1,?,?)",
        (
            document.revision,
            json.dumps(document.model_dump(), separators=(",", ":"), sort_keys=True),
        ),
    )
    repository._connection.commit()
    active = document.model_copy(
        update={
            "libraries": list(
                item.model_copy(update={"display_name": "Active 100%_ Room"})
                for item in document.libraries
            )
        }
    )
    settings = (
        FileOwnerSettingsRepository(active)
        if file_backed
        else repository.owner_settings_store()
    )
    if not file_backed:
        repository._connection.execute(
            "UPDATE owner_settings SET document=?",
            (json.dumps(active.model_dump(), separators=(",", ":"), sort_keys=True),),
        )
        repository._connection.commit()
    sessions = SQLiteReviewSessionRepository.open_ephemeral()
    application = compose_review_application(
        state_owner=repository,
        session_store=sessions,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda _: True,
        session_digest=lambda token: "a" * 64,
        clock=lambda: 20,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        settings=OwnerSettingsOperations(settings),
    )
    try:
        with patch.object(settings, "read", wraps=settings.read) as reads:
            first = application.library(
                lens="all", search="100%_", limit=1, cursor=None
            ).to_data()
            assert reads.call_count == 1
        assert first["counts"]["all"] == 2
        assert first["items"][0]["library_display_name"] == "Active 100%_ Room"
        second = application.library(
            lens="all", search="100%_", limit=1, cursor=first["next_cursor"]
        ).to_data()
        assert second["counts"]["all"] == 2 and len(second["items"]) == 1
        assert second["items"][0]["library_display_name"] == "Active 100%_ Room"
        assert {
            item["detail_url"] is None
            for item in cast(list[dict[str, object]], first["items"])
            + cast(list[dict[str, object]], second["items"])
        } == {False, True}
        for query in ("Stale Store Label", "100XX"):
            assert (
                application.library(
                    lens="all", search=query, limit=10, cursor=None
                ).to_data()["counts"]["all"]
                == 0
            )
        assert (
            repository.read_review_library("Stale Store Label", 10, 0, None).total == 0
        )
        assert (
            repository.read_review_library(
                "Active", 10, 0, None, (("other", library, "Active"),)
            ).total
            == 0
        )
        with patch.object(settings, "read", wraps=settings.read) as reads:
            attention = application.attention(
                search="100%_", limit=1, cursor=None
            ).to_data()
            assert reads.call_count == 1
        assert cast(int, attention["total"]) > 0 and len(attention["items"]) == 1
        assert attention["items"][0]["library_display_name"] == "Active 100%_ Room"
        if attention["next_cursor"] is not None:
            following = application.attention(
                search="100%_", limit=1, cursor=attention["next_cursor"]
            ).to_data()
            assert following["total"] == attention["total"]
            assert following["items"] != attention["items"]
        assert (
            application.attention(
                search="Stale Store Label", limit=10, cursor=None
            ).to_data()["total"]
            == 0
        )
        assert (
            repository.read_review_attention(
                "Active", 10, 0, (("other", library, "Active"),)
            ).total
            == 0
        )
        before = application.library(
            lens="all", search=None, limit=1, cursor=None
        ).to_data()
        renamed_document = active.model_copy(
            update={
                "libraries": list(
                    item.model_copy(update={"display_name": "Renamed Room"})
                    for item in active.libraries
                )
            }
        )
        if file_backed:
            assert isinstance(settings, FileOwnerSettingsRepository)
            settings._document = renamed_document
        else:
            repository._connection.execute(
                "UPDATE owner_settings SET document=?",
                (
                    json.dumps(
                        renamed_document.model_dump(),
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            repository._connection.commit()
        after = application.library(
            lens="all", search=None, limit=1, cursor=None
        ).to_data()
        assert before["items"][0].pop("library_display_name") == "Active 100%_ Room"
        assert after["items"][0].pop("library_display_name") == "Renamed Room"
        assert before == after
        assert (
            application.library(
                lens="all", search="Active", limit=10, cursor=None
            ).to_data()["counts"]["all"]
            == 0
        )
        assert (
            application.library(
                lens="all", search="Renamed Room", limit=10, cursor=None
            ).to_data()["counts"]["all"]
            == 2
        )
    finally:
        sessions.close()
        repository.close()


@pytest.mark.parametrize("surface", ("library", "attention"))
@pytest.mark.parametrize(
    "corruption",
    (
        "marker_type",
        "marker_value",
        "negative_total",
        "boolean_total",
        "present_null",
        "absent_document",
    ),
)
def test_combined_browse_validates_empty_marker_and_totals(
    tmp_path, surface, corruption
):
    from mechanical_librarian.application.metadata_report import MetadataReportError

    repository = open_repository(tmp_path / "review.sqlite")
    marker = 7 if surface == "library" else 8
    reached = False

    def corrupt_row(cursor, row):
        nonlocal reached
        if len(cursor.description) != 10:
            return row
        reached = True
        values = list(row)
        if corruption == "marker_type":
            values[marker] = "0"
        elif corruption == "marker_value":
            values[marker] = 2
        elif corruption == "negative_total":
            values[marker + 1] = -1
        elif corruption == "boolean_total":
            values[marker + 1] = False
        elif corruption == "present_null":
            values[marker] = 1
        else:
            values[1] = "{}"
        return tuple(values)

    repository._connection.row_factory = corrupt_row
    try:
        with pytest.raises(
            MetadataReportError, match=f"review {surface} is unavailable"
        ):
            if surface == "library":
                repository.read_current_library_page(None, 10, 0, None)
            else:
                repository.read_current_attention_page(None, 10, 0)
        assert reached
    finally:
        repository.close()
