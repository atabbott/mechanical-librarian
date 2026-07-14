from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mechanical_librarian.bootstrap._epub_child as child
from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
)


class EpubChildCompositionTests(unittest.TestCase):
    def test_production_child_routes_the_logical_request_through_the_application_service(
        self,
    ) -> None:
        limits = EpubPackageLimits()
        digest = "a" * 64
        logical_identity = "b" * 64
        expected = Fingerprint("sha256", SchemaVersion("source-v1"), digest)
        report = LocalEvidenceReport(
            LocalEvidenceOutcome.SUCCESS,
            LocalEvidenceCapability.PACKAGE,
            limits,
            expected,
        )
        context = {
            "expected_source": {
                "algorithm": "sha256",
                "digest": digest,
                "schema": "source-v1",
            },
            "limits": limits.to_data(),
            "logical_identity": logical_identity,
        }

        with (
            patch.object(child, "_install_parent_guard") as install_parent_guard,
            patch.object(child, "_apply_limits") as apply_limits,
            patch.object(child, "_read_context", return_value=context),
            patch.object(child, "_write_report") as write_report,
            patch.object(child.signal, "signal"),
            patch.object(child.os, "fstat", return_value=SimpleNamespace(st_size=128)),
            patch.object(child.os, "close"),
            patch.object(child, "extract_local_evidence", return_value=report) as extract,
        ):
            exit_code = child.main(["10", "11"])

        self.assertEqual(exit_code, 0)
        install_parent_guard.assert_called_once()
        apply_limits.assert_called_once_with()
        write_report.assert_called_once_with(report)
        extractor, request = extract.call_args.args
        self.assertIsInstance(extractor, EpubPackageExtractor)
        self.assertEqual(request.expected_source, expected)
        self.assertEqual(request.target.external_item.value, logical_identity)
        self.assertEqual(request.file.relative_path, f"{logical_identity}.epub")


if __name__ == "__main__":
    unittest.main()
