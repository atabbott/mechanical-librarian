"""Stored documents' versions stay on the server (#1847).

The browser reads the content of these stored or packaged documents but never
their `schema_version`: the bundle identity already ties the page to the
build, and a version that reached the browser made every storage move edit the
OpenAPI document and the browser's fixtures by hand. API response versions,
and the four history event versions the browser tells items apart by, are the
browser's own and stay.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]

SERVER_ONLY = (
    "authored-description-decision",
    "cover-consideration",
    "cover-consideration-event",
    "cover-policy",
    "cover-policy-report",
    "description-advice-candidate",
    "metadata-policy-profile",
    "owner-settings",
    "reviewer-cost-evidence",
    "setup-state",
    "shadow-outcome-set",
)
_LABEL = re.compile(
    r"(?<![A-Za-z0-9_-])("
    + "|".join(re.escape(name) for name in SERVER_ONLY)
    + r")-v[0-9]+(?![0-9A-Za-z_-])"
)


def test_the_api_contract_names_no_stored_document_version() -> None:
    contract = ROOT / "docs" / "reference" / "review-openapi-v1.json"
    assert _LABEL.findall(contract.read_text(encoding="utf-8")) == []


def test_no_browser_source_or_fixture_spells_one() -> None:
    found = [
        f"{path.relative_to(ROOT)}: {match.group(0)}"
        for top in ("src", "tests")
        for path in sorted((ROOT / "frontend" / top).rglob("*"))
        if path.suffix in {".ts", ".tsx", ".json"} and "generated" not in path.parts
        for match in _LABEL.finditer(path.read_text(encoding="utf-8"))
    ]
    assert found == []
