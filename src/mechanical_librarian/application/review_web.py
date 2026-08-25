"""Authenticated current-case queries and one exact human decision command."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.application.review_pipeline import (
    preparation_failure_reference,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    decision_actions,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderLaneObservation,
    ReportValueError,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import canonical_target_fields
from mechanical_librarian.domain.review_case import ReviewCase, ReviewCaseValueError
from mechanical_librarian.domain.review_field_classification import (
    ReviewFieldCause,
    classify_review_field,
)


class ReviewWebError(RuntimeError):
    """A fixed, value-free review-browser failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SessionTokens:
    session_token: str
    csrf_token: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class CurrentReviewCaseSnapshot:
    case_document: str
    report_document: str
    decision_documents: tuple[str, ...]
    history: tuple[dict[str, object], ...]
    automatic_attention_destinations: tuple[str, ...] = ()
    automatic_safety_suspended_destinations: tuple[str, ...] = ()
    shadow_abstention_destinations: tuple[str, ...] = ()
    override_blocked_destinations: tuple[str, ...] = ()
    suspended_destinations: tuple[str, ...] = ()
    suspension_causes: tuple[tuple[str, ReviewFieldCause], ...] = ()
    cover_consideration_document: str | None = None
    cover_history: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewLibrarySnapshot:
    rows: tuple[
        tuple[
            str | None,
            str,
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            str | None,
            int,
        ],
        ...,
    ]
    total: int
    attention_total: int


@dataclass(frozen=True, slots=True)
class ReviewAttentionSnapshot:
    rows: tuple[tuple[str, str, str, int, str | None, ReviewFieldCause | None], ...]
    total: int


class ReviewSessionStore(Protocol):
    def begin_login(
        self, now: int, window_seconds: int, maximum_failures: int
    ) -> bool: ...
    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
    ) -> None: ...
    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]: ...
    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str: ...
    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str: ...


class CurrentReviewCaseStore(Protocol):
    def review_case_exists(self, review_case_id: str) -> bool: ...
    def get_review_case(self, review_case_id: str) -> str: ...
    def review_case_ready_at(self, review_case_id: str) -> int | None: ...
    def current_review_case_successor(self, review_case_id: str) -> str | None: ...
    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot: ...


class ReviewLibraryStore(Protocol):
    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> ReviewLibrarySnapshot: ...
    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot: ...


class HumanDecisionStore(CurrentReviewCaseStore, Protocol):
    def find_human_decision(self, idempotency_digest: str) -> str | None: ...
    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
    ) -> str: ...


class OwnerSessionService:
    """Owner login, authentication, rotation, and revocation over one store.

    ``ReviewSessionStore`` is a protocol the composition root binds to an
    arbitrary adapter, and the password verifier is an injected callable, so
    neither declares an error type and no call below has an enumerable raisable
    set. Every handler in this class therefore stays broad over the one call it
    guards and converts the failure into a ``ReviewWebError`` with the cause
    chained: none discards a failure, and none reads a fault as an answer the
    store or the verifier gave. The two that answer with something other than
    ``persistence`` say why where they stand.
    """

    def __init__(
        self,
        repository: ReviewSessionStore,
        verify_password: Callable[[str], bool],
        clock: Callable[[], int],
        lifetime_seconds: int,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        self._repository = repository
        self._verify_password = verify_password
        self._clock = clock
        self._lifetime_seconds = lifetime_seconds
        self._token_factory = token_factory

    def login(self, password: str) -> SessionTokens:
        if type(password) is not str or not 1 <= len(password.encode("utf-8")) <= 1024:
            raise ReviewWebError("invalid_credentials")
        now = self._clock()
        try:
            permitted = self._repository.begin_login(now, 300, 5)
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if permitted is not True:
            raise ReviewWebError("throttled")
        try:
            verified = self._verify_password(password)
        except Exception as error:
            # The verifier answers with a bool, and a wrong password is
            # ``False`` rather than a raise. A raise is therefore a fault in the
            # hasher or the stored digest, and reporting it as
            # ``invalid_credentials`` told the owner their password was wrong
            # for a condition no password can satisfy.
            raise ReviewWebError("runtime") from error
        if not verified:
            raise ReviewWebError("invalid_credentials")
        session_token = self._token_factory(32)
        csrf_token = self._token_factory(32)
        if not 32 <= len(session_token) <= 128 or not 32 <= len(csrf_token) <= 128:
            raise ReviewWebError("runtime")
        expires_at = now + self._lifetime_seconds
        try:
            self._repository.create_review_session(
                _digest(session_token), _digest(csrf_token), now, expires_at
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        return SessionTokens(session_token, csrf_token, expires_at)

    def authenticate(self, session_token: str, csrf_token: str | None = None) -> str:
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        try:
            actor, expected_csrf, _ = self._repository.authenticate_review_session(
                _digest(session_token), self._clock()
            )
        except Exception as error:
            # Authentication is fail-closed, so the departure from this class's
            # persistence invariant is deliberate: no answer from the store is
            # not a session, whatever withheld the answer. The cause stays
            # chained, so a store fault remains legible in the traceback while
            # never being read as an authenticated actor.
            raise ReviewWebError("unauthorized") from error
        if csrf_token is not None and (
            not _bounded_token(csrf_token)
            or not secrets.compare_digest(expected_csrf, _digest(csrf_token))
        ):
            raise ReviewWebError("csrf")
        return actor

    def issue_browser_csrf(self, session_token: str) -> str:
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        csrf_token = self._token_factory(32)
        if not 32 <= len(csrf_token) <= 128:
            raise ReviewWebError("runtime")
        try:
            outcome = self._repository.rotate_review_session_csrf(
                _digest(session_token), _digest(csrf_token), self._clock()
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if outcome != "rotated":
            raise ReviewWebError("unauthorized")
        return csrf_token

    def logout(self, session_token: str, csrf_token: str) -> None:
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        if not _bounded_token(csrf_token):
            raise ReviewWebError("csrf")
        try:
            outcome = self._repository.revoke_review_session(
                _digest(session_token), _digest(csrf_token), self._clock()
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if outcome != "revoked":
            raise ReviewWebError(
                outcome if outcome in {"unauthorized", "csrf"} else "persistence"
            )


class InspectCurrentReviewCase:
    def __init__(
        self,
        repository: CurrentReviewCaseStore,
        cover_considerations: CoverConsiderationService | None = None,
    ) -> None:
        self._repository = repository
        self._cover_considerations = cover_considerations

    def _read(
        self, review_case_id: str, history_limit: int | None = None
    ) -> tuple[dict[str, object], MetadataReport, CurrentReviewCaseSnapshot]:
        try:
            snapshot = self._repository.read_current_review_case(
                review_case_id, history_limit
            )
            raw = json.loads(snapshot.case_document)
            if (
                type(raw) is not dict
                or raw.get("current") is not True
                or type(raw.get("case")) is not dict
            ):
                raise ReviewWebError("stale")
            case = ReviewCase.create_from_data(raw["case"])
            report = MetadataReport.from_json(snapshot.report_document)
            if report.report_id != case.report_id:
                raise ReviewWebError("unavailable")
            decisions: dict[str, HumanDecision] = {}
            automatic: dict[str, dict[str, object]] = {}
            # Declared once: the parse loop below binds a decision, while the
            # per-field projection later looks one up and may find none.
            decision: HumanDecision | None
            for document in snapshot.decision_documents:
                value = json.loads(document)
                if type(value) is not dict:
                    raise ReviewWebError("unavailable")
                if value.get("origin") == "automatic_policy":
                    # The durable authority document deliberately contains
                    # qualification and replay bindings.  The review surface is
                    # a closed read model, never that document by accident.
                    automatic[str(value["destination"])] = {
                        key: value[key]
                        for key in (
                            "decision_id",
                            "destination",
                            "occurred_at",
                            "outcome",
                            "proposal_id",
                            "review_case_id",
                            "target_fingerprint",
                        )
                    } | {"candidate_id": value.get("candidate_id")}
                else:
                    decision = HumanDecision.from_json(document)
                    decisions[decision.destination] = decision
        except ReviewWebError:
            raise
        except Exception as error:
            # The block spans an undeclared repository read, the domain parsers
            # that reject the documents it returns, and this layer's own
            # projection of those documents into the read model, so nothing
            # reaching here has an enumerable origin. Every one of them is the
            # same outcome: one snapshot the surface cannot show. The codes
            # chosen above are re-raised untouched first, and the cause is
            # chained.
            raise ReviewWebError("unavailable") from error
        target_fields = {
            destination.value: value.to_data()
            for destination, value in canonical_target_fields(
                report.target.fields
            ).items()
        }
        current_cover_consideration = (
            None
            if self._cover_considerations is None
            else self._cover_considerations.project(
                snapshot.cover_consideration_document
            )
        )
        if current_cover_consideration is None:
            # A stale or replay-obsolete cover decision remains immutable history,
            # but it no longer resolves the current owner interaction.
            decisions.pop("cover", None)
        fields = []
        suspension_causes = dict(snapshot.suspension_causes)
        # One index per case, not one per field. This read is what next-field
        # authority waits on after a decision, and the index is a pure function
        # of the report, so rebuilding it inside the loop multiplied the only
        # cost on that path by the number of fields under review.
        evidence = _evidence_by_reference(report)
        publication_date_present = any(
            value["destination"] == "publishedDate" for value in case.proposals
        )
        for proposal in case.proposals:
            result = proposal["result"]
            assert type(result) is dict
            destination = cast(str, proposal["destination"])
            all_alternatives = result["alternatives"]
            assert type(all_alternatives) is list
            eligible_alternatives = [
                alternative
                for alternative in all_alternatives
                if alternative["assessment"] != "preserves_target"
            ]
            alternatives = all_alternatives
            references = sorted(
                {
                    reference
                    for alternative in alternatives
                    for reference in alternative["evidence_references"]
                }
            )
            reasons = result["reasons"]
            assert type(reasons) is list
            decision = decisions.get(destination)
            automatic_decision = automatic.get(destination)
            if automatic_decision is not None:
                candidate_id = automatic_decision.get("candidate_id")
                selected = next(
                    (
                        alternative["value"]
                        for alternative in alternatives
                        if alternative["alternative_id"] == candidate_id
                    ),
                    None,
                )
                if automatic_decision["outcome"] == "candidate" and selected is None:
                    raise ReviewWebError("unavailable")
                automatic_decision = {
                    **automatic_decision,
                    "selected_value": selected,
                }
            choice_contexts = _choice_contexts(
                # Direct choices pair projected values with proposed contexts by
                # position. Keep their exact eligible order first, while
                # retaining preservation lineage after it for inspection.
                [
                    *eligible_alternatives,
                    *(
                        alternative
                        for alternative in all_alternatives
                        if alternative["assessment"] == "preserves_target"
                    ),
                ],
                result,
                report,
                target_fields[destination],
                automatic_decision,
                evidence,
            )
            classification = classify_review_field(
                attention_candidate=_needs_attention(
                    proposal,
                    snapshot.automatic_attention_destinations,
                ),
                automatic_outcome=(
                    "abstention"
                    if destination in snapshot.shadow_abstention_destinations
                    else None
                    if automatic_decision is None
                    else str(automatic_decision["outcome"])
                ),
                override_available=(
                    destination not in snapshot.override_blocked_destinations
                ),
                has_human_decision=decision is not None,
                suspended=destination in snapshot.suspended_destinations,
                cause=(
                    "automatic_safety_suspended"
                    if destination in snapshot.automatic_safety_suspended_destinations
                    else suspension_causes.get(destination)
                ),
                current_value=target_fields[destination]["value"],
                proposed_values=tuple(
                    alternative["value"] for alternative in alternatives
                ),
            )
            actions = decision_actions(proposal)
            # Classification retains the field's owner context. Action
            # availability is a separate exact-proposal contract; an empty
            # action list is context-only, never an implied choice envelope.
            needs_attention = classification.needs_attention and bool(actions)
            inventory_role = _field_inventory_role(
                classification.state,
                destination,
                report.file.format,
                alternatives,
                publication_date_present,
            )
            if inventory_role == "omitted":
                continue
            field = {
                "current": target_fields[destination],
                "deterministic_disposition": result["outcome"],
                "destination": destination,
                "manual_required": proposal["manual_required"],
                "human_decision": (
                    None if decision is None else _decision_projection(decision)
                ),
                "automatic_decision": automatic_decision,
                "manual_hold": decision is not None and automatic_decision is not None,
                "classification": classification.state,
                "classification_reason": classification.reason,
                "editable": classification.editable and bool(actions),
                "navigation_role": classification.navigation_role,
                "needs_attention": needs_attention,
                "decision_actions": list(actions),
                "inventory_role": inventory_role,
                "preservation_only": classification.preservation_only,
                "notices": reasons,
                "proposed_values": [
                    alternative["value"] for alternative in eligible_alternatives
                ],
                "choice_contexts": choice_contexts,
                "provenance": references,
                "corroboration_sources": _corroboration_source_labels(
                    report,
                    references,
                    reasons,
                ),
                "proposal_id": proposal["proposal_id"],
                "target_fingerprint": case.target_fingerprint,
                "uncertainty": (
                    reasons
                    if result["outcome"] == "unresolved_choice"
                    or proposal["manual_required"] is True
                    else []
                ),
            }
            if destination == "cover" and self._cover_considerations is not None:
                field["cover_consideration"] = current_cover_consideration
            fields.append(field)
        fields.sort(key=lambda value: not value["needs_attention"])
        return (
            {
                "comparison_notices": _provider_comparison_notices(report),
                "current": True,
                "fields": fields,
                "review_case_id": case.review_case_id,
                "schema_version": "review-case-detail-v2",
                # Read models never learn write authority. The composition root
                # stamps the truth before the view leaves the application, and
                # the default keeps a surface that never asks honest.
                "writes_consented": False,
                "target": {
                    "authors": _report_authors(report),
                    "connection": report.target.connection,
                    "item": report.target.item,
                    "library": report.target.library,
                    "media_kind": report.file.format,
                    "title": report.target.current_title or "Untitled",
                },
            },
            report,
            snapshot,
        )

    def successor(self, review_case_id: str) -> str | None:
        """Name the current replacement for one stale case, if it has one."""
        try:
            return self._repository.current_review_case_successor(review_case_id)
        except Exception as error:
            raise ReviewWebError("unavailable") from error

    def run(self, review_case_id: str) -> dict[str, object]:
        detail, _, _ = self._read(review_case_id)
        return detail

    def evidence(
        self,
        review_case_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        detail, report, _ = self._read(review_case_id)
        fields = detail["fields"]
        assert type(fields) is list
        wanted = {reference for field in fields for reference in field["provenance"]}
        evidence = _evidence_by_reference(report)
        missing_lineage_count = len(wanted.difference(evidence))
        reference_set_digest = hashlib.sha256(
            json.dumps(
                sorted(wanted),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        ordered = [reference for reference in sorted(wanted) if reference in evidence]
        bounded_limit = min(max(limit, 1), 50)
        offset = _decode_cursor(
            cursor,
            "evidence",
            review_case_id,
            reference_set_digest,
        )
        bounded = ordered[offset : offset + bounded_limit]
        return {
            "items": [_choice_evidence(evidence[reference]) for reference in bounded],
            "missing_lineage_count": missing_lineage_count,
            "next_cursor": (
                _encode_cursor(
                    offset + bounded_limit,
                    "evidence",
                    review_case_id,
                    reference_set_digest,
                )
                if offset + bounded_limit < len(ordered)
                else None
            ),
            "review_case_id": review_case_id,
            "schema_version": "review-case-evidence-v3",
            "source_ledger": _provider_outcomes(report),
        }

    def history(self, review_case_id: str, limit: int = 50) -> dict[str, object]:
        detail, _, snapshot = self._read(review_case_id, limit)
        decisions: dict[str, dict[str, object]] = {}
        for document in snapshot.decision_documents:
            try:
                decision = HumanDecision.from_json(document)
            except HumanDecisionError:
                continue
            decisions[decision.decision_id] = _decision_projection(decision)
        automatic_values: dict[
            str, tuple[dict[str, object] | None, dict[str, object] | None]
        ] = {}
        fields = detail["fields"]
        assert type(fields) is list
        for field in fields:
            assert type(field) is dict
            automatic = field["automatic_decision"]
            current = field["current"]
            destination = field["destination"]
            if (
                type(automatic) is not dict
                or type(automatic.get("decision_id")) is not str
                or type(current) is not dict
                or type(destination) is not str
            ):
                continue
            value_kind = (
                "list"
                if destination in {"authors", "genres", "tags"}
                else "series"
                if destination == "series"
                else "cover"
                if destination == "cover"
                else "text"
            )
            previous = (
                {"kind": value_kind, "value": current["value"]}
                if current.get("state") == "value"
                else None
            )
            selected = (
                cast(dict[str, object], automatic.get("selected_value"))
                if automatic.get("outcome") == "candidate"
                else previous
                if automatic.get("outcome") == "keep_current"
                else None
            )
            automatic_values[automatic["decision_id"]] = (previous, selected)
        history_items = []
        for raw in snapshot.history:
            item = dict(raw)
            if item.get("kind") == "automatic_decision_recorded":
                decision_id = item.get("decision_id")
                joined = (
                    automatic_values.get(decision_id)
                    if type(decision_id) is str
                    else None
                )
                item["previous_value"], item["selected_value"] = joined or (
                    None,
                    None,
                )
            history_items.append(item)
        cover_history = []
        for raw in snapshot.cover_history:
            item = dict(raw)
            consideration_document = item.pop("consideration_document", None)
            if (
                type(consideration_document) is not str
                or self._cover_considerations is None
            ):
                raise ReviewWebError("history")
            item["consideration"] = self._cover_considerations.project_historical(
                consideration_document
            )
            decision_id = item.get("decision_id")
            item["decision"] = (
                decisions.get(decision_id) if type(decision_id) is str else None
            )
            cover_history.append(item)
        items = sorted(
            (*history_items, *cover_history),
            key=lambda item: int(cast(int, item.get("occurred_at", 0))),
            reverse=True,
        )[:limit]
        return {
            "items": list(items),
            "next_cursor": None,
            "review_case_id": review_case_id,
            "schema_version": "review-case-history-v2",
        }


class RecordHumanDecision:
    """Record one explicit owner decision without granting apply authority.

    ``HumanDecisionStore`` is a protocol with no declared error type, so the
    handlers over its individual calls stay broad and convert to a
    ``ReviewWebError("persistence")`` with the cause chained. Every store call
    now carries one, which is what makes ``persistence`` a true statement about
    where the fault was rather than this command's answer for any cause it
    cannot name. ``HumanDecisionError`` and the codes chosen here are re-raised
    ahead of every conversion so a domain refusal is never relabelled as
    storage. Two handlers depart from that shape and say so where they stand:
    the advisory readiness read, the only one that discards its failure, and the
    last resort over the whole command, which is over no store call at all and
    answers ``unavailable`` for this layer's own projection.
    """

    def __init__(
        self,
        repository: HumanDecisionStore,
        clock: Callable[[], int],
        timing: StageTimer | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._timing = timing or StageTimer(None)

    def run(
        self,
        review_case_id: str,
        *,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        actor: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_key: str,
        consideration_id: str | None = None,
    ) -> dict[str, object]:
        try:
            idempotency_digest = HumanDecision.idempotency_digest_for(idempotency_key)
            try:
                replay_document = self._repository.find_human_decision(
                    idempotency_digest
                )
                replay = (
                    None
                    if replay_document is None
                    else HumanDecision.from_json(replay_document)
                )
            except Exception as error:
                raise ReviewWebError("persistence") from error
            if replay is not None:
                if not replay.matches_request(
                    review_case_id=review_case_id,
                    proposal_id=proposal_id,
                    target_fingerprint=target_fingerprint,
                    destination=destination,
                    actor=actor,
                    outcome=outcome,
                    selected_value=selected_value,
                    idempotency_key=idempotency_key,
                ):
                    raise ReviewWebError("idempotency_conflict")
                try:
                    stored_replay = HumanDecision.from_json(
                        self._repository.record_human_decision(
                            replay,
                            cover_consideration_id=consideration_id,
                        )
                    )
                except HumanDecisionError:
                    raise
                except Exception as error:
                    raise ReviewWebError("persistence") from error
                return {
                    **_decision_projection(stored_replay),
                    "schema_version": "human-decision-ack-v1",
                }
            try:
                case_document = self._repository.get_review_case(review_case_id)
            except Exception as error:
                # The store cannot say why the read failed, so a second read
                # settles absence against fault. A failure of that second read
                # proves nothing about the case and stays ``persistence``
                # rather than being resolved as ``not_found``.
                try:
                    exists = self._repository.review_case_exists(review_case_id)
                except Exception as persistence_error:
                    raise ReviewWebError("persistence") from persistence_error
                raise ReviewWebError(
                    "persistence" if exists else "not_found"
                ) from error
            raw = json.loads(case_document)
            if (
                type(raw) is not dict
                or raw.get("current") is not True
                or type(raw.get("case")) is not dict
            ):
                raise ReviewWebError("stale")
            case = ReviewCase.create_from_data(raw["case"])
            try:
                ready_at = self._repository.review_case_ready_at(review_case_id)
            except Exception:
                # This timestamp is advisory and cannot gate an owner decision,
                # so every failure of the read -- including a fault the store
                # cannot name -- must leave the decision itself unaffected. The
                # only consequence is that no ``human_wait`` sample is recorded.
                ready_at = None
            decision = HumanDecision.create(
                case,
                proposal_id=proposal_id,
                target_fingerprint=target_fingerprint,
                destination=destination,
                actor=actor,
                outcome=outcome,
                selected_value=selected_value,
                idempotency_key=idempotency_key,
                occurred_at=self._clock(),
            )
            try:
                stored = HumanDecision.from_json(
                    self._repository.record_human_decision(
                        decision,
                        cover_consideration_id=consideration_id,
                    )
                )
            except HumanDecisionError:
                raise
            except Exception as error:
                raise ReviewWebError("persistence") from error
            if stored.request_digest != decision.request_digest:
                raise ReviewWebError("persistence")
            if (
                type(ready_at) is int
                and ready_at >= 0
                and stored.occurred_at >= ready_at
            ):
                self._timing.record(
                    "human_wait",
                    "owner",
                    (stored.occurred_at - ready_at) * 1_000,
                    recorded_at=stored.occurred_at,
                )
            return {
                **_decision_projection(stored),
                "schema_version": "human-decision-ack-v1",
            }
        except ReviewWebError:
            raise
        except HumanDecisionError as error:
            raise ReviewWebError(error.code) from None
        except Exception as error:
            # Last resort over the whole command. Every store call now carries
            # its own ``persistence`` handler, so what reaches here touched no
            # store: the idempotency digest, the case parse, the clock reading,
            # the decision the domain builds from them, and the acknowledgement
            # projection. Reporting those as ``persistence`` named a store that
            # had answered correctly, or had not been asked at all. They are
            # ``unavailable`` instead -- still one code for a cause this layer
            # cannot name, but no longer a false finding about where the fault
            # was. Domain refusals and the codes chosen above keep their own
            # identity, and the cause is chained, so the origin stays legible.
            raise ReviewWebError("unavailable") from error


class BrowseReviewLibrary:
    def __init__(self, repository: ReviewLibraryStore) -> None:
        self._repository = repository

    def library(
        self,
        *,
        lens: str,
        search: str | None,
        limit: int,
        cursor: str | None,
    ) -> dict[str, object]:
        normalized = _review_search(search)
        if lens not in {"all", "needs_attention", "no_attention_needed"}:
            raise ReviewWebError("request")
        bounded_limit = _review_limit(limit)
        offset = _decode_cursor(cursor, "library", lens, normalized)
        attention = None if lens == "all" else lens == "needs_attention"
        try:
            snapshot = self._repository.read_review_library(
                normalized, bounded_limit + 1, offset, attention
            )
            items = []
            for row in snapshot.rows[:bounded_limit]:
                (
                    case_document,
                    report_document,
                    decision_documents,
                    automatic_attention_destinations,
                    suspended_destinations,
                    shadow_abstention_destinations,
                    terminal_document,
                    attention_count,
                ) = row
                items.append(
                    _library_item(
                        case_document,
                        report_document,
                        decision_documents,
                        automatic_attention_destinations,
                        suspended_destinations,
                        shadow_abstention_destinations,
                        terminal_document,
                        attention_count,
                    )
                )
        except Exception as error:
            # The undeclared page read and the row projection are one outcome:
            # a page this surface cannot render. The row is consumed at its
            # declared width, so a shape the adapter no longer supplies fails
            # the page rather than silently dropping a work from the library.
            raise ReviewWebError("unavailable") from error
        return {
            "counts": {
                "all": snapshot.total,
                "needs_attention": snapshot.attention_total,
                "no_attention_needed": snapshot.total - snapshot.attention_total,
            },
            "items": items,
            "lens": lens,
            "next_cursor": (
                _encode_cursor(offset + bounded_limit, "library", lens, normalized)
                if len(snapshot.rows) > bounded_limit
                else None
            ),
            # Browsing is only reversible if the page before this one can be
            # named. The cursor already carries the offset and its lens/query
            # binding, so the backward step is the same encoding, clamped at the
            # first page.
            "previous_cursor": (
                _encode_cursor(
                    max(offset - bounded_limit, 0), "library", lens, normalized
                )
                if offset > 0
                else None
            ),
            "page": offset // bounded_limit + 1,
            "query": normalized,
            "schema_version": "review-library-v1",
        }

    def attention(
        self,
        *,
        search: str | None,
        limit: int,
        cursor: str | None,
    ) -> dict[str, object]:
        normalized = _review_search(search)
        bounded_limit = _review_limit(limit)
        offset = _decode_cursor(cursor, "attention", "needs_attention", normalized)
        try:
            # A row may become contradictory after the SQL snapshot's durable
            # membership check. Read at most two existing bounded repository
            # pages to replace it; the authoritative raw total proves whether
            # that bounded scan reached terminal exhaustion.
            scan_offset = offset
            consumed = 0
            items: list[dict[str, object]] = []
            has_more = False
            snapshot: ReviewAttentionSnapshot | None = None
            raw_total: int | None = None
            for batch in range(2):
                snapshot = self._repository.read_review_attention(
                    normalized, bounded_limit + 1, scan_offset
                )
                raw_rows = snapshot.rows
                if raw_total is None:
                    raw_total = snapshot.total
                for row in raw_rows:
                    (
                        case_document,
                        report_document,
                        destination,
                        work_attention_count,
                        automatic_outcome,
                        cause,
                    ) = row
                    item = _attention_item(
                        case_document,
                        report_document,
                        destination,
                        work_attention_count,
                        automatic_outcome=automatic_outcome,
                        cause=cause,
                    )
                    if len(items) < bounded_limit:
                        consumed += 1
                        if item is not None:
                            items.append(item)
                        continue
                    if item is not None:
                        # Leave the visible successor at the next cursor. A
                        # contradictory lookahead row is safe to consume, but
                        # this row must remain available to the next page.
                        has_more = True
                        break
                    consumed += 1
                if has_more:
                    break
                if scan_offset + len(raw_rows) >= raw_total:
                    break
                if batch == 1:
                    # The bounded race-defense scan cannot prove that a later
                    # raw page is empty.  Keep the cursor truthful rather
                    # than claiming terminal exhaustion.
                    has_more = True
                    break
                scan_offset += len(raw_rows)
            if snapshot is None:
                raise ValueError
        except Exception as error:
            # As in ``library``: the undeclared page reads and the row
            # projection they feed are one outcome. The ``ValueError`` above is
            # this scan's own unreachable-state guard and is deliberately
            # caught here rather than escaping as a bare fault.
            raise ReviewWebError("unavailable") from error
        return {
            "items": items,
            "next_cursor": (
                _encode_cursor(
                    offset + consumed,
                    "attention",
                    "needs_attention",
                    normalized,
                )
                if has_more
                else None
            ),
            "query": normalized,
            "schema_version": "review-attention-v1",
            "total": raw_total,
        }


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decision_projection(decision: HumanDecision) -> dict[str, object]:
    return {
        "actor": decision.actor,
        "decision_id": decision.decision_id,
        "destination": decision.destination,
        "occurred_at": decision.occurred_at,
        "outcome": decision.outcome,
        "proposal_id": decision.proposal_id,
        "review_case_id": decision.review_case_id,
        "selected_value": decision.selected_value,
        "target_fingerprint": decision.target_fingerprint,
    }


def _corroboration_source_labels(
    report: MetadataReport,
    references: list[str],
    reasons: list[object],
) -> list[str]:
    """Project only closed provider authorities for #416 creator corroboration."""
    if reasons != ["explicit_author_corroborates_untyped_creator"]:
        return []
    labels = {
        "hardcover": "Hardcover",
        "open_library": "Open Library",
        "google_books": "Google Books",
    }
    by_reference = {
        claim.reference: provider.authority
        for provider in report.providers
        if provider.authority in labels
        for candidate in provider.candidates
        for claim in candidate.claims
    }
    authorities = {
        by_reference[reference] for reference in references if reference in by_reference
    }
    return [labels[authority] for authority in sorted(authorities)]


def _choice_contexts(
    alternatives: list[dict[str, object]],
    result: dict[str, object],
    report: MetadataReport,
    target: dict[str, object],
    automatic_decision: dict[str, object] | None = None,
    evidence: dict[str, dict[str, str | None]] | None = None,
) -> list[dict[str, object]]:
    """Project retained proposal lineage into owner-readable choice context."""
    local_value_present = target.get("state") == "value" and _current_value_present(
        target.get("value")
    )
    preserves = [
        item for item in alternatives if item.get("assessment") == "preserves_target"
    ]
    evidence = _evidence_by_reference(report) if evidence is None else evidence
    current_rows = [
        _choice_evidence(row)
        for item in preserves
        for reference in cast("list[object]", item.get("evidence_references", []))
        if type(reference) is str and reference in evidence
        for row in [evidence[reference]]
    ]
    current_recommended = automatic_decision is not None and automatic_decision.get(
        "outcome"
    ) in {"keep_current", "preserves_target"}
    contexts: list[dict[str, object]] = [
        {
            "alternative_id": None,
            "evidence": (
                [
                    {
                        "observation": "This value is currently present in the local library.",
                        "role": "local_presence",
                        "source": "Local library record",
                    }
                ]
                if local_value_present
                else []
            )
            + current_rows,
            "limitations": _choice_limitations(preserves[0], result)
            if preserves
            else [],
            "rationale": (
                (
                    "Deterministic policy retained the local value."
                    if current_recommended
                    else "Keep the value currently held by the local library."
                )
                if local_value_present
                else "The local library has no retained value for this field."
            ),
            "recommendation": "recommended"
            if current_recommended
            else "no_recommendation",
            "recommendation_reason": (
                "Deterministic policy retained the local value."
                if current_recommended
                else "No deterministic recommendation is retained for the current value."
            ),
            "selection": "current",
        }
    ]
    for alternative in alternatives:
        references = alternative["evidence_references"]
        assert type(references) is list
        rows = [
            _choice_evidence(evidence[reference])
            for reference in references
            if reference in evidence
        ]
        alternative_id = alternative["alternative_id"]
        assert type(alternative_id) is str
        automatic_candidate = (
            automatic_decision.get("candidate_id")
            if automatic_decision is not None
            and automatic_decision.get("outcome") == "candidate"
            else None
        )
        recommended = alternative_id == automatic_candidate
        contexts.append(
            {
                "alternative_id": alternative_id,
                "evidence": rows,
                "limitations": _choice_limitations(alternative, result),
                "rationale": _choice_rationale(alternative, bool(rows)),
                "recommendation": "recommended" if recommended else "no_recommendation",
                "recommendation_reason": (
                    "Deterministic policy selected this retained alternative."
                    if recommended
                    else "No deterministic recommendation is retained for this alternative."
                ),
                "selection": "proposed",
            }
        )
    return contexts


def _choice_limitations(
    alternative: dict[str, object], result: dict[str, object]
) -> list[str]:
    """Keep material comparison constraints with their retained alternative lineage."""
    alternatives = result.get("alternatives")
    alternative_id = alternative.get("alternative_id")
    if (
        not isinstance(alternatives, list)
        or len(alternatives) != 1
        or alternative_id is None
    ):
        return []
    only = alternatives[0]
    if not isinstance(only, dict) or only.get("alternative_id") != alternative_id:
        return []
    reasons = result.get("reasons")
    if not isinstance(reasons, list):
        return []
    material = {
        "insufficient_independent_corroboration",
        "populated_subjective_field",
        "date_refinement_requires_corroboration",
        "date_year_conflict",
    }
    return [
        reason for reason in reasons if isinstance(reason, str) and reason in material
    ][:3]


def _current_value_present(value: object) -> bool:
    if value is None or value == "":
        return False
    return not (type(value) is list and not value)


def _choice_rationale(alternative: dict[str, object], supported: bool) -> str:
    assessment = cast(
        "dict[object, str]",
        {
            "fills_empty": "fills an empty local value",
            "preserves_target": "preserves the local value",
            "replaces_target": "replaces the local value",
        },
    ).get(alternative.get("assessment"), "retains a proposal value")
    scope = cast(
        "dict[object, str]",
        {
            "cover": "cover-specific",
            "edition": "edition-specific",
            "work": "work-level",
        },
    ).get(alternative.get("scope"), "retained")
    evidence = (
        "with retained supporting evidence"
        if supported
        else "from the retained proposal"
    )
    return f"This alternative {assessment} using {scope} evidence {evidence}."


def _choice_evidence(evidence: dict[str, str | None]) -> dict[str, str]:
    return {
        "observation": _choice_observation(evidence),
        "role": "local_presence"
        if _choice_is_local(evidence)
        else "external_corroboration",
        "source": _choice_source_label(evidence),
    }


def _choice_source_label(evidence: dict[str, str | None]) -> str:
    labels = {
        "google_books": "Google Books",
        "hardcover": "Hardcover",
        "open_library": "Open Library",
    }
    provider = evidence["provider"]
    if provider in labels:
        return labels[provider]
    if _choice_is_local(evidence):
        return "Local library record"
    return "Retained observation (provider name unavailable)"


def _choice_is_local(evidence: dict[str, str | None]) -> bool:
    """Retain local identifier observations as local presence, not provider claims."""
    return evidence["source"] == "local" or (
        evidence["source"] == "identifier"
        and type(evidence.get("reference")) is str
        and cast(str, evidence["reference"]).startswith("local_")
    )


def _choice_observation(evidence: dict[str, str | None]) -> str:
    try:
        fact = json.loads(evidence["fact"] or "{}")
    except (TypeError, ValueError):
        return "A retained observation supports this choice."
    if type(fact) is not dict:
        return "A retained observation supports this choice."
    kind = cast(
        "dict[object, str]",
        {
            "authors": "Contributors",
            "description": "Description",
            "isbn": "ISBN",
            "publication_date": "Publication date",
            "series": "Series membership",
            "title": "Title",
        },
    ).get(fact.get("kind"), "Metadata")
    value = fact.get("value", fact.get("observed"))
    if type(value) in {str, int, float}:
        return f"{kind}: {_choice_text(cast('str | int | float', value))}"
    return f"{kind} observation retained."


def _choice_text(value: str | int | float) -> str:
    text = str(value)
    cleaned = "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith("C")
    )
    normalized = " ".join(cleaned.split())
    if not normalized:
        return "Value retained"
    return normalized[:160] + ("…" if len(normalized) > 160 else "")


def _bounded_token(value: object) -> bool:
    return type(value) is str and 32 <= len(value) <= 128 and value.strip() == value


def _evidence_by_reference(
    report: MetadataReport,
) -> dict[str, dict[str, str | None]]:
    data = report.to_data()
    values: dict[str, dict[str, str | None]] = {}

    def add(
        source: str,
        fact: object,
        *,
        reference_key: str = "reference",
        provider: str | None = None,
        candidate: str | None = None,
    ) -> None:
        if type(fact) is not dict or type(fact.get(reference_key)) is not str:
            return
        reference = fact[reference_key]
        values[reference] = {
            "candidate": candidate,
            "fact": json.dumps(
                fact, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
            "kind": str(fact.get("kind", "evidence")),
            "provider": provider,
            "reference": reference,
            "source": source,
        }

    local = data["local"]
    assert type(local) is dict
    for fact in local["claims"]:
        add("local", fact)
    public_authority_by_provider = {
        provider["provider"]: authority
        for provider in cast("list[object]", data["providers"])
        if type(provider) is dict
        and type(provider.get("provider")) is str
        and type(provider.get("authority")) is str
        and provider["authority"] in {"google_books", "hardcover", "open_library"}
        for authority in [cast(str, provider["authority"])]
    }

    def public_provider(value: object) -> str | None:
        if type(value) is not str:
            return None
        if value in {"google_books", "hardcover", "open_library"}:
            return value
        return public_authority_by_provider.get(value)

    for provider in cast("list[object]", data["providers"]):
        assert type(provider) is dict
        provider_name = provider["provider"]
        assert type(provider_name) is str
        authority = provider.get("authority")
        public_authority = public_provider(authority)
        for candidate in provider["candidates"]:
            assert type(candidate) is dict
            candidate_identity = candidate["identity"]
            assert type(candidate_identity) is str
            for fact in candidate["claims"]:
                add(
                    "provider_claim",
                    fact,
                    provider=public_authority or provider_name,
                    candidate=candidate_identity,
                )
            for fact in candidate["evidence"]:
                assert type(fact) is dict
                reference = (
                    "evidence_"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "candidate": candidate_identity,
                                "evidence": fact,
                                "provider": provider_name,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                )
                rich_fact = dict(fact)
                rich_fact["reference"] = reference
                add(
                    "provider_evidence",
                    rich_fact,
                    provider=public_authority or provider_name,
                    candidate=candidate_identity,
                )
    for fact in cast("list[object]", data["identifier_evidence"]):
        assert type(fact) is dict
        add(
            "identifier",
            fact,
            provider=public_provider(fact.get("provider")),
            candidate=(
                fact.get("candidate_identity")
                if type(fact.get("candidate_identity")) is str
                else None
            ),
        )
    for fact in cast("list[object]", data["covers"]):
        assert type(fact) is dict
        provenance = fact.get("provenance")
        add(
            "cover",
            fact,
            reference_key="source_id",
            provider=public_provider(provenance.get("provider"))
            if type(provenance) is dict
            else None,
        )
    return values


def _review_search(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = " ".join(value.split())
    if len(normalized) > 128:
        raise ReviewWebError("request")
    return normalized


def _review_limit(value: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 50:
        raise ReviewWebError("request")
    return value


def _cursor_binding(kind: str, lens: str, search: str | None) -> str:
    return hashlib.sha256(
        json.dumps(
            [kind, lens, search],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]


def _encode_cursor(offset: int, kind: str, lens: str, search: str | None) -> str:
    if (
        type(offset) is not int
        or isinstance(offset, bool)
        or not 0 <= offset <= 100_000
    ):
        raise ReviewWebError("request")
    payload = json.dumps(
        [offset, _cursor_binding(kind, lens, search)],
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, kind: str, lens: str, search: str | None) -> int:
    if cursor is None:
        return 0
    try:
        if (
            type(cursor) is not str
            or not 1 <= len(cursor) <= 128
            or cursor.strip() != cursor
        ):
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        raw = json.loads(
            base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode(
                "ascii"
            )
        )
        if (
            type(raw) is not list
            or len(raw) != 2
            or type(raw[0]) is not int
            or isinstance(raw[0], bool)
            or not 0 <= raw[0] <= 100_000
            or raw[1] != _cursor_binding(kind, lens, search)
        ):
            raise ValueError
        return raw[0]
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ReviewWebError("request") from error


def _case_and_report(
    case_document: str, report_document: str
) -> tuple[ReviewCase, MetadataReport]:
    try:
        case = ReviewCase.from_json(case_document)
        report = MetadataReport.from_json(report_document)
    except (ReviewCaseValueError, ReportValueError) as error:
        # Both parsers name their malformed-document outcome, so this matches
        # those two types rather than the ``ValueError`` they happen to share
        # with the rest of this projection. The agreement check below is this
        # layer's own and refuses in its own right.
        raise ReviewWebError("unavailable") from error
    if (
        case.report_id != report.report_id
        or case.target != report.target.to_data()
        or case.target_fingerprint != report.target.fingerprint
        or case.file_identity != report.file.identity
    ):
        raise ReviewWebError("unavailable")
    return case, report


def _needs_attention(
    proposal: dict[str, object], automatic_attention_destinations: tuple[str, ...] = ()
) -> bool:
    result = proposal["result"]
    assert type(result) is dict
    reasons = result["reasons"]
    assert type(reasons) is list
    return (
        proposal["destination"] in automatic_attention_destinations
        or (proposal["destination"] == "cover" and "current_cover_verified" in reasons)
        or result["outcome"] == "unresolved_choice"
        or proposal["manual_required"] is True
        or "no_safe_bibliographic_combination" in reasons
    )


def _report_authors(report: MetadataReport) -> list[str]:
    """Derive the bounded current author display value without persistence."""
    value = next(
        (
            field.value
            for field in report.target.fields
            if field.destination is TargetDestination.AUTHORS
        ),
        (),
    )
    return (
        list(cast("tuple[str, ...]", value))
        if type(value) is tuple and all(type(author) is str for author in value)
        else []
    )


# One owner-readable consequence for every closed ``ProviderStatus``. The lane
# state alone told the owner nothing they could act on: "candidate limit
# exceeded" and "malformed" both arrived as "a bounded terminal status". The
# text says what the source did and what followed from it, and never a query,
# header, credential, payload or path.
_NON_LIMITING_PROVIDER_STATUSES = frozenset(
    {"candidates", "disabled", "insufficient_input"}
)
_PROVIDER_OUTCOME_REASONS: dict[str, str] = {
    "candidates": "Returned matching metadata for this work.",
    "no_match": "Returned no matching metadata, so nothing was retained.",
    "disabled": "Not enabled, so this source was never asked.",
    "insufficient_input": "This work lacks the identifiers this source needs.",
    "authentication": "Refused the credentials it was given; nothing was retained.",
    "authorization": "Refused this request; nothing was retained.",
    "rate_limited": "Limited the request rate; a bounded retry is scheduled.",
    "unavailable": "Could not be reached; a bounded retry is scheduled.",
    "incomplete": "Answered only partially, so nothing was retained.",
    "malformed": "Returned an unreadable answer, so nothing was retained.",
    "oversized": "Returned more than the accepted bound, so nothing was retained.",
    "candidate_limit_exceeded": "Returned too many candidates to compare safely.",
    "query_limit_exceeded": "Reached the bounded query allowance for this work.",
}


def _provider_outcomes(report: MetadataReport) -> list[dict[str, object]]:
    """Expose only bounded lane state, never diagnostics or queries.

    Every lane is reported, matches included, so the selected work can show one
    complete source ledger. Suppressing the successful ones left the owner
    unable to tell a source that matched from a source that was never asked.
    """
    outcomes: list[dict[str, object]] = []
    for provider in report.providers:
        lanes = provider.lanes or (provider,)
        for lane in lanes:
            status = lane.status.value
            kind = (
                cast(ProviderLaneObservation, lane).kind
                if provider.lanes
                else provider.query_kind
            ).value
            outcomes.append(
                {
                    "provider_requests": provider.request_accounting.logical_requests,
                    "lane_kind": kind,
                    "provider": _provider_label(provider.authority),
                    "reason": _PROVIDER_OUTCOME_REASONS.get(
                        status, "Ended in a bounded state this build cannot name."
                    ),
                    "status": status,
                }
            )
    return outcomes


def _provider_label(authority: str | None) -> str:
    return cast(
        "dict[str | None, str]",
        {
            "google_books": "Google Books",
            "hardcover": "Hardcover",
            "open_library": "Open Library",
        },
    ).get(authority, "Provider (name unavailable)")


def _provider_comparison_notices(report: MetadataReport) -> list[str]:
    """Keep provider limitations with affected choices, never as case banners."""
    del report
    return []


_TERMINAL_PREPARATION_GUIDANCE: dict[str, tuple[str, str]] = {
    "provider": (
        "A provider ended without a usable result.",
        "No owner action is currently available.",
    ),
    "configuration": (
        "Provider configuration needs owner attention.",
        "Update the configured provider, then scan this library again.",
    ),
    "authentication": (
        "Provider credentials need owner attention.",
        "Update the configured provider credentials, then scan this library again.",
    ),
    "stale": (
        "The item changed while preparation was in progress.",
        "Automatic recovery is pending for the current item.",
    ),
    "transient": (
        "Bounded automatic recovery attempts were exhausted.",
        "No owner action is currently available.",
    ),
    "projection": (
        "Review projection could not be completed.",
        "No owner action is currently available.",
    ),
    "failed": (
        "Preparation failed; no safe detail is available.",
        "No owner action is currently available.",
    ),
    "currentness": (
        "The source changed before preparation could be completed.",
        "Automatic recovery is pending for the current source.",
    ),
    "persistence": (
        "Preparation state could not be durably recorded.",
        "No owner action is currently available.",
    ),
    "unsupported_media": (
        "This media type is not supported for review preparation.",
        "Use a supported EPUB edition, then scan this library again.",
    ),
    "unreadable_media": (
        "The local media file could not be read.",
        "Restore a readable media file in the library, then scan this library again.",
    ),
    "parser_refused": (
        "The EPUB could not be safely read within supported parsing limits.",
        "Use an EPUB that meets the supported parsing limits, then scan this library again.",
    ),
    "missing_required_input": (
        "Required preparation input is unavailable.",
        "Provide the required supported item input, then scan this library again.",
    ),
    "malformed_retained_data": (
        "Retained preparation data could not be safely reopened.",
        "No owner action is currently available.",
    ),
    "internal_failure": (
        "Preparation stopped before a usable review package was produced.",
        "No owner action is currently available.",
    ),
    "unknown": (
        "Preparation failed; no safe detail is available.",
        "No owner action is currently available.",
    ),
}


def _library_item(
    case_document: str | None,
    report_document: str,
    decision_documents: tuple[str, ...],
    automatic_attention_destinations: tuple[str, ...] = (),
    suspended_destinations: tuple[str, ...] = (),
    shadow_abstention_destinations: tuple[str, ...] = (),
    terminal_document: str | None = None,
    attention_count: int | None = None,
) -> dict[str, object]:
    if case_document is None:
        try:
            terminal = json.loads(report_document)
            if (
                type(terminal) is dict
                and terminal.get("schema_version") == "review-terminal-work-v1"
            ):
                status = terminal["status"]
                lane = terminal["lane"]
                root = terminal["failure_root"]
                if (
                    type(status) is not str
                    or type(lane) is not str
                    or (root is not None and type(root) is not str)
                ):
                    raise ValueError
                guidance = _TERMINAL_PREPARATION_GUIDANCE.get(status)
                if guidance is None:
                    raise ValueError
                reason, recovery = guidance
                work_id, item, library = (
                    terminal["work_id"],
                    terminal["item"],
                    terminal["library"],
                )
                if (
                    type(work_id) is not str
                    or type(item) is not str
                    or type(library) is not str
                ):
                    raise ValueError
                return {
                    "authors": [],
                    "attention_count": 0,
                    "attention_state": "no_attention_needed",
                    "detail_url": None,
                    "dispositions": ["terminal_failure"],
                    "item": item,
                    "library": library,
                    "media_kind": "unknown",
                    "review_case_id": None,
                    "title": "Preparation unavailable",
                    "work_id": work_id,
                    "preparation": {
                        "attempt": terminal["attempt"],
                        "diagnostic_reference": preparation_failure_reference(
                            work_id,
                            lane,
                            terminal["attempt"],
                            terminal["recovery_attempt"],
                            status,
                        ),
                        "provider": root or "provider",
                        "reason": reason,
                        "recovery": recovery,
                        "recovery_attempt": terminal["recovery_attempt"],
                        "stage": lane,
                        "status": status,
                    },
                    "preparation_outcomes": [],
                    "summary": {
                        "fields": [],
                        "reason": reason,
                        "state": "preparation_failed",
                    },
                }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReviewWebError("unavailable") from error
    if case_document is None:
        try:
            report = MetadataReport.from_json(report_document)
        except ReportValueError as error:
            # ``from_json`` now funnels every malformed-document outcome, the
            # length guard's codec failure included, into its one declared type.
            raise ReviewWebError("unavailable") from error
        case = None
    else:
        case, report = _case_and_report(case_document, report_document)
    decisions = set()
    automatic_outcomes: dict[str, str] = {}
    for document in decision_documents:
        value = json.loads(document)
        if type(value) is dict and value.get("origin") == "automatic_policy":
            destination = str(value["destination"])
            automatic_outcomes[destination] = str(value["outcome"])
            if value.get("outcome") != "abstention":
                decisions.add(destination)
        else:
            decision = HumanDecision.from_json(document)
            assert case is not None
            decision.validate_for(case)
            decisions.add(decision.destination)
    attention = (
        []
        if case is None
        else [
            value
            for value in case.proposals
            if (
                not _omitted_for_epub(
                    cast(str, value["destination"]), report.file.format
                )
                and value["destination"] not in decisions
                and value["destination"] not in suspended_destinations
                and classify_review_field(
                    attention_candidate=_needs_attention(
                        value,
                        automatic_attention_destinations,
                    ),
                    automatic_outcome=(
                        "abstention"
                        if value["destination"] in shadow_abstention_destinations
                        else automatic_outcomes.get(cast(str, value["destination"]))
                    ),
                    has_human_decision=value["destination"] in decisions,
                    suspended=value["destination"] in suspended_destinations,
                    cause=(
                        "automatic_safety_suspended"
                        if value["destination"] in automatic_attention_destinations
                        else None
                    ),
                    current_value=None,
                    proposed_values=(),
                ).needs_attention
            )
        ]
    )
    outcomes = (
        []
        if case is None
        else sorted(
            {
                value["result"]["outcome"]  # type: ignore[index]
                for value in case.proposals
            }
        )
    )
    material = (
        f"review-library-work-v1\0{report.target.connection}\0"
        f"{report.target.library}\0{report.target.item}"
    )
    work_id = "library_work_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    current_attention_count = (
        len(attention) if attention_count is None else attention_count
    )
    value = {
        "authors": _report_authors(report),
        "attention_count": current_attention_count,
        "attention_state": (
            "needs_attention" if current_attention_count else "no_attention_needed"
        ),
        "detail_url": None if case is None else f"/review/{case.review_case_id}",
        "dispositions": outcomes,
        "item": report.target.item,
        "library": report.target.library,
        "media_kind": report.file.format,
        "review_case_id": None if case is None else case.review_case_id,
        "title": report.target.current_title or "Untitled",
        "work_id": work_id,
        "preparation": None
        if terminal_document is None
        else _library_item(None, terminal_document, ())["preparation"],
        "preparation_outcomes": _provider_outcomes(report),
        # A work with no current review case has no case identity to open, so
        # its current values were unreachable from Library. The bounded target
        # projection travels with the row the owner already has, which keeps
        # every published work inspectable without a second authority.
        "summary": None
        if case is not None
        else {
            "fields": cast(
                "list[dict[str, object]]", report.target.to_data()["fields"]
            ),
            "reason": (
                "No review decision is recorded for this work. Its current values "
                "are shown exactly as Mechanical Librarian last read them."
            ),
            "state": "no_decision_recorded",
        },
    }
    return value


def _field_inventory_role(
    classification: str,
    destination: str,
    media_kind: str,
    alternatives: list[object],
    publication_date_present: bool,
) -> str:
    """Project one canonical field into the owner's finite field inventory."""
    if destination == "publishedYear" and publication_date_present:
        return "omitted"
    if _omitted_for_epub(destination, media_kind):
        return "omitted"
    if classification == "unsupported" and not alternatives:
        return "contextual"
    return "primary"


def _omitted_for_epub(destination: str, media_kind: str) -> bool:
    """Keep EPUB-inapplicable fields out of every owner-facing projection."""
    return media_kind == "epub" and destination in {"narrators", "explicit"}


def _attention_item(
    case_document: str,
    report_document: str,
    destination: str,
    work_attention_count: int,
    automatic_outcome: str | None = None,
    cause: ReviewFieldCause | None = None,
) -> dict[str, object] | None:
    case, report = _case_and_report(case_document, report_document)
    if _omitted_for_epub(destination, report.file.format):
        return None
    if destination == "publishedYear" and any(
        value["destination"] == "publishedDate" for value in case.proposals
    ):
        return None
    proposal = next(
        (value for value in case.proposals if value["destination"] == destination),
        None,
    )
    if proposal is None:
        return None
    classification = classify_review_field(
        attention_candidate=(
            _needs_attention(proposal) or cause == "automatic_safety_suspended"
        ),
        automatic_outcome=("abstention" if automatic_outcome == "abstention" else None),
        has_human_decision=False,
        suspended=False,
        cause=cause,
        current_value=None,
        proposed_values=(),
    )
    if not classification.needs_attention:
        # Query membership can race a durable automatic or human decision.  A
        # stale browse row is omitted; it is never an outage for other work.
        return None
    result = proposal["result"]
    assert type(result) is dict
    return {
        "authors": _report_authors(report),
        "classification_reason": classification.reason,
        "destination": destination,
        "detail_url": f"/review/{case.review_case_id}?field={destination}",
        "item": report.target.item,
        "library": report.target.library,
        "media_kind": report.file.format,
        "notices": result["reasons"],
        "provenance": sorted(
            {
                reference
                for alternative in result["alternatives"]
                for reference in alternative["evidence_references"]
            }
        ),
        "review_case_id": case.review_case_id,
        "title": report.target.current_title or "Untitled",
        "work_attention_count": work_attention_count,
    }
