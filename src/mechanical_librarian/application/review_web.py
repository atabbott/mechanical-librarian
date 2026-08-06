"""Authenticated, read-only current review-case application boundary."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.review_case import ReviewCase


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
    def revoke_review_session(self, session_digest: str, now: int) -> None: ...
    def get_review_case(self, review_case_id: str) -> str: ...
    def get_review_case_report(self, review_case_id: str) -> str: ...
    def get_review_model_advice(
        self, review_case_id: str
    ) -> dict[str, object]: ...
    def get_review_case_history(self, review_case_id: str, limit: int) -> tuple[dict[str, object], ...]: ...


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

    def logout(self, session_token: str, csrf_token: str) -> None:
        self.authenticate(session_token, csrf_token)
        try:
            self._repository.revoke_review_session(
                _digest(session_token), self._clock()
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error


class InspectCurrentReviewCase:
    def __init__(self, repository: ReviewWebRepository) -> None:
        self._repository = repository

    def run(self, review_case_id: str) -> dict[str, object]:
        try:
            raw = json.loads(self._repository.get_review_case(review_case_id))
            if type(raw) is not dict or raw.get("current") is not True or type(raw.get("case")) is not dict:
                raise ReviewWebError("stale")
            case = ReviewCase.create_from_data(raw["case"])
            report = MetadataReport.from_json(
                self._repository.get_review_case_report(review_case_id)
            )
            if report.report_id != case.report_id:
                raise ReviewWebError("unavailable")
            advice = self._repository.get_review_model_advice(review_case_id)
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
            fields.append(
                {
                    "current": target_fields[destination],
                    "deterministic_disposition": result["outcome"],
                    "destination": destination,
                    "manual_required": proposal["manual_required"],
                    "notices": reasons,
                    "proposed_values": [
                        alternative["value"] for alternative in alternatives
                    ],
                    "provenance": references,
                    "uncertainty": (
                        reasons
                        if result["outcome"] == "unresolved_choice"
                        or proposal["manual_required"] is True
                        else []
                    ),
                }
            )
        return {
            "current": True,
            "fields": fields,
            "model_advice": advice,
            "review_case_id": case.review_case_id,
            "schema_version": "review-case-detail-v1",
            "target": {
                "connection": report.target.connection,
                "item": report.target.item,
                "library": report.target.library,
            },
        }

    def evidence(self, review_case_id: str, limit: int = 50) -> dict[str, object]:
        detail = self.run(review_case_id)
        fields = detail["fields"]
        assert type(fields) is list
        wanted = {
            reference
            for field in fields
            for reference in field["provenance"]
        }
        try:
            report = MetadataReport.from_json(
                self._repository.get_review_case_report(review_case_id)
            )
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        evidence = _evidence_by_reference(report)
        bounded = [
            evidence[reference]
            for reference in sorted(wanted)
            if reference in evidence
        ][:limit]
        return {
            "items": bounded,
            "next_cursor": None,
            "review_case_id": review_case_id,
            "schema_version": "review-case-evidence-v1",
        }

    def history(self, review_case_id: str, limit: int = 50) -> dict[str, object]:
        self.run(review_case_id)
        try:
            items = self._repository.get_review_case_history(review_case_id, limit)
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        return {
            "items": list(items),
            "next_cursor": None,
            "review_case_id": review_case_id,
            "schema_version": "review-case-history-v1",
        }


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_token(value: object) -> bool:
    return type(value) is str and 32 <= len(value) <= 128 and value.strip() == value


def _evidence_by_reference(
    report: MetadataReport,
) -> dict[str, dict[str, str]]:
    data = report.to_data()
    values: dict[str, dict[str, str]] = {}

    def add(source: str, fact: object) -> None:
        if type(fact) is not dict or type(fact.get("reference")) is not str:
            return
        reference = fact["reference"]
        values[reference] = {
            "fact": json.dumps(
                fact, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
            "kind": str(fact.get("kind", "evidence")),
            "reference": reference,
            "source": source,
        }

    local = data["local"]
    assert type(local) is dict
    for fact in local["claims"]:
        add("local", fact)
    for provider in data["providers"]:
        assert type(provider) is dict
        for candidate in provider["candidates"]:
            assert type(candidate) is dict
            for fact in candidate["claims"]:
                add("provider_claim", fact)
            for fact in candidate["evidence"]:
                add("provider_evidence", fact)
    for fact in data["identifier_evidence"]:
        add("identifier", fact)
    for fact in data["covers"]:
        add("cover", fact)
    return values
