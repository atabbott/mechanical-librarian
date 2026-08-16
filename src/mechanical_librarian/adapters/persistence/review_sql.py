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
_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL = (
    "json_extract(result.value,'$.destination') IN ('title','subtitle','series') "
    "AND json_array_length(json_extract(result.value,'$.reasons'))=1 "
    "AND json_extract(result.value,'$.reasons[0]')="
    "'no_safe_bibliographic_combination'"
)
_SHADOW_ABSTENTION_SQL = (
    "EXISTS (SELECT 1 FROM shadow_decision_outcomes shadow,"
    "json_each(shadow.document,'$.outcomes') outcome WHERE "
    "shadow.review_case_id=rc.review_case_id AND "
    "json_extract(outcome.value,'$.destination')=json_extract(result.value,'$.destination') AND "
    "json_extract(outcome.value,'$.disposition')='abstention')"
)
_AUTOMATIC_SAFETY_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM shadow_decision_outcomes shadow,"
    " json_each(shadow.document,'$.outcomes') outcome,automatic_safety_state safety "
    "WHERE shadow.review_case_id=rc.review_case_id "
    "AND json_extract(outcome.value,'$.destination')=json_extract(result.value,'$.destination') "
    "AND json_extract(outcome.value,'$.policy_destination')='automatic' AND ("
    "safety.paused=1 OR safety.global_suspension IS NOT NULL OR EXISTS("
    "SELECT 1 FROM json_each(safety.category_suspensions_document) category "
    "WHERE json_extract(category.value,'$[0]')=json_extract(outcome.value,'$.policy_category'))))"
)
_REVIEW_FIELD_ATTENTION_SQL = (
    "(json_extract(result.value,'$.outcome')='unresolved_choice' "
    "OR "
    + _REVIEW_MANUAL_BIBLIOGRAPHIC_SQL
    + " OR "
    + _AUTOMATIC_SAFETY_ATTENTION_SQL
    + " OR "
    + _SHADOW_ABSTENTION_SQL
    + ") AND NOT EXISTS (SELECT 1 FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=json_extract(result.value,'$.destination')) "
    "AND NOT EXISTS (SELECT 1 FROM automatic_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=json_extract(result.value,'$.destination') "
    ") "
    "AND NOT EXISTS (SELECT 1 FROM field_effect_suspensions suspension "
    "WHERE suspension.connection_name=c.connection_name AND ("
    "(suspension.scope='connection' AND suspension.destination='') OR "
    "(suspension.scope='destination' AND suspension.destination="
    "json_extract(result.value,'$.destination'))))"
)
_REVIEW_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM json_each(rc.document,'$.results') result WHERE "
    + _REVIEW_FIELD_ATTENTION_SQL
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


__all__ = [
    "_AUTOMATIC_SAFETY_ATTENTION_SQL",
    "_CURRENT_LIBRARY_FROM_SQL",
    "_CURRENT_REVIEW_FROM_SQL",
    "_REVIEW_ATTENTION_SQL",
    "_REVIEW_FIELD_ATTENTION_SQL",
    "_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL",
    "_REVIEW_SEARCH_SQL",
    "_SHADOW_ABSTENTION_SQL",
]
