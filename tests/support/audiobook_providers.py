"""Captured audiobook-provider payloads and the plans that reach them.

Every JSON document under `audiobook_provider_payloads/` is a live capture kept
verbatim, so a parser proved against one is proved against what the provider
actually sends.  The one exception is `audiosilo_unknown_path.html`, which is
the head of the 35 KB single-page document AudioSilo answers an unknown API path
with; nothing reads that body, so its head is the whole proof.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    TargetSnapshot,
    audiobook_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    TargetDestination,
    TargetField,
    TargetFieldState,
    default_target_fields,
)

PAYLOADS = Path(__file__).parent / "audiobook_provider_payloads"


def payload(name: str) -> dict[str, Any]:
    """Return one captured JSON document, decoded."""
    value = json.loads((PAYLOADS / f"{name}.json").read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def payload_bytes(name: str) -> bytes:
    return (PAYLOADS / f"{name}.json").read_bytes()


def unknown_path_html() -> bytes:
    return (PAYLOADS / "audiosilo_unknown_path.html").read_bytes()


def audio_target(
    title: str | None = "Project Hail Mary",
    authors: tuple[str, ...] = ("Andy Weir",),
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


def local_claims(asin: str = "B08G9PRS1K") -> tuple[LocalClaim, ...]:
    """One audiobook's local evidence: an identifier, a title, and a runtime."""
    return (
        audio_claim(ClaimKind.ASIN, asin, 1),
        audio_claim(ClaimKind.TITLE, "Project Hail Mary", 2),
        audio_claim(ClaimKind.CONTRIBUTOR, "Ray Porter", 3, role="narrator"),
        audio_claim(ClaimKind.DURATION, "58244.201", 4),
    )


def asin_plan(asin: str = "B08G9PRS1K") -> MetadataDiscoveryPlan:
    return audiobook_discovery_plan(audio_target(), local_claims(asin))


def target_title_claim(title: str = "Project Hail Mary") -> LocalClaim:
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


def work_plan() -> MetadataDiscoveryPlan:
    """One plan whose only lane is the title-and-author fallback."""
    return audiobook_discovery_plan(audio_target(), (target_title_claim(),))
