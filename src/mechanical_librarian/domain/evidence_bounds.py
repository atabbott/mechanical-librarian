"""Shared bounds and validation for untrusted evidence values."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import TypeVar


MAX_CODE_LENGTH = 64
MAX_SHORT_TEXT_LENGTH = 256
MAX_FIELD_VALUE_LENGTH = 4_096
MAX_FACT_VALUE_LENGTH = 1_024
MAX_EXCERPT_LENGTH = 280
MAX_FIELD_VALUES = 32
MAX_FACTS = 64
MAX_WARNINGS = 32
MAX_EVIDENCE_REFERENCES = 64
MAX_RISKS = 32
MAX_NOTICES = 32
MAX_PACKET_RECORDS = 32
MAX_SERIALIZED_BYTES = 524_288

_BIDI_CONTROLS = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:"
    r"\bauthorization\s*:\s*(?:bearer|basic)\s+\S+"
    r"|\b(?:sk-(?:proj-)?[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{10,}|akia[0-9a-z]{12,})\b"
    r"|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"
    r")"
)
_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"[\"']?([A-Za-z][A-Za-z0-9_-]{1,80})[\"']?\s*[:=]"
)
_MULTIWORD_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:client\s+secret|consumer\s+secret|secret\s+key|"
    r"secret\s+access\s+key|api\s+key|access\s+token|refresh\s+token|"
    r"private\s+key)\s*[:=]"
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FILESYSTEM_URI_PATTERN = re.compile(r"(?i)\b(?:file|smb|nfs|afp)://")
_HOME_PATH_PATTERN = re.compile(r"(?:^|[\s:=;,([{])~[/\\]")
_LABELLED_PATH_PATTERN = re.compile(
    r"(?i)\b(?:path|file|location|source)\s*[:=]\s*(?:~[/\\]|[/\\]|[a-z]:[/\\])"
)
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[\s:=;,([{])/(?:[^\s/:]+/)+[^\s/]*"
)

T = TypeVar("T")


class EvidenceValueError(ValueError):
    """An untrusted value cannot enter the bounded evidence domain."""


def validate_text(value: object, label: str, maximum: int) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise EvidenceValueError(f"{label} must be non-empty trimmed text")
    if len(value) > maximum or len(value.encode("utf-8", errors="surrogatepass")) > maximum * 4:
        raise EvidenceValueError(f"{label} exceeds its size limit")
    if any(_forbidden_codepoint(ord(character)) for character in value):
        raise EvidenceValueError(f"{label} contains forbidden control or Unicode text")
    return value


def validate_code(value: object, label: str) -> str:
    text = validate_text(value, label, MAX_CODE_LENGTH)
    if any(
        not (character.isascii() and (character.isalnum() or character in "._:-"))
        for character in text
    ):
        raise EvidenceValueError(f"{label} must use the bounded code vocabulary")
    return text


def validate_excerpt(value: object, label: str = "evidence excerpt") -> str:
    return validate_projection_text(value, label, MAX_EXCERPT_LENGTH)


def validate_projection_text(value: object, label: str, maximum: int) -> str:
    text = validate_text(value, label, maximum)
    lowered = text.casefold()
    if (
        text.startswith(("/", "\\"))
        or (
            len(text) >= 3
            and text[0].isascii()
            and text[0].isalpha()
            and text[1] == ":"
            and text[2] in "/\\"
        )
        or _FILESYSTEM_URI_PATTERN.search(text)
        or _HOME_PATH_PATTERN.search(text)
        or _LABELLED_PATH_PATTERN.search(text)
        or _ABSOLUTE_PATH_PATTERN.search(text)
        or "/users/" in lowered
        or "/home/" in lowered
        or "../" in text
        or "..\\" in text
    ):
        raise EvidenceValueError(f"{label} must not contain a production path")
    if _credential_shaped(text):
        raise EvidenceValueError(f"{label} must not contain credential-shaped text")
    return text


def validate_tuple(
    values: object,
    *,
    label: str,
    item_type: type[T],
    maximum: int,
    required: bool = False,
    unique: bool = True,
) -> tuple[T, ...]:
    if type(values) is not tuple or any(type(item) is not item_type for item in values):
        raise EvidenceValueError(f"{label} must be an immutable typed tuple")
    if required and not values:
        raise EvidenceValueError(f"{label} must not be empty")
    if len(values) > maximum:
        raise EvidenceValueError(f"{label} exceeds its count limit")
    if unique and len(values) != len(set(values)):
        raise EvidenceValueError(f"{label} must not contain duplicates")
    return values


def validate_string_tuple(
    values: object,
    *,
    label: str,
    maximum: int,
    item_maximum: int,
    codes: bool = False,
    required: bool = False,
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise EvidenceValueError(f"{label} must be an immutable tuple")
    if required and not values:
        raise EvidenceValueError(f"{label} must not be empty")
    if len(values) > maximum:
        raise EvidenceValueError(f"{label} exceeds its count limit")
    validated = tuple(
        validate_code(value, label) if codes else validate_text(value, label, item_maximum)
        for value in values
    )
    if len(validated) != len(set(validated)):
        raise EvidenceValueError(f"{label} must not contain duplicates")
    return validated


def ensure_unique(values: Iterable[object], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise EvidenceValueError(f"{label} must not contain duplicates")


def _forbidden_codepoint(codepoint: int) -> bool:
    return (
        codepoint < 0x20
        or 0x7F <= codepoint <= 0x9F
        or 0xD800 <= codepoint <= 0xDFFF
        or codepoint in _BIDI_CONTROLS
        or codepoint in {0x2028, 0x2029}
        or 0xFDD0 <= codepoint <= 0xFDEF
        or codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
    )


def _credential_shaped(value: str) -> bool:
    if _CREDENTIAL_PATTERN.search(value) or _MULTIWORD_CREDENTIAL_PATTERN.search(value):
        return True
    for match in _CREDENTIAL_ASSIGNMENT_PATTERN.finditer(value):
        raw_label = match.group(1)
        label = _CAMEL_BOUNDARY.sub("_", raw_label).replace("-", "_").casefold()
        parts = tuple(part for part in label.split("_") if part)
        compact = "".join(character for character in label if character.isalnum())
        compound = (
            "_" in raw_label
            or "-" in raw_label
            or _CAMEL_BOUNDARY.search(raw_label) is not None
        )
        structured_prefix = match.start() == 0 or value[match.start() - 1] in "?&{[,;"
        if compact == "secret":
            if structured_prefix:
                return True
            continue
        if compact in {"authorization", "password", "passwd", "token"}:
            return True
        if compact.endswith(
            ("password", "passwd", "secret", "token", "apikey", "accesskey", "privatekey")
        ):
            return True
        if any(marker in compact for marker in ("apikey", "secretaccesskey", "accesskeyid", "privatekey")):
            return True
        if "key" in parts and any(
            part in {"api", "access", "secret", "private"} for part in parts
        ):
            return True
    return False
