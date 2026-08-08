"""Authenticated current-case queries and one exact human decision command."""

from __future__ import annotations

import hashlib
import json
import secrets
import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.application.metadata_policy import PolicyTransition


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
    model_advice: dict[str, object]
    decision_documents: tuple[str, ...]
    history: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class ReviewLibrarySnapshot:
    rows: tuple[tuple[str, str, tuple[str, ...]], ...]
    total: int
    attention_total: int


@dataclass(frozen=True, slots=True)
class ReviewAttentionSnapshot:
    rows: tuple[tuple[str, str, str], ...]
    total: int


class ReviewWebRepository(Protocol):
    def begin_login(self, now: int, window_seconds: int, maximum_failures: int) -> bool: ...
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
    def review_case_exists(self, review_case_id: str) -> bool: ...
    def get_review_case(self, review_case_id: str) -> str: ...
    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot: ...
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
    def find_human_decision(self, idempotency_digest: str) -> str | None: ...
    def record_human_decision(self, decision: HumanDecision) -> str: ...
    def create_metadata_policy_draft(self, document: str) -> str: ...
    def get_metadata_policy_draft(self, policy_id: str) -> str: ...
    def current_unapplied_count(self, library: str) -> int: ...
    def active_metadata_policy(self) -> str: ...
    def activate_metadata_policy(self, policy_id: str, expected_active_policy_id: str, resolutions: tuple[tuple[str, str, str], ...], activated_at: int) -> PolicyTransition | None: ...
    def metadata_policy_transition(self) -> PolicyTransition | None: ...
    def retry_metadata_policy_transition(self, transition_id: str, retried_at: int) -> PolicyTransition: ...
    def acknowledge_metadata_policy_transition(self, transition_id: str, acknowledged_at: int) -> PolicyTransition: ...
    def current_resolved_policy_id(self, library: str) -> str: ...
    def current_resolved_metadata_policy(self, library: str): ...


class OwnerSessionService:
    def __init__(
        self,
        repository: ReviewWebRepository,
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
        except Exception:
            verified = False
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
            raise ReviewWebError(outcome if outcome in {"unauthorized", "csrf"} else "persistence")


class InspectCurrentReviewCase:
    def __init__(self, repository: ReviewWebRepository) -> None:
        self._repository = repository

    def _read(
        self, review_case_id: str, history_limit: int | None = None
    ) -> tuple[dict[str, object], MetadataReport, CurrentReviewCaseSnapshot]:
        try:
            snapshot = self._repository.read_current_review_case(
                review_case_id, history_limit
            )
            raw = json.loads(snapshot.case_document)
            if type(raw) is not dict or raw.get("current") is not True or type(raw.get("case")) is not dict:
                raise ReviewWebError("stale")
            case = ReviewCase.create_from_data(raw["case"])
            report = MetadataReport.from_json(snapshot.report_document)
            if report.report_id != case.report_id:
                raise ReviewWebError("unavailable")
            decisions: dict[str, HumanDecision] = {}
            automatic: dict[str, dict[str, object]] = {}
            for document in snapshot.decision_documents:
                value = json.loads(document)
                if type(value) is not dict:
                    raise ReviewWebError("unavailable")
                if value.get("origin") == "automatic_policy":
                    automatic[str(value["destination"])] = value
                else:
                    decision = HumanDecision.from_json(document)
                    decisions[decision.destination] = decision
        except ReviewWebError:
            raise
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        target_fields = {
            value["destination"]: value
            for value in report.target.to_data()["fields"]  # type: ignore[index]
        }
        fields = []
        for proposal in case.proposals:
            result = proposal["result"]
            assert type(result) is dict
            destination = proposal["destination"]
            alternatives = result["alternatives"]
            assert type(alternatives) is list
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
            fields.append(
                {
                    "current": target_fields[destination],
                    "deterministic_disposition": result["outcome"],
                    "destination": destination,
                    "manual_required": proposal["manual_required"],
                    "human_decision": (
                        None if decision is None else _decision_projection(decision)
                    ),
                    "automatic_decision": automatic_decision,
                    "needs_attention": decision is None and automatic_decision is None and _needs_attention(proposal),
                    "notices": reasons,
                    "proposed_values": [
                        alternative["value"] for alternative in alternatives
                    ],
                    "provenance": references,
                    "proposal_id": proposal["proposal_id"],
                    "target_fingerprint": case.target_fingerprint,
                    "uncertainty": (
                        reasons
                        if result["outcome"] == "unresolved_choice"
                        or proposal["manual_required"] is True
                        else []
                    ),
                }
            )
        fields.sort(key=lambda value: not value["needs_attention"])
        return {
            "current": True,
            "fields": fields,
            "model_advice": snapshot.model_advice,
            "review_case_id": case.review_case_id,
            "schema_version": "review-case-detail-v1",
            "target": {
                "connection": report.target.connection,
                "item": report.target.item,
                "library": report.target.library,
            },
        }, report, snapshot

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
        wanted = {
            reference
            for field in fields
            for reference in field["provenance"]
        }
        model_advice = detail["model_advice"]
        assert type(model_advice) is dict
        for result in model_advice["results"]:
            assert type(result) is dict
            for recommendation in result["recommendations"]:
                assert type(recommendation) is dict
                citations = recommendation["citations"]
                assert type(citations) is list
                wanted.update(citations)
        evidence = _evidence_by_reference(report)
        if wanted.difference(evidence):
            raise ReviewWebError("not_found")
        reference_set_digest = hashlib.sha256(
            json.dumps(
                sorted(wanted),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        ordered = [
            evidence[reference]
            for reference in sorted(wanted)
            if reference in evidence
        ]
        bounded_limit = min(max(limit, 1), 50)
        offset = _decode_cursor(
            cursor,
            "evidence",
            review_case_id,
            reference_set_digest,
        )
        bounded = ordered[offset : offset + bounded_limit]
        return {
            "items": bounded,
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
            "schema_version": "review-case-evidence-v2",
        }

    def history(self, review_case_id: str, limit: int = 50) -> dict[str, object]:
        _, _, snapshot = self._read(review_case_id, limit)
        return {
            "items": list(snapshot.history),
            "next_cursor": None,
            "review_case_id": review_case_id,
            "schema_version": "review-case-history-v1",
        }


class RecordHumanDecision:
    """Record one explicit owner decision without granting apply authority."""

    def __init__(
        self,
        repository: ReviewWebRepository,
        clock: Callable[[], int],
    ) -> None:
        self._repository = repository
        self._clock = clock

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
    ) -> dict[str, object]:
        try:
            idempotency_digest = HumanDecision.idempotency_digest_for(
                idempotency_key
            )
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
                return {
                    **_decision_projection(replay),
                    "schema_version": "human-decision-ack-v1",
                }
            try:
                case_document = self._repository.get_review_case(review_case_id)
            except Exception as error:
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
            stored = HumanDecision.from_json(
                self._repository.record_human_decision(decision)
            )
            if stored.request_digest != decision.request_digest:
                raise ReviewWebError("persistence")
            return {
                **_decision_projection(stored),
                "schema_version": "human-decision-ack-v1",
            }
        except ReviewWebError:
            raise
        except HumanDecisionError as error:
            raise ReviewWebError(error.code) from None
        except Exception as error:
            raise ReviewWebError("persistence") from error

class BrowseReviewLibrary:
    def __init__(self, repository: ReviewWebRepository) -> None:
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
        attention = (
            None
            if lens == "all"
            else lens == "needs_attention"
        )
        try:
            snapshot = self._repository.read_review_library(
                normalized, bounded_limit + 1, offset, attention
            )
            items = [
                _library_item(
                    case_document,
                    report_document,
                    decision_documents,
                )
                for case_document, report_document, decision_documents
                in snapshot.rows[:bounded_limit]
            ]
        except Exception as error:
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
                _encode_cursor(
                    offset + bounded_limit, "library", lens, normalized
                )
                if len(snapshot.rows) > bounded_limit
                else None
            ),
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
            snapshot = self._repository.read_review_attention(
                normalized, bounded_limit + 1, offset
            )
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        items = [
            _attention_item(case_document, report_document, destination)
            for case_document, report_document, destination
            in snapshot.rows[:bounded_limit]
        ]
        return {
            "items": items,
            "next_cursor": (
                _encode_cursor(
                    offset + bounded_limit,
                    "attention",
                    "needs_attention",
                    normalized,
                )
                if len(snapshot.rows) > bounded_limit
                else None
            ),
            "query": normalized,
            "schema_version": "review-attention-v1",
            "total": snapshot.total,
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
    for provider in data["providers"]:
        assert type(provider) is dict
        provider_name = provider["provider"]
        assert type(provider_name) is str
        for candidate in provider["candidates"]:
            assert type(candidate) is dict
            candidate_identity = candidate["identity"]
            assert type(candidate_identity) is str
            for fact in candidate["claims"]:
                add(
                    "provider_claim",
                    fact,
                    provider=provider_name,
                    candidate=candidate_identity,
                )
            for fact in candidate["evidence"]:
                assert type(fact) is dict
                reference = "evidence_" + hashlib.sha256(
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
                rich_fact = dict(fact)
                rich_fact["reference"] = reference
                add(
                    "provider_evidence",
                    rich_fact,
                    provider=provider_name,
                    candidate=candidate_identity,
                )
    for fact in data["identifier_evidence"]:
        assert type(fact) is dict
        add(
            "identifier",
            fact,
            provider=(
                fact.get("provider")
                if type(fact.get("provider")) is str
                else None
            ),
            candidate=(
                fact.get("candidate_identity")
                if type(fact.get("candidate_identity")) is str
                else None
            ),
        )
    for fact in data["covers"]:
        assert type(fact) is dict
        provenance = fact.get("provenance")
        add(
            "cover",
            fact,
            reference_key="source_id",
            provider=(
                provenance.get("provider")
                if type(provenance) is dict
                and type(provenance.get("provider")) is str
                else None
            ),
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


def _encode_cursor(
    offset: int, kind: str, lens: str, search: str | None
) -> str:
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


def _decode_cursor(
    cursor: str | None, kind: str, lens: str, search: str | None
) -> int:
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
            base64.b64decode(
                cursor + padding, altchars=b"-_", validate=True
            ).decode("ascii")
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
        if (
            case.report_id != report.report_id
            or case.target != report.target.to_data()
            or case.target_fingerprint != report.target.fingerprint
            or case.file_identity != report.file.identity
        ):
            raise ValueError
        return case, report
    except Exception as error:
        raise ReviewWebError("unavailable") from error


def _needs_attention(proposal: dict[str, object]) -> bool:
    result = proposal["result"]
    assert type(result) is dict
    reasons = result["reasons"]
    assert type(reasons) is list
    return (
        result["outcome"] == "unresolved_choice"
        or proposal["manual_required"] is True
        or "no_safe_bibliographic_combination" in reasons
    )


def _library_item(
    case_document: str,
    report_document: str,
    decision_documents: tuple[str, ...],
) -> dict[str, object]:
    case, report = _case_and_report(case_document, report_document)
    decisions = set()
    for document in decision_documents:
        value = json.loads(document)
        if type(value) is dict and value.get("origin") == "automatic_policy":
            decisions.add(str(value["destination"]))
        else:
            decision = HumanDecision.from_json(document)
            decision.validate_for(case)
            decisions.add(decision.destination)
    attention = [
        value
        for value in case.proposals
        if value["destination"] not in decisions and _needs_attention(value)
    ]
    outcomes = sorted(
        {
            value["result"]["outcome"]  # type: ignore[index]
            for value in case.proposals
        }
    )
    return {
        "attention_count": len(attention),
        "attention_state": (
            "needs_attention" if attention else "no_attention_needed"
        ),
        "detail_url": f"/review/{case.review_case_id}",
        "dispositions": outcomes,
        "item": report.target.item,
        "library": report.target.library,
        "review_case_id": case.review_case_id,
        "title": report.target.current_title or "Untitled",
    }


def _attention_item(
    case_document: str,
    report_document: str,
    destination: str,
) -> dict[str, object]:
    case, report = _case_and_report(case_document, report_document)
    proposal = next(
        (
            value
            for value in case.proposals
            if value["destination"] == destination
        ),
        None,
    )
    if proposal is None or not _needs_attention(proposal):
        raise ReviewWebError("unavailable")
    result = proposal["result"]
    assert type(result) is dict
    return {
        "destination": destination,
        "detail_url": f"/review/{case.review_case_id}?field={destination}",
        "item": report.target.item,
        "library": report.target.library,
        "notices": result["reasons"],
        "provenance": sorted(
            {
                reference
                for alternative in result["alternatives"]  # type: ignore[union-attr]
                for reference in alternative["evidence_references"]
            }
        ),
        "review_case_id": case.review_case_id,
        "title": report.target.current_title or "Untitled",
    }
