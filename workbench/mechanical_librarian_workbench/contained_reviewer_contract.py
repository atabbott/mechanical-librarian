"""Narrow source-workbench port for one contained reviewer invocation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

MAX_INPUT_BYTES = 1_048_576
MAX_SCHEMA_BYTES = 65_536
MAX_IMAGE_COUNT = 12
MAX_IMAGE_BYTES = 1_048_576
MAX_IMAGE_TOTAL_BYTES = 8_388_608
_COVER_REFERENCE = re.compile(r"^cover_ref_[0-9a-f]{64}$")
_PACKET_REFERENCE = re.compile(r"^cover_packet_[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SOURCE_KINDS = frozenset({"abs", "epub", "hardcover", "open_library"})
_DESIGNATIONS = frozenset({"exact_edition", "related_edition", "work", "default"})
_CONTEXT_FIELDS = frozenset({
    "country", "format", "isbn", "language", "publication_date",
    "publication_place", "publisher",
})
_OMISSION_REASONS = frozenset({
    "aggregate_byte_limit", "duplicate_preview", "preview_byte_limit",
    "shortlist_limit", "source_absent", "source_animated_or_multiframe",
    "source_decode_failed", "source_invalid_declaration", "source_oversized",
    "source_rejected", "source_source_changed", "source_timeout",
    "source_truncated", "source_unavailable", "source_unsafe_remote",
    "source_unsupported_format",
})
_COVER_ADMISSION = object()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ContainedReviewerError(RuntimeError):
    """A value-free failure at the contained-reviewer trust boundary."""

    _CODES = frozenset({
        "auth", "cancel", "malformed_output", "profile", "response_format",
        "runtime", "setup", "timeout",
    })

    def __init__(self, code: str, *, count: int = 0, digest: str | None = None) -> None:
        if code not in self._CODES or type(count) is not int or not 0 <= count <= 2_147_483_647:
            raise ValueError("invalid contained-reviewer failure")
        if digest is not None and re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("invalid contained-reviewer failure")
        self.code = code
        self.count = count
        self.digest = digest
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ContainedReviewerIdentity:
    executable_sha256: str
    executable_version: str
    sandbox_sha256: str
    sandbox_version: str
    model: str
    reasoning: str
    timeout_seconds: int
    output_bytes: int
    memory_bytes: int
    process_limit: int
    file_bytes: int

    def __post_init__(self) -> None:
        if (
            any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in
                (self.executable_sha256, self.sandbox_sha256))
            or any(type(value) is not str or not value or len(value.encode("utf-8")) > 256
                   for value in (self.executable_version, self.sandbox_version, self.model, self.reasoning))
            or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 600
            or type(self.output_bytes) is not int or not 2 <= self.output_bytes <= 1_048_576
            or type(self.memory_bytes) is not int or not 16_777_216 <= self.memory_bytes <= 8_589_934_592
            or type(self.process_limit) is not int or not 1 <= self.process_limit <= 64
            or type(self.file_bytes) is not int or not 2 <= self.file_bytes <= 1_048_576
            or self.output_bytes > self.file_bytes
        ):
            raise ContainedReviewerError("profile")


@dataclass(frozen=True, slots=True)
class _CoverReviewImage:
    """One opaque, digest-bound PNG input; no caller path or URL is admitted."""

    reference: str
    digest: str
    payload: bytes

    def __post_init__(self) -> None:
        if (
            _COVER_REFERENCE.fullmatch(self.reference) is None
            or _DIGEST.fullmatch(self.digest) is None
            or type(self.payload) is not bytes
            or not 1 <= len(self.payload) <= MAX_IMAGE_BYTES
            or hashlib.sha256(self.payload).hexdigest() != self.digest
            or not self.payload.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise ContainedReviewerError("response_format")


@dataclass(frozen=True, slots=True)
class ContainedReviewRequest:
    """Sanitized image-free input and one closed JSON result schema."""

    instruction: str
    input_document: str
    output_schema: str

    def __post_init__(self) -> None:
        try:
            input_value = json.loads(
                self.input_document,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            schema_value = json.loads(
                self.output_schema,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            if (
                type(self.instruction) is not str
                or not 1 <= len(self.instruction.encode("utf-8")) <= 4096
                or any(ord(value) < 32 and value not in "\n\t" for value in self.instruction)
                or not 2 <= len(self.input_document.encode("utf-8")) <= MAX_INPUT_BYTES
                or not 2 <= len(self.output_schema.encode("utf-8")) <= MAX_SCHEMA_BYTES
                or canonical_json(input_value) != self.input_document
                or canonical_json(schema_value) != self.output_schema
                or type(input_value) is not dict
                or type(schema_value) is not dict
            ):
                raise ValueError
        except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError, ValueError) as error:
            raise ContainedReviewerError("response_format") from error

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json({
            "input": self.input_document,
            "instruction": self.instruction,
            "images": [],
            "output_schema": self.output_schema,
        }).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class _CoverReviewRequest:
    """Fully parsed cover packet plus its exact ordered private images."""

    instruction: str
    input_document: str
    output_schema: str
    images: tuple[_CoverReviewImage, ...]
    _admission: object

    def __new__(cls) -> "_CoverReviewRequest":
        raise TypeError("cover requests require full packet admission")

    @classmethod
    def _admit(
        cls,
        base: ContainedReviewRequest,
        images: tuple[_CoverReviewImage, ...],
    ) -> "_CoverReviewRequest":
        request = object.__new__(cls)
        object.__setattr__(request, "instruction", base.instruction)
        object.__setattr__(request, "input_document", base.input_document)
        object.__setattr__(request, "output_schema", base.output_schema)
        object.__setattr__(request, "images", images)
        object.__setattr__(request, "_admission", _COVER_ADMISSION)
        return request

    @property
    def _is_admitted(self) -> bool:
        return self._admission is _COVER_ADMISSION

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json({
            "input": self.input_document,
            "instruction": self.instruction,
            "images": [
                {"reference": value.reference, "sha256": value.digest}
                for value in self.images
            ],
            "output_schema": self.output_schema,
        }).encode("utf-8")).hexdigest()


def admit_cover_review_request(
    instruction: str,
    packet_document: str,
    output_schema: str,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> _CoverReviewRequest:
    """Fully validate one exact cover-review-packet-v1 and its image mapping."""
    try:
        base = ContainedReviewRequest(instruction, packet_document, output_schema)
        packet = json.loads(
            packet_document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if (
            type(packet) is not dict
            or set(packet) != {"candidates", "omissions", "packet_reference", "schema_version"}
            or packet["schema_version"] != "cover-review-packet-v1"
            or type(packet["candidates"]) is not list
            or not 1 <= len(packet["candidates"]) <= MAX_IMAGE_COUNT
            or type(packet["omissions"]) is not list
            or _PACKET_REFERENCE.fullmatch(packet["packet_reference"]) is None
            or type(image_payloads) is not tuple
            or any(type(value) is not tuple or len(value) != 3 for value in image_payloads)
        ):
            raise ValueError
        candidates = packet["candidates"]
        candidate_bindings = tuple(_validate_candidate(value) for value in candidates)
        supplied_bindings = tuple((value[0], value[1]) for value in image_payloads)
        if (
            supplied_bindings != tuple(value[:2] for value in candidate_bindings)
            or len(set(supplied_bindings)) != len(supplied_bindings)
            or len({value[0] for value in supplied_bindings}) != len(supplied_bindings)
            or len({value[1] for value in supplied_bindings}) != len(supplied_bindings)
        ):
            raise ValueError
        images = tuple(_CoverReviewImage(*value) for value in image_payloads)
        if (
            any(len(image.payload) != candidate[2] for image, candidate in zip(images, candidate_bindings, strict=True))
            or sum(len(value.payload) for value in images) > MAX_IMAGE_TOTAL_BYTES
        ):
            raise ValueError
        _validate_candidate_set(candidates)
        _validate_omissions(packet["omissions"], {value[0] for value in candidate_bindings})
        packet_body = canonical_json({
            "candidates": packet["candidates"],
            "omissions": packet["omissions"],
            "schema_version": packet["schema_version"],
        })
        if packet["packet_reference"] != "cover_packet_" + hashlib.sha256(packet_body.encode()).hexdigest():
            raise ValueError
        return _CoverReviewRequest._admit(base, images)
    except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise ContainedReviewerError("response_format") from error


def _validate_candidate(value: object) -> tuple[str, str, int]:
    if (
        type(value) is not dict
        or set(value) != {
            "height", "incumbent", "media_type", "preview_bytes",
            "preview_sha256", "preview_variant", "provenance", "reference", "width",
        }
        or _COVER_REFERENCE.fullmatch(value["reference"]) is None
        or _DIGEST.fullmatch(value["preview_sha256"]) is None
        or value["media_type"] != "image/png"
        or value["preview_variant"] != "preview-320x480-rgba-png-v1"
        or type(value["preview_bytes"]) is not int
        or not 1 <= value["preview_bytes"] <= MAX_IMAGE_BYTES
        or type(value["width"]) is not int
        or type(value["height"]) is not int
        or not 1 <= value["width"] <= 8192
        or not 1 <= value["height"] <= 8192
        or value["width"] * value["height"] > 16_777_216
        or type(value["incumbent"]) is not bool
        or type(value["provenance"]) is not list
        or not value["provenance"]
    ):
        raise ValueError
    provenance = tuple(_validate_provenance(item) for item in value["provenance"])
    if provenance != tuple(sorted(set(provenance))):
        raise ValueError
    has_incumbent = any(item[0] == "abs" for item in provenance)
    if value["incumbent"] is not has_incumbent:
        raise ValueError
    return value["reference"], value["preview_sha256"], value["preview_bytes"]


def _validate_provenance(value: object) -> tuple[str, str, str, tuple[tuple[str, str], ...]]:
    if (
        type(value) is not dict
        or set(value) != {"context", "designation", "provider", "source_kind"}
        or value["source_kind"] not in _SOURCE_KINDS
        or type(value["context"]) is not list
        or len(value["context"]) > 16
    ):
        raise ValueError
    source_kind = value["source_kind"]
    local = source_kind in {"abs", "epub"}
    if local:
        if value["provider"] is not None or value["designation"] is not None or value["context"]:
            raise ValueError
        return source_kind, "", "", ()
    if (
        type(value["provider"]) is not str
        or _PROVIDER.fullmatch(value["provider"]) is None
        or value["designation"] not in _DESIGNATIONS
    ):
        raise ValueError
    context = tuple(_validate_context(item) for item in value["context"])
    if context != tuple(sorted(set(context))):
        raise ValueError
    return source_kind, value["provider"], value["designation"], context


def _validate_context(value: object) -> tuple[str, str]:
    if (
        type(value) is not dict
        or set(value) != {"field", "value"}
        or value["field"] not in _CONTEXT_FIELDS
        or type(value["value"]) is not str
        or not 1 <= len(value["value"].encode("utf-8")) <= 280
        or value["value"].strip() != value["value"]
        or any(ord(character) < 32 or ord(character) == 127 for character in value["value"])
    ):
        raise ValueError
    return value["field"], value["value"]


def _validate_candidate_set(candidates: list[object]) -> None:
    incumbent_positions = [
        index for index, candidate in enumerate(candidates)
        if type(candidate) is dict and candidate["incumbent"] is True
    ]
    if len(incumbent_positions) > 1 or incumbent_positions not in ([], [0]):
        raise ValueError


def _validate_omissions(omissions: list[object], retained: set[str]) -> None:
    normalized: list[tuple[str, str]] = []
    omitted_references: set[str] = set()
    for omission in omissions:
        if type(omission) is not dict or omission.get("reason") not in _OMISSION_REASONS:
            raise ValueError
        reason = omission["reason"]
        expected = {"reason", "reference", "retained_reference"} if reason == "duplicate_preview" else {"reason", "reference"}
        if set(omission) != expected or _COVER_REFERENCE.fullmatch(omission["reference"]) is None:
            raise ValueError
        if omission["reference"] in retained or omission["reference"] in omitted_references:
            raise ValueError
        omitted_references.add(omission["reference"])
        if reason == "duplicate_preview" and omission["retained_reference"] not in retained:
            raise ValueError
        normalized.append((omission["reference"], reason))
    if normalized != sorted(normalized):
        raise ValueError


@dataclass(frozen=True, slots=True)
class ContainedReviewResult:
    document: str
    digest: str

    def __post_init__(self) -> None:
        try:
            value = json.loads(self.document, object_pairs_hook=_unique_object)
            if (type(value) is not dict or canonical_json(value) != self.document
                    or hashlib.sha256(self.document.encode("utf-8")).hexdigest() != self.digest):
                raise ValueError
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
            raise ContainedReviewerError("malformed_output") from error


class ContainedReviewer(Protocol):
    def run(
        self,
        request: ContainedReviewRequest,
        dispatched: Callable[[int, int, str], None],
    ) -> ContainedReviewResult: ...


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
    "ContainedReviewRequest", "ContainedReviewResult", "ContainedReviewer",
    "ContainedReviewerError", "ContainedReviewerIdentity",
    "admit_cover_review_request", "canonical_json",
]
