"""Reconcile a case to the configured Book matching level (#1355, S10).

A case is born reconciled: in the transaction that writes it, at a wider
configured level, its level set, the would-admit notes its level decision set
leaves for live manual decisions, its level offer (when the level substitutes
a manual field) and its reconciled-level marker are written from stored data
alone, with no external request. At ``identifier`` nothing is written, no
marker reads as ``identifier``, and a stored wider marker becomes
``identifier``.

The configured level is ``review_preparation_control.work_identity_level``,
which every gate compares a case's marker with (``level_sql``). When the level
changes, the projection reconciles each case again (``reconcile_case_level``):
a field whose offer changed under its standing owner choice is asked again
with one valueless ``matching_changed`` row and that choice's pending write
closes, in the transaction that moves the marker. Nothing else is decided.

``stored_level_offers``, ``stored_level_offer_documents`` and
``current_level_offer`` are the one reader of stored level offers (amendment
2): retained-decision validation, the decision command and the review API's
composition. The level storage is read by no automatic decision, consent,
planning or field-effect write path; only the gate reads the markers.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from typing import cast

from mechanical_librarian.adapters.persistence.level_sql import (
    _TARGET_LEVEL_SQL,
    _reconciled_level_sql,
    _unreconciled_case_sql,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _episode_decision_sql,
)
from mechanical_librarian.adapters.persistence.schema import (
    refresh_offered_field_facts,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    offer_digest,
)
from mechanical_librarian.domain.level_proposal_set import (
    LevelDecisionNote,
    LevelProposalSetError,
    derive_level,
    validate_level_proposal_set,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import POLICY_VERSION, ProposalSet
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
    compose_offer,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

# A cover choice binds its consideration, not an offer digest (AC16).
_UNBOUND_DESTINATIONS = frozenset({"cover"})
_NOTE_COLUMNS = (
    "decision_set_id,level_proposal_set_id,field,work_identity_level,rule,"
    "alternative_reference,decision_policy_version,decision_policy_sha256,"
    "proposal_policy_version"
)


def reconcile_case_level_in_transaction(
    connection: sqlite3.Connection,
    case: ReviewCase,
    proposal: ProposalSet,
    report: MetadataReport,
    current_policy: ResolvedMetadataPolicy | None,
    revoke: Callable[[str, int], object],
    at: int | None = None,
) -> None:
    """Reconcile one case to the configured level inside the caller's transaction.

    The configured level is ``review_preparation_control.work_identity_level``,
    the one every gate compares with. At a wider level the case's level set,
    notes and offer are written; at ``identifier`` nothing is, and a stored
    wider marker becomes ``identifier``. Everything written is content-addressed
    or keyed by case and level, so a replay writes nothing new, and a stored
    row that differs from what derivation gives now refuses the whole
    transaction. When the case was reconciled at another level, each field
    whose offer changed is asked again (``_ask_changed_fields``), in this same
    transaction that moves the marker. ``revoke`` closes a superseded choice's
    pending effect as ``matching_changed``; ``at`` defaults to now.
    """
    if not connection.in_transaction:
        raise MetadataReportError("level reconciliation request is invalid")
    level = target_level(connection)
    previous = reconciled_level(connection, case.review_case_id)
    occurred_at = int(time.time()) if at is None else at
    if level is WorkIdentityLevel.IDENTIFIER and previous is level:
        return
    offer = (
        None
        if level is WorkIdentityLevel.IDENTIFIER
        else _store_level(connection, case, proposal, report, current_policy, level)
    )
    if previous is not level:
        _ask_changed_fields(
            connection,
            case,
            compose_offer(case.review_case_id, case.proposals, offer),
            level,
            revoke,
            occurred_at,
        )
    _write_marker(connection, case.review_case_id, level)
    if previous is not level:
        # The works list and attention count a field by what it offers now
        # (amendment 14).
        refresh_offered_field_facts(connection, case.review_case_id)


def _store_level(
    connection: sqlite3.Connection,
    case: ReviewCase,
    proposal: ProposalSet,
    report: MetadataReport,
    current_policy: ResolvedMetadataPolicy | None,
    level: WorkIdentityLevel,
) -> str | None:
    """Write the level set, notes and offer at ``level``; return the offer."""
    policy, generation = _decision_policy(connection, report, current_policy)
    try:
        derivation = derive_level(
            report,
            proposal,
            case,
            policy,
            generation,
            level,
            None if current_policy is None else human_review_destinations(policy),
        )
    except (LevelProposalSetError, ReviewCaseValueError, ValueError) as error:
        raise MetadataReportError("level reconciliation failed") from error
    stored = connection.execute(
        "SELECT proposal_set_id,report_id,work_identity_level,document "
        "FROM level_proposal_sets WHERE level_proposal_set_id=?",
        (derivation.level_proposal_set_id,),
    ).fetchone()
    if stored is None:
        connection.execute(
            "INSERT INTO level_proposal_sets(level_proposal_set_id,proposal_set_id,"
            "report_id,work_identity_level,policy_version,document) "
            "VALUES(?,?,?,?,?,?)",
            (
                derivation.level_proposal_set_id,
                proposal.proposal_set_id,
                report.report_id,
                level.value,
                POLICY_VERSION,
                derivation.document,
            ),
        )
    else:
        # A stored set must read back exactly and be the one derived now.
        read_level_proposal_set(connection, derivation.level_proposal_set_id)
        if tuple(stored) != (
            proposal.proposal_set_id,
            report.report_id,
            level.value,
            derivation.document,
        ):
            raise MetadataReportError("level proposal set identity conflicts")
    for note in derivation.notes:
        _record_note(connection, note)
    if derivation.offer is not None:
        stored_offers = dict(stored_level_offers(connection, case))
        if level.value not in stored_offers:
            connection.execute(
                "INSERT INTO review_case_level_offers(review_case_id,"
                "work_identity_level,level_proposal_set_id,reconciled_level,"
                "document) VALUES(?,?,?,?,?)",
                (
                    case.review_case_id,
                    level.value,
                    derivation.level_proposal_set_id,
                    level.value,
                    derivation.offer,
                ),
            )
        elif stored_offers[level.value] != derivation.offer:
            raise MetadataReportError("review case level offer conflicts")
    return derivation.offer


def _write_marker(
    connection: sqlite3.Connection, review_case_id: str, level: WorkIdentityLevel
) -> None:
    connection.execute(
        "INSERT INTO review_case_level_reconciliation(review_case_id,"
        "reconciled_level) VALUES(?,?) ON CONFLICT(review_case_id) DO UPDATE "
        "SET reconciled_level=excluded.reconciled_level",
        (review_case_id, level.value),
    )


# A standing owner choice: what the field's latest decision of the case's
# episode settles it with. A withdrawal, the valueless row included, stands for
# nothing, so a field already asked again is not asked twice.
_STANDING_OUTCOMES = frozenset({"accept", "keep_current", "owner_entered"})


def _ask_changed_fields(
    connection: sqlite3.Connection,
    case: ReviewCase,
    offer: tuple[dict[str, object], ...],
    level: WorkIdentityLevel,
    revoke: Callable[[str, int], object],
    occurred_at: int,
) -> None:
    """Ask again each field whose offer is no longer its standing choice's.

    Stored data only (S4): the latest owner decision of the case's episode per
    field is compared with the field's offer at ``level`` by digest. Where they
    differ, the valueless ``matching_changed`` row is appended (no event, so no
    Activity row) and the superseded choice's pending effect closes as
    ``matching_changed``. Automatic decisions, consents and the manual hold
    are untouched, so a field asked again stays the owner's to decide.
    """
    destinations = {
        cast("str", proposal["destination"])
        for proposal in offer
        if proposal["destination"] not in _UNBOUND_DESTINATIONS
    }
    for destination in sorted(destinations):
        row = connection.execute(
            "SELECT document FROM human_decisions WHERE review_case_id=? "
            "AND destination=? AND "
            + _episode_decision_sql("human_decisions")
            + " ORDER BY ordinal DESC LIMIT 1",
            (case.review_case_id, destination),
        ).fetchone()
        if row is None:
            continue
        try:
            standing = HumanDecision.from_json(row[0])
            if (
                standing.outcome not in _STANDING_OUTCOMES
                or standing.offered_alternatives_digest
                == offer_digest(offer, destination)
            ):
                continue
            cause = HumanDecision.matching_changed(
                case, standing, level.value, offer, occurred_at
            )
        except HumanDecisionError as error:
            raise MetadataReportError("level reconciliation failed") from error
        connection.execute(
            "INSERT INTO human_decisions("
            "decision_id,review_case_id,proposal_id,target_fingerprint,"
            "destination,actor,outcome,selected_value_document,"
            "idempotency_digest,request_digest,occurred_at,document) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (
                cause.decision_id,
                cause.review_case_id,
                cause.proposal_id,
                cause.target_fingerprint,
                cause.destination,
                cause.actor,
                cause.outcome,
                "null",
                cause.idempotency_digest,
                cause.request_digest,
                cause.occurred_at,
                cause.to_json(),
            ),
        )
        revoke(standing.decision_id, occurred_at)


def target_level(connection: sqlite3.Connection) -> WorkIdentityLevel:
    """The configured level every case is reconciled to (amendment 5)."""
    row = connection.execute("SELECT " + _TARGET_LEVEL_SQL).fetchone()
    try:
        return WorkIdentityLevel(row[0])
    except (TypeError, ValueError) as error:
        raise MetadataReportError("work identity level is invalid") from error


def reconciled_level(
    connection: sqlite3.Connection, review_case_id: str
) -> WorkIdentityLevel:
    """The level a case was last reconciled at: marker, offer row, identifier."""
    row = connection.execute(
        "SELECT "
        + _reconciled_level_sql("candidate.review_case_id")
        + " FROM (SELECT ? AS review_case_id) candidate",
        (review_case_id,),
    ).fetchone()
    try:
        return WorkIdentityLevel(row[0])
    except (TypeError, ValueError) as error:
        raise MetadataReportError("work identity level is invalid") from error


def case_level_unreconciled(
    connection: sqlite3.Connection, review_case_id: str
) -> bool:
    """Whether a case still waits for its Book matching check (S10).

    The same comparison the executor's deferral and the re-check selection
    make, so the decision gate, the review API's ``checking`` and the writes
    agree on every case.
    """
    row = connection.execute(
        "SELECT "
        + _unreconciled_case_sql("candidate.review_case_id")
        + " FROM (SELECT ? AS review_case_id) candidate",
        (review_case_id,),
    ).fetchone()
    return tuple(row) == (1,)


def stored_level_offers(
    connection: sqlite3.Connection, case: ReviewCase
) -> tuple[tuple[str, str], ...]:
    """A case's stored level offers, every level, each validated (amendment 2).

    Offers are never deleted, so a choice bound to an earlier level's offer can
    be checked against it. With ``stored_level_offer_documents`` and
    ``current_level_offer``, this is the only reader of
    ``review_case_level_offers``: validation and composition only, never a
    decision.
    """
    return _offer_rows(connection, case.review_case_id, case.proposals)


def stored_level_offer_documents(
    connection: sqlite3.Connection, review_case_id: str
) -> tuple[str, ...]:
    """The documents of a case's stored level offers, for retained validation.

    Only the row's own columns are checked here: ``DecisionScope`` composes
    each document over the case's projected proposals, which validates it
    whole, so no reader projects a case twice.
    """
    return tuple(_offer_documents_by_level(connection, review_case_id).values())


def current_level_offer(
    connection: sqlite3.Connection, review_case_id: str
) -> str | None:
    """The offer row at the level the case was last reconciled at, or None.

    The reconciled level is ``COALESCE(marker, offer row level, 'identifier')``,
    the marker first because reconciliation always writes it. ``None`` means the
    fields offer their live proposals. The caller composes the document over
    the case (``current_offer``), which validates it whole.
    """
    offers = _offer_documents_by_level(connection, review_case_id)
    marker = connection.execute(
        "SELECT reconciled_level FROM review_case_level_reconciliation "
        "WHERE review_case_id=?",
        (review_case_id,),
    ).fetchone()
    if marker is not None:
        level = marker[0]
    elif len(offers) == 1:
        (level,) = offers
    else:
        level = WorkIdentityLevel.IDENTIFIER.value
    return offers.get(level)


def _offer_documents_by_level(
    connection: sqlite3.Connection, review_case_id: str
) -> dict[str, str]:
    documents: dict[str, str] = {}
    for level, level_proposal_set_id, reconciled, document in connection.execute(
        "SELECT work_identity_level,level_proposal_set_id,reconciled_level,document "
        "FROM review_case_level_offers WHERE review_case_id=? "
        "ORDER BY work_identity_level",
        (review_case_id,),
    ).fetchall():
        try:
            raw = json.loads(document)
            if (
                type(raw) is not dict
                or reconciled != level
                or raw.get("work_identity_level") != level
                or raw.get("level_proposal_set_id") != level_proposal_set_id
            ):
                raise ValueError
        except (TypeError, ValueError) as error:
            raise MetadataReportError("review case level offer is invalid") from error
        documents[level] = document
    return documents


def _offer_rows(
    connection: sqlite3.Connection,
    review_case_id: str,
    proposals: tuple[dict[str, object], ...],
) -> tuple[tuple[str, str], ...]:
    rows = connection.execute(
        "SELECT work_identity_level,level_proposal_set_id,reconciled_level,document "
        "FROM review_case_level_offers WHERE review_case_id=? "
        "ORDER BY work_identity_level",
        (review_case_id,),
    ).fetchall()
    offers: list[tuple[str, str]] = []
    for level, level_proposal_set_id, reconciled, document in rows:
        try:
            compose_offer(review_case_id, proposals, document)
            raw = json.loads(document)
        except (ReviewCaseValueError, json.JSONDecodeError) as error:
            raise MetadataReportError("review case level offer is invalid") from error
        if (
            reconciled != level
            or raw["work_identity_level"] != level
            or raw["level_proposal_set_id"] != level_proposal_set_id
        ):
            raise MetadataReportError("review case level offer is invalid")
        offers.append((level, document))
    return tuple(offers)


def read_level_decision_notes(
    connection: sqlite3.Connection, level_proposal_set_id: str
) -> tuple[LevelDecisionNote, ...]:
    """One level set's notes, refused when any came from another engine (S10)."""
    try:
        return tuple(
            LevelDecisionNote(*row)
            for row in connection.execute(
                f"SELECT {_NOTE_COLUMNS} FROM level_decision_notes "
                "WHERE level_proposal_set_id=? ORDER BY decision_set_id,field",
                (level_proposal_set_id,),
            ).fetchall()
        )
    except (LevelProposalSetError, TypeError) as error:
        raise MetadataReportError("level decision note is invalid") from error


def read_level_proposal_set(
    connection: sqlite3.Connection, level_proposal_set_id: str
) -> ProposalSet:
    """One stored level set, read back exactly."""
    row = connection.execute(
        "SELECT proposal_set_id,report_id,work_identity_level,policy_version,"
        "document FROM level_proposal_sets WHERE level_proposal_set_id=?",
        (level_proposal_set_id,),
    ).fetchone()
    if row is None:
        raise MetadataReportError("level proposal set is unavailable")
    try:
        return validate_level_proposal_set(
            level_proposal_set_id, row[0], row[1], row[2], row[4]
        )
    except LevelProposalSetError as error:
        raise MetadataReportError("level proposal set is invalid") from error


def _record_note(connection: sqlite3.Connection, note: LevelDecisionNote) -> None:
    values = (
        note.decision_set_id,
        note.level_proposal_set_id,
        note.field,
        note.work_identity_level,
        note.rule,
        note.alternative_reference,
        note.decision_policy_version,
        note.decision_policy_sha256,
        note.proposal_policy_version,
    )
    connection.execute(
        f"INSERT INTO level_decision_notes({_NOTE_COLUMNS}) "
        "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        values,
    )
    if note not in read_level_decision_notes(connection, note.level_proposal_set_id):
        raise MetadataReportError("level decision note conflicts")


def _decision_policy(
    connection: sqlite3.Connection,
    report: MetadataReport,
    current_policy: ResolvedMetadataPolicy | None,
) -> tuple[ResolvedMetadataPolicy, int]:
    """The policy and generation the live outcome set is projected under.

    The same resolution `persist_shadow_outcome` uses: the current resolved
    policy, or the first generation's active draft.
    """
    state = connection.execute(
        "SELECT s.generation,d.document FROM metadata_policy_state s "
        "JOIN metadata_policy_drafts d ON d.policy_id=s.active_policy_id "
        "WHERE s.singleton=1"
    ).fetchone()
    if state is None:
        raise MetadataReportError("level reconciliation policy is unavailable")
    policy = current_policy
    if policy is None and state[0] == 1:
        policy = resolve_policy(
            MetadataPolicyProfile.from_data(json.loads(state[1])),
            report.target.library,
        )
    if policy is None:
        raise MetadataReportError("level reconciliation policy is unavailable")
    return policy, int(state[0])


__all__ = [
    "case_level_unreconciled",
    "current_level_offer",
    "read_level_decision_notes",
    "read_level_proposal_set",
    "reconcile_case_level_in_transaction",
    "reconciled_level",
    "stored_level_offer_documents",
    "stored_level_offers",
    "target_level",
]
