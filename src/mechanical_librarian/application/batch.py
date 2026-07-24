"""Immutable foreground catalogue evidence-batch contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol

from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import ApplicationConfiguration
from mechanical_librarian.domain.metadata_report import canonical_json


class BatchError(RuntimeError):
    """Value-free batch preparation or execution failure."""


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


def batch_fingerprint(configuration: ApplicationConfiguration) -> str:
    """Hash only report authority that a frozen batch is permitted to use."""
    value = {
        "catalogue": catalogue_fingerprint(configuration),
        "providers": [
            {"name": item.name, "adapter": item.adapter, "enabled": item.enabled, "cover_enabled": item.cover_enabled}
            for item in configuration.providers
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
    from mechanical_librarian.application.catalogue import catalogue_fingerprint as current
    return current(configuration).digest


def select(snapshot: CatalogueSnapshot, requested: tuple[tuple[str, str, str, str], ...], all_eligible: bool) -> tuple[BatchSelection, ...]:
    if all_eligible == bool(requested) or len(requested) > 10_000:
        raise BatchError("batch selection is invalid")
    values = {(entry.connection, entry.library, entry.library_id, entry.item_id): entry for entry in snapshot.entries}
    keys = tuple(key for key, entry in values.items() if entry.eligible) if all_eligible else requested
    if len(keys) > 10_000 or len(set(keys)) != len(keys):
        raise BatchError("batch selection is invalid")
    try:
        entries = tuple(values[key] for key in keys)
    except KeyError:
        raise BatchError("batch selection is unavailable") from None
    return tuple(BatchSelection(entry.connection, entry.library, entry.library_id, entry.item_id, entry.file_identity, entry.classification, entry.eligible) for entry in entries)


def manifest(snapshot: CatalogueSnapshot, config_fingerprint: str, selections: tuple[BatchSelection, ...], providers: tuple[str, ...]) -> bytes:
    if type(snapshot.generation) is not int or snapshot.generation < 1 or not _digest(snapshot.fingerprint) or not _digest(config_fingerprint) or len(selections) > 10_000 or tuple(sorted(set(providers))) != providers:
        raise BatchError("batch manifest is invalid")
    body = {
        "schema": "batch-manifest-v1",
        "catalogue_generation": snapshot.generation,
        "catalogue_fingerprint": snapshot.fingerprint,
        "batch_config_fingerprint": config_fingerprint,
        "entries": [asdict(value) for value in selections],
        "providers": list(providers),
        "call_maxima": {"target_observations": len(selections), "provider_metadata": len(selections) * len(providers)},
    }
    canonical = canonical_json(body).encode("utf-8") + b"\n"
    value = {**body, "id": hashlib.sha256(canonical).hexdigest()}
    return canonical_json(value).encode("utf-8") + b"\n"


def parse_manifest(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, ValueError, RecursionError):
        raise BatchError("batch manifest is invalid") from None
    if type(value) is not dict or set(value) != {"schema", "id", "catalogue_generation", "catalogue_fingerprint", "batch_config_fingerprint", "entries", "providers", "call_maxima"} or value.get("schema") != "batch-manifest-v1" or not _digest(value.get("id")):
        raise BatchError("batch manifest is invalid")
    identity = value["id"]
    body = {key: item for key, item in value.items() if key != "id"}
    if raw != canonical_json(value).encode("utf-8") + b"\n" or identity != hashlib.sha256(canonical_json(body).encode("utf-8") + b"\n").hexdigest():
        raise BatchError("batch manifest is invalid")
    if type(body["catalogue_generation"]) is not int or body["catalogue_generation"] < 1 or not _digest(body["catalogue_fingerprint"]) or not _digest(body["batch_config_fingerprint"]) or type(body["entries"]) is not list or len(body["entries"]) > 10_000 or type(body["providers"]) is not list or tuple(body["providers"]) != tuple(sorted(set(body["providers"]))) or type(body["call_maxima"]) is not dict:
        raise BatchError("batch manifest is invalid")
    return value


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = ["BatchError", "BatchSelection", "CatalogueSnapshot", "CatalogueSnapshotReader", "batch_fingerprint", "catalogue_fingerprint", "manifest", "parse_manifest", "select"]
