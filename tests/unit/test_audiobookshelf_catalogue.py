from __future__ import annotations

import hashlib
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

import httpx
import pytest

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    AudiobookshelfCatalogueReader,
    _get,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    _entry as catalogue_entry,
)
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.catalogue import (
    CatalogueError,
    RefreshCurrentCatalogue,
    TargetConnectionError,
    catalogue_fingerprint,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    ProviderFailureObserver,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_pipeline_stages import ReviewPipelineStages
from mechanical_librarian.domain.metadata_report import ProviderStatus, canonical_json


def _library(
    name: str = "books", identifier: str = "library-1"
) -> LibraryConfiguration:
    return LibraryConfiguration(name, "primary", identifier, "book", None, ())


def _item(identifier: str = "item-1") -> dict[str, object]:
    return {
        "id": identifier,
        "libraryId": "library-1",
        "updatedAt": 1,
        "media": {
            "metadata": {"title": "Title", "authors": [{"name": "Ada"}]},
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode",
                "metadata": {"relPath": "books/title.epub", "size": 7},
            },
        },
    }


def test_probe_requires_every_configured_book_library() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/libraries"
        return httpx.Response(
            200,
            json={
                "libraries": [
                    {"id": "library-1", "mediaType": "book"},
                    {"id": "audio", "mediaType": "podcast"},
                ]
            },
        )

    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    with pytest.raises(TargetConnectionError) as raised:
        reader.probe_connection("primary", (_library(), _library("history", "missing")))
    assert raised.value.code == "unavailable"


def test_aggregate_capacity_is_checked_before_any_detail_request() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/libraries":
            return httpx.Response(
                200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]}
            )
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"total": 2, "results": [{"id": "item-1"}]})
        raise AssertionError("details must not be fetched")

    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    with pytest.raises(CatalogueError):
        reader.read_library("primary", _library(), 1)
    assert requests == ["/api/libraries", "/api/libraries/library-1/items"]


def test_singular_unmounted_book_is_catalogued_but_not_eligible_and_matches_item_identity() -> (
    None
):
    detail = _item()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/libraries":
            return httpx.Response(
                200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]}
            )
        if request.url.path.endswith("/items"):
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "results": [
                        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
                    ],
                },
            )
        return httpx.Response(200, json=detail)

    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    entry = reader.read_library("primary", _library(), 10)[0]
    expected = hashlib.sha256(
        canonical_json(
            {
                "format": "epub",
                "inode": "inode",
                "relative": "books/title.epub",
                "size": 7,
            }
        ).encode("utf-8")
    ).hexdigest()
    assert (entry.classification, entry.eligible, entry.file_identity) == (
        "singular-supported",
        False,
        expected,
    )


def test_untrimmed_title_is_retained_as_attention_without_hiding_valid_sibling() -> (
    None
):
    valid = _item("valid")
    untrimmed = _item("untrimmed")
    invalid = _item("invalid")
    untrimmed["media"]["metadata"]["title"] = "  Needs attention  "
    invalid["media"]["metadata"]["title"] = "   "
    details = {"valid": valid, "untrimmed": untrimmed, "invalid": invalid}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/libraries":
            return httpx.Response(
                200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]}
            )
        if request.url.path.endswith("/items"):
            return httpx.Response(
                200,
                json={
                    "total": 3,
                    "results": [
                        {"id": "valid", "libraryId": "library-1", "updatedAt": 1},
                        {
                            "id": "untrimmed",
                            "libraryId": "library-1",
                            "updatedAt": 1,
                        },
                        {"id": "invalid", "libraryId": "library-1", "updatedAt": 1},
                    ],
                },
            )
        return httpx.Response(200, json=details[request.url.path.rsplit("/", 1)[1]])

    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    configuration = ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "https://abs.invalid", "/token"),),
        (_library(),),
        (),
        (),
    )
    with tempfile.TemporaryDirectory() as directory:
        repository = open_catalogue(Path(directory) / "catalogue.sqlite", create=True)
        RefreshCurrentCatalogue(reader, repository).refresh(configuration)
        entries = repository.inspect(catalogue_fingerprint(configuration).digest)
        repository.close()

    assert [
        (entry.item_id, entry.title, entry.classification) for entry in entries
    ] == [
        ("invalid", "Untitled item requiring attention", "needs-attention"),
        ("untrimmed", "Needs attention", "needs-attention"),
        ("valid", "Title", "singular-supported"),
    ]
    corrected = _item("untrimmed")
    corrected["media"]["metadata"]["title"] = "Needs attention"
    assert (
        next(
            entry for entry in entries if entry.item_id == "untrimmed"
        ).source_fingerprint
        != catalogue_entry(
            "primary",
            _library(),
            None,
            {"id": "untrimmed", "libraryId": "library-1", "updatedAt": 1},
            corrected,
        ).source_fingerprint
    )


@pytest.mark.parametrize(
    ("status", "code"),
    (
        pytest.param(401, "authentication", id="authentication"),
        pytest.param(403, "authorization", id="authorization"),
        pytest.param(500, "unavailable", id="status"),
    ),
)
def test_status_connection_classifications_are_preserved(
    status: int, code: str
) -> None:
    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(status)),
            base_url="https://abs.invalid",
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    with pytest.raises(TargetConnectionError) as raised:
        reader.read_library("primary", _library(), 10)
    assert raised.value.code == code


@pytest.mark.parametrize(
    "error",
    (
        pytest.param(httpx.ConnectError("offline"), id="transport"),
        pytest.param(httpx.ReadTimeout("slow"), id="timeout"),
    ),
)
def test_transport_connection_classifications_are_preserved(
    error: httpx.TransportError,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise error

    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    with pytest.raises(TargetConnectionError) as raised:
        reader.read_library("primary", _library(), 10)
    assert raised.value.code == "unavailable"


def test_malformed_top_level_catalogue_remains_a_catalogue_failure() -> None:
    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])),
            base_url="https://abs.invalid",
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    with pytest.raises(CatalogueError, match="catalogue response is invalid"):
        reader.read_library("primary", _library(), 10)


def test_preparation_discovery_keeps_an_untrimmed_title_out_of_connection_pause() -> (
    None
):
    valid = _item("valid")
    untrimmed = _item("untrimmed")
    untrimmed["media"]["metadata"]["title"] = "  Needs attention  "
    details = {"valid": valid, "untrimmed": untrimmed}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/libraries":
            return httpx.Response(
                200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]}
            )
        if request.url.path.endswith("/items"):
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "results": [
                        {"id": "valid", "libraryId": "library-1", "updatedAt": 1},
                        {"id": "untrimmed", "libraryId": "library-1", "updatedAt": 1},
                    ],
                },
            )
        return httpx.Response(200, json=details[request.url.path.rsplit("/", 1)[1]])

    mount = MediaMountConfiguration("media", "/media", "/library", True)
    library = LibraryConfiguration("books", "primary", "library-1", "book", "media", ())
    configuration = ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "https://abs.invalid", "/token"),),
        (library,),
        (mount,),
        (),
    )
    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
        ),
        mounts={"media": mount},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )

    def unreachable(*_arguments: object) -> object:
        raise AssertionError

    with tempfile.TemporaryDirectory() as directory:
        catalogue_path = Path(directory) / "catalogue.sqlite"
        application_path = Path(directory) / "application.sqlite"
        open_repository(application_path).close()
        stages = ReviewPipelineStages(
            configuration=configuration,
            catalogue=lambda create: open_catalogue(catalogue_path, create=create),
            catalogue_reader=reader,
            probe_connection=cast("Callable[..., None]", unreachable),
            connections=frozenset({"primary"}),
            repository_factory=lambda: open_repository(application_path),
            recover_artifacts=cast("Callable[[], None]", unreachable),
            generate_report=cast("Callable[..., str]", unreachable),
            create_proposal_set=cast("Callable[..., str]", unreachable),
            admit_review_case=cast("Callable[..., str]", unreachable),
            cover_considerations=cast(
                "Callable[[ReviewPipelineRepository], CoverConsiderationService]",
                unreachable,
            ),
            recovery_observer=cast(ProviderFailureObserver, object()),
            drain_outbox=cast("Callable[..., None]", unreachable),
            discovery_lock=threading.Lock(),
            clock=lambda: 1,
        )
        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(application_path),
            libraries=(("primary", "books", "library-1", "f" * 64),),
            discover=stages.discover,
            extract=cast("Callable[..., tuple[str, bool]]", unreachable),
            providers=cast(
                "Callable[[ReviewPipelineRepository, str], tuple[ProviderStatus, ...]]",
                unreachable,
            ),
            propose=cast("Callable[..., str]", unreachable),
            admit=cast("Callable[..., tuple[str, str | None]]", unreachable),
            project=cast("Callable[..., bool]", unreachable),
            clock=lambda: 2,
        )
        pipeline.request("primary", "books")
        assert pipeline.run_next()
        assert pipeline.connection_conditions() == ()
        repository = open_repository(application_path)
        action = repository.claim_review_pipeline_action(3)
        repository.close()
        assert action is not None
        assert (action.stage, action.item_id) == ("extraction", "valid")


@pytest.mark.parametrize(
    "body",
    (
        pytest.param(b'{"a":1,"a":2}', id="duplicate_key"),
        pytest.param(b'{"a":NaN}', id="nonfinite"),
        pytest.param(b"[" * 101 + b"]" * 101, id="depth"),
    ),
)
def test_strict_stream_json_rejects_duplicates_nonfinite_and_depth(body: bytes) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "application/json"}, content=body
            )
        ),
        base_url="https://abs.invalid",
    )
    with client, pytest.raises(CatalogueError):
        _get(client, "/api/libraries", 65_536, 1)


def test_missing_items_and_one_element_plural_remain_unusable() -> None:
    detail = _item()
    detail["isMissing"] = True
    detail["media"] = {
        "metadata": {"title": "Title", "authors": []},
        "ebookFiles": [{"ebookFormat": "epub"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/libraries":
            return httpx.Response(
                200, json={"libraries": [{"id": "library-1", "mediaType": "book"}]}
            )
        if request.url.path.endswith("/items"):
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "results": [
                        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
                    ],
                },
            )
        return httpx.Response(200, json=detail)

    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://abs.invalid"
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    with pytest.raises(CatalogueError):
        reader.read_library("primary", _library(), 10)


def test_path_only_mapping_matches_item_identity_and_enforces_relative_bounds() -> None:
    mount = MediaMountConfiguration("books", "/library", "/media", True)
    summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}

    def detail(path: str) -> dict[str, object]:
        return {
            **_item(),
            "media": {
                "metadata": {"title": "Title", "authors": [{"name": "Ada"}]},
                "ebookFile": {
                    "ebookFormat": "epub",
                    "ino": "inode",
                    "metadata": {"path": path, "size": 7},
                },
            },
        }

    entry = catalogue_entry(
        "primary", _library(), mount, summary, detail("/media/books/title.epub")
    )
    expected = hashlib.sha256(
        canonical_json(
            {
                "format": "epub",
                "inode": "inode",
                "relative": "books/title.epub",
                "size": 7,
            }
        ).encode("utf-8")
    ).hexdigest()
    assert (entry.eligible, entry.file_identity, entry.service_path) == (
        True,
        expected,
        "books/title.epub",
    )
    for relative, classification in (
        ("a" * 1024, "singular-supported"),
        ("a" * 1025, "needs-attention"),
    ):
        value = catalogue_entry(
            "primary", _library(), mount, summary, detail("/media/" + relative)
        )
        assert (value.classification, value.eligible) == (
            classification,
            classification == "singular-supported",
        )
    assert not catalogue_entry(
        "primary", _library(), mount, summary, detail("/outside/book.epub")
    ).eligible


@pytest.mark.parametrize(
    ("plural", "expected"),
    (
        pytest.param([], (False, "none"), id="empty"),
        pytest.param([{}], (True, "needs-attention"), id="one_element"),
        pytest.param([{}, {}], (True, "needs-attention"), id="two_elements"),
    ),
)
def test_empty_plural_is_none_and_plural_values_need_attention(
    plural: object, expected: tuple[bool, str]
) -> None:
    summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
    detail = {
        **_item(),
        "media": {"metadata": {"title": "Title", "authors": []}, "ebookFiles": plural},
    }
    entry = catalogue_entry("primary", _library(), None, summary, detail)
    assert (entry.has_ebook, entry.classification) == expected


def test_volatile_activity_fields_do_not_change_catalogue_identity() -> None:
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
        "primary",
        _library(),
        None,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        baseline,
    )
    second = catalogue_entry(
        "primary",
        _library(),
        None,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 2},
        active,
    )
    assert first.source_fingerprint == second.source_fingerprint


def test_item_revision_is_fallback_when_material_revision_is_unavailable() -> None:
    baseline = _item()
    changed = _item()
    changed["updatedAt"] = 2
    first = catalogue_entry(
        "primary",
        _library(),
        None,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        baseline,
    )
    second = catalogue_entry(
        "primary",
        _library(),
        None,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 2},
        changed,
    )
    assert first.source_fingerprint != second.source_fingerprint


@pytest.mark.parametrize(
    "invalid_revision",
    (
        pytest.param(None, id="none"),
        pytest.param(-1, id="negative"),
        pytest.param("", id="empty_text"),
        pytest.param([], id="list"),
        pytest.param({}, id="mapping"),
    ),
)
def test_invalid_material_revision_retains_item_revision_fallback(
    invalid_revision: object,
) -> None:
    summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
    baseline = _item()
    baseline["media"]["ebookFile"]["metadata"]["mtimeMs"] = invalid_revision
    changed = _item()
    changed["updatedAt"] = 2
    changed["media"]["ebookFile"]["metadata"]["mtimeMs"] = invalid_revision
    first = catalogue_entry("primary", _library(), None, summary, baseline)
    second = catalogue_entry(
        "primary",
        _library(),
        None,
        {**summary, "updatedAt": 2},
        changed,
    )
    assert first.source_fingerprint != second.source_fingerprint


def test_reviewed_field_presence_changes_source_fingerprint() -> None:
    summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
    absent = _item()
    absent["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
    explicit_null = _item()
    explicit_null["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
    explicit_null["media"]["metadata"]["subtitle"] = None
    first = catalogue_entry("primary", _library(), None, summary, absent)
    second = catalogue_entry("primary", _library(), None, summary, explicit_null)
    assert first.source_fingerprint != second.source_fingerprint


def test_cover_field_presence_and_raw_state_change_source_fingerprint() -> None:
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
    assert len(set(fingerprints)) == len(fingerprints)


def test_item_revision_protects_cover_bytes_without_a_cover_revision() -> None:
    baseline = _item()
    baseline["coverPath"] = "/cover/item-1"
    baseline["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
    changed = _item()
    changed["coverPath"] = "/cover/item-1"
    changed["updatedAt"] = 2
    changed["media"]["ebookFile"]["metadata"]["mtimeMs"] = 1
    first = catalogue_entry(
        "primary",
        _library(),
        None,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        baseline,
    )
    second = catalogue_entry(
        "primary",
        _library(),
        None,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 2},
        changed,
    )
    assert first.source_fingerprint != second.source_fingerprint


def _apply_subtitle(detail: dict[str, object]) -> None:
    detail["media"]["metadata"].update({"subtitle": "A subtitle"})


def _apply_isbn(detail: dict[str, object]) -> None:
    detail["media"]["metadata"].update({"isbn": "9780000000002"})


def _apply_cover_path(detail: dict[str, object]) -> None:
    detail.update({"coverPath": "/cover/item-1"})


def _apply_material_revision(detail: dict[str, object]) -> None:
    detail["media"]["ebookFile"]["metadata"]["mtimeMs"] = 2


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(_apply_subtitle, id="subtitle"),
        pytest.param(_apply_isbn, id="isbn"),
        pytest.param(_apply_cover_path, id="cover_path"),
        pytest.param(_apply_material_revision, id="material_revision"),
    ),
)
def test_each_material_review_source_dimension_changes_fingerprint(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
    baseline = _item()
    original = catalogue_entry("primary", _library(), None, summary, baseline)
    detail = _item()
    mutate(detail)
    assert (
        original.source_fingerprint
        != catalogue_entry(
            "primary", _library(), None, summary, detail
        ).source_fingerprint
    )
