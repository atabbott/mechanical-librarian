"""Atomic SQLite owner-settings document storage."""

from __future__ import annotations

import sqlite3

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    OwnerSettingsError,
    SecretWrite,
    SettingsView,
    secret_presence,
    store_field_metadata,
)
from mechanical_librarian.domain.validation import credential


class OwnerSettingsStore:
    """The only persistence boundary that ever receives raw settings secrets."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def read(self) -> SettingsView:
        try:
            document = self._document()
            values = self._secret_values()
            return SettingsView(
                document=document,
                fields=store_field_metadata(
                    document, frozenset(document.pending_restart)
                ),
                secrets=tuple(
                    secret_presence(name, values.get(name))
                    for name in sorted(document.secret_fields())
                ),
                pending_restart=tuple(document.pending_restart),
                authority="store",
            )
        except OwnerSettingsError:
            raise
        except sqlite3.Error as error:
            raise OwnerSettingsError("owner settings are unavailable") from error

    def activate_runtime(self) -> tuple[SettingsView, dict[str, str], str]:
        """Snapshot complete store authority without acknowledging its application."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            document = self._document()
            values = self._secret_values()
            verifier = values.get("owner.password.verifier")
            configured = frozenset(values) - {"owner.password.verifier"}
            if (
                verifier is None
                or not verifier.startswith("$argon2id$")
                or not document.required_secret_fields().issubset(values)
                or not configured.issubset(document.secret_fields())
            ):
                raise OwnerSettingsError("owner settings are unavailable")
            self._connection.commit()
        except OwnerSettingsError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error
        public_values = {
            name: value
            for name, value in values.items()
            if name in document.secret_fields()
        }
        return self.read(), public_values, verifier

    def acknowledge_runtime(self, revision: int) -> None:
        """Clear restart debt only after this exact snapshot composed successfully."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            document = self._document()
            if type(revision) is not int or document.revision != revision:
                raise OwnerSettingsError("settings revision is stale")
            if document.pending_restart:
                document = document.model_copy(update={"pending_restart": []})
                self._connection.execute(
                    "UPDATE owner_settings SET document=? WHERE singleton=1",
                    (_canonical_json(document.model_dump()),),
                )
            self._connection.commit()
        except OwnerSettingsError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error

    def write(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
    ) -> SettingsView:
        if (
            type(expected_revision) is not int
            or document.revision != expected_revision + 1
        ):
            raise OwnerSettingsError("settings revision is invalid")
        if len({entry.field_id for entry in secrets}) != len(secrets):
            raise OwnerSettingsError("settings secret operation is invalid")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            previous = self._document()
            if previous.revision != expected_revision:
                self._connection.rollback()
                raise OwnerSettingsError("settings revision is stale")
            if document.pending_restart != previous.pending_restart:
                self._connection.rollback()
                raise OwnerSettingsError("settings restart state is invalid")
            values = self._secret_values()
            document = document.model_copy(
                update={
                    "pending_restart": sorted(
                        set(previous.pending_restart)
                        | _restart_changes(previous, document)
                        | {entry.field_id for entry in secrets}
                    )
                }
            )
            self._validate_secret_writes(document, secrets, values)
            for entry in secrets:
                if entry.operation == "clear":
                    values.pop(entry.field_id, None)
                else:
                    assert entry.value is not None
                    values[entry.field_id] = entry.value
            obsolete = {
                name
                for name in values
                if name != "owner.password.verifier"
                and name not in document.secret_fields()
            }
            for name in obsolete:
                values.pop(name)
            if not document.required_secret_fields().issubset(values):
                self._connection.rollback()
                raise OwnerSettingsError("settings required secret is unavailable")
            if (
                self._connection.execute(
                    "UPDATE owner_settings SET revision=?,document=? WHERE singleton=1 AND revision=?",
                    (
                        document.revision,
                        _canonical_json(document.model_dump()),
                        expected_revision,
                    ),
                ).rowcount
                != 1
            ):
                self._connection.rollback()
                raise OwnerSettingsError("settings revision is stale")
            for entry in secrets:
                if entry.operation == "clear":
                    self._connection.execute(
                        "DELETE FROM owner_setting_secrets WHERE field_id=?",
                        (entry.field_id,),
                    )
                else:
                    self._connection.execute(
                        "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?) ON CONFLICT(field_id) DO UPDATE SET value=excluded.value",
                        (entry.field_id, entry.value),
                    )
            for name in obsolete:
                self._connection.execute(
                    "DELETE FROM owner_setting_secrets WHERE field_id=?", (name,)
                )
            self._connection.commit()
        except OwnerSettingsError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error
        return self.read()

    def _document(self) -> OwnerSettingsDocument:
        row = self._connection.execute(
            "SELECT document FROM owner_settings WHERE singleton=1"
        ).fetchone()
        if row is None or type(row[0]) is not str:
            raise OwnerSettingsError("owner settings are unavailable")
        try:
            return OwnerSettingsDocument.model_validate_json(row[0])
        except ValueError as error:
            raise OwnerSettingsError("owner settings are unavailable") from error

    def _secret_values(self) -> dict[str, str]:
        rows = self._connection.execute(
            "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
        ).fetchall()
        if any(type(name) is not str or not credential(value) for name, value in rows):
            raise OwnerSettingsError("owner settings are unavailable")
        return {name: value for name, value in rows}

    @staticmethod
    def _validate_secret_writes(
        document: OwnerSettingsDocument,
        secrets: tuple[SecretWrite, ...],
        existing: dict[str, str],
    ) -> None:
        valid = document.secret_fields()
        if any(entry.field_id not in valid for entry in secrets):
            raise OwnerSettingsError("settings secret operation is invalid")
        if any(
            entry.operation == "replace" and entry.field_id not in existing
            for entry in secrets
        ):
            raise OwnerSettingsError("settings secret operation is invalid")
        if any(
            entry.operation == "set" and entry.field_id in existing for entry in secrets
        ):
            raise OwnerSettingsError("settings secret operation is invalid")


def _restart_changes(
    previous: OwnerSettingsDocument, current: OwnerSettingsDocument
) -> set[str]:
    fields = {
        "connections",
        "providers",
        "libraries",
        "limits",
        "provider_recovery_generation",
    }
    changed = {
        field for field in fields if getattr(previous, field) != getattr(current, field)
    }
    changed.update(
        f"server.{field}"
        for field in type(current.server).model_fields
        if getattr(previous.server, field) != getattr(current.server, field)
    )
    return changed


__all__ = ["OwnerSettingsStore"]
