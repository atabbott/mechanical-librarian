"""The decision aggregate: human decisions, automatic decisions, safety state.

This module owns every write to `human_decisions`, `human_decision_events`,
`automatic_decisions` and the singleton `automatic_safety_state`, and the reads
that project them -- the case decision list, the idempotency lookup, and the
review history ledger. Nothing outside it inserts into those four tables.

It was deferred until the review and field-effect aggregates existed because
`record_human_decision` supersedes prior authority and records the owner's
answer (ADR-0108) in the same transaction: its seams cross the boundary and each needs an owner on
the other side to delegate to.

Every seam arrives as a callable resolved through the composing repository at
call time. `_record_automatic_outcomes` is substituted on the composing
repository *instance* by tests which drive it through
`persist_shadow_outcome`; that caller stays on the composing repository, so its
delegating stub is what those tests replace.

`_record_automatic_outcomes` and `_set_automatic_safety_state` are deliberately
scope-free: `persist_shadow_outcome` owns the enclosing transaction and these
run inside it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.description_advice import (
    _advised_reading,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.level_reconciliation import (
    HUMAN_DECISION_INSERT_SQL,
    case_level_unreconciled,
    current_level_offer,
    decision_copies,
    stored_level_offer_documents,
    target_level,
)
from mechanical_librarian.adapters.persistence.owner_field_answers import (
    case_episode,
    case_record_sql,
    mark_pin,
    next_ordinal,
    owner_row_counts_sql,
    record_answer,
    withdraw_answer,
)
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkItem,
    owner_link_bound,
    owner_link_rows,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    request_matching_check,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _AUTOMATIC_SAFETY_ROW_SQL,
    _REPORT_AUTHORS_SQL,
    _REPORT_TITLE_SQL,
    _SHADOW_OUTCOME_HISTORY_SQL,
    _active_generation_automatic_sql,
    _authored_description_sql,
    _episode_decision_sql,
    _pin_record_sql,
    _record_holds_sql,
)
from mechanical_librarian.adapters.persistence.schema import (
    _FIELD_EFFECT_COMPANION_DECISION_SQL,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import OWNER_LINK_HISTORY_EVENT_SCHEMA
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
    audiobook_title_candidate_bundle,
    audiobook_title_subtitle_bundle,
    automatic_safety_category,
    qualification_manifest_sha256,
    title_preservation_bundle,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    parse_automatic_authority,
)
from mechanical_librarian.domain.decision_question import DecisionQuestion
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceCandidate,
)
from mechanical_librarian.domain.field_effect import (
    CLEARABLE_DESTINATIONS,
    FieldEffectError,
    FieldEffectNoChange,
    FieldEffectOperation,
    decision_value_kind,
    field_value_version,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import (
    CARRIED_OUTCOMES,
    HUMAN_DECISION_EVENT_SCHEMA,
    DecisionScope,
    HumanDecision,
    HumanDecisionError,
    case_question,
    chosen_value_fingerprint,
    description_unrestorable,
    pinned_field_fingerprint,
    target_value,
)
from mechanical_librarian.domain.owner_link import OwnerLinkAction, OwnerLinkScope
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
    current_offer,
)
from mechanical_librarian.domain.shadow_outcome import (
    ShadowOutcomeError,
    ShadowOutcomeSet,
)


def _question_halves(
    decision: HumanDecision, case: ReviewCase, offer: tuple[dict[str, object], ...]
) -> tuple[str | None, str | None]:
    """The question an owner answer is given against, for the question check
    at the book's next case or episode (ADR-0108): its offer part and target
    part. A cover, a pin, an unpin and a withdrawal have none."""
    question = (
        case_question(case, offer, decision.destination)
        if decision.outcome in CARRIED_OUTCOMES and decision.destination != "cover"
        else None
    )
    return (
        (None, None)
        if question is None
        else (question.offer_part, question.target_part)
    )


_REPLACEMENT_BLOCKING_EFFECT_SQL = (
    "(state IN ('claimed','applying','reconciling') OR (state='verified' AND ?))"
)
# What the owner did that keeps Automatic from deciding a field on a case in
# a generation: an owner decision of any episode, a withdrawal or an unpin
# included, or a pin on the record, in force or changed (ADR-0085, Amendment
# 2). The record is read by item key through the case's lineage (ADR-0108).
# An owner decision Resume has taken back does not count: one at or below the
# open or latest completed request's `over_ordinal`, or a valueless cause row
# of a field Resume was asked of, so the requeued projection records
# Automatic's decision. Nor does one where the record holds the field's answer
# and either Automatic decided the field on the case in an earlier generation
# (ADR-0085, Amendment 4) or the owner only pinned it and took the pin back
# (Amendment 6): Automatic decides it, and the record refuses the write until
# Resume, so the field shows the held choice and Resume on this case as on a
# later one. A pin still refuses, and so does an answer on a field Automatic
# had not decided on the case, which shows as the owner's answer (Amendment
# 1). A withdrawal still refuses: where it gives the field back to an earlier
# case's answer, the field's latest answer is the withdrawal, which the
# exemption leaves out. Bound by `_owner_mint_conflict`.
_OWNER_MINT_CONFLICT_SQL = (
    "SELECT 1 FROM human_decisions human WHERE human.review_case_id=:case "
    "AND human.destination=:destination AND "
    + owner_row_counts_sql("human")
    + " AND NOT (EXISTS ("
    + case_record_sql(":case", ":destination")
    + ") AND (EXISTS (SELECT 1 FROM automatic_decisions earlier "
    "WHERE earlier.review_case_id=:case AND earlier.destination=:destination "
    "AND earlier.policy_generation<:generation) "
    "OR NOT EXISTS (SELECT 1 FROM human_decisions answer "
    "WHERE answer.review_case_id=:case AND answer.destination=:destination "
    "AND answer.outcome NOT IN ('pin','unpin'))) "
    "AND COALESCE((SELECT latest.outcome FROM human_decisions latest "
    "WHERE latest.review_case_id=:case AND latest.destination=:destination "
    "AND latest.outcome NOT IN ('pin','unpin') "
    "ORDER BY latest.ordinal DESC LIMIT 1),'')<>'withdraw') UNION ALL "
    + case_record_sql(":case", ":destination")
    + " AND record.pin_fingerprint IS NOT NULL"
)
# What keeps Automatic from deciding a field on a case: the owner's conflict
# above, or its own decision in this generation. Bound by
# `_automatic_mint_conflict`.
_AUTOMATIC_MINT_CONFLICT_SQL = (
    _OWNER_MINT_CONFLICT_SQL
    + " UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=:case "
    "AND destination=:destination AND policy_generation=:generation"
)


def _field_effect_authorities_sql(case: str, destination: str) -> str:
    """The decisions whose writes count for the field of the case: the owner
    decisions of its episode, and its automatic and description decisions."""
    return (
        "SELECT waiting_human.decision_id FROM human_decisions waiting_human "
        f"WHERE waiting_human.review_case_id={case} "
        f"AND waiting_human.destination={destination} AND "
        + _episode_decision_sql("waiting_human")
        + " UNION ALL SELECT waiting_automatic.decision_id FROM automatic_decisions "
        f"waiting_automatic WHERE waiting_automatic.review_case_id={case} "
        f"AND waiting_automatic.destination={destination} UNION ALL "
        + _authored_description_sql(
            "SELECT waiting_authored.decision_id FROM {table} waiting_authored "
            "WHERE waiting_authored.review_case_id="
            + case
            + " AND "
            + destination
            + "='description'"
        )
    )


def _field_effect_waiting_sql(case: str, destination: str) -> str:
    """Whether a write for the field waits or was not yet read back on the case.

    The write is planned, in flight or retryable, whichever episode planned
    it, or it landed in the case's episode, under an owner decision of the
    episode, an automatic or a description decision on the case, or as a
    subtitle companion of one. The case's value is then not what the library
    will hold. A reused plan appends no event, so no bound places a live write
    in an episode: one planned before the case was admitted again still waits
    (#1973).
    """
    authorities = _field_effect_authorities_sql(case, destination)
    return (
        "EXISTS (SELECT 1 FROM field_effects waiting_effect WHERE "
        "waiting_effect.rowid IN (SELECT rowid FROM field_effects WHERE decision_id IN ("
        + authorities
        + ") UNION ALL SELECT rowid FROM field_effects WHERE "
        + _FIELD_EFFECT_COMPANION_DECISION_SQL
        + " IN ("
        + authorities
        + ")) AND waiting_effect.state IN "
        "('planned','claimed','applying','reconciling','retryable','verified') "
        "AND (waiting_effect.state<>'verified' OR (SELECT MAX(landed.rowid) "
        "FROM field_effect_events landed WHERE landed.plan_id=waiting_effect.plan_id "
        "AND landed.kind='verified')>COALESCE((SELECT waiting_episode.event_floor "
        "FROM review_case_episodes waiting_episode "
        f"WHERE waiting_episode.review_case_id={case}),0)))"
    )


def field_write_waiting_sql(case: str, destination: str) -> str:
    """Whether a change to the field of the case is still to be written.

    A write for it is planned, in flight or retryable; Automatic's decision of
    the field's latest policy generation on the case chose a value and has no
    verified write; or the description's chosen advice has none. While a pin
    is refused as `pin_change_waiting` and none of these holds, what is left
    is the next scan reading the written value back, which the pin line says
    (#1984). Writes off plan nothing, so an unwritten decision counts by its
    missing verified write rather than by a waiting one.
    """

    def unwritten(alias: str) -> str:
        return (
            "NOT EXISTS (SELECT 1 FROM field_effects written WHERE "
            "written.rowid IN (SELECT rowid FROM field_effects WHERE "
            f"decision_id={alias}.decision_id UNION ALL SELECT rowid FROM "
            "field_effects WHERE "
            + _FIELD_EFFECT_COMPANION_DECISION_SQL
            + f"={alias}.decision_id) AND written.state='verified')"
        )

    authorities = _field_effect_authorities_sql(case, destination)
    return (
        "(EXISTS (SELECT 1 FROM field_effects pending_effect WHERE "
        "pending_effect.rowid IN (SELECT rowid FROM field_effects WHERE decision_id IN ("
        + authorities
        + ") UNION ALL SELECT rowid FROM field_effects WHERE "
        + _FIELD_EFFECT_COMPANION_DECISION_SQL
        + " IN ("
        + authorities
        + ")) AND pending_effect.state IN "
        "('planned','claimed','applying','reconciling','retryable')) "
        "OR EXISTS (SELECT 1 FROM automatic_decisions latest_automatic "
        f"WHERE latest_automatic.review_case_id={case} "
        f"AND latest_automatic.destination={destination} "
        "AND json_extract(latest_automatic.document,'$.outcome')='candidate' "
        "AND latest_automatic.policy_generation=(SELECT MAX(generation.policy_generation) "
        "FROM automatic_decisions generation "
        f"WHERE generation.review_case_id={case} "
        f"AND generation.destination={destination}) AND "
        + unwritten("latest_automatic")
        + f") OR ({destination}='description' AND EXISTS (SELECT 1 FROM "
        "current_description_decisions chosen_text "
        f"WHERE chosen_text.review_case_id={case} AND "
        + unwritten("chosen_text")
        + ")))"
    )


def _standing_decision_sql(column: str, case: str, destination: str) -> str:
    """One column of the field's standing owner decision on the case.

    That is the latest row of the case's episode that is not a pin or an
    unpin, which are never a field's latest decision (ADR-0085, Amendment 2).
    """
    return (
        f"(SELECT standing.{column} FROM human_decisions standing "
        f"WHERE standing.review_case_id={case} "
        f"AND standing.destination={destination} "
        "AND standing.outcome NOT IN ('pin','unpin') AND "
        + _episode_decision_sql("standing")
        + " ORDER BY standing.ordinal DESC LIMIT 1)"
    )


def field_change_waiting_sql(case: str, destination: str) -> str:
    """Whether a pin on the field of the case is refused as `pin_change_waiting`.

    Where no answer of the owner's stands on the case, a write for the field
    waits (`_field_effect_waiting_sql`), or the owner's choice of the advised
    description stands, which a pin would drop unwritten. Where one stands,
    the record does not name it with no Resume open: Resume took it back or
    was asked of it, or another answer replaced it, so a pin there would
    revive a write that is over (ADR-0085, Amendment 2; ADR-0108). It lives
    here rather than in `review_sql`, which imports nothing, because it reads
    the companion expression the schema indexes.
    """
    return (
        "(CASE WHEN COALESCE("
        + _standing_decision_sql("outcome", case, destination)
        + ",'') IN ('accept','keep_current','owner_entered') THEN NOT EXISTS ("
        + case_record_sql(case, destination)
        + " AND record.open_resume_id IS NULL AND record.answer_id="
        + _standing_decision_sql("decision_id", case, destination)
        + ") ELSE ("
        + _field_effect_waiting_sql(case, destination)
        # The owner's own choice of the advised description is recorded
        # apart from the field's answers, and a pin would replace it.
        + f" OR ({destination}='description' AND EXISTS (SELECT 1 FROM "
        "current_description_decisions chosen_advice JOIN owner_authored_descriptions "
        "advice ON advice.decision_id=chosen_advice.decision_id "
        f"WHERE chosen_advice.review_case_id={case}))) END)"
    )


def pin_in_force_sql(case: str, destination: str) -> str:
    """Whether the record's pin on the field reads in force on the current
    case `case`, the own case's waiting write included (S15), as the case
    page and a repeated pin read it."""
    return _pin_record_sql(
        case,
        destination,
        True,
        own_case_waiting=_field_effect_waiting_sql(case, destination),
    )


def _owner_mint_conflict(
    review_case_id: str, destination: str, generation: int
) -> dict[str, str | int]:
    return {
        "case": review_case_id,
        "destination": destination,
        "generation": generation,
    }


def _automatic_mint_conflict(decision: AutomaticDecision) -> dict[str, str | int]:
    return _owner_mint_conflict(
        decision.review_case_id, decision.destination, decision.policy_generation
    )


def owner_keeps_automatic_off(
    connection: sqlite3.Connection,
    review_case_id: str,
    destinations: Iterable[str],
    generation: int,
) -> bool:
    """Whether the owner keeps Automatic off any of these fields of the case
    once it decides in `generation`: by the same rows that refuse its
    decision, or by the record's answer for the field, which refuses the
    write of a decision it records under it until Resume (ADR-0085,
    Amendment 4; ADR-0108). A bundle is decided all or none, so a caller
    passes every field of one.
    """
    return any(
        connection.execute(
            _OWNER_MINT_CONFLICT_SQL,
            _owner_mint_conflict(review_case_id, destination, generation),
        ).fetchone()
        is not None
        or connection.execute(
            case_record_sql("?", "?"), (review_case_id, destination)
        ).fetchone()
        is not None
        for destination in destinations
    )


def _automatic_decision_documents(
    connection: sqlite3.Connection, review_case_id: str
) -> tuple[str, ...]:
    """Read every canonical automatic authority attached to one review case."""
    try:
        rows = connection.execute(
            "SELECT decision_id,destination,idempotency_digest,occurred_at,document "
            "FROM automatic_decisions WHERE review_case_id=? "
            "UNION ALL SELECT selection.decision_id,'cover',"
            "json_extract(json_extract(selection.document,'$.decision_document'),'$.idempotency_digest'),"
            "selection.occurred_at,json_extract(selection.document,'$.decision_document') "
            "FROM automatic_cover_selections selection JOIN cover_considerations consideration "
            "ON consideration.consideration_id=selection.consideration_id "
            "WHERE consideration.review_case_id=? "
            "ORDER BY occurred_at,decision_id",
            (review_case_id, review_case_id),
        ).fetchall()
        documents = []
        for row in rows:
            if len(row) != 5 or type(row[4]) is not str:
                raise MetadataReportError("automatic decision is unavailable")
            decision = parse_automatic_authority(row[4])
            if (
                decision.decision_id,
                decision.destination,
                decision.idempotency_digest,
                decision.occurred_at,
            ) != row[:4] or decision.review_case_id != review_case_id:
                raise MetadataReportError("automatic decision is unavailable")
            documents.append(decision.to_json())
        return tuple(documents)
    except (sqlite3.Error, AutomaticDecisionError, TypeError) as error:
        raise MetadataReportError("automatic decision is unavailable") from error


# History's word for each link row (owner-link-history-event-v2).
_OWNER_LINK_HISTORY_KINDS = {
    OwnerLinkAction.LINK: "linked",
    OwnerLinkAction.REPLACE: "link_changed",
    OwnerLinkAction.WITHDRAW: "link_withdrawn",
}


class _UnreconciledCase(Exception):
    """A decision met a case that waits for its Book matching check."""


class SQLiteDecisionRepository(ConnectionScoped):
    """Record and project one library's human and automatic review decisions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        get_review_case: Callable[[str], str],
        requeue_title_projections: Callable[[int], None],
        review_case_accepts_authority: Callable[[str], bool],
        record_cover_decision_outcome: Callable[..., None],
        revoke_field_effect_authority_in_transaction: Callable[..., int],
        validate_shadow_outcome_row: Callable[
            [tuple[object, ...] | sqlite3.Row], tuple[str, int]
        ],
        previous_field_values: Callable[
            [str, str, str], tuple[tuple[str, object], ...]
        ],
        written_field_values: Callable[[str], tuple[tuple[str, object], ...]],
        recorded_follows: Callable[[HumanDecision], tuple[str | None, bool]],
        followed_write: Callable[[HumanDecision], sqlite3.Row | tuple[str, ...] | None],
        description_form: Callable[[HumanDecision, ReviewCase, object], str | None],
    ) -> None:
        super().__init__(connection, binding)
        self._requeue_title_projections = requeue_title_projections
        self.get_review_case = get_review_case
        self._review_case_accepts_authority = review_case_accepts_authority
        self._record_cover_decision_outcome = record_cover_decision_outcome
        self._revoke_field_effect_authority_in_transaction = (
            revoke_field_effect_authority_in_transaction
        )
        self._validate_shadow_outcome_row = validate_shadow_outcome_row
        self._previous_field_values = previous_field_values
        self._written_field_values = written_field_values
        self._recorded_follows = recorded_follows
        self._followed_write = followed_write
        self._description_form = description_form

    def _record_automatic_outcomes(
        self, outcome: ShadowOutcomeSet, occurred_at: int
    ) -> None:
        """Append eligible dispositions, leaving paused/suspended work human."""
        decisions = self._validated_automatic_decisions(outcome, occurred_at)
        # A normal human winner, a pin or a replay is not an authority failure.
        # Remove its whole bundle before any insert, so a bundle is all-or-none.
        blocked_bundles: set[str] = set()
        for decision in decisions:
            conflict = self._connection.execute(
                _AUTOMATIC_MINT_CONFLICT_SQL, _automatic_mint_conflict(decision)
            ).fetchone()
            if conflict is not None and decision.bundle_id is not None:
                blocked_bundles.add(decision.bundle_id)
        decisions = [
            decision
            for decision in decisions
            if decision.bundle_id not in blocked_bundles
        ]
        # Complete validation precedes every insert: an authority defect cannot
        # leave a subset of a multi-field bundle durable.
        for decision in decisions:
            existing = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if existing is not None:
                # The event timestamp is deliberately not part of the replay
                # key; a later projection retry must reproduce the immutable
                # authority fact rather than manufacture a second decision.
                continue
            conflict = self._connection.execute(
                _AUTOMATIC_MINT_CONFLICT_SQL, _automatic_mint_conflict(decision)
            ).fetchone()
            if conflict is not None:
                # Human wins and independent replay are ordinary fallback, not
                # a global safety event.  Bundles were removed as a unit above.
                continue
            self._connection.execute(
                "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,idempotency_digest,occurred_at,document,policy_generation) VALUES(?,?,?,?,?,?,?)",
                (
                    decision.decision_id,
                    decision.review_case_id,
                    decision.destination,
                    decision.idempotency_digest,
                    decision.occurred_at,
                    decision.to_json(),
                    decision.policy_generation,
                ),
            )

    def _validated_automatic_decisions(
        self, outcome: ShadowOutcomeSet, occurred_at: int
    ) -> list[AutomaticDecision]:
        """Form every decision the outcome qualifies, its bundles coherent.

        Any construction or protected-bundle defect raises before the caller
        inserts anything.
        """
        safety = self.automatic_safety_state()
        automatic_dispositions = {"candidate", "keep_current", "ignore"}
        required_bundles = {
            # Subtitle remains a closed witness for the selected title
            # combination. Independently qualified series decisions use their
            # own category and preserve the proposal's combination evidence.
            "title_layout": ("title",),
            "date_precision": ("publishedDate",),
        }
        bundle_ids: dict[str, str] = {}
        for category, destinations in required_bundles.items():
            if not safety.permits(category):
                continue
            members = tuple(
                field
                for field in outcome.outcomes
                if field.policy_category == category
                and field.policy_destination == "automatic"
                and field.destination in destinations
            )
            all_automatic = bool(members) and all(
                field.disposition in automatic_dispositions for field in members
            )
            evidence_ids = tuple(
                field.combination_ids
                if category == "title_layout"
                else field.citation_ids
                for field in members
            )
            coherent = (
                len(members) == len(destinations)
                and {field.destination for field in members} == set(destinations)
                and all_automatic
                and len(set(evidence_ids)) == 1
                and bool(evidence_ids[0])
            )
            witness = (
                (
                    title_preservation_bundle(members[0])
                    or audiobook_title_candidate_bundle(members[0])
                )
                if category == "title_layout" and len(members) == 1 and all_automatic
                else None
            )
            if witness is not None:
                bundle_ids[category] = witness
                continue
            if not coherent:
                # A fully automatic but incoherent protected tuple is an
                # authority defect, unlike an ordinary abstention/conflict.
                if len(members) == len(destinations) and all_automatic:
                    raise AutomaticDecisionError("category:" + category)
                continue
            bundle_ids[category] = (
                "automatic_bundle_"
                + hashlib.sha256(
                    _canonical_json(
                        {
                            "category": category,
                            "evidence_ids": list(evidence_ids[0]),
                        }
                    ).encode("utf-8")
                ).hexdigest()
            )
        split_requested = any(
            field.policy_category == "subtitle"
            and (field.policy_option or "").startswith("corroborated_split:")
            for field in outcome.outcomes
        )
        split_members = tuple(
            field
            for field in outcome.outcomes
            if field.destination in {"title", "subtitle"}
        )
        split_bundle = (
            audiobook_title_subtitle_bundle(split_members) if split_requested else None
        )
        if split_bundle is not None and not all(
            field.policy_category is not None and safety.permits(field.policy_category)
            for field in split_members
        ):
            split_bundle = None
        if split_requested:
            bundle_ids.pop("title_layout", None)
            if split_bundle is not None and all(
                field.policy_category is not None
                and safety.permits(field.policy_category)
                for field in split_members
            ):
                bundle_ids["title_layout"] = split_bundle
                bundle_ids["subtitle"] = split_bundle
        decisions: list[AutomaticDecision] = []
        for field in outcome.outcomes:
            if (
                (
                    split_requested
                    and field.destination in {"title", "subtitle"}
                    and split_bundle is None
                )
                or field.disposition not in automatic_dispositions
                or field.policy_category is None
                or field.policy_option is None
                or field.policy_destination != "automatic"
                or not safety.permits(
                    automatic_safety_category(
                        field.policy_category, field.policy_option
                    )
                )
                or (
                    field.policy_category in required_bundles
                    and field.policy_category not in bundle_ids
                )
                or (
                    field.policy_category == "date_precision"
                    and field.destination != "publishedDate"
                )
            ):
                continue
            try:
                decision = AutomaticDecision.create(
                    review_case_id=outcome.review_case_id,
                    report_id=outcome.report_id,
                    proposal_set_id=outcome.proposal_set_id,
                    proposal_id=field.proposal_id,
                    target_fingerprint=outcome.target_fingerprint,
                    destination=field.destination,
                    outcome=field.disposition,
                    candidate_id=field.candidate_id,
                    resolved_policy_id=outcome.resolved_policy_id,
                    policy_generation=outcome.policy_generation,
                    rule_id=field.rule_id,
                    policy_category=field.policy_category,
                    policy_option=field.policy_option,
                    qualification_manifest_sha256=qualification_manifest_sha256(
                        field.policy_category, field.policy_option
                    ),
                    source_outcome_id=field.outcome_id,
                    bundle_id=bundle_ids.get(field.policy_category),
                    occurred_at=occurred_at,
                )
            except AutomaticDecisionError as error:
                # Construction and identity/source/manifest binding defects
                # invalidate automatic authority as a whole.  Category
                # suspension is reserved for the explicit protected-bundle
                # coherence check above.
                raise AutomaticDecisionError("authority") from error
            decisions.append(decision)
        return decisions

    @local_operation
    def get_review_case_decisions(
        self, review_case_id: str, case_document: str | None = None
    ) -> tuple[str, ...]:
        """Read a case's decisions; a reader holding the case passes its document."""
        try:
            envelope = json.loads(
                self.get_review_case(review_case_id)
                if case_document is None
                else case_document
            )
            if type(envelope) is not dict or type(envelope.get("case")) is not dict:
                raise MetadataReportError("human decision is unavailable")
            case = ReviewCase.create_from_data(envelope["case"])
            rows = self._connection.execute(
                "SELECT decision_id,proposal_id,target_fingerprint,destination,"
                "actor,outcome,selected_value_document,idempotency_digest,"
                "request_digest,occurred_at,document "
                "FROM human_decisions WHERE review_case_id=? AND "
                + _episode_decision_sql("human_decisions")
                + " ORDER BY ordinal",
                (review_case_id,),
            ).fetchall()
            documents = []
            # Projected once, at the first decision, as `validate_for` did (#1215).
            scope: DecisionScope | None = None
            for row in rows:
                decision = HumanDecision.from_json(row[10])
                if scope is None:
                    scope = DecisionScope.of(
                        case,
                        stored_level_offer_documents(
                            self._connection, case.review_case_id
                        ),
                    )
                decision.validate_in(scope)
                if (
                    decision.decision_id,
                    decision.proposal_id,
                    decision.target_fingerprint,
                    decision.destination,
                    decision.actor,
                    decision.outcome,
                    _canonical_json(decision.selected_value),
                    decision.idempotency_digest,
                    decision.request_digest,
                    decision.occurred_at,
                ) != row[:10]:
                    raise MetadataReportError("human decision is invalid")
                documents.append(decision.to_json())
            documents.extend(
                _automatic_decision_documents(self._connection, review_case_id)
            )
            for row in self._connection.execute(
                _authored_description_sql(
                    "SELECT d.document FROM current_description_decisions p JOIN {table} d ON d.decision_id=p.decision_id WHERE p.review_case_id=?"
                ),
                (review_case_id, review_case_id),
            ):
                authored = AuthoredDescriptionDecision.from_json(row[0])
                if (
                    authored.review_case_id != case.review_case_id
                    or authored.target_fingerprint != case.target_fingerprint
                ):
                    raise MetadataReportError("authored decision is invalid")
                documents.append(authored.to_json())
            return tuple(documents)
        except (sqlite3.Error, HumanDecisionError, TypeError) as error:
            raise MetadataReportError("human decision is unavailable") from error

    @local_operation
    def review_case_level_offer(self, review_case_id: str) -> str | None:
        """The case's level offer at its reconciled level, or None (#1355)."""
        try:
            return current_level_offer(self._connection, review_case_id)
        except sqlite3.Error as error:
            raise MetadataReportError(
                "review case level offer is unavailable"
            ) from error

    @local_operation
    def find_human_decision(self, idempotency_digest: str) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT decision_id,review_case_id,proposal_id,"
                "target_fingerprint,destination,actor,outcome,"
                "selected_value_document,idempotency_digest,"
                "request_digest,occurred_at,document FROM human_decisions "
                "WHERE idempotency_digest=?",
                (idempotency_digest,),
            ).fetchone()
            if row is None:
                return None
            decision = HumanDecision.from_json(row[11])
            stored_columns = (
                decision.decision_id,
                decision.review_case_id,
                decision.proposal_id,
                decision.target_fingerprint,
                decision.destination,
                decision.actor,
                decision.outcome,
                _canonical_json(decision.selected_value),
                decision.idempotency_digest,
                decision.request_digest,
                decision.occurred_at,
            )
            if (
                stored_columns != row[:11]
                or decision.idempotency_digest != idempotency_digest
            ):
                raise MetadataReportError("human decision is unavailable")
            return decision.to_json()
        except (sqlite3.Error, HumanDecisionError, TypeError) as error:
            raise MetadataReportError("human decision is unavailable") from error

    def _record_owner_no_change(
        self, decision: HumanDecision, case: ReviewCase
    ) -> None:
        """Record an owner's accept of the value the case's latest write
        holds as settled with nothing to write, so it is never pending."""
        record = FieldEffectNoChange.create(
            decision_id=decision.decision_id,
            review_case_id=decision.review_case_id,
            report_id=case.report_id,
            connection=str(case.target["connection"]),
            library=str(case.target["library"]),
            work_id=str(case.target["item"]),
            destination=decision.destination,
            target_fingerprint=decision.target_fingerprint,
            target_version=field_value_version(
                decision.destination,
                project_decision_value(decision.destination, decision.selected_value),
            ),
            occurred_at=decision.occurred_at,
            origin="human",
        )
        self._connection.execute(
            "INSERT INTO field_effect_no_changes(decision_id,review_case_id,"
            "report_id,connection_name,library_name,work_id,destination,"
            "target_fingerprint,target_version,origin,occurred_at,event_id,"
            "display_title,display_authors) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,"
            + _REPORT_TITLE_SQL
            + ","
            + _REPORT_AUTHORS_SQL
            + ")",
            (
                record.decision_id,
                record.review_case_id,
                record.report_id,
                record.connection,
                record.library,
                record.work_id,
                record.destination,
                record.target_fingerprint,
                record.target_version,
                record.origin,
                record.occurred_at,
                record.event_id,
                record.report_id,
                record.report_id,
            ),
        )

    @local_operation
    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
        cover_confirmation: dict[str, object] | None = None,
    ) -> str:
        transaction = self._transaction("record_human_decision")
        try:
            transaction.begin()
            replay = self._connection.execute(
                "SELECT request_digest,document FROM human_decisions "
                "WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if replay is not None:
                if replay[0] != decision.request_digest:
                    raise HumanDecisionError("idempotency_conflict")
                try:
                    stored = HumanDecision.from_json(replay[1])
                except HumanDecisionError as error:
                    raise MetadataReportError(
                        "human decision persistence failed"
                    ) from error
                if (
                    stored.idempotency_digest != decision.idempotency_digest
                    or stored.request_digest != replay[0]
                ):
                    raise MetadataReportError("human decision persistence failed")
                self._record_cover_decision_outcome(
                    stored,
                    cover_consideration_id,
                    cover_confirmation,
                    validate_only=True,
                )
                transaction.commit()
                return stored.to_json()
            case_envelope = json.loads(self.get_review_case(decision.review_case_id))
            if (
                type(case_envelope) is not dict
                or case_envelope.get("current") is not True
                or type(case_envelope.get("case")) is not dict
                or not self._review_case_accepts_authority(decision.review_case_id)
            ):
                raise HumanDecisionError("stale")
            case = ReviewCase.create_from_data(case_envelope["case"])
            target = case.target
            # The book link changed since this case's report was prepared
            # (#1477 Q6, L9): every owner write binding the report waits for it.
            if not owner_link_bound(self._connection, case.review_case_id):
                raise HumanDecisionError("link_changed")
            if case_level_unreconciled(self._connection, case.review_case_id):
                raise _UnreconciledCase
            offer = self._require_current_offer(case)
            entered = decision.outcome == "owner_entered"
            written = (
                dict(self._written_field_values(case.review_case_id)) if entered else {}
            )
            decision.validate_new_for(
                case,
                dict(
                    self._previous_field_values(
                        str(target["connection"]),
                        str(target["library"]),
                        str(target["item"]),
                    )
                ).get(decision.destination)
                if entered
                else None,
                offer,
                (written[decision.destination],)
                if decision.destination in written
                else None,
            )
            # The write every decision follows is frozen with it, by the rule
            # planning reads now, so neither a later return nor a later write
            # changes what it replaced or a keep goes back on (#1523, #1973).
            follows, nothing_to_write = self._recorded_follows(decision)
            decision = replace(
                decision,
                follows=follows,
                episode=case_episode(self._connection, case.review_case_id),
            )
            if (
                decision.outcome == "keep_current"
                and decision.follows is not None
                and decision.destination not in CLEARABLE_DESTINATIONS
                and decision.destination != "cover"
                and target_value(case, decision.destination) is None
            ):
                # Keeping the empty value the case read would go back to
                # empty, which this field cannot be (#1523).
                raise HumanDecisionError("owner_value_required")
            if (
                decision.outcome == "keep_current"
                and decision.follows is not None
                and decision.destination == "description"
                and description_unrestorable(case)
            ):
                # Its formatting can't be written back, so the keep would be
                # recorded and its restore refused before any request (#1424).
                raise HumanDecisionError("restore_unavailable")
            self._record_cover_decision_outcome(
                decision, cover_consideration_id, cover_confirmation, validate_only=True
            )
            # A pin over a standing answer, and every unpin, only marks the
            # record's answer: it replaces no decision and revokes no authority.
            pin: dict[str, object] | None = None
            annotation = decision.outcome == "unpin"
            if decision.outcome == "pin":
                standing, pin = self._require_pinnable(decision, case)
                annotation = standing is not None
            elif decision.outcome == "unpin":
                self._require_unpinnable(decision)
            elif decision.outcome == "withdraw":
                self._require_withdrawable(decision)
            previous, authored, automatic = (
                (None, None, None)
                if annotation
                else self._replacement_authorities(decision, case)
            )
            self._insert_owner_decision(decision, case, offer)
            self._record_cover_decision_outcome(
                decision,
                cover_consideration_id,
                cover_confirmation,
                took_over=automatic is not None,
            )
            if annotation:
                # A pin over the standing answer, or an unpin, marks the
                # record's answer; nothing else changes, so its write goes on.
                mark_pin(
                    self._connection,
                    decision.decision_id,
                    None if pin is None else cast(str, pin["fingerprint"]),
                )
            else:
                self._close_replaced_authorities(
                    decision, previous, authored, automatic, pin=pin
                )
            if nothing_to_write:
                # An accept of the value the case's latest write holds is the
                # owner's answer with nothing to write: settled as recorded,
                # never pending (ADR-0105).
                self._record_owner_no_change(decision, case)
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return decision.to_json()
        except _UnreconciledCase:
            # Refused before any write; the case's check is asked for again
            # in its own transaction, so the rollback cannot undo it.
            transaction.rollback()
            self._require_reconciled_case(decision.review_case_id, decision.occurred_at)
            raise HumanDecisionError("offer_changed", "unreconciled_case") from None
        except HumanDecisionError:
            transaction.rollback()
            raise
        except MetadataReportError:
            transaction.rollback()
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            TypeError,
            ValueError,
        ) as error:
            transaction.rollback()
            raise MetadataReportError("human decision persistence failed") from error

    def _insert_owner_decision(
        self,
        decision: HumanDecision,
        case: ReviewCase,
        offer: tuple[dict[str, object], ...],
    ) -> None:
        """Insert an owner decision with its ordinal, copies, question halves
        and audit event, inside the caller's transaction: the rows an owner's
        request and a carried answer both write (ADR-0108)."""
        copies = decision_copies(case, decision.destination)
        self._connection.execute(
            HUMAN_DECISION_INSERT_SQL,
            (
                next_ordinal(self._connection),
                decision.decision_id,
                decision.review_case_id,
                decision.proposal_id,
                decision.target_fingerprint,
                decision.destination,
                decision.actor,
                decision.outcome,
                _canonical_json(decision.selected_value),
                decision.idempotency_digest,
                decision.request_digest,
                decision.occurred_at,
                decision.to_json(),
                *copies,
                # What review history shows an accepted description chose,
                # read from its offer now (#2003, ADR-0108).
                self._chosen_form(decision, case)
                if decision.destination == "description"
                and decision.outcome == "accept"
                else None,
                decision.carried_from,
                *_question_halves(decision, case, offer),
            ),
        )
        self._record_human_decision_event(decision, copies[:3])

    def record_carried_decision(
        self,
        case: ReviewCase,
        offer: tuple[dict[str, object], ...],
        answer_id: str,
        expected: DecisionQuestion,
        written: bool,
        episode: int,
        occurred_at: int,
    ) -> str:
        """Carry the record's answer ``answer_id`` to ``case``, a later case
        or episode of its book asking the same question, inside the caller's
        transaction (ADR-0108, step 4). Returns the carried decision's id.

        The question check runs under the projection's staged-currentness
        guard or the retry's current-case checks, in place of an owner
        request's. The carried row goes through the ordinary recording
        steps: its write follows what the episode last wrote, the
        authorities it replaces are closed (an agreeing automatic decision on
        the case among them), its audit event is `human_decision_carried`,
        and the record takes it, restoring its root. A carry writes no
        no-change row. A constructor refusal raises `HumanDecisionError`.
        """
        row = self._connection.execute(
            "SELECT document FROM human_decisions WHERE decision_id=?",
            (answer_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("human decision persistence failed")
        original = HumanDecision.from_json(row[0])
        target = case.target
        entered = original.outcome == "owner_entered"
        written_values = (
            dict(self._written_field_values(case.review_case_id)) if entered else {}
        )
        decision = HumanDecision.carried(
            case,
            original,
            offer=offer,
            expected=expected,
            written=written,
            episode=episode,
            occurred_at=occurred_at,
            previous=dict(
                self._previous_field_values(
                    str(target["connection"]),
                    str(target["library"]),
                    str(target["item"]),
                )
            ).get(original.destination)
            if entered
            else None,
            case_written=(written_values[original.destination],)
            if original.destination in written_values
            else None,
        )
        follows, _nothing_to_write = self._recorded_follows(decision)
        decision = replace(decision, follows=follows)
        previous, authored, automatic = self._replacement_authorities(decision, case)
        self._insert_owner_decision(decision, case, offer)
        self._close_replaced_authorities(
            decision, previous, authored, automatic, pin=None
        )
        return decision.decision_id

    def _record_human_decision_event(
        self, decision: HumanDecision, item: tuple[str, ...]
    ) -> None:
        """Write the decision's audit event with its item key (ADR-0108):
        `human_decision_carried` for an answer the question check carried,
        else `human_decision_recorded`."""
        kind = (
            "human_decision_recorded"
            if decision.carried_from is None
            else "human_decision_carried"
        )
        event_id = (
            "human_decision_event_"
            + hashlib.sha256(
                (HUMAN_DECISION_EVENT_SCHEMA + ":" + decision.decision_id).encode(
                    "utf-8"
                )
            ).hexdigest()
        )
        event_document = _canonical_json(
            {
                "actor": decision.actor,
                "decision_id": decision.decision_id,
                "destination": decision.destination,
                "event_id": event_id,
                "kind": kind,
                "occurred_at": decision.occurred_at,
                "outcome": decision.outcome,
                "review_case_id": decision.review_case_id,
                "schema_version": HUMAN_DECISION_EVENT_SCHEMA,
            }
        )
        self._connection.execute(
            "INSERT INTO human_decision_events("
            "event_id,decision_id,review_case_id,destination,actor,kind,"
            "occurred_at,document,connection_name,library_name,item_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                decision.decision_id,
                decision.review_case_id,
                decision.destination,
                decision.actor,
                kind,
                decision.occurred_at,
                event_document,
                *item,
            ),
        )

    def _close_replaced_authorities(
        self,
        decision: HumanDecision,
        previous: sqlite3.Row | tuple[object, ...] | None,
        authored: sqlite3.Row | tuple[object, ...] | None,
        automatic: sqlite3.Row | tuple[object, ...] | None,
        *,
        pin: dict[str, object] | None,
    ) -> None:
        """Revoke the replaced and earlier-episode authorities, and write the
        record of the owner's answers (#1358, ADR-0085, ADR-0108).

        A pin where no answer of the owner's stands is the field's answer
        with its fingerprint; a withdrawal takes its case's standing answer
        back. A pin over a standing answer and an unpin never come here; they
        mark the record's answer (ADR-0085, Amendment 2). The case's
        active-generation automatic decision is revoked under every answer,
        so a withdrawal that leaves the record without an answer leaves that
        decision planning nothing either (ADR-0108).

        Runs inside `record_human_decision`'s transaction.
        """
        if previous is not None:
            self._revoke_field_effect_authority_in_transaction(
                previous[0], decision.occurred_at, required=False
            )
        # An earlier episode's decisions authorize nothing now; their
        # plans close with this one (#1358).
        for (earlier,) in self._connection.execute(
            "SELECT decision_id FROM human_decisions WHERE review_case_id=? "
            "AND destination=? AND decision_id<>? AND NOT "
            + _episode_decision_sql("human_decisions"),
            (decision.review_case_id, decision.destination, decision.decision_id),
        ).fetchall():
            self._revoke_field_effect_authority_in_transaction(
                earlier, decision.occurred_at, required=False
            )
        if automatic is not None and (previous is None or automatic[0] != previous[0]):
            self._revoke_field_effect_authority_in_transaction(
                automatic[0], decision.occurred_at, required=False
            )
        if decision.outcome == "withdraw":
            withdraw_answer(self._connection, decision.decision_id)
        else:
            record_answer(
                self._connection,
                "human",
                decision.decision_id,
                pin_fingerprint=None if pin is None else cast(str, pin["fingerprint"]),
            )
        if authored is not None:
            self._connection.execute(
                "DELETE FROM current_description_decisions WHERE review_case_id=?",
                (decision.review_case_id,),
            )

    def _require_pinnable(
        self, decision: HumanDecision, case: ReviewCase
    ) -> tuple[str | None, dict[str, object]]:
        """Refuse a pin in its fixed order; nothing is written (ADR-0085, Amendment 2).

        Returns the owner's standing answer on the case, if one stands, and
        the pin: the chosen value where that answer chose one and the case's
        value otherwise, in force only when the case shows it and no write
        for the field waits.
        """
        field = {
            "review_case_id": decision.review_case_id,
            "destination": decision.destination,
        }
        refused, waiting, standing, outcome, selected = self._connection.execute(
            "SELECT "
            + field_change_waiting_sql(":review_case_id", ":destination")
            + ","
            + _field_effect_waiting_sql(":review_case_id", ":destination")
            + ","
            + ",".join(
                _standing_decision_sql(column, ":review_case_id", ":destination")
                for column in ("decision_id", "outcome", "selected_value_document")
            ),
            field,
        ).fetchone()
        if refused:
            raise HumanDecisionError("pin_change_waiting")
        shown = pinned_field_fingerprint(case, decision.destination)
        fingerprint = (
            chosen_value_fingerprint(decision.destination, json.loads(selected))
            if outcome in {"accept", "owner_entered"}
            else shown
        )
        pin: dict[str, object] = {
            "fingerprint": fingerprint,
            "in_force": fingerprint == shown and not waiting,
        }
        # The same pin again: the record keeps this fingerprint, and reads
        # in force or changed here as this pin would (S15).
        same = self._connection.execute(
            "SELECT "
            + pin_in_force_sql(":review_case_id", ":destination")
            + " FROM current_review_case_lineages lineage "
            "JOIN owner_field_answers record "
            "ON record.connection_name=lineage.connection_name "
            "AND record.library_name=lineage.library_name "
            "AND record.item_id=lineage.item_id "
            "WHERE lineage.review_case_id=:review_case_id "
            "AND record.destination=:destination AND record.pin_fingerprint=:fingerprint",
            {**field, "fingerprint": fingerprint},
        ).fetchone()
        if same is not None and bool(same[0]) is pin["in_force"]:
            raise HumanDecisionError("already_pinned")
        return (
            standing if outcome in {"accept", "keep_current", "owner_entered"} else None
        ), pin

    def _require_unpinnable(self, decision: HumanDecision) -> None:
        """Refuse an unpin where the record's answer for the field carries no pin."""
        if not self._connection.execute(
            case_record_sql("?", "?") + " AND record.pin_fingerprint IS NOT NULL",
            (decision.review_case_id, decision.destination),
        ).fetchone():
            raise HumanDecisionError("not_pinned")

    @local_operation
    def require_reconciled_case(self, review_case_id: str, refused_at: int) -> None:
        """Refuse a decision on a case that waits for its Book matching check.

        Every decision command, cover included, is refused while the case's
        reconciled level is not the configured one (#1355, amendments 4 and
        15). The refusal asks for that case's check again (queued when it is
        neither queued nor running, ahead of nothing), committed before the
        refusal is raised.
        """
        self._require_reconciled_case(review_case_id, refused_at)

    def _require_reconciled_case(self, review_case_id: str, refused_at: int) -> None:
        transaction = self._transaction("require_reconciled_case")
        try:
            transaction.begin()
            # A changed book link answers before the matching check (L9).
            bound = owner_link_bound(self._connection, review_case_id)
            unreconciled = bound and case_level_unreconciled(
                self._connection, review_case_id
            )
            if unreconciled:
                request_matching_check(
                    self._connection,
                    target_level(self._connection).value,
                    updated_at=refused_at,
                    review_case_id=review_case_id,
                )
            transaction.commit()
        except MetadataReportError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("human decision persistence failed") from error
        if not bound:
            raise HumanDecisionError("link_changed")
        if unreconciled:
            raise HumanDecisionError("offer_changed", "unreconciled_case")

    def _replacement_authorities(
        self, decision: HumanDecision, case: ReviewCase
    ) -> tuple[
        sqlite3.Row | tuple[object, ...] | None,
        sqlite3.Row | tuple[object, ...] | None,
        sqlite3.Row | tuple[object, ...] | None,
    ]:
        """Select the authority an owner decision replaces, and refuse
        while any write it would replace is still out with the service.

        Runs inside `record_human_decision`'s transaction and returns the
        decision being replaced, the current authored description decision
        and the automatic decision to hold, in that order.
        """
        cover_automatic = None
        if decision.destination == "cover":
            target = case.target
            cover_automatic = self._connection.execute(
                " UNION ALL ".join(
                    f"SELECT selection.decision_id,'{kind}' "
                    f"FROM {kind}_cover_pointers current "
                    f"JOIN {kind}_cover_selections selection "
                    "ON selection.selection_id=current.selection_id "
                    "WHERE current.connection_name=? AND current.library_name=? "
                    "AND current.item_id=?"
                    for kind in ("owner", "automatic")
                ),
                (target["connection"], target["library"], target["item"]) * 2,
            ).fetchone()
        # Only this episode's decision is replaced; an earlier
        # episode's is history (#1358).
        previous = self._connection.execute(
            "SELECT decision_id,'human' FROM human_decisions "
            "WHERE review_case_id=? AND destination=? "
            "AND outcome NOT IN ('pin','unpin') AND "
            + _episode_decision_sql("human_decisions")
            + " ORDER BY ordinal DESC LIMIT 1",
            (decision.review_case_id, decision.destination),
        ).fetchone()
        authored = None
        if decision.destination == "description":
            authored = self._connection.execute(
                "SELECT decision_id,'authored' FROM current_description_decisions WHERE review_case_id=?",
                (decision.review_case_id,),
            ).fetchone()
            if authored is not None:
                previous = authored
        if cover_automatic is not None:
            previous = cover_automatic
        # Without an owner, authored or cover decision, the decisions
        # being replaced are automatic. Only the active generation's
        # decides the field, but a write any generation's decision still
        # has out with the service blocks replacement until it is
        # reconciled (#1573).
        replaced_automatic: tuple[str, ...] = ()
        if previous is None:
            previous = self._connection.execute(
                "SELECT decision_id,'automatic' FROM automatic_decisions "
                "WHERE review_case_id=? AND destination=? AND "
                + _active_generation_automatic_sql("automatic_decisions"),
                (decision.review_case_id, decision.destination),
            ).fetchone()
            replaced_automatic = tuple(
                str(row[0])
                for row in self._connection.execute(
                    "SELECT decision_id FROM automatic_decisions "
                    "WHERE review_case_id=? AND destination=? "
                    "ORDER BY policy_generation",
                    (decision.review_case_id, decision.destination),
                )
                if previous is None or row[0] != previous[0]
            )
        automatic = self._connection.execute(
            "SELECT decision_id FROM automatic_decisions "
            "WHERE review_case_id=? AND destination=? AND "
            + _active_generation_automatic_sql("automatic_decisions"),
            (decision.review_case_id, decision.destination),
        ).fetchone()
        if cover_automatic is not None and cover_automatic[1] == "automatic":
            automatic = cover_automatic
        if previous is not None:
            effect = self._connection.execute(
                "SELECT 1 FROM field_effects WHERE rowid IN (SELECT rowid FROM field_effects "
                "WHERE decision_id=? UNION ALL SELECT rowid FROM field_effects WHERE "
                + _FIELD_EFFECT_COMPANION_DECISION_SQL
                + "=?) AND "
                + _REPLACEMENT_BLOCKING_EFFECT_SQL
                + " LIMIT 1",
                (previous[0], previous[0], decision.destination == "cover"),
            ).fetchone()
            if effect is not None:
                raise HumanDecisionError("already_decided")
        for earlier in replaced_automatic:
            if self._connection.execute(
                "SELECT 1 FROM field_effects WHERE rowid IN (SELECT rowid FROM field_effects "
                "WHERE decision_id=? UNION ALL SELECT rowid FROM field_effects WHERE "
                + _FIELD_EFFECT_COMPANION_DECISION_SQL
                + "=?) AND "
                + _REPLACEMENT_BLOCKING_EFFECT_SQL
                + " LIMIT 1",
                (earlier, earlier, decision.destination == "cover"),
            ).fetchone():
                raise HumanDecisionError("already_decided")
        return previous, authored, automatic

    def _require_current_offer(self, case: ReviewCase) -> tuple[dict[str, object], ...]:
        """The case's current offer, read inside the command's transaction.

        A choice stands only in the offer its field shows now (#1355, S10), so
        the digest check that follows sees exactly what the write binds.
        """
        try:
            return current_offer(
                case, current_level_offer(self._connection, case.review_case_id)
            )
        except ReviewCaseValueError as error:
            raise MetadataReportError("review case level offer is invalid") from error

    def _matching_changed_history(
        self, review_case_id: str, limit: int
    ) -> list[tuple[int, int, str, dict[str, object]]]:
        """History's events for Book matching's cause rows (#1355, AC2).

        A cause row writes no decision event, so History reads it from the
        decision itself, as "asked again", never as the owner's withdrawal.
        """
        events: list[tuple[int, int, str, dict[str, object]]] = []
        for (
            decision_id,
            destination,
            occurred_at,
            ordinal,
            document,
        ) in self._connection.execute(
            "SELECT decision_id,destination,occurred_at,ordinal,document "
            "FROM human_decisions WHERE review_case_id=? AND "
            "json_extract(document,'$.cause') IS NOT NULL "
            "ORDER BY occurred_at DESC,ordinal DESC LIMIT ?",
            (review_case_id, limit),
        ).fetchall():
            cause = HumanDecision.from_json(document)
            if (
                cause.decision_id != decision_id
                or cause.review_case_id != review_case_id
                or cause.destination != destination
                or cause.occurred_at != occurred_at
                or cause.cause != "matching_changed"
            ):
                raise MetadataReportError("review history is invalid")
            events.append(
                (
                    occurred_at,
                    int(ordinal),
                    "matching:" + decision_id,
                    {
                        "decision_id": decision_id,
                        "destination": destination,
                        "kind": "asked_again_matching",
                        "occurred_at": occurred_at,
                        "origin": "book_matching",
                        "review_case_id": review_case_id,
                        "schema_version": "matching-changed-history-event-v1",
                    },
                )
            )
        return events

    def _require_withdrawable(self, decision: HumanDecision) -> None:
        """Refuse a withdraw in its fixed order; nothing is written (ADR-0085)."""
        latest = self._connection.execute(
            "SELECT decision_id,outcome,carried_from FROM human_decisions "
            "WHERE review_case_id=? AND destination=? "
            "AND outcome NOT IN ('pin','unpin') AND "
            + _episode_decision_sql("human_decisions")
            + " ORDER BY ordinal DESC LIMIT 1",
            (decision.review_case_id, decision.destination),
        ).fetchone()
        if latest is None or latest[1] in {"withdraw", "reject"}:
            raise HumanDecisionError("nothing_to_withdraw")
        if self._connection.execute(
            case_record_sql("?", "?") + " AND record.pin_fingerprint IS NOT NULL",
            (decision.review_case_id, decision.destination),
        ).fetchone():
            raise HumanDecisionError("pinned")
        # Only an automatic decision that settles the field refuses, as
        # Attention and the case page read it: of the active generation
        # (ADR-0036), where the record holds no answer for the field against
        # Automatic. Beside the record's answer it plans nothing, so the
        # answer can be taken back (ADR-0108).
        if self._connection.execute(
            "SELECT 1 FROM automatic_decisions automatic "
            "JOIN current_review_case_lineages lineage "
            "ON lineage.review_case_id=automatic.review_case_id "
            "WHERE automatic.review_case_id=? AND automatic.destination=? AND "
            + _active_generation_automatic_sql("automatic")
            + " AND NOT "
            + _record_holds_sql("lineage", "automatic.destination"),
            (decision.review_case_id, decision.destination),
        ).fetchone():
            raise HumanDecisionError("automatic_hold")
        # A carried copy is written wherever its chain was: whether it can be
        # withdrawn follows `carried_from` to the plans of its root original
        # and every row carried from it (ADR-0108).
        chain = (
            (latest[0],)
            if latest[2] is None
            else tuple(
                str(row[0])
                for row in self._connection.execute(
                    "SELECT decision_id FROM human_decisions "
                    "WHERE decision_id=? OR carried_from=?",
                    (latest[2], latest[2]),
                )
            )
        )
        states = {
            row[0]
            for answer in chain
            for row in self._connection.execute(
                "SELECT state FROM field_effects WHERE rowid IN (SELECT rowid FROM "
                "field_effects WHERE decision_id=? UNION ALL SELECT rowid FROM "
                "field_effects WHERE " + _FIELD_EFFECT_COMPANION_DECISION_SQL + "=?)",
                (answer, answer),
            )
        }
        if states & {"claimed", "applying", "reconciling"}:
            raise HumanDecisionError("already_decided")
        if "verified" in states:
            raise HumanDecisionError("decision_written")

    @local_operation
    def automatic_safety_state(self) -> AutomaticSafetyState:
        try:
            row = self._connection.execute(_AUTOMATIC_SAFETY_ROW_SQL).fetchone()
            if row is None:
                raise MetadataReportError("automatic safety state is unavailable")
            categories = json.loads(row[2])
            return AutomaticSafetyState(
                bool(row[0]), row[1], tuple(tuple(value) for value in categories)
            )
        except (
            sqlite3.Error,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            AutomaticDecisionError,
        ) as error:
            raise MetadataReportError(
                "automatic safety state is unavailable"
            ) from error

    @local_operation
    def set_automatic_safety_state(
        self, state: AutomaticSafetyState, updated_at: int
    ) -> None:
        if (
            type(state) is not AutomaticSafetyState
            or type(updated_at) is not int
            or updated_at < 0
        ):
            raise MetadataReportError("automatic safety state is invalid")
        transaction = self._transaction("set_automatic_safety_state")
        try:
            transaction.begin()
            self._set_automatic_safety_state(state, updated_at)
            transaction.commit()
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "automatic safety state is unavailable"
            ) from error

    @local_operation
    def pause_automatic_safety(
        self, paused: bool, updated_at: int
    ) -> AutomaticSafetyState:
        if type(paused) is not bool or type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("automatic safety state is invalid")
        transaction = self._transaction("pause_automatic_safety")
        try:
            transaction.begin()
            current = self.automatic_safety_state()
            result = AutomaticSafetyState(
                paused, current.global_suspension, current.category_suspensions
            )
            self._set_automatic_safety_state(result, updated_at)
            if not current.permits("title_layout") and result.permits("title_layout"):
                self._requeue_title_projections(updated_at)
            transaction.commit()
            return result
        except BaseException as error:
            transaction.rollback()
            if isinstance(error, (sqlite3.Error, MetadataReportError)):
                raise MetadataReportError(
                    "automatic safety state is unavailable"
                ) from error
            raise

    @local_operation
    def recover_automatic_safety(
        self, updated_at: int, category: str | None
    ) -> AutomaticSafetyState:
        if (
            type(updated_at) is not int
            or updated_at < 0
            or (category is not None and (type(category) is not str or not category))
        ):
            raise MetadataReportError("automatic safety state is invalid")
        transaction = self._transaction("recover_automatic_safety")
        try:
            transaction.begin()
            current = self.automatic_safety_state()
            result = AutomaticSafetyState(
                current.paused,
                None if category is None else current.global_suspension,
                tuple(
                    value
                    for value in current.category_suspensions
                    if value[0] != category
                ),
            )
            self._set_automatic_safety_state(result, updated_at)
            if not current.permits("title_layout") and result.permits("title_layout"):
                self._requeue_title_projections(updated_at)
            transaction.commit()
            return result
        except BaseException as error:
            transaction.rollback()
            if isinstance(error, (sqlite3.Error, MetadataReportError)):
                raise MetadataReportError(
                    "automatic safety state is unavailable"
                ) from error
            raise

    def _set_automatic_safety_state(
        self, state: AutomaticSafetyState, updated_at: int
    ) -> None:
        """Update safety state inside a caller-owned transaction when present."""
        try:
            changed = self._connection.execute(
                "UPDATE automatic_safety_state SET paused=?,global_suspension=?,category_suspensions_document=?,updated_at=? WHERE singleton=1",
                (
                    int(state.paused),
                    state.global_suspension,
                    _canonical_json(
                        [list(value) for value in state.category_suspensions]
                    ),
                    updated_at,
                ),
            ).rowcount
            if changed != 1:
                raise MetadataReportError("automatic safety state is unavailable")
        except (sqlite3.Error, MetadataReportError) as error:
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "automatic safety state is unavailable"
            ) from error

    @local_operation
    def get_review_case_history(
        self, review_case_id: str, limit: int
    ) -> tuple[dict[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MetadataReportError("review history is unavailable")
        try:
            transition_rows = self._connection.execute(
                "SELECT transition_id,checkpoint,kind,reason,from_review_case_id,to_review_case_id "
                "FROM review_case_transitions WHERE from_review_case_id=? OR to_review_case_id=? "
                "ORDER BY checkpoint DESC,transition_id DESC LIMIT ?",
                (review_case_id, review_case_id, limit),
            ).fetchall()
            # Each source cuts at its limit in the order the history displays.
            event_rows = self._connection.execute(
                "SELECT event.event_id,event.decision_id,event.review_case_id,"
                "event.destination,event.actor,event.kind,event.occurred_at,"
                "event.document FROM human_decision_events event "
                "LEFT JOIN human_decisions decision ON decision.decision_id=event.decision_id "
                "WHERE event.review_case_id=? ORDER BY event.occurred_at DESC,"
                "decision.ordinal DESC,event.event_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            shadow_rows = self._connection.execute(
                _SHADOW_OUTCOME_HISTORY_SQL,
                (review_case_id, limit),
            ).fetchall()
            automatic_rows = self._connection.execute(
                "SELECT decision_id,destination,occurred_at,document FROM automatic_decisions "
                "WHERE review_case_id=? ORDER BY occurred_at DESC,decision_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            transitions = [
                {
                    "checkpoint": row[1],
                    "kind": row[2],
                    "reason": row[3],
                    "transition_id": row[0],
                }
                for row in transition_rows
            ]
            events: list[tuple[int, int, str, dict[str, object]]] = []
            for row in event_rows:
                events.append(self._human_decision_history_event(review_case_id, row))
            for row in shadow_rows:
                document, evaluated_at = self._validate_shadow_outcome_row(row)
                outcome = ShadowOutcomeSet.from_json(document)
                if (
                    outcome.outcome_set_id != row[0]
                    or outcome.review_case_id != review_case_id
                ):
                    raise MetadataReportError("review history is invalid")
                dispositions: dict[str, int] = {}
                for field in outcome.outcomes:
                    dispositions[field.disposition] = (
                        dispositions.get(field.disposition, 0) + 1
                    )
                events.append(
                    (
                        evaluated_at,
                        0,
                        "shadow:" + row[0],
                        {
                            "authority": "none",
                            "dispositions": dispositions,
                            "kind": "preview_recorded",
                            "occurred_at": evaluated_at,
                            "origin": "policy_preview",
                            "outcome_set_id": row[0],
                            "review_case_id": review_case_id,
                            "schema_version": "preview-history-event-v1",
                        },
                    )
                )
            events.extend(self._matching_changed_history(review_case_id, limit))
            for decision_id, destination, occurred_at, document in automatic_rows:
                value = json.loads(document)
                if (
                    type(value) is not dict
                    or value.get("schema_version") != "automatic-decision-v1"
                    or value.get("origin") != "automatic_policy"
                    or value.get("decision_id") != decision_id
                    or value.get("review_case_id") != review_case_id
                    or value.get("destination") != destination
                    or value.get("occurred_at") != occurred_at
                ):
                    raise MetadataReportError("review history is invalid")
                events.append(
                    (
                        occurred_at,
                        0,
                        "automatic:" + decision_id,
                        {
                            "decision_id": decision_id,
                            "destination": destination,
                            "kind": "automatic_decision_recorded",
                            "occurred_at": occurred_at,
                            "origin": "automatic_policy",
                            "outcome": value["outcome"],
                            "review_case_id": review_case_id,
                            "schema_version": "automatic-decision-history-event-v1",
                        },
                    )
                )
            events.extend(self._description_advice_history(review_case_id, limit))
            events.extend(self._owner_link_history(review_case_id, limit))
            ordered_events = [value for _, _, _, value in sorted(events, reverse=True)]
            return tuple((ordered_events + transitions)[:limit])
        except (
            sqlite3.Error,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ShadowOutcomeError,
        ) as error:
            raise MetadataReportError("review history is unavailable") from error

    def _owner_link_history(
        self, review_case_id: str, limit: int
    ) -> list[tuple[int, int, str, dict[str, object]]]:
        """History's book link events for the case's work (#1477, S31).

        Every link row of the work, newest first; a withdrawal names the
        link it withdrew. A file link (#1478) names its ISBN or ASIN and no
        source or title; `with_import` marks a row an identifiers file import
        made, its link or its Withdraw this import. A row outside its grammar
        fails the read closed.
        """
        item = self._connection.execute(
            "SELECT report.connection_name,report.library_name,report.item_id "
            "FROM review_cases review JOIN metadata_reports report "
            "ON report.report_id=review.report_id WHERE review.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        if item is None:
            return []
        rows = owner_link_rows(self._connection, OwnerLinkItem(*item))
        events: list[tuple[int, int, str, dict[str, object]]] = []
        for row in rows[-limit:]:
            target = (
                row.target
                if row.target is not None
                else rows[row.sequence - 2].target
                if row.sequence > 1
                else None
            )
            if target is None:
                raise MetadataReportError("review history is invalid")
            events.append(
                (
                    row.occurred_at,
                    row.sequence,
                    "link:" + row.link_row_id,
                    {
                        "identifier": None
                        if target.scope is OwnerLinkScope.WORK
                        else target.identity,
                        "kind": _OWNER_LINK_HISTORY_KINDS[row.action],
                        "link_row_id": row.link_row_id,
                        "occurred_at": row.occurred_at,
                        "provider": target.provider,
                        "review_case_id": review_case_id,
                        "schema_version": OWNER_LINK_HISTORY_EVENT_SCHEMA,
                        "scope": target.scope.value,
                        "source": target.source,
                        "title": target.display_title,
                        "with_import": row.import_id is not None,
                    },
                )
            )
        return events

    def _human_decision_history_event(
        self, review_case_id: str, row: Any
    ) -> tuple[int, int, str, dict[str, object]]:
        """One owner-decision history entry, with its previous and selected values."""
        value = json.loads(row[7])
        if (
            type(value) is not dict
            or set(value)
            != {
                "actor",
                "decision_id",
                "destination",
                "event_id",
                "kind",
                "occurred_at",
                "outcome",
                "review_case_id",
                "schema_version",
            }
            or value["schema_version"] != HUMAN_DECISION_EVENT_SCHEMA
            or (
                value["event_id"],
                value["decision_id"],
                value["review_case_id"],
                value["destination"],
                value["actor"],
                value["kind"],
                value["occurred_at"],
            )
            != row[:7]
        ):
            raise MetadataReportError("review history is invalid")
        decision_row = self._connection.execute(
            "SELECT ordinal,document FROM human_decisions WHERE decision_id=?",
            (row[1],),
        ).fetchone()
        if decision_row is None:
            raise MetadataReportError("review history is invalid")
        decision = HumanDecision.from_json(decision_row[1])
        case_document = json.loads(self.get_review_case(review_case_id))
        if (
            type(case_document) is not dict
            or type(case_document.get("case")) is not dict
        ):
            raise MetadataReportError("review history is invalid")
        case = ReviewCase.create_from_data(case_document["case"])
        fields = case.target.get("fields")
        if type(fields) is not list:
            raise MetadataReportError("review history is invalid")
        target = next(
            (
                field
                for field in fields
                if type(field) is dict
                and field.get("destination") == decision.destination
            ),
            None,
        )
        if type(target) is not dict:
            raise MetadataReportError("review history is invalid")
        previous = target.get("value") if target.get("state") == "value" else None
        previous_formatted = target.get("formatted")
        # Every decision follows the write frozen with it when it was
        # recorded, the one that landed last in its episode before it: never
        # one read again from episode floors that later returns move, nor
        # placed before it by a clock in whole seconds (#1523, #1973).
        prior = self._followed_write(decision)
        if prior is not None:
            plan = FieldEffectOperation.from_json(prior[0]).plan
            previous = (
                plan.subtitle_companion.desired_value
                if decision.destination == "subtitle"
                and plan.subtitle_companion is not None
                else plan.desired_value
            )
            previous_formatted = plan.desired_form
        kind = decision_value_kind(decision.destination)
        # A carried answer keeps its root original's choice: its day (#2039).
        root = (
            None
            if decision.carried_from is None
            else self._connection.execute(
                "SELECT occurred_at FROM human_decisions WHERE decision_id=?",
                (decision.carried_from,),
            ).fetchone()
        )
        value["kept_from"] = None if root is None else int(root[0])
        value["previous_value"] = (
            {"kind": kind, "value": previous} if previous is not None else None
        )
        value["selected_value"] = (
            decision.selected_value
            if decision.outcome in {"accept", "owner_entered"}
            else {"kind": kind, "value": target.get("value")}
            if decision.outcome == "keep_current" and target.get("state") == "value"
            else None
        )
        if decision.destination == "description":
            # Each value's own copy, as it was read, written or chosen (#2003).
            if previous is not None and type(previous_formatted) is str:
                value["previous_formatted"] = previous_formatted
            selected_formatted = (
                self._chosen_form(decision, case)
                if decision.outcome == "accept"
                else target.get("formatted")
                if value["selected_value"] is not None
                else None
            )
            if type(selected_formatted) is str:
                value["selected_formatted"] = selected_formatted
        return (row[6], int(decision_row[0]), row[0], value)

    def _chosen_form(self, decision: HumanDecision, case: ReviewCase) -> str | None:
        """The copy an accepted description writes, as its plan binds it; a
        binding that can't be read leaves it plain (#2003)."""
        try:
            return self._description_form(decision, case, decision.selected_value)
        except FieldEffectError as error:
            if error.code != "authority_binding":
                raise
            return None

    def _description_advice_history(
        self, review_case_id: str, limit: int
    ) -> list[tuple[int, int, str, dict[str, object]]]:
        """Advice attempts, selections and applications, newest first."""
        events: list[tuple[int, int, str, dict[str, object]]] = []
        for (
            attempt_id,
            state,
            engine,
            model,
            at,
            candidate_document,
        ) in self._connection.execute(
            "SELECT a.attempt_id,a.state,a.engine,a.model,a.updated_at,c.document FROM description_advice_attempts a LEFT JOIN description_advice_candidates c ON c.attempt_id=a.attempt_id WHERE json_extract(a.binding_document,'$.review_case_id')=? ORDER BY a.updated_at DESC,a.attempt_id DESC LIMIT ?",
            (review_case_id, limit),
        ):
            events.append(
                (
                    at,
                    0,
                    attempt_id,
                    {
                        "schema_version": "description-advice-history-v1",
                        "kind": "description_advice_recorded",
                        "occurred_at": at,
                        "review_case_id": review_case_id,
                        "state": state,
                        "engine": engine,
                        "model": model,
                        **_advice_reading(
                            None
                            if candidate_document is None
                            else DescriptionAdviceCandidate.from_json(
                                candidate_document
                            )
                        ),
                        "decision": None,
                    },
                )
            )
        for (
            document,
            candidate_document,
            selected_current,
        ) in self._connection.execute(
            "SELECT document,candidate_document,selected_current FROM ("
            + _authored_description_sql(
                "SELECT d.decision_id,d.document,c.document candidate_document,EXISTS(SELECT 1 FROM current_description_decisions current WHERE current.decision_id=d.decision_id) selected_current FROM {table} d JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id WHERE d.review_case_id=?"
            )
            + ") ORDER BY json_extract(document,'$.occurred_at') DESC,decision_id DESC LIMIT ?",
            (review_case_id, review_case_id, limit),
        ):
            authored = AuthoredDescriptionDecision.from_json(document)
            candidate = DescriptionAdviceCandidate.from_json(candidate_document)
            events.append(
                (
                    authored.occurred_at,
                    1,
                    authored.decision_id,
                    {
                        "schema_version": "description-advice-history-v1",
                        "kind": "description_selected",
                        "occurred_at": authored.occurred_at,
                        "review_case_id": review_case_id,
                        "state": "succeeded" if selected_current else "superseded",
                        "engine": candidate.engine,
                        "model": candidate.model,
                        **_advice_reading(candidate),
                        "decision": authored.model_dump(),
                    },
                )
            )
        for (
            event_id,
            at,
            state,
            document,
            candidate_document,
        ) in self._connection.execute(
            _authored_description_sql(
                "SELECT event.event_id,event.occurred_at,event.kind,decision.document,candidate.document "
                "FROM field_effect_events event JOIN field_effects effect ON effect.plan_id=event.plan_id "
                "JOIN {table} decision ON decision.decision_id=effect.decision_id "
                "JOIN description_advice_candidates candidate ON candidate.candidate_id=decision.candidate_id "
                "WHERE decision.review_case_id=? UNION ALL "
                "SELECT event.event_id,event.occurred_at,'no_change_required',decision.document,candidate.document "
                "FROM field_effect_no_changes event JOIN {table} decision ON decision.decision_id=event.decision_id "
                "JOIN description_advice_candidates candidate ON candidate.candidate_id=decision.candidate_id "
                "WHERE decision.review_case_id=?"
            )
            + " ORDER BY 2 DESC,1 DESC LIMIT ?",
            (review_case_id,) * 4 + (limit,),
        ):
            authored = AuthoredDescriptionDecision.from_json(document)
            candidate = DescriptionAdviceCandidate.from_json(candidate_document)
            events.append(
                (
                    at,
                    2,
                    event_id,
                    {
                        "schema_version": "description-advice-history-v1",
                        "kind": "description_application",
                        "occurred_at": at,
                        "review_case_id": review_case_id,
                        "state": state,
                        "engine": candidate.engine,
                        "model": candidate.model,
                        **_advice_reading(candidate),
                        "decision": authored.model_dump(),
                    },
                )
            )
        return events


def _advice_reading(
    candidate: DescriptionAdviceCandidate | None,
) -> dict[str, object]:
    """A History advice item's candidate, and its text as it is written,
    never the adviser's own markup (#2003)."""
    if candidate is None:
        return {"candidate": None, "formatted": None, "visible_text": None}
    return {"candidate": candidate.model_dump(), **_advised_reading(candidate)}
