from __future__ import annotations

import hashlib
import unittest

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    AudiobookshelfCatalogueReader,
    _get,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    _entry as catalogue_entry,
)
from mechanical_librarian.application.catalogue import (
    CatalogueError,
    TargetConnectionError,
)
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
)
from mechanical_librarian.domain.metadata_report import canonical_json


def _library(name: str = "books", identifier: str = "library-1") -> LibraryConfiguration:
    return LibraryConfiguration(name, "primary", identifier, "book", None, ())


def _item(identifier: str = "item-1") -> dict[str, object]:
    return {"id": identifier, "libraryId": "library-1", "updatedAt": 1, "media": {"metadata": {"title": "Title", "authors": [{"name": "Ada"}]}, "ebookFile": {"ebookFormat": "epub", "ino": "inode", "metadata": {"relPath": "books/title.epub", "size": 7}}}}


class AudiobookshelfCatalogueTests(unittest.TestCase):
    def test_probe_requires_every_configured_book_library(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/libraries")
            return httpx.Response(200, json={"libraries": [
                {"id": "library-1", "mediaType": "book"},
                {"id": "audio", "mediaType": "podcast"},
            ]})

        reader = AudiobookshelfCatalogueReader(
            client_factory=lambda _: httpx.Client(
                transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
            ), mounts={}, maximum_bytes=65_536, timeout_seconds=1,
        )
        with self.assertRaises(TargetConnectionError) as raised:
            reader.probe_connection("primary", (_library(), _library("history", "missing")))
        self.assertEqual(raised.exception.code, "unavailable")

    def test_aggregate_capacity_is_checked_before_any_detail_request(self) -> None:
        requests: list[str] = []
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path == "/api/libraries":
                return httpx.Response(200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]})
            if request.url.path.endswith("/items"):
                return httpx.Response(200, json={"total": 2, "results": [{"id": "item-1"}]})
            raise AssertionError("details must not be fetched")
        reader = AudiobookshelfCatalogueReader(client_factory=lambda _: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://abs.invalid"), mounts={}, maximum_bytes=65_536, timeout_seconds=1)
        with self.assertRaises(CatalogueError):
            reader.read_library("primary", _library(), 1)
        self.assertEqual(requests, ["/api/libraries", "/api/libraries/library-1/items"])

    def test_singular_unmounted_book_is_catalogued_but_not_eligible_and_matches_item_identity(self) -> None:
        detail = _item()
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/libraries":
                return httpx.Response(200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]})
            if request.url.path.endswith("/items"):
                return httpx.Response(200, json={"total": 1, "results": [{"id": "item-1", "libraryId": "library-1", "updatedAt": 1}]})
            return httpx.Response(200, json=detail)
        reader = AudiobookshelfCatalogueReader(client_factory=lambda _: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://abs.invalid"), mounts={}, maximum_bytes=65_536, timeout_seconds=1)
        entry = reader.read_library("primary", _library(), 10)[0]
        expected = hashlib.sha256(canonical_json({"format": "epub", "inode": "inode", "relative": "books/title.epub", "size": 7}).encode("utf-8")).hexdigest()
        self.assertEqual((entry.classification, entry.eligible, entry.file_identity), ("singular-supported", False, expected))

    def test_strict_stream_json_rejects_duplicates_nonfinite_and_depth(self) -> None:
        for body in (b'{"a":1,"a":2}', b'{"a":NaN}', b"[" * 101 + b"]" * 101):
            client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "application/json"}, content=body)), base_url="https://abs.invalid")  # noqa: B023 - closure is created and consumed within this iteration
            with client, self.assertRaises(CatalogueError):
                _get(client, "/api/libraries", 65_536, 1)

    def test_missing_items_and_one_element_plural_remain_unusable(self) -> None:
        detail = _item()
        detail["isMissing"] = True
        detail["media"] = {"metadata": {"title": "Title", "authors": []}, "ebookFiles": [{"ebookFormat": "epub"}]}
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/libraries":
                return httpx.Response(200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]})
            if request.url.path.endswith("/items"):
                return httpx.Response(200, json={"total": 1, "results": [{"id": "item-1", "libraryId": "library-1", "updatedAt": 1}]})
            return httpx.Response(200, json=detail)
        reader = AudiobookshelfCatalogueReader(client_factory=lambda _: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://abs.invalid"), mounts={}, maximum_bytes=65_536, timeout_seconds=1)
        with self.assertRaises(CatalogueError):
            reader.read_library("primary", _library(), 10)

    def test_path_only_mapping_matches_item_identity_and_enforces_relative_bounds(self) -> None:
        mount = MediaMountConfiguration("books", "/library", "/media", True)
        summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
        def detail(path: str) -> dict[str, object]:
            return {**_item(), "media": {"metadata": {"title": "Title", "authors": [{"name": "Ada"}]}, "ebookFile": {"ebookFormat": "epub", "ino": "inode", "metadata": {"path": path, "size": 7}}}}
        entry = catalogue_entry("primary", _library(), mount, summary, detail("/media/books/title.epub"))
        expected = hashlib.sha256(canonical_json({"format": "epub", "inode": "inode", "relative": "books/title.epub", "size": 7}).encode("utf-8")).hexdigest()
        self.assertEqual((entry.eligible, entry.file_identity, entry.service_path), (True, expected, "books/title.epub"))
        for relative, classification in (("a" * 1024, "singular-supported"), ("a" * 1025, "needs-attention")):
            value = catalogue_entry("primary", _library(), mount, summary, detail("/media/" + relative))
            self.assertEqual((value.classification, value.eligible), (classification, classification == "singular-supported"))
        self.assertFalse(catalogue_entry("primary", _library(), mount, summary, detail("/outside/book.epub")).eligible)

    def test_empty_plural_is_none_and_plural_values_need_attention(self) -> None:
        summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
        for plural, expected in (([], (False, "none")), ([{}], (True, "needs-attention")), ([{}, {}], (True, "needs-attention"))):
            detail = {**_item(), "media": {"metadata": {"title": "Title", "authors": []}, "ebookFiles": plural}}
            entry = catalogue_entry("primary", _library(), None, summary, detail)
            self.assertEqual((entry.has_ebook, entry.classification), expected)

    def test_volatile_activity_fields_do_not_change_catalogue_identity(self) -> None:
        baseline = _item()
        baseline["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
        active = {
            **baseline,
            "updatedAt": 2,
            "progress": 0.75,
            "currentTime": 1234,
            "bookmarks": [{"title": "Later", "time": 1200}],
            "mediaProgress": {"isFinished": False, "progress": 0.75},
        }
        first = catalogue_entry(
            "primary", _library(), None,
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}, baseline,
        )
        second = catalogue_entry(
            "primary", _library(), None,
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 2}, active,
        )
        self.assertEqual(first.source_fingerprint, second.source_fingerprint)

    def test_item_revision_is_fallback_when_material_revision_is_unavailable(self) -> None:
        baseline = _item()
        changed = _item()
        changed["updatedAt"] = 2
        first = catalogue_entry(
            "primary", _library(), None,
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}, baseline,
        )
        second = catalogue_entry(
            "primary", _library(), None,
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 2}, changed,
        )
        self.assertNotEqual(first.source_fingerprint, second.source_fingerprint)

    def test_invalid_material_revision_retains_item_revision_fallback(self) -> None:
        summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
        for invalid_revision in (None, -1, "", [], {}):
            baseline = _item()
            baseline["media"]["ebookFile"]["metadata"]["mtimeMs"] = invalid_revision
            changed = _item()
            changed["updatedAt"] = 2
            changed["media"]["ebookFile"]["metadata"]["mtimeMs"] = invalid_revision
            first = catalogue_entry(
                "primary", _library(), None, summary, baseline
            )
            second = catalogue_entry(
                "primary",
                _library(),
                None,
                {**summary, "updatedAt": 2},
                changed,
            )
            self.assertNotEqual(first.source_fingerprint, second.source_fingerprint)

    def test_reviewed_field_presence_changes_source_fingerprint(self) -> None:
        summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
        absent = _item()
        absent["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
        explicit_null = _item()
        explicit_null["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
        explicit_null["media"]["metadata"]["subtitle"] = None
        first = catalogue_entry("primary", _library(), None, summary, absent)
        second = catalogue_entry("primary", _library(), None, summary, explicit_null)
        self.assertNotEqual(first.source_fingerprint, second.source_fingerprint)

    def test_cover_field_presence_and_raw_state_change_source_fingerprint(self) -> None:
        summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
        absent = object()
        fingerprints = []
        for cover in (absent, None, "", 7, "/cover/item-1"):
            detail = _item()
            detail["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
            if cover is not absent:
                detail["coverPath"] = cover
            fingerprints.append(
                catalogue_entry(
                    "primary", _library(), None, summary, detail
                ).source_fingerprint
            )
        self.assertEqual(len(set(fingerprints)), len(fingerprints))

    def test_item_revision_protects_cover_bytes_without_a_cover_revision(self) -> None:
        baseline = _item()
        baseline["coverPath"] = "/cover/item-1"
        baseline["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
        changed = _item()
        changed["coverPath"] = "/cover/item-1"
        changed["updatedAt"] = 2
        changed["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
        first = catalogue_entry(
            "primary", _library(), None,
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}, baseline,
        )
        second = catalogue_entry(
            "primary", _library(), None,
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 2}, changed,
        )
        self.assertNotEqual(first.source_fingerprint, second.source_fingerprint)

    def test_each_material_review_source_dimension_changes_fingerprint(self) -> None:
        summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
        baseline = _item()
        original = catalogue_entry("primary", _library(), None, summary, baseline)
        variants = []
        for mutation in (
            {"subtitle": "A subtitle"},
            {"isbn": "9780000000002"},
            {"coverPath": "/cover/item-1"},
        ):
            detail = _item()
            if "coverPath" in mutation:
                detail.update(mutation)
            else:
                detail["media"]["metadata"].update(mutation)
            variants.append(detail)
        media_revision = _item()
        media_revision["media"]["ebookFile"]["metadata"]["mtimeMs"] = 2
        variants.append(media_revision)
        for detail in variants:
            self.assertNotEqual(
                original.source_fingerprint,
                catalogue_entry("primary", _library(), None, summary, detail).source_fingerprint,
            )


if __name__ == "__main__":
    unittest.main()
