"""Retired diagnostics are not installed commands or current documentation."""

from __future__ import annotations

import unittest
from io import StringIO
from pathlib import Path

from mechanical_librarian.interfaces.cli import _parser, main as cli_main


class RetiredCommandAbsenceTests(unittest.TestCase):
    retired_commands = (
        "epub-package-report",
        "epub-opening-report",
        "epub-opening-evidence",
        "synthetic-title-proposal-report",
        "hardcover-title-proposal-report",
        "synthetic-title-review-case-report",
    )

    def test_retired_commands_are_not_public(self) -> None:
        help_text = _parser().format_help()
        self.assertIn("live-title-proposal-report", help_text)
        for command in self.retired_commands:
            self.assertNotIn(command, help_text)

    def test_retired_commands_are_unknown_to_the_installed_cli(self) -> None:
        for command in self.retired_commands:
            errors = StringIO()
            self.assertEqual(
                cli_main([command], state_check_runner=lambda _database: None, stderr=errors),
                2,
            )
            self.assertEqual(errors.getvalue(), "unknown command\n")

    def test_documentation_marks_retired_command_references_as_historical(self) -> None:
        markers = ("historical", "retired", "formerly", "former", "added")
        for path in (*Path("docs").rglob("*.md"), Path("CHANGELOG.md")):
            for paragraph in path.read_text(encoding="utf-8").lower().split("\n\n"):
                for command in self.retired_commands:
                    if f"`{command}`" in paragraph:
                        self.assertTrue(
                            any(marker in paragraph for marker in markers),
                            f"{path} presents retired command {command!r} as current",
                        )
