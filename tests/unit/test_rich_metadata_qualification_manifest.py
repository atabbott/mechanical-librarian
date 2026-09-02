"""Public contract proof for the field-local F20 qualification candidate."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    QUALIFIED_POLICY_OPTIONS,
)
from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.metadata_policy import (
    OPTION_CATALOGUE,
    QUALIFIED_AUTOMATIC,
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_OPTION_DISPOSITIONS,
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_QUALIFICATION_CANDIDATE_SHA256,
    public_summary,
)

MANIFEST = (
    Path(__file__).parents[2]
    / "docs/reference/f20-rich-metadata-qualification-candidate-v7.json"
)
HISTORICAL_MANIFEST = (
    Path(__file__).parents[2]
    / "docs/reference/f20-rich-metadata-qualification-candidate-v6.json"
)
F19A_MANIFEST = (
    Path(__file__).parents[2] / "docs/reference/f19a-qualification-manifest-v5.json"
)
F38_MANIFEST = (
    Path(__file__).parents[2]
    / "docs/reference/f38-contributor-display-qualification-manifest-v1.json"
)
# The root binds the private qualification results into both manifests after
# this head is measured; until then these literals stand in and
# `test_qualification_digests_are_pinned` is the one red sentinel.
ROOT_PLACEHOLDER = "ROOT-FILLS"
PLACEHOLDER_DIGESTS = frozenset({"0" * 64, "1" * 64})

RICH_FIELDS = (
    "subtitle",
    "series",
    "genres",
    "tags",
    "description_fill",
    "isbn_form",
    "asin",
    "explicit",
)


def test_candidate_is_closed_field_local_and_exactly_authorizing() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert "f20-rich-metadata-qualification-candidate-v7" == manifest["schema"]
    assert "fixed-conservative-v19" == manifest["proposal_policy_version"]
    assert "decision_only" == manifest["automatic_authority"]
    passing = {
        "description_fill:safe_plain_text",
        "genres:fill_empty",
        "genres:add",
        "genres:replace",
        "series:primary_narrative",
        "series:source_order",
        "series:all_qualified",
        "subtitle:fill_empty",
        "tags:fill_empty",
        "tags:add",
        "tags:replace",
        "isbn_form:preserve_exact",
        "isbn_form:prefer_isbn13",
    }
    assert passing == set(manifest["passing_automatic_options"])
    assert ["explicit:unsupported"] == manifest["human_only_options"]
    assert ["narrators", "cover"] == manifest["excluded_destinations"]
    assert 15 == manifest["candidate_execution_count"]
    assert {
        "archived_replay_result_sha256",
        "development_result_sha256",
        "prior_aggregate_receipt_sha256",
        "prior_manifest_sha256",
    } == set(manifest["authority_rotation"])
    assert (
        "15cfb36db142e3783fa47a73157c72ecfd08bb282a967abc83fba152d1199205"
        == manifest["authority_rotation"]["prior_manifest_sha256"]
    )
    assert {
        "applicability_confirmation": {
            "automatic_precision": 1.0,
            "automatic_predictions": 20,
            "case_count": 23,
            "retained_candidates": 58,
            "wrong_work_admitted": 0,
        },
        "applicability_fields": ["tags"],
        "current_provider_comparison": {
            "case_count": 49,
            "gained_incorrect_projections": 0,
            "lost_correct_projections": 0,
            "network_access": True,
            "outcome_changes": 0,
            "provider_request_count": 456,
            "tags_correct_predictions_per_option": 7,
        },
        "deferred_applicability_fields": {
            "description_fill": {
                "baseline_correct_predictions": 9,
                "candidate_correct_predictions": 8,
                "gained_correct_projections": 3,
                "lost_correct_projections": 4,
                "reason": "live_correct_projection_churn",
            },
            "genres": {
                "baseline_correct_predictions": {
                    "add": 9,
                    "fill_empty": 30,
                    "replace": 9,
                },
                "candidate_correct_predictions": {
                    "add": 4,
                    "fill_empty": 12,
                    "replace": 4,
                },
                "reason": "live_correct_projection_loss",
            },
            "series": {
                "current_baseline_predictions": 0,
                "current_candidate_predictions": 0,
                "historical_qualified_predictions": {
                    "all_qualified": 20,
                    "primary_narrative": 19,
                    "source_order": 20,
                },
                "reason": "positive_preservation_unproved",
            },
        },
        "evaluated_reports": 49,
        "exposure_unit": "case_field_option",
        "genres_fill_empty_correct_predictions_by_state": {
            "empty": 5,
            "missing": 5,
            "null": 5,
        },
        "genres_fill_empty_predictions_by_state": {
            "empty": 5,
            "missing": 5,
            "null": 5,
        },
        "genres_fill_empty_target_states": ["missing", "null", "empty"],
        "network_access": False,
        "prior_unrelated_work_use_allowed": True,
        "replayed_provider_requests": 421,
        "retained_document_comparison": {
            "aggregate_metrics_equal": True,
            "case_count": 49,
            "network_access": False,
            "replayed_provider_requests": 421,
        },
        "f38_authority_rotation": {
            "aggregate_metrics_equal": True,
            "case_count": 49,
            "gained_projections": 0,
            "lost_projections": 0,
            "network_access": False,
            "replayed_provider_requests": 421,
        },
        "subtitle_segment_corroboration": {
            "adjudicated_counts": {"abstain": 37, "fill": 76, "unknown": 0},
            "case_count": 113,
            "holdout_claim": False,
            "network_access": False,
            "retained_49_report_route_predictions_at_base": 0,
            "review_agreement_count": 110,
            "route": "archived_744_development_challenge_set",
        },
        "subjective_field_truth_model": "exact_work_bound_source_projection",
        "series_truth_model": "valid_membership_with_preferred_value",
        "subtitle_fill_empty_correct_predictions_by_state": {
            "empty": 4,
            "missing": 4,
            "null": 4,
        },
        "subtitle_fill_empty_predictions_by_state": {
            "empty": 4,
            "missing": 4,
            "null": 4,
        },
        "subtitle_fill_empty_target_states": ["missing", "null", "empty"],
        "subtitle_truth_model": "ebook_manifestation_bibliographic_subtitle",
        "unseen_holdout_required": False,
    } == {
        name: value
        for name, value in manifest["evidence_method"].items()
        if name != "isbn_manifestation_election"
    }
    assert {
        "development": {
            "automatic_precision": 1.0,
            "automatic_predictions": 42,
            "distinct_automatic_cases_per_option": 14,
            "result_sha256": "2bf55518f44cffff1ab90a0446f085790b3859310f865ff55a0e551b7363e876",
            "target_states": ["missing", "null", "empty"],
            "zero_failures": True,
        },
        "holdout": {
            "automatic_precision": 1.0,
            "automatic_predictions": 30,
            "distinct_automatic_cases_per_option": 10,
            "result_sha256": "325e81e236630c12cac77bd01440e725b46b1f1fcf8cac5434daf1dd48b85241",
            "target_states": ["missing", "null", "empty"],
            "zero_failures": True,
        },
        "archived_replay": {
            "abstentions": 85,
            "decisions": 22,
            "result_sha256": "1918ded38c4b0b7075b589564647a94dba15c95c80d04aa45ce931e37f8c0272",
            "retained_alternatives": True,
            "scenarios": 107,
            "zero_forbidden_outcomes": True,
        },
        "holdout_continuity": {
            "receipt_sha256": "c540c44037e02961409e5a43d425c00a6ca71ba4800e817ad3dd0542a67973dc",
            "scope": "unresolved_work_identity_guard",
            "spent_holdout_result_sha256": "325e81e236630c12cac77bd01440e725b46b1f1fcf8cac5434daf1dd48b85241",
        },
    } == manifest["evidence_method"]["isbn_manifestation_election"]

    expected = {
        f"{field}:{option}"
        for field in RICH_FIELDS
        for option in OPTION_CATALOGUE[field]
    } - {"tags:off"}
    actual = {entry["identity"] for entry in manifest["options"]}
    assert expected == actual
    assert ["tags:off"] == manifest["policy_only_options"]
    assert {"explicit:unsupported"} == {
        entry["identity"]
        for entry in manifest["options"]
        if entry["disposition"] == "passing_human_only"
    }
    assert {
        "subtitle:corroborated_split",
        "description_fill:google_visible_text",
    } == {
        entry["identity"]
        for entry in manifest["options"]
        if entry["disposition"] == "not_qualified"
    }
    assert passing == {
        entry["identity"]
        for entry in manifest["options"]
        if entry["disposition"] == "passing_automatic"
    }
    terminal = {
        entry["identity"]
        for entry in manifest["options"]
        if entry["disposition"] == "insufficient_coverage"
    }
    assert (
        expected
        - passing
        - {"explicit:unsupported"}
        - {
            "subtitle:corroborated_split",
            "description_fill:google_visible_text",
        }
        == terminal
    )
    for entry in manifest["options"]:
        assert {
            "cohort_sufficiency",
            "disposition",
            "eligible_opportunities",
            "identity",
            "metrics",
        } == set(entry)
        assert entry["cohort_sufficiency"] in {
            "sufficient",
            "insufficient",
            "not_applicable",
        }
        assert {
            "automatic_precision",
            "automatic_predictions",
            "automatic_recall",
            "structural_or_sensitive_violations",
            "unsupported_output",
        } == set(entry["metrics"])
        assert 0 == entry["metrics"]["structural_or_sensitive_violations"]
    isbn = {
        entry["identity"]: entry
        for entry in manifest["options"]
        if entry["identity"] in passing
    }
    assert 1.0 == isbn["isbn_form:preserve_exact"]["metrics"]["automatic_precision"]
    assert 42 == isbn["isbn_form:preserve_exact"]["metrics"]["automatic_predictions"]
    assert 1.0 == isbn["isbn_form:prefer_isbn13"]["metrics"]["automatic_precision"]
    assert 42 == isbn["isbn_form:prefer_isbn13"]["metrics"]["automatic_predictions"]
    series = {
        entry["identity"]: entry
        for entry in manifest["options"]
        if entry["identity"].startswith("series:")
    }
    assert 19 == series["series:primary_narrative"]["metrics"]["automatic_predictions"]
    assert 20 == series["series:source_order"]["metrics"]["automatic_predictions"]
    assert 20 == series["series:all_qualified"]["metrics"]["automatic_predictions"]
    assert all(
        entry["metrics"]["automatic_precision"] == 1.0 for entry in series.values()
    )
    subjective = {
        entry["identity"]: entry
        for entry in manifest["options"]
        if entry["identity"] in passing
        and entry["identity"].split(":", 1)[0] in {"genres", "tags", "description_fill"}
    }
    assert subjective
    assert all(
        entry["metrics"]["automatic_precision"] == 1.0 for entry in subjective.values()
    )
    genre_fill = subjective["genres:fill_empty"]
    assert 147 == genre_fill["eligible_opportunities"]
    assert 15 == genre_fill["metrics"]["automatic_predictions"]
    subtitle_fill = {
        entry["identity"]: entry
        for entry in manifest["options"]
        if entry["identity"] == "subtitle:fill_empty"
    }["subtitle:fill_empty"]
    assert 113 == subtitle_fill["eligible_opportunities"]
    assert 0 == subtitle_fill["metrics"]["structural_or_sensitive_violations"]
    assert 0 == subtitle_fill["metrics"]["unsupported_output"]
    if subtitle_fill["metrics"]["automatic_precision"] != ROOT_PLACEHOLDER:
        assert 1.0 == subtitle_fill["metrics"]["automatic_precision"]
        assert 3 <= subtitle_fill["metrics"]["automatic_predictions"]


def test_candidate_exposes_only_closed_aggregate_contract_data() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert {
        "automatic_authority",
        "authority_rotation",
        "candidate_execution_count",
        "evidence_method",
        "excluded_destinations",
        "frozen_contract",
        "human_only_options",
        "options",
        "passing_automatic_options",
        "policy_only_options",
        "proposal_policy_version",
        "schema",
        "zero_tolerance",
    } == set(manifest)
    for name in ("automatic_decision", "compiler", "policy", "target_shape"):
        item = manifest["frozen_contract"][name]
        assert {"identity", "sha256"} == set(item)
        if item["sha256"] != ROOT_PLACEHOLDER:
            assert (
                hashlib.sha256(
                    (MANIFEST.parents[2] / item["identity"]).read_bytes()
                ).hexdigest()
                == item["sha256"]
            )
    for name in ("adjudication", "evaluator", "evidence_execution", "thresholds"):
        item = manifest["frozen_contract"][name]
        assert {"identity", "sha256"} == set(item)
        assert re.search(r"^[0-9a-f]{64}$", item["sha256"])
    assert {
        "automatic_decision_sha256",
        "preview_sha256",
        "proposal_policy_sha256",
        "proposal_policy_version",
    } == set(manifest["frozen_contract"]["isbn_manifestation_election"])
    assert (
        "fixed-conservative-v19"
        == manifest["frozen_contract"]["isbn_manifestation_election"][
            "proposal_policy_version"
        ]
    )
    for source, name in {
        "src/mechanical_librarian/domain/automatic_decision.py": "automatic_decision_sha256",
        "src/mechanical_librarian/domain/rich_metadata_preview.py": "preview_sha256",
        "src/mechanical_librarian/domain/proposal_set.py": "proposal_policy_sha256",
    }.items():
        bound = manifest["frozen_contract"]["isbn_manifestation_election"][name]
        if bound != ROOT_PLACEHOLDER:
            assert (
                hashlib.sha256((MANIFEST.parents[2] / source).read_bytes()).hexdigest()
                == bound
            )


def test_v6_manifest_remains_historical_readable_after_v7_rotation() -> None:
    historical = json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))
    current = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = current

    assert "f20-rich-metadata-qualification-candidate-v6" == historical["schema"]
    assert (
        hashlib.sha256(HISTORICAL_MANIFEST.read_bytes()).hexdigest()
        == current["authority_rotation"]["prior_manifest_sha256"]
    )
    assert (
        historical["passing_automatic_options"] == current["passing_automatic_options"]
    )
    assert {
        "evaluation_status",
        "failure_counts",
        "required_controls",
    } == set(manifest["zero_tolerance"])
    assert "passed" == manifest["zero_tolerance"]["evaluation_status"]
    assert {
        "cross_source_list_synthesis",
        "incomplete_ordered_list",
        "invalid_list_operation",
        "invented_value",
        "private_data_disclosure",
        "silent_description_rewrite_or_truncation",
        "stale_target_apply",
        "taxonomy_translation",
        "unsafe_description",
        "wrong_work_applicability",
        "wrong_manifestation_identifier",
    } == set(manifest["zero_tolerance"]["required_controls"])
    assert {
        name: 0 for name in manifest["zero_tolerance"]["required_controls"]
    } == manifest["zero_tolerance"]["failure_counts"]
    forbidden = {
        "accepted_gold",
        "case_id",
        "filename",
        "identifier_value",
        "path",
        "receipt",
        "source_sha256",
        "title",
        "value",
    }

    def keys(value):
        if isinstance(value, dict):
            yield from value
            for child in value.values():
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert forbidden.isdisjoint(keys(manifest))


def test_f19a_authority_is_preserved_and_exact_f20_is_added() -> None:
    assert {
        "title_layout:preserve",
        "title_layout:title_and_subtitle",
        "date_precision:year",
        "date_precision:full_date",
        "language_rendering:preserve",
        "language_rendering:display_name",
        "contributor_names:preserve",
        "contributor_names:canonical",
        "contributor_names:canonical:provider_consensus_display",
        "publisher_preference:publisher",
        "title:replace_target:provider_consensus_noise",
        "title:replace_target:decoration_tidy",
        "description_fill:safe_plain_text",
        "genres:fill_empty",
        "genres:add",
        "genres:replace",
        "series:primary_narrative",
        "series:source_order",
        "series:all_qualified",
        "subtitle:fill_empty",
        "tags:fill_empty",
        "tags:add",
        "tags:replace",
        "isbn_form:preserve_exact",
        "isbn_form:prefer_isbn13",
    } == QUALIFIED_POLICY_OPTIONS
    assert {
        "title_layout",
        "date_precision",
        "language_rendering",
        "contributor_names",
        "publisher_preference",
        "subtitle",
        "series",
        "genres",
        "tags",
        "description_fill",
        "isbn_form",
    } == QUALIFIED_AUTOMATIC
    policy = resolve_policy(MetadataPolicyProfile.builtin(), "library")
    assert "unsupported" == policy.qualification("explicit")
    assert "available" == policy.qualification("subtitle")
    assert "available" == policy.qualification("series")
    assert "available" == policy.qualification("description_fill")
    assert "available" == policy.qualification("isbn_form")
    for field in set(RICH_FIELDS[:-1]) - {
        "subtitle",
        "series",
        "genres",
        "description_fill",
        "isbn_form",
    }:
        assert "manual_only" == policy.qualification(field)


def test_f19a_successor_manifest_binds_f35_and_preserves_f36_evidence() -> None:
    manifest_bytes = F19A_MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)

    if F19A_QUALIFICATION_MANIFEST_SHA256 not in PLACEHOLDER_DIGESTS:
        assert (
            hashlib.sha256(manifest_bytes).hexdigest()
            == F19A_QUALIFICATION_MANIFEST_SHA256
        )
    assert "f19a-v13-result" == manifest["schema"]
    assert (
        "e4dc35aa64e3e052f3866225a492fab9572e5727194691782b881b60a46144ec"
        == manifest["prior_manifest_sha256"]
    )
    assert 12 == manifest["replayed_prior_option_count"]
    assert 0 == manifest["changed_prior_option_count"]
    assert 0 == manifest["zero_tolerance_failures"]
    assert {
        "automatic_precision": 1.0,
        "automatic_predictions": 8,
        "automatic_recall": 1.0,
        "case_count": 16,
        "classification_accuracy": 1.0,
        "zero_tolerance_failures": 0,
    } == manifest["split_metrics"]["holdout"]
    assert {
        "automatic_precision": 1.0,
        "automatic_predictions": 5,
        "automatic_recall": 1.0,
        "correct_predictions": 5,
        "eligible_opportunities": 50,
        "positive_opportunities": 5,
        "qualified": True,
        "unsupported_rate": 0.0,
    } == manifest["metrics"]["title:replace_target:provider_consensus_noise"]
    tranche = manifest["title_tranche"]
    assert {
        "candidate_execution_count",
        "changed_prior_option_count",
        "corpus_role",
        "decoration_equivalence",
        "evaluator_contract",
        "options",
        "replayed_prior_option_count",
        "zero_tolerance_failures",
    } == set(tranche)
    assert 0 == tranche["changed_prior_option_count"]
    assert "development_challenge_set" == tranche["corpus_role"]
    assert "f35-title-policy-v1+f41-decoration-tidy-v1" == tranche["evaluator_contract"]
    assert 12 == tranche["replayed_prior_option_count"]
    assert 0 == tranche["zero_tolerance_failures"]
    assert [
        "title:replace_target:provider_consensus_noise",
        "title:replace_target:decoration_tidy",
    ] == tranche["options"]
    assert 75 == tranche["decoration_equivalence"]["case_count"]
    assert 70 == tranche["decoration_equivalence"]["review_agreement_count"]
    assert 25 == tranche["decoration_equivalence"]["stratum_sizes"]["tidy_eligible"]
    assert (
        "f35-title-policy-v1+f36-language-equivalence-v1+f41-decoration-tidy-v1"
        == manifest["evaluator_contract"]
    )
    tidy = manifest["metrics"]["title:replace_target:decoration_tidy"]
    assert 0.0 == tidy["unsupported_rate"]
    assert tidy["qualified"] is True
    if tidy["automatic_precision"] != ROOT_PLACEHOLDER:
        assert 1.0 == tidy["automatic_precision"]
        assert 3 <= tidy["automatic_predictions"]


def test_qualification_digests_are_pinned() -> None:
    """The one red sentinel until the root binds the private qualification.

    It clears when both manifests carry real numbers and both runtime
    constants bind their exact file digests.
    """
    assert F19A_MANIFEST.exists() and MANIFEST.exists()
    assert F19A_QUALIFICATION_MANIFEST_SHA256 not in PLACEHOLDER_DIGESTS
    assert F20_QUALIFICATION_CANDIDATE_SHA256 not in PLACEHOLDER_DIGESTS
    assert ROOT_PLACEHOLDER not in F19A_MANIFEST.read_text(encoding="utf-8")
    assert ROOT_PLACEHOLDER not in MANIFEST.read_text(encoding="utf-8")


def test_f38_manifest_separately_qualifies_provider_consensus_contributor_display() -> (
    None
):
    manifest_bytes = F38_MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)

    assert (
        hashlib.sha256(manifest_bytes).hexdigest() == F38_QUALIFICATION_MANIFEST_SHA256
    )
    assert "f38-contributor-display-qualification-manifest-v1" == manifest["schema"]
    assert (
        f"{F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY}:"
        f"{F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION}" == manifest["policy_identity"]
    )
    assert "decision_only" == manifest["automatic_authority"]
    assert "development_exposed" == manifest["corpus_role"]
    assert False is manifest["holdout_claim"]
    assert {
        "automatic_precision": 1.0,
        "automatic_predictions": 3,
        "automatic_recall": 1.0,
        "positive_opportunities": 3,
        "zero_tolerance_failures": 0,
    } == manifest["metrics"]


def test_runtime_envelope_pins_every_terminal_disposition_and_authority() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if F20_QUALIFICATION_CANDIDATE_SHA256 not in PLACEHOLDER_DIGESTS:
        assert (
            hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
            == F20_QUALIFICATION_CANDIDATE_SHA256
        )
    passing = frozenset(
        {
            "description_fill:safe_plain_text",
            "genres:fill_empty",
            "genres:add",
            "genres:replace",
            "series:primary_narrative",
            "series:source_order",
            "series:all_qualified",
            "subtitle:fill_empty",
            "tags:fill_empty",
            "tags:add",
            "tags:replace",
            "isbn_form:preserve_exact",
            "isbn_form:prefer_isbn13",
        }
    )
    assert passing == frozenset(manifest["passing_automatic_options"])
    assert passing == F20_PASSING_AUTOMATIC_OPTIONS
    assert (
        {entry["identity"]: entry["disposition"] for entry in manifest["options"]}
        | {"tags:off": "policy_only"}
    ) == F20_OPTION_DISPOSITIONS
    summary = public_summary()
    assert set(F20_OPTION_DISPOSITIONS) == {
        f"{entry['field']}:{entry['option']}" for entry in summary
    }
    assert passing == {
        f"{entry['field']}:{entry['option']}"
        for entry in summary
        if entry["automatic_available"] is True
    }
    assert {
        "insufficient_coverage",
        "not_qualified",
        "passing_automatic",
        "policy_only",
        "passing_human_only",
    } == {entry["disposition"] for entry in summary}
    with pytest.raises(TypeError):
        F20_OPTION_DISPOSITIONS["genres:add"] = "passing_automatic"
    assert "passing_automatic" == F20_OPTION_DISPOSITIONS["genres:add"]
