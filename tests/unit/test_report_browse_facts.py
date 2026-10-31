"""Persisted report browse facts (#1199): projection, refusal and equality.

Library, Attention, search and their counts read title, format, author and
provider-limited facts from `metadata_report_browse_facts` and
`metadata_report_browse_authors`, and each proposal's resolved policy from
`proposal_set_browse_facts`, instead of every participating report and
proposal document. The `_ORACLE_*` statements below are those four statement families
exactly as they read before #1199, kept verbatim so every equality test here
compares the persisted-fact statements with the document-reading ones they
replaced on the same database.
"""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _AUTOMATIC_SAFETY_ATTENTION_SQL,
    _CURRENT_LIBRARY_FROM_SQL,
    _CURRENT_REVIEW_FROM_SQL,
    _EPUB_INAPPLICABLE_ATTENTION_SQL,
    _LIBRARY_DISPLAY_SEARCH_SQL,
    _POLICY_ASK_ATTENTION_SQL,
    _REVIEW_ATTENTION_SQL,
    _REVIEW_FIELD_ATTENTION_SQL,
    _TERMINAL_LIBRARY_SEARCH_SQL,
    _terminal_library_sql,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import LibraryLensFilter
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.audiobook_reports import admitted_audiobook_report
from tests.support.provider_recovery_rail import provider_failure_report
from tests.support.review_cases import _report

# --- Equality oracle: the statements as they read before #1199 (verbatim). ---

_ORACLE_REVIEW_SEARCH_SQL = (
    " AND (? IS NULL OR "
    "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')) "
    "LIKE ? ESCAPE '\\' OR ml_casefold(ml.item_id) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(ml.library_name) LIKE ? ESCAPE '\\' "
    "OR EXISTS (SELECT 1 FROM json_each(mr.document,'$.target.fields') author_field, "
    "json_each(author_field.value,'$.value') author WHERE "
    "json_extract(author_field.value,'$.destination')='authors' "
    "AND ml_casefold(COALESCE(author.value,'')) LIKE ? ESCAPE '\\') "
    "OR ml_casefold(COALESCE(json_extract(mr.document,'$.file.format'),'')) "
    "LIKE ? ESCAPE '\\' OR " + _LIBRARY_DISPLAY_SEARCH_SQL + ")"
)
_ORACLE_CURRENT_REVIEW_CASE_COUNT_SQL = (
    "SELECT count(*),COALESCE(sum(CASE WHEN "
    + _REVIEW_ATTENTION_SQL
    + " THEN 1 ELSE 0 END),0) "
    + _CURRENT_LIBRARY_FROM_SQL
    + " WHERE COALESCE(c.outcome,'')<>'tombstoned'"
    + _ORACLE_REVIEW_SEARCH_SQL
)


def _oracle_browse_scalar_sql(sql: str) -> str:
    return sql.replace(
        "json_extract(mr.document,'$.file.format')", "scalars.format"
    ).replace("json_extract(mr.document,'$.target.fields[0].value')", "scalars.title")


# #1407: an unmatched package's reason, read from its report document, with
# the lanes a provider status falls back to exactly as the limited flag reads.
_ORACLE_LANES_SQL = (
    "FROM json_each(mr.document,'$.providers') provider "
    "LEFT JOIN json_each(provider.value,'$.lanes') lane ON 1 WHERE "
)
_ORACLE_STATUS_SQL = (
    "COALESCE(json_extract(lane.value,'$.status'),"
    "json_extract(provider.value,'$.status'))"
)
_ORACLE_CANDIDATES_SQL = (
    "EXISTS(SELECT 1 " + _ORACLE_LANES_SQL + _ORACLE_STATUS_SQL + "='candidates')"
)
_ORACLE_MATCH_REASON_SQL = (
    "CASE WHEN ml.review_case_id IS NULL AND (ml.outcome='evidence_only' OR NOT "
    + _ORACLE_CANDIDATES_SQL
    + ") THEN CASE WHEN "
    + _ORACLE_CANDIDATES_SQL
    + " THEN 'refused' WHEN EXISTS(SELECT 1 "
    + _ORACLE_LANES_SQL
    + "json_extract(lane.value,'$.status')='no_match' AND "
    "(json_extract(lane.value,'$.dispatch.outcome')='refused' OR EXISTS("
    "SELECT 1 FROM json_each(lane.value,'$.diagnostics') diagnostic WHERE "
    "json_extract(diagnostic.value,'$.category')='work_identity_refused'))) "
    "THEN 'refused' WHEN EXISTS(SELECT 1 "
    + _ORACLE_LANES_SQL
    + _ORACLE_STATUS_SQL
    + " IN ('authentication','authorization','rate_limited','unavailable',"
    "'incomplete','malformed','oversized','candidate_limit_exceeded',"
    "'query_limit_exceeded')) THEN 'unavailable' ELSE 'no_match' END END"
)
_LENSES: tuple[LibraryLensFilter | None, ...] = (
    None,
    "needs_attention",
    "no_attention_needed",
    "not_matched",
    "preparation_failed",
)
_ORACLE_LENS_CONDITIONS = {
    None: "1",
    "needs_attention": "attention=1",
    "no_attention_needed": "attention=0 AND match_reason IS NULL AND failed=0",
    "not_matched": "attention=0 AND match_reason IS NOT NULL",
    "preparation_failed": "attention=0 AND failed=1",
}
# Whether the work's latest preparation failed for good (#1413).
_ORACLE_FAILED_SQL = (
    "CASE WHEN (SELECT w.state='failed' AND w.failure_code IS NOT NULL "
    "FROM review_preparation_work w WHERE w.connection_name=ml.connection_name "
    "AND w.library_name=ml.library_name AND w.item_id=ml.item_id "
    "ORDER BY w.updated_at DESC,w.work_id DESC LIMIT 1) THEN 1 ELSE 0 END"
)


def _oracle_library_sql(attached: bool, condition: str) -> str:
    terminal_union = (
        " UNION ALL SELECT NULL,NULL,NULL,connection_name,library_name,"
        "item_id,NULL,0,0,NULL,1,ml_casefold(display_title),'',work_id,attempt,"
        "stage,failure_root,recovery_attempt,failure_code,"
        "display_title,display_authors,display_format FROM terminal"
        + _TERMINAL_LIBRARY_SEARCH_SQL
    )
    return (
        "WITH "
        + _terminal_library_sql(attached)
        + ", report_scalars AS MATERIALIZED (SELECT mr.report_id,"
        "json_extract(mr.document,'$.target.fields[0].value') AS title,"
        "json_extract(mr.document,'$.file.format') AS format "
        "FROM metadata_reports mr JOIN (SELECT DISTINCT report_id "
        "FROM current_review_packages) participating USING(report_id)),"
        "candidates AS MATERIALIZED (SELECT rc.review_case_id AS case_id,"
        "mr.report_id,"
        "CASE WHEN "
        "rc.proposal_set_id=ml.proposal_set_id AND rc.report_id=ml.report_id "
        "THEN rc.proposal_set_id ELSE NULL END AS proposal_set_id,"
        "ml.connection_name AS connection_name,ml.library_name AS library_name,"
        "ml.item_id AS item_id,c.outcome AS outcome,"
        "CASE WHEN "
        + _oracle_browse_scalar_sql(_REVIEW_ATTENTION_SQL)
        + " THEN 1 ELSE 0 END AS attention,"
        "CASE WHEN EXISTS(SELECT 1 FROM json_each(mr.document,'$.providers') provider "
        "LEFT JOIN json_each(provider.value,'$.lanes') lane ON 1 "
        "WHERE COALESCE(json_extract(lane.value,'$.status'),"
        "json_extract(provider.value,'$.status')) IN ("
        "'authentication','authorization','rate_limited','unavailable',"
        "'incomplete','malformed','oversized','candidate_limit_exceeded',"
        "'query_limit_exceeded')) THEN 1 ELSE 0 END AS provider_limited,"
        + _ORACLE_MATCH_REASON_SQL
        + " AS match_reason,"
        + _ORACLE_FAILED_SQL
        + " AS failed,"
        "ml_casefold(COALESCE(scalars.title,'')) AS title_order,"
        "COALESCE(rc.review_case_id,'') AS case_order,"
        "NULL AS work_id,NULL AS attempt,NULL AS stage,NULL AS failure_root,"
        "NULL AS recovery_attempt,NULL AS failure_code, NULL AS display_title,"
        "NULL AS display_authors,NULL AS display_format "
        + _CURRENT_LIBRARY_FROM_SQL
        + " JOIN report_scalars scalars ON scalars.report_id=mr.report_id "
        " WHERE COALESCE(c.outcome,'')<>'tombstoned'"
        + _oracle_browse_scalar_sql(_ORACLE_REVIEW_SEARCH_SQL)
        + terminal_union
        + "),totals AS (SELECT count(*) AS total,COALESCE(sum(attention),0) "
        "AS attention_total,COALESCE(sum(CASE WHEN attention=0 AND match_reason "
        "IS NOT NULL THEN 1 ELSE 0 END),0) AS not_matched_total,"
        "COALESCE(sum(CASE WHEN attention=0 AND failed=1 THEN 1 ELSE 0 END),0) "
        "AS failed_total FROM candidates),"
        "page AS MATERIALIZED (SELECT "
        "1 AS present,case_id,report_id,proposal_set_id,"
        "connection_name,library_name,item_id,outcome,"
        "CASE WHEN attention=1 THEN 0 ELSE 1 END AS attention_order,"
        "CASE WHEN attention=1 AND provider_limited=1 THEN 1 ELSE 0 END "
        "AS provider_order,CASE WHEN attention=0 AND failed=1 THEN 0 "
        "WHEN attention=0 AND match_reason IS NOT NULL "
        "THEN 1 ELSE 2 END AS match_order,match_reason,title_order,case_order,"
        "work_id,attempt,stage,"
        "failure_root,recovery_attempt,failure_code,display_title,display_authors,display_format FROM candidates WHERE "
        + condition
        + " ORDER BY attention_order,provider_order,match_order,title_order,"
        "library_name,item_id,connection_name,case_order LIMIT ? OFFSET ?) "
        "SELECT rc.document,CASE WHEN page.present=1 AND page.report_id IS NULL THEN json_object("
        "'attempt',page.attempt,'connection',page.connection_name,"
        "'failure_root',page.failure_root,'item',page.item_id,'lane',page.stage,"
        "'library',page.library_name,'recovery_attempt',page.recovery_attempt,"
        "'schema_version','review-terminal-work-v1','status',page.failure_code,"
        "'work_id',page.work_id,'title',page.display_title,'authors',json(page.display_authors),"
        "'media_kind',page.display_format) ELSE mr.document END,page.proposal_set_id,"
        "page.connection_name,page.library_name,page.item_id,page.outcome, "
        "COALESCE(page.present,0),totals.total,totals.attention_total,"
        "page.match_reason,totals.not_matched_total,totals.failed_total "
        "FROM totals LEFT JOIN page ON 1 LEFT JOIN review_cases rc ON rc.review_case_id=page.case_id "
        "LEFT JOIN metadata_reports mr ON mr.report_id=page.report_id "
        "ORDER BY page.attention_order,page.provider_order,page.match_order,"
        "page.title_order,page.library_name,page.item_id,page.connection_name,"
        "page.case_order"
    )


_ORACLE_ATTENTION_SQL = (
    "WITH report_scalars AS MATERIALIZED (SELECT mr.report_id,"
    "json_extract(mr.document,'$.target.fields[0].value') AS title,"
    "json_extract(mr.document,'$.file.format') AS format "
    "FROM metadata_reports mr JOIN (SELECT DISTINCT rc.report_id "
    "FROM current_review_packages c JOIN review_cases rc "
    "ON rc.review_case_id=c.review_case_id) participating USING(report_id)),"
    "search_matches AS MATERIALIZED (SELECT "
    "c.connection_name,c.library_name,c.item_id "
    + _CURRENT_REVIEW_FROM_SQL
    + " JOIN report_scalars scalars ON scalars.report_id=mr.report_id WHERE 1"
    + _oracle_browse_scalar_sql(_ORACLE_REVIEW_SEARCH_SQL)
    + "),candidates AS MATERIALIZED (SELECT rc.review_case_id AS review_case_id,"
    "mr.report_id AS report_id,CASE WHEN "
    "rc.proposal_set_id=c.proposal_set_id AND rc.report_id=c.report_id "
    "THEN rc.proposal_set_id ELSE NULL END AS proposal_set_id,"
    "result.destination AS destination,"
    "CASE WHEN "
    + _oracle_browse_scalar_sql(_REVIEW_FIELD_ATTENTION_SQL)
    + _oracle_browse_scalar_sql(_EPUB_INAPPLICABLE_ATTENTION_SQL)
    + " THEN 1 ELSE 0 END AS attention,"
    "ml_casefold(COALESCE(scalars.title,'')) "
    "AS title_order,c.library_name AS library_name,c.item_id AS item_id "
    + _CURRENT_REVIEW_FROM_SQL
    + " JOIN report_scalars scalars ON scalars.report_id=mr.report_id "
    " JOIN review_case_field_facts result "
    "ON result.review_case_id=rc.review_case_id "
    "WHERE (c.connection_name,c.library_name,c.item_id) IN "
    "(SELECT connection_name,library_name,item_id FROM search_matches)"
    + "),attentive AS MATERIALIZED (SELECT review_case_id,report_id,"
    "proposal_set_id,destination,count(*) OVER (PARTITION BY review_case_id) "
    "AS attention_count,title_order,library_name,item_id "
    "FROM candidates WHERE attention=1),totals AS (SELECT count(*) AS total "
    "FROM attentive),page AS MATERIALIZED (SELECT 1 AS present,* "
    "FROM attentive" + " ORDER BY "
    "title_order,library_name,item_id,destination,review_case_id "
    "LIMIT ? OFFSET ?) "
    "SELECT rc.document,mr.document,page.proposal_set_id,page.destination,"
    "page.attention_count,"
    "(SELECT outcome.disposition FROM shadow_decision_outcomes shadow "
    "JOIN shadow_outcome_field_facts outcome "
    "ON outcome.outcome_set_id=shadow.outcome_set_id "
    "WHERE outcome.review_case_id=rc.review_case_id "
    "AND outcome.destination=result.destination "
    "ORDER BY shadow.evaluated_at DESC,shadow.outcome_set_id DESC LIMIT 1) "
    "AS disposition,"
    "(CASE WHEN "
    + _AUTOMATIC_SAFETY_ATTENTION_SQL
    + " THEN 'automatic_safety_suspended' ELSE NULL END) AS cause,"
    "CASE WHEN " + _POLICY_ASK_ATTENTION_SQL + " THEN 1 ELSE 0 END AS policy_attention,"
    "COALESCE(page.present,0),totals.total FROM totals LEFT JOIN page ON 1 "
    "LEFT JOIN review_cases rc ON rc.review_case_id=page.review_case_id "
    "LEFT JOIN metadata_reports mr ON mr.report_id=page.report_id "
    "LEFT JOIN review_case_field_facts result "
    "ON result.review_case_id=page.review_case_id AND result.destination=page.destination "
    "ORDER BY page.title_order,page.library_name,page.item_id,"
    "page.destination,page.review_case_id"
)
_ORACLE_ATTENTION_COUNT_SQL = (
    "SELECT count(*) "
    + _CURRENT_REVIEW_FROM_SQL
    + " JOIN review_case_field_facts result "
    "ON result.review_case_id=rc.review_case_id WHERE "
    + _REVIEW_FIELD_ATTENTION_SQL
    + _EPUB_INAPPLICABLE_ATTENTION_SQL
    + _ORACLE_REVIEW_SEARCH_SQL
)


def _pattern(search: str | None) -> tuple[str | None, str]:
    if search is None:
        return None, "%"
    escaped = (
        search.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return search, f"%{escaped}%"


def _search_parameters(
    search: str | None, labels: tuple[tuple[str, str, str], ...]
) -> tuple[object, ...]:
    query, pattern = _pattern(search)
    return (query, *(pattern,) * 5, json.dumps(labels), pattern)


def _catalogue_parameters(repository: SQLiteMetadataReportRepository) -> list[object]:
    catalogue = repository._review_cases.catalogue
    return [catalogue.fingerprint] if catalogue is not None and catalogue.active else []


def _oracle_library(
    repository: SQLiteMetadataReportRepository,
    search: str | None,
    limit: int,
    offset: int,
    lens: LibraryLensFilter | None,
    labels: tuple[tuple[str, str, str], ...],
) -> tuple[
    tuple[tuple[str | None, str], ...], int, int, int, tuple[str | None, ...], int
]:
    condition = _ORACLE_LENS_CONDITIONS[lens]
    catalogue = _catalogue_parameters(repository)
    rows = repository._connection.execute(
        _oracle_library_sql(bool(catalogue), condition),
        (
            *catalogue,
            *repository._cover_read_authority(),
            *_search_parameters(search, labels),
            *_search_parameters(search, labels),
            limit,
            offset,
        ),
    ).fetchall()
    values: list[tuple[str | None, str]] = []
    reasons: list[str | None] = []
    for row in rows:
        if row[7] == 0:
            continue
        reasons.append(row[10])
        if row[0] is None and row[6] is None:
            terminal = json.loads(row[1])
            if terminal.get("schema_version") == "review-terminal-work-v1":
                values.append((None, _canonical_json(terminal)))
                continue
        values.append((row[0], row[1]))
    return (
        tuple(values),
        rows[0][8],
        rows[0][9],
        rows[0][11],
        tuple(reasons),
        rows[0][12],
    )


def _oracle_attention(
    repository: SQLiteMetadataReportRepository,
    search: str | None,
    limit: int,
    offset: int,
    labels: tuple[tuple[str, str, str], ...],
) -> tuple[tuple[object, ...], int]:
    rows = repository._connection.execute(
        _ORACLE_ATTENTION_SQL,
        (
            *_search_parameters(search, labels),
            *repository._cover_read_authority(),
            limit,
            offset,
        ),
    ).fetchall()
    return (
        tuple(
            (row[0], row[1], row[3], row[4], row[5], row[6], bool(row[7]))
            for row in rows
            if row[8] == 1
        ),
        rows[0][9],
    )


def _oracle_counts(
    repository: SQLiteMetadataReportRepository,
    search: str | None,
    labels: tuple[tuple[str, str, str], ...],
) -> tuple[tuple[int, int], int]:
    connection = repository._connection
    authority = repository._cover_read_authority()
    library = connection.execute(
        _ORACLE_CURRENT_REVIEW_CASE_COUNT_SQL,
        (*authority, *_search_parameters(search, labels)),
    ).fetchone()
    catalogue = _catalogue_parameters(repository)
    terminal = connection.execute(
        "WITH "
        + _terminal_library_sql(bool(catalogue))
        + " SELECT count(*) FROM terminal"
        + _TERMINAL_LIBRARY_SEARCH_SQL,
        (*catalogue, *_search_parameters(search, labels)),
    ).fetchone()
    attention = connection.execute(
        _ORACLE_ATTENTION_COUNT_SQL,
        (*authority, *_search_parameters(search, labels)),
    ).fetchone()
    return (library[0] + terminal[0], library[1]), attention[0]


def _assert_equal_to_oracle(
    repository: SQLiteMetadataReportRepository,
    searches: tuple[str | None, ...],
    pages: tuple[tuple[int, int], ...],
    labels: tuple[tuple[str, str, str], ...],
) -> int:
    """Compare every page, total and count with the pre-#1199 statements."""
    compared = 0
    for search in searches:
        assert (
            repository.count_current_review_cases(search, labels),
            repository.count_current_attention_fields(search, labels),
        ) == _oracle_counts(repository, search, labels), search
        for limit, offset in pages:
            for lens in _LENSES:
                page = repository.read_current_library_page(
                    search, limit, offset, lens, labels
                )
                assert (
                    page.rows,
                    page.total,
                    page.attention_total,
                    page.not_matched_total,
                    page.match_reasons,
                    page.preparation_failed_total,
                ) == _oracle_library(repository, search, limit, offset, lens, labels), (
                    search,
                    limit,
                    offset,
                    lens,
                )
                compared += 1
            attention = repository.read_current_attention_page(
                search, limit, offset, labels
            )
            assert (attention.rows, attention.total) == _oracle_attention(
                repository, search, limit, offset, labels
            ), (search, limit, offset)
            compared += 1
    return compared


# --- Synthetic edge library. ---

_LABELS = (("primary", "ebooks", "Shelf ẞ Label 50%"),)
_EDGE_SEARCHES: tuple[str | None, ...] = (
    None,
    "straße",
    "STRASSE",
    "σίσυφος",
    "ΣΊΣΥΦΟΣ",
    "fi",
    "i̇stanbul",
    "%",
    "100%",
    "_",
    "snake_c",
    "\\",
    "back\\s",
    "ada",
    "grace hopper",
    "ὀδυσσ",
    "untitled",
    "epub",
    "m4b",
    "ebooks",
    "audio",
    "shelf ss",
    "50%",
    "work-0003",
    "terminal",
    "catalogue author",
    "alpha",
    "absent-search-term",
    "a",
)
_EDGE_PAGES = ((25, 0), (3, 0), (3, 3), (2, 7), (4, 50))


def _resolve_policies(
    repository: SQLiteMetadataReportRepository, ask_library: str
) -> None:
    """Resolve every proposal's policy: one library asks, the rest automate."""
    connection = repository._connection
    destinations = (
        "title_layout",
        "contributor_names",
        "series",
        "description_fill",
        "isbn_form",
        "genres",
        "language_rendering",
        "subtitle",
    )
    for library, resolved in connection.execute(
        "SELECT DISTINCT mr.library_name,json_extract(p.document,'$.resolved_policy_id') "
        "FROM proposal_sets p JOIN metadata_reports mr USING(report_id)"
    ).fetchall():
        mode = "ask" if library == ask_library else "automatic"
        connection.execute(
            "INSERT INTO metadata_policy_resolutions"
            "(generation,library_name,resolved_policy_id,document) VALUES(1,?,?,?)",
            (
                library,
                resolved,
                json.dumps({"destinations": dict.fromkeys(destinations, mode)}),
            ),
        )
    connection.commit()


def _edge_report(
    index: int,
    title: str | None,
    *,
    library: str = "ebooks",
    authors: tuple[str, ...] = (),
    status: ProviderStatus = ProviderStatus.NO_MATCH,
    actionable: bool = True,
) -> MetadataReport:
    source = _report(actionable=actionable)
    fields = tuple(
        replace(field, state=TargetFieldState.VALUE, value=authors)
        if authors and field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(title)
    )
    return MetadataReport.create(
        target=TargetFacts("primary", library, f"work-{index:04d}", fields),
        file=FileFacts(0, f"{index + 1:064x}", f"{index + 5001:064x}"),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(ProviderObservation(f"provider-{index}", status),),
    )


def _lane_limited_report(index: int, title: str) -> MetadataReport:
    """A provider whose aggregate is CANDIDATES while one of its lanes is limited."""
    source = provider_failure_report()
    provider = source.providers[0]
    lanes = tuple(
        replace(lane, status=ProviderStatus.RATE_LIMITED)
        if lane.status is ProviderStatus.UNAVAILABLE
        else lane
        for lane in provider.lanes
    )
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", f"work-{index:04d}", default_target_fields(title)
        ),
        file=FileFacts(0, f"{index + 1:064x}", f"{index + 5001:064x}"),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(replace(provider, lanes=lanes),),
    )


def _publish(
    repository: SQLiteMetadataReportRepository,
    report: MetadataReport,
    *,
    propose: bool = True,
) -> None:
    repository.publish(report, lambda: True)
    if propose:
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)


def _edge_repository(root: Path) -> SQLiteMetadataReportRepository:
    repository = open_repository(root / "application.sqlite3")
    works = (
        _edge_report(0, "Straße und Weg", authors=("Ada Lovelace", "GRACE Hopper")),
        _edge_report(
            1, "ΣΊΣΥΦΟΣ", authors=("Ὀδυσσεύς",), status=ProviderStatus.UNAVAILABLE
        ),
        _edge_report(2, "ﬁnal ligature", status=ProviderStatus.RATE_LIMITED),
        _edge_report(3, "İstanbul notes", library="audio"),
        _edge_report(4, "100% proof", authors=("Percent_Author", "percent_author")),
        _edge_report(5, "snake_case tales", status=ProviderStatus.MALFORMED),
        _edge_report(6, "back\\slash saga", authors=("Back\\Slash",)),
        _edge_report(7, None, authors=("Untitled Writer",)),
        _edge_report(8, "alpha", actionable=False),
        _edge_report(9, "Alpha", library="audio", actionable=False),
        _edge_report(10, "ALPHA", status=ProviderStatus.INCOMPLETE),
        _edge_report(11, "Alpha", library="ebooks"),
        _edge_report(12, "Zulu shared", library="ebooks"),
        _edge_report(13, "Zulu shared", library="audio"),
    )
    for report in works:
        _publish(repository, report)
    _publish(repository, _edge_report(14, "Report only Straße"), propose=False)
    _publish(repository, _lane_limited_report(15, "Lane limited"))
    # A successor makes the first report historical: the browse reads only
    # the current package's report, never every report.
    _publish(repository, replace_title(works[2], "ﬁnal successor"))
    for index in range(3):
        _publish(
            repository,
            admitted_audiobook_report(
                library="audiobooks",
                item=f"recording-{index}",
                identity=f"{index + 9001:064x}",
                content_digest=f"{index + 9101:064x}",
            ),
        )
    _terminal_rows(root, repository)
    return repository


def replace_title(report: MetadataReport, title: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            report.target.connection,
            report.target.library,
            report.target.item,
            default_target_fields(title),
        ),
        file=FileFacts(0, "e" * 64, "f" * 64),
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
    )


def _terminal_rows(root: Path, repository: SQLiteMetadataReportRepository) -> None:
    """Two failed works that never published a report: terminal Library rows."""
    catalogue = open_catalogue(root / "catalogue.sqlite", create=True)
    entries = tuple(
        CatalogueEntry(
            "primary",
            "terminal",
            "lib-terminal",
            f"terminal-{index}",
            1,
            f"Terminal 100%_ title {index}",
            (f"Catalogue Author {index}",),
            True,
            False,
            "singular-supported",
            f"{index + 7001:064x}",
            "/synthetic.epub",
            "epub",
            "/media",
        )
        for index in range(2)
    )
    catalogue.acquire_refresh()
    generation = catalogue.publish("a" * 64, entries)
    repository.bind_library_catalogue(root / "catalogue.sqlite", "a" * 64)
    repository.request_review_scan("primary", "terminal", "lib-terminal", "f" * 64, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, generation, "a" * 64, entries, 12)
    tick = 13
    while action := repository.claim_review_pipeline_action(tick):
        repository.fail_review_pipeline_action(
            action, "unreadable_media", False, tick + 1
        )
        tick += 2
    catalogue.close()


def test_edge_library_pages_counts_and_searches_equal_the_document_statements(
    tmp_path: Path,
) -> None:
    repository = _edge_repository(tmp_path)
    try:
        # The fixture holds what the oracle must be checked against: terminal,
        # case-less, report-only, superseded and audiobook rows, and both
        # provider groups inside attention.
        everything = repository.read_current_library_page(None, 100, 0, None)
        assert everything.total == 21
        assert 0 < everything.attention_total < everything.total
        assert sum(case is None for case, _ in everything.rows) >= 5
        compared = sum(
            _assert_equal_to_oracle(repository, _EDGE_SEARCHES, _EDGE_PAGES, labels)
            for labels in ((), _LABELS)
        )
        # Resolved policies make the policy-ask clause join every proposal.
        before = repository.count_current_attention_fields(None, ())
        _resolve_policies(repository, "ebooks")
        assert repository.count_current_attention_fields(None, ()) != before
        compared += sum(
            _assert_equal_to_oracle(repository, _EDGE_SEARCHES, _EDGE_PAGES, labels)
            for labels in ((), _LABELS)
        )
        assert compared == 4 * len(_EDGE_SEARCHES) * len(_EDGE_PAGES) * 6
    finally:
        repository.close()


def test_owner_fixture_pages_equal_the_document_statements(tmp_path: Path) -> None:
    from tests.support.owner_responsiveness import OwnerResponsivenessFixture

    fixture = OwnerResponsivenessFixture(tmp_path, 40, mixed=True)
    try:
        repository = fixture.repository
        assert repository.read_current_library_page(None, 1, 0, None).total == 40
        _resolve_policies(repository, "books-1")
        _assert_equal_to_oracle(
            repository,
            (None, "synthetic", "deliberately long", "work-003", "books-2", "absent"),
            ((25, 0), (7, 7), (7, 35), (10, 39), (5, 80)),
            (("primary", "books-1", "Owner room books-1"),),
        )
    finally:
        fixture.close()


def test_candidate_statements_read_no_report_or_proposal_document(
    tmp_path: Path,
) -> None:
    """Every candidate's documents are unreadable; only rendered rows keep theirs."""
    repository = _edge_repository(tmp_path)
    try:
        _resolve_policies(repository, "ebooks")
        connection = repository._connection
        searches = (None, "straße", "ada", "m4b", "absent-search-term")
        counts = {
            search: (
                repository.count_current_review_cases(search, _LABELS),
                repository.count_current_attention_fields(search, _LABELS),
            )
            for search in searches
        }
        library = {
            search: repository.read_current_library_page(
                search, 1, 0, "needs_attention", _LABELS
            )
            for search in searches
        }
        attention = {
            search: repository.read_current_attention_page(search, 1, 0, _LABELS)
            for search in searches
        }
        settled = {
            search: repository.read_current_library_page(
                search, 1, 0, "no_attention_needed", _LABELS
            )
            for search in searches
        }
        rendered = {
            report_id
            for page in (*library.values(), *attention.values(), *settled.values())
            for row in page.rows
            for report_id in [json.loads(row[1]).get("report_id")]
            if report_id is not None
        }
        connection.execute("DROP TRIGGER metadata_reports_no_update")
        corrupted = connection.execute(
            "UPDATE metadata_reports SET document='not json' "
            "WHERE report_id NOT IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(rendered)),),
        ).rowcount
        assert corrupted > 10
        for search in searches:
            assert (
                repository.count_current_review_cases(search, _LABELS),
                repository.count_current_attention_fields(search, _LABELS),
            ) == counts[search]
            assert (
                repository.read_current_library_page(
                    search, 1, 0, "needs_attention", _LABELS
                )
                == library[search]
            )
            assert (
                repository.read_current_attention_page(search, 1, 0, _LABELS)
                == attention[search]
            )
        # Only a few fields reach the policy-ask clause, and attention pages
        # render exactly those; render only settled rows so every proposal the
        # counts join is unreadable.
        proposals = {
            json.loads(row[0])["proposal_set_id"]
            for page in settled.values()
            for row in page.rows
            if row[0] is not None
        }
        connection.execute("DROP TRIGGER proposal_sets_no_update")
        assert (
            connection.execute(
                "UPDATE proposal_sets SET document='not json' "
                "WHERE proposal_set_id NOT IN (SELECT value FROM json_each(?))",
                (json.dumps(sorted(proposals)),),
            ).rowcount
            > 10
        )
        for search in searches:
            assert (
                repository.count_current_review_cases(search, _LABELS),
                repository.count_current_attention_fields(search, _LABELS),
            ) == counts[search]
            assert (
                repository.read_current_library_page(
                    search, 1, 0, "no_attention_needed", _LABELS
                )
                == settled[search]
            )
        # The oracle still reads every document, so it now fails.
        with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
            _oracle_counts(repository, "straße", _LABELS)
    finally:
        repository.close()


def test_report_insert_projects_exact_folded_facts(tmp_path: Path) -> None:
    repository = _edge_repository(tmp_path)
    try:
        connection = repository._connection
        facts = dict(
            (row[0], row[1:])
            for row in connection.execute(
                "SELECT mr.target_item,facts.title_key,facts.format,facts.format_key,"
                "facts.provider_limited FROM metadata_report_browse_facts facts "
                "JOIN (SELECT report_id,item_id AS target_item FROM metadata_reports) mr "
                "USING(report_id) WHERE mr.target_item IN "
                "('work-0000','work-0001','work-0007','work-0015','recording-0')"
            )
        )
        # A limited lane under a CANDIDATES aggregate is still limited.
        assert facts["work-0015"] == ("lane limited", "epub", "epub", 1)
        assert facts["work-0000"] == ("strasse und weg", "epub", "epub", 0)
        assert facts["work-0001"] == ("σίσυφοσ", "epub", "epub", 1)
        assert facts["work-0007"] == ("", "epub", "epub", 0)
        assert facts["recording-0"][1:] == ("m4b", "m4b", 0)
        authors = connection.execute(
            "SELECT author_key FROM metadata_report_browse_authors JOIN "
            "metadata_reports USING(report_id) WHERE item_id IN ('work-0000','work-0004') "
            "ORDER BY 1"
        ).fetchall()
        # Values are folded, one row per distinct folded author.
        assert authors == [("ada lovelace",), ("grace hopper",), ("percent_author",)]
        assert (
            connection.execute("SELECT count(*) FROM metadata_reports").fetchone()
            == connection.execute(
                "SELECT count(*) FROM metadata_report_browse_facts"
            ).fetchone()
        )
        assert connection.execute(
            "SELECT count(*),count(DISTINCT facts.resolved_policy_id) "
            "FROM proposal_sets p JOIN proposal_set_browse_facts facts "
            "USING(proposal_set_id) WHERE facts.resolved_policy_id="
            "json_extract(p.document,'$.resolved_policy_id') "
            "AND facts.resolved_policy_id GLOB 'resolved_policy_*'"
        ).fetchone() == (
            connection.execute("SELECT count(*) FROM proposal_sets").fetchone()[0],
            connection.execute(
                "SELECT count(DISTINCT library_name) FROM metadata_reports"
            ).fetchone()[0],
        )
    finally:
        repository.close()


def _report_row(report: MetadataReport) -> tuple[object, ...]:
    document = report.to_json()
    import hashlib

    return (
        report.report_id,
        report.target.connection,
        report.target.library,
        report.target.item,
        report.file.identity,
        report.file.content_digest,
        report.schema_version,
        hashlib.sha256(document.encode()).hexdigest(),
        document,
    )


_INSERT_REPORT_SQL = (
    "INSERT INTO metadata_reports(report_id,connection_name,library_name,item_id,"
    "file_identity,content_digest,schema_version,document_digest,document) "
    "VALUES(?,?,?,?,?,?,?,?,?)"
)


def test_connection_without_casefold_writes_and_validates_nothing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.OperationalError, match="ml_casefold"):
            connection.execute(_INSERT_REPORT_SQL, _report_row(_edge_report(1, "Ab")))
        assert connection.execute(
            "SELECT (SELECT count(*) FROM metadata_reports),"
            "(SELECT count(*) FROM metadata_report_browse_facts),"
            "(SELECT count(*) FROM metadata_report_browse_authors)"
        ).fetchone() == (0, 0, 0)
        connection.rollback()
        schema._register_casefold(connection)
        connection.execute(
            _INSERT_REPORT_SQL, _report_row(_edge_report(1, "Ab", authors=("Cd",)))
        )
        connection.commit()
    finally:
        connection.close()
    unfolded = sqlite3.connect(database)
    try:
        # Facts are validated through the registered function only.
        with pytest.raises(sqlite3.OperationalError, match="ml_casefold"):
            schema._validate_schema(unfolded)
    finally:
        unfolded.close()
    open_repository(database, create=False).close()


def test_a_different_casefold_writes_facts_that_open_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "application.sqlite3"
    with monkeypatch.context() as patched:
        patched.setattr(schema, "_review_casefold", lambda value: value)
        repository = open_repository(database)
        _publish(repository, _edge_report(1, "Upper Case"), propose=False)
        repository.close()
    with pytest.raises(
        MetadataReportError, match="database is unavailable or incompatible"
    ):
        open_repository(database, create=False)


@pytest.mark.parametrize(
    "corruption",
    (
        "UPDATE metadata_report_browse_facts SET title_key='other'",
        "UPDATE metadata_report_browse_facts SET format=NULL",
        "UPDATE metadata_report_browse_facts SET format_key='m4b'",
        "UPDATE metadata_report_browse_facts SET provider_limited=1-provider_limited",
        "DELETE FROM metadata_report_browse_facts",
        "DELETE FROM metadata_report_browse_authors",
        "INSERT INTO metadata_report_browse_authors(report_id,author_key) "
        "SELECT report_id,'invented' FROM metadata_reports",
        "UPDATE proposal_set_browse_facts SET resolved_policy_id=NULL",
        "DELETE FROM proposal_set_browse_facts",
    ),
)
def test_restart_refuses_browse_facts_that_disagree_with_reports(
    tmp_path: Path, corruption: str
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    _publish(repository, _edge_report(1, "Ab", authors=("Cd",)))
    repository.close()
    connection = sqlite3.connect(database)
    assert connection.execute(corruption).rowcount == 1
    connection.commit()
    connection.close()
    with pytest.raises(
        MetadataReportError, match="database is unavailable or incompatible"
    ):
        open_repository(database, create=False)


def test_open_refuses_facts_folded_under_another_unicode_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    _publish(repository, _edge_report(1, "Ab"))
    repository.close()
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT unicode_version FROM browse_fact_identity"
    ).fetchall() == [(unicodedata.unidata_version,)]
    connection.execute("UPDATE browse_fact_identity SET unicode_version='15.0.0-other'")
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(
        MetadataReportError,
        match="^database browse facts were folded under a different Unicode "
        "version; a clean rebuild is required$",
    ):
        open_repository(database, create=False)
    assert database.read_bytes() == before


def _supersede(
    repository: SQLiteMetadataReportRepository,
    historical: MetadataReport,
    successor: MetadataReport,
) -> None:
    """Admit a successor, so no package or lineage points at the history."""
    repository.publish(successor, lambda: True, historical.report_id)
    proposal = evaluate_report(successor)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)


@pytest.mark.parametrize(
    "tamper",
    (
        "UPDATE metadata_report_browse_facts SET title_key='zzz tampered' "
        "WHERE report_id=:report",
        "UPDATE proposal_set_browse_facts SET resolved_policy_id='tampered' "
        "WHERE proposal_set_id=:proposal",
    ),
)
def test_open_refuses_tampered_facts_staged_only_in_a_lineage(
    tmp_path: Path, tamper: str
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    older = _edge_report(1, "Ab")
    staged = _edge_report(1, "Staged")
    _publish(repository, older, propose=False)
    repository.publish(staged, lambda: True, older.report_id)
    proposal = evaluate_report(staged)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    repository.close()
    connection = sqlite3.connect(database)
    # The package rewinds to the older report-only row, leaving the staged
    # report and proposal reachable only through the lineage pointers a
    # preparation copy promotes without a check.
    connection.execute(
        "UPDATE current_review_packages SET proposal_set_id=NULL,report_id=?,"
        "review_case_id=NULL,checkpoint=NULL,outcome='report_only'",
        (older.report_id,),
    )
    connection.commit()
    connection.close()
    open_repository(database, create=False).close()
    connection = sqlite3.connect(database)
    assert (
        connection.execute(
            tamper,
            {"report": staged.report_id, "proposal": proposal.proposal_set_id},
        ).rowcount
        == 1
    )
    connection.commit()
    connection.close()
    with pytest.raises(
        MetadataReportError, match="^database is unavailable or incompatible$"
    ):
        open_repository(database, create=False)


def test_unselected_historical_proposal_tamper_does_not_block_open(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    historical = _edge_report(1, "Ab")
    successor = _edge_report(1, "Successor")
    _publish(repository, historical)
    _supersede(repository, historical, successor)
    repository.close()
    connection = sqlite3.connect(database)
    connection.execute("DROP TRIGGER proposal_sets_no_update")
    connection.execute(
        "UPDATE proposal_sets SET document='not json' WHERE report_id=?",
        (historical.report_id,),
    )
    connection.execute(schema._PROPOSAL_NO_UPDATE_SQL)
    connection.commit()
    connection.close()
    repository = open_repository(database, create=False)
    try:
        assert repository.count_current_review_cases(None, _LABELS)[0] == 1
    finally:
        repository.close()


def _tamper_history(database: Path, statement: str, key: str) -> None:
    connection = sqlite3.connect(database)
    assert connection.execute(statement, (key,)).rowcount == 1
    connection.commit()
    connection.close()


def _current_package(database: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            "SELECT proposal_set_id,report_id,outcome FROM current_review_packages"
        ).fetchall()
    finally:
        connection.close()


def test_reselecting_a_report_with_tampered_facts_refuses_and_commits_nothing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    historical = _edge_report(1, "Ab", authors=("Cd",))
    successor = _edge_report(1, "Successor")
    _publish(repository, historical, propose=False)
    repository.publish(successor, lambda: True, historical.report_id)
    repository.close()
    _tamper_history(
        database,
        "UPDATE metadata_report_browse_facts SET title_key='zzz tampered' "
        "WHERE report_id=?",
        historical.report_id,
    )
    before = _current_package(database)
    repository = open_repository(database, create=False)
    try:
        with pytest.raises(
            MetadataReportError, match="^database is unavailable or incompatible$"
        ):
            repository.publish(historical, lambda: True, successor.report_id)
        assert repository.count_current_review_cases("zzz", _LABELS) == (0, 0)
    finally:
        repository.close()
    assert _current_package(database) == before


def test_readmitting_a_proposal_with_tampered_facts_refuses_and_commits_nothing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    historical = _edge_report(1, "Ab")
    successor = _edge_report(1, "Successor")
    _publish(repository, historical)
    _supersede(repository, historical, successor)
    proposal = evaluate_report(historical)
    repository.close()
    _tamper_history(
        database,
        "UPDATE proposal_set_browse_facts SET resolved_policy_id='tampered' "
        "WHERE proposal_set_id=?",
        proposal.proposal_set_id,
    )
    repository = open_repository(database, create=False)
    try:
        repository.publish(historical, lambda: True, successor.report_id)
        repository.publish_proposal(proposal)
        before = repository._connection.execute(
            "SELECT * FROM current_review_case_lineages"
        ).fetchall()
        with pytest.raises(
            MetadataReportError, match="^database is unavailable or incompatible$"
        ):
            repository.admit_review_case(proposal)
        assert (
            repository._connection.execute(
                "SELECT * FROM current_review_case_lineages"
            ).fetchall()
            == before
        )
    finally:
        repository.close()


_DROP_BROWSE_FACTS_SQL = (
    "DROP TRIGGER metadata_reports_project_browse_facts;"
    "DROP TABLE metadata_report_browse_authors;"
    "DROP TABLE metadata_report_browse_facts;"
    "DROP TRIGGER proposal_sets_project_browse_facts;"
    "DROP TABLE proposal_set_browse_facts;"
    "DROP TABLE browse_fact_identity;"
)


def test_startup_refuses_the_predecessor_schema_without_browse_facts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    _publish(repository, _edge_report(1, "Ab"), propose=False)
    repository.close()
    connection = sqlite3.connect(database)
    connection.executescript(_DROP_BROWSE_FACTS_SQL)
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(
        MetadataReportError, match="database is unavailable or incompatible"
    ):
        open_repository(database, create=False)
    assert database.read_bytes() == before
