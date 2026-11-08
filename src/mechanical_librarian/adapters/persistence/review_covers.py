"""The Covers view's page read over current packages (#1633, #1363).

One statement groups every current work with a review case by its cover field,
and the same relation feeds the page and the tab counts. The groups read only
persisted facts: Library's own cover attention facts (#1232) under the live
cover authority, the case's cover result, and the automatic cover writes.

Switched follows the release rule (ADR-0093, Amendment 6). A replaced cover
is listed while its switch is in `retained_cover_listings` with no removal,
so a released original never shows; a fill replaced nothing and comes from
its verified plan with no previous cover. Either leaves once the owner
records a cover decision after it, or a later cover write lands.
"""

from __future__ import annotations

import sqlite3
from typing import cast

from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_CURRENT_SQL,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_covers import (
    COVER_GROUPS,
    CoverGroup,
    CoverKey,
    CoverPageSnapshot,
    CoverRow,
    CoverView,
    ReplacedCover,
)

_PREVIEW_VARIANT = "preview-320x480-rgba-png-v1"
# Each work's newest verified cover write, when it is automatic and the owner
# has not answered it since.
_SWITCH_SQL = (
    "switch AS (SELECT effect.connection_name,effect.library_name,"
    "effect.work_id,effect.plan_id,effect.updated_at AS switched_at,"
    "listing.sequence AS sequence,binding.original_digest AS original_digest "
    "FROM field_effects effect "
    "LEFT JOIN retained_cover_bindings binding ON binding.plan_id=effect.plan_id "
    "LEFT JOIN retained_cover_listings listing ON listing.plan_id=effect.plan_id "
    "WHERE effect.destination='cover' AND effect.state='verified' "
    "AND json_extract(effect.document,'$.plan.authority.origin')='automatic_policy' "
    "AND ((listing.plan_id IS NOT NULL AND NOT EXISTS (SELECT 1 "
    "FROM retained_cover_removals removal WHERE removal.plan_id=effect.plan_id)) "
    "OR (binding.plan_id IS NULL "
    "AND json_extract(effect.document,'$.plan.pre_value') IS NULL)) "
    # Two listed switches of one work are ordered by listing sequence, as the
    # release rule and the newest retained original are; any other later
    # write by when it was verified.
    "AND NOT EXISTS (SELECT 1 FROM field_effects later "
    "LEFT JOIN retained_cover_listings later_listing "
    "ON later_listing.plan_id=later.plan_id "
    "WHERE later.connection_name=effect.connection_name "
    "AND later.library_name=effect.library_name AND later.work_id=effect.work_id "
    "AND later.destination='cover' AND later.state='verified' "
    "AND later.plan_id<>effect.plan_id AND CASE "
    "WHEN listing.sequence IS NOT NULL AND later_listing.sequence IS NOT NULL "
    "THEN later_listing.sequence>listing.sequence "
    "ELSE later.updated_at>effect.updated_at OR (later.updated_at=effect.updated_at "
    "AND later.plan_id>effect.plan_id) END) "
    # The owner's answer is read through the work's current selection, a
    # primary-key search: automation never supersedes an owner selection, so
    # an owner answer after the write is always the current one.
    "AND NOT EXISTS (SELECT 1 FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities answered "
    "ON answered.selection_id=current.selection_id "
    "WHERE current.connection_name=effect.connection_name "
    "AND current.library_name=effect.library_name "
    "AND current.item_id=effect.work_id AND answered.kind='owner' "
    "AND answered.occurred_at>=effect.updated_at))"
)
# Library's cover attention, read from the same facts its lens reads.
_DECIDING_SQL = (
    "EXISTS (SELECT 1 FROM review_attention_facts f "
    "WHERE f.connection_name=ml.connection_name AND f.library_name=ml.library_name "
    "AND f.item_id=ml.item_id AND f.report_id=ml.report_id "
    "AND f.destination='cover' AND " + _ATTENTION_FACT_CURRENT_SQL + ")"
)
_COVER_OUTCOME_SQL = (
    "(SELECT json_extract(cover_result.value,'$.outcome') "
    "FROM review_cases rc,json_each(rc.document,'$.results') cover_result "
    "WHERE rc.review_case_id=ml.review_case_id "
    "AND json_extract(cover_result.value,'$.destination')='cover')"
)
_CHOOSABLE_SQL = (
    "COALESCE((SELECT result.has_owner_decision_actions "
    "FROM review_case_field_facts result "
    "WHERE result.review_case_id=ml.review_case_id "
    "AND result.destination='cover'),0)"
)
_WORKS_SQL = (
    "WITH "
    + _SWITCH_SQL
    + ", work AS (SELECT ml.review_case_id,mr.document AS report_document,"
    "facts.title_key,ml.item_id,"
    + _CHOOSABLE_SQL
    + " AS choosable,switch.plan_id,switch.switched_at,switch.sequence,"
    "switch.original_digest,CASE WHEN "
    + _DECIDING_SQL
    + " THEN CASE WHEN "
    + _COVER_OUTCOME_SQL
    + "='unresolved_choice' THEN 'choice' ELSE 'proposal' END "
    "WHEN switch.plan_id IS NOT NULL THEN 'auto' WHEN "
    + _CHOOSABLE_SQL
    + "=1 THEN 'kept' ELSE 'none' END AS cover_group "
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "JOIN metadata_report_browse_facts facts ON facts.report_id=mr.report_id "
    "LEFT JOIN switch ON switch.connection_name=ml.connection_name "
    "AND switch.library_name=ml.library_name AND switch.work_id=ml.item_id "
    "WHERE ml.review_case_id IS NOT NULL) "
)
_VIEW_FILTER_SQL: dict[CoverView, str] = {
    "decide": "cover_group IN ('proposal','choice')",
    "kept": "cover_group='kept'",
    "switched": "cover_group='auto'",
    "all": "1",
}
# Suggestions before choices, as the queue groups them; Switched newest first
# in the release rule's order, a fill placed by when it was verified. Each
# order is its row key, so a page resumes strictly after the last row it
# showed (application/review_covers.py).
_VIEW_KEY_SQL: dict[CoverView, str] = {
    "decide": "cover_group='choice',title_key,item_id,review_case_id",
    "kept": "title_key,item_id,review_case_id",
    "switched": "switched_at,COALESCE(sequence,0),plan_id",
    "all": "title_key,item_id,review_case_id",
}
_VIEW_ORDER_SQL: dict[CoverView, str] = {
    **_VIEW_KEY_SQL,
    "switched": "switched_at DESC,COALESCE(sequence,0) DESC,plan_id DESC",
}
# The retained original's preview and size, from a report that observed it.
_REPLACED_SQL = (
    "SELECT ref.preview_digest,json_extract(source.value,'$.artifact.width'),"
    "json_extract(source.value,'$.artifact.height') "
    "FROM metadata_report_cover_references ref "
    "JOIN metadata_reports observed ON observed.report_id=ref.report_id,"
    "json_each(observed.document,'$.covers') source "
    "WHERE ref.artifact_id=? AND ref.preview_variant=? "
    "AND json_extract(source.value,'$.source_id')=ref.source_id "
    "ORDER BY ref.report_id,ref.source_id LIMIT 1"
)


def _replaced(
    connection: sqlite3.Connection, plan_id: str, digest: str
) -> ReplacedCover | None:
    row = connection.execute(
        _REPLACED_SQL, ("cover_" + digest, _PREVIEW_VARIANT)
    ).fetchone()
    if row is None:
        return None
    preview, width, height = row
    if type(preview) is not str or type(width) is not int or type(height) is not int:
        raise MetadataReportError("review covers are unavailable")
    return ReplacedCover(plan_id, preview, width, height)


def read_cover_page(
    connection: sqlite3.Connection,
    view: CoverView,
    limit: int,
    after: CoverKey | None,
    review_case_id: str | None,
    cover_authority: tuple[str | None, str | None],
) -> CoverPageSnapshot:
    """Read one page of a tab, after a row key, and every tab's count.

    With ``review_case_id`` the page holds only that book, if the tab has it.
    """
    where = [_VIEW_FILTER_SQL[view]]
    parameters: list[object] = list(cover_authority)
    if after is not None:
        where.append(
            "("
            + _VIEW_KEY_SQL[view]
            + ")"
            + ("<" if view == "switched" else ">")
            + "("
            + ",".join("?" * len(after))
            + ")"
        )
        parameters.extend(after)
    if review_case_id is not None:
        where.append("review_case_id=?")
        parameters.append(review_case_id)
    rows = connection.execute(
        _WORKS_SQL + "SELECT review_case_id,report_document,cover_group,choosable,"
        "switched_at,plan_id,original_digest," + _VIEW_KEY_SQL[view] + " FROM work "
        "WHERE "
        + " AND ".join(where)
        + " ORDER BY "
        + _VIEW_ORDER_SQL[view]
        + " LIMIT ?",
        (*parameters, limit),
    ).fetchall()
    grouped = dict(
        connection.execute(
            _WORKS_SQL + "SELECT cover_group,count(*) FROM work GROUP BY cover_group",
            cover_authority,
        ).fetchall()
    )
    if any(group not in COVER_GROUPS for group in grouped):
        raise MetadataReportError("review covers are unavailable")
    page: list[CoverRow] = []
    for case_id, report, group, choosable, switched_at, plan_id, digest, *key in rows:
        if (
            type(case_id) is not str
            or type(report) is not str
            or group not in COVER_GROUPS
            or choosable not in (0, 1)
            or any(type(value) not in (int, str) for value in key)
        ):
            raise MetadataReportError("review covers are unavailable")
        auto = group == "auto"
        page.append(
            CoverRow(
                case_id,
                report,
                cast(CoverGroup, group),
                choosable == 1,
                cast(int, switched_at) if auto else None,
                auto and digest is None,
                _replaced(connection, cast(str, plan_id), cast(str, digest))
                if auto and digest is not None
                else None,
                tuple(key),
            )
        )
    return CoverPageSnapshot(
        tuple(page),
        {
            "decide": grouped.get("proposal", 0) + grouped.get("choice", 0),
            "kept": grouped.get("kept", 0),
            "switched": grouped.get("auto", 0),
            "all": sum(grouped.values()),
        },
    )


def read_replaced_preview(connection: sqlite3.Connection, plan_id: str) -> str | None:
    """The preview of a listed switch's original while it has no removal."""
    row = connection.execute(
        "SELECT binding.original_digest FROM retained_cover_listings listing "
        "JOIN retained_cover_bindings binding ON binding.plan_id=listing.plan_id "
        "WHERE listing.plan_id=? AND NOT EXISTS (SELECT 1 FROM "
        "retained_cover_removals removal WHERE removal.plan_id=listing.plan_id)",
        (plan_id,),
    ).fetchone()
    if row is None:
        return None
    replaced = _replaced(connection, plan_id, str(row[0]))
    return None if replaced is None else replaced.preview_digest
