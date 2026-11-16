"""Evidence reached through an owner link decides nothing on its own (#1477).

End to end through the real seams: `GenerateMetadataReport` with real
adapters over synthetic answers and a real report store, then
`evaluate_report`, the shadow review packet, `decide_shadow_packet` and the
cover identity classifier. One ebook and one audiobook work link: the report
publishes, no `recommend_candidate` stands only on records the link or its
anchor reached, no ASIN proposal cites them, and no such cover verifies. Each
assertion has an ungated control showing the same records would otherwise
have decided, been proposed or verified.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AudibleCatalogMetadataProvider,
)
from mechanical_librarian.adapters.providers.audnexus_metadata import (
    AudnexusMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application import cover_identity
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    MetadataReportError,
    ProviderBinding,
    ProviderCoverDescriptor,
    TargetFile,
    TargetSnapshot,
    _audiobook_link_stands,
)
from mechanical_librarian.application.owner_link_preparation import (
    owner_link_anchor_moved,
    resettled_owner_link,
)
from mechanical_librarian.domain import proposal_set, reviewer_decision_summary
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    LocalClaim,
    MetadataReport,
    ProviderQueryKind,
    TargetDestination,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLink,
    OwnerLinkScope,
    OwnerLinkTarget,
)
from mechanical_librarian.domain.proposal_set import evaluate_report, owner_link_reached
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.integration import test_owner_link_preparation as prep
from tests.support.audiobook_providers import payload
from tests.support.cover_producers import _preview
from tests.support.hardcover_evidence import books_envelope
from tests.support.metadata_report_doubles import (
    FakeAudioInspector,
    FakeInspector,
    FakeTarget,
    audio_result,
)

AUDIO_TITLE = "Ember Tide Protocol"
AUDIO_AUTHOR = "Corin Halloway"
AUDIO_LINK = OwnerLinkTarget(OwnerLinkScope.WORK, "hardcover", "4321", AUDIO_TITLE)
LEAD_ASIN = "B0SYNL0NG3"


def _ungated() -> ExitStack:
    """Every shadow-first gate opened: the link's records count as any other."""
    stack = ExitStack()
    for module in (proposal_set, reviewer_decision_summary, cover_identity):
        stack.enter_context(
            mock.patch.object(module, "owner_link_reached", return_value=None)
        )
    return stack


def _decisions(report: MetadataReport) -> dict[str, tuple[str, str]]:
    proposals = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposals, report), proposals, report
    )
    packet = ShadowReviewPacket.from_json(packet.to_json(), report, proposals)
    return {
        value.destination: (value.recommendation, value.reason_code)
        for value in decide_shadow_packet(packet).decisions
    }


def _reached(report: MetadataReport) -> set[str]:
    """Every claim reference of a record only the link or its anchor reached."""
    return {
        claim.reference
        for provider in report.providers
        for candidate in provider.candidates
        if owner_link_reached(report, provider, candidate) is not None
        for claim in candidate.claims
    }


def _asin_citations(report: MetadataReport) -> set[str]:
    return {
        reference
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.ASIN
        for alternative in result.alternatives
        for reference in alternative.evidence_references
    }


_COVER_KINDS = {
    "audible_catalog": CoverSourceKind.AUDIBLE_CATALOG,
    "hardcover": CoverSourceKind.HARDCOVER,
}


def _cover_outcomes(report: MetadataReport) -> list[str]:
    """How a cover each reached record states would classify."""
    outcomes = []
    for provider in report.providers:
        kind = _COVER_KINDS.get(provider.authority or "")
        for candidate in provider.candidates:
            if kind is None or owner_link_reached(report, provider, candidate) is None:
                continue
            source = CoverSource(
                cover_source_id(kind, candidate.identity),
                kind,
                CoverSourceState.RETRIEVED,
                CoverArtifact.create(b"reached", _preview(5), "image/png", 400, 600),
                provenance=CoverProvenance(
                    provider.provider,
                    CoverDesignation.DEFAULT,
                    candidate.work_identity,
                    candidate.identity,
                ),
            )
            outcomes.append(
                cover_identity._classify_sources(
                    report, (source,), WorkIdentityLevel.TITLE_AND_CREATOR
                )[0]
            )
    return outcomes


# --- Ebook: a Hardcover work link, Google Books by ISBN and by the anchor ---


def _ebook_report(tmp_path: Path) -> MetadataReport:
    path = prep._store(tmp_path)
    prep._link(path, "ebooks", "ebook", prep.WORK_LINK)
    isbn = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed=prep.ISBN,
        canonical=prep.ISBN,
        excerpt=prep.ISBN,
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
    )
    return prep._prepare_ebook(
        path,
        (
            ProviderBinding(
                "hardcover",
                True,
                lambda: prep._hardcover(
                    lambda: prep._json(books_envelope(prep._linked_book()))
                ),
                authority="hardcover",
            ),
            ProviderBinding(
                "google_books",
                True,
                lambda: prep._google(
                    [], lambda _: prep._volume(prep.LINKED_TITLE, prep.LINKED_AUTHOR)
                ),
                authority="google_books",
            ),
        ),
        (isbn,),
    )


def _link_only(report: MetadataReport) -> dict[str, bool]:
    """Each proposal's candidates as the packet summarises them for the gate."""
    proposals = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposals, report), proposals, report
    )
    return {
        proposal["destination"]: all(
            value["decision_summary"]["owner_link_only"]
            for value in proposal["candidates"]
        )
        for proposal in json.loads(packet.to_json())["proposals"]
    }


def test_an_ebook_work_link_decides_nothing_on_what_only_it_reached(
    tmp_path: Path,
) -> None:
    report = _ebook_report(tmp_path)
    assert report.owner_link is not None
    assert report.owner_link.outcome == "admitted"
    # Google Books' ISBN record names the linked book, not the file's local
    # title: it represents the work only against the anchor.
    google = prep._provider(report, "google_books")
    assert {
        owner_link_reached(report, google, value) for value in google.candidates
    } == {"owner_link_anchor"}
    gated = _link_only(report)
    assert gated["title"] and gated["authors"] and gated["language"]
    assert not gated["isbn"]  # The local ISBN claim is the item's own.
    assert not any(
        recommendation == "recommend_candidate"
        for recommendation, _ in _decisions(report).values()
    )
    assert _cover_outcomes(report) == ["plausible_match"]
    # Control: ungated, the same records count as any other source's. This
    # single-source ebook stays manual on the engine's own preservation and
    # sufficiency rules, so the gate's effect shows in the packet summary.
    with _ungated():
        assert not any(_link_only(report).values())
        assert _cover_outcomes(report) != ["plausible_match"]


# --- Audiobook: a Hardcover work link, Audible search and the Audnexus feed --


def _audible(request: httpx.Request, *, search: bool) -> httpx.Response:
    body = payload("audible_search")
    if request.url.path.endswith("/products"):
        return httpx.Response(
            200,
            json=body if search else {**body, "products": [], "total_results": 0},
        )
    return httpx.Response(200, json={"product": body["products"][0]})


def _audnexus(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/chapters"):
        return httpx.Response(404, json={})
    book = payload("audnexus_book")
    book["asin"] = request.url.path.split("/")[2]
    return httpx.Response(200, json=book)


def _hardcover_audio(
    editions: tuple[dict[str, object], ...],
) -> HardcoverMetadataProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        operation = json.loads(request.content)["operationName"]
        if operation == "BookById":
            return prep._json(
                books_envelope(
                    [
                        {
                            "id": 4321,
                            "title": AUDIO_TITLE,
                            "contributions": [
                                {
                                    "id": 1,
                                    "contribution": None,
                                    "author": {"id": 9, "name": AUDIO_AUTHOR},
                                }
                            ],
                        }
                    ]
                )
            )
        if operation == "AudioEditions":
            offset = json.loads(request.content)["variables"]["offset"]
            return prep._json(
                {"data": {"editions": list(editions) if offset == 0 else []}}
            )
        if operation == "EditionCovers":
            return prep._json({"data": {"editions": []}})
        return prep._json({"data": {"books": [], "tags": []}})

    return HardcoverMetadataProvider(
        "hardcover",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def _audio_client(
    cls: Any, name: str, handler: Callable[[httpx.Request], httpx.Response]
) -> Any:
    return cls(
        name,
        httpx.Client(transport=httpx.MockTransport(handler)),
        262_144,
        5.0,
        marketplaces=("us",),
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def _audio_report(
    tmp_path: Path,
    *,
    search: bool = True,
    editions: tuple[dict[str, object], ...] = (),
    link: OwnerLinkTarget = AUDIO_LINK,
    cover_observer: Any = None,
) -> MetadataReport:
    path = prep._store(tmp_path)
    prep._link(path, "audio", "audiobook", link)
    selected = TargetFile("a" * 64, "book.m4b", "m4b")
    snapshot = TargetSnapshot(
        "audiobook", prep.LOCAL_TITLE, (selected,), fields=prep._fields()
    )
    bindings = (
        ProviderBinding(
            "audible_catalog",
            True,
            lambda: _audio_client(
                AudibleCatalogMetadataProvider,
                "audible_catalog",
                lambda request: _audible(request, search=search),
            ),
            authority="audible_catalog",
        ),
        ProviderBinding(
            "audnexus",
            True,
            lambda: _audio_client(AudnexusMetadataProvider, "audnexus", _audnexus),
            authority="audnexus",
        ),
        ProviderBinding(
            "hardcover", True, lambda: _hardcover_audio(editions), authority="hardcover"
        ),
    )
    return MetadataReport.from_json(
        GenerateMetadataReport(
            target=FakeTarget([snapshot, snapshot]),
            inspector=FakeInspector(InspectionResult("c" * 64, ())),
            repository_factory=lambda: open_repository(path),
            providers=bindings,
            connection=TargetConnectionConfiguration(
                prep.CONNECTION, "https://example.invalid", "/private/target"
            ),
            library=LibraryConfiguration(
                "audio",
                prep.CONNECTION,
                "library-id",
                "audiobook",
                "audiobooks",
                tuple(sorted(binding.name for binding in bindings)),
            ),
            mount=MediaMountConfiguration("audiobooks", "/media", "/library", True),
            limits=ReportLimits(),
            audio_inspector=FakeAudioInspector(
                audio_result(((ClaimKind.DURATION, "58200.000", "embedded_tag", None),))
            ),
            cover_observer=cover_observer,
        ).run(prep.ITEM, 0)
    )


def test_an_audiobook_work_link_publishes_and_decides_nothing_it_alone_reached(
    tmp_path: Path,
) -> None:
    # The main phase searches Audible with the anchor and feeds the ASINs it
    # promoted to Audnexus: the report publishes, every record those lanes
    # kept is reached through the link, and nothing decides on them.
    report = _audio_report(tmp_path)
    assert report.owner_link is not None
    assert report.owner_link.anchor == (AUDIO_TITLE, (AUDIO_AUTHOR,))
    audnexus = prep._provider(report, "audnexus")
    assert audnexus.candidates
    assert all(
        owner_link_reached(report, audnexus, value) == "owner_link_anchor"
        for value in audnexus.candidates
    )
    reached = _reached(report)
    gated = _decisions(report)
    assert not any(
        recommendation == "recommend_candidate" for recommendation, _ in gated.values()
    )
    assert not _asin_citations(report) & reached
    assert set(_cover_outcomes(report)) == {"plausible_match"}
    with _ungated():
        assert set(_cover_outcomes(report)) != {"plausible_match"}
        ungated = _decisions(report)
        assert _asin_citations(report) & reached
    assert any(
        recommendation == "recommend_candidate"
        for recommendation, _ in ungated.values()
    )


def test_hardcover_link_audio_editions_feed_the_asin_sources_as_link_reached(
    tmp_path: Path,
) -> None:
    # S18: the linked book's audio editions are leads like a search's. With
    # no Audible match, the lead reaches Audnexus on the anchor's inputs.
    edition = {
        "id": 77,
        "book_id": 4321,
        "asin": LEAD_ASIN,
        "audio_seconds": 58_200,
        "image": None,
        "language": None,
        "contributions": [
            {
                "id": 7,
                "contribution": "Author",
                "author": {"id": 9, "name": AUDIO_AUTHOR},
            },
            {
                "id": 8,
                "contribution": "Narrator",
                "author": {"id": 10, "name": "Ilsa Brandt"},
            },
        ],
    }
    (tmp_path / "lead").mkdir(mode=0o700)
    (tmp_path / "bare").mkdir(mode=0o700)
    report = _audio_report(tmp_path / "lead", search=False, editions=(edition,))
    audnexus = prep._provider(report, "audnexus")
    assert [value.identity for value in audnexus.candidates] == [
        f"audnexus-book-{LEAD_ASIN}"
    ]
    assert {
        owner_link_reached(report, audnexus, value) for value in audnexus.candidates
    } == {"owner_link_anchor"}
    assert not _asin_citations(report) & _reached(report)
    # Control: with no audio edition listed, nothing reaches Audnexus.
    bare = _audio_report(tmp_path / "bare", search=False)
    assert not prep._provider(bare, "audnexus").candidates


@pytest.mark.parametrize(
    ("medium", "scope", "stands"),
    [
        pytest.param("audiobook", OwnerLinkScope.WORK, True, id="audiobook-work"),
        pytest.param("audiobook", OwnerLinkScope.RECORDING, True, id="recording"),
        pytest.param("ebook", OwnerLinkScope.WORK, False, id="ebook-work"),
    ],
)
def test_any_standing_audiobook_link_supersedes_the_carried_admission(
    medium: str, scope: OwnerLinkScope, stands: bool
) -> None:
    # Finding 4 (coordinator ruling): under a work link a carried product
    # would be re-admitted on the local values, so a work link supersedes the
    # carried admission as a recording link does.
    link = OwnerLink(
        "owner_link_" + "0" * 64,
        medium,
        scope,
        "audible_catalog" if scope is OwnerLinkScope.RECORDING else "hardcover",
        "B0SYNREC01" if scope is OwnerLinkScope.RECORDING else "4321",
    )
    assert _audiobook_link_stands(link) is stands
    assert not _audiobook_link_stands(None)


class _DescriptorObserver:
    """Admits provider descriptors as the cover observer does: one per source."""

    def __init__(self) -> None:
        self.descriptors: tuple[ProviderCoverDescriptor, ...] = ()

    def observe_current(self, *_: object, **__: object) -> tuple[CoverSource, ...]:
        return ()

    def observe(
        self,
        target: object,
        providers: object,
        local_sources: object,
        descriptors: tuple[ProviderCoverDescriptor, ...] = (),
        *_: object,
    ) -> tuple[CoverSource, ...]:
        if len({value.source_id for value in descriptors}) != len(descriptors):
            raise MetadataReportError("provider cover result is invalid")
        self.descriptors = descriptors
        return ()


def test_a_recording_link_the_anchored_search_also_finds_prepares(
    tmp_path: Path,
) -> None:
    # Codex P1 on #1938: the linked product reaches the link phase as the
    # exact edition and the anchored search as a related one, under one cover
    # source. The phases' descriptors merge as one lane's would, so the
    # observer admits them once and the report publishes.
    asin = payload("audible_search")["products"][0]["asin"]
    observer = _DescriptorObserver()
    report = _audio_report(
        tmp_path,
        link=OwnerLinkTarget(OwnerLinkScope.RECORDING, "audible_catalog", asin, "A"),
        cover_observer=observer,
    )
    assert report.owner_link is not None
    assert report.owner_link.outcome == "admitted"
    audible = prep._provider(report, "audible_catalog")
    assert {lane.kind for lane in audible.lanes} == {
        ProviderQueryKind.ASIN,
        ProviderQueryKind.WORK,
    }
    (linked,) = (
        value for value in observer.descriptors if value.provider == "audible_catalog"
    )
    assert linked.provenance is not None
    assert linked.provenance.designation is CoverDesignation.EXACT_EDITION


def test_a_successor_keeps_the_anchor_its_carried_lanes_were_asked_with(
    tmp_path: Path,
) -> None:
    # Codex P2-3 on #1938: a retried link lane that now keeps a record from
    # a provider sorted first would settle another anchor than the one the
    # carried main-phase lanes were admitted against, and publication failed
    # on their lineage. The source's anchor stands, and the moved anchor asks
    # for a fresh preparation instead.
    asin = payload("audible_search")["products"][0]["asin"]
    report = _audio_report(
        tmp_path,
        link=OwnerLinkTarget(OwnerLinkScope.RECORDING, "audible_catalog", asin, "A"),
    )
    facts = report.owner_link
    assert facts is not None and facts.anchor == (AUDIO_TITLE, (AUDIO_AUTHOR,))
    # As if the source had read its anchor from a record another provider
    # kept while Audible's link lane was unavailable.
    source = replace(facts, anchor=(AUDIO_TITLE, ("C. Halloway",)))
    assert resettled_owner_link(source, report.providers, None).anchor == (
        source.anchor
    )
    assert owner_link_anchor_moved(source, report.providers, None)
    # Control: a source that read this anchor keeps it and asks for nothing.
    assert resettled_owner_link(facts, report.providers, None) == facts
    assert not owner_link_anchor_moved(facts, report.providers, None)
