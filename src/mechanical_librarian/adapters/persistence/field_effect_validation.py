"""Deep validation of the persisted field-effect state.

`schema.py` calls this during open-time total validation. It deliberately
imports nothing from any persistence module, so the schema spine stays acyclic
once the field-effect aggregate moves into its own module.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import cast

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
)
from mechanical_librarian.domain.field_effect import (
    FIELD_EFFECT_FAILURES,
    TERMINAL_STATES,
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectNoChange,
    FieldEffectOperation,
    authority_to_data,
    field_value_version,
    project_decision_value,
)
from mechanical_librarian.domain.field_effect import (
    canonical_json as canonical_effect_json,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    project_f20_automatic_decision_value,
    select_isbn_representation,
)
from mechanical_librarian.domain.validation import (
    boolean,
    bounded_integer,
    closed_document,
    literal,
    non_negative_integer,
    text,
)

__all__ = ["_automatic_decision_value", "_validate_field_effect_state"]


def _automatic_decision_value(
    decision: AutomaticDecision, case: ReviewCase, report: MetadataReport,
    proposal_set: ProposalSet,
) -> object:
    """Project one persisted automatic decision to its exact target envelope."""
    target = case.target
    if (
        decision.outcome != "candidate"
        or decision.review_case_id != case.review_case_id
        or decision.report_id != case.report_id
        or decision.proposal_set_id != case.proposal_set_id
        or decision.target_fingerprint != case.target_fingerprint
        or proposal_set.proposal_set_id != case.proposal_set_id
        or proposal_set.report_id != case.report_id
        or proposal_set.target_fingerprint != case.target_fingerprint
        or report.report_id != case.report_id
        or report.target.fingerprint != case.target_fingerprint
        or (target.get("connection"), target.get("library"), target.get("item"))
        != (report.target.connection, report.target.library, report.target.item)
    ):
        raise FieldEffectError("authority_binding")
    proposal = next(
        (item for item in case.proposals
         if item["proposal_id"] == decision.proposal_id
         and item["destination"] == decision.destination),
        None,
    )
    # Every ReviewCase proposal carries a "result" object, and TargetFacts
    # always serialises "fields" as a list of field objects.
    alternatives = None if proposal is None else cast("dict[str, object]", proposal["result"]).get("alternatives")
    selected = next(
        (item for item in cast("list[dict[str, object]] | None", alternatives) or ()
         if item.get("alternative_id") == decision.candidate_id),
        None,
    )
    target_field = next(
        (item for item in cast("list[object]", target.get("fields", ()))
         if type(item) is dict and item.get("destination") == decision.destination),
        None,
    )
    if (
        selected is None
        or type(target_field) is not dict
        or target_field.get("state") not in {"value", "null", "empty", "missing"}
    ):
        raise FieldEffectError("authority_binding")
    value = selected["value"]
    if decision.policy_category == "isbn_form":
        try:
            result = next(
                item for item in proposal_set.results
                if item.destination.value == decision.destination
            )
            candidate = next(
                item for item in result.alternatives
                if item.alternative_id == decision.candidate_id
            )
            exact_value = select_isbn_representation(report, candidate, decision.policy_option)
        except (StopIteration, RichMetadataPreviewError) as error:
            raise FieldEffectError("authority_binding") from error
        if exact_value is None:
            raise FieldEffectError("authority_binding")
        value = {"kind": "text", "value": exact_value}
    pre_value = target_field.get("value") if target_field["state"] == "value" else None
    if decision.policy_category in {"series", "genres", "tags", "description_fill", "isbn_form"}:
        try:
            value = project_f20_automatic_decision_value(
                decision.policy_category, decision.policy_option, value, pre_value,
            )
        except RichMetadataPreviewError as error:
            raise FieldEffectError("authority_binding") from error
    return value


def _validate_field_effect_state(connection: sqlite3.Connection) -> None:
    """Fail closed on any non-canonical field-effect envelope or row mismatch."""
    try:
        for row in connection.execute(
            "SELECT decision_id,review_case_id,report_id,connection_name,library_name,work_id,destination,target_fingerprint,target_version,origin,occurred_at,event_id FROM field_effect_no_changes"
        ).fetchall():
            record = FieldEffectNoChange.create(
                decision_id=row[0], review_case_id=row[1], report_id=row[2],
                connection=row[3], library=row[4], work_id=row[5], destination=row[6],
                target_fingerprint=row[7], target_version=row[8], occurred_at=row[10],
            )
            if row[9] != record.origin or row[11] != record.event_id:
                raise FieldEffectError("persistence")
            decision_row = connection.execute(
                "SELECT document FROM automatic_decisions WHERE decision_id=?", (record.decision_id,)
            ).fetchone()
            case_row = connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?", (record.review_case_id,)
            ).fetchone()
            report_row = connection.execute(
                "SELECT document FROM metadata_reports WHERE report_id=?", (record.report_id,)
            ).fetchone()
            if decision_row is None or case_row is None or report_row is None:
                raise FieldEffectError("persistence")
            decision = AutomaticDecision.from_json(decision_row[0])
            case = ReviewCase.from_json(case_row[0])
            report = MetadataReport.from_json(report_row[0])
            proposal_row = connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (decision.proposal_set_id,),
            ).fetchone()
            if proposal_row is None:
                raise FieldEffectError("persistence")
            proposal_set = ProposalSet.from_json(proposal_row[0])
            if (
                decision.destination != record.destination
                or decision.target_fingerprint != record.target_fingerprint
                or report.target.connection != record.connection
                or report.target.library != record.library
                or report.target.item != record.work_id
            ):
                raise FieldEffectError("persistence")
            # TargetFacts always serialises "fields" as a list of field objects.
            target = next((value for value in cast("list[object]", case.target.get("fields", ()))
                           if type(value) is dict
                           and value.get("destination") == record.destination), None)
            proposal = next((value for value in case.proposals
                             if value.get("proposal_id") == decision.proposal_id
                             and value.get("destination") == record.destination), None)
            result = proposal.get("result") if type(proposal) is dict else None
            alternatives = result.get("alternatives") if type(result) is dict else None
            selected = next((value for value in alternatives or ()
                             if type(value) is dict
                             and value.get("alternative_id") == decision.candidate_id), None)
            if (type(target) is not dict or target.get("state") not in {"value", "null", "empty", "missing"}
                    or type(selected) is not dict):
                raise FieldEffectError("persistence")
            pre_value = target.get("value") if target["state"] == "value" else None
            desired = project_decision_value(
                record.destination,
                _automatic_decision_value(decision, case, report, proposal_set),
            )
            if desired != pre_value or record.target_version != field_value_version(record.destination, pre_value):
                raise FieldEffectError("persistence")
        control = connection.execute(
            "SELECT singleton,paused,updated_at FROM field_effect_control"
        ).fetchall()
        if (len(control) != 1 or control[0][0] != 1 or control[0][1] not in (0, 1)
                or not non_negative_integer(control[0][2])):
            raise FieldEffectError("invalid_control")

        suspension = closed_document(
            "connection", "destination", "reason", "scope", "updated_at",
            schema_version=literal("field-effect-suspension-v1"))
        for scope, connection_name, destination, reason, updated_at, document in connection.execute(
            "SELECT scope,connection_name,destination,reason,updated_at,document "
            "FROM field_effect_suspensions"
        ):
            raw = json.loads(document)
            if (not suspension(raw)
                    or (raw["scope"], raw["connection"], raw["destination"],
                        raw["reason"], raw["updated_at"])
                    != (scope, connection_name, destination, reason, updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_suspension")

        authorities: dict[str, tuple[EffectAuthority, bool, int]] = {}
        authority_state = closed_document(
            schema_version=literal("field-effect-authority-state-v1"), current=boolean,
            updated_at=non_negative_integer,
            authority=closed_document(
                "decision_id", "origin", "outcome", "policy_identity",
                "qualification_identity", "review_case_id", "source",
                "target_fingerprint"))
        for decision_id, current, updated_at, document in connection.execute(
            "SELECT decision_id,current,updated_at,document FROM field_effect_authorities"
        ):
            raw = json.loads(document)
            if (not authority_state(raw)
                    or (raw["current"], raw["updated_at"]) != (bool(current), updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_authority")
            item = raw["authority"]
            authority = EffectAuthority(
                item["decision_id"], item["review_case_id"], item["target_fingerprint"],
                item["source"], item["origin"], item["outcome"], item["policy_identity"],
                item["qualification_identity"],
            )
            if decision_id != authority.decision_id or item != authority_to_data(authority):
                raise FieldEffectError("invalid_authority")
            authorities[decision_id] = (authority, bool(current), updated_at)

        consents: dict[str, tuple[EffectConsent, bool, int]] = {}
        consent_state = closed_document(
            "consent", schema_version=literal("field-effect-consent-state-v1"),
            current=boolean, updated_at=non_negative_integer)
        for consent_id, current, updated_at, document in connection.execute(
            "SELECT consent_id,current,updated_at,document FROM field_effect_consents"
        ):
            raw = json.loads(document)
            if (not consent_state(raw)
                    or (raw["current"], raw["updated_at"]) != (bool(current), updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_consent")
            consent = EffectConsent.from_data(raw["consent"])
            if consent_id != consent.consent_id:
                raise FieldEffectError("invalid_consent")
            consents[consent_id] = (consent, bool(current), updated_at)

        pending_return = closed_document(requested_at=non_negative_integer,
                                        scan_id=lambda value: type(value) is str and value != "")
        manual_hold = closed_document(
            "destination", "pending_return", "review_case_id",
            schema_version=literal("field-effect-manual-hold-v2"), active=boolean,
            decision_id=lambda value: type(value) is str and re.fullmatch(r"human_decision_[0-9a-f]{64}", value) is not None,
            updated_at=non_negative_integer)
        for review_case_id, destination, active, updated_at, document in connection.execute(
            "SELECT review_case_id,destination,active,updated_at,document "
            "FROM field_effect_manual_holds"
        ):
            raw = json.loads(document)
            if (not manual_hold(raw)
                or (raw["review_case_id"], raw["destination"], raw["active"],
                    raw["updated_at"]) != (review_case_id, destination,
                                            bool(active), updated_at)
                or (raw["pending_return"] is not None
                    and (not raw["active"] or not pending_return(raw["pending_return"])))
                or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_manual_hold")

        operations: dict[str, FieldEffectOperation] = {}
        # Rebound below both from a parsed document and from a dict lookup.
        operation: FieldEffectOperation | None
        for row in connection.execute(
            "SELECT plan_id,decision_id,connection_name,library_name,work_id,destination,state,attempt,updated_at,document FROM field_effects"
        ):
            operation = FieldEffectOperation.from_json(row[9])
            plan = operation.plan
            expected = (plan.plan_id, plan.authority.decision_id, plan.connection,
                        plan.library, plan.work_id, plan.destination, operation.state,
                        operation.attempt, operation.updated_at)
            if (row[:9] != expected or canonical_effect_json(operation.to_data()) != row[9]
                    or plan.authority.decision_id not in authorities
                    or plan.consent.consent_id not in consents
                    or authorities[plan.authority.decision_id][0] != plan.authority
                    or consents[plan.consent.consent_id][0] != plan.consent):
                raise FieldEffectError("invalid_operation")
            operations[plan.plan_id] = operation

        event = closed_document(
            "decision_id", "desired_value", "destination", "event_id", "origin",
            "plan_id", "pre_value",
            schema_version=literal("field-effect-event-v1"), sequence=bounded_integer(1),
            attempt=bounded_integer(0, 3), occurred_at=non_negative_integer,
            kind=text, state=text, failure=FIELD_EFFECT_FAILURES.__contains__)
        next_sequence: dict[str, int] = {}
        last_events: dict[str, dict[str, object]] = {}
        for event_id, plan_id, sequence, kind, occurred_at, document in connection.execute(
            "SELECT event_id,plan_id,sequence,kind,occurred_at,document FROM field_effect_events ORDER BY plan_id,sequence"
        ):
            raw = json.loads(document)
            operation = operations.get(plan_id)
            expected_sequence = next_sequence.get(plan_id, 1)
            if (operation is None or not event(raw)
                    or (raw["event_id"], raw["plan_id"], raw["sequence"], raw["kind"], raw["occurred_at"])
                    != (event_id, plan_id, sequence, kind, occurred_at)
                    or sequence != expected_sequence
                    or raw["decision_id"] != operation.plan.authority.decision_id
                    or raw["origin"] != operation.plan.authority.origin
                    or raw["destination"] != operation.plan.destination
                    or raw["pre_value"] != operation.plan.pre_value
                    or raw["desired_value"] != operation.plan.desired_value
                    or raw["state"] != kind
                    or canonical_effect_json(raw) != document
                    or event_id != "effect_event_" + hashlib.sha256(
                        canonical_effect_json({key: value for key, value in raw.items()
                                               if key != "event_id"}).encode()
                    ).hexdigest()):
                raise FieldEffectError("invalid_event")
            next_sequence[plan_id] = expected_sequence + 1
            last_events[plan_id] = raw
        if (set(next_sequence) != set(operations)
                or set(authorities) != {
                    value.plan.authority.decision_id for value in operations.values()
                }
                or not {
                    value.plan.consent.consent_id for value in operations.values()
                }.issubset(consents)):
            raise FieldEffectError("invalid_event")
        for plan_id, operation in operations.items():
            last = last_events[plan_id]
            if ((last["state"], last["attempt"], last["failure"])
                    != (operation.state, operation.attempt, operation.failure)):
                raise FieldEffectError("invalid_event")

        action = closed_document(
            "action_id", "plan_id", schema_version=literal("field-effect-action-v1"),
            attempt=bounded_integer(0, 3), updated_at=non_negative_integer,
            kind=text, state=text)
        actions: dict[tuple[str, str], tuple[str, int]] = {}
        for action_id, plan_id, kind, state, attempt, updated_at, document in connection.execute(
            "SELECT action_id,plan_id,kind,state,attempt,updated_at,document FROM field_effect_actions"
        ):
            raw = json.loads(document)
            if (plan_id not in operations or not action(raw)
                    or (raw["action_id"], raw["plan_id"], raw["kind"], raw["state"],
                        raw["attempt"], raw["updated_at"])
                    != (action_id, plan_id, kind, state, attempt, updated_at)
                    or canonical_effect_json(raw) != document
                    or action_id != "effect_action_" + hashlib.sha256(
                        f"{plan_id}:{kind}".encode()).hexdigest()):
                raise FieldEffectError("invalid_action")
            actions[(plan_id, kind)] = (state, attempt)
        for plan_id, operation in operations.items():
            targeted = actions.get((plan_id, "targeted_read"))
            if ((operation.state == "reconciling") != (targeted is not None and targeted[0] in {"pending", "claimed"})
                    or (targeted is not None and operation.state != "reconciling"
                        and targeted[0] != "completed")):
                raise FieldEffectError("invalid_action")
        for (plan_id, kind), (state, _) in actions.items():
            if (kind == "forward_reevaluation"
                    and (state not in {"pending", "claimed", "completed"}
                         or operations[plan_id].failure != "third_value")):
                raise FieldEffectError("invalid_action")

        cleanup = closed_document(
            "plan_id", schema_version=literal("field-effect-cleanup-v1"),
            attempt=bounded_integer(0, 3), updated_at=non_negative_integer, state=text)
        cleanups: set[str] = set()
        for plan_id, state, attempt, updated_at, document in connection.execute(
            "SELECT plan_id,state,attempt,updated_at,document FROM field_effect_cleanups"
        ):
            raw = json.loads(document)
            operation = operations.get(plan_id)
            if (operation is None or operation.plan.destination != "cover"
                    or operation.state not in TERMINAL_STATES or not cleanup(raw)
                    or (raw["plan_id"], raw["state"], raw["attempt"], raw["updated_at"])
                    != (plan_id, state, attempt, updated_at)
                    or canonical_effect_json(raw) != document):
                raise FieldEffectError("invalid_cleanup")
            cleanups.add(plan_id)
        expected_cleanups = {plan_id for plan_id, operation in operations.items()
                             if operation.plan.destination == "cover"
                             and operation.state in TERMINAL_STATES}
        if cleanups != expected_cleanups:
            raise FieldEffectError("invalid_cleanup")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, sqlite3.Error) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error
