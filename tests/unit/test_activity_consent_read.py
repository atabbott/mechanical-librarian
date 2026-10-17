"""Library's write-ready notice reads Activity's consent inputs alone (#1236).

The notice needs the waiting reasons, the destination capabilities, the
configured libraries and the null backup readiness Activity reports. None is an
Activity fact, so the narrow read takes no catch-up. These oracles compare it,
state by state over one store, with the consent fields of Activity's own page,
and show it touches no history projection while Activity's page would.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence import field_effect_activity
from mechanical_librarian.adapters.persistence.activity_facts import ActivityFactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.bootstrap.composition import (
    compose_field_effect_activity_projection,
)
from tests.unit.test_pending_field_effect_lookup import (  # noqa: F401
    _digest,
    _effect,
    store,
)

CONSENT_KEYS = ("backup_ready", "capabilities", "configured_libraries", "waiting")


def _configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "http://127.0.0.1:1", None),),
        (LibraryConfiguration("books", "primary", "Books", "ebook", None, ()),),
        (),
        (),
        review=ReviewWebConfiguration("http://127.0.0.1:1", "/synthetic/owner.hash"),
    )


def _state(waiting: dict[str, int]) -> str:
    """The notice's state, as `readWriteConsent` reads it off the queue."""
    if waiting["applying"]:
        return "applying"
    if waiting["awaiting_permission"]:
        return "off"
    return "enabled" if waiting["scheduled"] else "complete"


class _Readers:
    """The generation's two readers over one store, composed as production does."""

    def __init__(self, path: Path) -> None:
        self.recovery: tuple[dict[str, int | str | list[str] | None], ...] = ()
        recovery = cast(
            "SQLiteProviderRecoveryStore",
            SimpleNamespace(activity_snapshot=lambda: self.recovery),
        )
        self.connections = [
            sqlite3.connect(path, check_same_thread=False) for _ in range(2)
        ]
        history = SQLiteMetadataReportRepository(self.connections[0])
        history.configure_activity_page_reader(ActivityFactStore())
        permissions = SQLiteMetadataReportRepository(self.connections[1])
        permissions.configure_activity_page_reader()
        self.history_repository = history
        self.history = compose_field_effect_activity_projection(
            _configuration(), history, recovery
        )
        self.permissions = compose_field_effect_activity_projection(
            _configuration(), permissions, recovery
        )

    def close(self) -> None:
        for connection in self.connections:
            connection.close()


@pytest.fixture
def readers(
    store: tuple[sqlite3.Connection, list[str]],  # noqa: F811
) -> Iterator[tuple[sqlite3.Connection, list[str], _Readers]]:
    writer, pending = store
    value = _Readers(Path(writer.execute("PRAGMA database_list").fetchone()[2]))
    try:
        yield writer, pending, value
    finally:
        value.close()


def _assert_oracle(
    application: ContinuousGuardedApplication, history: ContinuousGuardedApplication
) -> dict[str, object]:
    consent = application.activity_consent()
    page = history.activity()
    assert set(consent) == set(CONSENT_KEYS)
    assert consent == {key: page[key] for key in CONSENT_KEYS}
    return consent


def test_the_consent_read_states_what_activity_states_in_every_queue_state(
    readers: tuple[sqlite3.Connection, list[str], _Readers],
) -> None:
    writer, _, value = readers
    states: list[str] = []
    unavailable: list[str] = []

    def step() -> dict[str, object]:
        consent = _assert_oracle(value.permissions, value.history)
        # The same projection answers on the history reader's own lock.
        assert value.history.activity_consent() == consent
        waiting = cast("dict[str, int]", consent["waiting"])
        states.append(_state(waiting))
        capabilities = cast("list[dict[str, object]]", consent["capabilities"])
        unavailable.append(str(capabilities[0]["state"]))
        return consent

    off = step()
    assert off["backup_ready"] is None
    # A connection that needs checking, and a provider recovery holding work.
    writer.execute(
        "INSERT INTO field_effect_suspensions VALUES('connection','primary','',"
        "'connection_unavailable',1,'{}')"
    )
    writer.commit()
    value.recovery = (
        {"state": "dormant", "affected_count": 3, "next_eligible_at": None},
    )
    held = step()
    assert cast("dict[str, int]", held["waiting"])["held_for_recovery"] == 4
    writer.execute("DELETE FROM field_effect_suspensions")
    writer.commit()
    value.recovery = ()
    # Permission granted, then the queue moves through each state it reports.
    SQLiteMetadataReportRepository(writer).grant_field_effect_consent_matrix(
        "primary", (("books", ("title", "authors")),), 5
    )
    step()
    # Effects whose decisions are gone leave the prepared counts alone, so
    # only the queue the reasons are read off moves.
    for label, state in (("planned", "planned"), ("applying", "applying")):
        decision = "human_decision_" + _digest(label)
        _effect(writer, label, decision, json.dumps({"plan": {}}), state)
        writer.commit()
        step()
    writer.execute("UPDATE field_effects SET state='verified'")
    writer.commit()
    step()
    assert states == ["off", "off", "complete", "enabled", "applying", "complete"]
    assert unavailable == ["allowed", "connection_unavailable", *["allowed"] * 4]


def test_the_consent_read_takes_no_activity_history_step(
    readers: tuple[sqlite3.Connection, list[str], _Readers],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even on the history reader, nothing builds or catches up its facts."""
    _, _, value = readers
    expected = value.permissions.activity_consent()

    def touched(*_arguments: object, **_options: object) -> None:
        raise AssertionError("the consent read touched Activity history")

    activity = field_effect_activity.SQLiteFieldEffectActivityRepository
    for name in ("_caught_up", "_activity_fact_page", "_activity_fact_step"):
        monkeypatch.setattr(activity, name, touched)
    monkeypatch.setattr(ActivityFactStore, "refresh", touched)
    facts = value.history_repository._field_effect_activity._activity_facts
    assert facts is not None and facts.snapshot() is None
    assert value.history.activity_consent() == expected
    assert facts.snapshot() is None
    # The same guard stops Activity's own page, so it is not vacuous.
    with pytest.raises(AssertionError, match="touched Activity history"):
        value.history.activity()
