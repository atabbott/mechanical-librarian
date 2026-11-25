"""A policy activation re-reads a work whose stored report predates a write.

Activation retires every current case and restarts its work from the stored
report. When the product has since verified a write to that work, the stored
report no longer says what Audiobookshelf holds, and production refuses to
propose from it (`require_report_current`). Such a work is read again from the
library instead, after a discovery of its library, so its new case starts from
the written value and the activation counts it as checked (ADR-0034).

The harness is `_prepared` with its library model on: discovery, extraction,
proposal and admission behave as production's do against what the library
holds, and the write goes through the real guarded application.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mechanical_librarian.adapters.field_effect_fakes import FakeTargetService
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.field_effect import FieldEffectPlan
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.review_case import ReviewCase
from tests.integration.test_leaving_automatic_posture import _read
from tests.support.prepared_library import PRODUCER as _PRODUCER
from tests.support.prepared_library import Held as _Held
from tests.support.prepared_library import Library as _Library
from tests.support.prepared_library import accept as _accept
from tests.support.prepared_library import applier as _applier
from tests.support.prepared_library import planned as _planned
from tests.support.prepared_library import prepared as _prepared

_SITES = ("publication", "advance")


@pytest.fixture(params=_SITES, ids=_SITES)
def library(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Library]:
    """One work decided under Automatic, against a modelled library."""
    with _prepared(tmp_path, automatic=True, library_model=request.param) as value:
        yield value


def _held(library: _Library) -> _Held:
    assert library.held is not None
    return library.held


def _written(
    library: _Library, tmp_path: Path
) -> tuple[
    ContinuousGuardedApplication,
    GuardedFieldApplication,
    FakeTargetService,
    FieldEffectPlan,
]:
    """Write Automatic's authors decision and verify it, as the library now holds it."""
    automatic = library.automatic()
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    target.seed(plan)
    assert service.apply(plan.plan_id).state == "verified"
    assert plan.desired_value == ["Ada Example"]
    _held(library).write(TargetDestination.AUTHORS, ("Ada Example",))
    return scheduler, service, target, plan


def _answer(case: ReviewCase, outcome: str, key: str, at: int) -> HumanDecision:
    """Pin or unpin the authors field on `case`."""
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


def _ask_me(library: _Library, at: int) -> None:
    """Activate a changed policy that asks about contributor names."""
    root = library.root
    options = dict(DEFAULT_OPTIONS)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    options["contributor_names"] = "canonical"
    profile = MetadataPolicyProfile.create("Ask for names", options, destinations)
    resolved = resolve_policy(profile, "ebooks")
    root.create_metadata_policy_draft(profile.canonical_json)
    (active, generation) = root._connection.execute(
        "SELECT active_policy_id,generation FROM metadata_policy_state"
    ).fetchone()
    root.activate_metadata_policy(
        profile.policy_id,
        active,
        (("ebooks", resolved.resolved_id, resolved.canonical_json),),
        at,
        expected_active_generation=generation,
    )


def _transition(library: _Library) -> tuple[Any, ...]:
    return tuple(
        library.root._connection.execute(
            "SELECT t.state,COALESCE(SUM(i.state='complete'),0),"
            "COALESCE(SUM(i.state='failed'),0) FROM metadata_policy_transitions t "
            "JOIN metadata_policy_transition_items i ON i.transition_id=t.transition_id "
            "GROUP BY t.transition_id ORDER BY t.generation DESC LIMIT 1"
        ).fetchone()
    )


def _items(library: _Library) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in library.root._connection.execute(
            "SELECT i.state,i.failure_code,i.report_id FROM metadata_policy_transition_items i "
            "JOIN metadata_policy_transitions t ON t.transition_id=i.transition_id "
            "WHERE t.generation=(SELECT MAX(generation) FROM metadata_policy_transitions)"
        )
    ]


def _current_report(library: _Library) -> str:
    (report_id,) = library.root._connection.execute(
        "SELECT report_id FROM current_metadata_reports WHERE item_id='item-1'"
    ).fetchone()
    assert type(report_id) is str
    return report_id


def _settle(library: _Library, at: int) -> None:
    library.now[0] = at
    library.drain()


def _field_effect_states(library: _Library) -> list[str]:
    return [
        str(state)
        for (state,) in library.root._connection.execute(
            "SELECT state FROM field_effects WHERE work_id='item-1' ORDER BY rowid"
        )
    ]


def _read_again(library: _Library, before: str, proposed: int) -> str:
    """The work was read again, and its new case starts from the written value."""
    held = _held(library)
    report_id = _current_report(library)
    assert report_id != library.report.report_id
    # Nothing was proposed from the report that predates the write.
    assert held.proposed[proposed:] == [report_id]
    assert library.case_id() != before
    case = library.case()
    assert case.report_id == report_id
    assert [
        value["value"]
        for value in case.target["fields"]
        if value["destination"] == "authors"
    ] == [["Ada Example"]]
    assert _transition(library) == ("completed", 1, 0)
    assert _items(library) == [("complete", None, report_id)]
    return report_id


def test_a_changed_policy_reads_a_written_work_again(
    library: _Library, tmp_path: Path
) -> None:
    held = _held(library)
    scheduler, service, target, plan = _written(library, tmp_path)
    before = library.case_id()
    proposed = len(held.proposed)

    _ask_me(library, 30)
    assert library.root._connection.execute(
        "SELECT state FROM review_preparation_scans"
    ).fetchall() == [("queued",)]
    _settle(library, 31)

    # One discovery of the library, then one extraction of the work.
    assert library.external == ["discovery", "extraction"]
    _read_again(library, before, proposed)
    authors = _read(library, scheduler)["fields"]["authors"]
    assert authors["owner_control"]["current"] == ["Ada Example"]

    # The owner can pin the field on the new case, and answering it with the
    # value it offers plans nothing that goes stale: the library still holds
    # what was written.
    case = library.case()
    library.root.record_human_decision(_answer(case, "pin", "pin-after-the-read", 40))
    assert library.root._connection.execute(
        "SELECT count(*) FROM field_effect_manual_holds WHERE active=1 "
        "AND destination='authors' AND review_case_id=?",
        (case.review_case_id,),
    ).fetchone() == (1,)
    library.root.record_human_decision(
        _answer(case, "unpin", "unpin-after-the-read", 41)
    )
    try:
        library.root.record_human_decision(
            _accept(case, "authors", "accept-after-the-read", 42)
        )
    except HumanDecisionError as refused:
        # The library already holds the offered value, so the field offers
        # no answer to record; either way nothing may plan a stale write.
        assert str(refused) == "unsupported"
    library.now[0] = 43
    scheduler.schedule()
    for operation in _planned(library).values():
        if operation.state == "planned":
            target.seed(operation.plan)
            service.apply(operation.plan.plan_id)
    assert "stale" not in _field_effect_states(library)
    assert target.read(plan).value == ["Ada Example"]

    # The next scan lists the written authors it has already seen, so it
    # carries the work forward without extracting it again.
    library.root.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, 50)
    _settle(library, 51)
    assert library.external == ["discovery", "extraction", "discovery"]


def test_leaving_automatic_reads_a_written_work_again(
    library: _Library, tmp_path: Path
) -> None:
    held = _held(library)
    _written(library, tmp_path)
    before = library.case_id()
    proposed = len(held.proposed)

    library.leave_automatic()

    assert library.root.shadow_posture() == "preview"
    assert library.external == ["discovery", "extraction"]
    _read_again(library, before, proposed)


def test_a_changed_policy_restarts_an_unwritten_work_from_its_report(
    library: _Library,
) -> None:
    held = _held(library)
    proposed = len(held.proposed)

    _ask_me(library, 30)
    # The scan was never queued for a discovery.
    assert library.root._connection.execute(
        "SELECT state FROM review_preparation_scans"
    ).fetchall() == [("processing",)]
    _settle(library, 31)

    assert library.external == []
    assert held.proposed[proposed:] == [library.report.report_id]
    assert _transition(library) == ("completed", 1, 0)
    assert _items(library) == [("complete", None, library.report.report_id)]


def test_reading_a_written_work_again_is_not_a_scheduled_search(
    library: _Library, tmp_path: Path
) -> None:
    _written(library, tmp_path)
    library.root._connection.execute(
        "UPDATE review_preparation_work SET research_not_before=25"
    )
    library.root._connection.commit()

    _ask_me(library, 30)

    assert library.root._connection.execute(
        "SELECT stage,research_not_before FROM review_preparation_work"
    ).fetchall() == [("extraction", None)]
    _settle(library, 31)
    (extraction,) = _held(library).extractions
    assert extraction.research is False


def test_retry_reads_a_written_work_again_after_its_extraction_failed(
    library: _Library, tmp_path: Path
) -> None:
    held = _held(library)
    _written(library, tmp_path)
    held.failures.append("unknown")

    _ask_me(library, 30)
    _settle(library, 31)

    assert _transition(library) == ("completed_with_failures", 0, 1)
    assert _items(library) == [("failed", "failed", library.report.report_id)]
    transition = library.root.metadata_policy_transition()
    assert transition is not None
    library.root.retry_metadata_policy_transition(transition.transition_id, 40)
    assert library.root._connection.execute(
        "SELECT stage,state FROM review_preparation_work"
    ).fetchall() == [("extraction", "queued")]
    _settle(library, 41)

    assert library.external == ["discovery", "extraction", "extraction"]
    assert _transition(library) == ("completed", 1, 0)
    assert _items(library) == [("complete", None, _current_report(library))]


def test_retry_reads_again_a_work_written_after_the_activation(
    library: _Library, tmp_path: Path
) -> None:
    """A write that lands after the activation restarted the work fails it stale.

    The write was still out when the policy changed, so the work restarted
    from its report as before, and proposal refused that report once the
    write verified. Retrying reads the work again.
    """
    held = _held(library)
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, _, _ = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    root.claim_field_effect(plan.plan_id, "planned", "c" * 64, 14)
    root.transition_field_effect(plan.plan_id, "claimed", "applying", 15)

    _ask_me(library, 30)
    root.transition_field_effect(plan.plan_id, "applying", "verified", 31)
    held.write(TargetDestination.AUTHORS, ("Ada Example",))
    _settle(library, 32)

    assert library.external == []
    assert _transition(library) == ("completed_with_failures", 0, 1)
    assert _items(library) == [("failed", "stale", library.report.report_id)]
    transition = root.metadata_policy_transition()
    assert transition is not None
    root.retry_metadata_policy_transition(transition.transition_id, 40)
    assert root._connection.execute(
        "SELECT state FROM review_preparation_scans"
    ).fetchall() == [("queued",)]
    _settle(library, 41)

    assert library.external == ["discovery", "extraction"]
    assert _transition(library) == ("completed", 1, 0)
    assert _items(library) == [("complete", None, _current_report(library))]
    assert [
        value["value"]
        for value in library.case().target["fields"]
        if value["destination"] == "authors"
    ] == [["Ada Example"]]


def test_an_activation_keeps_a_requested_scan_waiting_for_its_discovery(
    library: _Library,
) -> None:
    """A scan requested and not yet started still discovers its library."""
    library.root.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, 20)
    assert library.root._connection.execute(
        "SELECT state FROM review_preparation_scans"
    ).fetchall() == [("queued",)]

    _ask_me(library, 30)

    assert library.root._connection.execute(
        "SELECT state FROM review_preparation_scans"
    ).fetchall() == [("queued",)]
    _settle(library, 31)
    assert library.external == ["discovery"]
    assert _transition(library) == ("completed", 1, 0)
