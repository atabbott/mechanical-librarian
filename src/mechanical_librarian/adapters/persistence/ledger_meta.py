"""Read the ledger's derived epoch (ADR-0108).

A leaf module, so every adapter that addresses derived ids over the epoch can
read it in its own transaction without importing the schema.
"""

from __future__ import annotations

import sqlite3

from mechanical_librarian.application.metadata_report import MetadataReportError


def derived_epoch(connection: sqlite3.Connection) -> int:
    """The derived epoch the ledger names, read in the caller's transaction."""
    row = connection.execute(
        "SELECT derived_epoch FROM ledger_meta WHERE singleton=1"
    ).fetchone()
    if row is None or type(row[0]) is not int or row[0] < 0:
        raise MetadataReportError("database is unavailable or incompatible")
    epoch: int = row[0]
    return epoch


__all__ = ["derived_epoch"]
