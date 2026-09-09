"""Installed-wheel phase-free owner-decision/Activity smoke qualification."""

import json
import re
import sqlite3
import time
from typing import cast

import pytest
from tests.support import continuous_review_scale as harness
from tests.support.continuous_review_scale import (
    qualify_installed_decision_activity_http,
)
from tests.support.inventaire_preparation import InventairePreparationFixture

from mechanical_librarian.domain.cover_evidence import CoverSourceState


def test_installed_owner_decision_activity_smoke() -> None:
    evidence = qualify_installed_decision_activity_http(
        {"fiction": 4, "history": 20, "science": 20},
        expected_total=45,
    )
    assert evidence.history_events == 1
    assert evidence.category == "owner"
    assert evidence.outcome == "accept"


@pytest.mark.parametrize(
    "state",
    (
        CoverSourceState.RETRIEVED,
        CoverSourceState.ABSENT,
        CoverSourceState.UNAVAILABLE,
        CoverSourceState.TIMEOUT,
    ),
)
def test_inventaire_failure_then_authenticated_reprepare_preserves_history(
    state: CoverSourceState,
) -> None:
    with harness._installed_qualification_environment(
        {"fiction": 1, "history": 1, "science": 1}, 3
    ) as environment:
        fixture = InventairePreparationFixture(state, environment.artifacts)
        with environment.running_preparation(report_factory=fixture) as run:
            run.release_discovery()
            with harness._review_client() as client:
                assert (
                    client.post(
                        environment.origin + "/api/v1/session/login",
                        headers={"Origin": environment.origin},
                        json={"password": "synthetic password"},
                    ).status_code
                    == 200
                )
                deadline = time.monotonic() + 30
                while True:
                    status = run.status(client)
                    libraries = cast(list[dict[str, object]], status["libraries"])
                    if not status["active"] and all(
                        item["state"] in {"completed", "completed_with_failures"}
                        for item in libraries
                    ):
                        break
                    assert time.monotonic() < deadline, status
                    time.sleep(0.05)
                assert (
                    next(item for item in libraries if item["library"] == "fiction")[
                        "state"
                    ]
                    == "completed_with_failures"
                )
                with sqlite3.connect(environment.database) as connection:
                    assert connection.execute(
                        "SELECT state,failure_code FROM review_preparation_work WHERE item_id='fiction-0000'"
                    ).fetchall() == [("failed", "unknown")]
                    assert connection.execute(
                        "SELECT count(*) FROM metadata_reports"
                    ).fetchone() == (2,)
                fixture.fail = False
                html = client.get(environment.origin + "/library").text
                csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]{32,128})"', html)
                build = re.search(r'data-build-identity="([0-9a-f]{64})"', html)
                assert csrf is not None and build is not None
                assert (
                    client.post(
                        environment.origin
                        + "/api/v1/review-preparation/primary/fiction/scan",
                        headers={
                            "Origin": environment.origin,
                            "x-csrf-token": csrf.group(1),
                            "x-ml-build-identity": build.group(1),
                        },
                    ).status_code
                    == 202
                )
                run.wait_until_terminal()
                with sqlite3.connect(environment.database) as connection:
                    assert [
                        connection.execute("SELECT count(*) FROM " + table).fetchone()[
                            0
                        ]
                        for table in (
                            "metadata_reports",
                            "proposal_sets",
                            "review_cases",
                        )
                    ] == [3, 3, 3]
                    documents = [
                        json.loads(row[0])
                        for row in connection.execute(
                            "SELECT document FROM metadata_reports"
                        )
                    ]
                    assert all(
                        document["covers"][0]["kind"] == "inventaire"
                        for document in documents
                    )
                    assert all(
                        document["covers"][0]["state"] == state.value
                        for document in documents
                    )
