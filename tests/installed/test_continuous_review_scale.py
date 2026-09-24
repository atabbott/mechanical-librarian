"""Installed-wheel phase-free owner-decision/Activity smoke qualification."""

import json
import re
import sqlite3
import time
from typing import Any, cast

import pytest
from tests.support import continuous_review_scale as harness
from tests.support.continuous_review_scale import (
    qualify_installed_decision_activity_http,
)
from tests.support.inventaire_preparation import InventairePreparationFixture

from mechanical_librarian.domain.cover_evidence import CoverSourceState
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalSet


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
def test_inventaire_failure_then_authenticated_reprepare_starts_new_run(
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
                    prior_documents = dict(
                        connection.execute(
                            "SELECT report_id,document FROM metadata_reports"
                        )
                    )
                    first_run = connection.execute(
                        "SELECT run_id,state FROM review_preparation_presentation WHERE row_id='run'"
                    ).fetchone()
                    assert (
                        first_run is not None
                        and first_run[1] == "completed_with_failures"
                    )
                    assert connection.execute(
                        "SELECT count(*) FROM review_preparation_presentation WHERE row_kind='work' AND work_outcome='failed'"
                    ).fetchone() == (1,)
                    assert [
                        connection.execute("SELECT count(*) FROM " + table).fetchone()[
                            0
                        ]
                        for table in ("proposal_sets", "review_cases")
                    ] == [2, 2]

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
                    final_run = connection.execute(
                        "SELECT run_id,state FROM review_preparation_presentation WHERE row_id='run'"
                    ).fetchone()
                    assert final_run is not None and final_run[0] != first_run[0]
                    assert final_run[1] == "completed_clean"
                    current_documents = dict(
                        connection.execute(
                            "SELECT report_id,document FROM metadata_reports"
                        )
                    )
                    assert all(
                        current_documents[identity] == document
                        for identity, document in prior_documents.items()
                    )
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
                    if state is CoverSourceState.RETRIEVED:
                        for (document,) in connection.execute(
                            "SELECT document FROM proposal_sets"
                        ):
                            proposal = ProposalSet.from_json(document)
                            report = MetadataReport.from_json(
                                current_documents[proposal.report_id]
                            )
                            source = report.cover_sources[0]
                            alternative = next(
                                result
                                for result in proposal.results
                                if result.destination is TargetDestination.COVER
                            ).alternatives[0]
                            assert (
                                source.source_id,
                            ) == alternative.evidence_references
                            assert source.artifact is not None
                            assert (
                                source.artifact.artifact_id == alternative.value.value
                            )


def test_current_authority_and_predecessor_refusal_without_history(
    tmp_path, monkeypatch
):
    """The exact wheel uses compiled grants with no checkout documents or Git."""
    from contextlib import closing
    from pathlib import Path

    from tests.support.ebook_synopsis import publish_synopsis_preview, synopsis_report

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.metadata_report import MetadataReportError
    from mechanical_librarian.domain.automatic_decision import (
        AutomaticDecision,
        AutomaticDecisionError,
        qualification_manifest_is_current,
    )

    fixture = Path(__file__).parents[1] / "fixtures/qualification-authority-predecessor"
    historical = (fixture / "decision.json").read_text().strip()
    predecessor = (fixture / "empty-v8.sql").read_text()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "")
    assert not Path("docs").exists()
    assert not Path(".git").exists()
    path = tmp_path / "current.sqlite"
    with closing(open_repository(path)) as repository:
        case, _ = publish_synopsis_preview(repository, synopsis_report())
        repository.persist_shadow_outcome(case, 4)
        document = next(
            d
            for d in repository.get_review_case_decisions(case)
            if json.loads(d)["destination"] == "description"
        )
        current = AutomaticDecision.from_json(document)
        assert qualification_manifest_is_current(
            current.policy_category,
            current.policy_option,
            current.qualification_manifest_sha256,
        )
    with closing(open_repository(path, create=False)) as repository:
        assert document in repository.get_review_case_decisions(case)
    old = AutomaticDecision.from_json(historical)
    assert old.to_json() == historical
    assert not qualification_manifest_is_current(
        old.policy_category, old.policy_option, old.qualification_manifest_sha256
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **cast("dict[str, Any]", old._core()), occurred_at=old.occurred_at
        )
    old_path = tmp_path / "prior.sqlite"
    with closing(sqlite3.connect(old_path)) as connection:
        connection.executescript(predecessor)
    before = old_path.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(old_path, create=False)
    assert old_path.read_bytes() == before
