"""A synthetic ledger migration chain, registered inside a test only.

This build's chain is empty until the first ledger change rides it. A test
that proves the runner registers its own steps through `chain`, which every
reader of the chain and the format version sees, and never touches the
product registry.
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
