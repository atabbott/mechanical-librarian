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

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/shadow-outcome-v9.json").read_text()
    )
    assert fixture["source_sha"] == "08ae544e20ea79037f7e4a1d5f66b482554ed1dc"
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
    for historical in fixture["decisions"].values():
        old = AutomaticDecision.from_json(historical)
        assert old.to_json() == historical
        assert not qualification_manifest_is_current(
            old.policy_category, old.policy_option, old.qualification_manifest_sha256
        )
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **cast("dict[str, Any]", old._core()), occurred_at=old.occurred_at
            )
    import hashlib
    from types import SimpleNamespace

    from tests.support.automatic_decisions import _automatic_decisions

    from mechanical_librarian.adapters.persistence import schema
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase

    cover_fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/cover-authority-v2.json").read_text()
    )
    proposal_fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "support/proposal_predecessors/fixed-conservative-v32.json"
        ).read_text()
    )
    old_path = tmp_path / "prior.sqlite"
    with monkeypatch.context() as old_schema:
        old_schema.setattr(
            schema,
            "_COVER_CONSIDERATIONS_SQL",
            json.loads(
                (
                    Path(__file__).parents[1]
                    / "fixtures/cover-consideration-embedded-report.json"
                ).read_text()
            )["cover_considerations_sql"],
        )
        # Preserve the frozen producer's full schema inventory, including its
        # reviewer table; the historical digest remains unchanged.
        old_schema.setattr(
            schema,
            "_REVIEWER_ACCESS_SQL",
            "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=1),document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 262144))",
        )
        old_schema.setattr(
            schema,
            "_COVER_SELECTION_AUTHORITIES_SQL",
            cover_fixture["cover_selection_authorities_sql"],
        )
        old_schema.setattr(
            schema,
            "_SHADOW_DECISION_OUTCOMES_SQL",
            fixture["shadow_decision_outcomes_sql"],
        )
        old_schema.setattr(
            schema, "_PROPOSAL_SETS_SQL", proposal_fixture["proposal_sets_ddl"]
        )
        with closing(open_repository(old_path)) as repository:
            # The producer predates #1199's report browse facts, #1202's
            # history indexes, #1204's indexes and #1211's case indexes.
            repository._connection.executescript(
                "DROP TRIGGER metadata_reports_project_browse_facts;"
                "DROP TABLE metadata_report_browse_authors;"
                "DROP TABLE metadata_report_browse_facts;"
                "DROP TRIGGER proposal_sets_project_browse_facts;"
                "DROP TABLE proposal_set_browse_facts;"
                "DROP TABLE browse_fact_identity;"
                "DROP INDEX human_decisions_case_destination;"
                "DROP INDEX metadata_policy_resolutions_resolved;"
                "DROP INDEX field_effects_decision;"
                "DROP INDEX field_effects_companion_decision;"
                "DROP INDEX field_effects_ready;"
                "DROP INDEX field_effects_waiting;"
                "DROP INDEX field_effects_target;"
                "DROP INDEX description_decisions_case;"
                "DROP INDEX current_review_case_lineages_case;"
                "DROP INDEX current_review_packages_case;"
            )
            rows = repository._connection.execute(
                "SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            assert len(rows) == 104
            assert (
                hashlib.sha256(
                    json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest()
                == fixture["schema_inventory_sha256"]
            )
    before = old_path.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(old_path, create=False)
    assert old_path.read_bytes() == before
    refusal_path = tmp_path / "refusal.sqlite"
    with closing(open_repository(refusal_path)) as repository:
        assert (
            _automatic_decisions(
                SimpleNamespace(repository=repository),
                synopsis_report(summaries=("",)),
                "description_fill",
                "safe_plain_text",
                qualified=True,
            )
            == ()
        )
        refusal_case = repository._connection.execute(
            "SELECT review_case_id FROM review_cases"
        ).fetchone()[0]
    with closing(open_repository(refusal_path, create=False)) as repository:

        def forbidden(*args, **kwargs):
            raise AssertionError("installed explanation compiled evidence")

        monkeypatch.setattr(
            "mechanical_librarian.domain.shadow_outcome.compile_rich_metadata_preview",
            forbidden,
        )
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(refusal_case)["fields"]
            if f["destination"] == "description"
        )
        assert (
            field["automatic_withheld_reason"]
            == "No eligible value was found in the retained evidence."
        )
        assert repository._connection.total_changes == 0
