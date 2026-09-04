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
    / "docs/reference/f20-rich-metadata-qualification-candidate-v11.json"
)
HISTORICAL_MANIFEST = (
    Path(__file__).parents[2]
    / "docs/reference/f20-rich-metadata-qualification-candidate-v10.json"
)
F19A_MANIFEST = (
    Path(__file__).parents[2] / "docs/reference/f19a-qualification-manifest-v5.json"
)
F38_MANIFEST = (
    Path(__file__).parents[2]
    / "docs/reference/f38-contributor-display-qualification-manifest-v1.json"
)
# The private qualification results are bound into both manifests after the
# final head is measured; `test_qualification_digests_are_pinned` fails while
# either literal still stands in.
ROOT_PLACEHOLDER = "ROOT-FILLS"
PLACEHOLDER_DIGESTS = frozenset({"0" * 64, "1" * 64})
# The segment-corroboration route reports these four counts and the two
# by-state maps only once the root has measured the final candidate.
MEASURED_SEGMENT_AGGREGATES = (
    "automatic_predictions",
    "correct_predictions",
    "positive_opportunities",
    "zero_tolerance_failures",
)
MEASURED_BY_STATE = (
    "subtitle_fill_empty_correct_predictions_by_state",
    "subtitle_fill_empty_predictions_by_state",
)

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

    assert "f20-rich-metadata-qualification-candidate-v11" == manifest["schema"]
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
    assert 23 == manifest["candidate_execution_count"]
    assert {
        "archived_replay_result_sha256",
        "development_result_sha256",
        "prior_aggregate_receipt_sha256",
        "prior_manifest_sha256",
    } == set(manifest["authority_rotation"])
    assert (
        "c8843d96dfda862b04f6269fd1ab3ab202338815ed5d0efbc0c4c480293adea3"
        == manifest["authority_rotation"]["prior_manifest_sha256"]
    )
    evidence = {
        name: value
        for name, value in manifest["evidence_method"].items()
        if name not in {"isbn_manifestation_election", "series_abstention_correction"}
    }
    segment = evidence.pop("subtitle_segment_corroboration")
    by_state = {name: evidence.pop(name) for name in MEASURED_BY_STATE}
    assert {
        "applicability_confirmation": {
            "automatic_precision": 1.0,
            "automatic_predictions": 20,
            "case_count": 23,
            "retained_candidates": 58,
            "wrong_work_admitted": 0,
        },
        "applicability_fields": ["description_fill", "tags"],
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
        "description_tiebreaker": {
            "confirmation": {
                "automatic_precision": 1.0,
                "automatic_predictions": 13,
                "case_count": 23,
                "correction_replays": 3,
                "errors": 0,
            },
            "contract": {
                "authority_order": ["hardcover", "google_books", "open_library"],
                "minimum_characters": 80,
                "minimum_words": 16,
                "reject_visible_cutoff": True,
                "selection_mode": "target_work_applicability_then_quality_floor_then_provider_authority",
            },
            "holdout": {
                "automatic_precision": 1.0,
                "automatic_predictions": 14,
                "case_count": 25,
                "errors": 0,
                "executions": 4,
                "initial_unseen_executions": 1,
                "spent_correction_replays": 3,
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
        "subjective_field_truth_model": "exact_work_bound_source_projection",
        "series_truth_model": "valid_membership_with_preferred_value",
        "subtitle_fill_empty_target_states": ["missing", "null", "empty"],
        "subtitle_truth_model": "ebook_manifestation_bibliographic_subtitle",
        "unseen_holdout_required": False,
    } == evidence
    assert {
        "adjudication_v3_sha256": "aad68fa4c458e3d863c076b6ce080b0a5f2786effbe38ab4ef51d365df5eb199",
        "allowed_membership_truth_with_separate_preference": True,
        "compiler_sha256": "c5d637b662b311a565feb766f772ce3481e4452f7ef8e3e88e18ba7cca0558d5",
        "corpus_role": "development_exposed",
        "current_report_evaluator_v2_sha256": "07b9d4130cbf43d41664c182db6ea0c436692163df0ef5da00ca978b7bcfcb37",
        "direct_epub_reopenings": 19,
        "fresh_mixed_confirmation": {
            "activation_issue": 895,
            "holdout_claim": False,
            "required": True,
            "status": "not_completed",
        },
        "independent_review_receipt_sha256": "1fa219248b9c9e267282feb66755d58632c5ffdf35a55bdcbba02f9196d2db91",
        "proposal_policy_sha256": "d893bc8567ce99e99b5f5f601caf4910cab42d2d7d3cadb0063a8ecd9a9d4789",
        "qualification_contract_sha256": "1f1fab25c6bdc52dcadfa46fc7fdaf4a7bd1f74e219f74e35624bab51198c075",
        "retained_document_evaluator_v2_sha256": "0363d622529256e3c4a8caafb6a853fa6df6e15e6039e6e0265745af7d4408e1",
        "retained_reports": 118,
        "series_rows_reviewed": 49,
        "truth_corrections": 15,
        "unchanged_cases": 34,
    } == {
        name: value
        for name, value in manifest["evidence_method"][
            "series_abstention_correction"
        ].items()
        if name != "cohorts"
    }
    assert {
        "49-v1": {
            "eligible_reports": 49,
            "positive_reports": 37,
            "projected_memberships": 21,
            "scored_exact_position_predictions": 19,
            "correct_scored_predictions": 19,
            "allowed_membership_unknown_position_unscored": 2,
            "automatic_precision": 1.0,
            "result_sha256": "8b4b0a05eff75fc40ed2552ac4b2c566621ced593319c0cc7000322e53bc6ece",
        },
        "49-v2": {
            "eligible_reports": 49,
            "positive_reports": 37,
            "projected_memberships": 22,
            "scored_exact_position_predictions": 20,
            "correct_scored_predictions": 20,
            "allowed_membership_unknown_position_unscored": 2,
            "automatic_precision": 1.0,
            "result_sha256": "bc0cf78f68ba9964d39a0be698d4924caea00c73034bdfbca55ebdb078795bf3",
        },
        "series-20": {
            "eligible_reports": 20,
            "positive_reports": 20,
            "projected_memberships": 15,
            "scored_exact_position_predictions": 15,
            "correct_scored_predictions": 15,
            "allowed_membership_unknown_position_unscored": 0,
            "automatic_precision": 1.0,
            "result_sha256": "203379df096fa6f506d2a8cf4d3916c253c38a1a20e5e4e7e6fdca03e7e66e29",
        },
        "retained-provider-documents": {
            "eligible_reports": 49,
            "positive_reports": 37,
            "projected_memberships": 19,
            "scored_exact_position_predictions": 19,
            "correct_scored_predictions": 19,
            "allowed_membership_unknown_position_unscored": 0,
            "automatic_precision": 1.0,
            "result_sha256": "4ab318ed289d30d24033dc77d101849da5fcc42a5248ef4602c768ca0959bf1b",
        },
    } == manifest["evidence_method"]["series_abstention_correction"]["cohorts"]
    assert {
        "adjudicated_counts": {"abstain": 37, "fill": 76, "unknown": 0},
        "case_count": 113,
        "holdout_claim": False,
        "network_access": False,
        "distinct_cases": 113,
        "retained_49_report_route_predictions_at_base": 0,
        "review_agreement_count": 110,
        "route": "archived_744_development_challenge_set",
        "target_states_measured": ["empty", "missing", "null"],
    } == {
        name: value
        for name, value in segment.items()
        if name not in MEASURED_SEGMENT_AGGREGATES
    }
    assert set(MEASURED_SEGMENT_AGGREGATES) <= set(segment)
    if segment["automatic_predictions"] != ROOT_PLACEHOLDER:
        # 113 cases measured under each fill_empty target state.
        assert 63 == segment["automatic_predictions"]
        assert 63 == segment["correct_predictions"]
        assert 228 == segment["positive_opportunities"]
        assert 0 == segment["zero_tolerance_failures"]
        assert {"empty": 21, "missing": 21, "null": 21} == by_state[
            "subtitle_fill_empty_predictions_by_state"
        ]
        assert {"empty": 21, "missing": 21, "null": 21} == by_state[
            "subtitle_fill_empty_correct_predictions_by_state"
        ]
    else:
        assert all(value == ROOT_PLACEHOLDER for value in by_state.values())
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
    description_fill = subjective["description_fill:safe_plain_text"]
    assert 48 == description_fill["eligible_opportunities"]
    assert 27 == description_fill["metrics"]["automatic_predictions"]
    assert 0.5625 == description_fill["metrics"]["automatic_recall"]
    subtitle_fill = {
        entry["identity"]: entry
        for entry in manifest["options"]
        if entry["identity"] == "subtitle:fill_empty"
    }["subtitle:fill_empty"]
    assert 0 == subtitle_fill["metrics"]["structural_or_sensitive_violations"]
    assert 0 == subtitle_fill["metrics"]["unsupported_output"]
    if subtitle_fill["metrics"]["automatic_precision"] != ROOT_PLACEHOLDER:
        assert 339 == subtitle_fill["eligible_opportunities"]  # 113 cases x 3 states
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
    assert {
        "candidate_head": "e1e13200a784229bcb5694e716c1c7b0e0a8128e",
        "confirmation_correction_receipt_sha256": "bcf746e16bdfd9ed8ee3174006546f4d3de7a375c3b52f62641d86470bbe136c",
        "confirmation_transform_filter_correction_receipt_sha256": "d7f27b3b68d7b87c1db75eeb310e2c9e5cb4882823eaf2f486c16773a548b1f7",
        "confirmation_unranked_authority_correction_receipt_sha256": "2bf2bcfac6e6b632bf0c8660a7bd5fa2541b2c1373a0d146bd32f55f38eaff29",
        "holdout_continuity_receipt_sha256": "16b87e09e1b51cf9162d583852bf54b8c964e8d12913fbf36486ce3d58faa024",
        "holdout_transform_filter_continuity_receipt_sha256": "b1960e81d472d048a762aa6d9a440dcf9bd3401328a860850db9e671d4e64b2c",
        "holdout_unranked_authority_continuity_receipt_sha256": "10a88f1701b9c249e4d6fb6d5657bacf597cfa548a1d9f9dc8e74e4fa741216c",
        "initial_holdout_receipt_sha256": "4637df813899ec39d2896fc626076d09924165433674cef35cfcc4fc0c6c48dd",
        "preview_sha256": "f5fdad27f901735ef0e3075bfb1a98cb4aa5eb0461cdc7e63f0deb638b05e52b",
    } == manifest["frozen_contract"]["description_tiebreaker"]
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


def test_v10_manifest_remains_historical_readable_after_v11_rotation() -> None:
    historical = json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))
    current = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = current

    assert "f20-rich-metadata-qualification-candidate-v10" == historical["schema"]
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
