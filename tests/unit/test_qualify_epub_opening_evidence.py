from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from scripts.qualify_epub_opening_evidence import _entrypoint


class QualifyEpubOpeningEvidenceTests(unittest.TestCase):
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
