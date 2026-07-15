#!/usr/bin/env python3
"""Run an installed F07a CLI over a synthetic or private EPUB cohort safely."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.materialize_epub_fixtures import (
    MaterializationError,
    OPENING_CLAIM_FIELDS,
    OPENING_RESOURCE_DISPOSITIONS,
    OPENING_USAGE_FIELDS,
    OPENING_WARNING_CODES,
    _manifest,
    is_opening_selection_path,
)


MAX_REPORT_BYTES = 524_288
LIBRARY_ID = "00000000-0000-4000-8000-000000000009"
_OPENING_OUTCOMES = {"success", "no_applicable_evidence"}
_PACKAGE_OUTCOMES = {
    "success", "no_applicable_evidence", "invalid_invocation",
    "invalid_configuration", "invalid_reference", "unsupported_format",
    "unsupported_version", "unsupported_compression",
    "unsupported_multiple_renditions", "encrypted", "unavailable_source",
    "malformed", "integrity_failure", "resource_limit", "timeout",
    "cancellation", "unstable_source", "child_failure", "internal_failure",
}
_REJECTION_OUTCOMES = {
    "resource_limit", "timeout", "unavailable_source", "unstable_source",
}
_CLAIM_FIELDS = OPENING_CLAIM_FIELDS
_WARNINGS = OPENING_WARNING_CODES
_RESOURCE_DISPOSITIONS = OPENING_RESOURCE_DISPOSITIONS
_PARSER_MODES = {"strict", "recovered"}
_EXIT_BY_OUTCOME = {
    "invalid_invocation": 2, "success": 0, "no_applicable_evidence": 0,
    "invalid_configuration": 3, "invalid_reference": 3,
    "unavailable_source": 3, "unsupported_format": 4,
    "unsupported_version": 4, "unsupported_compression": 4,
    "unsupported_multiple_renditions": 4, "encrypted": 4, "malformed": 5,
    "integrity_failure": 5, "resource_limit": 5, "timeout": 5,
    "cancellation": 5, "unstable_source": 5, "child_failure": 70,
    "internal_failure": 70,
}
_OPENING_LIMITS = {
    "selected_documents": 5,
    "expanded_bytes_per_resource": 1_048_576,
    "expanded_bytes_per_selection": 5_242_880,
    "text_characters_per_selection": 524_288,
    "nodes_per_resource": 25_000,
    "nodes_per_selection": 100_000,
    "document_depth": 64,
    "navigation_links": 2_048,
    "wall_seconds": 60,
    "child_cpu_seconds": 30,
    "termination_grace_seconds": 2,
    "facts": 64,
    "warnings": 32,
    "excerpt_characters": 280,
    "report_bytes": 524_288,
}
_PACKAGE_LIMITS = {
    "profile": "epub-package-limits-v1",
    "source_archive_bytes": 268_435_456,
    "archive_entries": 2_048,
    "compressed_bytes_per_member": 268_435_456,
    "expanded_bytes_per_member": 268_435_456,
    "expanded_bytes_per_archive": 1_073_741_824,
    "compression_ratio_per_member": 200,
    "container_xml_bytes": 65_536,
    "opf_package_bytes": 4_194_304,
    "xml_nodes_per_document": 50_000,
    "xml_attributes_per_document": 100_000,
    "attributes_per_element": 64,
    "xml_depth": 64,
    "decoded_xml_text_characters": 262_144,
    "metadata_nodes": 512,
    "manifest_items": 4_096,
    "spine_items": 4_096,
    "guide_references": 256,
    "prefix_mappings": 64,
}
_USAGE_FIELDS = OPENING_USAGE_FIELDS
_ROOT_CAUSE_CATEGORIES = {
    "f06_no_applicable_evidence", "f06_invalid_reference",
    "f06_unsupported_format", "f06_unsupported_version",
    "f06_unsupported_compression", "f06_unsupported_multiple_renditions",
    "f06_encrypted", "f06_malformed", "f06_integrity_failure",
    "f06_resource_limit", "encrypted", "unsupported_media", "unsupported_encoding",
    "unsafe_construct", "unstable_source", "resource_limit", "timeout",
}


def _selection_digest(
    same_file_lineage: str, resources: list[dict[str, object]]
) -> str:
    payload = json.dumps(
        [
            {
                "resource_identity": resource["resource_identity"],
                "selection_path": resource["selection_path"],
            }
            for resource in resources
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(
        ("epub-opening-selection-v1\0" + same_file_lineage + "\0" + payload).encode(
            "utf-8"
        )
    ).hexdigest()


class QualificationError(ValueError):
    """One aggregate qualification invariant failed without exposing a book."""


def _configuration(mount: Path) -> dict[str, object]:
    return {
        "schema_version": "config-v1",
        "instance_name": "epub-opening-qualification",
        "connections": [
            {
                "adapter": "audiobookshelf",
                "endpoint": "https://qualification.invalid",
                "secret_reference": "unused-qualification-secret",
                "enabled_libraries": [LIBRARY_ID],
                "capability_overrides": ["inventory-read"],
            }
        ],
        "media_mounts": [
            {
                "name": "cohort",
                "library_type": "ebook",
                "container_path": str(mount),
                "service_path": "/qualification",
                "read_only": True,
            }
        ],
        # Reproduce the closed F06 private baseline with the package profile's
        # default archive ceiling. The ordinary deployment default remains the
        # narrower shared 64 MiB ceiling.
        "evidence": {"maximum_document_bytes": 268_435_456},
        "providers": [],
        "reviewers": [],
        "notifications": [],
        "backups_enabled": True,
    }


def _synthetic_cases(
    manifest_path: Path, cohort: Path
) -> list[tuple[str, int, str, dict[str, object]]]:
    try:
        manifest = _manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, MaterializationError):
        raise QualificationError("synthetic registry schema is unsupported") from None
    result: list[tuple[str, int, str, dict[str, object]]] = []
    for entry in manifest.get("fixtures", []):
        if (
            type(entry) is not dict
            or "f07a-opening-qualification" not in entry.get("consumer_ownership", [])
            or entry.get("classification") != "materialized"
        ):
            continue
        expected = entry.get("expected", {}).get("opening")
        filename = entry.get("materialized_filename")
        if (
            type(expected) is not dict
            or type(filename) is not str
            or Path(filename).name != filename
            or not (cohort / filename).is_file()
        ):
            raise QualificationError("synthetic cohort is incomplete")
        result.append((filename, expected["exit"], expected["outcome"], expected))
    if not result:
        raise QualificationError("synthetic cohort has no F07a consumers")
    return sorted(result)


def _private_cases(cohort: Path) -> list[tuple[str, int, str]]:
    result: list[tuple[str, int, str]] = []
    for root, directories, files in os.walk(cohort, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if not (Path(root) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(root) / name
            try:
                mode = path.lstat().st_mode
            except OSError:
                raise QualificationError("private cohort could not be enumerated") from None
            if name.casefold().endswith(".epub") and stat.S_ISREG(mode):
                result.append((path.relative_to(cohort).as_posix(), -1, ""))
    return result


def _root_causes(path: Path) -> dict[str, frozenset[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise QualificationError("private root-cause record is invalid") from None
    if type(value) is not dict or set(value) != {"cases", "schema"} or value["schema"] != "epub-opening-private-root-causes-v1" or type(value["cases"]) is not dict:
        raise QualificationError("private root-cause record is invalid")
    result: dict[str, frozenset[str]] = {}
    for relative, categories in value["cases"].items():
        if (
            type(relative) is not str
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or type(categories) is not list
            or not categories
            or any(type(category) is not str or category not in _ROOT_CAUSE_CATEGORIES for category in categories)
            or len(categories) != len(set(categories))
        ):
            raise QualificationError("private root-cause record is invalid")
        result[relative] = frozenset(categories)
    return result


def _invoke(
    executable: Path,
    command: str,
    config: Path,
    relative: str,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    try:
        completed = subprocess.run(
            (
                str(executable),
                command,
                "--config",
                str(config),
                "--mount",
                "cohort",
                "--file",
                relative,
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise QualificationError("installed CLI did not complete within its envelope") from None
    payload = completed.stdout if completed.returncode == 0 else completed.stderr
    if (
        not payload.endswith(b"\n")
        or payload.count(b"\n") != 1
        or len(payload) > MAX_REPORT_BYTES + 1
        or (completed.stderr and completed.returncode == 0)
    ):
        raise QualificationError("installed CLI returned an invalid envelope")
    try:
        report = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise QualificationError("installed CLI returned invalid JSON") from None
    if type(report) is not dict or type(report.get("outcome")) is not str:
        raise QualificationError("installed CLI returned an invalid report")
    return completed.returncode, report


def qualify(
    executable: Path,
    cohort: Path,
    cases: list[tuple[str, int, str] | tuple[str, int, str, dict[str, object]]],
    *,
    private: bool,
    timeout: int,
    root_causes: dict[str, frozenset[str]] | None = None,
) -> dict[str, object]:
    outcomes: Counter[str] = Counter()
    package_outcomes: Counter[str] = Counter()
    claims: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    resources: Counter[str] = Counter()
    parser_modes: Counter[str] = Counter()
    f06_readable = 0
    privacy_checks = Counter({"report_envelope_bounded": 0, "aggregate_output_allowlisted": 1})
    required_root_causes: dict[str, set[str]] = {}
    usage_totals = {name: 0 for name in sorted(_USAGE_FIELDS)}
    usage_maxima = {name: 0 for name in sorted(_USAGE_FIELDS)}

    def require_root_cause(relative: str, category: str) -> None:
        required_root_causes.setdefault(relative, set()).add(category)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ml-f07a-qualification-") as directory:
        config = Path(directory) / "configuration.json"
        config.write_text(
            json.dumps(_configuration(cohort), separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        for case in cases:
            relative, expected_exit, expected_outcome = case[:3]
            expected_details = case[3] if len(case) == 4 else None
            if private:
                package_exit, package = _invoke(
                    executable, "epub-package-report", config, relative, timeout
                )
                package_outcome = package["outcome"]
                if package_outcome not in _PACKAGE_OUTCOMES:
                    raise QualificationError("F06 outcome vocabulary is malformed")
                if package_exit != _EXIT_BY_OUTCOME[package_outcome]:
                    raise QualificationError("F06 exit does not match its outcome")
                package_outcomes[package_outcome] += 1
                if package_outcome != "success":
                    category = f"f06_{package_outcome}"
                    if category not in _ROOT_CAUSE_CATEGORIES:
                        raise QualificationError("F06 rejection lacks a closed root cause")
                    require_root_cause(relative, category)
                    continue
                f06_readable += 1
            exit_code, report = _invoke(
                executable, "epub-opening-report", config, relative, timeout
            )
            privacy_checks["report_envelope_bounded"] += 1
            if not private and (
                exit_code != expected_exit or report["outcome"] != expected_outcome
            ):
                raise QualificationError("synthetic expectation did not match")
            outcome = report["outcome"]
            if outcome not in _PACKAGE_OUTCOMES or exit_code != _EXIT_BY_OUTCOME[outcome]:
                raise QualificationError("opening exit does not match its outcome")
            if set(report) != {
                "claims", "limits", "outcome", "resources", "same_file_lineage",
                "schema", "selection_digest", "source_fingerprint", "usage",
                "warnings",
            } or report.get("schema") != "epub-opening-report-v1":
                raise QualificationError("opening report envelope is malformed")
            if private and outcome not in _OPENING_OUTCOMES:
                expected_category = {
                    "resource_limit": "resource_limit",
                    "timeout": "timeout",
                    "unstable_source": "unstable_source",
                    "unavailable_source": "unstable_source",
                }.get(outcome)
                if (
                    outcome not in _REJECTION_OUTCOMES
                    or expected_category is None
                ):
                    raise QualificationError("opening rejection lacks a closed root cause")
                require_root_cause(relative, expected_category)
            outcomes[outcome] += 1
            claim_values = report.get("claims", [])
            warning_values = report.get("warnings", [])
            resource_values = report.get("resources", [])
            limits_value = report.get("limits")
            usage_value = report.get("usage")
            if any(type(value) is not list for value in (claim_values, warning_values, resource_values)) or type(limits_value) is not dict or type(usage_value) is not dict:
                raise QualificationError("opening aggregate collections are malformed")
            if (
                set(limits_value) != {"package", "profile", *_OPENING_LIMITS}
                or limits_value.get("profile") != "epub-opening-limits-v1"
                or limits_value.get("package") != _PACKAGE_LIMITS
                or any(limits_value.get(name) != expected for name, expected in _OPENING_LIMITS.items())
                or set(usage_value) != _USAGE_FIELDS
                or any(type(value) is not int or value < 0 for value in usage_value.values())
            ):
                raise QualificationError("opening limits or usage profile is malformed")
            if outcome not in _OPENING_OUTCOMES and (
                claim_values
                or warning_values
                or resource_values
                or any(usage_value.values())
                or report.get("source_fingerprint") is not None
                or report.get("same_file_lineage") is not None
            ):
                raise QualificationError("opening rejection contains partial results")
            if (
                usage_value["selected_documents"] > _OPENING_LIMITS["selected_documents"]
                or usage_value["opened_resources"] > _OPENING_LIMITS["selected_documents"] + 1
                or usage_value["expanded_bytes"] > _OPENING_LIMITS["expanded_bytes_per_selection"]
                or usage_value["text_characters"] > _OPENING_LIMITS["text_characters_per_selection"]
                or usage_value["nodes"] > _OPENING_LIMITS["nodes_per_selection"]
                or usage_value["navigation_links"] > _OPENING_LIMITS["navigation_links"]
                or usage_value["strict_documents"] + usage_value["recovered_documents"] > usage_value["opened_resources"]
            ):
                raise QualificationError("opening usage exceeds the exact profile")
            for name in _USAGE_FIELDS:
                usage_totals[name] += usage_value[name]
                usage_maxima[name] = max(usage_maxima[name], usage_value[name])
            for claim in claim_values:
                citation = claim.get("citation") if type(claim) is dict else None
                if (
                    type(claim) is not dict
                    or claim.get("field") not in _CLAIM_FIELDS
                    or type(citation) is not dict
                    or type(citation.get("resource_identity")) is not str
                    or citation.get("parse_mode") not in _PARSER_MODES
                    or type(citation.get("selection_path")) is not list
                ):
                    raise QualificationError("claim aggregate is malformed")
                claims[claim["field"]] += 1
            for warning in warning_values:
                if warning not in _WARNINGS:
                    raise QualificationError("warning aggregate is malformed")
                warnings[warning] += 1
            content_resources = 0
            opened_resources = 0
            strict_resources = 0
            recovered_resources = 0
            selected_resources: dict[str, dict[str, object]] = {}
            navigation_resources = 0
            for resource in resource_values:
                if (
                    type(resource) is not dict
                    or set(resource) != {"disposition", "opened", "parse_mode", "resource_identity", "selection_path", "warnings"}
                    or resource.get("disposition") not in _RESOURCE_DISPOSITIONS
                    or type(resource.get("opened")) is not bool
                    or type(resource.get("resource_identity")) is not str
                    or len(resource["resource_identity"]) != 64
                    or any(value not in "0123456789abcdef" for value in resource["resource_identity"])
                    or type(resource.get("selection_path")) is not list
                    or not resource["selection_path"]
                    or any(type(value) is not str for value in resource["selection_path"])
                    or type(resource.get("warnings")) is not list
                    or any(type(value) is not str or value not in _WARNINGS for value in resource["warnings"])
                ):
                    raise QualificationError("resource aggregate is malformed")
                path = resource["selection_path"]
                if not is_opening_selection_path(path):
                    raise QualificationError("resource selection path is malformed")
                if path != ["epub3_navigation"]:
                    content_resources += 1
                else:
                    navigation_resources += 1
                    if navigation_resources > 1 or resource is not resource_values[0]:
                        raise QualificationError("navigation resource cardinality is malformed")
                resources[resource["disposition"]] += 1
                if private:
                    disposition = resource["disposition"]
                    if disposition == "missing":
                        raise QualificationError("private resource rejection is not permitted")
                    if disposition == "malformed" and "unsafe_construct" not in resource["warnings"]:
                        raise QualificationError("ordinary malformed private resource is not permitted")
                    category = {
                        "encrypted": "encrypted",
                        "unsupported_media": "unsupported_media",
                        "unsupported_encoding": "unsupported_encoding",
                        "malformed": "unsafe_construct",
                        "resource_limit": "resource_limit",
                    }.get(disposition)
                    if category is not None:
                        require_root_cause(relative, category)
                mode = resource.get("parse_mode")
                if mode is not None and mode not in _PARSER_MODES:
                    raise QualificationError("parser-mode aggregate is malformed")
                disposition = resource["disposition"]
                opened = resource["opened"]
                parsed_dispositions = {"selected", "no_claims", "excluded"}
                if (
                    (mode is not None and disposition not in parsed_dispositions)
                    or (mode is not None and not opened)
                    or (disposition in {"selected", "no_claims"} and (mode is None or not opened))
                    or (
                        disposition in {"missing", "image_only", "unsupported_media", "encrypted"}
                        and (mode is not None or opened)
                    )
                    or (disposition == "unsupported_encoding" and (mode is not None or not opened))
                    or (disposition == "excluded" and opened != (mode is not None))
                    or (
                        path == ["epub3_navigation"]
                        and disposition not in {"no_claims", "malformed", "resource_limit", "encrypted"}
                    )
                ):
                    raise QualificationError("resource state is inconsistent")
                opened_resources += int(opened)
                if type(mode) is str:
                    parser_modes[mode] += 1
                    strict_resources += int(mode == "strict")
                    recovered_resources += int(mode == "recovered")
                if disposition == "selected" and path != ["epub3_navigation"]:
                    selected_resources[resource["resource_identity"]] = resource
            if len(
                {(resource["resource_identity"], tuple(resource["selection_path"])) for resource in resource_values}
            ) != len(resource_values) or len(
                {resource["resource_identity"] for resource in resource_values if resource["selection_path"] != ["epub3_navigation"]}
            ) != content_resources:
                raise QualificationError("resource identities are not unique")
            same_file_lineage = report.get("same_file_lineage")
            source_fingerprint = report.get("source_fingerprint")
            if outcome in _OPENING_OUTCOMES:
                if (
                    type(same_file_lineage) is not str
                    or len(same_file_lineage) != 64
                    or any(value not in "0123456789abcdef" for value in same_file_lineage)
                    or type(source_fingerprint) is not dict
                    or set(source_fingerprint) != {"algorithm", "digest", "schema"}
                    or source_fingerprint.get("algorithm") != "sha256"
                    or source_fingerprint.get("schema") != "source-v1"
                    or type(source_fingerprint.get("digest")) is not str
                    or len(source_fingerprint["digest"]) != 64
                    or any(value not in "0123456789abcdef" for value in source_fingerprint["digest"])
                    or same_file_lineage
                    != hashlib.sha256(
                        ("source-v1\0" + source_fingerprint["digest"] + "\0local-file-v1").encode("ascii")
                    ).hexdigest()
                    or report.get("selection_digest")
                    != _selection_digest(same_file_lineage, resource_values)
                ):
                    raise QualificationError("opening selection provenance is malformed")
            elif report.get("selection_digest") is not None:
                raise QualificationError("opening rejection selection provenance is malformed")
            for claim in claim_values:
                citation = claim["citation"]
                selected = selected_resources.get(citation["resource_identity"])
                if (
                    selected is None
                    or selected["parse_mode"] != citation["parse_mode"]
                    or selected["selection_path"] != citation["selection_path"]
                ):
                    raise QualificationError("claim resource provenance is inconsistent")
            if (
                usage_value["selected_documents"] != content_resources
                or usage_value["opened_resources"] != opened_resources
                or usage_value["strict_documents"] != strict_resources
                or usage_value["recovered_documents"] != recovered_resources
                or (outcome == "success" and not claim_values)
                or (outcome == "no_applicable_evidence" and claim_values)
            ):
                raise QualificationError("opening aggregate relationships are malformed")
            if expected_details is not None and set(expected_details) != {"exit", "outcome"}:
                expected_claims = expected_details["claims"]
                expected_resources = expected_details["resources"]
                if (
                    [
                        {"field": claim["field"], "raw_value": claim["raw_value"]}
                        for claim in claim_values
                    ]
                    != expected_claims
                    or [
                        {
                            "disposition": resource["disposition"],
                            "selection_path": resource["selection_path"],
                            "warnings": resource["warnings"],
                        }
                        for resource in resource_values
                    ]
                    != expected_resources
                    or warning_values != expected_details["warnings"]
                    or any(
                        usage_value[name] != amount
                        for name, amount in expected_details.get("usage", {}).items()
                    )
                ):
                    raise QualificationError(
                        "synthetic discriminating expectation did not match"
                    )
            report = None
    normalized_required = {
        relative: frozenset(categories)
        for relative, categories in required_root_causes.items()
    }
    if root_causes is not None and root_causes != normalized_required:
        raise QualificationError("private root-cause record does not exactly match rejections")
    return {
        "claims_by_field": dict(sorted(claims.items())),
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "f06_outcomes": dict(sorted(package_outcomes.items())),
        "f06_readable": f06_readable if private else None,
        "limits_profile": {
            "profile": "epub-opening-limits-v1",
            **_OPENING_LIMITS,
        },
        "opening_outcomes": dict(sorted(outcomes.items())),
        "parser_modes": dict(sorted(parser_modes.items())),
        "privacy_checks": dict(sorted(privacy_checks.items())),
        "resource_dispositions": dict(sorted(resources.items())),
        "total": len(cases),
        "usage_maxima": usage_maxima,
        "usage_totals": usage_totals,
        "warnings": dict(sorted(warnings.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "epub_package"
        / "manifest.json",
    )
    parser.add_argument("--private", action="store_true")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-f06-readable", type=int)
    parser.add_argument("--root-causes", type=Path)
    parser.add_argument("--timeout", type=int, default=65)
    arguments = parser.parse_args(argv)
    try:
        cohort = arguments.cohort.resolve(strict=True)
        executable = arguments.executable.resolve(strict=True)
        cases = (
            _private_cases(cohort)
            if arguments.private
            else _synthetic_cases(arguments.manifest, cohort)
        )
        if arguments.private and (
            arguments.expected_total != 348
            or arguments.expected_f06_readable != 347
            or arguments.root_causes is None
        ):
            raise QualificationError("private qualification requires the closed 348/347 gate")
        causes = _root_causes(arguments.root_causes) if arguments.private else None
        if arguments.expected_total is not None and len(cases) != arguments.expected_total:
            raise QualificationError("cohort total does not match the closed expectation")
        aggregate = qualify(
            executable,
            cohort,
            cases,
            private=arguments.private,
            timeout=arguments.timeout,
            root_causes=causes,
        )
        if (
            arguments.expected_f06_readable is not None
            and aggregate["f06_readable"] != arguments.expected_f06_readable
        ):
            raise QualificationError("F06-readable total does not match the closed expectation")
        result = {
            "aggregate": aggregate,
            "artifact": "installed-wheel-cli",
            "corpus": "private" if arguments.private else "synthetic-generated",
            "schema": "epub-opening-qualification-v1",
        }
        serialized = json.dumps(result, separators=(",", ":"), sort_keys=True)
        if any(case[0] in serialized for case in cases) or ".epub" in serialized.casefold():
            raise QualificationError("aggregate output contains a private case identity")
        print(serialized)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, QualificationError):
        parser.error("EPUB opening qualification failed safely")


if __name__ == "__main__":
    raise SystemExit(main())
