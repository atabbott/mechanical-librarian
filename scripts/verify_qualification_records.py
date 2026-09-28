#!/usr/bin/env python3
"""Check or explicitly refresh compact qualification source and authority bindings.

Only the reviewed semantic partition rotates authority. This maintenance command
and its mutation tests share the validator; the installed product never reads it.
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
EXCLUSIONS = [
    "F19A_QUALIFICATION_MANIFEST_SHA256",
    "F20_QUALIFICATION_CANDIDATE_SHA256",
    "F46_QUALIFICATION_MANIFEST_SHA256",
    "_PRIOR_F19A_QUALIFICATION_MANIFESTS",
    "_PRIOR_F20_QUALIFICATION_MANIFESTS",
]
AST_SOURCES = sorted(
    "src/mechanical_librarian/domain/" + value[1] for value in RECORDS.values()
)
# Reviewed path/classification snapshot. Amend explicitly when source ownership changes.
CLASSIFICATION_SHA256 = (
    "f449d705906328f5eb5f59a70562a0f491850c58c3dd35e8ebc0d0c42c9bc41e"
)


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def source_digest(name: str, content: bytes) -> str:
    if name in AST_SOURCES:
        tree = ast.parse(content)
        tree.body = [
            node
            for node in tree.body
            if not (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id in EXCLUSIONS
                    for target in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
            )
        ]
        content = ast.dump(tree, include_attributes=False).encode()
    return hashlib.sha256(content).hexdigest()


def source_bindings(
    root: Path, provenance: dict[str, Any], overrides: dict[str, bytes] | None = None
) -> dict[str, str]:
    classifications = provenance["source_classification"]
    if canonical_digest(classifications) != CLASSIFICATION_SHA256:
        raise ValueError("source classification requires explicit review")
    if (
        provenance["binding_exclusions"] != EXCLUSIONS
        or provenance["ast_sources"] != AST_SOURCES
    ):
        raise ValueError("unknown binding exclusion")
    product_sources = {
        p.relative_to(root).as_posix()
        for p in (root / "src/mechanical_librarian").rglob("*")
        if p.is_file()
        and p.relative_to(root).as_posix() != "src/mechanical_librarian/py.typed"
        and not (p.parent.name == "__pycache__" and p.suffix == ".pyc")
    }
    classified_sources = {
        p for p in classifications if p.startswith("src/mechanical_librarian/")
    }
    if product_sources != classified_sources:
        raise ValueError("product source inventory requires explicit review")
    overrides = overrides or {}
    if not overrides.keys() <= classifications.keys():
        raise ValueError("unclassified source override")
    return {
        name: source_digest(
            name, overrides[name] if name in overrides else (root / name).read_bytes()
        )
        for name in classifications
        if name != PROVENANCE.as_posix()
    }


def semantic_digest(provenance: dict[str, Any], bindings: dict[str, str]) -> str:
    return canonical_digest(
        {
            name: digest
            for name, digest in bindings.items()
            if provenance["source_classification"][name] == "semantic"
        }
    )


def verify_references(root: Path, provenance: dict[str, Any]) -> None:
    """Check local immutable reference coherence without resolving archive bytes."""
    archive = json.loads(
        (root / REFERENCE / "qualification-archive-index.json").read_bytes()
    )["records"]
    if set(provenance["historical_measurement_origins"]) != set(RECORDS):
        raise ValueError("measurement origin family mismatch")
    for family, (name, _, _) in RECORDS.items():
        record = json.loads((root / REFERENCE / name).read_bytes())
        if type(record) is not dict or set(record) != {
            "schema",
            "authority",
            "predecessor",
            "provenance",
        }:
            raise ValueError("qualification record keys are invalid")
        authority = record["authority"]
        authority_keys = {
            "family",
            "media",
            "semantic_revision",
            "semantic_input_sha256",
            "permitted_operations",
        } | (
            {"dispositions"}
            if family == "F20"
            else {"paired_operations"}
            if family == "F46"
            else set()
        )
        if type(authority) is not dict or set(authority) != authority_keys:
            raise ValueError("qualification authority keys are invalid")
        if (
            record["schema"] != "qualification-current-v1"
            or authority["family"] != family
            or authority["media"] != ("audiobook" if family == "F46" else "ebook")
            or type(authority["semantic_revision"]) is not int
            or authority["semantic_revision"] < 1
            or record["provenance"] != PROVENANCE.name
        ):
            raise ValueError("qualification family or representation mismatch")
        for reference in (
            record["predecessor"],
            provenance["historical_measurement_origins"][family],
        ):
            if (
                set(reference) != {"source_sha", "path", "sha256"}
                or re.fullmatch(r"[0-9a-f]{40}", reference["source_sha"]) is None
                or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None
                or not reference["path"].startswith(
                    "docs/reference/"
                    + name.split("-qualification-")[0]
                    + "-qualification-"
                )
                or archive.get(reference["source_sha"], {}).get(reference["path"])
                != reference["sha256"]
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


def verify(root: Path = ROOT, *, overrides: dict[str, bytes] | None = None) -> None:
    provenance = json.loads((root / PROVENANCE).read_bytes())
    verify_references(root, provenance)
    bindings = source_bindings(root, provenance, overrides)
    if bindings != provenance["source_bindings"]:
        raise ValueError("source binding drift")
    semantic = semantic_digest(provenance, bindings)
    for name, module, constant in RECORDS.values():
        record = json.loads((root / REFERENCE / name).read_bytes())
        if record["authority"]["semantic_input_sha256"] != semantic:
            raise ValueError("semantic authority drift")
        source = (root / "src/mechanical_librarian/domain" / module).read_text()
        if binding_value(source, constant).value != canonical_digest(
            record["authority"]
        ):
            raise ValueError("runtime authority binding drift")


def refresh(root: Path = ROOT) -> None:
    """Refresh reviewed source bytes; never infer a new classification or permission."""
    provenance = json.loads((root / PROVENANCE).read_bytes())
    verify_references(root, provenance)
    semantic = semantic_digest(provenance, source_bindings(root, provenance))
    for name, module, constant in RECORDS.values():
        path = root / REFERENCE / name
        record = json.loads(path.read_bytes())
        record["authority"]["semantic_input_sha256"] = semantic
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        module_path = root / "src/mechanical_librarian/domain" / module
        source = module_path.read_text()
        value = binding_value(source, constant)
        lines = source.encode().splitlines(keepends=True)
        start = sum(map(len, lines[: value.lineno - 1])) + value.col_offset
        assert value.end_lineno is not None and value.end_col_offset is not None
        end = sum(map(len, lines[: value.end_lineno - 1])) + value.end_col_offset
        replacement = json.dumps(canonical_digest(record["authority"])).encode()
        module_path.write_bytes(
            source.encode()[:start] + replacement + source.encode()[end:]
        )
    provenance["source_bindings"] = source_bindings(root, provenance)
    (root / PROVENANCE).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n"
    )
    verify(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="refresh reviewed bindings after an intentional change",
    )
    args = parser.parse_args()
    if args.refresh:
        refresh()
    else:
        verify()
    print("Qualification records verified.")
