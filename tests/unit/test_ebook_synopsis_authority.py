"""Ebook synopsis authority through actual provider and persisted owner seams."""

import json
from contextlib import closing

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from tests.support.ebook_synopsis import publish_synopsis_preview, synopsis_report


@pytest.mark.parametrize(
    "summaries",
    [
        ("(duplicate)",),
        ("294 pages ; 22 cm",),
        ("(Duplicate)", "(duplicate)"),
        ("294 pages;22 cm", "(duplicate)"),
    ],
)
def test_placeholders_never_create_automatic_authority(tmp_path, summaries):
    with closing(open_repository(tmp_path / "state.sqlite")) as repository:
        case, _ = publish_synopsis_preview(
            repository, synopsis_report(summaries=summaries)
        )
        repository.persist_shadow_outcome(case, 4)
        assert all(
            json.loads(d)["destination"] != "description"
            for d in repository.get_review_case_decisions(case)
        )
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case)["fields"]
            if f["destination"] == "description"
        )
        assert "No eligible automatic synopsis" in field["automatic_withheld_reason"]


def test_concise_source_election_reopens_exact_bytes(tmp_path):
    path = tmp_path / "state.sqlite"
    with closing(open_repository(path)) as repository:
        case, _ = publish_synopsis_preview(repository, synopsis_report())
        repository.persist_shadow_outcome(case, 4)
    with closing(open_repository(path)) as repository:
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case)["fields"]
            if f["destination"] == "description"
        )
        assert field["automatic_decision"] is not None
        assert (
            field["automatic_decision"]["selected_value"]["value"]
            == "A voyage reveals a hidden city."
        )


@pytest.mark.parametrize(
    "value",
    [
        "(Duplicate) A new voyage begins.",
        "Duplicate lives intertwine.",
        "294 pages of adventure await.",
        "22 cm separates the rivals.",
        "A sequel to Synthetic Voyage.",
        "Short.",
        "294 pp.;22 cm",
        "Chapter 1",
    ],
)
def test_closed_fitness_negative_controls(value):
    from mechanical_librarian.domain.rich_metadata_preview import (
        _eligible_ebook_synopsis,
    )

    assert _eligible_ebook_synopsis(value)


@pytest.mark.parametrize(
    "summaries",
    [
        ("(duplicate)", "A voyage begins."),
        ("294 pages ; 22 cm", "A voyage begins."),
        ("A voyage begins.", "(duplicate)"),
    ],
)
def test_mixed_sources_select_useful_text(tmp_path, summaries):
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.domain.automatic_decision import AutomaticDecision

    with closing(open_repository(tmp_path / "state.sqlite")) as repository:
        case, _ = publish_synopsis_preview(
            repository, synopsis_report(summaries=summaries)
        )
        repository.persist_shadow_outcome(case, 4)
        decision = next(
            AutomaticDecision.from_json(d)
            for d in repository.get_review_case_decisions(case)
            if json.loads(d)["destination"] == "description"
        )
        assert PersistedFieldEffectAuthority(repository).resolve(
            decision
        ).decision_value == {"kind": "text", "value": "A voyage begins."}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("swap", [False, True])
def test_election_follows_actual_source_identity_not_prose(reverse, swap):
    from mechanical_librarian.domain.metadata_report import (
        TargetDestination,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from mechanical_librarian.domain.rich_metadata_preview import (
        automatic_description_candidate,
    )

    summaries: tuple[str, ...] = (
        "Tiny.",
        "A much longer voyage through the sea and into the distant hidden city.",
    )
    if swap:
        summaries = tuple(reversed(summaries))
    report = synopsis_report(summaries=summaries, reverse=reverse)
    proposal = evaluate_report(report)
    result = next(
        r for r in proposal.results if r.destination is TargetDestination.DESCRIPTION
    )
    selected = automatic_description_candidate(report, result)
    assert selected is not None
    assert selected[0].value.value == summaries[0]
    source = report.providers[0].candidates[0]
    assert source.identity == "hardcover-edition-0000000001"
    assert selected[1] == tuple(
        c.reference for c in source.claims if c.kind.value == "description"
    )


@pytest.mark.parametrize("mode", ["ask", "ignore"])
@pytest.mark.parametrize("keep", [False, True])
def test_owner_placeholder_choice_attention_and_finality(tmp_path, mode, keep):
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        RecordHumanDecision,
        ReviewWebError,
    )

    path = tmp_path / "state.sqlite"
    with closing(open_repository(path)) as repository:
        case, _ = publish_synopsis_preview(
            repository, synopsis_report(summaries=("(duplicate)",)), mode
        )
        repository.persist_shadow_outcome(case, 4)
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case)["fields"]
            if f["destination"] == "description"
        )
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
        assert any(i["destination"] == "description" for i in attention["items"]) is (
            mode == "ask"
        )
        assert field["editable"] is (mode == "ask")
        if mode == "ignore":
            assert field["decision_actions"] == []
            return
        assert {v["value"] for v in field["proposed_values"]} == {"(duplicate)"}
        args = dict(
            proposal_id=field["proposal_id"],
            target_fingerprint=field["target_fingerprint"],
            destination="description",
            actor="owner",
            outcome="keep_current" if keep else "accept",
            selected_value=None if keep else field["proposed_values"][0],
            idempotency_key="ebook-owner-final",
        )
        command = RecordHumanDecision(repository, clock=lambda: 10)
        with pytest.raises(ReviewWebError):
            command.run(
                case,
                **(
                    args
                    | {
                        "outcome": "accept",
                        "selected_value": {"kind": "text", "value": "Forged"},
                    }
                ),
            )
        command.run(case, **args)
        repository.persist_shadow_outcome(case, 11)
    with closing(open_repository(path)) as repository:
        repository.persist_shadow_outcome(case, 12)
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case)["fields"]
            if f["destination"] == "description"
        )
        assert field["classification"] == "human_recorded"
        assert field["human_decision"]["outcome"] == (
            "keep_current" if keep else "accept"
        )


@pytest.mark.parametrize(
    "value,current",
    [
        ("(duplicate)", None),
        ("294 pages;22 cm", None),
        ("Useful.", "Existing.\n\nParagraph."),
        ("<p>Unsafe</p>", None),
        ("Cut off...", None),
    ],
)
def test_persisted_projection_refuses_ineligible_or_populated(value, current):
    from mechanical_librarian.domain.rich_metadata_preview import (
        RichMetadataPreviewError,
        project_f20_automatic_decision_value,
    )

    with pytest.raises(RichMetadataPreviewError):
        project_f20_automatic_decision_value(
            "description_fill",
            "safe_plain_text",
            {"kind": "text", "value": value},
            current,
        )


def test_actual_predecessors_refuse_authority_and_store_before_mutation(tmp_path):
    import hashlib
    import sqlite3
    from pathlib import Path

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        MetadataReportError,
    )
    from mechanical_librarian.application.field_effects import authority_from_decision
    from mechanical_librarian.domain.automatic_decision import (
        AutomaticDecision,
        qualification_manifest_is_current,
    )
    from mechanical_librarian.domain.metadata_report import MetadataReport
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from mechanical_librarian.domain.shadow_decision import ShadowDecisionSet
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

    root = Path(__file__).parents[1] / "fixtures/ebook-synopsis-predecessor"
    for capture in (root, root / "qualified"):
        provenance = json.loads((capture / "provenance.json").read_text())
        assert provenance["source_sha"] == "b3186df6c4835a4fd7b5dc31cf65d019f5bb0446"
        for name, digest in provenance["files"].items():
            assert hashlib.sha256((capture / name).read_bytes()).hexdigest() == digest
        for scenario in ("duplicate", "extent", "concise"):
            MetadataReport.from_json(
                (capture / f"{scenario}-report.json").read_text().strip()
            )
            ProposalSet.from_json(
                (capture / f"{scenario}-proposal.json").read_text().strip()
            )
            for kind, parser in [
                ("shadow-decision", ShadowDecisionSet),
                ("outcome", ShadowOutcomeSet),
            ]:
                with pytest.raises(ValueError):
                    parser.from_json(
                        (capture / f"{scenario}-{kind}.json").read_text().strip()
                    )
            for path in capture.glob(f"{scenario}-automatic-*.json"):
                decision = AutomaticDecision.from_json(path.read_text().strip())
                assert authority_from_decision(decision) is None
                assert not qualification_manifest_is_current(
                    decision.policy_category,
                    decision.policy_option,
                    decision.qualification_manifest_sha256,
                )
            directory = tmp_path / capture.name / scenario
            directory.mkdir(parents=True, mode=0o700)
            database = directory / "state.sqlite"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
                    (capture / f"{scenario}-store.sql").read_text()
                )
            database.chmod(0o600)
            before = database.read_bytes()
            with pytest.raises(MetadataReportError):
                open_repository(database, create=False)
            assert database.read_bytes() == before


@pytest.mark.parametrize("substitution", ["winner", "citation", "source", "populated"])
def test_persisted_authority_reconstructs_exact_winner(tmp_path, substitution):
    from mechanical_librarian.adapters.persistence.field_effect_validation import (
        _automatic_decision_value,
    )
    from mechanical_librarian.domain.automatic_decision import AutomaticDecision
    from mechanical_librarian.domain.field_effect import FieldEffectError
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from mechanical_librarian.domain.review_case import ReviewCase
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

    report = synopsis_report()
    with closing(open_repository(tmp_path / "state.sqlite")) as repository:
        case_id, _ = publish_synopsis_preview(repository, report)
        repository.persist_shadow_outcome(case_id, 4)
        decision = next(
            AutomaticDecision.from_json(d)
            for d in repository.get_review_case_decisions(case_id)
            if json.loads(d)["destination"] == "description"
        )
        connection = repository._connection
        case = ReviewCase.from_json(
            connection.execute("SELECT document FROM review_cases").fetchone()[0]
        )
        proposal = ProposalSet.from_json(
            connection.execute("SELECT document FROM proposal_sets").fetchone()[0]
        )
        outcome = ShadowOutcomeSet.from_json(
            connection.execute(
                "SELECT document FROM shadow_decision_outcomes"
            ).fetchone()[0]
        )
        assert _automatic_decision_value(decision, case, report, proposal, outcome) == {
            "kind": "text",
            "value": "A voyage reveals a hidden city.",
        }
        result = next(
            r for r in proposal.results if r.destination.value == "description"
        )
        other = next(
            a for a in result.alternatives if a.alternative_id != decision.candidate_id
        )
        if substitution in {"winner", "citation"}:
            from mechanical_librarian.domain.shadow_outcome import _digest

            data = json.loads(outcome.to_json())
            source = next(
                o for o in data["outcomes"] if o["destination"] == "description"
            )
            if substitution == "winner":
                source["candidate_id"] = other.alternative_id
            source["citation_ids"] = list(other.evidence_references)
            source["qualification_candidate_id"] = "qualification_candidate_" + _digest(
                {
                    "action_id": source["action_id"],
                    "candidate_id": source["candidate_id"],
                    "catalogue_sha256": data["catalogue_sha256"],
                    "citation_ids": source["citation_ids"],
                    "combination_ids": source["combination_ids"],
                    "rule_id": source["rule_id"],
                }
            )
            source["outcome_id"] = "shadow_outcome_" + _digest(
                {k: v for k, v in source.items() if k != "outcome_id"}
            )
            data["outcome_set_id"] = "shadow_outcome_set_" + _digest(
                {k: v for k, v in data.items() if k != "outcome_set_id"}
            )
            outcome = ShadowOutcomeSet.from_json(
                json.dumps(data, sort_keys=True, separators=(",", ":"))
            )
            args = {
                k: v
                for k, v in json.loads(decision.to_json()).items()
                if k
                not in {"decision_id", "idempotency_digest", "origin", "schema_version"}
            }
            args["source_outcome_id"] = source["outcome_id"]
            if substitution == "winner":
                args["candidate_id"] = other.alternative_id
            decision = AutomaticDecision.from_json(
                AutomaticDecision.create(**args).to_json()
            )
            assert (
                next(
                    o for o in outcome.outcomes if o.destination == "description"
                ).citation_ids
                == other.evidence_references
            )
        elif substitution == "source":
            report = synopsis_report(
                summaries=(
                    "A changed source.",
                    "An explorer discovers an ancient city.",
                )
            )
        else:
            report = synopsis_report(current="Existing.\n\nParagraph.")
        with pytest.raises(FieldEffectError, match="authority_binding"):
            _automatic_decision_value(decision, case, report, proposal, outcome)


def test_actual_policy_preview_explains_retained_placeholder(tmp_path):
    from mechanical_librarian.application.metadata_policy import preview_policy
    from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile

    with closing(open_repository(tmp_path / "state.sqlite")) as repository:
        report = synopsis_report(summaries=("(duplicate)",))
        _, _ = publish_synopsis_preview(repository, report)
        profile = MetadataPolicyProfile.from_data(
            json.loads(repository.active_metadata_policy())
        )
        preview = preview_policy(profile, (report.target.library,), {}, repository)
        field = next(
            f
            for f in preview.libraries[0].cases[0].fields
            if f.field == "description_fill"
        )
        assert field.reason == "description_quality_floor_not_met"
        assert field.outcome == "abstain"
