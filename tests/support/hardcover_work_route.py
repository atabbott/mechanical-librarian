"""A recording-less audiobook whose book Hardcover admits, through the real adapter.

The Hardcover answer is parsed by `HardcoverMetadataProvider` for an audiobook
WORK lane, so its work candidate, its audio editions and its first-publication
claim are what production states (#1642). No audiobook authority admits a
recording, so the report reaches the work route.
"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import observe_provider
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
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from tests.support.audiobook_providers import (
    audio_claim,
    audio_target,
    local_runtime_claim,
    work_plan,
)
from tests.support.audiobook_reports import recorded_dispatch
from tests.support.hardcover_evidence import hardcover_answer
from tests.support.metadata_report_doubles import single_track_audio_facts

TITLE = "Ember Tide Protocol"
AUTHOR = "Corin Halloway"
NARRATOR = "Imra Vossel"
WORK = "hardcover-work-0000000010"
# 58244.201 s on the file, as `local_runtime_claim` states it.
LOCAL_SECONDS = 58_244


def audio_edition(
    edition_id: int,
    narrators: tuple[str, ...] = (NARRATOR,),
    *,
    seconds: int = LOCAL_SECONDS,
    asin: str | None = None,
) -> dict[str, object]:
    """One Hardcover audio edition row, as the AudioEditions query answers."""
    return {
        "id": edition_id,
        "book_id": 10,
        "asin": asin,
        "audio_seconds": seconds,
        "image": None,
        "language": None,
        "contributions": [
            {"id": 7, "contribution": "Author", "author": {"id": 7, "name": AUTHOR}},
            *(
                {
                    "id": 100 + index,
                    "contribution": "Narrator",
                    "author": {"id": 100 + index, "name": name},
                }
                for index, name in enumerate(narrators)
            ),
        ],
    }


def hardcover_work_observation(
    *,
    release_date: str | None = "1999-05-01",
    description: str | None = "A tide that keeps its own time.",
    editions: tuple[dict[str, object], ...] = (),
    image: bool = True,
    local_claims: tuple[LocalClaim, ...] = (),
) -> ProviderObservation:
    """Hardcover's answer for the audiobook WORK lane, with its audio editions."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["operationName"] == "AudioEditions":
            rows = editions if body["variables"]["offset"] == 0 else ()
            return httpx.Response(200, json={"data": {"editions": list(rows)}})
        return httpx.Response(
            200,
            json=hardcover_answer(
                request,
                [],
                [
                    {
                        "id": 10,
                        "title": TITLE,
                        "description": description,
                        "release_date": release_date,
                        "image": (
                            {"url": "https://assets.hardcover.app/book/10.jpg"}
                            if image
                            else None
                        ),
                        "contributions": [
                            {
                                "id": 100,
                                "contribution": None,
                                "author": {"id": 7, "name": AUTHOR},
                            }
                        ],
                    }
                ],
            ),
        )

    plan = replace(
        work_plan(local_runtime_claim(), *local_claims),
        audio=single_track_audio_facts(),
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = HardcoverMetadataProvider(
            "hardcover",
            client,
            262_144,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda _seconds: None,
            jitter=lambda: 0.0,
        )
        return recorded_dispatch(
            observe_provider("hardcover", "hardcover", provider, plan).observation
        )


def work_cover_source(
    width: int = 500,
    height: int = 750,
    *,
    work_identity: str = WORK,
    index: int = 1,
    preview: bytes | None = None,
) -> CoverSource:
    """A retrieved Hardcover book cover bound to the admitted work.

    `preview` is a real preview image for a consumer that decodes it.
    """
    return CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, f"hardcover:work:{index}"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(
            f"work-original-{index}".encode(),
            preview or f"work-preview-{index}".encode(),
            "image/png",
            width,
            height,
            None,
        ),
        provenance=CoverProvenance(
            "hardcover", CoverDesignation.DEFAULT, work_identity
        ),
    )


def incumbent_cover_source(width: int, height: int) -> CoverSource:
    """The library's own current cover, retrieved and measured."""
    return CoverSource(
        cover_source_id(CoverSourceKind.ABS, "incumbent"),
        CoverSourceKind.ABS,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(
            b"incumbent-original",
            b"incumbent-preview",
            "image/png",
            width,
            height,
            None,
        ),
    )


def hardcover_work_report(
    *,
    release_date: str | None = "1999-05-01",
    description: str | None = "A tide that keeps its own time.",
    editions: tuple[dict[str, object], ...] = (),
    providers: tuple[ProviderObservation, ...] = (),
    cover_sources: tuple[CoverSource, ...] = (),
    current: dict[TargetDestination, str | tuple[str, ...]] | None = None,
    local_narrator: str | None = None,
    local_claims: tuple[LocalClaim, ...] = (),
    states: dict[TargetDestination, TargetFieldState] | None = None,
) -> MetadataReport:
    """A recording-less audiobook whose book Hardcover admits exactly.

    `current` sets target values, and an incumbent cover source binds the
    target cover to that incumbent's artifact. `states` gives a field a
    non-value state, such as a malformed one.
    """
    claims = (
        *local_claims,
        *(
            ()
            if local_narrator is None
            else (
                audio_claim(ClaimKind.CONTRIBUTOR, local_narrator, 3, role="narrator"),
            )
        ),
    )
    hardcover = hardcover_work_observation(
        release_date=release_date,
        description=description,
        editions=editions,
        local_claims=claims,
    )
    plan = work_plan(local_runtime_claim(), *claims)
    values: dict[TargetDestination, str | tuple[str, ...]] = dict(current or {})
    incumbent = next(
        (source for source in cover_sources if source.kind is CoverSourceKind.ABS),
        None,
    )
    if incumbent is not None and incumbent.artifact is not None:
        values.setdefault(TargetDestination.COVER, incumbent.artifact.artifact_id)
    fields = tuple(
        TargetField(field.destination, (states or {})[field.destination])
        if field.destination in (states or {})
        else TargetField(
            field.destination, TargetFieldState.VALUE, values[field.destination]
        )
        if field.destination in values
        else field
        for field in audio_target(TITLE, (AUTHOR,)).fields
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=plan.local_claims,
        local_reasons=("target_metadata_title",),
        providers=tuple(
            sorted(
                (hardcover, *(recorded_dispatch(value) for value in providers)),
                key=lambda value: value.provider,
            )
        ),
        audio=single_track_audio_facts(),
        cover_sources=tuple(sorted(cover_sources, key=lambda value: value.source_id)),
    )
    return MetadataReport.from_json(report.to_json())
