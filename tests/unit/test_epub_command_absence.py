"""The EPUB inspector is internal-only; legacy diagnostics are not commands."""

from __future__ import annotations

import unittest

from mechanical_librarian.interfaces.cli import _parser


class EpubCommandAbsenceTests(unittest.TestCase):
    def test_only_live_report_remains_public(self) -> None:
        help_text = _parser().format_help()
        self.assertIn("live-title-proposal-report", help_text)
        for command in ("epub-package-report", "epub-opening-report", "epub-opening-evidence"):
            self.assertNotIn(command, help_text)
