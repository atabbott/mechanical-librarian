"""Serialized owner-settings application boundary."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from mechanical_librarian.application.configuration import ApplicationConfiguration
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    OwnerSettingsError,
    SecretPresence,
    SecretWrite,
    SettingsView,
    file_field_metadata,
    provider_capabilities,
    secret_presence,
)


class OwnerSettingsRepository(Protocol):
    def read(self) -> SettingsView: ...

    def write(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
        live_update: AbstractContextManager[None] | None = None,
    ) -> SettingsView: ...


class OwnerSettingsOperations:
    def __init__(self, repository: OwnerSettingsRepository) -> None:
        self._repository = repository

    def read(self) -> SettingsView:
        return self._repository.read()

    def write(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
        live_update: AbstractContextManager[None] | None = None,
    ) -> SettingsView:
        return self._repository.write(document, expected_revision, secrets, live_update)


class FileOwnerSettingsRepository:
    """File authority is observable but never writable through this boundary."""

    def __init__(
        self, document: OwnerSettingsDocument, secrets: tuple[SecretPresence, ...] = ()
    ) -> None:
        self._document = document
        self._secrets = secrets

    def read(self) -> SettingsView:
        return SettingsView(
            document=self._document,
            fields=file_field_metadata(self._document),
            secrets=self._secrets,
            provider_capabilities=provider_capabilities(self._document),
            authority="file",
            managed_path="/data/config.json",
        )

    def write(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
        live_update: AbstractContextManager[None] | None = None,
    ) -> SettingsView:
        raise OwnerSettingsError("settings locked")


def file_settings_projection(
    configuration: ApplicationConfiguration,
    credentials: dict[str, str] | None = None,
) -> SettingsView:
    if configuration.review is None:
        raise OwnerSettingsError("owner settings are unavailable")
    review = configuration.review
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v2",
            "revision": 1,
            "connections": [
                {"name": value.name, "endpoint": value.endpoint}
                for value in configuration.connections
            ],
            "providers": [
                {
                    key: item
                    for key, item in {
                        "name": value.name,
                        "adapter": value.adapter,
                        "enabled": value.enabled,
                        "cover_enabled": value.cover_enabled,
                        "contact_email": value.contact_email,
                    }.items()
                    if item is not None
                }
                for value in configuration.providers
            ],
            "libraries": [
                {
                    "name": value.name,
                    "connection": value.connection,
                    "library_id": value.library_id,
                    "media_kind": value.media_kind,
                    "provider_connections": list(value.provider_connections),
                    "mount": value.mount,
                    "policy_override": value.policy_override,
                }
                for value in configuration.libraries
            ],
            "limits": {
                "target_response_bytes": configuration.limits.target_response_bytes,
                "provider_response_bytes": configuration.limits.provider_response_bytes,
                "source_archive_bytes": configuration.limits.source_archive_bytes,
                "timeout_seconds": configuration.limits.timeout_seconds,
            },
            "provider_recovery_generation": configuration.provider_recovery_generation,
            "server": {
                "session_lifetime_seconds": review.session_lifetime_seconds,
                "library_polling_interval_seconds": review.library_polling_interval_seconds,
                "graceful_shutdown_seconds": review.graceful_shutdown_seconds,
                "diagnostic_timings_enabled": review.diagnostic_timings_enabled,
            },
            "cover_preferences": {
                "avoid_promotional_text": review.cover_avoid_promotional_text,
                "avoid_tie_in_branding": review.cover_avoid_tie_in_branding,
                "prefer_minimal_composition": review.cover_prefer_minimal_composition,
            },
        }
    )
    return SettingsView(
        document=document,
        fields=file_field_metadata(document),
        secrets=tuple(
            secret_presence(
                name,
                None
                if credentials is None
                else credentials.get(
                    name.replace("connection.", "target:")
                    .replace("provider.", "provider:")
                    .removesuffix(".key")
                ),
            )
            for name in sorted(document.secret_fields())
        ),
        provider_capabilities=provider_capabilities(document),
        authority="file",
        managed_path="/data/config.json",
    )


__all__ = [
    "FileOwnerSettingsRepository",
    "OwnerSettingsOperations",
    "OwnerSettingsRepository",
    "file_settings_projection",
]
