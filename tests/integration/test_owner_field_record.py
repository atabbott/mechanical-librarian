"""The record of the owner's answers decides what Automatic records, plans and
writes, and Resume (ADR-0108, #2038).

Each proof drives the real store through the prepared-library harness and
ends by recomputing the record from the ledger (`assert_record_recomputes`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.domain import cover_qualification_policy
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.review_case import ReviewCase
from tests.integration.test_cover_rules_activation import (
    _planned as _cover_planned,
)
from tests.integration.test_cover_rules_activation import (
    _prepared as _cover_prepared,
)
from tests.integration.test_cover_rules_activation import (
    _reprepare,
    _selections,
    _service,
)
from tests.integration.test_cover_taste_automatic import _keep_cover
from tests.support.audiobook_covers import SYNTHETIC_MEASURED_RECORD
from tests.support.owner_field_record import (
    assert_record_recomputes,
    complete_resume,
    record_recomputes,  # noqa: F401
    request_resume,
    resume,
)
from tests.support.prepared_library import (
    PRODUCER,
    Library,
    accept,
    applier,
    planned,
    prepared,
)
from tests.support.prepared_library import library as library
from tests.support.prepared_library import preview_library as preview_library


def _confirm_automatic(library: Library, at: int) -> None:
    library.root.activate_qualified_automatic(at)
    library.now[0] = at + 1
    library.drain()


def _resume_case(root: Any, case_id: str, destination: str, at: int) -> None:
    """Resume the field of the current case and let its checks complete."""
    assert resume(root, case_id, destination, at)


def _resume(library: Library, destination: str, at: int) -> None:
    _resume_case(library.root, library.case_id(), destination, at)


def _plan_states(library: Library, decision_id: str) -> list[str]:
    return [
        str(state)
        for (state,) in library.root._connection.execute(
            "SELECT state FROM field_effects WHERE decision_id=? ORDER BY rowid",
            (decision_id,),
        )
    ]


def test_an_automatic_plan_the_record_holds_is_refused_at_authority_time(
    library: Library, tmp_path: Path
) -> None:
    """An automatic plan recorded before the owner answers writes nothing
    once the record holds the field: the answer revokes its authority, and
    the record refuses it at write time even where that authority stays
    current (here set current again by hand, the guard's only reach). After
    Resume completes, Automatic's decision is planned again."""
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 11)
    scheduler, _service, _target = applier(library, tmp_path)
    library.now[0] = 12
    scheduler.schedule()
    first = planned(library)[automatic.decision_id]
    assert first.state == "planned"

    root.record_human_decision(
        accept(library.case(), "authors", "owner-answers-authors", 13)
    )

    assert planned(library)[automatic.decision_id].state == "superseded"
    assert root.field_effect_authority_is_current(first.plan.authority) is False
    root._connection.execute(
        "UPDATE field_effect_authorities SET current=1,document=json_set(document,"
        "'$.current',json('true')) WHERE decision_id=?",
        (automatic.decision_id,),
    )
    root._connection.commit()
    assert root.field_effect_authority_is_current(first.plan.authority) is False
    assert automatic.decision_id not in library.pending()

    _resume(library, "authors", 20)

    assert root.field_effect_authority_is_current(first.plan.authority) is True
    assert automatic.decision_id in library.pending()
    library.now[0] = 21
    applier(library, tmp_path)[0].schedule()
    assert _plan_states(library, automatic.decision_id) == ["planned"]
    assert_record_recomputes(root._connection)


def test_an_automatic_cover_switch_the_record_holds_is_refused_at_authority_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic's planned cover switch writes nothing once the owner keeps
    the cover over it; after cover Resume completes, the stage's switch of
    the current comparison is admitted again and plans."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _cover_prepared(tmp_path) as prepared:
        repository = prepared.repository
        service = _service(prepared)
        _application, first, _target = _cover_planned(prepared)
        assert first.state == "planned"
        (switch,) = _selections(repository)

        _keep_cover(prepared, switch["selection_id"])

        assert repository.field_effect_authority_is_current(first.plan.authority) is (
            False
        )
        service.update_policy(CoverPolicy(prefer_edition=True))
        later = json.loads(_reprepare(service, prepared))
        assert [row["kind"] for row in _selections(repository)] == ["owner"]
        pending = [
            json.loads(document)["decision_id"]
            for document in repository.pending_field_effect_decision_documents(100)
        ]
        assert switch["decision_id"] not in pending

        _resume_case(repository, prepared.review_case_id, "cover", 20)

        (again,) = _selections(repository)
        assert again["kind"] == "automatic"
        assert again["consideration_id"] == later["consideration_id"]
        assert again["decision_id"] in [
            json.loads(document)["decision_id"]
            for document in repository.pending_field_effect_decision_documents(100)
        ]
        assert_record_recomputes(repository._connection)


def _authors(library: Library) -> list[str]:
    """Every automatic authors decision, in recorded order."""
    return [
        str(decision_id)
        for (decision_id,) in library.root._connection.execute(
            "SELECT decision_id FROM automatic_decisions WHERE destination='authors' "
            "ORDER BY rowid"
        )
    ]


def _writes(target: Any, writes: list[str] | None = None) -> list[str]:
    """Every decision the fake target is asked to write, in order."""
    recorded = [] if writes is None else writes
    apply = target.apply

    def recording(plan: Any) -> Any:
        recorded.append(plan.authority.decision_id)
        return apply(plan)

    target.apply = recording
    return recorded


def _write_all(
    library: Library, scheduler: Any, service: Any, target: Any, at: int
) -> None:
    """Plan what is pending and apply every planned plan to the fake target."""
    library.now[0] = at
    scheduler.schedule()
    for operation in planned(library).values():
        if library.root.get_field_effect(operation.plan.plan_id).state == "planned":
            target.seed(operation.plan)
            service.apply(operation.plan.plan_id)


def _read_back(library: Library, authors: tuple[str, ...], at: int) -> None:
    """The library holds `authors` now, and a scan reads the work again."""
    held = library.held
    assert held is not None
    held.write(TargetDestination.AUTHORS, authors)
    library.root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, at)
    library.now[0] = at + 1
    library.drain()


def _value(decision: HumanDecision) -> tuple[str, ...]:
    selected = decision.selected_value
    assert type(selected) is dict
    return tuple(str(name) for name in selected["value"])


@pytest.mark.parametrize(
    "read",
    ("written_back", "changed_elsewhere"),
    ids=("written_back", "changed_elsewhere"),
)
def test_an_answer_the_record_holds_is_never_written_over(
    tmp_path: Path, read: str
) -> None:
    """P1: the owner's answer is written, and the work is read back. However
    it reads (its write back or a change made elsewhere, then the same value
    on later scans), Automatic may decide the field again on the case it reads,
    but the record holds the answer, so nothing of Automatic's is planned or
    written to the target."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        first = library.automatic()
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
        scheduler, service, target = applier(library, tmp_path)
        writes = _writes(target)
        _write_all(library, scheduler, service, target, 13)
        assert writes == [owner.decision_id]

        value = _value(owner) if read == "written_back" else ("Someone Else",)
        for at in (14, 16, 18):
            _read_back(library, value, at)
            _write_all(library, scheduler, service, target, at + 1)

        assert library.case_id() != owner.review_case_id
        # Automatic decided the field again on the case the read made.
        assert [d for d in _authors(library) if d != first.decision_id]
        assert all(d not in library.pending() for d in _authors(library))
        assert writes == [owner.decision_id]
        assert set(planned(library)) == {owner.decision_id}
        assert_record_recomputes(root._connection)


def _record_row(root: Any, destination: str) -> tuple[object, ...] | None:
    row = root._connection.execute(
        "SELECT answer_id,open_resume_id FROM owner_field_answers "
        "WHERE item_id='item-1' AND destination=?",
        (destination,),
    ).fetchone()
    return None if row is None else tuple(row)


def test_resume_hands_the_field_back_and_automatic_writes_once(
    library: Library, tmp_path: Path
) -> None:
    """P3: the owner's answer is written once. Resume completes: the record
    lets the answer go, so it is never planned or written again, and
    Automatic's decision is planned and written once."""
    root = library.root
    automatic = library.automatic()
    owner = accept(library.case(), "authors", "owner-answers-authors", 11)
    root.record_human_decision(owner)
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = applier(library, tmp_path)
    writes = _writes(target)
    _write_all(library, scheduler, service, target, 13)
    assert writes == [owner.decision_id]

    _resume(library, "authors", 14)

    assert _record_row(root, "authors") is None
    assert library.pending() == [automatic.decision_id]
    # A scheduler that starts over, as completing in its own loop does.
    scheduler, service, target = applier(library, tmp_path)
    _writes(target, writes)
    for at in (15, 17, 19):
        _write_all(library, scheduler, service, target, at)
    assert writes == [owner.decision_id, automatic.decision_id]
    assert library.pending() == []
    assert_record_recomputes(root._connection)


@pytest.mark.parametrize("state", ("claimed", "planned"), ids=("claimed", "planned"))
def test_completion_waits_on_a_claimed_owner_write_and_revokes_a_planned_one(
    library: Library, tmp_path: Path, state: str
) -> None:
    """P3 variant: Resume's completion waits, completing nothing, while the
    owner's write it covers is claimed, applying or reconciling; a planned
    one it revokes, closing the plan superseded. Either way Automatic's
    decision is then the field's pending one, and the only one written."""
    root = library.root
    automatic = library.automatic()
    owner = accept(library.case(), "authors", "owner-answers-authors", 11)
    root.record_human_decision(owner)
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, _service, _target = applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = planned(library)[owner.decision_id].plan
    request = request_resume(root, library.case_id(), "authors", 14)
    assert request is not None

    if state == "claimed":
        root.claim_field_effect(plan.plan_id, "planned", "c" * 64, 15)
        assert not complete_resume(root, request, 16)
        root.transition_field_effect(plan.plan_id, "claimed", "applying", 17)
        assert not complete_resume(root, request, 18)
        assert _record_row(root, "authors") == (owner.decision_id, request)
        assert automatic.decision_id not in library.pending()
        root.transition_field_effect(plan.plan_id, "applying", "verified", 19)
        assert complete_resume(root, request, 20)
        assert root.get_field_effect(plan.plan_id).state == "verified"
    else:
        assert complete_resume(root, request, 16)
        assert root.get_field_effect(plan.plan_id).state == "superseded"
        assert root.field_effect_authority_is_current(plan.authority) is False

    assert _record_row(root, "authors") is None
    assert library.pending() == [automatic.decision_id]
    # Only Automatic's decision is then written, once.
    scheduler, service, target = applier(library, tmp_path)
    writes = _writes(target)
    for at in (21, 23, 25):
        _write_all(library, scheduler, service, target, at)
    assert writes == [automatic.decision_id]
    assert_record_recomputes(root._connection)


@pytest.mark.parametrize("plan", (False, True), ids=("unplanned", "planned"))
def test_resume_with_nothing_to_rearm_settles_nothing(
    preview_library: Library, tmp_path: Path, plan: bool
) -> None:
    """P3b: in Preview Automatic decides nothing, so Resume has nothing to
    re-arm. Once it completes the owner's answer no longer stands as the
    field's decision: nothing is pending or written, and an unwritten plan
    of the answer is closed superseded (the variant)."""
    library = preview_library
    root = library.root
    owner = accept(library.case(), "authors", "owner-answers-authors", 11)
    root.record_human_decision(owner)
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = applier(library, tmp_path)
    writes = _writes(target)
    if plan:
        library.now[0] = 13
        scheduler.schedule()
        assert planned(library)[owner.decision_id].state == "planned"

    _resume(library, "authors", 14)

    assert _record_row(root, "authors") is None
    assert library.pending() == []
    if plan:
        assert planned(library)[owner.decision_id].state == "superseded"
    _write_all(library, scheduler, service, target, 15)
    assert writes == []
    assert set(planned(library)) <= {owner.decision_id}
    assert_record_recomputes(root._connection)


def test_resume_completes_across_a_reread(tmp_path: Path) -> None:
    """P4: Resume is the work's, not the case's. Asked on one case, it
    survives a read that makes a new case, and completing it there hands the
    field back to Automatic's decision on the new case."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        first = library.automatic()
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        request = request_resume(root, library.case_id(), "authors", 12)
        assert request is not None

        _read_back(library, ("Someone Else",), 13)

        assert library.case_id() != owner.review_case_id
        later = [d for d in _authors(library) if d != first.decision_id]
        assert later
        assert _record_row(root, "authors") == (owner.decision_id, request)
        assert all(d not in library.pending() for d in later)
        assert complete_resume(root, request, 20)
        assert _record_row(root, "authors") is None
        assert library.pending() == [later[-1]]
        assert_record_recomputes(root._connection)


def test_cover_resume_is_asked_before_it_releases_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P12: the owner keeps the cover over Automatic's switch, and a later
    comparison would switch it but for the owner. Asking Resume releases
    nothing: the owner's cover, its mark and the record's answer stay until
    the checks complete. Completion then hands the cover back, and the
    current comparison's switch is admitted and pending."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _cover_prepared(tmp_path) as prepared_cover:
        repository = prepared_cover.repository
        service = _service(prepared_cover)
        (switch,) = _selections(repository)
        _keep_cover(prepared_cover, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        later = json.loads(_reprepare(service, prepared_cover))
        (kept,) = _selections(repository)
        assert kept["kind"] == "owner"

        def marks() -> int:
            (count,) = repository._connection.execute(
                "SELECT count(*) FROM cover_automatic_held"
            ).fetchone()
            return int(count)

        assert marks() == 1
        request = request_resume(repository, prepared_cover.review_case_id, "cover", 20)
        assert request is not None
        assert _selections(repository) == [kept]
        assert marks() == 1
        assert repository._connection.execute(
            "SELECT open_resume_id FROM owner_field_answers WHERE destination='cover'"
        ).fetchone() == (request,)

        assert complete_resume(repository, request, 21)

        (again,) = _selections(repository)
        assert again["kind"] == "automatic"
        assert again["consideration_id"] == later["consideration_id"]
        assert marks() == 0
        assert again["decision_id"] in [
            json.loads(document)["decision_id"]
            for document in repository.pending_field_effect_decision_documents(100)
        ]
        assert_record_recomputes(repository._connection)


@pytest.mark.parametrize("source", ("authored", "human"), ids=("authored", "human"))
def test_automatic_description_waits_for_resume_under_either_owner_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """P11, planning: the owner's description answer, an authored
    description or a decision, is the record's. A later reading's automatic
    advice is recorded on the successor but neither pending nor
    authoritative until Resume completes; then it is pending."""
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from mechanical_librarian.domain.field_effect import FieldEffectError
    from tests.support.description_advice import (
        _prepared as _description_prepared,
    )
    from tests.support.description_advice import qualify_description_advice
    from tests.unit.test_description_advice_persistence import _publish_successor

    qualify_description_advice(monkeypatch)
    access: list[Any] = []
    repository, store, binding, original, _ = _description_prepared(
        tmp_path,
        automatic_policy=None
        if source == "authored"
        else ("cover_order", "fixed_source_order"),
        pipeline_probe=lambda *values: access.append(values[3]),
    )
    try:
        if source == "authored":
            store.admit(original.candidate_id, binding, "human", "e" * 64, 11)
        else:
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(binding.review_case_id))["case"]
            )
            proposal = next(
                value
                for value in case.proposals
                if value["destination"] == "description"
            )
            repository.record_human_decision(
                HumanDecision.create(
                    case,
                    proposal_id=str(proposal["proposal_id"]),
                    target_fingerprint=case.target_fingerprint,
                    destination="description",
                    actor="owner",
                    outcome="keep_current",
                    selected_value=None,
                    idempotency_key="owner-keeps-description",
                    occurred_at=11,
                )
            )
        report, fresh = _publish_successor(repository, binding)
        candidate = DescriptionAdviceService(
            store, access[0], store.current_settings, lambda: 12
        ).prepare(report, fresh, lambda: store.binding_is_current(fresh))
        assert candidate is not None
        decision = store.admit(
            candidate.candidate_id, fresh, "automatic_policy", "a" * 64, 15
        )
        authority = PersistedFieldEffectAuthority(repository)

        def pending() -> list[str]:
            return [
                json.loads(document)["decision_id"]
                for document in repository.pending_field_effect_decision_documents(32)
                if json.loads(document)["destination"] == "description"
            ]

        assert decision.decision_id not in pending()
        with pytest.raises(FieldEffectError):
            authority.resolve(decision)

        assert resume(repository, fresh.review_case_id, "description", 18)

        assert pending() == [decision.decision_id]
        assert authority.resolve(decision) is not None
        assert_record_recomputes(repository._connection)
    finally:
        repository.close()


def test_asking_resume_requeues_the_case_with_no_trigger(
    library: Library,
) -> None:
    """Resume's request requeues the projection of the work's current case
    with no trigger (C2b-4). Preparation, as the owner reads it, is the same
    before and after: no trigger, run, enrolment or work row. Only the
    progress counts of the scan the page asked for move: the case's work
    counts as queued for projection there, no longer as checked and ready,
    until it is projected again."""
    root = library.root
    owner = accept(library.case(), "authors", "owner-answers-authors", 11)
    root.record_human_decision(owner)
    scan = root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 12)

    def presentation() -> tuple[object, ...]:
        return (
            root.preparation_presentation(13),
            root._connection.execute(
                "SELECT * FROM review_preparation_presentation ORDER BY row_id"
            ).fetchall(),
        )

    def progress() -> dict[str, object]:
        value = root.review_pipeline_progress("primary", "ebooks")
        assert value is not None
        return {
            name: getattr(value, name)
            for name in value.__dataclass_fields__
            if name not in {"checked", "ready", "stages"}
        } | {"counts": (value.checked, value.ready, dict(value.stages))}

    before, counts = presentation(), progress()
    assert request_resume(root, library.case_id(), "authors", 12, scan_id=scan.scan_id)

    assert presentation() == before
    after = progress()
    checked, ready, stages = cast("tuple[int, int, dict[str, int]]", counts["counts"])
    assert after == counts | {
        "counts": (
            checked - 1,
            ready - 1,
            stages
            | {"ready": stages["ready"] - 1, "projection": stages["projection"] + 1},
        )
    }
    assert root._connection.execute(
        "SELECT scan_id,stage,state FROM review_preparation_work"
    ).fetchall() == [(scan.scan_id, "projection", "queued")]
    assert_record_recomputes(root._connection)
