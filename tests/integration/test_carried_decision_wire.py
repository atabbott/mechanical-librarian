"""A carried decision on the owner's wire (ADR-0108; #2040 packet 6).

The question check carries an answer to a later case of the same book, and
the case's detail (`review-case-detail-v16`) and history
(`review-case-history-v9`) read it through the real endpoints: the carried
row is the field's owner decision, `settled` where it plans nothing, and its
audit event is `human_decision_carried`. Until C3 words it, it reads as an
owner row does; Activity's response is unchanged by it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mechanical_librarian.application.review_web import (
    REVIEW_CASE_DETAIL_SCHEMA,
    REVIEW_CASE_HISTORY_SCHEMA,
)
from mechanical_librarian.domain.human_decision import HUMAN_DECISION_EVENT_SCHEMA
from tests.support.owner_values import rewritten
from tests.unit.test_level_offer_binding import field_of, owner_client
from tests.unit.test_owner_answer_holds import SOURCES, _store


@pytest.mark.parametrize(
    ("outcome", "selected", "application"),
    (
        ("keep_current", None, "settled"),
        ("accept", {"kind": "text", "value": SOURCES}, "apply"),
    ),
    ids=("settled_keep", "unwritten_accept"),
)
def test_a_carried_decision_and_its_event_pass_the_detail_and_history_wire(
    tmp_path: Path,
    outcome: str,
    selected: dict[str, object] | None,
    application: str,
) -> None:
    store, report, _ = _store(tmp_path, posture="preview")
    try:
        store.publish(report)
        original = store.decide(outcome, selected)
        later = store.publish(
            rewritten(report, publisher="Another Publisher")
        ).review_case_id
        ((carried_id, kind),) = store.repository._connection.execute(
            "SELECT event.decision_id,event.kind FROM human_decision_events event "
            "JOIN human_decisions decision USING(decision_id) "
            "WHERE decision.carried_from=?",
            (original.decision_id,),
        ).fetchall()
        assert kind == "human_decision_carried"

        with owner_client(store, tmp_path) as (client, _csrf):
            detail = client.get(f"/api/v1/review-cases/{later}")
            history = client.get(f"/api/v1/review-cases/{later}/history")
            activity = client.get("/api/v1/activity")
        assert detail.status_code == 200, detail.text
        assert history.status_code == 200, history.text
        assert activity.status_code == 200, activity.text

        body = detail.json()
        assert body["schema_version"] == REVIEW_CASE_DETAIL_SCHEMA
        decision = field_of(body, "title")["human_decision"]
        assert (
            decision["decision_id"],
            decision["review_case_id"],
            decision["outcome"],
            decision["application"],
            decision["application_reason"],
        ) == (carried_id, later, outcome, application, None)

        page = history.json()
        assert page["schema_version"] == REVIEW_CASE_HISTORY_SCHEMA
        (item,) = (
            value for value in page["items"] if value.get("decision_id") == carried_id
        )
        assert (
            item["kind"],
            item["actor"],
            item["outcome"],
            item["application"],
            item["application_reason"],
            item["schema_version"],
        ) == (
            "human_decision_carried",
            "owner",
            outcome,
            application,
            None,
            HUMAN_DECISION_EVENT_SCHEMA,
        )
    finally:
        store.close()
