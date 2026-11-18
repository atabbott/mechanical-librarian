"""The Covers view's page read over current packages (#1633, #1363).

One statement groups every current work with a review case by its cover field,
and the same relation feeds the page and the tab counts. The groups read only
persisted facts: Library's own cover attention facts (#1232) under the live
cover authority, the case's cover result, and the automatic cover writes.

Switched follows the release rule (ADR-0093, Amendment 6). A replaced cover
is listed while its switch is in `retained_cover_listings` with no removal,
so a released original never shows; a fill replaced nothing and comes from
its verified plan with no previous cover. Either leaves once the owner
records a cover decision after it, or a later cover write lands. A Put back
of the switch is the one answer that keeps it listed: its copy is released
only when the Put back's write verifies, and until then the row carries that
write's state, so a Put back the target refused can say so (#1363, Q18).

A proposed cover the owner chose whose write has not verified is `chosen`,
listed only under All, never as kept (#1866).

The calibration set (#1372 PR 3) reads the same relation: the works whose
cover the owner can pick now, where every pick counts toward the taste
profile.
"""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_CURRENT_SQL,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_covers import (
    COVER_GROUPS,
    CalibrationRow,
    CoverGroup,
    CoverKey,
    CoverPageSnapshot,
    CoverRow,
    CoverView,
    PutBack,
    SwitchCover,
    SwitchTaste,
)
from mechanical_librarian.domain.cover_taste import (
    TASTE_MIN_ARTS,
    TASTE_STRONG_CONFIDENCE,
    TasteProfile,
)

_PREVIEW_VARIANT = "preview-320x480-rgba-png-v1"
# Each work's newest verified cover write, when it is automatic and the owner
# has not answered it since.
_SWITCH_SQL = (
    "switch AS (SELECT effect.connection_name,effect.library_name,"
    "effect.work_id,effect.plan_id,effect.updated_at AS switched_at,"
    "listing.sequence AS sequence,binding.original_digest AS original_digest,"
    "json_extract(effect.document,'$.plan.desired_value') AS replacement "
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
    "AND answered.occurred_at>=effect.updated_at "
    "AND json_extract(answered.document,'$.retained_plan_id') IS NOT effect.plan_id))"
)
# The owner's current Put back of one switch and its write's state. A Put back
# recorded before its write is planned reads as planned.
_PUT_BACK_SQL = (
    "SELECT json_object('state',COALESCE(effect.state,'planned'),"
    "'failure',json_extract(effect.document,'$.failure')) "
    "FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities answered "
    "ON answered.selection_id=current.selection_id "
    "LEFT JOIN field_effects effect ON effect.decision_id=answered.decision_id "
    "AND effect.destination='cover' "
    "WHERE current.connection_name=? AND current.library_name=? "
    "AND current.item_id=? AND answered.kind='owner' "
    "AND json_extract(answered.document,'$.retained_plan_id')=? "
    "ORDER BY effect.updated_at DESC,effect.plan_id DESC LIMIT 1"
)
# Whether the work's current comparison offers this switch's original, so
# Put back can be recorded now; before the work is prepared again after its
# switch it does not, and Put back is not offered (#1363).
_PUT_BACK_OFFERED_SQL = (
    "SELECT EXISTS (SELECT 1 FROM current_cover_considerations current "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=current.consideration_id,"
    "json_each(consideration.document,'$.candidates') candidate "
    "WHERE current.review_case_id=? "
    "AND json_extract(candidate.value,'$.candidate_kind')='retained' "
    "AND json_extract(candidate.value,'$.plan_id')=? "
    "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events event "
    "WHERE event.consideration_id=consideration.consideration_id "
    "AND event.kind='stale'))"
)
# Library's cover attention, read from the same facts its lens reads.
_DECIDING_SQL = (
    "EXISTS (SELECT 1 FROM review_attention_facts f "
    "WHERE f.connection_name=ml.connection_name AND f.library_name=ml.library_name "
    "AND f.item_id=ml.item_id AND f.report_id=ml.report_id "
    "AND f.destination='cover' AND " + _ATTENTION_FACT_CURRENT_SQL + ")"
)
# The measured stage's outcome and reason for the work's cover, as a JSON
# pair, when it suggests a switch or holds one, its current comparison is
# current under the live authority, and the owner has not answered it, nor
# automation admitted the switch. A suggestion the owner can choose is
# optional work: it is listed under To decide as "Switch suggested", never in
# Library's attention (#1363, S9). A hold carries its reason to the row's
# pill. It binds the cover authority again.
_STAGE_SQL = (
    "(SELECT json_array(json_extract(pointed.currentness_document,"
    "'$.cover_stage.outcome'),json_extract(pointed.currentness_document,"
    "'$.cover_stage.reason')) "
    "FROM current_cover_considerations pointer JOIN cover_considerations pointed "
    "ON pointed.consideration_id=pointer.consideration_id "
    "WHERE pointer.review_case_id=ml.review_case_id "
    "AND json_extract(pointed.currentness_document,'$.cover_stage.outcome') "
    "IN ('suggest','replace','hold') "
    "AND json_extract(pointed.document,'$.policy_reference')=? "
    "AND ml_cover_consideration_current(pointed.currentness_document,?)=1 "
    "AND NOT EXISTS (SELECT 1 FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities answered "
    "ON answered.selection_id=current.selection_id "
    "WHERE current.connection_name=ml.connection_name "
    "AND current.library_name=ml.library_name AND current.item_id=ml.item_id "
    "AND (answered.kind='owner' OR answered.consideration_id=pointed.consideration_id)))"
)
# The taste judge's current suggestion (#1372 PR 4b, J8): the confidence of
# the newest verdict on the work's current comparison, when that verdict
# chose another cover, the comparison is current under the live authority and
# nothing has answered it. A verdict from an older profile version stands
# until the field is judged again. It binds the cover authority again.
_TASTE_SQL = (
    "(SELECT json_extract(verdict.document,'$.confidence') "
    "FROM current_cover_considerations pointer JOIN cover_considerations pointed "
    "ON pointed.consideration_id=pointer.consideration_id "
    "JOIN cover_taste_verdicts verdict "
    "ON verdict.consideration_id=pointed.consideration_id "
    "WHERE pointer.review_case_id=ml.review_case_id "
    "AND verdict.rowid=(SELECT newest.rowid FROM cover_taste_verdicts newest "
    "WHERE newest.consideration_id=pointed.consideration_id "
    "ORDER BY newest.created_at DESC,newest.rowid DESC LIMIT 1) "
    "AND json_extract(verdict.document,'$.decision')='choose' "
    "AND json_extract(pointed.document,'$.policy_reference')=? "
    "AND ml_cover_consideration_current(pointed.currentness_document,?)=1 "
    "AND NOT EXISTS (SELECT 1 FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities answered "
    "ON answered.selection_id=current.selection_id "
    "WHERE current.connection_name=ml.connection_name "
    "AND current.library_name=ml.library_name AND current.item_id=ml.item_id "
    "AND (answered.kind='owner' OR answered.consideration_id=pointed.consideration_id)))"
)
# The measured rule's reason an automatic switch recorded, and the edition
# language that reason names, from the decision and its comparison.
_SWITCH_REASON_SQL = (
    "SELECT json_extract(json_extract(selection.document,'$.decision_document'),"
    "'$.reason'),json_extract(json_extract(consideration.document,"
    "'$.policy_report_document'),'$.stage.language'),"
    "json_extract(json_extract(selection.document,'$.decision_document'),"
    "'$.verdict_id'),json_extract(json_extract(selection.document,"
    "'$.decision_document'),'$.taste_version') "
    "FROM field_effects effect JOIN cover_selection_authorities selection "
    "ON selection.decision_id=effect.decision_id AND selection.kind='automatic' "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=selection.consideration_id "
    "WHERE effect.plan_id=?"
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
# The owner's current selection chose a proposed cover and its write has not
# verified yet, so the cover Audiobookshelf holds is not the one kept (#1866).
# The selection stays the owner's choice when a later comparison is prepared
# (a policy change), so it is read from the current selection, not the
# current comparison. A Put back stays with its switch, which the CASE reads
# first; once the write verifies, the work reads as kept.
_CHOSEN_SQL = (
    "EXISTS (SELECT 1 FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities answered "
    "ON answered.selection_id=current.selection_id "
    "WHERE current.connection_name=ml.connection_name "
    "AND current.library_name=ml.library_name AND current.item_id=ml.item_id "
    "AND answered.kind='owner' "
    "AND json_extract(answered.document,'$.candidate_kind') IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM field_effects effect "
    "WHERE effect.decision_id=answered.decision_id "
    "AND effect.destination='cover' AND effect.state='verified'))"
)
_WORKS_SQL = (
    "WITH "
    + _SWITCH_SQL
    + ", work AS (SELECT ml.review_case_id,mr.document AS report_document,"
    # The order key takes the folded title's first 256 characters, so a
    # page cursor that carries it stays bounded for every accepted title
    # (application/review_covers.py); titles sharing that prefix fall back
    # to item and case order, which is still one total order.
    "substr(facts.title_key,1,256) AS title_key,"
    "ml.connection_name,ml.library_name,ml.item_id,"
    + _CHOOSABLE_SQL
    + " AS choosable,switch.plan_id,switch.switched_at,switch.sequence,"
    "switch.original_digest,switch.replacement,ml.suggestion_reason,"
    "ml.hold_reason,ml.cover_taste,"
    # Within To decide: Strong taste suggestions, then Likely, then the rest
    # (J8, S21).
    f"CASE WHEN ml.cover_taste>={TASTE_STRONG_CONFIDENCE} THEN 0 "
    "WHEN ml.cover_taste IS NOT NULL THEN 1 ELSE 2 END AS taste_rank,CASE WHEN "
    + _DECIDING_SQL
    + " THEN CASE WHEN "
    + _COVER_OUTCOME_SQL
    + "='unresolved_choice' THEN 'choice' ELSE 'proposal' END "
    # A suggestion with no decision the owner can record, such as an
    # unreadable current cover the comparison cannot act on, is not To
    # decide; it is listed where its other facts put it (S9, Q16).
    "WHEN (ml.suggestion_reason IS NOT NULL OR ml.cover_taste IS NOT NULL) AND "
    + _CHOOSABLE_SQL
    + "=1 THEN 'proposal' "
    "WHEN switch.plan_id IS NOT NULL THEN 'auto' WHEN "
    + _CHOSEN_SQL
    + " THEN 'chosen' WHEN "
    + _CHOOSABLE_SQL
    + "=1 THEN 'kept' ELSE 'none' END AS cover_group "
    "FROM (SELECT staged.*,CASE WHEN json_extract(staged.cover_stage,'$[0]') "
    "IN ('suggest','replace') THEN json_extract(staged.cover_stage,'$[1]') END "
    "AS suggestion_reason,CASE WHEN json_extract(staged.cover_stage,'$[0]')="
    "'hold' THEN json_extract(staged.cover_stage,'$[1]') END AS hold_reason "
    "FROM (SELECT ml.*,"
    + _STAGE_SQL
    + " AS cover_stage,"
    + _TASTE_SQL
    + " AS cover_taste FROM current_review_packages ml) staged) ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "JOIN metadata_report_browse_facts facts ON facts.report_id=mr.report_id "
    "LEFT JOIN switch ON switch.connection_name=ml.connection_name "
    "AND switch.library_name=ml.library_name AND switch.work_id=ml.item_id "
    "WHERE ml.review_case_id IS NOT NULL) "
)
# The works calibration offers (#1372 S2): a cover the owner can pick now,
# from at least two arts, on a current comparison that is ready and not held,
# with no owner answer yet. A comparison offering a larger copy or a retained
# original is left out, and so is a report naming no file format, because a
# pick there would not count toward the taste profile (cover_taste.py).
# Works offering three or more arts come first, then by when each comparison
# was prepared. It binds the cover authority again.
_CALIBRATION_SQL = (
    "SELECT work.review_case_id,work.report_document,work.cover_group,"
    # The first author and the series names the caps count, read here so the
    # selector parses only the reports of the works it keeps (S3).
    "(SELECT json_extract(field.value,'$.value[0]') "
    "FROM json_each(work.report_document,'$.target.fields') field "
    "WHERE json_extract(field.value,'$.destination')='authors' "
    "AND json_extract(field.value,'$.state')='value'),"
    "(SELECT json_group_array(json_extract(member.value,'$.name')) "
    "FROM json_each(work.report_document,'$.target.fields') field,"
    "json_each(field.value,'$.value') member "
    "WHERE json_extract(field.value,'$.destination')='series' "
    "AND json_extract(field.value,'$.state')='value') "
    "FROM work JOIN current_cover_considerations pointer "
    "ON pointer.review_case_id=work.review_case_id "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=pointer.consideration_id "
    "JOIN metadata_report_browse_facts facts "
    "ON facts.report_id=consideration.report_id "
    "WHERE work.cover_group IN ('proposal','choice','kept') AND work.choosable=1 "
    "AND json_extract(consideration.document,'$.policy_reference')=? "
    "AND ml_cover_consideration_current(consideration.currentness_document,?)=1 "
    "AND json_extract(consideration.currentness_document,'$.manual_ready')=1 "
    "AND json_extract(consideration.currentness_document,'$.provider_fetching')=0 "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.outcome') "
    "IS NOT 'hold' "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.arts')>=? "
    "AND typeof(facts.format)='text' "
    # Keeping a current cover that could not be read names no preview, so that
    # pick would not count; a work with no cover at all offers no keep (S2).
    "AND json_extract(consideration.document,'$.incumbent.state') "
    "IS NOT 'unavailable' "
    "AND NOT EXISTS (SELECT 1 FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities answered "
    "ON answered.selection_id=current.selection_id "
    "WHERE current.connection_name=work.connection_name "
    "AND current.library_name=work.library_name AND current.item_id=work.item_id "
    "AND answered.kind='owner') "
    "AND NOT EXISTS (SELECT 1 FROM json_each(consideration.document,'$.candidates') "
    "offered WHERE json_extract(offered.value,'$.candidate_kind') "
    "IN ('retained','larger_copy')) "
    "ORDER BY json_extract(consideration.currentness_document,'$.cover_stage.arts')"
    ">=3 DESC,consideration.occurred_at,work.review_case_id"
)
_VIEW_FILTER_SQL: dict[CoverView, str] = {
    "decide": "cover_group IN ('proposal','choice')",
    "kept": "cover_group='kept'",
    "switched": "cover_group='auto'",
    "all": "1",
}
# Suggestions before choices, as the queue groups them, Strong taste
# suggestions first, then Likely, then the measured rules'; Switched newest first
# in the release rule's order, a fill placed by when it was verified. Each
# order is its row key, so a page resumes strictly after the last row it
# showed (application/review_covers.py).
_VIEW_KEY_SQL: dict[CoverView, str] = {
    "decide": "cover_group='choice',taste_rank,title_key,item_id,review_case_id",
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


def _observed_preview(
    connection: sqlite3.Connection, artifact_id: str
) -> tuple[str, int, int] | None:
    row = connection.execute(_REPLACED_SQL, (artifact_id, _PREVIEW_VARIANT)).fetchone()
    if row is None:
        return None
    preview, width, height = row
    if type(preview) is not str or type(width) is not int or type(height) is not int:
        raise MetadataReportError("review covers are unavailable")
    return preview, width, height


def _replaced(
    connection: sqlite3.Connection, plan_id: str, digest: str
) -> SwitchCover | None:
    found = _observed_preview(connection, "cover_" + digest)
    return None if found is None else SwitchCover(plan_id, *found)


def _replacement(
    connection: sqlite3.Connection, plan_id: str, artifact_id: object
) -> SwitchCover | None:
    """The cover an automatic write put in place, from a report that offered it.

    The work's report still shows the cover it observed before the write until
    the next scan, so Switched names its Now from the verified plan.
    """
    if type(artifact_id) is not str:
        return None
    found = _observed_preview(connection, artifact_id)
    return None if found is None else SwitchCover(plan_id, *found)


def _put_back(
    connection: sqlite3.Connection,
    target: tuple[object, object, object],
    plan_id: str,
) -> PutBack | None:
    row = connection.execute(_PUT_BACK_SQL, (*target, plan_id)).fetchone()
    if row is None:
        return None
    value = json.loads(row[0])
    state, failure = value.get("state"), value.get("failure")
    if type(state) is not str or (failure is not None and type(failure) is not str):
        raise MetadataReportError("review covers are unavailable")
    return PutBack(state, failure)


def _switch_reason(
    connection: sqlite3.Connection, plan_id: str
) -> tuple[str | None, str | None, SwitchTaste | None]:
    """The reason a switch recorded and the language it names; a fill has
    none. A taste switch also names its profile version and the rules its
    verdict cited, as that version holds them (#1372 PR 5, K10)."""
    row = connection.execute(_SWITCH_REASON_SQL, (plan_id,)).fetchone()
    if row is None:
        return None, None, None
    reason, language, verdict_id, version = row
    if (
        (reason is not None and type(reason) is not str)
        or (language is not None and type(language) is not str)
        or (verdict_id is None) is not (version is None)
    ):
        raise MetadataReportError("review covers are unavailable")
    if verdict_id is None:
        return reason, language, None
    taste = connection.execute(
        "SELECT verdict.document,profile.document FROM cover_taste_verdicts verdict "
        "JOIN cover_taste_profile_versions profile ON profile.version=verdict.version "
        "WHERE verdict.verdict_id=? AND verdict.version=?",
        (verdict_id, version),
    ).fetchone()
    if taste is None:
        raise MetadataReportError("review covers are unavailable")
    rules = {
        rule.number: rule
        for rule in TasteProfile.from_rules(json.loads(taste[1])["rules"]).rules
    }
    cited = json.loads(taste[0])["rules"]
    if any(number not in rules for number in cited):
        raise MetadataReportError("review covers are unavailable")
    return (
        reason,
        language,
        SwitchTaste(
            cast(int, version),
            tuple(
                (number, rules[number].section, rules[number].text) for number in cited
            ),
        ),
    )


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
    # The authority is bound for attention, the suggestion and the taste
    # suggestion.
    authority = (*cover_authority, *cover_authority, *cover_authority)
    parameters: list[object] = list(authority)
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
        "switched_at,plan_id,original_digest,replacement,"
        "connection_name,library_name,item_id,suggestion_reason,hold_reason,"
        "cover_taste," + _VIEW_KEY_SQL[view] + " FROM work "
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
            authority,
        ).fetchall()
    )
    if any(group not in COVER_GROUPS for group in grouped):
        raise MetadataReportError("review covers are unavailable")
    page: list[CoverRow] = []
    for row in rows:
        case_id, report, group, choosable, switched_at, plan_id, digest, replacement = (
            row[:8]
        )
        # The work's target, for its owner's current Put back (#1363, Q18).
        target = (row[8], row[9], row[10])
        suggestion, hold, taste = row[11], row[12], row[13]
        key = row[14:]
        if (
            type(case_id) is not str
            or type(report) is not str
            or group not in COVER_GROUPS
            or choosable not in (0, 1)
            or any(type(value) not in (int, str) for value in key)
            or (suggestion is not None and type(suggestion) is not str)
            or (hold is not None and type(hold) is not str)
            or (taste is not None and type(taste) is not int)
        ):
            raise MetadataReportError("review covers are unavailable")
        auto = group == "auto"
        replaced = auto and digest is not None
        reason, language, switch_taste = (
            _switch_reason(connection, cast(str, plan_id))
            if auto
            else (None, None, None)
        )
        page.append(
            CoverRow(
                case_id,
                report,
                cast(CoverGroup, group),
                choosable == 1,
                cast(int, switched_at) if auto else None,
                auto and digest is None,
                _replaced(connection, cast(str, plan_id), cast(str, digest))
                if replaced
                else None,
                _replacement(connection, cast(str, plan_id), replacement)
                if auto
                else None,
                tuple(key),
                _put_back(connection, target, cast(str, plan_id)) if replaced else None,
                replaced
                and connection.execute(
                    _PUT_BACK_OFFERED_SQL, (case_id, plan_id)
                ).fetchone()[0]
                == 1,
                reason,
                language,
                suggestion if group == "proposal" else None,
                None if auto else hold,
                None
                if group != "proposal" or taste is None
                else "strong"
                if taste >= TASTE_STRONG_CONFIDENCE
                else "likely",
                switch_taste,
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


def read_calibration_rows(
    connection: sqlite3.Connection, cover_authority: tuple[str | None, str | None]
) -> tuple[CalibrationRow, ...]:
    """Every work calibration may offer, in its order (#1372 S2, S3)."""
    rows = connection.execute(
        _WORKS_SQL + _CALIBRATION_SQL,
        (*cover_authority,) * 4 + (TASTE_MIN_ARTS,),
    ).fetchall()
    calibration: list[CalibrationRow] = []
    for case_id, report, group, author, series_document in rows:
        series = json.loads(series_document)
        if (
            type(case_id) is not str
            or type(report) is not str
            or group not in ("proposal", "choice", "kept")
            or (author is not None and type(author) is not str)
            or type(series) is not list
            or any(type(name) is not str for name in series)
        ):
            raise MetadataReportError("review covers are unavailable")
        calibration.append(
            CalibrationRow(
                CoverRow(
                    case_id,
                    report,
                    cast(CoverGroup, group),
                    True,
                    None,
                    False,
                    None,
                    None,
                    (),
                ),
                author,
                tuple(series),
            )
        )
    return tuple(calibration)


def read_switch_previews(
    connection: sqlite3.Connection, plan_id: str
) -> frozenset[str]:
    """The previews a Switched row may show for one switch.

    Its original's while listed with no removal, and the cover the verified
    automatic write put in place.
    """
    previews: set[str] = set()
    written = connection.execute(
        "SELECT json_extract(document,'$.plan.desired_value') FROM field_effects "
        "WHERE plan_id=? AND destination='cover' AND state='verified' "
        "AND json_extract(document,'$.plan.authority.origin')='automatic_policy'",
        (plan_id,),
    ).fetchone()
    replacement = (
        None if written is None else _replacement(connection, plan_id, written[0])
    )
    if replacement is not None:
        previews.add(replacement.preview_digest)
    row = connection.execute(
        "SELECT binding.original_digest FROM retained_cover_listings listing "
        "JOIN retained_cover_bindings binding ON binding.plan_id=listing.plan_id "
        "WHERE listing.plan_id=? AND NOT EXISTS (SELECT 1 FROM "
        "retained_cover_removals removal WHERE removal.plan_id=listing.plan_id)",
        (plan_id,),
    ).fetchone()
    replaced = None if row is None else _replaced(connection, plan_id, str(row[0]))
    if replaced is not None:
        previews.add(replaced.preview_digest)
    return frozenset(previews)
