#!/usr/bin/env python3
"""Capture and replay sealed private provider evidence for qualification."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
import zlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Never

import httpx

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.qualification_state_bridge import (
    _connect_read_only,
)
from mechanical_librarian.adapters.persistence.schema import (
    _validate_report_row,
    _validate_schema,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
    GoogleBooksPacer,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
    HardcoverPacer,
)
from mechanical_librarian.adapters.providers.inventaire_metadata import (
    InventaireMetadataProvider,
    InventairePacer,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.adapters.providers.retry_safety import ProviderRequestPacer
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ProviderConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    TargetCover,
    TargetSnapshot,
    metadata_discovery_plan,
    observe_provider,
)
from mechanical_librarian.domain.metadata_report import REPORT_SCHEMA, MetadataReport
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.infrastructure.configuration import (
    load_configuration_document,
)
from mechanical_librarian.infrastructure.security.credentials import (
    google_books_client,
    hardcover_client_token,
    inventaire_client,
    open_library_client,
    read_secret,
)

_BUNDLE_SCHEMA = "provider-replay-bundle-v1"
_RESULT_SCHEMA = "provider-replay-result-v1"
_SELECTION_SCHEMA = "provider-replay-selection-v1"
_MAX_BUNDLE_BYTES = 536_870_912
_MAX_CONFIG_BYTES = 262_144
_MAX_SELECTION_BYTES = 65_536
_MAX_COHORT = 100
_MAX_EXCHANGES = {
    "google_books": 54,
    "hardcover": 27,
    "inventaire": 27,
    "open_library": 49,
}
_EXPECTED_HOSTS = {
    "google_books": "www.googleapis.com",
    "hardcover": "api.hardcover.app",
    "inventaire": "inventaire.io",
    "open_library": "openlibrary.org",
}
_HTTP_FAILURES: dict[str, type[httpx.HTTPError]] = {
    value.__name__: value
    for value in (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.PoolTimeout,
        httpx.ReadError,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        httpx.WriteError,
        httpx.WriteTimeout,
    )
}
_SOURCE_HEAD = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ID = re.compile(r"^report_[0-9a-f]{64}$")
_IGNORED_REQUEST_HEADERS = {
    "connection",
    "content-length",
    "host",
    "transfer-encoding",
}
_SECRET_HEADER = re.compile(
    r"authorization|cookie|credential|password|secret|signature|token|(?:^|[-_])key(?:$|[-_])",
    re.IGNORECASE,
)
RESPONSE_HEADERS = {
    "content-encoding",
    "content-length",
    "content-type",
    "ratelimit",
    "retry-after",
    "x-ratelimit-daily-remaining",
    "x-ratelimit-daily-reset",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+")


class ReplayFailure(RuntimeError):
    """A fixed, value-free provider replay qualification failure."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> Never:
        raise ReplayFailure("arguments_invalid")


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def private_write(path: Path, payload: bytes) -> None:
    expected_parent = require_private_directory(path.parent)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if not getattr(os, "O_NOFOLLOW", 0):
        raise ReplayFailure("no_follow_unavailable")
    parent = -1
    descriptor = -1
    try:
        try:
            parent = os.open(
                path.parent,
                os.O_RDONLY
                | os.O_DIRECTORY
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as error:
            raise ReplayFailure("private_output_unavailable") from error
        actual_parent = os.fstat(parent)
        if (
            (actual_parent.st_dev, actual_parent.st_ino)
            != (expected_parent.st_dev, expected_parent.st_ino)
            or not stat.S_ISDIR(actual_parent.st_mode)
            or actual_parent.st_uid != os.geteuid()
            or actual_parent.st_mode & 0o077
        ):
            raise ReplayFailure("private_authority_changed")
        try:
            descriptor = os.open(path.name, flags, 0o600, dir_fd=parent)
        except OSError as error:
            raise ReplayFailure("private_output_unavailable") from error
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ReplayFailure("private_output_unavailable")
            offset += written
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.fsync(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)


def _private_stat(path: Path, *, directory: bool) -> os.stat_result:
    try:
        value = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ReplayFailure("private_input_unavailable") from error
    if (
        (stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
        is False
        or value.st_uid != os.geteuid()
        or (not directory and value.st_nlink != 1)
        or value.st_mode & 0o077
    ):
        raise ReplayFailure("private_authority_unsafe")
    return value


def _require_private_regular(value: os.stat_result) -> None:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_nlink != 1
        or value.st_mode & 0o077
    ):
        raise ReplayFailure("private_authority_unsafe")


def require_private_file(path: Path) -> os.stat_result:
    return _private_stat(path, directory=False)


def require_private_directory(path: Path) -> os.stat_result:
    return _private_stat(path, directory=True)


def require_safe_parent_chain(path: Path) -> None:
    """Reject replaceable path authorities before descriptor-bound reads."""
    current = path.absolute()
    while True:
        try:
            value = current.stat(follow_symlinks=False)
        except OSError as error:
            raise ReplayFailure("private_input_unavailable") from error
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or value.st_mode & 0o022
            and not value.st_mode & stat.S_ISVTX
        ):
            raise ReplayFailure("private_authority_unsafe")
        if current.parent == current:
            return
        current = current.parent


def read_private(path: Path, maximum: int) -> bytes:
    expected = require_private_file(path)
    if expected.st_size > maximum:
        raise ReplayFailure("private_document_oversized")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ReplayFailure("no_follow_unavailable")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | nofollow)
    except OSError as error:
        raise ReplayFailure("private_input_unavailable") from error
    try:
        actual = os.fstat(descriptor)
        _require_private_regular(actual)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ReplayFailure("private_authority_changed")
        payload = bytearray()
        while len(payload) <= maximum:
            block = os.read(descriptor, min(65_536, maximum + 1 - len(payload)))
            if not block:
                break
            payload.extend(block)
        if len(payload) > maximum:
            raise ReplayFailure("private_document_oversized")
        final = os.fstat(descriptor)
        _require_private_regular(final)
        if (final.st_dev, final.st_ino, final.st_size, final.st_mode) != (
            actual.st_dev,
            actual.st_ino,
            actual.st_size,
            actual.st_mode,
        ):
            raise ReplayFailure("private_authority_changed")
        return bytes(payload)
    except OSError as error:
        raise ReplayFailure("private_input_unavailable") from error
    finally:
        os.close(descriptor)


def load_private_configuration(path: Path) -> ApplicationConfiguration:
    require_safe_parent_chain(path.parent)
    try:
        return load_configuration_document(read_private(path, _MAX_CONFIG_BYTES))
    except Exception as error:
        raise ReplayFailure("configuration_invalid") from error


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ReplayFailure("private_document_invalid")
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise ReplayFailure("private_document_invalid")


def parse_canonical(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_unique,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ReplayFailure("private_document_invalid") from error
    if type(value) is not dict or canonical(value) != raw:
        raise ReplayFailure("private_document_noncanonical")
    return value


def require_environment(repository: Path, expected_head: str) -> None:
    if _SOURCE_HEAD.fullmatch(expected_head) is None:
        raise ReplayFailure("source_identity_invalid")
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository, text=True
        ).strip()
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=repository, text=True
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repository,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReplayFailure("source_repository_invalid") from error
    if Path(root).resolve() != repository.resolve() or head != expected_head or status:
        raise ReplayFailure("source_repository_invalid")


def _source_connection(
    path: Path,
) -> tuple[sqlite3.Connection, _LinuxDatabaseBinding | None, int]:
    expected = require_private_file(path)
    for suffix in ("-journal", "-shm", "-wal"):
        if Path(str(path) + suffix).exists():
            raise ReplayFailure("source_database_sidecar")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ReplayFailure("no_follow_unavailable")
    descriptor = -1
    binding: _LinuxDatabaseBinding | None = None
    connection: sqlite3.Connection | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | nofollow)
        actual = os.fstat(descriptor)
        if (
            (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
            or not stat.S_ISREG(actual.st_mode)
            or actual.st_uid != os.geteuid()
            or actual.st_nlink != 1
            or actual.st_mode & 0o077
        ):
            raise ReplayFailure("source_database_changed")
        if sys.platform.startswith("linux"):
            binding = _LinuxDatabaseBinding.create(path, descriptor)
            descriptor = -1
            binding.require_current()
        bound_descriptor = binding.descriptor if binding is not None else descriptor
        connection = _connect_read_only(path, bound_descriptor, binding)
        _validate_schema(connection)
        return connection, binding, descriptor
    except Exception as error:
        if connection is not None:
            connection.close()
        if binding is not None:
            binding.close()
        elif descriptor >= 0:
            os.close(descriptor)
        raise ReplayFailure("source_database_invalid") from error


def require_empty_output(path: Path) -> None:
    require_private_directory(path)
    try:
        if any(path.iterdir()):
            raise ReplayFailure("private_output_not_empty")
    except OSError as error:
        raise ReplayFailure("private_output_unavailable") from error


def load_reports(database: Path, report_ids: tuple[str, ...]) -> list[MetadataReport]:
    connection, binding, descriptor = _source_connection(database)
    try:
        rows = []
        for report_id in report_ids:
            row = connection.execute(
                """SELECT report_id, connection_name, library_name, item_id,
                          file_identity, content_digest, schema_version,
                          document_digest, document
                   FROM metadata_reports WHERE report_id=?""",
                (report_id,),
            ).fetchone()
            if row is None:
                raise ReplayFailure("selected_report_unavailable")
            try:
                rows.append(_validate_report_row(tuple(row)))
            except Exception as error:
                raise ReplayFailure("selected_report_invalid") from error
        if binding is not None:
            binding.require_current()
    finally:
        connection.close()
        if binding is not None:
            binding.close()
        elif descriptor >= 0:
            os.close(descriptor)
    return [MetadataReport.from_json(document) for document in rows]


def load_selection(path: Path) -> tuple[str, ...]:
    value = parse_canonical(read_private(path, _MAX_SELECTION_BYTES))
    if (
        set(value) != {"report_ids", "schema"}
        or value.get("schema") != _SELECTION_SCHEMA
    ):
        raise ReplayFailure("selection_invalid")
    report_ids = value.get("report_ids")
    if (
        type(report_ids) is not list
        or not 0 < len(report_ids) <= _MAX_COHORT
        or any(
            type(item) is not str or _REPORT_ID.fullmatch(item) is None
            for item in report_ids
        )
        or len(set(report_ids)) != len(report_ids)
    ):
        raise ReplayFailure("selection_invalid")
    return tuple(report_ids)


def plan_category(report: MetadataReport) -> str:
    plan = metadata_discovery_plan(_target_snapshot(report), report.local_claims)
    kinds = {lane.kind.value for lane in plan.lanes}
    if kinds == {"isbn", "work"}:
        return "isbn+work"
    if kinds == {"isbn"}:
        return "isbn"
    if kinds == {"work"}:
        return "work"
    return "none"


def _target_snapshot(report: MetadataReport) -> TargetSnapshot:
    cover_field = report.target.fields[-1]
    cover = (
        None
        if cover_field.value is None
        else TargetCover(
            report.file.identity,
            str(cover_field.value),
            report.target.item,
        )
    )
    return TargetSnapshot(
        "book",
        None,
        (),
        cover=cover,
        fields=report.target.fields,
    )


def sanitize_header(name: str, value: str) -> str:
    lowered = name.casefold()
    if _SECRET_HEADER.search(lowered):
        return "<credential>"
    if lowered == "user-agent":
        return EMAIL.sub("<contact>", value)
    return value


def request_document(request: httpx.Request) -> dict[str, object]:
    query = [
        [name, "<credential>" if name.casefold() == "key" else value]
        for name, value in request.url.params.multi_items()
    ]
    headers = [
        [name.casefold(), sanitize_header(name, value)]
        for name, value in request.headers.multi_items()
        if name.casefold() not in _IGNORED_REQUEST_HEADERS
    ]
    headers.sort()
    body = request.read()
    return {
        "body_base64": base64.b64encode(body).decode("ascii"),
        "headers": headers,
        "host": request.url.host,
        "method": request.method,
        "path": request.url.path,
        "port": request.url.port,
        "query": query,
        "scheme": request.url.scheme,
    }


def response_headers(headers: httpx.Headers) -> list[list[str]]:
    result = [
        [name.casefold(), value]
        for name, value in headers.multi_items()
        if name.casefold() in RESPONSE_HEADERS
    ]
    result.sort()
    return result


class RecordingStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream, record: dict[str, Any]) -> None:
        self._stream = stream
        self._record = record

    def __iter__(self):
        try:
            for chunk in self._stream:
                self._record["chunks_base64"].append(
                    base64.b64encode(chunk).decode("ascii")
                )
                yield chunk
            self._record["complete"] = True
        except httpx.HTTPError as error:
            self._record["stream_failure"] = type(error).__name__
            raise

    def close(self) -> None:
        self._stream.close()


def require_endpoint(request: httpx.Request, adapter: str) -> None:
    if (
        adapter not in _EXPECTED_HOSTS
        or request.url.scheme != "https"
        or request.url.host != _EXPECTED_HOSTS[adapter]
        or request.url.port not in {None, 443}
    ):
        raise ReplayFailure("provider_endpoint_invalid")


class RecordingTransport(httpx.BaseTransport):
    def __init__(
        self,
        exchanges: list[dict[str, Any]],
        adapter: str,
        upstream: httpx.BaseTransport | None = None,
    ) -> None:
        self._exchanges = exchanges
        self._adapter = adapter
        self._transport = (
            httpx.HTTPTransport(
                retries=0,
                verify=True,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            )
            if upstream is None
            else upstream
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        require_endpoint(request, self._adapter)
        if len(self._exchanges) >= _MAX_EXCHANGES[self._adapter]:
            raise ReplayFailure("provider_exchange_limit_exceeded")
        record: dict[str, Any] = {
            "request": request_document(request),
            "response": None,
        }
        self._exchanges.append(record)
        try:
            response = self._transport.handle_request(request)
        except httpx.HTTPError as error:
            record["transport_failure"] = type(error).__name__
            raise
        response_record: dict[str, Any] = {
            "chunks_base64": [],
            "complete": False,
            "headers": response_headers(response.headers),
            "status": response.status_code,
            "stream_failure": None,
        }
        record["response"] = response_record
        if not isinstance(response.stream, httpx.SyncByteStream):
            raise ReplayFailure("provider_stream_invalid")
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=RecordingStream(response.stream, response_record),
            extensions=response.extensions,
            request=request,
        )

    def close(self) -> None:
        self._transport.close()


class ReplayStream(httpx.SyncByteStream):
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def __iter__(self):
        for encoded in self._response["chunks_base64"]:
            yield base64.b64decode(encoded, validate=True)
        failure = self._response.get("stream_failure")
        if failure is not None:
            raise _HTTP_FAILURES[failure]("sealed replay stream failure")

    def close(self) -> None:
        return None


class ReplayTransport(httpx.BaseTransport):
    def __init__(self, exchanges: list[dict[str, Any]], adapter: str) -> None:
        self._exchanges = exchanges
        self._adapter = adapter
        self.cursor = 0
        self.mismatch = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        require_endpoint(request, self._adapter)
        if self.cursor >= len(self._exchanges):
            self.mismatch = True
            raise httpx.NetworkError("sealed replay request is unavailable")
        exchange = self._exchanges[self.cursor]
        self.cursor += 1
        if request_document(request) != exchange["request"]:
            self.mismatch = True
            raise httpx.NetworkError("sealed replay request changed")
        if "transport_failure" in exchange:
            raise _HTTP_FAILURES[exchange["transport_failure"]](
                "sealed replay transport failure"
            )
        response = exchange["response"]
        if type(response) is not dict:
            self.mismatch = True
            raise httpx.NetworkError("sealed replay response is unavailable")
        return httpx.Response(
            response["status"],
            headers=response["headers"],
            stream=ReplayStream(response),
            request=request,
        )

    def close(self) -> None:
        return None


class VirtualTime:
    def __init__(self) -> None:
        self.value = 1_000_000.0

    def clock(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += max(0.0, seconds)


def secret(provider: ProviderConnectionConfiguration) -> str | None:
    if provider.adapter not in {"google_books", "hardcover"}:
        return None
    if provider.secret_file is None:
        if provider.adapter == "google_books":
            return None
        raise ReplayFailure("provider_credential_unavailable")
    path = Path(provider.secret_file)
    require_private_file(path)
    return read_secret(str(path))


def build_provider(
    provider: ProviderConnectionConfiguration,
    configuration: ApplicationConfiguration,
    transport: httpx.BaseTransport,
    *,
    live: bool,
    capture_epoch: float,
    pacers: dict[str, ProviderRequestPacer],
    live_credential: str | None = None,
):
    timeout = configuration.limits.timeout_seconds
    maximum = configuration.limits.provider_response_bytes
    credential = (
        live_credential
        if live
        else "replay-placeholder"
        if provider.secret_file is not None
        else None
    )
    virtual = VirtualTime()
    clock = time.monotonic if live else virtual.clock
    sleep = time.sleep if live else virtual.sleep
    if provider.adapter == "google_books":
        client = google_books_client(timeout, transport=transport)
        return GoogleBooksMetadataProvider(
            provider.name,
            client,
            maximum,
            timeout,
            api_key=credential,
            pacer=pacers[provider.adapter],
            clock=clock,
            wall_clock=lambda: capture_epoch,
            sleep=sleep,
            jitter=lambda: 0.5,
        )
    if provider.adapter == "hardcover":
        if credential is None:
            raise RuntimeError("hardcover credential is unavailable")
        client = hardcover_client_token(credential, timeout, transport=transport)
        return HardcoverMetadataProvider(
            provider.name,
            client,
            maximum,
            timeout,
            pacer=pacers[provider.adapter],
            clock=clock,
            wall_clock=lambda: capture_epoch,
            sleep=sleep,
            jitter=lambda: 0.5,
        )
    if provider.adapter == "open_library":
        contact = provider.contact_email if live else "replay@example.invalid"
        if contact is None:
            raise ReplayFailure("provider_contact_unavailable")
        client = open_library_client(contact, timeout, transport=transport)
        return OpenLibraryMetadataProvider(
            provider.name,
            client,
            maximum,
            timeout,
            pacer=pacers[provider.adapter],
            clock=clock,
            wall_clock=lambda: capture_epoch,
            sleep=sleep,
            jitter=lambda: 0.5,
        )
    if provider.adapter == "inventaire":
        client = inventaire_client(timeout, transport=transport)
        return InventaireMetadataProvider(
            provider.name,
            client,
            maximum,
            timeout,
            pacer=pacers[provider.adapter],
            clock=clock,
            wall_clock=lambda: capture_epoch,
            sleep=sleep,
            jitter=lambda: 0.5,
        )
    raise ReplayFailure("provider_adapter_unsupported")


def pacers(*, live: bool) -> dict[str, ProviderRequestPacer]:
    if live:
        return {
            "google_books": GoogleBooksPacer(),
            "hardcover": HardcoverPacer(),
            "inventaire": InventairePacer(),
            "open_library": OpenLibraryPacer(),
        }
    virtual = VirtualTime()
    return {
        "google_books": GoogleBooksPacer(clock=virtual.clock, sleep=virtual.sleep),
        "hardcover": HardcoverPacer(clock=virtual.clock, sleep=virtual.sleep),
        "inventaire": InventairePacer(clock=virtual.clock, sleep=virtual.sleep),
        "open_library": OpenLibraryPacer(clock=virtual.clock, sleep=virtual.sleep),
    }


def seed_report(report: MetadataReport) -> MetadataReport:
    return MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=(),
    )


def _inflate_bounded(payload: bytes, encoding: str) -> bytes:
    modes = (
        (zlib.MAX_WBITS | 16,)
        if encoding == "gzip"
        else (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    )
    for mode in modes:
        try:
            decoder = zlib.decompressobj(mode)
            decoded = decoder.decompress(payload, 67_108_865)
            if len(decoded) > 67_108_864 or decoder.unconsumed_tail:
                raise ReplayFailure("provider_response_decoding_oversized")
            decoded += decoder.flush(67_108_865 - len(decoded))
            if len(decoded) > 67_108_864 or not decoder.eof:
                raise ReplayFailure("provider_response_decoding_invalid")
            return decoded
        except zlib.error:
            continue
    raise ReplayFailure("provider_response_decoding_invalid")


def decoded_response_body(response: dict[str, Any]) -> bytes:
    payload = b"".join(
        base64.b64decode(chunk, validate=True) for chunk in response["chunks_base64"]
    )
    encodings = [
        encoding.strip().casefold()
        for name, value in response["headers"]
        if name == "content-encoding"
        for encoding in value.split(",")
        if encoding.strip()
    ]
    for encoding in reversed(encodings):
        if encoding == "identity":
            continue
        if encoding not in {"gzip", "deflate"}:
            raise ReplayFailure("provider_response_encoding_unsupported")
        payload = _inflate_bounded(payload, encoding)
    return payload


def _contains_private_identity(bundle: dict[str, Any], values: set[bytes]) -> bool:
    if not values:
        return False
    if any(value in canonical(bundle) for value in values):
        return True
    for row in bundle["rows"]:
        for provider in row["providers"]:
            for exchange in provider["exchanges"]:
                request = exchange["request"]
                raw_values = [base64.b64decode(request["body_base64"], validate=True)]
                response = exchange.get("response")
                if type(response) is dict:
                    raw_values.extend(
                        base64.b64decode(chunk, validate=True)
                        for chunk in response["chunks_base64"]
                    )
                    raw_values.append(decoded_response_body(response))
                if any(value in raw for value in values for raw in raw_values):
                    return True
    return False


def capture(
    configuration: ApplicationConfiguration,
    selected: Sequence[MetadataReport],
    capture_head: str,
) -> dict[str, Any]:
    live_pacers = pacers(live=True)
    rows: list[dict[str, Any]] = []
    credential_values: set[bytes] = set()
    contact_values: set[bytes] = set()
    for retained in selected:
        if retained.audio is not None:
            raise ReplayFailure("selected_report_unsupported")
        seed = seed_report(retained)
        plan = metadata_discovery_plan(_target_snapshot(seed), seed.local_claims)
        _connection, _library, _mount, scoped = configuration.select(
            seed.target.connection, seed.target.library
        )
        enabled = [provider for provider in scoped if provider.enabled]
        if not enabled:
            raise ReplayFailure("provider_scope_empty")
        observations = []
        provider_rows = []
        for provider in enabled:
            credential = secret(provider)
            if credential is not None:
                credential_values.add(credential.encode("utf-8"))
            if provider.contact_email is not None:
                contact_values.add(provider.contact_email.encode("utf-8"))
            exchanges: list[dict[str, Any]] = []
            transport = RecordingTransport(exchanges, provider.adapter)
            capture_epoch = time.time()
            instance = build_provider(
                provider,
                configuration,
                transport,
                live=True,
                capture_epoch=capture_epoch,
                pacers=live_pacers,
                live_credential=credential,
            )
            observation = observe_provider(
                provider.name,
                provider.adapter,
                instance,
                plan,
            ).observation
            if any(
                type(exchange.get("response")) is dict
                and not exchange["response"]["complete"]
                and exchange["response"].get("stream_failure") is None
                for exchange in exchanges
            ):
                raise ReplayFailure("provider_response_incomplete")
            observations.append(observation)
            provider_rows.append(
                {
                    "adapter": provider.adapter,
                    "capture_epoch": capture_epoch,
                    "exchanges": exchanges,
                    "name": provider.name,
                }
            )
        live_report = MetadataReport.create(
            target=seed.target,
            file=seed.file,
            local_claims=seed.local_claims,
            local_reasons=seed.local_reasons,
            providers=tuple(sorted(observations, key=lambda value: value.provider)),
        )
        proposal = evaluate_report(live_report)
        report_payload = live_report.to_json().encode("utf-8")
        proposal_payload = proposal.to_json().encode("utf-8")
        rows.append(
            {
                "category": plan_category(seed),
                "expected_proposal_sha256": digest(proposal_payload),
                "expected_report_sha256": digest(report_payload),
                "providers": provider_rows,
                "seed": json.loads(seed.to_json()),
                "source_report_sha256": digest(retained.to_json().encode("utf-8")),
            }
        )
    bundle = {
        "capture_head": capture_head,
        "configuration_schema": configuration.schema_version,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "report_schema": REPORT_SCHEMA,
        "rows": rows,
        "schema": _BUNDLE_SCHEMA,
    }
    payload = canonical(bundle)
    if _contains_private_identity(bundle, credential_values | contact_values):
        raise ReplayFailure("private_identity_persisted")
    if len(payload) > _MAX_BUNDLE_BYTES:
        raise ReplayFailure("bundle_oversized")
    return bundle


def _closed_map(value: object, keys: set[str], reason: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ReplayFailure(reason)
    return value


def _text(value: object, maximum: int, reason: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum:
        raise ReplayFailure(reason)
    return value


def _pairs(value: object, allowed: set[str], reason: str) -> list[list[str]]:
    if type(value) is not list or len(value) > 32:
        raise ReplayFailure(reason)
    result: list[list[str]] = []
    for pair in value:
        if (
            type(pair) is not list
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            or pair[0] not in allowed
            or len(pair[1].encode("utf-8")) > 8_192
        ):
            raise ReplayFailure(reason)
        result.append(pair)
    if result != sorted(result):
        raise ReplayFailure(reason)
    return result


def _query_pairs(value: object) -> list[list[str]]:
    if type(value) is not list or len(value) > 64:
        raise ReplayFailure("bundle_request_invalid")
    result: list[list[str]] = []
    for pair in value:
        if (
            type(pair) is not list
            or len(pair) != 2
            or type(pair[0]) is not str
            or not pair[0]
            or len(pair[0].encode("utf-8")) > 128
            or type(pair[1]) is not str
            or len(pair[1].encode("utf-8")) > 8_192
        ):
            raise ReplayFailure("bundle_request_invalid")
        result.append(pair)
    return result


def _request_header_pairs(value: object) -> list[list[str]]:
    if type(value) is not list or len(value) > 64:
        raise ReplayFailure("bundle_request_invalid")
    result: list[list[str]] = []
    for pair in value:
        if (
            type(pair) is not list
            or len(pair) != 2
            or type(pair[0]) is not str
            or not pair[0]
            or pair[0] != pair[0].casefold()
            or len(pair[0].encode("ascii", "strict")) > 128
            or type(pair[1]) is not str
            or len(pair[1].encode("utf-8")) > 8_192
        ):
            raise ReplayFailure("bundle_request_invalid")
        result.append(pair)
    if result != sorted(result):
        raise ReplayFailure("bundle_request_invalid")
    return result


def _base64(value: object, maximum: int, reason: str) -> bytes:
    if type(value) is not str or len(value) > (maximum * 4 // 3) + 4:
        raise ReplayFailure(reason)
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ReplayFailure(reason) from error
    if len(decoded) > maximum:
        raise ReplayFailure(reason)
    return decoded


def _validate_request(value: object, adapter: str) -> None:
    request = _closed_map(
        value,
        {"body_base64", "headers", "host", "method", "path", "port", "query", "scheme"},
        "bundle_request_invalid",
    )
    if (
        request["scheme"] != "https"
        or request["host"] != _EXPECTED_HOSTS[adapter]
        or request["port"] not in {None, 443}
        or _text(request["method"], 16, "bundle_request_invalid") not in {"GET", "POST"}
    ):
        raise ReplayFailure("bundle_request_invalid")
    _text(request["path"], 2_048, "bundle_request_invalid")
    _base64(request["body_base64"], 65_536, "bundle_request_invalid")
    headers = _request_header_pairs(request["headers"])
    query = _query_pairs(request["query"])
    if any(
        _SECRET_HEADER.search(name) and value != "<credential>"
        for name, value in headers
    ):
        raise ReplayFailure("bundle_request_invalid")
    if any(name == "user-agent" and EMAIL.search(value) for name, value in headers):
        raise ReplayFailure("bundle_request_invalid")
    if any(
        name.casefold() == "key" and value != "<credential>" for name, value in query
    ):
        raise ReplayFailure("bundle_request_invalid")


def _validate_response(value: object) -> None:
    response = _closed_map(
        value,
        {"chunks_base64", "complete", "headers", "status", "stream_failure"},
        "bundle_response_invalid",
    )
    if (
        type(response["status"]) is not int
        or isinstance(response["status"], bool)
        or not 100 <= response["status"] <= 599
        or type(response["complete"]) is not bool
        or response["stream_failure"] is not None
        and (
            type(response["stream_failure"]) is not str
            or response["stream_failure"] not in _HTTP_FAILURES
        )
        or not response["complete"]
        and response["stream_failure"] is None
    ):
        raise ReplayFailure("bundle_response_invalid")
    _pairs(response["headers"], RESPONSE_HEADERS, "bundle_response_invalid")
    chunks = response["chunks_base64"]
    if type(chunks) is not list or len(chunks) > 16_384:
        raise ReplayFailure("bundle_response_invalid")
    total = 0
    for chunk in chunks:
        total += len(_base64(chunk, 16_777_216, "bundle_response_invalid"))
        if total > 67_108_864:
            raise ReplayFailure("bundle_response_invalid")


def validate_bundle(value: dict[str, Any]) -> None:
    _closed_map(
        value,
        {
            "capture_head",
            "configuration_schema",
            "created_at",
            "report_schema",
            "rows",
            "schema",
        },
        "bundle_invalid",
    )
    if (
        value["schema"] != _BUNDLE_SCHEMA
        or type(value["capture_head"]) is not str
        or _SOURCE_HEAD.fullmatch(value["capture_head"]) is None
        or value["report_schema"] != REPORT_SCHEMA
    ):
        raise ReplayFailure("bundle_invalid")
    _text(value["configuration_schema"], 128, "bundle_invalid")
    _text(value["created_at"], 128, "bundle_invalid")
    rows = value["rows"]
    if type(rows) is not list or not 0 < len(rows) <= _MAX_COHORT:
        raise ReplayFailure("bundle_invalid")
    for row in rows:
        row = _closed_map(
            row,
            {
                "category",
                "expected_proposal_sha256",
                "expected_report_sha256",
                "providers",
                "seed",
                "source_report_sha256",
            },
            "bundle_row_invalid",
        )
        if row["category"] not in {"isbn", "isbn+work", "work"} or any(
            type(row[key]) is not str or _SHA256.fullmatch(row[key]) is None
            for key in (
                "expected_proposal_sha256",
                "expected_report_sha256",
                "source_report_sha256",
            )
        ):
            raise ReplayFailure("bundle_row_invalid")
        seed = MetadataReport.from_json(canonical(row["seed"]).decode("utf-8"))
        if (
            seed.providers
            or seed.cover_sources
            or plan_category(seed) != row["category"]
        ):
            raise ReplayFailure("bundle_row_invalid")
        providers = row["providers"]
        if type(providers) is not list or not 0 < len(providers) <= 8:
            raise ReplayFailure("bundle_provider_invalid")
        identities: set[str] = set()
        for provider in providers:
            provider = _closed_map(
                provider,
                {"adapter", "capture_epoch", "exchanges", "name"},
                "bundle_provider_invalid",
            )
            adapter = provider["adapter"]
            name = _text(provider["name"], 128, "bundle_provider_invalid")
            if (
                adapter not in _EXPECTED_HOSTS
                or name in identities
                or type(provider["capture_epoch"]) not in {int, float}
                or isinstance(provider["capture_epoch"], bool)
                or not math.isfinite(provider["capture_epoch"])
                or provider["capture_epoch"] <= 0
            ):
                raise ReplayFailure("bundle_provider_invalid")
            identities.add(name)
            exchanges = provider["exchanges"]
            if type(exchanges) is not list or len(exchanges) > _MAX_EXCHANGES[adapter]:
                raise ReplayFailure("bundle_exchange_invalid")
            for exchange in exchanges:
                if type(exchange) is not dict or set(exchange) not in (
                    {"request", "response"},
                    {"request", "response", "transport_failure"},
                ):
                    raise ReplayFailure("bundle_exchange_invalid")
                _validate_request(exchange.get("request"), adapter)
                if "transport_failure" in exchange:
                    if (
                        type(exchange["transport_failure"]) is not str
                        or exchange["transport_failure"] not in _HTTP_FAILURES
                    ):
                        raise ReplayFailure("bundle_exchange_invalid")
                    if exchange["response"] is not None:
                        raise ReplayFailure("bundle_exchange_invalid")
                else:
                    _validate_response(exchange["response"])


def read_bundle(path: Path, digest_path: Path) -> tuple[dict[str, Any], str]:
    raw = read_private(path, _MAX_BUNDLE_BYTES)
    expected = read_private(digest_path, 128).decode("ascii", "strict").strip()
    actual = digest(raw)
    if _SHA256.fullmatch(expected) is None or expected != actual:
        raise ReplayFailure("bundle_digest_invalid")
    value = parse_canonical(raw)
    validate_bundle(value)
    return value, actual


def replay(
    bundle: dict[str, Any],
    configuration: ApplicationConfiguration,
) -> tuple[list[MetadataReport], list[dict[str, object]], list[dict[str, object]]]:
    current_pacers = pacers(live=False)
    reports: list[MetadataReport] = []
    invalidated: list[dict[str, object]] = []
    provenance: list[dict[str, object]] = []
    for row_index, row in enumerate(bundle["rows"]):
        seed_payload = canonical(row["seed"]).decode("utf-8")
        seed = MetadataReport.from_json(seed_payload)
        plan = metadata_discovery_plan(_target_snapshot(seed), seed.local_claims)
        _connection, _library, _mount, scoped = configuration.select(
            seed.target.connection, seed.target.library
        )
        providers_by_name = {
            provider.name: provider for provider in scoped if provider.enabled
        }
        captured_provider_names = {
            provider_row["name"] for provider_row in row["providers"]
        }
        for provider_name in sorted(
            set(providers_by_name).difference(captured_provider_names)
        ):
            invalidated.append(
                {
                    "provider": provider_name,
                    "reason": "provider_evidence_missing",
                    "row": row_index,
                }
            )
        observations = []
        for provider_row in row["providers"]:
            provider = providers_by_name.get(provider_row["name"])
            if provider is None or provider.adapter != provider_row["adapter"]:
                invalidated.append(
                    {
                        "provider": provider_row["name"],
                        "reason": "provider_contract_changed",
                        "row": row_index,
                    }
                )
                continue
            exchanges = provider_row["exchanges"]
            transport = ReplayTransport(exchanges, provider.adapter)
            instance = build_provider(
                provider,
                configuration,
                transport,
                live=False,
                capture_epoch=provider_row["capture_epoch"],
                pacers=current_pacers,
            )
            observation = observe_provider(
                provider.name,
                provider.adapter,
                instance,
                plan,
            ).observation
            if transport.mismatch or transport.cursor != len(exchanges):
                invalidated.append(
                    {
                        "provider": provider.name,
                        "reason": "request_contract_changed",
                        "row": row_index,
                    }
                )
                continue
            observations.append(observation)
        report = MetadataReport.create(
            target=seed.target,
            file=seed.file,
            local_claims=seed.local_claims,
            local_reasons=seed.local_reasons,
            providers=tuple(sorted(observations, key=lambda value: value.provider)),
        )
        proposal = evaluate_report(report)
        report_digest = digest(report.to_json().encode("utf-8"))
        proposal_digest = digest(proposal.to_json().encode("utf-8"))
        provenance.append(
            {
                "proposal_sha256": proposal_digest,
                "report_sha256": report_digest,
                "row": row_index,
                "same_as_capture": (
                    report_digest == row["expected_report_sha256"]
                    and proposal_digest == row["expected_proposal_sha256"]
                ),
            }
        )
        reports.append(report)
    return reports, invalidated, provenance


def publish_database(
    database: Path, reports: Sequence[MetadataReport]
) -> dict[str, int]:
    if database.exists():
        raise ReplayFailure("replay_output_exists")
    repository = open_repository(database)
    admitted = 0
    try:
        for report in reports:
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            result = json.loads(repository.admit_review_case(proposal))
            if result["status"] == "admitted":
                admitted += 1
    finally:
        repository.close()
    os.chmod(database, 0o600)
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    try:
        report_count = connection.execute(
            "SELECT count(*) FROM metadata_reports"
        ).fetchone()[0]
        proposal_count = connection.execute(
            "SELECT count(*) FROM proposal_sets"
        ).fetchone()[0]
        case_count = connection.execute("SELECT count(*) FROM review_cases").fetchone()[
            0
        ]
    finally:
        connection.close()
    return {
        "admitted": admitted,
        "cases": case_count,
        "proposals": proposal_count,
        "reports": report_count,
    }


def private_digest(path: Path) -> str:
    expected = require_private_file(path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ReplayFailure("no_follow_unavailable")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | nofollow)
    value = hashlib.sha256()
    try:
        actual = os.fstat(descriptor)
        _require_private_regular(actual)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ReplayFailure("private_authority_changed")
        while block := os.read(descriptor, 1_048_576):
            value.update(block)
        final = os.fstat(descriptor)
        _require_private_regular(final)
        if (final.st_dev, final.st_ino, final.st_size, final.st_mode) != (
            actual.st_dev,
            actual.st_ino,
            actual.st_size,
            actual.st_mode,
        ):
            raise ReplayFailure("private_authority_changed")
    finally:
        os.close(descriptor)
    return value.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Capture or replay one sealed private provider-evidence cohort."
    )
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("capture", "replay"):
        command = commands.add_parser(action)
        command.add_argument("--repository", required=True, type=Path)
        command.add_argument("--expected-head", required=True)
        command.add_argument("--config", required=True, type=Path)
        command.add_argument("--output-directory", required=True, type=Path)
        if action == "capture":
            command.add_argument("--database", required=True, type=Path)
            command.add_argument("--selection", required=True, type=Path)
        else:
            command.add_argument("--bundle", required=True, type=Path)
            command.add_argument("--bundle-digest", required=True, type=Path)
    return parser


def _capture(arguments: argparse.Namespace) -> dict[str, object]:
    require_environment(arguments.repository, arguments.expected_head)
    require_empty_output(arguments.output_directory)
    selection = load_selection(arguments.selection)
    configuration = load_private_configuration(arguments.config)
    selected = load_reports(arguments.database, selection)
    bundle = capture(configuration, selected, arguments.expected_head)
    validate_bundle(bundle)
    payload = canonical(bundle)
    private_write(arguments.output_directory / "bundle.json", payload)
    private_write(
        arguments.output_directory / "bundle.json.sha256",
        (digest(payload) + "\n").encode("ascii"),
    )
    return {
        "action": "capture",
        "cohort": len(selected),
        "exchanges": sum(
            len(provider["exchanges"])
            for row in bundle["rows"]
            for provider in row["providers"]
        ),
        "providers": len(
            {
                provider["name"]
                for row in bundle["rows"]
                for provider in row["providers"]
            }
        ),
        "status": "captured",
    }


def _replay(arguments: argparse.Namespace) -> dict[str, object]:
    require_environment(arguments.repository, arguments.expected_head)
    require_empty_output(arguments.output_directory)
    bundle, bundle_digest = read_bundle(arguments.bundle, arguments.bundle_digest)
    configuration = load_private_configuration(arguments.config)
    reports, invalidated, provenance = replay(bundle, configuration)
    repeated, repeated_invalidated, repeated_provenance = replay(bundle, configuration)
    if (
        [report.to_json() for report in reports]
        != [report.to_json() for report in repeated]
        or invalidated != repeated_invalidated
        or provenance != repeated_provenance
    ):
        raise ReplayFailure("replay_nondeterministic")
    if bundle["capture_head"] == arguments.expected_head and (
        invalidated or not all(row["same_as_capture"] for row in provenance)
    ):
        raise ReplayFailure("same_head_replay_changed")
    database_path = arguments.output_directory / "current.sqlite3"
    database = publish_database(database_path, reports)
    result = {
        "bundle_sha256": bundle_digest,
        "capture_head": bundle["capture_head"],
        "configuration_schema": configuration.schema_version,
        "database": database,
        "database_sha256": private_digest(database_path),
        "deterministic_replays": 2,
        "invalidations": invalidated,
        "media_writes": 0,
        "replay_head": arguments.expected_head,
        "replay_network_requests": 0,
        "report_schema": REPORT_SCHEMA,
        "reviewer_requests": 0,
        "rows": provenance,
        "schema": _RESULT_SCHEMA,
        "target_requests": 0,
    }
    payload = canonical(result)
    private_write(arguments.output_directory / "result.json", payload)
    private_write(
        arguments.output_directory / "result.json.sha256",
        (digest(payload) + "\n").encode("ascii"),
    )
    return {
        "action": "replay",
        "cohort": len(reports),
        "fresh_database_reports": database["reports"],
        "invalidations": len(invalidated),
        "replay_network_requests": 0,
        "same_as_capture": sum(row["same_as_capture"] is True for row in provenance),
        "status": "replayed",
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        result = (
            _capture(arguments) if arguments.action == "capture" else _replay(arguments)
        )
    except ReplayFailure:
        sys.stderr.write("Provider-evidence qualification failed safely.\n")
        return 1
    except Exception:
        sys.stderr.write("Provider-evidence qualification failed safely.\n")
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
