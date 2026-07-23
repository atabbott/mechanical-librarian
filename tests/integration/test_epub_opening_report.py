from __future__ import annotations

import hashlib
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mechanical_librarian.adapters.evidence_extractors import process as process_module
from mechanical_librarian.adapters.evidence_extractors.process import run_epub_inspector_child
from mechanical_librarian.application.epub_opening_evidence import EpubOpeningExecution
from mechanical_librarian.bootstrap import composition as bootstrap
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import EpubInspectionOutcome
from tests.support.epub_packages import opening_claims_complete, opening_strict_xhtml


LIBRARY_ID = "00000000-0000-4000-8000-000000000001"


def _configuration(mount: Path) -> dict[str, object]:
    return {
        "schema_version": "config-v1",
        "instance_name": "opening-test",
        "connections": [
            {
                "adapter": "audiobookshelf",
                "endpoint": "https://library.example",
                "secret_reference": "docker-secret/abs",
                "enabled_libraries": [LIBRARY_ID],
                "capability_overrides": ["inventory-read"],
            }
        ],
        "media_mounts": [
            {
                "name": "books",
                "library_type": "ebook",
                "container_path": str(mount),
                "service_path": "/library/books",
                "read_only": True,
            }
        ],
        "evidence": {"epub_opening": {"wall_seconds": 60}},
        "providers": [],
        "reviewers": [],
        "notifications": [],
        "backups_enabled": True,
    }


class EpubOpeningProcessTests(unittest.TestCase):
    def test_isolated_child_succeeds_and_cancelled_sibling_does_not_poison_next_run(self) -> None:
        value = opening_strict_xhtml()
        expected = Fingerprint(
            "sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()
        )
        with tempfile.TemporaryFile() as source:
            source.write(value)
            source.flush()
            cancelled = run_epub_inspector_child(
                source.fileno(),
                expected,
                EpubOpeningLimits(),
                cancelled=lambda: True,
            )
            sibling = run_epub_inspector_child(
                source.fileno(), expected, EpubOpeningLimits()
            )
        self.assertEqual(cancelled.outcome, EpubInspectionOutcome.CANCELLATION)
        self.assertEqual(sibling.outcome, EpubInspectionOutcome.SUCCESS)
        self.assertTrue(sibling.claims)

    def test_timeout_and_output_overflow_discard_partial_state_and_leave_a_usable_sibling(self) -> None:
        value = opening_strict_xhtml()
        expected = Fingerprint(
            "sha256", SchemaVersion("source-v1"), hashlib.sha256(value).hexdigest()
        )
        real_popen = process_module.subprocess.Popen

        def replacement(program: str):
            def launch(_arguments: object, *args: object, **kwargs: object):
                return real_popen(
                    [sys.executable, "-c", program], *args, **kwargs
                )

            return launch

        with tempfile.TemporaryFile() as source:
            source.write(value)
            source.flush()
            with patch.object(
                process_module.subprocess,
                "Popen",
                side_effect=replacement("import time; time.sleep(10)"),
            ):
                timed_out = run_epub_inspector_child(
                    source.fileno(),
                    expected,
                    EpubOpeningLimits(wall_seconds=1),
                )
            with patch.object(
                process_module.subprocess,
                "Popen",
                side_effect=replacement(
                    "import os; os.write(1, b'x' * 1048577)"
                ),
            ):
                overflow = run_epub_inspector_child(
                    source.fileno(), expected, EpubOpeningLimits()
                )
            sibling = run_epub_inspector_child(
                source.fileno(), expected, EpubOpeningLimits()
            )
        self.assertEqual(timed_out.outcome, EpubInspectionOutcome.TIMEOUT)
        self.assertEqual(overflow.outcome, EpubInspectionOutcome.CHILD_FAILURE)
        self.assertFalse(timed_out.claims)
        self.assertFalse(overflow.claims)
        self.assertEqual(sibling.outcome, EpubInspectionOutcome.SUCCESS)

    def test_composition_rejects_concurrent_in_place_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mount = root / "media"
            mount.mkdir()
            source = mount / "book.epub"
            original = opening_strict_xhtml()
            source.write_bytes(original)
            config = root / "configuration.json"
            config.write_text(json.dumps(_configuration(mount)), encoding="utf-8")

            def mutate(
                _descriptor: int,
                expected: Fingerprint,
                limits: EpubOpeningLimits,
                **_keywords: object,
            ) -> EpubOpeningReport:
                source.write_bytes(b"X" + original[1:])
                return EpubOpeningReport(
                    EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE,
                    limits,
                    expected,
                )

            with patch.object(
                bootstrap, "run_epub_inspector_child", side_effect=mutate
            ):
                report = bootstrap._run_epub_opening_report(
                    config, "books", "book.epub"
                )
        self.assertEqual(report.outcome, EpubInspectionOutcome.UNSTABLE_SOURCE)
        self.assertFalse(report.claims)

    def test_internal_execution_provenance_covers_success_and_negative_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mount = root / "media"
            mount.mkdir()
            config = root / "configuration.json"
            config.write_text(json.dumps(_configuration(mount)), encoding="utf-8")
            cases = (
                ("success.epub", opening_strict_xhtml(), EpubInspectionOutcome.SUCCESS),
                ("negative.epub", b"not-an-epub", EpubInspectionOutcome.UNSUPPORTED_FORMAT),
            )
            for filename, payload, expected_outcome in cases:
                with self.subTest(outcome=expected_outcome):
                    (mount / filename).write_bytes(payload)
                    execution = bootstrap._run_epub_opening_execution(
                        config, "books", filename
                    )
                    self.assertIsInstance(execution, EpubOpeningExecution)
                    assert isinstance(execution, EpubOpeningExecution)
                    self.assertEqual(execution.report.outcome, expected_outcome)
                    self.assertEqual(
                        execution.source_fingerprint,
                        hashlib.sha256(payload).hexdigest(),
                    )
                    if expected_outcome is EpubInspectionOutcome.UNSUPPORTED_FORMAT:
                        self.assertIsNone(execution.report.source_fingerprint)

    def test_disabled_epub_is_a_typed_pre_open_no_applicable_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mount = root / "media"
            mount.mkdir()
            configuration = _configuration(mount)
            configuration["evidence"] = {
                "enabled_formats": ["m4b"],
                "epub_opening": {"wall_seconds": 60},
            }
            config = root / "configuration.json"
            config.write_text(json.dumps(configuration), encoding="utf-8")
            report = bootstrap._run_epub_opening_report(
                config, "books", "absent.epub"
            )
        self.assertEqual(
            report.outcome, EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
        )
        self.assertIsNone(report.source_fingerprint)


if __name__ == "__main__":
    unittest.main()
