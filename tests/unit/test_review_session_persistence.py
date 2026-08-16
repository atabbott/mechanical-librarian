"""Review session and decision persistence: snapshot isolation,
decision atomicity and idempotency, logout classification, and
exact-current schema admission across reopen.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
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
from tests.support.review_cases import _report
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)


class ReviewSessionPersistenceTests(unittest.TestCase):
    def test_current_case_snapshot_cannot_mix_concurrent_supersession(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            reader = open_repository(database)
            report = _report()
            reader.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            reader.publish_proposal(proposal_set)
            case_id = json.loads(reader.admit_review_case(proposal_set))[
                "review_case_id"
            ]
            newer = _report(title="New Title", file_identity="c")
            pointer_written = threading.Event()
            finished = threading.Event()
            writer_errors: list[BaseException] = []
            writer_path = (
                database
                if reader._binding is None
                else reader._binding.alias_path
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
                        lambda statement: pointer_written.set()
                        if statement.startswith("INSERT INTO current_metadata_reports")
                        else None
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
                        self.fail(f"supersession did not reach its pointer: {writer_errors!r}")
                return document

            with patch.object(reader, "get_review_case", side_effect=interleave):
                snapshot = reader.read_current_review_case(case_id, 50)
            self.assertIs(json.loads(snapshot.case_document)["current"], True)
            self.assertEqual(
                MetadataReport.from_json(snapshot.report_document).report_id,
                report.report_id,
            )
            self.assertTrue(finished.wait(5))
            self.assertEqual(writer_errors, [])
            self.assertIs(json.loads(reader.get_review_case(case_id))["current"], False)
            reader.close()

    def test_review_reads_hold_one_snapshot_across_concurrent_decision(self) -> None:
        for surface in ("case", "library", "attention"):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "application.sqlite3"
                reader = open_repository(database)
                report = _report()
                reader.publish(report, lambda: True)
                proposal_set = evaluate_report(report)
                reader.publish_proposal(proposal_set)
                case_id = json.loads(reader.admit_review_case(proposal_set))[
                    "review_case_id"
                ]
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
                    database
                    if reader._binding is None
                    else reader._binding.alias_path
                )

                def write_decision() -> None:
                    writer = None
                    try:
                        connection = sqlite3.connect(
                            writer_path,  # noqa: B023 - closure is created and consumed within this iteration
                            isolation_level=None,
                        )
                        connection.execute("PRAGMA foreign_keys=ON")
                        connection.execute("PRAGMA busy_timeout=5000")
                        writer = SQLiteMetadataReportRepository(connection)
                        writer._connection.set_trace_callback(
                            lambda statement: inserted.set()  # noqa: B023 - closure is created and consumed within this iteration
                            if statement.startswith("INSERT INTO human_decisions")
                            else None
                        )
                        writer.record_human_decision(decision)  # noqa: B023 - closure is created and consumed within this iteration
                    except BaseException as error:
                        writer_errors.append(error)  # noqa: B023 - closure is created and consumed within this iteration
                    finally:
                        if writer is not None:
                            writer.close()
                        finished.set()  # noqa: B023 - closure is created and consumed within this iteration

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
                        threading.Thread(
                            target=write_decision, daemon=True
                        ).start()
                        self.assertTrue(inserted.wait(5))  # noqa: B023 - closure is created and consumed within this iteration
                    return result

                if surface == "case":
                    original = reader.get_review_case
                    patched = patch.object(
                        reader,
                        "get_review_case",
                        side_effect=lambda *args: interleave(original, *args),  # noqa: B023 - closure is created and consumed within this iteration
                    )
                elif surface == "library":
                    original = reader.list_current_review_case_documents
                    patched = patch.object(
                        reader,
                        "list_current_review_case_documents",
                        side_effect=lambda *args: interleave(original, *args),  # noqa: B023 - closure is created and consumed within this iteration
                    )
                else:
                    original = reader.list_current_attention_documents
                    patched = patch.object(
                        reader,
                        "list_current_attention_documents",
                        side_effect=lambda *args: interleave(original, *args),  # noqa: B023 - closure is created and consumed within this iteration
                    )

                with patched:
                    if surface == "case":
                        before = reader.read_current_review_case(case_id, 50)
                        self.assertEqual(before.decision_documents, ())
                        self.assertFalse(
                            any(
                                item.get("kind") == "human_decision_recorded"
                                for item in before.history
                            )
                        )
                    elif surface == "library":
                        before = reader.read_review_library(None, 10, 0, None)
                        self.assertEqual(before.attention_total, 1)
                        self.assertEqual(before.rows[0][2], ())
                    else:
                        before = reader.read_review_attention(None, 10, 0)
                        self.assertEqual(before.total, 1)
                        self.assertEqual(len(before.rows), 1)

                self.assertTrue(finished.wait(5))
                self.assertEqual(writer_errors, [])
                if surface == "case":
                    after = reader.read_current_review_case(case_id, 50)
                    self.assertEqual(len(after.decision_documents), 1)
                    self.assertTrue(
                        any(
                            item.get("kind") == "human_decision_recorded"
                            for item in after.history
                        )
                    )
                elif surface == "library":
                    after = reader.read_review_library(None, 10, 0, None)
                    self.assertEqual(after.attention_total, 0)
                    self.assertEqual(len(after.rows[0][2]), 1)
                else:
                    after = reader.read_review_attention(None, 10, 0)
                    self.assertEqual(after.total, 0)
                    self.assertEqual(after.rows, ())
                reader.close()

    def test_review_snapshot_failure_rolls_back_without_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "application.sqlite3")
            with patch.object(
                repository,
                "list_current_review_case_documents",
                side_effect=RuntimeError("synthetic"),
            ):
                with self.assertRaises(MetadataReportError):
                    repository.read_review_library(None, 10, 0, None)
            self.assertFalse(repository._connection.in_transaction)
            repository.close()

    def test_sqlite_logout_classification_is_atomic_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            first = open_repository(database, cross_thread=True)
            second = SQLiteMetadataReportRepository(
                sqlite3.connect(
                    database,
                    check_same_thread=False,
                    isolation_level=None,
                )
            )
            first.create_review_session("a" * 64, "c" * 64, 100, 200)
            competing_started = threading.Event()
            competing_outcomes: list[str] = []
            competing_errors: list[BaseException] = []

            def competing_logout() -> None:
                competing_started.set()
                try:
                    competing_outcomes.append(
                        second.revoke_review_session(
                            "a" * 64, "c" * 64, 151
                        )
                    )
                except BaseException as error:
                    competing_errors.append(error)

            competing = threading.Thread(target=competing_logout)

            connection = first._connection

            class InterleavingConnection:
                def execute(self, statement: str, parameters=()):
                    if statement.startswith("SELECT 1 FROM review_sessions"):
                        competing.start()
                        self.assert_competing_started()
                    return connection.execute(statement, parameters)

                def assert_competing_started(self) -> None:
                    if not competing_started.wait(timeout=1):
                        raise AssertionError("competing logout did not start")

                def __getattr__(self, name: str):
                    return getattr(connection, name)

            first._connection = InterleavingConnection()  # type: ignore[assignment]
            self.assertEqual(
                first.revoke_review_session("a" * 64, "d" * 64, 151),
                "csrf",
            )
            competing.join(timeout=2)
            self.assertFalse(competing.is_alive())
            self.assertFalse(competing_errors)
            self.assertEqual(competing_outcomes, ["revoked"])
            first.close()
            second.close()

    def test_pre_decision_application_schema_is_rejected_without_mutation(self) -> None:
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
            with self.assertRaises(MetadataReportError):
                open_repository(database, create=False)
            self.assertEqual(database.read_bytes(), before)

    def test_concurrent_http_decisions_preserve_both_immutable_revisions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "application.sqlite3"
            hash_path = root / "owner.hash"
            hash_path.write_text(
                PasswordHasher().hash("correct horse"), encoding="utf-8"
            )
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
                field = client.get(
                    f"/api/v1/review-cases/{case_id}"
                ).json()["fields"][0]
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
                self.assertEqual(statuses, [200, 200])
                self.assertEqual(
                    repository._connection.execute(
                        "SELECT count(*) FROM human_decisions"
                    ).fetchone(),
                    (2,),
                )
                self.assertEqual(
                    repository._connection.execute(
                        "SELECT count(*) FROM human_decision_events"
                    ).fetchone(),
                    (2,),
                )
                library = client.get(
                    "/api/v1/library?lens=no_attention_needed"
                )
                self.assertEqual(library.status_code, 200, library.text)
                self.assertEqual(
                    library.json()["counts"],
                    {
                        "all": 1,
                        "needs_attention": 0,
                        "no_attention_needed": 1,
                    },
                )
                self.assertEqual(len(library.json()["items"]), 1)
                self.assertEqual(
                    library.json()["items"][0]["attention_state"],
                    "no_attention_needed",
                )
                self.assertEqual(
                    library.json()["items"][0]["attention_count"], 0
                )
                client.cookies.clear()
                second_login = client.post(
                    "/api/v1/session/login",
                    headers={"Origin": "https://review.test"},
                    json={"password": "correct horse"},
                )
                self.assertEqual(second_login.status_code, 200)
                second_context = client.get(
                    f"/api/v1/review-cases/{case_id}"
                ).json()
                self.assertIsNotNone(
                    second_context["fields"][0]["human_decision"]
                )
            finally:
                client.close()
                repository.close()

    def test_decision_event_failure_rolls_back_acknowledgement_atomically(self) -> None:
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
            with self.assertRaises(MetadataReportError):
                repository.record_human_decision(decision)
            self.assertEqual(
                repository._connection.execute(
                    "SELECT count(*) FROM human_decisions"
                ).fetchone(),
                (0,),
            )
            repository.close()

    def test_sqlite_human_decision_is_atomic_idempotent_and_reopens_exactly(
        self,
    ) -> None:
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
            self.assertEqual(
                HumanDecision.from_json(
                    repository.record_human_decision(decision)
                ).decision_id,
                decision.decision_id,
            )
            self.assertEqual(
                HumanDecision.from_json(
                    repository.find_human_decision(
                        decision.idempotency_digest
                    )
                    or ""
                ).decision_id,
                decision.decision_id,
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
            self.assertEqual(
                HumanDecision.from_json(
                    repository.record_human_decision(retry)
                ).decision_id,
                decision.decision_id,
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
            with self.assertRaises(HumanDecisionError) as raised:
                repository.record_human_decision(conflict)
            self.assertEqual(raised.exception.code, "idempotency_conflict")
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
            self.assertEqual(
                HumanDecision.from_json(
                    repository.record_human_decision(duplicate)
                ).decision_id,
                duplicate.decision_id,
            )
            self.assertEqual(repository.count_current_attention_fields(None), 0)
            self.assertEqual(
                repository.get_review_case_history(case_id, 10)[0]["kind"],
                "human_decision_recorded",
            )
            repository.close()

            reopened = open_repository(database, create=False)
            try:
                documents = reopened.get_review_case_decisions(case_id)
                self.assertEqual(len(documents), 2)
                self.assertEqual(
                    HumanDecision.from_json(documents[0]).decision_id,
                    decision.decision_id,
                )
                self.assertEqual(
                    HumanDecision.from_json(documents[1]).decision_id,
                    duplicate.decision_id,
                )
                self.assertEqual(
                    HumanDecision.from_json(
                        reopened.find_human_decision(
                            decision.idempotency_digest
                        )
                        or ""
                    ).decision_id,
                    decision.decision_id,
                )
                detail = InspectCurrentReviewCase(reopened).run(case_id)
                self.assertFalse(detail["fields"][0]["needs_attention"])
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
                self.assertEqual(replayed["decision_id"], decision.decision_id)
                self.assertEqual(
                    reopened._connection.execute(
                        "SELECT (SELECT count(*) FROM human_decisions),"
                        "(SELECT count(*) FROM human_decision_events)"
                    ).fetchone(),
                    (2, 2),
                )
                with self.assertRaises(ReviewWebError) as replay_conflict:
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
                self.assertEqual(
                    replay_conflict.exception.code, "idempotency_conflict"
                )
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
                with self.assertRaises(HumanDecisionError) as stale:
                    reopened.record_human_decision(stale_decision)
                self.assertEqual(stale.exception.code, "stale")
            finally:
                reopened.close()

    def test_decision_reports_persistence_only_where_the_store_was_asked(
        self,
    ) -> None:
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
                case_id = json.loads(
                    repository.admit_review_case(proposal_set)
                )["review_case_id"]
                case = ReviewCase.create_from_data(
                    json.loads(repository.get_review_case(case_id))["case"]
                )
                proposal = next(
                    value
                    for value in case.proposals
                    if value["result"]["outcome"] == "unresolved_choice"
                )

                def record(clock, key: str) -> ReviewWebError:
                    with self.assertRaises(ReviewWebError) as raised:
                        RecordHumanDecision(repository, clock).run(
                            case_id,
                            proposal_id=proposal["proposal_id"],
                            target_fingerprint=case.target_fingerprint,
                            destination=proposal["destination"],
                            actor="owner",
                            outcome="accept",
                            selected_value=(
                                proposal["result"]["alternatives"][0]["value"]
                            ),
                            idempotency_key=key,
                        )
                    return raised.exception

                # The clock is this layer's own, and the store has answered two
                # reads correctly by the time it is called.
                clock_fault = ValueError("clock is broken")

                def broken_clock() -> int:
                    raise clock_fault

                local = record(broken_clock, "decision-local-0001")
                self.assertEqual(local.code, "unavailable")
                self.assertIs(local.__cause__, clock_fault)
                self.assertEqual(
                    (0,),
                    repository._connection.execute(
                        "SELECT count(*) FROM human_decisions"
                    ).fetchone(),
                )

                # The store's own failure keeps the identity that names it.
                store_fault = OSError("database is gone")
                with patch.object(
                    type(repository), "record_human_decision",
                    side_effect=store_fault,
                ):
                    stored = record(lambda: 1_000, "decision-store-0001")
                self.assertEqual(stored.code, "persistence")
                self.assertIs(stored.__cause__, store_fault)
            finally:
                repository.close()

    def test_session_survives_exact_current_reopen_and_prior_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "application.sqlite3"
            repository = open_repository(path)
            repository.create_review_session("a" * 64, "b" * 64, 100, 200)
            repository.close()
            reopened = open_repository(path, create=False)
            self.assertEqual(
                reopened.authenticate_review_session("a" * 64, 150),
                ("owner", "b" * 64, 200),
            )
            self.assertEqual(
                reopened.rotate_review_session_csrf("a" * 64, "c" * 64, 150),
                "rotated",
            )
            self.assertEqual(
                reopened.authenticate_review_session("a" * 64, 150),
                ("owner", "c" * 64, 200),
            )
            self.assertEqual(
                reopened.revoke_review_session("a" * 64, "d" * 64, 151),
                "csrf",
            )
            self.assertEqual(
                reopened.authenticate_review_session("a" * 64, 151),
                ("owner", "c" * 64, 200),
            )
            self.assertEqual(
                reopened.revoke_review_session("a" * 64, "c" * 64, 151),
                "revoked",
            )
            self.assertEqual(
                reopened.revoke_review_session("a" * 64, "c" * 64, 151),
                "unauthorized",
            )
            reopened.create_review_session("e" * 64, "f" * 64, 100, 200)
            self.assertEqual(
                reopened.revoke_review_session("e" * 64, "f" * 64, 200),
                "unauthorized",
            )
            with self.assertRaises(MetadataReportError):
                reopened.authenticate_review_session("a" * 64, 152)
            self.assertEqual(
                reopened.rotate_review_session_csrf("a" * 64, "d" * 64, 152),
                "unauthorized",
            )
            reopened.close()

    def test_real_repository_serves_login_from_fastapi_worker_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "application.sqlite3"
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            repository = open_repository(database, cross_thread=True)
            client = TestClient(
                create_transport_test_app(
                    repository,
                    ReviewWebConfiguration(
                        canonical_origin="https://review.test",
                        owner_password_hash_file=str(hash_path),
                    ),
                ),
                base_url="https://review.test",
            )
            try:
                response = client.post(
                    "/api/v1/session/login",
                    headers={"Origin": "https://review.test"},
                    json={"password": "correct horse"},
                )
                self.assertEqual(response.status_code, 200, response.text)
            finally:
                client.close()
                repository.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_authenticated_preparation_status_survives_writer_journal_churn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "application.sqlite3"
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            repository = open_repository(database, cross_thread=True)
            repository.request_review_scan(
                "primary", "fiction", "lib-1", "f" * 64, 1
            )
            repository.set_review_pipeline_paused(True, 2)
            authority = repository.authority
            self.assertIsNotNone(authority)
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
                        canonical_origin="https://review.test",
                        owner_password_hash_file=str(hash_path),
                    ),
                    review_preparation=preparation,
                ),
                base_url="https://review.test",
            )
            writer_errors: list[BaseException] = []

            def churn_journal_descriptors() -> None:
                writer_repository = None
                try:
                    writer_repository = open_repository(
                        database, authority=authority
                    )
                    for updated_at in range(3, 28):
                        if not release_writer.wait(5):
                            self.fail("authenticated status read did not release writer churn")
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
                self.assertEqual(login.status_code, 200, login.text)
                client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
                writer.start()
                for _ in range(25):
                    control_started.clear()
                    commit_attempted.clear()
                    commit_completed.clear()
                    status = client.get("/api/v1/review-preparation")
                    self.assertEqual(status.status_code, 200, status.text)
                    self.assertTrue(control_started.is_set())
                    self.assertTrue(commit_attempted.is_set())
                    payload = status.json()
                    self.assertIn(
                        (payload["paused"], payload["libraries"][0]["state"]),
                        ((True, "queued"), (False, "discovering")),
                    )
                    self.assertEqual(payload["libraries"][0]["connection"], "primary")
                    self.assertEqual(payload["libraries"][0]["library"], "fiction")
                    self.assertTrue(commit_completed.wait(5))
            finally:
                release_writer.set()
                writer.join(5)
                client.close()
                repository.close()
            self.assertFalse(writer.is_alive())
            self.assertEqual(writer_errors, [])
            self.assertEqual(trace_errors, [])


if __name__ == "__main__":
    unittest.main()
