"""The record of the owner's answers decides what Automatic records, plans and
writes, and Resume (ADR-0108, #2038).

Each proof drives the real store through the prepared-library harness and
ends by recomputing the record from the ledger (`assert_record_recomputes`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.domain import cover_qualification_policy
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
    record_recomputes,  # noqa: F401
)
from tests.support.prepared_library import Library, accept, applier, planned
from tests.support.prepared_library import library as library


def _confirm_automatic(library: Library, at: int) -> None:
    library.root.activate_qualified_automatic(at)
    library.now[0] = at + 1
    library.drain()


def _resume_case(root: Any, case_id: str, destination: str, at: int) -> None:
    """Resume the field of the current case and let its checks complete."""
    (token,) = root._connection.execute(
        "SELECT answer_id FROM owner_field_answers WHERE destination=?",
        (destination,),
    ).fetchone()
    assert root.clear_field_effect_manual_hold(case_id, destination, at, token)


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
