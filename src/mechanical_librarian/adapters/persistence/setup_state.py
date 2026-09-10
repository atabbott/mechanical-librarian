"""SQLite persistence for the bootstrap-bound, incomplete setup document."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from _thread import RLock
from collections.abc import Callable
from typing import Literal, TypeVar

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.owner_settings import (
    _load_owner_settings_authority,
    _settings_view,
)
from mechanical_librarian.adapters.persistence.schema import (
    _SETUP_AUTOMATIC_SAFETY_ROW,
    _SETUP_FIELD_EFFECT_CONTROL_ROW,
    _SETUP_POLICY_DRAFT_ROW,
    _SETUP_POLICY_STATE_ROW,
    _SETUP_POLICY_TRANSITION_ROW,
    _SETUP_REVIEW_CONTROL_ROW,
)
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.domain.owner_settings import OwnerSettingsError, SettingsView
from mechanical_librarian.domain.reviewer_access import ReviewerAccessDocument
from mechanical_librarian.domain.setup_state import SetupStateDocument, SetupStateError
from mechanical_librarian.domain.validation import credential

_T = TypeVar("_T")
_UNCLAIMED_VERIFIER_DIGEST = "0" * 64
_SETUP_ROW_TABLES = frozenset(
    {
        "metadata_policy_drafts",
        "metadata_policy_state",
        "metadata_policy_transitions",
        "automatic_safety_state",
        "field_effect_control",
        "owner_setting_secrets",
        "owner_settings",
        "reviewer_access",
        "reviewer_access_secrets",
        "review_preparation_control",
    }
)


class SetupStateStore(ConnectionScoped):
    """Keep incomplete setup authority separate from normal owner authority."""

    def __init__(
        self, connection: sqlite3.Connection, serialization_lock: RLock | None = None
    ) -> None:
        super().__init__(connection, serialization_lock=serialization_lock)

    def initialize(self, bootstrap_verifier: str | None = None) -> SetupStateDocument:
        """Create the verifier-free exact-current setup predecessor."""
        document = SetupStateDocument(
            schema_version="setup-state-v3",
            revision=1,
            # This closed member has no verifier, claim-code, or authentication
            # meaning; it binds only the exact empty-volume predecessor.
            bootstrap_verifier_digest=_UNCLAIMED_VERIFIER_DIGEST,
        )
        transaction = self._transaction("initialize_setup_state")
        try:
            transaction.begin()
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
            transaction.commit()
        except (SetupStateError, sqlite3.Error) as error:
            transaction.rollback()
            raise SetupStateError("setup state is unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None
        return document

    def read(self, bootstrap_verifier: str | None = None) -> SetupStateDocument:
        def read() -> SetupStateDocument:
            document = self._read()
            if document.bootstrap_verifier_digest != _UNCLAIMED_VERIFIER_DIGEST:
                raise SetupStateError("setup state is unavailable")
            return document

        return self._serialized(read)

    def is_exact_schema_only(self) -> bool:
        """Admit only the seeded schema left before first setup-state commit."""

        def inspect() -> bool:
            try:
                return self._setup_rows_are_closed() and all(
                    self._connection.execute(
                        f'SELECT 1 FROM "{table}" LIMIT 1'
                    ).fetchone()
                    is None
                    for table in (
                        "owner_settings",
                        "owner_setting_secrets",
                    )
                )
            except sqlite3.Error:
                return False

        return self._serialized(inspect)

    def use_secret(
        self,
        field_id: str,
        bootstrap_verifier: str,
        consumer: Callable[[str], _T],
    ) -> _T:
        """Pass one admitted secret directly to its setup-only infrastructure consumer."""

        def use() -> _T:
            try:
                document = self.read()
                if field_id not in document.secret_fields():
                    raise SetupStateError("setup state is unavailable")
                rows = self._connection.execute(
                    "SELECT value FROM owner_setting_secrets WHERE field_id=?",
                    (field_id,),
                ).fetchall()
                if len(rows) != 1 or type(rows[0][0]) is not str:
                    raise SetupStateError("setup state is unavailable")
                return consumer(rows[0][0])
            except SetupStateError:
                raise
            except sqlite3.Error as error:
                raise SetupStateError("setup state is unavailable") from error

        return self._serialized(use)

    def write(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        bootstrap_verifier: str | None = None,
        secret_values: dict[str, str | None] | None = None,
    ) -> SetupStateDocument:
        if document.revision != expected_revision + 1:
            raise SetupStateError("setup state is unavailable")
        transaction = self._transaction("write_setup_state")
        try:
            transaction.begin()
            current = self.read()
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
            permitted = set(document.secret_fields())
            if document.claimed:
                permitted.add("owner.password.verifier")
            for field in set(values) - permitted:
                values.pop(field)
            if not document.required_secret_fields().issubset(values) or not set(
                values
            ).issubset(permitted):
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
            transaction.commit()
        except (SetupStateError, sqlite3.Error) as error:
            transaction.rollback()
            raise SetupStateError("setup state is unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None
        return document

    def transition_reviewer(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        reviewer_revision: int,
        outcome: Literal["skipped", "configured"],
    ) -> SetupStateDocument:
        """Commit setup outcome and reviewer authority in one SQLite transaction."""
        if document.revision != expected_revision + 1:
            raise SetupStateError("setup state is unavailable")
        transaction = self._transaction("transition_setup_reviewer")
        try:
            transaction.begin()
            current = self._read()
            if (
                current.revision != expected_revision
                or document.bootstrap_verifier_digest
                != current.bootstrap_verifier_digest
            ):
                raise SetupStateError("setup state is stale")
            row = self._connection.execute(
                "SELECT revision,document FROM reviewer_access WHERE singleton=1"
            ).fetchone()
            if row is None:
                reviewer = ReviewerAccessDocument(
                    schema_version="reviewer-access-v2", revision=1
                )
            else:
                reviewer = ReviewerAccessDocument.model_validate_json(row[1])
                if reviewer.revision != row[0]:
                    raise SetupStateError("setup state is unavailable")
            if reviewer.revision != reviewer_revision or (
                outcome == "configured" and reviewer.cover_observation is None
            ):
                raise SetupStateError("setup state is stale")
            if (
                self._connection.execute(
                    "UPDATE owner_settings SET revision=?,document=? "
                    "WHERE singleton=1 AND revision=?",
                    (
                        document.revision,
                        _canonical_json(document.model_dump()),
                        expected_revision,
                    ),
                ).rowcount
                != 1
            ):
                raise SetupStateError("setup state is stale")
            if outcome == "skipped":
                cleared = reviewer.model_copy(
                    update={
                        "revision": reviewer.revision + 1,
                        "cover_observation": None,
                    }
                )
                if row is None:
                    self._connection.execute(
                        "INSERT INTO reviewer_access(singleton,revision,document) VALUES(1,?,?)",
                        (cleared.revision, _canonical_json(cleared.model_dump())),
                    )
                elif (
                    self._connection.execute(
                        "UPDATE reviewer_access SET revision=?,document=? "
                        "WHERE singleton=1 AND revision=?",
                        (
                            cleared.revision,
                            _canonical_json(cleared.model_dump()),
                            reviewer_revision,
                        ),
                    ).rowcount
                    != 1
                ):
                    raise SetupStateError("setup state is stale")
            transaction.commit()
            return document
        except (SetupStateError, ValueError, sqlite3.Error) as error:
            transaction.rollback()
            raise SetupStateError("setup state is unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None

    def claim_owner(
        self,
        *,
        expected_revision: int,
        owner_verifier: str,
    ) -> SetupStateDocument:
        """Atomically establish the first owner's claim and verifier.

        The first session is not part of this transaction: #912 moved session
        state to its own database, so the claim endpoint issues it through the
        ordinary login path after this commit. If that later step fails, the
        claim stands and resubmitting the same form reaches the
        already-claimed login branch.
        """
        _verifier_digest(owner_verifier)
        if type(expected_revision) is not int:
            raise SetupStateError("setup state is unavailable")
        transaction = self._transaction("claim_setup_owner")
        try:
            transaction.begin()
            current = self._read()
            if (
                current.revision != expected_revision
                or current.next_step() != "claim"
                or current.claimed
            ):
                raise SetupStateError("setup state is stale")
            claimed = SetupStateDocument.model_validate(
                {
                    **current.model_dump(),
                    "revision": expected_revision + 1,
                    "completed_steps": ["claim"],
                    "claimed": True,
                }
            )
            if (
                self._connection.execute(
                    "UPDATE owner_settings SET revision=?,document=? "
                    "WHERE singleton=1 AND revision=?",
                    (
                        claimed.revision,
                        _canonical_json(claimed.model_dump()),
                        expected_revision,
                    ),
                ).rowcount
                != 1
            ):
                raise SetupStateError("setup state is stale")
            self._connection.execute(
                "INSERT INTO owner_setting_secrets(field_id,value) "
                "VALUES('owner.password.verifier',?)",
                (owner_verifier,),
            )
            transaction.commit()
            return claimed
        except SetupStateError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise SetupStateError("setup state is unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None

    def use_owner_verifier(self, consumer: Callable[[str], _T]) -> _T:
        """Keep the established verifier inside the persistence boundary."""

        def use() -> _T:
            try:
                document = self._read()
                if not document.claimed:
                    raise SetupStateError("setup state is unavailable")
                rows = self._connection.execute(
                    "SELECT value FROM owner_setting_secrets "
                    "WHERE field_id='owner.password.verifier'"
                ).fetchall()
                if len(rows) != 1 or type(rows[0][0]) is not str:
                    raise SetupStateError("setup state is unavailable")
                return consumer(rows[0][0])
            except sqlite3.Error as error:
                raise SetupStateError("setup state is unavailable") from error

        return self._serialized(use)

    def activation_snapshot(
        self, expected_revision: int
    ) -> tuple[SettingsView, dict[str, str], str]:
        """Project a complete dormant runtime candidate without committing it."""

        def snapshot() -> tuple[SettingsView, dict[str, str], str]:
            try:
                state = self._read()
                if (
                    state.revision != expected_revision
                    or state.next_step() != "kick_off"
                ):
                    raise SetupStateError("setup state is stale")
                document = state.complete_owner_document()
                values = dict(
                    self._connection.execute(
                        "SELECT field_id,value FROM owner_setting_secrets"
                    )
                )
                verifier = values.get("owner.password.verifier")
                if type(verifier) is not str or not verifier.startswith("$argon2id$"):
                    raise SetupStateError("setup state is unavailable")
                credentials = {
                    name: value
                    for name, value in values.items()
                    if name in document.secret_fields()
                }
                if not document.required_secret_fields().issubset(credentials):
                    raise SetupStateError("setup state is unavailable")
                return _settings_view(document, values), credentials, verifier
            except sqlite3.Error as error:
                raise SetupStateError("setup state is unavailable") from error

        return self._serialized(snapshot)

    def promote(
        self,
        bootstrap_verifier: str,
        owner_verifier: str,
        expected_revision: int | None = None,
    ) -> None:
        """Replace setup authority with normal owner authority in one transaction."""
        transaction = self._transaction("promote_setup_state")
        try:
            transaction.begin()
            try:
                state = self._read()
            except SetupStateError:
                try:
                    document, secrets = _load_owner_settings_authority(self._connection)
                except (LookupError, OwnerSettingsError) as error:
                    raise SetupStateError("setup state is unavailable") from error
                if (
                    document.revision != 1
                    or document.pending_restart
                    or not secrets.get("owner.password.verifier", "").startswith(
                        "$argon2id$"
                    )
                ):
                    raise SetupStateError("setup state is unavailable") from None
                transaction.commit()
                return
            if state.bootstrap_verifier_digest != _UNCLAIMED_VERIFIER_DIGEST:
                raise SetupStateError("setup state is unavailable")
            if expected_revision is not None and state.revision != expected_revision:
                raise SetupStateError("setup state is stale")
            document = state.complete_owner_document()
            verifier = self._connection.execute(
                "SELECT value FROM owner_setting_secrets "
                "WHERE field_id='owner.password.verifier'"
            ).fetchone()
            if verifier is None and owner_verifier.startswith("$argon2id$"):
                # Compatibility for already-constructed in-process callers;
                # the supported first-run path establishes this at claim time.
                self._connection.execute(
                    "INSERT INTO owner_setting_secrets(field_id,value) "
                    "VALUES('owner.password.verifier',?)",
                    (owner_verifier,),
                )
                verifier = (owner_verifier,)
            if (
                not state.claimed
                or verifier is None
                or type(verifier[0]) is not str
                or not verifier[0].startswith("$argon2id$")
            ):
                raise SetupStateError("setup state is unavailable")
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
            transaction.commit()
        except (SetupStateError, sqlite3.Error) as error:
            transaction.rollback()
            raise SetupStateError("setup state is unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None

    def _read(self) -> SetupStateDocument:
        try:
            rows = self._connection.execute(
                "SELECT revision,document FROM owner_settings"
            ).fetchall()
            secrets = self._connection.execute(
                "SELECT field_id,value FROM owner_setting_secrets"
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
            values = {name: value for name, value in secrets}
            permitted = set(document.secret_fields())
            if document.claimed:
                permitted.add("owner.password.verifier")
            if (
                not document.required_secret_fields().issubset(values)
                or not set(values).issubset(permitted)
                or any(
                    type(value) is not str
                    or (
                        name == "owner.password.verifier"
                        and not value.startswith("$argon2id$")
                    )
                    or (name != "owner.password.verifier" and not credential(value))
                    for name, value in values.items()
                )
                or not self._setup_rows_are_closed()
            ):
                raise SetupStateError("setup state is unavailable")
            return document
        except (SetupStateError, ValueError, sqlite3.Error) as error:
            raise SetupStateError("setup state is unavailable") from error

    def _setup_rows_are_closed(self) -> bool:
        tables = {
            name
            for (name,) in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for table in tables - _SETUP_ROW_TABLES:
            if self._connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                return False
        if "metadata_policy_drafts" in tables and self._connection.execute(
            "SELECT policy_id,document FROM metadata_policy_drafts"
        ).fetchall() != [_SETUP_POLICY_DRAFT_ROW]:
            return False
        if "metadata_policy_state" in tables and self._connection.execute(
            "SELECT singleton,active_policy_id,generation,activated_at,posture "
            "FROM metadata_policy_state"
        ).fetchall() != [_SETUP_POLICY_STATE_ROW]:
            return False
        if "metadata_policy_transitions" in tables and self._connection.execute(
            "SELECT transition_id,from_policy_id,to_policy_id,generation,state,total,"
            "acknowledged,created_at,updated_at FROM metadata_policy_transitions"
        ).fetchall() != [_SETUP_POLICY_TRANSITION_ROW]:
            return False
        if "automatic_safety_state" in tables and self._connection.execute(
            "SELECT singleton,paused,global_suspension,category_suspensions_document,"
            "updated_at FROM automatic_safety_state"
        ).fetchall() != [_SETUP_AUTOMATIC_SAFETY_ROW]:
            return False
        if "field_effect_control" in tables and self._connection.execute(
            "SELECT singleton,paused,updated_at FROM field_effect_control"
        ).fetchall() != [_SETUP_FIELD_EFFECT_CONTROL_ROW]:
            return False
        if "review_preparation_control" in tables and self._connection.execute(
            "SELECT singleton,paused,claim_sequence,first_boot_acknowledged,last_library,circuit_failures,"
            "circuit_open_until,updated_at FROM review_preparation_control"
        ).fetchall() != [_SETUP_REVIEW_CONTROL_ROW]:
            return False
        return True


def _verifier_digest(verifier: str) -> str:
    if type(verifier) is not str or not verifier.startswith("$argon2id$"):
        raise SetupStateError("setup state is unavailable")
    return hashlib.sha256(verifier.encode("utf-8")).hexdigest()


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


__all__ = ["SetupStateStore"]
