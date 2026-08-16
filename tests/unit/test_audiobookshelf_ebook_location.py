from __future__ import annotations

import pytest

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


def _catalogue(payload: dict[str, object]):
    return catalogue_entry(
        "primary",
        _library(),
        MediaMountConfiguration("books", "/library", "/media", True),
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        payload,
    )


def _current(payload: dict[str, object]):
    return _snapshot(payload, "item-1", "library-1", "/media").files[0]


@pytest.mark.parametrize(
    "payload",
    (_payload(path=None), _payload(relative=None), _payload()),
    ids=("relative_only", "path_only", "both_coordinates"),
)
def test_nested_coordinate_variants_share_one_canonical_path_and_identity(
    payload: dict[str, object],
) -> None:
    catalogue = _catalogue(payload)
    current = _current(payload)
    assert "Author/Book/book.epub" == catalogue.service_path
    assert "Author/Book/book.epub" == current.relative_path
    assert catalogue.service_path == current.relative_path
    assert catalogue.file_identity == current.identity


def test_nested_coordinate_variants_agree_on_one_file_identity() -> None:
    identities = {
        _catalogue(payload).file_identity
        for payload in (_payload(path=None), _payload(relative=None), _payload())
    }
    assert 1 == len(identities)


def test_flat_item_path_is_shared_at_both_boundaries() -> None:
    payload = _payload(
        relative="flat.epub",
        path="/media/flat.epub",
        item_relative="flat.epub",
        item_path="/media/flat.epub",
        is_file=True,
    )
    catalogue = _catalogue(payload)
    current = _current(payload)
    assert "flat.epub" == catalogue.service_path
    assert catalogue.file_identity == current.identity


def test_all_available_path_sources_must_agree() -> None:
    payload = _payload(path="/media/Other/book.epub")
    entry = _catalogue(payload)
    assert "needs-attention" == entry.classification
    with pytest.raises(MetadataReportError):
        _current(payload)


def test_path_only_record_must_still_agree_with_item_coordinates() -> None:
    payload = _payload(relative=None, path="/media/Other/book.epub")
    assert "needs-attention" == _catalogue(payload).classification
    with pytest.raises(MetadataReportError):
        _current(payload)
    payload = _payload(item_path="/media/Other")
    entry = _catalogue(payload)
    assert "needs-attention" == entry.classification
    with pytest.raises(MetadataReportError):
        _current(payload)


def _ambiguous() -> dict[str, object]:
    payload = _payload()
    media = payload["media"]
    assert type(media) is dict
    media["ebookFiles"] = [media["ebookFile"]]
    return payload


@pytest.mark.parametrize(
    "payload",
    (
        _payload(item_relative="Author/../Book"),
        _payload(relative=None, path=None),
        _ambiguous(),
    ),
    ids=("traversal", "missing", "ambiguous"),
)
def test_traversal_missing_and_ambiguous_ebooks_fail_closed(
    payload: dict[str, object],
) -> None:
    entry = _catalogue(payload)
    assert "singular-supported" != entry.classification
    with pytest.raises(MetadataReportError):
        _current(payload)


def test_unchanged_identity_is_current_and_changed_file_identity_is_not() -> None:
    first = _payload()
    unchanged = _payload()
    changed = _payload(size=8)
    catalogue = _catalogue(first)
    assert catalogue.file_identity == _current(unchanged).identity
    assert catalogue.file_identity != _current(changed).identity


def test_normalizer_rejects_path_without_a_service_root() -> None:
    with pytest.raises(ValueError):
        normalize_ebook_location(
            _payload()["media"]["ebookFile"],
            service_path=None,
            item=_payload(),
        )


def test_flat_item_coordinates_cannot_be_interpreted_as_a_directory() -> None:
    payload = _payload(
        relative="book.epub",
        path=None,
        item_relative="book.epub",
        item_path="/media/book.epub",
        is_file=True,
    )
    catalogue = _catalogue(payload)
    current = _current(payload)
    assert "book.epub" == catalogue.service_path
    assert catalogue.file_identity == current.identity
    payload["media"]["ebookFile"]["metadata"]["relPath"] = "nested/book.epub"
    assert "needs-attention" == _catalogue(payload).classification
    with pytest.raises(MetadataReportError):
        _current(payload)
