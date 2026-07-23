from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import mechanical_librarian.bootstrap.epub_child as child
from mechanical_librarian.adapters.evidence_extractors.epub_package import (
    EpubPackageExtractor,
    _Rejected,
)
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageCapability,
    EpubPackageLimits,
    EpubPackageReport,
    EpubPackageSourceError,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.epub_inspection import EpubInspectionOutcome
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits


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
        written: list[EpubPackageReport] = []
        with (
            patch.object(child, "_install_parent_guard"),
            patch.object(child, "_apply_limits"),
            patch.object(child, "_read_context", return_value=context),
            patch.object(child, "_write_report", side_effect=written.append),
            patch.object(child.signal, "signal"),
            patch.object(child.os, "close"),
            patch.object(EpubPackageExtractor, "extract") as extract,
        ):
            self.assertEqual(child.main(["10", "11", "123"]), 0)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0].outcome, EpubInspectionOutcome.INTERNAL_FAILURE)
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


class EpubChildArchiveConstructionTests(unittest.TestCase):
    def test_constructor_timeout_preserves_typed_outcome(self) -> None:
        self.assertEqual(
            self._constructed_outcome(_Rejected(EpubInspectionOutcome.TIMEOUT)),
            EpubInspectionOutcome.TIMEOUT,
        )

    def test_constructor_source_failure_remains_unavailable(self) -> None:
        self.assertEqual(
            self._constructed_outcome(EpubPackageSourceError("read failed")),
            EpubInspectionOutcome.UNAVAILABLE_SOURCE,
        )

    @staticmethod
    def _constructed_outcome(error: Exception) -> EpubInspectionOutcome:
        extractor = Mock()
        extractor.preflight.return_value = None
        with (
            patch.object(child, "EpubPackageExtractor", return_value=extractor),
            patch.object(child, "_SourceIO", return_value=object()),
            patch.object(child.zipfile, "ZipFile", side_effect=error),
        ):
            return child._inspect_opening(object(), object(), EpubOpeningLimits()).outcome
