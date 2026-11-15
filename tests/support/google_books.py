"""Google Books answers and discovery plans shared by provider tests."""

from __future__ import annotations

from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    ProviderQueryKind,
)


def isbn(value: str = "9780306406157") -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed=value,
        canonical=value,
        excerpt=f"ISBN {value}",
        applicability="ebook",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def volume(volume_id: str = "volume_1") -> dict[str, object]:
    return {
        "id": volume_id,
        "volumeInfo": {
            "title": "Example Book",
            "subtitle": "A Test",
            "authors": ["Ada Example"],
            "publisher": "Example Press",
            "publishedDate": "2026-Spring",
            "description": "Synthetic description",
            "industryIdentifiers": [
                {"type": "ISBN_10", "identifier": "0306406152"},
                {"type": "ISBN_13", "identifier": "9780306406157"},
            ],
            "pageCount": 321,
            "printType": "BOOK",
            "categories": ["Synthetic fiction"],
            "language": "en",
            "contentVersion": "1.2.3.0.preview.0",
        },
    }


def discovery_plan() -> MetadataDiscoveryPlan:
    values = (
        "9780306406157",
        "9780140328721",
        "9780439023528",
        "9780061120084",
        "9780743273565",
        "9780316769488",
        "9780451524935",
        "9780547928227",
    )
    claims = tuple(isbn(value) for value in values)
    lanes = [
        MetadataDiscoveryLane(
            f"lane_{index:064x}",
            ProviderQueryKind.ISBN,
            (claim.reference,),
            matched_family=value,
            canonical_isbns=(value,),
        )
        for index, (claim, value) in enumerate(zip(claims, values, strict=False))
    ]
    lanes.append(
        MetadataDiscoveryLane(
            f"lane_{len(lanes):064x}",
            ProviderQueryKind.WORK,
            (claims[0].reference,),
            title="Example Book",
            authors=("Ada Example",),
        )
    )
    return MetadataDiscoveryPlan(claims, tuple(lanes))


def identifier_first_plan(claim: LocalClaim | None = None) -> MetadataDiscoveryPlan:
    claim = isbn() if claim is None else claim
    return MetadataDiscoveryPlan(
        (claim,),
        (
            MetadataDiscoveryLane(
                "lane_" + "1" * 64,
                ProviderQueryKind.WORK,
                (claim.reference,),
                title="Example Book",
                authors=("Ada Example",),
            ),
            MetadataDiscoveryLane(
                "lane_" + "2" * 64,
                ProviderQueryKind.ISBN,
                (claim.reference,),
                matched_family="9780306406157",
                canonical_isbns=("9780306406157",),
            ),
        ),
    )
