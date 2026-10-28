#!/usr/bin/env python3
"""Check that each compact qualification record binds its runtime authority constant.

Authority is the permitted operation set alone (ADR-0087); source bytes never
rotate it. This maintenance command and its mutation tests share the validator;
the installed product never reads it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = Path("docs/reference")
PROVENANCE = REFERENCE / "qualification-provenance-current.json"
RECORDS = {
    "F19A": (
        "f19a-qualification-current.json",
        "automatic_decision.py",
        "F19A_QUALIFICATION_MANIFEST_SHA256",
    ),
    "F20": (
        "f20-rich-metadata-qualification-current.json",
        "rich_metadata_qualification.py",
        "F20_QUALIFICATION_CANDIDATE_SHA256",
    ),
    "F46": (
        "f46-audiobook-qualification-current.json",
        "audiobook_decision_qualification.py",
        "F46_QUALIFICATION_MANIFEST_SHA256",
    ),
}


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def verify_record(family: str, record: Any, provenance: dict[str, Any]) -> None:
    """Check one record's closed shape and its measurement origin reference."""
    if type(record) is not dict or set(record) != {
        "schema",
        "authority",
        "provenance",
    }:
        raise ValueError("qualification record keys are invalid")
    authority = record["authority"]
    authority_keys = {"family", "media", "permitted_operations"} | (
        {"dispositions"}
        if family == "F20"
        else {"paired_operations"}
        if family == "F46"
        else set()
    )
    if type(authority) is not dict or set(authority) != authority_keys:
        raise ValueError("qualification authority keys are invalid")
    if (
        record["schema"] != "qualification-current-v2"
        or authority["family"] != family
        or authority["media"] != ("audiobook" if family == "F46" else "ebook")
        or record["provenance"] != PROVENANCE.name
    ):
        raise ValueError("qualification family or representation mismatch")
    name = RECORDS[family][0]
    origin = provenance["historical_measurement_origins"][family]
    if (
        set(origin) != {"source_sha", "path", "sha256"}
        or re.fullmatch(r"[0-9a-f]{40}", origin["source_sha"]) is None
        or re.fullmatch(r"[0-9a-f]{64}", origin["sha256"]) is None
        or not origin["path"].startswith(
            "docs/reference/" + name.split("-qualification-")[0] + "-qualification-"
        )
    ):
        raise ValueError("immutable qualification reference mismatch")


def binding_value(source: str, constant: str) -> ast.Constant:
    """Locate the single actual top-level literal binding, never a textual decoy."""
    values = [
        node.value
        for node in ast.parse(source).body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == constant
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    if (
        len(values) != 1
        or not isinstance(values[0], ast.Constant)
        or type(values[0].value) is not str
        or re.fullmatch(r"[0-9a-f]{64}", values[0].value) is None
    ):
        raise ValueError("runtime binding assignment is invalid")
    return values[0]


def verify(root: Path = ROOT) -> None:
    provenance = json.loads((root / PROVENANCE).read_bytes())
    if set(provenance["historical_measurement_origins"]) != set(RECORDS):
        raise ValueError("measurement origin family mismatch")
    for family, (name, module, constant) in RECORDS.items():
        record = json.loads((root / REFERENCE / name).read_bytes())
        verify_record(family, record, provenance)
        source = (root / "src/mechanical_librarian/domain" / module).read_text()
        if binding_value(source, constant).value != canonical_digest(
            record["authority"]
        ):
            raise ValueError("runtime authority binding drift")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    verify()
    print("Qualification records verified.")
