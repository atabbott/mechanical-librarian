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
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
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
    """P3b: the owner answers in Preview, where Automatic decides nothing,
    and Automatic is then confirmed: the record holds the field, so
    Automatic decides nothing for it and Resume (offered only once Automatic
    is on, C0 `resume_offer`) has nothing to re-arm. Once it completes the
    owner's answer no longer stands as the field's decision: nothing is
    pending or written, and an unwritten plan of the answer is closed
    superseded (the variant)."""
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
    _confirm_automatic(library, 14)
    assert library.pending() == ([] if plan else [owner.decision_id])

    _resume(library, "authors", 16)

    assert _record_row(root, "authors") is None
    assert library.pending() == []
    if plan:
        assert planned(library)[owner.decision_id].state == "superseded"
    _write_all(library, scheduler, service, target, 17)
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
    before and after: no trigger, run, enrolment or work row, and the scan
    the page asked for counts the same works checked, ready and at each
    stage, though the case's work is queued for projection again (C2b-12)."""
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

    before, counts = presentation(), root.review_pipeline_progress("primary", "ebooks")
    assert counts is not None and counts.checked == 1
    assert request_resume(root, library.case_id(), "authors", 12, scan_id=scan.scan_id)

    assert presentation() == before
    assert root.review_pipeline_progress("primary", "ebooks") == counts
    assert root._connection.execute(
        "SELECT scan_id,stage,state FROM review_preparation_work"
    ).fetchall() == [(scan.scan_id, "projection", "queued")]
    assert_record_recomputes(root._connection)


def test_a_projection_requeue_during_a_failed_resume_counts_as_projection(
    library: Library,
) -> None:
    """A failed Resume stays open, but its work waiting for projection again
    is ordinary projection work: not checked, not ready (C2b-12)."""
    from mechanical_librarian.adapters.persistence.owner_field_answers import (
        fail_open_resumes_for_scan,
    )

    root = library.root
    root.record_human_decision(
        accept(library.case(), "authors", "owner-answers-authors", 11)
    )
    scan = root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 12)
    assert request_resume(root, library.case_id(), "authors", 12, scan_id=scan.scan_id)
    requested = root.review_pipeline_progress("primary", "ebooks")
    assert requested is not None and (requested.checked, requested.ready) == (1, 1)
    with root._connection:
        assert fail_open_resumes_for_scan(root._connection, scan.scan_id, 13) == 1

    assert root._connection.execute(
        "SELECT stage,state FROM review_preparation_work"
    ).fetchall() == [("projection", "queued")]
    progress = root.review_pipeline_progress("primary", "ebooks")
    (status,), _paused = root.review_pipeline_status((("primary", "ebooks"),))
    assert progress is not None and status is not None
    assert (progress.checked, progress.ready) == (0, 0)
    assert (status.checked, status.ready) == (0, 0)
    assert dict(progress.stages)["projection"] == 1
    assert_record_recomputes(root._connection)


@pytest.mark.parametrize("superseded", [False, True], ids=["resume", "answered"])
def test_a_scan_never_counts_fewer_works_checked_across_a_resume(
    tmp_path: Path, superseded: bool
) -> None:
    """Asking Resume while the page's scan runs requeues the case's work for
    projection, and the scan's "X of Y works checked" never goes backwards:
    not at the request, not while the work waits or is projected again, not
    when the owner answers the field again before projection (the answer
    supersedes the request), and the scan ends with the same count (C2b-12,
    Amendment 4)."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        root.record_human_decision(
            accept(library.case(), "authors", "owner-answers-authors", 11)
        )
        scan = root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 12)
        library.now[0] = 13
        assert library.pipeline.run_next()
        seen: list[tuple[str, int, int, int, str, str]] = []

        def observe() -> None:
            progress = root.review_pipeline_progress("primary", "ebooks")
            (status,), _paused = root.review_pipeline_status((("primary", "ebooks"),))
            assert progress is not None and status is not None
            assert (status.checked, status.ready) == (progress.checked, progress.ready)
            assert sum(count for _name, count in progress.stages) == progress.total
            ((stage, state),) = root._connection.execute(
                "SELECT stage,state FROM review_preparation_work"
            ).fetchall()
            seen.append(
                (
                    progress.state,
                    progress.checked,
                    progress.total,
                    progress.ready,
                    str(stage),
                    str(state),
                )
            )

        observe()
        assert request_resume(
            root, library.case_id(), "authors", 14, scan_id=scan.scan_id
        )
        observe()
        if superseded:
            root.record_human_decision(
                _owner_command(library, "keep_current", "owner-keeps-authors", 14)
            )
            assert [
                kind
                for (kind,) in root._connection.execute(
                    "SELECT kind FROM owner_field_events ORDER BY rowid"
                )
            ][-2:] == ["resume_requested", "resume_superseded"]
            observe()
            assert seen[-1][4:] == ("projection", "queued")
        library.now[0] = 15
        while library.pipeline.run_next():
            observe()
        observe()

        assert seen[0][:2] == ("processing", 1)
        # The request requeued the work, and projection ended it again.
        assert seen[1][4:] == ("projection", "queued")
        assert seen[-1] == ("completed", 1, 1, 1, "complete", "complete")
        checked = [row[1] for row in seen]
        assert checked == sorted(checked) and min(checked) == 1
        assert_record_recomputes(root._connection)


def _fields(library: Library) -> dict[str, dict[str, Any]]:
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase

    detail = InspectCurrentReviewCase(library.root).run(library.case_id())
    return {
        str(field["destination"]): field
        for field in cast(list[dict[str, Any]], detail["fields"])
    }


def _asked(library: Library) -> tuple[set[str], set[str]]:
    """The fields the case page asks for, and the ones Attention lists."""
    return (
        {name for name, field in _fields(library).items() if field["needs_attention"]},
        {str(destination) for _, destination in library.attention()},
    )


def _owner_command(library: Library, outcome: str, key: str, at: int) -> HumanDecision:
    """An owner command on the case's authors that carries no value: a pin,
    an unpin, keep current or a withdrawal."""
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
        outcome=outcome,
        selected_value=None,
        idempotency_key=key,
        occurred_at=at,
    )


def _held_reads(library: Library) -> dict[str, tuple[str, str, bool]]:
    """How each field the record holds an answer for reads on the current
    case: its classification, the branch that chose its sentence, and
    whether Resume is offered."""
    held = {
        str(destination)
        for (destination,) in library.root._connection.execute(
            "SELECT destination FROM owner_field_answers WHERE item_id='item-1'"
        )
    }
    assert held
    return {
        name: (
            str(field["classification"]),
            str(field["classification_cause"]),
            bool(field["manual_hold"]),
        )
        for name, field in _fields(library).items()
        if name in held
    }


_DECIDED = ("human_recorded", "human_decided")
_ASKED = ("needs_attention", "review_needed")


def test_a_case_kept_for_a_write_reads_its_answer_as_decided(
    tmp_path: Path,
) -> None:
    """C2b-18: the case kept for the owner's write offers no decision on the
    title, so the earlier case's answer settles it rather than asking it.
    The work's row, Attention, the case's Needs you and the title agree:
    nothing is asked and the title reads "You decided this field"."""
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        InspectCurrentReviewCase,
    )
    from tests.support.owner_values import OwnerStore
    from tests.support.review_cases import _report

    written = "Exact evaluator candidate"
    store = OwnerStore(tmp_path, _report())
    try:
        store.write(store.decide("owner_entered", {"kind": "text", "value": written}))
        (verified_at,) = store.repository._connection.execute(
            "SELECT MAX(occurred_at) FROM field_effect_events WHERE kind='verified'"
        ).fetchone()
        store.publish(_report(written, competing=False), now=verified_at + 1)
        case_id = store.case.review_case_id
        assert store.repository.review_case_kept(case_id)

        browse = BrowseReviewLibrary(store.repository)
        (row,) = cast(
            list[dict[str, Any]],
            browse.library(lens="all", search=None, limit=50, cursor=None)["items"],
        )
        assert (row["attention_count"], row["attention_state"]) == (
            0,
            "no_attention_needed",
        )
        assert browse.attention(search=None, limit=50, cursor=None)["items"] == []
        assert store.repository.count_current_attention_fields(None, ()) == 0
        fields = cast(
            list[dict[str, Any]],
            InspectCurrentReviewCase(store.repository).run(case_id)["fields"],
        )
        assert [
            field["destination"] for field in fields if field["needs_attention"]
        ] == []
        title = next(field for field in fields if field["destination"] == "title")
        assert title["decision_actions"] == []
        assert (title["classification"], title["classification_cause"]) == _DECIDED
        assert str(title["classification_reason"]).startswith("You decided this field")
        assert_record_recomputes(store.repository._connection)
    finally:
        store.close()


@pytest.mark.parametrize(
    "posture", ("automatic", "preview"), ids=("automatic", "preview")
)
def test_no_field_the_record_holds_reads_nothing_to_decide(
    tmp_path: Path, posture: str
) -> None:
    """C2b-15: while the record holds an answer for a field, the field never
    reads "Nothing to decide for this field.". An answer of the case and its
    episode reads decided, a pin's answer after its unpin among them; one
    from an earlier case or episode, which nothing carried, asks the field
    with Resume offered once Automatic is on where the case offers a decision
    on it (S16), and reads decided where none is offered (C2b-18, in
    `test_a_case_kept_for_a_write_reads_its_answer_as_decided`). Driven over the same
    case, a pin taken back, an open and a failed Resume, a later case and a
    return to the first case as a new episode, in Preview and in Automatic."""
    automatic = posture == "automatic"
    seen: list[dict[str, tuple[str, str, bool]]] = []
    with prepared(
        tmp_path, automatic=automatic, library_model="publication"
    ) as library:

        def reads() -> dict[str, tuple[str, str, bool]]:
            value = _held_reads(library)
            seen.append(value)
            return value

        root = library.root
        held = library.held
        assert held is not None
        original, first = held.authors, library.case_id()
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        assert reads() == {"authors": (*_DECIDED, automatic)}

        # A pin taken back on the same case leaves the case's own answer.
        for outcome, at in (("pin", 12), ("unpin", 13)):
            root.record_human_decision(
                _owner_command(library, outcome, f"owner-command-{outcome}-{at}", at)
            )
        assert reads() == {"authors": (*_DECIDED, automatic)}

        if automatic:
            request = request_resume(root, first, "authors", 14)
            assert request is not None
            # Automatic's decision settles the field while Resume is open.
            assert reads()["authors"][2] is True
            (scan,) = root._connection.execute(
                "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
            ).fetchone()
            assert root._fail_open_resumes_for_scan(scan, 15) == 1
            assert reads() == {"authors": (*_DECIDED, True)}

        # A later case: the answer is the earlier case's, and nothing carried.
        _read_back(library, ("Someone Else",), 16)
        assert library.case_id() != first
        assert reads() == {"authors": (*_ASKED, automatic)}
        # The case page and Attention ask it alike.
        page, attention = _asked(library)
        assert "authors" in page and "authors" in attention
        # The work returns to the first case's content: a new episode.
        _read_back(library, original, 18)
        assert library.case_id() == first
        assert reads() == {"authors": (*_ASKED, automatic)}

        # The other posture, with the record unchanged: Preview still shows
        # the failed Resume, to be asked again; Automatic offers Resume.
        if automatic:
            library.leave_automatic()
        else:
            _confirm_automatic(library, 40)
        assert reads() == {"authors": (*_ASKED, True)}
        assert all(
            cause != "nothing_to_decide"
            for value in seen
            for _, cause, _ in value.values()
        )
        assert_record_recomputes(root._connection)


def _audit_case(tmp_path: Path, case: str) -> tuple[Any, Any, str, dict[str, Any]]:
    """One of the audit's cases (P1, P2), set up through the owner-values
    store as its unit test sets it up: the store, its report, the field, and
    the values a later read finds the library holding."""
    from tests.support.owner_values import OwnerStore, rewritten
    from tests.unit import test_owner_answer_holds as audit

    if case.startswith("genres"):
        store = OwnerStore(tmp_path, None)
        if case == "genres_after_automatic":
            store.automatic("genres", "replace", audit.GENRES.target.library)
            store.publish(audit.GENRES)
            queued = audit._queued(store, "genres")
            assert queued is not None
            store.write(queued)
            store.publish(rewritten(audit.GENRES, genres=("Fantasy",)))
            decision = store.decide(
                "owner_entered",
                {"kind": "list", "value": ["Drama"]},
                destination="genres",
            )
            store.write(decision)
            return store, audit.GENRES, "genres", {"genres": ("Drama",)}
        library = audit.GENRES.target.library
        store.automatic("genres", "replace", library)
        store.repository.enter_shadow_preview(store.clock(), ())
        first = store.publish(audit.GENRES)
        store.decide("keep_current", destination="genres")
        store.repository.activate_qualified_automatic(store.clock())
        store.repository.persist_shadow_outcome(first.review_case_id, store.clock())
        return store, audit.GENRES, "genres", {}
    store, report, ask = audit._store(
        tmp_path,
        title="ask" if case == "kept_under_ask_me" else "automatic",
        posture="preview" if case.endswith("in_preview") else "automatic",
    )
    if case in {"typed_after_automatic", "original_put_back"}:
        audit._automatic_writes_first(store, report)
        value = (
            audit.OWN["value"] if case == "typed_after_automatic" else audit.ORIGINAL
        )
        store.write(store.decide("owner_entered", {"kind": "text", "value": value}))
        return store, report, "title", {"title": value}
    if case == "typed_in_preview":
        store.publish(report)
        store.write(store.decide("owner_entered", audit.OWN))
        store.repository.activate_qualified_automatic(store.clock())
        return store, report, "title", {"title": audit.OWN["value"]}
    if case == "kept_while_paused":
        store.repository.pause_automatic_safety(True, store.clock())
        store.publish(report)
        store.decide("keep_current", destination="title")
        store.repository.pause_automatic_safety(False, store.clock())
        return store, report, "title", {}
    first = store.publish(report)
    store.decide("keep_current", destination="title")
    if case == "kept_in_preview":
        store.repository.activate_qualified_automatic(store.clock())
        store.repository.persist_shadow_outcome(first.review_case_id, store.clock())
    else:
        audit._activate(
            store,
            audit._profile("Second policy", "automatic"),
            ask,
            report.target.library,
        )
    return store, report, "title", {}


_AUDIT_CASES = (
    "typed_after_automatic",
    "original_put_back",
    "kept_in_preview",
    "typed_in_preview",
    "kept_while_paused",
    "kept_under_ask_me",
    "genres_after_automatic",
    "genres_in_preview",
)


@pytest.mark.parametrize("case", _AUDIT_CASES, ids=_AUDIT_CASES)
def test_the_audit_cases_write_nothing_of_automatic_over_the_answer(
    tmp_path: Path, case: str
) -> None:
    """P1 and P2: the audit's six title cases and its two genres cases (the
    working path, and the Preview case it fixes). On the read-back and every
    later read nothing of Automatic's is planned or written over the owner's
    answer, and Resume is offered on each case, never reading as nothing to
    decide (ADR-0108, V1, C2b-15)."""
    from tests.support.owner_values import rewritten
    from tests.unit.test_owner_answer_holds import _field, _queued

    store, report, destination, held = _audit_case(tmp_path, case)
    try:
        patches = list(store.patches)
        for publisher in ("Read Back", "Later Read", "Another Later Read"):
            store.publish(rewritten(report, publisher=publisher, **held))
            assert _queued(store, destination) is None
            field = _field(store, destination)
            assert field["manual_hold"] is True
            assert field["classification_cause"] != "nothing_to_decide"
        assert store.patches == patches
        assert_record_recomputes(store.repository._connection)
    finally:
        store.close()


def _withdraw_command(library: Library, at: int) -> HumanDecision:
    return _owner_command(library, "withdraw", f"owner-withdraws-authors-{at}", at)


def _record_of(root: Any) -> tuple[object, ...] | None:
    """The record's authors row: its answer, the case it was given on,
    whether it may be carried, and its open Resume."""
    row = root._connection.execute(
        "SELECT answer_id,review_case_id,carry_eligible,open_resume_id "
        "FROM owner_field_answers WHERE item_id='item-1' AND destination='authors'"
    ).fetchone()
    return None if row is None else tuple(row)


@pytest.mark.parametrize(
    "posture", ("automatic", "preview"), ids=("automatic", "preview")
)
def test_withdrawing_the_second_answer_on_one_case_empties_the_record(
    tmp_path: Path, posture: str
) -> None:
    """P5: A then B on one case, withdraw B: the record is empty, so nothing
    of the owner's holds the field; under Automatic the next case is decided
    by Automatic and its decision is pending."""
    automatic = posture == "automatic"
    with prepared(
        tmp_path, automatic=automatic, library_model="publication"
    ) as library:
        root = library.root
        root.record_human_decision(
            accept(library.case(), "authors", "owner-answers-a-at-11", 11)
        )
        root.record_human_decision(
            _owner_command(library, "keep_current", "owner-answers-b-keeps-12", 12)
        )
        root.record_human_decision(_withdraw_command(library, 13))
        assert _record_of(root) is None
        assert "authors" not in _fields(library) or (
            _fields(library)["authors"]["human_decision"] is None
        )
        if not automatic:
            _confirm_automatic(library, 20)
        _read_back(library, ("Someone Else",), 30)
        later = [
            str(row[0])
            for row in root._connection.execute(
                "SELECT decision_id FROM automatic_decisions WHERE review_case_id=? "
                "AND destination='authors'",
                (library.case_id(),),
            )
        ]
        assert len(later) == 1
        assert library.pending() == later
        assert_record_recomputes(root._connection)


def test_an_emptying_withdrawal_beside_automatic_asks_the_field(
    tmp_path: Path,
) -> None:
    """Withdrawal and carry eligibility: under Automatic, the owner answers
    a field Automatic decided on the case, then withdraws the answer. The
    record is empty and the withdrawal revoked Automatic's authority, so its
    decision does not settle the field again: the field is asked by its own
    reason, on the case page and in Attention, and nothing is pending. On
    the book's next case Automatic decides the field again."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        decided = ("automatic_recorded", "automatic_decided")
        field = _fields(library)["authors"]
        assert (field["classification"], field["classification_cause"]) == decided
        root.record_human_decision(
            accept(library.case(), "authors", "owner-answers-a-on-x-11", 11)
        )
        assert _fields(library)["authors"]["owner_control"]["withdraw"] is not None

        root.record_human_decision(_withdraw_command(library, 13))

        assert _record_of(root) is None
        field = _fields(library)["authors"]
        assert (field["classification"], field["classification_cause"]) == _ASKED
        assert field["manual_hold"] is False
        page, attention = _asked(library)
        assert "authors" in page and "authors" in attention
        assert library.pending() == []
        assert_record_recomputes(root._connection)

        _read_back(library, ("Someone Else",), 30)
        later = [
            str(row[0])
            for row in root._connection.execute(
                "SELECT decision_id FROM automatic_decisions WHERE review_case_id=? "
                "AND destination='authors'",
                (library.case_id(),),
            )
        ]
        assert len(later) == 1 and library.pending() == later
        field = _fields(library)["authors"]
        assert (field["classification"], field["classification_cause"]) == decided
        assert_record_recomputes(root._connection)


@pytest.mark.parametrize(
    "posture", ("automatic", "preview"), ids=("automatic", "preview")
)
def test_withdrawing_a_later_cases_answer_restores_the_earlier_one(
    tmp_path: Path, posture: str
) -> None:
    """P5: A on X, B on Y, withdraw B: the record holds A again, as given on
    X and not to be carried (the explicit restore assertion). Y asks the
    field and nothing of Automatic's is pending (S16, C2b-15)."""
    automatic = posture == "automatic"
    with prepared(
        tmp_path, automatic=automatic, library_model="publication"
    ) as library:
        root = library.root
        first = library.case_id()
        a = accept(library.case(), "authors", "owner-answers-a-on-x-11", 11)
        root.record_human_decision(a)
        _read_back(library, ("Someone Else",), 12)
        assert library.case_id() != first
        b = _owner_command(library, "keep_current", "owner-answers-b-on-y-14", 14)
        root.record_human_decision(b)
        assert _record_of(root) == (b.decision_id, library.case_id(), 1, None)

        root.record_human_decision(_withdraw_command(library, 15))

        assert _record_of(root) == (a.decision_id, first, 0, None)
        field = _fields(library)["authors"]
        assert (field["classification"], field["classification_cause"]) == _ASKED
        assert field["manual_hold"] is automatic
        page, attention = _asked(library)
        assert "authors" in page and "authors" in attention
        assert library.pending() == []
        assert_record_recomputes(root._connection)


@pytest.mark.parametrize(
    "posture", ("automatic", "preview"), ids=("automatic", "preview")
)
def test_a_withdrawal_during_an_open_resume_still_completes_it(
    tmp_path: Path, posture: str
) -> None:
    """P5: A on X, B on Y, Resume asked over B, then withdraw B. Resume is
    asked under Automatic. In the Preview row Automatic is then left, so Y's
    automatic decision is an earlier generation's: the withdrawal stands, the
    record holds A again with the Resume still open over it. In the Automatic
    row the open Resume lets Y's automatic decision settle the field, so the
    store refuses the withdrawal as it does beside any settling automatic
    decision (plan 3.6). Either way completing the Resume clears the
    record."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        first = library.case_id()
        a = accept(library.case(), "authors", "owner-answers-a-on-x-11", 11)
        root.record_human_decision(a)
        _read_back(library, ("Someone Else",), 12)
        root.record_human_decision(
            _owner_command(library, "keep_current", "owner-answers-b-on-y-14", 14)
        )
        request = request_resume(root, library.case_id(), "authors", 15)
        assert request is not None
        if posture == "preview":
            library.leave_automatic()

        if posture == "preview":
            root.record_human_decision(_withdraw_command(library, 31))
            assert _record_of(root) == (a.decision_id, first, 0, request)
        else:
            held = _record_of(root)
            with pytest.raises(HumanDecisionError, match="^automatic_hold$"):
                root.record_human_decision(_withdraw_command(library, 31))
            assert _record_of(root) == held
        assert complete_resume(root, request, 32)
        assert _record_of(root) is None
        assert_record_recomputes(root._connection)


@pytest.mark.parametrize(
    "posture", ("automatic", "preview"), ids=("automatic", "preview")
)
def test_a_new_case_after_an_answer_asks_the_field(
    tmp_path: Path, posture: str
) -> None:
    """P8 (S16): the owner answers on X and the work is read again as Y.
    Nothing is carried, so Y asks the field, on the case page and in
    Attention, Automatic's decision on Y settles nothing and is not pending,
    and under Automatic Resume is offered."""
    automatic = posture == "automatic"
    with prepared(
        tmp_path, automatic=automatic, library_model="publication"
    ) as library:
        root = library.root
        root.record_human_decision(
            accept(library.case(), "authors", "owner-answers-authors", 11)
        )
        _read_back(library, ("Someone Else",), 12)
        field = _fields(library)["authors"]
        assert (field["classification"], field["classification_cause"]) == _ASKED
        assert field["manual_hold"] is automatic
        page, attention = _asked(library)
        assert "authors" in page and "authors" in attention
        # Automatic decided the field again on Y; it settles nothing there.
        assert root._connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE review_case_id=? "
            "AND destination='authors'",
            (library.case_id(),),
        ).fetchone() == ((1,) if automatic else (0,))
        assert library.pending() == []
        assert_record_recomputes(root._connection)


@pytest.mark.parametrize(
    "posture", ("automatic", "preview"), ids=("automatic", "preview")
)
def test_a_failed_resume_asks_the_field(tmp_path: Path, posture: str) -> None:
    """P8: a Resume whose scan ends with failures leaves the record holding
    the answer: the field is asked, shows the failed Resume to ask again,
    and nothing of Automatic's is pending; the same once Automatic is left."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        root.record_human_decision(
            accept(library.case(), "authors", "owner-answers-authors", 11)
        )
        _read_back(library, ("Someone Else",), 12)
        request = request_resume(root, library.case_id(), "authors", 14)
        assert request is not None
        (scan,) = root._connection.execute(
            "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
        ).fetchone()
        assert root._fail_open_resumes_for_scan(scan, 15) == 1
        if posture == "preview":
            library.leave_automatic()
        field = _fields(library)["authors"]
        assert (field["classification"], field["classification_cause"]) == _ASKED
        assert field["manual_return_failed"] is True
        page, attention = _asked(library)
        assert "authors" in page and "authors" in attention
        assert library.pending() == []
        assert_record_recomputes(root._connection)


@pytest.mark.parametrize(
    "posture", ("automatic", "preview"), ids=("automatic", "preview")
)
def test_resume_completed_with_nothing_to_rearm_asks_the_field(
    tmp_path: Path, posture: str
) -> None:
    """P8: Resume completes on a field Automatic abstains on, so there is
    nothing to re-arm: the record is empty and the field is asked, by
    Automatic's abstention; the same once Automatic is left."""
    from tests.support.evidence_led_titles import isbn_title_report
    from tests.unit.test_owner_answer_holds import _field, _store
    from tests.unit.test_owner_pins import _asked as asked_in_attention

    store, _, _ = _store(tmp_path)
    try:
        store.publish(isbn_title_report(empty_target_title=True, titles=(None,)))
        assert _field(store, "title")["classification_cause"] == "automatic_abstained"
        store.decide("keep_current", destination="title")
        assert _field(store, "title")["classification_cause"] == "human_decided"
        assert resume(
            store.repository, store.case.review_case_id, "title", store.clock()
        )
        if posture == "preview":
            store.repository.enter_shadow_preview(store.clock(), ())
        assert (
            store.repository._connection.execute(
                "SELECT count(*) FROM owner_field_answers"
            ).fetchone()[0]
            == 0
        )
        field = _field(store, "title")
        assert (field["classification"], field["classification_cause"]) == (
            "manual_fallback",
            "automatic_abstained",
        )
        assert asked_in_attention(store) is True
        assert_record_recomputes(store.repository._connection)
    finally:
        store.close()


def test_resume_answered_in_preview_survives_leaving_and_reentering_automatic(
    preview_library: Library,
) -> None:
    """P10: the owner answers in Preview, Automatic is confirmed, left and
    confirmed again so the case is prepared again; Resume is still offered
    and completes, handing the field to Automatic's decision."""
    library = preview_library
    root = library.root
    root.record_human_decision(
        accept(library.case(), "authors", "owner-answers-in-preview", 11)
    )
    _confirm_automatic(library, 14)
    library.leave_automatic()
    _confirm_automatic(library, 40)
    assert _fields(library)["authors"]["manual_hold"] is True
    _resume(library, "authors", 42)
    library.now[0] = 50
    library.drain()
    assert _record_row(root, "authors") is None
    decided = library.automatic(library.generation())
    assert library.pending() == [decided.decision_id]
    assert_record_recomputes(root._connection)


def test_a_pin_in_force_follows_the_cases_field_with_no_stored_flag(
    tmp_path: Path,
) -> None:
    """S15: whether a pin is in force is computed when it is read, from the
    case's field: in force on the pinned value, changed on a case that reads
    another, in force again when the work returns to it. The record stores
    the pinned fingerprint alone, unchanged throughout."""
    with prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        held = library.held
        assert held is not None
        original = held.authors
        # The pin is the owner's answer itself, so no write of it waits.
        root.record_human_decision(
            _owner_command(library, "pin", "owner-command-pin-12", 12)
        )

        def pin() -> tuple[object, str]:
            (fingerprint,) = root._connection.execute(
                "SELECT pin_fingerprint FROM owner_field_answers "
                "WHERE item_id='item-1' AND destination='authors'"
            ).fetchone()
            return fingerprint, str(_fields(library)["authors"]["classification_cause"])

        stored, cause = pin()
        assert stored is not None and cause == "pinned"
        _read_back(library, ("Someone Else",), 13)
        assert pin() == (stored, "pin_changed")
        _read_back(library, original, 15)
        assert pin() == (stored, "pinned")
        columns = {
            str(row[1])
            for row in root._connection.execute(
                "PRAGMA table_info(owner_field_answers)"
            )
        }
        assert not any("force" in column for column in columns)
        assert_record_recomputes(root._connection)


@pytest.mark.parametrize("source", ("authored", "human"), ids=("authored", "human"))
def test_a_later_description_case_asks_and_a_failed_resume_keeps_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """P11, the asked parts: the owner's description answer, authored or a
    decision, given on the work's earlier case; Automatic's advice is
    recorded on the later case. The later case asks the description with
    Resume offered (S16, C2b-15), and a Resume that fails leaves the record
    holding the answer and the field asked."""
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
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
        store.admit(candidate.candidate_id, fresh, "automatic_policy", "a" * 64, 15)

        def field() -> dict[str, Any]:
            return next(
                value
                for value in InspectCurrentReviewCase(repository).run(
                    fresh.review_case_id
                )["fields"]
                if value["destination"] == "description"
            )

        def asked() -> None:
            value = field()
            assert (value["classification"], value["classification_cause"]) == _ASKED
            assert value["needs_attention"] is True
            assert repository._connection.execute(
                "SELECT count(*) FROM review_attention_facts "
                "WHERE destination='description'"
            ).fetchone() == (1,)

        asked()
        assert field()["manual_hold"] is True
        request = request_resume(repository, fresh.review_case_id, "description", 18)
        assert request is not None
        (scan,) = repository._connection.execute(
            "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
        ).fetchone()
        assert repository._fail_open_resumes_for_scan(scan, 19) == 1
        assert repository._connection.execute(
            "SELECT count(*) FROM owner_field_answers WHERE destination='description'"
        ).fetchone() == (1,)
        asked()
        assert field()["manual_return_failed"] is True
        assert_record_recomputes(repository._connection)
    finally:
        repository.close()
