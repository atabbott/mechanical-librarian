"""The ledger pin (ADR-0108, "Ledger format and migrations").

`PINNED` names, for each `LEDGER_FORMAT_VERSION`, the digest of that ledger
and the issue whose CHANGELOG entry declared it. The digest covers the ledger
DDL, every ledger document's schema versions (and the JSON schema of each
pydantic ledger document, which names its settings and their values) and the
closed vocabularies a ledger document may hold, all read from the product's
own definitions through `tests/support/ledger_documents.py`.

A ledger change (any change to a ledger table or document, or to one of those
vocabularies) moves the digest, so this test fails until the change takes a
new `LEDGER_FORMAT_VERSION`, a new `PINNED` entry and its CHANGELOG marker:
**Clean rebuild required** until C6 builds ledger migrations, then **Ledger
migration**. The frozen fixture of the old version stays as it is; C6
migrates from it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from tests.support import ledger_documents

ROOT = Path(__file__).parents[2]

# {ledger format version: (digest, issue whose CHANGELOG entry declares it)}
PINNED: dict[int, tuple[str, int]] = {
    1: ("62fde80aa634ac04e85f91c2518a759d47549f0f040b8a651afce1b22f02375b", 2040),
}


def ledger_digest() -> str:
    """The digest of this build's ledger: DDL, documents and vocabularies."""
    payload = {
        "ddl": sorted(
            [kind, name, sql]
            for kind, name, _, sql in schema.build_partition().ledger_objects
        ),
        "documents": ledger_documents.versions(),
        "vocabularies": ledger_documents.VOCABULARIES,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def changelog_entries() -> list[str]:
    """Each CHANGELOG entry as one line: a `- ` line and its continuation."""
    entries: list[str] = []
    for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("- "):
            entries.append(line)
        elif line.startswith("  ") and entries:
            entries[-1] += " " + line.strip()
    return entries


def declares(marker: str, issue: int) -> bool:
    """Whether a CHANGELOG entry opens with ``marker`` and names ``issue``."""
    return any(
        entry.startswith(f"- **{marker}.**") and re.search(rf"#{issue}\b", entry)
        for entry in changelog_entries()
    )


def test_the_pin_names_this_builds_ledger() -> None:
    assert max(PINNED) == schema.LEDGER_FORMAT_VERSION
    assert PINNED[schema.LEDGER_FORMAT_VERSION][0] == ledger_digest(), (
        "the ledger changed: give it a new LEDGER_FORMAT_VERSION, pin it here "
        "and declare it in the CHANGELOG (ADR-0108)"
    )


def test_every_ledger_document_column_is_registered() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        schema._create_schema(connection)
        assert ledger_documents.missing_documents(connection) == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    "change",
    ["ddl", "document_version", "vocabulary_member"],
    ids=["ddl", "document_version", "vocabulary_member"],
)
def test_the_pin_moves_with_each_part_of_the_ledger(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Negative control: each part the pin covers moves it."""
    before = ledger_digest()
    if change == "ddl":
        build = schema.build_partition()
        kind, name, table, sql = build.ledger_objects[0]
        assert sql is not None
        moved = (kind, name, table, sql.replace("(", "( ", 1))
        monkeypatch.setattr(
            schema,
            "build_partition",
            lambda: type(build)(
                (moved, *build.ledger_objects[1:]),
                build.derived_objects,
                build.derived_schema_digest,
                build.derived_seed_rows,
            ),
        )
    elif change == "document_version":
        key = ("human_decisions", "document")
        document = ledger_documents.DOCUMENTS[key]
        monkeypatch.setitem(
            ledger_documents.DOCUMENTS,
            key,
            type(document)(document.decode, document.encode, ("human-decision-v0",)),
        )
    else:
        destinations = ledger_documents.VOCABULARIES["DESTINATIONS"]
        assert type(destinations) is tuple
        monkeypatch.setitem(
            ledger_documents.VOCABULARIES,
            "DESTINATIONS",
            ("asked", *destinations[1:]),
        )
    assert ledger_digest() != before


def test_each_pinned_ledger_format_is_declared_in_the_changelog() -> None:
    """The cutover's format declares **Clean rebuild required**; a later one
    declares that or, from C6, **Ledger migration**."""
    for version, (_, issue) in PINNED.items():
        markers = (
            ("Clean rebuild required",)
            if version == 1
            else ("Clean rebuild required", "Ledger migration")
        )
        assert any(declares(marker, issue) for marker in markers), (version, issue)
