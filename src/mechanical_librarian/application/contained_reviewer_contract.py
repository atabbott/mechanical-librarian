"""Narrow source-workbench port for one contained reviewer invocation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.application.cover_packet_semantics import (
    MAX_COVER_PREVIEW_BYTES as MAX_IMAGE_BYTES,
    bind_cover_packet,
)

MAX_INPUT_BYTES = 1_048_576
MAX_SCHEMA_BYTES = 65_536
COVER_MEMORY_BYTES = 1_073_741_824
COVER_PROCESS_LIMIT = 32
_COVER_REFERENCE = re.compile(r"^cover_transport_ref_[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ContainedReviewerError(RuntimeError):
    """A value-free failure at the contained-reviewer trust boundary."""

    _CODES = frozenset({
        "auth", "cancel", "local_cli_abort", "malformed_output", "profile",
        "response_format", "runtime", "setup", "timeout",
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
        return request

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
    """Fully validate one exact cover-review-packet-v2 and its image mapping."""
    try:
        base = ContainedReviewRequest(instruction, packet_document, output_schema)
        admitted = bind_cover_packet(packet_document, image_payloads)
        images = tuple(_CoverReviewImage(*value) for value in admitted.images)
        return _CoverReviewRequest._admit(base, images)
    except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise ContainedReviewerError("response_format") from error


@dataclass(frozen=True, slots=True)
class ContainedReviewResult:
    document: str
    digest: str
    attempts: tuple["ContainedReviewerAttempt", ...] = ()

    def __post_init__(self) -> None:
        try:
            value = json.loads(self.document, object_pairs_hook=_unique_object)
            if (
                type(value) is not dict
                or canonical_json(value) != self.document
                or hashlib.sha256(self.document.encode("utf-8")).hexdigest()
                != self.digest
                or type(self.attempts) is not tuple
                or len(self.attempts) > 2
                or any(
                    type(attempt) is not ContainedReviewerAttempt
                    for attempt in self.attempts
                )
            ):
                raise ValueError
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as error:
            raise ContainedReviewerError("malformed_output") from error


@dataclass(frozen=True, slots=True)
class ContainedReviewerAttempt:
    dispatched: bool
    failure_code: str | None = None
    diagnostic_bytes: int | None = None
    diagnostic_sha256: str | None = None

    def __post_init__(self) -> None:
        detailed = (self.failure_code, self.diagnostic_bytes, self.diagnostic_sha256)
        if self.dispatched is not True or (
            detailed != (None, None, None)
            and (
                self.failure_code not in ContainedReviewerError._CODES
                or type(self.diagnostic_bytes) is not int
                or not 1 <= self.diagnostic_bytes <= 2_147_483_647
                or type(self.diagnostic_sha256) is not str
                or _DIGEST.fullmatch(self.diagnostic_sha256) is None
            )
        ):
            raise ValueError("invalid contained-reviewer attempt")


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
    "ContainedReviewerAttempt", "ContainedReviewerError", "ContainedReviewerIdentity",
    "admit_cover_review_request", "canonical_json",
]
