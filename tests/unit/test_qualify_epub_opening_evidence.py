from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from scripts.qualify_epub_opening_evidence import (
    QualificationError,
    _TemporarilyRemoved,
    _entrypoint,
    _evidence,
    _selector_roles,
)


class QualifyEpubOpeningEvidenceTests(unittest.TestCase):
    def test_selector_roles_require_zero_one_two_or_three_distinct_durable_values(self) -> None:
        selectors = [
            {
                "target": "target_" + str(index) * 32,
                "snapshot": "snapshot_" + str(index) * 32,
                "file_index": index,
                "relative_path": f"book-{index}.epub",
            }
            for index in range(3)
        ]
        keys = [
            (str(value["target"]), str(value["snapshot"]), int(value["file_index"]))
            for value in selectors
        ]
        for count in range(3):
            with (
                self.subTest(count=count),
                self.assertRaisesRegex(
                    QualificationError, "at least three distinct durable selectors"
                ),
            ):
                _selector_roles(selectors[:count], set(keys[:count]))
        self.assertEqual(
            _selector_roles(selectors + [dict(selectors[0])], set(keys)),
            tuple(selectors),
        )

    def test_failure_isolation_removes_and_restores_source_without_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "book.epub"
            source.write_bytes(b"synthetic")
            with _TemporarilyRemoved(source):
                self.assertFalse(source.exists())
                self.assertEqual(
                    (Path(directory) / ".book.epub.qualification-unavailable").read_bytes(),
                    b"synthetic",
                )
            self.assertEqual(source.read_bytes(), b"synthetic")

    def test_failure_isolation_restores_source_when_qualification_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "book.epub"
            source.write_bytes(b"synthetic")
            with self.assertRaisesRegex(RuntimeError, "injected"):
                with _TemporarilyRemoved(source):
                    raise RuntimeError("injected")
            self.assertEqual(source.read_bytes(), b"synthetic")

    def test_evidence_requires_the_complete_redacted_cli_envelope(self) -> None:
        private_path = "/private/cohort/identifying-title.epub"
        selector = {
            "target": "target_" + "a" * 32,
            "snapshot": "snapshot_" + "b" * 32,
            "file_index": 0,
        }
        success = subprocess.CompletedProcess(
            (),
            0,
            stdout=(
                "EPUB opening evidence passed.\n"
                "mode=executed evidence=evidence_"
                + "c" * 32
                + " current=true outcome=success\n"
                "claims=0 citations=0 selected=0 rejected=0 warnings=0 "
                "parser=strict\n"
            ),
            stderr=private_path,
        )
        failure = subprocess.CompletedProcess(
            (),
            1,
            stdout=private_path,
            stderr=(
                "EPUB opening evidence failed safely.\n"
                "failure=source_changed\n"
            ),
        )
        unknown_outcome = subprocess.CompletedProcess(
            (),
            0,
            stdout=(
                "EPUB opening evidence passed.\n"
                "mode=executed evidence=evidence_"
                + "c" * 32
                + " current=true outcome=private_title_123\n"
                "claims=0 citations=0 selected=0 rejected=0 warnings=0 "
                "parser=strict\n"
            ),
            stderr="",
        )
        unknown_failure = subprocess.CompletedProcess(
            (),
            1,
            stdout="",
            stderr=(
                "EPUB opening evidence failed safely.\n"
                "failure=private_title_123\n"
            ),
        )
        for completed in (
            success,
            failure,
            unknown_outcome,
            unknown_failure,
        ):
            with (
                self.subTest(returncode=completed.returncode),
                patch(
                    "scripts.qualify_epub_opening_evidence._run",
                    return_value=completed,
                ),
                self.assertRaises(QualificationError),
            ):
                _evidence(
                    Path("installed-cli"),
                    Path("configuration.json"),
                    Path("state.sqlite3"),
                    selector,
                )

    def test_entrypoint_redacts_unexpected_private_path_errors(self) -> None:
        private_path = "/private/cohort/identifying-title.epub"
        stderr = io.StringIO()
        with (
            patch(
                "scripts.qualify_epub_opening_evidence.main",
                side_effect=OSError(private_path),
            ),
            redirect_stderr(stderr),
        ):
            self.assertEqual(_entrypoint(), 1)
        self.assertEqual(
            stderr.getvalue(), "EPUB evidence qualification failed safely\n"
        )
        self.assertNotIn(private_path, stderr.getvalue())

    def test_entrypoint_preserves_operator_interrupt(self) -> None:
        with (
            patch(
                "scripts.qualify_epub_opening_evidence.main",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            _entrypoint()


if __name__ == "__main__":
    unittest.main()
