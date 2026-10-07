"""Synthetic provider-produced first-book membership shared by persistence and UI."""

from dataclasses import replace

import httpx

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.support.audiobook_providers import audio_claim
from tests.support.audiobook_reports import synthetic_audiobook_product_lookup_report
from tests.support.hardcover_evidence import hardcover_envelope, isbn_claim
from tests.support.proposal_evidence import _fields, _report, consensus_local_title


def same_name_series_report(
    media: str,
    *,
    name: str | None = None,
    ebook_title: str = "First Journey",
    state: TargetFieldState = TargetFieldState.MISSING,
    positions: tuple[str, ...] = ("1",),
    independent_title: bool = True,
    source_author: str | None = None,
) -> MetadataReport:
    """One actual discovery/adapter report; positions are distinct candidates."""
    title = "Synthetic Journey" if media == "audiobook" else ebook_title
    name = title if name is None else name
    if media == "audiobook":
        products: tuple[dict[str, object], ...] = tuple(
            {
                "runtimeLengthMin": 970,
                "asin": f"B00000000{index + 1}",
                "title": title,
                "authors": [{"name": source_author or "Example Author"}],
                "narrators": [{"name": "Example Narrator"}],
                "seriesPrimary": {"name": name, "position": position},
            }
            for index, position in enumerate(
                positions if len(positions) > 1 else positions * 2
            )
        )
        base = synthetic_audiobook_product_lookup_report(
            products, recording_runtime="58200.000"
        )
        local = base.local_claims + (
            (audio_claim(ClaimKind.TITLE, title, 10),) if independent_title else ()
        )
        target = replace(
            base.target,
            fields=tuple(
                TargetField(
                    field.destination,
                    state,
                    None,
                )
                if field.destination is TargetDestination.SERIES
                else field
                for field in base.target.fields
            ),
        )
        report = MetadataReport.create(
            target=target,
            file=base.file,
            local_claims=local,
            local_reasons=base.local_reasons,
            providers=base.providers,
            audio=base.audio,
        )
    else:
        fields = _fields(
            title,
            authors=TargetField(
                TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Target Author",)
            ),
            series=TargetField(
                TargetDestination.SERIES,
                state,
                None,
            ),
        )
        local = (isbn_claim(),) + (
            (consensus_local_title(title),) if independent_title else ()
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", title, (), fields=fields), local
        )
        payload = hardcover_envelope(
            [
                {"id": i + 1, "book_id": i + 10, "isbn_13": "9780306406157"}
                for i in range(len(positions))
            ],
            [
                {
                    "id": i + 10,
                    "title": title,
                    "contributions": [
                        {
                            "id": 1,
                            "contribution": "Author",
                            "author": {
                                "id": 1,
                                "name": source_author or "Target Author",
                            },
                        }
                    ],
                    "book_series": [
                        {
                            "id": 1,
                            "position": position,
                            "series": {"id": 1, "name": name},
                        }
                    ],
                }
                for i, position in enumerate(positions)
            ],
        )
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )
        ) as client:
            observation = (
                HardcoverMetadataProvider("hardcover", client, 65_536)
                .observe(plan)
                .observation
            )
        report = _report(fields=fields, local_claims=local, providers=(observation,))
    return MetadataReport.from_json(report.to_json())
