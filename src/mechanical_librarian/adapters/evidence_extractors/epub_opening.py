"""Bounded EPUB opening-resource selection, projection, and claim extraction."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import time
import unicodedata
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree
from xml.parsers import expat

from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubInspectionOutcome,
    EpubObservationApplicability,
    EpubPackageCapability,
    EpubPackageDeclarationKind,
    EpubPackageReport,
    EpubPackageRequest,
    EpubPackageSourceError,
    epub_file_lineage,
    epub_resource_identity,
)
from mechanical_librarian.domain.epub_inspection import (
    DateEvent,
    DatePrecision,
    normalize_date_value,
)
from mechanical_librarian.domain.epub_opening import (
    EpubOpeningLimits,
    EpubOpeningReport,
    EpubOpeningUsage,
    OpeningCitation,
    OpeningClaim,
    OpeningField,
    OpeningParseMode,
    OpeningResourceDisposition,
    OpeningResourceResult,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)

_CONTENT_MEDIA = {"application/xhtml+xml", "text/html"}
_IMAGE_MEDIA = {"image/gif", "image/jpeg", "image/png", "image/svg+xml"}
_ACTIVE = {"script", "style", "template", "object", "iframe", "frame", "embed", "canvas"}
_EPUB_NS = "http://www.idpf.org/2007/ops"
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_OPF_NS = "http://www.idpf.org/2007/opf"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_UNKNOWN_NS = "urn:mechanical-librarian:unknown-prefix"
_SVG_NS = "http://www.w3.org/2000/svg"
_MATHML_NS = "http://www.w3.org/1998/Math/MathML"
_XINCLUDE_NS = "http://www.w3.org/2001/XInclude"
_XSLT_NS = "http://www.w3.org/1999/XSL/Transform"
_HTML_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_HTML_IMPLIED_CLOSE = {
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "p": {"address", "article", "aside", "blockquote", "div", "dl", "fieldset", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "menu", "nav", "ol", "p", "pre", "section", "table", "ul"},
    "tr": {"tr"},
    "th": {"th", "td"},
    "td": {"th", "td"},
}
_EXCLUDED_SEMANTICS = {"preview", "sample", "advertisement"}
_OPENING_SEMANTICS = {
    "titlepage", "halftitlepage", "copyright-page", "dedication", "contributors",
    "imprint", "frontmatter", "seriespage", "other-credits",
}
_SOURCE_RANK = {
    "epub3_landmark": 0,
    "epub2_guide": 1,
    "linear_spine_fallback": 2,
}
_ROLE_ORDER = {
    role: index
    for index, role in enumerate(
        (
            "titlepage", "title_page", "halftitlepage", "copyright-page",
            "copyright_page", "contributors", "other-credits", "imprint",
            "seriespage", "dedication", "acknowledgements", "preface",
            "foreword", "frontmatter", "other.ms_firstpage",
        )
    )
}
_BODY_SEMANTICS = {"bodymatter"}
_CLAIM_SEMANTICS = {"title", "fulltitle", "subtitle", "publisher", "imprint", "contributor", "creator"}
_CLAIM_ELEMENTS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "dt", "dd", "li",
    "address", "figcaption",
}
_BIBLIOGRAPHIC_INDICIA_LABEL = re.compile(
    r"^(?P<label>subtitle|publisher|imprint)\s*:\s*(?P<value>.+)$",
    re.I,
)
_BIBLIOGRAPHIC_INDICIA_LABEL_CUE = re.compile(
    r"(?P<label>subtitle|publisher|imprint)\s*:\s*",
    re.I,
)
# One past the immutable 64-fact cap is sufficient to prove exhaustion.
_BIBLIOGRAPHIC_INDICIA_PER_ELEMENT = 65
_NON_BIBLIOGRAPHIC_INDICIA = re.compile(
    r"^(?:(?:©|\(c\))\s*"
    r"|copyright(?:(?:\s+|-)holder\b"
    r"|\s*(?:©|\(c\))"
    r"|\s+(?:18|19|20|21)\d{2}\b"
    r"|\s+by\b"
    r"|\s*[:\-])"
    r"|(?:printed|printing|distributed|retailed|converted)\s+by\b"
    r"|(?:epub|e-?book|digital)\s+conversion\s+by\b"
    r"|(?:printer|distributor|retailer|conversion)\s*[:\-])",
    re.I,
)
_SPACE = re.compile(r"\s+")
_ISBN = re.compile(r"(?<![0-9Xx])(?:97[89][\s-]*)?[0-9][0-9Xx\s-]{8,20}[0-9Xx](?![0-9Xx])")
_DATE_TEXT = (
    r"(?<![\w/:+\-])(?:18|19|20|21)\d{2}"
    r"(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?"
    r"(?:T(?:[01]\d|2[0-3]):[0-5]\d"
    r"(?::[0-5]\d(?:\.\d{1,6})?)?"
    r"(?:Z|[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00)))?"
    r"(?![\w/:+\-]|\.\d)"
)
_DATE = re.compile(_DATE_TEXT)
_WORK_FIRST_DATE_CUE_TEXT = (
    r"(?:\bfirst\s+published\b|\boriginally\s+published\b"
    r"|\boriginal\s+publication(?:\s+date)?\b)"
)
_SELECTED_EDITION_DATE_CUE_TEXT = (
    r"(?:\bthis\s+edition\b|\b(?:this\s+)?"
    r"(?:e-?book|electronic|digital|epub)(?:\s+edition)?\b)"
)
_MIXED_DATE_GAP = r"[^.;|\n]{0,96}?"
_MIXED_EDITION_DATE_PATTERNS = tuple(
    re.compile(_MIXED_DATE_GAP.join(order), re.I)
    for order in (
        (
            _WORK_FIRST_DATE_CUE_TEXT,
            _SELECTED_EDITION_DATE_CUE_TEXT,
            _DATE_TEXT,
        ),
        (
            _WORK_FIRST_DATE_CUE_TEXT,
            _DATE_TEXT,
            _SELECTED_EDITION_DATE_CUE_TEXT,
        ),
        (
            _SELECTED_EDITION_DATE_CUE_TEXT,
            _WORK_FIRST_DATE_CUE_TEXT,
            _DATE_TEXT,
        ),
        (
            _SELECTED_EDITION_DATE_CUE_TEXT,
            _DATE_TEXT,
            _WORK_FIRST_DATE_CUE_TEXT,
        ),
        (
            _DATE_TEXT,
            _WORK_FIRST_DATE_CUE_TEXT,
            _SELECTED_EDITION_DATE_CUE_TEXT,
        ),
        (
            _DATE_TEXT,
            _SELECTED_EDITION_DATE_CUE_TEXT,
            _WORK_FIRST_DATE_CUE_TEXT,
        ),
    )
)
_DATE_EVENT_PATTERNS = (
    (
        DateEvent.MANIFESTATION_PUBLICATION,
        re.compile(
            rf"(?:\b(?:this\s+)?(?:e-?book|electronic|digital|epub)"
            rf"(?:\s+edition)?\s+(?:was\s+)?published"
            rf"|\b(?:e-?book|electronic|digital|epub)"
            rf"(?:\s+edition)?\s+publication\s+date\s*[:\-]?"
            rf"|\bthis\s+edition\s+(?:was\s+)?published"
            rf"|\bpublication\s+date\s*(?:for\s+)?(?:this\s+)?"
            rf"(?:e-?book|electronic|digital|epub)(?:\s+edition)?\s*[:\-]?)"
            rf"[^.;|\n]{{0,96}}?(?P<date>{_DATE_TEXT})",
            re.I,
        ),
    ),
    (
        DateEvent.WORK_FIRST_PUBLICATION,
        re.compile(
            rf"(?:\bfirst\s+published|\boriginally\s+published"
            rf"|\boriginal\s+publication(?:\s+date)?\s*[:\-]?)"
            rf"[^.;|\n]{{0,96}}?(?P<date>{_DATE_TEXT})",
            re.I,
        ),
    ),
    (
        DateEvent.COPYRIGHT,
        re.compile(
            rf"(?:\bcopyright(?:ed)?\b|©)[^.;|\n]{{0,96}}?"
            rf"(?P<date>{_DATE_TEXT})",
            re.I,
        ),
    ),
    (
        DateEvent.CREATION,
        re.compile(
            rf"(?:\bcreated\b|\bcreation\s+date\s*[:\-]?)"
            rf"[^.;|\n]{{0,96}}?(?P<date>{_DATE_TEXT})",
            re.I,
        ),
    ),
    (
        DateEvent.MODIFICATION,
        re.compile(
            rf"(?:\blast\s+modified\b|\bmodified\b"
            rf"|\bmodification\s+date\s*[:\-]?)"
            rf"[^.;|\n]{{0,96}}?(?P<date>{_DATE_TEXT})",
            re.I,
        ),
    ),
    (
        DateEvent.CONVERSION,
        re.compile(
            rf"(?:\bconverted\b|\bconversion\s+date\s*[:\-]?)"
            rf"[^.;|\n]{{0,96}}?(?P<date>{_DATE_TEXT})",
            re.I,
        ),
    ),
)
_BIBLIOGRAPHIC_CREDIT_CUE_TEXT = (
    r"(?:translated|translation|illustrated|illustrations|edited|foreword"
    r"|introduction|(?:book|cover|interior)\s+design|photographs?"
    r"|photography|maps?|cartography|adapted)\s+by"
)
_CONTEXTUAL_CREDIT_CUE_TEXT = (
    r"(?:(?:epub|e-?book|digital)\s+conversion\s+by|converted\s+by"
    r"|scann(?:ed|ing)\s+by|(?:produced|created)\s+(?:using|with)"
    r"|(?:distributed|retailed)\s+by|workflow\s*[:\-])"
)
_CREDIT_CUE_TEXT = (
    rf"(?:{_BIBLIOGRAPHIC_CREDIT_CUE_TEXT}|{_CONTEXTUAL_CREDIT_CUE_TEXT})"
)
_CREDIT_NAME_TEXT = (
    rf"(?P<name>[^;|\n]+?)(?="
    rf"(?:\s*\.\s*|\s+and\s+){_CREDIT_CUE_TEXT}\b|[;|\n]|$)"
)
_BIBLIOGRAPHIC_CREDITS = (
    (
        re.compile(
            rf"\b(?:translated|translation)\s+and\s+"
            rf"(?:illustrated|illustrations)\s+by\s+{_CREDIT_NAME_TEXT}",
            re.I,
        ),
        ("translator", "illustrator"),
        EpubObservationApplicability.EDITION,
    ),
    (re.compile(rf"\b(?:translated|translation)\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("translator",), EpubObservationApplicability.EDITION),
    (re.compile(rf"\b(?:illustrated|illustrations)\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("illustrator",), EpubObservationApplicability.EDITION),
    (re.compile(rf"\bedited\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("editor",), EpubObservationApplicability.WORK),
    (re.compile(rf"\b(?:foreword|introduction)\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("foreword",), EpubObservationApplicability.WORK),
    (re.compile(rf"\b(?:book|cover|interior)\s+design\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("designer",), EpubObservationApplicability.EDITION),
    (re.compile(rf"\b(?:photographs?|photography)\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("photographer",), EpubObservationApplicability.EDITION),
    (re.compile(rf"\b(?:maps?|cartography)\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("cartographer",), EpubObservationApplicability.EDITION),
    (re.compile(rf"\badapted\s+by\s+{_CREDIT_NAME_TEXT}", re.I), ("adapter",), EpubObservationApplicability.WORK),
)
_CONTEXTUAL_CREDITS = (
    (re.compile(rf"\b(?:epub|e-?book|digital)\s+conversion\s+by\s+{_CREDIT_NAME_TEXT}", re.I), "conversion"),
    (re.compile(rf"\bconverted\s+by\s+{_CREDIT_NAME_TEXT}", re.I), "conversion"),
    (re.compile(rf"\bscann(?:ed|ing)\s+by\s+{_CREDIT_NAME_TEXT}", re.I), "scanner"),
    (re.compile(rf"\b(?:produced|created)\s+(?:using|with)\s+{_CREDIT_NAME_TEXT}", re.I), "software"),
    (re.compile(rf"\b(?:distributed|retailed)\s+by\s+{_CREDIT_NAME_TEXT}", re.I), "retailer"),
    (re.compile(rf"\bworkflow\s*[:\-]\s*{_CREDIT_NAME_TEXT}", re.I), "workflow"),
)
_SERIES = re.compile(r"\b(?:book|volume)\s+([0-9]+(?:\.[0-9]+)?)\s+(?:of|in)\s+(?:the\s+)?(.+)", re.I)
_EBOOK_LABEL = re.compile(
    r"(?<![\w])(?:e[\s-]?book|epub|electronic|digital)(?![\w])",
    re.I,
)
_PRINT_LABEL = re.compile(
    r"(?<![\w])(?:paperback|hardback|hardcover|print\s+isbn)(?![\w])",
    re.I,
)
_PAPERBACK_LABEL = re.compile(r"(?<![\w])paperback(?![\w])", re.I)
_HARDCOVER_LABEL = re.compile(
    r"(?<![\w])(?:hardback|hardcover)(?![\w])", re.I
)
_NON_ISBN_IDENTIFIER = re.compile(
    r"(?i)(?P<scheme>urn:isbn|[a-z][a-z0-9+.-]{0,31}):\s*(?://)?[^\s|]+"
)
_INDICIA_PROMOTION_NOTICE = re.compile(
    r"^\s*(?:advertisement|preview(?:\s+of\b.*)?|"
    r"sample(?:\s+(?:chapter|excerpt|of)\b.*)?|"
    r"also by(?:\b.*)?|other works? by(?:\b.*)?)\s*[:.!]?\s*$",
    re.I,
)
_CHARSET_PARAMETER = re.compile(
    rb"(?:^|;)\s*charset\s*=\s*([A-Za-z0-9._-]+)(?:\s*;|\s*$)", re.I
)
_SUPPORTED_CODECS = {
    "utf-8": "utf-8-sig",
    "utf8": "utf-8-sig",
    "windows-1252": "windows-1252",
    "cp1252": "windows-1252",
    "iso-8859-1": "iso-8859-1",
    "latin-1": "iso-8859-1",
}
_PACKAGE_WARNING_CODES = {
    "invalid_navigation_declaration",
    "missing_manifest_resource",
    "duplicate_manifest_resource",
    "invalid_manifest_item",
    "invalid_manifest_fallback",
    "invalid_spine_item",
    "invalid_ncx_declaration",
    "invalid_guide_reference",
    "opaque_resource_encrypted",
}


class _AggregateRejected(Exception):
    def __init__(
        self,
        outcome: EpubInspectionOutcome,
        partial_facts: tuple[object, ...] = (),
    ) -> None:
        self.outcome = outcome
        self.partial_facts = partial_facts


class _ResourceRejected(Exception):
    """One optional resource exceeded a per-resource bound."""

    def __init__(self, *, nodes: int = 0, text_characters: int = 0) -> None:
        self.nodes = nodes
        self.text_characters = text_characters


class _MalformedMarkup(ValueError):
    """One parser rejected markup after consuming bounded logical work."""

    def __init__(self, message: str, *, nodes: int, text_characters: int) -> None:
        super().__init__(message)
        self.nodes = nodes
        self.text_characters = text_characters


class _UnsafeConstruct(_MalformedMarkup):
    """Markup that recovery must never reinterpret."""


class _UnsupportedEncoding(_MalformedMarkup):
    """Markup declared an encoding outside the closed recovery profile."""


@dataclass
class _Element:
    ordinal: int
    name: str
    attributes: dict[str, str]
    semantics: set[str]
    depth: int
    text_parts: list[str] = field(default_factory=list)
    start: int = 0
    end: int = 0
    exact_text: str = ""
    active: bool = False
    ancestors: tuple[int, ...] = ()
    namespaces: dict[str, str] = field(default_factory=dict)
    namespace: str = ""
    fragment_ids: tuple[str, ...] = ()
    invalid_attributes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _BibliographicIndicia:
    field: OpeningField
    value: str
    context_span: tuple[int, int] | None = None


@dataclass(frozen=True)
class _BibliographicIndiciaAllocation:
    owner_ordinal: int
    emitter_ordinal: int


@dataclass
class _Projection:
    mode: OpeningParseMode
    elements: list[_Element]
    text: str
    nodes: int
    maximum_depth: int
    warnings: tuple[str, ...]
    work_nodes: int
    work_text_characters: int


@dataclass(frozen=True)
class _RoleAssociation:
    identity: str
    source_tier: str
    role: str
    fragment: str | None
    selection_path: tuple[str, ...]
    order: int


@dataclass(frozen=True)
class _Candidate:
    identity: str
    media_type: str
    selection_path: tuple[str, ...]
    order: int
    target_fragment: str | None = None
    recognition_semantics: frozenset[str] = frozenset()
    opening_boundaries: frozenset[str | None] = frozenset()
    role_associations: tuple[_RoleAssociation, ...] = ()


@dataclass(frozen=True)
class _RecognitionContext:
    selection_path: tuple[str, ...]
    recognition_semantics: frozenset[str]
    title_scopes: tuple[int | None, ...]
    body_ordinal: int | None
    body_scopes: tuple[int, ...]
    excluded_scopes: tuple[int | None, ...]
    warnings: tuple[str, ...]
    suppress_claims: bool = False


class _SourceIO(io.RawIOBase):
    def __init__(self, request: EpubPackageRequest) -> None:
        self._request = request
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        _check(self._request)
        length = self._request.source.length
        position = offset if whence == io.SEEK_SET else self._position + offset if whence == io.SEEK_CUR else length + offset if whence == io.SEEK_END else -1
        if not 0 <= position <= length:
            raise OSError("bounded EPUB source seek is invalid")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        _check(self._request)
        remaining = self._request.source.length - self._position
        amount = remaining if size is None or size < 0 else min(size, remaining)
        value = self._request.source.read_at(self._position, amount)
        self._position += len(value)
        _check(self._request)
        return value


def _check(request: EpubPackageRequest) -> None:
    if request.cancellation.is_cancelled():
        raise _AggregateRejected(EpubInspectionOutcome.CANCELLATION)
    if time.monotonic() >= request.monotonic_deadline:
        raise _AggregateRejected(EpubInspectionOutcome.TIMEOUT)


def _tokens(attributes: dict[str, str]) -> set[str]:
    value = attributes.get(f"{{{_EPUB_NS}}}type", attributes.get("epub:type", ""))
    return {token.casefold() for token in value.split() if ":" not in token}


def _is_html_namespace(namespace: str) -> bool:
    return namespace in {"", _XHTML_NS}


def _same_namespace_region(left: str, right: str) -> bool:
    return left == right or (
        _is_html_namespace(left) and _is_html_namespace(right)
    )


def _unknown_namespace(prefix: str, state: str) -> str:
    identity = hashlib.sha256(
        f"epub-opening-unknown-namespace-v1\0{state}\0{prefix}".encode("utf-8")
    ).hexdigest()
    return f"{_UNKNOWN_NS}:{identity}"


def _qualified_namespace(namespaces: dict[str, str], prefix: str) -> str:
    namespace = namespaces.get(prefix)
    if namespace is None:
        return _unknown_namespace(prefix, "missing")
    if not namespace:
        return _unknown_namespace(prefix, "empty")
    return namespace


def _element_namespace(
    namespaces: dict[str, str],
    prefix: str,
    qualified: bool,
    local_name: str,
    parent_namespace: str | None,
    *,
    default_declared: bool = False,
) -> str:
    if qualified:
        return _qualified_namespace(namespaces, prefix)
    namespace = namespaces.get("", "")
    if not default_declared or namespace in {"", _XHTML_NS}:
        if local_name == "svg":
            namespaces[""] = _SVG_NS
            return _SVG_NS
        if local_name == "math":
            namespaces[""] = _MATHML_NS
            return _MATHML_NS
    if default_declared:
        return namespace
    if namespace:
        return namespace
    if parent_namespace is not None and not _is_html_namespace(parent_namespace):
        return parent_namespace
    return ""


def _recovery_namespaces(
    inherited: dict[str, str],
    attributes: list[tuple[str, str]],
) -> tuple[dict[str, str], bool, bool]:
    namespaces = dict(inherited)
    declarations: dict[str, list[str]] = {}
    for name, value in attributes:
        if name == "xmlns":
            declarations.setdefault("", []).append(value)
        elif name.startswith("xmlns:"):
            declarations.setdefault(name.partition(":")[2], []).append(value)
    duplicate = any(len(values) > 1 for values in declarations.values())
    for prefix, values in declarations.items():
        if len(values) > 1:
            namespaces[prefix] = _unknown_namespace(prefix, "duplicate")
        elif values[0]:
            namespaces[prefix] = values[0]
        elif not prefix:
            namespaces[prefix] = ""
        else:
            namespaces[prefix] = _unknown_namespace(prefix, "empty")
    return namespaces, "" in declarations, duplicate


def _recovery_attributes(
    attributes: list[tuple[str, str]],
    namespaces: dict[str, str],
) -> tuple[dict[str, str], tuple[str, ...], frozenset[str]]:
    values: dict[str, list[str]] = {}
    for name, value in attributes:
        if name == "xmlns" or name.startswith("xmlns:"):
            continue
        prefix, separator, local_name = name.partition(":")
        namespace = _qualified_namespace(namespaces, prefix) if separator else ""
        mapped_name = f"{{{namespace}}}{local_name}" if namespace else name
        values.setdefault(mapped_name, []).append(value)

    controlled = {
        "aria-hidden",
        "hidden",
        "href",
        "id",
        "type",
        f"{{{_EPUB_NS}}}type",
        f"{{{_XML_NS}}}id",
    }
    invalid = {
        name
        for name, candidates in values.items()
        if name in controlled and len(candidates) != 1
    }
    identity_names = {"id", f"{{{_XML_NS}}}id"}
    if sum(len(values.get(name, ())) for name in identity_names) > 1:
        invalid.update(identity_names & values.keys())
    mapped = {
        name: candidates[-1]
        for name, candidates in values.items()
        if name not in invalid
    }
    fragment_ids = tuple(
        mapped[name]
        for name in ("id", f"{{{_XML_NS}}}id")
        if mapped.get(name)
    )
    return mapped, fragment_ids, frozenset(invalid)


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].casefold()


def _projected_text_usage(events: list[tuple[str, tuple[int, ...]]]) -> int:
    length = 0
    pending_space = False
    for value, _ordinals in events:
        for character in value:
            if character.isspace():
                pending_space = True
            else:
                if pending_space and length:
                    length += 1
                pending_space = False
                length += 1
    return length


def _markup_end(data: bytes, start: int) -> int:
    quote: int | None = None
    for index in range(start, len(data)):
        value = data[index]
        if quote is not None:
            if value == quote:
                quote = None
        elif value in (ord('"'), ord("'")):
            quote = value
        elif value == ord(">"):
            return index + 1
    return len(data)


def _contains_unquoted(data: bytes, expected: int) -> bool:
    quote: int | None = None
    for value in data:
        if quote is not None:
            if value == quote:
                quote = None
        elif value in (ord('"'), ord("'")):
            quote = value
        elif value == expected:
            return True
    return False


def _preflight_declarations(data: bytes) -> None:
    """Reject real unsafe declarations without treating inert text as markup."""

    lower = data.lower()
    index = 0
    raw_end: bytes | None = None
    while index < len(data):
        if raw_end is not None:
            closing_match = re.search(
                re.escape(raw_end) + br"(?=[\s/>])", lower[index:]
            )
            if closing_match is None:
                return
            index += closing_match.start()
            raw_end = None
        opening = lower.find(b"<", index)
        if opening < 0:
            return
        if lower.startswith(b"<!--", opening):
            abrupt = re.match(br"-?>", lower[opening + 4 :])
            if abrupt is not None:
                index = opening + 4 + abrupt.end()
            else:
                closing = re.search(br"--!?>", lower[opening + 4 :])
                if closing is None:
                    return
                index = opening + 4 + closing.end()
            continue
        if lower.startswith(b"<![cdata[", opening):
            closing = lower.find(b"]]>", opening + 9)
            if closing < 0:
                return
            index = closing + 3
            continue
        if lower.startswith(b"<?", opening):
            closing = lower.find(b"?>", opening + 2)
            if closing < 0:
                index = _markup_end(data, opening + 2)
            else:
                index = closing + 2
            continue
        end = _markup_end(data, opening + 1)
        declaration = lower[opening:end]
        if re.match(br"<!entity(?:\s|%)", declaration):
            raise _UnsafeConstruct(
                "unsupported entity declaration", nodes=0, text_characters=0
            )
        doctype = re.match(br"<!doctype\s+([^\s\[>]+)", declaration)
        if doctype is not None:
            if doctype.group(1) != b"html" or _contains_unquoted(
                declaration, ord("[")
            ):
                raise _UnsafeConstruct(
                    "unsupported doctype declaration",
                    nodes=0,
                    text_characters=0,
                )
            index = end
            continue
        tag = re.match(br"<\s*(/?)\s*([a-z][a-z0-9:-]*)", declaration)
        if tag is not None and not tag.group(1) and tag.group(2) in {b"script", b"style"}:
            raw_end = b"</" + tag.group(2)
        index = end


def _project_strict(data: bytes, limits: EpubOpeningLimits, request: EpubPackageRequest) -> _Projection:
    elements: list[_Element] = []
    stack: list[_Element] = []
    events: list[tuple[str, tuple[int, ...]]] = []
    try:
        parser = expat.ParserCreate(namespace_separator="}")
        if not parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER):
            raise ValueError("entity parsing could not be disabled")
    except (AttributeError, MemoryError):
        raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT) from None

    def expanded(value: str) -> str:
        return "{" + value if "}" in value else value

    def start(name: str, attributes: dict[str, str]) -> None:
        _check(request)
        if len(elements) >= limits.nodes_per_resource or len(stack) + 1 > limits.document_depth:
            raise _ResourceRejected(
                nodes=len(elements),
                text_characters=_projected_text_usage(events),
            )
        mapped = {expanded(key): value for key, value in attributes.items()}
        expanded_name = expanded(name)
        namespace = expanded_name[1:].partition("}")[0] if expanded_name.startswith("{") else ""
        local_name = _local(expanded_name)
        if namespace in {_XINCLUDE_NS, _XSLT_NS}:
            raise _UnsafeConstruct(
                "unsupported foreign inclusion construct",
                nodes=len(elements),
                text_characters=_projected_text_usage(events),
            )
        parent_active = bool(stack and stack[-1].active)
        hidden = (
            "hidden" in {key.rsplit("}", 1)[-1].casefold() for key in mapped}
            or any(
                key.rsplit("}", 1)[-1].casefold() == "aria-hidden"
                and value.casefold() == "true"
                for key, value in mapped.items()
            )
            or (
                local_name == "input"
                and any(
                    key.rsplit("}", 1)[-1].casefold() == "type"
                    and value.casefold() == "hidden"
                    for key, value in mapped.items()
                )
            )
        )
        element = _Element(
            len(elements) + 1, local_name, mapped,
            _tokens(mapped) if _is_html_namespace(namespace) else set(),
            len(stack) + 1,
            active=parent_active or hidden or local_name in _ACTIVE,
            ancestors=tuple(value.ordinal for value in stack),
            namespace=namespace,
            fragment_ids=tuple(
                value
                for key, value in mapped.items()
                if key in {"id", f"{{{_XML_NS}}}id"} and value
            ),
        )
        elements.append(element)
        if local_name in {"br", "wbr"} and stack and not stack[-1].active:
            events.append((" ", tuple(value.ordinal for value in stack)))
        stack.append(element)

    def end(_name: str) -> None:
        _check(request)
        if not stack:
            raise ValueError("unbalanced XML")
        stack.pop()

    def text(value: str) -> None:
        if stack and not stack[-1].active:
            events.append((value, tuple(element.ordinal for element in stack)))

    def reject(*_args: object) -> None:
        raise ValueError("active XML declaration")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = lambda *_args: 0
    try:
        parser.Parse(data, True)
    except (_AggregateRejected, _ResourceRejected, _UnsafeConstruct):
        raise
    except (expat.ExpatError, UnicodeError, LookupError, ValueError, RecursionError):
        raise _MalformedMarkup(
            "strict parsing failed",
            nodes=len(elements),
            text_characters=_projected_text_usage(events),
        ) from None
    return _finalize_projection(OpeningParseMode.STRICT, elements, events, limits)


class _RecoveryParser(HTMLParser):
    def __init__(self, limits: EpubOpeningLimits, request: EpubPackageRequest) -> None:
        super().__init__(convert_charrefs=True)
        self.limits = limits
        self.request = request
        self.elements: list[_Element] = []
        self.stack: list[_Element] = []
        self.events: list[tuple[str, tuple[int, ...]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _check(self.request)
        raw_attributes = [
            (name.casefold(), "" if value is None else value)
            for name, value in attrs
        ]
        namespaces, default_declared, duplicate_namespace = _recovery_namespaces(
            self.stack[-1].namespaces if self.stack else {"xml": _XML_NS},
            raw_attributes,
        )
        if duplicate_namespace:
            raise ValueError("duplicate namespace declaration")
        raw_name = tag.casefold()
        prefix, separator, unqualified = raw_name.partition(":")
        local_name = unqualified if separator else raw_name
        namespace = _element_namespace(
            namespaces,
            prefix,
            bool(separator),
            local_name,
            self.stack[-1].namespace if self.stack else None,
            default_declared=default_declared,
        )
        if namespace in {_XINCLUDE_NS, _XSLT_NS}:
            raise _UnsafeConstruct(
                "unsupported foreign inclusion construct",
                nodes=len(self.elements),
                text_characters=_projected_text_usage(self.events),
            )
        mapped, fragment_ids, invalid_attributes = _recovery_attributes(
            raw_attributes, namespaces
        )
        if _is_html_namespace(namespace):
            for opened, closing in _HTML_IMPLIED_CLOSE.items():
                if local_name not in closing:
                    continue
                for index in range(len(self.stack) - 1, -1, -1):
                    if not _is_html_namespace(self.stack[index].namespace):
                        break
                    if self.stack[index].name == opened:
                        del self.stack[index:]
                        break
        if len(self.elements) >= self.limits.nodes_per_resource or len(self.stack) + 1 > self.limits.document_depth:
            raise _ResourceRejected(
                nodes=len(self.elements),
                text_characters=_projected_text_usage(self.events),
            )
        parent_active = bool(self.stack and self.stack[-1].active)
        invalid_hiding = bool(
            invalid_attributes & {"aria-hidden", "hidden", "type"}
        )
        hidden = (
            invalid_hiding
            or "hidden" in mapped
            or mapped.get("aria-hidden", "").casefold() == "true"
            or (
                local_name == "input"
                and mapped.get("type", "").casefold() == "hidden"
            )
        )
        element = _Element(
            len(self.elements) + 1, local_name, mapped,
            _tokens(mapped) if _is_html_namespace(namespace) else set(),
            len(self.stack) + 1,
            active=parent_active or hidden or local_name in _ACTIVE,
            ancestors=tuple(value.ordinal for value in self.stack),
            namespaces=namespaces,
            namespace=namespace,
            fragment_ids=fragment_ids,
            invalid_attributes=invalid_attributes,
        )
        self.elements.append(element)
        if local_name in {"br", "wbr"} and self.stack and not self.stack[-1].active:
            self.events.append((" ", tuple(value.ordinal for value in self.stack)))
        if local_name not in _HTML_VOID or not _is_html_namespace(namespace):
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element_count = len(self.elements)
        self.handle_starttag(tag, attrs)
        if (
            len(self.elements) > element_count
            and self.stack
            and self.stack[-1] is self.elements[-1]
        ):
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        raw_name = tag.casefold()
        prefix, separator, unqualified = raw_name.partition(":")
        wanted = unqualified if separator else raw_name
        if not self.stack:
            return
        namespace = (
            _qualified_namespace(self.stack[-1].namespaces, prefix)
            if separator
            else self.stack[-1].namespaces.get("", "")
        )
        region = self.stack[-1].namespace if self.stack else ""
        for index in range(len(self.stack) - 1, -1, -1):
            element = self.stack[index]
            matches = (
                element.name == wanted
                and (
                    element.namespace == namespace
                    or (
                        not separator
                        and _is_html_namespace(element.namespace)
                        and _is_html_namespace(namespace)
                    )
                )
            )
            if not _same_namespace_region(element.namespace, region):
                if matches and not _is_html_namespace(element.namespace):
                    del self.stack[index:]
                return
            if matches:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.stack and not self.stack[-1].active:
            self.events.append((data, tuple(element.ordinal for element in self.stack)))

    def handle_decl(self, decl: str) -> None:
        if not decl.casefold().startswith("doctype html"):
            raise ValueError("unsupported HTML declaration")

    def unknown_decl(self, data: str) -> None:
        if data.casefold().startswith("cdata["):
            return
        raise ValueError("unsupported HTML declaration")


class _EncodingScanner(HTMLParser):
    """Collect active HTML charset declarations without decoding publication text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.declarations: list[str] = []
        self.invalid = False
        self.stack: list[tuple[str, str, dict[str, str], bool, bool]] = []
        self.namespace_invalid = False

    @staticmethod
    def _encoding_name(value: str) -> str:
        return (
            value.casefold()
            if re.fullmatch(r"[A-Za-z0-9._-]+", value) is not None
            else "unsupported-nonascii-encoding"
        )

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        lowered = [
            (name.casefold(), "" if value is None else value)
            for name, value in attrs
        ]
        namespaces, default_declared, duplicate_namespace = _recovery_namespaces(
            self.stack[-1][2] if self.stack else {"xml": _XML_NS}, lowered
        )
        self.namespace_invalid = self.namespace_invalid or duplicate_namespace
        namespace_prefixes = [
            "" if name == "xmlns" else name.partition(":")[2]
            for name, _value in lowered
            if name == "xmlns" or name.startswith("xmlns:")
        ]
        duplicate_default = namespace_prefixes.count("") > 1
        raw_name = tag.casefold()
        prefix, separator, unqualified = raw_name.partition(":")
        local_name = unqualified if separator else raw_name
        namespace = _element_namespace(
            namespaces,
            prefix,
            bool(separator),
            local_name,
            self.stack[-1][1] if self.stack else None,
            default_declared=default_declared,
        )
        parent_inert = bool(self.stack and self.stack[-1][3])
        inherited_ambiguous_default = bool(self.stack and self.stack[-1][4])
        ambiguous_default = duplicate_default or (
            inherited_ambiguous_default and not default_declared
        )
        if (
            not default_declared
            and local_name in {"svg", "math"}
            and namespace in {_SVG_NS, _MATHML_NS}
        ):
            ambiguous_default = False
        inert = parent_inert or local_name == "template"
        active_unqualified_meta = (
            not separator
            and local_name == "meta"
            and (_is_html_namespace(namespace) or ambiguous_default)
        )
        if active_unqualified_meta and not inert:
            values: dict[str, list[str]] = {}
            for name, value in lowered:
                if ":" not in name:
                    values.setdefault(name, []).append(value.strip())
            if any(
                len(values.get(name, ())) > 1
                for name in ("charset", "http-equiv", "content")
            ):
                self.invalid = True
            else:
                charset = values.get("charset", [])
                if charset and charset[0]:
                    self.declarations.append(self._encoding_name(charset[0]))
                http_equiv = values.get("http-equiv", [])
                content = values.get("content", [])
                if (
                    http_equiv
                    and http_equiv[0].casefold() == "content-type"
                    and content
                ):
                    parameter = _CHARSET_PARAMETER.search(
                        content[0].encode("latin-1", "strict")
                    )
                    if parameter is not None:
                        self.declarations.append(
                            parameter.group(1)
                            .decode("ascii", "strict")
                            .casefold()
                        )
        if local_name not in _HTML_VOID or not _is_html_namespace(namespace):
            self.stack.append(
                (local_name, namespace, namespaces, inert, ambiguous_default)
            )

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        size = len(self.stack)
        self.handle_starttag(tag, attrs)
        if len(self.stack) > size:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        raw_name = tag.casefold()
        prefix, separator, unqualified = raw_name.partition(":")
        local_name = unqualified if separator else raw_name
        if not self.stack:
            return
        namespace = (
            _qualified_namespace(self.stack[-1][2], prefix)
            if separator
            else self.stack[-1][2].get("", "")
        )
        region = self.stack[-1][1] if self.stack else ""
        for index in range(len(self.stack) - 1, -1, -1):
            element = self.stack[index]
            matches = (
                element[0] == local_name
                and (
                    element[1] == namespace
                    or (
                        not separator
                        and _is_html_namespace(element[1])
                        and _is_html_namespace(namespace)
                    )
                )
            )
            if not _same_namespace_region(element[1], region):
                if matches and not _is_html_namespace(element[1]):
                    del self.stack[index:]
                return
            if matches:
                del self.stack[index:]
                return


def _xml_declaration_encoding(
    head: bytes, offset: int
) -> tuple[tuple[str, ...], bool]:
    declaration = head[offset:]
    if declaration[:5].lower() != b"<?xml":
        return (), False
    if len(declaration) == 5:
        return (), True
    if declaration[5] not in b" \t\r\n":
        return (), declaration[5:6] != b"-"
    if not declaration.startswith(b"<?xml"):
        return (), True

    quote: int | None = None
    closing = -1
    index = 5
    while index + 1 < len(declaration):
        value = declaration[index]
        if quote is not None:
            if value == quote:
                quote = None
        elif value in {ord("'"), ord('"')}:
            quote = value
        elif declaration[index:index + 2] == b"?>":
            closing = index
            break
        index += 1
    if closing < 0 or quote is not None:
        return (), True
    body = declaration[5:closing]
    attributes: list[tuple[str, bytes]] = []
    index = 0
    while index < len(body):
        while index < len(body) and body[index] in b" \t\r\n":
            index += 1
        if index == len(body):
            break
        name = re.match(br"[A-Za-z_:][A-Za-z0-9_.:-]*", body[index:])
        if name is None:
            return (), True
        raw_name = name.group(0)
        index += name.end()
        while index < len(body) and body[index] in b" \t\r\n":
            index += 1
        if index >= len(body) or body[index] != ord("="):
            return (), True
        index += 1
        while index < len(body) and body[index] in b" \t\r\n":
            index += 1
        if index >= len(body) or body[index] not in {ord("'"), ord('"')}:
            return (), True
        value_quote = body[index]
        index += 1
        end = body.find(bytes((value_quote,)), index)
        if end < 0:
            return (), True
        attribute_value = body[index:end]
        index = end + 1
        if index < len(body) and body[index] not in b" \t\r\n":
            return (), True
        attributes.append(
            (raw_name.decode("ascii", "strict"), attribute_value)
        )

    names = [name for name, _value in attributes]
    if not attributes or len(names) != len(set(names)):
        return (), True
    if names[0] != "version" or attributes[0][1] not in {b"1.0", b"1.1"}:
        return (), True
    position = 1
    encodings: tuple[str, ...] = ()
    if position < len(attributes) and attributes[position][0] == "encoding":
        encoding = attributes[position][1]
        if re.fullmatch(br"[A-Za-z][A-Za-z0-9._-]*", encoding) is None:
            return (), True
        encodings = (encoding.decode("ascii", "strict").casefold(),)
        position += 1
    if position < len(attributes) and attributes[position][0] == "standalone":
        if attributes[position][1] not in {b"yes", b"no"}:
            return (), True
        position += 1
    if position != len(attributes):
        return (), True
    return encodings, False


def _real_encoding_declarations(
    data: bytes,
) -> tuple[tuple[str, ...], bool, bool]:
    """Return declarations plus independent encoding and namespace invalidity."""

    head = data[:2_048]
    declarations: list[str] = []
    offset = 3 if head.startswith(b"\xef\xbb\xbf") else 0
    xml_encodings, invalid = _xml_declaration_encoding(head, offset)
    declarations.extend(xml_encodings)
    scanner = _EncodingScanner()
    try:
        scanner.feed(head.decode("latin-1", "strict"))
        scanner.close()
    except (UnicodeError, ValueError, RecursionError):
        invalid = True
    declarations.extend(scanner.declarations)
    return (
        tuple(declarations),
        invalid or scanner.invalid,
        scanner.namespace_invalid,
    )


def _looks_wide_encoded(data: bytes) -> bool:
    if data.startswith(
        (b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")
    ):
        return True
    return b"\x00" in data


def _recovery_codec(data: bytes) -> tuple[str, bool]:
    if _looks_wide_encoded(data):
        raise _UnsupportedEncoding(
            "unsupported encoding", nodes=0, text_characters=0
        )
    declarations, invalid, namespace_invalid = _real_encoding_declarations(data)
    if invalid:
        raise _UnsupportedEncoding(
            "unsupported encoding", nodes=0, text_characters=0
        )
    declarations = list(declarations)
    if data.startswith(b"\xef\xbb\xbf"):
        declarations.append("utf-8")
    if not declarations:
        return "utf-8-sig", namespace_invalid
    codecs = {_SUPPORTED_CODECS.get(value) for value in declarations}
    if None in codecs or len(codecs) != 1:
        raise _UnsupportedEncoding(
            "unsupported encoding", nodes=0, text_characters=0
        )
    codec = codecs.pop()
    assert codec is not None
    return codec, namespace_invalid


def _project_recovered(data: bytes, limits: EpubOpeningLimits, request: EpubPackageRequest) -> _Projection:
    codec, namespace_invalid = _recovery_codec(data)
    try:
        text = data.decode(codec, "strict")
    except UnicodeDecodeError:
        raise _UnsupportedEncoding(
            "unsupported encoding", nodes=0, text_characters=0
        ) from None
    parser = _RecoveryParser(limits, request)
    try:
        parser.feed(text)
        parser.close()
        if namespace_invalid:
            raise ValueError("duplicate namespace declaration")
    except (_AggregateRejected, _ResourceRejected, _UnsafeConstruct):
        raise
    except (ValueError, RecursionError):
        raise _MalformedMarkup(
            "recovery failed",
            nodes=len(parser.elements),
            text_characters=_projected_text_usage(parser.events),
        ) from None
    return _finalize_projection(
        OpeningParseMode.RECOVERED, parser.elements, parser.events, limits,
        ("recovered_markup",),
    )


def _finalize_projection(
    mode: OpeningParseMode,
    elements: list[_Element],
    events: list[tuple[str, tuple[int, ...]]],
    limits: EpubOpeningLimits,
    warnings: tuple[str, ...] = (),
) -> _Projection:
    parts: list[str] = []
    starts: dict[int, int] = {}
    ends: dict[int, int] = {}
    pending_space: set[int] | None = None
    prior: set[int] = set()
    for value, ordinals in events:
        current = set(ordinals)
        for character in value:
            if character.isspace():
                pending_space = current if pending_space is None else pending_space & current
                continue
            if pending_space is not None and parts:
                shared = pending_space & current & prior
                parts.append(" ")
                for ordinal in shared:
                    starts.setdefault(ordinal, len(parts) - 1)
                    ends[ordinal] = len(parts)
            pending_space = None
            position = len(parts)
            parts.append(character)
            for ordinal in current:
                starts.setdefault(ordinal, position)
                ends[ordinal] = position + 1
            prior = current
            if len(parts) > limits.text_characters_per_selection:
                raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
    text = "".join(parts)
    for element in elements:
        if element.ordinal not in starts or element.active:
            continue
        element.start = starts[element.ordinal]
        element.end = ends[element.ordinal]
        element.exact_text = text[element.start:element.end].strip()
        if element.exact_text:
            leading = len(text[element.start:element.end]) - len(text[element.start:element.end].lstrip())
            element.start += leading
            element.end = element.start + len(element.exact_text)
    return _Projection(
        mode, elements, text, len(elements),
        max((element.depth for element in elements), default=0), warnings,
        len(elements), len(text),
    )


def _parse(data: bytes, media_type: str, limits: EpubOpeningLimits, request: EpubPackageRequest) -> _Projection:
    if _looks_wide_encoded(data):
        raise _UnsupportedEncoding(
            "unsupported encoding", nodes=0, text_characters=0
        )
    _preflight_declarations(data)
    if media_type == "application/xhtml+xml":
        try:
            return _project_strict(data, limits, request)
        except _UnsafeConstruct:
            raise
        except _MalformedMarkup as strict_error:
            try:
                projection = _project_recovered(data, limits, request)
            except _ResourceRejected as recovery_error:
                raise _ResourceRejected(
                    nodes=strict_error.nodes + recovery_error.nodes,
                    text_characters=(
                        strict_error.text_characters
                        + recovery_error.text_characters
                    ),
                ) from None
            except _UnsupportedEncoding as recovery_error:
                raise _UnsupportedEncoding(
                    "unsupported encoding",
                    nodes=strict_error.nodes + recovery_error.nodes,
                    text_characters=(
                        strict_error.text_characters
                        + recovery_error.text_characters
                    ),
                ) from None
            except _UnsafeConstruct as recovery_error:
                raise _UnsafeConstruct(
                    str(recovery_error),
                    nodes=strict_error.nodes + recovery_error.nodes,
                    text_characters=(
                        strict_error.text_characters
                        + recovery_error.text_characters
                    ),
                ) from None
            except _MalformedMarkup as recovery_error:
                raise _MalformedMarkup(
                    str(recovery_error),
                    nodes=strict_error.nodes + recovery_error.nodes,
                    text_characters=(
                        strict_error.text_characters
                        + recovery_error.text_characters
                    ),
                ) from None
            projection.work_nodes += strict_error.nodes
            projection.work_text_characters += strict_error.text_characters
            return projection
    return _project_recovered(data, limits, request)


def _read_member(archive: zipfile.ZipFile, name: str, maximum: int, request: EpubPackageRequest) -> bytes:
    try:
        info = archive.getinfo(name)
        if info.file_size > maximum:
            raise _ResourceRejected
        with archive.open(info, "r") as stream:
            chunks: list[bytes] = []
            total = 0
            while True:
                _check(request)
                chunk = stream.read(min(65_536, maximum + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise _ResourceRejected
                chunks.append(chunk)
            return b"".join(chunks)
    except (_AggregateRejected, _ResourceRejected):
        raise
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
        raise ValueError("member read failed") from None


def _target_fragment(href: str) -> str | None:
    if "#" not in href:
        return None
    try:
        fragment = unquote(href.partition("#")[2], errors="strict")
    except (UnicodeError, ValueError):
        raise ValueError("invalid fragment") from None
    if not fragment:
        return None
    return hashlib.sha256(
        unicodedata.normalize("NFC", fragment).encode("utf-8")
    ).hexdigest()


def _safe_target(
    base_name: str, href: str, names: set[str]
) -> tuple[str, str | None] | None:
    raw = href.split("#", 1)[0]
    if not raw or "?" in raw or "\\" in raw or raw.startswith("/") or ":" in raw.split("/", 1)[0]:
        return None
    try:
        decoded = unquote(raw, errors="strict")
    except (UnicodeError, ValueError):
        return None
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(base_name), decoded))
    if normalized.startswith("../") or normalized == ".." or normalized not in names:
        return None
    try:
        boundary = _target_fragment(href)
    except ValueError:
        return None
    return normalized, boundary


def _manifest(package_report: EpubPackageReport) -> tuple[dict[str, tuple[str, str, tuple[str, ...]]], dict[str, str], set[str], str | None]:
    by_id: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    spine: dict[str, str] = {}
    encrypted: set[str] = set()
    package_identity: str | None = None
    for declaration in package_report.declarations:
        if declaration.kind is EpubPackageDeclarationKind.MANIFEST_ITEM and declaration.identifier and declaration.resource_identity and declaration.media_type:
            by_id[declaration.identifier] = (declaration.resource_identity, declaration.media_type, declaration.properties)
        elif declaration.kind is EpubPackageDeclarationKind.SPINE_ITEM and declaration.identifier and declaration.resource_identity:
            spine[declaration.identifier] = declaration.resource_identity
        elif declaration.kind is EpubPackageDeclarationKind.ENCRYPTION and declaration.resource_identity:
            encrypted.add(declaration.resource_identity)
        elif declaration.kind is EpubPackageDeclarationKind.PACKAGE:
            package_identity = declaration.resource_identity
    return by_id, spine, encrypted, package_identity


def _raw_package_annotations(
    package_data: bytes,
    package_report: EpubPackageReport,
) -> tuple[
    tuple[tuple[str, bool], ...] | None,
    dict[int, str | None],
    dict[str, str],
    bool,
]:
    """Recover annotations only for exact declarations already accepted by F06."""

    if b"<!DOCTYPE" in package_data.upper() or b"<!ENTITY" in package_data.upper():
        return None, {}, {}, False
    try:
        root = ElementTree.fromstring(package_data)
    except (ElementTree.ParseError, RecursionError):
        return None, {}, {}, False
    accepted_manifest = {
        declaration.source_ordinal: declaration
        for declaration in package_report.declarations
        if declaration.kind is EpubPackageDeclarationKind.MANIFEST_ITEM
        and declaration.identifier
        and declaration.resource_identity
    }
    accepted_spine = {
        declaration.source_ordinal: declaration
        for declaration in package_report.declarations
        if declaration.kind is EpubPackageDeclarationKind.SPINE_ITEM
        and declaration.identifier
        and declaration.resource_identity
    }
    accepted_guides = {
        declaration.source_ordinal: declaration
        for declaration in package_report.declarations
        if declaration.kind is EpubPackageDeclarationKind.GUIDE_REFERENCE
        and declaration.identifier
        and declaration.resource_identity
    }
    parents = {child: parent for parent in root.iter() for child in parent}
    spine: list[tuple[str, bool]] = []
    guide_fragments: dict[int, str | None] = {}
    manifest_fallbacks: dict[str, str] = {}
    invalid_linear = False
    for ordinal, node in enumerate(root.iter(), 1):
        parent = parents.get(node)
        if ordinal in accepted_manifest:
            declaration = accepted_manifest[ordinal]
            if (
                node.tag == f"{{{_OPF_NS}}}item"
                and parent is not None
                and parent.tag == f"{{{_OPF_NS}}}manifest"
                and not list(node)
                and not (node.text or "").strip()
                and node.attrib.get("id") == declaration.identifier
            ):
                fallback = node.attrib.get("fallback")
                if fallback:
                    manifest_fallbacks[declaration.identifier] = fallback
        if ordinal in accepted_spine:
            declaration = accepted_spine[ordinal]
            if (
                node.tag == f"{{{_OPF_NS}}}itemref"
                and parent is not None
                and parent.tag == f"{{{_OPF_NS}}}spine"
                and not list(node)
                and not (node.text or "").strip()
                and node.attrib.get("idref") == declaration.identifier
            ):
                linear = node.attrib.get("linear")
                if linear is None or linear == "yes":
                    spine.append((declaration.identifier, True))
                elif linear == "no":
                    spine.append((declaration.identifier, False))
                else:
                    invalid_linear = True
        if ordinal in accepted_guides:
            declaration = accepted_guides[ordinal]
            href = node.attrib.get("href")
            if (
                node.tag == f"{{{_OPF_NS}}}reference"
                and parent is not None
                and parent.tag == f"{{{_OPF_NS}}}guide"
                and not list(node)
                and not (node.text or "").strip()
                and node.attrib.get("type") == declaration.identifier
                and href
            ):
                try:
                    guide_fragments[ordinal] = _target_fragment(href)
                except ValueError:
                    pass
    accepted_identifiers = {
        declaration.identifier for declaration in accepted_manifest.values()
    }
    return (
        tuple(spine),
        guide_fragments,
        {
            source: target
            for source, target in manifest_fallbacks.items()
            if target in accepted_identifiers
        },
        invalid_linear,
    )


def _navigation_targets(projection: _Projection, nav_name: str, names: set[str], identity_by_name: dict[str, str], manifest_by_identity: dict[str, tuple[str, tuple[str, ...]]], limits: EpubOpeningLimits) -> tuple[list[_Candidate], list[str], int, list[_RoleAssociation], list[_RoleAssociation]]:
    targets: list[_Candidate] = []
    warnings: list[str] = []
    links = 0
    seen_by_semantic: dict[str, set[str]] = {}
    body_targets: list[_RoleAssociation] = []
    excluded_targets: list[_RoleAssociation] = []
    by_ordinal = {element.ordinal: element for element in projection.elements}
    for element in projection.elements:
        if (
            element.active
            or not _is_html_namespace(element.namespace)
            or element.name != "a"
        ):
            continue
        landmarks = False
        for ordinal in reversed(element.ancestors):
            ancestor = by_ordinal[ordinal]
            if not _is_html_namespace(ancestor.namespace):
                break
            if (
                ancestor.name == "nav"
                and not ancestor.active
                and "landmarks" in ancestor.semantics
            ):
                landmarks = True
                break
        if not landmarks:
            continue
        invalid_href = "href" in element.invalid_attributes
        if "href" not in element.attributes and not invalid_href:
            continue
        links += 1
        if links > limits.navigation_links:
            raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        if invalid_href:
            warnings.append("invalid_navigation_target")
            continue
        semantics = element.semantics & (
            _OPENING_SEMANTICS | _BODY_SEMANTICS | _EXCLUDED_SEMANTICS
        )
        if not semantics:
            continue
        target = _safe_target(nav_name, element.attributes["href"], names)
        if target is None:
            warnings.append("invalid_navigation_target")
            continue
        name, target_fragment = target
        identity = identity_by_name[name]
        declared = manifest_by_identity.get(identity)
        if declared is None:
            warnings.append("unmanifested_navigation_target")
            continue
        media_type, _properties = declared
        if semantics & _BODY_SEMANTICS:
            body_targets.append(
                _RoleAssociation(
                    identity, "epub3_landmark", "bodymatter", target_fragment,
                    ("epub3_landmark", "bodymatter"), element.ordinal,
                )
            )
        if semantics & _EXCLUDED_SEMANTICS:
            for semantic in sorted(semantics & _EXCLUDED_SEMANTICS):
                excluded_targets.append(
                    _RoleAssociation(
                        identity, "epub3_landmark", semantic, target_fragment,
                        ("epub3_landmark", semantic), element.ordinal,
                    )
                )
        for semantic in sorted(semantics):
            if semantic not in _OPENING_SEMANTICS:
                continue
            seen_by_semantic.setdefault(semantic, set()).add(identity)
            association = _RoleAssociation(
                identity,
                "epub3_landmark",
                semantic,
                target_fragment,
                ("epub3_landmark", semantic),
                element.ordinal,
            )
            targets.append(
                _Candidate(
                    identity,
                    media_type,
                    ("epub3_landmark", semantic),
                    element.ordinal,
                    target_fragment,
                    frozenset({semantic}),
                    frozenset({target_fragment}),
                    (association,),
                )
            )
    if any(len(values) > 1 for values in seen_by_semantic.values()):
        warnings.append("ambiguous_navigation_landmark")
    return targets, warnings, links, body_targets, excluded_targets


def _candidate_graph(package_report: EpubPackageReport, archive: zipfile.ZipFile, names_by_identity: dict[str, str], limits: EpubOpeningLimits, request: EpubPackageRequest) -> tuple[list[_Candidate], list[str], list[OpeningResourceResult], int, set[str], list[_RoleAssociation], list[_RoleAssociation], tuple[int, int, int, int, int, int]]:
    by_id, declared_spine, encrypted, package_identity = _manifest(package_report)
    declarations_by_identity: dict[str, set[tuple[str, tuple[str, ...]]]] = {}
    for identity, media, properties in by_id.values():
        declarations_by_identity.setdefault(identity, set()).add(
            (media, properties)
        )
    manifest_by_identity = {
        identity: next(iter(declarations))
        for identity, declarations in declarations_by_identity.items()
        if len(declarations) == 1
    }
    names = set(names_by_identity.values())
    identity_by_name = {name: identity for identity, name in names_by_identity.items()}
    candidates: list[_Candidate] = []
    warnings: list[str] = [
        warning.code
        for warning in package_report.warnings
        if warning.code in _PACKAGE_WARNING_CODES
    ]
    pre_resources: list[OpeningResourceResult] = []
    navigation_links = 0
    navigation_expanded = navigation_text = navigation_nodes = 0
    navigation_opened = navigation_strict = navigation_recovered = 0
    body_targets: list[_RoleAssociation] = []
    excluded_targets: list[_RoleAssociation] = []
    navigation = [value for value in package_report.declarations if value.kind is EpubPackageDeclarationKind.NAVIGATION and value.resource_identity]
    if (
        len(navigation) == 1
        and navigation[0].resource_identity in names_by_identity
        and navigation[0].resource_identity not in encrypted
    ):
        identity = navigation[0].resource_identity
        name = names_by_identity[identity]
        data: bytes | None = None
        try:
            navigation_opened = 1
            data = _read_member(archive, name, limits.expanded_bytes_per_resource, request)
            navigation_expanded = len(data)
            projection = _parse(data, "application/xhtml+xml", limits, request)
            navigation_text = projection.work_text_characters
            navigation_nodes = projection.work_nodes
            navigation_strict = int(projection.mode is OpeningParseMode.STRICT)
            navigation_recovered = int(projection.mode is OpeningParseMode.RECOVERED)
            (
                selected,
                nav_warnings,
                navigation_links,
                nav_body_targets,
                nav_excluded_targets,
            ) = _navigation_targets(
                projection,
                name,
                names,
                identity_by_name,
                manifest_by_identity,
                limits,
            )
            body_targets.extend(nav_body_targets)
            excluded_targets.extend(nav_excluded_targets)
            candidates.extend(selected)
            warnings.extend(projection.warnings)
            warnings.extend(nav_warnings)
            pre_resources.append(
                OpeningResourceResult(
                    identity,
                    OpeningResourceDisposition.NO_CLAIMS,
                    ("epub3_navigation",),
                    projection.mode,
                    tuple(sorted(set((*projection.warnings, *nav_warnings)))),
                    opened=True,
                    resource_digest=hashlib.sha256(data).hexdigest(),
                )
            )
        except _ResourceRejected as error:
            navigation_nodes = max(navigation_nodes, error.nodes)
            navigation_text = max(navigation_text, error.text_characters)
            warnings.append("navigation_resource_limit")
            pre_resources.append(
                OpeningResourceResult(
                    identity, OpeningResourceDisposition.RESOURCE_LIMIT,
                    ("epub3_navigation",), warnings=("navigation_resource_limit",),
                    opened=navigation_opened == 1,
                )
            )
        except _UnsafeConstruct as error:
            navigation_nodes = max(navigation_nodes, error.nodes)
            navigation_text = max(navigation_text, error.text_characters)
            warnings.extend(("unsafe_construct", "unusable_navigation_document"))
            pre_resources.append(
                OpeningResourceResult(
                    identity, OpeningResourceDisposition.MALFORMED,
                    ("epub3_navigation",),
                    warnings=("unsafe_construct", "unusable_navigation_document"),
                    opened=True,
                )
            )
        except _UnsupportedEncoding as error:
            navigation_nodes = max(navigation_nodes, error.nodes)
            navigation_text = max(navigation_text, error.text_characters)
            warnings.append("unusable_navigation_document")
            pre_resources.append(
                OpeningResourceResult(
                    identity, OpeningResourceDisposition.MALFORMED,
                    ("epub3_navigation",), warnings=("unusable_navigation_document",),
                    opened=True,
                )
            )
        except _MalformedMarkup as error:
            navigation_nodes = max(navigation_nodes, error.nodes)
            navigation_text = max(navigation_text, error.text_characters)
            warnings.append("unusable_navigation_document")
            pre_resources.append(
                OpeningResourceResult(
                    identity, OpeningResourceDisposition.MALFORMED,
                    ("epub3_navigation",), warnings=("unusable_navigation_document",),
                    opened=True,
                )
            )
        except ValueError:
            warnings.append("unusable_navigation_document")
            pre_resources.append(
                OpeningResourceResult(
                    identity, OpeningResourceDisposition.MALFORMED,
                    ("epub3_navigation",), warnings=("unusable_navigation_document",),
                    opened=navigation_opened == 1,
                )
            )
    elif (
        len(navigation) == 1
        and navigation[0].resource_identity in encrypted
    ):
        warnings.append("encrypted_navigation_document")
        pre_resources.append(
            OpeningResourceResult(
                navigation[0].resource_identity, OpeningResourceDisposition.ENCRYPTED,
                ("epub3_navigation",), warnings=("encrypted_navigation_document",),
            )
        )
    elif navigation:
        warnings.append("ambiguous_navigation_document")
    raw_spine: tuple[tuple[str, bool], ...] | None = None
    guide_fragments: dict[int, str | None] = {}
    manifest_fallbacks: dict[str, str] = {}
    if package_identity in names_by_identity:
        try:
            package_data = _read_member(
                archive,
                names_by_identity[package_identity],
                limits.package.opf_package_bytes,
                request,
            )
            (
                raw_spine,
                guide_fragments,
                manifest_fallbacks,
                invalid_linear,
            ) = _raw_package_annotations(package_data, package_report)
            if invalid_linear:
                warnings.append("unusable_spine_relationship")
        except (_ResourceRejected, ValueError):
            warnings.append("unusable_spine_relationship")
    for declaration in package_report.declarations:
        if declaration.kind is not EpubPackageDeclarationKind.GUIDE_REFERENCE or not declaration.resource_identity or not declaration.identifier:
            continue
        semantic = declaration.identifier.casefold()
        target_fragment = guide_fragments.get(declaration.source_ordinal)
        if semantic in {"text", "bodymatter"}:
            body_targets.append(
                _RoleAssociation(
                    declaration.resource_identity, "epub2_guide", semantic,
                    target_fragment, ("epub2_guide", semantic),
                    declaration.source_ordinal,
                )
            )
            continue
        if semantic in {"preview", "sample", "advertisement"}:
            excluded_targets.append(
                _RoleAssociation(
                    declaration.resource_identity, "epub2_guide", semantic,
                    target_fragment, ("epub2_guide", semantic),
                    declaration.source_ordinal,
                )
            )
            continue
        if semantic not in {"title-page", "copyright-page", "dedication", "acknowledgements", "preface", "foreword", "other.ms-firstpage"}:
            continue
        declared = manifest_by_identity.get(declaration.resource_identity)
        if declared is None:
            warnings.append("unmanifested_guide_target")
            continue
        normalized_semantic = semantic.replace("-", "_")
        association = _RoleAssociation(
            declaration.resource_identity,
            "epub2_guide",
            normalized_semantic,
            target_fragment,
            ("epub2_guide", normalized_semantic),
            declaration.source_ordinal,
        )
        candidates.append(
            _Candidate(
                declaration.resource_identity,
                declared[0],
                ("epub2_guide", normalized_semantic),
                declaration.source_ordinal,
                target_fragment,
                frozenset({normalized_semantic}),
                frozenset({target_fragment}),
                (association,),
            )
        )
    guide_targets: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.selection_path[0] == "epub2_guide":
            guide_targets.setdefault(candidate.selection_path[-1], set()).add(candidate.identity)
    if any(len(values) > 1 for values in guide_targets.values()):
        warnings.append("ambiguous_guide_reference")
    if raw_spine is None:
        raw_spine = tuple((identifier, True) for identifier in declared_spine)

    def resolved_spine_declaration(
        identifier: str,
    ) -> tuple[str, str, tuple[str, ...]] | None:
        current = identifier
        visited: set[str] = set()
        original = by_id.get(identifier)
        while current not in visited:
            visited.add(current)
            declared = by_id.get(current)
            if declared is None:
                return original
            if declared[1] in _CONTENT_MEDIA:
                return declared
            fallback = manifest_fallbacks.get(current)
            if fallback is None:
                return declared
            current = fallback
        return original

    for order, (identifier, linear) in enumerate(raw_spine, 1):
        declared = resolved_spine_declaration(identifier)
        if not linear or declared is None:
            continue
        identity, media_type, _properties = declared
        association = _RoleAssociation(
            identity, "linear_spine_fallback", "linear_spine_fallback", None,
            ("linear_spine_fallback",), 1_000_000 + order,
        )
        candidates.append(
            _Candidate(
                identity,
                media_type,
                ("linear_spine_fallback",),
                1_000_000 + order,
                opening_boundaries=frozenset({None}),
                role_associations=(association,),
            )
        )

    def candidate_key(value: _Candidate) -> tuple[int, int, int, tuple[str, ...]]:
        return (
            _SOURCE_RANK[value.selection_path[0]],
            _ROLE_ORDER.get(value.selection_path[-1], len(_ROLE_ORDER)),
            value.order,
            value.selection_path,
        )

    grouped: dict[str, list[_Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.identity, []).append(candidate)
    deduplicated: list[_Candidate] = []
    for group in grouped.values():
        canonical = min(group, key=candidate_key)
        role_candidates = tuple(
            candidate for candidate in group if candidate.recognition_semantics
        )
        deduplicated.append(
            _Candidate(
                canonical.identity,
                canonical.media_type,
                canonical.selection_path,
                canonical.order,
                canonical.target_fragment,
                frozenset().union(
                    *(candidate.recognition_semantics for candidate in group)
                ),
                frozenset().union(
                    *(
                        candidate.opening_boundaries
                        for candidate in (role_candidates or tuple(group))
                    )
                ),
                tuple(
                    association
                    for candidate in group
                    for association in candidate.role_associations
                ),
            )
        )
    deduplicated.sort(key=candidate_key)
    return (
        deduplicated,
        warnings,
        pre_resources,
        navigation_links,
        encrypted,
        body_targets,
        excluded_targets,
        (
            navigation_expanded,
            navigation_text,
            navigation_nodes,
            navigation_opened,
            navigation_strict,
            navigation_recovered,
        ),
    )


def _normal(value: str, field: OpeningField) -> str:
    result = _SPACE.sub(" ", unicodedata.normalize("NFC", value)).strip()
    if field is OpeningField.IDENTIFIER:
        return "".join(character for character in result.upper() if character.isdigit() or character == "X")
    return result


def _bibliographic_indicia(
    semantics: set[str],
    text: str,
    labelled: tuple[_BibliographicIndicia, ...] | None = None,
) -> tuple[_BibliographicIndicia, ...]:
    """Admit only structurally explicit or exactly labelled target indicia."""

    if labelled is None:
        labelled = _bibliographic_indicia_label_occurrences(text)
    if labelled:
        return labelled
    if _is_bibliographic_indicia_label(text):
        return ()
    if _is_non_bibliographic_indicia(text):
        return ()
    return tuple(
        _BibliographicIndicia(field, text)
        for token, field in (
            ("subtitle", OpeningField.SUBTITLE),
            ("publisher", OpeningField.PUBLISHER),
            ("imprint", OpeningField.IMPRINT),
        )
        if token in semantics
    )


def _is_non_bibliographic_indicia(text: str) -> bool:
    return (
        _NON_BIBLIOGRAPHIC_INDICIA.match(text) is not None
        or _INDICIA_PROMOTION_NOTICE.fullmatch(text) is not None
        or any(
            pattern.search(text) is not None
            for pattern, _role in _CONTEXTUAL_CREDITS
        )
    )


def _is_bibliographic_indicia_label(text: str) -> bool:
    return _BIBLIOGRAPHIC_INDICIA_LABEL.fullmatch(text) is not None


def _bibliographic_indicia_label_occurrences(
    text: str,
    *,
    base_start: int = 0,
    cue_boundaries: set[int] | None = None,
) -> tuple[_BibliographicIndicia, ...]:
    cues: list[re.Match[str]] = []
    for cue in _BIBLIOGRAPHIC_INDICIA_LABEL_CUE.finditer(text):
        if not cues and cue.start() != 0:
            return ()
        if (
            cue.start() != 0
            and not text[cue.start() - 1].isspace()
            and (
                cue_boundaries is None
                or base_start + cue.start() not in cue_boundaries
            )
        ):
            continue
        if len(cues) == _BIBLIOGRAPHIC_INDICIA_PER_ELEMENT:
            break
        cues.append(cue)
    occurrences: list[_BibliographicIndicia] = []
    for index, cue in enumerate(cues):
        context_end = (
            cues[index + 1].start()
            if index + 1 < len(cues)
            else len(text)
        )
        while context_end > cue.end() and text[context_end - 1].isspace():
            context_end -= 1
        value = text[cue.end():context_end].strip()
        if not value or _is_non_bibliographic_indicia(value):
            continue
        field = {
            "subtitle": OpeningField.SUBTITLE,
            "publisher": OpeningField.PUBLISHER,
            "imprint": OpeningField.IMPRINT,
        }[cue.group("label").casefold()]
        occurrences.append(
            _BibliographicIndicia(
                field,
                value,
                (cue.start(), context_end),
            )
        )
    return tuple(occurrences)


def _bibliographic_indicia_label_key(
    element: _Element,
    indicia: _BibliographicIndicia,
) -> tuple[OpeningField, int, int]:
    if indicia.context_span is None:
        raise ValueError("unlabelled bibliographic indicia has no owner key")
    start, end = indicia.context_span
    return indicia.field, element.start + start, element.start + end


def _bibliographic_indicia_label_allocations(
    elements: list[_Element],
    eligible_ordinals: set[int],
) -> tuple[
    dict[int, tuple[_BibliographicIndicia, ...]],
    dict[
        tuple[OpeningField, int, int],
        _BibliographicIndiciaAllocation,
    ],
]:
    """Assign each labelled span a deepest owner and nearest claim emitter."""

    owners: dict[
        tuple[OpeningField, int, int],
        tuple[int, int],
    ] = {}
    emitters: dict[
        tuple[OpeningField, int, int],
        tuple[int, int],
    ] = {}
    labels_by_ordinal: dict[int, tuple[_BibliographicIndicia, ...]] = {}
    cue_boundaries = {
        candidate.start
        for candidate in elements
        if candidate.ordinal in eligible_ordinals
    }
    for candidate in elements:
        if candidate.ordinal not in eligible_ordinals:
            continue
        labelled = _bibliographic_indicia_label_occurrences(
            candidate.exact_text,
            base_start=candidate.start,
            cue_boundaries=cue_boundaries,
        )
        labels_by_ordinal[candidate.ordinal] = labelled
        for indicia in labelled:
            key = _bibliographic_indicia_label_key(candidate, indicia)
            owner = (candidate.depth, candidate.ordinal)
            if owner > owners.get(key, (-1, -1)):
                owners[key] = owner
            if (
                candidate.name in _CLAIM_ELEMENTS
                or candidate.semantics & _CLAIM_SEMANTICS
            ) and owner > emitters.get(key, (-1, -1)):
                emitters[key] = owner
    return (
        labels_by_ordinal,
        {
            key: _BibliographicIndiciaAllocation(
                owner_ordinal,
                emitters.get(key, (owner_depth, owner_ordinal))[1],
            )
            for key, (owner_depth, owner_ordinal) in owners.items()
        },
    )


def _isbn_valid(value: str) -> bool:
    compact = "".join(character for character in value.upper() if character.isdigit() or character == "X")
    if len(compact) == 10 and compact[:9].isdigit() and (compact[9].isdigit() or compact[9] == "X"):
        return sum((10 - index) * (10 if character == "X" else int(character)) for index, character in enumerate(compact)) % 11 == 0
    if len(compact) == 13 and compact.isdigit():
        return sum(int(character) * (1 if index % 2 == 0 else 3) for index, character in enumerate(compact)) % 10 == 0
    return False


def _non_isbn_identifier_spans(value: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for owner in _NON_ISBN_IDENTIFIER.finditer(value):
        scheme = owner.group("scheme")
        if (
            scheme.casefold() in {"isbn", "urn:isbn", "print"}
            or _EBOOK_LABEL.fullmatch(scheme) is not None
            or _PRINT_LABEL.fullmatch(scheme) is not None
        ):
            continue
        spans.append(owner.span())
    return tuple(spans)


def _isbn_owned_by_non_isbn_identifier(
    match: re.Match[str], owner_spans: tuple[tuple[int, int], ...]
) -> bool:
    for start, end in owner_spans:
        if start <= match.start() and match.end() <= end:
            return True
    return False


def _date_precision(value: str) -> DatePrecision | None:
    """Validate one exact ISO-like lexical date without inventing precision."""

    if _DATE.fullmatch(value) is None:
        return None
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            return DatePrecision.TIMESTAMP
        pieces = value.split("-")
        if len(pieces) == 1:
            return DatePrecision.YEAR
        if len(pieces) == 2:
            date(int(pieces[0]), int(pieces[1]), 1)
            return DatePrecision.MONTH
        if len(pieces) == 3:
            date(int(pieces[0]), int(pieces[1]), int(pieces[2]))
            return DatePrecision.DAY
    except ValueError:
        return None
    return None


def _mixed_edition_date_spans(value: str) -> tuple[tuple[int, int], ...]:
    """Find same-statement date spans with conflicting work/edition cues."""

    return tuple(
        sorted(
            {
                match.span()
                for pattern in _MIXED_EDITION_DATE_PATTERNS
                for match in pattern.finditer(value)
            }
        )
    )


def _indicia_date_matches(
    value: str,
) -> tuple[tuple[str, DateEvent, DatePrecision, tuple[int, int]], ...]:
    """Recover only locally labelled date events from one bounded indicia span."""

    found: list[
        tuple[int, str, DateEvent, DatePrecision, tuple[int, int]]
    ] = []
    occupied: set[tuple[int, int, DateEvent]] = set()
    ambiguous_spans = _mixed_edition_date_spans(value)
    for event, pattern in _DATE_EVENT_PATTERNS:
        for match in pattern.finditer(value):
            if any(
                start < match.end() and match.start() < end
                for start, end in ambiguous_spans
            ):
                continue
            lexical = match.group("date")
            precision = _date_precision(lexical)
            key = (*match.span("date"), event)
            if precision is None or key in occupied:
                continue
            occupied.add(key)
            found.append(
                (
                    match.start("date"),
                    lexical,
                    event,
                    precision,
                    match.span("date"),
                )
            )
    event_counts: dict[tuple[int, int], set[DateEvent]] = {}
    for _position, _lexical, event, _precision, span in found:
        event_counts.setdefault(span, set()).add(event)
    return tuple(
        (lexical, event, precision, span)
        for _position, lexical, event, precision, span in sorted(
            found, key=lambda item: (item[0], item[2].value)
        )
        if len(event_counts[span]) == 1
    )


def _indicia_dates(
    value: str,
) -> tuple[tuple[str, DateEvent, DatePrecision], ...]:
    return tuple(
        (lexical, event, precision)
        for lexical, event, precision, _span in _indicia_date_matches(value)
    )


def _indicia_credits(
    value: str,
) -> tuple[
    tuple[OpeningField, str, str, EpubObservationApplicability], ...
]:
    """Recover explicit controlled roles while keeping production credits separate."""

    found: list[
        tuple[
            int,
            OpeningField,
            str,
            str,
            EpubObservationApplicability,
            tuple[int, int],
        ]
    ] = []
    occupied: list[tuple[int, int]] = []
    for pattern, roles, applicability in _BIBLIOGRAPHIC_CREDITS:
        for match in pattern.finditer(value):
            if any(
                start < match.end() and match.start() < end
                for start, end in occupied
            ):
                continue
            name = _credit_name(match.group("name"))
            if not name:
                continue
            occupied.append(match.span())
            found.append(
                (
                    match.start(),
                    OpeningField.CONTRIBUTOR,
                    name,
                    "+".join(roles),
                    applicability,
                    match.span(),
                )
            )
    for pattern, role in _CONTEXTUAL_CREDITS:
        for match in pattern.finditer(value):
            name = _credit_name(match.group("name"))
            if not name:
                continue
            found.append(
                (
                    match.start(),
                    OpeningField.CONTEXTUAL_CREDIT,
                    name,
                    role,
                    EpubObservationApplicability.FILE,
                    match.span(),
                )
            )
    return tuple(
        (field, name, role, applicability)
        for _position, field, name, role, applicability, _span in sorted(
            found, key=lambda item: (item[0], item[1].value, item[3])
        )
    )


def _credit_name(value: str) -> str:
    """Stop a credit at prose while preserving dotted personal initials."""

    name = _SPACE.sub(" ", value).strip(" ,:")
    for boundary in re.finditer(r"\.\s+(?=\S)", name):
        preceding = name[: boundary.start()].rstrip()
        token = preceding.rsplit(" ", 1)[-1]
        if len(token) == 1 and token.isalpha():
            continue
        name = preceding
        break
    return name.strip(" ,:")


def _claim(field: OpeningField, value: str, applicability: EpubObservationApplicability, element: _Element, projection: _Projection, source, resource_identity: str, resource_digest: str, selection_path: tuple[str, ...], lineage: str, excerpt_characters: int, *, normalized: str | None = None, identifier_type: str | None = None, applicability_label: str | None = None, citation_context: tuple[_Element, int, int] | None = None, date_event: DateEvent | None = None, date_precision: DatePrecision | None = None, role: str | None = None) -> OpeningClaim | None:
    original = _SPACE.sub(" ", value).strip()
    if not original:
        return None
    try:
        validate_projection_text(original, "opening source value", 280)
        state = "valid"
    except EvidenceValueError:
        state = "oversized" if len(original) > excerpt_characters else "unsupported"
    prefix = "unsafe" if state == "unsupported" else state
    raw = original[:excerpt_characters] if state == "valid" else f"{prefix}-source-text-sha256:" + hashlib.sha256(original.encode("utf-8")).hexdigest()
    if not raw:
        return None
    start = element.start
    if state == "valid" and raw != element.exact_text:
        contextual_relative = (
            projection.text[
                citation_context[1]:citation_context[2]
            ].find(raw)
            if (
                citation_context is not None
                and citation_context[0] is element
            )
            else -1
        )
        relative = element.exact_text.find(raw)
        if contextual_relative >= 0 and citation_context is not None:
            start = citation_context[1] + contextual_relative
        elif relative >= 0:
            start += relative
        else:
            raw = element.exact_text[:excerpt_characters]
    end = start + len(raw)
    citation_element = element
    citation_start = start
    citation_end = end
    semantic_path = f"{element.name}[{element.ordinal}]" + (
        ":" + ".".join(sorted(element.semantics)) if element.semantics else ""
    )
    if citation_context is not None:
        citation_element, citation_start, citation_end = citation_context
        if (
            citation_element is element
            and projection.text[citation_start:citation_end] == raw
        ):
            start, end = citation_start, citation_end
        if not (
            0 <= citation_start <= start
            and end <= citation_end <= len(projection.text)
            and citation_end - citation_start <= excerpt_characters
        ):
            return None
        if citation_element is not element:
            semantic_path = (
                f"{citation_element.name}[{citation_element.ordinal}]"
                f">{element.name}[{element.ordinal}]"
            )
    citation_excerpt = projection.text[citation_start:citation_end]
    leading = len(citation_excerpt) - len(citation_excerpt.lstrip())
    citation_excerpt = citation_excerpt.strip()
    citation_start += leading
    citation_end = citation_start + len(citation_excerpt)
    if not citation_excerpt or (state == "valid" and raw not in citation_excerpt):
        return None
    if state != "valid":
        citation_excerpt = raw
        citation_end = citation_start + len(citation_excerpt)
    normalized_value = (
        normalized
        if state != "valid" and field in {OpeningField.CREATOR_ROLE, OpeningField.CONTRIBUTOR_ROLE}
        else None
        if state != "valid"
        else None
        if field is OpeningField.IDENTIFIER
        and identifier_type in {"isbn_10", "isbn_13"}
        and normalized is None
        and not _isbn_valid(raw)
        else _normal(raw, field) if normalized is None else normalized
    )
    if date_event is not None and state == "valid":
        try:
            normalized_value, inferred_precision = normalize_date_value(raw)
        except ValueError:
            return None
        if inferred_precision is not date_precision:
            return None
    elif date_event is not None:
        normalized_value = None
        date_precision = None
    if normalized_value is not None and len(normalized_value) > excerpt_characters:
        return None
    try:
        citation = OpeningCitation(
            source, resource_identity, resource_digest,
            semantic_path,
            selection_path, lineage, citation_excerpt,
            hashlib.sha256(citation_excerpt.encode("utf-8")).hexdigest(),
            citation_start, citation_end,
            f"element-{citation_element.ordinal}", projection.mode,
            recovery_warnings=projection.warnings,
        )
        return OpeningClaim(
            field,
            raw,
            normalized_value,
            applicability,
            citation,
            identifier_type,
            applicability_label,
            state,
            date_event,
            date_precision,
            role,
        )
    except EvidenceValueError:
        return None


def _association_key(
    association: _RoleAssociation,
) -> tuple[int, int, int, tuple[str, ...]]:
    return (
        _SOURCE_RANK[association.source_tier],
        _ROLE_ORDER.get(association.role, len(_ROLE_ORDER)),
        association.order,
        association.selection_path,
    )


def _fragment_index(projection: _Projection) -> dict[str, list[_Element]]:
    result: dict[str, list[_Element]] = {}
    for element in projection.elements:
        if not _is_html_namespace(element.namespace):
            continue
        for identifier in element.fragment_ids:
            digest = hashlib.sha256(
                unicodedata.normalize("NFC", identifier).encode("utf-8")
            ).hexdigest()
            result.setdefault(digest, []).append(element)
    return result


def _scope_contains(scope: int | None, element: _Element) -> bool:
    return scope is None or scope == element.ordinal or scope in element.ancestors


def _promotion_excluded_ordinals(projection: _Projection) -> frozenset[int]:
    """Bound promotional notices to their forward structural scope."""

    by_ordinal = {element.ordinal: element for element in projection.elements}
    structural = {"section", "article", "aside", "nav"}
    claim_containers = {
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p", "dt", "dd", "li", "address", "figcaption",
    }
    excluded: set[int] = set()
    semantic_scopes = {
        element.ordinal
        for element in projection.elements
        if (
            _is_html_namespace(element.namespace)
            and not element.active
            and element.semantics & _EXCLUDED_SEMANTICS
        )
    }
    notice_starts: dict[int, int] = {}
    uncontained_start: int | None = None
    for notice in projection.elements:
        if (
            notice.active
            or not _is_html_namespace(notice.namespace)
            or _INDICIA_PROMOTION_NOTICE.fullmatch(notice.exact_text) is None
        ):
            continue
        claim_container = next(
            (
                ancestor
                for ancestor in reversed(notice.ancestors)
                if by_ordinal[ancestor].name in claim_containers
            ),
            None,
        )
        container = (
            claim_container
            if claim_container is not None
            else (
                notice.ordinal
                if notice.name in structural
                else next(
                    (
                        ancestor
                        for ancestor in reversed(notice.ancestors)
                        if (
                            _is_html_namespace(
                                by_ordinal[ancestor].namespace
                            )
                            and by_ordinal[ancestor].name in structural
                        )
                    ),
                    None,
                )
            )
        )
        if container is None:
            uncontained_start = (
                notice.ordinal
                if uncontained_start is None
                else min(uncontained_start, notice.ordinal)
            )
        else:
            notice_starts[container] = min(
                notice_starts.get(container, notice.ordinal),
                notice.ordinal,
            )
    for element in projection.elements:
        scopes = (element.ordinal, *element.ancestors)
        bounded = any(by_ordinal[scope].name in structural for scope in scopes)
        if (
            element.ordinal in semantic_scopes
            or any(scope in semantic_scopes for scope in element.ancestors)
            or (
                uncontained_start is not None
                and element.ordinal >= uncontained_start
                and not bounded
            )
            or any(
                element.ordinal >= notice_starts[scope]
                for scope in scopes
                if scope in notice_starts
            )
        ):
            excluded.add(element.ordinal)
    excluded.update(
        ancestor
        for ordinal in tuple(excluded)
        for ancestor in by_ordinal[ordinal].ancestors
        if by_ordinal[ancestor].name in claim_containers
    )
    return frozenset(excluded)


def _recognition_context(
    projection: _Projection,
    candidate: _Candidate,
    body_targets: list[_RoleAssociation],
    excluded_targets: list[_RoleAssociation],
) -> _RecognitionContext:
    fragments = _fragment_index(projection)
    warnings: list[str] = []

    def resolve(
        association: _RoleAssociation,
    ) -> _Element | None | bool:
        if association.fragment is None:
            return None
        matches = fragments.get(association.fragment, [])
        if len(matches) != 1:
            warnings.append("invalid_fragment_target")
            return False
        return matches[0]

    relevant_body = [
        association
        for association in body_targets
        if association.identity == candidate.identity
    ]
    relevant_excluded = [
        association
        for association in excluded_targets
        if association.identity == candidate.identity
    ]
    body_positions = [
        element.ordinal
        for element in projection.elements
        if _is_html_namespace(element.namespace)
        and not element.active
        and element.semantics & _BODY_SEMANTICS
    ]
    for association in relevant_body:
        target = resolve(association)
        if target is False:
            continue
        body_positions.append(0 if target is None else target.ordinal)
    body_ordinal = min(body_positions) if body_positions else None
    body_scopes = tuple(position for position in body_positions if position > 0)

    excluded_scopes: list[int | None] = [
        element.ordinal
        for element in projection.elements
        if _is_html_namespace(element.namespace)
        and not element.active
        and element.semantics & _EXCLUDED_SEMANTICS
    ]
    for association in relevant_excluded:
        target = resolve(association)
        if target is False:
            continue
        excluded_scopes.append(None if target is None else target.ordinal)

    opening_roles = [
        association
        for association in candidate.role_associations
        if association.source_tier != "linear_spine_fallback"
    ]
    resolved_roles: list[tuple[_RoleAssociation, _Element | None]] = []
    for association in opening_roles:
        target = resolve(association)
        if target is not False:
            resolved_roles.append((association, target))
    if resolved_roles:
        canonical_tier = min(
            (association.source_tier for association, _target in resolved_roles),
            key=_SOURCE_RANK.__getitem__,
        )
        tier_roles = [
            (association, target)
            for association, target in resolved_roles
            if association.source_tier == canonical_tier
        ]
    else:
        tier_roles = []
    surviving: list[tuple[_RoleAssociation, _Element | None]] = []
    for association, target in tier_roles:
        position = 0 if target is None else target.ordinal
        if body_ordinal is not None and position >= body_ordinal:
            continue
        if any(
            scope is None
            or (
                target is not None
                and _scope_contains(scope, target)
            )
            for scope in excluded_scopes
        ):
            continue
        surviving.append((association, target))

    if surviving:
        canonical = min(
            (association for association, _target in surviving),
            key=_association_key,
        )
        selection_path = canonical.selection_path
        semantics = frozenset(
            association.role for association, _target in surviving
        )
        title_scopes = tuple(
            None if target is None else target.ordinal
            for association, target in surviving
            if association.role in {"titlepage", "halftitlepage", "title_page"}
        )
        suppress_claims = False
    elif tier_roles:
        selection_path = min(
            (association for association, _target in tier_roles),
            key=_association_key,
        ).selection_path
        semantics = frozenset()
        title_scopes = ()
        suppress_claims = True
    else:
        fallback_roles = [
            association
            for association in candidate.role_associations
            if association.source_tier == "linear_spine_fallback"
        ]
        selection_path = (
            min(
                fallback_roles or opening_roles,
                key=_association_key,
            ).selection_path
            if fallback_roles or opening_roles
            else candidate.selection_path
        )
        semantics = frozenset()
        title_scopes = ()
        suppress_claims = bool(opening_roles and not fallback_roles)
    return _RecognitionContext(
        selection_path,
        semantics,
        title_scopes,
        body_ordinal,
        tuple(dict.fromkeys(body_scopes)),
        tuple(dict.fromkeys(excluded_scopes)),
        tuple(sorted(set(warnings))),
        suppress_claims,
    )


def _recognize(projection: _Projection, source, resource_identity: str, resource_digest: str, context: _RecognitionContext, lineage: str, excerpt_characters: int) -> tuple[list[OpeningClaim], list[str], bool, bool]:
    claims: list[OpeningClaim] = []
    warnings: list[str] = [*projection.warnings, *context.warnings]
    body_transition = context.body_ordinal is not None
    excluded = None in context.excluded_scopes
    if body_transition:
        warnings.append("body_transition_reached")
    if context.excluded_scopes:
        warnings.append("explicitly_excluded_resource")
    if excluded or context.suppress_claims:
        return [], warnings, body_transition, excluded
    by_ordinal = {element.ordinal: element for element in projection.elements}
    promotion_exclusions = _promotion_excluded_ordinals(projection)

    def admissible_context_element(candidate: _Element) -> bool:
        return not (
            candidate.active
            or not candidate.exact_text
            or not _is_html_namespace(candidate.namespace)
            or candidate.ordinal in promotion_exclusions
            or (
                context.body_ordinal is not None
                and candidate.ordinal >= context.body_ordinal
            )
            or any(
                candidate.ordinal in by_ordinal[scope].ancestors
                for scope in context.body_scopes
            )
            or any(
                _scope_contains(scope, candidate)
                or (
                    scope is not None
                    and candidate.ordinal in by_ordinal[scope].ancestors
                )
                for scope in context.excluded_scopes
            )
        )

    eligible_ordinals = {
        candidate.ordinal
        for candidate in projection.elements
        if admissible_context_element(candidate)
    }
    (
        bibliographic_labels,
        bibliographic_label_allocations,
    ) = _bibliographic_indicia_label_allocations(
        projection.elements,
        eligible_ordinals,
    )
    for element in projection.elements:
        text = element.exact_text
        if element.active or not _is_html_namespace(element.namespace):
            continue
        if element.ordinal in promotion_exclusions:
            continue
        if (
            context.body_ordinal is not None
            and element.ordinal >= context.body_ordinal
        ):
            break
        if any(
            element.ordinal in by_ordinal[scope].ancestors
            for scope in context.body_scopes
        ):
            continue
        if any(
            _scope_contains(scope, element)
            or (
                scope is not None
                and element.ordinal in by_ordinal[scope].ancestors
            )
            for scope in context.excluded_scopes
        ):
            continue
        semantic = element.semantics
        if not text or element.end <= element.start:
            continue
        lowered = text.casefold()
        bibliographic_label = _is_bibliographic_indicia_label(text)
        if (
            element.name not in _CLAIM_ELEMENTS
            and not semantic & _CLAIM_SEMANTICS
            and not bibliographic_label
        ):
            continue
        title_context = any(
            scope is None or scope == element.ordinal or scope in element.ancestors
            for scope in context.title_scopes
        )
        title_ancestor = any(
            _is_html_namespace(by_ordinal[ordinal].namespace)
            and by_ordinal[ordinal].semantics & {"titlepage", "halftitlepage"}
            for ordinal in element.ancestors
        )
        candidates: list[
            tuple[
                OpeningField,
                str,
                EpubObservationApplicability,
                str | None,
                str | None,
                str | None,
                tuple[_Element, int, int] | None,
                DateEvent | None,
                DatePrecision | None,
                str | None,
            ]
        ] = []

        def add(
            field: OpeningField,
            value: str,
            applicability: EpubObservationApplicability,
            normalized: str | None = None,
            identifier_type: str | None = None,
            applicability_label: str | None = None,
            citation_context: tuple[_Element, int, int] | None = None,
            date_event: DateEvent | None = None,
            date_precision: DatePrecision | None = None,
            role: str | None = None,
        ) -> None:
            candidates.append(
                (
                    field,
                    value,
                    applicability,
                    normalized,
                    identifier_type,
                    applicability_label,
                    citation_context,
                    date_event,
                    date_precision,
                    role,
                )
            )

        bibliographic_indicia = _bibliographic_indicia(
            semantic,
            text,
            bibliographic_labels.get(element.ordinal, ()),
        )
        for indicia in bibliographic_indicia:
            if indicia.context_span is not None:
                key = _bibliographic_indicia_label_key(element, indicia)
                allocation = bibliographic_label_allocations.get(key)
                if (
                    allocation is None
                    or allocation.emitter_ordinal != element.ordinal
                ):
                    continue
                owner = by_ordinal[allocation.owner_ordinal]
                claim = _claim(
                    indicia.field,
                    indicia.value,
                    (
                        EpubObservationApplicability.WORK
                        if indicia.field is OpeningField.SUBTITLE
                        else EpubObservationApplicability.EDITION
                    ),
                    owner,
                    projection,
                    source,
                    resource_identity,
                    resource_digest,
                    context.selection_path,
                    lineage,
                    excerpt_characters,
                    citation_context=(
                        owner,
                        key[1],
                        key[2],
                    ),
                )
                if claim is None:
                    warnings.append("unsafe_claim_suppressed")
                else:
                    claims.append(claim)
                continue
            add(
                indicia.field,
                indicia.value,
                (
                    EpubObservationApplicability.WORK
                    if indicia.field is OpeningField.SUBTITLE
                    else EpubObservationApplicability.EDITION
                ),
            )
        if not bibliographic_label and not bibliographic_indicia and (
            semantic & {"fulltitle", "title"}
            or (element.name == "h1" and (title_context or title_ancestor))
        ):
            add(OpeningField.TITLE, text, EpubObservationApplicability.WORK)
        if (
            not bibliographic_label
            and not bibliographic_indicia
            and lowered.endswith(" imprint")
        ):
            add(OpeningField.IMPRINT, text, EpubObservationApplicability.EDITION)
        if lowered.startswith(("by ", "author: ", "written by ")):
            value = re.sub(r"^(?:by|author:|written by)\s+", "", text, flags=re.I)
            add(
                OpeningField.CREATOR,
                value,
                EpubObservationApplicability.WORK,
                role="author",
            )
            add(OpeningField.CREATOR_ROLE, text, EpubObservationApplicability.WORK, "author")
        recovered_credits = _indicia_credits(text)
        bibliographic_credit = False
        for credit_field, credit_value, credit_role, credit_applicability in recovered_credits:
            if credit_field is OpeningField.CONTRIBUTOR:
                bibliographic_credit = True
                add(
                    credit_field,
                    credit_value,
                    credit_applicability,
                    role=credit_role,
                )
                add(
                    OpeningField.CONTRIBUTOR_ROLE,
                    text,
                    credit_applicability,
                    credit_role,
                )
                role_parts = credit_role.split("+")
                if "translator" in role_parts:
                    add(
                        OpeningField.TRANSLATION,
                        text,
                        EpubObservationApplicability.EDITION,
                    )
                if "illustrator" in role_parts:
                    add(
                        OpeningField.ILLUSTRATION,
                        text,
                        EpubObservationApplicability.EDITION,
                    )
            else:
                add(
                    OpeningField.CONTEXTUAL_CREDIT,
                    credit_value,
                    credit_applicability,
                    role=credit_role,
                )
        explicit_semantics = _tokens(element.attributes)
        if "creator" in explicit_semantics and not lowered.startswith(("by ", "author: ", "written by ")):
            add(OpeningField.CREATOR, text, EpubObservationApplicability.WORK)
        elif "contributor" in explicit_semantics and not bibliographic_credit:
            add(OpeningField.CONTRIBUTOR, text, EpubObservationApplicability.WORK)
        if "edition" in lowered:
            add(OpeningField.EDITION, text, EpubObservationApplicability.EDITION)
        if "printing" in lowered or "impression" in lowered:
            add(OpeningField.PRINTING, text, EpubObservationApplicability.EDITION)
        if "revision" in lowered or "revised" in lowered:
            add(OpeningField.REVISION, text, EpubObservationApplicability.EDITION)
        if _mixed_edition_date_spans(text):
            warnings.append("ambiguous_date_event")
        for lexical, event, precision, date_span in _indicia_date_matches(text):
            field = (
                OpeningField.PUBLICATION_DATE
                if event is DateEvent.MANIFESTATION_PUBLICATION
                else OpeningField.DATE_EVENT
            )
            add(
                field,
                lexical,
                (
                    EpubObservationApplicability.EDITION
                    if event
                    in {
                        DateEvent.MANIFESTATION_PUBLICATION,
                        DateEvent.COPYRIGHT,
                        DateEvent.MODIFICATION,
                        DateEvent.CONVERSION,
                    }
                    else EpubObservationApplicability.WORK
                ),
                citation_context=(
                    element,
                    element.start + date_span[0],
                    element.start + date_span[1],
                ),
                date_event=event,
                date_precision=precision,
            )
            if event is DateEvent.COPYRIGHT:
                add(
                    OpeningField.COPYRIGHT_DATE,
                    lexical,
                    EpubObservationApplicability.EDITION,
                    citation_context=(
                        element,
                        element.start + date_span[0],
                        element.start + date_span[1],
                    ),
                )
        series = _SERIES.search(text)
        if series:
            add(OpeningField.SERIES_ORDER, series.group(1), EpubObservationApplicability.WORK)
            add(OpeningField.SERIES, series.group(2), EpubObservationApplicability.WORK)
        if "omnibus" in lowered:
            add(OpeningField.OMNIBUS_MEMBERSHIP, text, EpubObservationApplicability.WORK)
        if "anthology" in lowered:
            add(OpeningField.ANTHOLOGY_MEMBERSHIP, text, EpubObservationApplicability.WORK)
        isbn_matches = list(_ISBN.finditer(text))
        owner_spans = _non_isbn_identifier_spans(text)
        for match_index, match in enumerate(isbn_matches):
            if _isbn_owned_by_non_isbn_identifier(match, owner_spans):
                continue
            identifier = match.group(0)
            compact = _normal(identifier, OpeningField.IDENTIFIER)
            if len(compact) not in {10, 13}:
                continue
            identifier_valid = _isbn_valid(identifier)
            left_boundary = isbn_matches[match_index - 1].end() if match_index else max(0, match.start() - 64)
            right_boundary = isbn_matches[match_index + 1].start() if match_index + 1 < len(isbn_matches) else min(len(text), match.end() + 64)
            separator = r"[;,|/\n\u2022]"
            left_vicinity = re.split(separator, lowered[left_boundary:match.start()])[-1]
            right_vicinity = re.split(separator, lowered[match.end():right_boundary])[0]
            if match_index + 1 < len(isbn_matches):
                right_label = _EBOOK_LABEL.search(right_vicinity) or _PRINT_LABEL.search(right_vicinity)
                if right_label is not None and right_vicinity[right_label.end():].strip() in {"isbn", "isbn-10", "isbn-13"}:
                    right_vicinity = right_vicinity[:right_label.start()]
            left_has_labels = bool(
                _EBOOK_LABEL.search(left_vicinity)
                or _PRINT_LABEL.search(left_vicinity)
            )
            right_has_labels = bool(
                _EBOOK_LABEL.search(right_vicinity)
                or _PRINT_LABEL.search(right_vicinity)
            )
            if left_has_labels and right_has_labels:
                left_ebook = bool(_EBOOK_LABEL.search(left_vicinity))
                left_print = bool(_PRINT_LABEL.search(left_vicinity))
                right_ebook = bool(_EBOOK_LABEL.search(right_vicinity))
                right_print = bool(_PRINT_LABEL.search(right_vicinity))
                vicinity = (
                    left_vicinity
                    if (left_ebook, left_print) == (right_ebook, right_print)
                    else left_vicinity + " " + right_vicinity
                )
            elif left_has_labels:
                vicinity = left_vicinity
            else:
                vicinity = right_vicinity
            if not (
                _EBOOK_LABEL.search(vicinity) or _PRINT_LABEL.search(vicinity)
            ):
                previous = next(
                    (
                        candidate
                        for candidate in reversed(projection.elements)
                        if candidate.end <= element.start
                        and admissible_context_element(candidate)
                    ),
                    None,
                )
                following = next(
                    (
                        candidate
                        for candidate in projection.elements
                        if candidate.start >= element.end
                        and admissible_context_element(candidate)
                    ),
                    None,
                )
                previous_text = "" if previous is None else previous.exact_text.casefold()[-48:]
                following_text = "" if following is None else following.exact_text.casefold()[:48]
                if _EBOOK_LABEL.search(previous_text) or _PRINT_LABEL.search(previous_text):
                    neighbor = previous
                    neighbor_before = True
                    vicinity = previous_text + " " + vicinity
                elif _EBOOK_LABEL.search(following_text) or _PRINT_LABEL.search(following_text):
                    neighbor = following
                    neighbor_before = False
                    vicinity = vicinity + " " + following_text
                else:
                    neighbor = None
                    neighbor_before = False
            else:
                neighbor = None
                neighbor_before = False
            ebook = bool(_EBOOK_LABEL.search(vicinity))
            printed = bool(_PRINT_LABEL.search(vicinity))
            if ebook and not printed:
                applicability = EpubObservationApplicability.EDITION
                applicability_label = "ebook"
            elif printed and not ebook:
                applicability = EpubObservationApplicability.INAPPLICABLE
                applicability_label = "paperback" if _PAPERBACK_LABEL.search(vicinity) else "hardcover" if _HARDCOVER_LABEL.search(vicinity) else "print"
            elif ebook and printed:
                applicability = EpubObservationApplicability.UNKNOWN
                applicability_label = "ambiguous"
            else:
                applicability = EpubObservationApplicability.UNKNOWN
                applicability_label = "unlabelled"
            citation_context: tuple[_Element, int, int] | None = None
            if left_has_labels or right_has_labels:
                citation_context = (
                    element,
                    element.start + left_boundary,
                    element.start + right_boundary,
                )
            elif neighbor is not None and (ebook or printed):
                shared = set(neighbor.ancestors) & set(element.ancestors)
                context_element = next(
                    (
                        by_ordinal[ordinal]
                        for ordinal in reversed(neighbor.ancestors)
                        if ordinal in shared
                    ),
                    None,
                )
                context_start = (
                    max(neighbor.start, neighbor.end - 48)
                    if neighbor_before
                    else element.start + left_boundary
                )
                context_end = (
                    element.start + right_boundary
                    if neighbor_before
                    else min(neighbor.end, neighbor.start + 48)
                )
                if (
                    context_element is not None
                    and context_end - context_start <= excerpt_characters
                ):
                    citation_context = (
                        context_element,
                        context_start,
                        context_end,
                    )
                else:
                    applicability = EpubObservationApplicability.UNKNOWN
                    applicability_label = "unlabelled"
            add(
                OpeningField.IDENTIFIER,
                identifier,
                applicability,
                compact if identifier_valid else None,
                "isbn_10" if len(compact) == 10 else "isbn_13",
                applicability_label,
                citation_context,
            )
            if applicability is EpubObservationApplicability.UNKNOWN:
                warnings.append("identifier_applicability_unknown")
        for (
            field,
            value,
            applicability,
            normalized,
            identifier_type,
            applicability_label,
            citation_context,
            date_event,
            date_precision,
            role,
        ) in candidates:
            claim = _claim(
                field,
                value,
                applicability,
                element,
                projection,
                source,
                resource_identity,
                resource_digest,
                context.selection_path,
                lineage,
                excerpt_characters,
                normalized=normalized,
                identifier_type=identifier_type,
                applicability_label=applicability_label,
                citation_context=citation_context,
                date_event=date_event,
                date_precision=date_precision,
                role=role,
            )
            if claim is None:
                warnings.append("unsafe_claim_suppressed")
            else:
                claims.append(claim)
    values: dict[OpeningField, set[str]] = {}
    for claim in claims:
        values.setdefault(claim.field, set()).add(claim.normalized_value or claim.raw_value)
    for field, warning in ((OpeningField.TITLE, "ambiguous_title"), (OpeningField.IDENTIFIER, "ambiguous_identifier"), (OpeningField.EDITION, "ambiguous_edition"), (OpeningField.SERIES, "contradictory_series")):
        if len(values.get(field, set())) > 1:
            warnings.append(warning)
    return claims, warnings, body_transition, False


class EpubOpeningExtractor:
    """Compose F06 package validation with bounded F07a opening evidence."""

    def extract(
        self,
        request: EpubPackageRequest,
        limits: EpubOpeningLimits,
        package_report: EpubPackageReport,
        archive: zipfile.ZipFile | None = None,
    ) -> EpubOpeningReport:
        if (
            type(request) is not EpubPackageRequest
            or type(limits) is not EpubOpeningLimits
            or type(package_report) is not EpubPackageReport
            or request.capability is not EpubPackageCapability.OPENING_MATTER
            or request.limits != limits.package
            or package_report.capability is not EpubPackageCapability.PACKAGE
            or package_report.limits != limits.package
            or (
                package_report.source_fingerprint is not None
                and package_report.source_fingerprint != request.expected_source
            )
        ):
            return EpubOpeningReport(EpubInspectionOutcome.INTERNAL_FAILURE, limits)
        if not package_report.succeeded:
            return EpubOpeningReport(package_report.outcome, limits)
        if package_report.outcome is EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE:
            return EpubOpeningReport(EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE, limits, request.expected_source)
        try:
            return self._extract_validated(request, limits, package_report, archive)
        except _AggregateRejected as error:
            return EpubOpeningReport(error.outcome, limits)
        except EpubPackageSourceError:
            return EpubOpeningReport(EpubInspectionOutcome.UNAVAILABLE_SOURCE, limits)
        except (MemoryError, RecursionError):
            return EpubOpeningReport(EpubInspectionOutcome.RESOURCE_LIMIT, limits)
        except Exception:
            return EpubOpeningReport(EpubInspectionOutcome.INTERNAL_FAILURE, limits)

    def _extract_validated(self, request: EpubPackageRequest, limits: EpubOpeningLimits, package_report: EpubPackageReport, archive: zipfile.ZipFile | None = None) -> EpubOpeningReport:
        archive_context = nullcontext(archive) if archive is not None else zipfile.ZipFile(_SourceIO(request), "r")
        with archive_context as archive:
            assert archive is not None
            package_lineage = epub_file_lineage(request.expected_source)
            names_by_identity = {epub_resource_identity(info.filename): info.filename for info in archive.infolist() if not info.is_dir()}
            (
                candidates,
                warnings,
                pre_resources,
                navigation_links,
                encrypted,
                body_targets,
                excluded_targets,
                navigation_usage,
            ) = _candidate_graph(
                package_report, archive, names_by_identity, limits, request
            )
            claims: list[OpeningClaim] = []
            resources: list[OpeningResourceResult] = list(pre_resources)
            (
                expanded,
                text_characters,
                nodes,
                opened,
                strict,
                recovered,
            ) = navigation_usage
            selected = 0
            if (
                expanded > limits.expanded_bytes_per_selection
                or text_characters > limits.text_characters_per_selection
                or nodes > limits.nodes_per_selection
            ):
                raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)

            for candidate in candidates:
                _check(request)
                if selected >= limits.selected_documents:
                    warnings.append("selected_document_limit_reached")
                    break
                selected += 1
                if candidate.identity in encrypted:
                    resources.append(OpeningResourceResult(candidate.identity, OpeningResourceDisposition.ENCRYPTED, candidate.selection_path))
                    warnings.append("encrypted_optional_resource")
                    continue
                name = names_by_identity.get(candidate.identity)
                if name is None:
                    resources.append(OpeningResourceResult(candidate.identity, OpeningResourceDisposition.MISSING, candidate.selection_path))
                    warnings.append("missing_selected_resource")
                    continue
                if candidate.media_type in _IMAGE_MEDIA:
                    resources.append(OpeningResourceResult(candidate.identity, OpeningResourceDisposition.IMAGE_ONLY, candidate.selection_path))
                    warnings.append("image_only_resource")
                    continue
                if candidate.media_type not in _CONTENT_MEDIA:
                    resources.append(OpeningResourceResult(candidate.identity, OpeningResourceDisposition.UNSUPPORTED_MEDIA, candidate.selection_path))
                    warnings.append("unsupported_resource_media")
                    continue
                data: bytes | None = None
                try:
                    opened += 1
                    data = _read_member(archive, name, limits.expanded_bytes_per_resource, request)
                    expanded += len(data)
                    if expanded > limits.expanded_bytes_per_selection:
                        raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
                    projection = _parse(data, candidate.media_type, limits, request)
                    nodes += projection.work_nodes
                    text_characters += projection.work_text_characters
                    if nodes > limits.nodes_per_selection or text_characters > limits.text_characters_per_selection:
                        raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
                    if projection.mode is OpeningParseMode.STRICT:
                        strict += 1
                    else:
                        recovered += 1
                    context = _recognition_context(
                        projection, candidate, body_targets, excluded_targets
                    )
                    resource_digest = hashlib.sha256(data).hexdigest()
                    resource_claims, resource_warnings, body_transition, excluded = _recognize(
                        projection, request.expected_source, candidate.identity,
                        resource_digest, context,
                        package_lineage,
                        limits.excerpt_characters,
                    )
                    claims.extend(resource_claims)
                    warnings.extend(resource_warnings)
                    disposition = OpeningResourceDisposition.EXCLUDED if excluded else OpeningResourceDisposition.SELECTED if resource_claims else OpeningResourceDisposition.NO_CLAIMS
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            disposition,
                            context.selection_path,
                            projection.mode,
                            tuple(sorted(set(resource_warnings))),
                            opened=True,
                            resource_digest=resource_digest,
                        )
                    )
                    if body_transition:
                        warnings.append("body_transition_reached")
                        break
                except _AggregateRejected:
                    raise
                except _ResourceRejected as error:
                    nodes += error.nodes
                    text_characters += error.text_characters
                    if (
                        nodes > limits.nodes_per_selection
                        or text_characters > limits.text_characters_per_selection
                    ):
                        raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
                    code = "selected_resource_limit"
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            OpeningResourceDisposition.RESOURCE_LIMIT,
                            candidate.selection_path,
                            warnings=(code,),
                            opened=True,
                        )
                    )
                    warnings.append(code)
                except _UnsafeConstruct as error:
                    nodes += error.nodes
                    text_characters += error.text_characters
                    if (
                        nodes > limits.nodes_per_selection
                        or text_characters > limits.text_characters_per_selection
                    ):
                        raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
                    code = "unsafe_construct"
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            OpeningResourceDisposition.MALFORMED,
                            candidate.selection_path,
                            warnings=(code,),
                            opened=True,
                        )
                    )
                    warnings.append(code)
                except _UnsupportedEncoding as error:
                    nodes += error.nodes
                    text_characters += error.text_characters
                    if (
                        nodes > limits.nodes_per_selection
                        or text_characters > limits.text_characters_per_selection
                    ):
                        raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
                    code = "unsupported_encoding"
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            OpeningResourceDisposition.UNSUPPORTED_ENCODING,
                            candidate.selection_path,
                            warnings=(code,),
                            opened=True,
                        )
                    )
                    warnings.append(code)
                except _MalformedMarkup as error:
                    nodes += error.nodes
                    text_characters += error.text_characters
                    if (
                        nodes > limits.nodes_per_selection
                        or text_characters > limits.text_characters_per_selection
                    ):
                        raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
                    code = "malformed_selected_resource"
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            OpeningResourceDisposition.MALFORMED,
                            candidate.selection_path,
                            warnings=(code,),
                            opened=True,
                        )
                    )
                    warnings.append(code)
                except ValueError:
                    code = "malformed_selected_resource"
                    resources.append(OpeningResourceResult(candidate.identity, OpeningResourceDisposition.MALFORMED, candidate.selection_path, warnings=(code,), opened=True))
                    warnings.append(code)
            fact_limit_reached = len(claims) > limits.facts
            if fact_limit_reached:
                claims = claims[: limits.facts]
            unique_warning_values = sorted(set(warnings))
            if (
                fact_limit_reached
                and len(unique_warning_values) <= limits.warnings
            ):
                marker = "opening_fact_limit"
                unique_warning_values = [
                    warning
                    for warning in unique_warning_values
                    if warning != marker
                ][: limits.warnings - 1]
                unique_warning_values.append(marker)
            unique_warnings = tuple(sorted(unique_warning_values))
            if len(unique_warnings) > limits.warnings:
                raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT)
            usage = EpubOpeningUsage(selected, opened, expanded, text_characters, nodes, navigation_links, strict, recovered)
            outcome = EpubInspectionOutcome.SUCCESS if claims else EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
            try:
                return EpubOpeningReport(
                    outcome, limits, request.expected_source, tuple(claims),
                    tuple(resources), unique_warnings, usage,
                    same_file_lineage=package_lineage,
                )
            except EvidenceValueError as error:
                if "byte limit" in str(error):
                    raise _AggregateRejected(EpubInspectionOutcome.RESOURCE_LIMIT) from None
                raise


__all__ = ["EpubOpeningExtractor"]
