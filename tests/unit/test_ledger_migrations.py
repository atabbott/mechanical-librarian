"""The ledger's migrations (ADR-0108, "Ledger format and migrations").

This build's chain is empty: no ledger change has ridden it yet. Each test
here registers synthetic steps inside the test only (`chain`), never in the
product registry, and runs the real command over a store the product's own
writers made, or over the frozen v1 fixture.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.adapters.persistence import (
    ledger_migrations,
    regeneration,
    schema,
)
from mechanical_librarian.adapters.persistence.ledger_migrations import (
    LedgerMigration,
)


def chain(
    monkeypatch: pytest.MonkeyPatch, *steps: LedgerMigration, current: int
) -> None:
    """This build as one whose ledger format is `current` and whose chain is
    `steps`: every reader of either name sees the patch."""
    monkeypatch.setattr(ledger_migrations, "LEDGER_MIGRATIONS", steps)
    monkeypatch.setattr(schema, "LEDGER_FORMAT_VERSION", current)
    monkeypatch.setattr(regeneration, "LEDGER_FORMAT_VERSION", current)


def identity(source: int) -> LedgerMigration:
    """An additive step: a new format whose stored rows are unchanged."""
    return LedgerMigration(source, lambda _connection: None)


# --- The chain ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("sources", "version", "current", "expected"),
    [
        ((), 1, 1, None),
        ((1,), 1, 2, (1,)),
        ((1, 2), 1, 3, (1, 2)),
        ((1, 2), 2, 3, (2,)),
        ((2,), 1, 3, None),
        ((1,), 1, 3, None),
        ((1,), 2, 2, None),
        ((1,), 3, 2, None),
        ((1,), 0, 2, None),
    ],
    ids=[
        "empty",
        "one_step",
        "two_steps",
        "from_the_middle",
        "first_missing",
        "last_missing",
        "current",
        "newer",
        "before_the_first",
    ],
)
def test_steps_from_names_each_exact_predecessor_step_or_none(
    monkeypatch: pytest.MonkeyPatch,
    sources: tuple[int, ...],
    version: int,
    current: int,
    expected: tuple[int, ...] | None,
) -> None:
    monkeypatch.setattr(
        ledger_migrations,
        "LEDGER_MIGRATIONS",
        tuple(identity(source) for source in sources),
    )
    steps = ledger_migrations.steps_from(version, current)
    assert (None if steps is None else tuple(s.source for s in steps)) == expected
