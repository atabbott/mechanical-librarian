from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

import mechanical_librarian.bootstrap.epub_child as child
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
    def test_unknown_capability_is_rejected_without_package_dispatch(self) -> None:
        context = {
            "capability": "unknown",
            "child_seconds": 4.5,
            "expected_source": {
                "algorithm": "sha256",
                "digest": "a" * 64,
                "schema": "source-v1",
            },
            "limits": EpubPackageLimits().to_data(),
            "logical_identity": "b" * 64,
        }
        written: list[LocalEvidenceReport] = []
        with (
            patch.object(child, "_install_parent_guard"),
            patch.object(child, "_apply_limits"),
            patch.object(child, "_read_context", return_value=context),
            patch.object(child, "_write_report", side_effect=written.append),
            patch.object(child.signal, "signal"),
            patch.object(child.os, "close"),
            patch.object(child, "extract_local_evidence") as extract,
        ):
            self.assertEqual(child.main(["10", "11", "123"]), 0)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0].outcome, LocalEvidenceOutcome.INTERNAL_FAILURE)
        extract.assert_not_called()

    def test_entrypoint_import_does_not_load_network_database_or_secret_implementations(
        self,
    ) -> None:
        source = Path(__file__).parents[2] / "src"
        program = """
import json
import sys
import mechanical_librarian.bootstrap.epub_child
forbidden = (
    "httpx",
    "pydantic",
    "sqlite3",
    "mechanical_librarian.adapters.library_services",
    "mechanical_librarian.adapters.persistence",
    "mechanical_librarian.infrastructure.security.target_secrets",
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(forbidden))))
"""
        completed = subprocess.run(
            [sys.executable, "-B", "-c", program],
            cwd="/",
            env={"PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(json.loads(completed.stdout), [])

    def test_linux_guard_rejects_a_child_already_reparented_before_installation(
        self,
    ) -> None:
        with (
            patch.object(child.sys, "platform", "linux"),
            patch.object(child.os, "getppid", return_value=1),
            patch.object(child.os, "_exit", side_effect=SystemExit(70)) as exit_process,
        ):
            with self.assertRaises(SystemExit):
                child._install_parent_guard(10, threading.Event(), 999)
        exit_process.assert_called_once_with(70)

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
            "child_seconds": 4.5,
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
            exit_code = child.main(["10", "11", "123"])

        self.assertEqual(exit_code, 0)
        install_parent_guard.assert_called_once_with(11, ANY, 123)
        apply_limits.assert_called_once_with()
        write_report.assert_called_once_with(report)
        extractor, request = extract.call_args.args
        self.assertIsInstance(extractor, EpubPackageExtractor)
        self.assertEqual(request.expected_source, expected)
        self.assertEqual(request.target.external_item.value, logical_identity)
        self.assertEqual(request.file.relative_path, f"{logical_identity}.epub")


if __name__ == "__main__":
    unittest.main()
