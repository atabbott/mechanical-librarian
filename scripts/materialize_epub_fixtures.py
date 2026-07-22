#!/usr/bin/env python3
"""Materialize the registry's deterministic, licence-safe EPUB cohort offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "fixtures" / "epub_package" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "build" / "epub-fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support.epub_packages import MATERIALIZED_RECIPES  # noqa: E402


class MaterializationError(ValueError):
    """The fixture registry cannot be materialized safely."""


_ENTRY_REQUIRED = {
    "classification", "classification_reason", "consumer_ownership",
    "deterministic_recipe", "expected", "generation_inputs", "id",
    "materialized_filename", "provenance", "provenance_kind", "spdx",
    "stable_identity",
}
_PACKAGE_ENTRY_FIELDS = {
    "expected_exit", "expected_outcome", "expected_projection",
    "expected_projection_sha256", "generator", "resource_partition",
}
_LINKED_GENERATORS = {
    "child-cancellation": "evidence_process_cancellation",
    "child-crash": "evidence_child_crash",
    "child-timeout": "evidence_process_timeout",
    "closed-child-environment": "evidence_closed_child_environment",
    "opening-canonical-envelope-matrix": "tests.unit.test_epub_opening.EpubOpeningTests.test_canonical_envelope_rejects_forged_versions_lineage_and_usage",
    "opening-navigation-document-limit-matrix": "tests.unit.test_epub_opening.EpubOpeningTests.test_node_text_link_and_document_limits_cover_exact_and_rejection_boundaries",
    "opening-node-text-limit-matrix": "tests.unit.test_epub_opening.EpubOpeningTests.test_node_text_link_and_document_limits_cover_exact_and_rejection_boundaries",
    "opening-private-gate-matrix": "tests.unit.test_qualify_epub_opening.EpubOpeningQualificationTests.test_private_usage_and_rejection_decision_table_fail_closed",
    "opening-registry-parser-matrix": "tests.unit.test_materialize_epub_fixtures.MaterializeEpubFixturesTests.test_registry_parser_rejects_consumer_recipe_expectation_and_schema_drift",
    "opening-shared-lineage-envelope-matrix": "tests.unit.test_epub_opening.EpubOpeningTests.test_citations_are_exact_versioned_path_free_and_round_trip_canonically",
    "parent-loss": "evidence_parent_loss",
    "process-group-reap": "evidence_process_group_reap",
}
_VIRTUAL_GENERATORS = {
    "aggregate-expansion-above": "limit_aggregate_expansion",
    "calibre-repetition-conflict": "generate_calibre_conflict",
    "compression-ratio-above": "limit_compression_ratio",
    "doctype-entity": "mutate_doctype_entity",
    "duplicate-global-id": "mutate_duplicate_global_id",
    "foreign-container-namespace": "mutate_foreign_container_namespace",
    "identifier-applicability-matrix": "generate_identifier_matrix",
    "multiple-rootfiles": "generate_multiple_renditions",
    "opaque-resource-encryption": "generate_opaque_encryption",
    "per-member-expansion-above": "limit_per_member_expansion",
    "required-member-crc": "mutate_required_member_crc",
    "required-resource-encryption": "generate_required_encryption",
    "required-resource-encryption-percent-alias": "generate_percent_alias_encryption",
    "standard-refined-metadata-matrix": "generate_metadata_matrix",
    "unsafe-general-purpose-zip-flag": "mutate_unsafe_zip_flag",
    "unsafe-metadata-one-way-projection": "generate_unsafe_metadata",
}
_EXIT_BY_OUTCOME = {
    "success": 0, "no_applicable_evidence": 0, "invalid_invocation": 2,
    "invalid_configuration": 3, "invalid_reference": 3,
    "unavailable_source": 3, "unsupported_format": 4,
    "unsupported_version": 4, "unsupported_compression": 4,
    "unsupported_multiple_renditions": 4, "encrypted": 4, "malformed": 5,
    "integrity_failure": 5, "resource_limit": 5, "timeout": 5,
    "cancellation": 5, "unstable_source": 5, "child_failure": 70,
    "internal_failure": 70,
}
OPENING_CLAIM_FIELDS = frozenset({
    "title", "subtitle", "contributor", "contributor_role", "publisher",
    "imprint", "publication_date", "copyright_date", "edition", "printing",
    "revision", "translation", "illustration", "series", "series_order",
    "omnibus_membership", "anthology_membership", "identifier",
})
OPENING_WARNING_CODES = frozenset({
    "ambiguous_edition", "ambiguous_guide_reference", "ambiguous_identifier",
    "ambiguous_navigation_document", "ambiguous_navigation_landmark",
    "ambiguous_title", "body_transition_reached", "contradictory_series",
    "duplicate_manifest_resource", "encrypted_navigation_document",
    "encrypted_optional_resource", "explicitly_excluded_resource",
    "identifier_applicability_unknown", "image_only_resource",
    "invalid_fragment_target", "invalid_guide_reference",
    "invalid_manifest_fallback", "invalid_manifest_item",
    "invalid_navigation_declaration", "invalid_navigation_target",
    "invalid_ncx_declaration", "invalid_spine_item",
    "malformed_selected_resource", "missing_manifest_resource",
    "missing_selected_resource", "navigation_resource_limit",
    "opaque_resource_encrypted", "recovered_markup", "unsafe_construct",
    "selected_document_limit_reached", "selected_resource_limit",
    "unsafe_claim_suppressed", "unsupported_encoding",
    "unsupported_resource_media", "unmanifested_guide_target",
    "unmanifested_navigation_target", "unusable_navigation_document",
    "unusable_spine_relationship",
})
OPENING_RESOURCE_DISPOSITIONS = frozenset({
    "selected", "no_claims", "missing", "image_only", "malformed",
    "unsupported_media", "unsupported_encoding", "encrypted",
    "resource_limit", "excluded",
})
OPENING_USAGE_FIELDS = frozenset({
    "selected_documents", "opened_resources", "expanded_bytes",
    "text_characters", "nodes", "navigation_links", "strict_documents",
    "recovered_documents",
})
_EPUB3_LANDMARKS = frozenset({
    "titlepage", "halftitlepage", "copyright-page", "dedication",
    "contributors", "imprint", "frontmatter", "seriespage", "other-credits",
})
_EPUB2_GUIDE_TYPES = frozenset({
    "title_page", "copyright_page", "dedication", "acknowledgements",
    "preface", "foreword", "other.ms_firstpage",
})


def is_opening_selection_path(value: object) -> bool:
    if type(value) is not list or any(type(part) is not str for part in value):
        return False
    return (
        value == ["epub3_navigation"]
        or value == ["linear_spine_fallback"]
        or (
            len(value) == 2
            and (
                (value[0] == "epub3_landmark" and value[1] in _EPUB3_LANDMARKS)
                or (value[0] == "epub2_guide" and value[1] in _EPUB2_GUIDE_TYPES)
            )
        )
    )


def _expected(value: object, label: str) -> dict[str, object] | None:
    if value is None and label == "opening":
        return None
    allowed = {"exit", "outcome"}
    if label == "opening":
        allowed.update({"claims", "resources", "usage", "warnings"})
    if (
        type(value) is not dict
        or not {"exit", "outcome"} <= set(value) <= allowed
        or type(value.get("outcome")) is not str
        or value["outcome"] not in _EXIT_BY_OUTCOME
        or type(value.get("exit")) is not int
        or value["exit"] != _EXIT_BY_OUTCOME[value["outcome"]]
    ):
        raise MaterializationError(f"fixture {label} expectation is malformed")
    if label == "opening" and set(value) != {"exit", "outcome"}:
        claims = value.get("claims")
        resources = value.get("resources")
        usage = value.get("usage", {})
        warnings = value.get("warnings")
        if (
            type(claims) is not list
            or any(
                type(claim) is not dict
                or set(claim) != {"field", "raw_value"}
                or claim["field"] not in OPENING_CLAIM_FIELDS
                or type(claim["raw_value"]) is not str
                or not claim["raw_value"]
                or len(claim["raw_value"]) > 280
                or any(marker in claim["raw_value"].casefold() for marker in (".epub", "/", "\\"))
                for claim in claims
            )
            or type(resources) is not list
            or any(
                type(resource) is not dict
                or set(resource) != {"disposition", "selection_path", "warnings"}
                or resource["disposition"] not in OPENING_RESOURCE_DISPOSITIONS
                or not is_opening_selection_path(resource["selection_path"])
                or type(resource["warnings"]) is not list
                or any(warning not in OPENING_WARNING_CODES for warning in resource["warnings"])
                for resource in resources
            )
            or type(usage) is not dict
            or not set(usage) <= OPENING_USAGE_FIELDS
            or any(type(amount) is not int or amount < 0 for amount in usage.values())
            or type(warnings) is not list
            or any(warning not in OPENING_WARNING_CODES for warning in warnings)
        ):
            raise MaterializationError(
                "fixture opening discriminating expectation is malformed"
            )
    return value


def _manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(value) is not dict
        or set(value) != {
            "boundary_matrix", "cohort", "coverage_matrix", "fixtures",
            "generator_module", "materialization", "projections", "schema",
        }
        or value.get("schema") != "synthetic-fixture-manifest-v2"
        or value.get("cohort") != "epub-evidence-f07a-v2"
        or type(value.get("boundary_matrix")) is not dict
        or type(value.get("coverage_matrix")) is not list
        or type(value.get("projections")) is not dict
        or value.get("generator_module") != "tests.unit.test_epub_package_fixture_manifest"
        or type(value.get("fixtures")) is not list
        or value.get("materialization")
        != {
            "flat_output": True,
            "sha256_index": "SHA256SUMS",
            "network": "forbidden",
            "generated_files_committed": False,
            "limits": {
                "maximum_fixture_count": 64,
                "maximum_fixture_bytes": 8_388_608,
                "maximum_total_bytes": 67_108_864,
            },
        }
    ):
        raise MaterializationError("unsupported fixture manifest")
    identities: set[str] = set()
    stable_identities: set[str] = set()
    filenames: set[str] = set()
    for entry in value["fixtures"]:
        if type(entry) is not dict:
            raise MaterializationError("fixture manifest entry is malformed")
        owner = entry.get("consumer_ownership")
        classification = entry.get("classification")
        package_owned = owner == ["f06-package-qualification"]
        opening_owned = owner == ["f07a-opening-qualification"]
        expected_keys = _ENTRY_REQUIRED | (
            _PACKAGE_ENTRY_FIELDS
            if package_owned
            else ({"generator"} if classification == "linked" else set())
        )
        identity = entry.get("id")
        stable_identity = entry.get("stable_identity")
        if (
            set(entry) != expected_keys
            or type(identity) is not str
            or not identity
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in identity)
            or identity in identities
            or stable_identity != f"epub-fixture:{identity}:v1"
            or stable_identity in stable_identities
            or classification not in {"materialized", "virtual", "linked"}
            or not (package_owned or opening_owned)
            or type(entry.get("classification_reason")) is not str
            or not entry["classification_reason"]
            or type(entry.get("provenance")) is not str
            or not entry["provenance"]
            or entry.get("provenance_kind") != "repository-authored-synthetic"
            or entry.get("spdx") != "AGPL-3.0-or-later"
            or type(entry.get("expected")) is not dict
            or set(entry["expected"]) != {"opening", "package"}
            or type(entry.get("generation_inputs")) is not dict
            or set(entry["generation_inputs"]) != {"recipe", "seed"}
            or entry["generation_inputs"].get("seed") != "deterministic"
        ):
            raise MaterializationError("fixture manifest entry is malformed")
        identities.add(identity)
        stable_identities.add(stable_identity)
        package = _expected(entry["expected"]["package"], "package")
        opening = _expected(entry["expected"]["opening"], "opening")
        if package_owned:
            if (
                opening is not None
                or entry.get("expected_outcome") != package["outcome"]
                or entry.get("expected_exit") != package["exit"]
                or type(entry.get("generator")) is not str
                or entry["generation_inputs"]["recipe"] != entry["generator"]
            ):
                raise MaterializationError("package fixture ownership is malformed")
        elif (
            package != {"outcome": "success", "exit": 0}
            or opening is None
            or entry["generation_inputs"]["recipe"]
            != entry.get("generator", entry.get("deterministic_recipe"))
        ):
            raise MaterializationError("opening fixture ownership is malformed")
        recipe = entry.get("deterministic_recipe")
        filename = entry.get("materialized_filename")
        if classification == "materialized":
            if (
                type(recipe) is not str
                or recipe not in MATERIALIZED_RECIPES
                or filename != f"{identity}.epub"
                or filename in filenames
            ):
                raise MaterializationError("materialized fixture recipe is malformed")
            filenames.add(filename)
        elif recipe is not None or filename is not None:
            raise MaterializationError("non-materialized fixture recipe is malformed")
        if (
            classification == "linked"
            and entry.get("generator") != _LINKED_GENERATORS.get(identity)
        ):
            raise MaterializationError("linked fixture generator is not owned")
        if (
            classification == "virtual"
            and entry.get("generator") != _VIRTUAL_GENERATORS.get(identity)
        ):
            raise MaterializationError("virtual fixture generator is not owned")
    if {
        entry["id"] for entry in value["fixtures"] if entry["classification"] == "linked"
    } != set(_LINKED_GENERATORS) or {
        entry["id"] for entry in value["fixtures"] if entry["classification"] == "virtual"
    } != set(_VIRTUAL_GENERATORS):
        raise MaterializationError("fixture evidence rows are incomplete")
    return value


def _materialized_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = manifest.get("fixtures")
    if not isinstance(entries, list):
        raise MaterializationError("fixture manifest entries are malformed")
    selected: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("classification") != "materialized":
            continue
        selected.append(entry)
    return sorted(selected, key=lambda entry: entry["stable_identity"])


def materialize(manifest_path: Path, output: Path) -> tuple[Path, ...]:
    manifest = _manifest(manifest_path)
    contract = manifest.get("materialization")
    if not isinstance(contract, dict):
        raise MaterializationError("fixture materialization contract is malformed")
    limits = contract.get("limits")
    if not isinstance(limits, dict):
        raise MaterializationError("fixture materialization limits are malformed")
    entries = _materialized_entries(manifest)
    maximum_count = limits.get("maximum_fixture_count")
    maximum_fixture_bytes = limits.get("maximum_fixture_bytes")
    maximum_total_bytes = limits.get("maximum_total_bytes")
    if (
        not isinstance(maximum_count, int)
        or not isinstance(maximum_fixture_bytes, int)
        or not isinstance(maximum_total_bytes, int)
        or len(entries) > maximum_count
    ):
        raise MaterializationError("fixture materialization limits are exceeded")
    if output.exists() and any(output.iterdir()):
        raise MaterializationError("output directory must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    total = 0
    written: list[Path] = []
    index: list[str] = []
    for entry in entries:
        value = MATERIALIZED_RECIPES[entry["deterministic_recipe"]]()
        if type(value) is not bytes or len(value) > maximum_fixture_bytes:
            raise MaterializationError("fixture byte limit is exceeded")
        total += len(value)
        if total > maximum_total_bytes:
            raise MaterializationError("cohort byte limit is exceeded")
        target = output / entry["materialized_filename"]
        target.write_bytes(value)
        os.chmod(target, 0o644)
        written.append(target)
        index.append(f"{hashlib.sha256(value).hexdigest()}  {target.name}\n")
    index_name = contract.get("sha256_index")
    if index_name != "SHA256SUMS":
        raise MaterializationError("fixture SHA-256 index contract is malformed")
    index_path = output / index_name
    index_path.write_text("".join(index), encoding="ascii", newline="\n")
    os.chmod(index_path, 0o644)
    written.append(index_path)
    return tuple(written)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    try:
        written = materialize(arguments.manifest, arguments.output)
    except (OSError, json.JSONDecodeError, MaterializationError) as error:
        parser.error(str(error))
    print(f"materialized={len(written) - 1} index={written[-1].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
