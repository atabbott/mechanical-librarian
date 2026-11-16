"""Recovering a failed owner-link lane, and the lanes beside it (#1477, S22).

A source report is prepared with real adapters over synthetic answers. The
successor rebuilds the link lane from the report's own link facts and
retries it alone; when it now admits, the successor's publication marks the
work fresh so the next preparation runs the anchor. A main lane is retried
under the plan the link anchored.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.review_preparation import (
    SQLiteReviewPreparationRepository,
    preparation_work_id,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
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
    MetadataProvider,
    MetadataReportError,
    ProviderBinding,
    ReportRepository,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.application.provider_recovery_activation import (
    _request_scan,
)
from mechanical_librarian.application.provider_retry_successor import (
    ConfiguredProviderRetry,
    GenerateProviderRetrySuccessor,
    ProviderRetryResolver,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.owner_link import OwnerLink, OwnerLinkScope
from tests.support.hardcover_evidence import books_envelope
from tests.support.metadata_report_doubles import (
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
)
from tests.support.metadata_report_schemas import default_target_fields

LINKED_TITLE = "Linked Title"
LINKED_AUTHOR = "Linked Author"
LINK = OwnerLink(
    "owner_link_" + "a" * 64, "ebook", OwnerLinkScope.WORK, "hardcover", "4321"
)


def _json(value: object) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/json"}, json=value)


def _book() -> httpx.Response:
    return _json(
        books_envelope(
            [
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
        )
    )


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


def _google(answer: Callable[[], httpx.Response]) -> GoogleBooksMetadataProvider:
    return GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(lambda _: answer())),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def _volume() -> httpx.Response:
    return _json(
        {
            "totalItems": 1,
            "items": [
                {
                    "id": "volume_1",
                    "volumeInfo": {
                        "title": LINKED_TITLE,
                        "authors": [LINKED_AUTHOR],
                        "printType": "BOOK",
                        "language": "en",
                    },
                }
            ],
        }
    )


SNAPSHOT = TargetSnapshot(
    "ebook",
    "Local Title",
    (TargetFile("a" * 64, "book.epub", "epub"),),
    fields=tuple(
        TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Local Author",)
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("Local Title")
    ),
)


def _source(
    hardcover: Callable[[], httpx.Response], google: Callable[[], httpx.Response]
) -> MetadataReport:
    repository = FakeReportRepository()
    repository.owner_link = LINK
    return MetadataReport.from_json(
        GenerateMetadataReport(
            target=FakeTarget([SNAPSHOT, SNAPSHOT]),
            inspector=FakeInspector(InspectionResult("c" * 64, ())),
            repository_factory=lambda: cast(ReportRepository, repository),
            providers=(
                ProviderBinding(
                    "google_books",
                    True,
                    lambda: _google(google),
                    authority="google_books",
                ),
                ProviderBinding(
                    "hardcover",
                    True,
                    lambda: _hardcover(hardcover),
                    authority="hardcover",
                ),
            ),
            connection=TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "ebooks",
                "primary",
                "library-id",
                "ebook",
                "books",
                ("google_books", "hardcover"),
            ),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        ).run("item-1", 0)
    )


class _Inspector:
    def unchanged(self, *_args: object, **_keywords: object) -> bool:
        return True


class _Repository:
    def __init__(self, report: MetadataReport) -> None:
        self.values = {report.report_id: report.to_json()}
        self.current_id = report.report_id
        self.package: tuple[str | None, str, str | None, int | None, str] = (
            None,
            report.report_id,
            None,
            None,
            "report_only",
        )

    def get(self, report_id: str) -> str:
        return self.values[report_id]

    def current_optional(self, *_args: object) -> str:
        return self.values[self.current_id]

    def current_review_package(
        self, *_args: object
    ) -> tuple[str | None, str, str | None, int | None, str]:
        return self.package

    def publish(
        self,
        report: MetadataReport,
        currentness: Callable[[], bool],
        expected_current: str | None = None,
        after_publication: Callable[[ReportRepository, MetadataReport], object]
        | None = None,
        *,
        expected_package: object = None,
    ) -> str:
        assert expected_current == self.current_id and currentness()
        self.values[report.report_id] = report.to_json()
        self.current_id = report.report_id
        if after_publication is not None:
            after_publication(cast(ReportRepository, self), report)
        return report.to_json()

    def close(self) -> None:
        pass


def _successor(
    source: MetadataReport,
    name: str,
    provider: Callable[[], MetadataProvider],
    admitted: list[MetadataReport],
) -> MetadataReport:
    observation = next(value for value in source.providers if value.provider == name)
    lanes = tuple(
        sorted(
            lane.identity
            for lane in observation.lanes
            if lane.status is ProviderStatus.UNAVAILABLE
        )
    )
    repository = _Repository(source)
    configured = ConfiguredProviderRetry(
        ProviderBinding(name, True, provider, name), name, 1
    )
    return MetadataReport.from_json(
        GenerateProviderRetrySuccessor(
            target=FakeTarget([SNAPSHOT] * 3),
            inspector=cast("FakeInspector", _Inspector()),
            repository_factory=lambda: cast(ReportRepository, repository),
            provider_resolver=cast("ProviderRetryResolver", lambda *_args: configured),
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
            after_publication=lambda *_args: None,
            owner_link_admitted=lambda _bound, report: admitted.append(report),
        ).run(
            source.report_id,
            lanes,
            provider_name=name,
            adapter=name,
            generation=1,
        )
    )


def _provider(report: MetadataReport, name: str) -> ProviderObservation:
    return next(value for value in report.providers if value.provider == name)


def test_a_recovered_link_lane_admits_and_marks_the_work_fresh() -> None:
    source = _source(lambda: httpx.Response(502), _volume)
    facts = source.owner_link
    assert facts is not None and facts.outcome == "unavailable"
    # No anchor was read, so no other source searched.
    assert not any(
        lane.kind is ProviderQueryKind.WORK
        for value in source.providers
        for lane in value.lanes
    )
    admitted: list[MetadataReport] = []
    successor = _successor(source, "hardcover", lambda: _hardcover(_book), admitted)
    facts = successor.owner_link
    assert facts is not None
    assert (facts.outcome, facts.anchor) == (
        "admitted",
        (LINKED_TITLE, (LINKED_AUTHOR,)),
    )
    (lane,) = _provider(successor, "hardcover").lanes
    assert (lane.kind, lane.status) == (
        ProviderQueryKind.LINK,
        ProviderStatus.CANDIDATES,
    )
    # The successor fans out to no other source: the fresh preparation does.
    assert [value.report_id for value in admitted] == [successor.report_id]
    assert _provider(successor, "google_books") == _provider(source, "google_books")


def test_a_main_lane_is_retried_under_the_anchor_and_marks_nothing() -> None:
    source = _source(_book, lambda: httpx.Response(502))
    (failed,) = _provider(source, "google_books").lanes
    assert failed.status is ProviderStatus.UNAVAILABLE
    admitted: list[MetadataReport] = []
    successor = _successor(source, "google_books", lambda: _google(_volume), admitted)
    (lane,) = _provider(successor, "google_books").lanes
    assert (lane.identity, lane.status) == (
        failed.identity,
        ProviderStatus.CANDIDATES,
    )
    assert successor.owner_link == source.owner_link
    assert admitted == []


def test_the_fresh_mark_is_written_in_the_successors_transaction(
    tmp_path: Path,
) -> None:
    source = _source(_book, _volume)
    path = tmp_path / "state.sqlite3"
    open_repository(path).close()
    work_id = preparation_work_id("primary", "ebooks", "item-1")
    # The work row stands alone here, with no scan row behind it, as the
    # owner-link store's own tests seed it.
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(
            "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,"
            "connection_name,library_name,library_id,item_id,file_identity,"
            "source_revision,source_fingerprint,producer_fingerprint,"
            "catalogue_generation,catalogue_fingerprint,stage,state,updated_at) "
            "VALUES(?,'scan',0,'primary','ebooks','lib','item-1',?,0,?,?,1,?,"
            "'complete','failed',0)",
            (work_id, "a" * 64, "c" * 64, "d" * 64, "e" * 64),
        )
        # The method reads only its connection; the repository's other
        # collaborators are not built for this one write.
        repository = cast(
            "SQLiteReviewPreparationRepository",
            SimpleNamespace(_connection=connection),
        )
        mark = SQLiteReviewPreparationRepository.mark_owner_link_work_fresh
        with pytest.raises(MetadataReportError):
            mark(repository, source)
        connection.execute("BEGIN IMMEDIATE")
        mark(repository, source)
        connection.execute("COMMIT")
        (fresh,) = connection.execute(
            "SELECT refresh_requested FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
    finally:
        connection.close()
    assert fresh == 1


class _Pipeline:
    def __init__(self, state: str | None, *, refuse: bool = False) -> None:
        self.state = state
        self.refuse = refuse
        self.requests: list[tuple[str, str]] = []

    def progress(self, connection: str, library: str) -> object:
        if self.state is None:
            return None
        return SimpleNamespace(state=self.state, rescan_requested=False)

    def request(self, connection: str, library: str) -> object:
        if self.refuse:
            raise ReviewPipelineError("persistence")
        self.requests.append((connection, library))
        return None


@pytest.mark.parametrize(
    ("state", "refuse", "requested"),
    [
        pytest.param(None, False, True, id="no-scan-yet"),
        pytest.param("completed", False, True, id="scan-finished"),
        pytest.param("queued", False, False, id="scan-owed"),
        pytest.param(None, True, False, id="refused-stays-marked"),
    ],
)
def test_an_admitted_link_asks_for_one_scan(
    state: str | None, refuse: bool, requested: bool
) -> None:
    pipeline = _Pipeline(state, refuse=refuse)
    _request_scan(cast("ReviewPreparationPipeline", pipeline), "primary", "ebooks")
    assert pipeline.requests == ([("primary", "ebooks")] if requested else [])
