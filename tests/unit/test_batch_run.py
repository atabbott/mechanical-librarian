from __future__ import annotations

import unittest

from mechanical_librarian.application.batch import BatchSelection, CatalogueSnapshot
from mechanical_librarian.application.batch_run import ForegroundBatchRun
from mechanical_librarian.application.catalogue import CatalogueEntry, CatalogueStaleError
from mechanical_librarian.application.metadata_report import CredentialError, CurrentnessError, UnsupportedMediaError


def entry(item: str = "item") -> CatalogueEntry:
    return CatalogueEntry("primary", "books", "library", item, 1, "title", (), True, False, "singular-supported", "a" * 64, "book.epub", "epub", "books")


class ForegroundBatchRunTests(unittest.TestCase):
    def exercise(self, report, current=lambda *_: entry()):
        lines = []; selected = BatchSelection("primary", "books", "library", "item", "a" * 64, "singular-supported", True)
        result = ForegroundBatchRun(snapshot=CatalogueSnapshot(1, "b" * 64, (entry(),)), selections=(selected,), projection=lambda: "p", expected_projection="p", current_entry=current, report=report, emit=lines.append).run()
        return lines, result

    def test_source_stale_continues(self) -> None:
        lines, result = self.exercise(lambda _: (_ for _ in ()).throw(CurrentnessError("changed")))
        self.assertEqual((lines[0]["outcome"], result["skipped"], result["stopped"]), ("stale", 1, 0))

    def test_credential_and_catalogue_drift_stop(self) -> None:
        lines, result = self.exercise(lambda _: (_ for _ in ()).throw(CredentialError("unavailable")))
        self.assertEqual((lines[0]["outcome"], result["stopped"]), ("stopped", 1))
        lines, result = self.exercise(lambda _: "report", current=lambda *_: (_ for _ in ()).throw(CatalogueStaleError("refresh")))
        self.assertEqual((lines[0]["outcome"], result["stopped"]), ("stopped", 1))

    def test_item_local_failure_and_unclassified_stop(self) -> None:
        lines, result = self.exercise(lambda _: (_ for _ in ()).throw(UnsupportedMediaError("format")))
        self.assertEqual((lines[0]["outcome"], result["failed"], result["stopped"]), ("item_failed", 1, 0))
        lines, result = self.exercise(lambda _: (_ for _ in ()).throw(RuntimeError("broken")))
        self.assertEqual((lines[0]["outcome"], result["stopped"]), ("stopped", 1))
