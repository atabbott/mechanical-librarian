"""Who reads sidecar-carried local claims, and the association import boundary.

A1'' (#1481): an inventory of every function in `domain/` and `application/`
that iterates `local_claims` and names a kind a sidecar can carry.  Each
carries its classification from the contract's S13 reader table.  A function
that is not listed fails the test, so a new reader is reviewed against echo
withholding before it lands.  This prompts review; the guarantee is the
`MetadataReport` invariant and its property test (A1').

A2 (guard): the association module and the work-identity levels it calls
import only the domain.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "mechanical_librarian"
_SIDECAR_KINDS = frozenset(
    {
        "ASIN",
        "CONTRIBUTOR",
        "LANGUAGE",
        "DESCRIPTION",
        "SERIES",
        "SERIES_SEQUENCE",
        "PERFORMANCE_TYPE",
    }
)

# The S13 classes.  `asin_by_basis`: lanes, feeds and identity read an ASIN,
# and an echoed or refused one no longer exists.  `beside_target`: local values
# compared with the target's, which counts once through its own claims.
# `recording_signal`: corroboration whose input already carries the target's
# values, so the union is unchanged.  `refusal`: a reader that can only refuse,
# which reads the target's value for any field a manifest entry echoed, so an
# echo withholds belief and never a refusal (review B1, T16g).  `tightens`:
# the narrator corroboration guard, which an echo no longer satisfies, so a
# single-source cast is held for the owner.  `per_source`: per-source states, where a withheld field reads
# absent.  `embedded_only`: binds from `embedded_tag` alone (S15).
# `invariant`: the S13 check itself.  `display`: shows evidence, decides
# nothing.
_READERS: dict[str, str] = {
    "domain/audiobook_corroboration.py::recording_identity_basis": "asin_by_basis",
    "domain/audiobook_corroboration.py::_recording_narrators": "recording_signal",
    "domain/audiobook_corroboration.py::_local_series": "recording_signal",
    "domain/audiobook_corroboration.py::carried_asin_claim": "asin_by_basis",
    "domain/metadata_report.py::recording_variant_conflict": "refusal",
    "domain/metadata_report.py::local_identifier_asins": "asin_by_basis",
    "domain/metadata_report.py::_validate_sidecar_claims": "invariant",
    "domain/metadata_report.py::derive_field_results": "per_source",
    "domain/metadata_report.py::recording_association_references": "embedded_only",
    "domain/metadata_report.py::source_state": "per_source",
    "domain/proposal_set.py::_unoffered_references": "beside_target",
    "domain/proposal_set.py::_observations": "beside_target",
    "domain/proposal_set.py::_narrator_corroboration_guard": "tightens",
    "domain/proposal_set.py::_list_observations": "beside_target",
    "domain/proposal_set.py::provider_candidate_represents_target_work": (
        "asin_by_basis"
    ),
    "domain/proposal_set.py::_series_observations": "beside_target",
    "domain/provider_value_language.py::established_work_languages": ("beside_target"),
    "domain/reviewer_decision_summary.py::creator_role_evidence": "display",
    "domain/rich_metadata_preview.py::_item_languages": "display",
    "domain/shadow_outcome.py::_withdrawn_audiobook_identity": "refusal",
    "application/metadata_report.py::audiobook_discovery_plan": "asin_by_basis",
    "application/metadata_report.py::_asserted_asin_references": "asin_by_basis",
    "application/metadata_report.py::audiobook_asin_feed_plan": "asin_by_basis",
    "application/review_web.py::_review_unselectable_evidence": "display",
}
_CLASSES = frozenset(
    {
        "asin_by_basis",
        "beside_target",
        "recording_signal",
        "refusal",
        "tightens",
        "per_source",
        "embedded_only",
        "invariant",
        "display",
    }
)


def _names(node: ast.AST) -> set[str]:
    return {
        value.attr if isinstance(value, ast.Attribute) else value.id
        for value in ast.walk(node)
        if isinstance(value, (ast.Name, ast.Attribute))
    }


def _readers() -> dict[str, frozenset[str]]:
    found: dict[str, frozenset[str]] = {}
    for layer in ("domain", "application"):
        for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if "local_claims" not in _names(node):
                    continue
                iterates = any(
                    isinstance(value, (ast.For, ast.comprehension))
                    and "local_claims" in _names(value.iter)
                    for value in ast.walk(node)
                )
                kinds = frozenset(
                    value.attr
                    for value in ast.walk(node)
                    if isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "ClaimKind"
                    and value.attr in _SIDECAR_KINDS
                )
                if iterates and kinds:
                    relative = path.relative_to(PACKAGE_ROOT).as_posix()
                    found[f"{relative}::{node.name}"] = kinds
    return found


def test_every_reader_of_a_sidecar_carried_kind_is_classified() -> None:
    readers = _readers()
    unlisted = sorted(set(readers) - set(_READERS))
    assert not unlisted, (
        f"classify each new reader against the S13 table (#1481): {unlisted}"
    )


def _function(reader: str) -> ast.FunctionDef:
    relative, name = reader.split("::")
    path = PACKAGE_ROOT / relative
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_every_refusal_reader_reads_the_targets_value_for_an_echo() -> None:
    """Review B1: a refuse-only reader is handed what an echo withheld."""
    refusals = sorted(key for key, value in _READERS.items() if value == "refusal")
    assert refusals
    for reader in refusals:
        names = _names(_function(reader))
        assert names & {"echoed_languages", "echoed_target_values"}, reader


def test_the_inventory_names_no_reader_that_is_gone() -> None:
    assert sorted(set(_READERS) - set(_readers())) == []
    assert set(_READERS.values()) <= _CLASSES


@pytest.mark.parametrize(
    "module",
    ("domain/audio_sidecar_association.py", "domain/work_identity_levels.py"),
)
def test_association_imports_only_the_domain(module: str) -> None:
    """A2 (guard)."""
    tree = ast.parse((PACKAGE_ROOT / module).read_text(encoding="utf-8"))
    targets = [
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    ] + [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    own = [value for value in targets if value.startswith("mechanical_librarian")]
    assert own and all(
        value.startswith("mechanical_librarian.domain.") for value in own
    ), own
    assert {value.split(".")[0] for value in targets} <= {
        "__future__",
        "dataclasses",
        "decimal",
        "enum",
        "re",
        "mechanical_librarian",
    }
