"""Canonical complete cover producers shared by semantic and scale fixtures."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverFinalHost,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.cover_rules import CoverMeasures
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    comparison_links,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report


def _preview(
    index: int,
    *,
    size: int | None = None,
    width: int = 1,
    height: int = 1,
    mode: str = "RGBA",
    icc_profile: bytes | None = None,
) -> bytes:
    output = io.BytesIO()
    color = (
        (index % 251, (index * 3) % 251, (index * 7) % 251, 255)
        if mode == "RGBA"
        else (index % 251, (index * 3) % 251, (index * 7) % 251)
    )
    save_options: dict[str, object] = {"optimize": False, "compress_level": 9}
    if icc_profile is not None:
        save_options["icc_profile"] = icc_profile
    Image.new(mode, (width, height), color).save(output, "PNG", **save_options)
    payload = output.getvalue()
    if size is None:
        return payload
    raw = b"\x00" + bytes(color)
    # Inflate the valid zlib stream with empty stored blocks, then divide that
    # one stream across a small consecutive IDAT run. This reaches exact byte
    # boundaries without ancillary chunks or bytes after the zlib stream.
    extra_idat_chunks = next(
        value for value in range(5) if (73 + 12 * value) % 5 == size % 5
    )
    fixed_bytes = 73 + 12 * extra_idat_chunks
    empty_blocks, remainder = divmod(size - fixed_bytes, 5)
    if empty_blocks < 0 or remainder:
        raise ValueError("requested PNG size is too small")
    compressed = (
        b"\x78\x01"
        + b"\x00\x00\x00\xff\xff" * empty_blocks
        + b"\x01"
        + len(raw).to_bytes(2, "little")
        + (0xFFFF - len(raw)).to_bytes(2, "little")
        + raw
        + struct.pack(">I", zlib.adler32(raw))
    )

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data))
        )

    idat_count = extra_idat_chunks + 1
    boundaries = [
        len(compressed) * value // idat_count for value in range(idat_count + 1)
    ]
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    result = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    result += b"".join(
        chunk(b"IDAT", compressed[boundaries[value] : boundaries[value + 1]])
        for value in range(idat_count)
    )
    result += chunk(b"IEND", b"")
    if len(result) != size:
        raise AssertionError("exact PNG construction failed")
    return result


def _source(
    index: int, payload: bytes, *, incumbent: bool = False, embedded: bool = False
) -> CoverSource:
    artifact = CoverArtifact.create(
        f"original-{index}".encode(), payload, "image/png", 300 + index, 450 + index
    )
    kind = (
        CoverSourceKind.ABS
        if incumbent
        else CoverSourceKind.EPUB
        if embedded
        else (CoverSourceKind.HARDCOVER if index % 2 else CoverSourceKind.OPEN_LIBRARY)
    )
    provenance = (
        None
        if incumbent or embedded
        else CoverProvenance(
            kind.value,
            CoverDesignation.EXACT_EDITION if index % 3 == 0 else CoverDesignation.WORK,
            f"work-{index}",
            f"edition-{index}" if index % 3 == 0 else None,
        )
    )
    return CoverSource(
        cover_source_id(kind, f"source-{index}"),
        kind,
        CoverSourceState.RETRIEVED,
        artifact,
        provenance=provenance,
        final_host=(
            CoverFinalHost.US_ARCHIVE if kind is CoverSourceKind.OPEN_LIBRARY else None
        ),
    )


def adapter_cover_value(item: str = "item-1") -> str:
    """The target cover value the Audiobookshelf adapter states for a cover.

    It is the digest of the cover's path, never of its bytes, so a write that
    took it for the bytes' identity would bind nothing real (#1853).
    """
    path = f"/metadata/items/{item}/cover.jpg"
    return hashlib.sha256(path.encode()).hexdigest()


def _qualified_cover_report(
    root: Path,
    *,
    incumbent_index: int = 1,
    candidate_index: int = 2,
    incumbent_size: tuple[int, int] | None = None,
    incumbent_duplicate_size: tuple[int, int] | None = None,
    candidate_size: tuple[int, int] | None = None,
    candidate_designation: CoverDesignation = CoverDesignation.EXACT_EDITION,
    typed_timeout: bool = False,
    readable_current: bool = True,
    shared_measures: CoverMeasures | None = None,
    abs_state: CoverSourceState | None = None,
    abs_digest: str | None = None,
    target_state: TargetFieldState = TargetFieldState.VALUE,
) -> tuple[MetadataReport, object]:
    """`readable_current=False` leaves the current cover unread, so it asks.

    A readable current cover is kept without asking (#1310). `shared_measures`
    gives every image the same same-art measures, so the candidate shows the
    current cover's picture too. With `readable_current=False`, `abs_state`
    is the state Audiobookshelf's read ended in, with `abs_digest` the bytes'
    digest a decoder refusal records; without it there is no read at all
    (#1362). `target_state` is the cover's target state, whose value is the
    adapter's path digest only when it is a value.
    """
    incumbent_payload = _preview(incumbent_index)
    candidate_payload = _preview(candidate_index)
    incumbent_dimensions = incumbent_size or (
        300 + incumbent_index,
        450 + incumbent_index,
    )
    candidate_dimensions = candidate_size or (
        300 + candidate_index,
        450 + candidate_index,
    )
    incumbent_artifact = CoverArtifact.create(
        f"original-{incumbent_index}".encode(),
        incumbent_payload,
        "image/png",
        *incumbent_dimensions,
        measures=shared_measures,
    )
    incumbent_duplicate_artifact = (
        CoverArtifact.create(
            b"duplicate-original",
            incumbent_payload,
            "image/png",
            *incumbent_duplicate_size,
            measures=shared_measures,
        )
        if incumbent_duplicate_size is not None
        else incumbent_artifact
    )
    candidate_artifact = CoverArtifact.create(
        f"original-{candidate_index}".encode(),
        candidate_payload,
        "image/png",
        *candidate_dimensions,
        measures=shared_measures,
    )
    incumbent = replace(
        _source(1, incumbent_payload, incumbent=True),
        artifact=incumbent_artifact,
    )
    incumbent_corroboration = replace(
        _source(1, incumbent_payload, embedded=True),
        artifact=incumbent_duplicate_artifact,
    )
    candidate = replace(
        _source(3, candidate_payload),
        artifact=candidate_artifact,
        provenance=CoverProvenance(
            "hardcover",
            candidate_designation,
            "work-3",
            "edition-3"
            if candidate_designation is CoverDesignation.EXACT_EDITION
            else None,
        ),
    )
    local = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    bare = ProviderClaim.create(
        provider="hardcover",
        candidate="edition-3",
        kind=ClaimKind.ISBN,
        value="9780306406157",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    provider_claim = replace(bare, links=comparison_links(bare, (local,)))
    fields = tuple(
        TargetField(
            value.destination,
            target_state,
            adapter_cover_value() if target_state is TargetFieldState.VALUE else None,
        )
        if value.destination is TargetDestination.COVER
        else value
        for value in default_target_fields("Synthetic title")
    )
    sources = [
        *((incumbent,) if readable_current else ()),
        incumbent_corroboration,
        candidate,
    ]
    if not readable_current and abs_state is not None:
        sources.append(
            replace(
                incumbent,
                state=abs_state,
                artifact=None,
                timeout_scope="attempted"
                if abs_state is CoverSourceState.TIMEOUT
                else None,
                original_digest=abs_digest,
            )
        )
    if typed_timeout:
        sources.append(
            replace(
                _source(103, candidate_payload),
                state=CoverSourceState.TIMEOUT,
                artifact=None,
                timeout_scope="attempted",
            )
        )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(local,),
        local_reasons=("opening_matter_isbn",),
        providers=(
            ProviderObservation(
                "hardcover",
                ProviderStatus.CANDIDATES,
                candidates=(
                    ProviderCandidate("edition-3", "work-3", (provider_claim,)),
                ),
            ),
        ),
        cover_sources=tuple(sorted(sources, key=lambda value: value.source_id)),
    )
    artifacts = CoverArtifactStore(root / "artifacts")
    staged = [
        (incumbent, incumbent_payload, f"original-{incumbent_index}".encode()),
        (candidate, candidate_payload, f"original-{candidate_index}".encode()),
    ]
    if incumbent_duplicate_size is not None:
        staged.append(
            (incumbent_corroboration, incumbent_payload, b"duplicate-original")
        )
    for source, payload, original in staged:
        assert source.artifact is not None
        artifacts.stage(source.artifact, original, payload)
    return report, evaluate_report(report)


@contextmanager
def incumbent_identity(outcome: str, reasons: tuple[str, ...] = ()) -> Iterator[None]:
    """Classify the current cover's own candidate with this identity row.

    The policy reads the incumbent's row for a positive contradiction
    (ADR-0094 S6); the classifier's evidence for one is not needed to prove
    what the stage does with it.
    """
    from mechanical_librarian.application import cover_consideration, cover_policy
    from mechanical_librarian.application.cover_packet_semantics import (
        canonical_packet_json,
    )

    classify = cover_policy.classify_cover_identity

    def classified(
        report_document: str, packet_document: str, *args: object, **kwargs: object
    ) -> object:
        result = classify(report_document, packet_document, *args, **kwargs)  # type: ignore[arg-type]
        incumbent = next(
            candidate["reference"]
            for candidate in json.loads(packet_document)["candidates"]
            if candidate["incumbent"] is True
        )
        document = json.loads(result.document)
        for row in document["candidates"]:
            if row["candidate_reference"] == incumbent:
                row["outcome"], row["reasons"] = outcome, list(reasons)
                if outcome == "conflict":
                    row["evidence_references"] = []
        return replace(result, document=canonical_packet_json(document))

    # The consideration classifies again to bind what the policy read.
    with (
        patch.object(cover_policy, "classify_cover_identity", classified),
        patch.object(cover_consideration, "classify_cover_identity", classified),
    ):
        yield


def held_measured_stage(*args: object, **kwargs: object) -> object:
    """The measured stage, turned into a title hold."""
    import dataclasses

    from mechanical_librarian.domain.cover_rules import (
        CoverIssue,
        CoverOutcome,
        elect_measured_covers,
    )

    election = elect_measured_covers(*args, **kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(
        election,
        outcome=CoverOutcome.HOLD,
        choice=None,
        issues=(CoverIssue.TITLE_NAMES_OTHER_WORK,),
        reason=CoverIssue.TITLE_NAMES_OTHER_WORK,
        edition_preferred=False,
    )
