"""Write a case's Book matching level storage with the case (#1355, S10).

A case is born reconciled: in the transaction that writes it, at a wider
configured level, its level set, the would-admit notes its level decision set
leaves for live manual decisions, its level offer (when the level substitutes
a manual field) and its reconciled-level marker are written from stored data
alone, with no external request. At ``identifier`` nothing is written, and no
marker reads as ``identifier``.

``stored_level_offers``, ``stored_level_offer_documents`` and
``current_level_offer`` are the one reader of stored level offers (amendment
2): retained-decision validation, the decision command and the review API's
composition. The level storage is read by no automatic decision, consent,
planning or field-effect write path.
"""

from __future__ import annotations

import json
import sqlite3

from mechanical_librarian.application.metadata_report import MetadataReportError
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
    level: WorkIdentityLevel,
) -> None:
    """Write one case's level storage at ``level`` inside the caller's transaction.

    The caller holds the write transaction that writes or re-admits the case.
    Everything written is content-addressed or keyed by case and level, so a
    replay at the same level writes nothing new, and a stored row that differs
    from what derivation gives now refuses the whole transaction.
    """
    if not connection.in_transaction or type(level) is not WorkIdentityLevel:
        raise MetadataReportError("level reconciliation request is invalid")
    if level is WorkIdentityLevel.IDENTIFIER:
        return
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
    connection.execute(
        "INSERT INTO review_case_level_reconciliation(review_case_id,"
        "reconciled_level) VALUES(?,?) ON CONFLICT(review_case_id) DO UPDATE "
        "SET reconciled_level=excluded.reconciled_level",
        (case.review_case_id, level.value),
    )


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
    "current_level_offer",
    "read_level_decision_notes",
    "read_level_proposal_set",
    "reconcile_case_level_in_transaction",
    "stored_level_offer_documents",
    "stored_level_offers",
]
