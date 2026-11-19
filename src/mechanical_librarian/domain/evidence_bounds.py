"""Shared bounds and validation for untrusted evidence values."""

from __future__ import annotations

import html
import re

MAX_CODE_LENGTH = 64
MAX_SHORT_TEXT_LENGTH = 256
MAX_FIELD_VALUE_LENGTH = 4_096
MAX_FACTS = 64
MAX_WARNINGS = 32
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
# Any tag-like span, opening or with attributes: markup text must also pass the
# default scan with every such span removed, read both plainly and with quoted
# attribute values (which may hold `>`) kept inside their tag (#1251).
_TAG_SPAN_PATTERN = re.compile(r"<[^<>]*>")
_QUOTED_TAG_SPAN_PATTERN = re.compile(r"""<(?:[^<>"']|"[^"]*"|'[^']*')*>""")
# Windows-1252 text decoded as Latin-1 leaves these; an incumbent description
# holding them is refused with its own reason, never repaired (#1251).
_C1_CONTROLS = re.compile("[\x80-\x9f]")
_WINDOWS_DRIVE_ROOT_PATTERN = re.compile(r"(?i)(?<![a-z0-9_])[a-z]:[/\\]")
_UNC_PATH_PATTERN = re.compile(r"(?<![\w\\])\\\\[^\\\s]+\\[^\\\s]+")
_SOURCE_TEXT_SENTINEL = re.compile(
    r"(?:unsafe|oversized)-source-text-sha256:[0-9a-f]{64}"
)
_SOURCE_TEXT_SENTINEL_PREFIX = re.compile(r"(?:unsafe|oversized)-source-text-sha256:")


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


# The tags a formatted description copy is written with (ADR-0107), each
# bare: a copy never carries an attribute.
_FORMATTED_TAG_PATTERN = re.compile(r"</?(?:p|br|ul|ol|li|b|strong|i|em|del)>")


def validate_formatted_description(value: object, label: str, maximum: int) -> str:
    """Admit one formatted copy of a description (#1995, ADR-0107).

    It passes the incumbent description checks, which also read it with its
    tags removed, carries at least one tag, and every tag it carries is a
    bare tag of the closed set: no other `<` or `>` is left once they are
    removed, since the copy's text is escaped.
    """
    text = validate_incumbent_description(value, label, maximum)
    bare = _FORMATTED_TAG_PATTERN.sub("", text)
    if bare == text or "<" in bare or ">" in bare or not _well_nested(text):
        raise EvidenceValueError(f"{label} is not a formatted copy")
    return text


# A copy is never nested deeper than this, and markup nested deeper has none.
MAX_FORMATTED_DEPTH = 32
_FORMATTED_TOKEN_PATTERN = re.compile(r"<(/?)(p|br|ul|ol|li|b|strong|i|em|del)>|[^<]+")
_FORMATTED_INLINE = frozenset({"b", "strong", "i", "em", "del"})
# What each place in a copy may open: a list holds only items, a paragraph
# or inline element only inline elements, and the top level or an item any
# paragraph, list or inline element.
_FORMATTED_OPENS = {
    "flow": _FORMATTED_INLINE | {"p", "ul", "ol"},
    "inline": _FORMATTED_INLINE,
    "list": frozenset({"li"}),
}


def _formatted_context(tag: str | None) -> str:
    if tag in {"ul", "ol"}:
        return "list"
    return "flow" if tag in {None, "li"} else "inline"


def _well_nested(copy: str) -> bool:
    """Whether a copy of bare tags nests as the serialiser writes one.

    Every element closes in order and shows some text, a line break is never
    closed and never sits directly in a list, a list holds no text, and no
    element is nested deeper than the copy's bound.
    """
    # Each open element, and whether any text inside it shows yet.
    stack: list[str] = []
    shown: list[bool] = []
    for match in _FORMATTED_TOKEN_PATTERN.finditer(copy):
        closing, name = match.group(1), match.group(2)
        context = _formatted_context(stack[-1] if stack else None)
        if name is None:
            if context == "list":
                return False
            if match.group(0).strip():
                shown = [True] * len(stack)
        elif name == "br":
            if closing or context == "list":
                return False
        elif closing:
            if not stack or stack[-1] != name or not shown[-1]:
                return False
            stack.pop()
            shown.pop()
        elif name in _FORMATTED_OPENS[context] and len(stack) < MAX_FORMATTED_DEPTH:
            stack.append(name)
            shown.append(False)
        else:
            return False
    return not stack


_FORMATTED_BLOCK_PATTERN = re.compile(r"</?(?:p|br|ul|ol|li)>")
_FORMATTED_INLINE_PATTERN = re.compile(r"</?(?:b|strong|i|em|del)>")


def formatted_description_text(copy: str) -> str:
    """The visible text an admitted formatted copy reads as (#1995, ADR-0107).

    The provider renderer's own rule for a copy's bare tags: each block tag
    ends a line, an inline tag shows nothing, character references decode,
    and each line's words join with " • ". A holder admits a copy only when
    this equals the visible text it keeps beside it.
    """
    lines = html.unescape(
        _FORMATTED_INLINE_PATTERN.sub("", _FORMATTED_BLOCK_PATTERN.sub("\n", copy))
    )
    return " • ".join(
        " ".join(line.split()) for line in lines.splitlines() if line.split()
    )


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
    if rooted is not text:
        # Any tag can also split a credential, a label or a non-absolute path,
        # or hide a start-anchored label behind a space, so the text with every
        # tag-like span removed and its edges stripped must pass the whole
        # default scan, with spans read both plainly and quote-aware (a quoted
        # `>` or an unbalanced quote defeats either alone); that only ever adds
        # refusals (#1251).
        for spans in (_TAG_SPAN_PATTERN, _QUOTED_TAG_SPAN_PATTERN):
            _validate_projection_safety(spans.sub("", text).strip(" \t\r\n"), label)
    if _credential_shaped(text):
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
