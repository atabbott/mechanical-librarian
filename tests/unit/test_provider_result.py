"""The application envelope survives real adapter dispatch and cleanup failures."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import MethodType

import httpx
import pytest

from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AudibleCatalogMetadataProvider,
)
from mechanical_librarian.adapters.providers.audiosilo_metadata import (
    AudioSiloMetadataProvider,
)
from mechanical_librarian.adapters.providers.audnexus_metadata import (
    AudnexusMetadataProvider,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.inventaire_metadata import (
    InventaireMetadataProvider,
)
from mechanical_librarian.adapters.providers.itunes_search_metadata import (
    ITunesSearchMetadataProvider,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
)
from mechanical_librarian.adapters.providers.retry_safety import ProviderRequestPacer
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    ProviderCoverDescriptor,
    ProviderFailureSnapshot,
    ProviderObservationCancelled,
    ProviderObservationFailure,
    ProviderObservationInterrupted,
    ProviderResult,
    TargetSnapshot,
    metadata_discovery_plan,
    observe_provider,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderAttemptRecord,
    ProviderOperationalEvidence,
)
from mechanical_librarian.application.provider_retry_successor import (
    GenerateProviderRetrySuccessor,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceKind
from mechanical_librarian.domain.metadata_report import (
    ProviderObservation,
    ProviderStatus,
)
from tests.support.audiobook_providers import asin_plan, work_plan
from tests.support.hardcover_evidence import isbn_claim

_PROVIDERS = (
    (GoogleBooksMetadataProvider, "google_books", "ebook"),
    (HardcoverMetadataProvider, "hardcover", "ebook"),
    (OpenLibraryMetadataProvider, "open_library", "ebook"),
    (InventaireMetadataProvider, "inventaire", "ebook"),
    (AudibleCatalogMetadataProvider, "audible_catalog", "asin"),
    (ITunesSearchMetadataProvider, "itunes_search", "work"),
    (AudnexusMetadataProvider, "audnexus", "asin"),
    (AudioSiloMetadataProvider, "audiosilo", "asin"),
)


def _plan(kind):
    if kind == "asin":
        return asin_plan()
    if kind == "work":
        return work_plan()
    return metadata_discovery_plan(
        TargetSnapshot("ebook", "Example", ()), (isbn_claim(),)
    )


@pytest.mark.parametrize("factory,name,kind", _PROVIDERS)
@pytest.mark.parametrize(
    "cause_type,carrier",
    (
        (RuntimeError, ProviderObservationFailure),
        (asyncio.CancelledError, ProviderObservationCancelled),
        (KeyboardInterrupt, ProviderObservationInterrupted),
    ),
)
@pytest.mark.parametrize("cleanup_fails", (False, True))
def test_real_adapter_failure_retains_original_cause_and_first_access(
    factory, name, kind, cause_type, carrier, cleanup_fails
):
    cause = cause_type("synthetic dispatch failure")
    requests = []

    class Transport(httpx.MockTransport):
        def close(self):
            if cleanup_fails:
                raise RuntimeError("synthetic cleanup failure")

    def dispatch(request):
        requests.append(request)
        raise cause

    client = httpx.Client(
        transport=Transport(dispatch), headers={"User-Agent": "synthetic"}
    )
    provider = factory(
        name,
        client,
        65536,
        pacer=ProviderRequestPacer(clock=lambda: 0.0, sleep=lambda _: None),
        clock=lambda: 0.0,
        wall_clock=lambda: 1700000000.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    with pytest.raises(carrier) as caught:
        provider.observe(_plan(kind), deadline=10.0)
    failure = caught.value
    assert failure.__cause__ is cause
    assert failure.__cause__.args == ("synthetic dispatch failure",)
    assert len(requests) == 1
    assert failure.snapshot.evidence.accessed_at == "2023-11-14T22:13:20.000000Z"
    assert type(failure.snapshot.evidence.attempt_records) is tuple
    assert type(failure.snapshot.evidence.incomplete_causes) is tuple
    assert type(failure.snapshot.cover_descriptors) is tuple
    for removed in (
        "attempt_records",
        "incomplete_causes",
        "accessed_at",
        "cover_descriptors",
    ):
        assert not hasattr(provider, removed)
    with pytest.raises(FrozenInstanceError):
        failure.snapshot.evidence.accessed_at = None


@pytest.mark.parametrize("factory,name,kind", _PROVIDERS)
def test_no_dispatch_has_known_empty_evidence_and_no_clock_read(factory, name, kind):
    def forbidden(*_args):
        raise AssertionError("no dispatch may read the clock or transport")

    provider = factory(
        name,
        httpx.Client(
            transport=httpx.MockTransport(forbidden),
            headers={"User-Agent": "synthetic"},
        ),
        65536,
        wall_clock=forbidden,
    )
    result = provider.observe(MetadataDiscoveryPlan((), (), True))
    assert isinstance(result, ProviderResult)
    assert result.observation.status is ProviderStatus.QUERY_LIMIT_EXCEEDED
    assert result.evidence == ProviderOperationalEvidence(None, (), ())
    assert result.cover_descriptors == ()


def test_snapshot_preserves_callback_owner_without_copying_its_lock():
    pacer = ProviderRequestPacer(clock=lambda: 0.0, sleep=lambda _: None)
    wait = pacer.wait
    descriptor = ProviderCoverDescriptor(
        "catalogue", CoverSourceKind.OPEN_LIBRARY, "source", None, wait=wait
    )
    evidence = ProviderOperationalEvidence(None, (), None)
    result = ProviderResult(
        ProviderObservation("catalogue", ProviderStatus.NO_MATCH),
        (descriptor,),
        evidence,
    )
    snapshot = ProviderFailureSnapshot(result.cover_descriptors, result.evidence)
    assert snapshot.cover_descriptors is result.cover_descriptors
    assert snapshot.cover_descriptors[0].wait is wait
    assert isinstance(snapshot.cover_descriptors[0].wait, MethodType)
    assert snapshot.cover_descriptors[0].wait.__self__ is pacer
    assert wait(1.0) is True
    assert wait(0.5) is False
    assert snapshot.evidence.incomplete_causes is None
    assert snapshot.evidence.attempt_records == ()
    with pytest.raises(FrozenInstanceError):
        result.cover_descriptors = ()  # type: ignore[misc]  # reason: exercise the frozen field rejection at runtime


@pytest.mark.parametrize(
    "delay,expected",
    (
        (37.0, 37),
        (37, 37),
        (37.5, None),
        (True, None),
        (float("nan"), None),
        (float("inf"), None),
        (0, None),
        (86400, 86400),
        (86401, None),
        (None, None),
    ),
)
def test_retry_after_uses_only_final_canonical_attempt(delay, expected):
    records = (
        ProviderAttemptRecord("p", 1, "http_429", 12.0, 0.0, "terminal"),
        ProviderAttemptRecord("p", 2, "http_429", delay, 0.0, "exhausted"),
    )
    assert (
        GenerateProviderRetrySuccessor._retry_after(
            ProviderOperationalEvidence(attempt_records=records)
        )
        == expected
    )


@pytest.mark.parametrize(
    "category,outcome",
    (("http_503", "exhausted"), ("http_429", "retry"), ("http_429", "cancelled")),
)
def test_retry_after_rejects_other_final_outcomes(category, outcome):
    record = ProviderAttemptRecord("p", 1, category, 37.0, 0.0, outcome)
    assert (
        GenerateProviderRetrySuccessor._retry_after(
            ProviderOperationalEvidence(attempt_records=(record,))
        )
        is None
    )


@pytest.mark.parametrize(
    "carrier", (ProviderObservationCancelled, ProviderObservationInterrupted)
)
def test_initial_report_propagates_cancellation_snapshot(carrier):
    snapshot = ProviderFailureSnapshot(
        evidence=ProviderOperationalEvidence(None, None, None)
    )
    failure = carrier(snapshot)

    class Provider:
        def observe(self, _plan, *, deadline=None):
            raise failure

    with pytest.raises(carrier) as caught:
        observe_provider("p", None, Provider(), MetadataDiscoveryPlan((), ()))
    assert caught.value is failure
    assert caught.value.snapshot is snapshot


def test_cleanup_failure_after_success_is_not_hidden_by_callers_exception_context():
    cleanup = RuntimeError("cleanup failed")

    class Transport(httpx.MockTransport):
        def close(self):
            raise cleanup

    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=Transport(lambda _: httpx.Response(404, json={}))),
        65536,
        wall_clock=lambda: 1700000000.0,
    )
    try:
        raise ValueError("unrelated caller context")
    except ValueError:
        with pytest.raises(ProviderObservationFailure) as caught:
            provider.observe(_plan("ebook"))
    assert caught.value.__cause__ is cleanup
    assert caught.value.snapshot.evidence.accessed_at == "2023-11-14T22:13:20.000000Z"


def test_snapshot_construction_cannot_replace_dispatch_failure(monkeypatch):
    from mechanical_librarian.adapters.providers import google_books_metadata

    cause = RuntimeError("dispatch failed")

    def dispatch(_request):
        raise cause

    def snapshot(*_args):
        raise RuntimeError("snapshot failed")

    provider = GoogleBooksMetadataProvider(
        "google_books", httpx.Client(transport=httpx.MockTransport(dispatch)), 65536
    )
    monkeypatch.setattr(google_books_metadata, "ProviderFailureSnapshot", snapshot)
    with pytest.raises(RuntimeError) as caught:
        provider.observe(_plan("ebook"))
    assert caught.value is cause
    assert caught.value.args == ("dispatch failed",)
