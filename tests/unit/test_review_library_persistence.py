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

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.review_application import (
    ReviewApplication,
    ReviewApplicationError,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
    RecordHumanDecision,
    _library_item,
    _provider_comparison_notices,
    _provider_outcomes,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    FieldEffectOperation,
    FieldEffectPlan,
)
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
    outcomes = _provider_outcomes(report)
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
    assert len(_provider_outcomes(many)) == 33


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
    assert [outcome["status"] for outcome in _provider_outcomes(report)] == [
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
        "provider": "hardcover-main",
        "reason": "Provider credentials need owner attention.",
        "recovery": "Retry review preparation after correcting the reported condition.",
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
                "review_case_id": case_id,
                "schema_version": "cover-consideration-v1",
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
        repository.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="title",
            reason="field_unavailable",
            changed_at=1,
        )
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
        repository.suspend_field_effect_capability(
            scope="connection",
            connection="primary",
            destination="title",
            reason="connection_unavailable",
            changed_at=2,
        )
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
        repository.suspend_field_effect_capability(
            scope="destination",
            connection="primary",
            destination="title",
            reason="insufficient_permission",
            changed_at=4,
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
