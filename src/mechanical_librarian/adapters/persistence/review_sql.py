"""The shared review query fragments.

Several aggregates read the same current-review join and the same field
attention predicate. These fragments compose into each other, so they live in
one leaf module that imports nothing and is imported downward, rather than
being copied into each aggregate that reads through them.

The text is verbatim. Nothing here is reflowed: these strings concatenate into
executed SQL, and a rewrap would change statements this unit must leave alone.
"""

from __future__ import annotations

_CURRENT_REVIEW_FROM_SQL = (
    "FROM current_review_packages c "
    "JOIN current_review_packages ml ON ml.connection_name=c.connection_name "
    "AND ml.library_name=c.library_name AND ml.item_id=c.item_id "
    "JOIN review_cases rc ON rc.review_case_id=c.review_case_id "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
)
_CURRENT_LIBRARY_FROM_SQL = (
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "LEFT JOIN current_review_packages c "
    "ON c.connection_name=ml.connection_name "
    "AND c.library_name=ml.library_name AND c.item_id=ml.item_id "
    "LEFT JOIN review_cases rc ON rc.review_case_id=ml.review_case_id"
)
_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL = "result.manual_bibliographic_attention=1"
# Original case-bound policy only: automatic projection must not become Ask.
_POLICY_ASK_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM proposal_sets policy_proposal "
    "JOIN metadata_policy_resolutions policy_resolution ON "
    "policy_resolution.resolved_policy_id="
    "json_extract(policy_proposal.document,'$.resolved_policy_id') "
    "AND policy_resolution.library_name=mr.library_name "
    "WHERE policy_proposal.proposal_set_id=rc.proposal_set_id AND ("
    "(result.destination='title' AND "
    "json_extract(policy_resolution.document,'$.destinations.title_layout')='ask') OR "
    "(result.destination='authors' AND "
    "json_extract(policy_resolution.document,'$.destinations.contributor_names')='ask') OR "
    "(result.destination='series' AND "
    "json_extract(policy_resolution.document,'$.destinations.series')='ask') OR "
    "(result.destination='genres' AND result.has_owner_decision_actions=1 AND "
    "json_extract(policy_resolution.document,'$.destinations.genres')='ask') OR "
    "(result.destination='language' AND result.has_owner_decision_actions=1 AND "
    "json_extract(policy_resolution.document,'$.destinations.language_rendering')='ask') OR "
    "(result.destination='subtitle' AND "
    "(json_extract(policy_resolution.document,'$.destinations.title_layout')='ask' OR "
    "json_extract(policy_resolution.document,'$.destinations.subtitle')='ask') AND "
    "json_extract(policy_resolution.document,'$.destinations.title_layout')<>'automatic' AND "
    "json_extract(policy_resolution.document,'$.destinations.subtitle')<>'automatic')))"
)
_SHADOW_ABSTENTION_SQL = (
    "EXISTS (SELECT 1 FROM shadow_outcome_field_facts outcome WHERE "
    "outcome.review_case_id=rc.review_case_id AND "
    "outcome.destination=result.destination AND outcome.disposition='abstention')"
)
_AUTOMATIC_SAFETY_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM shadow_outcome_field_facts outcome,automatic_safety_state safety "
    "WHERE outcome.review_case_id=rc.review_case_id "
    "AND outcome.destination=result.destination "
    "AND outcome.automatic_destination=1 AND ("
    "safety.paused=1 OR safety.global_suspension IS NOT NULL OR EXISTS("
    "SELECT 1 FROM json_each(safety.category_suspensions_document) category "
    "WHERE json_extract(category.value,'$[0]')=CASE WHEN "
    "outcome.policy_category='title' "
    "THEN 'title_layout' ELSE outcome.policy_category END)))"
)
# No automatic authority reaches a recording: F45 forms the case and F47 owns
# the writes, so on an audiobook there is nothing for a settled proposal to
# wait for and every field that still admits a choice is the owner's. The
# `has_owner_decision_actions` gate above is what decides whether one does
# (#966).
_AUDIOBOOK_OWNER_ONLY_ATTENTION_SQL = (
    "json_extract(mr.document,'$.file.format') <> 'epub'"
)
_CURRENT_COVER_ATTENTION_AUTHORITY_SQL = (
    "(result.destination<>'cover' OR NOT EXISTS (SELECT 1 "
    "FROM current_cover_considerations current_cover_pointer "
    "WHERE current_cover_pointer.review_case_id=rc.review_case_id) "
    "OR EXISTS (SELECT 1 "
    "FROM current_cover_considerations current_cover_authority "
    "JOIN cover_considerations authority_consideration "
    "ON authority_consideration.consideration_id="
    "current_cover_authority.consideration_id "
    "WHERE current_cover_authority.review_case_id=rc.review_case_id "
    "AND json_extract(authority_consideration.document,'$.consideration_id')="
    "authority_consideration.consideration_id "
    "AND json_extract(authority_consideration.document,'$.review_case_id')="
    "rc.review_case_id "
    "AND json_extract(authority_consideration.document,'$.report_id')=rc.report_id "
    "AND json_extract(authority_consideration.document,'$.report_document')="
    "mr.document "
    "AND json_extract(authority_consideration.document,'$.policy_reference')=? "
    "AND json_extract(json_extract(authority_consideration.document,"
    "'$.policy_report_document'),'$.owner_ready')=1 "
    "AND CASE ml_cover_consideration_current(authority_consideration.currentness_document,?) "
    "WHEN 1 THEN 1 WHEN 2 THEN result.unresolved_choice ELSE 0 END=1))"
)
_REVIEW_FIELD_ATTENTION_SQL = (
    "result.has_owner_decision_actions=1 AND (result.unresolved_choice=1 "
    "OR (result.destination='cover' AND EXISTS (SELECT 1 "
    "FROM current_cover_considerations current_cover "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=current_cover.consideration_id "
    "WHERE current_cover.review_case_id=rc.review_case_id "
    "AND json_extract(json_extract(consideration.document,'$.policy_report_document'),"
    "'$.owner_ready')=1)) "
    "OR "
    + _AUDIOBOOK_OWNER_ONLY_ATTENTION_SQL
    + " OR "
    + _REVIEW_MANUAL_BIBLIOGRAPHIC_SQL
    + " OR "
    + _POLICY_ASK_ATTENTION_SQL
    + " OR "
    + _AUTOMATIC_SAFETY_ATTENTION_SQL
    + " OR "
    + _SHADOW_ABSTENTION_SQL
    + ") AND "
    + _CURRENT_COVER_ATTENTION_AUTHORITY_SQL
    + " AND NOT EXISTS (SELECT 1 FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination) "
    "AND NOT EXISTS (SELECT 1 FROM current_description_decisions authored WHERE authored.review_case_id=rc.review_case_id AND result.destination='description') "
    "AND NOT EXISTS (SELECT 1 FROM automatic_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination "
    ") "
    "AND NOT EXISTS (SELECT 1 FROM current_cover_selection_authorities current_selection "
    "JOIN cover_selection_authorities selection "
    "ON selection.selection_id=current_selection.selection_id "
    "JOIN current_cover_considerations current_cover "
    "ON current_cover.consideration_id=selection.consideration_id "
    "WHERE result.destination='cover' AND selection.kind='automatic' "
    "AND current_cover.review_case_id=rc.review_case_id "
    "AND current_selection.connection_name=c.connection_name "
    "AND current_selection.library_name=c.library_name "
    "AND current_selection.item_id=c.item_id) "
    "AND NOT EXISTS (SELECT 1 FROM field_effect_suspensions suspension "
    "WHERE suspension.connection_name=c.connection_name AND ("
    "(suspension.scope='connection' AND suspension.destination='') OR "
    "(suspension.scope='destination' AND suspension.destination="
    "result.destination)))"
)
_EPUB_INAPPLICABLE_ATTENTION_SQL = (
    " AND NOT (json_extract(mr.document,'$.file.format')='epub' "
    "AND result.destination IN ('narrators','explicit'))"
)
_REVIEW_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM review_case_field_facts result WHERE "
    "result.review_case_id=rc.review_case_id AND "
    + _REVIEW_FIELD_ATTENTION_SQL
    + _EPUB_INAPPLICABLE_ATTENTION_SQL
    + ")"
)
_REVIEW_SEARCH_SQL = (
    " AND (? IS NULL OR "
    "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')) "
    "LIKE ? ESCAPE '\\' OR ml_casefold(ml.item_id) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(ml.library_name) LIKE ? ESCAPE '\\' "
    "OR EXISTS (SELECT 1 FROM json_each(mr.document,'$.target.fields') author_field, "
    "json_each(author_field.value,'$.value') author WHERE "
    "json_extract(author_field.value,'$.destination')='authors' "
    "AND ml_casefold(COALESCE(author.value,'')) LIKE ? ESCAPE '\\') "
    "OR ml_casefold(COALESCE(json_extract(mr.document,'$.file.format'),'')) "
    "LIKE ? ESCAPE '\\')"
)
_CURRENT_REVIEW_CASE_COUNT_SQL = (
    "SELECT count(*),COALESCE(sum(CASE WHEN "
    + _REVIEW_ATTENTION_SQL
    + " THEN 1 ELSE 0 END),0) "
    + _CURRENT_LIBRARY_FROM_SQL
    + " WHERE COALESCE(c.outcome,'')<>'tombstoned'"
    + _REVIEW_SEARCH_SQL
)
_LATEST_PREPARATION_WORK_SQL = (
    "SELECT work_id,stage,attempt,recovery_attempt,state,failure_code,failure_root "
    "FROM review_preparation_work WHERE connection_name=? AND library_name=? AND item_id=? "
    "ORDER BY updated_at DESC,work_id DESC LIMIT 1"
)
_SHADOW_OUTCOME_HISTORY_SQL = (
    "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
    "resolved_policy_id,source_decision_set_id,evaluated_at,document "
    "FROM shadow_decision_outcomes "
    "WHERE review_case_id=? ORDER BY evaluated_at DESC,outcome_set_id DESC LIMIT ?"
)


__all__ = [
    "_AUTOMATIC_SAFETY_ATTENTION_SQL",
    "_CURRENT_COVER_ATTENTION_AUTHORITY_SQL",
    "_CURRENT_LIBRARY_FROM_SQL",
    "_CURRENT_REVIEW_CASE_COUNT_SQL",
    "_CURRENT_REVIEW_FROM_SQL",
    "_AUDIOBOOK_OWNER_ONLY_ATTENTION_SQL",
    "_EPUB_INAPPLICABLE_ATTENTION_SQL",
    "_LATEST_PREPARATION_WORK_SQL",
    "_POLICY_ASK_ATTENTION_SQL",
    "_REVIEW_ATTENTION_SQL",
    "_REVIEW_FIELD_ATTENTION_SQL",
    "_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL",
    "_REVIEW_SEARCH_SQL",
    "_SHADOW_ABSTENTION_SQL",
    "_SHADOW_OUTCOME_HISTORY_SQL",
]
