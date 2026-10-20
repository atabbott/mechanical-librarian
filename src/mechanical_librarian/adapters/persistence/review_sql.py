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
    "(result.destination='description' AND result.has_owner_decision_actions=1 AND "
    "json_extract(policy_resolution.document,'$.destinations.description_fill')='ask') OR "
    "(result.destination='isbn' AND result.has_owner_decision_actions=1 AND "
    "json_extract(policy_resolution.document,'$.destinations.isbn_form')='ask') OR "
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
# A case has at most one current cover pointer. The authority admits a cover
# field when it has none, or when the pointed consideration agrees with the
# case and report rows (`_STORED`), is manually ready, matches the live policy
# reference, and is current or refreshable under the live replay reference.
# The persisted attention facts (#1232) evaluate only the stored part.
_CURRENT_COVER_AUTHORITY_PREFIX_SQL = (
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
    "AND authority_consideration.report_id=mr.report_id "
    "AND json_extract(authority_consideration.document,'$.report_document_digest')="
    "mr.document_digest "
)
_CURRENT_COVER_AUTHORITY_MANUAL_READY_SQL = (
    "AND json_extract(authority_consideration.currentness_document,'$.manual_ready')=1 "
)
_CURRENT_COVER_ATTENTION_AUTHORITY_SQL = (
    _CURRENT_COVER_AUTHORITY_PREFIX_SQL
    + "AND json_extract(authority_consideration.document,'$.policy_reference')=? "
    + _CURRENT_COVER_AUTHORITY_MANUAL_READY_SQL
    + "AND CASE ml_cover_consideration_current(authority_consideration.currentness_document,?) "
    "WHEN 1 THEN 1 WHEN 2 THEN result.unresolved_choice ELSE 0 END=1))"
)
_STORED_COVER_ATTENTION_AUTHORITY_SQL = (
    _CURRENT_COVER_AUTHORITY_PREFIX_SQL
    + _CURRENT_COVER_AUTHORITY_MANUAL_READY_SQL
    + "))"
)
# A field asks for the owner when one of these reasons holds, the cover
# authority below admits it, and nothing in `_REVIEW_FIELD_ATTENTION_CLEAR_SQL`
# has already settled or suspended it. No reason is medium-specific: a
# recording joins the queue under exactly an ebook's conditions (#1249).
_REVIEW_FIELD_ATTENTION_REASON_SQL = (
    "result.has_owner_decision_actions=1 AND (result.unresolved_choice=1 "
    "OR (result.destination='cover' AND EXISTS (SELECT 1 "
    "FROM current_cover_considerations current_cover "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=current_cover.consideration_id "
    "WHERE current_cover.review_case_id=rc.review_case_id "
    "AND json_extract(consideration.currentness_document,'$.manual_ready')=1)) "
    "OR "
    + _REVIEW_MANUAL_BIBLIOGRAPHIC_SQL
    + " OR "
    + _POLICY_ASK_ATTENTION_SQL
    + " OR "
    + _AUTOMATIC_SAFETY_ATTENTION_SQL
    + " OR "
    + _SHADOW_ABSTENTION_SQL
    + ")"
)
_REVIEW_FIELD_ATTENTION_CLEAR_SQL = (
    " AND NOT EXISTS (SELECT 1 FROM human_decisions decision "
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
    "ON current_cover.review_case_id=rc.review_case_id "
    "WHERE result.destination='cover' "
    "AND (selection.kind='owner' OR (selection.kind='automatic' "
    "AND current_cover.consideration_id=selection.consideration_id)) "
    "AND current_selection.connection_name=c.connection_name "
    "AND current_selection.library_name=c.library_name "
    "AND current_selection.item_id=c.item_id) "
    "AND NOT EXISTS (SELECT 1 FROM field_effect_suspensions suspension "
    "WHERE suspension.connection_name=c.connection_name AND ("
    "(suspension.scope='connection' AND suspension.destination='') OR "
    "(suspension.scope='destination' AND suspension.destination="
    "result.destination)))"
)
_REVIEW_FIELD_ATTENTION_SQL = (
    _REVIEW_FIELD_ATTENTION_REASON_SQL
    + " AND "
    + _CURRENT_COVER_ATTENTION_AUTHORITY_SQL
    + _REVIEW_FIELD_ATTENTION_CLEAR_SQL
)
_EPUB_INAPPLICABLE_ATTENTION_SQL = (
    " AND NOT (json_extract(mr.document,'$.file.format')='epub' "
    "AND result.destination IN ('narrators','explicit'))"
)
# A work is in attention when any one of its fields is. `destination` is NOT
# NULL, so the two probes partition the fields exactly; the cover authority
# admits every non-cover field, and its consideration document and currentness
# function are therefore read only for a work no other field already put in
# attention (#1202). The bound authority parameters stay in the cover probe.
_REVIEW_ATTENTION_SQL = (
    "(EXISTS (SELECT 1 FROM review_case_field_facts result WHERE "
    "result.review_case_id=rc.review_case_id AND result.destination<>'cover' AND "
    + _REVIEW_FIELD_ATTENTION_REASON_SQL
    + _REVIEW_FIELD_ATTENTION_CLEAR_SQL
    + _EPUB_INAPPLICABLE_ATTENTION_SQL
    + ") OR EXISTS (SELECT 1 FROM review_case_field_facts result WHERE "
    "result.review_case_id=rc.review_case_id AND result.destination='cover' AND "
    + _REVIEW_FIELD_ATTENTION_SQL
    + _EPUB_INAPPLICABLE_ATTENTION_SQL
    + "))"
)
_LIBRARY_DISPLAY_SEARCH_SQL = (
    "EXISTS (SELECT 1 FROM json_each(?) label "
    "WHERE json_extract(label.value,'$[1]')=ml.library_name "
    "AND json_extract(label.value,'$[0]')=ml.connection_name "
    "AND ml_casefold(COALESCE(json_extract(label.value,'$[2]'),'')) LIKE ? ESCAPE '\\')"
)
# Search and order read the report's persisted browse facts (#1199), never its
# document: a statement joining `metadata_report_browse_facts facts` on
# `mr.report_id` folds nothing per candidate except the package's own keys.
_REPORT_BROWSE_FACTS_JOIN_SQL = (
    " JOIN metadata_report_browse_facts facts ON facts.report_id=mr.report_id "
)
_REVIEW_SEARCH_SQL = (
    " AND (? IS NULL OR facts.title_key LIKE ? ESCAPE '\\' "
    "OR ml_casefold(ml.item_id) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(ml.library_name) LIKE ? ESCAPE '\\' "
    "OR EXISTS (SELECT 1 FROM metadata_report_browse_authors author "
    "WHERE author.report_id=mr.report_id AND author.author_key LIKE ? ESCAPE '\\') "
    "OR facts.format_key LIKE ? ESCAPE '\\' OR " + _LIBRARY_DISPLAY_SEARCH_SQL + ")"
)


def _browse_scalar_sql(sql: str) -> str:
    """Read a shared predicate's document scalars from the persisted browse facts."""
    return sql.replace(
        "json_extract(mr.document,'$.file.format')", "facts.format"
    ).replace(
        "FROM proposal_sets policy_proposal JOIN metadata_policy_resolutions "
        "policy_resolution ON policy_resolution.resolved_policy_id="
        "json_extract(policy_proposal.document,'$.resolved_policy_id')",
        "FROM proposal_set_browse_facts policy_proposal JOIN metadata_policy_resolutions "
        "policy_resolution ON policy_resolution.resolved_policy_id="
        "policy_proposal.resolved_policy_id",
    )


# #1232: one row per current package, report and field that asks for the
# owner on every stored input. A package's attention is read under both the
# report it names (Library) and its case's report (Attention); they are the
# same report whenever the preparation pipeline wrote the package. A cover
# field whose case has a current consideration keeps the parts that depend on
# live authority -- the policy and replay references and provider fetching --
# for `_ATTENTION_FACT_CURRENT_SQL` to decide per request.
_ATTENTION_FACT_SOURCE_SQL = (
    "SELECT c.connection_name,c.library_name,c.item_id,mr.report_id,"
    "result.destination,CASE WHEN result.destination='cover' THEN (SELECT "
    "pointer.consideration_id FROM current_cover_considerations pointer "
    "WHERE pointer.review_case_id=rc.review_case_id) END,"
    "CASE WHEN result.destination='cover' THEN (SELECT "
    "json_extract(pointed.document,'$.policy_reference') "
    "FROM current_cover_considerations pointer JOIN cover_considerations pointed "
    "ON pointed.consideration_id=pointer.consideration_id "
    "WHERE pointer.review_case_id=rc.review_case_id) END,"
    "CASE WHEN result.destination='cover' AND EXISTS (SELECT 1 "
    "FROM current_cover_considerations pointer "
    "WHERE pointer.review_case_id=rc.review_case_id) "
    "THEN result.unresolved_choice END "
    "FROM current_review_packages c "
    "JOIN review_cases rc ON rc.review_case_id=c.review_case_id "
    "JOIN metadata_reports mr ON mr.report_id IN (c.report_id,rc.report_id)"
    + _REPORT_BROWSE_FACTS_JOIN_SQL
    + "JOIN review_case_field_facts result "
    "ON result.review_case_id=rc.review_case_id WHERE "
    + _browse_scalar_sql(
        _REVIEW_FIELD_ATTENTION_REASON_SQL
        + " AND "
        + _STORED_COVER_ATTENTION_AUTHORITY_SQL
        + _REVIEW_FIELD_ATTENTION_CLEAR_SQL
        + _EPUB_INAPPLICABLE_ATTENTION_SQL
    )
)
# The live remainder of `_CURRENT_COVER_ATTENTION_AUTHORITY_SQL` over one
# fact row `f`, binding the same policy and replay references in that order.
_ATTENTION_FACT_CURRENT_SQL = (
    "(f.cover_consideration_id IS NULL OR (f.cover_policy_reference=? AND "
    "CASE ml_cover_consideration_current((SELECT pointed.currentness_document "
    "FROM cover_considerations pointed "
    "WHERE pointed.consideration_id=f.cover_consideration_id),?) "
    "WHEN 1 THEN 1 WHEN 2 THEN f.cover_refresh_admits ELSE 0 END=1))"
)
_PACKAGE_ATTENTION_FACT_SQL = (
    "f.connection_name=ml.connection_name AND f.library_name=ml.library_name "
    "AND f.item_id=ml.item_id AND f.report_id=ml.report_id"
)
# As `_REVIEW_ATTENTION_SQL`, a work's live cover authority is read only when
# no other field already put it in attention.
_LIBRARY_ATTENTION_SQL = (
    "(EXISTS (SELECT 1 FROM review_attention_facts f WHERE "
    + _PACKAGE_ATTENTION_FACT_SQL
    + " AND f.cover_consideration_id IS NULL) OR EXISTS (SELECT 1 "
    "FROM review_attention_facts f WHERE "
    + _PACKAGE_ATTENTION_FACT_SQL
    + " AND f.cover_consideration_id IS NOT NULL AND "
    + _ATTENTION_FACT_CURRENT_SQL
    + "))"
)
_ATTENTION_FACTS_JOIN_SQL = (
    "JOIN review_attention_facts f ON f.connection_name=c.connection_name "
    "AND f.library_name=c.library_name AND f.item_id=c.item_id "
    "AND f.report_id=mr.report_id "
)
_CURRENT_REVIEW_CASE_COUNT_SQL = (
    "SELECT count(*),COALESCE(sum(CASE WHEN "
    + _LIBRARY_ATTENTION_SQL
    + " THEN 1 ELSE 0 END),0) "
    + _CURRENT_LIBRARY_FROM_SQL
    + _REPORT_BROWSE_FACTS_JOIN_SQL
    + "WHERE COALESCE(c.outcome,'')<>'tombstoned'"
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
    "_ATTENTION_FACT_CURRENT_SQL",
    "_ATTENTION_FACT_SOURCE_SQL",
    "_ATTENTION_FACTS_JOIN_SQL",
    "_LIBRARY_ATTENTION_SQL",
    "_STORED_COVER_ATTENTION_AUTHORITY_SQL",
    "_AUTOMATIC_SAFETY_ATTENTION_SQL",
    "_CURRENT_COVER_ATTENTION_AUTHORITY_SQL",
    "_CURRENT_LIBRARY_FROM_SQL",
    "_CURRENT_REVIEW_CASE_COUNT_SQL",
    "_CURRENT_REVIEW_FROM_SQL",
    "_EPUB_INAPPLICABLE_ATTENTION_SQL",
    "_LATEST_PREPARATION_WORK_SQL",
    "_POLICY_ASK_ATTENTION_SQL",
    "_REPORT_BROWSE_FACTS_JOIN_SQL",
    "_REVIEW_ATTENTION_SQL",
    "_REVIEW_FIELD_ATTENTION_SQL",
    "_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL",
    "_REVIEW_SEARCH_SQL",
    "_SHADOW_ABSTENTION_SQL",
    "_SHADOW_OUTCOME_HISTORY_SQL",
]


_TERMINAL_LIBRARY_SEARCH_SQL = (
    " WHERE (? IS NULL OR ml_casefold(item_id) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(library_name) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(display_title) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(display_authors) LIKE ? ESCAPE '\\' "
    "OR ml_casefold(display_format) LIKE ? ESCAPE '\\' OR "
    + _LIBRARY_DISPLAY_SEARCH_SQL.replace("ml.", "terminal.")
    + ")"
)


def _terminal_library_sql(catalogue_attached: bool) -> str:
    """The same current terminal identity relation feeds page and count."""
    display = (
        "COALESCE(NULLIF(e.title,''),'Preparation unavailable') AS display_title,"
        "COALESCE(e.authors,'[]') AS display_authors,"
        "COALESCE(e.format,CASE WHEN e.has_audio=1 THEN 'audio' ELSE 'unknown' END) AS display_format "
        if catalogue_attached
        else "'Preparation unavailable' AS display_title,'[]' AS display_authors,'unknown' AS display_format "
    )
    join = (
        " LEFT JOIN retained_catalogue.catalogue_generations g ON g.current=1 AND g.fingerprint=?"
        " LEFT JOIN retained_catalogue.catalogue_entries e ON e.generation=g.generation "
        "AND e.connection=w.connection_name AND e.library=w.library_name "
        "AND e.library_id=w.library_id AND e.item_id=w.item_id "
        if catalogue_attached
        else " "
    )
    return (
        "terminal AS (SELECT w.*, "
        + display
        + "FROM review_preparation_work w"
        + join
        + "WHERE w.state='failed' AND w.failure_code IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM current_review_packages p WHERE p.connection_name=w.connection_name "
        "AND p.library_name=w.library_name AND p.item_id=w.item_id))"
    )
