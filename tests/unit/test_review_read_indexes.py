from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_REVIEW_CASE_COUNT_SQL,
    _LATEST_PREPARATION_WORK_SQL,
    _SHADOW_OUTCOME_HISTORY_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _NO_UPDATE_SQL,
    _create_schema,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    ReviewWebError,
)
from mechanical_librarian.domain.automatic_decision import (
    F20_QUALIFICATION_CANDIDATE_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import OPTION_CATALOGUE
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    MetadataReport,
    ReportValueError,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase, ReviewTransition
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.support.proposal_evidence import (
    _claim as _proposal_claim,
)
from tests.support.proposal_evidence import (
    _provider as _proposal_provider,
)
from tests.support.proposal_evidence import (
    _report as _proposal_report,
)
from tests.support.review_cases import _report

_WORKS = 2_000
_READ_VM_STEP_BUDGET = 4_000_000
_READ_WALL_BUDGET_SECONDS = 1.0
_Result = TypeVar("_Result")


def _representative_owner_action_count(works: int) -> int:
    return len(range(0, works, 2))


def _representative_human_decision_count(works: int) -> int:
    return max(1, min(10, works // 8))


def _field_fact_digest(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _representative_database(path: Path, works: int = _WORKS) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.create_function(
        "ml_casefold",
        1,
        lambda value: value.casefold() if type(value) is str else value,
    )
    connection.create_function(
        "ml_cover_consideration_current", 2, lambda _document, _replay: 0
    )
    _create_schema(connection)
    reports = []
    proposals = []
    cases = []
    domain_cases = []
    packages = []
    report_lineages = []
    current_reports = []
    transitions = []
    case_lineages = []
    case_ids = []
    work = []
    scan_id = "review_scan_" + "f" * 64
    actionable_source = _report()
    for index in range(works):
        identity = f"{index:064x}"
        item_id = f"item-{index:04d}"
        title = (
            f"Representative title {index:04d}"
            if index % 2 == 0
            else "Exact evaluator candidate"
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "books",
                item_id,
                default_target_fields(title),
            ),
            file=FileFacts(0, identity, f"{works + index:064x}"),
            local_claims=actionable_source.local_claims,
            local_reasons=actionable_source.local_reasons,
            providers=actionable_source.providers,
        )
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        domain_cases.append(case)
        report_document = report.to_json()
        report_id = report.report_id
        proposal_id = proposal.proposal_set_id
        case_id = case.review_case_id
        case_ids.append(case_id)
        reports.append(
            (
                report_id,
                "primary",
                "books",
                item_id,
                report.file.identity,
                report.file.content_digest,
                report.schema_version,
                hashlib.sha256(report_document.encode()).hexdigest(),
                report_document,
            )
        )
        proposals.append(
            (proposal_id, report_id, proposal.policy_version, proposal.to_json())
        )
        cases.append((case_id, proposal_id, report_id, case.to_json()))
        transition_id = ReviewTransition(
            "primary",
            "books",
            item_id,
            1,
            proposal_id,
            report_id,
            "admitted",
            None,
            case_id,
            "actionable",
        ).transition_id
        report_lineages.append(("primary", "books", item_id, report_id))
        current_reports.append(
            ("primary", "books", item_id, report.file.identity, report_id)
        )
        transitions.append(
            (
                transition_id,
                "primary",
                "books",
                item_id,
                1,
                None,
                case_id,
                proposal_id,
                report_id,
            )
        )
        case_lineages.append(
            (
                "primary",
                "books",
                item_id,
                proposal_id,
                report_id,
                case_id,
                transition_id,
                1,
                "admitted",
            )
        )
        packages.append(
            (
                "primary",
                "books",
                item_id,
                proposal_id,
                report_id,
                case_id,
                1,
                "admitted",
            )
        )
        failed = index % 100 == 1
        work.append(
            (
                "review_work_" + identity,
                scan_id,
                index,
                "primary",
                "books",
                "library-id",
                item_id,
                report.file.identity,
                1,
                report.file.content_digest,
                "c" * 64,
                1,
                "d" * 64,
                "provider" if failed else "complete",
                "failed" if failed else "complete",
                "provider" if failed else None,
                None if failed else proposal_id,
                None if failed else case_id,
                None if failed else "case",
                index,
            )
        )
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO review_preparation_scans(scan_id,connection_name,library_name,library_id,producer_fingerprint,state,requested_at,updated_at,catalogue_generation,catalogue_fingerprint,qualification_epoch,total) "
        "VALUES(?,?,?,?,?,'completed',0,0,1,?,0,?)",
        (scan_id, "primary", "books", "library-id", "c" * 64, "d" * 64, works),
    )
    connection.executemany(
        "INSERT INTO metadata_reports(report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document) VALUES(?,?,?,?,?,?,?,?,?)",
        reports,
    )
    connection.executemany(
        "INSERT INTO proposal_sets(proposal_set_id,report_id,policy_version,document) VALUES(?,?,?,?)",
        proposals,
    )
    connection.executemany(
        "INSERT INTO review_cases(review_case_id,proposal_set_id,report_id,document) VALUES(?,?,?,?)",
        cases,
    )
    connection.executemany(
        "INSERT INTO current_metadata_report_lineages(connection_name,library_name,item_id,report_id) VALUES(?,?,?,?)",
        report_lineages,
    )
    connection.executemany(
        "INSERT INTO current_metadata_reports(connection_name,library_name,item_id,file_identity,report_id) VALUES(?,?,?,?,?)",
        current_reports,
    )
    connection.executemany(
        "INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) "
        "VALUES(?,?,?,?,?,'review-admission-v1',?, ?,?,?,'admitted','actionable')",
        transitions,
    )
    connection.executemany(
        "INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?,?)",
        case_lineages,
    )
    connection.executemany(
        "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?)",
        packages,
    )
    connection.executemany(
        "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,library_name,library_id,item_id,file_identity,source_revision,source_fingerprint,producer_fingerprint,catalogue_generation,catalogue_fingerprint,stage,state,failure_code,proposal_set_id,review_case_id,outcome,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        work,
    )
    connection.commit()
    repository = SQLiteMetadataReportRepository(connection)
    repository.enter_shadow_preview(1)
    automatic_count = max(1, min(20, works // 4))
    human_count = _representative_human_decision_count(works)
    cover_count = max(1, min(10, works // 8))
    shadow_documents = []
    for index, case_id in enumerate(case_ids[::2]):
        shadow_documents.append(repository.persist_shadow_outcome(case_id, index + 2))

    for index, document in enumerate(shadow_documents[:automatic_count]):
        outcome_set = json.loads(document)
        field = next(
            outcome
            for outcome in outcome_set["outcomes"]
            if outcome["destination"] == "isbn"
        )
        decision = AutomaticDecision.create(
            review_case_id=outcome_set["review_case_id"],
            report_id=outcome_set["report_id"],
            proposal_set_id=outcome_set["proposal_set_id"],
            proposal_id=field["proposal_id"],
            target_fingerprint=outcome_set["target_fingerprint"],
            destination="isbn",
            outcome="keep_current",
            candidate_id=None,
            resolved_policy_id=outcome_set["resolved_policy_id"],
            policy_generation=outcome_set["policy_generation"],
            rule_id=field["rule_id"],
            policy_category="isbn_form",
            policy_option="preserve_exact",
            qualification_manifest_sha256=F20_QUALIFICATION_CANDIDATE_SHA256,
            source_outcome_id=field["outcome_id"],
            bundle_id=None,
            occurred_at=works + index,
        )
        connection.execute(
            "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,"
            "idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
            (
                decision.decision_id,
                decision.review_case_id,
                decision.destination,
                decision.idempotency_digest,
                decision.occurred_at,
                decision.to_json(),
            ),
        )
    connection.commit()

    human_start = automatic_count * 2
    human_stop = human_start + human_count * 2
    for index, case in enumerate(domain_cases[human_start:human_stop:2]):
        human_proposal = next(
            value
            for value in case.proposals
            if value["result"]["outcome"]
            in {"propose_change", "acceptable_choice", "unresolved_choice"}
            or value["manual_required"] is True
        )
        human_proposal_id = human_proposal["proposal_id"]
        human_destination = human_proposal["destination"]
        assert type(human_proposal_id) is str
        assert type(human_destination) is str
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=human_proposal_id,
                target_fingerprint=case.target_fingerprint,
                destination=human_destination,
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key=f"scale-human-{index:04d}",
                occurred_at=works * 2 + index,
            )
        )

    for index, case in enumerate(domain_cases[1 : cover_count * 2 : 2]):
        replay_digest = f"{works * 3 + index:064x}"
        consideration_id = "cover_consideration_" + f"{works * 4 + index:064x}"
        document = json.dumps(
            {
                "consideration_id": consideration_id,
                "report_id": case.report_id,
                "report_document": connection.execute(
                    "SELECT document FROM metadata_reports WHERE report_id=?",
                    (case.report_id,),
                ).fetchone()[0],
                "replay_digest": replay_digest,
                "review_case_id": case.review_case_id,
                "schema_version": "cover-consideration-v4",
                "supersedes_id": None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        repository.record_cover_consideration(
            document,
            review_case_id=case.review_case_id,
            report_id=case.report_id,
            replay_digest=replay_digest,
            supersedes_id=None,
            occurred_at=works * 3 + index,
        )

    assert connection.execute(
        "SELECT count(*) FROM automatic_decisions"
    ).fetchone() == (automatic_count,)
    assert connection.execute("SELECT count(*) FROM human_decisions").fetchone()[0] > 0
    assert (
        connection.execute("SELECT count(*) FROM human_decision_events").fetchone()[0]
        > 0
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM review_preparation_work WHERE state='failed'"
        ).fetchone()[0]
        > 0
    )
    assert (
        connection.execute("SELECT count(*) FROM cover_considerations").fetchone()[0]
        > 0
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM cover_consideration_events"
        ).fetchone()[0]
        > 0
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM review_cases case_record "
            "WHERE NOT EXISTS (SELECT 1 FROM automatic_decisions automatic "
            "WHERE automatic.review_case_id=case_record.review_case_id) "
            "AND NOT EXISTS (SELECT 1 FROM human_decisions human "
            "WHERE human.review_case_id=case_record.review_case_id)"
        ).fetchone()[0]
        > 0
    )
    return connection


def _plan(
    connection: sqlite3.Connection, sql: str, parameters: tuple[object, ...]
) -> tuple[str, ...]:
    return tuple(
        str(row[3])
        for row in connection.execute("EXPLAIN QUERY PLAN " + sql, parameters)
    )


def _call_with_step_limit(
    connection: sqlite3.Connection,
    maximum: int,
    call: Callable[[], _Result],
) -> tuple[_Result, int, float]:
    calls = 0

    def progress() -> int:
        nonlocal calls
        calls += 1
        return int(calls * 1_000 > maximum)

    connection.set_progress_handler(progress, 1_000)
    started = time.perf_counter()
    try:
        result = call()
    finally:
        elapsed = time.perf_counter() - started
        connection.set_progress_handler(None, 0)
    return result, calls * 1_000, elapsed


def _count_with_step_limit(
    connection: sqlite3.Connection, maximum: int
) -> tuple[tuple[int, int], int, float]:
    row, steps, elapsed = _call_with_step_limit(
        connection,
        maximum,
        lambda: connection.execute(
            _CURRENT_REVIEW_CASE_COUNT_SQL,
            (None, None, None, "", "", "", "", "", "[]", ""),
        ).fetchone(),
    )
    assert row is not None
    return (int(row[0]), int(row[1])), steps, elapsed


def _traced_selects(
    connection: sqlite3.Connection, call: Callable[[], object]
) -> tuple[str, ...]:
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        call()
    finally:
        connection.set_trace_callback(None)
    return tuple(
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "WITH"))
    )


def _read_each_preparation_lineage(
    connection: sqlite3.Connection,
) -> None:
    for index in range(_WORKS):
        connection.execute(
            _LATEST_PREPARATION_WORK_SQL,
            ("primary", "books", f"item-{index:04d}"),
        ).fetchone()


def _read_each_shadow_history(connection: sqlite3.Connection) -> None:
    case_ids = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT review_case_id FROM shadow_decision_outcomes"
        )
    )
    for _ in range(2):
        for case_id in case_ids:
            connection.execute(
                _SHADOW_OUTCOME_HISTORY_SQL,
                (case_id, 100),
            ).fetchall()


def test_indexes_preserve_exact_browse_search_detail_and_history_results() -> None:
    with tempfile.TemporaryDirectory() as directory:
        connection = _representative_database(
            Path(directory) / "application.sqlite3", works=24
        )
        repository = SQLiteMetadataReportRepository(connection)
        case_id = str(
            connection.execute(
                "SELECT review_case_id FROM review_cases LIMIT 1"
            ).fetchone()[0]
        )

        def results() -> tuple[object, ...]:
            return (
                repository.read_review_library(None, 7, 3, None),
                repository.read_review_library(None, 7, 3, True),
                repository.read_review_library(None, 7, 3, False),
                repository.read_review_library("Representative title 0006", 7, 0, None),
                repository.read_review_attention(None, 7, 3),
                repository.read_review_attention("Representative title 0006", 7, 0),
                repository.read_current_review_case(case_id, 100),
                repository.get_review_case_decisions(case_id),
                repository.get_review_case_history(case_id, 100),
            )

        indexed = results()
        connection.execute("DROP INDEX review_preparation_work_lineage_history")
        connection.execute("DROP INDEX shadow_decision_outcomes_case_history")
        connection.commit()
        assert results() == indexed
        repository.close()


def test_representative_attention_expectation_counts_only_remaining_owner_actions() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        connection = _representative_database(
            Path(directory) / "application.sqlite3", works=24
        )
        try:
            action_items = {
                row[0]
                for row in connection.execute(
                    "SELECT report.item_id FROM current_review_packages current "
                    "JOIN review_cases case_record "
                    "ON case_record.review_case_id=current.review_case_id "
                    "JOIN metadata_reports report ON report.report_id=case_record.report_id "
                    "JOIN review_case_field_facts fact "
                    "ON fact.review_case_id=case_record.review_case_id "
                    "WHERE fact.has_owner_decision_actions=1"
                )
            }
            recorded_items = {
                row[0]
                for row in connection.execute(
                    "SELECT report.item_id FROM human_decisions decision "
                    "JOIN review_cases case_record "
                    "ON case_record.review_case_id=decision.review_case_id "
                    "JOIN metadata_reports report ON report.report_id=case_record.report_id"
                )
            }
            assert action_items == {f"item-{index:04d}" for index in range(0, 24, 2)}
            assert recorded_items == {f"item-{index:04d}" for index in range(12, 18, 2)}
            assert _representative_owner_action_count(24) == 12
            assert _representative_human_decision_count(24) == 3
            assert _count_with_step_limit(connection, 1_000_000)[0] == (24, 9)
        finally:
            connection.close()


@pytest.mark.parametrize(
    "index_name",
    [
        "review_preparation_work_lineage_history",
        "shadow_decision_outcomes_case_history",
    ],
)
def test_exact_schema_requires_both_measured_indexes(index_name: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        repository = open_repository(database)
        repository.close()

        connection = sqlite3.connect(database)
        connection.execute(f"DROP INDEX {index_name}")
        connection.commit()
        connection.close()

        with pytest.raises(
            MetadataReportError, match="database is unavailable or incompatible"
        ):
            open_repository(database, create=False)


def test_canonical_inserts_project_exact_review_and_shadow_field_facts(
    tmp_path: Path,
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    review_expected = connection.execute(
        "SELECT count(*) FROM review_cases rc,json_each(rc.document,'$.results')"
    ).fetchone()
    shadow_expected = connection.execute(
        "SELECT count(*) FROM shadow_decision_outcomes shadow,"
        "json_each(shadow.document,'$.outcomes')"
    ).fetchone()

    assert (
        connection.execute("SELECT count(*) FROM review_case_field_facts").fetchone()
        == review_expected
    )
    assert (
        connection.execute("SELECT count(*) FROM shadow_outcome_field_facts").fetchone()
        == shadow_expected
    )
    connection.close()
    (tmp_path / "application.sqlite3").chmod(0o600)

    repository = open_repository(tmp_path / "application.sqlite3", create=False)
    repository.close()


def test_repository_browse_projection_does_not_repeat_report_or_case_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    repository = SQLiteMetadataReportRepository(connection)
    expected_library = repository.read_review_library(None, 6, 0, None)
    expected_attention = repository.read_review_attention(None, 6, 0)

    def unexpected_validation(*_args: object) -> object:
        raise AssertionError("browse repeated full canonical validation")

    for document_type in (MetadataReport, ReviewCase):
        monkeypatch.setattr(document_type, "from_json", unexpected_validation)

    assert repository.read_review_library(None, 6, 0, None) == expected_library
    assert repository.read_review_attention(None, 6, 0) == expected_attention
    repository.close()


def test_browse_projection_rejects_a_decision_document_that_disagrees_with_its_row(
    tmp_path: Path,
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    decision_id, item_id = connection.execute(
        "SELECT decision.decision_id,report.item_id "
        "FROM human_decisions decision "
        "JOIN review_cases case_record "
        "ON case_record.review_case_id=decision.review_case_id "
        "JOIN metadata_reports report ON report.report_id=case_record.report_id LIMIT 1"
    ).fetchone()
    connection.execute("DROP TRIGGER human_decisions_no_update")
    connection.execute(
        "UPDATE human_decisions SET destination='corrupt-destination' "
        "WHERE decision_id=?",
        (decision_id,),
    )
    connection.commit()
    repository = SQLiteMetadataReportRepository(connection)

    with pytest.raises(ReviewWebError, match="unavailable"):
        BrowseReviewLibrary(repository).library(
            lens="all", search=item_id, limit=50, cursor=None
        )
    repository.close()


def test_browse_projection_rejects_an_automatic_decision_bound_to_another_case(
    tmp_path: Path,
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    decision_id, document, item_id, relational_case_id = connection.execute(
        "SELECT decision.decision_id,decision.document,report.item_id "
        ",decision.review_case_id "
        "FROM automatic_decisions decision "
        "JOIN review_cases case_record "
        "ON case_record.review_case_id=decision.review_case_id "
        "JOIN metadata_reports report ON report.report_id=case_record.report_id LIMIT 1"
    ).fetchone()
    original = AutomaticDecision.from_json(document)
    foreign = AutomaticDecision.create(
        review_case_id="review_case_" + "f" * 64,
        report_id=original.report_id,
        proposal_set_id=original.proposal_set_id,
        proposal_id=original.proposal_id,
        target_fingerprint=original.target_fingerprint,
        destination=original.destination,
        outcome=original.outcome,
        candidate_id=original.candidate_id,
        resolved_policy_id=original.resolved_policy_id,
        policy_generation=original.policy_generation,
        rule_id=original.rule_id,
        policy_category=original.policy_category,
        policy_option=original.policy_option,
        qualification_manifest_sha256=original.qualification_manifest_sha256,
        source_outcome_id=original.source_outcome_id,
        bundle_id=original.bundle_id,
        occurred_at=original.occurred_at,
    )
    connection.execute("DROP TRIGGER automatic_decisions_no_update")
    connection.execute(
        "UPDATE automatic_decisions SET decision_id=?,destination=?,"
        "idempotency_digest=?,occurred_at=?,document=? WHERE decision_id=?",
        (
            foreign.decision_id,
            foreign.destination,
            foreign.idempotency_digest,
            foreign.occurred_at,
            foreign.to_json(),
            decision_id,
        ),
    )
    assert relational_case_id != foreign.review_case_id
    connection.commit()
    repository = SQLiteMetadataReportRepository(connection)

    with pytest.raises(ReviewWebError, match="unavailable"):
        BrowseReviewLibrary(repository).library(
            lens="all", search=item_id, limit=50, cursor=None
        )
    repository.close()


def test_relationship_bearing_case_keeps_its_historical_human_proposal_identity(
    tmp_path: Path,
) -> None:
    claims = tuple(
        _proposal_claim(
            "catalogue",
            "edition-1",
            kind,
            value,
            scope=ClaimScope.WORK,
        )
        for kind, value in (
            (ClaimKind.TITLE, "Candidate title"),
            (ClaimKind.SUBTITLE, "A Novel"),
            (ClaimKind.SERIES, "Synthetic series"),
        )
    )
    report = _proposal_report(
        providers=(_proposal_provider("catalogue", "edition-1", claims),)
    )
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    combinations = case.relationships["bibliographic_combinations"]
    assert type(combinations) is list and combinations
    chosen = next(
        value
        for value in case.proposals
        if value["result"]["outcome"]
        in {"propose_change", "acceptable_choice", "unresolved_choice"}
    )
    repository = SQLiteMetadataReportRepository(
        _representative_database(tmp_path / "application.sqlite3", works=8)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    review_case_id = json.loads(repository.admit_review_case(proposal))[
        "review_case_id"
    ]
    repository.record_human_decision(
        HumanDecision.create(
            case,
            proposal_id=str(chosen["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=str(chosen["destination"]),
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="relationship-identity-0001",
            occurred_at=50,
        )
    )

    page = BrowseReviewLibrary(repository).library(
        lens="all", search="Current title", limit=50, cursor=None
    )
    item = next(
        value for value in page["items"] if value["review_case_id"] == review_case_id
    )
    assert item["review_case_id"] == review_case_id
    repository.close()


def test_visible_row_semantic_corruption_fails_closed_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    connection = _representative_database(database, works=8)
    report_id, document = connection.execute(
        "SELECT report_id,document FROM metadata_reports ORDER BY item_id LIMIT 1"
    ).fetchone()
    value = json.loads(document)
    value["providers"] = [
        {
            "authority": "bogus-authority",
            "lanes": [{"kind": "bogus-lane", "status": "bogus-status"}],
            "request_accounting": {"logical_requests": 1},
        }
    ]
    corrupt = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(corrupt)
    connection.execute("DROP TRIGGER metadata_reports_no_update")
    connection.execute(
        "UPDATE metadata_reports SET document=?,document_digest=? WHERE report_id=?",
        (corrupt, hashlib.sha256(corrupt.encode("utf-8")).hexdigest(), report_id),
    )
    connection.execute(_NO_UPDATE_SQL)
    connection.commit()
    connection.close()
    database.chmod(0o600)

    repository = open_repository(database, create=False)
    with pytest.raises(ReviewWebError, match="unavailable"):
        BrowseReviewLibrary(repository).library(
            lens="all",
            search="Representative title 0000",
            limit=50,
            cursor=None,
        )
    repository.close()


def test_visible_case_must_bind_the_exact_proposal_admitted_for_its_report(
    tmp_path: Path,
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    report_document, proposal_document, review_case_id = connection.execute(
        "SELECT mr.document,ps.document,rc.review_case_id FROM review_cases rc "
        "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
        "JOIN proposal_sets ps ON ps.proposal_set_id=rc.proposal_set_id "
        "ORDER BY mr.item_id LIMIT 1"
    ).fetchone()
    report = MetadataReport.from_json(report_document)
    admitted = ProposalSet.from_json(proposal_document)
    alternate = evaluate_report(
        report,
        admitted.resolved_policy_id,
        {destination: "ignore" for destination in OPTION_CATALOGUE},
    )
    assert alternate.to_json() != admitted.to_json()
    alternate_case = ReviewCase.create(alternate, report)
    assert ReviewCase.from_json(alternate_case.to_json()) == alternate_case

    connection.execute("DROP TRIGGER review_cases_no_update")
    connection.execute(
        "UPDATE review_cases SET document=? WHERE review_case_id=?",
        (alternate_case.to_json(), review_case_id),
    )
    connection.commit()
    repository = SQLiteMetadataReportRepository(connection)

    with pytest.raises(ReviewWebError, match="unavailable"):
        BrowseReviewLibrary(repository).library(
            lens="all",
            search="Representative title 0000",
            limit=50,
            cursor=None,
        )
    repository.close()


def test_post_start_work_becomes_visible_only_at_complete_pipeline_boundaries(
    tmp_path: Path,
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    repository = SQLiteMetadataReportRepository(connection)
    browse = BrowseReviewLibrary(repository)
    source = _report()
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "books",
            "item-streamed",
            default_target_fields("Streamed work"),
        ),
        file=FileFacts(0, "f" * 64, "e" * 64),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
    )

    repository.publish(report, lambda: True)
    report_page = browse.library(lens="all", search="Streamed", limit=50, cursor=None)
    assert report_page["counts"] == {
        "all": 1,
        "needs_attention": 0,
        "no_attention_needed": 1,
    }
    assert report_page["items"][0]["review_case_id"] is None

    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    case_page = browse.library(lens="all", search="Streamed", limit=50, cursor=None)
    assert case_page["counts"] == {
        "all": 1,
        "needs_attention": 1,
        "no_attention_needed": 0,
    }
    item = case_page["items"][0]
    assert item["review_case_id"] is not None
    assert item["attention_count"] == 1
    repository.close()


def test_projection_failure_rolls_back_the_canonical_insert(tmp_path: Path) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    proposal_set_id, report_id, document = connection.execute(
        "SELECT proposal_set_id,report_id,document FROM review_cases LIMIT 1"
    ).fetchone()
    value = json.loads(document)
    value["results"].append(value["results"][0])
    duplicate_destination_document = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    review_case_id = "review_case_" + "a" * 64

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO review_cases("
            "review_case_id,proposal_set_id,report_id,document) VALUES(?,?,?,?)",
            (
                review_case_id,
                proposal_set_id,
                report_id,
                duplicate_destination_document,
            ),
        )

    assert connection.execute(
        "SELECT count(*) FROM review_cases WHERE review_case_id=?", (review_case_id,)
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT count(*) FROM review_case_field_facts WHERE review_case_id=?",
        (review_case_id,),
    ).fetchone() == (0,)
    connection.close()


def test_shadow_projection_failure_rolls_back_the_canonical_insert(
    tmp_path: Path,
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    row = connection.execute(
        "SELECT review_case_id,report_id,proposal_set_id,resolved_policy_id,"
        "source_decision_set_id,evaluated_at,document "
        "FROM shadow_decision_outcomes LIMIT 1"
    ).fetchone()
    value = json.loads(row[6])
    value["outcomes"].append(value["outcomes"][0])
    duplicate_destination_document = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    outcome_set_id = "shadow_outcome_set_" + "a" * 64

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO shadow_decision_outcomes("
            "outcome_set_id,review_case_id,report_id,proposal_set_id,"
            "resolved_policy_id,source_decision_set_id,evaluated_at,document) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (outcome_set_id, *row[:6], duplicate_destination_document),
        )

    assert connection.execute(
        "SELECT count(*) FROM shadow_decision_outcomes WHERE outcome_set_id=?",
        (outcome_set_id,),
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT count(*) FROM shadow_outcome_field_facts WHERE outcome_set_id=?",
        (outcome_set_id,),
    ).fetchone() == (0,)
    connection.close()


def test_attention_uses_the_newest_shadow_disposition(tmp_path: Path) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    repository = SQLiteMetadataReportRepository(connection)
    before = repository.list_current_attention_documents(
        "Representative title 0000", 50, 0
    )
    destination = "title"
    older = next(item for item in before if item[2] == destination)
    assert older[4] == "abstention"
    row = connection.execute(
        "SELECT review_case_id,report_id,proposal_set_id,resolved_policy_id,"
        "source_decision_set_id,evaluated_at,document "
        "FROM shadow_decision_outcomes WHERE review_case_id=(SELECT review_case_id "
        "FROM current_review_packages WHERE item_id='item-0000')"
    ).fetchone()
    value = json.loads(row[6])
    outcome = next(
        item for item in value["outcomes"] if item["destination"] == destination
    )
    outcome["candidate_id"] = "alternative_" + "b" * 64
    outcome["disposition"] = "candidate"
    outcome["qualification_candidate_id"] = (
        "qualification_candidate_"
        + _field_fact_digest(
            {
                "action_id": outcome["action_id"],
                "candidate_id": outcome["candidate_id"],
                "catalogue_sha256": value["catalogue_sha256"],
                "citation_ids": outcome["citation_ids"],
                "combination_ids": outcome["combination_ids"],
                "rule_id": outcome["rule_id"],
            }
        )
    )
    outcome_identity = dict(outcome)
    del outcome_identity["outcome_id"]
    outcome["outcome_id"] = "shadow_outcome_" + _field_fact_digest(outcome_identity)
    value_identity = dict(value)
    del value_identity["outcome_set_id"]
    value["outcome_set_id"] = "shadow_outcome_set_" + _field_fact_digest(value_identity)
    newest_document = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    newest = ShadowOutcomeSet.from_json(newest_document)
    assert newest.outcome_set_id == value["outcome_set_id"]
    assert older[4] != outcome["disposition"]
    connection.execute(
        "INSERT INTO shadow_decision_outcomes("
        "outcome_set_id,review_case_id,report_id,proposal_set_id,"
        "resolved_policy_id,source_decision_set_id,evaluated_at,document) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (newest.outcome_set_id, *row[:5], row[5] + 1, newest_document),
    )
    connection.commit()

    after = repository.list_current_attention_documents(
        "Representative title 0000", 50, 0
    )
    selected = next(item for item in after if item[2] == destination)
    assert selected[4] == "candidate"
    repository.close()


@pytest.mark.parametrize(
    "corruption",
    (
        "DELETE FROM review_case_field_facts WHERE (review_case_id,destination)="
        "(SELECT review_case_id,destination FROM review_case_field_facts LIMIT 1)",
        "INSERT INTO review_case_field_facts("
        "review_case_id,destination,unresolved_choice,manual_bibliographic_attention,has_owner_decision_actions) "
        "SELECT review_case_id,'unexpected',0,0,0 "
        "FROM review_cases LIMIT 1",
    ),
)
def test_restart_rejects_projection_that_disagrees_with_canonical_state(
    tmp_path: Path, corruption: str
) -> None:
    database = tmp_path / "application.sqlite3"
    connection = _representative_database(database, works=8)
    connection.execute(corruption)
    connection.commit()
    connection.close()
    database.chmod(0o600)

    with pytest.raises(
        MetadataReportError, match="database is unavailable or incompatible"
    ):
        open_repository(database, create=False)


@pytest.mark.parametrize("surface", ("library", "attention"))
def test_combined_browse_preserves_package_and_case_report_disagreement(
    tmp_path: Path, surface: str
) -> None:
    connection = _representative_database(tmp_path / "application.sqlite3", works=8)
    original, other = connection.execute(
        "SELECT report_id FROM current_review_packages ORDER BY item_id LIMIT 2"
    ).fetchall()
    connection.execute(
        "UPDATE current_review_packages SET report_id=? WHERE report_id=?",
        (other[0], original[0]),
    )
    connection.commit()
    repository = SQLiteMetadataReportRepository(connection)
    try:
        with pytest.raises(
            MetadataReportError, match=f"review {surface} is unavailable"
        ):
            if surface == "library":
                repository.read_current_library_page(None, 50, 0, None)
            else:
                repository.read_current_attention_page(None, 50, 0)
    finally:
        repository.close()
