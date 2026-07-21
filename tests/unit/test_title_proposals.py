from __future__ import annotations

import copy
import hashlib
import json
import unittest
from dataclasses import FrozenInstanceError

from mechanical_librarian.domain.title_proposals import (
    ProposalExecutionMode,
    TITLE_COMPARISON_VERSION,
    TITLE_PROPOSAL_POLICY_VERSION,
    TITLE_PROPOSAL_SCHEMA,
    TitleProposalResult,
    TitleProposalSet,
    TitleProposalValueError,
    normalized_title_key,
    proposal_set_from_manifest,
)


def manifest() -> dict[str, object]:
    return {
        "comparison_version": TITLE_COMPARISON_VERSION,
        "epub_digest": "a" * 64,
        "f06": {
            "chosen_citation": {
                "expanded_name": "{http://purl.org/dc/elements/1.1/}title",
                "resource_identity": "e" * 64,
                "section": "metadata",
                "selector": None,
                "source_ordinal": 1,
            },
            "chosen_observation": {
                "applicability": "work",
                "compatibility_version": "epub-package-contract-v1",
                "extractor_version": "epub-package-v1",
                "normalized_value": "A Synthetic Book",
                "raw_value": "A Synthetic Book",
                "schema_version": "epub-package-observation-v1",
            },
            "extractor_versions": ["epub-package-v1"],
            "report_identity": "b" * 64,
            "report_schema": "local-evidence-report-v1",
            "source_fingerprint": "a" * 64,
        },
        "fixture_version": "synthetic-title-scenarios-v1",
        "generator_version": "synthetic-epub-generator-v1",
        "lineage": "synthetic-title-preview-agree-empty",
        "lineage_revision": 1,
        "mode": ProposalExecutionMode.SYNTHETIC_PREVIEW.value,
        "opaque_file_identity": "c" * 64,
        "policy_version": TITLE_PROPOSAL_POLICY_VERSION,
        "previous_set_id": None,
        "proposal_schema": TITLE_PROPOSAL_SCHEMA,
        "provider": {
            "candidate": None,
            "outcome": "empty",
            "version": "synthetic-title-provider-v1",
        },
        "scenario": "agree-empty",
        "scenario_revision": 1,
        "snapshot": {
            "current_fingerprint": "d" * 64,
            "fingerprint": "d" * 64,
            "id": "synthetic-snapshot-1",
            "schema": "synthetic-library-snapshot-v1",
            "shape": {
                "external_item": "synthetic-item",
                "metadata": {"title": None},
                "state": "loaded",
            },
        },
        "unicode_version": "16.0.0",
    }


def hardcover_manifest(
    version: str = "hardcover-title-poc-v1",
) -> dict[str, object]:
    value = manifest()
    provider = {
        "candidate": None,
        "failure_kind": None,
        "outcome": "disabled",
        "version": version,
    }
    digest = hashlib.sha256(
        json.dumps(provider, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    value["lineage"] = f"hardcover-poc-{digest[:48]}"
    value["provider"] = provider
    return value


class TitleProposalDomainTests(unittest.TestCase):
    def test_hardcover_profile_is_closed_canonical_and_does_not_relax_f09a(self) -> None:
        value = proposal_set_from_manifest(
            manifest=hardcover_manifest(),
            result=TitleProposalResult.PROVIDER_DISABLED,
        )
        self.assertEqual(TitleProposalSet.from_json(value.to_json()), value)
        for mutate in (
            lambda data: data["manifest"]["provider"].__setitem__("version", "other"),
            lambda data: data["manifest"]["provider"].__setitem__("failure_kind", "timeout"),
            lambda data: data["manifest"].__setitem__("lineage", "hardcover-poc-" + "0" * 48),
            lambda data: data["manifest"].__setitem__("scenario", "agree-different"),
        ):
            with self.subTest(mutate=mutate):
                data = json.loads(value.to_json())
                mutate(data)
                with self.assertRaises(TitleProposalValueError):
                    TitleProposalSet.from_json(
                        json.dumps(data, separators=(",", ":"), sort_keys=True)
                    )

    def test_hardcover_v1_history_hydrates_but_v2_no_candidate_identity_is_distinct(
        self,
    ) -> None:
        legacy = proposal_set_from_manifest(
            manifest=hardcover_manifest("hardcover-title-poc-v1"),
            result=TitleProposalResult.PROVIDER_DISABLED,
        )
        current = proposal_set_from_manifest(
            manifest=hardcover_manifest("hardcover-title-poc-v2"),
            result=TitleProposalResult.PROVIDER_DISABLED,
        )

        self.assertEqual(TitleProposalSet.from_json(legacy.to_json()), legacy)
        self.assertEqual(TitleProposalSet.from_json(current.to_json()), current)
        self.assertNotEqual(legacy.lineage, current.lineage)
        self.assertNotEqual(legacy.identity_digest, current.identity_digest)
        self.assertNotEqual(legacy.set_id, current.set_id)
        self.assertNotEqual(legacy.to_json(), current.to_json())

    def test_unicode_comparison_is_nfc_casefold_nfc_and_unicode_space_collapsed(self) -> None:
        equivalent = (
            ("Café", "CAFE\u0301"),
            ("Straße", "STRASSE"),
            ("A\u00a0Synthetic\tBook", "  a synthetic book  "),
            ("\u212b", "A\u030a"),
        )
        for left, right in equivalent:
            with self.subTest(left=left, right=right):
                self.assertEqual(normalized_title_key(left), normalized_title_key(right))

    def test_identity_roundtrip_is_canonical_and_applicability_is_derived(self) -> None:
        value = proposal_set_from_manifest(
            manifest=manifest(),
            result=TitleProposalResult.NEEDS_REVIEW,
            proposed_value="A Synthetic Book",
        )
        self.assertEqual(TitleProposalSet.from_json(value.to_json()), value)
        self.assertFalse(value.production_applicable)
        self.assertNotIn("production_applicable", value.__dict__)
        with self.assertRaises(FrozenInstanceError):
            value.mode = ProposalExecutionMode.SYNTHETIC_PREVIEW  # type: ignore[misc]

    def test_every_manifest_field_is_identity_bearing(self) -> None:
        original = proposal_set_from_manifest(
            manifest=manifest(), result=TitleProposalResult.PROVIDER_EMPTY
        )
        for field in manifest():
            with self.subTest(field=field):
                data = json.loads(original.to_json())
                changed = copy.deepcopy(data)
                if field == "lineage_revision":
                    changed["manifest"][field] = 2
                elif field == "previous_set_id":
                    changed["manifest"][field] = "ps-predecessor"
                else:
                    changed["manifest"][field] = "changed"
                serialized = json.dumps(
                    changed, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                with self.assertRaises(TitleProposalValueError):
                    TitleProposalSet.from_json(serialized)

    def test_unknown_mode_and_independent_applicability_fail_closed(self) -> None:
        value = proposal_set_from_manifest(
            manifest=manifest(), result=TitleProposalResult.PROVIDER_EMPTY
        )
        data = json.loads(value.to_json())
        data["mode"] = "production"
        with self.assertRaises(TitleProposalValueError):
            TitleProposalSet.from_json(json.dumps(data, separators=(",", ":"), sort_keys=True))

    def test_synthetic_result_vocabulary_rejects_live_only_values_on_hydration(self) -> None:
        value = proposal_set_from_manifest(
            manifest=manifest(), result=TitleProposalResult.PROVIDER_EMPTY
        )
        for live_only in (
            "source_not_ready",
            "provider_not_ready",
            "evidence_disagreement",
        ):
            with self.subTest(result=live_only):
                data = json.loads(value.to_json())
                data["result"] = live_only
                with self.assertRaisesRegex(
                    TitleProposalValueError, "proposal-set result is invalid"
                ):
                    TitleProposalSet.from_json(
                        json.dumps(data, separators=(",", ":"), sort_keys=True)
                    )

    def test_complete_proposal_semantics_are_identity_bearing_and_exactly_projected(self) -> None:
        value = proposal_set_from_manifest(
            manifest=manifest(),
            result=TitleProposalResult.NEEDS_REVIEW,
            proposed_value="A Synthetic Book",
        )
        mutations = (
            lambda data: data.__setitem__("result", "no_change"),
            lambda data: data["proposals"][0].__setitem__("proposed_value", "Changed"),
            lambda data: data["proposals"][0].__setitem__("current_value", "Changed"),
            lambda data: data["proposals"][0].__setitem__("kind", "replace_populated"),
            lambda data: data["proposals"][0].__setitem__("id", "proposal-changed"),
            lambda data: data["proposals"].append(copy.deepcopy(data["proposals"][0])),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = json.loads(value.to_json())
                mutate(data)
                with self.assertRaises(TitleProposalValueError):
                    TitleProposalSet.from_json(
                        json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                    )

        changed_manifest = manifest()
        changed_manifest["f06"]["chosen_observation"]["raw_value"] = "Changed"  # type: ignore[index]
        changed_manifest["f06"]["chosen_observation"]["normalized_value"] = "Changed"  # type: ignore[index]
        changed = proposal_set_from_manifest(
            manifest=changed_manifest,
            result=TitleProposalResult.NEEDS_REVIEW,
            proposed_value="Changed",
        )
        changed_result = proposal_set_from_manifest(
            manifest=manifest(), result=TitleProposalResult.PROVIDER_EMPTY
        )
        self.assertNotEqual(value.identity_digest, changed.identity_digest)
        self.assertNotEqual(value.identity_digest, changed_result.identity_digest)
        self.assertNotEqual(value.proposals[0].id, changed.proposals[0].id)

    def test_manifest_storage_and_projections_do_not_alias_mutable_inputs(self) -> None:
        source = manifest()
        value = proposal_set_from_manifest(
            manifest=source, result=TitleProposalResult.PROVIDER_EMPTY
        )
        canonical = value.to_json()
        source["snapshot"]["shape"]["metadata"]["title"] = "Mutated"  # type: ignore[index]
        self.assertEqual(value.to_json(), canonical)
        with self.assertRaises(TypeError):
            value.manifest["scenario"] = "mutated"  # type: ignore[index]
        projection = value.to_data()
        projection["manifest"]["snapshot"]["shape"]["metadata"]["title"] = "Mutated"  # type: ignore[index]
        self.assertEqual(value.to_json(), canonical)
        data = json.loads(value.to_json())
        data["production_applicable"] = True
        with self.assertRaises(TitleProposalValueError):
            TitleProposalSet.from_json(json.dumps(data, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
