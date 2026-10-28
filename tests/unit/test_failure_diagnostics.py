from __future__ import annotations

from dataclasses import dataclass

import pytest

from mechanical_librarian.application.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    provider_attempt_diagnostics,
    provider_result_diagnostics,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderOperationalEvidence,
)
from mechanical_librarian.domain.metadata_report import (
    _PROVIDER_DIAGNOSTIC_STAGES,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    work_ladder_diagnostics,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleTransformationFamily,
)


@dataclass(frozen=True)
class _Attempt:
    category: str
    outcome: str
    private_body: str = "credential=/private/library/secret"


class _MalformedAttempt:
    @property
    def category(self):
        raise RuntimeError("credential=/private/library/secret")

    @property
    def outcome(self):
        raise RuntimeError("credential=/private/library/secret")


@dataclass(frozen=True)
class _IncompleteCause:
    cause: object
    private_query: str = "credential=/private/library/secret"


_VALID_DOCUMENT = FailureDiagnostic(
    FailureCategory.TRANSPORT,
    FailureComponent.PROVIDER,
    FailureStage.REQUEST,
).to_json()

# The signal name each production mapping is reached through, expressed as the
# attempt category and provider status that produce it.
_PROVIDER_SIGNALS: dict[str, tuple[str, str | None]] = {
    "provider.lookup_limit": ("none", "query_limit_exceeded"),
    "provider.graphql_errors": ("provider_declared_error", None),
    "provider.http": ("http_503", None),
    "provider.quota": ("http_429", None),
    "provider.no_match": ("none", "no_match"),
    "provider.response_malformed": ("response_malformed", None),
    "provider.response_oversized": ("response_oversized", None),
    "provider.candidate_limit": ("candidate_limit_exceeded", None),
    "provider.not_found": ("not_found", None),
    "provider.transport": ("transport_connect_error", None),
    "provider.deadline": ("deadline", None),
    "provider.cancellation": ("cancellation", None),
}

_EXPECTED_PROVIDER_MAPPINGS: dict[
    str, tuple[FailureCategory, FailureComponent, FailureStage]
] = {
    "provider.lookup_limit": (
        FailureCategory.LOOKUP_CONSTRUCTION,
        FailureComponent.PROVIDER,
        FailureStage.LOOKUP,
    ),
    "provider.graphql_errors": (
        FailureCategory.PROVIDER_DECLARED_ERROR,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.http": (
        FailureCategory.HTTP_NON_SUCCESS,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.quota": (
        FailureCategory.QUOTA_RATE_LIMIT,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.no_match": (
        FailureCategory.EMPTY_NO_MATCH,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.response_malformed": (
        FailureCategory.MALFORMED_RESPONSE,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.response_oversized": (
        FailureCategory.OVERSIZED_RESPONSE,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.candidate_limit": (
        FailureCategory.CANDIDATE_LIMIT_EXCEEDED,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.not_found": (
        FailureCategory.EMPTY_NO_MATCH,
        FailureComponent.PROVIDER,
        FailureStage.RESPONSE,
    ),
    "provider.transport": (
        FailureCategory.TRANSPORT,
        FailureComponent.PROVIDER,
        FailureStage.REQUEST,
    ),
    "provider.deadline": (
        FailureCategory.TIMEOUT,
        FailureComponent.PROVIDER,
        FailureStage.REQUEST,
    ),
    "provider.cancellation": (
        FailureCategory.CANCELLATION,
        FailureComponent.PROVIDER,
        FailureStage.EXECUTION,
    ),
}


def test_category_vocabulary_is_exact_and_closed() -> None:
    assert {
        "authentication",
        "burst_throttle",
        "candidate_limit_exceeded",
        "cancellation",
        "citation_membership",
        "currentness",
        "daily_quota_exhausted",
        "empty_no_match",
        "http_non_success",
        "identity",
        "live_schema_rejection",
        "lookup_construction",
        "malformed_output",
        "malformed_response",
        "oversized_response",
        "persistence",
        "provider_declared_error",
        "quota_rate_limit",
        "schema_construction",
        "setup_configuration",
        "spawn_runtime",
        "timeout",
        "transport",
        "unknown",
        "work_identity_refused",
    } == {value.value for value in FailureCategory}


def test_specific_rate_limit_evidence_does_not_gain_a_generic_duplicate() -> None:
    diagnostics = provider_attempt_diagnostics(
        (_Attempt("burst_throttled", "terminal"),), status="rate_limited"
    )
    assert [FailureCategory.BURST_THROTTLE] == [
        diagnostic.category for diagnostic in diagnostics
    ]


def test_closed_diagnostic_round_trips_only_bounded_fields() -> None:
    diagnostic = FailureDiagnostic(
        FailureCategory.TRANSPORT,
        FailureComponent.PROVIDER,
        FailureStage.REQUEST,
        retryable=True,
        count=2,
    )

    serialized = diagnostic.to_json()

    assert diagnostic == FailureDiagnostic.from_json(serialized)
    assert {
        "category",
        "component",
        "count",
        "current",
        "retryable",
        "stage",
    } == set(diagnostic.to_data())
    for forbidden in ("credential", "private", "secret", "body", "path"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "serialized",
    (
        _VALID_DOCUMENT.replace('"transport"', '"new_failure"'),
        _VALID_DOCUMENT[:-1] + ',"category":"unknown"}',
        _VALID_DOCUMENT + "\n",
        '{"category":"unknown"}',
        _VALID_DOCUMENT.replace('"retryable":null', '"retryable":1'),
    ),
    ids=(
        "unknown_category",
        "duplicate_key",
        "trailing_newline",
        "absent_fields",
        "noncanonical_retryable",
    ),
)
def test_document_rejects_unknown_duplicate_and_noncanonical_fields(
    serialized: str,
) -> None:
    with pytest.raises(ValueError):
        FailureDiagnostic.from_json(serialized)


def test_provider_attempts_are_redacted_and_stably_aggregated() -> None:
    diagnostics = provider_attempt_diagnostics(
        (
            _Attempt("http_503", "retry"),
            _Attempt("none", "response_received"),
            _Attempt("http_503", "exhausted"),
            _Attempt("response_malformed", "terminal"),
            _Attempt("provider_declared_error", "terminal"),
            _Attempt("transport_connect_error", "retry"),
        )
    )

    assert (
        FailureCategory.HTTP_NON_SUCCESS,
        FailureCategory.MALFORMED_RESPONSE,
        FailureCategory.PROVIDER_DECLARED_ERROR,
        FailureCategory.TRANSPORT,
    ) == tuple(value.category for value in diagnostics)
    assert (2, 1, 1, 1) == tuple(value.count for value in diagnostics)
    serialized = "".join(value.to_json() for value in diagnostics)
    for forbidden in ("credential", "private", "secret", "body", "ordinal"):
        assert forbidden not in serialized


def test_malformed_optional_attempt_records_fail_closed_to_unknown() -> None:
    diagnostics = provider_attempt_diagnostics(
        (
            object(),
            _MalformedAttempt(),
            _Attempt("http_503", "terminal"),
        )
    )

    assert (
        FailureCategory.HTTP_NON_SUCCESS,
        FailureCategory.UNKNOWN,
    ) == tuple(value.category for value in diagnostics)
    assert (1, 2) == tuple(value.count for value in diagnostics)
    malformed_collection = provider_attempt_diagnostics(
        [_Attempt("http_503", "terminal")]
    )
    assert 1 == len(malformed_collection)
    assert FailureCategory.UNKNOWN == malformed_collection[0].category
    assert "credential" not in "".join(value.to_json() for value in diagnostics)


def test_incomplete_causes_are_closed_truthful_and_redacted() -> None:
    diagnostics = provider_attempt_diagnostics(
        (),
        status="incomplete",
        incomplete_causes=(
            _IncompleteCause("deadline"),
            _IncompleteCause("aggregate_bytes"),
            _IncompleteCause("dispatch_count"),
        ),
    )

    assert (
        FailureCategory.LOOKUP_CONSTRUCTION,
        FailureCategory.OVERSIZED_RESPONSE,
        FailureCategory.TIMEOUT,
    ) == tuple(value.category for value in diagnostics)
    assert FailureCategory.TIMEOUT not in tuple(
        value.category for value in diagnostics[:2]
    )
    serialized = "".join(value.to_json() for value in diagnostics)
    for forbidden in ("credential", "private", "secret", "query", "path"):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "causes",
    (
        (),
        (_IncompleteCause("credential=/private/library/secret"),),
        [_IncompleteCause("deadline")],
    ),
    ids=("absent", "unrecognised_cause", "unbounded_collection"),
)
def test_incomplete_causes_fail_closed_when_absent_or_malformed(
    causes: object,
) -> None:
    diagnostics = provider_attempt_diagnostics(
        (), status="incomplete", incomplete_causes=causes
    )
    assert (FailureCategory.UNKNOWN,) == tuple(value.category for value in diagnostics)


def test_sole_identity_conflict_reserves_the_malformed_response_category() -> None:
    """An identity conflict is not a malformed response."""
    suppressed = provider_attempt_diagnostics(
        (), status="malformed", identity_conflict_only=True
    )
    assert () == suppressed

    unobserved = provider_attempt_diagnostics((), status="malformed")
    assert (FailureCategory.MALFORMED_RESPONSE,) == tuple(
        value.category for value in unobserved
    )

    oversized = provider_attempt_diagnostics(
        (), status="oversized", identity_conflict_only=True
    )
    assert (FailureCategory.OVERSIZED_RESPONSE,) == tuple(
        value.category for value in oversized
    )


@pytest.mark.parametrize(
    ("status", "category"),
    (
        ("malformed", FailureCategory.MALFORMED_RESPONSE),
        ("oversized", FailureCategory.OVERSIZED_RESPONSE),
        ("candidate_limit_exceeded", FailureCategory.CANDIDATE_LIMIT_EXCEEDED),
    ),
)
def test_each_over_bound_status_names_its_own_category(
    status: str, category: FailureCategory
) -> None:
    """The three statuses #1294's run found under one name each keep their own (#1308)."""
    assert ((category, FailureStage.RESPONSE, False),) == tuple(
        (value.category, value.stage, value.retryable)
        for value in provider_attempt_diagnostics((), status=status)
    )


def test_a_not_found_attempt_is_a_miss_rather_than_an_http_failure() -> None:
    """An adapter's own `not_found` joins the no-match it answers, never twice."""
    diagnostics = provider_attempt_diagnostics(
        (_Attempt("not_found", "terminal"),), status="no_match"
    )
    assert ((FailureCategory.EMPTY_NO_MATCH, 1),) == tuple(
        (value.category, value.count) for value in diagnostics
    )


_LOCAL_REFERENCE = "local_" + "e" * 64


def _failed_lane(
    suffix: str,
    diagnostics: tuple[FailureDiagnostic, ...],
    status: ProviderStatus = ProviderStatus.MALFORMED,
) -> ProviderLaneObservation:
    return ProviderLaneObservation(
        "lane_" + suffix * 64,
        ProviderQueryKind.WORK,
        status,
        (_LOCAL_REFERENCE,),
        None,
        (),
        diagnostics,
    )


def _over_bound_observation(
    lanes: tuple[ProviderLaneObservation, ...],
    diagnostics: tuple[FailureDiagnostic, ...],
) -> ProviderObservation:
    return ProviderObservation(
        "hardcover",
        ProviderStatus.MALFORMED,
        (_LOCAL_REFERENCE,),
        (),
        ProviderQueryKind.WORK if len(lanes) == 1 else ProviderQueryKind.DISCOVERY,
        None,
        lanes,
        (),
        None,
        diagnostics,
    )


_IDENTITY_DIAGNOSTIC = FailureDiagnostic(
    FailureCategory.IDENTITY,
    FailureComponent.PROVIDER,
    FailureStage.IDENTITY,
    retryable=False,
)
_MALFORMED_DIAGNOSTIC = FailureDiagnostic(
    FailureCategory.MALFORMED_RESPONSE,
    FailureComponent.PROVIDER,
    FailureStage.RESPONSE,
    retryable=False,
)
_IDENTITY_LANE = _failed_lane("a", (_IDENTITY_DIAGNOSTIC,))
_INDEPENDENT_LANE = _failed_lane("b", (_MALFORMED_DIAGNOSTIC,))
# A candidate-limited lane names its own category on the lane, so it neither
# needs nor defeats the suppression of the observation's malformed aggregate.
_OVER_BOUND_LANE = _failed_lane("c", (), ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)


@pytest.mark.parametrize(
    ("lanes", "diagnostics", "expected"),
    (
        pytest.param(
            (_IDENTITY_LANE,),
            (_IDENTITY_DIAGNOSTIC,),
            (FailureCategory.IDENTITY,),
            id="conflict_only",
        ),
        pytest.param(
            (_IDENTITY_LANE, _INDEPENDENT_LANE),
            (_IDENTITY_DIAGNOSTIC,),
            (
                FailureCategory.IDENTITY,
                FailureCategory.MALFORMED_RESPONSE,
            ),
            id="conflict_with_independent_malformed_lane",
        ),
        pytest.param(
            (_IDENTITY_LANE, _OVER_BOUND_LANE),
            (_IDENTITY_DIAGNOSTIC,),
            (FailureCategory.IDENTITY,),
            id="conflict_beside_candidate_limited_lane",
        ),
        pytest.param(
            (_INDEPENDENT_LANE,),
            (),
            (FailureCategory.MALFORMED_RESPONSE,),
            id="malformed_only",
        ),
    ),
)
def test_independent_malformed_lane_keeps_the_response_category(
    lanes: tuple[ProviderLaneObservation, ...],
    diagnostics: tuple[FailureDiagnostic, ...],
    expected: tuple[FailureCategory, ...],
) -> None:
    """Suppression needs every malformed lane to state the identity conflict."""
    observed = provider_result_diagnostics(
        _over_bound_observation(lanes, diagnostics), ProviderOperationalEvidence()
    )
    assert expected == tuple(value.category for value in observed)


def test_only_the_malformed_status_yields_the_malformed_category() -> None:
    """The identity suppression reads malformed lanes only, so no other status may
    name the category it suppresses."""
    mapped = frozenset(
        status
        for status in ProviderStatus
        if any(
            value.category is FailureCategory.MALFORMED_RESPONSE
            for value in provider_attempt_diagnostics((), status=status.value)
        )
    )
    assert {ProviderStatus.MALFORMED} == mapped


def test_provider_signal_mappings_cover_every_declared_signal() -> None:
    assert set(_EXPECTED_PROVIDER_MAPPINGS) == set(_PROVIDER_SIGNALS)


@pytest.mark.parametrize("signal", tuple(_PROVIDER_SIGNALS))
def test_provider_signals_have_exact_production_mappings(signal: str) -> None:
    category, status = _PROVIDER_SIGNALS[signal]
    values = provider_attempt_diagnostics(
        (_Attempt(category, "terminal"),),
        status=status,
    )
    assert 1 == len(values)
    diagnostic = values[0]
    assert _EXPECTED_PROVIDER_MAPPINGS[signal] == (
        diagnostic.category,
        diagnostic.component,
        diagnostic.stage,
    )


def test_work_identity_refusal_is_a_closed_identity_stage_provider_category() -> None:
    assert "work_identity_refused" == FailureCategory.WORK_IDENTITY_REFUSED.value
    assert (
        FailureStage.IDENTITY
        is _PROVIDER_DIAGNOSTIC_STAGES[FailureCategory.WORK_IDENTITY_REFUSED]
    )
    assert (
        FailureStage.RESPONSE
        is _PROVIDER_DIAGNOSTIC_STAGES[FailureCategory.EMPTY_NO_MATCH]
    )
    assert (
        FailureDiagnostic(
            FailureCategory.WORK_IDENTITY_REFUSED,
            FailureComponent.PROVIDER,
            FailureStage.IDENTITY,
            retryable=False,
            count=4,
        ),
    ) == work_ladder_diagnostics(0, 4)


_LADDER = (
    WorkDispatchLineage(WorkDispatchRung.NORMALIZED),
    WorkDispatchLineage(
        WorkDispatchRung.SUBTITLE_STRIPPED,
        (WorkTitleTransformationFamily.SUBTITLE_SEGMENT,),
    ),
)


def _no_match_observation(
    empty_rungs: int, refused_products: int, *, exhausted: bool = True
) -> ProviderObservation:
    lane = ProviderLaneObservation(
        "lane_" + "d" * 64,
        ProviderQueryKind.WORK,
        ProviderStatus.NO_MATCH,
        (_LOCAL_REFERENCE,),
        None,
        (),
        work_ladder_diagnostics(empty_rungs, refused_products) if exhausted else (),
        exhausted_dispatch=_LADDER if exhausted else (),
    )
    return ProviderObservation(
        "audible",
        ProviderStatus.NO_MATCH,
        (_LOCAL_REFERENCE,),
        (),
        ProviderQueryKind.WORK,
        "audible_catalog",
        (lane,),
    )


@pytest.mark.parametrize(
    ("observation", "expected"),
    (
        pytest.param(
            _no_match_observation(0, 3),
            ((FailureCategory.WORK_IDENTITY_REFUSED, FailureStage.IDENTITY, 3),),
            id="wholly_refused",
        ),
        pytest.param(
            _no_match_observation(1, 2),
            (
                (FailureCategory.EMPTY_NO_MATCH, FailureStage.RESPONSE, 1),
                (FailureCategory.WORK_IDENTITY_REFUSED, FailureStage.IDENTITY, 2),
            ),
            id="refused_and_empty",
        ),
        pytest.param(
            _no_match_observation(2, 0),
            ((FailureCategory.EMPTY_NO_MATCH, FailureStage.RESPONSE, 1),),
            id="empty_ladder",
        ),
        pytest.param(
            _no_match_observation(0, 0, exhausted=False),
            ((FailureCategory.EMPTY_NO_MATCH, FailureStage.RESPONSE, 1),),
            id="unwalked_no_match",
        ),
    ),
)
def test_refused_products_are_lifted_once_and_never_called_empty(
    observation: ProviderObservation,
    expected: tuple[tuple[FailureCategory, FailureStage, int], ...],
) -> None:
    """Only a wholly refused no-match loses the status-derived empty aggregate."""
    observed = provider_result_diagnostics(observation, ProviderOperationalEvidence())
    assert expected == tuple(
        (value.category, value.stage, value.count) for value in observed
    )
    assert all(value.retryable is False for value in observed)


def test_a_refusal_never_suppresses_an_independent_no_match_lane() -> None:
    refused = _no_match_observation(0, 3).lanes[0]
    unwalked = ProviderLaneObservation(
        "lane_" + "e" * 64,
        ProviderQueryKind.ISBN,
        ProviderStatus.NO_MATCH,
        (_LOCAL_REFERENCE,),
        "9780000000002",
    )
    observed = provider_result_diagnostics(
        ProviderObservation(
            "audible",
            ProviderStatus.NO_MATCH,
            (_LOCAL_REFERENCE,),
            (),
            ProviderQueryKind.DISCOVERY,
            "audible_catalog",
            (refused, unwalked),
        ),
        ProviderOperationalEvidence(),
    )
    assert (
        FailureCategory.EMPTY_NO_MATCH,
        FailureCategory.WORK_IDENTITY_REFUSED,
    ) == tuple(value.category for value in observed)


def test_status_derived_empty_no_match_is_suppressed_only_on_request() -> None:
    assert () == provider_attempt_diagnostics(
        (), status="no_match", work_identity_refused_only=True
    )
    assert (FailureCategory.EMPTY_NO_MATCH,) == tuple(
        value.category for value in provider_attempt_diagnostics((), status="no_match")
    )
    assert (FailureCategory.MALFORMED_RESPONSE,) == tuple(
        value.category
        for value in provider_attempt_diagnostics(
            (), status="malformed", work_identity_refused_only=True
        )
    )


def test_provider_events_carry_no_title_url_credential_path_or_item(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every key is allow-listed and every value closed, so nothing leaks (#1299)."""
    import json

    from mechanical_librarian.application.failure_diagnostics import (
        emit_provider_events,
        provider_dispatch_events,
        provider_item_ref,
    )
    from mechanical_librarian.application.metadata_report import (
        MetadataDiscoveryLane,
    )
    from mechanical_librarian.application.provider_evidence import (
        ProviderAttemptRecord,
    )

    url = "https://x.invalid/?q=secret&key=TOKEN"
    lane = MetadataDiscoveryLane(
        "lane_" + "a" * 64,
        ProviderQueryKind.WORK,
        (),
        title="Secret Title",
        authors=("Private Author",),
    )
    emit_provider_events(
        provider_dispatch_events,
        adapter="hardcover",
        provider="Private Provider",
        item_ref=provider_item_ref("primary", "books", "/media/private/book.m4b"),
        phase="discovery",
        lanes=(lane,),
        observation=None,
        evidence=ProviderOperationalEvidence(
            attempt_records=(
                ProviderAttemptRecord("catalogue", 1, url, None, 0.1, "Bearer TOKEN"),
            )
        ),
        audio=False,
        elapsed_seconds=0.25,
        deadline_seconds=20.0,
        deadline_kind="provider_share",
    )
    emit_provider_events(
        lambda: (
            {
                "event": "provider_request_failed",
                "item_ref": "/media/private/book.m4b",
                "outcome_class": url,
                "title": "Secret Title",
                "message": "Bearer TOKEN",
            },
        )
    )

    def raises() -> tuple[dict[str, object], ...]:
        raise RuntimeError(f"{url} Bearer TOKEN /media/private/book.m4b")

    emit_provider_events(raises)

    written = capsys.readouterr().err
    for leaked in (
        "Secret Title",
        "Private Author",
        "x.invalid",
        "secret",
        "TOKEN",
        "Bearer",
        "/media/private",
        "book.m4b",
        "Private Provider",
    ):
        assert leaked not in written
    dispatched, injected = (json.loads(line) for line in written.splitlines())
    assert dispatched["provider"] is None
    assert dispatched["attempt_outcome"] is None
    assert dispatched["outcome_class"] == "unknown"
    assert dispatched["item_ref"].startswith("item-")
    assert {"lane_kind": "work", "query_family": "asserted"}.items() <= (
        dispatched.items()
    )
    assert {
        "event": "provider_request_failed",
        "item_ref": None,
        "outcome_class": None,
    } == injected
    assert all(len(line) + 1 <= 1024 for line in written.splitlines())


def test_provider_events_need_no_environment_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Events are on by default: nothing in the environment enables them."""
    import os

    from mechanical_librarian.application.failure_diagnostics import (
        emit_provider_events,
    )

    for name in tuple(os.environ):
        monkeypatch.delenv(name)

    emit_provider_events(
        lambda: ({"event": "provider_throttled", "outcome_class": "held"},)
    )

    assert (
        '{"event":"provider_throttled","outcome_class":"held"}\n'
        == capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "seconds", (1e308, float("inf"), float("nan"), 10**400, 86_400.001, -1e308)
)
def test_an_unbounded_duration_names_no_milliseconds_and_keeps_the_event(
    seconds: float,
) -> None:
    """A duration past the day bound is null, never an overflow (#1299)."""
    from mechanical_librarian.application.failure_diagnostics import (
        provider_dispatch_events,
    )
    from mechanical_librarian.application.provider_evidence import (
        ProviderAttemptRecord,
    )

    (event,) = provider_dispatch_events(
        adapter="hardcover",
        provider="catalogue",
        item_ref="item-" + "0" * 16,
        phase="discovery",
        lanes=(),
        observation=None,
        evidence=ProviderOperationalEvidence(
            attempt_records=(
                ProviderAttemptRecord(
                    "catalogue", 1, "http_500", None, 0.1, "terminal"
                ),
            )
        ),
        audio=False,
        elapsed_seconds=seconds,
        deadline_seconds=86_400,
        deadline_kind="provider_share",
    )
    assert (None, 86_400_000) == (event["elapsed_ms"], event["deadline_ms"])
