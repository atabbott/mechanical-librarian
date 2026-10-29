from __future__ import annotations

import hashlib
import json
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
    CatalogueEntry,
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
from mechanical_librarian.domain.audio_inspection import MAX_AUDIO_TRACKS
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
    surrogate = _item("surrogate")
    untrimmed["media"]["metadata"]["title"] = "  Needs attention  "
    invalid["media"]["metadata"]["title"] = "   "
    surrogate["media"]["metadata"]["title"] = "\ud800"
    details = {
        "valid": valid,
        "untrimmed": untrimmed,
        "invalid": invalid,
        "surrogate": surrogate,
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
                    "total": 4,
                    "results": [
                        {"id": "valid", "libraryId": "library-1", "updatedAt": 1},
                        {
                            "id": "untrimmed",
                            "libraryId": "library-1",
                            "updatedAt": 1,
                        },
                        {"id": "invalid", "libraryId": "library-1", "updatedAt": 1},
                        {"id": "surrogate", "libraryId": "library-1", "updatedAt": 1},
                    ],
                },
            )
        return httpx.Response(
            200,
            content=json.dumps(
                details[request.url.path.rsplit("/", 1)[1]], ensure_ascii=True
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

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
        ("surrogate", "Untitled item requiring attention", "needs-attention"),
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
    different_surrogate = _item("surrogate")
    different_surrogate["media"]["metadata"]["title"] = "\ud801"
    assert (
        next(
            entry for entry in entries if entry.item_id == "surrogate"
        ).source_fingerprint
        != catalogue_entry(
            "primary",
            _library(),
            None,
            {"id": "surrogate", "libraryId": "library-1", "updatedAt": 1},
            different_surrogate,
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


@pytest.mark.parametrize("libraries", (None, {}, "books"))
def test_non_list_libraries_is_bounded_at_the_catalogue_boundary(
    libraries: object,
) -> None:
    reader = AudiobookshelfCatalogueReader(
        client_factory=lambda _: httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"libraries": libraries})
            ),
            base_url="https://abs.invalid",
        ),
        mounts={},
        maximum_bytes=65_536,
        timeout_seconds=1,
    )
    with pytest.raises(CatalogueError, match="catalogue response is invalid"):
        reader.read_library("primary", _library(), 10)
    with pytest.raises(TargetConnectionError) as raised:
        reader.probe_connection("primary", (_library(),))
    assert raised.value.code == "unavailable"


@pytest.mark.parametrize(
    "invalid_title",
    (
        pytest.param("  Needs attention  ", id="untrimmed"),
        pytest.param("\ud800", id="lone_surrogate"),
    ),
)
def test_preparation_discovery_keeps_an_untrimmed_title_out_of_connection_pause(
    invalid_title: str,
) -> None:
    valid = _item("valid")
    untrimmed = _item("untrimmed")
    untrimmed["media"]["metadata"]["title"] = invalid_title
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
        return httpx.Response(
            200,
            content=json.dumps(
                details[request.url.path.rsplit("/", 1)[1]], ensure_ascii=True
            ).encode("utf-8"),
            headers={"content-type": "application/json"},
        )

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


def _audio_item(files: object) -> dict[str, object]:
    return {
        "id": "item-1",
        "libraryId": "library-1",
        "updatedAt": 1,
        "media": {
            "metadata": {"title": "Recorded title", "authors": [{"name": "Ada"}]},
            "audioFiles": files,
        },
    }


def _audio_record(name: str = "book.m4b", extension: str = ".m4b") -> dict[str, object]:
    return {
        "ino": "inode",
        "metadata": {"relPath": name, "size": 7, "ext": extension},
    }


def test_a_mounted_multi_file_audiobook_is_one_preparable_item() -> None:
    """An item is the preparable unit however many files it holds.

    The identity is the ordered set's, so adding, removing, or reordering a
    track moves it exactly once, and the service path is the first track's.
    """
    entry = catalogue_entry(
        "primary",
        _library("audio"),
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _audio_item(
            [
                _audio_record("part-1.m4b"),
                _audio_record("part-2.m4b"),
                _audio_record("part-3.m4b"),
            ]
        ),
    )

    assert (entry.classification, entry.eligible) == ("singular-supported", True)
    assert (entry.format, entry.service_path) == ("m4b", "part-1.m4b")
    reordered = catalogue_entry(
        "primary",
        _library("audio"),
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _audio_item(
            [
                _audio_record("part-2.m4b"),
                _audio_record("part-1.m4b"),
                _audio_record("part-3.m4b"),
            ]
        ),
    )
    assert reordered.file_identity != entry.file_identity
    assert reordered.source_fingerprint != entry.source_fingerprint


def test_a_track_edit_alone_moves_a_multi_file_audiobook_fingerprint() -> None:
    """Every track's revision is fingerprinted, not just the first one's.

    A set whose second file was rewritten and whose first was not is a
    different recording, and a fingerprint reading one member would not have
    noticed.
    """

    def item(second_revision: int) -> dict[str, object]:
        first = _audio_record("part-1.m4b")
        second = _audio_record("part-2.m4b")
        cast(dict[str, object], first["metadata"])["mtimeMs"] = 1
        cast(dict[str, object], second["metadata"])["mtimeMs"] = second_revision
        return _audio_item([first, second])

    def fingerprint(detail: dict[str, object]) -> str | None:
        return catalogue_entry(
            "primary",
            _library("audio"),
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
            detail,
        ).source_fingerprint

    assert fingerprint(item(1)) != fingerprint(item(2))


def test_a_declared_index_alone_moves_a_multi_file_audiobook_fingerprint() -> None:
    """A renumbered set is a set whose report no longer states its order.

    The declared index becomes each track's `sequence`, and with it the track
    manifest and the gap and disorder observations composed from it.  Nothing
    else moves when only the index does, so a fingerprint reading revisions
    alone would leave the published order stale.  One file declares no order
    at all, so its shape is the v34 one and cannot carry an index.
    """

    def fingerprint(detail: dict[str, object]) -> str | None:
        return catalogue_entry(
            "primary",
            _library("audio"),
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
            detail,
        ).source_fingerprint

    def indexed(name: str, index: int) -> dict[str, object]:
        record = _audio_record(name)
        record["index"] = index
        return record

    def three(second_index: int) -> dict[str, object]:
        return _audio_item(
            [
                indexed("part-1.m4b", 1),
                indexed("part-2.m4b", second_index),
                indexed("part-3.m4b", 3),
            ]
        )

    def one(index: int) -> dict[str, object]:
        return _audio_item([indexed("book.m4b", index)])

    assert fingerprint(three(2)) != fingerprint(three(9))
    assert fingerprint(one(1)) == fingerprint(one(9))


def test_an_excluded_track_is_not_part_of_the_admitted_item() -> None:
    excluded = _audio_record("part-x.m4b")
    excluded["exclude"] = True
    entry = catalogue_entry(
        "primary",
        _library("audio"),
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _audio_item([_audio_record("part-1.m4b"), excluded]),
    )
    plain = catalogue_entry(
        "primary",
        _library("audio"),
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _audio_item([_audio_record("part-1.m4b")]),
    )

    assert entry.file_identity == plain.file_identity


def _audio_entry(files: list[object]) -> CatalogueEntry:
    return catalogue_entry(
        "primary",
        _library("audio"),
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _audio_item(files),
    )


@pytest.mark.parametrize(
    ("count", "classification", "eligible"),
    (
        pytest.param(MAX_AUDIO_TRACKS, "singular-supported", True, id="widest-set"),
        pytest.param(MAX_AUDIO_TRACKS + 1, "needs-attention", False, id="one-too-wide"),
    ),
)
def test_a_set_wider_than_composition_admits_is_attended_to_not_hidden(
    count: int, classification: str, eligible: bool
) -> None:
    """An item this build cannot compose is a state the owner can act on.

    Classified `none` it left the library entirely, so the only way to learn
    the item existed was a scan racing the item reader's typed terminal. It
    still carries no `file_identity`: nothing prepares it, and attention is
    the whole outcome.
    """
    entry = _audio_entry(
        [_audio_record(f"part-{index}.m4b") for index in range(1, count + 1)]
    )

    assert (entry.classification, entry.eligible) == (classification, eligible)
    assert (entry.file_identity is None) is not eligible


def test_the_excluded_records_are_dropped_before_the_set_is_counted() -> None:
    """The set this build composes is the set the service plays.

    Counting the payload's records instead refused a five-hundred-and-ten
    track item because ten files beside it were excluded from the recording.
    """
    excluded = [_audio_record(f"extra-{index}.m4b") for index in range(10)]
    for record in excluded:
        record["exclude"] = True
    entry = _audio_entry(
        [
            *(
                _audio_record(f"part-{index}.m4b")
                for index in range(1, MAX_AUDIO_TRACKS - 1)
            ),
            *excluded,
        ]
    )

    assert (entry.classification, entry.eligible) == ("singular-supported", True)
    assert (
        entry.file_identity
        == _audio_entry(
            [
                _audio_record(f"part-{index}.m4b")
                for index in range(1, MAX_AUDIO_TRACKS - 1)
            ]
        ).file_identity
    )


def test_a_mounted_single_file_audiobook_is_singular_supported() -> None:
    entry = catalogue_entry(
        "primary",
        _library("audio"),
        MediaMountConfiguration("audiobooks", "/media", "/library", True),
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _audio_item([_audio_record()]),
    )

    assert (entry.classification, entry.eligible, entry.has_ebook) == (
        "singular-supported",
        True,
        False,
    )
    assert (entry.format, entry.mount, entry.service_path) == (
        "m4b",
        "audiobooks",
        "book.m4b",
    )


@pytest.mark.parametrize(
    ("mount", "files"),
    (
        pytest.param(None, [_audio_record()], id="unmounted"),
        pytest.param(
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            [_audio_record("part-1.m4b"), _audio_record("book.wav", ".wav")],
            id="one-unsupported-track",
        ),
        pytest.param(
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            [_audio_record("book.wav", ".wav")],
            id="unsupported-format",
        ),
        pytest.param(
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            [],
            id="no-audio-file",
        ),
        pytest.param(
            MediaMountConfiguration("audiobooks", "/media", "/library", True),
            "not-a-list",
            id="not-a-list",
        ),
    ),
)
def test_audiobook_shapes_outside_the_slice_stay_uneligible_without_attention(
    mount: MediaMountConfiguration | None, files: object
) -> None:
    entry = catalogue_entry(
        "primary",
        _library("audio"),
        mount,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _audio_item(files),
    )

    assert (entry.classification, entry.eligible, entry.has_ebook) == (
        "none",
        False,
        False,
    )


_AUDIO_MOUNT = MediaMountConfiguration("audiobooks", "/media", "/library", True)


def _recorded_audio_item(revision: int, updated_at: int) -> dict[str, object]:
    record = _audio_record()
    cast(dict[str, object], record["metadata"])["mtimeMs"] = revision
    detail = _audio_item([record])
    detail["updatedAt"] = updated_at
    return detail


def _audio_fingerprint(detail: dict[str, object]) -> str | None:
    return catalogue_entry(
        "primary",
        _library("audio"),
        _AUDIO_MOUNT,
        {
            "id": "item-1",
            "libraryId": "library-1",
            "updatedAt": detail["updatedAt"],
        },
        detail,
    ).source_fingerprint


def test_playback_alone_does_not_re_prepare_an_audiobook() -> None:
    """An audiobook's fingerprint follows its file, not its item timestamp.

    Audiobookshelf advances `updatedAt` on playback, and the audio branch
    populated no file revision, so every audiobook someone listened to came
    back changed and was prepared again -- re-hashing gigabytes for a file
    nobody wrote.  A cover path is no reason to keep that fallback here: an
    audiobook's art is recorded as presence and never acquired.
    """
    baseline = _recorded_audio_item(1, 1)
    played = _recorded_audio_item(1, 2)
    covered = _recorded_audio_item(1, 3)
    covered["coverPath"] = "/cover/item-1"

    assert _audio_fingerprint(baseline) == _audio_fingerprint(played)
    # The cover path itself is still material; the timestamp behind it is not.
    assert _audio_fingerprint(covered) != _audio_fingerprint(played)
    assert _audio_fingerprint(covered) == _audio_fingerprint(
        {**_recorded_audio_item(1, 9), "coverPath": "/cover/item-1"}
    )
    # And a file that actually moved still re-prepares.
    assert _audio_fingerprint(_recorded_audio_item(2, 1)) != _audio_fingerprint(
        baseline
    )


def test_an_ebook_with_a_cover_reference_still_follows_the_item_timestamp() -> None:
    """The fallback the audio branch drops is the one the ebook branch needs.

    This build acquires an ebook item's cover bytes, and the supported payload
    gives a cover path no revision of its own, so the item timestamp stays the
    only signal that those bytes may have been replaced.
    """
    summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}
    fingerprints = []
    for updated_at in (1, 2):
        detail = _item()
        cast(
            dict[str, object],
            cast(dict[str, object], detail["media"])["ebookFile"],
        )["metadata"]["mtimeMs"] = 1
        detail["coverPath"] = "/cover/item-1"
        detail["updatedAt"] = updated_at
        fingerprints.append(
            catalogue_entry(
                "primary",
                _library(),
                None,
                {**summary, "updatedAt": updated_at},
                detail,
            ).source_fingerprint
        )

    assert fingerprints[0] != fingerprints[1]


def test_nested_cover_declarations_are_bound_into_catalogue_fingerprint():
    declarations: tuple[tuple[tuple[str, object], ...], ...] = (
        (),
        (("media", None),),
        (("media", ""),),
        (("media", "/a"),),
        (("media", True),),
        (("media", 1),),
        (("media", {}),),
        (("media", []),),
        (("media", "/a"), ("metadata", None)),
        (("media", "/a"), ("metadata", "/a")),
        (("media", "/a"), ("metadata", "/b")),
    )
    fingerprints = set()
    for declaration in declarations:
        detail = _item()
        for location, value in declaration:
            mapping = (
                detail["media"] if location == "media" else detail["media"]["metadata"]
            )
            mapping["coverPath"] = value
        entry = catalogue_entry(
            "primary",
            _library(),
            None,
            {
                "id": "item-1",
                "libraryId": "library-1",
                "updatedAt": detail["updatedAt"],
            },
            detail,
        )
        fingerprints.add(entry.source_fingerprint)
    assert len(fingerprints) == len(declarations)


def _mixed_item(
    *, epub_revision: int = 1, updated_at: int = 1, audio: object = None
) -> dict[str, object]:
    detail = _audio_item(
        [_audio_record()] if audio is None else audio,
    )
    detail["updatedAt"] = updated_at
    record = cast(list[dict[str, object]], cast(dict, detail["media"])["audioFiles"])
    for value in record:
        cast(dict[str, object], value["metadata"])["mtimeMs"] = 1
    cast(dict[str, object], detail["media"])["ebookFile"] = {
        "ebookFormat": "epub",
        "ino": "inode-epub",
        "metadata": {"relPath": "book.epub", "size": 7, "mtimeMs": epub_revision},
    }
    return detail


def test_an_item_holding_audio_and_an_epub_is_prepared_as_its_audiobook() -> None:
    """The recording is the preparable unit; the EPUB is evidence (#1319)."""
    entry = catalogue_entry(
        "primary",
        _library("audio"),
        _AUDIO_MOUNT,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        _mixed_item(),
    )

    assert (entry.classification, entry.eligible) == ("singular-supported", True)
    assert (entry.format, entry.service_path) == ("m4b", "book.m4b")
    assert (entry.has_audio, entry.has_ebook) == (True, True)


def test_a_mixed_item_moves_with_its_epub_but_not_with_playback() -> None:
    baseline = _audio_fingerprint(_mixed_item())

    assert baseline == _audio_fingerprint(_mixed_item(updated_at=9))
    assert baseline != _audio_fingerprint(_mixed_item(epub_revision=2))
    # The same tracks alone are a different item from the tracks with an EPUB.
    assert baseline != _audio_fingerprint(_recorded_audio_item(1, 1))


def test_single_medium_fingerprints_keep_their_exact_earlier_values() -> None:
    """Fingerprints of items that hold one medium are unchanged by #1319.

    The digests are the ones main produced for these fixtures before the
    change, so no audio-only or ebook-only item is prepared again for it.
    """
    summary = {"id": "item-1", "libraryId": "library-1", "updatedAt": 1}

    assert _audio_fingerprint(_recorded_audio_item(1, 1)) == (
        "8323b3c382777d3b5df96fa5584bd08bacaad9abb3741d1c511c70935e4768d6"
    )
    assert catalogue_entry(
        "primary", _library("audio"), None, summary, _item()
    ).source_fingerprint == (
        "0858270f838bc1d9b966c92b43514293ff531a310ccd41cac9f26537c67253d2"
    )


@pytest.mark.parametrize(
    "audio_files",
    (
        pytest.param([], id="empty"),
        pytest.param([{**_audio_record(), "exclude": True}], id="all-excluded"),
    ),
)
def test_an_ebook_with_no_included_audio_has_no_audio(audio_files: object) -> None:
    """An empty or wholly excluded `audioFiles` list is not audio (#1319).

    Audiobookshelf answers an empty list for every ebook-only item, which
    once set `has_audio` and put every ebook under the catalogue's audio
    filter.
    """
    detail = _item()
    cast(dict[str, object], detail["media"])["audioFiles"] = audio_files
    entry = catalogue_entry(
        "primary",
        _library(),
        None,
        {"id": "item-1", "libraryId": "library-1", "updatedAt": 1},
        detail,
    )

    assert (entry.has_ebook, entry.has_audio, entry.format) == (True, False, "epub")
