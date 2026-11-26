"""The owner's state across `regenerate-derived` (ADR-0108, #2040).

Each proof makes its store through the product's own writers, runs the real
command over it as an upgrade leaves it (the ledger names another derived
version) and reads the result through the real readers.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import PersistedEffectConsent
from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.regeneration import (
    SQLiteRegenerationStore,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.regenerate_derived import (
    Outcome,
    Reason,
    regenerate_derived,
)
from mechanical_librarian.domain import cover_qualification_policy
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.cli import regenerate as cli
from tests.integration.test_cover_rules_activation import (
    _prepared as _cover_prepared,
)
from tests.integration.test_cover_rules_activation import (
    _reprepare,
    _selections,
    _service,
)
from tests.integration.test_cover_taste_automatic import _republished
from tests.integration.test_owner_field_record import _write_all, _writes
from tests.support.field_effects import CAPABILITY, Clock
from tests.support.ledger_partition import opens, rewrite
from tests.support.owner_field_record import (
    assert_record_recomputes,
    complete_resume,
    record_recomputes,  # noqa: F401
    request_resume,
    resume,
)
from tests.support.prepared_library import (
    Library,
    accept,
    applier,
    decided_after_the_run,
    planned,
    prepared,
    regenerate,
)


def _store_mode() -> Any:
    raise FileNotFoundError


def _another_build(path: Path) -> None:
    """The ledger names a derived partition this build did not make, as an
    upgrade leaves it."""
    assert rewrite(path, "UPDATE ledger_meta SET derived_version=2") == 1


def _run(path: Path, *, retire_pending: bool = False) -> tuple[int, list[str], Outcome]:
    outcome = regenerate_derived(
        SQLiteRegenerationStore(path, lambda: 1_700_000_000),
        retire_pending=retire_pending,
        configuration=_store_mode,
    )
    out = io.StringIO()
    status = cli.render(outcome, out.write)
    return status, out.getvalue().splitlines(), outcome


def _rows(path: Path, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# --- Resume, through the page's own request -----------------------------------


def test_an_open_resume_refuses_until_the_flag_closes_it_over_its_answer(
    tmp_path: Path,
) -> None:
    """A Resume the owner asked through the real request blocks the command;
    with `--retire-pending` it closes `resume_superseded` naming the answer
    it covered, the answer stays the record's, no longer carried, and the
    record still equals the ledger's fold."""
    with prepared(tmp_path, automatic=True) as library:
        root = library.root
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        request = request_resume(root, library.case_id(), "authors", 12)
        assert request is not None
        target = library.case().target
        root.close()
        path = tmp_path / "application.sqlite3"
        _another_build(path)
        before = path.read_bytes()
        status, lines, outcome = _run(path)
        assert status == 1
        assert [item.reason for item in outcome.items] == [Reason.RESUME]
        assert lines == [
            cli.GATE_HEADING,
            f"  {target['connection']}/{target['library']}, work {target['item']}, "
            f"Authors: {cli.REASONS[Reason.RESUME]} {cli.NO_CAUSE}",
        ]
        assert path.read_bytes() == before
        assert not (tmp_path / "regeneration-backup").exists()
        status, lines, _ = _run(path, retire_pending=True)
        assert (status, lines[-1]) == (0, cli.REGENERATED), lines
        assert opens(path)
        assert _rows(
            path,
            "SELECT kind,request_id,answer_id FROM owner_field_events ORDER BY rowid",
        ) == [
            ("resume_requested", None, owner.decision_id),
            ("resume_superseded", request, owner.decision_id),
        ]
        assert _rows(
            path,
            "SELECT answer_id,open_resume_id,carry_eligible FROM owner_field_answers",
        ) == [(owner.decision_id, None, 0)]
        connection = sqlite3.connect(path)
        try:
            assert_record_recomputes(connection)
            (counts,) = connection.execute(
                "SELECT counts_document FROM derived_regenerations"
            ).fetchone()
        finally:
            connection.close()
        assert json.loads(counts)["resume_requests_closed"] == 1


# --- Covers (design 14.5 case 3) ----------------------------------------------


def _write_owner_cover(prepared: Any, decision: HumanDecision) -> None:
    """Plan and write the owner's cover choice through the guarded writer, as
    the scheduler does, over the fake library."""
    repository = prepared.repository
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
    repository.grant_field_effect_consents(
        snapshot.connection, (snapshot.library,), (snapshot.destination,), 20
    )
    originals = CoverArtifactStore(prepared.root / "artifacts")
    recovery = PrivateCoverRecoveryStore(
        prepared.root / "recovery",
        lambda plan: (
            None
            if plan.pre_value is None
            else originals.read_original_digest(
                str(plan.pre_value).removeprefix("cover_")
            )
        ),
    )
    target = FakeTargetService()
    application = GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver(CAPABILITY),
        authority,
        FakeDependencyCurrentness(),
        PersistedEffectConsent(repository),
        recovery,
        FakeForwardReevaluation(),
        Clock(),
    )
    operation = application.plan(
        decision,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination=snapshot.destination,
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    target.seed(operation.plan)
    assert application.apply(operation.plan.plan_id).state == "verified"


def _choose_cover(
    prepared: Any,
    candidate: dict[str, Any],
    key: str = "synthetic-owner-cover-0001",
    at: int = 11,
) -> HumanDecision:
    """The owner chooses `candidate` on the work's case, through the real
    decision command."""
    repository = prepared.repository
    case_id = prepared.review_case_id
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(row for row in case.proposals if row["destination"] == "cover")
    document = repository.current_cover_consideration_document(case_id)
    assert document is not None
    return HumanDecision.from_json(
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=str(proposal["proposal_id"]),
                target_fingerprint=case.target_fingerprint,
                destination="cover",
                actor="owner",
                outcome="accept",
                selected_value={"kind": "cover", "value": candidate["artifact_id"]},
                idempotency_key=key,
                occurred_at=at,
            ),
            cover_consideration_id=json.loads(document)["consideration_id"],
            cover_confirmation={
                "expected_selection_id": None,
                "confirm_unverified_match": True,
            },
        )
    )


def test_a_written_cover_keeps_its_preview_across_two_regenerations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design 14.5 case 3, the written choice: the owner writes cover A. The
    sweep keeps every file while the restore point is kept; once it is
    discarded, A's original and preview still read, because the ledger
    references them, and still do after a second regeneration whose copy is
    discarded too. Files only derived rows referenced are removed."""
    monkeypatch.setattr(
        cover_qualification_policy, "MEASURED_REPLACEMENT_QUALIFICATION_SHA256", None
    )
    with _cover_prepared(
        tmp_path, duplicate_size=(1200, 1800), candidate_size=(250, 375)
    ) as prepared:
        repository = prepared.repository
        _service(prepared)
        document = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert document is not None
        chosen = next(
            candidate
            for candidate in json.loads(document)["candidates"]
            if not candidate["incumbent"] and candidate["selectable"]
        )
        _write_owner_cover(prepared, _choose_cover(prepared, chosen))
        (selection,) = _selections(repository)
        repository.close()
    path = tmp_path / "reports.sqlite"
    artifacts = CoverArtifactStore(tmp_path / "artifacts")
    files = {entry.name for entry in (tmp_path / "artifacts").iterdir()}
    original, preview = selection["original_sha256"], selection["preview_sha256"]
    assert {original, preview} <= files

    def sweep() -> set[str]:
        reopened = open_repository(path, create=False)
        try:
            artifacts.recover_unreferenced(reopened.artifact_is_referenced)
        finally:
            reopened.close()
        return {entry.name for entry in (tmp_path / "artifacts").iterdir()}

    _another_build(path)
    status, lines, _ = _run(path)
    assert (status, lines[-1]) == (0, cli.REGENERATED), lines
    assert _rows(path, "SELECT selection_id FROM owner_cover_pointers") == [
        (selection["selection_id"],)
    ]
    assert sweep() == files
    shutil.rmtree(tmp_path / "regeneration-backup")
    kept = sweep()
    assert kept == {original, preview}
    assert artifacts.read_preview_digest(preview)
    assert artifacts.read_original_digest(original)
    _another_build(path)
    status, lines, _ = _run(path)
    assert (status, lines[-1]) == (0, cli.REGENERATED), lines
    shutil.rmtree(tmp_path / "regeneration-backup")
    assert sweep() == kept
    assert artifacts.read_preview_digest(preview)
    with pytest.raises(MetadataReportError):
        artifacts.read_preview_digest(next(iter(files - kept)))


def test_a_written_then_released_cover_keeps_its_bytes_and_the_unwritten_choice_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design 14.5 case 3(b), through cover Resume: the owner writes cover A,
    Resume hands the cover back (the pointer goes), the work is read again as
    a new case, and the owner chooses B there, which is not written. The command reports B's work as keeping its
    written cover and needs the flag; with it, the pointer names A again.
    After that regeneration and a second one, each with its restore point
    discarded, A's original and preview still read, because the ledger
    references them (B's files are kept too: the record's answer names B);
    nothing is planned after either run."""
    monkeypatch.setattr(
        cover_qualification_policy, "MEASURED_REPLACEMENT_QUALIFICATION_SHA256", None
    )
    with _cover_prepared(
        tmp_path, duplicate_size=(1200, 1800), candidate_size=(250, 375)
    ) as prepared:
        repository = prepared.repository
        service = _service(prepared)
        document = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert document is not None
        first, second = (
            candidate
            for candidate in json.loads(document)["candidates"]
            if not candidate["incumbent"] and candidate["selectable"]
        )
        _write_owner_cover(prepared, _choose_cover(prepared, first))
        (written,) = _selections(repository)
        request = request_resume(repository, prepared.review_case_id, "cover", 30)
        assert request is not None
        assert complete_resume(repository, request, 31)
        assert _selections(repository) == []
        case_id, successor = _republished(repository, prepared.report)
        later = dataclasses.replace(prepared, review_case_id=case_id, report=successor)
        _reprepare(service, later)
        unwritten = _choose_cover(later, second, "synthetic-owner-cover-0002", 32)
        (chosen,) = _selections(repository)
        assert chosen["decision_id"] == unwritten.decision_id
        target = prepared.report.target.to_data()
        repository.close()
    path = tmp_path / "reports.sqlite"
    artifacts = CoverArtifactStore(tmp_path / "artifacts")
    files = {entry.name for entry in (tmp_path / "artifacts").iterdir()}
    original, preview = written["original_sha256"], written["preview_sha256"]
    assert {original, preview} <= files

    def sweep() -> set[str]:
        reopened = open_repository(path, create=False)
        try:
            artifacts.recover_unreferenced(reopened.artifact_is_referenced)
        finally:
            reopened.close()
        return {entry.name for entry in (tmp_path / "artifacts").iterdir()}

    _another_build(path)
    before = path.read_bytes()
    status, lines, _ = _run(path)
    assert status == 1
    assert "    " + cli.COVER_KEPT.format(item_id=target["item"]) in lines, lines
    assert path.read_bytes() == before
    status, lines, _ = _run(path, retire_pending=True)
    assert (status, lines[-1]) == (0, cli.REGENERATED), lines
    assert _rows(path, "SELECT selection_id FROM owner_cover_pointers") == [
        (written["selection_id"],)
    ]
    assert sweep() == files
    shutil.rmtree(tmp_path / "regeneration-backup")
    kept = sweep()
    # A's files, which the ledger references as the written choice, and B's,
    # behind the record's answer.
    assert kept == {
        original,
        preview,
        chosen["original_sha256"],
        chosen["preview_sha256"],
    }
    assert artifacts.read_preview_digest(preview)
    assert artifacts.read_original_digest(original)
    _another_build(path)
    status, lines, _ = _run(path)
    assert (status, lines[-1]) == (0, cli.REGENERATED), lines
    shutil.rmtree(tmp_path / "regeneration-backup")
    assert sweep() == kept
    assert artifacts.read_preview_digest(preview)
    assert artifacts.read_original_digest(original)
    # Write discipline (case 6): no plan was made after either run.
    assert _rows(path, "SELECT count(*) FROM field_effects") == [(1,)]


# --- Kept rows, write discipline and the epoch control (design 14.5, 5-7) ------


def _ledger_digests(path: Path) -> dict[str, str]:
    """A digest of every ledger table's rows, in primary-key order."""
    connection = sqlite3.connect(path)
    try:
        return {
            table: hashlib.sha256(
                repr(
                    sorted(connection.execute(f"SELECT * FROM {table}").fetchall())
                ).encode()
            ).hexdigest()
            for table in sorted(schema.LEDGER_TABLES)
        }
    finally:
        connection.close()


# The rows the run writes (ADR-0108, "The offline command", 6.1-6.6): the
# epoch, version and digest, its audit row, and every authority it revokes.
_WRITTEN_BY_THE_RUN = frozenset(
    {"ledger_meta", "derived_regenerations", "field_effect_authorities"}
)


def test_the_run_keeps_every_ledger_row_it_does_not_name(tmp_path: Path) -> None:
    """Design 14.5 case 5: a store with consents, a written answer (its plan,
    events and actions), a Resume answered and completed, policy rows and the
    record keeps every ledger row across the run, except the rows the run
    names: the epoch and version, its audit row, and the authorities it
    revokes, which keep their decisions and are no longer current."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
        scheduler, service, target = applier(library, tmp_path)
        writes = _writes(target)
        _write_all(library, scheduler, service, target, 13)
        assert writes == [owner.decision_id]
        assert resume(root, library.case_id(), "authors", 14)
        library.close()
    path = tmp_path / "application.sqlite3"
    _another_build(path)
    before = _ledger_digests(path)
    authorities = _rows(path, "SELECT decision_id FROM field_effect_authorities")
    assert authorities
    for table in (
        "field_effect_consents",
        "field_effects",
        "field_effect_events",
        "human_decisions",
        "owner_field_events",
        "metadata_policy_state",
    ):
        assert _rows(path, f"SELECT count(*) FROM {table}") != [(0,)], table

    status, lines, _ = _run(path)

    assert (status, lines[-1]) == (0, cli.REGENERATED), lines
    after = _ledger_digests(path)
    assert {
        table for table in before if before[table] != after[table]
    } <= _WRITTEN_BY_THE_RUN
    assert {table: before[table] for table in before.keys() - _WRITTEN_BY_THE_RUN} == {
        table: after[table] for table in after.keys() - _WRITTEN_BY_THE_RUN
    }
    assert (
        _rows(path, "SELECT decision_id FROM field_effect_authorities") == authorities
    )
    assert _rows(
        path, "SELECT count(*) FROM field_effect_authorities WHERE current=1"
    ) == [(0,)]
    assert opens(path)


@pytest.mark.parametrize(
    "epoch", ("increments", "never_increments"), ids=("increments", "never_increments")
)
def test_the_recurring_case_writes_only_what_was_decided_after_the_run(
    tmp_path: Path, epoch: str
) -> None:
    """Design 14.5 cases 1, 6 and 7. The work reads the same report before
    and after the run. The owner's answer was not written: no consent at
    the run. After it, the case the upgrade makes differs, the old decision
    is never pending or planned, the question check carries it, and once
    consent is given the one write the fake library receives is the carried
    row's, recorded after the run.

    Case 7, the negative control: with an epoch that never increments (a
    test-side rewrite of `ledger_meta` reaching every real reader, contract
    S9) the same case comes back, nothing is checked and the old decision is
    pending, so the proof fails on its pending listing. The id comparison is
    skipped in that mode, so the control cannot pass for the wrong reason."""
    frozen = epoch == "never_increments"
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        library.root.record_human_decision(owner)
        first, report = library.case_id(), library.case().report_id

        regenerate(library, 20, epoch_never_increments=frozen)

        assert library.case().report_id == report
        if not frozen:
            assert library.case_id() != first

        def proof(library: Library) -> None:
            pending = library.pending()
            assert owner.decision_id not in pending, "the old decision is pending"
            (outcome,) = library.root._connection.execute(
                "SELECT outcome,carried_decision_id FROM carry_outcomes"
            ).fetchall()
            assert outcome == ("carried", pending[0]) and len(pending) == 1
            writes: list[str] = []
            library.root.grant_field_effect_consents(
                "primary", ("ebooks",), ("authors",), 30
            )
            scheduler, service, target = applier(library, tmp_path)
            _writes(target, writes)
            _write_all(library, scheduler, service, target, 31)
            assert writes == pending
            assert all(decided_after_the_run(library, written) for written in writes)
            assert owner.decision_id not in planned(library)

        if frozen:
            assert library.case_id() == first
            with pytest.raises(AssertionError, match="the old decision is pending"):
                proof(library)
        else:
            proof(library)
        assert_record_recomputes(library.root._connection)
