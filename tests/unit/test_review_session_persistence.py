"""Review session and decision persistence: snapshot isolation,
decision atomicity and idempotency, logout classification, and
exact-current schema admission across reopen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    OwnerSessionService,
    RecordHumanDecision,
    ReviewWebError,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web.app import owner_password_binding
from tests.support.review_cases import _report
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)


def test_retained_policy_snapshot_cannot_borrow_concurrent_resolution(tmp_path) -> None:
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from tests.support.proposal_evidence import retained_decoration_report

    database = tmp_path / "policy-snapshot.sqlite"
    reader = open_repository(database)
    report = retained_decoration_report("subtitle")
    builtin = MetadataPolicyProfile.builtin()
    destinations = dict(builtin.destinations)
    destinations.update(title_layout="ask", subtitle="ask")
    first = MetadataPolicyProfile.create(
        "First Ask", dict(builtin.options), destinations
    )
    first_resolution = resolve_policy(first, report.target.library)
    reader.enter_shadow_preview(1, ())
    reader.create_metadata_policy_draft(first.canonical_json)
    reader.activate_metadata_policy(
        first.policy_id,
        builtin.policy_id,
        (
            (
                report.target.library,
                first_resolution.resolved_id,
                first_resolution.canonical_json,
            ),
        ),
        2,
        expected_active_generation=1,
    )
    proposal = evaluate_report(
        report,
        first_resolution.resolved_id,
        human_review_destinations(first_resolution),
    )
    reader.publish(report, lambda: True)
    reader.publish_proposal(proposal)
    case_id = json.loads(reader.admit_review_case(proposal))["review_case_id"]
    destinations.update(title_layout="automatic", subtitle="automatic")
    second = MetadataPolicyProfile.create(
        "Second Automatic", dict(builtin.options), destinations
    )
    second_resolution = resolve_policy(second, report.target.library)
    reader.create_metadata_policy_draft(second.canonical_json)
    written = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []
    writer_path = database if reader._binding is None else reader._binding.alias_path

    def advance():
        connection = sqlite3.connect(writer_path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        writer = SQLiteMetadataReportRepository(connection)
        try:
            connection.set_trace_callback(
                lambda statement: (
                    written.set()
                    if statement.startswith("INSERT INTO metadata_policy_resolutions")
                    else None
                )
            )
            writer.activate_metadata_policy(
                second.policy_id,
                first.policy_id,
                (
                    (
                        report.target.library,
                        second_resolution.resolved_id,
                        second_resolution.canonical_json,
                    ),
                ),
                3,
                expected_active_generation=2,
            )
            successor = evaluate_report(
                report,
                second_resolution.resolved_id,
                human_review_destinations(second_resolution),
            )
            writer.publish_proposal(successor)
            writer.admit_review_case(successor)
        except BaseException as error:
            errors.append(error)
        finally:
            writer.close()
            finished.set()

    original = reader.get_review_case

    def interleave(identity):
        result = original(identity)
        threading.Thread(target=advance, daemon=True).start()
        assert written.wait(5), errors
        return result

    with patch.object(reader, "get_review_case", side_effect=interleave):
        before = reader.read_current_review_case(case_id, None)
    assert json.loads(before.case_document)["current"] is True
    assert {"title", "subtitle"} <= set(before.policy_attention_destinations)
    assert finished.wait(5)
    assert errors == []
    # The historical case retains its exact Ask resolution, while its successor
    # reads Automatic even though both generations remain in the same store.
    after = reader.read_current_review_case(case_id, None)
    assert json.loads(after.case_document)["current"] is False
    assert after.policy_attention_destinations == before.policy_attention_destinations
    successor_id = reader.current_review_case_successor(case_id)
    assert successor_id is not None
    assert (
        reader.read_current_review_case(
            successor_id, None
        ).policy_attention_destinations
        == ()
    )
    reader.close()


def test_current_case_snapshot_cannot_mix_concurrent_supersession() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        reader = open_repository(database)
        report = _report()
        reader.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        reader.publish_proposal(proposal_set)
        case_id = json.loads(reader.admit_review_case(proposal_set))["review_case_id"]
        newer = _report(title="New Title", file_identity="c")
        pointer_written = threading.Event()
        finished = threading.Event()
        writer_errors: list[BaseException] = []
        writer_path = (
            database if reader._binding is None else reader._binding.alias_path
        )

        def supersede() -> None:
            writer = None
            try:
                connection = sqlite3.connect(
                    writer_path,
                    isolation_level=None,
                )
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                writer = SQLiteMetadataReportRepository(connection)
                writer._connection.set_trace_callback(
                    lambda statement: (
                        pointer_written.set()
                        if statement.startswith("INSERT INTO current_metadata_reports")
                        else None
                    )
                )
                writer.publish(
                    newer,
                    lambda: True,
                    expected_current=report.report_id,
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
            document = original(review_case_id)
            if not started:
                started = True
                threading.Thread(target=supersede, daemon=True).start()
                if not pointer_written.wait(5):
                    pytest.fail(
                        f"supersession did not reach its pointer: {writer_errors!r}"
                    )
            return document

        with patch.object(reader, "get_review_case", side_effect=interleave):
            snapshot = reader.read_current_review_case(case_id, 50)
        assert json.loads(snapshot.case_document)["current"] is True
        assert (
            MetadataReport.from_json(snapshot.report_document).report_id
            == report.report_id
        )
        assert finished.wait(5)
        assert writer_errors == []
        assert json.loads(reader.get_review_case(case_id))["current"] is False
        reader.close()


@pytest.mark.parametrize(
    "surface", ("case", "library", "attention"), ids=("case", "library", "attention")
)
def test_review_reads_hold_one_snapshot_across_concurrent_decision(
    surface: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        reader = open_repository(database)
        report = _report()
        reader.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        reader.publish_proposal(proposal_set)
        case_id = json.loads(reader.admit_review_case(proposal_set))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(reader.get_review_case(case_id))["case"]
        )
        proposal = next(
            value
            for value in case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )
        decision = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"],
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key=f"snapshot-{surface}-decision",
            occurred_at=1_000,
        )
        inserted = threading.Event()
        finished = threading.Event()
        writer_errors: list[BaseException] = []
        writer_path = (
            database if reader._binding is None else reader._binding.alias_path
        )

        def write_decision() -> None:
            writer = None
            try:
                connection = sqlite3.connect(
                    writer_path,
                    isolation_level=None,
                )
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                writer = SQLiteMetadataReportRepository(connection)
                writer._connection.set_trace_callback(
                    lambda statement: (
                        inserted.set()
                        if statement.startswith("INSERT INTO human_decisions")
                        else None
                    )
                )
                writer.record_human_decision(decision)
            except BaseException as error:
                writer_errors.append(error)
            finally:
                if writer is not None:
                    writer.close()
                finished.set()

        started = False
        # `original` and `before`/`after` each take one of three
        # per-surface reader shapes below.
        original: Any
        before: Any
        after: Any

        def interleave(original, *args):
            nonlocal started
            result = original(*args)
            if not started:
                started = True
                threading.Thread(target=write_decision, daemon=True).start()
                assert inserted.wait(5)
            return result

        if surface == "case":
            original = reader.get_review_case
            patched = patch.object(
                reader,
                "get_review_case",
                side_effect=lambda *args: interleave(original, *args),
            )
        elif surface == "library":
            original = reader.read_current_library_page
            patched = patch.object(
                reader,
                "read_current_library_page",
                side_effect=lambda *args: interleave(original, *args),
            )
        else:
            original = reader.read_current_attention_page
            patched = patch.object(
                reader,
                "read_current_attention_page",
                side_effect=lambda *args: interleave(original, *args),
            )

        with patched as invoked:
            if surface == "case":
                before = reader.read_current_review_case(case_id, 50)
                assert before.decision_documents == ()
                assert not any(
                    item.get("kind") == "human_decision_recorded"
                    for item in before.history
                )
            elif surface == "library":
                before = reader.read_review_library(None, 10, 0, None)
                assert before.attention_total == 1
                assert before.rows[0][2] == ()
            else:
                before = reader.read_review_attention(None, 10, 0)
                assert before.total == 1
                assert len(before.rows) == 1

        if surface == "case":
            invoked.assert_called()
        else:
            invoked.assert_called_once()
        assert started
        assert finished.wait(5)
        assert writer_errors == []
        if surface == "case":
            after = reader.read_current_review_case(case_id, 50)
            assert len(after.decision_documents) == 1
            assert any(
                item.get("kind") == "human_decision_recorded" for item in after.history
            )
        elif surface == "library":
            after = reader.read_review_library(None, 10, 0, None)
            assert after.attention_total == 0
            assert len(after.rows[0][2]) == 1
        else:
            after = reader.read_review_attention(None, 10, 0)
            assert after.total == 0
            assert after.rows == ()
        reader.close()


def test_review_snapshot_failure_rolls_back_without_partial_state() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        with patch.object(
            repository,
            "read_current_library_page",
            side_effect=RuntimeError("synthetic"),
        ) as invoked:
            with pytest.raises(MetadataReportError):
                repository.read_review_library(None, 10, 0, None)
        invoked.assert_called_once()
        assert not repository._connection.in_transaction
        repository.close()


def test_sqlite_logout_classification_is_atomic_across_connections() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "review-sessions.sqlite"
        first = SQLiteReviewSessionRepository.open(database)
        second = SQLiteReviewSessionRepository.open(database)
        first.create_review_session("a" * 64, "c" * 64, 100, 200, "9" * 64)
        competing_started = threading.Event()
        competing_outcomes: list[str] = []
        competing_errors: list[BaseException] = []

        def competing_logout() -> None:
            competing_started.set()
            try:
                competing_outcomes.append(
                    second.revoke_review_session("a" * 64, "c" * 64, 151)
                )
            except BaseException as error:
                competing_errors.append(error)

        competing = threading.Thread(target=competing_logout)

        class InterleavingConnection:
            """Let the competing logout win the race on the session lookup."""

            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection

            def execute(self, statement: str, parameters=()):
                if statement.startswith("SELECT 1 FROM review_sessions"):
                    competing.start()
                    self.assert_competing_started()
                return self._connection.execute(statement, parameters)

            def assert_competing_started(self) -> None:
                if not competing_started.wait(timeout=1):
                    raise AssertionError("competing logout did not start")

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        # The interleaving connection is supplied at construction, so the
        # statement below reaches it: the store holds its own reference to
        # its one private connection.
        interleaved = SQLiteReviewSessionRepository(
            InterleavingConnection(first._connection)
        )
        assert interleaved.revoke_review_session("a" * 64, "d" * 64, 151) == "csrf"
        competing.join(timeout=2)
        assert not competing.is_alive()
        assert not competing_errors
        assert competing_outcomes == ["revoked"]
        first.close()
        second.close()


def test_pre_decision_application_schema_is_rejected_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        repository = open_repository(database)
        repository.close()
        connection = sqlite3.connect(database)
        try:
            connection.execute("DROP TRIGGER human_decision_events_no_delete")
            connection.execute("DROP TRIGGER human_decision_events_no_update")
            connection.execute("DROP TRIGGER human_decisions_no_delete")
            connection.execute("DROP TRIGGER human_decisions_no_update")
            connection.execute("DROP TABLE human_decision_events")
            connection.execute("DROP TABLE human_decisions")
            connection.commit()
        finally:
            connection.close()
        before = database.read_bytes()
        with pytest.raises(MetadataReportError):
            open_repository(database, create=False)
        assert database.read_bytes() == before


def test_concurrent_http_decisions_preserve_both_immutable_revisions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "application.sqlite3"
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        repository = open_repository(database, cross_thread=True)
        report = _report()
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        client = TestClient(
            create_transport_test_app(
                repository,
                ReviewWebConfiguration(
                    tls_certificate_file="/synthetic/review.crt",
                    tls_private_key_file="/synthetic/review.key",
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(hash_path),
                ),
                clock=lambda: 1_000,
            ),
            base_url="https://review.test",
        )
        try:
            login = client.post(
                "/api/v1/session/login",
                headers={"Origin": "https://review.test"},
                json={"password": "correct horse"},
            ).json()
            field = client.get(f"/api/v1/review-cases/{case_id}").json()["fields"][0]
            base_payload = {
                "destination": field["destination"],
                "proposal_id": field["proposal_id"],
                "selected_value": None,
                "target_fingerprint": field["target_fingerprint"],
            }

            def submit(value: tuple[str, str]) -> int:
                outcome, key = value
                return client.post(
                    f"/api/v1/review-cases/{case_id}/decisions",
                    headers={
                        "Origin": "https://review.test",
                        "X-CSRF-Token": login["csrf_token"],
                        "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
                        "Idempotency-Key": key,
                    },
                    json={**base_payload, "outcome": outcome},
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as executor:
                statuses = sorted(
                    executor.map(
                        submit,
                        (
                            ("keep_current", "concurrent-decision-0001"),
                            ("reject", "concurrent-decision-0002"),
                        ),
                    )
                )
            assert statuses == [200, 200]
            assert repository._connection.execute(
                "SELECT count(*) FROM human_decisions"
            ).fetchone() == (2,)
            assert repository._connection.execute(
                "SELECT count(*) FROM human_decision_events"
            ).fetchone() == (2,)
            historical_reject = next(
                event
                for event in repository.get_review_case_history(case_id, 10)
                if event.get("kind") == "human_decision_recorded"
                and event.get("outcome") == "reject"
            )
            assert historical_reject["selected_value"] is None
            library = client.get("/api/v1/library?lens=no_attention_needed")
            assert library.status_code == 200, library.text
            assert library.json()["counts"] == {
                "all": 1,
                "needs_attention": 0,
                "no_attention_needed": 1,
                "not_matched": 0,
                "preparation_failed": 0,
            }
            assert len(library.json()["items"]) == 1
            assert (
                library.json()["items"][0]["attention_state"] == "no_attention_needed"
            )
            assert library.json()["items"][0]["attention_count"] == 0
            client.cookies.clear()
            second_login = client.post(
                "/api/v1/session/login",
                headers={"Origin": "https://review.test"},
                json={"password": "correct horse"},
            )
            assert second_login.status_code == 200
            second_context = client.get(f"/api/v1/review-cases/{case_id}").json()
            assert second_context["fields"][0]["human_decision"] is not None
        finally:
            client.close()
            repository.close()


def test_decision_event_failure_rolls_back_acknowledgement_atomically() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        report = _report()
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(
            value
            for value in case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )
        decision = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"],
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="rollback-decision-0001",
            occurred_at=1_000,
        )
        repository._connection.execute(
            "CREATE TEMP TRIGGER synthetic_event_failure "
            "BEFORE INSERT ON human_decision_events "
            "BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        with pytest.raises(MetadataReportError):
            repository.record_human_decision(decision)
        assert repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (0,)
        repository.close()


def test_sqlite_human_decision_is_atomic_idempotent_and_reopens_exactly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        repository = open_repository(database)
        report = _report()
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(
            value
            for value in case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )
        selected = proposal["result"]["alternatives"][0]["value"]
        decision = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"],
            actor="owner",
            outcome="accept",
            selected_value=selected,
            idempotency_key="sqlite-decision-0001",
            occurred_at=1_000,
        )
        assert (
            HumanDecision.from_json(
                repository.record_human_decision(decision)
            ).decision_id
            == decision.decision_id
        )
        assert (
            HumanDecision.from_json(
                repository.find_human_decision(decision.idempotency_digest) or ""
            ).decision_id
            == decision.decision_id
        )
        retry = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"],
            actor="owner",
            outcome="accept",
            selected_value=selected,
            idempotency_key="sqlite-decision-0001",
            occurred_at=1_001,
        )
        assert (
            HumanDecision.from_json(repository.record_human_decision(retry)).decision_id
            == decision.decision_id
        )
        conflict = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"],
            actor="owner",
            outcome="reject",
            selected_value=None,
            idempotency_key="sqlite-decision-0001",
            occurred_at=1_002,
        )
        with pytest.raises(HumanDecisionError) as raised:
            repository.record_human_decision(conflict)
        assert raised.value.code == "idempotency_conflict"
        duplicate = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=proposal["destination"],
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="sqlite-decision-0002",
            occurred_at=1_003,
        )
        assert (
            HumanDecision.from_json(
                repository.record_human_decision(duplicate)
            ).decision_id
            == duplicate.decision_id
        )
        assert repository.count_current_attention_fields(None) == 0
        assert (
            repository.get_review_case_history(case_id, 10)[0]["kind"]
            == "human_decision_recorded"
        )
        repository.close()

        reopened = open_repository(database, create=False)
        try:
            documents = reopened.get_review_case_decisions(case_id)
            assert len(documents) == 2
            assert (
                HumanDecision.from_json(documents[0]).decision_id
                == decision.decision_id
            )
            assert (
                HumanDecision.from_json(documents[1]).decision_id
                == duplicate.decision_id
            )
            assert (
                HumanDecision.from_json(
                    reopened.find_human_decision(decision.idempotency_digest) or ""
                ).decision_id
                == decision.decision_id
            )
            detail = InspectCurrentReviewCase(reopened).run(case_id)
            assert not detail["fields"][0]["needs_attention"]
            newer = _report(content_digest="c", file_identity="d")
            reopened.publish(newer, lambda: True)
            replayed = RecordHumanDecision(reopened, lambda: 2_000).run(
                case_id,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="accept",
                selected_value=selected,
                idempotency_key="sqlite-decision-0001",
            )
            assert replayed["decision_id"] == decision.decision_id
            assert reopened._connection.execute(
                "SELECT (SELECT count(*) FROM human_decisions),"
                "(SELECT count(*) FROM human_decision_events)"
            ).fetchone() == (2, 2)
            with pytest.raises(ReviewWebError) as replay_conflict:
                RecordHumanDecision(reopened, lambda: 2_001).run(
                    case_id,
                    proposal_id=proposal["proposal_id"],
                    target_fingerprint=case.target_fingerprint,
                    destination=proposal["destination"],
                    actor="owner",
                    outcome="reject",
                    selected_value=None,
                    idempotency_key="sqlite-decision-0001",
                )
            assert replay_conflict.value.code == "idempotency_conflict"
            stale_decision = HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="reject",
                selected_value=None,
                idempotency_key="sqlite-decision-0003",
                occurred_at=2_002,
            )
            with pytest.raises(HumanDecisionError) as stale:
                reopened.record_human_decision(stale_decision)
            assert stale.value.code == "stale"
        finally:
            reopened.close()


def test_decision_reports_persistence_only_where_the_store_was_asked() -> None:
    """`persistence` is a finding about the store, not a catch-all.

    The command's last resort converted everything it caught to
    `persistence`, so the clock, the case parse, the domain decision and the
    acknowledgement projection all reported a storage failure -- for a store
    that had answered every call correctly, or had not been asked yet. Each
    store call carries its own `persistence` handler now, and what is left
    under the last resort touches no store and answers `unavailable` (#698).
    """
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "application.sqlite3")
        try:
            report = _report()
            repository.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            repository.publish_proposal(proposal_set)
            case_id = json.loads(repository.admit_review_case(proposal_set))[
                "review_case_id"
            ]
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            )
            proposal = next(
                value
                for value in case.proposals
                if value["result"]["outcome"] == "unresolved_choice"
            )

            def record(clock, key: str) -> ReviewWebError:
                with pytest.raises(ReviewWebError) as raised:
                    RecordHumanDecision(repository, clock).run(
                        case_id,
                        proposal_id=proposal["proposal_id"],
                        target_fingerprint=case.target_fingerprint,
                        destination=proposal["destination"],
                        actor="owner",
                        outcome="accept",
                        selected_value=(proposal["result"]["alternatives"][0]["value"]),
                        idempotency_key=key,
                    )
                return raised.value

            # The clock is this layer's own, and the store has answered two
            # reads correctly by the time it is called.
            clock_fault = ValueError("clock is broken")

            def broken_clock() -> int:
                raise clock_fault

            local = record(broken_clock, "decision-local-0001")
            assert local.code == "unavailable"
            assert local.__cause__ is clock_fault
            assert (0,) == repository._connection.execute(
                "SELECT count(*) FROM human_decisions"
            ).fetchone()

            # The store's own failure keeps the identity that names it.
            store_fault = OSError("database is gone")
            with patch.object(
                type(repository),
                "record_human_decision",
                side_effect=store_fault,
            ):
                stored = record(lambda: 1_000, "decision-store-0001")
            assert stored.code == "persistence"
            assert stored.__cause__ is store_fault
        finally:
            repository.close()


def test_session_survives_exact_current_reopen_and_prior_schema_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "review-sessions.sqlite"
        repository = SQLiteReviewSessionRepository.open(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        repository.create_review_session("a" * 64, "b" * 64, 100, 200, "9" * 64)
        repository.close()
        reopened = SQLiteReviewSessionRepository.open(path)
        assert reopened.authenticate_review_session("a" * 64, 150) == (
            "owner",
            "b" * 64,
            200,
        )
        assert reopened.rotate_review_session_csrf("a" * 64, "c" * 64, 150) == "rotated"
        assert reopened.authenticate_review_session("a" * 64, 150) == (
            "owner",
            "c" * 64,
            200,
        )
        assert reopened.revoke_review_session("a" * 64, "d" * 64, 151) == "csrf"
        assert reopened.authenticate_review_session("a" * 64, 151) == (
            "owner",
            "c" * 64,
            200,
        )
        assert reopened.revoke_review_session("a" * 64, "c" * 64, 151) == "revoked"
        assert reopened.revoke_review_session("a" * 64, "c" * 64, 151) == "unauthorized"
        reopened.create_review_session("e" * 64, "f" * 64, 100, 200, "9" * 64)
        assert reopened.revoke_review_session("e" * 64, "f" * 64, 200) == "unauthorized"
        with pytest.raises(MetadataReportError):
            reopened.authenticate_review_session("a" * 64, 152)
        assert (
            reopened.rotate_review_session_csrf("a" * 64, "d" * 64, 152)
            == "unauthorized"
        )
        reopened.close()
        foreign = Path(directory) / "foreign.sqlite"
        connection = sqlite3.connect(foreign)
        connection.execute("CREATE TABLE not_sessions(x)")
        connection.commit()
        connection.close()
        before = foreign.read_bytes()
        with pytest.raises(MetadataReportError):
            SQLiteReviewSessionRepository.open(foreign)
        assert foreign.read_bytes() == before


def test_predecessor_throttle_schema_requires_a_clean_rebuild(tmp_path: Path) -> None:
    """#1384 keys failures by client, so the unkeyed table is refused unchanged."""
    path = tmp_path / "review-sessions.sqlite"
    SQLiteReviewSessionRepository.open(path).close()
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("DROP TABLE review_login_failures")
    connection.execute(
        "CREATE TABLE review_login_failures ("
        "failure_id TEXT PRIMARY KEY NOT NULL CHECK(length(failure_id)=64 AND failure_id NOT GLOB '*[^0-9a-f]*'), "
        "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0))"
    )
    connection.execute(
        "INSERT INTO review_login_failures VALUES(?,?)", ("a" * 64, 1_000)
    )
    connection.close()
    before = {value.name: value.read_bytes() for value in tmp_path.iterdir()}

    with pytest.raises(MetadataReportError):
        SQLiteReviewSessionRepository.open(path)

    assert {value.name: value.read_bytes() for value in tmp_path.iterdir()} == before


def test_login_client_key_is_its_own_verifier_bound_domain() -> None:
    verifier = PasswordHasher().hash("correct horse")
    _, session_digest, client_digest = owner_password_binding(verifier)
    _, _, replaced = owner_password_binding(PasswordHasher().hash("correct horse"))
    identity = "client:192.0.2.10"

    assert len(client_digest(identity)) == 64
    assert client_digest(identity) == client_digest(identity)
    assert client_digest(identity) != session_digest(identity)
    assert client_digest(identity) != client_digest("client:192.0.2.11")
    assert client_digest(identity) != replaced(identity)
    assert "192.0.2.10" not in client_digest(identity)


def test_existing_empty_session_database_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "review-sessions.sqlite"
    path.write_bytes(b"")
    path.chmod(0o600)

    with pytest.raises(MetadataReportError):
        SQLiteReviewSessionRepository.open(path)

    assert path.read_bytes() == b""


@pytest.mark.parametrize("suffix", ("-journal", "-wal", "-shm"))
def test_orphan_session_sidecar_is_rejected_without_mutation(
    tmp_path: Path, suffix: str
) -> None:
    path = tmp_path / "review-sessions.sqlite"
    sidecar = Path(f"{path}{suffix}")
    sidecar.write_bytes(b"retained partial session state")
    before = {value.name: value.read_bytes() for value in tmp_path.iterdir()}

    with pytest.raises(MetadataReportError):
        SQLiteReviewSessionRepository.open(path)

    assert {value.name: value.read_bytes() for value in tmp_path.iterdir()} == before


def test_wal_session_database_is_rejected_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "review-sessions.sqlite"
    repository = SQLiteReviewSessionRepository.open(path)
    repository.close()
    connection = sqlite3.connect(path, isolation_level=None)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    before = {value.name: value.read_bytes() for value in tmp_path.iterdir()}

    with pytest.raises(MetadataReportError):
        SQLiteReviewSessionRepository.open(path)

    assert {value.name: value.read_bytes() for value in tmp_path.iterdir()} == before


def test_session_database_symlink_is_rejected_without_target_mutation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.sqlite"
    target.write_bytes(b"")
    target.chmod(0o600)
    path = tmp_path / "review-sessions.sqlite"
    path.symlink_to(target)

    with pytest.raises(MetadataReportError):
        SQLiteReviewSessionRepository.open(path)

    assert path.is_symlink()
    assert target.read_bytes() == b""


def test_non_owner_only_session_database_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "review-sessions.sqlite"
    repository = SQLiteReviewSessionRepository.open(path)
    repository.close()
    path.chmod(0o640)
    before = path.read_bytes()

    with pytest.raises(MetadataReportError):
        SQLiteReviewSessionRepository.open(path)

    assert path.read_bytes() == before


def test_verifier_bound_session_survives_reopen_without_sliding_and_rejects_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "review-sessions.sqlite"
    verifier = PasswordHasher().hash("correct horse")
    verify, digest, client = owner_password_binding(verifier)
    tokens = iter(("s" * 32, "c" * 32))
    repository = SQLiteReviewSessionRepository.open(path)
    issued = OwnerSessionService(
        repository,
        verify,
        digest,
        lambda: 100,
        600,
        lambda _length: next(tokens),
        client_digest=client,
    ).login("correct horse", "client:192.0.2.10")
    repository.close()

    reopened = SQLiteReviewSessionRepository.open(path)
    resumed = OwnerSessionService(
        reopened, verify, digest, lambda: 699, 600, client_digest=client
    )
    assert resumed.authenticate(issued.session_token) == "owner"
    assert resumed.issue_browser_csrf(issued.session_token)
    assert resumed.authenticate(issued.session_token) == "owner"
    assert reopened._connection.execute(
        "SELECT expires_at FROM review_sessions"
    ).fetchone() == (700,)
    expired = OwnerSessionService(
        reopened, verify, digest, lambda: 700, 600, client_digest=client
    )
    with pytest.raises(ReviewWebError, match="unauthorized"):
        expired.authenticate(issued.session_token)

    _, replacement_digest, replacement_client = owner_password_binding(
        PasswordHasher().hash("correct horse")
    )
    replaced = OwnerSessionService(
        reopened,
        verify,
        replacement_digest,
        lambda: 699,
        600,
        client_digest=replacement_client,
    )
    with pytest.raises(ReviewWebError, match="unauthorized"):
        replaced.authenticate(issued.session_token)
    reopened.close()


def test_real_repository_serves_login_from_fastapi_worker_thread() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "application.sqlite3"
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        repository = open_repository(database, cross_thread=True)
        session_store = SQLiteReviewSessionRepository.open(
            root / "review-sessions.sqlite"
        )
        client = TestClient(
            create_transport_test_app(
                repository,
                ReviewWebConfiguration(
                    tls_certificate_file="/synthetic/review.crt",
                    tls_private_key_file="/synthetic/review.key",
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(hash_path),
                ),
                session_store=session_store,
            ),
            base_url="https://review.test",
        )
        try:
            response = client.post(
                "/api/v1/session/login",
                headers={"Origin": "https://review.test"},
                json={"password": "correct horse"},
            )
            assert response.status_code == 200, response.text
        finally:
            client.close()
            session_store.close()
            repository.close()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_authenticated_preparation_status_survives_writer_journal_churn() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "application.sqlite3"
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        repository = open_repository(database, cross_thread=True)
        session_store = SQLiteReviewSessionRepository.open(
            root / "review-sessions.sqlite"
        )
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 1)
        repository.set_review_pipeline_paused(True, 2)
        authority = repository.authority
        assert authority is not None
        assert authority is not None
        control_started = threading.Event()
        release_writer = threading.Event()
        commit_attempted = threading.Event()
        commit_completed = threading.Event()
        trace_errors: list[BaseException] = []

        def open_status_repository():
            reader = open_repository(database, authority=authority)

            def trace(statement: str) -> None:
                if statement.startswith(
                    "SELECT paused FROM review_preparation_control"
                ):
                    control_started.set()
                    release_writer.set()
                    try:
                        if not commit_attempted.wait(5):
                            trace_errors.append(
                                AssertionError(
                                    "writer did not attempt commit during status read"
                                )
                            )
                    finally:
                        reader._connection.set_trace_callback(None)

            reader._connection.set_trace_callback(trace)
            return reader

        preparation = ReviewPreparationPipeline(
            repository_factory=open_status_repository,
            libraries=(("primary", "fiction", "lib-1", "f" * 64),),
            discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
            extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
            providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
            propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
            admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
            project=lambda *_: (_ for _ in ()).throw(AssertionError()),
        )
        client = TestClient(
            create_transport_test_app(
                repository,
                ReviewWebConfiguration(
                    tls_certificate_file="/synthetic/review.crt",
                    tls_private_key_file="/synthetic/review.key",
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(hash_path),
                ),
                session_store=session_store,
                review_preparation=preparation,
            ),
            base_url="https://review.test",
        )
        writer_errors: list[BaseException] = []

        def churn_journal_descriptors() -> None:
            writer_repository = None
            try:
                writer_repository = open_repository(database, authority=authority)
                for updated_at in range(3, 28):
                    if not release_writer.wait(5):
                        pytest.fail(
                            "authenticated status read did not release writer churn"
                        )
                    release_writer.clear()
                    paused = updated_at % 2 == 1
                    state = "queued" if paused else "discovering"
                    writer_repository._connection.execute("BEGIN IMMEDIATE")
                    writer_repository._connection.execute(
                        "UPDATE review_preparation_control "
                        "SET paused=?,updated_at=? WHERE singleton=1",
                        (paused, updated_at),
                    )
                    writer_repository._connection.execute(
                        "UPDATE review_preparation_scans "
                        "SET state=?,updated_at=? WHERE connection_name='primary' "
                        "AND library_name='fiction'",
                        (state, updated_at),
                    )
                    commit_attempted.set()
                    writer_repository._connection.execute("COMMIT")
                    commit_completed.set()
            except BaseException as error:
                writer_errors.append(error)
            finally:
                if writer_repository is not None:
                    writer_repository.close()

        writer = threading.Thread(target=churn_journal_descriptors)
        try:
            login = client.post(
                "/api/v1/session/login",
                headers={"Origin": "https://review.test"},
                json={"password": "correct horse"},
            )
            assert login.status_code == 200, login.text
            client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
            writer.start()
            for _ in range(25):
                control_started.clear()
                commit_attempted.clear()
                commit_completed.clear()
                status = client.get("/api/v1/review-preparation")
                assert status.status_code == 200, status.text
                assert control_started.is_set()
                assert commit_attempted.is_set()
                payload = status.json()
                assert (payload["paused"], payload["libraries"][0]["state"]) in (
                    (True, "queued"),
                    (False, "discovering"),
                )
                assert payload["libraries"][0]["connection"] == "primary"
                assert payload["libraries"][0]["library"] == "fiction"
                assert commit_completed.wait(5)
        finally:
            release_writer.set()
            writer.join(5)
            client.close()
            session_store.close()
            repository.close()
        assert not writer.is_alive()
        assert writer_errors == []
        assert trace_errors == []


def test_login_ignores_application_database_writer_and_gate(tmp_path: Path) -> None:
    """#912: owner sign-in must not wait on scan writes.

    One writer holds the application database's rollback-journal write lock
    for the whole request, and another thread holds the application
    serialization gate; login still answers well inside the five-second busy
    backstop the old shared-database coupling queued on.
    """
    database = tmp_path / "application.sqlite3"
    hash_path = tmp_path / "owner.hash"
    hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(hash_path, 0o600)
    repository = open_repository(database, cross_thread=True)
    session_store = SQLiteReviewSessionRepository.open(
        tmp_path / "review-sessions.sqlite"
    )
    app = create_transport_test_app(
        repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin="https://review.test",
            owner_password_hash_file=str(hash_path),
        ),
        session_store=session_store,
    )
    application = app.state.review_application
    client = TestClient(app, base_url="https://review.test")
    writer = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
    gate_held = threading.Event()
    release_gate = threading.Event()

    def hold_gate() -> None:
        with application._gate:
            gate_held.set()
            release_gate.wait(30)

    holder = threading.Thread(target=hold_gate)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "UPDATE review_preparation_control SET updated_at=97 WHERE singleton=1"
        )
        holder.start()
        assert gate_held.wait(5)
        started = time.monotonic()
        response = client.post(
            "/api/v1/session/login",
            headers={"Origin": "https://review.test"},
            json={"password": "correct horse"},
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 200, response.text
        assert elapsed < 2.0
    finally:
        release_gate.set()
        holder.join(5)
        writer.execute("ROLLBACK")
        writer.close()
        client.close()
        session_store.close()
        repository.close()
