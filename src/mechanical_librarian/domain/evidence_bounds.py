"""Shared bounds and validation for untrusted evidence values."""

from __future__ import annotations

import re
from collections.abc import Iterable
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
# What a projection puts where a refused codepoint was, so the shape of the
# value it was read from survives without the codepoint doing.
REPLACEMENT_CHARACTER = "\ufffd"

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
_FORBIDDEN_CODEPOINTS = re.compile(
    "[\x00-\x1f\x7f-\x9f\ud800-\udfff\u2028\u2029\ufdd0-\ufdef"
    + "".join(map(chr, sorted(_BIDI_CONTROLS)))
    + "".join(chr(plane << 16 | low) for plane in range(17) for low in (0xFFFE, 0xFFFF))
    + "]"
)
# The same set's ASCII members: for ASCII text it decides exactly what the
# whole class does, at a fraction of the scan (#1202).
_FORBIDDEN_ASCII_CODEPOINTS = re.compile("[\x00-\x1f\x7f]")
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:"
    r"\bauthorization\s*:\s*(?:bearer|basic)\s+\S+"
    r"|\b(?:sk-(?:proj-)?[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{10,}|akia[0-9a-z]{12,})\b"
    r"|\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"
    r")"
)
_BARE_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+([a-z0-9._~+/=-]{8,})(?=\s|$)")
_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"[\"']?([A-Za-z][A-Za-z0-9_.:-]{1,80})[\"']?\s*([:=])"
)
_MULTIWORD_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:client\s+secret|consumer\s+secret|secret\s+key|"
    r"secret\s+access\s+key|api\s+key|access\s+token|refresh\s+token|"
    r"private\s+key)\s*[:=]"
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CREDENTIAL_LABEL_SEPARATOR = re.compile(r"[._:-]+")
_FILESYSTEM_URI_PATTERN = re.compile(r"(?i)\b(?:file|smb|nfs|afp)://")
_HOME_PATH_PATTERN = re.compile(r"(?<!\w)~[/\\]")
_LABELLED_PATH_PATTERN = re.compile(
    r"(?i)\b(?:path|file|location|source)\s*[:=]\s*(?:~[/\\]|[/\\]|[a-z]:[/\\])"
)
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w/~])/(?!/)[^\s/:]+(?:/[^\s/:]+)*")
# An attribute-free closing or self-closing tag (`</p>`, `<br />`): its slash
# is markup, not a path root, in an incumbent description only (#1251).
_HTML_TAG_SLASH_PATTERN = re.compile(
    r"<(/)?([a-z][a-z0-9]*)\s*(?(1)|/)>", re.ASCII | re.IGNORECASE
)
# Windows-1252 text decoded as Latin-1 leaves these; an incumbent description
# holding them is refused with its own reason, never repaired (#1251).
_C1_CONTROLS = re.compile("[\x80-\x9f]")
_WINDOWS_DRIVE_ROOT_PATTERN = re.compile(r"(?i)(?<![a-z0-9_])[a-z]:[/\\]")
_UNC_PATH_PATTERN = re.compile(r"(?<![\w\\])\\\\[^\\\s]+\\[^\\\s]+")
_COOKIE_HEADER_PATTERN = re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:")
_SENSITIVE_DIAGNOSTIC_PATTERN = re.compile(
    r"(?i)\b(?:api[ _-]?key|client[ _-]?secret|access[ _-]?token|"
    r"refresh[ _-]?token|session[ _-]?token|password|passwd)\b\s*(?:[:=]|\s)\s*\S+"
)
_SOURCE_TEXT_SENTINEL = re.compile(
    r"(?:unsafe|oversized)-source-text-sha256:[0-9a-f]{64}"
)
_SOURCE_TEXT_SENTINEL_PREFIX = re.compile(r"(?:unsafe|oversized)-source-text-sha256:")

T = TypeVar("T")


class EvidenceValueError(ValueError):
    """An untrusted value cannot enter the bounded evidence domain."""


def validate_text(value: object, label: str, maximum: int) -> str:
    if type(value) is not str or not value or value.strip() != value:
        raise EvidenceValueError(f"{label} must be non-empty trimmed text")
    if (
        len(value) > maximum
        or len(value.encode("utf-8", errors="surrogatepass")) > maximum * 4
    ):
        raise EvidenceValueError(f"{label} exceeds its size limit")
    if (
        _FORBIDDEN_ASCII_CODEPOINTS if value.isascii() else _FORBIDDEN_CODEPOINTS
    ).search(value):
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
    return _validate_projection_safety(text, label)


def validate_incumbent_description(value: object, label: str, maximum: int) -> str:
    """Admit exact paragraph text without changing scalar or provider contracts."""
    if type(value) is not str or not value.strip():
        raise EvidenceValueError(f"{label} must be non-empty text")
    if (
        len(value) > maximum
        or len(value.encode("utf-8", errors="surrogatepass")) > maximum * 4
    ):
        raise EvidenceValueError(f"{label} exceeds its size limit")
    # CRLF folds before U+2028 does, so `\r\u2028` still exposes a lone CR;
    # ASCII text holds neither U+2028 nor C1 and keeps #1202's scan (#1251).
    controls = value.replace("\r\n", "\n")
    mojibake = False
    if not controls.isascii():
        controls = controls.replace("\u2028", "\n")
        mojibake = _C1_CONTROLS.search(controls) is not None
        if mojibake:
            controls = _C1_CONTROLS.sub(REPLACEMENT_CHARACTER, controls)
    controls = controls.strip(" \t\n")
    if controls != controls.strip() or _FORBIDDEN_CODEPOINTS.search(
        controls.replace("\n", " ")
    ):
        raise EvidenceValueError(f"{label} contains forbidden control or Unicode text")
    if mojibake:
        raise EvidenceValueError(f"{label} contains C1 control text")
    _validate_projection_safety(
        value.replace("\u2028", "\n").strip(" \t\r\n"), label, markup=True
    )
    return value


def _validate_projection_safety(text: str, label: str, *, markup: bool = False) -> str:
    lowered = text.casefold()
    # Each pattern below can match only text holding the literal its guard
    # names, and no other codepoint matches that literal under the pattern's
    # flags, so a guard decides nothing and only skips a scan that would fail
    # (#1202).
    assignment = ":" in text or "=" in text
    # Only the absolute-path rule can read a tag slash as a root, so only it
    # sees the tags rewritten; every other path rule reads the original (#1251).
    rooted = (
        _HTML_TAG_SLASH_PATTERN.sub(r"<\2>", text) if markup and "<" in text else text
    )
    if (
        text.startswith("\\")
        or (
            len(text) >= 3
            and text[0].isascii()
            and text[0].isalpha()
            and text[1] == ":"
            and text[2] in "/\\"
        )
        or ("://" in text and _FILESYSTEM_URI_PATTERN.search(text))
        or ("~" in text and _HOME_PATH_PATTERN.search(text))
        or (assignment and _LABELLED_PATH_PATTERN.search(text))
        or ("/" in rooted and _ABSOLUTE_PATH_PATTERN.search(rooted))
        or (":" in text and _WINDOWS_DRIVE_ROOT_PATTERN.search(text))
        or ("\\\\" in text and _UNC_PATH_PATTERN.search(text))
        or "/users/" in lowered
        or "/home/" in lowered
        or "../" in text
        or "..\\" in text
    ):
        raise EvidenceValueError(f"{label} must not contain a production path")
    # A tag can split a credential URL's userinfo, so markup text is also
    # scanned with its tags rewritten; that only ever adds refusals (#1251).
    if _credential_shaped(text) or (rooted is not text and _credential_shaped(rooted)):
        raise EvidenceValueError(f"{label} must not contain credential-shaped text")
    return text


def replace_forbidden_codepoints(value: str) -> str:
    """Substitute U+FFFD for every codepoint the validators above refuse.

    The refusal set is this module's, so a caller that has to keep a value the
    validators would reject asks for the substitution here rather than
    restating which codepoints are forbidden and drifting from them.  This
    loosens nothing: the projected text is still handed to a validator, which
    still refuses it whole if anything else about it is inadmissible.
    """

    return _FORBIDDEN_CODEPOINTS.sub(REPLACEMENT_CHARACTER, value)


def validate_role_projection(value: object, label: str = "contributor role") -> str:
    """Accept a bounded public role or one exact source-text sentinel."""

    if type(value) is not str:
        raise EvidenceValueError(f"{label} must be text")
    if _SOURCE_TEXT_SENTINEL.fullmatch(value):
        return value
    if _SOURCE_TEXT_SENTINEL_PREFIX.match(value):
        raise EvidenceValueError(f"{label} has a malformed source-text sentinel")
    return validate_projection_text(value, label, 64)


def validate_safe_diagnostic_text(value: object, label: str, maximum: int) -> str:
    """Reject secrets, cookies, credential-bearing URLs, and private paths.

    Diagnostics are a stricter projection boundary shared by application ports;
    callers receive only the validated public-safe text, never a redacted copy
    that could conceal a changed operational meaning.
    """

    text = validate_projection_text(value, label, maximum)
    if _COOKIE_HEADER_PATTERN.search(text) or _SENSITIVE_DIAGNOSTIC_PATTERN.search(
        text
    ):
        raise EvidenceValueError(
            f"{label} must not contain credential or cookie material"
        )
    return text


def validate_persisted_fact_text(value: object, label: str, maximum: int) -> str:
    """Reject credential-shaped facts while retaining private path evidence."""

    text = validate_text(value, label, maximum)
    if _credential_shaped(text):
        raise EvidenceValueError(f"{label} must not contain credential-shaped text")
    return text


def validate_persisted_fact_code(value: object, label: str) -> str:
    """Reject high-confidence credential labels before fact persistence."""

    code = validate_code(value, label)
    if _credential_label_shaped(code, reject_exact_secret=True):
        raise EvidenceValueError(f"{label} must not use a credential label")
    return code


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
        validate_code(value, label)
        if codes
        else validate_text(value, label, item_maximum)
        for value in values
    )
    if len(validated) != len(set(validated)):
        raise EvidenceValueError(f"{label} must not contain duplicates")
    return validated


def ensure_unique(values: Iterable[object], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise EvidenceValueError(f"{label} must not contain duplicates")


def _credential_shaped(value: str) -> bool:
    # Both labelled forms end in a literal `:` or `=`; see the guards above.
    assignment = ":" in value or "=" in value
    if _CREDENTIAL_PATTERN.search(value) or (
        assignment and _MULTIWORD_CREDENTIAL_PATTERN.search(value)
    ):
        return True
    bare_bearer = _BARE_BEARER_PATTERN.search(value)
    if bare_bearer is not None:
        credential = bare_bearer.group(1)
        if (
            any(
                character.isdigit() or character in "_~+/=-" for character in credential
            )
            or credential.count(".") >= 2
        ):
            return True
    if not assignment:
        return False
    for match in _CREDENTIAL_ASSIGNMENT_PATTERN.finditer(value):
        raw_label = match.group(1)
        delimiter = match.group(2)
        structured_prefix = match.start() == 0 or (
            match.start() > 0 and value[match.start() - 1] in "?&{[,;"
        )
        if _credential_label_shaped(
            raw_label,
            reject_exact_secret=delimiter == "=" or structured_prefix,
        ):
            return True
    return False


def _credential_label_shaped(raw_label: str, *, reject_exact_secret: bool) -> bool:
    label = _CREDENTIAL_LABEL_SEPARATOR.sub(
        "_", _CAMEL_BOUNDARY.sub("_", raw_label)
    ).casefold()
    parts = tuple(part for part in label.split("_") if part)
    compact = "".join(character for character in label if character.isalnum())
    if compact in {"credential", "credentials"}:
        return True
    if compact == "secret":
        return reject_exact_secret
    if compact in {"authorization", "password", "passwd", "token"}:
        return True
    if compact.endswith(
        ("password", "passwd", "secret", "token", "apikey", "accesskey", "privatekey")
    ):
        return True
    if any(
        marker in compact
        for marker in ("apikey", "secretaccesskey", "accesskeyid", "privatekey")
    ):
        return True
    return "key" in parts and any(
        part in {"api", "access", "secret", "private"} for part in parts
    )
