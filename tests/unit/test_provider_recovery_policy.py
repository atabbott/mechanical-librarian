from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from zoneinfo import ZoneInfo

from mechanical_librarian.application.provider_recovery_policy import (
    provider_recovery_decision,
)
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    ProviderObservation,
    ProviderStatus,
)


def _limited(
    authority: str | None, *categories: FailureCategory
) -> ProviderObservation:
    return ProviderObservation(
        "catalogue",
        ProviderStatus.RATE_LIMITED,
        authority=authority,
        diagnostics=tuple(
            FailureDiagnostic(
                category,
                FailureComponent.PROVIDER,
                FailureStage.RESPONSE,
            )
            for category in categories
        ),
    )


def test_burst_classification_uses_provider_specific_windows() -> None:
    assert ("burst_throttled", 100) == provider_recovery_decision(
        _limited("google_books", FailureCategory.BURST_THROTTLE), now=0
    )
    assert ("burst_throttled", 60) == provider_recovery_decision(
        _limited("hardcover", FailureCategory.BURST_THROTTLE), now=0
    )


def test_google_daily_reset_tracks_pacific_dst_days() -> None:
    pacific = ZoneInfo("America/Los_Angeles")
    spring = int(datetime(2026, 3, 8, tzinfo=pacific).timestamp())
    autumn = int(datetime(2026, 11, 1, tzinfo=pacific).timestamp())
    assert ("quota_exhausted", 82_800) == provider_recovery_decision(
        _limited("google_books", FailureCategory.DAILY_QUOTA_EXHAUSTED),
        now=spring,
    )
    assert ("quota_exhausted", 90_000) == provider_recovery_decision(
        _limited("google_books", FailureCategory.DAILY_QUOTA_EXHAUSTED),
        now=autumn,
    )


def test_hardcover_daily_reset_tracks_midnight_utc() -> None:
    noon = int(datetime(2026, 8, 27, 12, tzinfo=timezone.utc).timestamp())
    assert ("quota_exhausted", 43_200) == provider_recovery_decision(
        _limited("hardcover", FailureCategory.DAILY_QUOTA_EXHAUSTED), now=noon
    )


def test_conflicting_or_unknown_specific_evidence_stays_ambiguous() -> None:
    assert ("rate_limited", None) == provider_recovery_decision(
        _limited(
            "google_books",
            FailureCategory.BURST_THROTTLE,
            FailureCategory.DAILY_QUOTA_EXHAUSTED,
        ),
        now=0,
    )
    assert ("rate_limited", None) == provider_recovery_decision(
        _limited(None, FailureCategory.BURST_THROTTLE), now=0
    )


def test_specific_and_generic_rate_limit_evidence_stays_ambiguous_across_scopes() -> (
    None
):
    specific = FailureDiagnostic(
        FailureCategory.BURST_THROTTLE,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    )
    generic = FailureDiagnostic(
        FailureCategory.QUOTA_RATE_LIMIT,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    )
    for provider_diagnostic, lane_diagnostic in (
        (specific, generic),
        (generic, specific),
    ):
        observation = cast(
            "ProviderObservation",
            SimpleNamespace(
                status=ProviderStatus.RATE_LIMITED,
                authority="google_books",
                diagnostics=(provider_diagnostic,),
                lanes=(
                    SimpleNamespace(
                        status=ProviderStatus.RATE_LIMITED,
                        diagnostics=(lane_diagnostic,),
                    ),
                ),
            ),
        )
        assert ("rate_limited", None) == provider_recovery_decision(observation, now=0)
