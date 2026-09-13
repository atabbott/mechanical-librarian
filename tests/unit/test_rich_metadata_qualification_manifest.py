"""Public contract proof for the field-local F20 qualification candidate."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from mechanical_librarian.domain.audiobook_decision_qualification import (
    F46_PASSING_AUTOMATIC_OPTIONS,
    F46_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    F19A_QUALIFIED_POLICY_OPTIONS,
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
V12_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v12.json")
V13_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v13.json")
V14_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v14.json")
V15_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v15.json")
V16_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v16.json")
CURRENT_MANIFEST = MANIFEST.with_name(
    "f20-rich-metadata-qualification-candidate-v17.json"
)
ASSOCIATION_MANIFEST = MANIFEST.with_name(
    "f20-rich-metadata-qualification-candidate-v18.json"
)
V19_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v19.json")
V20_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v20.json")
V21_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v21.json")
TRANSPORT_V21_MANIFEST = MANIFEST.with_name(
    "f20-rich-metadata-qualification-candidate-v21-local-transport.json"
)
V22_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v22.json")
V23_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v23.json")
V24_MANIFEST = MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v24.json")
LATEST_MANIFEST = MANIFEST.with_name(
    "f20-rich-metadata-qualification-candidate-v34.json"
)
ASSOCIATION_F19A_MANIFEST = MANIFEST.with_name("f19a-qualification-manifest-v6.json")
V7_F19A_MANIFEST = MANIFEST.with_name("f19a-qualification-manifest-v7.json")
V8_F19A_MANIFEST = MANIFEST.with_name("f19a-qualification-manifest-v8.json")
V9_F19A_MANIFEST = MANIFEST.with_name("f19a-qualification-manifest-v9.json")
V10_F19A_MANIFEST = MANIFEST.with_name("f19a-qualification-manifest-v10.json")
LATEST_F19A_MANIFEST = MANIFEST.with_name("f19a-qualification-manifest-v20.json")
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


def test_v17_preserves_historical_v11_to_v17_bytes() -> None:
    manifest = json.loads(CURRENT_MANIFEST.read_bytes())
    assert manifest["schema"] == "f20-rich-metadata-qualification-candidate-v17"
    assert manifest["automatic_authority"] == "decision_only"
    prior = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert prior == "4ede15a2aa84e8726ea2dff4e4f9f0d04b27a70276ed53c400c2de5f7619d445"
    v12 = hashlib.sha256(V12_MANIFEST.read_bytes()).hexdigest()
    assert v12 == "091cdf7f08f60e9e4e1860ff574a9c04c405c914d498560e9802294b69c53a30"
    assert (
        json.loads(V12_MANIFEST.read_bytes())["authority_rotation"][
            "prior_manifest_sha256"
        ]
        == prior
    )
    v13 = hashlib.sha256(V13_MANIFEST.read_bytes()).hexdigest()
    assert v13 == "70563a47e768fe51cb911511ca67849893e4495266c84bb2e9fc7f6165ed2879"
    assert (
        json.loads(V13_MANIFEST.read_bytes())["authority_rotation"][
            "prior_manifest_sha256"
        ]
        == v12
    )
    v14 = hashlib.sha256(V14_MANIFEST.read_bytes()).hexdigest()
    assert v14 == "4a89dfc24a27bbe2a548a9b0c5ad2793cf2da1a2b0445f9dd1ad2f9323822bc9"
    assert (
        json.loads(V14_MANIFEST.read_bytes())["authority_rotation"][
            "prior_manifest_sha256"
        ]
        == v13
    )
    v15 = hashlib.sha256(V15_MANIFEST.read_bytes()).hexdigest()
    assert v15 == "a7af724b0711177ff04ff1ae56c8a7db42c1166fe97390c29e60fed506440500"
    assert (
        json.loads(V15_MANIFEST.read_bytes())["authority_rotation"][
            "prior_manifest_sha256"
        ]
        == v14
    )
    v16 = hashlib.sha256(V16_MANIFEST.read_bytes()).hexdigest()
    assert v16 == "14061bb1d1df8305d75cc2330ce37a931d01c954f1be3cd03e7587558704fbfc"
    assert (
        json.loads(V16_MANIFEST.read_bytes())["authority_rotation"][
            "prior_manifest_sha256"
        ]
        == v15
    )
    assert manifest["authority_rotation"]["prior_manifest_sha256"] == v16
    assert prior != F20_QUALIFICATION_CANDIDATE_SHA256
    assert (
        hashlib.sha256(CURRENT_MANIFEST.read_bytes()).hexdigest()
        == "4420a5d1ea34f2e46e9c854b07c068e827324fbce942b4da8c301b3cd7409168"
    )


def test_v16_records_complete_non_cover_continuity_without_fresh_holdout() -> None:
    manifest = json.loads(CURRENT_MANIFEST.read_bytes())
    evidence = manifest["evidence_method"]["cover_choice_source_rotation"]

    assert manifest["proposal_policy_version"] == "fixed-conservative-v22"
    assert evidence["qualification_method"] == "owner_approved_exposed_regression"
    assert evidence["fresh_holdout_claim"] is False
    retained = evidence["same_mode_f20_continuity"]
    assert retained["options_or_modes_pooled"] is False
    assert retained["lost_correct_projections"] == 0
    assert retained["gained_incorrect_projections"] == 0
    assert {item["mode"] for item in retained["modes"]} == {
        "live-three-provider-49-v1",
        "live-three-provider-49-v2",
    }
    assert all(
        item["reports"] == 49
        and item["outcomes"] == 588
        and item["complete_outcomes_equal"] is True
        for item in retained["modes"]
    )
    non_series = evidence["non_series_continuity"]
    assert non_series["reports"] == 49
    assert non_series["complete_previews"] == 980
    assert non_series["canonical_complete_previews_equal"] is True
    series = evidence["series_continuity"]
    assert series["reports"] == 36
    assert series["complete_previews"] == 108
    assert series["canonical_complete_previews_equal"] is True
    assert series["per_batch_option_aggregates_equal"] is True
    assert series["sealed_bindings_unchanged"] is True
    assert series["fresh_holdout_claim"] is False
    exact = evidence["exact_description_source"]
    assert {
        name: exact["confirmation"][name]
        for name in ("cases", "automatic_predictions", "correct_predictions", "errors")
    } == {
        "cases": 23,
        "automatic_predictions": 13,
        "correct_predictions": 13,
        "errors": 0,
    }
    assert {
        name: exact["holdout"][name]
        for name in (
            "cases",
            "automatic_predictions",
            "correct_predictions",
            "errors",
            "spent",
        )
    } == {
        "cases": 25,
        "automatic_predictions": 14,
        "correct_predictions": 14,
        "errors": 0,
        "spent": True,
    }
    assert exact["complete_outcomes_and_source_bindings_equal"] is True
    assert exact["lost_correct_projections"] == 0
    assert exact["gained_incorrect_projections"] == 0
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for section in (retained, non_series, series, exact)
        for name, value in section.items()
        if name.endswith("sha256")
    )


def test_v14_records_target_work_regression_without_fresh_holdout_claim() -> None:
    manifest = json.loads(V14_MANIFEST.read_bytes())
    evidence = manifest["evidence_method"]["target_work_contradiction"]

    assert manifest["proposal_policy_version"] == "fixed-conservative-v20"
    assert evidence["qualification_method"] == "owner_approved_exposed_regression"
    assert evidence["fresh_holdout_claim"] is False
    assert evidence["measured_candidate_head"] == (
        "d8d724fdbbe82cef2a4f18f2cc801c452c15d994"
    )
    retained = evidence["same_mode_retained_reports"]
    assert retained["evaluator_sha256"] == (
        "07b9d4130cbf43d41664c182db6ea0c436692163df0ef5da00ca978b7bcfcb37"
    )
    assert retained["correct_genre_projections"] == {
        "add": 9,
        "fill_empty": 30,
        "replace": 9,
    }
    assert retained["restored_correct_genre_projections"] == {
        "add": 3,
        "fill_empty": 9,
        "replace": 3,
        "total": 15,
    }
    for name in (
        "changed_outcomes",
        "changed_values",
        "lost_correct_projections",
        "gained_incorrect_projections",
        "series_option_changes",
    ):
        assert retained[name] == 0
    assert {artifact["mode"] for artifact in retained["artifacts"]} == {
        "live-three-provider-49-v1",
        "live-three-provider-49-v2",
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", artifact[name])
        for artifact in retained["artifacts"]
        for name in (
            "baseline_result_sha256",
            "candidate_result_sha256",
            "comparison_sha256",
        )
    )
    continuity = evidence["non_series_continuity"]
    assert continuity["changed_preview_observations"] == 18
    assert continuity["changed_fields"] == {
        "genres:add": 6,
        "genres:fill_empty": 6,
        "genres:replace": 6,
    }
    assert continuity["unchanged_non_genre_observations"] == 686
    series = evidence["series_continuity"]
    assert series["reports"] == 36
    assert series["source_schema"] == "metadata-report-v37"
    assert series["target_schema"] == "metadata-report-v38"
    assert series["sealed_bindings_unchanged"] is True
    assert series["fresh_holdout_claim"] is False
    assert series["monotonic_comparison"] == {
        "removed_projections": 6,
        "unchanged_projections": 39,
        "unchanged_nonprojections": 63,
    }
    assert series["batches"] == {
        "confirmation": {
            "cases": 24,
            "correct_projections_per_option": 9,
            "abstentions_per_option": 15,
            "membership_precision": 1,
            "sequence_precision": 1,
            "negative_false_positives": 0,
        },
        "final": {
            "cases": 12,
            "correct_projections_per_option": 4,
            "abstentions_per_option": 8,
            "membership_precision": 1,
            "sequence_precision": 1,
            "negative_false_positives": 0,
        },
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", series[name])
        for name in (
            "artifact_sha256",
            "receipt_sha256",
            "wrapper_sha256",
            "sealed_replay_sha256",
        )
    )
    assert evidence["synthetic_request_cost"] == {
        "actual_total_requests_before": 2,
        "actual_total_requests_after": 4,
        "added_work_fallback_requests": 2,
        "provider_count": 2,
    }
    assert evidence["retained_request_cost"] == {
        "network_requests": 0,
        "production_scheduling_estimate": "not_measured",
    }
    assert manifest["external_acceptance_required"] == [
        "Supported isolated Audiobookshelf decision-to-effect qualification",
        "Exact-installed acceptance and terminal candidate preflight",
    ]


def test_v15_records_exact_description_source_and_complete_continuity() -> None:
    manifest = json.loads(V15_MANIFEST.read_bytes())
    evidence = manifest["evidence_method"]["exact_description_source"]

    assert manifest["proposal_policy_version"] == "fixed-conservative-v21"
    assert evidence["qualification_method"] == "owner_approved_exposed_regression"
    assert evidence["fresh_holdout_claim"] is False
    assert evidence["measured_candidate_head"] == (
        "e13809341a66371c008e154df80a8b7d50ae1b50"
    )
    f37 = evidence["f37_description"]
    assert f37["evaluator_sha256"] == (
        "2d0762bea9cf1ad3e79eabfa64c26b999ecd7ca0bf94a5aec0dfc237f515780d"
    )
    assert f37["execution_context"] == "correction_replay"
    assert f37["confirmation"] == {
        "cases": 23,
        "automatic_predictions": 13,
        "correct_predictions": 13,
        "errors": 0,
        "automatic_precision": 1,
        "receipt_sha256": (
            "00722ad492e9c40af6bd0085e6792befe977898f62f06f2b2b244ba00b81a084"
        ),
    }
    assert f37["holdout"] == {
        "cases": 25,
        "automatic_predictions": 14,
        "correct_predictions": 14,
        "errors": 0,
        "automatic_precision": 1,
        "spent": True,
        "receipt_sha256": (
            "7388728de7f072d3fdd9537dfea204d2cfff03ed7d5ec44d272857b99305a126"
        ),
    }
    assert re.fullmatch(r"[0-9a-f]{64}", f37["correctness_comparison_sha256"])

    retained = evidence["same_mode_f20_continuity"]
    assert retained["options_or_modes_pooled"] is False
    assert retained["gained_incorrect_projections"] == 0
    assert retained["lost_correct_projections"] == 0
    assert {mode["mode"] for mode in retained["modes"]} == {
        "live-three-provider-49-v1",
        "live-three-provider-49-v2",
    }
    assert all(
        mode["reports"] == 49
        and mode["outcomes"] == 588
        and mode["complete_outcomes_equal"] is True
        and mode["non_description_outcomes_equal"] is True
        and mode["description_outcomes_equal"] is True
        for mode in retained["modes"]
    )
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", mode[name])
        for mode in retained["modes"]
        for name in (
            "result_sha256",
            "comparison_sha256",
            "correctness_comparison_sha256",
        )
    )

    non_series = evidence["non_series_continuity"]
    assert non_series["observations"] == 980
    assert non_series["description_observations"] == 98
    assert non_series["non_description_observations"] == 882
    assert non_series["canonical_complete_previews_equal"] is True
    assert non_series["comparator_reported_changes"] == 980
    series = evidence["series_continuity"]
    assert series["reports"] == 36
    assert series["complete_previews"] == 108
    assert series["private_previews_equal"] is True
    assert series["per_batch_option_aggregates_equal"] is True
    assert series["fresh_holdout_claim"] is False
    assert series["network_requests"] == 0
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", section[name])
        for section in (non_series, series)
        for name in (
            ("artifact_sha256", "comparison_sha256")
            if section is non_series
            else ("artifact_sha256", "receipt_sha256", "comparison_sha256")
        )
    )
    assert any(
        "previously exposed" in limitation for limitation in evidence["limitations"]
    )
    assert any(
        "no equivalent local/provider" in limitation
        for limitation in evidence["limitations"]
    )


@pytest.mark.parametrize("batch", ("confirmation", "final"))
@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_v12_separates_membership_sequence_and_distinct_book_support(
    batch: str, option: str
) -> None:
    manifest = json.loads(V12_MANIFEST.read_bytes())
    series = manifest["evidence_method"]["series"]
    assert series["precision_required"] == 1
    assert series["minimum_distinct_scored_books_per_option_per_batch"] == 3
    assert series["options_or_batches_pooled"] is False
    assert series["metric_result_alone_grants_authority"] is False
    assert series["recall_is_gate"] is False
    assert series["unknown_positions"] == "unscored"
    assert series["known_wrong_positions"] == "block_option"
    evidence = series["batches"][batch]
    expected = (
        (24, 19, 4, 1, 10, 14, 8) if batch == "confirmation" else (12, 7, 3, 2, 5, 7, 5)
    )
    cases, supported, negative, unknown, correct, abstained, scored = expected
    assert evidence["case_count"] == cases
    assert evidence["source_states"] == {
        "supported": supported,
        "known_empty": negative,
        "unknown": unknown,
    }
    metrics = evidence["options"][option]
    assert metrics["complete_projection_counts"] == {
        "correct": correct,
        "abstained": abstained,
    }
    assert metrics["distinct_membership_scored_books"] == correct
    assert metrics["distinct_sequence_scored_books"] == scored
    assert metrics["membership_gate"] == "passed"
    assert metrics["sequence_gate"] == "qualified"
    assert metrics["negative_false_positives"] == 0
    assert metrics["supported_complete_projection_coverage"] == correct / supported
    for name in (
        "membership_projection_precision",
        "membership_claim_precision",
        "sequence_precision",
    ):
        assert metrics[name] == 1.0
    extra_convention = batch == "final" and option == "all_qualified"
    assert metrics["membership_claim_counts"] == {
        "correct": 7 if extra_convention else correct
    }
    assert metrics["sequence_claim_counts"] == (
        {"correct": 8, "missing": 1, "unknown": 1}
        if batch == "confirmation"
        else {"correct": 6, "unknown": 1}
        if extra_convention
        else {"correct": 5}
    )
    target = metrics["target_value_metrics"]
    assert target["membership_counts"] == {"correct": correct}
    assert target["sequence_scored_cases"] == scored
    assert target["sequence_counts"] == (
        {"correct": 8, "missing": 1, "unknown": 1}
        if batch == "confirmation"
        else {"correct": 5}
    )
    assert target["activation_authorized"] is False
    correction = evidence["correction"]
    assert correction["source_rows_corrected"] == (1 if batch == "confirmation" else 2)
    assert correction["blind_source_review"] is True
    for name in (
        "candidate_changed",
        "scorer_changed",
        "thresholds_changed",
        "recaptured",
    ):
        assert correction[name] is False
    for name in (
        "result_sha256",
        "execution_seal_sha256",
        "capture_receipt_sha256",
        "original_evaluation_sha256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", evidence[name])


@pytest.mark.parametrize(
    "manifest_path",
    (
        V12_MANIFEST,
        V13_MANIFEST,
        V14_MANIFEST,
        V15_MANIFEST,
        V16_MANIFEST,
        CURRENT_MANIFEST,
    ),
)
def test_v12_to_v17_retain_non_series_dispositions_and_historical_metrics(
    manifest_path: Path,
) -> None:
    historical = json.loads(MANIFEST.read_bytes())
    current = json.loads(manifest_path.read_bytes())
    assert (
        current["passing_automatic_options"] == historical["passing_automatic_options"]
    )
    prior_options = {entry["identity"]: entry for entry in historical["options"]}
    for entry in current["options"]:
        if not entry["identity"].startswith("series:"):
            assert entry == {
                **prior_options[entry["identity"]],
                "evidence_origin": "historical_v11",
            }
    controls = current["zero_tolerance"]
    assert controls["evaluation_status"] == "passed"
    assert controls["failure_counts"] == {
        name: 0 for name in controls["required_controls"]
    }
    assert {
        "populated_series_replacement",
        "historical_qualification_write",
        "inferred_series_sequence",
    } <= set(controls["required_controls"])


@pytest.mark.parametrize("batch", ("confirmation", "final"))
@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_v13_records_exposed_regression_without_relabelling_holdouts(
    batch: str, option: str
) -> None:
    manifest = json.loads(V13_MANIFEST.read_bytes())
    series = manifest["evidence_method"]["series"]
    assert series["qualification_method"] == "owner_approved_exposed_regression"
    assert series["fresh_holdout_claim"] is False
    assert series["precision_required"] == 1
    assert series["minimum_distinct_scored_books_per_option_per_batch"] == 3
    assert series["options_or_batches_pooled"] is False
    assert series["metric_result_alone_grants_authority"] is False
    assert series["recall_is_gate"] is False
    replay = series["replay"]
    assert (
        replay["candidate_result_sha256"]
        == "1e8414fa7903b1ba3a80c7098bb95ffac2caab4eff747cb92d46f030129ee611"
    )
    assert (
        replay["sha256"]
        == "8e2c4a7ca4229fb981a99adf3fb64b5a7fd5eba5f8094babd1e85e7c3d01b3d5"
    )
    assert replay["complete_preview_comparison"] == {
        "removed_option_projections": 6,
        "unchanged_option_projections": 39,
        "unchanged_nonprojections": 63,
        "new_or_changed_projections": 0,
    }
    evidence = series["batches"][batch]
    historical = json.loads(V12_MANIFEST.read_bytes())["evidence_method"]["series"][
        "batches"
    ][batch]
    assert evidence["historical_capture_and_adjudication"] == {
        name: entry
        for name, entry in historical.items()
        if name not in {"options", "case_count", "source_states"}
    }
    assert evidence["case_count"] == historical["case_count"]
    assert evidence["source_states"] == historical["source_states"]
    assert evidence["evidence_role"] == "previously_exposed_" + batch
    assert evidence["result_sha256"] == replay["candidate_result_sha256"]
    correct, abstained, scored = (9, 15, 7) if batch == "confirmation" else (4, 8, 4)
    metrics = evidence["options"][option]
    assert metrics["complete_projection_counts"] == {
        "correct": correct,
        "abstained": abstained,
    }
    assert metrics["distinct_membership_scored_books"] == correct
    assert metrics["distinct_sequence_scored_books"] == scored
    extra = batch == "final" and option == "all_qualified"
    assert metrics["membership_claim_counts"] == {"correct": 6 if extra else correct}
    assert metrics["sequence_claim_counts"] == (
        {"correct": 7, "missing": 1, "unknown": 1}
        if batch == "confirmation"
        else {"correct": 5, "unknown": 1}
        if extra
        else {"correct": 4}
    )
    assert metrics["membership_gate"] == "passed"
    assert metrics["sequence_gate"] == "qualified"
    assert (
        metrics["negative_false_positives"]
        == metrics["unknown_source_projections"]
        == 0
    )
    assert (
        metrics["supported_complete_projection_coverage"]
        == correct / evidence["source_states"]["supported"]
    )
    for name in (
        "membership_projection_precision",
        "membership_claim_precision",
        "sequence_precision",
    ):
        assert metrics[name] == 1
    target = metrics["target_value_metrics"]
    assert target["projection_counts"] == metrics["complete_projection_counts"]
    assert target["membership_counts"] == {"correct": correct}
    assert target["sequence_counts"] == (
        {"correct": 7, "missing": 1, "unknown": 1}
        if batch == "confirmation"
        else {"correct": 4}
    )
    assert target["sequence_scored_cases"] == scored
    assert target["activation_authorized"] is False


def test_historical_v11_retains_its_exact_option_metrics_and_evidence() -> None:
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
        "compiler_sha256": "cbfbdb7930659da30025a3712056053ca5aa370c419cbbb0c3d6f0f8749b4579",
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
        "proposal_policy_sha256": "9da439d1967eeaccfe70525cacd1b515ccf3d8020bef8ef0db658d1ba33a0c32",
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
            "projected_memberships": 19,
            "scored_exact_position_predictions": 17,
            "correct_scored_predictions": 17,
            "allowed_membership_unknown_position_unscored": 2,
            "automatic_precision": 1.0,
            "result_sha256": "2db875a373342d6c27a60fc6589b09b9f635300215b04429a81ca7e088770021",
        },
        "49-v2": {
            "eligible_reports": 49,
            "positive_reports": 37,
            "projected_memberships": 20,
            "scored_exact_position_predictions": 18,
            "correct_scored_predictions": 18,
            "allowed_membership_unknown_position_unscored": 2,
            "automatic_precision": 1.0,
            "result_sha256": "2d35e76f77d41e333d35ea1e2fef48a99d3805c571038d9b6dd903d03548bcf8",
        },
        "series-20": {
            "eligible_reports": 20,
            "positive_reports": 20,
            "projected_memberships": 13,
            "scored_exact_position_predictions": 13,
            "correct_scored_predictions": 13,
            "allowed_membership_unknown_position_unscored": 0,
            "automatic_precision": 1.0,
            "result_sha256": "bd83cc28c362e79b27a72da4803db8dd1cdaa7e8da3ddf07ba4a9207f76158f4",
        },
        "retained-provider-documents": {
            "eligible_reports": 49,
            "positive_reports": 37,
            "projected_memberships": 15,
            "scored_exact_position_predictions": 15,
            "correct_scored_predictions": 15,
            "allowed_membership_unknown_position_unscored": 0,
            "automatic_precision": 1.0,
            "result_sha256": "5b93f939f31f70868e6fbfe29c4c4ac3d3db883cb20d19cdb311b56ed2ae0389",
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
    } - {
        "tags:off",
        # These later F46-only choices were not part of the immutable v11 corpus.
        "description_fill:replace_safe_plain_text",
        "series:add_qualified",
        "series:replace_qualified",
    }
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
        # Historical source bindings describe the earlier measured compiler.
        # Their immutable bytes are pinned above; current source is bound by v13.
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
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
    for _source, name in {
        "src/mechanical_librarian/domain/automatic_decision.py": "automatic_decision_sha256",
        "src/mechanical_librarian/domain/rich_metadata_preview.py": "preview_sha256",
        "src/mechanical_librarian/domain/proposal_set.py": "proposal_policy_sha256",
    }.items():
        bound = manifest["frozen_contract"]["isbn_manifestation_election"][name]
        assert re.fullmatch(r"[0-9a-f]{64}", bound)


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
    assert forbidden.isdisjoint(keys(json.loads(CURRENT_MANIFEST.read_bytes())))


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
        "title:replace_target:isbn_creator_supported",
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
    } == QUALIFIED_POLICY_OPTIONS - F46_PASSING_AUTOMATIC_OPTIONS
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

    assert (
        hashlib.sha256(manifest_bytes).hexdigest()
        == "f7a4493d94f0188e6974aad1b68f9238d9e7733aa1a2e064e5663e385af0fe6e"
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
    manifest = json.loads(LATEST_MANIFEST.read_text(encoding="utf-8"))
    assert (
        hashlib.sha256(LATEST_MANIFEST.read_bytes()).hexdigest()
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
    assert {entry["candidate_sha256"] for entry in summary} == {
        F20_QUALIFICATION_CANDIDATE_SHA256
    }
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


def test_association_successors_bind_current_sources_and_retained_scoring():
    rich = json.loads(ASSOCIATION_MANIFEST.read_text())
    title = json.loads(ASSOCIATION_F19A_MANIFEST.read_text())
    assert (
        hashlib.sha256(ASSOCIATION_F19A_MANIFEST.read_bytes()).hexdigest()
        == "7f0bfa050ec5956f9d21fcb521f5c06f3a9310cbee42fd7e206a4ec2d71be1e6"
    )
    assert (
        title["prior_manifest_sha256"]
        == hashlib.sha256(F19A_MANIFEST.read_bytes()).hexdigest()
    )
    assert (
        rich["authority_rotation"]["prior_manifest_sha256"]
        == hashlib.sha256(CURRENT_MANIFEST.read_bytes()).hexdigest()
    )
    assert (
        rich["proposal_policy_version"]
        == title["proposal_policy_version"]
        == "fixed-conservative-v23"
    )
    assert rich["schema"] == "f20-rich-metadata-qualification-candidate-v18"
    sources = rich["frozen_contract"]["behavior_sources"]
    assert set(sources) == set(
        json.loads(CURRENT_MANIFEST.read_text())["frozen_contract"]["behavior_sources"]
    )
    assert (
        hashlib.sha256(ASSOCIATION_MANIFEST.read_bytes()).hexdigest()
        == "10b10aa37008e6cfeb01745ebe75eed15913ca2e7c325517e110dbaf30bbdb74"
    )
    evidence = rich["evidence_method"]["title_component_associations"]
    assert title["association_requalification"] == evidence
    latest_projection = json.loads(LATEST_MANIFEST.read_text())["source_closure"]
    assert set(evidence["review_projection_followup"]["behavior_sources"]) < set(
        latest_projection
    )
    for name, digest in latest_projection.items():
        assert (
            hashlib.sha256((MANIFEST.parents[2] / name).read_bytes()).hexdigest()
            == digest
        )
    assert evidence["fresh_holdout_claim"] is False
    assert evidence["title"]["metrics"]["zero_tolerance_failures"] == 0
    assert evidence["title"]["metrics"]["automatic_predictions"] == 17
    assert evidence["title"]["semantic_decisions_equal"] is True
    assert evidence["subtitle"]["metrics"]["distinct_cases"] == 113
    for state in ("missing", "null", "empty"):
        metrics = evidence["subtitle"]["metrics"]["by_target_state"][state]
        assert metrics["correct_predictions"] == metrics["automatic_predictions"] == 21
        assert metrics["zero_tolerance_failures"] == 0
    assert evidence["continuity"]["series"]["observations"] == 108
    assert evidence["continuity"]["other"]["observations"] == 980
    assert all(
        value["semantic_outcomes_equal"] for value in evidence["continuity"].values()
    )
    assert (
        rich["passing_automatic_options"]
        == json.loads(CURRENT_MANIFEST.read_text())["passing_automatic_options"]
    )


def test_inventaire_successor_binds_current_sources_and_preserves_authority() -> None:
    current = json.loads(V19_MANIFEST.read_text())
    prior = json.loads(ASSOCIATION_MANIFEST.read_text())
    assert (
        current["authority_rotation"]["prior_manifest_sha256"]
        == hashlib.sha256(ASSOCIATION_MANIFEST.read_bytes()).hexdigest()
    )
    assert (
        current["proposal_policy_version"]
        == prior["proposal_policy_version"]
        == "fixed-conservative-v23"
    )
    assert current["options"] == prior["options"]
    assert current["passing_automatic_options"] == prior["passing_automatic_options"]
    assert (
        hashlib.sha256(V19_MANIFEST.read_bytes()).hexdigest()
        == "47b142cfea8c5d73ed267cb71f85a0edf3ab4b646ad29bba457a1f03b7aa37f2"
    )
    proof = current["evidence_method"]
    assert proof["non_series_complete_previews"] == 980
    assert proof["series_complete_previews"] == 108
    assert proof["all_13_options_unchanged"] is True
    assert proof["retained_source_contract"]["canonical_sources_admitted"] == 21
    assert proof["retained_source_contract"]["full_preparation_recovery_claim"] is False
    for name in ("non-series.json", "series.json"):
        assert proof["continuity"][name]["complete_semantic_results_equal"] is True


def test_inventaire_f19a_successor_preserves_historical_measurements_and_options() -> (
    None
):
    current = json.loads(V7_F19A_MANIFEST.read_text())
    prior = json.loads(ASSOCIATION_F19A_MANIFEST.read_text())
    assert (
        hashlib.sha256(V7_F19A_MANIFEST.read_bytes()).hexdigest()
        == "62e8db714fe9a35065bb700e6eac58f1611ec3acca5a90e29965fa2e5c5e6014"
    )
    assert (
        current["prior_manifest_sha256"]
        == hashlib.sha256(ASSOCIATION_F19A_MANIFEST.read_bytes()).hexdigest()
    )
    assert {
        k: v
        for k, v in current.items()
        if k
        not in {
            "prior_manifest_sha256",
            "inventaire_source_closure",
            "historical_evidence",
        }
    } == {k: v for k, v in prior.items() if k != "prior_manifest_sha256"}
    closure = current["inventaire_source_closure"]
    assert closure["qualified_policy_options"] == sorted(
        F19A_QUALIFIED_POLICY_OPTIONS - {"title:replace_target:isbn_creator_supported"}
    )
    assert len(closure["qualified_policy_options"]) == 11
    assert closure["all_evaluator_bodies_unchanged"] is True
    assert closure["fresh_holdout_claim"] is False
    assert closure["fresh_scoring_run"] is False
    assert current["historical_evidence"]["measurements_reexecuted"] is False
    assert set(closure["behavior_sources"]) <= set(
        json.loads(LATEST_F19A_MANIFEST.read_bytes())["source_closure"]
    )


def test_provider_contract_rotation_binds_complete_sources_without_rescoring():
    current = json.loads(V20_MANIFEST.read_text())
    prior = json.loads(V19_MANIFEST.read_text())
    assert current["schema"] == "f20-rich-metadata-qualification-candidate-v20"
    assert (
        current["authority_rotation"]["prior_manifest_sha256"]
        == hashlib.sha256(V19_MANIFEST.read_bytes()).hexdigest()
    )
    for field in (
        "options",
        "passing_automatic_options",
        "proposal_policy_version",
        "automatic_authority",
        "evidence_method",
    ):
        assert current[field] == prior[field]
    assert len(current["passing_automatic_options"]) == 13
    closure = current["provider_contract_source_closure"]
    assert closure["fresh_scoring_run"] is False
    assert closure["fresh_holdout_claim"] is False
    assert set(closure["behavior_sources"]) == {
        "src/mechanical_librarian/" + path
        for path in (
            "adapters/providers/google_books_metadata.py",
            "adapters/providers/hardcover_metadata.py",
            "adapters/providers/open_library_metadata.py",
            "adapters/providers/inventaire_metadata.py",
            "adapters/providers/audible_catalog_metadata.py",
            "adapters/providers/itunes_search_metadata.py",
            "adapters/providers/audnexus_metadata.py",
            "adapters/providers/audiosilo_metadata.py",
            "adapters/providers/sdk.py",
            "adapters/providers/retry_safety.py",
            "application/provider_evidence.py",
            "application/metadata_report.py",
            "application/failure_diagnostics.py",
            "application/provider_retry_successor.py",
            "application/provider_recovery_evidence.py",
        )
    }
    assert set(current["frozen_contract"]["behavior_sources"]) == set(
        prior["frozen_contract"]["behavior_sources"]
    )
    assert hashlib.sha256(V20_MANIFEST.read_bytes()).hexdigest() == (
        "12607559ec04da639e36fe0afe8e9e01eefb59c6851555a43175fe3c07323dc5"
    )


def test_review_projection_successors_bind_current_sources_without_rescoring():
    rich = json.loads(V21_MANIFEST.read_text())
    title = json.loads(V8_F19A_MANIFEST.read_text())
    prior_rich = json.loads(V20_MANIFEST.read_text())
    prior_title = json.loads(V7_F19A_MANIFEST.read_text())
    assert (
        hashlib.sha256(V21_MANIFEST.read_bytes()).hexdigest()
        == "6535df8f7c4cebefa62f9c38ab01823a377c0c7048fca20b81aaf250b498b7ff"
    )
    assert (
        hashlib.sha256(V8_F19A_MANIFEST.read_bytes()).hexdigest()
        == "77fd7323b01446306577865ebdb4b47f4fb34748021a89ffdca3fa4d51017335"
    )
    assert rich["schema"] == "f20-rich-metadata-qualification-candidate-v21"
    assert (
        title["prior_manifest_sha256"]
        == hashlib.sha256(V7_F19A_MANIFEST.read_bytes()).hexdigest()
    )
    assert (
        rich["authority_rotation"]["prior_manifest_sha256"]
        == hashlib.sha256(V20_MANIFEST.read_bytes()).hexdigest()
    )
    assert {
        k: v
        for k, v in title.items()
        if k not in {"prior_manifest_sha256", "review_projection_source_closure"}
    } == {k: v for k, v in prior_title.items() if k != "prior_manifest_sha256"}
    assert {
        k: v
        for k, v in rich.items()
        if k
        not in {
            "schema",
            "authority_rotation",
            "review_projection_source_closure",
            "frozen_contract",
        }
    } == {
        k: v
        for k, v in prior_rich.items()
        if k not in {"schema", "authority_rotation", "frozen_contract"}
    }
    assert {
        k: v for k, v in rich["frozen_contract"].items() if k != "behavior_sources"
    } == {
        k: v
        for k, v in prior_rich["frozen_contract"].items()
        if k != "behavior_sources"
    }
    broad = rich["frozen_contract"]["behavior_sources"]
    assert set(broad) == set(prior_rich["frozen_contract"]["behavior_sources"])
    assert len(broad) == 36
    assert {
        name
        for name in broad
        if broad[name] != prior_rich["frozen_contract"]["behavior_sources"][name]
    } == {
        "src/mechanical_librarian/adapters/persistence/metadata_reports.py",
        "src/mechanical_librarian/domain/automatic_decision.py",
    }
    assert (
        rich["provider_contract_source_closure"]
        == prior_rich["provider_contract_source_closure"]
    )
    assert len(rich["provider_contract_source_closure"]["behavior_sources"]) == 15
    closure = rich["review_projection_source_closure"]
    assert closure == title["review_projection_source_closure"]
    assert (
        closure["fresh_scoring_run"] is False
        and closure["fresh_holdout_claim"] is False
    )
    assert set(closure["behavior_sources"]) == {
        "src/mechanical_librarian/" + name
        for name in (
            "adapters/persistence/metadata_reports.py",
            "adapters/persistence/metadata_policy.py",
            "adapters/persistence/review_cases.py",
            "adapters/persistence/review_projections.py",
            "adapters/persistence/review_sql.py",
            "application/review_web.py",
        )
    }
    authority = rich["authority_rotation"]["source_closure"]
    prior_authority = prior_rich["authority_rotation"]["source_closure"]
    assert set(authority["bound_source_classification"]) == set(broad)
    assert authority["f20_option_outcomes_changed"] is False
    assert authority["f20_scoring_inputs_changed"] is False
    assert authority["bound_source_classification"] == {
        **prior_authority["bound_source_classification"],
        "src/mechanical_librarian/adapters/persistence/metadata_reports.py": "read_only_review_projection",
    }
    current_bytes = authority["current_byte_bindings"]
    assert (
        set(current_bytes)
        == set(prior_authority["current_byte_bindings"])
        == {
            "frontend/src/api/http.ts",
            "src/mechanical_librarian/domain/human_decision.py",
            "scripts/qualify_audiobookshelf_field_effect.py",
        }
    )
    assert {
        name
        for name in current_bytes
        if current_bytes[name] != prior_authority["current_byte_bindings"][name]
    } == {"frontend/src/api/http.ts"}
    history = rich["authority_rotation"]["historical_reader_delta"]
    assert (
        history["identity"] == "src/mechanical_librarian/domain/automatic_decision.py"
    )
    assert history["current_sha256"] == broad[history["identity"]]
    assert (
        history["measured_sha256"]
        == prior_rich["frozen_contract"]["behavior_sources"][history["identity"]]
    )


def test_v20_binds_the_book_media_guarded_application_rotation() -> None:
    manifest = json.loads(V20_MANIFEST.read_text())
    sources = manifest["frozen_contract"]["behavior_sources"]
    closure = manifest["authority_rotation"]["source_closure"]

    assert closure["f20_option_outcomes_changed"] is False
    assert closure["f20_scoring_inputs_changed"] is False
    assert len(closure["bound_source_classification"]) == 36
    assert set(closure["bound_source_classification"]) == set(sources)
    assert set(closure["bound_source_classification"].values()) == {
        "unchanged",
        "book_media_guarded_application",
        "historical_reader_only",
    }
    assert "All 36 F20 behavior-source hashes were checked." in closure["proof"]
    assert set(closure["current_byte_bindings"]) == {
        "frontend/src/api/http.ts",
        "scripts/qualify_audiobookshelf_field_effect.py",
        "src/mechanical_librarian/domain/human_decision.py",
    }
    history = manifest["authority_rotation"]["historical_reader_delta"]
    assert history["current_sha256"] == sources[history["identity"]]
    assert (
        history["measured_sha256"]
        == json.loads(V19_MANIFEST.read_bytes())["frozen_contract"]["behavior_sources"][
            history["identity"]
        ]
    )


def test_v21_binds_transport_sources_without_rescoring_or_new_authority() -> None:
    assert hashlib.sha256(TRANSPORT_V21_MANIFEST.read_bytes()).hexdigest() == (
        "7aeaca9b020022b9ab385a9baf249d1b63dc7ffcfcc4a20735d6f6480b5b68e7"
    )
    current = json.loads(TRANSPORT_V21_MANIFEST.read_bytes())
    prior = json.loads(V20_MANIFEST.read_bytes())
    assert current["schema"] == "f20-rich-metadata-qualification-candidate-v21"
    rotation = current["authority_rotation"]
    assert (
        rotation["prior_manifest_sha256"]
        == hashlib.sha256(V20_MANIFEST.read_bytes()).hexdigest()
    )
    for field in set(prior) - {"schema", "authority_rotation", "frozen_contract"}:
        assert current[field] == prior[field]
    assert len(current["passing_automatic_options"]) == 13
    for field in set(prior["frozen_contract"]) - {"behavior_sources"}:
        assert current["frozen_contract"][field] == prior["frozen_contract"][field]
    sources = current["frozen_contract"]["behavior_sources"]
    prior_sources = prior["frozen_contract"]["behavior_sources"]
    assert len(sources) == 36
    assert set(sources) == set(prior_sources)
    closure = rotation["source_closure"]
    expected = dict.fromkeys(sources, "unchanged")
    expected["src/mechanical_librarian/domain/validation.py"] = (
        "transport_admission_addition"
    )
    expected["src/mechanical_librarian/domain/automatic_decision.py"] = (
        "historical_reader_only"
    )
    assert closure["bound_source_classification"] == expected
    for source, category in expected.items():
        if category == "unchanged":
            assert sources[source] == prior_sources[source]
        else:
            assert sources[source] != prior_sources[source]
    assert closure["f20_option_outcomes_changed"] is False
    assert closure["f20_scoring_inputs_changed"] is False
    provider = current["provider_contract_source_closure"]
    assert len(provider["behavior_sources"]) == 15
    assert provider["fresh_scoring_run"] is False
    assert provider["fresh_holdout_claim"] is False
    additional = closure["current_byte_bindings"]
    assert set(additional) == set(
        prior["authority_rotation"]["source_closure"]["current_byte_bindings"]
    )
    assert len(additional) == 3
    history = rotation["historical_reader_delta"]
    assert history["measured_sha256"] == prior_sources[history["identity"]]
    assert history["current_sha256"] == sources[history["identity"]]


def test_v22_combines_exact_predecessors_and_exhaustive_current_source_closure() -> (
    None
):
    current = json.loads(V22_MANIFEST.read_bytes())
    primary = json.loads(V21_MANIFEST.read_bytes())
    transport = json.loads(TRANSPORT_V21_MANIFEST.read_bytes())
    assert current["schema"] == "f20-rich-metadata-qualification-candidate-v22"
    rotation = current["authority_rotation"]
    assert (
        rotation["prior_manifest_sha256"]
        == hashlib.sha256(V21_MANIFEST.read_bytes()).hexdigest()
    )
    assert (
        rotation["additional_prior_manifest_path"]
        == "docs/reference/f20-rich-metadata-qualification-candidate-v21-local-transport.json"
    )
    assert (
        rotation["additional_prior_manifest_sha256"]
        == hashlib.sha256(TRANSPORT_V21_MANIFEST.read_bytes()).hexdigest()
    )
    for field in set(primary) - {
        "schema",
        "authority_rotation",
        "frozen_contract",
        "provider_contract_source_closure",
    }:
        assert current[field] == primary[field]
    for field in set(primary["frozen_contract"]) - {"behavior_sources"}:
        assert current["frozen_contract"][field] == primary["frozen_contract"][field]
    assert (
        current["passing_automatic_options"] == transport["passing_automatic_options"]
    )
    assert len(current["passing_automatic_options"]) == 13
    sources = current["frozen_contract"]["behavior_sources"]
    primary_sources = primary["frozen_contract"]["behavior_sources"]
    assert len(sources) == 36
    assert (
        set(sources)
        == set(primary_sources)
        == set(transport["frozen_contract"]["behavior_sources"])
    )
    expected = dict.fromkeys(sources, "unchanged")
    expected["src/mechanical_librarian/domain/validation.py"] = (
        "transport_admission_addition"
    )
    expected["src/mechanical_librarian/domain/automatic_decision.py"] = (
        "historical_reader_only"
    )
    expected["src/mechanical_librarian/domain/metadata_report.py"] = (
        "provider_identity_diagnostic_stage_v39"
    )
    closure = rotation["source_closure"]
    assert closure["bound_source_classification"] == expected
    for name, category in expected.items():
        assert (sources[name] == primary_sources[name]) is (category == "unchanged")
    assert closure["f20_option_outcomes_changed"] is False
    assert closure["f20_scoring_inputs_changed"] is False
    provider = current["provider_contract_source_closure"]
    projection = current["review_projection_source_closure"]
    assert len(provider["behavior_sources"]) == 15
    assert len(projection["behavior_sources"]) == 6
    primary_provider = primary["provider_contract_source_closure"]
    assert set(provider) == set(primary_provider)
    assert {
        name
        for name, digest in provider["behavior_sources"].items()
        if digest != primary_provider["behavior_sources"][name]
    } == {
        "src/mechanical_librarian/adapters/providers/itunes_search_metadata.py",
        "src/mechanical_librarian/application/failure_diagnostics.py",
        "src/mechanical_librarian/application/metadata_report.py",
    }
    assert projection == primary["review_projection_source_closure"]
    for retained in (provider, projection):
        assert retained["fresh_scoring_run"] is False
        assert retained["fresh_holdout_claim"] is False
    additional = closure["current_byte_bindings"]
    assert len(additional) == 3
    assert set(additional) == set(
        primary["authority_rotation"]["source_closure"]["current_byte_bindings"]
    )
    qualifier = "scripts/qualify_audiobookshelf_field_effect.py"
    for name, value in additional.items():
        predecessor = transport if name == qualifier else primary
        assert (
            value
            == predecessor["authority_rotation"]["source_closure"][
                "current_byte_bindings"
            ][name]
        )
    history = rotation["historical_reader_delta"]
    assert history["measured_sha256"] == primary_sources[history["identity"]]
    assert history["current_sha256"] == sources[history["identity"]]


def test_f39_successors_preserve_history_and_exhaustive_live_source_closure() -> None:
    prior = json.loads(V22_MANIFEST.read_bytes())
    current = json.loads(V23_MANIFEST.read_bytes())
    title_prior = json.loads(V8_F19A_MANIFEST.read_bytes())
    title = json.loads(V9_F19A_MANIFEST.read_bytes())
    assert (
        hashlib.sha256(V22_MANIFEST.read_bytes()).hexdigest()
        == "aacb6e2ebce0490f58b86c8069963445dcb470ffdd70283b5558dde082449103"
    )
    assert (
        hashlib.sha256(V8_F19A_MANIFEST.read_bytes()).hexdigest()
        == "77fd7323b01446306577865ebdb4b47f4fb34748021a89ffdca3fa4d51017335"
    )
    assert (
        hashlib.sha256(V9_F19A_MANIFEST.read_bytes()).hexdigest()
        == "15298611ad0b50dc9a8adb924e6e3551a4da0dd641d2600843f3dac260a71dfb"
    )
    assert (
        hashlib.sha256(V23_MANIFEST.read_bytes()).hexdigest()
        == "d065def423f5e3316172fae8b378ae2a4407481d6ccd1a9db94427baade2a9d1"
    )
    assert (
        title["prior_manifest_sha256"]
        == hashlib.sha256(V8_F19A_MANIFEST.read_bytes()).hexdigest()
    )
    assert (
        current["authority_rotation"]["prior_manifest_sha256"]
        == hashlib.sha256(V22_MANIFEST.read_bytes()).hexdigest()
    )
    alias = V22_MANIFEST.with_name(
        "f20-rich-metadata-qualification-candidate-v22-before-identity-diagnostics.json"
    )
    assert hashlib.sha256(alias.read_bytes()).hexdigest() == (
        "1dcf4278d8cbba00df764ea17b13809a42304fece748632c217531296dfcddee"
    )
    assert current["authority_rotation"]["additional_prior_manifest_sha256"] == (
        hashlib.sha256(alias.read_bytes()).hexdigest()
    )
    assert current["authority_rotation"]["additional_prior_manifest_path"] == (
        "docs/reference/" + alias.name
    )
    assert (
        json.loads(alias.read_bytes())["passing_automatic_options"]
        == prior["passing_automatic_options"]
    )
    assert current["schema"] == "f20-rich-metadata-qualification-candidate-v23"
    for key in set(title_prior) - {
        "prior_manifest_sha256",
        "review_projection_source_closure",
    }:
        assert title[key] == title_prior[key]
    for key in set(prior) - {
        "schema",
        "authority_rotation",
        "frozen_contract",
        "review_projection_source_closure",
    }:
        assert current[key] == prior[key]
    for key in set(prior["frozen_contract"]) - {"behavior_sources"}:
        assert current["frozen_contract"][key] == prior["frozen_contract"][key]
    assert (
        current["review_projection_source_closure"]
        == title["review_projection_source_closure"]
    )
    closure = current["authority_rotation"]["source_closure"]
    assert closure["f20_option_outcomes_changed"] is False
    assert closure["f20_scoring_inputs_changed"] is False
    groups = (
        (
            current["frozen_contract"]["behavior_sources"],
            prior["frozen_contract"]["behavior_sources"],
            36,
        ),
        (
            current["provider_contract_source_closure"]["behavior_sources"],
            prior["provider_contract_source_closure"]["behavior_sources"],
            15,
        ),
        (
            current["review_projection_source_closure"]["behavior_sources"],
            prior["review_projection_source_closure"]["behavior_sources"],
            6,
        ),
        (
            closure["current_byte_bindings"],
            prior["authority_rotation"]["source_closure"]["current_byte_bindings"],
            3,
        ),
    )
    # V23 is history now: its bindings are read against its own predecessor,
    # and the live tree is hashed by the successor that carries current
    # authority.
    for bindings, predecessor, count in groups:
        assert len(bindings) == count
        assert set(bindings) == set(predecessor)
    sources, predecessor, _ = groups[0]
    assert closure["bound_source_classification"] == {
        name: "unchanged"
        if digest == predecessor[name]
        else "authored_description_integration"
        for name, digest in sources.items()
    }
    for manifest in (current, title):
        projection = manifest["review_projection_source_closure"]
        assert projection["fresh_scoring_run"] is False
        assert projection["fresh_holdout_claim"] is False


def test_current_title_successors_bind_complete_consumer_source_inventory() -> None:
    rich = json.loads(LATEST_MANIFEST.read_bytes())
    title = json.loads(LATEST_F19A_MANIFEST.read_bytes())
    prior = json.loads(V24_MANIFEST.read_bytes())
    expected = (
        set(prior["frozen_contract"]["behavior_sources"])
        | set(prior["provider_contract_source_closure"]["behavior_sources"])
        | set(prior["review_projection_source_closure"]["behavior_sources"])
        | set(prior["authority_rotation"]["source_closure"]["current_byte_bindings"])
        | {
            "src/mechanical_librarian/adapters/persistence/schema.py",
            "src/mechanical_librarian/adapters/persistence/decisions.py",
            "src/mechanical_librarian/application/decision_preview.py",
            "src/mechanical_librarian/domain/audiobook_decision_qualification.py",
            "scripts/qualify_provider_evidence_replay.py",
        }
    )
    expected |= {
        "src/mechanical_librarian/" + name
        for name in (
            "adapters/evidence_extractors/audio_local.py",
            "domain/audiobook_corroboration.py",
            "domain/audio_composition.py",
            "bootstrap/audio_child.py",
            "bootstrap/composition.py",
            "application/review_application.py",
            "interfaces/web/app.py",
        )
    } | {
        "tests/unit/test_shadow_outcome_persistence.py",
        "frontend/src/components/RecordingIdentityBasis.tsx",
        "frontend/src/styles.css",
        "frontend/src/routes/LibraryPage.tsx",
        "frontend/src/routes/DetailPage.tsx",
    }
    expected.add(
        "src/mechanical_librarian/adapters/persistence/qualification_state_bridge.py"
    )
    expected |= {
        "frontend/src/routes/PolicyPage.tsx",
        "src/mechanical_librarian/adapters/persistence/cover_considerations.py",
        "src/mechanical_librarian/application/cover_consideration.py",
        "src/mechanical_librarian/application/cover_identity.py",
        "src/mechanical_librarian/domain/cover_automatic_decision.py",
    }
    assert set(rich["source_closure"]) == expected
    assert set(title["source_closure"]) == expected - {
        "src/mechanical_librarian/domain/automatic_decision.py"
    }
    for manifest, path, digest in (
        (rich, LATEST_MANIFEST, F20_QUALIFICATION_CANDIDATE_SHA256),
        (title, LATEST_F19A_MANIFEST, F19A_QUALIFICATION_MANIFEST_SHA256),
    ):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert manifest["proposal_policy_version"] == "fixed-conservative-v25"
        for name, source_digest in manifest["source_closure"].items():
            assert (
                hashlib.sha256((MANIFEST.parents[2] / name).read_bytes()).hexdigest()
                == source_digest
            )
    for current, filename, expected_digest in (
        (
            title,
            "f19a-qualification-manifest-v19.json",
            "b07048c857e5946634f6eaa0ec57cd609e5288899eb020ef20d26df8ed37e426",
        ),
        (
            rich,
            "f20-rich-metadata-qualification-candidate-v33.json",
            "3b62f0fdbad07a0d893f1e18f9d20d23e0c4101db14a001f2df550a383a51927",
        ),
    ):
        assert (
            hashlib.sha256(MANIFEST.with_name(filename).read_bytes()).hexdigest()
            == expected_digest
        )
        assert current["prior_manifest_sha256"] == expected_digest
        assert current["prior_manifest_path"] == "docs/reference/" + filename
    assert title["new_title_measurement"]["matrix_inventory"] == {
        "retained_synthetic_positives": 19,
        "retained_synthetic_negatives": 15,
        "r1_positive_controls": 6,
        "r1_negative_controls": 10,
    }
    assert title["qualified_policy_options"] == sorted(F19A_QUALIFIED_POLICY_OPTIONS)
    common = {
        "retained-report-store",
        "r2-title-inputs",
        "r2-script-and-omission-truth",
        "r2-malformed-title-truth",
        "r2-evaluator",
        "r2-results",
        "retained-raw-capture",
        "retained-replay-configuration",
        "retained-local-inputs",
        "current-report-reconstruction",
        "current-report-reconstruction-result",
        "current-report-derivatives",
        "current-provider-replay",
        "retained-independent-title-labels",
        "synthetic-title-inputs",
        "candidate-evaluator",
        "durable-evaluator",
        "candidate-results",
        "durable-results",
        "synthetic-report-builder",
        "f20-persistence-effect-test",
        "f20-persistence-effect-result",
    }
    assert set(rich["measurement_artifacts"]) == common
    assert set(title["measurement_artifacts"]) == common | {
        f"{control}-{part}"
        for control in ("f35", "f41")
        for part in ("inputs", "truth", "constructor")
    }
    for name, binding in title["measurement_artifacts"].items():
        assert set(binding) == {"sha256", "evidence_mode"}
        assert len(binding["sha256"]) == 64
        assert all(c in "0123456789abcdef" for c in binding["sha256"])
        assert binding["evidence_mode"]
        if name in common:
            assert rich["measurement_artifacts"][name] == binding
    for successor in (title, rich):
        predecessor = json.loads(
            (MANIFEST.parents[2] / successor["prior_manifest_path"]).read_bytes()
        )
        assert (
            successor["measurement_artifacts"] == predecessor["measurement_artifacts"]
        )
        assert (
            successor["chapter_runtime_increment"][
                "historical_measurements_reused_without_rescoring"
            ]
            is True
        )
    assert (
        title["measurement_artifacts"]["synthetic-report-builder"]["sha256"]
        == hashlib.sha256(
            (MANIFEST.parents[2] / "tests/support/evidence_led_titles.py").read_bytes()
        ).hexdigest()
    )
    assert title["new_title_measurement"]["synthetic"]["candidate_predictions"] == 25
    assert (
        title["durable_title_measurement"]["synthetic_positive"]["durable_predictions"]
        == 25
    )
    assert title["durable_title_measurement"]["synthetic_negative"]["withheld"] == 25
    assert title["durable_title_measurement"]["retained"]["durable_predictions"] == 2

    r2 = title["r2_edge_measurement"]
    assert r2["script_controls"] == {
        "cases": 6,
        "positive_opportunities": 4,
        "negative_controls": 2,
        "candidate_predictions": 4,
        "durable_predictions": 4,
        "withheld": 2,
        "incorrect": 0,
    }
    assert r2["neutral_preservation_controls"] == {
        "cases": 3,
        "automatic_opportunities": 0,
        "candidate_predictions": 0,
        "durable_predictions": 0,
        "original_results_preserved": 3,
    }
    assert r2["owner_reachable"] == 9


def test_v24_binds_the_media_health_projection_without_rescoring() -> None:
    current = json.loads(V24_MANIFEST.read_bytes())
    prior = json.loads(V23_MANIFEST.read_bytes())
    assert (
        hashlib.sha256(V24_MANIFEST.read_bytes()).hexdigest()
        == "2728215f5ade910611d6806e58de40c867a05315efe9670192de48298e54a54b"
    )
    assert current["schema"] == "f20-rich-metadata-qualification-candidate-v24"
    rotation = current["authority_rotation"]
    assert (
        rotation["prior_manifest_sha256"]
        == hashlib.sha256(V23_MANIFEST.read_bytes()).hexdigest()
    )
    assert rotation["prior_manifest_path"] == "docs/reference/" + V23_MANIFEST.name
    # V24 succeeds one predecessor, so the second-predecessor keys v23 needed
    # for the two v22 artifacts are absent rather than restated.
    assert set(rotation) == {
        "historical_reader_delta",
        "prior_manifest_path",
        "prior_manifest_sha256",
        "scope",
        "source_closure",
    }
    for field in set(prior) - {
        "schema",
        "authority_rotation",
        "frozen_contract",
        "provider_contract_source_closure",
        "review_projection_source_closure",
    }:
        assert current[field] == prior[field]
    assert len(current["passing_automatic_options"]) == 13
    for field in set(prior["frozen_contract"]) - {"behavior_sources"}:
        assert current["frozen_contract"][field] == prior["frozen_contract"][field]
    sources = current["frozen_contract"]["behavior_sources"]
    prior_sources = prior["frozen_contract"]["behavior_sources"]
    assert len(sources) == 36
    assert set(sources) == set(prior_sources)
    closure = rotation["source_closure"]
    # The classification map states the delta against v23 alone. A source an
    # earlier candidate restated reads `unchanged` here because it did not
    # move again.
    expected = dict.fromkeys(sources, "unchanged")
    expected["src/mechanical_librarian/domain/audio_inspection.py"] = (
        "audio_probe_facts"
    )
    expected["src/mechanical_librarian/domain/automatic_decision.py"] = (
        "historical_reader_and_audiobook_tranche_wiring"
    )
    # V24 is re-pinned in place over the inert F46 audiobook tranche wiring, so
    # its delta against v23 states the media-health projection and that wiring
    # together. The wiring grants no identity while no audiobook record is
    # published, so v24 keeps its options, scoring and every authority rule
    # (#967).
    for name in (
        "adapters/persistence/field_effect_validation.py",
        "adapters/persistence/metadata_reports.py",
        "application/metadata_policy.py",
        "domain/metadata_policy.py",
        "domain/rich_metadata_preview.py",
        "domain/shadow_outcome.py",
    ):
        expected["src/mechanical_librarian/" + name] = "audiobook_tranche_wiring"
    # One source moved in both units: F46 unit B restated a comment in it and
    # this unit advanced the report discriminator through it, so its class
    # names both rather than the one that would read as the whole truth.
    expected["src/mechanical_librarian/domain/metadata_report.py"] = (
        "report_schema_v40_and_comment"
    )
    assert closure["bound_source_classification"] == expected
    # A classification and a digest cannot disagree: every source the map calls
    # unchanged reads byte-equal to v23 and every other one does not.
    for name, category in expected.items():
        assert (sources[name] == prior_sources[name]) is (category == "unchanged")
    assert closure["f20_option_outcomes_changed"] is False
    assert closure["f20_scoring_inputs_changed"] is False
    assert (
        "states the delta against v23 alone and is not cumulative" in closure["proof"]
    )
    # The byte binding is rotated twice over: the media-health observation
    # view, and the policy state's audiobook qualification summary the owner's
    # routing surface reads (#967).
    for reason in (
        "media_health_projection",
        "audio_probe_facts",
        "report_schema_v40",
        "metadata-report-v40",
        "audio-inspection-v3",
        "review-case-detail-v3",
        "review-library-v2",
        "audiobook qualification summary",
        "no rescoring or fresh holdout is claimed",
    ):
        assert reason in closure["proof"]
    provider = current["provider_contract_source_closure"]
    projection = current["review_projection_source_closure"]
    prior_provider = prior["provider_contract_source_closure"]
    assert set(provider) == set(prior_provider)
    assert len(provider["behavior_sources"]) == 15
    assert set(provider["behavior_sources"]) == set(prior_provider["behavior_sources"])
    # Qualification now records the truncation comparison on the candidate it
    # shapes, which is the one provider-contract source this unit moves.
    assert {
        name
        for name, digest in provider["behavior_sources"].items()
        if digest != prior_provider["behavior_sources"][name]
    } == {"src/mechanical_librarian/application/metadata_report.py"}
    assert len(projection["behavior_sources"]) == 6
    assert set(projection["behavior_sources"]) == set(
        prior["review_projection_source_closure"]["behavior_sources"]
    )
    assert {
        name
        for name, digest in projection["behavior_sources"].items()
        if digest != prior["review_projection_source_closure"]["behavior_sources"][name]
    } == {
        "src/mechanical_librarian/" + name
        for name in (
            "adapters/persistence/metadata_policy.py",
            "adapters/persistence/metadata_reports.py",
            "application/review_web.py",
        )
    }
    assert projection["issue"] == 144
    for retained in (provider, projection):
        assert retained["fresh_scoring_run"] is False
        assert retained["fresh_holdout_claim"] is False
    additional = closure["current_byte_bindings"]
    prior_additional = prior["authority_rotation"]["source_closure"][
        "current_byte_bindings"
    ]
    assert len(additional) == 3
    assert set(additional) == set(prior_additional)
    assert {
        name for name, value in additional.items() if value != prior_additional[name]
    } == {"frontend/src/api/http.ts"}
    # These immutable bindings describe v24. The successor test below owns
    # current source equality; historical bytes never acquire current authority.
    history = rotation["historical_reader_delta"]
    assert (
        history["identity"] == "src/mechanical_librarian/domain/automatic_decision.py"
    )
    assert history["measured_sha256"] == prior_sources[history["identity"]]
    assert history["current_sha256"] == sources[history["identity"]]

    title = json.loads(V10_F19A_MANIFEST.read_bytes())
    prior_title = json.loads(V9_F19A_MANIFEST.read_bytes())
    assert (
        hashlib.sha256(V10_F19A_MANIFEST.read_bytes()).hexdigest()
        == "f30e2c102d55c532dff554f7b6ca17c3998bcd965ce55d9ca6e2df052c48b7d9"
    )
    assert (
        title["prior_manifest_sha256"]
        == hashlib.sha256(V9_F19A_MANIFEST.read_bytes()).hexdigest()
    )
    rotated = {
        "association_requalification",
        "prior_manifest_sha256",
        "review_projection_source_closure",
    }
    assert {k: v for k, v in title.items() if k not in rotated} == {
        k: v for k, v in prior_title.items() if k not in rotated
    }
    # The association evidence v9 measured is inherited exactly. Only the
    # projection follow-up's scope is restated, because the tranche wiring
    # reshaped those sources: the claim the retained measurements carry on is
    # byte-identical ebook output, not an unchanged Python AST (#967).
    association = title["association_requalification"]
    prior_association = prior_title["association_requalification"]
    followup = "review_projection_followup"
    assert {k: v for k, v in association.items() if k != followup} == {
        k: v for k, v in prior_association.items() if k != followup
    }
    assert {k: v for k, v in association[followup].items() if k != "scope"} == {
        k: v for k, v in prior_association[followup].items() if k != "scope"
    }
    assert "the Python AST is unchanged" in prior_association[followup]["scope"]
    assert "the Python AST is unchanged" not in association[followup]["scope"]
    assert "ebook output is byte-identical" in association[followup]["scope"]
    assert title["review_projection_source_closure"] == projection


@pytest.mark.parametrize(
    "filename,original_digest,intervening_digest,main_digest,referrer",
    [
        (
            "f19a-qualification-manifest-v10.json",
            "91292dd98bef221efc6d75ed336bc1031e3917fa75c05dcd38474eef5b29e523",
            "010c06261591a6f4fd959c8c1fe0cc8e1346b3fc58a7923daf4de1ec93049cae",
            "f30e2c102d55c532dff554f7b6ca17c3998bcd965ce55d9ca6e2df052c48b7d9",
            "f19a-qualification-manifest-v11.json",
        ),
        (
            "f20-rich-metadata-qualification-candidate-v24.json",
            "679b4edfa4f3d79919dfed756140d852acc215e79e08d5bee9b77e3a41977321",
            "2e76fdb8372867c95b476208ebb0ae2bd175171439559d4f56eec8d29611c6c6",
            "2728215f5ade910611d6806e58de40c867a05315efe9670192de48298e54a54b",
            "f20-rich-metadata-qualification-candidate-v25.json",
        ),
    ],
)
def test_base_integration_retains_both_exact_historical_manifest_origins(
    filename, original_digest, intervening_digest, main_digest, referrer
):
    original_path = "docs/reference/" + filename
    retained_path = (
        original_path.removesuffix(".json") + "-" + original_digest + ".json"
    )
    assert (
        hashlib.sha256((MANIFEST.parents[2] / retained_path).read_bytes()).hexdigest()
        == original_digest
    )
    assert (
        hashlib.sha256(MANIFEST.with_name(filename).read_bytes()).hexdigest()
        == main_digest
    )
    historical = json.loads(MANIFEST.with_name(referrer).read_bytes())
    assert historical["prior_manifest_path"] == original_path
    assert historical["prior_manifest_sha256"] == original_digest
    expected = [
        {
            "origin_commit": "bdf65cd39d0c03afbe30811216e8620d52baa3f4",
            "original_path": original_path,
            "sha256": original_digest,
            "retained_path": retained_path,
        },
        {
            "origin_commit": "dd7f7b7f911688b7df579ce288ffff5905577efe",
            "original_path": original_path,
            "sha256": intervening_digest,
            "retained_path": original_path.removesuffix(".json")
            + "-"
            + intervening_digest
            + ".json",
        },
        {
            "origin_commit": "2261514c458c6bc11a7805127ea330bc833260ed",
            "original_path": original_path,
            "sha256": main_digest,
            "retained_path": original_path,
        },
    ]
    for entry in expected:
        assert (
            hashlib.sha256(
                (MANIFEST.parents[2] / entry["retained_path"]).read_bytes()
            ).hexdigest()
            == entry["sha256"]
        )
    for path in (LATEST_MANIFEST, LATEST_F19A_MANIFEST):
        integration = json.loads(path.read_bytes())["base_integration"]
        assert [
            entry
            for entry in integration["historical_origins"]
            if entry["original_path"] == original_path
        ] == expected
        assert integration["f46"]["manifest_sha256"] is None
        assert integration["f46"]["passing_options"] == []


def test_complete_chapter_successors_refresh_sources_without_measurement_or_authority():
    for path in (
        MANIFEST.with_name("f20-rich-metadata-qualification-candidate-v31.json"),
        MANIFEST.with_name("f19a-qualification-manifest-v17.json"),
    ):
        current = json.loads(path.read_bytes())
        prior = json.loads(
            (MANIFEST.parents[2] / current["prior_manifest_path"]).read_bytes()
        )
        increment = current["complete_chapter_increment"]
        assert current["issue"] == 1004
        assert increment["report_schema"] == "metadata-report-v42"
        assert increment["clean_rebuild_required"] is True
        assert increment["historical_measurements_reused"] is True
        assert increment["fresh_measurement_claimed"] is False
        for key in (
            "measurement_artifacts",
            "historical_measurements",
            "chapter_runtime_increment",
        ):
            assert current[key] == prior[key]
        for key in ("options", "passing_automatic_options", "qualified_policy_options"):
            if key in prior:
                assert current[key] == prior[key]


def test_f46_accepted_record_binds_exact_six_field_candidate_registration():
    path = MANIFEST.with_name("f46-audiobook-qualification-candidate-v3.json")
    document = json.loads(path.read_bytes())
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == F46_QUALIFICATION_MANIFEST_SHA256
    )
    assert (
        document["frozen_candidate_sha"] == "be6cb086d14aff4c61e6e9ae983f23ed3a996b94"
    )
    assert document["status"] == "qualified_automatic_correspondence_complete"
    assert set(document["passing_automatic_options"]) == F46_PASSING_AUTOMATIC_OPTIONS
    assert len(F46_PASSING_AUTOMATIC_OPTIONS) == 44
    assert document["passing_fields"] == [
        "authors",
        "genres",
        "language",
        "narrators",
        "series",
        "title",
    ]
    assert document["corpus"]["selected_positive_books_per_split"] == [3, 2, 1]
    assert document["corpus"]["model_requests"] == 0
    assert document["corpus"]["media_or_library_writes"] == 0
    assert set(document["evidence_receipts"]) == {
        split + suffix
        for split in ("development", "confirmation", "final")
        for suffix in ("_source_labels", "_candidate_operations")
    }
    assert all(
        re.fullmatch("[0-9a-f]{64}", digest)
        for digest in document["evidence_receipts"].values()
    )
    assert (
        sum(
            item["selected_field_values"]
            for item in document["natural_selected_values"].values()
        )
        == 36
    )
    assert (
        sum(item["changes"] for item in document["natural_selected_values"].values())
        == 25
    )
    assert (
        sum(
            item["preservations"]
            for item in document["natural_selected_values"].values()
        )
        == 11
    )
    for path in (LATEST_MANIFEST, LATEST_F19A_MANIFEST):
        successor = json.loads(path.read_bytes())
        assert (
            successor["audiobook_activation"]["manifest_sha256"]
            == F46_QUALIFICATION_MANIFEST_SHA256
        )
        assert successor["audiobook_activation"]["passing_options"] == sorted(
            F46_PASSING_AUTOMATIC_OPTIONS
        )
        assert successor["audiobook_activation"]["clean_rebuild_required"] is True
    # The only bound production changes after the freeze are registry/manifest
    # literals. Other rule consumers retain their exact frozen bytes.
    exceptions = {
        "src/mechanical_librarian/domain/audiobook_decision_qualification.py",
        "src/mechanical_librarian/domain/automatic_decision.py",
        "tests/unit/test_shadow_outcome_persistence.py",
    }
    for name, digest in document["source_closure"].items():
        if name not in exceptions:
            assert (
                hashlib.sha256((MANIFEST.parents[2] / name).read_bytes()).hexdigest()
                == digest
            )


def test_withdrawn_preservation_authority_retains_adverse_receipts():
    path = MANIFEST.with_name("f46-audiobook-qualification-candidate-v2.json")
    document = json.loads(path.read_bytes())
    prior = MANIFEST.with_name("f46-audiobook-qualification-candidate-v1.json")
    assert (
        hashlib.sha256(prior.read_bytes()).hexdigest()
        == document["prior_manifest_sha256"]
    )
    withdrawn = set(document["withdrawn_after_software_correspondence"])
    assert withdrawn == {
        f"contributor_names:{option}:audiobook_authors_preserve"
        for option in ("preserve", "canonical")
    } | {
        f"title_layout:{option}:audiobook_title_preserve"
        for option in ("preserve", "title_and_subtitle")
    }
    assert (
        set(json.loads(prior.read_bytes())["passing_automatic_options"]) - withdrawn
        == F46_PASSING_AUTOMATIC_OPTIONS
    )
    assert (
        document["software_correspondence_attempt"]["author_preservation_failures"]
        == 318
    )
    assert (
        document["software_correspondence_attempt"][
            "title_category_suspension_scenarios"
        ]
        == 50
    )
    assert (
        document["software_correspondence_attempt"]["no_wrong_values_written"] is True
    )
    assert len(document["software_receipts"]) == 3
    assert all(
        re.fullmatch("[0-9a-f]{64}", value)
        for value in document["software_receipts"].values()
    )


def test_final_record_preserves_verified_rules_and_exact_passing_set():
    document = json.loads(
        MANIFEST.with_name("f46-audiobook-qualification-candidate-v3.json").read_bytes()
    )
    prior = MANIFEST.with_name("f46-audiobook-qualification-candidate-v2.json")
    assert (
        hashlib.sha256(prior.read_bytes()).hexdigest()
        == document["prior_manifest_sha256"]
    )
    assert (
        document["passing_automatic_options"]
        == json.loads(prior.read_bytes())["passing_automatic_options"]
    )
    completed = document["completed_correspondence"]
    assert completed["source_sha"] == "c7c0c65da90444a0f8bc36fe96ee92032ea8a946"
    assert completed["scenarios_completed"] == 530
    assert completed["counts"] == {
        "scenario_complete": 530,
        "verified_fake_service": 1165,
        "no_change": 162,
    }
    assert (
        completed["errors"]
        == completed["unintended_suspension"]
        == completed["value_or_operation_mismatches"]
        == 0
    )
    assert set(completed["identity_counts"]) == F46_PASSING_AUTOMATIC_OPTIONS
    assert all(
        sum(counts.values()) > 0 for counts in completed["identity_counts"].values()
    )
    equivalence = document["binding_only_source_equivalence"]
    for name, expected in equivalence["ast_sha256"].items():
        tree = ast.parse((MANIFEST.parents[2] / name).read_bytes())
        tree.body = [
            node
            for node in tree.body
            if not (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name)
                    and target.id in equivalence["excluded_binding_assignments"]
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
            )
        ]
        assert (
            hashlib.sha256(
                ast.dump(tree, include_attributes=False).encode()
            ).hexdigest()
            == expected
        )
