"""A level alternative chosen in review reads everywhere and writes (#1355, R1).

Every retained-decision reader validates a choice against the stored level
offers (S10 "Offer binding", amendment 2). A reader that missed them would
fail every read after the first level-alternative choice, so this test makes
that choice through the review API and then reads the case detail, the
works list, attention, History and Activity, and finally executes the write
through the persisted authority check.
"""

from __future__ import annotations

import json
from pathlib import Path

from mechanical_librarian.domain.human_decision import HumanDecision, offer_digest
from tests.unit.test_level_offer_binding import (
    SYNOPSIS,
    counts,
    field_of,
    level_store,
    offer_of,
    owner_client,
    post_choice,
)


def test_a_level_alternative_reads_everywhere_and_writes(tmp_path: Path) -> None:
    store = level_store(tmp_path / "store")
    try:
        case_id = store.case.review_case_id
        with owner_client(store, tmp_path) as (client, csrf):
            field = field_of(
                client.get(f"/api/v1/review-cases/{case_id}").json(), "description"
            )
            assert field["offer_digest"] == offer_digest(offer_of(store), "description")
            assert field["offer_digest"] != offer_digest(
                store.case.proposals, "description"
            )
            assert {"kind": "text", "value": SYNOPSIS} in field["proposed_values"]
            chosen = post_choice(
                client,
                csrf,
                case_id,
                field,
                SYNOPSIS,
                field["offer_digest"],
                "level-alternative-0001",
            )
            assert chosen.status_code == 200, chosen.text
            ((document,),) = store.repository._connection.execute(
                "SELECT document FROM human_decisions"
            ).fetchall()
            decision = HumanDecision.from_json(document)
            assert decision.selected_value == {"kind": "text", "value": SYNOPSIS}
            # The level value stands in the level offer only.
            assert SYNOPSIS not in json.dumps(store.case.proposals)

            detail = client.get(f"/api/v1/review-cases/{case_id}")
            assert detail.status_code == 200, detail.text
            projected = field_of(detail.json(), "description")["human_decision"]
            assert projected["decision_id"] == decision.decision_id
            assert projected["selected_value"] == {"kind": "text", "value": SYNOPSIS}

            library = client.get("/api/v1/library", params={"lens": "all"})
            assert library.status_code == 200, library.text
            assert case_id in library.text
            attention = client.get("/api/v1/attention")
            assert attention.status_code == 200, attention.text

            history = client.get(f"/api/v1/review-cases/{case_id}/history")
            assert history.status_code == 200, history.text
            assert [
                item["selected_value"]
                for item in history.json()["items"]
                if item.get("decision_id") == decision.decision_id
            ] == [{"kind": "text", "value": SYNOPSIS}]
            activity = client.get("/api/v1/activity")
            assert activity.status_code == 200, activity.text
            # The choice is pending write work: Activity counts it as waiting
            # for the field's permission, which its reader validated to count.
            assert activity.json()["waiting"]["awaiting_permission"] == 1

            written = store.write(decision)
            assert written.plan.desired_value == SYNOPSIS
            assert store.target.read(written.plan).value == SYNOPSIS

            after = client.get(f"/api/v1/review-cases/{case_id}")
            assert after.status_code == 200, after.text
            assert client.get("/api/v1/activity").status_code == 200
            assert client.get(
                f"/api/v1/review-cases/{case_id}/history"
            ).status_code == (200)
        assert counts(store)[0] == 1
    finally:
        store.repository.close()
