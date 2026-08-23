"""Closed, origin-neutral field-effect plan and persisted state machine."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Literal, Self


class FieldEffectError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


SCALAR_DESTINATIONS = frozenset(
    {
        "title",
        "subtitle",
        "publishedYear",
        "publishedDate",
        "publisher",
        "description",
        "isbn",
        "asin",
        "language",
        "cover",
    }
)
LIST_DESTINATIONS = frozenset({"authors", "genres", "tags"})
SERIES_DESTINATION = "series"
SUPPORTED_DESTINATIONS = SCALAR_DESTINATIONS | LIST_DESTINATIONS | {SERIES_DESTINATION}
TERMINAL_STATES = frozenset({"verified", "stale", "superseded", "abandoned"})
FIELD_EFFECT_FAILURES = frozenset(
    {
        None,
        "ambiguous",
        "authority",
        "authority_superseded",
        "backup",
        "capability",
        "circuit_open",
        "consent",
        "consent_revoked",
        "cover_recovery",
        "dependency",
        "not_applied",
        "owner_abandoned",
        "readback",
        "restart_readback",
        "retry_exhausted",
        "target_changed",
        "target_failure",
        "third_value",
    }
)
_STATES = frozenset(
    {
        "planned",
        "claimed",
        "applying",
        "reconciling",
        "retryable",
        "verified",
        "stale",
        "superseded",
        "abandoned",
    }
)
_TRANSITIONS = {
    "planned": frozenset({"claimed", "superseded", "stale", "abandoned"}),
    "claimed": frozenset({"applying", "reconciling", "retryable", "stale"}),
    "applying": frozenset({"verified", "reconciling", "retryable", "stale"}),
    "reconciling": frozenset({"verified", "retryable", "stale"}),
    "retryable": frozenset({"claimed", "stale", "abandoned"}),
}
_HEX = re.compile(r"[0-9a-f]{64}")
_IDENTITY = re.compile(r"[a-z][a-z0-9_-]{0,127}")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def field_value_version(destination: str, value: object) -> str:
    """Bind optimistic currentness to one normalized destination, not an item."""
    normalized = None if value is None else validate_effect_value(destination, value)
    return hashlib.sha256(
        canonical_json(
            {
                "destination": destination,
                "value": normalized,
            }
        ).encode("utf-8")
    ).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _valid_service_token(value: object) -> bool:
    if type(value) is not str or not value.isprintable() or value.isspace():
        return False
    try:
        return 1 <= len(value.encode("utf-8")) <= 1024
    except UnicodeEncodeError:
        return False


def validate_effect_value(destination: str, value: object) -> object:
    """Validate and normalize the closed fake-service ebook value vocabulary."""
    if destination not in SUPPORTED_DESTINATIONS:
        raise FieldEffectError("unsupported_destination")
    if destination in LIST_DESTINATIONS:
        if type(value) is not list or not 1 <= len(value) <= 128:
            raise FieldEffectError("invalid_value")
        if any(
            type(item) is not str or not 1 <= len(item.encode()) <= 1024
            for item in value
        ):
            raise FieldEffectError("invalid_value")
        return list(value)
    if destination == SERIES_DESTINATION:
        if type(value) is not list or not 1 <= len(value) <= 32:
            raise FieldEffectError("invalid_value")
        normalized: list[dict[str, object]] = []
        for item in value:
            if type(item) is not dict or set(item) != {"name", "sequence"}:
                raise FieldEffectError("invalid_value")
            name, sequence = item["name"], item["sequence"]
            if type(name) is not str or not 1 <= len(name.encode()) <= 1024:
                raise FieldEffectError("invalid_value")
            if sequence is not None and (
                type(sequence) is not str or len(sequence.encode()) > 128
            ):
                raise FieldEffectError("invalid_value")
            normalized.append({"name": name, "sequence": sequence})
        return normalized
    if destination == "cover":
        if type(value) is not str or re.fullmatch(r"cover_[0-9a-f]{64}", value) is None:
            raise FieldEffectError("invalid_value")
        return value
    if type(value) is not str or not 1 <= len(value.encode()) <= 32768:
        raise FieldEffectError("invalid_value")
    return value


def project_decision_value(destination: str, value: object) -> object:
    """Convert the exact existing ProposalValue envelope to a target value."""
    if type(value) is not dict or set(value) != {"kind", "value"}:
        raise FieldEffectError("invalid_decision_value")
    expected = (
        "list"
        if destination in LIST_DESTINATIONS
        else "series"
        if destination == SERIES_DESTINATION
        else "cover"
        if destination == "cover"
        else "text"
    )
    if value["kind"] != expected:
        raise FieldEffectError("invalid_decision_value")
    return validate_effect_value(destination, value["value"])


@dataclass(frozen=True, slots=True)
class FieldEffectNoChange:
    """Immutable automatic decision terminal fact; it never authorizes a write."""

    decision_id: str
    review_case_id: str
    report_id: str
    connection: str
    library: str
    work_id: str
    destination: str
    target_fingerprint: str
    target_version: str
    origin: str
    occurred_at: int
    event_id: str

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        review_case_id: str,
        report_id: str,
        connection: str,
        library: str,
        work_id: str,
        destination: str,
        target_fingerprint: str,
        target_version: str,
        occurred_at: int,
    ) -> Self:
        if (
            type(decision_id) is not str
            or not decision_id.startswith("automatic_decision_")
            or _HEX.fullmatch(decision_id.removeprefix("automatic_decision_")) is None
            or type(review_case_id) is not str
            or not review_case_id.startswith("review_case_")
            or _HEX.fullmatch(review_case_id.removeprefix("review_case_")) is None
            or type(report_id) is not str
            or not report_id.startswith("report_")
            or _HEX.fullmatch(report_id.removeprefix("report_")) is None
            or any(
                type(value) is not str or _IDENTITY.fullmatch(value) is None
                for value in (connection, library)
            )
            or not _valid_service_token(work_id)
            or destination not in SUPPORTED_DESTINATIONS
            or type(target_fingerprint) is not str
            or _HEX.fullmatch(target_fingerprint) is None
            or type(target_version) is not str
            or _HEX.fullmatch(target_version) is None
            or type(occurred_at) is not int
            or occurred_at < 0
        ):
            raise FieldEffectError("invalid_no_change")
        core = {
            "connection": connection,
            "decision_id": decision_id,
            "destination": destination,
            "library": library,
            "origin": "automatic_policy",
            "report_id": report_id,
            "review_case_id": review_case_id,
            "target_fingerprint": target_fingerprint,
            "target_version": target_version,
            "work_id": work_id,
        }
        return cls(
            **core,
            occurred_at=occurred_at,
            event_id="field_effect_no_change_" + _digest(core),
        )


@dataclass(frozen=True, slots=True)
class EffectAuthority:
    decision_id: str
    review_case_id: str
    target_fingerprint: str
    source: Literal["persisted_review_case", "fake_service"]
    origin: Literal["human", "automatic_policy"]
    outcome: Literal["candidate"]
    policy_identity: str | None
    qualification_identity: str | None

    def __post_init__(self) -> None:
        if (
            self.origin not in {"human", "automatic_policy"}
            or self.outcome != "candidate"
            or any(
                type(value) is not str
                for value in (
                    self.decision_id,
                    self.review_case_id,
                    self.target_fingerprint,
                    self.source,
                )
            )
        ):
            raise FieldEffectError("invalid_authority")
        prefix = "human_decision_" if self.origin == "human" else "automatic_decision_"
        if (
            not self.decision_id.startswith(prefix)
            or _HEX.fullmatch(self.decision_id[len(prefix) :]) is None
        ):
            raise FieldEffectError("invalid_authority")
        if (
            not self.review_case_id.startswith("review_case_")
            or _HEX.fullmatch(self.review_case_id.removeprefix("review_case_")) is None
            or _HEX.fullmatch(self.target_fingerprint) is None
            or self.source not in {"persisted_review_case", "fake_service"}
        ):
            raise FieldEffectError("invalid_authority")
        if self.origin == "human" and (
            self.policy_identity is not None or self.qualification_identity is not None
        ):
            raise FieldEffectError("invalid_authority")
        if self.origin == "automatic_policy" and (
            type(self.policy_identity) is not str
            or _HEX.fullmatch(str(self.qualification_identity)) is None
        ):
            raise FieldEffectError("invalid_authority")


@dataclass(frozen=True, slots=True)
class EffectConsent:
    consent_id: str
    connection: str
    library: str
    destination: str
    action: Literal["apply_field"]
    envelope_identity: str
    granted_at: int

    @classmethod
    def create(
        cls,
        *,
        connection: str,
        library: str,
        destination: str,
        envelope_identity: str,
        granted_at: int,
    ) -> Self:
        if (
            any(
                type(value) is not str or _IDENTITY.fullmatch(value) is None
                for value in (connection, library)
            )
            or destination not in SUPPORTED_DESTINATIONS
            or type(envelope_identity) is not str
            or _HEX.fullmatch(envelope_identity) is None
            or type(granted_at) is not int
            or granted_at < 0
        ):
            raise FieldEffectError("invalid_consent")
        identity = _digest(
            {
                "action": "apply_field",
                "connection": connection,
                "destination": destination,
                "envelope_identity": envelope_identity,
                "granted_at": granted_at,
                "library": library,
            }
        )
        return cls(
            "effect_consent_" + identity,
            connection,
            library,
            destination,
            "apply_field",
            envelope_identity,
            granted_at,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "action": self.action,
            "connection": self.connection,
            "consent_id": self.consent_id,
            "destination": self.destination,
            "envelope_identity": self.envelope_identity,
            "granted_at": self.granted_at,
            "library": self.library,
            "schema_version": "field-effect-consent-v2",
        }

    @classmethod
    def from_data(cls, raw: object) -> Self:
        keys = {
            "action",
            "connection",
            "consent_id",
            "destination",
            "envelope_identity",
            "granted_at",
            "library",
            "schema_version",
        }
        if (
            type(raw) is not dict
            or set(raw) != keys
            or raw.get("schema_version") != "field-effect-consent-v2"
            or raw.get("action") != "apply_field"
        ):
            raise FieldEffectError("invalid_consent")
        value = cls.create(
            connection=raw["connection"],
            library=raw["library"],
            destination=raw["destination"],
            envelope_identity=raw["envelope_identity"],
            granted_at=raw["granted_at"],
        )
        if value.consent_id != raw["consent_id"]:
            raise FieldEffectError("invalid_consent")
        return value


@dataclass(frozen=True, slots=True)
class FieldEffectPlan:
    plan_id: str
    connection: str
    library: str
    work_id: str
    destination: str
    target_fingerprint: str
    pre_value: object
    desired_value: object
    target_version: str
    dependency_digests: tuple[str, ...]
    authority: EffectAuthority
    consent: EffectConsent
    capability_digest: str
    idempotency_key: str
    created_at: int

    @classmethod
    def create(
        cls,
        *,
        connection: str,
        library: str,
        work_id: str,
        destination: str,
        pre_value: object,
        desired_value: object,
        target_fingerprint: str,
        target_version: str,
        dependency_digests: tuple[str, ...],
        authority: EffectAuthority,
        consent: EffectConsent,
        capability_digest: str,
        created_at: int,
    ) -> Self:
        for value in (connection, library):
            if type(value) is not str or _IDENTITY.fullmatch(value) is None:
                raise FieldEffectError("invalid_identity")
        for value in (work_id, target_version):
            if not _valid_service_token(value):
                raise FieldEffectError("invalid_service_token")
        if type(created_at) is not int or created_at < 0:
            raise FieldEffectError("invalid_time")
        desired = validate_effect_value(destination, desired_value)
        if pre_value is not None:
            validate_effect_value(destination, pre_value)
        if (
            _HEX.fullmatch(target_fingerprint) is None
            or target_fingerprint != authority.target_fingerprint
            or (
                consent.connection,
                consent.library,
                consent.destination,
                consent.action,
            )
            != (connection, library, destination, "apply_field")
        ):
            raise FieldEffectError("invalid_binding")
        if (
            _HEX.fullmatch(capability_digest) is None
            or not dependency_digests
            or any(_HEX.fullmatch(v) is None for v in dependency_digests)
        ):
            raise FieldEffectError("invalid_dependency")
        core = {
            "authority": authority_to_data(authority),
            "capability_digest": capability_digest,
            "connection": connection,
            "dependency_digests": list(dependency_digests),
            "destination": destination,
            "desired_value": desired,
            "library": library,
            "pre_value": pre_value,
            "target_fingerprint": target_fingerprint,
            "target_version": target_version,
            "work_id": work_id,
            "consent": consent.to_data(),
        }
        if len(canonical_json(core).encode()) > 120_000:
            raise FieldEffectError("plan_too_large")
        identity = _digest(core)
        return cls(
            "effect_plan_" + identity,
            connection,
            library,
            work_id,
            destination,
            target_fingerprint,
            pre_value,
            desired,
            target_version,
            dependency_digests,
            authority,
            consent,
            capability_digest,
            "effect_" + identity,
            created_at,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "schema_version": "field-effect-plan-v1",
            "plan_id": self.plan_id,
            "connection": self.connection,
            "library": self.library,
            "work_id": self.work_id,
            "destination": self.destination,
            "target_fingerprint": self.target_fingerprint,
            "pre_value": self.pre_value,
            "desired_value": self.desired_value,
            "target_version": self.target_version,
            "dependency_digests": list(self.dependency_digests),
            "authority": authority_to_data(self.authority),
            "consent": self.consent.to_data(),
            "capability_digest": self.capability_digest,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_data(cls, raw: object) -> Self:
        try:
            keys = {
                "authority",
                "capability_digest",
                "connection",
                "consent",
                "created_at",
                "dependency_digests",
                "desired_value",
                "destination",
                "idempotency_key",
                "library",
                "plan_id",
                "pre_value",
                "schema_version",
                "target_fingerprint",
                "target_version",
                "work_id",
            }
            if (
                type(raw) is not dict
                or set(raw) != keys
                or raw.get("schema_version") != "field-effect-plan-v1"
            ):
                raise FieldEffectError("invalid_plan")
            authority_raw = raw["authority"]
            if type(authority_raw) is not dict:
                raise FieldEffectError("invalid_plan")
            authority = EffectAuthority(
                authority_raw["decision_id"],
                authority_raw["review_case_id"],
                authority_raw["target_fingerprint"],
                authority_raw["source"],
                authority_raw["origin"],
                authority_raw["outcome"],
                authority_raw["policy_identity"],
                authority_raw["qualification_identity"],
            )
            if set(authority_raw) != {
                "decision_id",
                "origin",
                "outcome",
                "policy_identity",
                "qualification_identity",
                "review_case_id",
                "source",
                "target_fingerprint",
            }:
                raise FieldEffectError("invalid_plan")
            consent = EffectConsent.from_data(raw["consent"])
            value = cls.create(
                connection=raw["connection"],
                library=raw["library"],
                work_id=raw["work_id"],
                destination=raw["destination"],
                pre_value=raw["pre_value"],
                desired_value=raw["desired_value"],
                target_fingerprint=raw["target_fingerprint"],
                target_version=raw["target_version"],
                dependency_digests=tuple(raw["dependency_digests"]),
                authority=authority,
                consent=consent,
                capability_digest=raw["capability_digest"],
                created_at=raw["created_at"],
            )
            if (
                value.plan_id != raw["plan_id"]
                or value.idempotency_key != raw["idempotency_key"]
            ):
                raise FieldEffectError("invalid_plan")
            return value
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("invalid_plan") from error


@dataclass(frozen=True, slots=True)
class FieldEffectOperation:
    plan: FieldEffectPlan
    state: str = "planned"
    attempt: int = 0
    updated_at: int = 0
    failure: str | None = None
    claim_token: str | None = None
    affected_review_case_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.state not in _STATES
            or type(self.attempt) is not int
            or not 0 <= self.attempt <= 3
            or type(self.updated_at) is not int
            or self.updated_at < 0
            or self.failure not in FIELD_EFFECT_FAILURES
            or (
                self.claim_token is not None
                and (
                    type(self.claim_token) is not str
                    or _HEX.fullmatch(self.claim_token) is None
                )
            )
            or type(self.affected_review_case_ids) is not tuple
            or any(type(value) is not str for value in self.affected_review_case_ids)
            or self.affected_review_case_ids
            != tuple(sorted(set(self.affected_review_case_ids)))
        ):
            raise FieldEffectError("invalid_state")
        if (
            self.state in {"claimed", "applying", "reconciling"}
            and self.claim_token is None
        ):
            raise FieldEffectError("invalid_claim")

    def transition(
        self,
        state: str,
        *,
        at: int,
        failure: str | None = None,
        claim_token: str | None = None,
    ) -> Self:
        if (
            state not in _TRANSITIONS.get(self.state, ())
            or type(at) is not int
            or at < self.updated_at
        ):
            raise FieldEffectError("invalid_transition")
        token = self.claim_token if claim_token is None else claim_token
        attempt = self.attempt + (1 if state == "claimed" else 0)
        return replace(
            self,
            state=state,
            updated_at=at,
            failure=failure,
            claim_token=token,
            attempt=attempt,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "schema_version": "field-effect-operation-v1",
            "plan": self.plan.to_data(),
            "state": self.state,
            "attempt": self.attempt,
            "updated_at": self.updated_at,
            "failure": self.failure,
            "claim_token": self.claim_token,
        }

    @classmethod
    def from_json(cls, document: str) -> Self:
        try:
            raw = json.loads(document)
            keys = {
                "attempt",
                "claim_token",
                "failure",
                "plan",
                "schema_version",
                "state",
                "updated_at",
            }
            if (
                type(raw) is not dict
                or set(raw) != keys
                or raw.get("schema_version") != "field-effect-operation-v1"
            ):
                raise FieldEffectError("invalid_operation")
            return cls(
                FieldEffectPlan.from_data(raw["plan"]),
                raw["state"],
                raw["attempt"],
                raw["updated_at"],
                raw["failure"],
                raw["claim_token"],
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("invalid_operation") from error


def authority_to_data(value: EffectAuthority) -> dict[str, object]:
    return {
        "decision_id": value.decision_id,
        "review_case_id": value.review_case_id,
        "target_fingerprint": value.target_fingerprint,
        "source": value.source,
        "origin": value.origin,
        "outcome": value.outcome,
        "policy_identity": value.policy_identity,
        "qualification_identity": value.qualification_identity,
    }
