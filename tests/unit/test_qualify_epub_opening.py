from __future__ import annotations

import json
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.qualify_epub_opening import (
    QualificationError,
    _OPENING_LIMITS,
    _PACKAGE_LIMITS,
    _configuration,
    _invoke,
    _root_causes,
    qualify,
)


class EpubOpeningQualificationTests(unittest.TestCase):
    def test_invoke_rejects_output_on_the_unused_stream(self) -> None:
        completed = SimpleNamespace(
            returncode=5,
            stdout=b"/private/library/secret.epub\n",
            stderr=b'{"outcome":"malformed"}\n',
        )
        with patch("scripts.qualify_epub_opening.subprocess.run", return_value=completed):
            with self.assertRaises(QualificationError):
                _invoke(
                    Path("/installed/mechanical-librarian"),
                    "epub-opening-report",
                    Path("/configuration.json"),
                    "case.epub",
                    65,
                )

    def test_private_profile_reproduces_the_closed_f06_archive_ceiling(self) -> None:
        configuration = _configuration(Path("/private/cohort"))

        self.assertEqual(
            configuration["evidence"],
            {"maximum_document_bytes": 268_435_456},
        )
        self.assertEqual(_PACKAGE_LIMITS["source_archive_bytes"], 268_435_456)

    def test_fragment_target_warning_is_safe_to_aggregate(self) -> None:
        report = self._report(warnings=["invalid_fragment_target"])
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "scripts.qualify_epub_opening._invoke",
                return_value=(0, report),
            ),
        ):
            aggregate = qualify(
                Path("/installed/mechanical-librarian"),
                Path(directory),
                [("case.epub", 0, "no_applicable_evidence")],
                private=False,
                timeout=65,
            )

        self.assertEqual(aggregate["warnings"], {"invalid_fragment_target": 1})

    def test_synthetic_discriminating_expectation_rejects_extra_claims(self) -> None:
        resource = {"disposition": "selected", "parse_mode": "strict"}
        claim = {
            "field": "title",
            "raw_value": "Expected Synthetic Title",
            "citation": {
                "resource_identity": f"{1:064x}",
                "parse_mode": "strict",
                "selection_path": ["linear_spine_fallback"],
            },
        }
        report = self._report(
            outcome="success", resources=[resource], claims=[claim]
        )
        expected = {
            "exit": 0,
            "outcome": "success",
            "claims": [
                {"field": "title", "raw_value": "Expected Synthetic Title"}
            ],
            "resources": [
                {
                    "disposition": "selected",
                    "selection_path": ["linear_spine_fallback"],
                    "warnings": [],
                }
            ],
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "scripts.qualify_epub_opening._invoke", return_value=(0, report)
            ):
                aggregate = qualify(
                    Path("/installed/mechanical-librarian"),
                    Path(directory),
                    [("case.epub", 0, "success", expected)],
                    private=False,
                    timeout=65,
                )
            self.assertEqual(aggregate["claims_by_field"], {"title": 1})

            forged = json.loads(json.dumps(report))
            forged["claims"].append(dict(forged["claims"][0]))
            with patch(
                "scripts.qualify_epub_opening._invoke", return_value=(0, forged)
            ):
                with self.assertRaises(QualificationError):
                    qualify(
                        Path("/installed/mechanical-librarian"),
                        Path(directory),
                        [("case.epub", 0, "success", expected)],
                        private=False,
                        timeout=65,
                    )

            wrong_usage = {**expected, "usage": {"navigation_links": 1}}
            with patch(
                "scripts.qualify_epub_opening._invoke", return_value=(0, report)
            ):
                with self.assertRaises(QualificationError):
                    qualify(
                        Path("/installed/mechanical-librarian"),
                        Path(directory),
                        [("case.epub", 0, "success", wrong_usage)],
                        private=False,
                        timeout=65,
                    )

    def _report(
        self,
        *,
        outcome: str = "no_applicable_evidence",
        warnings: list[str] | None = None,
        resources: list[dict[str, object]] | None = None,
        claims: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        normalized_resources = []
        for index, resource in enumerate([] if resources is None else resources):
            normalized = {
                "resource_identity": f"{index + 1:064x}",
                "selection_path": ["linear_spine_fallback"],
                "warnings": [],
                **resource,
            }
            normalized.setdefault("opened", normalized.get("parse_mode") is not None)
            normalized.setdefault(
                "resource_digest",
                (
                    f"{index + 101:064x}"
                    if normalized.get("disposition") in {"selected", "no_claims"}
                    else None
                ),
            )
            normalized_resources.append(normalized)
        normalized_claims = json.loads(json.dumps([] if claims is None else claims))
        resources_by_identity = {
            resource["resource_identity"]: resource
            for resource in normalized_resources
        }
        for claim in normalized_claims:
            citation = claim.get("citation", {})
            resource = resources_by_identity.get(citation.get("resource_identity"))
            if resource is not None:
                citation.setdefault("resource_digest", resource["resource_digest"])
        strict = sum(item.get("parse_mode") == "strict" for item in normalized_resources)
        recovered = sum(item.get("parse_mode") == "recovered" for item in normalized_resources)
        succeeded = outcome in {"success", "no_applicable_evidence"}
        source_digest = "b" * 64
        lineage = (
            hashlib.sha256(
                ("source-v1\0" + source_digest + "\0local-file-v1").encode("ascii")
            ).hexdigest()
            if succeeded
            else None
        )
        selection_payload = json.dumps(
            [
                {
                    "resource_identity": resource["resource_identity"],
                    "resource_digest": resource["resource_digest"],
                    "selection_path": resource["selection_path"],
                }
                for resource in normalized_resources
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return {
            "outcome": outcome,
            "schema": "epub-opening-report-v1",
            "claims": normalized_claims,
            "warnings": [] if warnings is None else warnings,
            "resources": normalized_resources,
            "same_file_lineage": lineage,
            "selection_digest": (
                hashlib.sha256(
                    ("epub-opening-selection-v1\0" + lineage + "\0" + selection_payload).encode("utf-8")
                ).hexdigest()
                if lineage is not None
                else None
            ),
            "source_fingerprint": (
                {"algorithm": "sha256", "digest": source_digest, "schema": "source-v1"}
                if succeeded
                else None
            ),
            "limits": {
                "package": _PACKAGE_LIMITS,
                "profile": "epub-opening-limits-v1",
                **_OPENING_LIMITS,
            },
            "usage": {
                "selected_documents": len(normalized_resources),
                "opened_resources": sum(bool(item["opened"]) for item in normalized_resources),
                "expanded_bytes": 0,
                "text_characters": 0,
                "nodes": 0,
                "navigation_links": 0,
                "strict_documents": strict,
                "recovered_documents": recovered,
            },
        }

    def test_private_internal_failure_cannot_pass_as_an_aggregate(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[
                    (0, {"outcome": "success"}),
                    (70, {"outcome": "internal_failure"}),
                ],
            ),
        ):
            with self.assertRaises(QualificationError):
                qualify(
                    Path("/installed/mechanical-librarian"),
                    Path(directory),
                    [("case.epub", -1, "")],
                    private=True,
                    timeout=65,
                    root_causes={},
                )

    def test_private_aggregate_vocabulary_is_closed(self) -> None:
        poisoned = {
            "outcome": "success",
            "claims": [{"field": "PRIVATE-TITLE"}],
            "warnings": ["PRIVATE-PATH"],
            "resources": [{"disposition": "PRIVATE-RESOURCE", "parse_mode": "strict"}],
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(0, {"outcome": "success"}), (0, poisoned)],
            ),
        ):
            with self.assertRaises(QualificationError):
                qualify(
                    Path("/installed/mechanical-librarian"),
                    Path(directory),
                    [("case.epub", -1, "")],
                    private=True,
                    timeout=65,
                    root_causes={},
                )

    def test_private_exit_and_resource_rejections_require_exact_accounting(self) -> None:
        rejected = self._report(
            warnings=["selected_resource_limit", "contradictory_series"],
            resources=[{"disposition": "resource_limit", "parse_mode": None}],
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(9, {"outcome": "success"})],
            ):
                with self.assertRaises(QualificationError):
                    qualify(
                        Path("/installed/mechanical-librarian"), Path(directory),
                        [("case.epub", -1, "")], private=True, timeout=65,
                        root_causes={},
                    )
            with patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(0, {"outcome": "success"}), (0, rejected)],
            ):
                with self.assertRaises(QualificationError):
                    qualify(
                        Path("/installed/mechanical-librarian"), Path(directory),
                        [("case.epub", -1, "")], private=True, timeout=65,
                        root_causes={},
                    )
            with patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(0, {"outcome": "success"}), (0, rejected)],
            ):
                aggregate = qualify(
                    Path("/installed/mechanical-librarian"), Path(directory),
                    [("case.epub", -1, "")], private=True, timeout=65,
                    root_causes={"case.epub": frozenset({"resource_limit"})},
                )
            self.assertEqual(aggregate["resource_dispositions"], {"resource_limit": 1})
            self.assertIn("usage_maxima", aggregate)

    def test_private_usage_and_rejection_decision_table_fail_closed(self) -> None:
        missing = self._report(
            warnings=["missing_selected_resource"],
            resources=[{"disposition": "missing", "parse_mode": None}],
        )
        malformed = self._report(
            warnings=["malformed_selected_resource"],
            resources=[{"disposition": "malformed", "parse_mode": None}],
        )
        unsafe = self._report(
            warnings=["unsafe_construct"],
            resources=[
                {
                    "disposition": "malformed",
                    "parse_mode": None,
                    "warnings": ["unsafe_construct"],
                }
            ],
        )
        excessive = self._report()
        excessive["usage"]["expanded_bytes"] = _OPENING_LIMITS["expanded_bytes_per_selection"] + 1
        poisoned_package = self._report()
        poisoned_package["limits"]["package"] = {}
        with tempfile.TemporaryDirectory() as directory:
            for report in (missing, malformed, excessive, poisoned_package):
                with self.subTest(report=report):
                    with patch(
                        "scripts.qualify_epub_opening._invoke",
                        side_effect=[(0, {"outcome": "success"}), (0, report)],
                    ):
                        with self.assertRaises(QualificationError):
                            qualify(
                                Path("/installed/mechanical-librarian"),
                                Path(directory),
                                [("case.epub", -1, "")],
                                private=True,
                                timeout=65,
                                root_causes={},
                            )
            with patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(0, {"outcome": "success"}), (0, unsafe)],
            ):
                aggregate = qualify(
                    Path("/installed/mechanical-librarian"),
                    Path(directory),
                    [("case.epub", -1, "")],
                    private=True,
                    timeout=65,
                    root_causes={"case.epub": frozenset({"unsafe_construct"})},
                )
            self.assertEqual(aggregate["resource_dispositions"], {"malformed": 1})

    def test_resource_state_and_claim_provenance_are_exact(self) -> None:
        selected = {
            "disposition": "selected",
            "parse_mode": "strict",
        }
        valid_claim = {
            "field": "title",
            "citation": {
                "resource_identity": f"{1:064x}",
                "parse_mode": "strict",
                "selection_path": ["linear_spine_fallback"],
            },
        }
        reports = []
        orphan_open = self._report()
        orphan_open["usage"]["opened_resources"] = 6
        reports.append(orphan_open)
        reports.append(
            self._report(
                resources=[
                    {
                        "disposition": "unsupported_media",
                        "parse_mode": "strict",
                        "opened": True,
                    }
                ]
            )
        )
        forged_lineage = self._report(
            resources=[{"disposition": "no_claims", "parse_mode": "strict"}]
        )
        forged_lineage["same_file_lineage"] = "a" * 64
        payload = json.dumps(
            [
                {
                    "resource_identity": resource["resource_identity"],
                    "selection_path": resource["selection_path"],
                }
                for resource in forged_lineage["resources"]
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        forged_lineage["selection_digest"] = hashlib.sha256(
            ("epub-opening-selection-v1\0" + "a" * 64 + "\0" + payload).encode(
                "utf-8"
            )
        ).hexdigest()
        reports.append(forged_lineage)
        reports.append(
            self._report(
                resources=[
                    {
                        "disposition": "no_claims",
                        "parse_mode": "strict",
                        "resource_identity": "not-a-resource-digest",
                    }
                ]
            )
        )
        relabelled = self._report(resources=[selected])
        relabelled["resources"][0]["selection_path"] = ["epub3_navigation"]
        relabelled["usage"]["selected_documents"] = 0
        reports.append(relabelled)
        forged_citation = self._report(
            outcome="success", resources=[selected], claims=[valid_claim]
        )
        forged_citation["claims"][0]["citation"]["selection_path"] = [
            "epub2_guide",
            "title_page",
        ]
        reports.append(forged_citation)
        reports.append(
            self._report(
                resources=[
                    {
                        "disposition": "no_claims",
                        "parse_mode": "strict",
                        "resource_identity": "1" * 64,
                    },
                    {
                        "disposition": "no_claims",
                        "parse_mode": "strict",
                        "resource_identity": "1" * 64,
                    },
                ]
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            for report in reports:
                with self.subTest(report=report):
                    with patch(
                        "scripts.qualify_epub_opening._invoke",
                        side_effect=[(0, {"outcome": "success"}), (0, report)],
                    ):
                        with self.assertRaises(QualificationError):
                            qualify(
                                Path("/installed/mechanical-librarian"),
                                Path(directory),
                                [("case.epub", -1, "")],
                                private=True,
                                timeout=65,
                                root_causes={},
                            )

    def test_navigation_only_is_valid_and_rejections_cannot_publish_partial_work(self) -> None:
        navigation = self._report(
            resources=[
                {
                    "disposition": "no_claims",
                    "parse_mode": "strict",
                    "selection_path": ["epub3_navigation"],
                }
            ]
        )
        navigation["usage"]["selected_documents"] = 0
        shared_identity = self._report(
            resources=[
                {
                    "disposition": "no_claims",
                    "parse_mode": "strict",
                    "selection_path": ["epub3_navigation"],
                    "resource_identity": "1" * 64,
                },
                {
                    "disposition": "no_claims",
                    "parse_mode": "strict",
                    "resource_identity": "1" * 64,
                },
            ]
        )
        shared_identity["usage"]["selected_documents"] = 1
        partial = self._report(
            outcome="resource_limit",
            resources=[{"disposition": "selected", "parse_mode": "strict"}],
            claims=[
                {
                    "field": "title",
                    "citation": {
                        "resource_identity": f"{1:064x}",
                        "parse_mode": "strict",
                        "selection_path": ["linear_spine_fallback"],
                    },
                }
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(0, {"outcome": "success"}), (0, navigation)],
            ):
                aggregate = qualify(
                    Path("/installed/mechanical-librarian"), Path(directory),
                    [("case.epub", -1, "")], private=True, timeout=65,
                    root_causes={},
                )
            self.assertEqual(aggregate["opening_outcomes"], {"no_applicable_evidence": 1})
            with patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(0, {"outcome": "success"}), (0, shared_identity)],
            ):
                aggregate = qualify(
                    Path("/installed/mechanical-librarian"), Path(directory),
                    [("case.epub", -1, "")], private=True, timeout=65,
                    root_causes={},
                )
            self.assertEqual(aggregate["opening_outcomes"], {"no_applicable_evidence": 1})
            with patch(
                "scripts.qualify_epub_opening._invoke",
                side_effect=[(0, {"outcome": "success"}), (5, partial)],
            ):
                with self.assertRaises(QualificationError):
                    qualify(
                        Path("/installed/mechanical-librarian"), Path(directory),
                        [("case.epub", -1, "")], private=True, timeout=65,
                        root_causes={"case.epub": frozenset({"resource_limit"})},
                    )

    def test_root_cause_record_is_closed_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "root-causes.json"
            record.write_text(
                json.dumps(
                    {
                        "schema": "epub-opening-private-root-causes-v1",
                        "cases": {"case.epub": ["resource_limit"]},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                _root_causes(record), {"case.epub": frozenset({"resource_limit"})}
            )
            record.write_text(
                json.dumps(
                    {
                        "schema": "epub-opening-private-root-causes-v1",
                        "cases": {"case.epub": ["PRIVATE-TITLE"]},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(QualificationError):
                _root_causes(record)


if __name__ == "__main__":
    unittest.main()
