import io
import unittest
from pathlib import Path

from mechanical_librarian.interfaces.cli import main


class ReviewCaseCliTests(unittest.TestCase):
    def test_admit_passes_only_exact_arguments(self) -> None:
        case_id = "review_case_" + "a" * 64
        proposal_id = "proposal_set_" + "b" * 64
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            0,
            main(
                [
                    "review-case-admit",
                    "--config",
                    "/c",
                    "--database",
                    "/d",
                    "--artifact-store",
                    "/a",
                    "--proposal-set-id",
                    proposal_id,
                ],
                review_admit_runner=lambda *args: self.assertEqual(
                    (Path("/c"), Path("/d"), Path("/a"), proposal_id),
                    args,
                )
                or '{"review_case_id":"'
                + case_id
                + '","schema_version":"review-admission-v1","status":"admitted"}',
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", errors.getvalue())

    def test_malformed_typed_outputs_fail_closed(self) -> None:
        case_id = "review_case_" + "a" * 64
        proposal_id = "proposal_set_" + "b" * 64
        scenarios = (
            (
                [
                    "review-case-admit",
                    "--config",
                    "/c",
                    "--database",
                    "/d",
                    "--artifact-store",
                    "/a",
                    "--proposal-set-id",
                    proposal_id,
                ],
                {"review_admit_runner": lambda *_: "{}"},
            ),
            (
                [
                    "review-case-admit",
                    "--config",
                    "/c",
                    "--database",
                    "/d",
                    "--artifact-store",
                    "/a",
                    "--proposal-set-id",
                    proposal_id,
                ],
                {
                    "review_admit_runner": lambda *_: (
                        '{"review_case_id":null,'
                        '"schema_version":"review-admission-v1",'
                        '"status":"unknown"}'
                    )
                },
            ),
            (
                [
                    "review-case-get",
                    "--database",
                    "/d",
                    "--review-case-id",
                    case_id,
                ],
                {
                    "review_get_runner": lambda *args: self.assertEqual(
                        (Path("/d"), case_id),
                        args,
                    )
                    or '{"case":{},"current":true,'
                    '"schema_version":"review-case-get-v1"}'
                },
            ),
        )
        for argv, runners in scenarios:
            with self.subTest(argv=argv, runners=tuple(runners)):
                output, errors = io.StringIO(), io.StringIO()
                self.assertEqual(
                    1,
                    main(
                        argv,
                        stdout=output,
                        stderr=errors,
                        **runners,
                    ),
                )
                self.assertEqual("", output.getvalue())
                self.assertEqual(
                    '{"code":"persistence",'
                    '"schema_version":"review-admission-failure-v1"}\n',
                    errors.getvalue(),
                )

    def test_invalid_syntax_fails_before_runner(self) -> None:
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(2, main(["review-case-get", "--database", "/d", "--review-case-id", "bad"], review_get_runner=lambda *_: self.fail("called"), stdout=output, stderr=errors))
        self.assertEqual("", output.getvalue())
        self.assertEqual(
            '{"code":"invalid","schema_version":"review-admission-failure-v1"}\n',
            errors.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
