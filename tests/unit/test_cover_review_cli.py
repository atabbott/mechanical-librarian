from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.bootstrap.composition import run_review_activity
from mechanical_librarian.interfaces.cli import main


class CoverReviewCliTests(unittest.TestCase):
    def test_activity_composition_calls_the_shared_application_projection(self) -> None:
        repository = Mock()
        preparation = Mock()
        field_application = Mock()
        application = Mock()
        expected = {
            "events": [{"state": "verified"}],
            "schema_version": "continuous-field-activity-v1",
        }
        application.activity.return_value = expected
        configuration = Mock(review=object())

        with (
            patch(
                "mechanical_librarian.bootstrap.composition.load_configuration_file",
                return_value=configuration,
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.open_repository",
                return_value=repository,
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.create_review_pipeline",
                return_value=preparation,
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.compose_continuous_guarded_application",
                return_value=field_application,
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.compose_review_application",
                return_value=application,
            ) as compose,
        ):
            actual = run_review_activity(
                Path("/config.json"),
                Path("/database.sqlite3"),
                Path("/catalogue.sqlite3"),
                Path("/artifacts"),
            )

        self.assertEqual(expected, actual)
        self.assertIs(compose.call_args.kwargs["field_application"], field_application)
        application.activity.assert_called_once_with()
        repository.close.assert_called_once_with()

    def test_activity_exposes_guarded_and_recovery_states_from_shared_projection(self) -> None:
        states = ("planned", "verified", "stale", "recovery_required")
        runner = Mock(
            return_value={
                "events": [
                    {"plan_id": f"field_effect_{index}", "state": state}
                    for index, state in enumerate(states)
                ],
                "schema_version": "continuous-field-activity-v1",
            }
        )
        output = io.StringIO()

        self.assertEqual(
            main(
                [
                    "review-activity",
                    "--config", "/config.json",
                    "--database", "/database.sqlite3",
                    "--catalogue", "/catalogue.sqlite3",
                    "--artifacts", "/artifacts",
                ],
                review_activity_runner=runner,
                stdout=output,
            ),
            0,
        )

        runner.assert_called_once_with(
            Path("/config.json"),
            Path("/database.sqlite3"),
            Path("/catalogue.sqlite3"),
            Path("/artifacts"),
        )
        self.assertEqual(
            tuple(item["state"] for item in json.loads(output.getvalue())["events"]),
            states,
        )

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
