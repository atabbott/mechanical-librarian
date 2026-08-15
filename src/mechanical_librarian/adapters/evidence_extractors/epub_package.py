"""Bounded EPUB package metadata extraction.

The adapter deliberately reads only OCF control documents and the single OPF
package document. Declared content, navigation documents, covers, and linked
metadata remain opaque declarations.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import struct
import time
import unicodedata
import zipfile
import zlib
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from xml.parsers import expat

from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    MAX_DECLARATIONS,
    EpubInspectionOutcome,
    EpubObservationApplicability,
    EpubPackageCapability,
    EpubPackageCitation,
    EpubPackageCitationSection,
    EpubPackageDeclaration,
    EpubPackageDeclarationKind,
    EpubPackageField,
    EpubPackageLimits,
    EpubPackageObservation,
    EpubPackageReport,
    EpubPackageRequest,
    EpubPackageSourceError,
    EpubPackageUsage,
    EpubPackageWarning,
    epub_file_lineage,
    epub_resource_identity,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_code,
    validate_projection_text,
)
from mechanical_librarian.domain.identifiers import (
    CompatibilityVersion,
    ExtractorVersion,
    SchemaVersion,
)

_CONTAINER = "META-INF/container.xml"
_ENCRYPTION = "META-INF/encryption.xml"
_MIMETYPE = "mimetype"
_MIMETYPE_VALUE = b"application/epub+zip"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_OPF_NS = "http://www.idpf.org/2007/opf"
_ENC_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_XMLENC_NS = "http://www.w3.org/2001/04/xmlenc#"
_XMLDSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
_XINCLUDE_NS = "http://www.w3.org/2001/XInclude"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_SCHEMA_NS = "http://schema.org/"
_SCHEMA_HTTPS_NS = "https://schema.org/"
_DCTERMS_NS = "http://purl.org/dc/terms/"
_EXTRACTOR_VERSION = ExtractorVersion("epub-package-v1")
_OBSERVATION_SCHEMA = SchemaVersion("package-observation-v1")
_ISBN = re.compile(r"^(?:(ISBN(?:-1[03])?):?)?\s*([0-9Xx -]{10,20})$", re.I)
_DATE = re.compile(
    r"^\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?"
    r"(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)?$"
)
_SIGNED_OFFSET_DATE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?"
    r"[+-](?:(?:0\d|1[0-3]):[0-5]\d|14:00)$"
)
_MODIFIED_DATE = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?Z$"
)
_LANGUAGE = re.compile(
    r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,7}|"
    r"[xX](?:-[A-Za-z0-9]{1,8}){1,7})$"
)
_XML_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,255}$")
_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_VOCAB_IRI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:[^\s]{1,1023}$")
_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_UUID = re.compile(
    r"^(?:urn:uuid:)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$"
)
_DOI = re.compile(r"^(?:(?:doi:)|(?:https?://doi\.org/))?(10\.\d{4,9}/\S+)$", re.I)
_ASIN = re.compile(r"^(?:ASIN:)?(B0[A-Z0-9]{8})$", re.I)
_UNSAFE_MARKER = "unsafe-source-text-sha256:"
_OVERSIZED_MARKER = "oversized-source-text-sha256:"
_EPUB2_SPINE_MEDIA_TYPES = {
    "application/xhtml+xml",
    "application/x-dtbook+xml",
    "text/x-oeb1-document",
}
_EPUB3_SPINE_MEDIA_TYPES = {"application/xhtml+xml", "image/svg+xml"}


class _Rejected(Exception):
    def __init__(self, outcome: EpubInspectionOutcome) -> None:
        self.outcome = outcome


class _InvalidReferenceSyntax(Exception):
    """An optional package reference is syntactically invalid and can be omitted."""


def _check_request(request: EpubPackageRequest) -> None:
    if request.cancellation.is_cancelled():
        raise _Rejected(EpubInspectionOutcome.CANCELLATION)
    if time.monotonic() >= request.monotonic_deadline:
        raise _Rejected(EpubInspectionOutcome.TIMEOUT)


@dataclass(frozen=True)
class _Entry:
    name: str
    raw_name: bytes
    flags: int
    method: int
    crc: int
    compressed_size: int
    expanded_size: int
    local_offset: int
    central_extra_length: int
    local_extra_length: int
    data_offset: int


@dataclass
class _Node:
    name: str
    attributes: dict[str, str]
    children: list["_Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    ordinal: int = 0

    @property
    def text(self) -> str:
        return "".join(self.text_parts).strip()


@dataclass
class _XmlUsage:
    nodes: int = 0
    attributes: int = 0
    text_characters: int = 0
    maximum_depth: int = 0


class _SourceIO(io.RawIOBase):
    def __init__(self, request: EpubPackageRequest) -> None:
        self._request = request
        self._source = request.source
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        _check_request(self._request)
        position = (
            offset
            if whence == io.SEEK_SET
            else self._position + offset
            if whence == io.SEEK_CUR
            else self._source.length + offset
            if whence == io.SEEK_END
            else -1
        )
        if position < 0 or position > self._source.length:
            raise OSError("invalid bounded source seek")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        _check_request(self._request)
        remaining = self._source.length - self._position
        amount = remaining if size is None or size < 0 else min(size, remaining)
        value = self._source.read_at(self._position, amount)
        _check_request(self._request)
        self._position += len(value)
        return value


def _read_exact(request: EpubPackageRequest, offset: int, size: int) -> bytes:
    _check_request(request)
    if size < 0 or offset < 0 or offset + size > request.source.length:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    value = request.source.read_at(offset, size)
    _check_request(request)
    if len(value) != size:
        raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE)
    return value


def _zip64_values(extra: bytes, wanted: tuple[bool, bool, bool]) -> tuple[int | None, ...]:
    cursor = 0
    payload = None
    while cursor + 4 <= len(extra):
        kind, length = struct.unpack_from("<HH", extra, cursor)
        cursor += 4
        if cursor + length > len(extra):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        if kind == 0x0001:
            if payload is not None:
                raise _Rejected(EpubInspectionOutcome.MALFORMED)
            payload = extra[cursor : cursor + length]
        cursor += length
    if cursor != len(extra):
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    if not any(wanted):
        return (None, None, None)
    if payload is None:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    result: list[int | None] = []
    position = 0
    for required in wanted:
        if not required:
            result.append(None)
            continue
        if position + 8 > len(payload):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        result.append(struct.unpack_from("<Q", payload, position)[0])
        position += 8
    return tuple(result)


def _valid_member_name(raw: bytes, flags: int, limits: EpubPackageLimits) -> str:
    if b"\x00" in raw or len(raw) > limits.MEMBER_NAME_UTF8_BYTES_HARD_CAP:
        raise _Rejected(
            EpubInspectionOutcome.RESOURCE_LIMIT
            if len(raw) > limits.MEMBER_NAME_UTF8_BYTES_HARD_CAP
            else EpubInspectionOutcome.MALFORMED
        )
    try:
        if flags & 0x800:
            name = raw.decode("utf-8", "strict")
        else:
            name = raw.decode("ascii", "strict")
    except UnicodeDecodeError:
        raise _Rejected(EpubInspectionOutcome.MALFORMED) from None
    if unicodedata.normalize("NFC", name) != name:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in parts)
        or any(len(part.encode("utf-8")) > limits.COMPONENT_NAME_UTF8_BYTES_HARD_CAP for part in parts)
    ):
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    return name


def _canonical_zip_flags(method: int) -> int:
    return 0x0808 | (0x0006 if method == zipfile.ZIP_DEFLATED else 0)


def _uses_noncanonical_zip_flags(flags: int, method: int) -> bool:
    return bool(flags & ~_canonical_zip_flags(method))


def _central_directory(request: EpubPackageRequest) -> tuple[_Entry, ...]:
    limits = request.limits
    length = request.source.length
    if length < 22:
        raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_FORMAT)
    tail_size = min(length, 65_557)
    tail_offset = length - tail_size
    tail = _read_exact(request, tail_offset, tail_size)
    relative = tail.rfind(b"PK\x05\x06")
    if relative < 0 or relative + 22 > len(tail):
        raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_FORMAT)
    eocd_offset = tail_offset + relative
    disk, central_disk, disk_entries, entries, central_size, central_offset, comment = struct.unpack_from(
        "<4H2LH", tail, relative + 4
    )
    if eocd_offset + 22 + comment != length or disk or central_disk or disk_entries != entries:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    zip64 = entries == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF
    central_boundary = eocd_offset
    if zip64:
        if eocd_offset < 20:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        locator = _read_exact(request, eocd_offset - 20, 20)
        if locator[:4] != b"PK\x06\x07":
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        locator_disk, zip64_offset, disk_count = struct.unpack_from("<LQL", locator, 4)
        if locator_disk or disk_count != 1:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        record_header = _read_exact(request, zip64_offset, 12)
        if record_header[:4] != b"PK\x06\x06":
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        record_size = struct.unpack_from("<Q", record_header, 4)[0]
        if record_size < 44 or zip64_offset + 12 + record_size != eocd_offset - 20:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        record = _read_exact(request, zip64_offset, 12 + record_size)
        disk, central_disk = struct.unpack_from("<LL", record, 16)
        disk_entries, entries, central_size, central_offset = struct.unpack_from("<4Q", record, 24)
        if disk or central_disk or disk_entries != entries:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        central_boundary = zip64_offset
    if entries == 0 or entries > limits.archive_entries:
        raise _Rejected(
            EpubInspectionOutcome.RESOURCE_LIMIT if entries > limits.archive_entries else EpubInspectionOutcome.UNSUPPORTED_FORMAT
        )
    if central_offset + central_size != central_boundary:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    result: list[_Entry] = []
    normalized: set[str] = set()
    cursor = central_offset
    intervals: list[tuple[int, int]] = []
    declared_expanded = 0
    for _ in range(entries):
        header = _read_exact(request, cursor, 46)
        if header[:4] != b"PK\x01\x02":
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        (
            _made_by,
            needed,
            flags,
            method,
            _mtime,
            _mdate,
            crc,
            compressed,
            expanded,
            name_length,
            extra_length,
            comment_length,
            disk_start,
            _internal,
            _external,
            local_offset,
        ) = struct.unpack_from("<6H3L5H2L", header, 4)
        variable = _read_exact(request, cursor + 46, name_length + extra_length + comment_length)
        raw_name = variable[:name_length]
        extra = variable[name_length : name_length + extra_length]
        name = _valid_member_name(raw_name, flags, limits)
        if flags & 1:
            raise _Rejected(EpubInspectionOutcome.ENCRYPTED)
        if method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_COMPRESSION)
        # A deployed producer leaves the exact bits 1, 2, and 4 residue set on
        # both stored and Deflated entries. Accept that complete pattern only;
        # the selected method remains authoritative and every adjacent unknown
        # or capability-bearing flag remains rejected.
        compatibility_flags = (
            flags & 0x0016 == 0x0016 and flags & ~0x081E == 0
        )
        if flags & ~_canonical_zip_flags(method) and not compatibility_flags:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        if disk_start != 0:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        central_uses_zip64 = (
            expanded == 0xFFFFFFFF
            or compressed == 0xFFFFFFFF
            or local_offset == 0xFFFFFFFF
        )
        zip_expanded, zip_compressed, zip_offset = _zip64_values(
            extra,
            (expanded == 0xFFFFFFFF, compressed == 0xFFFFFFFF, local_offset == 0xFFFFFFFF),
        )
        expanded = expanded if zip_expanded is None else zip_expanded
        compressed = compressed if zip_compressed is None else zip_compressed
        local_offset = local_offset if zip_offset is None else zip_offset
        minimum_version = (
            45
            if central_uses_zip64
            else 20
            if method == zipfile.ZIP_DEFLATED or flags & 0x08
            else 10
        )
        if not minimum_version <= needed <= 45:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        if compressed > limits.compressed_bytes_per_member or expanded > limits.expanded_bytes_per_member:
            raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        declared_expanded += expanded
        if declared_expanded > limits.expanded_bytes_per_archive:
            raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        if expanded and compressed == 0:
            raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE)
        if compressed and expanded > compressed * limits.compression_ratio_per_member:
            raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        if name.endswith("/") and (expanded != 0 or crc != 0):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        collision = unicodedata.normalize("NFC", name.removesuffix("/")).casefold()
        if collision in normalized:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        normalized.add(collision)
        local = _read_exact(request, local_offset, 30)
        if local[:4] != b"PK\x03\x04":
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        local_needed, local_flags, local_method = struct.unpack_from("<3H", local, 4)
        local_crc, local_compressed, local_expanded = struct.unpack_from("<3L", local, 14)
        local_name_length, local_extra_length = struct.unpack_from("<2H", local, 26)
        local_variable = _read_exact(
            request, local_offset + 30, local_name_length + local_extra_length
        )
        local_name = local_variable[:local_name_length]
        local_extra = local_variable[local_name_length:]
        if (
            local_name != raw_name
            or local_flags != flags
            or local_method != method
            or local_needed != needed
        ):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        if flags & 0x08:
            if (
                local_crc not in {0, crc}
                or local_compressed not in {0, compressed, 0xFFFFFFFF}
                or local_expanded not in {0, expanded, 0xFFFFFFFF}
            ):
                raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE)
        elif (
            local_crc != crc
            or (local_compressed != 0xFFFFFFFF and local_compressed != compressed)
            or (local_expanded != 0xFFFFFFFF and local_expanded != expanded)
        ):
            raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE)
        local_zip_expanded, local_zip_compressed, _unused = _zip64_values(
            local_extra,
            (
                local_expanded == 0xFFFFFFFF,
                local_compressed == 0xFFFFFFFF,
                False,
            ),
        )
        if (
            (local_zip_expanded is not None and local_zip_expanded != expanded)
            or (local_zip_compressed is not None and local_zip_compressed != compressed)
        ):
            raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE)
        data_offset = local_offset + 30 + local_name_length + local_extra_length
        data_end = data_offset + compressed
        occupied_end = data_end
        if flags & 0x08:
            zip64_descriptor = (
                local_compressed == 0xFFFFFFFF
                or local_expanded == 0xFFFFFFFF
                or compressed > 0xFFFFFFFF
                or expanded > 0xFFFFFFFF
            )
            width = 8 if zip64_descriptor else 4
            prefix = _read_exact(request, data_end, 4)
            candidates: list[int] = []
            for has_signature in (False, True):
                if has_signature and prefix != b"PK\x07\x08":
                    continue
                crc_offset = data_end + (4 if has_signature else 0)
                candidate_end = crc_offset + 4 + width * 2
                if candidate_end > central_offset:
                    continue
                candidate_crc = struct.unpack(
                    "<L", _read_exact(request, crc_offset, 4)
                )[0]
                descriptor_sizes = _read_exact(request, crc_offset + 4, width * 2)
                candidate_compressed = int.from_bytes(
                    descriptor_sizes[:width], "little"
                )
                candidate_expanded = int.from_bytes(
                    descriptor_sizes[width:], "little"
                )
                if (
                    candidate_crc == crc
                    and candidate_compressed == compressed
                    and candidate_expanded == expanded
                ):
                    candidates.append(candidate_end)
            if len(candidates) != 1:
                raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE)
            occupied_end = candidates[0]
        if occupied_end > central_offset:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        intervals.append((local_offset, occupied_end))
        result.append(
            _Entry(
                name,
                raw_name,
                flags,
                method,
                crc,
                compressed,
                expanded,
                local_offset,
                extra_length,
                local_extra_length,
                data_offset,
            )
        )
        cursor += 46 + name_length + extra_length + comment_length
    if cursor != central_offset + central_size:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    for previous, current in zip(sorted(intervals), sorted(intervals)[1:]):
        if previous[1] > current[0]:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
    if not any(entry.name == _MIMETYPE for entry in result):
        raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_FORMAT)
    return tuple(result)


def _parse_xml(
    data: bytes,
    limits: EpubPackageLimits,
    request: EpubPackageRequest,
) -> tuple[_Node, _XmlUsage]:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    usage = _XmlUsage()
    stack: list[_Node] = []
    root: _Node | None = None
    try:
        parser = expat.ParserCreate(namespace_separator="}")
        if not hasattr(parser, "SetParamEntityParsing") or not parser.SetParamEntityParsing(
            expat.XML_PARAM_ENTITY_PARSING_NEVER
        ):
            raise _Rejected(EpubInspectionOutcome.UNAVAILABLE_SOURCE)
    except MemoryError:
        raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT) from None

    def expanded(name: str) -> str:
        return "{" + name if "}" in name else name

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal root
        _check_request(request)
        usage.nodes += 1
        usage.attributes += len(attributes)
        depth = len(stack) + 1
        usage.maximum_depth = max(usage.maximum_depth, depth)
        if (
            usage.nodes > limits.xml_nodes_per_document
            or usage.attributes > limits.xml_attributes_per_document
            or len(attributes) > limits.attributes_per_element
            or depth > limits.xml_depth
        ):
            raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        node = _Node(expanded(name), {expanded(key): value for key, value in attributes.items()}, ordinal=usage.nodes)
        if node.name.startswith("{" + _XINCLUDE_NS + "}"):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        if stack:
            stack[-1].children.append(node)
        elif root is None:
            root = node
        else:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        stack.append(node)

    def end(_name: str) -> None:
        _check_request(request)
        if not stack:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        stack.pop()

    def text(value: str) -> None:
        _check_request(request)
        if stack and value:
            usage.text_characters += len(value)
            if usage.text_characters > limits.decoded_xml_text_characters:
                raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
            stack[-1].text_parts.append(value)

    def reject(*_arguments: object) -> None:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.UnparsedEntityDeclHandler = reject
    parser.ExternalEntityRefHandler = lambda *_arguments: 0
    try:
        parser.Parse(data, True)
    except _Rejected:
        raise
    except MemoryError:
        raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT) from None
    except (expat.ExpatError, UnicodeError, ValueError, RecursionError):
        raise _Rejected(EpubInspectionOutcome.MALFORMED) from None
    if root is None or stack:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    return root, usage


def _member_bytes(
    request: EpubPackageRequest,
    archive: zipfile.ZipFile,
    entries: dict[str, _Entry],
    name: str,
    maximum: int,
) -> bytes:
    entry = entries.get(name)
    if entry is None:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    if entry.expanded_size > maximum:
        raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
    try:
        with archive.open(name, "r") as stream:
            chunks: list[bytes] = []
            total = 0
            while True:
                _check_request(request)
                chunk = stream.read(min(65_536, maximum + 1 - total))
                _check_request(request)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
            value = b"".join(chunks)
    except _Rejected:
        raise
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError, zlib.error):
        raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE) from None
    if len(value) != entry.expanded_size:
        raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE)
    return value


def _children(node: _Node, local: str, namespace: str) -> list[_Node]:
    expected = f"{{{namespace}}}{local}"
    return [child for child in node.children if child.name == expected]


def _attribute(node: _Node, local: str, namespace: str | None = None) -> str | None:
    return node.attributes.get(local if namespace is None else f"{{{namespace}}}{local}")


def _percent_decode_path(reference: str) -> str:
    output = bytearray()
    index = 0
    while index < len(reference):
        character = reference[index]
        if character == "%":
            if index + 2 >= len(reference):
                raise _InvalidReferenceSyntax
            try:
                value = int(reference[index + 1 : index + 3], 16)
            except ValueError:
                raise _InvalidReferenceSyntax from None
            if value in {0, ord("/"), ord("\\")}:
                raise _Rejected(EpubInspectionOutcome.MALFORMED)
            output.append(value)
            index += 3
            continue
        output.extend(character.encode("utf-8"))
        index += 1
    try:
        decoded = output.decode("utf-8", "strict")
    except UnicodeError:
        raise _InvalidReferenceSyntax from None
    return unicodedata.normalize("NFC", decoded)


def _normalize_member_reference(base: str, reference: str) -> str:
    first_segment = reference.split("/", 1)[0]
    if not reference or "?" in reference or "#" in reference:
        raise _InvalidReferenceSyntax
    if reference.startswith(("/", "\\")) or "\\" in reference:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    if ":" in first_segment:
        if first_segment.casefold().startswith("file:"):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        raise _InvalidReferenceSyntax
    decoded_reference = _percent_decode_path(reference)
    if decoded_reference.endswith("/"):
        raise _InvalidReferenceSyntax
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), decoded_reference))
    if joined.startswith("../") or joined in {".", ".."}:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    encoded = joined.encode("utf-8")
    if len(encoded) > EpubPackageLimits.MEMBER_NAME_UTF8_BYTES_HARD_CAP:
        raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
    return joined


def _resolve_member(base: str, reference: str, entries: dict[str, _Entry]) -> str:
    try:
        joined = _normalize_member_reference(base, reference)
    except _InvalidReferenceSyntax:
        raise _Rejected(EpubInspectionOutcome.MALFORMED) from None
    if joined not in entries:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    return joined


def _declared_resource_identity(
    base: str, reference: str, entries: dict[str, _Entry]
) -> tuple[str, str | None]:
    if reference.startswith(("https://", "http://")):
        parts = reference.split("/", 3)
        if len(reference) > 1_024:
            raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        if len(parts) < 3 or not parts[2]:
            raise _InvalidReferenceSyntax
        if any(
            ord(character) < 0x20 or character == "\\"
            for character in reference
        ) or "@" in parts[2]:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        return hashlib.sha256(("remote\x00" + reference).encode("utf-8")).hexdigest(), None
    resolved = _normalize_member_reference(base, reference)
    return epub_resource_identity(resolved), resolved


def _normalize_identifier(
    raw: str,
    declared_scheme: str | None = None,
) -> tuple[str, EpubObservationApplicability, str | None, bool]:
    stripped = raw.strip()
    urn_isbn = stripped.casefold().startswith("urn:isbn:")
    candidate = stripped[9:] if urn_isbn else raw
    declared_scheme_key = (
        None if declared_scheme is None else declared_scheme.casefold()
    )
    declared_isbn = (
        declared_scheme_key.upper()
        if declared_scheme_key in {"isbn", "isbn-10", "isbn-13"}
        else None
    )
    if declared_scheme is not None and declared_isbn is None:
        uuid = _UUID.fullmatch(stripped)
        if uuid and declared_scheme_key in {"uuid", "urn:uuid"}:
            return (
                "urn:uuid:" + uuid.group(1).lower(),
                EpubObservationApplicability.EDITION,
                "UUID",
                False,
            )
        doi = _DOI.fullmatch(stripped)
        if doi and declared_scheme_key == "doi":
            return doi.group(1), EpubObservationApplicability.EDITION, "DOI", False
        asin = _ASIN.fullmatch(stripped)
        if asin and declared_scheme_key == "asin":
            return asin.group(1).upper(), EpubObservationApplicability.EDITION, "ASIN", False
        return stripped, EpubObservationApplicability.UNKNOWN, None, False
    match = _ISBN.fullmatch(candidate)
    if not match:
        if (
            urn_isbn
            or stripped.casefold().startswith("isbn")
            or declared_isbn is not None
        ):
            return stripped, EpubObservationApplicability.UNKNOWN, "ISBN", True
        uuid = _UUID.fullmatch(stripped)
        if uuid:
            return (
                "urn:uuid:" + uuid.group(1).lower(),
                EpubObservationApplicability.EDITION,
                "UUID",
                False,
            )
        doi = _DOI.fullmatch(stripped)
        if doi:
            return doi.group(1), EpubObservationApplicability.EDITION, "DOI", False
        asin = _ASIN.fullmatch(stripped)
        if asin:
            return asin.group(1).upper(), EpubObservationApplicability.EDITION, "ASIN", False
        if stripped.casefold().startswith("urn:"):
            return stripped, EpubObservationApplicability.UNKNOWN, "URN", False
        return stripped, EpubObservationApplicability.UNKNOWN, None, False
    labelled_scheme = None if match.group(1) is None else match.group(1).upper()
    required_scheme = (
        labelled_scheme
        if labelled_scheme in {"ISBN-10", "ISBN-13"}
        else declared_isbn
    )
    compact = re.sub(r"[ -]", "", match.group(2)).upper()
    if required_scheme != "ISBN-13" and len(compact) == 10 and compact[:9].isdigit() and (
        compact[-1].isdigit() or compact[-1] == "X"
    ):
        total = sum((10 - index) * (10 if value == "X" else int(value)) for index, value in enumerate(compact))
        if total % 11 == 0:
            return compact, EpubObservationApplicability.UNKNOWN, "ISBN-10", False
    if (
        required_scheme != "ISBN-10"
        and len(compact) == 13
        and compact.isdigit()
        and compact.startswith(("978", "979"))
    ):
        total = sum(int(value) * (1 if index % 2 == 0 else 3) for index, value in enumerate(compact[:-1]))
        if (10 - total % 10) % 10 == int(compact[-1]):
            return compact, EpubObservationApplicability.UNKNOWN, "ISBN-13", False
    return raw.strip(), EpubObservationApplicability.UNKNOWN, "ISBN", True


def _valid_date(value: str) -> bool:
    if _DATE.fullmatch(value) is None:
        return False
    try:
        if len(value) == 4:
            return True
        if len(value) == 7:
            datetime(int(value[:4]), int(value[5:7]), 1)
        elif "T" in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def _valid_publication_date(value: str) -> bool:
    if _valid_date(value):
        return True
    if _SIGNED_OFFSET_DATE.fullmatch(value) is None:
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


def _valid_modified_date(value: str) -> bool:
    return _MODIFIED_DATE.fullmatch(value) is not None and _valid_date(value)


def _valid_language(value: str) -> bool:
    return _LANGUAGE.fullmatch(value) is not None


def _valid_group_position(value: str) -> bool:
    return re.fullmatch(r"[0-9]+", value) is not None and int(value) >= 1


def _compatibility(version: str) -> CompatibilityVersion:
    return CompatibilityVersion("opf-2.0.1" if version == "2.0" else "epub-3.3")


def _resolved_property(value: str | None, prefixes: dict[str, str]) -> str | None:
    if not value:
        return None
    if ":" not in value:
        return value
    prefix, local = value.split(":", 1)
    if not local:
        return None
    if prefix == "dcterms" and prefixes.get(prefix, _DCTERMS_NS) == _DCTERMS_NS:
        return "dcterms:" + local
    if prefix == "schema" and prefixes.get(prefix, _SCHEMA_NS) in {
        _SCHEMA_NS,
        _SCHEMA_HTTPS_NS,
    }:
        return "schema:" + local
    return None


def _public_text(value: str, maximum: int) -> tuple[str, bool]:
    if len(value) > maximum:
        return _OVERSIZED_MARKER + hashlib.sha256(value.encode("utf-8")).hexdigest(), True
    try:
        return validate_projection_text(value, "public EPUB metadata", maximum), False
    except EvidenceValueError:
        return _UNSAFE_MARKER + hashlib.sha256(value.encode("utf-8")).hexdigest(), True


def _walk(node: _Node) -> list[_Node]:
    result: list[_Node] = []
    pending = [node]
    while pending:
        current = pending.pop()
        result.append(current)
        pending.extend(reversed(current.children))
    return result


def _citation(
    request: EpubPackageRequest,
    resource: str,
    section: EpubPackageCitationSection,
    node: _Node,
    selector: str | None = None,
) -> EpubPackageCitation:
    expanded_name = node.name
    namespace = ""
    local_name = expanded_name
    if expanded_name.startswith("{"):
        namespace, separator, local_name = expanded_name[1:].partition("}")
        if not separator:
            namespace = ""
    public_name = (
        (namespace == _DC_NS and local_name in _DC_FIELDS)
        or (
            namespace == _OPF_NS
            and local_name
            in {"item", "itemref", "meta", "package", "reference", "spine"}
        )
        or (namespace == _CONTAINER_NS and local_name == "rootfile")
        or (namespace == _XMLENC_NS and local_name == "CipherReference")
    )
    if not public_name or len(expanded_name) > 512:
        expanded_name = (
            "{urn:sha256:"
            + hashlib.sha256(expanded_name.encode("utf-8")).hexdigest()
            + "}element"
        )
    return EpubPackageCitation(
        request.expected_source,
        epub_resource_identity(resource),
        section,
        expanded_name,
        node.ordinal,
        selector,
    )


def _observation(
    request: EpubPackageRequest,
    resource: str,
    version: str,
    node: _Node,
    field_name: EpubPackageField,
    *,
    normalized: str | None = None,
    applicability: EpubObservationApplicability = EpubObservationApplicability.WORK,
    display: int | None = None,
    language: str | None = None,
    selector: str | None = None,
    preserve_only: bool = False,
) -> EpubPackageObservation:
    raw = node.text
    safe_raw, raw_redacted = _public_text(raw, request.limits.MAX_RAW_VALUE_CHARACTERS)
    normalized_value = None if preserve_only else raw.strip() if normalized is None else normalized
    safe_normalized = None
    if normalized_value is not None:
        safe_normalized, normalized_redacted = _public_text(
            normalized_value,
            request.limits.MAX_NORMALIZED_VALUE_CHARACTERS,
        )
        if raw_redacted or normalized_redacted:
            safe_normalized = safe_raw
    return EpubPackageObservation(
        field_name,
        safe_raw,
        safe_normalized,
        applicability,
        _citation(request, resource, EpubPackageCitationSection.METADATA, node, selector),
        _OBSERVATION_SCHEMA,
        _EXTRACTOR_VERSION,
        _compatibility(version),
        display,
        language,
        epub_file_lineage(request.expected_source),
    )


_DC_FIELDS = {
    "title": EpubPackageField.TITLE,
    "creator": EpubPackageField.CREATOR,
    "contributor": EpubPackageField.CONTRIBUTOR,
    "language": EpubPackageField.LANGUAGE,
    "publisher": EpubPackageField.PUBLISHER,
    "date": EpubPackageField.PUBLICATION_DATE,
    "subject": EpubPackageField.SUBJECT,
    "description": EpubPackageField.DESCRIPTION,
    "rights": EpubPackageField.RIGHTS,
    "source": EpubPackageField.SOURCE,
    "relation": EpubPackageField.RELATION,
    "type": EpubPackageField.TYPE,
    "format": EpubPackageField.FORMAT,
    "coverage": EpubPackageField.REGION,
    "identifier": EpubPackageField.IDENTIFIER,
}


def _package_report(
    request: EpubPackageRequest,
    package_name: str,
    root: _Node,
    entries: dict[str, _Entry],
    encryption_names: set[str],
    encryption_declarations: list[tuple[str, _Node]],
    encryption_present: bool,
    usage: EpubPackageUsage,
    rootfile_node: _Node,
    noncanonical_mimetype: bool,
) -> EpubPackageReport:
    if root.name != "{" + _OPF_NS + "}package":
        raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_FORMAT)
    declared_version = _attribute(root, "version") or ""
    if declared_version == "1.0" or re.fullmatch(r"2(?:\.[0-9]+)+", declared_version):
        version = "2.0"
    elif re.fullmatch(r"3(?:\.[0-9]+)+", declared_version):
        version = "3.0"
    else:
        raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_VERSION)
    prefix_tokens = (_attribute(root, "prefix") or "").split()
    if (
        len(prefix_tokens) % 2
        or len(prefix_tokens) // 2 > request.limits.prefix_mappings
        or any(
            not prefix_tokens[index].endswith(":")
            or _PREFIX.fullmatch(prefix_tokens[index][:-1]) is None
            or _VOCAB_IRI.fullmatch(prefix_tokens[index + 1]) is None
            for index in range(0, len(prefix_tokens), 2)
        )
    ):
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    prefixes: dict[str, str] = {}
    for index in range(0, len(prefix_tokens), 2):
        name = prefix_tokens[index][:-1]
        if name in prefixes:
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        prefixes[name] = prefix_tokens[index + 1]
    metadata_groups = _children(root, "metadata", _OPF_NS)
    manifest_groups = _children(root, "manifest", _OPF_NS)
    spine_groups = _children(root, "spine", _OPF_NS)
    if len(metadata_groups) != 1 or len(manifest_groups) != 1 or len(spine_groups) != 1:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    metadata = metadata_groups[0]
    manifest = manifest_groups[0]
    spine = spine_groups[0]
    metadata_nodes: tuple[_Node, ...] = tuple(
        nested
        for node in metadata.children
        for nested in (
            tuple(node.children)
            if version == "2.0"
            and node.name
            in {
                f"{{{_OPF_NS}}}dc-metadata",
                f"{{{_OPF_NS}}}x-metadata",
            }
            else (node,)
        )
    )
    usage = replace(usage, metadata_nodes=len(metadata_nodes))
    supported_sequence = [
        f"{{{_OPF_NS}}}metadata",
        f"{{{_OPF_NS}}}manifest",
        f"{{{_OPF_NS}}}spine",
    ]
    child_names = [node.name for node in root.children]
    if child_names[:3] != supported_sequence:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    trailing = child_names[3:]
    guide_name = f"{{{_OPF_NS}}}guide"
    if version == "2.0":
        tours_name = f"{{{_OPF_NS}}}tours"
        if trailing not in (
            [],
            [tours_name],
            [guide_name],
            [tours_name, guide_name],
        ):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
    else:
        collection_name = f"{{{_OPF_NS}}}collection"
        bindings_name = f"{{{_OPF_NS}}}bindings"
        tours_name = f"{{{_OPF_NS}}}tours"
        trailing = [name for name in trailing if name != tours_name]
        position = 0
        if position < len(trailing) and trailing[position] == guide_name:
            position += 1
        if position < len(trailing) and trailing[position] == bindings_name:
            position += 1
        while position < len(trailing) and trailing[position] == collection_name:
            position += 1
        if position != len(trailing):
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
    if not manifest.children or not spine.children:
        raise _Rejected(EpubInspectionOutcome.MALFORMED)
    if len(metadata_nodes) > request.limits.metadata_nodes:
        raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
    if len(manifest.children) > request.limits.manifest_items or len(spine.children) > request.limits.spine_items:
        raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
    observations: list[EpubPackageObservation] = []
    declarations: list[EpubPackageDeclaration] = []
    warnings: list[EpubPackageWarning] = []
    if noncanonical_mimetype:
        warnings.append(EpubPackageWarning("noncanonical_mimetype"))
    if declared_version != version:
        warnings.append(EpubPackageWarning("noncanonical_package_version"))
    if any(
        _uses_noncanonical_zip_flags(entry.flags, entry.method)
        for entry in entries.values()
    ):
        warnings.append(EpubPackageWarning("noncanonical_zip_flags"))
    tours = _children(root, "tours", _OPF_NS)
    if tours:
        warnings.append(EpubPackageWarning("deprecated_tours"))
    declarations.extend(
        (
            EpubPackageDeclaration(
                EpubPackageDeclarationKind.ROOTFILE,
                rootfile_node.ordinal,
                _citation(request, _CONTAINER, EpubPackageCitationSection.PACKAGE, rootfile_node),
                epub_resource_identity(package_name),
                media_type="application/oebps-package+xml",
            ),
            EpubPackageDeclaration(
                EpubPackageDeclarationKind.PACKAGE,
                root.ordinal,
                _citation(request, package_name, EpubPackageCitationSection.PACKAGE, root),
                epub_resource_identity(package_name),
                media_type="application/oebps-package+xml",
            ),
        )
    )
    for encrypted_name, encrypted_node in encryption_declarations:
        declarations.append(
            EpubPackageDeclaration(
                EpubPackageDeclarationKind.ENCRYPTION,
                encrypted_node.ordinal,
                _citation(
                    request,
                    _ENCRYPTION,
                    EpubPackageCitationSection.ENCRYPTION,
                    encrypted_node,
                ),
                epub_resource_identity(encrypted_name),
            )
        )
    identifiers_by_id: dict[str, _Node] = {}
    id_registry: dict[str, _Node] = {}
    ambiguous_ids: set[str] = set()
    for node in _walk(root):
        identifier = _attribute(node, "id")
        if identifier is None:
            continue
        if _XML_ID.fullmatch(identifier) is None:
            warnings.append(
                EpubPackageWarning(
                    "invalid_package_id",
                    _citation(request, package_name, EpubPackageCitationSection.PACKAGE, node),
                )
            )
            continue
        if identifier in id_registry or identifier in ambiguous_ids:
            id_registry.pop(identifier, None)
            ambiguous_ids.add(identifier)
            warnings.append(EpubPackageWarning("ambiguous_package_id"))
            continue
        id_registry[identifier] = node
    standard_collection = any(
        _attribute(node, "property") == "belongs-to-collection" and bool(node.text)
        for node in metadata_nodes
    )
    standard_position = any(
        _attribute(node, "property") == "group-position"
        and _valid_group_position(node.text)
        for node in metadata_nodes
    )
    calibre: dict[str, list[tuple[str, _Node]]] = {
        "calibre:series": [],
        "calibre:series_index": [],
    }
    for node in metadata_nodes:
        legacy_name = _attribute(node, "name")
        if legacy_name in calibre:
            calibre[legacy_name].append(
                (_attribute(node, "content") or node.text, node)
            )
    refinement_graph: dict[str, str] = {}
    for node in metadata_nodes:
        identifier = _attribute(node, "id")
        refines = _attribute(node, "refines")
        if refines:
            if not refines.startswith("#") or not refines[1:]:
                warnings.append(EpubPackageWarning("invalid_metadata_refinement"))
                continue
            if refines[1:] not in id_registry:
                warnings.append(EpubPackageWarning("unresolved_metadata_refinement"))
                continue
            if identifier:
                refinement_graph[identifier] = refines[1:]
    for start in refinement_graph:
        visited: set[str] = set()
        current = start
        while current in refinement_graph:
            if current in visited:
                warnings.append(EpubPackageWarning("cyclic_metadata_refinement"))
                break
            visited.add(current)
            current = refinement_graph[current]
    title_types: dict[str, tuple[str, _Node]] = {}
    title_sequences: dict[str, tuple[int, _Node]] = {}
    duplicate_title_refinements: set[tuple[str, str]] = set()
    for node in metadata_nodes:
        if node.name != f"{{{_OPF_NS}}}meta":
            continue
        resolved_property = _resolved_property(_attribute(node, "property"), prefixes)
        if resolved_property not in {"title-type", "display-seq"}:
            continue
        refines = _attribute(node, "refines") or ""
        referenced = id_registry.get(refines[1:]) if refines.startswith("#") else None
        if referenced is None:
            warnings.append(EpubPackageWarning("unresolved_metadata_refinement"))
            continue
        if referenced.name != f"{{{_DC_NS}}}title":
            warnings.append(EpubPackageWarning("inapplicable_metadata_refinement"))
            continue
        value = node.text or _attribute(node, "content") or ""
        key = refines[1:]
        family_key = (key, resolved_property)
        target = title_types if resolved_property == "title-type" else title_sequences
        if family_key in duplicate_title_refinements or key in target:
            target.pop(key, None)
            duplicate_title_refinements.add(family_key)
            warnings.append(EpubPackageWarning("ambiguous_metadata_refinement"))
            continue
        if resolved_property == "title-type":
            if value not in {
                "main",
                "subtitle",
                "short",
                "collection",
                "edition",
                "expanded",
            }:
                warnings.append(EpubPackageWarning("unsupported_vendor_metadata"))
                continue
            title_types[key] = (value, node)
            continue
        try:
            display_value = int(value)
        except ValueError:
            warnings.append(EpubPackageWarning("invalid_metadata_refinement"))
            continue
        if not 0 <= display_value <= 1_000_000:
            warnings.append(EpubPackageWarning("invalid_metadata_refinement"))
            continue
        title_sequences[key] = (display_value, node)
    for node in metadata_nodes:
        local = node.name.rsplit("}", 1)[-1]
        identifier = _attribute(node, "id")
        if node.name.startswith("{" + _DC_NS + "}") and local in _DC_FIELDS:
            if not node.text:
                continue
            target_field = _DC_FIELDS[local]
            normalized = node.text.strip()
            applicability = EpubObservationApplicability.WORK
            preserve_only = False
            display: int | None = None
            selector: str | None = None
            inferred_scheme: str | None = None
            if local == "identifier":
                declared_identifier_scheme = _attribute(node, "scheme", _OPF_NS)
                normalized, applicability, inferred_scheme, invalid_identifier = (
                    _normalize_identifier(node.text, declared_identifier_scheme)
                )
                if identifier and id_registry.get(identifier) is node:
                    identifiers_by_id[identifier] = node
                if invalid_identifier:
                    applicability = EpubObservationApplicability.INAPPLICABLE
                    inferred_scheme = None
                    warnings.append(
                        EpubPackageWarning(
                            "invalid_identifier",
                            _citation(
                                request,
                                package_name,
                                EpubPackageCitationSection.METADATA,
                                node,
                            ),
                        )
                    )
            if local == "date":
                event = _attribute(node, "event", _OPF_NS)
                date_fields = {
                    "creation": EpubPackageField.CREATION_DATE,
                    "publication": EpubPackageField.PUBLICATION_DATE,
                    "modification": EpubPackageField.MODIFICATION_DATE,
                    "copyright": EpubPackageField.COPYRIGHT_DATE,
                }
                target_field = date_fields.get(
                    event or "", EpubPackageField.PUBLICATION_DATE
                )
                supported_event = event is None or event in date_fields
                valid_date = (
                    _valid_publication_date(normalized)
                    if target_field is EpubPackageField.PUBLICATION_DATE
                    and event in {None, "publication"}
                    else _valid_date(normalized)
                )
                if not supported_event or not valid_date:
                    warnings.append(
                        EpubPackageWarning(
                            "invalid_date",
                            _citation(request, package_name, EpubPackageCitationSection.METADATA, node),
                        )
                    )
                    applicability = EpubObservationApplicability.INAPPLICABLE
                    preserve_only = True
            if local == "language" and not _valid_language(normalized):
                warnings.append(
                    EpubPackageWarning(
                        "invalid_language",
                        _citation(request, package_name, EpubPackageCitationSection.METADATA, node),
                    )
                )
                applicability = EpubObservationApplicability.INAPPLICABLE
                preserve_only = True
            if local == "title":
                title_type = title_types.get(identifier or "")
                title_sequence = title_sequences.get(identifier or "")
                if title_type is not None:
                    target_field = {
                        "subtitle": EpubPackageField.SUBTITLE,
                        "collection": EpubPackageField.COLLECTION,
                        "edition": EpubPackageField.EDITION,
                    }.get(title_type[0], EpubPackageField.TITLE)
                if title_sequence is not None:
                    display = title_sequence[0]
                selector_parts = []
                if title_type is not None:
                    selector_parts.append(
                        f"title-type-{title_type[0]}@{title_type[1].ordinal}"
                    )
                if title_sequence is not None:
                    selector_parts.append(f"display-seq@{title_sequence[1].ordinal}")
                selector = ";".join(selector_parts) or None
            language = _attribute(node, "lang", _XML_NS)
            if language is not None and not _valid_language(language):
                warnings.append(
                    EpubPackageWarning(
                        "invalid_language",
                        _citation(request, package_name, EpubPackageCitationSection.METADATA, node),
                    )
                )
                language = None
            observations.append(
                _observation(
                    request,
                    package_name,
                    version,
                    node,
                    target_field,
                    normalized=normalized,
                    applicability=applicability,
                    display=display,
                    language=language,
                    selector=selector,
                    preserve_only=preserve_only,
                )
            )
            if (
                local == "identifier"
                and inferred_scheme is not None
                and _attribute(node, "scheme", _OPF_NS) is None
            ):
                observations.append(
                    _observation(
                        request,
                        package_name,
                        version,
                        _Node(
                            node.name,
                            node.attributes,
                            text_parts=[inferred_scheme],
                            ordinal=node.ordinal,
                        ),
                        EpubPackageField.IDENTIFIER_SCHEME,
                        applicability=applicability,
                        selector="inferred-identifier-type",
                    )
                )
            qualifiers = (
                ("role", _OPF_NS, EpubPackageField.CREATOR_ROLE if local == "creator" else EpubPackageField.CONTRIBUTOR_ROLE),
                ("file-as", _OPF_NS, EpubPackageField.SORT_KEY),
                ("scheme", _OPF_NS, EpubPackageField.IDENTIFIER_SCHEME),
                ("event", _OPF_NS, EpubPackageField.DATE_EVENT),
            )
            for attribute_name, namespace, qualifier_field in qualifiers:
                qualifier = _attribute(node, attribute_name, namespace)
                if not qualifier:
                    continue
                if attribute_name == "role" and local not in {"creator", "contributor"}:
                    continue
                if attribute_name == "file-as" and local not in {"creator", "contributor", "title"}:
                    continue
                if attribute_name == "scheme" and local != "identifier":
                    continue
                if attribute_name == "event" and local != "date":
                    continue
                qualifier_node = _Node(node.name, node.attributes, text_parts=[qualifier], ordinal=node.ordinal)
                observations.append(
                    _observation(
                        request,
                        package_name,
                        version,
                        qualifier_node,
                        qualifier_field,
                        applicability=EpubObservationApplicability.EDITION,
                        selector=attribute_name,
                    )
                )
        elif node.name == f"{{{_OPF_NS}}}meta":
            property_name = _attribute(node, "property")
            resolved_property = _resolved_property(property_name, prefixes)
            legacy_name = _attribute(node, "name")
            content = _attribute(node, "content")
            value = node.text or content or ""
            if resolved_property == "dcterms:modified" and value:
                synthetic = _Node(node.name, node.attributes, text_parts=[value], ordinal=node.ordinal)
                valid_modified = _valid_modified_date(value)
                observations.append(
                    _observation(
                        request,
                        package_name,
                        version,
                        synthetic,
                        EpubPackageField.MODIFICATION_DATE,
                        applicability=(
                            EpubObservationApplicability.WORK
                            if valid_modified
                            else EpubObservationApplicability.INAPPLICABLE
                        ),
                        preserve_only=not valid_modified,
                    )
                )
                if not valid_modified:
                    warnings.append(
                        EpubPackageWarning(
                            "invalid_date",
                            _citation(request, package_name, EpubPackageCitationSection.METADATA, node),
                        )
                    )
            elif resolved_property in {"belongs-to-collection", "group-position"} and value:
                if resolved_property == "group-position" and not _valid_group_position(value):
                    warnings.append(EpubPackageWarning("invalid_metadata_refinement"))
                    continue
                synthetic = _Node(node.name, node.attributes, text_parts=[value], ordinal=node.ordinal)
                observations.append(
                    _observation(
                        request,
                        package_name,
                        version,
                        synthetic,
                        EpubPackageField.COLLECTION if resolved_property == "belongs-to-collection" else EpubPackageField.COLLECTION_POSITION,
                        applicability=EpubObservationApplicability.EDITION,
                    )
                )
            elif resolved_property in {"title-type", "display-seq"}:
                continue
            elif resolved_property in {
                "role",
                "file-as",
                "identifier-type",
            } and value:
                refines = _attribute(node, "refines") or ""
                referenced = id_registry.get(refines[1:]) if refines.startswith("#") else None
                if referenced is None:
                    warnings.append(EpubPackageWarning("unresolved_metadata_refinement"))
                    continue
                allowed_targets = {
                    "role": {
                        f"{{{_DC_NS}}}creator",
                        f"{{{_DC_NS}}}contributor",
                    },
                    "file-as": {
                        f"{{{_DC_NS}}}title",
                        f"{{{_DC_NS}}}creator",
                        f"{{{_DC_NS}}}contributor",
                    },
                    "identifier-type": {f"{{{_DC_NS}}}identifier"},
                }
                if referenced.name not in allowed_targets[resolved_property]:
                    warnings.append(EpubPackageWarning("inapplicable_metadata_refinement"))
                    continue
                # Associate an EPUB3 refinement with its explicit DC target so
                # projection never attaches it to a merely preceding person.
                synthetic = _Node(node.name, node.attributes, text_parts=[value], ordinal=referenced.ordinal)
                observations.append(
                    _observation(
                        request,
                        package_name,
                        version,
                        synthetic,
                        (
                            EpubPackageField.CONTRIBUTOR_ROLE
                            if resolved_property == "role"
                            and referenced.name == f"{{{_DC_NS}}}contributor"
                            else EpubPackageField.CREATOR_ROLE
                            if resolved_property == "role"
                            else EpubPackageField.SORT_KEY
                            if resolved_property == "file-as"
                            else EpubPackageField.IDENTIFIER_SCHEME
                        ),
                        applicability=EpubObservationApplicability.EDITION,
                        selector=f"{resolved_property}-{node.ordinal}",
                    )
                )
            elif resolved_property in {
                "schema:bookEdition",
                "schema:bookFormat",
                "schema:contentLocation",
            } and value:
                synthetic = _Node(node.name, node.attributes, text_parts=[value], ordinal=node.ordinal)
                observations.append(
                    _observation(
                        request,
                        package_name,
                        version,
                        synthetic,
                        {
                            "schema:bookEdition": EpubPackageField.EDITION,
                            "schema:bookFormat": EpubPackageField.FORMAT,
                            "schema:contentLocation": EpubPackageField.REGION,
                        }[resolved_property],
                        applicability=EpubObservationApplicability.EDITION,
                    )
                )
            elif property_name or (
                legacy_name
                and legacy_name
                not in {"calibre:series", "calibre:series_index", "cover"}
            ):
                warnings.append(EpubPackageWarning("unsupported_vendor_metadata", _citation(request, package_name, EpubPackageCitationSection.METADATA, node)))
    series_values = calibre["calibre:series"]
    position_values = calibre["calibre:series_index"]
    for value, node in series_values:
        if not value.strip():
            continue
        synthetic = _Node(
            node.name, node.attributes, text_parts=[value], ordinal=node.ordinal
        )
        observations.append(
            _observation(
                request,
                package_name,
                version,
                synthetic,
                EpubPackageField.COLLECTION,
                applicability=EpubObservationApplicability.EDITION,
                selector="calibre-fallback",
                preserve_only=standard_collection,
            )
        )
    for index, (value, node) in enumerate(position_values):
        if (
            not value.strip()
            or index >= len(series_values)
            or not series_values[index][0].strip()
        ):
            continue
        synthetic = _Node(
            node.name, node.attributes, text_parts=[value], ordinal=node.ordinal
        )
        observations.append(
            _observation(
                request,
                package_name,
                version,
                synthetic,
                EpubPackageField.COLLECTION_POSITION,
                applicability=EpubObservationApplicability.EDITION,
                selector="calibre-fallback",
                preserve_only=standard_position,
            )
        )
    if (
        len(series_values) != len(position_values)
        or any(not value.strip() for value, _node in series_values)
        or any(not value.strip() for value, _node in position_values)
    ) and (series_values or position_values):
        warnings.append(EpubPackageWarning("incomplete_calibre_series"))
    required = {EpubPackageField.TITLE, EpubPackageField.LANGUAGE, EpubPackageField.IDENTIFIER}
    applicable_fields = {
        item.field
        for item in observations
        if item.applicability is not EpubObservationApplicability.INAPPLICABLE
    }
    if not required.issubset(applicable_fields):
        warnings.append(EpubPackageWarning("incomplete_required_metadata"))
    if version == "3.0" and EpubPackageField.MODIFICATION_DATE not in applicable_fields:
        warnings.append(EpubPackageWarning("missing_modification_date"))
    unique_identifier = _attribute(root, "unique-identifier")
    primary = identifiers_by_id.get(unique_identifier or "")
    if primary is None and len(identifiers_by_id) == 1:
        primary = next(iter(identifiers_by_id.values()))
        warnings.append(EpubPackageWarning("inferred_package_identifier"))
    elif primary is None:
        warnings.append(EpubPackageWarning("missing_package_identifier"))
    if primary is not None:
        primary_normalized, primary_applicability, _primary_scheme, primary_invalid = (
            _normalize_identifier(
                primary.text,
                _attribute(primary, "scheme", _OPF_NS),
            )
        )
        if primary_invalid:
            primary_applicability = EpubObservationApplicability.INAPPLICABLE
        observations.append(
            _observation(
                request,
                package_name,
                version,
                primary,
                EpubPackageField.PACKAGE_IDENTIFIER,
                normalized=primary_normalized,
                applicability=primary_applicability,
                selector="primary-identifier",
            )
        )
    version_node = _Node(root.name, root.attributes, text_parts=[declared_version], ordinal=root.ordinal)
    observations.append(_observation(request, package_name, version, version_node, EpubPackageField.PACKAGE_VERSION, applicability=EpubObservationApplicability.FILE))
    manifest_ids: dict[str, str] = {}
    manifest_nodes: dict[str, _Node] = {}
    manifest_media_types: dict[str, str] = {}
    local_manifest_ids: set[str] = set()
    manifest_resources: set[str] = set()
    manifest_fallbacks: dict[str, str] = {}
    media_overlays: list[str] = []
    nav_count = 0
    cover_current_ids: list[str] = []
    for item in manifest.children:
        if item.name != f"{{{_OPF_NS}}}item":
            raise _Rejected(EpubInspectionOutcome.MALFORMED)
        identifier = _attribute(item, "id")
        href = _attribute(item, "href")
        media_type = _attribute(item, "media-type")
        if (
            not identifier
            or id_registry.get(identifier) is not item
            or not href
            or not media_type
            or _MEDIA_TYPE.fullmatch(media_type) is None
        ):
            warnings.append(EpubPackageWarning("invalid_manifest_item"))
            continue
        properties = tuple(sorted(set((_attribute(item, "properties") or "").split())))
        if (
            len(properties) > 64
            or len(media_type) > 128
            or any(len(value) > 64 for value in properties)
        ):
            warnings.append(EpubPackageWarning("invalid_manifest_item"))
            continue
        try:
            validate_projection_text(identifier, "public manifest identifier", 256)
            validate_projection_text(media_type, "public manifest media type", 128)
            for property_value in properties:
                validate_code(property_value, "manifest property")
                validate_projection_text(
                    property_value, "public manifest property", 64
                )
        except EvidenceValueError:
            warnings.append(
                EpubPackageWarning(
                    "unsafe_manifest_item",
                    _citation(request, package_name, EpubPackageCitationSection.MANIFEST, item),
                )
            )
            continue
        try:
            resource_identity, resolved = _declared_resource_identity(
                package_name, href, entries
            )
        except _InvalidReferenceSyntax:
            warnings.append(EpubPackageWarning("invalid_manifest_item"))
            continue
        if resolved is not None and resolved not in entries:
            warnings.append(
                EpubPackageWarning(
                    "missing_manifest_resource",
                    _citation(request, package_name, EpubPackageCitationSection.MANIFEST, item),
                )
            )
            continue
        if resource_identity in manifest_resources:
            warnings.append(EpubPackageWarning("duplicate_manifest_resource"))
        manifest_resources.add(resource_identity)
        manifest_ids[identifier] = resource_identity
        manifest_nodes[identifier] = item
        manifest_media_types[identifier] = media_type
        if resolved is not None:
            local_manifest_ids.add(identifier)
        fallback = _attribute(item, "fallback")
        if fallback:
            manifest_fallbacks[identifier] = fallback
        media_overlay = _attribute(item, "media-overlay")
        if media_overlay:
            media_overlays.append(media_overlay)
        valid_navigation = (
            "nav" in properties
            and identifier in local_manifest_ids
            and media_type == "application/xhtml+xml"
        )
        valid_cover = (
            "cover-image" in properties
            and identifier in local_manifest_ids
            and media_type
            in {"image/gif", "image/jpeg", "image/png", "image/svg+xml", "image/webp"}
        )
        if "nav" in properties and not valid_navigation:
            warnings.append(EpubPackageWarning("invalid_navigation_declaration"))
        if "cover-image" in properties and not valid_cover:
            warnings.append(EpubPackageWarning("invalid_cover_declaration"))
        if valid_navigation:
            nav_count += 1
        if valid_cover:
            cover_current_ids.append(identifier)
        declarations.append(
            EpubPackageDeclaration(
                EpubPackageDeclarationKind.MANIFEST_ITEM,
                item.ordinal,
                _citation(request, package_name, EpubPackageCitationSection.MANIFEST, item),
                resource_identity,
                identifier,
                media_type,
                properties,
            )
        )
        if valid_navigation:
            declarations.append(
                EpubPackageDeclaration(
                    EpubPackageDeclarationKind.NAVIGATION,
                    item.ordinal,
                    _citation(request, package_name, EpubPackageCitationSection.NAVIGATION, item),
                    resource_identity,
                    identifier,
                    media_type,
                    properties,
                )
            )
        if valid_cover:
            declarations.append(
                EpubPackageDeclaration(
                    EpubPackageDeclarationKind.COVER,
                    item.ordinal,
                    _citation(request, package_name, EpubPackageCitationSection.COVER, item),
                    resource_identity,
                    identifier,
                    media_type,
                    properties,
                )
            )
    cover_current_resources = {
        manifest_ids[identifier] for identifier in cover_current_ids
    }
    cover_current_id = (
        cover_current_ids[0]
        if cover_current_ids and len(cover_current_resources) == 1
        else None
    )
    if len(cover_current_resources) > 1:
        warnings.append(EpubPackageWarning("contradictory_cover_declaration"))
    if any(fallback not in manifest_ids for fallback in manifest_fallbacks.values()):
        warnings.append(EpubPackageWarning("invalid_manifest_fallback"))
        manifest_fallbacks = {
            source: target
            for source, target in manifest_fallbacks.items()
            if target in manifest_ids
        }
    if any(overlay not in manifest_ids for overlay in media_overlays):
        warnings.append(EpubPackageWarning("invalid_media_overlay"))
    if any(
        overlay not in local_manifest_ids
        or manifest_media_types.get(overlay) != "application/smil+xml"
        for overlay in media_overlays
    ):
        warnings.append(EpubPackageWarning("invalid_media_overlay"))
    for start in manifest_fallbacks:
        visited: set[str] = set()
        current = start
        while current in manifest_fallbacks:
            if current in visited:
                warnings.append(EpubPackageWarning("cyclic_manifest_fallback"))
                break
            visited.add(current)
            current = manifest_fallbacks[current]
    if version == "3.0" and nav_count != 1:
        warnings.append(EpubPackageWarning("invalid_navigation_declaration"))

    spine_media_types = (
        _EPUB3_SPINE_MEDIA_TYPES if version == "3.0" else _EPUB2_SPINE_MEDIA_TYPES
    )

    def has_compatible_spine_target(identifier: str) -> bool:
        visited: set[str] = set()
        current = identifier
        while current not in visited:
            visited.add(current)
            if current not in local_manifest_ids:
                return False
            if manifest_media_types.get(current) in spine_media_types:
                return True
            fallback = manifest_fallbacks.get(current)
            if fallback is None:
                return False
            current = fallback
        return False

    accepted_spine_ids: set[str] = set()
    for spine_position, itemref in enumerate(spine.children, 1):
        if itemref.name != f"{{{_OPF_NS}}}itemref":
            warnings.append(EpubPackageWarning("invalid_spine_item"))
            continue
        citation = _citation(
            request, package_name, EpubPackageCitationSection.SPINE, itemref
        )
        if itemref.children or itemref.text:
            warnings.append(EpubPackageWarning("invalid_spine_item", citation))
            continue
        idref = _attribute(itemref, "idref")
        linear = _attribute(itemref, "linear")
        is_linear = linear in {None, "yes"}
        if not is_linear and linear != "no":
            warnings.append(EpubPackageWarning("invalid_spine_item", citation))
        declarations.append(
            EpubPackageDeclaration(
                EpubPackageDeclarationKind.SPINE_SLOT,
                itemref.ordinal,
                citation,
                manifest_ids.get(idref) if idref else None,
                idref,
                linear=is_linear,
                spine_position=spine_position,
            )
        )
        if not idref or not has_compatible_spine_target(idref):
            warnings.append(EpubPackageWarning("invalid_spine_item"))
            continue
        if idref in accepted_spine_ids:
            warnings.append(EpubPackageWarning("invalid_spine_item", citation))
            continue
        accepted_spine_ids.add(idref)
        declarations.append(
            EpubPackageDeclaration(
                EpubPackageDeclarationKind.SPINE_ITEM,
                itemref.ordinal,
                citation,
                manifest_ids[idref],
                idref,
                linear=is_linear,
                spine_position=spine_position,
            )
        )
    toc = _attribute(spine, "toc")
    if version == "2.0" and not toc:
        warnings.append(EpubPackageWarning("invalid_ncx_declaration"))
    elif toc:
        manifest_node = manifest_nodes.get(toc)
        if (
            toc not in local_manifest_ids
            or manifest_media_types.get(toc) != "application/x-dtbncx+xml"
            or manifest_node is None
            or (
                version == "2.0"
                and any(
                    _attribute(manifest_node, attribute) is not None
                    for attribute in (
                        "fallback",
                        "fallback-style",
                        "required-namespace",
                    )
                )
            )
        ):
            warnings.append(EpubPackageWarning("invalid_ncx_declaration"))
        else:
            declarations.append(EpubPackageDeclaration(EpubPackageDeclarationKind.NCX, spine.ordinal, _citation(request, package_name, EpubPackageCitationSection.NAVIGATION, spine), manifest_ids[toc], toc))
    if tours:
        valid_tours = version == "2.0" and len(tours) == 1 and bool(tours[0].children)
        for tour in tours[0].children if len(tours) == 1 else ():
            valid_tours = valid_tours and (
                tour.name == f"{{{_OPF_NS}}}tour"
                and bool(_attribute(tour, "title"))
                and bool(tour.children)
            )
            for site in tour.children:
                href = _attribute(site, "href")
                valid_tours = valid_tours and (
                    site.name == f"{{{_OPF_NS}}}site"
                    and bool(_attribute(site, "title"))
                    and bool(href)
                    and not site.children
                )
                if href:
                    clean_href = href.split("#", 1)[0]
                    if not clean_href:
                        valid_tours = False
                        continue
                    try:
                        resource_identity, resolved = _declared_resource_identity(
                            package_name,
                            clean_href,
                            entries,
                        )
                    except _InvalidReferenceSyntax:
                        valid_tours = False
                        continue
                    valid_tours = valid_tours and (
                        (resolved is None or resolved in entries)
                        and resource_identity in manifest_resources
                    )
        if not valid_tours:
            warnings.append(EpubPackageWarning("invalid_deprecated_tours"))
    guides = _children(root, "guide", _OPF_NS)
    if len(guides) > 1:
        warnings.append(EpubPackageWarning("invalid_guide_reference"))
    if len(guides) == 1:
        if len(guides[0].children) > request.limits.guide_references:
            raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
        for reference in guides[0].children:
            if reference.name != f"{{{_OPF_NS}}}reference":
                warnings.append(EpubPackageWarning("invalid_guide_reference"))
                continue
            href = _attribute(reference, "href")
            if not href or reference.children or reference.text:
                warnings.append(EpubPackageWarning("invalid_guide_reference"))
                continue
            clean_href = href.split("#", 1)[0]
            if not clean_href:
                warnings.append(EpubPackageWarning("invalid_guide_reference"))
                continue
            try:
                resource_identity, resolved = _declared_resource_identity(
                    package_name, clean_href, entries
                )
            except _InvalidReferenceSyntax:
                warnings.append(EpubPackageWarning("invalid_guide_reference"))
                continue
            reference_type = _attribute(reference, "type")
            if (
                not reference_type
                or _XML_ID.fullmatch(reference_type) is None
                or (resolved is not None and resolved not in entries)
            ):
                warnings.append(EpubPackageWarning("invalid_guide_reference"))
                continue
            try:
                validate_projection_text(
                    reference_type, "public guide reference type", 256
                )
            except EvidenceValueError:
                warnings.append(EpubPackageWarning("invalid_guide_reference"))
                continue
            declarations.append(EpubPackageDeclaration(EpubPackageDeclarationKind.GUIDE_REFERENCE, reference.ordinal, _citation(request, package_name, EpubPackageCitationSection.GUIDE, reference), resource_identity, reference_type))
    required_resources = {_MIMETYPE, _CONTAINER, _ENCRYPTION, package_name}
    if required_resources & encryption_names:
        raise _Rejected(EpubInspectionOutcome.ENCRYPTED)
    if encryption_names:
        warnings.append(EpubPackageWarning("opaque_resource_encrypted"))
    if encryption_present:
        warnings.append(EpubPackageWarning("encryption_declarations_present"))
    legacy_covers: list[tuple[str, _Node]] = []
    for node in metadata_nodes:
        if node.name == f"{{{_OPF_NS}}}meta" and _attribute(node, "name") == "cover":
            cover_id = _attribute(node, "content")
            if (
                not cover_id
                or cover_id not in local_manifest_ids
                or manifest_media_types.get(cover_id)
                not in {"image/gif", "image/jpeg", "image/png", "image/svg+xml", "image/webp"}
            ):
                warnings.append(EpubPackageWarning("invalid_cover_declaration"))
                continue
            legacy_covers.append((cover_id, node))
    legacy_cover_ids = [identifier for identifier, _node in legacy_covers]
    legacy_cover_resources = {
        manifest_ids[identifier] for identifier in legacy_cover_ids
    }
    legacy_cover_id = (
        legacy_cover_ids[0]
        if legacy_cover_ids and len(legacy_cover_resources) == 1
        else None
    )
    legacy_cover_node = next(
        (node for identifier, node in legacy_covers if identifier == legacy_cover_id),
        None,
    )
    if len(legacy_cover_resources) > 1:
        warnings.append(EpubPackageWarning("contradictory_cover_declaration"))
    selected_cover_id = (
        cover_current_id if cover_current_ids else legacy_cover_id
    )
    selected_cover = (
        manifest_ids[selected_cover_id] if selected_cover_id is not None else None
    )
    if selected_cover:
        cover_node = (
            manifest_nodes[selected_cover_id]
            if cover_current_ids
            else legacy_cover_node
        )
        assert cover_node is not None
        observations.append(_observation(request, package_name, version, _Node(cover_node.name, cover_node.attributes, text_parts=[selected_cover], ordinal=cover_node.ordinal), EpubPackageField.COVER, applicability=EpubObservationApplicability.FILE))
        if not cover_current_ids:
            declarations.append(
                EpubPackageDeclaration(
                    EpubPackageDeclarationKind.COVER,
                    cover_node.ordinal,
                    _citation(request, package_name, EpubPackageCitationSection.COVER, cover_node),
                    selected_cover,
                    selected_cover_id,
                    manifest_media_types[selected_cover_id],
                )
            )
    if (
        cover_current_ids
        and any(
            current_resource != legacy_resource
            for current_resource in cover_current_resources
            for legacy_resource in legacy_cover_resources
        )
    ):
        warnings.append(EpubPackageWarning("contradictory_cover_declaration"))
    values_by_field: dict[EpubPackageField, set[str]] = {}
    for observation in observations:
        if observation.applicability is EpubObservationApplicability.INAPPLICABLE:
            continue
        value = observation.normalized_value or observation.raw_value
        values_by_field.setdefault(observation.field, set()).add(value)
    for field_name, warning_code in (
        (EpubPackageField.TITLE, "ambiguous_title"),
        (EpubPackageField.CREATOR, "ambiguous_contributor"),
        (EpubPackageField.CONTRIBUTOR, "ambiguous_contributor"),
        (EpubPackageField.IDENTIFIER, "ambiguous_identifier"),
        (EpubPackageField.COLLECTION, "contradictory_series"),
        (EpubPackageField.COLLECTION_POSITION, "contradictory_series"),
        (EpubPackageField.EDITION, "ambiguous_edition"),
        (EpubPackageField.PUBLICATION_DATE, "contradictory_date"),
        (EpubPackageField.MODIFICATION_DATE, "contradictory_date"),
        (EpubPackageField.CREATION_DATE, "contradictory_date"),
        (EpubPackageField.COPYRIGHT_DATE, "contradictory_date"),
    ):
        if len(values_by_field.get(field_name, set())) > 1:
            warnings.append(EpubPackageWarning(warning_code))
    if any(
        observation.field in {EpubPackageField.IDENTIFIER, EpubPackageField.PACKAGE_IDENTIFIER}
        and observation.applicability is EpubObservationApplicability.UNKNOWN
        for observation in observations
    ):
        warnings.append(EpubPackageWarning("identifier_applicability_unknown"))
    redacted = next(
        (
            observation
            for observation in observations
            if observation.raw_value.startswith(_UNSAFE_MARKER)
        ),
        None,
    )
    if redacted is not None:
        warnings.append(
            EpubPackageWarning("unsafe_metadata_redacted", redacted.citation)
        )
    oversized = next(
        (
            observation
            for observation in observations
            if observation.raw_value.startswith(_OVERSIZED_MARKER)
            or (
                observation.normalized_value is not None
                and observation.normalized_value.startswith(_OVERSIZED_MARKER)
            )
        ),
        None,
    )
    if oversized is not None:
        warnings.append(
            EpubPackageWarning("oversized_metadata_redacted", oversized.citation)
        )
    warnings = list({warning.sort_key: warning for warning in warnings}.values())
    if len(warnings) > request.limits.MAX_WARNINGS:
        warnings = [
            EpubPackageWarning(code)
            for code in sorted({warning.code for warning in warnings})
        ]
        if len(warnings) < request.limits.MAX_WARNINGS:
            warnings.append(EpubPackageWarning("warning_occurrences_aggregated"))
    field_counts: dict[EpubPackageField, int] = {}
    for observation in observations:
        field_counts[observation.field] = field_counts.get(observation.field, 0) + 1
    if (
        len(observations) > request.limits.MAX_OBSERVATIONS
        or len(warnings) > request.limits.MAX_WARNINGS
        or len(declarations) > MAX_DECLARATIONS
        or any(
            count > request.limits.MAX_VALUES_PER_FIELD
            for count in field_counts.values()
        )
    ):
        raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
    observations.sort(key=lambda value: (value.citation.source_ordinal, value.citation.identity))
    declarations.sort(key=lambda value: (value.source_ordinal, value.citation.identity))
    warnings.sort(key=lambda value: value.sort_key)
    try:
        return EpubPackageReport(
            EpubInspectionOutcome.SUCCESS,
            EpubPackageCapability.PACKAGE,
            request.limits,
            request.expected_source,
            tuple(observations),
            tuple(warnings),
            tuple(declarations),
            usage,
        )
    except EvidenceValueError as error:
        if str(error) == "local evidence report exceeds its byte limit":
            raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT) from None
        raise


def _expat_supported() -> bool:
    try:
        return tuple(int(part) for part in expat.EXPAT_VERSION.split("_")[-1].split(".")) >= (2, 7, 2)
    except (AttributeError, TypeError, ValueError):
        return False


class EpubPackageExtractor:
    """Extract one bounded OPF package report from an authorized byte source."""

    def preflight(self, request: EpubPackageRequest) -> EpubInspectionOutcome | None:
        """Bound hostile central-directory work before ``ZipFile`` is opened."""
        try:
            _check_request(request)
            if datetime.now(timezone.utc) >= request.deadline:
                raise _Rejected(EpubInspectionOutcome.TIMEOUT)
            _central_directory(request)
        except _Rejected as rejected:
            return rejected.outcome
        return None

    def extract(
        self, request: EpubPackageRequest, archive: zipfile.ZipFile | None = None
    ) -> EpubPackageReport:
        if type(request) is not EpubPackageRequest or request.capability is not EpubPackageCapability.PACKAGE:
            return EpubPackageReport(EpubInspectionOutcome.INTERNAL_FAILURE, EpubPackageCapability.PACKAGE, EpubPackageLimits())
        try:
            _check_request(request)
            if datetime.now(timezone.utc) >= request.deadline:
                raise _Rejected(EpubInspectionOutcome.TIMEOUT)
            if not _expat_supported():
                raise _Rejected(EpubInspectionOutcome.UNAVAILABLE_SOURCE)
            entries_tuple = _central_directory(request)
            entries = {entry.name: entry for entry in entries_tuple}
            if _MIMETYPE not in entries or _CONTAINER not in entries:
                raise _Rejected(EpubInspectionOutcome.MALFORMED)
            own_archive = archive is None
            if own_archive:
                source_io = _SourceIO(request)
                try:
                    archive = zipfile.ZipFile(source_io, "r", allowZip64=True)
                except (zipfile.BadZipFile, OSError, ValueError):
                    raise _Rejected(EpubInspectionOutcome.INTEGRITY_FAILURE) from None
            with (archive if own_archive else nullcontext(archive)):
                marker = entries[_MIMETYPE]
                noncanonical_mimetype = (
                    entries_tuple[0].name != _MIMETYPE
                    or marker.local_offset != 0
                    or marker.method != zipfile.ZIP_STORED
                    or bool(marker.flags & 0x08)
                    or bool(marker.local_extra_length)
                    or bool(marker.central_extra_length)
                    or marker.expanded_size != len(_MIMETYPE_VALUE)
                )
                mimetype = _member_bytes(request, archive, entries, _MIMETYPE, 64)
                if mimetype != _MIMETYPE_VALUE:
                    if mimetype.removeprefix(b"\xef\xbb\xbf").strip() != _MIMETYPE_VALUE:
                        raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_FORMAT)
                    noncanonical_mimetype = True
                container_bytes = _member_bytes(request, archive, entries, _CONTAINER, request.limits.container_xml_bytes)
                container_root, container_usage = _parse_xml(container_bytes, request.limits, request)
                if container_root.name != "{" + _CONTAINER_NS + "}container" or _attribute(container_root, "version") != "1.0":
                    raise _Rejected(EpubInspectionOutcome.MALFORMED)
                rootfiles_groups = _children(container_root, "rootfiles", _CONTAINER_NS)
                container_child_names = [node.name for node in container_root.children]
                allowed_container_sequences = (
                    [f"{{{_CONTAINER_NS}}}rootfiles"],
                    [
                        f"{{{_CONTAINER_NS}}}rootfiles",
                        f"{{{_CONTAINER_NS}}}links",
                    ],
                )
                if (
                    len(rootfiles_groups) != 1
                    or container_child_names not in allowed_container_sequences
                ):
                    raise _Rejected(EpubInspectionOutcome.MALFORMED)
                rootfiles = _children(rootfiles_groups[0], "rootfile", _CONTAINER_NS)
                if len(rootfiles) != len(rootfiles_groups[0].children):
                    raise _Rejected(EpubInspectionOutcome.MALFORMED)
                paths: list[str] = []
                for rootfile in rootfiles:
                    full_path = _attribute(rootfile, "full-path")
                    media_type = _attribute(rootfile, "media-type")
                    if not full_path or media_type != "application/oebps-package+xml":
                        raise _Rejected(EpubInspectionOutcome.MALFORMED)
                    paths.append(_resolve_member("", full_path, entries))
                if not paths:
                    raise _Rejected(EpubInspectionOutcome.MALFORMED)
                if len(paths) != len(set(paths)):
                    raise _Rejected(EpubInspectionOutcome.MALFORMED)
                if len(paths) > 1:
                    raise _Rejected(EpubInspectionOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS)
                package_name = paths[0]
                encryption_names: set[str] = set()
                encryption_declarations: list[tuple[str, _Node]] = []
                encryption_usage = _XmlUsage()
                if _ENCRYPTION in entries:
                    encryption_bytes = _member_bytes(request, archive, entries, _ENCRYPTION, request.limits.container_xml_bytes)
                    encryption_root, encryption_usage = _parse_xml(encryption_bytes, request.limits, request)
                    if encryption_root.name != f"{{{_ENC_NS}}}encryption":
                        raise _Rejected(EpubInspectionOutcome.MALFORMED)
                    encrypted_data_name = f"{{{_XMLENC_NS}}}EncryptedData"
                    encrypted_key_name = f"{{{_XMLENC_NS}}}EncryptedKey"
                    if any(
                        node.name not in {encrypted_data_name, encrypted_key_name}
                        for node in encryption_root.children
                    ):
                        raise _Rejected(EpubInspectionOutcome.MALFORMED)
                    encrypted_data_nodes = [
                        node
                        for node in encryption_root.children
                        if node.name == encrypted_data_name
                    ]
                    if not encrypted_data_nodes:
                        raise _Rejected(EpubInspectionOutcome.MALFORMED)
                    for encrypted_data in encrypted_data_nodes:
                        allowed_encrypted_children = (
                            f"{{{_XMLENC_NS}}}EncryptionMethod",
                            f"{{{_XMLDSIG_NS}}}KeyInfo",
                            f"{{{_XMLENC_NS}}}CipherData",
                            f"{{{_XMLENC_NS}}}EncryptionProperties",
                        )
                        child_names = [node.name for node in encrypted_data.children]
                        if (
                            any(name not in allowed_encrypted_children for name in child_names)
                            or any(child_names.count(name) > 1 for name in allowed_encrypted_children)
                            or child_names != sorted(
                                child_names,
                                key=allowed_encrypted_children.index,
                            )
                        ):
                            raise _Rejected(EpubInspectionOutcome.MALFORMED)
                        cipher_data = _children(
                            encrypted_data, "CipherData", _XMLENC_NS
                        )
                        if len(cipher_data) != 1:
                            raise _Rejected(EpubInspectionOutcome.MALFORMED)
                        references = _children(
                            cipher_data[0], "CipherReference", _XMLENC_NS
                        )
                        if len(references) != 1 or len(cipher_data[0].children) != 1:
                            raise _Rejected(EpubInspectionOutcome.MALFORMED)
                        reference = references[0]
                        if reference.children:
                            raise _Rejected(EpubInspectionOutcome.MALFORMED)
                        uri = _attribute(reference, "URI")
                        if not uri:
                            raise _Rejected(EpubInspectionOutcome.MALFORMED)
                        normalized_uri = _resolve_member("", uri, entries)
                        encryption_names.add(normalized_uri)
                        encryption_declarations.append(
                            (normalized_uri, reference)
                        )
                if package_name in encryption_names:
                    raise _Rejected(EpubInspectionOutcome.ENCRYPTED)
                package_bytes = _member_bytes(request, archive, entries, package_name, request.limits.opf_package_bytes)
                package_root, package_usage = _parse_xml(package_bytes, request.limits, request)
            usage = EpubPackageUsage(
                archive_entries=len(entries),
                opened_members=3 + (_ENCRYPTION in entries),
                compressed_bytes=sum(entry.compressed_size for entry in entries.values()),
                expanded_bytes=sum(entry.expanded_size for entry in entries.values()),
                xml_nodes=container_usage.nodes + encryption_usage.nodes + package_usage.nodes,
                xml_attributes=container_usage.attributes + encryption_usage.attributes + package_usage.attributes,
                xml_text_characters=container_usage.text_characters + encryption_usage.text_characters + package_usage.text_characters,
                metadata_nodes=0,
                manifest_items=sum(len(node.children) for node in _children(package_root, "manifest", _OPF_NS)),
                spine_items=sum(len(node.children) for node in _children(package_root, "spine", _OPF_NS)),
                guide_references=sum(len(node.children) for node in _children(package_root, "guide", _OPF_NS)),
                prefix_mappings=len((_attribute(package_root, "prefix") or "").split()) // 2,
            )
            if usage.expanded_bytes > request.limits.expanded_bytes_per_archive or usage.prefix_mappings > request.limits.prefix_mappings:
                raise _Rejected(EpubInspectionOutcome.RESOURCE_LIMIT)
            return _package_report(
                request,
                package_name,
                package_root,
                entries,
                encryption_names,
                encryption_declarations,
                _ENCRYPTION in entries,
                usage,
                rootfiles[0],
                noncanonical_mimetype,
            )
        except _Rejected as rejected:
            return EpubPackageReport(rejected.outcome, EpubPackageCapability.PACKAGE, request.limits)
        except EpubPackageSourceError:
            return EpubPackageReport(EpubInspectionOutcome.UNAVAILABLE_SOURCE, EpubPackageCapability.PACKAGE, request.limits)
        except MemoryError:
            return EpubPackageReport(EpubInspectionOutcome.RESOURCE_LIMIT, EpubPackageCapability.PACKAGE, request.limits)
        except EvidenceValueError:
            return EpubPackageReport(EpubInspectionOutcome.MALFORMED, EpubPackageCapability.PACKAGE, request.limits)
        except Exception:
            return EpubPackageReport(EpubInspectionOutcome.INTERNAL_FAILURE, EpubPackageCapability.PACKAGE, request.limits)


__all__ = ["EpubPackageExtractor"]
