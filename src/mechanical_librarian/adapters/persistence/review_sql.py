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
# An owner's cover choice copies the facts the taste profile counts it by: the
# chosen offer's digest and size, the comparison's art count and the report's
# format (ADR-0108). Each copy reads `selection`, `consideration` and `facts`
# as the join below names them, so the writer and open validation read alike.
_OFFERED_COPY_SQL = (
    "(SELECT json_extract(offered.value,'$.{key}') "
    "FROM json_each(consideration.document,'$.candidates') offered "
    "WHERE json_extract(offered.value,'$.reference')="
    "json_extract(selection.document,'$.candidate_reference') "
    "AND json_extract(offered.value,'$.candidate_kind')='candidate' "
    "AND json_type(offered.value,'$.{key}')='{kind}' "
    "ORDER BY offered.key LIMIT 1)"
)
_OWNER_SELECTION_COPIES = (
    *(
        (key, _OFFERED_COPY_SQL.format(key=key, kind=kind))
        for key, kind in (
            ("transport_sha256", "text"),
            ("width", "integer"),
            ("height", "integer"),
        )
    ),
    (
        "offered_arts",
        "CASE WHEN json_type(consideration.currentness_document,"
        "'$.cover_stage.arts')='integer' THEN json_extract("
        "consideration.currentness_document,'$.cover_stage.arts') END",
    ),
    ("report_format", "CASE WHEN typeof(facts.format)='text' THEN facts.format END"),
)
_OWNER_SELECTION_COPY_JOIN_SQL = (
    "LEFT JOIN cover_considerations consideration "
    "ON consideration.consideration_id=selection.consideration_id "
    "LEFT JOIN metadata_report_browse_facts facts "
    "ON facts.report_id=consideration.report_id"
)
# The display title a ledger row copies from the report it names as `report`:
# the writers below and open validation read the same text (ADR-0108).
_REPORT_TITLE_VALUE_SQL = "json_extract(report.document,'$.target.fields[0].value')"
_REPORT_TITLE_SQL = (
    "(SELECT " + _REPORT_TITLE_VALUE_SQL + " "
    "FROM metadata_reports report WHERE report_id=?)"
)
_CASE_TITLE_SQL = (
    "(SELECT " + _REPORT_TITLE_VALUE_SQL + " "
    "FROM review_cases review JOIN metadata_reports report "
    "ON report.report_id=review.report_id WHERE review.review_case_id=?)"
)
# The authors an owner's ledger row copies from the same report, for the
# author label Activity shows: the first authors field's value as JSON where it
# is a list, as `report_work_labels` reads it (ADR-0108). Automatic rows keep
# none.
_REPORT_AUTHORS_VALUE_SQL = (
    "(SELECT CASE WHEN json_type(field.value,'$.value')='array' "
    "THEN json(json_extract(field.value,'$.value')) END "
    "FROM json_each(report.document,'$.target.fields') field "
    "WHERE json_extract(field.value,'$.destination')='authors' "
    "ORDER BY field.key LIMIT 1)"
)
_REPORT_AUTHORS_SQL = (
    "(SELECT " + _REPORT_AUTHORS_VALUE_SQL + " "
    "FROM metadata_reports report WHERE report_id=?)"
)
_CASE_AUTHORS_SQL = (
    "(SELECT " + _REPORT_AUTHORS_VALUE_SQL + " "
    "FROM review_cases review JOIN metadata_reports report "
    "ON report.report_id=review.report_id WHERE review.review_case_id=?)"
)
# The value an automatic no-change row kept, which Activity reads from the
# report it names as `named`: the report's field for the row's destination as
# JSON ('null' where it holds none), a published date falling back to the
# published year as `canonical_target_fields` reads it (ADR-0108). The owner's
# rows keep none: theirs is their decision's.
_REPORT_FIELD_SQL = (
    "(SELECT field.value FROM json_each(report.document,'$.target.fields') field "
    "WHERE json_extract(field.value,'$.destination')={} "
    "ORDER BY field.key LIMIT 1)"
)
_REPORT_KEPT_VALUE_SQL = (
    "COALESCE(CASE WHEN named.destination='publishedDate' AND "
    + _REPORT_FIELD_SQL.format("'publishedDate'")
    + "->>'$.state' IS NOT 'value' AND "
    + _REPORT_FIELD_SQL.format("'publishedYear'")
    + "->>'$.state'='value' AND json_type("
    + _REPORT_FIELD_SQL.format("'publishedYear'")
    + ",'$.value')='text' THEN "
    + _REPORT_FIELD_SQL.format("'publishedYear'")
    + "->'$.value' ELSE "
    + _REPORT_FIELD_SQL.format("named.destination")
    + "->'$.value' END,'null')"
)
# Binds the row's origin, destination and report id.
_REPORT_KEPT_SQL = (
    "CASE WHEN ?='automatic_policy' THEN (SELECT "
    + _REPORT_KEPT_VALUE_SQL
    + " FROM (SELECT ? AS destination) named "
    "JOIN metadata_reports report ON report.report_id=?) END"
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
    "(SELECT " + _REPORT_TITLE_VALUE_SQL + " "
    "FROM (SELECT ? AS decision_id) effect" + _PLAN_REPORT_JOIN_SQL + ")"
)
# The authors an owner's plan copies, binding its decision id: a plan of an
# owner decision or authored description, never Automatic's.
_PLAN_OWNER_SQL = (
    "(human.decision_id IS NOT NULL OR owner_description.decision_id IS NOT NULL)"
)
_PLAN_AUTHORS_SQL = (
    "(SELECT CASE WHEN "
    + _PLAN_OWNER_SQL
    + " THEN "
    + _REPORT_AUTHORS_VALUE_SQL
    + " END "
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


def _record_key_sql(record: str, item: str, destination: str) -> str:
    """Record row `record` is the field `destination` of the book whose item
    key columns row `item` names."""
    return (
        f"{record}.connection_name={item}.connection_name "
        f"AND {record}.library_name={item}.library_name "
        f"AND {record}.item_id={item}.item_id AND {record}.destination={destination}"
    )


def _pin_record_sql(
    case: str,
    destination: str,
    in_force: bool,
    *,
    item: str | None = None,
    own_case_waiting: str | None = None,
) -> str:
    """Whether the record's pin on the field of the case's book is in force
    there, or changed (ADR-0085, Amendment 2; ADR-0108).

    It is computed when read (S15): in force while `ml_pin_fingerprint` of the
    case's target field is the pin's fingerprint and, on the pin's own case,
    `own_case_waiting` (whether a write for the field waits there) is false.
    An unreadable field has no fingerprint and reads as changed. A pin in
    force settles its field; a changed one asks it again, with or without
    alternatives. The book is `item`'s item key, or the case's current
    lineage's. Attention passes no waiting term: on the pin's own case the
    owner's standing answer settles the field, and a pin with none standing
    is refused while a write waits, so the term cannot change what it reads,
    and leaving it out keeps the field's writes out of Attention's inputs.
    """
    source = (
        "owner_field_answers pin_record WHERE "
        + _record_key_sql("pin_record", item, destination)
        if item is not None
        else "current_review_case_lineages pin_case JOIN owner_field_answers "
        "pin_record ON pin_record.connection_name=pin_case.connection_name "
        "AND pin_record.library_name=pin_case.library_name "
        "AND pin_record.item_id=pin_case.item_id "
        f"WHERE pin_case.review_case_id={case} AND pin_record.destination={destination}"
    )
    waiting = (
        ""
        if own_case_waiting is None
        else f" AND (pin_record.review_case_id<>{case} OR NOT {own_case_waiting})"
    )
    return (
        f"EXISTS (SELECT 1 FROM {source} "
        "AND pin_record.pin_fingerprint IS NOT NULL AND "
        + ("" if in_force else "NOT ")
        + "(pin_record.pin_fingerprint IS ml_pin_fingerprint("
        f"{destination},(SELECT pinned_field.value FROM review_cases pinned_case,"
        "json_each(pinned_case.document,'$.target.fields') pinned_field "
        f"WHERE pinned_case.review_case_id={case} "
        f"AND json_extract(pinned_field.value,'$.destination')={destination}))"
        + waiting
        + "))"
    )


# Whether the field's pin on its case reads as changed.
_PIN_CHANGED_SQL = _pin_record_sql(
    "rc.review_case_id", "result.destination", False, item="c"
)


# A Resume open over a withdrawal that has not failed: nothing has closed or
# failed it yet (ADR-0108, "Automatic settles with a Resume open"). Kept as
# one clause apart from the completed case, the design ruling on C2b-20.
_RESUME_OPEN_UNFAILED_SQL = (
    " OR NOT EXISTS (SELECT 1 FROM owner_field_events answered_resume "
    "WHERE answered_resume.request_id=resumed.event_id)"
)


def _resumed_withdrawal_sql(case: str, destination: str, item: str | None) -> str:
    """Whether a Resume of the field of the case's book covers the owner row
    `withdrawn` (its `over_ordinal` is at or above the row's ordinal) and has
    completed, as `_resumed_decision_sql` reads it (D3), or is open and has
    not failed (`_RESUME_OPEN_UNFAILED_SQL`). The book is `item`'s item key,
    or the case's current lineage's."""
    source = (
        "owner_field_events resumed WHERE "
        + _record_key_sql("resumed", item, destination)
        if item is not None
        else "current_review_case_lineages resumed_case "
        "JOIN owner_field_events resumed "
        "ON resumed.connection_name=resumed_case.connection_name "
        "AND resumed.library_name=resumed_case.library_name "
        "AND resumed.item_id=resumed_case.item_id "
        f"WHERE resumed_case.review_case_id={case} "
        f"AND resumed.destination={destination}"
    )
    return (
        f"EXISTS (SELECT 1 FROM {source} "
        "AND resumed.kind='resume_requested' "
        "AND resumed.over_ordinal>=withdrawn.ordinal AND (EXISTS (SELECT 1 "
        "FROM owner_field_events completed WHERE completed.request_id="
        "resumed.event_id AND completed.kind='resume_completed')"
        + _RESUME_OPEN_UNFAILED_SQL
        + "))"
    )


def _withdrawn_automatic_sql(
    case: str, destination: str, item: str | None = None
) -> str:
    """Whether the owner withdrew the field on case `case` beside Automatic's
    decision there: the episode's latest owner row for the field `destination`,
    pins and unpins aside, is a withdrawal that no Resume took back
    (`_resumed_withdrawal_sql`), and the case holds an active-generation
    automatic decision for it. The owner asked to be asked again, so that
    decision settles nothing on the case (an emptying withdrawal also revoked
    its authority), and the field is asked where the case offers a decision
    on it. A Resume over the withdrawal, completed or open and not failed,
    hands the field back to Automatic, whose decision then settles it. The
    term also matches Book matching's `matching_changed` row; the answer that
    row asked again is read as the case's own (`_read_as_own_sql`), and the
    Attention clear settles it where Automatic decided the field.
    Automatic decides the field from the book's next case or episode
    (ADR-0108, Withdrawal and carry eligibility). The book is `item`'s item
    key, or the case's current lineage's. The case page reads the same term
    (`review_projections`)."""
    return (
        "(COALESCE((SELECT withdrawn.outcome='withdraw' AND NOT "
        + _resumed_withdrawal_sql(case, destination, item)
        + " FROM human_decisions withdrawn "
        f"WHERE withdrawn.review_case_id={case} "
        f"AND withdrawn.destination={destination} "
        "AND withdrawn.outcome NOT IN ('pin','unpin') AND "
        + _episode_decision_sql("withdrawn")
        + " ORDER BY withdrawn.ordinal DESC LIMIT 1),0)=1 AND EXISTS "
        "(SELECT 1 FROM automatic_decisions withdrawn_over WHERE "
        f"withdrawn_over.review_case_id={case} "
        f"AND withdrawn_over.destination={destination} AND "
        + _active_generation_automatic_sql("withdrawn_over")
        + "))"
    )


# The owner withdrew the field beside Automatic's decision on the case, which
# then settles nothing: the field is asked where the case offers a decision.
# `item` names a row with the book's item key columns, or None to reach the
# book through the case's current lineage.
def _withdrawn_asks_sql(item: str | None) -> str:
    return (
        "(result.has_owner_decision_actions=1 AND "
        + _withdrawn_automatic_sql("rc.review_case_id", "result.destination", item)
        + ")"
    )


_WITHDRAWN_ASKS_SQL = _withdrawn_asks_sql("c")


def _case_episode_answer_sql(answer: str, case: str) -> str:
    """Whether the record row `answer` was given on case `case` in its
    current episode."""
    return (
        f"{answer}.review_case_id={case} AND {answer}.episode=COALESCE((SELECT "
        "answer_episode.decision_floor FROM review_case_episodes answer_episode "
        f"WHERE answer_episode.review_case_id={case}),0)"
    )


def _latest_owner_row_sql(case: str, destination: str, read: str) -> str:
    """`read` of the episode's latest owner row on case `case` for the field
    `destination`, pins and unpins aside (alias `latest`), or NULL."""
    return (
        f"(SELECT {read} FROM human_decisions latest WHERE "
        f"latest.review_case_id={case} AND latest.destination={destination} "
        "AND latest.outcome NOT IN ('pin','unpin') AND "
        + _episode_decision_sql("latest")
        + " ORDER BY latest.ordinal DESC LIMIT 1)"
    )


def _own_answer_sql(answer: str, case: str, destination: str) -> str:
    """Whether the record row `answer`, for the field `destination`, is the
    answer case `case` holds as its own: given on it in its current episode,
    while the episode's latest owner row for the field other than a pin or
    unpin is not a withdrawal (C2b-15). The owner's withdrawal there gave the
    field back to the owner, so the answer is then read as one from elsewhere
    (ADR-0108, Attention); Book matching's `matching_changed` row is read
    apart (`_read_as_own_sql`). The case page's own answers read this term
    too (`review_projections`)."""
    return (
        "("
        + _case_episode_answer_sql(answer, case)
        + " AND COALESCE("
        + _latest_owner_row_sql(case, destination, "latest.outcome")
        + ",'')<>'withdraw')"
    )


def _read_as_own_sql(answer: str, case: str, destination: str) -> str:
    """Whether case `case` reads the record row `answer` for the field
    `destination` as its own answer (`_own_answer_sql`), or the row's answer
    is only a pin taken back on any case: a pin row, its pin cleared, no value
    chosen, or the answer was given on the case in its current episode and
    Book matching's `matching_changed` row is the episode's latest owner row
    for the field, pins and unpins aside. Like the case's own answer, such an
    answer settles nothing by itself and holds the field only where Automatic
    decided it, as main's carried hold did; elsewhere the field reads as it
    would without it, never as answered elsewhere (C2b-27, C2b-28; ADR-0085,
    Amendment 1: Book matching's row is not an owner answer and leaves the
    hold untouched). Kept apart so the case page's own answers read the same
    term (`review_projections`)."""
    return (
        f"({answer}.pin_fingerprint IS NULL AND ("
        + _own_answer_sql(answer, case, destination)
        + " OR EXISTS (SELECT 1 FROM human_decisions pin_only WHERE "
        f"pin_only.decision_id={answer}.answer_id AND pin_only.outcome='pin') "
        "OR ("
        + _case_episode_answer_sql(answer, case)
        + " AND COALESCE("
        + _latest_owner_row_sql(
            case,
            destination,
            "latest.outcome='withdraw' AND "
            "json_extract(latest.document,'$.cause')='matching_changed'",
        )
        + ",0)=1)))"
    )


def _record_case_answer_sql() -> str:
    """Whether the record holds an answer for the field of case `rc` (book
    row `c`) that the case reads as its own (`_read_as_own_sql`). A pinned
    answer is never the case's own: its pin settles or asks the field ahead
    of this (ADR-0085, Amendment 2), and once unpinned an answer of the case
    is its own. A cover's answer is the book's own pointer, so this never
    reads it (ADR-0108). An answer from elsewhere asks by
    `asked_again_sql`."""
    return (
        "EXISTS (SELECT 1 FROM owner_field_answers answered WHERE "
        "result.destination<>'cover' AND "
        + _record_key_sql("answered", "c", "result.destination")
        + " AND "
        + _read_as_own_sql("answered", "rc.review_case_id", "result.destination")
        + ")"
    )


def _carry_outcome_sql(answer: str, case: str, outcomes: str) -> str:
    """Whether the question check's outcome for the record row `answer` on
    case `case` in its current episode is one of `outcomes`, an SQL list."""
    return (
        "EXISTS (SELECT 1 FROM carry_outcomes checked WHERE "
        f"checked.answer_id={answer}.answer_id AND checked.review_case_id={case} "
        "AND checked.episode=COALESCE((SELECT checked_episode.decision_floor "
        "FROM review_case_episodes checked_episode "
        f"WHERE checked_episode.review_case_id={case}),0) "
        f"AND checked.outcome IN ({outcomes}))"
    )


def asked_again_sql(answer: str, case: str, destination: str) -> str:
    """Whether case `case` asks the field `destination` again over the
    record row `answer` (ADR-0108, Attention, `asked_again`): an unpinned
    answer the case does not read as its own (`_read_as_own_sql`), which the
    question check did not carry here (the choices or the value changed, it
    was asked again, or the carried row was refused), or which is no longer
    carried at all (withdrawn, retired, superseded by Book matching, or a
    failed Resume closed over it). An answer the check has not yet reached,
    or left waiting, does not ask by itself (contract Q14)."""
    return (
        f"({answer}.pin_fingerprint IS NULL AND NOT "
        + _read_as_own_sql(answer, case, destination)
        + f" AND ({answer}.carry_eligible=0 OR "
        + _carry_outcome_sql(
            answer,
            case,
            "'question_changed','target_changed','asked_again','invalid'",
        )
        + "))"
    )


def carry_waiting_sql(answer: str, case: str) -> str:
    """Whether the question check left the record row `answer` waiting on
    case `case` in its current episode: its plan is still in flight
    (`deferred`), or a Resume over it is open and has not failed
    (`resume_pending`). The field counts as settled meanwhile (ADR-0108,
    Attention)."""
    return (
        "("
        + _carry_outcome_sql(answer, case, "'deferred'")
        + " OR ("
        + _carry_outcome_sql(answer, case, "'resume_pending'")
        + f" AND {answer}.open_resume_id IS NOT NULL AND NOT EXISTS (SELECT 1 "
        "FROM owner_field_events waiting_failed "
        f"WHERE waiting_failed.request_id={answer}.open_resume_id "
        "AND waiting_failed.kind='resume_failed')))"
    )


# The record's answer from an earlier case or episode that the question check
# asks again asks its field only where the case offers the owner a decision
# on it; with none, the answer settles the field (C2b-15, C2b-18, S16;
# ADR-0108).
def _record_asks_sql(item: str = "c") -> str:
    return (
        "(result.has_owner_decision_actions=1 AND EXISTS (SELECT 1 FROM "
        "owner_field_answers answered WHERE result.destination<>'cover' AND "
        + _record_key_sql("answered", item, "result.destination")
        + " AND "
        + asked_again_sql("answered", "rc.review_case_id", "result.destination")
        + "))"
    )


# Asked by a reason beside the proposal's own, as the Attention list flags
# it: the policy's Ask me, the record's answer from an earlier case or
# episode that nothing carried (C2b-15, C2b-18, S16), or a withdrawal beside
# Automatic's decision.
_ASKED_BESIDE_PROPOSAL_SQL = (
    "(" + _POLICY_ASK_ATTENTION_SQL + " OR EXISTS (SELECT 1 FROM "
    "current_review_case_lineages asked_case WHERE "
    "asked_case.review_case_id=rc.review_case_id AND "
    + _record_asks_sql(item="asked_case")
    + ") OR "
    + _withdrawn_asks_sql(None)
    + ")"
)


# A changed pin asks with or without alternatives, so it stands outside the
# owner-action gate (ADR-0085, Amendment 2). An answer the record holds from
# elsewhere asks only where the case offers a decision (C2b-15, C2b-18, S16),
# as does a withdrawal beside Automatic's decision on the case.
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
    + " OR "
    + _record_asks_sql()
    + " OR "
    + _WITHDRAWN_ASKS_SQL
    + ")"
)


# The record holds the field's answer against Automatic: it holds one, and
# no Resume over it is open, or the one open failed (ADR-0108, Attention).
# `item` names a row with the book's item key columns.
def _record_holds_sql(item: str, destination: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM owner_field_answers held WHERE "
        + _record_key_sql("held", item, destination)
        + " AND (held.open_resume_id IS NULL OR EXISTS (SELECT 1 FROM "
        "owner_field_events failed WHERE failed.request_id=held.open_resume_id "
        "AND failed.kind='resume_failed')))"
    )


# Owner decision row `decision` is one a completed Resume took back: at or
# below the `over_ordinal` of a completed request for its field (ADR-0108).
# `item` names a row with the item key columns of the decision's book.
def _resumed_decision_sql(item: str) -> str:
    return (
        "EXISTS (SELECT 1 FROM owner_field_events resumed JOIN owner_field_events "
        "completed ON completed.request_id=resumed.event_id "
        "AND completed.kind='resume_completed' WHERE "
        + _record_key_sql("resumed", item, "decision.destination")
        + " AND resumed.kind='resume_requested' "
        "AND decision.ordinal<=resumed.over_ordinal)"
    )


# Automatic decided the field on case `rc`: an active-generation automatic
# decision, or Automatic's authored description, whether or not the record's
# answer lets it settle the field (main's condition for carrying the owner's
# hold, C2b-27).
_AUTOMATIC_DECIDED_SQL = (
    "(EXISTS (SELECT 1 FROM automatic_decisions decided "
    "WHERE decided.review_case_id=rc.review_case_id "
    "AND decided.destination=result.destination AND "
    + _active_generation_automatic_sql("decided")
    + ") OR EXISTS (SELECT 1 FROM current_description_decisions decided "
    "WHERE decided.review_case_id=rc.review_case_id "
    "AND result.destination='description' AND NOT EXISTS (SELECT 1 FROM "
    "owner_authored_descriptions owner_authored WHERE "
    "owner_authored.decision_id=decided.decision_id)))"
)


# A field the question check left waiting is settled meanwhile (ADR-0108).
_CARRY_WAITING_CLEAR_SQL = (
    " AND NOT EXISTS (SELECT 1 FROM owner_field_answers waiting WHERE "
    "result.destination<>'cover' AND "
    + _record_key_sql("waiting", "c", "result.destination")
    + " AND "
    + carry_waiting_sql("waiting", "rc.review_case_id")
    + ")"
)


# A field is decided by its latest owner decision of the case's episode unless
# that withdrew it (ADR-0085); an earlier decision never settles a field the
# owner took back, nor one the work has since left and returned to, nor one a
# completed Resume took back (ADR-0108). The record's answer the case holds as
# its own settles its field too, but with no decision of the case standing
# (a pin's answer after its unpin) only where Automatic decided the field, as
# main's carried hold did (`_AUTOMATIC_DECIDED_SQL`, C2b-27); one the owner's
# latest row withdrew does not (C2b-15), while one Book matching's row asked
# again is read as the case's own. An automatic decision,
# or Automatic's authored description, settles its field only in the active
# generation and only while the record holds no answer for the field against
# Automatic: none, or one with a Resume open that has not failed; and never
# while the case's latest row withdrew the field beside it, unless a Resume
# over the withdrawal completed or is open and has not failed
# (`_withdrawn_automatic_sql`). A pin in force settles its field. A pin or
# unpin row is never the latest decision, so an answer under a pin settles its
# field as it would without one, including while its pin waits for the chosen
# value to be read back. A changed pin with no answer standing asks again over
# an automatic, authored or cover decision a returning case still carries
# (ADR-0085, Amendment 2). A suspension still wins.
_REVIEW_FIELD_ATTENTION_CLEAR_SQL = (
    " AND COALESCE((SELECT decision.outcome FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination "
    "AND decision.outcome NOT IN ('pin','unpin') AND "
    + _episode_decision_sql("decision")
    + " AND NOT "
    + _resumed_decision_sql("c")
    + " ORDER BY decision.ordinal DESC LIMIT 1),'withdraw')='withdraw' "
    "AND NOT ("
    + _record_case_answer_sql()
    + " AND "
    + _AUTOMATIC_DECIDED_SQL
    + ") AND NOT "
    + _pin_record_sql("rc.review_case_id", "result.destination", True, item="c")
    + _CARRY_WAITING_CLEAR_SQL
    + " AND NOT EXISTS (SELECT 1 FROM current_description_decisions authored WHERE authored.review_case_id=rc.review_case_id AND result.destination='description'"
    " AND NOT " + _PIN_CHANGED_SQL + " AND (EXISTS (SELECT 1 FROM "
    "owner_authored_descriptions owner_authored WHERE "
    "owner_authored.decision_id=authored.decision_id) OR NOT "
    + _record_holds_sql("c", "result.destination")
    + " AND NOT "
    + _withdrawn_automatic_sql("rc.review_case_id", "result.destination", "c")
    + ")) "
    "AND NOT EXISTS (SELECT 1 FROM automatic_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination AND "
    + _active_generation_automatic_sql("decision")
    + " AND NOT "
    + _PIN_CHANGED_SQL
    + " AND NOT "
    + _record_holds_sql("c", "result.destination")
    + " AND NOT "
    + _withdrawn_automatic_sql("rc.review_case_id", "result.destination", "c")
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
    "_ASKED_BESIDE_PROPOSAL_SQL",
    "_REPORT_BROWSE_FACTS_JOIN_SQL",
    "_REVIEW_ATTENTION_SQL",
    "_REVIEW_FIELD_ATTENTION_SQL",
    "_REVIEW_MANUAL_BIBLIOGRAPHIC_SQL",
    "_REVIEW_SEARCH_SQL",
    "_SHADOW_ABSTENTION_SQL",
    "_SHADOW_OUTCOME_HISTORY_SQL",
    "_active_generation_automatic_sql",
    "_episode_decision_sql",
    "_withdrawn_automatic_sql",
    "_own_answer_sql",
    "asked_again_sql",
    "carry_waiting_sql",
    "_read_as_own_sql",
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
