"""The shared review query fragments.

Several aggregates read the same current-review join and the same field
attention predicate. These fragments compose into each other, so they live in
one leaf module that imports only the domain's definitions and is imported
downward, rather than being copied into each aggregate that reads through them.

The text is verbatim. Nothing here is reflowed: these strings concatenate into
executed SQL, and a rewrap would change statements this unit must leave alone.
"""

from __future__ import annotations

from mechanical_librarian.domain.automatic_decision import SAFETY_CATEGORY_LANES

# The owner's authored descriptions are ledger and Automatic's derived
# (ADR-0108). A statement that reads a description decision of either origin
# names `{table}` and runs once per half, joined by UNION ALL; each half is
# keyed and indexed alike, so each arm keeps its own index.
_AUTHORED_DESCRIPTION_TABLES = (
    "owner_authored_descriptions",
    "automatic_description_decisions",
)


def _authored_description_sql(select: str) -> str:
    """`select`, naming `{table}`, over both halves of the description decisions."""
    return " UNION ALL ".join(
        select.replace("{table}", table) for table in _AUTHORED_DESCRIPTION_TABLES
    )


# The display title Activity reads for a report, the first target field's
# value, copied onto the ledger rows that name the report (ADR-0108). Each
# binds the report or the case whose report it is.
_REPORT_TITLE_SQL = (
    "(SELECT json_extract(document,'$.target.fields[0].value') "
    "FROM metadata_reports WHERE report_id=?)"
)
_CASE_TITLE_SQL = (
    "(SELECT json_extract(report.document,'$.target.fields[0].value') "
    "FROM review_cases review JOIN metadata_reports report "
    "ON report.report_id=review.report_id WHERE review.review_case_id=?)"
)
# The report of the case a write plan's decision names, by whichever decision
# table holds it: Activity's join for a plan's rows. Joins `effect.decision_id`.
_PLAN_REPORT_JOIN_SQL = (
    "\n         LEFT JOIN human_decisions human ON human.decision_id=effect.decision_id"
    "\n         LEFT JOIN review_cases human_review ON human_review.review_case_id=human.review_case_id"
    "\n         LEFT JOIN automatic_decisions automatic ON automatic.decision_id=effect.decision_id"
    "\n         LEFT JOIN review_cases automatic_review ON automatic_review.review_case_id=automatic.review_case_id"
    "\n         LEFT JOIN automatic_cover_selections cover_selection ON cover_selection.decision_id=effect.decision_id"
    "\n         LEFT JOIN cover_considerations cover_consideration ON cover_consideration.consideration_id=cover_selection.consideration_id"
    "\n         LEFT JOIN review_cases cover_review ON cover_review.review_case_id=cover_consideration.review_case_id"
    "\n         LEFT JOIN owner_authored_descriptions owner_description ON owner_description.decision_id=effect.decision_id"
    "\n         LEFT JOIN automatic_description_decisions automatic_description ON automatic_description.decision_id=effect.decision_id"
    "\n         LEFT JOIN review_cases description_review ON description_review.review_case_id=COALESCE(owner_description.review_case_id,automatic_description.review_case_id)"
    "\n         LEFT JOIN metadata_reports report ON report.report_id=COALESCE(human_review.report_id,automatic_review.report_id,cover_review.report_id,description_review.report_id)"
)
# The title Activity shows for a plan's rows, binding its decision id.
_PLAN_TITLE_SQL = (
    "(SELECT json_extract(report.document,'$.target.fields[0].value') "
    "FROM (SELECT ? AS decision_id) effect" + _PLAN_REPORT_JOIN_SQL + ")"
)
# When the work was last written: its latest verified effect, of any origin
# and destination, covers included. Admission keeps a case on a recent write
# and preparation sends that case back to admission once it is old (#1523).
_LATEST_VERIFIED_WRITE_SQL = (
    "SELECT MAX(event.occurred_at) FROM field_effects effect "
    "JOIN field_effect_events event ON event.plan_id=effect.plan_id "
    "AND event.kind='verified' WHERE effect.connection_name=? "
    "AND effect.library_name=? AND effect.work_id=?"
)
# When a write landed: the append-only event that took its plan to verified,
# a global append order. Never the plan's row: a superseded automatic plan
# revived after a return to automatic keeps its old row while it lands after
# the owner's newer writes (#1523).
_LANDED_SQL = (
    "(SELECT MAX(landed.rowid) FROM field_effect_events landed "
    "WHERE landed.plan_id=effect.plan_id AND landed.kind='verified')"
)
# The case's write that landed last.
_LANDED_LAST_SQL = "ORDER BY " + _LANDED_SQL + " DESC LIMIT 1"
# A write landed in the case's episode when it landed after the case was last
# admitted, whichever episode planned it (#1973). Binds the case.
_LANDED_IN_EPISODE_SQL = (
    _LANDED_SQL + ">COALESCE((SELECT event_floor FROM review_case_episodes "
    "WHERE review_case_id=?),0) "
)
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
# "Authors and narrators" is one Policy control, so narrators asks under the
# key authors does; an EPUB never reaches it (#1249).
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
    "(result.destination='narrators' AND "
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
# The one stored safety row, read as (paused, global, category document).
_AUTOMATIC_SAFETY_ROW_SQL = (
    "SELECT paused,global_suspension,category_suspensions_document "
    "FROM automatic_safety_state WHERE singleton=1"
)
# Automatic is held for field fact `outcome` by safety row `safety`: the
# negation of `AutomaticSafetyState.permits` over the field's lane.
_AUTOMATIC_SAFETY_BLOCKED_SQL = (
    "safety.paused=1 OR safety.global_suspension IS NOT NULL OR EXISTS("
    "SELECT 1 FROM json_each(safety.category_suspensions_document) category "
    "WHERE json_extract(category.value,'$[0]')=CASE "
    + "".join(
        f"WHEN outcome.policy_category='{category}' THEN '{lane}' "
        for category, lane in SAFETY_CATEGORY_LANES.items()
    )
    + "ELSE outcome.policy_category END)"
)
_AUTOMATIC_SAFETY_ATTENTION_SQL = (
    "EXISTS (SELECT 1 FROM shadow_outcome_field_facts outcome,automatic_safety_state safety "
    "WHERE outcome.review_case_id=rc.review_case_id "
    "AND outcome.destination=result.destination "
    "AND outcome.automatic_destination=1 AND (" + _AUTOMATIC_SAFETY_BLOCKED_SQL + "))"
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
# Whether the case's cover result is an unresolved choice. A comparison
# awaiting refresh keeps a cover in attention by this raw outcome, not by the
# field fact, which reads a kept readable cover as settled (#1310): a reason
# that still queues a kept cover, such as a shadow abstention, keeps it.
_COVER_UNRESOLVED_OUTCOME_SQL = (
    "(SELECT count(*) FROM json_each(rc.document,'$.results') raw_cover "
    "WHERE json_extract(raw_cover.value,'$.destination')='cover' "
    "AND json_extract(raw_cover.value,'$.outcome')='unresolved_choice')"
)
_CURRENT_COVER_ATTENTION_AUTHORITY_SQL = (
    _CURRENT_COVER_AUTHORITY_PREFIX_SQL
    + "AND json_extract(authority_consideration.document,'$.policy_reference')=? "
    + _CURRENT_COVER_AUTHORITY_MANUAL_READY_SQL
    + "AND CASE ml_cover_consideration_current(authority_consideration.currentness_document,?) "
    "WHEN 1 THEN 1 WHEN 2 THEN " + _COVER_UNRESOLVED_OUTCOME_SQL + " ELSE 0 END=1))"
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
# A verified current cover is kept without asking (#1310): its alternatives
# stay choosable on the work's page, but neither its field fact nor its
# consideration puts it in the queue. A case has one cover result, so the probe
# reads the case's document rather than the field's own row.
_VERIFIED_CURRENT_COVER_SQL = (
    "EXISTS (SELECT 1 FROM json_each(rc.document,'$.results') cover_result,"
    "json_each(cover_result.value,'$.reasons') cover_reason "
    "WHERE json_extract(cover_result.value,'$.destination')='cover' "
    "AND cover_reason.value='current_cover_verified')"
)
# A cover the measured stage suggests replacing, or would replace were the
# switch admitted, is optional work: it is in Covers' To decide, never in
# attention (#1363, S9). A title that names another book holds the cover and
# always asks; any other comparison asks only while it is manually ready over
# a cover that is not kept.
_COVER_STAGE_OUTCOME_SQL = (
    "json_extract(consideration.currentness_document,'$.cover_stage.outcome')"
)
# A cover the stage kept with no fault, read or not, is optional too: a read
# that settled nothing keeps it until the next scan, and the owner has no keep
# to record for it (#1362, R1 as amended), as `review_web._cover_stage_kept`
# reads it.
_COVER_STAGE_KEPT_SQL = (
    "(COALESCE(" + _COVER_STAGE_OUTCOME_SQL + ",'')='keep' AND json_extract("
    "consideration.currentness_document,'$.cover_stage.reason') IS NULL)"
)
_COVER_SUGGESTED_SQL = (
    "(result.destination='cover' AND EXISTS (SELECT 1 "
    "FROM current_cover_considerations current_cover "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=current_cover.consideration_id "
    "WHERE current_cover.review_case_id=rc.review_case_id "
    "AND (" + _COVER_STAGE_OUTCOME_SQL + " IN ('suggest','replace') "
    "OR " + _COVER_STAGE_KEPT_SQL + ")))"
)


def _pin_hold_sql(case: str, destination: str, in_force: bool) -> str:
    """Whether the field's hold on the case carries a pin in force, or changed.

    A pin in force settles its field; a changed one asks it again, with or
    without alternatives (ADR-0085, Amendment 2).
    """
    return (
        "EXISTS (SELECT 1 FROM field_effect_manual_holds pin_hold "
        f"WHERE pin_hold.review_case_id={case} AND pin_hold.destination={destination} "
        "AND pin_hold.active=1 AND json_extract(pin_hold.document,'$.pin.in_force')="
        + ("1" if in_force else "0")
        + ")"
    )


# Whether the field's pin on its case is carried as changed.
_PIN_CHANGED_SQL = _pin_hold_sql("rc.review_case_id", "result.destination", False)
# A changed pin asks with or without alternatives, so it stands outside the
# owner-action gate (ADR-0085, Amendment 2).
_REVIEW_FIELD_ATTENTION_REASON_SQL = (
    "(result.has_owner_decision_actions=1 AND ((result.unresolved_choice=1 "
    "AND NOT " + _COVER_SUGGESTED_SQL + ") "
    "OR (result.destination='cover' AND EXISTS (SELECT 1 "
    "FROM current_cover_considerations current_cover "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=current_cover.consideration_id "
    "WHERE current_cover.review_case_id=rc.review_case_id "
    "AND (" + _COVER_STAGE_OUTCOME_SQL + "='hold' OR ("
    "json_extract(consideration.currentness_document,'$.manual_ready')=1 "
    "AND COALESCE(" + _COVER_STAGE_OUTCOME_SQL + ",'') NOT IN ('suggest','replace') "
    "AND NOT " + _COVER_STAGE_KEPT_SQL + " "
    "AND NOT " + _VERIFIED_CURRENT_COVER_SQL + ")))) "
    "OR "
    + _REVIEW_MANUAL_BIBLIOGRAPHIC_SQL
    + " OR "
    + _POLICY_ASK_ATTENTION_SQL
    + " OR "
    + _AUTOMATIC_SAFETY_ATTENTION_SQL
    + " OR "
    + _SHADOW_ABSTENTION_SQL
    + ") OR "
    + _PIN_CHANGED_SQL
    + ")"
)


def _episode_decision_sql(decision: str) -> str:
    """Whether the owner decision row `decision` is of its case's episode.

    A case id is a content hash, so a work that returns to an earlier content
    admits that case again as a new episode. The owner decisions recorded on
    it before then are history: they settle, word and hold nothing in it
    (#1358, ADR-0085). A case admitted once has no episode row.
    """
    return (
        f"{decision}.ordinal>COALESCE((SELECT episode.decision_floor "
        "FROM review_case_episodes episode "
        f"WHERE episode.review_case_id={decision}.review_case_id),0)"
    )


def _active_generation_automatic_sql(decision: str) -> str:
    """Whether the automatic decision row `decision` is of the active generation.

    An automatic decision decides its field only in the policy generation it
    was recorded under (ADR-0036). One from an earlier generation is history:
    it settles, holds and authorizes nothing, and blocks no fresh decision
    (#1573, #1601). The case page applies the same filter to the decisions it
    reads.
    """
    return (
        f"{decision}.policy_generation=(SELECT active_state.generation "
        "FROM metadata_policy_state active_state)"
    )


# The latest manual hold per destination across one work's cases, given its
# connection, library and item: the owner's hold follows the work, not the
# case (ADR-0085). Admission carries it to each case; a withdrawal restores it
# where the withdrawn answer's own hold stood. Equal update times (clocks are
# whole seconds) fall to rowid, which is sound because every owner write
# re-inserts its row and so takes a fresh rowid. Admission's carry copies the
# latest state, so a copy that outranks its source changes nothing, and on a
# re-admitted case it updates the old row in place, never outranking a later
# owner write.
_WORK_LATEST_MANUAL_HOLDS_SQL = (
    "SELECT destination,active,document FROM (SELECT hold.destination,"
    "hold.active,hold.document,row_number() OVER (PARTITION BY "
    "hold.destination ORDER BY hold.updated_at DESC,hold.rowid DESC) "
    "position FROM field_effect_manual_holds hold WHERE "
    "hold.review_case_id IN (SELECT to_review_case_id FROM "
    "review_case_transitions WHERE connection_name=? AND "
    "library_name=? AND item_id=? AND to_review_case_id IS NOT NULL)) "
    "WHERE position=1"
)


# A field is decided by its latest owner decision of the case's episode unless
# that withdrew it (ADR-0085); an earlier decision never settles a field the
# owner took back, nor one the work has since left and returned to. An
# automatic decision settles its field only in the active generation. A pin in
# force settles its field. A pin or unpin row is never the latest decision, so
# an answer under a pin settles its field as it would without one, including
# while its pin waits for the chosen value to be read back. A changed pin with
# no answer standing asks again over an automatic, authored or cover decision
# a returning case still carries (ADR-0085, Amendment 2). A suspension still
# wins.
_REVIEW_FIELD_ATTENTION_CLEAR_SQL = (
    " AND COALESCE((SELECT decision.outcome FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination "
    "AND decision.outcome NOT IN ('pin','unpin') AND "
    + _episode_decision_sql("decision")
    + " ORDER BY decision.ordinal DESC LIMIT 1),'withdraw')='withdraw' "
    "AND NOT " + _pin_hold_sql("rc.review_case_id", "result.destination", True) + " "
    "AND NOT EXISTS (SELECT 1 FROM current_description_decisions authored WHERE authored.review_case_id=rc.review_case_id AND result.destination='description'"
    " AND NOT " + _PIN_CHANGED_SQL + ") "
    "AND NOT EXISTS (SELECT 1 FROM automatic_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination AND "
    + _active_generation_automatic_sql("decision")
    + " AND NOT "
    + _PIN_CHANGED_SQL
    + ") "
    "AND NOT EXISTS (SELECT 1 FROM owner_cover_pointers current_selection "
    "JOIN current_cover_considerations current_cover "
    "ON current_cover.review_case_id=rc.review_case_id "
    "WHERE result.destination='cover' "
    "AND current_selection.connection_name=c.connection_name "
    "AND current_selection.library_name=c.library_name "
    "AND current_selection.item_id=c.item_id AND NOT " + _PIN_CHANGED_SQL + ") "
    "AND NOT EXISTS (SELECT 1 FROM automatic_cover_pointers current_selection "
    "JOIN automatic_cover_selections selection "
    "ON selection.selection_id=current_selection.selection_id "
    "JOIN current_cover_considerations current_cover "
    "ON current_cover.review_case_id=rc.review_case_id "
    "WHERE result.destination='cover' "
    "AND current_cover.consideration_id=selection.consideration_id "
    "AND current_selection.connection_name=c.connection_name "
    "AND current_selection.library_name=c.library_name "
    "AND current_selection.item_id=c.item_id AND NOT " + _PIN_CHANGED_SQL + ") "
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
    "THEN " + _COVER_UNRESOLVED_OUTCOME_SQL + " END "
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
# #1407: a current package no provider matched -- no case, and either no
# admitted work identity or no provider lane that returned candidates -- over
# the package `ml` and its report's browse facts `facts`. Library's lens and
# count and the masthead's preparation buckets read it alike. A work whose
# latest preparation failed terminally is that failure rather than unmatched,
# so it stays where it was: the same latest-work rule Library's row reads.
# Whether a current package's latest preparation failed for good: the failure
# its Library row carries and outranks the package's own outcome (#1407, #1413).
_LATEST_PREPARATION_FAILED_SQL = (
    "COALESCE(("
    "SELECT latest.state='failed' AND latest.failure_code IS NOT NULL "
    "FROM review_preparation_work latest "
    "WHERE latest.connection_name=ml.connection_name "
    "AND latest.library_name=ml.library_name AND latest.item_id=ml.item_id "
    "ORDER BY latest.updated_at DESC,latest.work_id DESC LIMIT 1),0)"
)
_NOT_MATCHED_PACKAGE_SQL = (
    "(ml.review_case_id IS NULL AND (ml.outcome='evidence_only' "
    "OR facts.provider_match_state<>'matched') AND "
    + _LATEST_PREPARATION_FAILED_SQL
    + "=0)"
)
# Its one reason, NULL for any other package. Candidates on an unmatched
# package are an audiobook's that no work identity admitted: found, but refused.
_NOT_MATCHED_REASON_SQL = (
    "CASE WHEN "
    + _NOT_MATCHED_PACKAGE_SQL
    + " THEN CASE WHEN facts.provider_match_state='matched' THEN 'refused' "
    "ELSE facts.provider_match_state END END"
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
# The latest scan of a work's library, the one a work queued again joins
# (#2023): the work's own scan may be an older one the latest replaced.
# Binds the work id.
_LIBRARY_LATEST_SCAN_SQL = (
    "(SELECT latest.scan_id FROM review_preparation_scans latest "
    "JOIN review_preparation_work keyed ON keyed.connection_name=latest.connection_name "
    "AND keyed.library_name=latest.library_name WHERE keyed.work_id=? "
    "ORDER BY latest.requested_at DESC,latest.scan_id DESC LIMIT 1)"
)
# When a closed scan opened again was last written: after the close its
# finished enrollment recorded, even within the same second, and never past
# SQLite's largest integer, where adding one would store a real. Binds the
# time.
_REOPENED_AT_SQL = "MAX(?,updated_at+(updated_at<9223372036854775807))"
# Work queued again needs its library's latest scan open (#2023). A latest
# scan whose discovery failed never listed the library, so it is not opened:
# it is queued for a fresh discovery with the reset a scan request gives a
# closed scan, and the work waits for that discovery to take it over. The
# first binds the time twice and the work id; the second binds the time and
# the work id.
_REDISCOVER_FAILED_LATEST_SCAN_SQL = (
    "UPDATE review_preparation_scans SET state='queued',discovery_failure=NULL,"
    "requested_at=?,updated_at="
    + _REOPENED_AT_SQL
    + ",catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
    "qualification_epoch=NULL,refresh_count=0,rescan_requested=0 WHERE scan_id="
    + _LIBRARY_LATEST_SCAN_SQL
    + " AND discovery_failure IS NOT NULL"
)
_REOPEN_LATEST_SCAN_SQL = (
    "UPDATE review_preparation_scans SET state='processing',updated_at="
    + _REOPENED_AT_SQL
    + " WHERE scan_id="
    + _LIBRARY_LATEST_SCAN_SQL
    + " AND state IN ('completed','completed_with_failures') "
    "AND discovery_failure IS NULL"
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
    "_NOT_MATCHED_PACKAGE_SQL",
    "_NOT_MATCHED_REASON_SQL",
    "_STORED_COVER_ATTENTION_AUTHORITY_SQL",
    "_AUTOMATIC_SAFETY_ATTENTION_SQL",
    "_AUTOMATIC_SAFETY_BLOCKED_SQL",
    "_AUTOMATIC_SAFETY_ROW_SQL",
    "_CURRENT_COVER_ATTENTION_AUTHORITY_SQL",
    "_CURRENT_LIBRARY_FROM_SQL",
    "_CURRENT_REVIEW_CASE_COUNT_SQL",
    "_CURRENT_REVIEW_FROM_SQL",
    "_EPUB_INAPPLICABLE_ATTENTION_SQL",
    "_LANDED_IN_EPISODE_SQL",
    "_LANDED_LAST_SQL",
    "_LANDED_SQL",
    "_COVER_UNRESOLVED_OUTCOME_SQL",
    "_VERIFIED_CURRENT_COVER_SQL",
    "_LATEST_PREPARATION_FAILED_SQL",
    "_LATEST_PREPARATION_WORK_SQL",
    "_POLICY_ASK_ATTENTION_SQL",
    "_REPORT_BROWSE_FACTS_JOIN_SQL",
    "_REVIEW_ATTENTION_SQL",
    "_REVIEW_FIELD_ATTENTION_SQL",
    "_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL",
    "_REVIEW_SEARCH_SQL",
    "_SHADOW_ABSTENTION_SQL",
    "_SHADOW_OUTCOME_HISTORY_SQL",
    "_active_generation_automatic_sql",
    "_episode_decision_sql",
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
        # `w.*` does not carry the rowid, which is the order the works were
        # first discovered in: what makes one that could not be read new (#1351).
        "terminal AS (SELECT w.*,w.rowid AS work_position, "
        + display
        + "FROM review_preparation_work w"
        + join
        + "WHERE w.state='failed' AND w.failure_code IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM current_review_packages p WHERE p.connection_name=w.connection_name "
        "AND p.library_name=w.library_name AND p.item_id=w.item_id))"
    )
