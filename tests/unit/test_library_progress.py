"""Library progress: Library's counts and Attention's fields per library (#1335).

The progress read counts works over the same candidate relation as the Library
page, with each lens's own condition, and fields over Attention's own count
statement, so its totals are Library's `counts` and its fields are Attention's
rows on the same state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
from collections import Counter
from collections.abc import Callable
from functools import partial
from itertools import repeat
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence import review_cases
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.support.proposal_evidence import _claim as _proposal_claim
from tests.support.proposal_evidence import _fields as _proposal_fields
from tests.support.proposal_evidence import _provider as _proposal_provider
from tests.support.proposal_evidence import _report as _proposal_report
from tests.support.review_cases import _report
from tests.unit import test_review_read_indexes as representative
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)

_LENSES = (
    "all",
    "needs_attention",
    "no_attention_needed",
    "not_matched",
    "preparation_failed",
)


def _at(source: MetadataReport, library: str, item: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            source.target.connection, library, item, source.target.fields
        ),
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )


def _admit(repository: SQLiteMetadataReportRepository, report: MetadataReport) -> None:
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)


def _narrator_conflict() -> MetadataReport:
    """An EPUB's conflicting narrators: excluded from attention (`review_sql`)."""
    return _proposal_report(
        title="Narrator conflict",
        fields=_proposal_fields(
            narrators=TargetField(TargetDestination.NARRATORS, TargetFieldState.MISSING)
        ),
        providers=tuple(
            _proposal_provider(
                provider,
                edition,
                (
                    _proposal_claim(
                        provider,
                        edition,
                        ClaimKind.CONTRIBUTOR,
                        narrator,
                        scope=ClaimScope.EDITION,
                        role="narrator",
                    ),
                ),
            )
            for provider, edition, narrator in (
                ("catalogue-a", "edition-a", "Nora Narrator"),
                ("catalogue-b", "edition-b", "Pia Narrator"),
            )
        ),
    )


def _three_libraries(root: Path) -> SQLiteMetadataReportRepository:
    """The Library-counts fixture (#1284), plus a second library of cases.

    `ebooks` holds a work needing attention and one no provider matched,
    `fiction` a book whose preparation stopped before its report, and `shelf`
    an EPUB whose only conflict is its narrators, which needs no attention,
    beside a work whose series does.
    """
    repository = open_repository(root / "review.sqlite")
    for report in (
        _at(_report("Needle attention work", actionable=True), "ebooks", "attention"),
        _at(_report("Settled work", "c", "d", actionable=False), "ebooks", "settled"),
        _at(_narrator_conflict(), "shelf", "narrator-conflict"),
        _at(
            _bibliographic_report(
                current_title="Mistborn Secret History",
                providers=(
                    _bibliographic_source(
                        "catalogue", "edition-1", titles=(), series="Mistborn"
                    ),
                ),
            ),
            "shelf",
            "series-attention",
        ),
    ):
        _admit(repository, report)
    catalogue = open_catalogue(root / "catalogue.sqlite", create=True)
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
    repository.bind_library_catalogue(root / "catalogue.sqlite", "a" * 64)
    repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, generation, "a" * 64, (entry,), 12)
    action = repository.claim_review_pipeline_action(13)
    assert action is not None
    repository.fail_review_pipeline_action(
        action, "unreadable_media", False, 14, "track_1"
    )
    return repository


def test_progress_totals_are_library_counts_and_its_fields_attention_rows(
    tmp_path: Path,
) -> None:
    repository = _three_libraries(tmp_path)
    try:
        browser = BrowseReviewLibrary(repository)
        labels = (
            ("primary", "shelf", "Second shelf"),
            ("primary", "ebooks", "E-books"),
        )
        progress = browser.progress(labels)
        totals = progress["totals"]
        assert totals == {
            "all": 5,
            "needs_attention": 2,
            "no_attention_needed": 1,
            "not_matched": 1,
            "preparation_failed": 1,
        }
        # Equal to every Library answer's counts, whatever its lens and page.
        for lens in _LENSES:
            for limit in (1, 25):
                assert (
                    browser.library(lens=lens, search=None, limit=limit, cursor=None)[
                        "counts"
                    ]
                    == totals
                ), (lens, limit)
        # Settings order, then the stable name no setting configures; a library
        # without a display name reads as its stable name.
        libraries = cast(list[dict[str, object]], progress["libraries"])
        assert [
            (row["library"], row["library_display_name"], row["counts"])
            for row in libraries
        ] == [
            (
                "shelf",
                "Second shelf",
                {
                    "all": 2,
                    "needs_attention": 1,
                    "no_attention_needed": 1,
                    "not_matched": 0,
                    "preparation_failed": 0,
                },
            ),
            (
                "ebooks",
                "E-books",
                {
                    "all": 2,
                    "needs_attention": 1,
                    "no_attention_needed": 0,
                    "not_matched": 1,
                    "preparation_failed": 0,
                },
            ),
            (
                "fiction",
                "fiction",
                {
                    "all": 1,
                    "needs_attention": 0,
                    "no_attention_needed": 0,
                    "not_matched": 0,
                    "preparation_failed": 1,
                },
            ),
        ]
        for lens in _LENSES:
            assert (
                sum(cast(dict[str, int], row["counts"])[lens] for row in libraries)
                == cast(dict[str, int], totals)[lens]
            )
        # Each field counts the works Attention lists under it; the EPUB's
        # conflicting narrators are in neither.
        attention = browser.attention(search=None, limit=50, cursor=None)
        listed = Counter(
            str(item["destination"])
            for item in cast(list[dict[str, object]], attention["items"])
        )
        assert progress["fields"] == [
            {"destination": "series", "need_you": 1},
            {"destination": "title", "need_you": 1},
        ]
        assert listed == {"series": 1, "title": 1}
        # Nothing names a work: counts and library names only.
        text = json.dumps(progress)
        for private in (
            "settled",
            "narrator-conflict",
            "series-attention",
            "terminal-item",
            "Needle",
            "Settled",
            "Stopped",
            "Mistborn",
            "lib-1",
            "review_case",
            "report",
        ):
            assert private not in text, private
    finally:
        repository.close()


def test_progress_refuses_counts_whose_lenses_do_not_partition_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stopped book counted as settled too would make the lenses overlap."""
    repository = _three_libraries(tmp_path)
    try:
        assert repository.read_review_library_progress(()).libraries
        monkeypatch.setitem(
            review_cases._LIBRARY_LENS_CONDITIONS,
            "no_attention_needed",
            "attention=0 AND match_reason IS NULL",
        )
        with pytest.raises(MetadataReportError, match="review library is unavailable"):
            repository.read_review_library_progress(())
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("corruption", "statement"),
    (
        ("negative", "library"),
        ("boolean", "library"),
        ("over_attention", "field"),
        ("unknown_library", "field"),
    ),
)
def test_progress_refuses_an_invalid_row(
    tmp_path: Path, corruption: str, statement: str
) -> None:
    repository = _three_libraries(tmp_path)
    reached = False

    def corrupt(cursor: sqlite3.Cursor, row: tuple[object, ...]) -> tuple[object, ...]:
        nonlocal reached
        width = 7 if statement == "library" else 4
        if len(cursor.description) != width:
            return row
        reached = True
        values = list(row)
        if corruption == "negative":
            values[4] = -1
        elif corruption == "boolean":
            values[2] = True
        elif corruption == "over_attention":
            values[3] = 2
        else:
            values[1] = "elsewhere"
        return tuple(values)

    repository._connection.row_factory = corrupt
    try:
        with pytest.raises(MetadataReportError, match="review library is unavailable"):
            repository.read_review_library_progress(())
        assert reached
    finally:
        repository._connection.row_factory = None
        repository.close()


# The Library page statement as it was before #1335 extracted its candidate
# relation: the trace of each read below, as its SHA-256 at the base commit.
_PAGE_STATEMENT_DIGESTS = {
    (False, None): "cc942ce8b5ffa7c29456e893b765645202b2b9cda86e81333039304fc7f7b7b6",
    (False, "needs_attention"): (
        "a75d30c43b3e52a2c49b1f9d8e7b3ad92092b70509987d3a2ce990100ad21a1a"
    ),
    (True, None): "945725359c9ce89a0f6adc2b2c3beefa22526c65815ed11c5815d731f9e433f2",
    (True, "needs_attention"): (
        "806bc20d398f378b4c3f87d87390146644f6ab53919b5e511c258deadb38df11"
    ),
}


def _traced(
    repository: SQLiteMetadataReportRepository, read: Callable[[], object]
) -> tuple[str, ...]:
    statements: list[str] = []
    repository._connection.set_trace_callback(statements.append)
    try:
        read()
    finally:
        repository._connection.set_trace_callback(None)
    return tuple(statement for statement in statements if statement.startswith("WITH "))


def test_the_library_page_statement_is_unchanged_and_shares_its_candidates(
    tmp_path: Path,
) -> None:
    repository = open_repository(tmp_path / "review.sqlite")
    try:
        for attached in (False, True):
            if attached:
                catalogue = open_catalogue(tmp_path / "catalogue.sqlite", create=True)
                catalogue.acquire_refresh()
                catalogue.publish("a" * 64, ())
                repository.bind_library_catalogue(
                    tmp_path / "catalogue.sqlite", "a" * 64
                )
            for lens in (None, "needs_attention"):
                (page,) = _traced(
                    repository,
                    partial(repository.read_review_library, None, 7, 3, lens),
                )
                assert (
                    hashlib.sha256(page.encode()).hexdigest()
                    == _PAGE_STATEMENT_DIGESTS[(attached, lens)]
                ), (attached, lens)
            (progress,) = _traced(
                repository, lambda: repository.read_review_library_progress(())
            )
            # The progress statement selects from the page's own relation.
            shared = page.index(",totals AS (")
            assert progress.startswith(page[:shared] + " SELECT connection_name,")
    finally:
        repository.close()


def test_field_counts_are_attention_rows_on_the_representative_store() -> None:
    with tempfile.TemporaryDirectory() as directory:
        connection = representative._representative_database(
            Path(directory) / "application.sqlite3", works=24
        )
        repository = SQLiteMetadataReportRepository(connection)
        try:
            progress = repository.read_review_library_progress(())
            fields = Counter[str]()
            for _connection, _library, destination, works in progress.fields:
                fields[destination] += works
            rows = repository.read_current_attention_page(None, 10_000, 0).rows
            assert fields == Counter(row[2] for row in rows)
            assert fields.total() == repository.count_current_attention_fields(None)
            assert fields.total() > 0
            # Library's own attention lens agrees with the work counts.
            library = BrowseReviewLibrary(repository).library(
                lens="all", search=None, limit=5, cursor=None
            )
            assert (
                BrowseReviewLibrary(repository).progress()["totals"]
                == (library["counts"])
            )
        finally:
            repository.close()


def test_a_cover_field_counts_only_while_its_live_authority_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attention's live cover term decides a cover field, as on Attention."""
    # Held covers ask; suggested ones would not be attention (#1363 S9).
    from mechanical_librarian.application import cover_policy
    from tests.support.cover_producers import held_measured_stage

    monkeypatch.setattr(cover_policy, "elect_measured_covers", held_measured_stage)
    fixture = OwnerResponsivenessFixture(tmp_path, 30, mixed=True)
    try:
        repository = fixture.repository
        policy, replay = repository._cover_read_authority()
        assert policy is not None and replay is not None
        another = "cover_policy_" + hashlib.sha256(b"another policy").hexdigest()

        def fields() -> Counter[str]:
            counted = Counter[str]()
            for (
                *_library,
                destination,
                works,
            ) in repository.read_current_library_progress(()).fields:
                counted[destination] += works
            listed = Counter(
                row[2]
                for row in repository.read_current_attention_page(None, 10_000, 0).rows
            )
            assert counted == listed
            assert counted.total() == repository.count_current_attention_fields(None)
            return counted

        repository.bind_cover_read_observation(
            repeat(((policy, replay), frozenset())).__next__
        )
        with repository.browse_observation():
            assert fields()["cover"] > 0
        repository.bind_cover_read_observation(
            repeat(((another, replay), frozenset())).__next__
        )
        with repository.browse_observation():
            assert fields()["cover"] == 0
            # Without the live term every stored cover fact would count, more
            # than the works Library has in attention, and the read refuses.
            monkeypatch.setattr(
                review_cases, "_ATTENTION_FACT_CURRENT_SQL", "(1 OR ?=?)"
            )
            with pytest.raises(MetadataReportError):
                repository.read_current_library_progress(())
    finally:
        fixture.close()


def test_progress_reads_its_two_statements_on_one_snapshot() -> None:
    """A decision committed between the work and field statements is unseen."""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        reader = open_repository(database)
        report = _report()
        reader.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        reader.publish_proposal(proposal_set)
        case_id = json.loads(reader.admit_review_case(proposal_set))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(reader.get_review_case(case_id))["case"]
        )
        proposal = next(
            value
            for value in case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )
        decision = HumanDecision.create(
            case,
            proposal_id=cast(str, proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=cast(str, proposal["destination"]),
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="progress-snapshot-decision",
            occurred_at=1_000,
        )
        committed = threading.Event()
        errors: list[BaseException] = []

        def write_decision() -> None:
            writer = None
            try:
                connection = sqlite3.connect(
                    database if reader._binding is None else reader._binding.alias_path,
                    isolation_level=None,
                )
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                writer = SQLiteMetadataReportRepository(connection)
                writer.record_human_decision(decision)
            except BaseException as error:
                errors.append(error)
            finally:
                if writer is not None:
                    writer.close()
                committed.set()

        started = False

        def between(statement: str) -> None:
            # The field statement is about to run: the work statement has read.
            nonlocal started
            if started or not statement.startswith("SELECT c.connection_name"):
                return
            started = True
            threading.Thread(target=write_decision, daemon=True).start()
            assert committed.wait(5)

        reader._connection.set_trace_callback(between)
        try:
            before = reader.read_review_library_progress(())
        finally:
            reader._connection.set_trace_callback(None)
        assert started and errors == []
        assert before.libraries == (("primary", "ebooks", 1, 1, 0, 0),)
        assert before.fields == (("primary", "ebooks", "title", 1),)
        after = reader.read_review_library_progress(())
        assert after.libraries == (("primary", "ebooks", 1, 0, 0, 0),)
        assert after.fields == ()
        reader.close()
