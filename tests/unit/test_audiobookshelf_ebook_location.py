from __future__ import annotations

import unittest

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    _entry as catalogue_entry,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_ebook_location import (
    normalize_ebook_location,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_item import _snapshot
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
)
from mechanical_librarian.application.metadata_report import MetadataReportError


def _library() -> LibraryConfiguration:
    return LibraryConfiguration("books", "primary", "library-1", "book", "books", ())


def _payload(*, relative: str | None = "book.epub", path: str | None = "/media/Author/Book/book.epub", item_relative: object = "Author/Book", item_path: str | None = "/media/Author/Book", is_file: bool = False, size: int = 7) -> dict[str, object]:
    metadata: dict[str, object] = {"size": size}
    if relative is not None:
        metadata["relPath"] = relative
    if path is not None:
        metadata["path"] = path
    return {
        "id": "item-1",
        "libraryId": "library-1",
        "updatedAt": 1,
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "relPath": item_relative,
        "path": item_path,
        "isFile": is_file,
        "media": {
            "metadata": {"title": "Title", "authors": [{"name": "Ada"}]},
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode",
                "metadata": metadata,
            },
        },
    }


class AudiobookshelfEbookLocationTests(unittest.TestCase):
    def _catalogue(self, payload: dict[str, object]):
        return catalogue_entry(
            "primary",
            _library(),
            MediaMountConfiguration("books", "/library", "/media", True),
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
            payload,
        )

    def _current(self, payload: dict[str, object]):
        return _snapshot(payload, "item-1", "library-1", "/media").files[0]

    def test_nested_flat_and_path_only_records_share_one_identity_at_both_boundaries(self) -> None:
        cases = (
            _payload(),
            _payload(relative="flat.epub", path="/media/flat.epub", item_relative="flat.epub", item_path="/media/flat.epub", is_file=True),
            _payload(relative=None, path="/media/Author/Book/book.epub"),
        )
        for payload in cases:
            with self.subTest(payload=payload["relPath"]):
                catalogue = self._catalogue(payload)
                current = self._current(payload)
                self.assertEqual(catalogue.service_path, current.relative_path)
                self.assertEqual(catalogue.file_identity, current.identity)

    def test_all_available_path_sources_must_agree(self) -> None:
        payload = _payload(path="/media/Other/book.epub")
        entry = self._catalogue(payload)
        self.assertEqual("needs-attention", entry.classification)
        with self.assertRaises(MetadataReportError):
            self._current(payload)

    def test_path_only_record_must_still_agree_with_item_coordinates(self) -> None:
        payload = _payload(relative=None, path="/media/Other/book.epub")
        self.assertEqual("needs-attention", self._catalogue(payload).classification)
        with self.assertRaises(MetadataReportError):
            self._current(payload)
        payload = _payload(item_path="/media/Other")
        entry = self._catalogue(payload)
        self.assertEqual("needs-attention", entry.classification)
        with self.assertRaises(MetadataReportError):
            self._current(payload)

    def test_traversal_missing_and_ambiguous_ebooks_fail_closed(self) -> None:
        traversal = _payload(item_relative="Author/../Book")
        missing = _payload(relative=None, path=None)
        ambiguous = _payload()
        media = ambiguous["media"]
        assert type(media) is dict
        media["ebookFiles"] = [media["ebookFile"]]
        for payload in (traversal, missing, ambiguous):
            with self.subTest(payload=payload):
                entry = self._catalogue(payload)
                self.assertNotEqual("singular-supported", entry.classification)
                with self.assertRaises(MetadataReportError):
                    self._current(payload)

    def test_unchanged_identity_is_current_and_changed_file_identity_is_not(self) -> None:
        first = _payload()
        unchanged = _payload()
        changed = _payload(size=8)
        catalogue = self._catalogue(first)
        self.assertEqual(catalogue.file_identity, self._current(unchanged).identity)
        self.assertNotEqual(catalogue.file_identity, self._current(changed).identity)

    def test_normalizer_rejects_path_without_a_service_root(self) -> None:
        with self.assertRaises(ValueError):
            normalize_ebook_location(
                _payload()["media"]["ebookFile"],
                service_path=None,
                item=_payload(),
            )

    def test_flat_item_coordinates_cannot_be_interpreted_as_a_directory(self) -> None:
        payload = _payload(
            relative="book.epub",
            path=None,
            item_relative="book.epub",
            item_path="/media/book.epub",
            is_file=True,
        )
        catalogue = self._catalogue(payload)
        current = self._current(payload)
        self.assertEqual("book.epub", catalogue.service_path)
        self.assertEqual(catalogue.file_identity, current.identity)
        payload["media"]["ebookFile"]["metadata"]["relPath"] = "nested/book.epub"
        self.assertEqual("needs-attention", self._catalogue(payload).classification)
        with self.assertRaises(MetadataReportError):
            self._current(payload)


if __name__ == "__main__":
    unittest.main()
