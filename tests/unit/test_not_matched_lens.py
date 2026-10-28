"""The Not matched lens: works no provider matched, and why (#1407).

A work with no review case, nothing to decide and no admitted provider
evidence leaves No attention needed for a fourth lens. Each row names one
reason, and only a scheduled recovery job lets that reason promise a retry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.provider_recovery import (
    ProviderRecoveryJob,
    ProviderRecoveryStoreError,
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import (
    ProviderLaneResult,
    ReportRepository,
    provider_observation,
)
from mechanical_librarian.application.review_pipeline import PipelineAction
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    ReviewWebError,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    DispatchOutcome,
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.audiobook_providers import work_plan
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    audio_local_claims,
    audio_target_fields,
    recorded_dispatch,
    recording_lookup_hint_report,
    single_track_audio_facts,
)
from tests.support.proposal_evidence import (
    _claim as _proposal_claim,
)
from tests.support.proposal_evidence import (
    _provider as _proposal_provider,
)
from tests.support.proposal_evidence import (
    _report as _proposal_report,
)
from tests.support.review_cases import _report
from tests.support.review_web_doubles import (
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)

_LENSES = ("all", "needs_attention", "no_attention_needed", "not_matched")
_RECOVERY = "fiction"


def _for(report: MetadataReport, library: str, item: str) -> MetadataReport:
    """The same evidence, published for another work."""
    return MetadataReport.create(
        target=TargetFacts(
            report.target.connection, library, item, report.target.fields
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        cover_sources=report.cover_sources,
        audio=report.audio,
    )


def _ebook(item: str, *statuses: ProviderStatus) -> MetadataReport:
    """A caseless ebook whose providers ended in the given statuses."""
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", item, default_target_fields(f"Work {item}")
        ),
        file=FileFacts(0, "c" * 64, "d" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=tuple(
            ProviderObservation(f"provider-{index}", status)
            for index, status in enumerate(statuses)
        ),
    )


def _audio_lane(provider: str, authority: str, status: ProviderStatus):
    plan = work_plan(*audio_local_claims())
    return recorded_dispatch(
        provider_observation(
            provider,
            (ProviderLaneResult(plan.lanes[0], status),),
            authority=authority,
        )
    )


def _audiobook(item: str, *providers: ProviderObservation) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", item, audio_target_fields()),
        file=FileFacts(0, "e" * 64, "b" * 64, "m4b"),
        local_claims=work_plan(*audio_local_claims()).local_claims,
        local_reasons=("embedded_tag_duration",),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        audio=single_track_audio_facts(),
    )


def _refused(item: str, *others: ProviderObservation) -> MetadataReport:
    """An audiobook whose Audible ladder found products admission refused."""
    base = recording_lookup_hint_report()
    (lane,) = base.providers[0].lanes
    assert lane.dispatch is not None
    assert lane.dispatch.outcome is DispatchOutcome.REFUSED
    return _audiobook(item, *base.providers, *others)


def _empty_or_refused(item: str) -> MetadataReport:
    """A lane that kept no ladder, so it cannot tell a refusal from nothing."""
    observation = _audio_lane("audible", "audible_catalog", ProviderStatus.NO_MATCH)
    assert observation.lanes[0].dispatch is not None
    assert observation.lanes[0].dispatch.outcome is DispatchOutcome.EMPTY_OR_REFUSED
    return _audiobook(item, observation)


def _matched_clear(item: str) -> MetadataReport:
    """An ebook whose providers agreed, so automatic handling settled it."""
    providers = tuple(
        _proposal_provider(
            name,
            f"{name}-edition",
            (
                _proposal_claim(
                    name,
                    f"{name}-edition",
                    ClaimKind.TITLE,
                    "Corrected title",
                    scope=ClaimScope.WORK,
                ),
            ),
            authority=authority,
        )
        for name, authority in (
            ("hardcover_primary", "hardcover"),
            ("open_library_primary", "open_library"),
        )
    )
    return _for(_proposal_report(providers=providers), "ebooks", item)


def _needs_a_decision() -> MetadataReport:
    """A local title claim and no provider: a case with a question to ask."""
    return _for(_report("Needle", actionable=True), "ebooks", "needs-a-decision")


def _publish(
    repository: SQLiteMetadataReportRepository, report: MetadataReport
) -> None:
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)


def _items(page: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        cast(str, item["item"]): item
        for item in cast("list[dict[str, object]]", page["items"])
    }


def _reasoned_library(directory: Path) -> SQLiteMetadataReportRepository:
    repository = open_repository(directory / "application.sqlite3")
    for report in (
        _ebook("no-match", ProviderStatus.NO_MATCH, ProviderStatus.DISABLED),
        _ebook("local-only"),
        _ebook("unavailable", ProviderStatus.NO_MATCH, ProviderStatus.UNAVAILABLE),
        _refused("refused"),
        # Refusal outranks a provider that could not be reached.
        _refused(
            "refused-and-unavailable",
            _audio_lane("itunes", "itunes_search", ProviderStatus.UNAVAILABLE),
        ),
        _empty_or_refused("empty-or-refused"),
        _matched_clear("matched"),
    ):
        _publish(repository, report)
    return repository


_EXPECTED_STATES = {
    "no-match": "not_matched_no_match",
    "local-only": "not_matched_no_match",
    "unavailable": "not_matched_unavailable",
    "refused": "not_matched_refused",
    "refused-and-unavailable": "not_matched_refused",
    "empty-or-refused": "not_matched_no_match",
}


def test_each_not_matched_work_names_one_reason_in_precedence(tmp_path: Path) -> None:
    repository = _reasoned_library(tmp_path)
    try:
        browse = BrowseReviewLibrary(repository)
        page = browse.library(lens="not_matched", search=None, limit=50, cursor=None)
        items = _items(page)
        assert {item: value["summary"]["state"] for item, value in items.items()} == (
            _EXPECTED_STATES
        )
        assert all(
            value["attention_state"] == "not_matched"
            and value["review_case_id"] is None
            and value["attention_count"] == 0
            for value in items.values()
        )
        assert page["counts"] == {
            "all": 7,
            "needs_attention": 0,
            "no_attention_needed": 1,
            "not_matched": 6,
        }
        clear = browse.library(
            lens="no_attention_needed", search=None, limit=50, cursor=None
        )
        assert list(_items(clear)) == ["matched"]
        assert _items(clear)["matched"]["review_case_id"] is not None
    finally:
        repository.close()


def test_not_matched_is_disjoint_from_attention_and_partitions_all(
    tmp_path: Path,
) -> None:
    repository = _reasoned_library(tmp_path)
    try:
        _publish(repository, _needs_a_decision())
        browse = BrowseReviewLibrary(repository)
        seen: dict[str, set[str]] = {}
        for lens in _LENSES:
            page = browse.library(lens=lens, search=None, limit=50, cursor=None)
            seen[lens] = set(_items(page))
            assert page["counts"]["all"] == 8
        assert seen["needs_attention"] == {"needs-a-decision"}
        assert seen["no_attention_needed"] == {"matched"}
        assert seen["not_matched"] == set(_EXPECTED_STATES)
        assert seen["all"] == (
            seen["needs_attention"] | seen["no_attention_needed"] | seen["not_matched"]
        )
        # The Attention queue names only the work that has a question.
        queue = browse.attention(search=None, limit=50, cursor=None)
        decision = _items(
            browse.library(lens="needs_attention", search=None, limit=5, cursor=None)
        )["needs-a-decision"]
        assert {
            item["review_case_id"]
            for item in cast("list[dict[str, object]]", queue["items"])
        } == {decision["review_case_id"]}
    finally:
        repository.close()


def test_the_all_lens_groups_not_matched_works_between_attention_and_clear(
    tmp_path: Path,
) -> None:
    repository = _reasoned_library(tmp_path)
    try:
        _publish(repository, _needs_a_decision())
        page = BrowseReviewLibrary(repository).library(
            lens="all", search=None, limit=50, cursor=None
        )
        states = [
            cast(str, item["attention_state"])
            for item in cast("list[dict[str, object]]", page["items"])
        ]
        assert states == [
            "needs_attention",
            *["not_matched"] * 6,
            "no_attention_needed",
        ]
    finally:
        repository.close()


def test_a_cursor_is_bound_to_the_not_matched_lens(tmp_path: Path) -> None:
    repository = _reasoned_library(tmp_path)
    try:
        browse = BrowseReviewLibrary(repository)
        first = browse.library(lens="not_matched", search=None, limit=2, cursor=None)
        cursor = cast(str, first["next_cursor"])
        assert cursor is not None
        second = browse.library(lens="not_matched", search=None, limit=2, cursor=cursor)
        assert not set(_items(first)) & set(_items(second))
        for lens in ("all", "no_attention_needed"):
            with pytest.raises(ReviewWebError):
                browse.library(lens=lens, search=None, limit=2, cursor=cursor)
    finally:
        repository.close()


class _Schedule:
    def __init__(self, scheduled: frozenset[tuple[str, str, str, str]]) -> None:
        self.scheduled = scheduled
        self.calls: list[frozenset[tuple[str, str, str, str]]] = []

    def scheduled_recoveries(
        self, reports: frozenset[tuple[str, str, str, str]]
    ) -> frozenset[tuple[str, str, str, str]]:
        self.calls.append(reports)
        return self.scheduled & reports


def test_only_a_scheduled_recovery_promises_another_check(tmp_path: Path) -> None:
    repository = _reasoned_library(tmp_path)
    try:
        report_id = repository._connection.execute(
            "SELECT report_id FROM current_review_packages WHERE item_id='unavailable'"
        ).fetchone()[0]
        identity = ("primary", "ebooks", "unavailable", report_id)
        unscheduled = _Schedule(frozenset())
        scheduled = _Schedule(frozenset({identity}))
        states = {}
        for schedule in (None, unscheduled, scheduled):
            browse = BrowseReviewLibrary(repository, recovery=schedule)
            page = browse.library(
                lens="not_matched", search=None, limit=50, cursor=None
            )
            states[id(schedule)] = _items(page)["unavailable"]["summary"]["state"]
            # The promise is a page fact; it never moves a count.
            assert page["counts"]["not_matched"] == 6
        assert states[id(None)] == "not_matched_unavailable"
        assert states[id(unscheduled)] == "not_matched_unavailable"
        assert states[id(scheduled)] == "not_matched_unavailable_scheduled"
        # One read for the visible page, naming only its unavailable rows.
        assert unscheduled.calls == scheduled.calls == [frozenset({identity})]

        # A page with no unavailable row asks nothing.
        quiet = _Schedule(frozenset())
        BrowseReviewLibrary(repository, recovery=quiet).library(
            lens="no_attention_needed", search=None, limit=50, cursor=None
        )
        assert quiet.calls == []
    finally:
        repository.close()


def _recovery_job(item: str, report_id: str) -> ProviderRecoveryJob:
    """A job enrolled as enrollment does it: its member is the package report."""
    return ProviderRecoveryJob(
        "google",
        "google_books",
        "books",
        1,
        report_id,
        "primary",
        "ebooks",
        item,
        f"file-{item}",
        report_id,
        "proposal",
        report_id,
        "case",
        1,
        "admitted",
        '["work"]',
    )


def test_recovery_store_names_only_reports_a_live_job_will_check(
    tmp_path: Path,
) -> None:
    store = SQLiteProviderRecoveryStore.open(tmp_path / "recovery.sqlite")
    try:
        store.enqueue_jobs(
            (_recovery_job("done", "report-d"), _recovery_job("waiting", "report-w")),
            enqueued_at=3,
        )
        store._connection.execute(
            "UPDATE provider_recovery_jobs SET state='complete' "
            "WHERE member_id='report-d'"
        )
        waiting = ("primary", "ebooks", "waiting", "report-w")
        done = ("primary", "ebooks", "done", "report-d")
        stale_report = ("primary", "ebooks", "waiting", "report-old")
        absent = ("primary", "ebooks", "absent", "report-a")
        # The same report under another work is not that job's package.
        elsewhere = ("primary", "ebooks", "elsewhere", "report-w")
        assert store.scheduled_recoveries(
            frozenset({waiting, done, stale_report, absent, elsewhere})
        ) == frozenset({waiting})
        assert store.scheduled_recoveries(frozenset()) == frozenset()

        # A scope waiting for the owner, or out of attempts, promises nothing.
        for state in ("dormant", "exhausted"):
            store._connection.execute(
                "UPDATE provider_recovery_health SET state=?,next_eligible_at=NULL",
                (state,),
            )
            assert store.scheduled_recoveries(frozenset({waiting})) == frozenset()

        for invalid in (
            {waiting},
            frozenset({("primary", "ebooks", "waiting")}),
            frozenset({("primary", "ebooks", "", "report-w")}),
            frozenset({("primary", "ebooks", 7, "report-w")}),
            # Exact text only: a subclass of str is refused, not trusted.
            frozenset({("primary", "ebooks", type("Text", (str,), {})("w"), "r")}),
            frozenset(
                ("primary", "ebooks", f"item-{index}", "report") for index in range(257)
            ),
        ):
            with pytest.raises(ProviderRecoveryStoreError, match="query is invalid"):
                store.scheduled_recoveries(invalid)  # type: ignore[arg-type]
    finally:
        store.close()


def test_an_unreadable_recovery_store_withdraws_only_the_promise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recovery store that cannot answer leaves the page and its counts whole
    and promises no retry, writing a closed code and no values (#1407, #1447)."""
    repository = _reasoned_library(tmp_path)
    (tmp_path / "recovery").mkdir()
    store = SQLiteProviderRecoveryStore.open(tmp_path / "recovery" / "recovery.sqlite")
    try:
        report_id = repository._connection.execute(
            "SELECT report_id FROM current_review_packages WHERE item_id='unavailable'"
        ).fetchone()[0]
        store.enqueue_jobs((_recovery_job("unavailable", report_id),), enqueued_at=3)
        browse = BrowseReviewLibrary(repository, recovery=store)

        def state() -> tuple[object, object]:
            page = browse.library(
                lens="not_matched", search=None, limit=50, cursor=None
            )
            row = _items(page)["unavailable"]["summary"]
            return cast("dict[str, object]", row)["state"], page["counts"]

        scheduled, counts = state()
        assert scheduled == "not_matched_unavailable_scheduled"
        store._connection.execute("DROP TABLE provider_recovery_jobs")
        capsys.readouterr()
        assert state() == ("not_matched_unavailable", counts)
        store.close()
        assert state() == ("not_matched_unavailable", counts)
        written = capsys.readouterr().err
        assert (
            written.splitlines()
            == [
                '{"code":"unavailable","error":"ProviderRecoveryStoreError",'
                '"event":"provider_recovery_schedule","level":"warning"}'
            ]
            * 2
        )
        assert report_id not in written
    finally:
        store.close()
        repository.close()


def test_the_refused_lane_reads_as_a_refusal_on_its_report(tmp_path: Path) -> None:
    repository = _reasoned_library(tmp_path)
    try:
        browse = BrowseReviewLibrary(repository)
        refused = browse.report_detail("primary", "audio", "refused")
        plain = browse.report_detail("primary", "ebooks", "no-match")
        assert [
            (source["status"], source["reason"])
            for source in cast("list[dict[str, object]]", refused["provider_sources"])
        ] == [("no_match", "Found candidates, but none was this work.")]
        assert ("no_match", "Returned no matching metadata.") in [
            (source["status"], source["reason"])
            for source in cast("list[dict[str, object]]", plain["provider_sources"])
        ]
        # The Library row carries the same sentence for the same lane.
        row = _items(
            browse.library(lens="not_matched", search=None, limit=50, cursor=None)
        )["refused"]
        assert [
            outcome["reason"]
            for outcome in cast("list[dict[str, object]]", row["preparation_outcomes"])
        ] == ["Found candidates, but none was this work."]
    finally:
        repository.close()


@pytest.fixture
def web() -> Iterator[ReviewWebContext[FakeReviewRepository]]:
    yield from review_web_context(FakeReviewRepository)


def test_the_served_lens_agrees_with_the_store_double(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The HTTP surface serves the fourth lens through its closed models."""
    for report in (
        _ebook("no-match", ProviderStatus.NO_MATCH),
        _ebook("unavailable", ProviderStatus.UNAVAILABLE),
        _refused("refused"),
    ):
        web.repository.browse_without_cases.append(report)
    web.login()

    served = web.client.get("/api/v1/library?lens=not_matched")
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["schema_version"] == "review-library-v3"
    assert body["lens"] == "not_matched"
    assert body["counts"]["not_matched"] == 3
    assert {item["item"]: item["summary"]["state"] for item in body["items"]} == {
        "no-match": "not_matched_no_match",
        "unavailable": "not_matched_unavailable",
        "refused": "not_matched_refused",
    }
    clear = web.client.get("/api/v1/library?lens=no_attention_needed").json()
    assert not {item["item"] for item in clear["items"]} & set(_EXPECTED_STATES)
    assert (
        body["counts"]["all"]
        == body["counts"]["needs_attention"]
        + body["counts"]["no_attention_needed"]
        + body["counts"]["not_matched"]
    )


# --- preparation: masthead and run line ----------------------------------------


def _entry(item: str) -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        _RECOVERY,
        "lib-1",
        item,
        1,
        f"Recorded {item}",
        ("Example Author",),
        False,
        False,
        "singular-supported",
        "e" * 64,
        f"/{item}.m4b",
        "m4b",
        "audiobooks",
    )


def _completes_without_identity(
    action: PipelineAction, at: int
) -> Callable[[ReportRepository, MetadataReport], None]:
    """Finish an extraction on the outcome no admitted work identity leaves."""

    def complete(value: ReportRepository, published: MetadataReport) -> None:
        cast(
            SQLiteMetadataReportRepository, value
        ).complete_review_extraction_publication(
            action, published.report_id, at, 0, "evidence_only"
        )

    return complete


def test_preparation_counts_not_matched_by_the_library_predicate(
    tmp_path: Path,
) -> None:
    """The masthead, the run line and the lens count are one predicate."""
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        reports = {
            "local-only": _for(_audiobook("x"), _RECOVERY, "local-only"),
            "refused": _for(_refused("x"), _RECOVERY, "refused"),
            # Candidates came back, but no work identity was admitted.
            "unadmitted": _for(admitted_audiobook_report(), _RECOVERY, "unadmitted"),
        }
        entries = tuple(_entry(item) for item in sorted(reports))
        repository.request_review_scan("primary", _RECOVERY, "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 12)
        tick = 13
        while extraction := repository.claim_review_pipeline_action(tick):
            repository.publish(
                reports[cast(str, extraction.item_id)],
                lambda: True,
                None,
                _completes_without_identity(extraction, tick + 1),
            )
            tick += 2
        progress = repository.review_pipeline_progress("primary", _RECOVERY)
        assert progress is not None
        assert (progress.ready, progress.recorded, progress.not_matched) == (0, 0, 3)
        assert dict(progress.stages)["not_matched"] == 3
        assert dict(progress.stages)["recorded"] == 0
        page = BrowseReviewLibrary(repository).library(
            lens="not_matched", search=None, limit=50, cursor=None
        )
        assert page["counts"]["not_matched"] == progress.not_matched
        assert {
            item: value["summary"]["state"] for item, value in _items(page).items()
        } == {
            "local-only": "not_matched_no_match",
            "refused": "not_matched_refused",
            "unadmitted": "not_matched_refused",
        }
    finally:
        repository.close()


def test_a_terminal_preparation_failure_stays_where_it_was(tmp_path: Path) -> None:
    """#1413 owns failed preparation; this lens does not move it."""
    (tmp_path / "catalogue").mkdir(mode=0o700)
    catalogue = open_catalogue(tmp_path / "catalogue" / "catalogue.sqlite", create=True)
    entry = CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        "terminal-item",
        1,
        "Stopped terminal title",
        ("Terminal Writer",),
        True,
        False,
        "singular-supported",
        "1" * 64,
        "/synthetic.epub",
        "epub",
        "/media",
    )
    catalogue.acquire_refresh()
    generation = catalogue.publish("a" * 64, (entry,))
    repository = open_repository(tmp_path / "review.sqlite")
    try:
        repository.bind_library_catalogue(
            tmp_path / "catalogue" / "catalogue.sqlite", "a" * 64
        )
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, generation, "a" * 64, (entry,), 12
        )
        action = repository.claim_review_pipeline_action(13)
        assert action is not None
        repository.fail_review_pipeline_action(
            action, "unreadable_media", False, 14, "track_1"
        )
        page = BrowseReviewLibrary(repository).library(
            lens="no_attention_needed", search=None, limit=50, cursor=None
        )
        assert page["counts"] == {
            "all": 1,
            "needs_attention": 0,
            "no_attention_needed": 1,
            "not_matched": 0,
        }
        (item,) = cast("list[dict[str, object]]", page["items"])
        assert item["summary"]["state"] == "preparation_failed"
        progress = repository.review_pipeline_progress("primary", "fiction")
        assert progress is not None
        assert (progress.blocked, progress.not_matched) == (1, 0)
    finally:
        repository.close()
        catalogue.close()


def test_a_later_terminal_failure_over_an_unmatched_report_stays_where_it_was(
    tmp_path: Path,
) -> None:
    """The latest preparation failed, so the retained report is not Not matched.

    The row carries that failure, so it reads as the failure and is counted
    where a failed preparation always was, never under Not matched (#1407).
    """
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        entry = _entry("retained")
        repository.request_review_scan("primary", _RECOVERY, "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(discovery, 1, "a" * 64, (entry,), 12)
        extraction = repository.claim_review_pipeline_action(13)
        assert extraction is not None
        repository.publish(
            _for(_audiobook("x"), _RECOVERY, "retained"),
            lambda: True,
            None,
            _completes_without_identity(extraction, 14),
        )
        browse = BrowseReviewLibrary(repository)
        assert (
            browse.library(lens="all", search=None, limit=50, cursor=None)["counts"][
                "not_matched"
            ]
            == 1
        )

        # A rescan finds the file changed and its extraction fails for good,
        # over the report the first pass retained.
        repository.request_review_scan("primary", _RECOVERY, "lib-1", "f" * 64, 20)
        discovery = next(
            action
            for tick in range(21, 200)
            if (action := repository.claim_review_pipeline_action(tick))
        )
        repository.complete_review_discovery(
            discovery, 2, "b" * 64, (replace(entry, file_identity="9" * 64),), 201
        )
        failing = repository.claim_review_pipeline_action(202)
        assert failing is not None
        assert (failing.stage, failing.item_id) == ("extraction", "retained")
        repository.fail_review_pipeline_action(
            failing, "unreadable_media", False, 203, "track_1"
        )

        page = browse.library(
            lens="no_attention_needed", search=None, limit=50, cursor=None
        )
        assert page["counts"] == {
            "all": 1,
            "needs_attention": 0,
            "no_attention_needed": 1,
            "not_matched": 0,
        }
        (item,) = cast("list[dict[str, object]]", page["items"])
        assert item["attention_state"] == "no_attention_needed"
        assert item["preparation"] is not None
        assert item["summary"]["state"] == "no_decision_recorded"
        assert not browse.library(
            lens="not_matched", search=None, limit=50, cursor=None
        )["items"]
        progress = repository.review_pipeline_progress("primary", _RECOVERY)
        assert progress is not None
        assert (progress.blocked, progress.not_matched) == (1, 0)
    finally:
        repository.close()
