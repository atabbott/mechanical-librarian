"""Choose and seal the recorded cover decisions one taste build sends.

Per decision: the chosen cover, the current cover, then one cover of each other
art, then other copies, at most four, each re-admitted from the artifact store
as the exact preview the consideration recorded (#1372 S3-S5, S16).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.application.cover_packet_semantics import (
    normalize_cover_preview,
)
from mechanical_librarian.application.cover_taste_contract import (
    COVER_TASTE_BUILD_PACKET_SCHEMA,
    TasteBuildImage,
    taste_image_request_bytes,
    taste_request_frame_bytes,
    taste_transport_jpeg,
)
from mechanical_librarian.domain.cover_taste import (
    TASTE_MAX_DECISIONS,
    TASTE_MAX_IMAGES,
    TASTE_MAX_REQUEST_BYTES,
    TASTE_PREVIEWS_PER_DECISION,
)

# A packet's own bytes per image and per decision, counted generously so the
# budget check before the packet exists is never below the admitted total.
_PACKET_BYTES_PER_IMAGE = 192
_PACKET_BYTES_PER_DECISION = 64


@dataclass(frozen=True, slots=True)
class TasteCandidateSource:
    """One previewed cover of a recorded decision, as the consideration kept it."""

    reference: str
    preview_sha256: str
    transport_sha256: str
    # The original's decoded size, which the preview cannot show (#1372 S5).
    width: int
    height: int
    current: bool
    rank: int | None
    ordinal: int
    wrong_shape: bool
    # Candidates sharing this key showed the same art in the stage.
    art: str


@dataclass(frozen=True, slots=True)
class TasteDecisionSource:
    selection_id: str
    medium: Literal["ebook", "audiobook"]
    chosen_reference: str
    candidates: tuple[TasteCandidateSource, ...]


@dataclass(frozen=True, slots=True)
class TasteBuildInput:
    packet_document: str
    images: tuple[TasteBuildImage, ...]
    record: dict[str, object]


def taste_previews(decision: TasteDecisionSource) -> list[TasteCandidateSource]:
    """The decision's previews in sending order: chosen, current, other arts, copies."""
    chosen = next(
        candidate
        for candidate in decision.candidates
        if candidate.reference == decision.chosen_reference
    )
    selected = [chosen]
    selected.extend(
        candidate
        for candidate in decision.candidates
        if candidate.current and candidate is not chosen
    )
    rest = sorted(
        (candidate for candidate in decision.candidates if candidate not in selected),
        key=lambda candidate: (
            candidate.rank is None,
            candidate.rank or 0,
            candidate.ordinal,
        ),
    )
    arts = {candidate.art for candidate in selected}
    for candidate in rest:
        if candidate.art not in arts:
            arts.add(candidate.art)
            selected.append(candidate)
    selected.extend(candidate for candidate in rest if candidate not in selected)
    return selected[:TASTE_PREVIEWS_PER_DECISION]


def _sealed(
    candidates: list[TasteCandidateSource], read_preview: Callable[[str], bytes]
) -> list[tuple[bytes, bytes]] | None:
    """Each preview's canonical PNG and JPEG, or None when any fails re-admission."""
    sealed: list[tuple[bytes, bytes]] = []
    for candidate in candidates:
        try:
            preview = normalize_cover_preview(
                read_preview(candidate.preview_sha256),
                source_sha256=candidate.preview_sha256,
            )
            if preview.digest != candidate.transport_sha256:
                return None
            sealed.append((preview.payload, taste_transport_jpeg(preview.payload)))
        except (OSError, ValueError):
            return None
    return sealed


def assemble_taste_inputs(
    decisions: Sequence[TasteDecisionSource],
    read_preview: Callable[[str], bytes],
    *,
    nonce: bytes | None = None,
) -> TasteBuildInput | None:
    """Seal up to 50 decisions in stable sample order within the image and byte caps.

    A decision whose preview fails re-admission is dropped and counted; it
    never fails the build. None when no decision could be sealed.
    """
    nonce = secrets.token_bytes(32) if nonce is None else nonce
    ordered = sorted(
        decisions,
        key=lambda decision: hashlib.sha256(
            decision.selection_id.encode("utf-8")
        ).hexdigest(),
    )
    packet_decisions: list[dict[str, object]] = []
    images: list[TasteBuildImage] = []
    included: list[tuple[str, list[str]]] = []
    dropped = 0
    capped = False
    budget = taste_request_frame_bytes("") + 64
    for decision in ordered:
        if len(packet_decisions) == TASTE_MAX_DECISIONS:
            capped = True
            break
        previews = taste_previews(decision)
        if len(images) + len(previews) > TASTE_MAX_IMAGES:
            capped = True
            break
        sealed = _sealed(previews, read_preview)
        if sealed is None:
            dropped += 1
            continue
        cost = _PACKET_BYTES_PER_DECISION + sum(
            _PACKET_BYTES_PER_IMAGE + taste_image_request_bytes(jpeg)
            for _png, jpeg in sealed
        )
        if budget + cost > TASTE_MAX_REQUEST_BYTES:
            capped = True
            break
        budget += cost
        letters: dict[str, str] = {}
        packet_images: list[dict[str, object]] = []
        for candidate, (png, jpeg) in zip(previews, sealed, strict=True):
            reference = (
                "taste_image_"
                + hashlib.sha256(nonce + len(images).to_bytes(4, "big")).hexdigest()
            )
            images.append(TasteBuildImage(reference, png, jpeg))
            packet_images.append(
                {
                    "current": candidate.current,
                    "group": letters.setdefault(
                        candidate.art, chr(ord("A") + len(letters))
                    ),
                    "height": candidate.height,
                    "reference": reference,
                    "role": "chosen"
                    if candidate.reference == decision.chosen_reference
                    else "passed_over",
                    "shape": "wrong_shape" if candidate.wrong_shape else "fits",
                    "width": candidate.width,
                }
            )
        packet_decisions.append({"images": packet_images, "medium": decision.medium})
        included.append(
            (
                decision.selection_id,
                [candidate.transport_sha256 for candidate in previews],
            )
        )
    if not packet_decisions:
        return None
    packet = canonical_json(
        {
            "decisions": packet_decisions,
            "schema_version": COVER_TASTE_BUILD_PACKET_SCHEMA,
        }
    )
    return TasteBuildInput(
        packet,
        tuple(images),
        {
            "corrections": 0,
            "dropped": dropped,
            "eligible": len(decisions),
            "images": len(images),
            "included": len(packet_decisions),
            "input_sha256": hashlib.sha256(
                canonical_json(included).encode("utf-8")
            ).hexdigest(),
            "truncated": capped or dropped > 0,
        },
    )


__all__ = [
    "TasteBuildInput",
    "TasteCandidateSource",
    "TasteDecisionSource",
    "assemble_taste_inputs",
    "taste_previews",
]
