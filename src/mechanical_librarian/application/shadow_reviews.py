"""Qualification-only shadow-review use cases."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.application.failure_diagnostics import (
    FailureDiagnostic,
    diagnostics_are_canonical,
    reviewer_diagnostic_is_admissible,
    reviewer_failure_diagnostic,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.shadow_review import (
    QUALIFICATION_MODE,
    ReviewerProfileIdentity,
    ShadowReviewIdentityError,
    ShadowReviewRequest,
    ShadowReviewResult,
    ShadowReviewValueError,
    canonical_json,
)
from mechanical_librarian.domain.review_case import ReviewAdmissionStaleError

_FAILURES = frozenset(
    {
        "runtime",
        "setup",
        "auth",
        "profile",
        "timeout",
        "cancel",
        "schema",
        "response_format",
        "malformed_output",
        "semantic_validation",
        "identity",
        "currentness",
        "persistence",
    }
)


class ShadowReviewError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        count: int = 0,
        digest: str | None = None,
        execution_provenance: ReviewerExecutionProvenance | None = None,
    ) -> None:
        if code not in _FAILURES:
            raise ValueError("invalid shadow-review failure code")
        self.code = code
        self.count = count
        self.digest = digest
        self.execution_provenance = execution_provenance
        self.diagnostic = reviewer_failure_diagnostic(code, count=count)
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CitationNormalizationMarker:
    """Aggregate-safe outcome of citation normalization at the trust boundary."""

    changed: bool
    duplicate_count: int

    def __post_init__(self) -> None:
        if (
            type(self.changed) is not bool
            or type(self.duplicate_count) is not int
            or not 0 <= self.duplicate_count <= 2_147_483_647
            or (not self.changed and self.duplicate_count != 0)
        ):
            raise ShadowReviewValueError(
                "citation normalization marker is invalid"
            )


_OBSERVED_IDENTITY_FIELDS = (
    "cost",
    "effort",
    "fallback",
    "model",
    "route",
    "tier",
    "usage",
)
_OBSERVATION_STATES = frozenset(
    {"observed", "not_exposed", "unverifiable"}
)


@dataclass(frozen=True, slots=True)
class ExecutionIdentityObservation:
    """One provider-exposed identity fact, or an explicit absence state."""

    state: str
    value: str | int | bool | None = None

    def __post_init__(self) -> None:
        if (
            self.state not in _OBSERVATION_STATES
            or (self.state == "observed") != (self.value is not None)
            or (
                self.value is not None
                and (
                    type(self.value) not in {str, int, bool}
                    or (
                        type(self.value) is str
                        and not 1 <= len(self.value.encode("utf-8")) <= 256
                    )
                    or (
                        type(self.value) is int
                        and not -(2**63) <= self.value <= 2**63 - 1
                    )
                )
            )
        ):
            raise ShadowReviewValueError(
                "execution identity observation is invalid"
            )

    def to_data(self) -> dict[str, object]:
        return {"state": self.state, "value": self.value}


@dataclass(frozen=True, slots=True)
class ReviewerExecutionProvenance:
    """Requested reviewer identity kept separate from provider observations."""

    requested: ReviewerProfileIdentity
    observed: tuple[tuple[str, ExecutionIdentityObservation], ...]
    schema_version: str = "shadow-review-execution-provenance-v1"

    def __post_init__(self) -> None:
        if (
            type(self.requested) is not ReviewerProfileIdentity
            or self.schema_version != "shadow-review-execution-provenance-v1"
            or type(self.observed) is not tuple
            or tuple(name for name, _value in self.observed)
            != _OBSERVED_IDENTITY_FIELDS
            or any(
                type(value) is not ExecutionIdentityObservation
                for _name, value in self.observed
            )
        ):
            raise ShadowReviewValueError(
                "reviewer execution provenance is invalid"
            )

    @classmethod
    def with_state(
        cls, requested: ReviewerProfileIdentity, state: str
    ) -> ReviewerExecutionProvenance:
        return cls(
            requested,
            tuple(
                (name, ExecutionIdentityObservation(state))
                for name in _OBSERVED_IDENTITY_FIELDS
            ),
        )

    def to_data(self) -> dict[str, object]:
        return {
            "observed": {
                name: value.to_data() for name, value in self.observed
            },
            "requested": self.requested.to_data(),
            "schema_version": self.schema_version,
        }

    def to_projection_data(self) -> dict[str, object]:
        return {
            "observed": {
                name: value.to_data() for name, value in self.observed
            },
            "requested": {
                "executable_sha256": self.requested.executable_sha256,
                "identity_digest": hashlib.sha256(
                    self.requested.to_json().encode("utf-8")
                ).hexdigest(),
                "mode": self.requested.mode,
                "model": self.requested.model,
                "profile_version": self.requested.profile_version,
                "reasoning": self.requested.reasoning,
                "sandbox_sha256": self.requested.sandbox_sha256,
            },
            "schema_version": self.schema_version,
        }

    @classmethod
    def validate_projection(cls, value: object) -> None:
        try:
            if (
                type(value) is not dict
                or set(value) != {"observed", "requested", "schema_version"}
                or value["schema_version"]
                != "shadow-review-execution-provenance-v1"
                or type(value["observed"]) is not dict
                or set(value["observed"]) != set(_OBSERVED_IDENTITY_FIELDS)
                or type(value["requested"]) is not dict
                or set(value["requested"])
                != {
                    "executable_sha256",
                    "identity_digest",
                    "mode",
                    "model",
                    "profile_version",
                    "reasoning",
                    "sandbox_sha256",
                }
                or value["requested"]["mode"] != QUALIFICATION_MODE
                or any(
                    type(value["requested"][name]) is not str
                    for name in value["requested"]
                )
                or any(
                    re.fullmatch(r"[0-9a-f]{64}", value["requested"][name])
                    is None
                    for name in (
                        "executable_sha256",
                        "identity_digest",
                        "sandbox_sha256",
                    )
                )
            ):
                raise ValueError
            for name in _OBSERVED_IDENTITY_FIELDS:
                observed = value["observed"][name]
                if (
                    type(observed) is not dict
                    or set(observed) != {"state", "value"}
                ):
                    raise ValueError
                ExecutionIdentityObservation(
                    observed["state"], observed["value"]
                )
        except (KeyError, TypeError, ValueError) as error:
            raise ShadowReviewValueError(
                "reviewer execution provenance projection is invalid"
            ) from error

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> ReviewerExecutionProvenance:
        try:
            raw = json.loads(serialized)
            if (
                type(raw) is not dict
                or canonical_json(raw) != serialized
                or set(raw) != {"observed", "requested", "schema_version"}
                or raw["schema_version"]
                != "shadow-review-execution-provenance-v1"
                or type(raw["observed"]) is not dict
                or set(raw["observed"]) != set(_OBSERVED_IDENTITY_FIELDS)
                or type(raw["requested"]) is not dict
            ):
                raise ValueError
            observed: list[tuple[str, ExecutionIdentityObservation]] = []
            for name in _OBSERVED_IDENTITY_FIELDS:
                value = raw["observed"][name]
                if type(value) is not dict or set(value) != {"state", "value"}:
                    raise ValueError
                observed.append(
                    (
                        name,
                        ExecutionIdentityObservation(
                            value["state"], value["value"]
                        ),
                    )
                )
            return cls(
                ReviewerProfileIdentity.from_json(
                    canonical_json(raw["requested"])
                ),
                tuple(observed),
                raw["schema_version"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ShadowReviewValueError(
                "reviewer execution provenance is invalid"
            ) from error


@dataclass(frozen=True, slots=True)
class ShadowReviewRunResult:
    """Validated advice plus non-persistent aggregate boundary diagnostics."""

    result: ShadowReviewResult
    citation_normalization: CitationNormalizationMarker
    execution_provenance: ReviewerExecutionProvenance | None = None

    def __post_init__(self) -> None:
        if (
            type(self.result) is not ShadowReviewResult
            or type(self.citation_normalization)
            is not CitationNormalizationMarker
            or (
                self.execution_provenance is not None
                and type(self.execution_provenance)
                is not ReviewerExecutionProvenance
            )
        ):
            raise ShadowReviewValueError("shadow-review run result is invalid")


class ShadowReviewRunner(Protocol):
    def run(
        self,
        request: ShadowReviewRequest,
        dispatched: Callable[[int, int, str], None],
    ) -> ShadowReviewRunResult: ...


class ShadowReviewRepository(Protocol):
    def validate_shadow_request(self, serialized: str) -> ShadowReviewRequest: ...
    def preview_shadow_request(
        self, review_case_id: str, profile_identity: str
    ) -> str: ...
    def prepare_shadow_request(
        self,
        exact_id: str,
        profile_identity: str,
        *,
        reevaluate: bool,
        claim_pid: int,
        claim_start_token: str,
        expected_prior_attempt_id: str | None,
    ) -> str: ...
    def inspect_shadow_request(
        self, review_request_id: str, profile_identity: str
    ) -> str: ...
    def record_shadow_dispatch(
        self, attempt_id: str, pid: int, pgid: int, start_token: str
    ) -> None: ...
    def record_shadow_result(
        self, attempt_id: str, result: str, execution_provenance: str
    ) -> None: ...
    def record_shadow_failure(
        self, attempt_id: str, diagnostic: str, execution_provenance: str
    ) -> None: ...
    def last_shadow_claim(
        self, review_request_id: str
    ) -> tuple[
        str,
        int,
        str,
        int | None,
        int | None,
        str | None,
    ]: ...


def preview_shadow_review(
    repository: ShadowReviewRepository,
    review_case_id: str,
    identity: ReviewerProfileIdentity,
) -> str:
    try:
        request = repository.validate_shadow_request(
            repository.preview_shadow_request(review_case_id, identity.to_json())
        )
        return canonical_json(
            {
                "mode": QUALIFICATION_MODE,
                "request": request.to_data(),
                "schema_version": "shadow-review-preview-v1",
                "status": "preview",
            }
        )
    except ShadowReviewError:
        raise
    except MetadataReportError as error:
        raise ShadowReviewError("persistence") from error
    except Exception as error:
        raise ShadowReviewError("currentness") from error


def inspect_shadow_review(
    repository: ShadowReviewRepository,
    review_request_id: str,
    identity: ReviewerProfileIdentity,
) -> str:
    try:
        return _validate_projection(
            repository,
            repository.inspect_shadow_request(
                review_request_id, identity.to_json()
            ),
            review_request_id,
        )
    except ShadowReviewError:
        raise
    except MetadataReportError as error:
        raise ShadowReviewError("persistence") from error
    except Exception as error:
        raise ShadowReviewError("runtime") from error


def execute_shadow_review(
    repository: ShadowReviewRepository,
    review_case_id: str,
    identity: ReviewerProfileIdentity,
    runner: ShadowReviewRunner,
    *,
    claimant: tuple[int, str],
    reevaluate: bool = False,
    process_alive: Callable[[int, int, str], bool] | None = None,
    claim_alive: Callable[[int, str], bool] | None = None,
) -> str:
    expected_prior_attempt_id = None
    if reevaluate:
        if process_alive is None:
            raise ShadowReviewError("runtime")
        prior = repository.last_shadow_claim(review_case_id)
        expected_prior_attempt_id = prior[0]
        dispatch = prior[3:]
        if dispatch[0] is not None and (
            dispatch[1] is None
            or dispatch[2] is None
            or process_alive(dispatch[0], dispatch[1], dispatch[2])
        ):
            raise ShadowReviewError("runtime")
        if dispatch[0] is None and (
            claim_alive is None or claim_alive(prior[1], prior[2])
        ):
            raise ShadowReviewError("runtime")
    try:
        prepared = json.loads(
            repository.prepare_shadow_request(
                review_case_id,
                identity.to_json(),
                reevaluate=reevaluate,
                claim_pid=claimant[0],
                claim_start_token=claimant[1],
                expected_prior_attempt_id=expected_prior_attempt_id,
            )
        )
        if (
            type(prepared) is not dict
            or set(prepared) != {"attempt_id", "request", "run"}
            or type(prepared["run"]) is not bool
            or type(prepared["attempt_id"]) is not str
            or type(prepared["request"]) is not dict
        ):
            raise ValueError
        request = repository.validate_shadow_request(
            canonical_json(prepared["request"])
        )
        if not prepared["run"]:
            return inspect_shadow_review(
                repository, request.review_request_id, identity
            )
        attempt_id = prepared["attempt_id"]
        dispatched = False

        def record_dispatch(pid: int, pgid: int, start_token: str) -> None:
            nonlocal dispatched
            repository.record_shadow_dispatch(
                attempt_id, pid, pgid, start_token
            )
            dispatched = True

        def record_failure(failure: ShadowReviewError) -> None:
            try:
                execution_provenance = failure.execution_provenance
                if execution_provenance is None:
                    execution_provenance = ReviewerExecutionProvenance.with_state(
                        identity, "unverifiable"
                    )
                repository.record_shadow_failure(
                    attempt_id,
                    failure.diagnostic.to_json(),
                    execution_provenance.to_json(),
                )
            except MetadataReportError as error:
                raise ShadowReviewError("persistence") from error

        try:
            run_result = runner.run(request, record_dispatch)
            if type(run_result) is not ShadowReviewRunResult:
                raise ShadowReviewValueError(
                    "shadow-review run result is invalid"
                )
            result = run_result.result
            result.validate_for(request)
            execution_provenance = run_result.execution_provenance
            if execution_provenance is None:
                execution_provenance = ReviewerExecutionProvenance.with_state(
                    identity, "unverifiable"
                )
            repository.record_shadow_result(
                attempt_id,
                result.to_json(),
                execution_provenance.to_json(),
            )
        except ShadowReviewError as error:
            record_failure(error)
            raise
        except ShadowReviewIdentityError as error:
            failure = ShadowReviewError("identity")
            record_failure(failure)
            raise failure from error
        except ShadowReviewValueError as error:
            failure = ShadowReviewError("semantic_validation")
            record_failure(failure)
            raise failure from error
        except MetadataReportError as error:
            raise ShadowReviewError("persistence") from error
        except Exception as error:
            code = "runtime" if dispatched else "setup"
            digest = hashlib.sha256(type(error).__name__.encode("utf-8")).hexdigest()
            failure = ShadowReviewError(code, digest=digest)
            record_failure(failure)
            raise failure from error
        return inspect_shadow_review(repository, request.review_request_id, identity)
    except ShadowReviewError:
        raise
    except ReviewAdmissionStaleError as error:
        raise ShadowReviewError("currentness") from error
    except MetadataReportError as error:
        raise ShadowReviewError("persistence") from error
    except ShadowReviewValueError as error:
        raise ShadowReviewError("malformed_output") from error
    except Exception as error:
        raise ShadowReviewError("runtime") from error


def _validate_projection(
    repository: ShadowReviewRepository,
    serialized: str,
    review_request_id: str,
) -> str:
    try:
        raw = json.loads(serialized)
        if (
            type(raw) is not dict
            or set(raw)
            != {
                "attempt_count",
                "current",
                "diagnostics",
                "execution_provenance",
                "mode",
                "review_case_id",
                "review_request_id",
                "schema_version",
                "status",
            }
            or raw["schema_version"] != "shadow-review-inspect-v3"
            or raw["mode"] != QUALIFICATION_MODE
            or type(raw["review_case_id"]) is not str
            or re.fullmatch(
                r"review_case_[0-9a-f]{64}", raw["review_case_id"]
            )
            is None
            or raw["review_request_id"] != review_request_id
            or type(raw["attempt_count"]) is not int
            or not 1 <= raw["attempt_count"] <= 32
            or type(raw["diagnostics"]) is not list
            or len(raw["diagnostics"]) > 20
            or type(raw["execution_provenance"]) is not list
            or len(raw["execution_provenance"]) > raw["attempt_count"]
            or type(raw["current"]) is not bool
            or raw["status"]
            not in {"pending", "unknown", "failed", "qualification_only"}
        ):
            raise ValueError
        diagnostics = tuple(
            FailureDiagnostic.from_json(canonical_json(value))
            for value in raw["diagnostics"]
        )
        for value in raw["execution_provenance"]:
            ReviewerExecutionProvenance.validate_projection(value)
        if (
            tuple(value.to_data() for value in diagnostics)
            != tuple(raw["diagnostics"])
            or not diagnostics_are_canonical(diagnostics)
            or any(
                not reviewer_diagnostic_is_admissible(value)
                for value in diagnostics
            )
            or (
                raw["status"] in {"failed", "unknown"}
                and not diagnostics
            )
        ):
            raise ValueError
        canonical = canonical_json(raw)
        if canonical != serialized:
            raise ValueError
        return canonical
    except (TypeError, ValueError, json.JSONDecodeError, ShadowReviewValueError) as error:
        raise ShadowReviewError("runtime") from error


__all__ = [
    "CitationNormalizationMarker",
    "ShadowReviewError",
    "ShadowReviewRepository",
    "ShadowReviewRunResult",
    "ShadowReviewRunner",
    "execute_shadow_review",
    "inspect_shadow_review",
    "preview_shadow_review",
]
