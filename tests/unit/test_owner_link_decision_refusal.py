"""A changed book link refuses every owner write that binds the case's report (#1477 L9).

The case's report was prepared under one link (or none). Once the owner links,
replaces or withdraws, that report no longer says what the work is, so an
owner decision or description choice recorded against it would bind the
wrong book. Each refusal is `link_changed`, writes nothing, and answers after
an idempotent replay and a stale case but before the matching check
(`offer_changed`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import decisions
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkItem,
    owner_link_bound,
    record_owner_link,
)
from mechanical_librarian.domain.human_decision import HumanDecisionError
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkScope,
    OwnerLinkTarget,
)
from tests.support.description_advice import _prepared
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.owner_values import OwnerStore, rewritten

WORK = OwnerLinkTarget(OwnerLinkScope.WORK, "open_library", "/works/OL1W", "A Title")


def _item(connection: sqlite3.Connection, review_case_id: str) -> OwnerLinkItem:
    row = connection.execute(
        "SELECT report.connection_name,report.library_name,report.item_id "
        "FROM review_cases review JOIN metadata_reports report "
        "ON report.report_id=review.report_id WHERE review.review_case_id=?",
        (review_case_id,),
    ).fetchone()
    return OwnerLinkItem(*row)


def _link(
    connection: sqlite3.Connection,
    review_case_id: str,
    action: OwnerLinkAction = OwnerLinkAction.LINK,
    expected: str | None = None,
    request: str = "request-link-0001",
) -> str:
    connection.execute("BEGIN IMMEDIATE")
    row = record_owner_link(
        connection,
        item=_item(connection, review_case_id),
        action=action,
        expected_current=expected,
        target=None if action is OwnerLinkAction.WITHDRAW else WORK,
        source="search",
        medium="ebook",
        actor="owner",
        occurred_at=1_700_000_000,
        request_id=request,
        request_sha256="a" * 64,
    )
    connection.execute("COMMIT")
    return row.link_row_id


def _decisions(store: OwnerStore) -> int:
    return store.repository._connection.execute(
        "SELECT count(*) FROM human_decisions"
    ).fetchone()[0]


def _refused(store: OwnerStore, code: str) -> None:
    before = _decisions(store)
    with pytest.raises(HumanDecisionError) as error:
        store.decide("keep_current")
    assert error.value.code == code
    assert _decisions(store) == before


def test_an_unlinked_case_is_bound_and_decides(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, isbn_title_report())
    connection = store.repository._connection
    assert owner_link_bound(connection, store.case.review_case_id) is True
    store.decide("keep_current")
    assert _decisions(store) == 1


@pytest.mark.parametrize(
    "actions",
    [
        (OwnerLinkAction.LINK,),
        (OwnerLinkAction.LINK, OwnerLinkAction.WITHDRAW),
    ],
    ids=["linked-after-report", "linked-then-withdrawn"],
)
def test_a_link_written_after_the_report_refuses_the_decision(
    tmp_path: Path, actions: tuple[OwnerLinkAction, ...]
) -> None:
    store = OwnerStore(tmp_path, isbn_title_report())
    connection = store.repository._connection
    current: str | None = None
    for index, action in enumerate(actions):
        current = _link(
            connection,
            store.case.review_case_id,
            action,
            current,
            f"request-link-{index:04d}",
        )
    # A withdrawn link leaves no current link, but the case's report was
    # prepared before any link existed: a link that came and went is still
    # a change only when the report recorded it. Here the report recorded
    # none, so only a live link refuses.
    expected = actions[-1] is OwnerLinkAction.WITHDRAW
    assert owner_link_bound(connection, store.case.review_case_id) is expected
    if expected:
        store.decide("keep_current")
        return
    _refused(store, "link_changed")


def test_a_replay_answers_before_the_link_refusal(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, isbn_title_report())
    decision = store.decide("keep_current")
    _link(store.repository._connection, store.case.review_case_id)
    # The same request again is answered from its stored row.
    assert store.repository.record_human_decision(decision) == decision.to_json()
    assert _decisions(store) == 1


def test_a_stale_case_answers_before_the_link_refusal(tmp_path: Path) -> None:
    report = isbn_title_report()
    store = OwnerStore(tmp_path, report)
    stale = store.case
    store.publish(rewritten(report, title="A Later Title"))
    assert store.case.review_case_id != stale.review_case_id
    _link(store.repository._connection, stale.review_case_id)
    before = _decisions(store)
    with pytest.raises(HumanDecisionError) as error:
        store.decide("keep_current", case=stale)
    assert (error.value.code, _decisions(store)) == ("stale", before)


@pytest.mark.parametrize("linked", [True, False], ids=["link-changed", "control"])
def test_the_link_refusal_answers_before_the_matching_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: bool
) -> None:
    store = OwnerStore(tmp_path, isbn_title_report())
    case_id = store.case.review_case_id
    requested: list[str | None] = []
    monkeypatch.setattr(decisions, "case_level_unreconciled", lambda *_: True)
    monkeypatch.setattr(
        decisions,
        "request_matching_check",
        lambda *_, review_case_id=None, **__: requested.append(review_case_id),
    )
    if linked:
        _link(store.repository._connection, case_id)
    with pytest.raises(HumanDecisionError) as error:
        store.repository.require_reconciled_case(case_id, 1_000)
    # A changed link asks for no matching check: the case is replaced anyway.
    assert (error.value.code, requested) == (
        ("link_changed", []) if linked else ("offer_changed", [case_id])
    )


@pytest.mark.parametrize("linked", [True, False], ids=["link-changed", "control"])
def test_an_owner_description_choice_is_refused_after_a_link_change(
    tmp_path: Path, linked: bool
) -> None:
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    connection = repository._connection
    if linked:
        _link(connection, binding.review_case_id)
    count = connection.execute("SELECT count(*) FROM description_decisions").fetchone()
    if not linked:
        store.admit(candidate.candidate_id, binding, "human", "b" * 64, 11)
        return
    with pytest.raises(HumanDecisionError) as error:
        store.admit(candidate.candidate_id, binding, "human", "b" * 64, 11)
    assert error.value.code == "link_changed"
    assert (
        connection.execute("SELECT count(*) FROM description_decisions").fetchone()
        == count
    )
