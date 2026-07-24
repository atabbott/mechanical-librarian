from __future__ import annotations

import tempfile
import unittest
from multiprocessing import Event, Process
from pathlib import Path

from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.application.catalogue import CatalogueBusyError, CatalogueEntry, CatalogueStaleError


def _entry(item: str = "item-1") -> CatalogueEntry:
    return CatalogueEntry("primary", "books", "library-id", item, 1, "Title", ("Author",), True, False, "singular-supported", "a" * 64, "book.epub", "epub", "books")


def _hold_refresh_lock(path: str, ready: Event, release: Event) -> None:
    repository = open_catalogue(Path(path))
    try:
        repository._connection.execute("BEGIN IMMEDIATE")
        ready.set()
        release.wait(10)
        repository._connection.execute("ROLLBACK")
    finally:
        repository.close()


class CatalogueRepositoryTests(unittest.TestCase):
    def test_atomic_current_switch_and_stale_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path)
            self.assertEqual(repository.publish("a" * 64, (_entry(),)), 1)
            self.assertEqual(repository.inspect("a" * 64), (_entry(),))
            with self.assertRaises(CatalogueStaleError):
                repository.inspect("b" * 64)
            repository.close()

    def test_cross_process_second_refresh_is_nonblocking_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            first = open_catalogue(path)
            first.close()
            ready = Event(); release = Event()
            holder = Process(target=_hold_refresh_lock, args=(str(path), ready, release))
            holder.start()
            try:
                self.assertTrue(ready.wait(5))
                second = open_catalogue(path)
                with self.assertRaises(CatalogueBusyError):
                    second.publish("a" * 64, (_entry(),))
            finally:
                release.set(); holder.join(10)
                self.assertEqual(holder.exitcode, 0)
                second.close()


if __name__ == "__main__":
    unittest.main()
