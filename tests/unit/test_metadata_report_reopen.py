from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
)
from mechanical_librarian.interfaces.cli import main
from mechanical_librarian.bootstrap.composition import reopen_metadata_report
from mechanical_librarian.application.metadata_report import MetadataReportError


class MetadataReportReopenTests(unittest.TestCase):
    def test_exact_report_id_reopens_without_configuration_arguments(self) -> None:
        report = MetadataReport.create(
            target=TargetFacts("primary", "books", "item-1", "Current title"),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
        )
        output = io.StringIO()
        result = main(
            ["metadata-report-get", "--database", "/private/reports.sqlite", "--report-id", report.report_id],
            reopen_runner=lambda database, report_id: report.to_json(),
            stdout=output,
            stderr=io.StringIO(),
        )
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), report.to_json() + "\n")

    def test_invalid_report_id_is_rejected_before_opening_database(self) -> None:
        result = main(
            ["metadata-report-get", "--database", "/private/reports.sqlite", "--report-id", "bad"],
            reopen_runner=lambda database, report_id: (_ for _ in ()).throw(AssertionError()),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        self.assertEqual(result, 2)

    def test_missing_database_does_not_create_a_database_when_reopening(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "missing.sqlite"
            with self.assertRaises(MetadataReportError):
                reopen_metadata_report(database, "report_" + "a" * 64)
            self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
