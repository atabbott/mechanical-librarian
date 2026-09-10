"""Atomic SQLite owner-settings document storage."""

from __future__ import annotations

import re
import sqlite3
from _thread import RLock
from contextlib import AbstractContextManager, nullcontext

from mechanical_librarian.adapters.persistence.description_advice import (
    request_description_preparation,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.preparation_presentation import (
    reconcile_preparation_library_additions,
)
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    OwnerSettingsError,
    SecretWrite,
    SettingsView,
    provider_capabilities,
    secret_presence,
    store_field_metadata,
)
from mechanical_librarian.domain.setup_state import SetupStateDocument, SetupStateError
from mechanical_librarian.domain.validation import credential


class OwnerSettingsStore(ConnectionScoped):
    """The only persistence boundary that ever receives raw settings secrets."""

    def __init__(
        self, connection: sqlite3.Connection, serialization_lock: RLock | None = None
    ) -> None:
        super().__init__(connection, serialization_lock=serialization_lock)

    def read(self) -> SettingsView:
        def read() -> SettingsView:
            try:
                document, values = _load_owner_settings_authority(self._connection)
                return _settings_view(document, values)
            except OwnerSettingsError:
                raise
            except sqlite3.Error as error:
                raise OwnerSettingsError("owner settings are unavailable") from error

        return self._serialized(read)

    def activate_runtime(self) -> tuple[SettingsView, dict[str, str], str]:
        """Snapshot complete store authority without acknowledging its application."""
        transaction = self._transaction("activate_owner_runtime")
        try:
            transaction.begin()
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
            transaction.commit()
        except OwnerSettingsError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None
        runtime_values = {
            name: value
            for name, value in values.items()
            if name in document.runtime_secret_fields()
        }
        return _settings_view(document, values), runtime_values, verifier

    def acknowledge_runtime(self, revision: int) -> None:
        """Clear restart debt only after this exact snapshot composed successfully."""
        transaction = self._transaction("acknowledge_owner_runtime")
        try:
            transaction.begin()
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
            transaction.commit()
        except OwnerSettingsError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None

    def write(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
        live_update: AbstractContextManager[None] | None = None,
        *,
        accepted_at: int = 0,
    ) -> SettingsView:
        # Candidate preparation enters before SQLite authority is locked;
        # successful exit occurs only after the durable commit.
        with live_update if live_update is not None else nullcontext():
            return self._write_transaction(
                document, expected_revision, secrets, accepted_at
            )

    def _write_transaction(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
        accepted_at: int,
    ) -> SettingsView:
        if (
            type(expected_revision) is not int
            or document.revision != expected_revision + 1
        ):
            raise OwnerSettingsError("settings revision is invalid")
        if len({entry.field_id for entry in secrets}) != len(secrets):
            raise OwnerSettingsError("settings secret operation is invalid")
        if type(accepted_at) is not int or accepted_at < 0:
            raise OwnerSettingsError("settings update time is invalid")
        transaction = self._transaction("write_owner_settings")
        try:
            transaction.begin()
            previous, current_values = _load_owner_settings_authority(self._connection)
            document, values, obsolete = self._candidate(
                document, expected_revision, secrets, previous, current_values
            )
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
            reconcile_preparation_library_additions(
                self._connection,
                tuple(
                    (library.connection, library.name) for library in previous.libraries
                ),
                tuple(
                    (library.connection, library.name) for library in document.libraries
                ),
                document.revision,
                accepted_at,
            )
            if previous.description_advice != document.description_advice:
                request_description_preparation(self._connection, accepted_at)
            committed = _settings_view(document, values)
            transaction.commit()
        except OwnerSettingsError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None
        return committed

    def activation_snapshot(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
    ) -> tuple[SettingsView, dict[str, str], str]:
        """Validate a candidate without making it authoritative."""
        transaction = self._transaction("snapshot_owner_activation")
        try:
            transaction.begin()
            previous, current_values = _load_owner_settings_authority(self._connection)
            candidate, values, _obsolete = self._candidate(
                document, expected_revision, secrets, previous, current_values
            )
            verifier = values.get("owner.password.verifier")
            if type(verifier) is not str or not verifier.startswith("$argon2id$"):
                raise OwnerSettingsError("owner settings are unavailable")
            credentials = {
                name: value
                for name, value in values.items()
                if name in candidate.runtime_secret_fields()
            }
            snapshot = _settings_view(candidate, values)
            transaction.rollback()
            return snapshot, credentials, verifier
        except OwnerSettingsError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise OwnerSettingsError("owner settings are unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None

    def _candidate(
        self,
        document: OwnerSettingsDocument,
        expected_revision: int,
        secrets: tuple[SecretWrite, ...],
        previous: OwnerSettingsDocument,
        current_values: dict[str, str],
    ) -> tuple[OwnerSettingsDocument, dict[str, str], set[str]]:
        if previous.revision != expected_revision:
            raise OwnerSettingsError("settings revision is stale")
        if document.pending_restart or previous.pending_restart:
            raise OwnerSettingsError("settings restart state is invalid")
        previous_library_state = dict(_non_settings_library_state(previous))
        candidate_library_state = dict(_non_settings_library_state(document))
        if any(
            candidate_library_state[name] != state
            for name, state in previous_library_state.items()
            if name in candidate_library_state
        ):
            raise OwnerSettingsError("settings document is invalid")
        _validate_available_provider_references(document)
        values = dict(current_values)
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
            raise OwnerSettingsError("settings required secret is unavailable")
        return document, values, obsolete

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
    except OwnerSettingsError:
        rows = connection.execute(
            "SELECT revision,document FROM owner_settings"
        ).fetchall()
        secrets = connection.execute(
            "SELECT field_id FROM owner_setting_secrets"
        ).fetchall()
        if len(rows) != 1:
            raise
        revision, encoded = rows[0]
        try:
            state = SetupStateDocument.model_validate_json(encoded)
        except (SetupStateError, ValueError) as error:
            raise OwnerSettingsError("owner settings are unavailable") from error
        if state.revision != revision or _canonical_json(state.model_dump()) != encoded:
            raise OwnerSettingsError("owner settings are unavailable") from None
        permitted = set(state.secret_fields())
        if state.claimed:
            permitted.add("owner.password.verifier")
        if {name for (name,) in secrets} != permitted:
            raise OwnerSettingsError("owner settings are unavailable") from None


def _settings_view(
    document: OwnerSettingsDocument, values: dict[str, str]
) -> SettingsView:
    projected = _available_settings_document(document)
    return SettingsView(
        document=projected,
        fields=store_field_metadata(projected),
        secrets=tuple(
            secret_presence(name, values.get(name))
            for name in sorted(document.secret_fields())
        ),
        provider_capabilities=provider_capabilities(projected),
        authority="store",
    )


def _available_settings_document(
    document: OwnerSettingsDocument,
) -> OwnerSettingsDocument:
    enabled = {provider.name for provider in document.providers if provider.enabled}
    libraries = [
        library.model_copy(
            update={
                "provider_connections": [
                    name for name in library.provider_connections if name in enabled
                ]
            }
        )
        for library in document.libraries
    ]
    if all(
        projected.provider_connections == current.provider_connections
        for projected, current in zip(libraries, document.libraries, strict=True)
    ):
        return document
    return document.model_copy(update={"libraries": libraries})


def _validate_available_provider_references(document: OwnerSettingsDocument) -> None:
    enabled = {provider.name for provider in document.providers if provider.enabled}
    if any(
        not set(library.provider_connections).issubset(enabled)
        for library in document.libraries
    ):
        raise OwnerSettingsError("settings library references are invalid")


def _non_settings_library_state(
    document: OwnerSettingsDocument,
) -> tuple[tuple[str, tuple[str | None, str | None, object]], ...]:
    return tuple(
        (
            library.name,
            (library.display_name, library.mount, library.policy_override),
        )
        for library in document.libraries
    )


__all__ = ["OwnerSettingsStore"]
