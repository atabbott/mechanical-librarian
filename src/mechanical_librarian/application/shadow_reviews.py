"""Qualification-only shadow-review use cases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.domain.shadow_review import (
    QUALIFICATION_MODE,
    ReviewerProfileIdentity,
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
        "currentness",
    }
)


class ShadowReviewError(RuntimeError):
    def __init__(
        self, code: str, *, count: int = 0, digest: str | None = None
    ) -> None:
        if code not in _FAILURES:
            raise ValueError("invalid shadow-review failure code")
        self.code = code
        self.count = count
        self.digest = digest
        super().__init__(code)


class ShadowReviewRunner(Protocol):
    def run(
        self,
        request: ShadowReviewRequest,
        dispatched: Callable[[int, int, str], None],
    ) -> ShadowReviewResult: ...


class ShadowReviewRepository(Protocol):
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
    def record_shadow_result(self, attempt_id: str, result: str) -> None: ...
    def record_shadow_failure(
        self, attempt_id: str, code: str, count: int, digest: str | None
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
        request = ShadowReviewRequest.from_json(
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
    except Exception as error:
        raise ShadowReviewError("currentness") from error


def inspect_shadow_review(
    repository: ShadowReviewRepository,
    review_request_id: str,
    identity: ReviewerProfileIdentity,
) -> str:
    try:
        return _validate_projection(
            repository.inspect_shadow_request(
                review_request_id, identity.to_json()
            ),
            review_request_id,
        )
    except ShadowReviewError:
        raise
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
        request = ShadowReviewRequest.from_json(canonical_json(prepared["request"]))
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

        try:
            result = runner.run(request, record_dispatch)
            result.validate_for(request)
            repository.record_shadow_result(attempt_id, result.to_json())
        except ShadowReviewError as error:
            repository.record_shadow_failure(
                attempt_id, error.code, error.count, error.digest
            )
            raise
        except Exception as error:
            code = "runtime" if dispatched else "setup"
            digest = hashlib.sha256(type(error).__name__.encode("utf-8")).hexdigest()
            repository.record_shadow_failure(attempt_id, code, 0, digest)
            raise ShadowReviewError(code, digest=digest) from error
        return inspect_shadow_review(repository, request.review_request_id, identity)
    except ShadowReviewError:
        raise
    except ReviewAdmissionStaleError as error:
        raise ShadowReviewError("currentness") from error
    except ShadowReviewValueError as error:
        raise ShadowReviewError("malformed_output") from error
    except Exception as error:
        raise ShadowReviewError("runtime") from error


def _validate_projection(serialized: str, review_request_id: str) -> str:
    try:
        raw = json.loads(serialized)
        if (
            type(raw) is not dict
            or set(raw)
            != {
                "attempts",
                "current",
                "mode",
                "request",
                "review_request_id",
                "schema_version",
                "status",
            }
            or raw["schema_version"] != "shadow-review-inspect-v1"
            or raw["mode"] != QUALIFICATION_MODE
            or raw["review_request_id"] != review_request_id
            or type(raw["attempts"]) is not list
            or type(raw["current"]) is not bool
            or raw["status"]
            not in {"pending", "unknown", "failed", "qualification_only"}
        ):
            raise ValueError
        request = ShadowReviewRequest.from_json(canonical_json(raw["request"]))
        if request.review_request_id != review_request_id:
            raise ValueError
        canonical = canonical_json(raw)
        if canonical != serialized:
            raise ValueError
        return canonical
    except (TypeError, ValueError, json.JSONDecodeError, ShadowReviewValueError) as error:
        raise ShadowReviewError("runtime") from error


__all__ = [
    "ShadowReviewError",
    "ShadowReviewRepository",
    "ShadowReviewRunner",
    "execute_shadow_review",
    "inspect_shadow_review",
    "preview_shadow_review",
]
