"""Audiobookshelf item reading: raw cover admission, opaque file
identity, and the complete target-field projection.
"""

from __future__ import annotations

import hashlib
import json
from typing import cast
from unittest.mock import patch

import httpx
import pytest

from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    AudiobookshelfItemReader,
    _raw_cover_authority,
)
from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.metadata_report import (
    CoverReadError,
    MetadataReportError,
    TargetCover,
    TargetNotFoundError,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_CONTRIBUTIONS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_SCALAR_CHARACTERS,
    MAX_SERIES,
    MAX_TAGS,
    TargetDestination,
    TargetFacts,
    TargetFieldState,
)
from tests.support.audiobookshelf_targets import target_snapshot


@pytest.mark.parametrize(
    "status",
    (401, 403, 503),
)
def test_item_404_is_typed_separately_from_credential_denial(status: int) -> None:
    def reader(status: int) -> AudiobookshelfItemReader:
        return AudiobookshelfItemReader(
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(status)
                )
            ),
            endpoint="https://example.invalid",
            library_id="library-id",
            service_path="/library",
            maximum_bytes=65_536,
        )

    with pytest.raises(TargetNotFoundError):
        reader(404).read("item-1")
    with pytest.raises(TargetConnectionError) as raised:
        reader(status).read("item-1")
    assert not isinstance(raised.value, TargetNotFoundError)
    assert raised.value.code == {401: "authentication", 403: "authorization", 503: "unavailable"}[status]


@pytest.mark.parametrize(
    ("content_type", "body"),
    (
        ("image/jpeg", b"\xff\xd8\xff\xe0synthetic-jpeg"),
        ("image/png", b"\x89PNG\r\n\x1a\nsynthetic-png"),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBPsynthetic-webp"),
    ),
    ids=("jpeg", "png", "webp"),
)
def test_raw_cover_accepts_matching_supported_image_signatures(
    content_type: str, body: bytes
) -> None:
    client_type = httpx.Client
    detail = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "coverPath": "/covers/item-1",
        "media": {"metadata": {"title": "Current title"}},
    }

    def detail_factory() -> httpx.Client:
        return client_type(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=detail,
                )
            )
        )

    cover = TargetCover(
        hashlib.sha256(b"/covers/item-1").hexdigest(),
        "/api/items/item-1/cover?raw=1",
        "item-1",
    )

    def raw_client() -> httpx.Client:
        return client_type(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": content_type},
                    content=body,
                )
            )
        )

    reader = AudiobookshelfItemReader(
        client_factory=detail_factory,
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )
    with patch(
        "mechanical_librarian.adapters.library_services.audiobookshelf_item.httpx.Client",
        side_effect=lambda **_kwargs: raw_client(),
    ):
        assert body == reader.read_cover(cover)


def test_raw_cover_rejects_declared_type_signature_mismatch() -> None:
    client_type = httpx.Client
    detail = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "coverPath": "/covers/item-1",
        "media": {"metadata": {"title": "Current title"}},
    }

    def detail_factory() -> httpx.Client:
        return client_type(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=detail,
                )
            )
        )

    def raw_client() -> httpx.Client:
        return client_type(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "image/png"},
                    content=b"\xff\xd8\xff\xe0synthetic-jpeg",
                )
            )
        )

    reader = AudiobookshelfItemReader(
        client_factory=detail_factory,
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )
    cover = TargetCover(
        hashlib.sha256(b"/covers/item-1").hexdigest(),
        "/api/items/item-1/cover?raw=1",
        "item-1",
    )
    with patch(
        "mechanical_librarian.adapters.library_services.audiobookshelf_item.httpx.Client",
        side_effect=lambda **_kwargs: raw_client(),
    ):
        with pytest.raises(CoverReadError):
            reader.read_cover(cover)


def test_raw_cover_rejects_nonidentity_content_encoding() -> None:
    client_type = httpx.Client
    detail = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "coverPath": "/covers/item-1",
        "media": {"metadata": {"title": "Current title"}},
    }

    def detail_factory() -> httpx.Client:
        return client_type(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=detail,
                )
            )
        )

    def raw_client() -> httpx.Client:
        class EncodedStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"not-a-raw-image"

        def raw_response(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={
                    "content-type": "image/png",
                    "content-encoding": "gzip",
                },
                stream=EncodedStream(),
                request=request,
            )

        return client_type(
            transport=httpx.MockTransport(raw_response)
        )

    reader = AudiobookshelfItemReader(
        client_factory=detail_factory,
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )
    cover = TargetCover(
        hashlib.sha256(b"/covers/item-1").hexdigest(),
        "/api/items/item-1/cover?raw=1",
        "item-1",
    )
    with patch(
        "mechanical_librarian.adapters.library_services.audiobookshelf_item.httpx.Client",
        side_effect=lambda **_kwargs: raw_client(),
    ):
        with pytest.raises(CoverReadError):
            reader.read_cover(cover)


def test_raw_cover_authority_preserves_nondefault_port() -> None:
    assert "example.invalid:13378" == _raw_cover_authority("http://example.invalid:13378")
    assert "example.invalid" == _raw_cover_authority("https://example.invalid")


def test_raw_cover_request_uses_endpoint_authority() -> None:
    seen: dict[str, str] = {}

    class Response:
        status_code = 404
        headers: dict[str, str] = {}
        def __enter__(self): return self
        def __exit__(self, *_: object): return None

    class Client:
        def __enter__(self): return self
        def __exit__(self, *_: object): return None
        def stream(self, _method: str, _url: str, *, headers: dict[str, str], timeout: object):
            del timeout
            seen.update(headers)
            return Response()

    reader = AudiobookshelfItemReader(client_factory=lambda: httpx.Client(), endpoint="http://example.invalid:13378", library_id="library-id", service_path="/library", maximum_bytes=1)
    with patch("mechanical_librarian.adapters.library_services.audiobookshelf_item.httpx.Client", return_value=Client()):
        assert reader._read_raw_cover(TargetCover("identity", "/cover", "item-1"), None) is None
    assert "example.invalid:13378" == seen["Host"]


def test_scoped_reader_derives_stable_opaque_file_identity() -> None:
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": {"title": "Current title"},
            "ebookFiles": [
                {"ebookFormat": "epub", "ino": "inode-2", "metadata": {"relPath": "two.epub", "path": "/library/two.epub", "size": 2}},
                {"ebookFormat": "epub", "ino": "inode-1", "metadata": {"relPath": "one.epub", "path": "/library/one.epub", "size": 1}},
            ],
        },
    }
    requests: list[httpx.Request] = []

    def factory() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, headers={"content-type": "application/json"}, content=json.dumps(payload).encode())
        return httpx.Client(transport=httpx.MockTransport(handler))

    reader = AudiobookshelfItemReader(client_factory=factory, endpoint="https://example.invalid", library_id="library-id", service_path="/library", maximum_bytes=65_536)
    first = reader.read("item-1")
    payload["media"]["ebookFiles"].reverse()
    second = reader.read("item-1")
    assert first.title == "Current title"
    assert first.files[0].identity == second.files[1].identity
    assert len(requests) == 2


def test_complete_target_projection_preserves_all_destination_states() -> None:
    metadata = {
        "title": "Current title",
        "subtitle": None,
        "authors": [{"name": "Ada Example"}, {"name": "Bea Example"}],
        "narrators": [],
        "series": [{"name": "Clockwork Archive", "sequence": "2.5"}],
        "genres": ["Fantasy"],
        "publishedYear": 2024,
        "publishedDate": "2024-03-04",
        "description": "",
        "isbn": "9780306406157",
        "asin": "B012345678",
        "language": "en",
        "explicit": False,
        "tags": ["Measured"],
    }
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "coverPath": "/metadata/items/item-1/cover.jpg",
        "media": {
            "metadata": metadata,
            "tags": ["Actual tag", "Second tag"],
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode-1",
                "metadata": {
                    "relPath": "book.epub",
                    "path": "/library/book.epub",
                    "size": 1,
                },
            },
        },
    }
    reader = AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )

    snapshot = reader.read("item-1")
    by_destination = {field.destination: field for field in snapshot.fields}
    assert tuple(field.destination for field in snapshot.fields) == tuple(TargetDestination)
    assert by_destination[TargetDestination.AUTHORS].value == ("Ada Example", "Bea Example")
    assert by_destination[TargetDestination.SUBTITLE].state == TargetFieldState.NULL
    assert by_destination[TargetDestination.NARRATORS].state == TargetFieldState.EMPTY
    assert by_destination[TargetDestination.PUBLISHER].state == TargetFieldState.MISSING
    assert by_destination[TargetDestination.DESCRIPTION].state == TargetFieldState.EMPTY
    assert by_destination[TargetDestination.EXPLICIT].value is False
    assert by_destination[TargetDestination.TAGS].value == ("Actual tag", "Second tag")
    assert by_destination[TargetDestination.COVER].value == cast(TargetCover, snapshot.cover).identity
    assert "/metadata/" not in json.dumps([field.to_data() for field in snapshot.fields])

    payload["media"]["tags"] = None
    explicit_null = reader.read("item-1")
    explicit_null_tags = next(
        field
        for field in explicit_null.fields
        if field.destination is TargetDestination.TAGS
    )
    assert TargetFieldState.NULL is explicit_null_tags.state
    assert explicit_null_tags.value is None

    payload["media"].pop("tags")
    fallback = reader.read("item-1")
    assert ("Measured",) == next(
            field.value
            for field in fallback.fields
            if field.destination is TargetDestination.TAGS
        )
    payload["media"]["tags"] = ["Actual tag", "Second tag"]
    payload["media"].pop("metadata")
    unloaded = reader.read("item-1")
    assert all(
            field.state is TargetFieldState.UNLOADED
            for field in unloaded.fields[:-1]
            if field.destination is not TargetDestination.TAGS
        )
    assert ("Actual tag", "Second tag") == next(field.value for field in unloaded.fields if field.destination is TargetDestination.TAGS)


@pytest.mark.parametrize(
    "unsafe_prefix",
    ("/Users/private/", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"),
    ids=("private_path", "credential"),
)
@pytest.mark.parametrize(
    "destination",
    (
        TargetDestination.SUBTITLE,
        TargetDestination.AUTHORS,
        TargetDestination.SERIES,
        TargetDestination.DESCRIPTION,
    ),
)
def test_target_text_bounds_preserve_oversized_and_malformed_states(
    destination: TargetDestination, unsafe_prefix: str
) -> None:
    exact = target_snapshot(
        {
            "title": "Current title",
            "subtitle": "s" * MAX_SCALAR_CHARACTERS,
            "authors": [{"name": "a" * MAX_SCALAR_CHARACTERS}],
            "series": [{"name": "r" * MAX_SCALAR_CHARACTERS}],
            "description": "d" * MAX_DESCRIPTION_CHARACTERS,
        }
    )
    exact_states = {
        field.destination: field.state for field in exact.fields
    }
    assert TargetFieldState.VALUE is exact_states[destination]

    oversized = target_snapshot(
        {
            "title": "Current title",
            "subtitle": "s" * (MAX_SCALAR_CHARACTERS + 1),
            "authors": [
                {"name": "a" * (MAX_SCALAR_CHARACTERS + 1)}
            ],
            "series": [
                {"name": "r" * (MAX_SCALAR_CHARACTERS + 1)}
            ],
            "publisher": "Publisher",
            "description": "d" * (MAX_DESCRIPTION_CHARACTERS + 1),
        }
    )
    oversized_destinations = {
        TargetDestination.SUBTITLE,
        TargetDestination.AUTHORS,
        TargetDestination.SERIES,
        TargetDestination.DESCRIPTION,
    }
    assert oversized_destinations == {
            field.destination
            for field in oversized.fields
            if field.state is TargetFieldState.OVERSIZED
        }
    aggregate = TargetFacts(
        "primary", "ebooks", "item-1", oversized.fields
    )
    serialized_fields = cast(
        list[dict[str, object]], aggregate.to_data()["fields"]
    )
    assert {
            entry["destination"]
            for entry in serialized_fields
            if entry["state"] == TargetFieldState.OVERSIZED.value
        } == {destination.value for destination in oversized_destinations}
    assert TargetFieldState.VALUE is next(
            field.state
            for field in oversized.fields
            if field.destination is TargetDestination.PUBLISHER
        )

    unsafe_scalar = unsafe_prefix + "x" * (
        MAX_SCALAR_CHARACTERS - len(unsafe_prefix) + 1
    )
    unsafe_description = unsafe_prefix + "x" * (
        MAX_DESCRIPTION_CHARACTERS - len(unsafe_prefix) + 1
    )
    malformed = target_snapshot(
        {
            "title": "Current title",
            "subtitle": unsafe_scalar,
            "authors": [{"name": unsafe_scalar}],
            "series": [{"name": unsafe_scalar}],
            "description": unsafe_description,
        }
    )
    malformed_states = {
        field.destination: field.state for field in malformed.fields
    }
    assert TargetFieldState.MALFORMED is malformed_states[destination]


def test_partial_publication_date_target_remains_unsupported() -> None:
    snapshot = target_snapshot(
        {
            "title": "Current title",
            "publishedDate": "2024-03",
        }
    )
    published_date = next(
        field
        for field in snapshot.fields
        if field.destination is TargetDestination.PUBLISHED_DATE
    )

    assert TargetFieldState.MALFORMED is published_date.state
    assert published_date.value is None


@pytest.mark.parametrize(
    "destination",
    (
        TargetDestination.ISBN,
        TargetDestination.ASIN,
        TargetDestination.PUBLISHED_DATE,
        TargetDestination.PUBLISHED_YEAR,
    ),
)
def test_typed_target_syntax_precedes_oversized_classification(
    destination: TargetDestination,
) -> None:
    snapshot = target_snapshot(
        {
            "title": "Current title",
            "isbn": "x" * (MAX_SCALAR_CHARACTERS + 1),
            "asin": "x" * (MAX_SCALAR_CHARACTERS + 1),
            "publishedDate": "x" * (MAX_SCALAR_CHARACTERS + 1),
            "publishedYear": "2" * (MAX_SCALAR_CHARACTERS + 1),
        }
    )
    states = {
        field.destination: field.state for field in snapshot.fields
    }
    assert TargetFieldState.MALFORMED is states[destination]


_UNSAFE_SCALAR = "/Users/private/" + "x" * MAX_SCALAR_CHARACTERS


def _collection_safety_cases() -> tuple[tuple[object, ...], ...]:
    """Every payload whose collection state the projection must classify."""

    cases: list[tuple[object, ...]] = []
    for destination, key, maximum, item in (
        (TargetDestination.AUTHORS, "authors", MAX_CONTRIBUTIONS, lambda value: {"name": value}),
        (TargetDestination.NARRATORS, "narrators", MAX_CONTRIBUTIONS, lambda value: value),
        (TargetDestination.GENRES, "genres", MAX_TAGS, lambda value: value),
        (TargetDestination.TAGS, "tags", MAX_TAGS, lambda value: value),
    ):
        filled = [item(f"Value {index}") for index in range(maximum)]
        cases += [
            ({"title": "Current title", key: [item(f"Value {index}") for index in range(maximum + 1)]}, destination, TargetFieldState.OVERSIZED, f"{key}_oversized"),
            ({"title": "Current title", key: [*filled, item(_UNSAFE_SCALAR)]}, destination, TargetFieldState.MALFORMED, f"{key}_unsafe"),
            ({"title": "Current title", key: [*filled, {"unexpected": "value"}]}, destination, TargetFieldState.MALFORMED, f"{key}_malformed"),
        ]
    safe_series = [{"name": f"Series {index}"} for index in range(MAX_SERIES + 1)]
    oversized_sequence = "s" * (MAX_SCALAR_CHARACTERS + 1)
    cases += [
        ({"title": "Current title", "series": safe_series}, TargetDestination.SERIES, TargetFieldState.OVERSIZED, "series_oversized"),
        ({"title": "Current title", "series": [*safe_series[:MAX_SERIES], {"name": _UNSAFE_SCALAR}]}, TargetDestination.SERIES, TargetFieldState.MALFORMED, "series_unsafe"),
        ({"title": "Current title", "series": [*safe_series[:MAX_SERIES], "not-a-series"]}, TargetDestination.SERIES, TargetFieldState.MALFORMED, "series_malformed"),
        ({"title": "Current title", "series": [{"name": "Series", "sequence": oversized_sequence}]}, TargetDestination.SERIES, TargetFieldState.OVERSIZED, "series_sequence_oversized"),
        ({"title": "Current title", "series": [{"name": "Series", "sequence": _UNSAFE_SCALAR}]}, TargetDestination.SERIES, TargetFieldState.MALFORMED, "series_sequence_unsafe"),
        ({"title": "t" * (MAX_SCALAR_CHARACTERS + 1)}, TargetDestination.TITLE, TargetFieldState.OVERSIZED, "title_oversized"),
    ]
    return tuple(cases)


_COLLECTION_SAFETY_CASES = _collection_safety_cases()


@pytest.mark.parametrize(
    ("payload", "destination", "expected"),
    tuple(case[:3] for case in _COLLECTION_SAFETY_CASES),
    ids=tuple(str(case[3]) for case in _COLLECTION_SAFETY_CASES),
)
def test_collection_safety_precedes_cardinality_oversized_state(
    payload: dict[str, object],
    destination: TargetDestination,
    expected: TargetFieldState,
) -> None:
    states = {
        field.destination: field.state
        for field in target_snapshot(payload).fields
    }
    assert expected is states[destination]


def test_safe_noncanonical_title_is_preserved_exactly() -> None:
    title = "Cafe\u0301  Story"
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": {"title": title},
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode-1",
                "metadata": {
                    "relPath": "book.epub",
                    "path": "/library/book.epub",
                    "size": 1,
                },
            },
        },
    }
    reader = AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )
    assert reader.read("item-1").title == title


@pytest.mark.parametrize(
    ("payload", "maximum"),
    ((b'{"id":"item-1","id":"item-1"}', 65_536), (b"x" * 1025, 1024)),
    ids=("duplicate_key", "oversized_body"),
)
def test_duplicate_and_oversized_target_responses_fail_closed(
    payload: bytes, maximum: int
) -> None:
    def client_factory(value: bytes = payload) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda request, body=value: httpx.Response(
                    200, headers={"content-type": "application/json"}, content=body
                )
            )
        )

    reader = AudiobookshelfItemReader(
        client_factory=client_factory,
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=maximum,
    )
    with pytest.raises(MetadataReportError):
        reader.read("item-1")


def test_ambiguous_singular_and_plural_file_shapes_are_rejected() -> None:
    file_value = {
        "ebookFormat": "epub",
        "ino": "inode-1",
        "metadata": {"relPath": "book.epub", "path": "/library/book.epub", "size": 1},
    }
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": {"title": "Current title"},
            "ebookFile": file_value,
            "ebookFiles": [file_value],
        },
    }
    reader = AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "application/json"}, json=payload
                )
            )
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )
    with pytest.raises(MetadataReportError):
        reader.read("item-1")


def test_root_service_path_fallback_and_dual_path_agreement_are_closed() -> None:
    base_file = {
        "ebookFormat": "epub",
        "ino": "inode-1",
        "metadata": {"path": "/book.epub", "size": 1},
    }
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": {"title": "Current title"},
            "ebookFile": base_file,
        },
    }

    def reader() -> AudiobookshelfItemReader:
        return AudiobookshelfItemReader(
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,
                    )
                )
            ),
            endpoint="https://example.invalid",
            library_id="library-id",
            service_path="/",
            maximum_bytes=65_536,
        )

    assert reader().read("item-1").files[0].relative_path == "book.epub"
    base_file["metadata"]["relPath"] = "book.epub"
    assert reader().read("item-1").files[0].relative_path == "book.epub"
    base_file["metadata"]["relPath"] = "other.epub"
    with pytest.raises(MetadataReportError):
        reader().read("item-1")


def test_item_relative_ebook_path_is_resolved_against_the_item_directory() -> None:
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "relPath": "Author/Book",
        "isFile": False,
        "media": {
            "metadata": {"title": "Current title"},
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode-1",
                "metadata": {
                    "relPath": "book.epub",
                    "path": "/library/Author/Book/book.epub",
                    "size": 1,
                },
            },
        },
    }
    reader = AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )

    assert "Author/Book/book.epub" == reader.read("item-1").files[0].relative_path


def test_item_relative_ebook_path_disagreement_and_traversal_fail_closed() -> None:
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "relPath": "Author/Book",
        "isFile": False,
        "media": {
            "metadata": {"title": "Current title"},
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode-1",
                "metadata": {
                    "relPath": "book.epub",
                    "path": "/library/Other/book.epub",
                    "size": 1,
                },
            },
        },
    }
    reader = AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )

    with pytest.raises(MetadataReportError):
        reader.read("item-1")
    payload["relPath"] = "Author/../Other"
    payload["media"]["ebookFile"]["metadata"]["path"] = (
        "/library/Other/book.epub"
    )
    with pytest.raises(MetadataReportError):
        reader.read("item-1")


@pytest.mark.parametrize(
    "hostile",
    (
        "Authorization: Bearer abcd1234",
        "Authorization: Bearer abcd1234"
        + "x" * MAX_SCALAR_CHARACTERS,
        "/Users/private/library/book.epub",
        "/Users/private/library/book.epub"
        + "x" * MAX_SCALAR_CHARACTERS,
    ),
    ids=("credential", "oversized_credential", "private_path", "oversized_private_path"),
)
def test_credential_and_private_path_target_titles_fail_without_raw_values(
    hostile: str,
) -> None:
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": {"title": hostile},
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode-1",
                "metadata": {
                    "relPath": "book.epub",
                    "path": "/library/book.epub",
                    "size": 1,
                },
            },
        },
    }
    reader = AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    )
    with pytest.raises(MetadataReportError) as raised:
        reader.read("item-1")
    assert hostile not in str(raised.value)
