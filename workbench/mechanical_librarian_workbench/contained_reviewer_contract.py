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
class ContainedReviewImage:
    """One opaque, digest-bound PNG input; no caller path or URL is admitted."""

    reference: str
    digest: str
    payload: bytes

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"cover_ref_[0-9a-f]{64}", self.reference) is None
            or re.fullmatch(r"[0-9a-f]{64}", self.digest) is None
            or type(self.payload) is not bytes
            or not 1 <= len(self.payload) <= MAX_IMAGE_BYTES
            or hashlib.sha256(self.payload).hexdigest() != self.digest
            or not self.payload.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise ContainedReviewerError("response_format")


@dataclass(frozen=True, slots=True)
class ContainedReviewRequest:
    """Sanitized typed input and one closed JSON result schema."""

    instruction: str
    input_document: str
    output_schema: str
    images: tuple[ContainedReviewImage, ...] = ()

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
                or type(self.images) is not tuple
                or not 0 <= len(self.images) <= MAX_IMAGE_COUNT
                or any(type(value) is not ContainedReviewImage for value in self.images)
                or len({value.reference for value in self.images}) != len(self.images)
                or len({value.digest for value in self.images}) != len(self.images)
                or sum(len(value.payload) for value in self.images) > MAX_IMAGE_TOTAL_BYTES
            ):
                raise ValueError
        except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError, ValueError) as error:
            raise ContainedReviewerError("response_format") from error

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
    "ContainedReviewImage", "ContainedReviewRequest", "ContainedReviewResult", "ContainedReviewer",
    "ContainedReviewerError", "ContainedReviewerIdentity", "canonical_json",
]
