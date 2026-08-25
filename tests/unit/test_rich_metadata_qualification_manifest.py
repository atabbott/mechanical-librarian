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
    / "docs/reference/f20-rich-metadata-qualification-candidate-v1.json"
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

    assert "f20-rich-metadata-qualification-candidate-v1" == manifest["schema"]
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
    assert 10 == manifest["candidate_execution_count"]
    assert {
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
    } == manifest["evidence_method"]

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
    assert 22 == isbn["isbn_form:preserve_exact"]["metrics"]["automatic_predictions"]
    assert 1.0 == isbn["isbn_form:prefer_isbn13"]["metrics"]["automatic_precision"]
    assert 22 == isbn["isbn_form:prefer_isbn13"]["metrics"]["automatic_predictions"]
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
    assert 147 == subtitle_fill["eligible_opportunities"]
    assert 12 == subtitle_fill["metrics"]["automatic_predictions"]
    assert 1.0 == subtitle_fill["metrics"]["automatic_precision"]


def test_candidate_exposes_only_closed_aggregate_contract_data() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert {
        "automatic_authority",
        "candidate_execution_count",
        "evidence_method",
        "excluded_destinations",
        "frozen_contract",
        "human_only_options",
        "options",
        "passing_automatic_options",
        "policy_only_options",
        "schema",
        "zero_tolerance",
    } == set(manifest)
    for name in ("automatic_decision", "compiler", "policy", "target_shape"):
        item = manifest["frozen_contract"][name]
        assert {"identity", "sha256"} == set(item)
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
    assert (
        "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37"
        == F19A_QUALIFICATION_MANIFEST_SHA256
    )
    assert {
        "title_layout:preserve",
        "title_layout:title_and_subtitle",
        "date_precision:year",
        "date_precision:full_date",
        "language_rendering:preserve",
        "language_rendering:display_name",
        "contributor_names:preserve",
        "contributor_names:canonical",
        "publisher_preference:publisher",
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


def test_runtime_envelope_pins_every_terminal_disposition_and_authority() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
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
