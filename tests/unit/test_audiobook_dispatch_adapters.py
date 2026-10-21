"""Every adapter an audiobook is dispatched to answers every audiobook plan.

ADR-0061 clause 9 sends an audiobook's plan to each adapter in
`AUDIOBOOK_DISPATCH_ADAPTERS` whatever lane kinds the plan holds, so each one
must answer a plan it cannot serve with a valid observation and no request,
rather than raise (#1306).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

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
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
)
from mechanical_librarian.application.configuration import (
    AUDIOBOOK_DISPATCH_ADAPTERS,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    MetadataProvider,
)
from mechanical_librarian.domain.metadata_report import (
    ProviderQueryKind,
    ProviderRequestAccounting,
    ProviderStatus,
)
from tests.support.audiobook_providers import asin_plan, fed_asin_plan, work_plan
from tests.support.metadata_report_doubles import single_track_audio_facts

_ASIN, _ISBN, _WORK = (
    ProviderQueryKind.ASIN,
    ProviderQueryKind.ISBN,
    ProviderQueryKind.WORK,
)
# Each dispatched adapter, and the lane kinds it can be asked about.
_ADAPTERS: dict[str, tuple[type, frozenset[ProviderQueryKind]]] = {
    "audible_catalog": (AudibleCatalogMetadataProvider, frozenset({_ASIN, _WORK})),
    "audiosilo": (AudioSiloMetadataProvider, frozenset({_ASIN})),
    "audnexus": (AudnexusMetadataProvider, frozenset({_ASIN})),
    "hardcover": (HardcoverMetadataProvider, frozenset({_ISBN, _WORK})),
    "open_library": (OpenLibraryMetadataProvider, frozenset({_ISBN, _WORK})),
}
_PLANS: dict[str, Callable[[], MetadataDiscoveryPlan]] = {
    "embedded_asin": asin_plan,
    "fed_asin": fed_asin_plan,
    "work_only": work_plan,
}


def _adapter(
    authority: str, handler: Callable[[httpx.Request], httpx.Response]
) -> MetadataProvider:
    adapter_type = _ADAPTERS[authority][0]
    marketplaces = (
        {"marketplaces": ("us",)}
        if authority in {"audible_catalog", "audnexus"}
        else {}
    )
    adapter: MetadataProvider = adapter_type(
        authority,
        httpx.Client(transport=httpx.MockTransport(handler)),
        262_144,
        5.0,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
        **marketplaces,
    )
    return adapter


def test_the_contract_covers_exactly_the_dispatch_set() -> None:
    assert AUDIOBOOK_DISPATCH_ADAPTERS == set(_ADAPTERS)


@pytest.mark.parametrize("plan_name", sorted(_PLANS))
@pytest.mark.parametrize("authority", sorted(_ADAPTERS))
def test_every_dispatched_adapter_answers_every_audiobook_plan(
    authority: str, plan_name: str
) -> None:
    served = _ADAPTERS[authority][1]
    plan = replace(_PLANS[plan_name](), audio=single_track_audio_facts())
    asked = {lane.kind for lane in plan.lanes} & served
    sent: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if not asked:
            pytest.fail(f"{authority} sent a request for a lane it does not serve")
        sent.append(str(request.url))
        return httpx.Response(404, json={})

    observation = _adapter(authority, handle).observe(plan).observation

    # Whatever the plan held, the answer is a valid observation naming only
    # lane kinds this adapter serves.
    assert {lane.kind for lane in observation.lanes} <= served
    if asked:
        assert sent
        assert {lane.identity for lane in plan.lanes if lane.kind in served} == {
            lane.identity for lane in observation.lanes
        }
        return
    # Nothing to ask: no request, no lane, no input claimed, and nothing a
    # provider-limited or recovery reading could take for a failure.
    assert [] == sent
    assert ProviderStatus.INSUFFICIENT_INPUT is observation.status
    assert () == observation.lanes
    assert () == observation.input_references
    assert ProviderRequestAccounting() == observation.request_accounting
