"""Purpose-specific current book catalogue refresh and inspection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion


class CatalogueError(RuntimeError):
    """Value-free catalogue operation failure."""


class CatalogueBusyError(CatalogueError):
    pass


class CatalogueStaleError(CatalogueError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    connection: str
    library: str
    library_id: str
    item_id: str
    updated_at: int
    title: str
    authors: tuple[str, ...]
    has_ebook: bool
    has_audio: bool
    classification: str
    file_identity: str | None = None
    service_path: str | None = None
    format: str | None = None
    mount: str | None = None

    @property
    def eligible(self) -> bool:
        return self.classification == "singular-supported" and self.mount is not None


class CatalogueReader(Protocol):
    def read_library(self, connection: str, library: LibraryConfiguration, remaining: int) -> tuple[CatalogueEntry, ...]: ...


class CatalogueRepository(Protocol):
    def acquire_refresh(self) -> None: ...
    def publish(self, fingerprint: str, entries: tuple[CatalogueEntry, ...]) -> int: ...
    def abort_refresh(self) -> None: ...
    def inspect(self, fingerprint: str) -> tuple[CatalogueEntry, ...]: ...
    def snapshot(self, fingerprint: str): ...
    def current_entry(self, generation: int, fingerprint: str, key: tuple[str, str, str, str]) -> CatalogueEntry | None: ...
    def close(self) -> None: ...


def catalogue_fingerprint(configuration: ApplicationConfiguration) -> Fingerprint:
    """Hash the deliberately small, secret-free catalogue configuration projection."""
    referenced_connections = {item.connection for item in configuration.libraries}
    referenced_mounts = {item.mount for item in configuration.libraries if item.mount is not None}
    connections = {
        value.name: {"name": value.name, "adapter": value.adapter, "endpoint": value.endpoint.rstrip("/")}
        for value in configuration.connections if value.name in referenced_connections
    }
    mounts = {
        value.name: {"name": value.name, "service_path": value.service_path, "container_path": value.container_path, "read_only": value.read_only}
        for value in configuration.mounts if value.name in referenced_mounts
    }
    value = {
        "schema": "config-v5",
        "connections": [connections[name] for name in sorted(connections)],
        "libraries": [
            {"name": item.name, "connection": item.connection, "library_id": item.library_id, "mount": item.mount}
            for item in configuration.libraries
        ],
        "mounts": [mounts[name] for name in sorted(mounts)],
        "limits": {"target_response_bytes": configuration.limits.target_response_bytes, "timeout_seconds": configuration.limits.timeout_seconds},
    }
    return Fingerprint.from_canonical_value(value, schema=SchemaVersion("catalogue-config-v1"))


class RefreshCurrentCatalogue:
    """One deep refresh interface hiding bounded enumeration and publication."""

    def __init__(self, reader: CatalogueReader, repository: CatalogueRepository) -> None:
        self._reader = reader
        self._repository = repository

    def refresh(self, configuration: ApplicationConfiguration) -> int:
        fingerprint = catalogue_fingerprint(configuration).digest
        entries: list[CatalogueEntry] = []
        self._repository.acquire_refresh()
        try:
            for library in configuration.libraries:
                entries.extend(self._reader.read_library(library.connection, library, 10_000 - len(entries)))
            return self._repository.publish(fingerprint, tuple(entries))
        except Exception:
            self._repository.abort_refresh()
            raise

    def refresh_library(
        self,
        configuration: ApplicationConfiguration,
        connection: str,
        library_name: str,
    ) -> int:
        fingerprint = catalogue_fingerprint(configuration).digest
        matches = tuple(
            library
            for library in configuration.libraries
            if library.connection == connection and library.name == library_name
        )
        if len(matches) != 1:
            raise CatalogueError("catalogue library is unavailable")
        library = matches[0]
        self._repository.acquire_refresh()
        try:
            try:
                existing = self._repository.inspect(fingerprint)
            except CatalogueStaleError:
                existing = ()
            retained = tuple(
                entry
                for entry in existing
                if (entry.connection, entry.library) != (connection, library_name)
            )
            maximum = 10_000 - len(retained)
            discovered = self._reader.read_library(
                library.connection, library, maximum
            )
            return self._repository.publish(
                fingerprint, retained + tuple(discovered)
            )
        except Exception:
            self._repository.abort_refresh()
            raise

    def inspect(self, configuration: ApplicationConfiguration, *, exact: tuple[str, str, str, str] | None = None, facet: str | None = None, limit: int = 500) -> tuple[CatalogueEntry, ...]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise CatalogueError("catalogue limit exceeded")
        entries = self._repository.inspect(catalogue_fingerprint(configuration).digest)
        if exact is not None:
            entries = tuple(item for item in entries if (item.connection, item.library, item.library_id, item.item_id) == exact)
            if len(entries) != 1:
                raise CatalogueError("catalogue item is unavailable")
        if facet == "eligible":
            entries = tuple(item for item in entries if item.eligible)
        elif facet == "ineligible":
            entries = tuple(item for item in entries if not item.eligible)
        elif facet == "ebook":
            entries = tuple(item for item in entries if item.has_ebook)
        elif facet == "audio":
            entries = tuple(item for item in entries if item.has_audio)
        return tuple(sorted(entries, key=lambda item: (item.connection, item.library, item.library_id, item.item_id)))[:limit]


__all__ = ["CatalogueBusyError", "CatalogueEntry", "CatalogueError", "CatalogueReader", "CatalogueRepository", "CatalogueStaleError", "RefreshCurrentCatalogue", "catalogue_fingerprint"]
