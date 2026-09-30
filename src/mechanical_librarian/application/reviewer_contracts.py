"""Engine-neutral reviewer access contracts owned above provider adapters."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.application.cover_observation import Reviewer
from mechanical_librarian.domain.reviewer_access import (
    OpenRouterTaskSelection,
    ReviewerEngine,
)

DeviceAuthStatus = Literal[
    "ready",
    "configured",
    "starting",
    "issued",
    "waiting",
    "validating",
    "usable",
    "expired",
    "cancelled",
    "failed",
    "interrupted",
]
DeviceAuthFailure = Literal[
    "permission_or_profile",
    "permission_or_process",
    "protocol_or_process",
    "restart_interrupted",
]
ReviewerProvisioningReason = Literal[
    "profile_not_provisioned", "profile_unreadable", "profile_contract_mismatch"
]


@dataclass(frozen=True, slots=True)
class ReviewerProvisioningStatus:
    available: bool
    reason: ReviewerProvisioningReason | None
    profile_sha256: str | None = None
    model: str | None = None


ReviewerStatus = Literal[
    "not_provisioned",
    "ready",
    "starting",
    "issued",
    "waiting",
    "validating",
    "usable",
    "expired",
    "cancelled",
    "failed",
    "interrupted",
    "host_unsupported",
    "unconfigured",
    "configured",
    "invalid_key",
    "quota_exhausted",
    "rate_limited",
    "model_unavailable",
    "model_text_only",
    "malformed_response",
    "unreachable",
]
ReviewerStatusReason = Literal[
    "profile_not_provisioned",
    "profile_unreadable",
    "profile_contract_mismatch",
    "host_evidence_unavailable",
    "cgroup_limit_below_1_gib",
    "headroom_below_1_gib",
    "permission_or_profile",
    "permission_or_process",
    "protocol_or_process",
    "restart_interrupted",
    "not_selected",
    "invalid_key",
    "quota_exhausted",
    "rate_limited",
    "model_unavailable",
    "model_text_only",
    "malformed_response",
    "unreachable",
]
OpenRouterFailure = Literal[
    "invalid_key",
    "quota_exhausted",
    "rate_limited",
    "model_unavailable",
    "model_text_only",
    "malformed_response",
    "unreachable",
    "transport",
    "profile_mismatch",
    "malformed_request",
]
_LOCAL_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OpenRouterReviewerError(RuntimeError):
    """One value-free provider failure identity."""

    def __init__(self, code: OpenRouterFailure) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CodexDeviceAuthState:
    state: DeviceAuthStatus
    verification_url: str | None = None
    user_code: str | None = None
    expires_in_seconds: int | None = None
    failure: DeviceAuthFailure | None = None


@dataclass(frozen=True, slots=True)
class OpenRouterAccountStatus:
    tier: Literal["free", "paid"]
    model: str
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    structured_outputs: Literal[True]
    observed_at: int | None = None
    prompt_price: str | None = None
    completion_price: str | None = None
    request_price: str | None = None
    image_price: str | None = None


def openrouter_profile(model: str) -> bytes:
    selection = OpenRouterTaskSelection(engine="openrouter", model=model)
    return canonical_json(
        {
            "engine": "openrouter",
            "model": selection.model,
            "schema_version": "openrouter-reviewer-profile-v1",
        }
    ).encode("utf-8")


def openrouter_cover_profile(model: str) -> bytes:
    """Bind cover advice to its exact model and shipped request posture."""
    selection = OpenRouterTaskSelection(engine="openrouter", model=model)
    return canonical_json(
        {
            "engine": "openrouter",
            "model": selection.model,
            "schema_version": "openrouter-cover-reviewer-profile-v1",
            "max_tokens": 8192,
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "reasoning": "minimal-exclude-when-supported",
        }
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ReviewerDispatch:
    access_revision: int
    engine: ReviewerEngine
    model: str
    profile: bytes
    runner: Reviewer
    close: Callable[[], None] = lambda: None

    def __post_init__(self) -> None:
        if type(self.access_revision) is not int or self.access_revision < 1:
            raise ValueError("invalid reviewer access revision")
        if self.engine == "openrouter":
            OpenRouterTaskSelection(engine="openrouter", model=self.model)
        elif not _LOCAL_MODEL.fullmatch(self.model):
            raise ValueError("invalid local reviewer model")
        if type(self.profile) is not bytes or not self.profile:
            raise ValueError("invalid reviewer profile")


__all__ = [
    "CodexDeviceAuthState",
    "DeviceAuthFailure",
    "DeviceAuthStatus",
    "OpenRouterAccountStatus",
    "OpenRouterFailure",
    "OpenRouterReviewerError",
    "ReviewerDispatch",
    "ReviewerStatus",
    "ReviewerStatusReason",
    "openrouter_profile",
    "openrouter_cover_profile",
]
