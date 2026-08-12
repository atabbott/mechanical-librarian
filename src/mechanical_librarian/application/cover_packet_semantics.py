"""One semantic validator shared by cover packet production and admission."""

from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import warnings
import zlib
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from mechanical_librarian.domain.cover_evidence import PREVIEW_VARIANT
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)

MAX_COVER_PREVIEWS = 12
MAX_COVER_PREVIEW_BYTES = 1_048_576
MAX_COVER_PACKET_BYTES = 8_388_608
MAX_COVER_PREVIEW_WIDTH = 320
MAX_COVER_PREVIEW_HEIGHT = 480

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_PNG_CHUNKS = 4_096
_APNG_CHUNKS = frozenset({b"acTL", b"fcTL", b"fdAT"})

_COVER_REFERENCE = re.compile(r"^cover_ref_[0-9a-f]{64}$")
_TRANSPORT_REFERENCE = re.compile(r"^cover_transport_ref_[0-9a-f]{64}$")
_PACKET_REFERENCE = re.compile(r"^cover_packet_[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SOURCE_KINDS = frozenset({"abs", "epub", "hardcover", "open_library"})
_DESIGNATION_RANK = {
    "exact_edition": 0,
    "related_edition": 1,
    "work": 2,
    "default": 3,
}
_CONTEXT_FIELDS = frozenset({
    "country", "format", "isbn", "language", "publication_date",
    "publication_place", "publisher",
})
_SOURCE_OMISSIONS = frozenset({
    "source_absent", "source_animated_or_multiframe", "source_decode_failed",
    "source_invalid_declaration", "source_oversized", "source_rejected",
    "source_source_changed", "source_timeout", "source_truncated",
    "source_unavailable", "source_unsafe_remote", "source_unsupported_format",
})


@dataclass(frozen=True, slots=True)
class AdmittedCoverPacket:
    document: str
    images: tuple[tuple[str, str, bytes], ...]


@dataclass(frozen=True, slots=True)
class NormalizedCoverPreview:
    payload: bytes
    digest: str
    width: int
    height: int


def canonical_packet_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def provenance_sort_key(
    value: dict[str, object],
) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    context = value["context"]
    if type(context) is not list:
        raise ValueError("cover provenance is invalid")
    return (
        value["source_kind"],
        value["provider"] or "",
        value["designation"] or "",
        tuple((item["field"], item["value"]) for item in context),
    )


def normalize_provenance(
    values: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Discard semantically empty multiplicity and return canonical order."""
    by_value = {canonical_packet_json(value): value for value in values}
    return sorted(by_value.values(), key=provenance_sort_key)


def candidate_priority(
    source_counts: dict[str, int],
    source_kinds: tuple[str, ...],
    identity_rank: int,
    width: int,
    height: int,
    reference: str,
) -> tuple[int, int, int, str, str]:
    return (
        sum(source_counts[value] for value in source_kinds),
        identity_rank,
        -(width * height),
        ",".join(source_kinds),
        reference,
    )


def designation_rank(value: str | None) -> int:
    return _DESIGNATION_RANK.get(value, 4)


def validate_cover_preview(
    payload: bytes,
    *,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> tuple[int, int]:
    """Fully verify one canonical, metadata-free RGBA PNG transport."""
    try:
        if (
            type(payload) is not bytes
            or not 1 <= len(payload) <= MAX_COVER_PREVIEW_BYTES
        ):
            raise ValueError
        structural_width, structural_height = _validate_png_structure(payload)
        pixels = _decode_rgba_pixels(
            payload, structural_width, structural_height
        )
        if _canonical_rgba_png(
            structural_width, structural_height, pixels
        ) != payload:
            raise ValueError
        if (
            expected_width is not None
            and structural_width != expected_width
        ) or (
            expected_height is not None
            and structural_height != expected_height
        ):
            raise ValueError
        return structural_width, structural_height
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as error:
        raise ValueError("cover preview is invalid") from error


def normalize_cover_preview(
    payload: bytes,
    *,
    source_sha256: str,
) -> NormalizedCoverPreview:
    """Bind, decode and canonically re-encode one report preview's pixels."""
    try:
        if (
            type(payload) is not bytes
            or not 1 <= len(payload) <= MAX_COVER_PREVIEW_BYTES
            or type(source_sha256) is not str
            or _DIGEST.fullmatch(source_sha256) is None
            or hashlib.sha256(payload).hexdigest() != source_sha256
        ):
            raise ValueError
        structural, width, height = _structural_png_without_ancillary(payload)
        _validate_png_structure(structural)
        pixels = _decode_rgba_pixels(structural, width, height)
        normalized = _canonical_rgba_png(width, height, pixels)
        validate_cover_preview(
            normalized, expected_width=width, expected_height=height
        )
        return NormalizedCoverPreview(
            normalized, hashlib.sha256(normalized).hexdigest(), width, height
        )
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ) as error:
        raise ValueError("cover preview normalization failed") from error


def cover_transport_reference(
    candidate_reference: str,
    source_bindings: list[dict[str, str]],
    transport_sha256: str,
) -> str:
    """Derive a transport reference from its source and canonical identities."""
    return "cover_transport_ref_" + hashlib.sha256(
        canonical_packet_json({
            "candidate_reference": candidate_reference,
            "source_bindings": source_bindings,
            "transport_sha256": transport_sha256,
        }).encode("utf-8")
    ).hexdigest()


def _decode_rgba_pixels(payload: bytes, width: int, height: int) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with Image.open(io.BytesIO(payload)) as image:
            if (
                image.format != "PNG"
                or image.mode != "RGBA"
                or getattr(image, "n_frames", 1) != 1
                or image.size != (width, height)
            ):
                raise ValueError
            image.load()
            pixels = image.tobytes()
    if len(pixels) != width * height * 4:
        raise ValueError
    return pixels


def _structural_png_without_ancillary(
    payload: bytes,
) -> tuple[bytes, int, int]:
    """Validate bounded PNG framing and remove untrusted ancillary chunks."""
    if not payload.startswith(_PNG_SIGNATURE):
        raise ValueError
    offset = len(_PNG_SIGNATURE)
    chunk_count = 0
    width = height = 0
    saw_header = saw_data = saw_end = False
    data_closed = False
    structural = bytearray(_PNG_SIGNATURE)
    while offset < len(payload):
        chunk_count += 1
        if chunk_count > _MAX_PNG_CHUNKS or len(payload) - offset < 12:
            raise ValueError
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        chunk_type = payload[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if (
            not all(65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type)
            or chunk_type[2] & 0x20 != 0
            or data_end < data_start
            or crc_end > len(payload)
        ):
            raise ValueError
        expected_crc = struct.unpack(">I", payload[data_end:crc_end])[0]
        if zlib.crc32(chunk_type + payload[data_start:data_end]) != expected_crc:
            raise ValueError
        if chunk_count == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError
            width, height = struct.unpack(">II", payload[data_start:data_start + 8])
            if (
                not 1 <= width <= MAX_COVER_PREVIEW_WIDTH
                or not 1 <= height <= MAX_COVER_PREVIEW_HEIGHT
                or payload[data_start + 8:data_end] != b"\x08\x06\x00\x00\x00"
            ):
                raise ValueError
            saw_header = True
            structural.extend(payload[offset:crc_end])
        elif chunk_type == b"IHDR" or saw_end:
            raise ValueError
        elif chunk_type == b"IDAT":
            if data_closed:
                raise ValueError
            saw_data = True
            structural.extend(payload[offset:crc_end])
        elif chunk_type == b"IEND":
            if length != 0 or not saw_data or crc_end != len(payload):
                raise ValueError
            saw_end = True
            data_closed = True
            structural.extend(payload[offset:crc_end])
        elif chunk_type in _APNG_CHUNKS:
            raise ValueError
        elif chunk_type[0] & 0x20:
            if saw_data:
                data_closed = True
        else:
            raise ValueError
        offset = crc_end
    if not (saw_header and saw_data and saw_end):
        raise ValueError
    return bytes(structural), width, height


def _canonical_rgba_png(width: int, height: int, pixels: bytes) -> bytes:
    if (
        type(width) is not int
        or type(height) is not int
        or type(pixels) is not bytes
        or not 1 <= width <= MAX_COVER_PREVIEW_WIDTH
        or not 1 <= height <= MAX_COVER_PREVIEW_HEIGHT
        or len(pixels) != width * height * 4
    ):
        raise ValueError

    scanlines = bytearray()
    row_bytes = width * 4
    for offset in range(0, len(pixels), row_bytes):
        scanlines.append(0)
        scanlines.extend(pixels[offset:offset + row_bytes])

    compressed = bytearray(b"\x78\x01")
    for offset in range(0, len(scanlines), 65_535):
        block = scanlines[offset:offset + 65_535]
        compressed.append(1 if offset + len(block) == len(scanlines) else 0)
        compressed.extend(struct.pack("<H", len(block)))
        compressed.extend(struct.pack("<H", len(block) ^ 0xFFFF))
        compressed.extend(block)
    compressed.extend(struct.pack(">I", zlib.adler32(scanlines)))

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + chunk_type
            + data
            + struct.pack(">I", zlib.crc32(chunk_type + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", bytes(compressed))
        + chunk(b"IEND", b"")
    )


def _validate_png_structure(payload: bytes) -> tuple[int, int]:
    if not payload.startswith(_PNG_SIGNATURE):
        raise ValueError
    offset = len(_PNG_SIGNATURE)
    chunk_count = 0
    width = height = 0
    saw_header = saw_data = saw_end = False
    data_closed = False
    compressed_image = bytearray()
    while offset < len(payload):
        chunk_count += 1
        if chunk_count > _MAX_PNG_CHUNKS or len(payload) - offset < 12:
            raise ValueError
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        chunk_type = payload[offset + 4:offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if (
            not all(65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type)
            or chunk_type[2] & 0x20 != 0
            or data_end < data_start
            or crc_end > len(payload)
        ):
            raise ValueError
        expected_crc = struct.unpack(">I", payload[data_end:crc_end])[0]
        if zlib.crc32(chunk_type + payload[data_start:data_end]) != expected_crc:
            raise ValueError
        if chunk_count == 1:
            if chunk_type != b"IHDR" or length != 13:
                raise ValueError
            width, height = struct.unpack(">II", payload[data_start:data_start + 8])
            if (
                not 1 <= width <= MAX_COVER_PREVIEW_WIDTH
                or not 1 <= height <= MAX_COVER_PREVIEW_HEIGHT
                or payload[data_start + 8:data_end] != b"\x08\x06\x00\x00\x00"
            ):
                raise ValueError
            saw_header = True
        elif chunk_type == b"IHDR" or saw_end:
            raise ValueError
        elif chunk_type == b"IDAT":
            if data_closed:
                raise ValueError
            saw_data = True
            compressed_image.extend(payload[data_start:data_end])
        elif chunk_type == b"IEND":
            if length != 0 or not saw_data or crc_end != len(payload):
                raise ValueError
            data_closed = True
            saw_end = True
        else:
            raise ValueError
        offset = crc_end
    if not (saw_header and saw_data and saw_end):
        raise ValueError
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(
            bytes(compressed_image),
            height * (1 + width * 4) + 1,
        )
    except zlib.error as error:
        raise ValueError from error
    if (
        len(decoded) != height * (1 + width * 4)
        or decoder.unconsumed_tail
        or not decoder.eof
        or decoder.unused_data
    ):
        raise ValueError
    return width, height


def bind_cover_packet(
    packet_document: str,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> AdmittedCoverPacket:
    """Validate every v2 packet field, invariant, and exact ordered image."""
    try:
        packet = json.loads(
            packet_document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if (
            type(packet) is not dict
            or canonical_packet_json(packet) != packet_document
            or set(packet) != {"candidates", "omissions", "packet_reference", "schema_version"}
            or packet["schema_version"] != "cover-review-packet-v2"
            or type(packet["candidates"]) is not list
            or not 1 <= len(packet["candidates"]) <= MAX_COVER_PREVIEWS
            or type(packet["omissions"]) is not list
            or _PACKET_REFERENCE.fullmatch(packet["packet_reference"]) is None
            or type(image_payloads) is not tuple
            or any(type(value) is not tuple or len(value) != 3 for value in image_payloads)
        ):
            raise ValueError
        candidates = packet["candidates"]
        candidate_facts = tuple(_validate_candidate(value) for value in candidates)
        supplied = tuple((value[0], value[1]) for value in image_payloads)
        expected = tuple(
            (value["transport_reference"], value["transport_sha256"])
            for value in candidates
        )
        if (
            supplied != expected
            or len({value[0] for value in supplied}) != len(supplied)
            or len({value[1] for value in supplied}) != len(supplied)
        ):
            raise ValueError
        total = 0
        for image, candidate in zip(image_payloads, candidates, strict=True):
            reference, digest, payload = image
            if (
                type(reference) is not str
                or type(digest) is not str
                or type(payload) is not bytes
                or not 1 <= len(payload) <= MAX_COVER_PREVIEW_BYTES
                or digest != candidate["transport_sha256"]
                or hashlib.sha256(payload).hexdigest() != digest
                or len(payload) != candidate["preview_bytes"]
            ):
                raise ValueError
            validate_cover_preview(
                payload,
                expected_width=candidate["preview_width"],
                expected_height=candidate["preview_height"],
            )
            total += len(payload)
        if total > MAX_COVER_PACKET_BYTES:
            raise ValueError
        _validate_candidate_order(candidates, candidate_facts)
        retained = {
            value["reference"]: value["preview_sha256"] for value in candidates
        }
        source_bindings = {
            binding["reference"]: (candidate["reference"], binding["preview_sha256"])
            for candidate in candidates
            for binding in candidate["source_bindings"]
        }
        if len(source_bindings) != sum(
            len(candidate["source_bindings"]) for candidate in candidates
        ):
            raise ValueError
        _validate_omissions(packet["omissions"], retained, source_bindings, total)
        packet_body = canonical_packet_json({
            "candidates": packet["candidates"],
            "omissions": packet["omissions"],
            "schema_version": packet["schema_version"],
        })
        if packet["packet_reference"] != "cover_packet_" + hashlib.sha256(packet_body.encode()).hexdigest():
            raise ValueError
        return AdmittedCoverPacket(packet_document, image_payloads)
    except (
        EvidenceValueError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise ValueError("cover packet is invalid") from error


def _validate_candidate(value: object) -> tuple[str, int]:
    if (
        type(value) is not dict
        or set(value) != {
            "height", "incumbent", "media_type", "preview_bytes",
            "preview_height", "preview_sha256", "preview_variant", "preview_width",
            "provenance", "reference", "source_bindings", "transport_reference",
            "transport_sha256", "width",
        }
        or _COVER_REFERENCE.fullmatch(value["reference"]) is None
        or _DIGEST.fullmatch(value["preview_sha256"]) is None
        or _DIGEST.fullmatch(value["transport_sha256"]) is None
        or _TRANSPORT_REFERENCE.fullmatch(value["transport_reference"]) is None
        or value["media_type"] != "image/png"
        or value["preview_variant"] != PREVIEW_VARIANT
        or type(value["preview_bytes"]) is not int
        or not 1 <= value["preview_bytes"] <= MAX_COVER_PREVIEW_BYTES
        or type(value["width"]) is not int
        or type(value["height"]) is not int
        or not 1 <= value["width"] <= 8192
        or not 1 <= value["height"] <= 8192
        or value["width"] * value["height"] > 16_777_216
        or type(value["preview_width"]) is not int
        or type(value["preview_height"]) is not int
        or not 1 <= value["preview_width"] <= MAX_COVER_PREVIEW_WIDTH
        or not 1 <= value["preview_height"] <= MAX_COVER_PREVIEW_HEIGHT
        or type(value["incumbent"]) is not bool
        or type(value["provenance"]) is not list
        or not value["provenance"]
        or type(value["source_bindings"]) is not list
        or not 1 <= len(value["source_bindings"]) <= 64
    ):
        raise ValueError
    source_bindings = tuple(
        _validate_source_binding(item) for item in value["source_bindings"]
    )
    if (
        source_bindings != tuple(sorted(set(source_bindings)))
        or (value["reference"], value["preview_sha256"]) not in source_bindings
        or value["transport_reference"] != cover_transport_reference(
            value["reference"], value["source_bindings"], value["transport_sha256"]
        )
    ):
        raise ValueError
    provenance = tuple(_validate_provenance(item) for item in value["provenance"])
    if provenance != tuple(sorted(set(provenance))):
        raise ValueError
    incumbent = any(item[0] == "abs" for item in provenance)
    if value["incumbent"] is not incumbent:
        raise ValueError
    source_kinds = tuple(sorted({item[0] for item in provenance}))
    rank = min(
        (designation_rank(item[2]) for item in provenance if item[2]),
        default=4,
    )
    return source_kinds, rank


def _validate_source_binding(value: object) -> tuple[str, str]:
    if (
        type(value) is not dict
        or set(value) != {"preview_sha256", "reference"}
        or _COVER_REFERENCE.fullmatch(value["reference"]) is None
        or _DIGEST.fullmatch(value["preview_sha256"]) is None
    ):
        raise ValueError
    return value["reference"], value["preview_sha256"]


def _validate_provenance(
    value: object,
) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    if (
        type(value) is not dict
        or set(value) != {"context", "designation", "provider", "source_kind"}
        or value["source_kind"] not in _SOURCE_KINDS
        or type(value["context"]) is not list
        or len(value["context"]) > 16
    ):
        raise ValueError
    source_kind = value["source_kind"]
    if source_kind in {"abs", "epub"}:
        if value["provider"] is not None or value["designation"] is not None or value["context"]:
            raise ValueError
        return source_kind, "", "", ()
    if (
        type(value["provider"]) is not str
        or _PROVIDER.fullmatch(value["provider"]) is None
        or value["designation"] not in _DESIGNATION_RANK
    ):
        raise ValueError
    context = tuple(_validate_context(item) for item in value["context"])
    if context != tuple(sorted(set(context))):
        raise ValueError
    return source_kind, value["provider"], value["designation"], context


def _validate_context(value: object) -> tuple[str, str]:
    if type(value) is not dict or set(value) != {"field", "value"} or value["field"] not in _CONTEXT_FIELDS:
        raise ValueError
    validated = validate_projection_text(value["value"], "cover context", 280)
    return value["field"], validated


def _validate_candidate_order(
    candidates: list[dict[str, object]],
    facts: tuple[tuple[tuple[str, ...], int], ...],
) -> None:
    incumbent_positions = [
        index for index, candidate in enumerate(candidates)
        if candidate["incumbent"] is True
    ]
    if len(incumbent_positions) > 1 or incumbent_positions not in ([], [0]):
        raise ValueError
    remaining = list(range(len(candidates)))
    expected: list[int] = []
    source_counts = {kind: 0 for kind in _SOURCE_KINDS}
    if incumbent_positions:
        index = incumbent_positions[0]
        remaining.remove(index)
        expected.append(index)
        for source_kind in facts[index][0]:
            source_counts[source_kind] += 1
    while remaining:
        index = min(
            remaining,
            key=lambda position: candidate_priority(
                source_counts,
                facts[position][0],
                facts[position][1],
                candidates[position]["width"],
                candidates[position]["height"],
                candidates[position]["reference"],
            ),
        )
        remaining.remove(index)
        expected.append(index)
        for source_kind in facts[index][0]:
            source_counts[source_kind] += 1
    if expected != list(range(len(candidates))):
        raise ValueError


def _validate_omissions(
    omissions: list[object],
    retained: dict[str, str],
    source_bindings: dict[str, tuple[str, str]],
    retained_bytes: int,
) -> None:
    normalized: list[tuple[str, str]] = []
    omitted_references: set[str] = set()
    duplicate_references: set[str] = set()
    for omission in omissions:
        if type(omission) is not dict or type(omission.get("reason")) is not str:
            raise ValueError
        reason = omission["reason"]
        if reason in _SOURCE_OMISSIONS:
            expected = {"reason", "reference"}
        elif reason == "duplicate_preview":
            expected = {"preview_sha256", "reason", "reference", "retained_reference"}
        elif reason in {"aggregate_byte_limit", "preview_byte_limit", "shortlist_limit"}:
            expected = {"preview_bytes", "reason", "reference"}
        else:
            raise ValueError
        if set(omission) != expected or _COVER_REFERENCE.fullmatch(omission["reference"]) is None:
            raise ValueError
        reference = omission["reference"]
        if reference in retained or reference in omitted_references:
            raise ValueError
        omitted_references.add(reference)
        if reason == "duplicate_preview":
            retained_reference = omission["retained_reference"]
            digest = omission["preview_sha256"]
            if (
                source_bindings.get(reference) != (retained_reference, digest)
                or retained_reference not in retained
                or _DIGEST.fullmatch(digest) is None
            ):
                raise ValueError
            duplicate_references.add(reference)
        elif reference in source_bindings:
            raise ValueError
        elif reason in {"aggregate_byte_limit", "preview_byte_limit", "shortlist_limit"}:
            preview_bytes = omission["preview_bytes"]
            if (
                type(preview_bytes) is not int
                or not 1 <= preview_bytes <= 2_147_483_647
            ):
                raise ValueError
            if reason == "preview_byte_limit" and preview_bytes <= MAX_COVER_PREVIEW_BYTES:
                raise ValueError
            if reason == "aggregate_byte_limit" and (
                preview_bytes > MAX_COVER_PREVIEW_BYTES
                or retained_bytes + preview_bytes <= MAX_COVER_PACKET_BYTES
            ):
                raise ValueError
            if reason == "shortlist_limit" and (
                preview_bytes > MAX_COVER_PREVIEW_BYTES
                or len(retained) != MAX_COVER_PREVIEWS
            ):
                raise ValueError
        normalized.append((reference, reason))
    if normalized != sorted(normalized):
        raise ValueError
    expected_duplicates = {
        reference
        for reference, (retained_reference, _digest) in source_bindings.items()
        if reference != retained_reference
    }
    if duplicate_references != expected_duplicates:
        raise ValueError


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(value)


__all__ = [
    "MAX_COVER_PACKET_BYTES", "MAX_COVER_PREVIEW_BYTES", "MAX_COVER_PREVIEWS",
    "bind_cover_packet", "candidate_priority", "canonical_packet_json",
    "cover_transport_reference", "designation_rank", "normalize_cover_preview",
    "normalize_provenance", "provenance_sort_key",
]
