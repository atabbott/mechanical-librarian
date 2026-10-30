"""Capture-shaped audiobook-provider payloads and the plans that reach them.

Every document under `audiobook_provider_payloads/` is synthetic: it keeps the
exact structure of a response the provider was observed to send -- its keys,
nesting, value types, array lengths, the fields one product states and another
omits, HTML summaries, regional and language variants, unicode and, for the
iTunes store, its raw text layout (the blank lines around the document and an
escaped solidus only where one closes an HTML tag) -- while every value in it
is invented.  The titles, people, publishers, identifiers, URLs, summaries and
reviews describe no real recording.  The structure is what a parser is proved
against, so it is kept faithful; a live capture would also carry publishers'
and critics' copyrighted prose, which a published repository cannot
redistribute, so none is kept, and most documents are shorter than the
responses they stand in for.  The first Audible search product is the
exception: it is kept near its captured size, because response-size bounds are
proved against copies of it.

`audiosilo_unknown_path.html` stands in for the head of the single-page site
document AudioSilo answers an unknown API path with; nothing reads that body,
so a synthetic head of the same size is the whole proof.
`open_library_original_date.json` was written synthetic from the start.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    TargetSnapshot,
    audiobook_asin_feed_plan,
    audiobook_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    AudioDiscoveryTitle,
    ClaimKind,
    ClaimScope,
    LocalClaim,
    ProviderQueryKind,
    TargetDestination,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

PAYLOADS = Path(__file__).parent / "audiobook_provider_payloads"


def payload(name: str) -> dict[str, Any]:
    """Return one capture-shaped JSON document, decoded."""
    value = json.loads((PAYLOADS / f"{name}.json").read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def payload_bytes(name: str) -> bytes:
    return (PAYLOADS / f"{name}.json").read_bytes()


def unknown_path_html() -> bytes:
    return (PAYLOADS / "audiosilo_unknown_path.html").read_bytes()


def audio_target(
    title: str | None = "Ember Tide Protocol",
    authors: tuple[str, ...] = ("Corin Halloway",),
) -> TargetSnapshot:
    populated = (
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, authors)
        if authors
        else TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
    )
    fields = tuple(
        populated if field.destination is TargetDestination.AUTHORS else field
        for field in default_target_fields(title)
    )
    return TargetSnapshot("audiobook", title, (), fields=fields)


def asserted_target(
    asin: str = "B0SYNE7P1K",
    title: str | None = "Ember Tide Protocol",
    authors: tuple[str, ...] = ("Corin Halloway",),
) -> TargetSnapshot:
    """One target that asserts an ASIN beside its title and authors."""
    asserted = TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, asin)
    return TargetSnapshot(
        "audiobook",
        title,
        (),
        fields=tuple(
            asserted if field.destination is TargetDestination.ASIN else field
            for field in audio_target(title, authors).fields
        ),
    )


def target_asin_claim(asin: str = "B0SYNE7P1K") -> LocalClaim:
    """The claim the audiobook path records for a target-asserted ASIN."""
    return LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed=asin,
        canonical=asin,
        excerpt=asin,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )


def audio_claim(
    kind: ClaimKind,
    value: str,
    occurrence: int = 1,
    *,
    role: str | None = None,
) -> LocalClaim:
    """Build one local audiobook claim exactly as the isolated inspector does."""
    return LocalClaim.create(
        kind=kind,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role=role,
        occurrence=occurrence,
    )


def local_claims(asin: str = "B0SYNE7P1K") -> tuple[LocalClaim, ...]:
    """One audiobook's local evidence: an identifier, a title, and a runtime."""
    return (
        audio_claim(ClaimKind.ASIN, asin, 1),
        audio_claim(ClaimKind.TITLE, "Ember Tide Protocol", 2),
        audio_claim(ClaimKind.CONTRIBUTOR, "Ilsa Brandt", 3, role="narrator"),
        audio_claim(ClaimKind.DURATION, "58244.201", 4),
    )


def asin_plan(asin: str = "B0SYNE7P1K") -> MetadataDiscoveryPlan:
    return audiobook_discovery_plan(audio_target(), local_claims(asin))


def fed_asin_plan(asin: str = "B0SYNE7P1K") -> MetadataDiscoveryPlan:
    """The ASIN feed for one target asserting `asin` and carrying no local one."""
    target = asserted_target(asin)
    feed = audiobook_asin_feed_plan(
        target,
        audiobook_discovery_plan(
            target, (target_title_claim(), target_asin_claim(asin))
        ),
        (),
    )
    assert feed is not None
    assert {ProviderQueryKind.ASIN} == {lane.kind for lane in feed.lanes}
    return feed


def with_asin_lane(plan: MetadataDiscoveryPlan) -> MetadataDiscoveryPlan:
    """`plan` with one embedded-ASIN product lane added beside its own lanes.

    No plan the product builds mixes the two -- an audiobook's lanes are ASIN
    or work and an ebook's carry no ASIN -- so this only proves that an adapter
    serving one kind leaves its answer to the other as it was.
    """
    product = asin_plan()
    return replace(
        plan,
        local_claims=(*plan.local_claims, *product.local_claims),
        lanes=tuple(
            sorted((*plan.lanes, *product.lanes), key=lambda lane: lane.identity)
        ),
    )


def target_title_claim(title: str = "Ember Tide Protocol") -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )


def work_plan(
    *extra_claims: LocalClaim,
    title: str = "Ember Tide Protocol",
    authors: tuple[str, ...] = ("Corin Halloway",),
) -> MetadataDiscoveryPlan:
    """One plan whose only lane is the title-and-author fallback.

    Extra local claims may be supplied so long as none of them is an ASIN: an
    ASIN claim would build a product lane and stop being this plan.
    """
    return audiobook_discovery_plan(
        audio_target(title, authors), (target_title_claim(title), *extra_claims)
    )


def local_runtime_claim(value: str = "58244.201") -> LocalClaim:
    """One container-stated running time, which the runtime band compares."""
    return audio_claim(ClaimKind.DURATION, value, 2)


def alternative_work_plan(
    title: str = "Asserted Title", authors: tuple[str, ...] = ("Ada Author",)
) -> MetadataDiscoveryPlan:
    """One audiobook plan whose asserted lane has an alternative beside it."""
    return audiobook_discovery_plan(
        audio_target(title, authors),
        (target_title_claim(title),),
        audio=replace(
            single_track_audio_facts(),
            discovery_titles=(AudioDiscoveryTitle("path_leaf", "The Real Title"),),
        ),
    )
