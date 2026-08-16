"""Immutable foreground catalogue evidence-batch contracts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Protocol

from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import ApplicationConfiguration
from mechanical_librarian.domain.metadata_report import canonical_json


class BatchError(RuntimeError):
    """Value-free batch preparation or execution failure."""


MAX_MANIFEST_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CatalogueSnapshot:
    generation: int
    fingerprint: str
    entries: tuple[CatalogueEntry, ...]


@dataclass(frozen=True, slots=True)
class BatchSelection:
    connection: str
    library: str
    library_id: str
    item: str
    file_identity: str | None
    classification: str
    eligible: bool


class CatalogueSnapshotReader(Protocol):
    def snapshot(self, fingerprint: str) -> CatalogueSnapshot: ...


def batch_fingerprint(
    configuration: ApplicationConfiguration, selections: tuple[BatchSelection, ...] = ()
) -> str:
    """Hash only report authority that a frozen batch is permitted to use."""
    selected_libraries = {(item.connection, item.library) for item in selections}
    libraries = tuple(
        item
        for item in configuration.libraries
        if not selected_libraries or (item.connection, item.name) in selected_libraries
    )
    connections = {item.name: item for item in configuration.connections}
    mounts = {item.name: item for item in configuration.mounts}
    provider_names = tuple(
        sorted({name for library in libraries for name in library.provider_connections})
    )
    value = {
        "catalogue": catalogue_fingerprint(configuration),
        "connections": [
            {
                "name": connections[item.connection].name,
                "adapter": connections[item.connection].adapter,
                "endpoint": connections[item.connection].endpoint.rstrip("/"),
            }
            for item in libraries
        ],
        "libraries": [
            {
                "name": item.name,
                "connection": item.connection,
                "library_id": item.library_id,
                "mount": item.mount,
                "provider_connections": list(item.provider_connections),
            }
            for item in libraries
        ],
        "mounts": [
            {"name": mounts[item.mount].name, "read_only": mounts[item.mount].read_only}
            for item in libraries
            if item.mount is not None
        ],
        "providers": [
            {
                "name": item.name,
                "adapter": item.adapter,
                "enabled": item.enabled,
                "cover_enabled": item.cover_enabled,
            }
            for item in configuration.providers
            if item.name in provider_names
        ],
        "limits": {
            "target_response_bytes": configuration.limits.target_response_bytes,
            "provider_response_bytes": configuration.limits.provider_response_bytes,
            "source_archive_bytes": configuration.limits.source_archive_bytes,
            "timeout_seconds": configuration.limits.timeout_seconds,
        },
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def catalogue_fingerprint(configuration: ApplicationConfiguration) -> str:
    from mechanical_librarian.application.catalogue import (
        catalogue_fingerprint as current,
    )

    return current(configuration).digest


def select(
    snapshot: CatalogueSnapshot,
    requested: tuple[tuple[str, str, str, str], ...],
    all_eligible: bool,
) -> tuple[BatchSelection, ...]:
    if all_eligible == bool(requested) or len(requested) > 10_000:
        raise BatchError("batch selection is invalid")
    values = {
        (entry.connection, entry.library, entry.library_id, entry.item_id): entry
        for entry in snapshot.entries
    }
    keys = (
        tuple(sorted(key for key, entry in values.items() if entry.eligible))
        if all_eligible
        else requested
    )
    if len(keys) > 10_000 or len(set(keys)) != len(keys):
        raise BatchError("batch selection is invalid")
    try:
        entries = tuple(values[key] for key in keys)
    except KeyError:
        raise BatchError("batch selection is unavailable") from None
    return tuple(
        BatchSelection(
            entry.connection,
            entry.library,
            entry.library_id,
            entry.item_id,
            entry.file_identity,
            entry.classification,
            entry.eligible,
        )
        for entry in entries
    )


def manifest(
    snapshot: CatalogueSnapshot,
    config_fingerprint: str,
    selections: tuple[BatchSelection, ...],
    providers: tuple[str, ...],
    provider_adapters: tuple[str, ...],
) -> bytes:
    identities = tuple(
        (value.connection, value.library, value.library_id, value.item)
        for value in selections
    )
    if (
        type(snapshot.generation) is not int
        or snapshot.generation < 1
        or not _digest(snapshot.fingerprint)
        or not _digest(config_fingerprint)
        or len(selections) > 10_000
        or len(set(identities)) != len(identities)
        or tuple(sorted(set(providers))) != providers
        or any(not _name(value) for value in providers)
        or len(provider_adapters) != len(providers)
        or any(
            value not in {"google_books", "hardcover", "open_library"}
            for value in provider_adapters
        )
        or any(not _entry_valid(asdict(value)) for value in selections)
    ):
        raise BatchError("batch manifest is invalid")
    body = {
        "schema": "batch-manifest-v5",
        "catalogue_generation": snapshot.generation,
        "catalogue_fingerprint": snapshot.fingerprint,
        "batch_config_fingerprint": config_fingerprint,
        "entries": [asdict(value) for value in selections],
        "providers": list(providers),
        "provider_adapters": list(provider_adapters),
        "call_maxima": _maxima(len(selections), provider_adapters),
    }
    canonical = canonical_json(body).encode("utf-8") + b"\n"
    value = {**body, "id": hashlib.sha256(canonical).hexdigest()}
    raw = canonical_json(value).encode("utf-8") + b"\n"
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BatchError("batch manifest is invalid")
    return raw


def parse_manifest(raw: bytes) -> dict[str, object]:
    if not 1 <= len(raw) <= MAX_MANIFEST_BYTES:
        raise BatchError("batch manifest is invalid")
    try:
        _depth(raw)
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_unique,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise BatchError("batch manifest is invalid") from None
    if (
        type(value) is not dict
        or set(value)
        != {
            "schema",
            "id",
            "catalogue_generation",
            "catalogue_fingerprint",
            "batch_config_fingerprint",
            "entries",
            "providers",
            "provider_adapters",
            "call_maxima",
        }
        or value.get("schema") != "batch-manifest-v5"
        or not _digest(value.get("id"))
    ):
        raise BatchError("batch manifest is invalid")
    identity = value["id"]
    body = {key: item for key, item in value.items() if key != "id"}
    if (
        raw != canonical_json(value).encode("utf-8") + b"\n"
        or identity
        != hashlib.sha256(canonical_json(body).encode("utf-8") + b"\n").hexdigest()
    ):
        raise BatchError("batch manifest is invalid")
    if (
        type(body["catalogue_generation"]) is not int
        or body["catalogue_generation"] < 1
        or not _digest(body["catalogue_fingerprint"])
        or not _digest(body["batch_config_fingerprint"])
        or type(body["entries"]) is not list
        or len(body["entries"]) > 10_000
        or type(body["providers"]) is not list
        or any(not _name(item) for item in body["providers"])
        or tuple(body["providers"]) != tuple(sorted(set(body["providers"])))
        or type(body["provider_adapters"]) is not list
        or len(body["provider_adapters"]) != len(body["providers"])
        or any(
            item not in {"google_books", "hardcover", "open_library"}
            for item in body["provider_adapters"]
        )
        or body["call_maxima"]
        != _maxima(len(body["entries"]), tuple(body["provider_adapters"]))
    ):
        raise BatchError("batch manifest is invalid")
    for entry in body["entries"]:
        if (
            type(entry) is not dict
            or set(entry)
            != {
                "connection",
                "library",
                "library_id",
                "item",
                "file_identity",
                "classification",
                "eligible",
            }
            or not _entry_valid(entry)
        ):
            raise BatchError("batch manifest is invalid")
    identities = tuple(
        (entry["connection"], entry["library"], entry["library_id"], entry["item"])
        for entry in body["entries"]
    )
    if len(set(identities)) != len(identities):
        raise BatchError("batch manifest is invalid")
    return value


def _digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _maxima(entries: int, adapters: tuple[str, ...]) -> dict[str, int]:
    hardcover = adapters.count("hardcover")
    google_books = adapters.count("google_books")
    open_library = adapters.count("open_library")
    return {
        "target_item_observations": entries * 6,
        "target_raw_cover_requests": entries * 4,
        "provider_metadata_requests": entries
        * (google_books * 54 + hardcover * 27 + open_library * 49),
        "remote_cover_requests": entries * (hardcover * 5 * 4 + open_library * 4),
    }


def _depth(raw: bytes) -> None:
    level = 0
    quoted = False
    escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            level += 1
            if level > 100:
                raise ValueError
        elif byte in (93, 125):
            level -= 1
            if level < 0:
                raise ValueError
    if quoted or level != 0:
        raise ValueError


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _reject_constant(_: str) -> float:
    raise ValueError


def _entry_valid(entry: dict[str, object]) -> bool:
    if (
        not _name(entry["connection"])
        or not _name(entry["library"])
        or not _opaque(entry["library_id"], 128)
        or not _opaque(entry["item"], 512)
    ):
        return False
    identity = entry["file_identity"]
    classification = entry["classification"]
    eligible = entry["eligible"]
    if classification == "singular-supported":
        return type(eligible) is bool and _digest(identity)
    return (
        classification in {"none", "needs-attention"}
        and eligible is False
        and identity is None
    )


def _name(value: object) -> bool:
    return (
        type(value) is str and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is not None
    )


def _opaque(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and value.strip() == value
        and not any(
            character in "\x00/\\"
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in value
        )
    )


__all__ = [
    "BatchError",
    "BatchSelection",
    "CatalogueSnapshot",
    "CatalogueSnapshotReader",
    "MAX_MANIFEST_BYTES",
    "batch_fingerprint",
    "catalogue_fingerprint",
    "manifest",
    "parse_manifest",
    "select",
]
