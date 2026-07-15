"""Bounded EPUB opening-resource selection, projection, and claim extraction."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import time
import unicodedata
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree
from xml.parsers import expat

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
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.local_evidence import (
    DeclarationKind,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
    LocalEvidenceRequest,
    LocalEvidenceSourceError,
    ObservationApplicability,
    local_file_lineage,
    local_resource_identity,
)


_CONTENT_MEDIA = {"application/xhtml+xml", "text/html"}
_IMAGE_MEDIA = {"image/gif", "image/jpeg", "image/png", "image/svg+xml"}
_ACTIVE = {"script", "style", "template", "object", "iframe", "frame", "embed", "canvas"}
_EPUB_NS = "http://www.idpf.org/2007/ops"
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
_BODY_SEMANTICS = {"bodymatter"}
_CLAIM_SEMANTICS = {"title", "fulltitle", "subtitle", "publisher", "imprint", "contributor", "creator"}
_SPACE = re.compile(r"\s+")
_ISBN = re.compile(r"(?<![0-9Xx])(?:97[89][\s-]*)?[0-9][0-9Xx\s-]{8,20}[0-9Xx](?![0-9Xx])")
_DATE = re.compile(r"\b(?:18|19|20|21)\d{2}(?:-[01]\d(?:-[0-3]\d)?)?\b")
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
_DECLARED_ENCODING = re.compile(
    rb"(?:encoding\s*=\s*['\"]|charset\s*=\s*['\"]?)([A-Za-z0-9._-]+)",
    re.I,
)
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
    def __init__(self, outcome: LocalEvidenceOutcome) -> None:
        self.outcome = outcome


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
class _Candidate:
    identity: str
    media_type: str
    selection_path: tuple[str, ...]
    order: int


class _SourceIO(io.RawIOBase):
    def __init__(self, request: LocalEvidenceRequest) -> None:
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


def _check(request: LocalEvidenceRequest) -> None:
    if request.cancellation.is_cancelled():
        raise _AggregateRejected(LocalEvidenceOutcome.CANCELLATION)
    if time.monotonic() >= request.monotonic_deadline:
        raise _AggregateRejected(LocalEvidenceOutcome.TIMEOUT)


def _tokens(attributes: dict[str, str]) -> set[str]:
    value = attributes.get(f"{{{_EPUB_NS}}}type", attributes.get("epub:type", ""))
    return {token.casefold() for token in value.split() if ":" not in token}


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


def _project_strict(data: bytes, limits: EpubOpeningLimits, request: LocalEvidenceRequest) -> _Projection:
    elements: list[_Element] = []
    stack: list[_Element] = []
    events: list[tuple[str, tuple[int, ...]]] = []
    try:
        parser = expat.ParserCreate(namespace_separator="}")
        if not parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER):
            raise ValueError("entity parsing could not be disabled")
    except (AttributeError, MemoryError):
        raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT) from None

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
            len(elements) + 1, local_name, mapped, _tokens(mapped), len(stack) + 1,
            active=parent_active or hidden or local_name in _ACTIVE,
            ancestors=tuple(value.ordinal for value in stack),
            namespace=namespace,
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
    def __init__(self, limits: EpubOpeningLimits, request: LocalEvidenceRequest) -> None:
        super().__init__(convert_charrefs=True)
        self.limits = limits
        self.request = request
        self.elements: list[_Element] = []
        self.stack: list[_Element] = []
        self.events: list[tuple[str, tuple[int, ...]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _check(self.request)
        raw_attributes = {
            name.casefold(): "" if value is None else value for name, value in attrs
        }
        namespaces = dict(self.stack[-1].namespaces) if self.stack else {}
        for name, value in raw_attributes.items():
            if name == "xmlns":
                namespaces[""] = value
            elif name.startswith("xmlns:"):
                namespaces[name.partition(":")[2]] = value
        raw_name = tag.casefold()
        prefix, separator, unqualified = raw_name.partition(":")
        local_name = unqualified if separator else raw_name
        namespace = namespaces.get(prefix if separator else "", "")
        if namespace in {_XINCLUDE_NS, _XSLT_NS}:
            raise _UnsafeConstruct(
                "unsupported foreign inclusion construct",
                nodes=len(self.elements),
                text_characters=_projected_text_usage(self.events),
            )
        mapped: dict[str, str] = {}
        for name, value in raw_attributes.items():
            attribute_prefix, attribute_separator, attribute_local = name.partition(":")
            attribute_namespace = namespaces.get(attribute_prefix, "") if attribute_separator else ""
            mapped[
                f"{{{attribute_namespace}}}{attribute_local}"
                if attribute_namespace
                else name
            ] = value
        for opened, closing in _HTML_IMPLIED_CLOSE.items():
            if local_name not in closing:
                continue
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index].name == opened:
                    del self.stack[index:]
                    break
        if len(self.elements) >= self.limits.nodes_per_resource or len(self.stack) + 1 > self.limits.document_depth:
            raise _ResourceRejected(
                nodes=len(self.elements),
                text_characters=_projected_text_usage(self.events),
            )
        parent_active = bool(self.stack and self.stack[-1].active)
        hidden = (
            "hidden" in mapped
            or mapped.get("aria-hidden", "").casefold() == "true"
            or (
                local_name == "input"
                and mapped.get("type", "").casefold() == "hidden"
            )
        )
        element = _Element(
            len(self.elements) + 1, local_name, mapped, _tokens(mapped), len(self.stack) + 1,
            active=parent_active or hidden or local_name in _ACTIVE,
            ancestors=tuple(value.ordinal for value in self.stack),
            namespaces=namespaces,
            namespace=namespace,
        )
        self.elements.append(element)
        if local_name in {"br", "wbr"} and self.stack and not self.stack[-1].active:
            self.events.append((" ", tuple(value.ordinal for value in self.stack)))
        if local_name not in _HTML_VOID or namespace not in {"", "http://www.w3.org/1999/xhtml"}:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        raw_name = tag.casefold()
        prefix, separator, unqualified = raw_name.partition(":")
        wanted = unqualified if separator else raw_name
        namespaces = self.stack[-1].namespaces if self.stack else {}
        namespace = namespaces.get(prefix if separator else "", "")
        for index in range(len(self.stack) - 1, -1, -1):
            if (
                self.stack[index].name == wanted
                and self.stack[index].namespace == namespace
            ):
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


def _project_recovered(data: bytes, limits: EpubOpeningLimits, request: LocalEvidenceRequest) -> _Projection:
    if data.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00<")):
        raise _UnsupportedEncoding(
            "unsupported encoding", nodes=0, text_characters=0
        )
    declared_match = _DECLARED_ENCODING.search(data[:2_048])
    declared = (
        declared_match.group(1).decode("ascii", "strict").casefold()
        if declared_match is not None
        else "utf-8"
    )
    codecs = {
        "utf-8": "utf-8-sig",
        "utf8": "utf-8-sig",
        "windows-1252": "windows-1252",
        "cp1252": "windows-1252",
        "iso-8859-1": "iso-8859-1",
        "latin-1": "iso-8859-1",
    }
    codec = codecs.get(declared)
    if codec is None:
        raise _UnsupportedEncoding(
            "unsupported encoding", nodes=0, text_characters=0
        )
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
                raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
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


def _parse(data: bytes, media_type: str, limits: EpubOpeningLimits, request: LocalEvidenceRequest) -> _Projection:
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


def _read_member(archive: zipfile.ZipFile, name: str, maximum: int, request: LocalEvidenceRequest) -> bytes:
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


def _safe_target(base_name: str, href: str, names: set[str]) -> str | None:
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
    return normalized


def _manifest(package_report: LocalEvidenceReport) -> tuple[dict[str, tuple[str, str, tuple[str, ...]]], dict[str, str], set[str], str | None]:
    by_id: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    spine: dict[str, str] = {}
    encrypted: set[str] = set()
    package_identity: str | None = None
    for declaration in package_report.declarations:
        if declaration.kind is DeclarationKind.MANIFEST_ITEM and declaration.identifier and declaration.resource_identity and declaration.media_type:
            by_id[declaration.identifier] = (declaration.resource_identity, declaration.media_type, declaration.properties)
        elif declaration.kind is DeclarationKind.SPINE_ITEM and declaration.identifier and declaration.resource_identity:
            spine[declaration.identifier] = declaration.resource_identity
        elif declaration.kind is DeclarationKind.ENCRYPTION and declaration.resource_identity:
            encrypted.add(declaration.resource_identity)
        elif declaration.kind is DeclarationKind.PACKAGE:
            package_identity = declaration.resource_identity
    return by_id, spine, encrypted, package_identity


def _raw_spine(package_data: bytes) -> tuple[tuple[str, bool], ...]:
    if b"<!DOCTYPE" in package_data.upper() or b"<!ENTITY" in package_data.upper():
        return ()
    try:
        root = ElementTree.fromstring(package_data)
    except (ElementTree.ParseError, RecursionError):
        return ()
    result: list[tuple[str, bool]] = []
    for node in root.iter():
        if _local(node.tag) == "itemref":
            identifier = node.attrib.get("idref")
            if identifier:
                result.append((identifier, node.attrib.get("linear", "yes").casefold() != "no"))
    return tuple(result)


def _navigation_targets(projection: _Projection, nav_name: str, names: set[str], identity_by_name: dict[str, str], manifest_by_identity: dict[str, tuple[str, tuple[str, ...]]], limits: EpubOpeningLimits) -> tuple[list[_Candidate], list[str], int, set[str], set[str]]:
    targets: list[_Candidate] = []
    warnings: list[str] = []
    links = 0
    seen_by_semantic: dict[str, set[str]] = {}
    body_targets: set[str] = set()
    excluded_targets: set[str] = set()
    by_ordinal = {element.ordinal: element for element in projection.elements}
    for element in projection.elements:
        if element.active or element.name != "a" or "href" not in element.attributes:
            continue
        links += 1
        if links > limits.navigation_links:
            raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
        landmarks = any(
            ancestor.name == "nav"
            and not ancestor.active
            and "landmarks" in ancestor.semantics
            for ordinal in element.ancestors
            if (ancestor := by_ordinal[ordinal])
        )
        if not landmarks:
            continue
        semantics = element.semantics & (
            _OPENING_SEMANTICS | _BODY_SEMANTICS | _EXCLUDED_SEMANTICS
        )
        if not semantics:
            continue
        name = _safe_target(nav_name, element.attributes["href"], names)
        if name is None:
            warnings.append("invalid_navigation_target")
            continue
        identity = identity_by_name[name]
        declared = manifest_by_identity.get(identity)
        if declared is None:
            warnings.append("unmanifested_navigation_target")
            continue
        media_type, _properties = declared
        if semantics & _BODY_SEMANTICS:
            body_targets.add(identity)
        if semantics & _EXCLUDED_SEMANTICS:
            excluded_targets.add(identity)
        for semantic in sorted(semantics):
            if semantic not in _OPENING_SEMANTICS:
                continue
            seen_by_semantic.setdefault(semantic, set()).add(identity)
            targets.append(_Candidate(identity, media_type, ("epub3_landmark", semantic), element.ordinal))
    if any(len(values) > 1 for values in seen_by_semantic.values()):
        warnings.append("ambiguous_navigation_landmark")
    return targets, warnings, links, body_targets, excluded_targets


def _candidate_graph(package_report: LocalEvidenceReport, archive: zipfile.ZipFile, names_by_identity: dict[str, str], limits: EpubOpeningLimits, request: LocalEvidenceRequest) -> tuple[list[_Candidate], list[str], list[OpeningResourceResult], int, set[str], set[str], set[str], tuple[int, int, int, int, int, int]]:
    by_id, declared_spine, encrypted, package_identity = _manifest(package_report)
    manifest_by_identity = {identity: (media, properties) for identity, media, properties in by_id.values()}
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
    body_targets: set[str] = set()
    excluded_targets: set[str] = set()
    navigation = [value for value in package_report.declarations if value.kind is DeclarationKind.NAVIGATION and value.resource_identity]
    if (
        len(navigation) == 1
        and navigation[0].resource_identity in names_by_identity
        and navigation[0].resource_identity not in encrypted
    ):
        identity = navigation[0].resource_identity
        name = names_by_identity[identity]
        data: bytes | None = None
        try:
            data = _read_member(archive, name, limits.expanded_bytes_per_resource, request)
            navigation_expanded = len(data)
            navigation_opened = 1
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
            body_targets.update(nav_body_targets)
            excluded_targets.update(nav_excluded_targets)
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
                    opened=data is not None,
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
    for declaration in package_report.declarations:
        if declaration.kind is not DeclarationKind.GUIDE_REFERENCE or not declaration.resource_identity or not declaration.identifier:
            continue
        semantic = declaration.identifier.casefold()
        if semantic in {"text", "bodymatter"}:
            body_targets.add(declaration.resource_identity)
            continue
        if semantic in {"preview", "sample", "advertisement"}:
            excluded_targets.add(declaration.resource_identity)
            continue
        if semantic not in {"title-page", "copyright-page", "dedication", "acknowledgements", "preface", "foreword", "other.ms-firstpage"}:
            continue
        declared = manifest_by_identity.get(declaration.resource_identity)
        if declared is None:
            warnings.append("unmanifested_guide_target")
            continue
        candidates.append(_Candidate(declaration.resource_identity, declared[0], ("epub2_guide", semantic.replace("-", "_")), declaration.source_ordinal))
    guide_targets: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.selection_path[0] == "epub2_guide":
            guide_targets.setdefault(candidate.selection_path[-1], set()).add(candidate.identity)
    if any(len(values) > 1 for values in guide_targets.values()):
        warnings.append("ambiguous_guide_reference")
    raw_spine: tuple[tuple[str, bool], ...] = ()
    if package_identity in names_by_identity:
        try:
            package_data = _read_member(archive, names_by_identity[package_identity], limits.package.opf_package_bytes, request)
            raw_spine = _raw_spine(package_data)
        except (_ResourceRejected, ValueError):
            warnings.append("unusable_spine_relationship")
    if not raw_spine:
        raw_spine = tuple((identifier, True) for identifier in declared_spine)
    for order, (identifier, linear) in enumerate(raw_spine, 1):
        declared = by_id.get(identifier)
        if not linear or declared is None:
            continue
        identity, media_type, _properties = declared
        candidates.append(_Candidate(identity, media_type, ("linear_spine_fallback",), 1_000_000 + order))
    deduplicated: list[_Candidate] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda value: (0 if value.selection_path[0] == "epub3_landmark" else 1 if value.selection_path[0] == "epub2_guide" else 2, value.order)):
        if candidate.identity not in seen:
            seen.add(candidate.identity)
            deduplicated.append(candidate)
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


def _isbn_valid(value: str) -> bool:
    compact = "".join(character for character in value.upper() if character.isdigit() or character == "X")
    if len(compact) == 10 and compact[:9].isdigit() and (compact[9].isdigit() or compact[9] == "X"):
        return sum((10 - index) * (10 if character == "X" else int(character)) for index, character in enumerate(compact)) % 11 == 0
    if len(compact) == 13 and compact.isdigit():
        return sum(int(character) * (1 if index % 2 == 0 else 3) for index, character in enumerate(compact)) % 10 == 0
    return False


def _claim(field: OpeningField, value: str, applicability: ObservationApplicability, element: _Element, projection: _Projection, source, resource_identity: str, resource_digest: str, selection_path: tuple[str, ...], lineage: str, excerpt_characters: int, *, normalized: str | None = None, identifier_type: str | None = None, applicability_label: str | None = None, citation_context: tuple[_Element, int, int] | None = None) -> OpeningClaim | None:
    raw = _SPACE.sub(" ", value).strip()[:excerpt_characters]
    if not raw:
        return None
    start = element.start
    if raw != element.exact_text:
        relative = element.exact_text.find(raw)
        if relative >= 0:
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
        if not (
            0 <= citation_start <= start
            and end <= citation_end <= len(projection.text)
            and citation_end - citation_start <= excerpt_characters
        ):
            return None
        semantic_path = (
            f"{citation_element.name}[{citation_element.ordinal}]"
            f">{element.name}[{element.ordinal}]"
        )
    citation_excerpt = projection.text[citation_start:citation_end]
    leading = len(citation_excerpt) - len(citation_excerpt.lstrip())
    citation_excerpt = citation_excerpt.strip()
    citation_start += leading
    citation_end = citation_start + len(citation_excerpt)
    if not citation_excerpt or raw not in citation_excerpt:
        return None
    normalized_value = _normal(raw, field) if normalized is None else normalized
    if len(normalized_value) > excerpt_characters:
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
        )
    except EvidenceValueError:
        return None


def _recognize(projection: _Projection, source, resource_identity: str, resource_digest: str, selection_path: tuple[str, ...], lineage: str, excerpt_characters: int) -> tuple[list[OpeningClaim], list[str], bool, bool]:
    claims: list[OpeningClaim] = []
    warnings: list[str] = list(projection.warnings)
    body_transition = any(not element.active and element.semantics & _BODY_SEMANTICS for element in projection.elements)
    excluded = any(not element.active and element.semantics & _EXCLUDED_SEMANTICS for element in projection.elements)
    if excluded:
        return [], warnings + ["explicitly_excluded_resource"], body_transition, True
    title_context = selection_path[0] in {"epub3_landmark", "epub2_guide"} and any(value in {"titlepage", "halftitlepage", "title_page"} for value in selection_path[1:])
    by_ordinal = {element.ordinal: element for element in projection.elements}
    body_elements = tuple(
        element
        for element in projection.elements
        if not element.active and element.semantics & _BODY_SEMANTICS
    )
    claim_elements = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "dt", "dd", "li", "address", "figcaption"}
    for element in projection.elements:
        text = element.exact_text
        if element.active:
            continue
        semantic = element.semantics
        if semantic & _BODY_SEMANTICS:
            break
        if any(element.ordinal in body.ancestors for body in body_elements):
            continue
        if not text or element.end <= element.start:
            continue
        lowered = text.casefold()
        if element.name not in claim_elements and not semantic & _CLAIM_SEMANTICS:
            continue
        title_ancestor = any(
            by_ordinal[ordinal].semantics & {"titlepage", "halftitlepage"}
            for ordinal in element.ancestors
        )
        candidates: list[
            tuple[
                OpeningField,
                str,
                ObservationApplicability,
                str | None,
                str | None,
                str | None,
                tuple[_Element, int, int] | None,
            ]
        ] = []

        def add(
            field: OpeningField,
            value: str,
            applicability: ObservationApplicability,
            normalized: str | None = None,
            identifier_type: str | None = None,
            applicability_label: str | None = None,
            citation_context: tuple[_Element, int, int] | None = None,
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
                )
            )

        if "subtitle" in semantic:
            add(OpeningField.SUBTITLE, text, ObservationApplicability.WORK)
        elif semantic & {"fulltitle", "title"} or (
            element.name == "h1" and (title_context or title_ancestor)
        ):
            add(OpeningField.TITLE, text, ObservationApplicability.WORK)
        if "publisher" in semantic:
            add(OpeningField.PUBLISHER, text, ObservationApplicability.EDITION)
        if "imprint" in semantic or lowered.endswith(" imprint"):
            add(OpeningField.IMPRINT, text, ObservationApplicability.EDITION)
        if lowered.startswith(("by ", "author: ", "written by ")):
            value = re.sub(r"^(?:by|author:|written by)\s+", "", text, flags=re.I)
            add(OpeningField.CONTRIBUTOR, value, ObservationApplicability.WORK)
            add(OpeningField.CONTRIBUTOR_ROLE, text, ObservationApplicability.WORK, "author")
        elif lowered.startswith("edited by "):
            add(OpeningField.CONTRIBUTOR, text[10:], ObservationApplicability.WORK)
            add(OpeningField.CONTRIBUTOR_ROLE, text, ObservationApplicability.WORK, "editor")
        elif "translated by" in lowered:
            add(OpeningField.TRANSLATION, text, ObservationApplicability.EDITION)
            translator = re.split(r"translated by", text, flags=re.I, maxsplit=1)[-1].strip()
            add(OpeningField.CONTRIBUTOR, translator, ObservationApplicability.EDITION)
            add(OpeningField.CONTRIBUTOR_ROLE, text, ObservationApplicability.EDITION, "translator")
        elif "illustrated by" in lowered or "illustrations by" in lowered:
            add(OpeningField.ILLUSTRATION, text, ObservationApplicability.EDITION)
            illustrator = re.split(r"illustrat(?:ed|ions) by", text, flags=re.I, maxsplit=1)[-1].strip()
            add(OpeningField.CONTRIBUTOR, illustrator, ObservationApplicability.EDITION)
            add(OpeningField.CONTRIBUTOR_ROLE, text, ObservationApplicability.EDITION, "illustrator")
        elif lowered.startswith("adapted by "):
            add(OpeningField.CONTRIBUTOR, text[11:], ObservationApplicability.WORK)
            add(OpeningField.CONTRIBUTOR_ROLE, text, ObservationApplicability.WORK, "adapter")
        elif lowered.startswith(("contributions by ", "contributor: ")):
            value = re.sub(
                r"^(?:contributions by|contributor:)\s+", "", text, flags=re.I
            )
            add(OpeningField.CONTRIBUTOR, value, ObservationApplicability.WORK)
            add(OpeningField.CONTRIBUTOR_ROLE, text, ObservationApplicability.WORK, "other")
        elif semantic & {"contributor", "creator"}:
            add(OpeningField.CONTRIBUTOR, text, ObservationApplicability.WORK)
        if "edition" in lowered:
            add(OpeningField.EDITION, text, ObservationApplicability.EDITION)
        if "printing" in lowered or "impression" in lowered:
            add(OpeningField.PRINTING, text, ObservationApplicability.EDITION)
        if "revision" in lowered or "revised" in lowered:
            add(OpeningField.REVISION, text, ObservationApplicability.EDITION)
        if "copyright" in lowered or "©" in text:
            for match in _DATE.finditer(text):
                add(OpeningField.COPYRIGHT_DATE, match.group(0), ObservationApplicability.EDITION)
        elif lowered.startswith(("published ", "first published ", "publication date")):
            for match in _DATE.finditer(text):
                add(OpeningField.PUBLICATION_DATE, match.group(0), ObservationApplicability.EDITION)
        series = _SERIES.search(text)
        if series:
            add(OpeningField.SERIES_ORDER, series.group(1), ObservationApplicability.WORK)
            add(OpeningField.SERIES, series.group(2), ObservationApplicability.WORK)
        if "omnibus" in lowered:
            add(OpeningField.OMNIBUS_MEMBERSHIP, text, ObservationApplicability.WORK)
        if "anthology" in lowered:
            add(OpeningField.ANTHOLOGY_MEMBERSHIP, text, ObservationApplicability.WORK)
        isbn_matches = list(_ISBN.finditer(text))
        for match_index, match in enumerate(isbn_matches):
            identifier = match.group(0)
            if not _isbn_valid(identifier):
                continue
            left_boundary = isbn_matches[match_index - 1].end() if match_index else max(0, match.start() - 64)
            right_boundary = isbn_matches[match_index + 1].start() if match_index + 1 < len(isbn_matches) else min(len(text), match.end() + 64)
            separator = r"[;,|/\n\u2022]"
            left_vicinity = re.split(separator, lowered[left_boundary:match.start()])[-1]
            right_vicinity = re.split(separator, lowered[match.end():right_boundary])[0]
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
                        if candidate.end <= element.start and candidate.exact_text
                    ),
                    None,
                )
                previous_text = (
                    "" if previous is None else previous.exact_text.casefold()[-48:]
                )
                vicinity = previous_text + " " + vicinity
            else:
                previous = None
            ebook = bool(_EBOOK_LABEL.search(vicinity))
            printed = bool(_PRINT_LABEL.search(vicinity))
            if ebook and not printed:
                applicability = ObservationApplicability.EDITION
                applicability_label = "ebook"
            elif printed and not ebook:
                applicability = ObservationApplicability.INAPPLICABLE
                applicability_label = "paperback" if _PAPERBACK_LABEL.search(vicinity) else "hardcover" if _HARDCOVER_LABEL.search(vicinity) else "print"
            else:
                applicability = ObservationApplicability.UNKNOWN
                applicability_label = "unlabelled"
            citation_context: tuple[_Element, int, int] | None = None
            if left_has_labels or right_has_labels:
                citation_context = (
                    element,
                    element.start + left_boundary,
                    element.start + right_boundary,
                )
            elif previous is not None and (ebook or printed):
                shared = set(previous.ancestors) & set(element.ancestors)
                context_element = next(
                    (
                        by_ordinal[ordinal]
                        for ordinal in reversed(previous.ancestors)
                        if ordinal in shared
                    ),
                    None,
                )
                context_start = max(previous.start, previous.end - 48)
                context_end = element.start + right_boundary
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
                    applicability = ObservationApplicability.UNKNOWN
                    applicability_label = "unlabelled"
            compact = _normal(identifier, OpeningField.IDENTIFIER)
            add(
                OpeningField.IDENTIFIER,
                identifier,
                applicability,
                compact,
                "isbn_10" if len(compact) == 10 else "isbn_13",
                applicability_label,
                citation_context,
            )
            if applicability is ObservationApplicability.UNKNOWN:
                warnings.append("identifier_applicability_unknown")
        for (
            field,
            value,
            applicability,
            normalized,
            identifier_type,
            applicability_label,
            citation_context,
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
                selection_path,
                lineage,
                excerpt_characters,
                normalized=normalized,
                identifier_type=identifier_type,
                applicability_label=applicability_label,
                citation_context=citation_context,
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
        request: LocalEvidenceRequest,
        limits: EpubOpeningLimits,
        package_report: LocalEvidenceReport,
    ) -> EpubOpeningReport:
        if (
            type(request) is not LocalEvidenceRequest
            or type(limits) is not EpubOpeningLimits
            or type(package_report) is not LocalEvidenceReport
            or request.capability is not LocalEvidenceCapability.OPENING_MATTER
            or request.limits != limits.package
            or package_report.capability is not LocalEvidenceCapability.PACKAGE
            or package_report.limits != limits.package
            or (
                package_report.source_fingerprint is not None
                and package_report.source_fingerprint != request.expected_source
            )
        ):
            return EpubOpeningReport(LocalEvidenceOutcome.INTERNAL_FAILURE, limits)
        if not package_report.succeeded:
            return EpubOpeningReport(package_report.outcome, limits)
        if package_report.outcome is LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE:
            return EpubOpeningReport(LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE, limits, request.expected_source)
        try:
            return self._extract_validated(request, limits, package_report)
        except _AggregateRejected as error:
            return EpubOpeningReport(error.outcome, limits)
        except LocalEvidenceSourceError:
            return EpubOpeningReport(LocalEvidenceOutcome.UNAVAILABLE_SOURCE, limits)
        except (MemoryError, RecursionError):
            return EpubOpeningReport(LocalEvidenceOutcome.RESOURCE_LIMIT, limits)
        except Exception:
            return EpubOpeningReport(LocalEvidenceOutcome.INTERNAL_FAILURE, limits)

    def _extract_validated(self, request: LocalEvidenceRequest, limits: EpubOpeningLimits, package_report: LocalEvidenceReport) -> EpubOpeningReport:
        with zipfile.ZipFile(_SourceIO(request), "r") as archive:
            package_lineage = local_file_lineage(request.expected_source)
            names_by_identity = {local_resource_identity(info.filename): info.filename for info in archive.infolist() if not info.is_dir()}
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
                raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
            for candidate in candidates:
                _check(request)
                if selected >= limits.selected_documents:
                    warnings.append("selected_document_limit_reached")
                    break
                if candidate.identity in body_targets:
                    selected += 1
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            OpeningResourceDisposition.EXCLUDED,
                            candidate.selection_path,
                            warnings=("body_transition_reached",),
                        )
                    )
                    warnings.append("body_transition_reached")
                    break
                selected += 1
                if candidate.identity in excluded_targets:
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            OpeningResourceDisposition.EXCLUDED,
                            candidate.selection_path,
                            warnings=("explicitly_excluded_resource",),
                        )
                    )
                    warnings.append("explicitly_excluded_resource")
                    continue
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
                    data = _read_member(archive, name, limits.expanded_bytes_per_resource, request)
                    expanded += len(data)
                    opened += 1
                    if expanded > limits.expanded_bytes_per_selection:
                        raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
                    projection = _parse(data, candidate.media_type, limits, request)
                    nodes += projection.work_nodes
                    text_characters += projection.work_text_characters
                    if nodes > limits.nodes_per_selection or text_characters > limits.text_characters_per_selection:
                        raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
                    if projection.mode is OpeningParseMode.STRICT:
                        strict += 1
                    else:
                        recovered += 1
                    resource_claims, resource_warnings, body_transition, excluded = _recognize(
                        projection, request.expected_source, candidate.identity,
                        hashlib.sha256(data).hexdigest(), candidate.selection_path,
                        package_lineage,
                        limits.excerpt_characters,
                    )
                    claims.extend(resource_claims)
                    warnings.extend(resource_warnings)
                    disposition = OpeningResourceDisposition.EXCLUDED if excluded else OpeningResourceDisposition.SELECTED if resource_claims else OpeningResourceDisposition.NO_CLAIMS
                    resources.append(OpeningResourceResult(candidate.identity, disposition, candidate.selection_path, projection.mode, tuple(sorted(set(resource_warnings))), opened=True))
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
                        raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
                    code = "selected_resource_limit"
                    resources.append(
                        OpeningResourceResult(
                            candidate.identity,
                            OpeningResourceDisposition.RESOURCE_LIMIT,
                            candidate.selection_path,
                            warnings=(code,),
                            opened=data is not None,
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
                        raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
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
                        raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
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
                        raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
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
                    resources.append(OpeningResourceResult(candidate.identity, OpeningResourceDisposition.MALFORMED, candidate.selection_path, warnings=(code,), opened=data is not None))
                    warnings.append(code)
            unique_warnings = tuple(sorted(set(warnings)))
            if len(unique_warnings) > limits.warnings or len(claims) > limits.facts:
                raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT)
            usage = EpubOpeningUsage(selected, opened, expanded, text_characters, nodes, navigation_links, strict, recovered)
            outcome = LocalEvidenceOutcome.SUCCESS if claims else LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE
            try:
                return EpubOpeningReport(
                    outcome, limits, request.expected_source, tuple(claims),
                    tuple(resources), unique_warnings, usage,
                    same_file_lineage=package_lineage,
                )
            except EvidenceValueError as error:
                if "byte limit" in str(error):
                    raise _AggregateRejected(LocalEvidenceOutcome.RESOURCE_LIMIT) from None
                raise


__all__ = ["EpubOpeningExtractor"]
