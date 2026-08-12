"""Public contract proof for the field-local F20 qualification candidate."""

from __future__ import annotations

import json
import hashlib
import unittest
from pathlib import Path

from mechanical_librarian.domain.metadata_policy import OPTION_CATALOGUE
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    QUALIFIED_POLICY_OPTIONS,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    QUALIFIED_AUTOMATIC,
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
    "subtitle", "series", "genres", "tags", "description_fill", "isbn_form",
    "asin", "explicit",
)


class RichMetadataQualificationManifestTests(unittest.TestCase):
    def test_candidate_is_closed_field_local_and_exactly_authorizing(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual("f20-rich-metadata-qualification-candidate-v1", manifest["schema"])
        self.assertEqual("decision_only", manifest["automatic_authority"])
        passing = {
            "series:primary_narrative", "series:source_order",
            "series:all_qualified",
            "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
        }
        self.assertEqual(passing, set(manifest["passing_automatic_options"]))
        self.assertEqual(["explicit:unsupported"], manifest["human_only_options"])
        self.assertEqual(["narrators", "cover"], manifest["excluded_destinations"])
        self.assertEqual(1, manifest["candidate_execution_count"])
        self.assertEqual(
            {
                "evaluated_reports": 49,
                "exposure_unit": "case_field_option",
                "network_access": False,
                "prior_unrelated_work_use_allowed": True,
                "replayed_provider_requests": 421,
                "series_truth_model": "valid_membership_with_preferred_value",
                "subtitle_truth_model": "ebook_manifestation_bibliographic_subtitle",
                "unseen_holdout_required": False,
            },
            manifest["evidence_method"],
        )

        expected = {
            f"{field}:{option}"
            for field in RICH_FIELDS
            for option in OPTION_CATALOGUE[field]
        } - {"tags:off"}
        actual = {entry["identity"] for entry in manifest["options"]}
        self.assertEqual(expected, actual)
        self.assertEqual(["tags:off"], manifest["policy_only_options"])
        self.assertEqual(
            {"explicit:unsupported"},
            {
                entry["identity"] for entry in manifest["options"]
                if entry["disposition"] == "passing_human_only"
            },
        )
        self.assertEqual(
            {
                "subtitle:fill_empty", "subtitle:corroborated_split",
            },
            {
                entry["identity"] for entry in manifest["options"]
                if entry["disposition"] == "not_qualified"
            },
        )
        self.assertEqual(
            passing,
            {
                entry["identity"] for entry in manifest["options"]
                if entry["disposition"] == "passing_automatic"
            },
        )
        terminal = {
            entry["identity"] for entry in manifest["options"]
            if entry["disposition"] == "insufficient_coverage"
        }
        self.assertEqual(
            expected - passing - {"explicit:unsupported"} - {
                "subtitle:fill_empty", "subtitle:corroborated_split",
            },
            terminal,
        )
        for entry in manifest["options"]:
            self.assertEqual(
                {
                    "cohort_sufficiency", "disposition", "eligible_opportunities",
                    "identity", "metrics",
                },
                set(entry),
            )
            self.assertIn(
                entry["cohort_sufficiency"],
                {"sufficient", "insufficient", "not_applicable"},
            )
            self.assertEqual(
                {
                    "automatic_precision", "automatic_predictions",
                    "automatic_recall", "structural_or_sensitive_violations",
                    "unsupported_output",
                },
                set(entry["metrics"]),
            )
            self.assertEqual(0, entry["metrics"]["structural_or_sensitive_violations"])
        isbn = {
            entry["identity"]: entry for entry in manifest["options"]
            if entry["identity"] in passing
        }
        self.assertEqual(1.0, isbn["isbn_form:preserve_exact"]["metrics"]["automatic_precision"])
        self.assertEqual(4, isbn["isbn_form:preserve_exact"]["metrics"]["automatic_predictions"])
        self.assertEqual(1.0, isbn["isbn_form:prefer_isbn13"]["metrics"]["automatic_precision"])
        self.assertEqual(22, isbn["isbn_form:prefer_isbn13"]["metrics"]["automatic_predictions"])
        series = {
            entry["identity"]: entry for entry in manifest["options"]
            if entry["identity"].startswith("series:")
        }
        self.assertEqual(19, series["series:primary_narrative"]["metrics"]["automatic_predictions"])
        self.assertEqual(20, series["series:source_order"]["metrics"]["automatic_predictions"])
        self.assertEqual(20, series["series:all_qualified"]["metrics"]["automatic_predictions"])
        self.assertTrue(all(entry["metrics"]["automatic_precision"] == 1.0 for entry in series.values()))

    def test_candidate_exposes_only_closed_aggregate_contract_data(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "automatic_authority", "candidate_execution_count", "evidence_method",
                "excluded_destinations", "frozen_contract",
                "human_only_options", "options", "passing_automatic_options",
                "policy_only_options", "schema", "zero_tolerance",
            },
            set(manifest),
        )
        for name in ("automatic_decision", "compiler", "policy", "target_shape"):
            item = manifest["frozen_contract"][name]
            self.assertEqual({"identity", "sha256"}, set(item))
            self.assertEqual(
                hashlib.sha256(
                    (MANIFEST.parents[2] / item["identity"]).read_bytes()
                ).hexdigest(),
                item["sha256"],
            )
        for name in ("adjudication", "evaluator", "evidence_execution", "thresholds"):
            item = manifest["frozen_contract"][name]
            self.assertEqual({"identity", "sha256"}, set(item))
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            {
                "evaluation_status", "failure_counts", "required_controls",
            },
            set(manifest["zero_tolerance"]),
        )
        self.assertEqual(
            "passed",
            manifest["zero_tolerance"]["evaluation_status"],
        )
        self.assertEqual(
            {
                "incomplete_ordered_list", "invalid_interaction", "invented_value",
                "private_leakage", "stale_or_unsupported_effect",
                "wrong_manifestation",
            },
            set(manifest["zero_tolerance"]["required_controls"]),
        )
        self.assertEqual(
            {name: 0 for name in manifest["zero_tolerance"]["required_controls"]},
            manifest["zero_tolerance"]["failure_counts"],
        )
        forbidden = {
            "accepted_gold", "case_id", "filename", "identifier_value", "path",
            "receipt", "source_sha256", "title", "value",
        }
        def keys(value):
            if isinstance(value, dict):
                yield from value
                for child in value.values():
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        self.assertTrue(forbidden.isdisjoint(keys(manifest)))

    def test_f19a_authority_is_preserved_and_exact_f20_is_added(self) -> None:
        self.assertEqual(
            "cb48d6a0dff269fd576ac9c8fbe7bbe313f291d0368251b097c9178ade456b37",
            F19A_QUALIFICATION_MANIFEST_SHA256,
        )
        self.assertEqual(
            {
                "title_layout:preserve", "title_layout:title_and_subtitle",
                "date_precision:year", "date_precision:full_date",
                "language_rendering:preserve", "language_rendering:display_name",
                "contributor_names:preserve", "contributor_names:canonical",
                "publisher_preference:publisher",
                "series:primary_narrative", "series:source_order",
                "series:all_qualified",
                "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
            },
            QUALIFIED_POLICY_OPTIONS,
        )
        self.assertEqual(
            {
                "title_layout", "date_precision", "language_rendering",
                "contributor_names", "publisher_preference", "series", "isbn_form",
            },
            QUALIFIED_AUTOMATIC,
        )
        policy = resolve_policy(MetadataPolicyProfile.builtin(), "library")
        self.assertEqual("unsupported", policy.qualification("explicit"))
        self.assertEqual("available", policy.qualification("series"))
        self.assertEqual("available", policy.qualification("isbn_form"))
        for field in set(RICH_FIELDS[:-1]) - {"series", "isbn_form"}:
            self.assertEqual("manual_only", policy.qualification(field))

    def test_runtime_envelope_pins_every_terminal_disposition_and_authority(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
            F20_QUALIFICATION_CANDIDATE_SHA256,
        )
        passing = frozenset({
            "series:primary_narrative", "series:source_order",
            "series:all_qualified",
            "isbn_form:preserve_exact", "isbn_form:prefer_isbn13",
        })
        self.assertEqual(passing, frozenset(manifest["passing_automatic_options"]))
        self.assertEqual(passing, F20_PASSING_AUTOMATIC_OPTIONS)
        self.assertEqual(
            {entry["identity"]: entry["disposition"] for entry in manifest["options"]}
            | {"tags:off": "policy_only"},
            F20_OPTION_DISPOSITIONS,
        )
        summary = public_summary()
        self.assertEqual(set(F20_OPTION_DISPOSITIONS), {f"{entry['field']}:{entry['option']}" for entry in summary})
        self.assertEqual(
            passing,
            {
                f"{entry['field']}:{entry['option']}" for entry in summary
                if entry["automatic_available"] is True
            },
        )
        self.assertEqual(
            {
                "insufficient_coverage", "not_qualified", "passing_automatic",
                "policy_only", "passing_human_only",
            },
            {entry["disposition"] for entry in summary},
        )
        with self.assertRaises(TypeError):
            F20_OPTION_DISPOSITIONS["genres:add"] = "passing_automatic"  # type: ignore[index]
        self.assertEqual("insufficient_coverage", F20_OPTION_DISPOSITIONS["genres:add"])


if __name__ == "__main__":
    unittest.main()
