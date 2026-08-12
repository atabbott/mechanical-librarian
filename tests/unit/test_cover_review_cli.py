from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from mechanical_librarian.interfaces.cli import main


class CoverReviewCliTests(unittest.TestCase):
    def test_show_and_select_delegate_closed_values(self) -> None:
        runner = Mock(return_value={"schema_version": "cover-consideration-v1"})
        output = io.StringIO()
        self.assertEqual(
            main(
                [
                    "cover-review", "show", "--config", "/config.json",
                    "--database", "/database.sqlite3", "--artifacts", "/artifacts",
                    "--case", "review_case_" + "a" * 64,
                ],
                cover_review_runner=runner,
                stdout=output,
            ),
            0,
        )
        self.assertEqual(json.loads(output.getvalue())["schema_version"], "cover-consideration-v1")
        runner.assert_called_once_with(
            Path("/config.json"), Path("/database.sqlite3"), Path("/artifacts"),
            "review_case_" + "a" * 64, "show", None, None,
        )

        runner.reset_mock()
        self.assertEqual(
            main(
                [
                    "cover-review", "history", "--config", "/config.json",
                    "--database", "/database.sqlite3", "--artifacts", "/artifacts",
                    "--case", "review_case_" + "a" * 64,
                ],
                cover_review_runner=runner,
                stdout=io.StringIO(),
            ),
            0,
        )
        runner.assert_called_once_with(
            Path("/config.json"), Path("/database.sqlite3"), Path("/artifacts"),
            "review_case_" + "a" * 64, "history", None, None,
        )

        runner.reset_mock()
        self.assertEqual(
            main(
                [
                    "cover-review", "select", "--config", "/config.json",
                    "--database", "/database.sqlite3", "--artifacts", "/artifacts",
                    "--case", "review_case_" + "a" * 64,
                    "--artifact", "cover_" + "b" * 64,
                    "--idempotency-key", "owner-command-0001",
                ],
                cover_review_runner=runner,
                stdout=io.StringIO(),
            ),
            0,
        )
        runner.assert_called_once_with(
            Path("/config.json"), Path("/database.sqlite3"), Path("/artifacts"),
            "review_case_" + "a" * 64, "select", "cover_" + "b" * 64,
            "owner-command-0001",
        )

    def test_invalid_choice_shape_fails_before_runner(self) -> None:
        runner = Mock()
        errors = io.StringIO()
        self.assertEqual(
            main(
                [
                    "cover-review", "select", "--config", "/config.json",
                    "--database", "/database.sqlite3", "--artifacts", "/artifacts",
                    "--case", "review_case_" + "a" * 64,
                    "--idempotency-key", "owner-command-0001",
                ],
                cover_review_runner=runner,
                stderr=errors,
            ),
            1,
        )
        runner.assert_not_called()
        self.assertEqual(errors.getvalue(), "Review server failed safely.\n")


if __name__ == "__main__":
    unittest.main()
