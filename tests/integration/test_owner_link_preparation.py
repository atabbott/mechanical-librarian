"""Preparing an item through the owner link the store holds (#1477, ADR-0104).

Each case records a link through `record_owner_link`, then prepares the item
against a real report store with real adapters over synthetic answers. The
link's own sources read the record first; while the link anchors, every
other source is asked with the linked record's title and creators, and a
record that matches only the item's local values is not admitted.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import httpx
import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkItem,
    current_owner_link,
    record_owner_link,
)
from mechanical_librarian.adapters.providers.audnexus_metadata import (
    AudnexusMetadataProvider,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    ProviderBinding,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.application.provider_recovery_policy import (
    provider_recovery_decision,
)
from mechanical_librarian.domain import proposal_set
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    owner_link_admission,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkScope,
    OwnerLinkTarget,
)
from tests.support.hardcover_evidence import books_envelope
from tests.support.metadata_report_doubles import (
    FakeAudioInspector,
    FakeInspector,
    FakeTarget,
    audio_result,
)
from tests.support.metadata_report_schemas import default_target_fields

LOCAL_TITLE = "Local Title"
LOCAL_AUTHOR = "Local Author"
LINKED_TITLE = "Linked Title"
LINKED_AUTHOR = "Linked Author"
ISBN = "9780306406157"
ASIN = "B000000001"
ITEM = "item-1"
CONNECTION = "primary"


def _store(tmp_path: Path) -> Path:
    path = tmp_path / "state.sqlite3"
    open_repository(path).close()
    return path


def _link(path: Path, library: str, medium: str, target: OwnerLinkTarget) -> None:
    """Record the owner's link through the store, in its own transaction."""
    connection = sqlite3.connect(path, isolation_level=None)
    # A target with no source is a file link (#1478), recorded under an import.
    import_id = None if target.provider is not None else "identifier_import_" + "f" * 64
    try:
        connection.execute("BEGIN IMMEDIATE")
        if import_id is not None:
            connection.execute(
                "INSERT INTO identifier_imports(import_id,connection_name,"
                "library_name,row_count,will_link,already_linked,no_item,ambiguous,"
                "invalid_identifier,wrong_medium,repeated,conflicting,unreadable,"
                "actor,created_at,request_id,request_sha256,preview_digest) "
                "VALUES(?,?,?,1,1,0,0,0,0,0,0,0,0,'owner',1,'import-request-0001',?,?)",
                (import_id, CONNECTION, library, "a" * 64, "b" * 64),
            )
        record_owner_link(
            connection,
            item=OwnerLinkItem(CONNECTION, library, ITEM),
            action=OwnerLinkAction.LINK,
            expected_current=None,
            target=target,
            source="search" if import_id is None else "file",
            medium=medium,
            actor="owner",
            occurred_at=1_700_000_000,
            request_id="request-0000000001",
            request_sha256="a" * 64,
            import_id=import_id,
        )
        connection.execute("COMMIT")
    finally:
        connection.close()


def _fields(title: str = LOCAL_TITLE) -> tuple[TargetField, ...]:
    return tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (LOCAL_AUTHOR,))
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(title)
    )


def _json(value: object) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/json"}, json=value)


def _hardcover(answer: Callable[[], httpx.Response]) -> HardcoverMetadataProvider:
    def handle(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["operationName"] == "BookById":
            return answer()
        return _json({"data": {"books": [], "tags": []}})

    return HardcoverMetadataProvider(
        "hardcover",
        httpx.Client(transport=httpx.MockTransport(handle)),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def _linked_book() -> list[dict[str, object]]:
    return [
        {
            "id": 4321,
            "title": LINKED_TITLE,
            "contributions": [
                {
                    "id": 1,
                    "contribution": None,
                    "author": {"id": 9, "name": LINKED_AUTHOR},
                }
            ],
        }
    ]


def _volume(title: str, author: str, published: str = "2031-01-02") -> object:
    return {
        "totalItems": 1,
        "items": [
            {
                "id": "volume_1",
                "volumeInfo": {
                    "title": title,
                    "authors": [author],
                    "publishedDate": published,
                    "industryIdentifiers": [{"type": "ISBN_13", "identifier": ISBN}],
                    "printType": "BOOK",
                    "language": "en",
                },
            }
        ],
    }


def _google(
    queries: list[str], answer: Callable[[str], object]
) -> GoogleBooksMetadataProvider:
    def handle(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q", "")
        queries.append(query)
        return _json(answer(query))

    return GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handle)),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def _prepare_ebook(
    path: Path,
    bindings: tuple[ProviderBinding, ...],
    claims: tuple[LocalClaim, ...] = (),
) -> MetadataReport:
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", LOCAL_TITLE, (selected,), fields=_fields())
    return MetadataReport.from_json(
        GenerateMetadataReport(
            target=FakeTarget([snapshot, snapshot]),
            inspector=FakeInspector(InspectionResult("c" * 64, claims)),
            repository_factory=lambda: open_repository(path),
            providers=bindings,
            connection=TargetConnectionConfiguration(
                CONNECTION, "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks",
                CONNECTION,
                "library-id",
                "ebook",
                "books",
                tuple(sorted(binding.name for binding in bindings)),
            ),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        ).run(ITEM, 0)
    )


def _provider(report: MetadataReport, name: str) -> ProviderObservation:
    return next(value for value in report.providers if value.provider == name)


WORK_LINK = OwnerLinkTarget(OwnerLinkScope.WORK, "hardcover", "4321", LINKED_TITLE)


def _work_link_report(
    tmp_path: Path,
    answer: Callable[[], httpx.Response],
    google: Callable[[str], object] = lambda _: {"totalItems": 0},
) -> tuple[MetadataReport, list[str], Path]:
    path = _store(tmp_path)
    _link(path, "ebooks", "ebook", WORK_LINK)
    queries: list[str] = []
    report = _prepare_ebook(
        path,
        (
            ProviderBinding(
                "hardcover", True, lambda: _hardcover(answer), authority="hardcover"
            ),
            ProviderBinding(
                "google_books",
                True,
                lambda: _google(queries, google),
                authority="google_books",
            ),
        ),
    )
    return report, queries, path


def test_a_linked_work_is_read_by_key_and_anchors_every_other_search(
    tmp_path: Path,
) -> None:
    report, queries, _ = _work_link_report(
        tmp_path, lambda: _json(books_envelope(_linked_book()))
    )
    facts = report.owner_link
    assert facts is not None
    assert (facts.outcome, facts.anchor) == (
        "admitted",
        (LINKED_TITLE, (LINKED_AUTHOR,)),
    )
    hardcover = _provider(report, "hardcover")
    (lane,) = hardcover.lanes
    assert (lane.kind, lane.status) == (
        ProviderQueryKind.LINK,
        ProviderStatus.CANDIDATES,
    )
    (member,) = lane.candidates
    assert owner_link_admission(report, "hardcover", member.edition_identity) == (
        "owner_link"
    )
    # The other source is asked with the linked record, never the local title.
    assert queries and all(LINKED_TITLE in query for query in queries)
    assert not any(LOCAL_TITLE in query for query in queries)


@pytest.mark.parametrize(
    ("title", "author", "admitted"),
    [
        pytest.param(LINKED_TITLE, LINKED_AUTHOR, True, id="matches-anchor"),
        pytest.param(LOCAL_TITLE, LOCAL_AUTHOR, False, id="matches-only-local"),
    ],
)
def test_other_sources_admit_against_the_anchor_not_the_local_values(
    tmp_path: Path, title: str, author: str, admitted: bool
) -> None:
    report, _, _ = _work_link_report(
        tmp_path,
        lambda: _json(books_envelope(_linked_book())),
        lambda _: _volume(title, author),
    )
    google = _provider(report, "google_books")
    identities = [
        member.edition_identity
        for lane in google.lanes
        if lane.kind is ProviderQueryKind.WORK
        for member in lane.candidates
    ]
    if not admitted:
        assert identities == []
        return
    assert identities
    assert {
        owner_link_admission(report, "google_books", identity)
        for identity in identities
    } == {"owner_link_anchor"}


@pytest.mark.parametrize(
    ("books", "outcome"),
    [
        pytest.param([], "missing", id="missing"),
        pytest.param(
            [{"id": 4321, "title": "Old", "canonical_id": 99}], "merged", id="merged"
        ),
    ],
)
def test_a_missing_or_merged_record_anchors_nothing_and_asks_no_search(
    tmp_path: Path, books: list[dict[str, object]], outcome: str
) -> None:
    report, queries, _ = _work_link_report(
        tmp_path, lambda: _json(books_envelope(books))
    )
    facts = report.owner_link
    assert facts is not None
    assert (facts.outcome, facts.anchor) == (outcome, None)
    # The link still steers: no source searches with the local values.
    assert queries == []


def test_an_outage_keeps_the_link_and_leaves_the_lane_for_recovery(
    tmp_path: Path,
) -> None:
    report, queries, path = _work_link_report(tmp_path, lambda: httpx.Response(502))
    facts = report.owner_link
    assert facts is not None
    assert facts.outcome == "unavailable"
    assert queries == []
    hardcover = _provider(report, "hardcover")
    assert provider_recovery_decision(hardcover, now=0) is not None
    connection = sqlite3.connect(path)
    try:
        link = current_owner_link(connection, CONNECTION, "ebooks", ITEM)
    finally:
        connection.close()
    assert link is not None and link.identity == "4321"


def test_a_link_for_the_other_medium_is_not_read(tmp_path: Path) -> None:
    path = _store(tmp_path)
    _link(
        path,
        "ebooks",
        "audiobook",
        OwnerLinkTarget(OwnerLinkScope.RECORDING, "audible_catalog", ASIN, "A"),
    )
    queries: list[str] = []
    report = _prepare_ebook(
        path,
        (
            ProviderBinding(
                "google_books",
                True,
                lambda: _google(queries, lambda _: {"totalItems": 0}),
                authority="google_books",
            ),
        ),
    )
    facts = report.owner_link
    assert facts is not None
    assert (facts.outcome, facts.anchor) == ("medium_changed", None)
    assert queries and all(LOCAL_TITLE in query for query in queries)


EDITION_LINK = OwnerLinkTarget(
    OwnerLinkScope.EDITION, "open_library", ISBN, LINKED_TITLE, (LINKED_AUTHOR,)
)


def _open_library(
    queries: list[str], physical_format: str
) -> OpenLibraryMetadataProvider:
    documents: dict[str, object] = {
        "/books/OL9M.json": {
            "key": "/books/OL9M",
            "works": [{"key": "/works/OL5W"}],
            "isbn_13": [ISBN],
            "title": LINKED_TITLE,
            "physical_format": physical_format,
        },
        "/works/OL5W.json": {
            "key": "/works/OL5W",
            "title": LINKED_TITLE,
            "authors": [
                {
                    "author": {"key": "/authors/OL7A"},
                    "type": {"key": "/type/author_role"},
                }
            ],
        },
        "/authors/OL7A.json": {"key": "/authors/OL7A", "name": LINKED_AUTHOR},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/search.json":
            return _json(documents[request.url.path])
        query = request.url.params.get("q", "")
        queries.append(query)
        if query != f"isbn:{ISBN}":
            return _json({"numFound": 0, "docs": []})
        return _json(
            {
                "numFound": 1,
                "docs": [
                    {
                        "key": "/works/OL5W",
                        "editions": {"docs": [{"key": "/books/OL9M", "isbn": [ISBN]}]},
                    }
                ],
            }
        )

    return OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handle)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )


@pytest.mark.parametrize(
    ("physical_format", "outcome", "reason"),
    [
        pytest.param("eBook", "admitted", None, id="ebook-edition"),
        pytest.param("Hardcover", "contradicted", "format", id="print-edition"),
    ],
)
def test_an_edition_link_is_asked_on_its_own_lane_and_re_checked(
    tmp_path: Path, physical_format: str, outcome: str, reason: str | None
) -> None:
    _assert_edition_link(tmp_path, EDITION_LINK, physical_format, outcome, reason)


def test_a_file_edition_link_prepares_as_a_search_link_to_the_same_isbn(
    tmp_path: Path,
) -> None:
    """An identifiers file's link (#1478) is the same lookup (A12)."""
    searched = _assert_edition_link(
        tmp_path / "search", EDITION_LINK, "eBook", "admitted", None
    )
    filed = _assert_edition_link(
        tmp_path / "file",
        OwnerLinkTarget(OwnerLinkScope.EDITION, None, ISBN, None),
        "eBook",
        "admitted",
        None,
    )
    assert filed.owner_link is not None and searched.owner_link is not None
    assert filed.owner_link.to_data() == searched.owner_link.to_data()


def _assert_edition_link(
    tmp_path: Path,
    target: OwnerLinkTarget,
    physical_format: str,
    outcome: str,
    reason: str | None,
) -> MetadataReport:
    tmp_path.mkdir(mode=0o700, exist_ok=True)
    path = _store(tmp_path)
    _link(path, "ebooks", "ebook", target)
    queries: list[str] = []
    report = _prepare_ebook(
        path,
        (
            ProviderBinding(
                "open_library",
                True,
                lambda: _open_library(queries, physical_format),
                authority="open_library",
            ),
            # Every edition source is asked with the link's ISBN; Open
            # Library's identifier answer names no creators, so here the
            # anchor is the other source's record.
            ProviderBinding(
                "google_books",
                True,
                lambda: _google(
                    [],
                    lambda query: (
                        _volume(LINKED_TITLE, LINKED_AUTHOR)
                        if query == f"isbn:{ISBN}" and outcome == "admitted"
                        else {"totalItems": 0}
                    ),
                ),
                authority="google_books",
            ),
        ),
    )
    facts = report.owner_link
    assert facts is not None
    assert (facts.outcome, facts.reason) == (outcome, reason)
    assert queries[0] == f"isbn:{ISBN}"
    link_lanes = [
        lane
        for lane in _provider(report, "open_library").lanes
        if lane.kind is ProviderQueryKind.ISBN and lane.matched_family == ISBN
    ]
    assert len(link_lanes) == 1
    assert bool(link_lanes[0].candidates) is (outcome == "admitted")
    # Every link steers (owner's choice, ADR-0104): an edition link anchors.
    assert facts.anchor == (
        (LINKED_TITLE, (LINKED_AUTHOR,)) if outcome == "admitted" else None
    )
    return report


def _audnexus(runtime_minutes: int | None) -> AudnexusMetadataProvider:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chapters"):
            return httpx.Response(404, json={})
        return httpx.Response(
            200,
            json={
                "asin": request.url.path.split("/")[2],
                "title": LINKED_TITLE,
                "authors": [{"name": LINKED_AUTHOR}],
                **(
                    {}
                    if runtime_minutes is None
                    else {"runtimeLengthMin": runtime_minutes}
                ),
            },
        )

    return AudnexusMetadataProvider(
        "audnexus",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        5.0,
        marketplaces=("us",),
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def _recording_report(
    tmp_path: Path, minutes: int | None, *, provider: str | None = "audible_catalog"
) -> MetadataReport:
    path = _store(tmp_path)
    _link(
        path,
        "audio",
        "audiobook",
        OwnerLinkTarget(
            OwnerLinkScope.RECORDING, provider, ASIN, None if provider is None else "A"
        ),
    )
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    snapshot = TargetSnapshot(
        "audiobook", LOCAL_TITLE, (selected,), fields=_fields(LOCAL_TITLE)
    )
    return MetadataReport.from_json(
        GenerateMetadataReport(
            target=FakeTarget([snapshot, snapshot]),
            inspector=FakeInspector(InspectionResult("c" * 64, ())),
            repository_factory=lambda: open_repository(path),
            providers=(
                ProviderBinding(
                    "audnexus",
                    True,
                    lambda: _audnexus(minutes),
                    authority="audnexus",
                ),
            ),
            connection=TargetConnectionConfiguration(
                CONNECTION, "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "audio",
                CONNECTION,
                "library-id",
                "audiobook",
                "audiobooks",
                ("audnexus",),
            ),
            mount=MediaMountConfiguration("audiobooks", "/media", "/library", True),
            limits=ReportLimits(),
            audio_inspector=FakeAudioInspector(
                audio_result(((ClaimKind.DURATION, "58200.000", "embedded_tag", None),))
            ),
        ).run(ITEM, 0)
    )


@pytest.mark.parametrize(
    ("minutes", "outcome", "reason"),
    [
        pytest.param(970, "admitted", None, id="inside-runtime-band"),
        pytest.param(500, "contradicted", "runtime", id="outside-runtime-band"),
        # Codex P2-2 on #1938: a product that states no runtime corroborates
        # nothing, so the link is not confirmed (ADR-0104 decision 4).
        pytest.param(None, "contradicted", "runtime", id="no-runtime-stated"),
    ],
)
def test_a_recording_link_is_re_checked_against_the_files_runtime(
    tmp_path: Path, minutes: int | None, outcome: str, reason: str | None
) -> None:
    report = _recording_report(tmp_path, minutes)
    facts = report.owner_link
    assert facts is not None
    assert (facts.outcome, facts.reason) == (outcome, reason)
    audnexus = _provider(report, "audnexus")
    (link_lane,) = (
        lane for lane in audnexus.lanes if lane.kind is ProviderQueryKind.ASIN
    )
    assert bool(link_lane.candidates) is (outcome == "admitted")
    # A recording link anchors too, but only with a record that passed.
    assert facts.anchor == (
        (LINKED_TITLE, (LINKED_AUTHOR,)) if outcome == "admitted" else None
    )
    if outcome == "admitted":
        (member,) = link_lane.candidates
        assert owner_link_admission(report, "audnexus", member.edition_identity) == (
            "owner_link"
        )


def test_a_file_recording_link_prepares_as_a_search_link_to_the_same_asin(
    tmp_path: Path,
) -> None:
    (tmp_path / "search").mkdir(mode=0o700)
    (tmp_path / "file").mkdir(mode=0o700)
    searched = _recording_report(tmp_path / "search", 970)
    filed = _recording_report(tmp_path / "file", 970, provider=None)
    assert filed.owner_link is not None and searched.owner_link is not None
    assert filed.owner_link.outcome == "admitted"
    assert filed.owner_link.to_data() == searched.owner_link.to_data()


def test_an_identifier_is_never_proposed_from_a_record_only_the_link_kept(
    tmp_path: Path,
) -> None:
    # S25 (#1477): a recording link's record names the book; the ASIN it
    # carries is not proposed from it. The ungated control shows that record
    # would otherwise supply exactly that observation.
    report = _recording_report(tmp_path, 970)
    (member,) = (
        candidate
        for lane in _provider(report, "audnexus").lanes
        if lane.kind is ProviderQueryKind.ASIN
        for candidate in lane.candidates
    )
    assert owner_link_admission(report, "audnexus", member.edition_identity) == (
        "owner_link"
    )
    assert proposal_set._observations(report, TargetDestination.ASIN) == ()
    with mock.patch.object(proposal_set, "owner_link_reached", return_value=None):
        ungated = proposal_set._observations(report, TargetDestination.ASIN)
    assert [observed.value.value for observed in ungated] == [ASIN]
