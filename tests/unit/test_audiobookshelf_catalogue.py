from __future__ import annotations

import hashlib
import json
import unittest

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import AudiobookshelfCatalogueReader, _get
from mechanical_librarian.application.catalogue import CatalogueError
from mechanical_librarian.application.configuration import LibraryConfiguration
from mechanical_librarian.domain.metadata_report import canonical_json


def _library(name: str = "books", identifier: str = "library-1") -> LibraryConfiguration:
    return LibraryConfiguration(name, "primary", identifier, "book", None, ())


def _item(identifier: str = "item-1") -> dict[str, object]:
    return {"id": identifier, "libraryId": "library-1", "updatedAt": 1, "media": {"metadata": {"title": "Title", "authors": [{"name": "Ada"}]}, "ebookFile": {"ebookFormat": "epub", "ino": "inode", "metadata": {"relPath": "books/title.epub", "size": 7}}}}


class AudiobookshelfCatalogueTests(unittest.TestCase):
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
            client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "application/json"}, content=body)), base_url="https://abs.invalid")
            with client, self.assertRaises(CatalogueError):
                _get(client, "/api/libraries", 65_536, 1)

    def test_missing_items_and_one_element_plural_remain_unusable(self) -> None:
        detail = _item(); detail["isMissing"] = True
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


if __name__ == "__main__":
    unittest.main()
