"""The decision aggregate: human decisions, automatic decisions, safety state.

This module owns every write to `human_decisions`, `human_decision_events`,
`automatic_decisions` and the singleton `automatic_safety_state`, and the reads
that project them -- the case decision list, the idempotency lookup, and the
review history ledger. Nothing outside it inserts into those four tables.

It was deferred until the review and field-effect aggregates existed because
`record_human_decision` supersedes prior authority and takes a manual hold in
the same transaction, and `_record_automatic_decision` resolves the current
policy: seven seams cross the boundary and each needs an owner on the other
side to delegate to.

Every seam arrives as a callable resolved through the composing repository at
call time. `current_resolved_policy_id` is substituted on the composing
repository's *class* by three consent tests, and `_record_automatic_outcomes`
is substituted on the composing repository *instance* by two authority-failure
tests which drive it through `persist_shadow_outcome`; that caller stays on the
composing repository, so its delegating stub is what those tests replace.

`_record_automatic_outcomes` and `_set_automatic_safety_state` are deliberately
scope-free: `persist_shadow_outcome` owns the enclosing transaction and these
run inside it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.review_sql import (
    _SHADOW_OUTCOME_HISTORY_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _FIELD_EFFECT_COMPANION_DECISION_SQL,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
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
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceCandidate,
)
from mechanical_librarian.domain.field_effect import (
    FieldEffectOperation,
    decision_value_kind,
)
from mechanical_librarian.domain.human_decision import (
    DecisionScope,
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
)
from mechanical_librarian.domain.shadow_outcome import (
    ShadowOutcomeError,
    ShadowOutcomeSet,
)

_REPLACEMENT_BLOCKING_EFFECT_SQL = (
    "(state IN ('claimed','applying','reconciling') OR (state='verified' AND ?))"
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
            "FROM cover_selection_authorities selection JOIN cover_considerations consideration "
            "ON consideration.consideration_id=selection.consideration_id "
            "WHERE selection.kind='automatic' AND consideration.review_case_id=? "
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
        current_resolved_policy_id: Callable[[str], str],
        record_cover_decision_outcome: Callable[..., None],
        revoke_field_effect_authority_in_transaction: Callable[..., int],
        set_field_effect_manual_hold: Callable[[str, str, int, str], None],
        validate_shadow_outcome_row: Callable[
            [tuple[object, ...] | sqlite3.Row], tuple[str, int]
        ],
    ) -> None:
        super().__init__(connection, binding)
        self._requeue_title_projections = requeue_title_projections
        self.get_review_case = get_review_case
        self._review_case_accepts_authority = review_case_accepts_authority
        self.current_resolved_policy_id = current_resolved_policy_id
        self._record_cover_decision_outcome = record_cover_decision_outcome
        self._revoke_field_effect_authority_in_transaction = (
            revoke_field_effect_authority_in_transaction
        )
        self._set_field_effect_manual_hold = set_field_effect_manual_hold
        self._validate_shadow_outcome_row = validate_shadow_outcome_row

    def _record_automatic_outcomes(
        self, outcome: ShadowOutcomeSet, occurred_at: int
    ) -> None:
        """Append eligible dispositions, leaving paused/suspended work human."""
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
        # A normal human winner or replay is not an authority failure.  Remove
        # its whole bundle before any insert, so a bundle is all-or-none.
        blocked_bundles: set[str] = set()
        for decision in decisions:
            conflict = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (
                    decision.review_case_id,
                    decision.destination,
                    decision.review_case_id,
                    decision.destination,
                ),
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
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (
                    decision.review_case_id,
                    decision.destination,
                    decision.review_case_id,
                    decision.destination,
                ),
            ).fetchone()
            if conflict is not None:
                # Human wins and independent replay are ordinary fallback, not
                # a global safety event.  Bundles were removed as a unit above.
                continue
            self._connection.execute(
                "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
                (
                    decision.decision_id,
                    decision.review_case_id,
                    decision.destination,
                    decision.idempotency_digest,
                    decision.occurred_at,
                    decision.to_json(),
                ),
            )

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
                "FROM human_decisions WHERE review_case_id=? "
                "ORDER BY ordinal",
                (review_case_id,),
            ).fetchall()
            documents = []
            # Projected once, at the first decision, as `validate_for` did (#1215).
            scope: DecisionScope | None = None
            for row in rows:
                decision = HumanDecision.from_json(row[10])
                if scope is None:
                    scope = DecisionScope.of(case)
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
                "SELECT d.document FROM current_description_decisions p JOIN description_decisions d ON d.decision_id=p.decision_id WHERE p.review_case_id=?",
                (review_case_id,),
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
            decision.validate_new_for(case)
            self._record_cover_decision_outcome(
                decision, cover_consideration_id, cover_confirmation, validate_only=True
            )
            cover_automatic = None
            if decision.destination == "cover":
                target = case.target
                cover_automatic = self._connection.execute(
                    "SELECT selection.decision_id,selection.kind "
                    "FROM current_cover_selection_authorities current "
                    "JOIN cover_selection_authorities selection "
                    "ON selection.selection_id=current.selection_id "
                    "WHERE current.connection_name=? AND current.library_name=? "
                    "AND current.item_id=?",
                    (target["connection"], target["library"], target["item"]),
                ).fetchone()
            previous = self._connection.execute(
                "SELECT decision_id,'human' FROM human_decisions "
                "WHERE review_case_id=? AND destination=? ORDER BY ordinal DESC LIMIT 1",
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
            if previous is None:
                previous = self._connection.execute(
                    "SELECT decision_id,'automatic' FROM automatic_decisions "
                    "WHERE review_case_id=? AND destination=?",
                    (decision.review_case_id, decision.destination),
                ).fetchone()
            automatic = self._connection.execute(
                "SELECT decision_id FROM automatic_decisions "
                "WHERE review_case_id=? AND destination=?",
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
            selected = _canonical_json(decision.selected_value)
            self._connection.execute(
                "INSERT INTO human_decisions("
                "decision_id,review_case_id,proposal_id,target_fingerprint,"
                "destination,actor,outcome,selected_value_document,"
                "idempotency_digest,request_digest,occurred_at,document) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision.decision_id,
                    decision.review_case_id,
                    decision.proposal_id,
                    decision.target_fingerprint,
                    decision.destination,
                    decision.actor,
                    decision.outcome,
                    selected,
                    decision.idempotency_digest,
                    decision.request_digest,
                    decision.occurred_at,
                    decision.to_json(),
                ),
            )
            self._record_cover_decision_outcome(
                decision, cover_consideration_id, cover_confirmation
            )
            event_id = (
                "human_decision_event_"
                + hashlib.sha256(
                    ("human-decision-event-v1:" + decision.decision_id).encode("utf-8")
                ).hexdigest()
            )
            event_document = _canonical_json(
                {
                    "actor": decision.actor,
                    "decision_id": decision.decision_id,
                    "destination": decision.destination,
                    "event_id": event_id,
                    "kind": "human_decision_recorded",
                    "occurred_at": decision.occurred_at,
                    "outcome": decision.outcome,
                    "review_case_id": decision.review_case_id,
                    "schema_version": "human-decision-event-v1",
                }
            )
            self._connection.execute(
                "INSERT INTO human_decision_events("
                "event_id,decision_id,review_case_id,destination,actor,kind,"
                "occurred_at,document) VALUES(?,?,?,?,?,'human_decision_recorded',?,?)",
                (
                    event_id,
                    decision.decision_id,
                    decision.review_case_id,
                    decision.destination,
                    decision.actor,
                    decision.occurred_at,
                    event_document,
                ),
            )
            if previous is not None:
                self._revoke_field_effect_authority_in_transaction(
                    previous[0], decision.occurred_at, required=False
                )
            if automatic is not None:
                if previous is None or automatic[0] != previous[0]:
                    self._revoke_field_effect_authority_in_transaction(
                        automatic[0], decision.occurred_at, required=False
                    )
                self._set_field_effect_manual_hold(
                    decision.review_case_id,
                    decision.destination,
                    decision.occurred_at,
                    decision.decision_id,
                )
            if authored is not None:
                self._connection.execute(
                    "DELETE FROM current_description_decisions WHERE review_case_id=?",
                    (decision.review_case_id,),
                )
                self._set_field_effect_manual_hold(
                    decision.review_case_id,
                    decision.destination,
                    decision.occurred_at,
                    decision.decision_id,
                )
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return decision.to_json()
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

    @local_operation
    def _record_automatic_decision(self, decision: AutomaticDecision) -> str:
        """Legacy internal single-row helper; projection admission owns policy classification.

        It is intentionally not a repository command or web/API seam.  The
        only supported admission path is ``persist_shadow_outcome``, which
        validates a complete outcome and applies bundle/race classification
        before any durable insert.
        """
        if type(decision) is not AutomaticDecision:
            raise MetadataReportError("automatic decision persistence failed")
        transaction = self._transaction("_record_automatic_decision")
        try:
            transaction.begin()
            replay = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if replay is not None:
                if replay != (decision.to_json(),):
                    raise AutomaticDecisionError("idempotency_conflict")
                transaction.commit()
                return decision.to_json()
            posture = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            # The persistence seam is intentionally inert until the complete
            # policy-control/suspension composition admits active posture.
            if posture != ("automatic",):
                raise AutomaticDecisionError("unavailable")
            if not self.automatic_safety_state().permits(
                automatic_safety_category(
                    decision.policy_category, decision.policy_option
                )
            ):
                raise AutomaticDecisionError("suspended")
            case = json.loads(self.get_review_case(decision.review_case_id))
            if type(case) is not dict or case.get("current") is not True:
                raise AutomaticDecisionError("stale")
            existing = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (
                    decision.review_case_id,
                    decision.destination,
                    decision.review_case_id,
                    decision.destination,
                ),
            ).fetchone()
            if existing is not None:
                raise AutomaticDecisionError("already_decided")
            outcome = self._connection.execute(
                "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? AND report_id=? "
                "AND proposal_set_id=? AND resolved_policy_id=?",
                (
                    decision.review_case_id,
                    decision.report_id,
                    decision.proposal_set_id,
                    decision.resolved_policy_id,
                ),
            ).fetchone()
            if outcome is None:
                raise AutomaticDecisionError("stale")
            source = ShadowOutcomeSet.from_json(outcome[0])
            field = next(
                (
                    value
                    for value in source.outcomes
                    if value.outcome_id == decision.source_outcome_id
                ),
                None,
            )
            if (
                field is None
                or (field.disposition != decision.outcome)
                or any(
                    (
                        field.proposal_id != decision.proposal_id,
                        field.destination != decision.destination,
                        field.rule_id != decision.rule_id,
                        field.policy_category != decision.policy_category,
                        field.policy_option != decision.policy_option,
                    )
                )
            ):
                raise AutomaticDecisionError("stale")
            if decision.policy_category in {"title_layout", "date_precision"}:
                required = (
                    ("title",)
                    if decision.policy_category == "title_layout"
                    else ("publishedDate",)
                )
                members = tuple(
                    value
                    for value in source.outcomes
                    if value.policy_category == decision.policy_category
                    and value.destination in required
                )
                expected_bundle = (
                    "automatic_bundle_"
                    + hashlib.sha256(
                        _canonical_json(
                            {
                                "category": decision.policy_category,
                                "combination_ids": list(field.combination_ids),
                            }
                        ).encode("utf-8")
                    ).hexdigest()
                )
                if (
                    len(members) != len(required)
                    or {value.destination for value in members} != set(required)
                    or len({value.combination_ids for value in members}) != 1
                    or not field.combination_ids
                    or decision.bundle_id != expected_bundle
                ):
                    raise AutomaticDecisionError("bundle_identity")
            if (
                self.current_resolved_policy_id(
                    json.loads(case["case"])["target"]["library"]
                )
                != decision.resolved_policy_id
            ):
                raise AutomaticDecisionError("stale")
            self._connection.execute(
                "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
                (
                    decision.decision_id,
                    decision.review_case_id,
                    decision.destination,
                    decision.idempotency_digest,
                    decision.occurred_at,
                    decision.to_json(),
                ),
            )
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return decision.to_json()
        except AutomaticDecisionError:
            transaction.rollback()
            raise
        except (
            sqlite3.Error,
            MetadataReportError,
            ShadowOutcomeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            transaction.rollback()
            raise MetadataReportError(
                "automatic decision persistence failed"
            ) from error

    @local_operation
    def automatic_safety_state(self) -> AutomaticSafetyState:
        try:
            row = self._connection.execute(
                "SELECT paused,global_suspension,category_suspensions_document FROM automatic_safety_state WHERE singleton=1"
            ).fetchone()
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
            event_rows = self._connection.execute(
                "SELECT event_id,decision_id,review_case_id,destination,actor,"
                "kind,occurred_at,document FROM human_decision_events "
                "WHERE review_case_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT ?",
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
                    or value["schema_version"] != "human-decision-event-v1"
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
                previous = (
                    target.get("value") if target.get("state") == "value" else None
                )
                prior = self._connection.execute(
                    "SELECT effect.document FROM field_effects effect JOIN ("
                    "SELECT earlier.decision_id,earlier.ordinal FROM human_decisions earlier "
                    "WHERE earlier.review_case_id=? AND earlier.destination=? AND earlier.ordinal<? "
                    "UNION ALL SELECT earlier.decision_id,-1 FROM automatic_decisions earlier "
                    "WHERE earlier.review_case_id=? AND earlier.destination=?"
                    ") recorded ON (recorded.decision_id=effect.decision_id "
                    "OR recorded.decision_id="
                    + _FIELD_EFFECT_COMPANION_DECISION_SQL
                    + ") WHERE effect.state='verified' "
                    "ORDER BY recorded.ordinal DESC,effect.rowid LIMIT 1",
                    (
                        review_case_id,
                        decision.destination,
                        decision_row[0],
                        review_case_id,
                        decision.destination,
                    ),
                ).fetchone()
                if prior is not None:
                    plan = FieldEffectOperation.from_json(prior[0]).plan
                    previous = (
                        plan.subtitle_companion.desired_value
                        if decision.destination == "subtitle"
                        and plan.subtitle_companion is not None
                        else plan.desired_value
                    )
                kind = decision_value_kind(decision.destination)
                value["previous_value"] = (
                    {"kind": kind, "value": previous} if previous is not None else None
                )
                value["selected_value"] = (
                    decision.selected_value
                    if decision.outcome == "accept"
                    else {"kind": kind, "value": target.get("value")}
                    if decision.outcome == "keep_current"
                    and target.get("state") == "value"
                    else None
                )
                events.append((row[6], int(decision_row[0]), row[0], value))
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
                            "kind": "shadow_preview_recorded",
                            "occurred_at": evaluated_at,
                            "origin": "policy_preview",
                            "outcome_set_id": row[0],
                            "review_case_id": review_case_id,
                            "schema_version": "shadow-preview-history-event-v1",
                        },
                    )
                )
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
            for (
                attempt_id,
                state,
                engine,
                model,
                at,
                candidate_document,
            ) in self._connection.execute(
                "SELECT a.attempt_id,a.state,a.engine,a.model,a.updated_at,c.document FROM description_advice_attempts a LEFT JOIN description_advice_candidates c ON c.attempt_id=a.attempt_id WHERE json_extract(a.binding_document,'$.review_case_id')=? ORDER BY a.updated_at DESC LIMIT ?",
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
                            "candidate": None
                            if candidate_document is None
                            else DescriptionAdviceCandidate.from_json(
                                candidate_document
                            ).model_dump(),
                            "decision": None,
                        },
                    )
                )
            for (
                document,
                candidate_document,
                selected_current,
            ) in self._connection.execute(
                "SELECT d.document,c.document,EXISTS(SELECT 1 FROM current_description_decisions current WHERE current.decision_id=d.decision_id) FROM description_decisions d JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id WHERE d.review_case_id=? ORDER BY json_extract(d.document,'$.occurred_at') DESC LIMIT ?",
                (review_case_id, limit),
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
                            "candidate": candidate.model_dump(),
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
                "SELECT event.event_id,event.occurred_at,event.kind,decision.document,candidate.document "
                "FROM field_effect_events event JOIN field_effects effect ON effect.plan_id=event.plan_id "
                "JOIN description_decisions decision ON decision.decision_id=effect.decision_id "
                "JOIN description_advice_candidates candidate ON candidate.candidate_id=decision.candidate_id "
                "WHERE decision.review_case_id=? UNION ALL "
                "SELECT event.event_id,event.occurred_at,'no_change_required',decision.document,candidate.document "
                "FROM field_effect_no_changes event JOIN description_decisions decision ON decision.decision_id=event.decision_id "
                "JOIN description_advice_candidates candidate ON candidate.candidate_id=decision.candidate_id "
                "WHERE decision.review_case_id=? ORDER BY 2 DESC,1 DESC LIMIT ?",
                (review_case_id, review_case_id, limit),
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
                            "candidate": candidate.model_dump(),
                            "decision": authored.model_dump(),
                        },
                    )
                )
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
