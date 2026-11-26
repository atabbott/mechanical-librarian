"""The question check end to end (ADR-0108, "The question check"; #2040 packet 7).

Each proof prepares one work through the real pipeline and store, records
the owner's answer through the real command, and reads the work again: within
a release, through a later scan, or after an upgrade, by stopping the store,
running the real `regenerate-derived` over it and starting it again
(`regenerate`). Writes go through the real scheduler and the guarded writer
to the fake library. Every proof ends by recomputing the record from the
ledger (`assert_record_recomputes`), and the autouse `record_recomputes`
checks every store the test left, the restore point included.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mechanical_librarian.adapters.persistence import decisions
from mechanical_librarian.adapters.persistence.regeneration import (
    SQLiteRegenerationStore,
)
from mechanical_librarian.application.regenerate_derived import regenerate_derived
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.interfaces.cli import regenerate as cli
from tests.integration.test_owner_field_record import (
    _authors,
    _fields,
    _write_all,
    _writes,
)
from tests.support.author_evidence import author_report
from tests.support.ledger_partition import rewrite
from tests.support.owner_field_record import (
    assert_record_recomputes,
    record_recomputes,  # noqa: F401
    request_resume,
    resume,
)
from tests.support.owner_values import OwnerStore
from tests.support.prepared_library import (
    PRODUCER,
    Library,
    accept,
    applier,
    decided_after_the_run,
    planned,
    prepared,
    regenerate,
)
from tests.unit.test_decision_question import _DECORATED, _title_report
from tests.unit.test_owner_answer_holds import _activate, _profile


def _outcomes(library: Library) -> list[tuple[str, str, str | None]]:
    """Every check outcome: the answer, the outcome and the carried row."""
    return [
        (str(answer), str(outcome), carried)
        for answer, outcome, carried in library.root._connection.execute(
            "SELECT answer_id,outcome,carried_decision_id FROM carry_outcomes "
            "ORDER BY checked_at,review_case_id"
        )
    ]


def _record(library: Library) -> tuple[object, ...]:
    """The record's authors row: its answer, carry eligibility and open
    Resume request."""
    row = library.root._connection.execute(
        "SELECT answer_id,carry_eligible,open_resume_id FROM owner_field_answers "
        "WHERE destination='authors'"
    ).fetchone()
    assert row is not None
    return tuple(row)


def _decision(connection: sqlite3.Connection, decision_id: str) -> HumanDecision:
    (document,) = connection.execute(
        "SELECT document FROM human_decisions WHERE decision_id=?", (decision_id,)
    ).fetchone()
    return HumanDecision.from_json(document)


def _carried_rows(library: Library) -> int:
    (count,) = library.root._connection.execute(
        "SELECT count(*) FROM human_decisions WHERE carried_from IS NOT NULL"
    ).fetchone()
    return int(count)


def _reread(library: Library, at: int) -> None:
    """A later scan within the release that reads the work with an unrelated
    field changed elsewhere, so the work has a new case that asks the
    authors question it asked before, unless the test changed it."""
    held = library.held
    assert held is not None
    held.write(TargetDestination.PUBLISHER, f"Publisher read at {at}")
    held.revision = at
    library.root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, at)
    library.now[0] = at + 1
    library.drain()


def _read_again(library: Library, across: str, at: int) -> None:
    if across == "regeneration":
        regenerate(library, at)
    else:
        _reread(library, at)


def _schedule_with_consent(
    library: Library, tmp_path: Path, at: int, writes: list[str]
) -> None:
    """Consent to authors writes and let a scheduler that starts then plan
    and write whatever is pending, recording each write in `writes`."""
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), at)
    scheduler, service, target = applier(library, tmp_path)
    _writes(target, writes)
    _write_all(library, scheduler, service, target, at + 1)


_ENTERED = ("Owner Named Author",)


def _enter(library: Library, key: str, at: int) -> HumanDecision:
    """The owner enters authors of their own on the current case."""
    case = library.case()
    proposal = next(
        value for value in case.proposals if value["destination"] == "authors"
    )
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome="owner_entered",
        selected_value={"kind": "list", "value": list(_ENTERED)},
        idempotency_key=key,
        occurred_at=at,
    )


def _automatic_waits(library: Library) -> None:
    """Automatic decided the field again on the current case, and the record
    keeps it from planning (ADR-0108, Readers)."""
    current = [
        decision_id
        for decision_id in _authors(library)
        if library.root._connection.execute(
            "SELECT 1 FROM automatic_decisions WHERE decision_id=? "
            "AND review_case_id=?",
            (decision_id, library.case_id()),
        ).fetchone()
    ]
    assert current, "Automatic decided nothing on the current case"
    assert not set(current) & set(library.pending()), "Automatic's decision plans"


# --- Carry once after consent --------------------------------------------------


def test_an_unwritten_answer_is_carried_and_written_once_after_consent(
    tmp_path: Path,
) -> None:
    """An unwritten owner-entered value is carried to the case the upgrade
    makes with its own application: it plans nothing until consent, and is then written
    once, as the carried row, never as its original. After the next upgrade
    the written answer is carried `settled`: the case asks nothing and
    nothing is written again. The expected target there is the value
    written, not the one seen (negative control: expected target = seen
    target)."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        owner = _enter(library, "owner-enters-authors", 11)
        library.root.record_human_decision(owner)
        first = library.case_id()

        regenerate(library, 20)

        second = library.case_id()
        assert second != first
        ((answer, outcome, carried_id),) = _outcomes(library)
        assert (answer, outcome) == (owner.decision_id, "carried")
        assert carried_id is not None
        carried = _decision(library.root._connection, carried_id)
        assert (
            carried.carried_from,
            carried.review_case_id,
            carried.outcome,
            carried.application,
        ) == (owner.decision_id, second, "owner_entered", "apply")
        assert _record(library) == (carried_id, 1, None)
        field = _fields(library)["authors"]
        assert (field["classification_cause"], field["needs_attention"]) == (
            "human_decided",
            False,
        )
        assert library.pending() == [carried_id]
        writes: list[str] = []
        scheduler, service, target = applier(library, tmp_path)
        _writes(target, writes)
        _write_all(library, scheduler, service, target, 30)
        assert writes == []

        _schedule_with_consent(library, tmp_path, 31, writes)

        assert writes == [carried_id]
        assert all(decided_after_the_run(library, written) for written in writes)
        assert owner.decision_id not in planned(library)
        assert_record_recomputes(library.root._connection)
        # The library now holds the value written.
        assert library.held is not None
        library.held.write(TargetDestination.AUTHORS, _ENTERED)

        regenerate(library, 40)

        assert library.case_id() not in {first, second}
        assert [outcome for _, outcome, _ in _outcomes(library)] == [
            "carried",
            "carried",
        ]
        (_, _, settled_id) = _outcomes(library)[-1]
        assert settled_id is not None
        settled = _decision(library.root._connection, settled_id)
        assert (settled.carried_from, settled.application) == (
            owner.decision_id,
            "settled",
        )
        assert _fields(library)["authors"]["needs_attention"] is False
        assert library.pending() == []
        _schedule_with_consent(library, tmp_path, 41, writes)
        assert writes == [carried_id]
        assert_record_recomputes(library.root._connection)


# --- Asked again on a changed question or target --------------------------------


def _change(library: Library, tmp_path: Path, change: str) -> None:
    held = library.held
    assert held is not None
    if change == "target":
        held.write(TargetDestination.AUTHORS, ("Someone Else",))
    else:
        # The providers now name other authors: the offered choices differ.
        held.providers = author_report(tmp_path / "other", "long").providers


@pytest.mark.parametrize(
    "across", ("reread", "regeneration"), ids=("reread", "regeneration")
)
@pytest.mark.parametrize(
    ("change", "expected"),
    (("target", "target_changed"), ("question", "question_changed")),
    ids=("target", "question"),
)
def test_a_changed_question_or_target_asks_the_field_again(
    tmp_path: Path, across: str, change: str, expected: str
) -> None:
    """Steps 3 and 5: the library's value changed elsewhere, or the offered
    choices did, so the later case does not ask the question the owner
    answered: nothing is carried, the record keeps the answer, the field is
    asked (on the case page and, within the release, in Attention), and
    neither the answer nor Automatic's new decision plans or writes
    (negative controls: the Attention clause; the automatic arm's record
    clause)."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        library.root.record_human_decision(owner)
        first = library.case_id()
        _change(library, tmp_path, change)

        _read_again(library, across, 20)

        assert library.case_id() != first
        assert _outcomes(library) == [(owner.decision_id, expected, None)]
        assert _carried_rows(library) == 0
        assert _record(library) == (owner.decision_id, 1, None)
        assert _fields(library)["authors"]["needs_attention"] is True
        if across == "reread":
            # After an upgrade the Attention list refuses every case (the
            # H2-D report's product defect), so only the page is read there.
            assert (library.case_id(), "authors") in library.attention()
        _automatic_waits(library)
        writes: list[str] = []
        _schedule_with_consent(library, tmp_path, 30, writes)
        assert writes == []
        assert library.pending() == []
        assert_record_recomputes(library.root._connection)


# --- Retired --------------------------------------------------------------------


def test_a_retired_answer_is_asked_again_after_the_upgrade(tmp_path: Path) -> None:
    """`--retire-pending` retires the answer whose write was still waiting:
    the plan closes, the record keeps the answer without carry eligibility,
    and the case the upgrade makes asks the field again (step 1): nothing
    is carried, and neither the answer nor Automatic plans or writes."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        library.root.record_human_decision(owner)
        library.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("authors",), 12
        )
        scheduler, _, _ = applier(library, tmp_path)
        library.now[0] = 13
        scheduler.schedule()
        (operation,) = planned(library).values()
        plan_id = operation.plan.plan_id
        assert operation.plan.authority.decision_id == owner.decision_id

        lines = regenerate(library, 20, retire_pending=True)

        assert lines[-1] == cli.REGENERATED, lines
        state = library.root.get_field_effect(plan_id)
        assert (state.state, state.failure) == ("superseded", "authority_superseded")
        assert library.root._connection.execute(
            "SELECT kind,answer_id FROM owner_field_events ORDER BY rowid"
        ).fetchall() == [("retired", owner.decision_id)]
        assert _outcomes(library) == [(owner.decision_id, "asked_again", None)]
        assert _record(library) == (owner.decision_id, 0, None)
        assert _fields(library)["authors"]["needs_attention"] is True
        _automatic_waits(library)
        writes: list[str] = []
        _schedule_with_consent(library, tmp_path, 30, writes)
        assert writes == []
        assert_record_recomputes(library.root._connection)


# --- Step 0: a failed Resume, then a re-read --------------------------------------


@pytest.mark.parametrize(
    "across", ("reread", "regeneration"), ids=("reread", "regeneration")
)
def test_a_failed_resume_then_a_reread_closes_it_and_asks_the_field(
    tmp_path: Path, across: str
) -> None:
    """Step 0 (ADR-0108, the design thread's ruling of 2026-11-24): Resume,
    then a failed scan, then a same-question re-read plans nothing, shows
    the field as asked and closes the request superseded naming the answer;
    the record loses carry eligibility (negative control: without step 0)."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        library.root.record_human_decision(owner)
        request = request_resume(library.root, library.case_id(), "authors", 12)
        assert request is not None
        (scan,) = library.root._connection.execute(
            "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
        ).fetchone()
        # As a scan that ends with failures settles it.
        assert library.root._fail_open_resumes_for_scan(scan, 13) == 1
        library.root._connection.commit()

        _read_again(library, across, 20)

        assert _outcomes(library) == [(owner.decision_id, "asked_again", None)]
        assert library.root._connection.execute(
            "SELECT kind,answer_id FROM owner_field_events WHERE request_id=? "
            "ORDER BY rowid",
            (request,),
        ).fetchall() == [
            ("resume_failed", None),
            ("resume_superseded", owner.decision_id),
        ]
        assert _record(library) == (owner.decision_id, 0, None)
        assert _carried_rows(library) == 0
        field = _fields(library)["authors"]
        assert field["needs_attention"] is True
        # The failed request is gone, and Resume is offered again.
        assert (field["manual_return_requested"], field["manual_return_failed"]) == (
            False,
            False,
        )
        assert library.pending() == []
        writes: list[str] = []
        _schedule_with_consent(library, tmp_path, 30, writes)
        assert writes == []
        assert request_resume(library.root, library.case_id(), "authors", 32)
        assert_record_recomputes(library.root._connection)


# --- Resume over a carried answer -------------------------------------------------


def test_resume_over_a_carried_unwritten_answer_writes_only_automatics(
    tmp_path: Path,
) -> None:
    """An unwritten answer carried by the upgrade, then Resume before any
    consent: the record lets the carried row go, so neither it nor its
    original is ever planned, and Automatic's decision on the case is
    written once (negative control: the owner arm's record test)."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        library.root.record_human_decision(owner)
        regenerate(library, 20)
        ((_, _, carried_id),) = _outcomes(library)
        assert carried_id is not None

        assert resume(library.root, library.case_id(), "authors", 30)

        assert library.root._connection.execute(
            "SELECT count(*) FROM owner_field_answers WHERE destination='authors'"
        ).fetchone() == (0,)
        (automatic,) = (
            decision_id
            for decision_id in _authors(library)
            if library.root._connection.execute(
                "SELECT 1 FROM automatic_decisions WHERE decision_id=? "
                "AND review_case_id=?",
                (decision_id, library.case_id()),
            ).fetchone()
        )
        assert library.pending() == [automatic]
        writes: list[str] = []
        _schedule_with_consent(library, tmp_path, 31, writes)
        _schedule_with_consent(library, tmp_path, 33, writes)
        assert writes == [automatic]
        assert all(decided_after_the_run(library, written) for written in writes)
        assert not {carried_id, owner.decision_id} & set(planned(library))
        assert_record_recomputes(library.root._connection)


# --- Retry after a kill or a store refusal ------------------------------------------


class _Killed(BaseException):
    """The process stops in the middle of the projection's transaction."""


@pytest.mark.parametrize(
    "stop", ("kill", "store_refusal"), ids=("kill", "store_refusal")
)
def test_a_check_a_kill_or_the_store_stopped_is_retried_at_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    """A check that never finished is retried at startup: a kill rolls the
    projection back, mint and carry together, and the next start's recovery
    runs the projection again; a store refusal of the carried row writes no
    row and leaves the case published, and the next start's retry carries
    it. Either way the answer is carried exactly once."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        library.root.record_human_decision(owner)

        def refused(*_arguments: Any) -> str:
            if stop == "kill":
                raise _Killed
            raise sqlite3.OperationalError("refused")

        with monkeypatch.context() as patch:
            patch.setattr(
                decisions.SQLiteDecisionRepository, "record_carried_decision", refused
            )
            if stop == "kill":
                with pytest.raises(_Killed):
                    regenerate(library, 20)
            else:
                regenerate(library, 20)
                assert _outcomes(library) == []
                assert _record(library) == (owner.decision_id, 1, None)
            assert _carried_rows(library) == 0

        library.close()
        library.reopen()
        library.pipeline.recover()
        library.drain()

        ((answer, outcome, carried_id),) = _outcomes(library)
        assert (answer, outcome) == (owner.decision_id, "carried")
        assert _carried_rows(library) == 1
        assert carried_id is not None
        carried = _decision(library.root._connection, carried_id)
        assert carried.review_case_id == library.case_id()
        assert library.pending() == [carried_id]
        assert_record_recomputes(library.root._connection)


# --- Amendment 1, R3: the decoration tidy, across upgrades ---------------------------


def _not_configured() -> Any:
    raise FileNotFoundError


def _regenerate_store(store: OwnerStore) -> OwnerStore:
    """Stop an `OwnerStore`, run `regenerate-derived` over its file as an
    upgrade leaves it, and open it again under the same policy."""
    resolved, ticks = store.resolved, store.ticks
    store.close()
    path = store.root / "owner.sqlite"
    assert rewrite(path, "UPDATE ledger_meta SET derived_version=2") == 1
    lines: list[str] = []
    outcome = regenerate_derived(
        SQLiteRegenerationStore(path, lambda: 1_700_000_000 + ticks),
        retire_pending=False,
        configuration=_not_configured,
    )
    assert cli.render(outcome, lines.append) == 0, lines
    reopened = OwnerStore(store.root, None)
    reopened.resolved, reopened.ticks = resolved, ticks
    return reopened


def test_an_answer_on_the_tidied_case_is_asked_once_across_upgrades(
    tmp_path: Path,
) -> None:
    """Amendment 1, R3, the regeneration form: the owner writes the consensus
    decoration tidy; the case that reads it back offers the decorated title
    again, an expected non-carry (T11), so the field is asked once there.
    The owner's answer on that case is carried at the next upgrade, and at
    the one after, so it is not asked at every upgrade."""
    before = _title_report(_DECORATED, _DECORATED, ("Plain Work", "Plain Work"))
    after = _title_report("Plain Work", _DECORATED, ("Plain Work", "Plain Work"))
    store = OwnerStore(tmp_path, None)
    store.repository.enter_shadow_preview(1, ())
    _activate(
        store,
        _profile("First policy", "ask"),
        MetadataPolicyProfile.builtin(),
        after.target.library,
    )
    store.publish(before)
    tidy = store.decide("accept", {"kind": "text", "value": "Plain Work"})
    store.write(tidy)
    tidied = store.publish(after).review_case_id

    def outcomes() -> list[tuple[str, str, str | None]]:
        return [
            (str(case), str(outcome), carried)
            for case, outcome, carried in store.repository._connection.execute(
                "SELECT review_case_id,outcome,carried_decision_id FROM "
                "carry_outcomes ORDER BY checked_at"
            )
        ]

    assert outcomes() == [(tidied, "question_changed", None)]
    kept = store.decide("keep_current")
    for upgrade in (1, 2):
        store = _regenerate_store(store)
        case = store.publish(after).review_case_id
        assert case != tidied
        assert len(outcomes()) == 1 + upgrade
        checked, outcome, carried_id = outcomes()[-1]
        assert (checked, outcome) == (case, "carried")
        assert carried_id is not None
        carried = _decision(store.repository._connection, carried_id)
        assert (carried.carried_from, carried.application) == (
            kept.decision_id,
            "settled",
        )
        assert_record_recomputes(store.repository._connection)
    store.close()
