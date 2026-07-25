import io
import unittest
from pathlib import Path

from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.cli import main


def _proposal():
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", "Title"),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    return report, evaluate_report(report)


class ProposalCliTests(unittest.TestCase):
    def test_create_and_get_emit_exact_canonical_document(self) -> None:
        report, proposal_set = _proposal()
        for command, runner in (
            (
                [
                    "proposal-set-create",
                    "--config",
                    "/config.json",
                    "--database",
                    "/reports.sqlite",
                    "--artifact-store",
                    "/artifacts",
                    "--report-id",
                    report.report_id,
                ],
                {
                    "proposal_create_runner": lambda *args: (
                        self.assertEqual(
                            (
                                Path("/config.json"),
                                Path("/reports.sqlite"),
                                Path("/artifacts"),
                                report.report_id,
                            ),
                            args,
                        )
                        or proposal_set.to_json()
                    )
                },
            ),
            (
                [
                    "proposal-set-get",
                    "--database",
                    "/reports.sqlite",
                    "--proposal-set-id",
                    proposal_set.proposal_set_id,
                ],
                {
                    "proposal_get_runner": lambda *args: (
                        self.assertEqual(
                            (
                                Path("/reports.sqlite"),
                                proposal_set.proposal_set_id,
                            ),
                            args,
                        )
                        or proposal_set.to_json()
                    )
                },
            ),
        ):
            with self.subTest(command=command[0]):
                output = io.StringIO()
                errors = io.StringIO()
                self.assertEqual(
                    0,
                    main(command, stdout=output, stderr=errors, **runner),
                )
                self.assertEqual(proposal_set.to_json() + "\n", output.getvalue())
                self.assertEqual("", errors.getvalue())

    def test_mismatched_or_invalid_output_fails_safely(self) -> None:
        report, proposal_set = _proposal()
        output = io.StringIO()
        errors = io.StringIO()
        result = main(
            [
                "proposal-set-create",
                "--config",
                "/config.json",
                "--database",
                "/reports.sqlite",
                "--artifact-store",
                "/artifacts",
                "--report-id",
                "report_" + "f" * 64,
            ],
            proposal_create_runner=lambda *args: proposal_set.to_json(),
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(1, result)
        self.assertEqual("", output.getvalue())
        self.assertEqual("Proposal set failed safely.\n", errors.getvalue())
        self.assertNotEqual(report.report_id, "report_" + "f" * 64)


if __name__ == "__main__":
    unittest.main()
