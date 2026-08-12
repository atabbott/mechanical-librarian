"""Public contract proof for the non-authorizing F20 qualification candidate."""

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


MANIFEST = (
    Path(__file__).parents[2]
    / "docs/reference/f20-rich-metadata-qualification-candidate-v1.json"
)
RICH_FIELDS = (
    "subtitle", "series", "genres", "tags", "description_fill", "isbn_form",
    "asin", "explicit",
)


class RichMetadataQualificationManifestTests(unittest.TestCase):
    def test_candidate_is_closed_field_local_and_non_authorizing(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual("f20-rich-metadata-qualification-candidate-v1", manifest["schema"])
        self.assertEqual("none", manifest["automatic_authority"])
        self.assertEqual([], manifest["passing_automatic_options"])
        self.assertEqual(["explicit:unsupported"], manifest["human_only_options"])
        self.assertEqual(["narrators", "cover"], manifest["excluded_destinations"])
        self.assertEqual(0, manifest["candidate_execution_count"])
        self.assertEqual(0, manifest["final_holdout"]["execution_count"])

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
            expected - {"explicit:unsupported"},
            {
                entry["identity"] for entry in manifest["options"]
                if entry["disposition"] == "insufficient_coverage"
            },
        )
        for entry in manifest["options"]:
            self.assertEqual(
                {
                    "cohort_sufficiency", "disposition", "eligible_opportunities",
                    "identity", "metrics",
                },
                set(entry),
            )
            self.assertIn(entry["cohort_sufficiency"], {"insufficient", "not_applicable"})
            self.assertEqual(0, entry["eligible_opportunities"])
            self.assertEqual(
                {
                    "automatic_precision": None,
                    "automatic_predictions": 0,
                    "automatic_recall": None,
                    "structural_or_sensitive_violations": 0,
                    "unsupported_output": 0,
                },
                entry["metrics"],
            )

    def test_candidate_exposes_only_closed_aggregate_contract_data(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "automatic_authority", "candidate_execution_count",
                "excluded_destinations", "final_holdout", "frozen_contract",
                "human_only_options", "options", "passing_automatic_options",
                "policy_only_options", "schema", "zero_tolerance",
            },
            set(manifest),
        )
        for name in ("compiler", "policy", "target_shape"):
            item = manifest["frozen_contract"][name]
            self.assertEqual({"identity", "sha256"}, set(item))
            self.assertEqual(
                hashlib.sha256(
                    (MANIFEST.parents[2] / item["identity"]).read_bytes()
                ).hexdigest(),
                item["sha256"],
            )
        for name in ("evaluator", "fake_service_qualification", "thresholds"):
            self.assertEqual(
                {"status": "not_frozen_without_a_fresh_batch"},
                manifest["frozen_contract"][name],
            )
        self.assertEqual(
            {
                "evaluation_status", "required_controls",
            },
            set(manifest["zero_tolerance"]),
        )
        self.assertEqual(
            "not_observed_without_a_fresh_frozen_batch",
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
        forbidden = {"case", "corpus", "digest", "identifier", "path", "receipt", "source", "title", "value"}
        def keys(value):
            if isinstance(value, dict):
                yield from value
                for child in value.values():
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        self.assertTrue(forbidden.isdisjoint(keys(manifest)))

    def test_existing_f19a_authority_and_rich_non_authority_remain_closed(self) -> None:
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
            },
            QUALIFIED_POLICY_OPTIONS,
        )
        self.assertEqual(
            {"title_layout", "date_precision", "language_rendering", "contributor_names", "publisher_preference"},
            QUALIFIED_AUTOMATIC,
        )
        policy = resolve_policy(MetadataPolicyProfile.builtin(), "library")
        self.assertEqual("unsupported", policy.qualification("explicit"))
        for field in RICH_FIELDS[:-1]:
            self.assertEqual("manual_only", policy.qualification(field))


if __name__ == "__main__":
    unittest.main()
