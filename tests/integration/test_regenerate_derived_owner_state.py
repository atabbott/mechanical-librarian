"""The owner's state across `regenerate-derived` (ADR-0108, #2040).

Each proof makes its store through the product's own writers, runs the real
command over it as an upgrade leaves it (the ledger names another derived
version) and reads the result through the real readers.
"""

from __future__ import annotations

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
from tests.integration.test_cover_rules_activation import _selections, _service
from tests.support.field_effects import CAPABILITY, Clock
from tests.support.ledger_partition import opens, rewrite
from tests.support.owner_field_record import assert_record_recomputes, request_resume
from tests.support.prepared_library import accept, prepared


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


def _choose_cover(prepared: Any, candidate: dict[str, Any]) -> HumanDecision:
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
                idempotency_key="synthetic-owner-cover-0001",
                occurred_at=11,
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
