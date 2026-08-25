"""SQLite persistence for the bootstrap-bound, incomplete setup document."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import TypeVar

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.owner_settings import (
    _load_owner_settings_authority,
)
from mechanical_librarian.domain.owner_settings import OwnerSettingsError
from mechanical_librarian.domain.setup_state import SetupStateDocument, SetupStateError
from mechanical_librarian.domain.validation import credential

_T = TypeVar("_T")


class SetupStateStore:
    """Keep incomplete setup authority separate from normal owner authority."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def initialize(self, bootstrap_verifier: str) -> SetupStateDocument:
        digest = _verifier_digest(bootstrap_verifier)
        document = SetupStateDocument(
            schema_version="setup-state-v1",
            revision=1,
            bootstrap_verifier_digest=digest,
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if (
                self._connection.execute("SELECT 1 FROM owner_settings").fetchone()
                is not None
            ):
                raise SetupStateError("setup state is unavailable")
            if (
                self._connection.execute(
                    "SELECT 1 FROM owner_setting_secrets"
                ).fetchone()
                is not None
            ):
                raise SetupStateError("setup state is unavailable")
            self._connection.execute(
                "INSERT INTO owner_settings(singleton,revision,document) VALUES(1,?,?)",
                (document.revision, _canonical_json(document.model_dump())),
            )
            self._connection.commit()
        except (SetupStateError, sqlite3.Error) as error:
            self._connection.rollback()
            raise SetupStateError("setup state is unavailable") from error
        return document

    def read(self, bootstrap_verifier: str) -> SetupStateDocument:
        document = self._read()
        if document.bootstrap_verifier_digest != _verifier_digest(bootstrap_verifier):
            raise SetupStateError("setup state is unavailable")
        return document

    def use_secret(
        self,
        field_id: str,
        bootstrap_verifier: str,
        consumer: Callable[[str], _T],
    ) -> _T:
        """Pass one admitted secret directly to its setup-only infrastructure consumer."""
        document = self.read(bootstrap_verifier)
        if field_id not in document.secret_fields():
            raise SetupStateError("setup state is unavailable")
        try:
            rows = self._connection.execute(
                "SELECT value FROM owner_setting_secrets WHERE field_id=?", (field_id,)
            ).fetchall()
            if len(rows) != 1 or type(rows[0][0]) is not str:
                raise SetupStateError("setup state is unavailable")
            return consumer(rows[0][0])
        except SetupStateError:
            raise
        except sqlite3.Error as error:
            raise SetupStateError("setup state is unavailable") from error

    def write(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        bootstrap_verifier: str,
        secret_values: dict[str, str | None] | None = None,
    ) -> SetupStateDocument:
        if document.revision != expected_revision + 1:
            raise SetupStateError("setup state is unavailable")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.read(bootstrap_verifier)
            if current.revision != expected_revision:
                raise SetupStateError("setup state is stale")
            if document.bootstrap_verifier_digest != current.bootstrap_verifier_digest:
                raise SetupStateError("setup state is unavailable")
            values = dict(
                self._connection.execute(
                    "SELECT field_id,value FROM owner_setting_secrets"
                )
            )
            delta = {} if secret_values is None else secret_values
            if any(
                field not in current.secret_fields()
                and field not in document.secret_fields()
                or value is not None
                and (type(value) is not str or not credential(value))
                for field, value in delta.items()
            ):
                raise SetupStateError("setup state is unavailable")
            for field, value in delta.items():
                if value is None:
                    values.pop(field, None)
                else:
                    values[field] = value
            for field in set(values) - document.secret_fields():
                values.pop(field)
            if set(values) != document.secret_fields():
                raise SetupStateError("setup state is unavailable")
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
                raise SetupStateError("setup state is stale")
            for field_id, value in delta.items():
                if value is None:
                    self._connection.execute(
                        "DELETE FROM owner_setting_secrets WHERE field_id=?",
                        (field_id,),
                    )
                else:
                    self._connection.execute(
                        "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?) ON CONFLICT(field_id) DO UPDATE SET value=excluded.value",
                        (field_id, value),
                    )
            for field_id in current.secret_fields() - document.secret_fields():
                self._connection.execute(
                    "DELETE FROM owner_setting_secrets WHERE field_id=?", (field_id,)
                )
            for field_id, value in values.items():
                self._connection.execute(
                    "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?) ON CONFLICT(field_id) DO NOTHING",
                    (field_id, value),
                )
            self._connection.commit()
        except (SetupStateError, sqlite3.Error) as error:
            self._connection.rollback()
            raise SetupStateError("setup state is unavailable") from error
        return document

    def promote(
        self,
        bootstrap_verifier: str,
        owner_verifier: str,
        expected_revision: int | None = None,
    ) -> None:
        """Replace setup authority with normal owner authority in one transaction."""
        _verifier_digest(owner_verifier)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                state = self._read()
            except SetupStateError:
                try:
                    document, secrets = _load_owner_settings_authority(self._connection)
                except (LookupError, OwnerSettingsError) as error:
                    raise SetupStateError("setup state is unavailable") from error
                if (
                    document.revision != 1
                    or document.pending_restart != ["setup_complete"]
                    or secrets.get("owner.password.verifier") != owner_verifier
                ):
                    raise SetupStateError("setup state is unavailable") from None
                self._connection.commit()
                return
            if state.bootstrap_verifier_digest != _verifier_digest(bootstrap_verifier):
                raise SetupStateError("setup state is unavailable")
            if expected_revision is not None and state.revision != expected_revision:
                raise SetupStateError("setup state is stale")
            document = state.complete_owner_document()
            if (
                self._connection.execute(
                    "UPDATE owner_settings SET revision=?,document=? WHERE singleton=1 AND revision=?",
                    (
                        document.revision,
                        _canonical_json(document.model_dump()),
                        state.revision,
                    ),
                ).rowcount
                != 1
            ):
                raise SetupStateError("setup state is stale")
            self._connection.execute(
                "INSERT INTO owner_setting_secrets(field_id,value) VALUES('owner.password.verifier',?)",
                (owner_verifier,),
            )
            self._connection.commit()
        except (SetupStateError, sqlite3.Error) as error:
            self._connection.rollback()
            raise SetupStateError("setup state is unavailable") from error

    def _read(self) -> SetupStateDocument:
        try:
            rows = self._connection.execute(
                "SELECT revision,document FROM owner_settings"
            ).fetchall()
            secrets = self._connection.execute(
                "SELECT field_id FROM owner_setting_secrets"
            ).fetchall()
            if len(rows) != 1:
                raise SetupStateError("setup state is unavailable")
            revision, encoded = rows[0]
            document = SetupStateDocument.model_validate_json(encoded)
            if (
                document.revision != revision
                or _canonical_json(document.model_dump()) != encoded
            ):
                raise SetupStateError("setup state is unavailable")
            if {name for (name,) in secrets} != document.secret_fields():
                raise SetupStateError("setup state is unavailable")
            return document
        except (SetupStateError, ValueError, sqlite3.Error) as error:
            raise SetupStateError("setup state is unavailable") from error


def _verifier_digest(verifier: str) -> str:
    if type(verifier) is not str or not verifier.startswith("$argon2id$"):
        raise SetupStateError("setup state is unavailable")
    return hashlib.sha256(verifier.encode("utf-8")).hexdigest()


__all__ = ["SetupStateStore"]
