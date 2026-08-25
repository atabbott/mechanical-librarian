"""Atomic SQLite owner-settings document storage."""

from __future__ import annotations

import re
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
            document, values = _load_owner_settings_authority(self._connection)
            return _settings_view(document, values)
        except OwnerSettingsError:
            raise
        except sqlite3.Error as error:
            raise OwnerSettingsError("owner settings are unavailable") from error

    def activate_runtime(self) -> tuple[SettingsView, dict[str, str], str]:
        """Snapshot complete store authority without acknowledging its application."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            document, values = _load_owner_settings_authority(self._connection)
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
        return _settings_view(document, values), public_values, verifier

    def acknowledge_runtime(self, revision: int) -> None:
        """Clear restart debt only after this exact snapshot composed successfully."""
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            document, _ = _load_owner_settings_authority(self._connection)
            if type(revision) is not int or document.revision != revision:
                raise OwnerSettingsError("settings revision is stale")
            if document.pending_restart:
                next_revision = document.revision + 1
                document = document.model_copy(
                    update={"revision": next_revision, "pending_restart": []}
                )
                if (
                    self._connection.execute(
                        "UPDATE owner_settings SET revision=?,document=? "
                        "WHERE singleton=1 AND revision=?",
                        (
                            next_revision,
                            _canonical_json(document.model_dump()),
                            revision,
                        ),
                    ).rowcount
                    != 1
                ):
                    raise OwnerSettingsError("settings revision is stale")
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
            previous, values = _load_owner_settings_authority(self._connection)
            if previous.revision != expected_revision:
                self._connection.rollback()
                raise OwnerSettingsError("settings revision is stale")
            if document.pending_restart != previous.pending_restart:
                self._connection.rollback()
                raise OwnerSettingsError("settings restart state is invalid")
            if _non_settings_library_state(previous) != _non_settings_library_state(
                document
            ):
                self._connection.rollback()
                raise OwnerSettingsError("settings document is invalid")
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
            committed = _settings_view(document, values)
            self._connection.commit()
        except OwnerSettingsError:
            self._connection.rollback()
            raise
        except sqlite3.Error as error:
            self._connection.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error
        return committed

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


_SECRET_FIELD_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


def _load_owner_settings_authority(
    connection: sqlite3.Connection, *, allow_empty: bool = False
) -> tuple[OwnerSettingsDocument, dict[str, str]]:
    rows = connection.execute(
        "SELECT revision,document FROM owner_settings ORDER BY singleton"
    ).fetchall()
    secrets = connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall()
    if not rows:
        if allow_empty and not secrets:
            raise LookupError
        raise OwnerSettingsError("owner settings are unavailable")
    if len(rows) != 1:
        raise OwnerSettingsError("owner settings are unavailable")
    revision, encoded = rows[0]
    if type(revision) is not int or type(encoded) is not str:
        raise OwnerSettingsError("owner settings are unavailable")
    try:
        document = OwnerSettingsDocument.model_validate_json(encoded)
    except ValueError as error:
        raise OwnerSettingsError("owner settings are unavailable") from error
    if (
        document.revision != revision
        or _canonical_json(document.model_dump()) != encoded
    ):
        raise OwnerSettingsError("owner settings are unavailable")
    if any(
        type(name) is not str
        or _SECRET_FIELD_ID.fullmatch(name) is None
        or not credential(value)
        for name, value in secrets
    ):
        raise OwnerSettingsError("owner settings are unavailable")
    values = {name: value for name, value in secrets}
    allowed = document.secret_fields() | {"owner.password.verifier"}
    if not set(values).issubset(allowed) or (
        "owner.password.verifier" in values
        and not values["owner.password.verifier"].startswith("$argon2id$")
    ):
        raise OwnerSettingsError("owner settings are unavailable")
    return document, values


def _validate_owner_settings_content(connection: sqlite3.Connection) -> None:
    """Admit only an empty pre-bootstrap store or one canonical authority snapshot."""
    tables = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('owner_settings','owner_setting_secrets')"
        )
    }
    if not tables:
        return
    if tables != {"owner_settings", "owner_setting_secrets"}:
        raise OwnerSettingsError("owner settings are unavailable")
    try:
        _load_owner_settings_authority(connection, allow_empty=True)
    except LookupError:
        return


def _settings_view(
    document: OwnerSettingsDocument, values: dict[str, str]
) -> SettingsView:
    return SettingsView(
        document=document,
        fields=store_field_metadata(document, frozenset(document.pending_restart)),
        secrets=tuple(
            secret_presence(name, values.get(name))
            for name in sorted(document.secret_fields())
        ),
        pending_restart=tuple(document.pending_restart),
        authority="store",
    )


def _non_settings_library_state(
    document: OwnerSettingsDocument,
) -> tuple[tuple[str, str | None, object], ...]:
    return tuple(
        (library.name, library.mount, library.policy_override)
        for library in document.libraries
    )


__all__ = ["OwnerSettingsStore"]
