"""Who reads the refused WORK answers and the level storage (#1355, S10, S11).

`ProviderObservation.identity_refused_candidates` holds products set admission
refused. They are never live evidence: only the level seam and the level
derivation read them, through its set rule, and their producers carry them.
Every function that reads the attribute, or the serialized key, is listed with
its role, so a new reader fails here and is reviewed against S11 before it
lands. The report module itself, which validates, serializes and bounds them,
and the provider adapters, which only copy their lanes' records into the lane
result, are allowed whole.

The level storage tables are read and written only by the level
reconciliation module, and created only by the schema (S10, amendment 2).
Automatic decisions, consent, field-effect planning and the executors never
name them.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
_ATTRIBUTE = "identity_refused_candidates"
_WHOLE_MODULES = (
    "src/mechanical_librarian/domain/metadata_report.py",
    "src/mechanical_librarian/adapters/providers/",
)
_READERS: dict[str, str] = {
    # The level seam (S5, S6): the set, rule 3 and the level-only identifier
    # classification of a retained record.
    "domain/proposal_set.py::_level_bound_identifiers": "level_seam",
    "domain/proposal_set.py::_level_candidate_sets": "level_seam",
    "domain/proposal_set.py::provider_candidate_level_admission": "level_seam",
    # S10's level derivation: the level's admissions over one report, and the
    # level packet's index of them (R2: never on the live path).
    "domain/proposal_set.py::level_evidence": "level_derivation",
    "domain/shadow_review.py::_packet_report_data": "level_derivation",
    # Producers: a lane result's own validation, a lane's ladder union and
    # the observation's aggregation.
    "application/metadata_report.py::ProviderLaneResult.__post_init__": "producer",
    "application/metadata_report.py::QualifiedLaneObservation."
    "identity_refused_candidates": "producer",
    "application/metadata_report.py::_rung_union": "producer",
    "application/metadata_report.py::_aggregate_identity_refused": "producer",
    # The review API cites a refused product's evidence when a level choice
    # names it (S10 "Readers", amendment 2): evidence only, never an offer.
    "application/review_web.py::_evidence_by_reference": "review_api_evidence",
    # A retry successor keeps each carried lane's own records (AC6).
    "application/provider_retry_successor.py::"
    "GenerateProviderRetrySuccessor._merged.result": "producer",
}


def _readers(path: Path) -> set[str]:
    found: set[str] = set()

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, (*scope, child.name))
            elif isinstance(child, ast.ClassDef):
                visit(child, (*scope, child.name))
            else:
                if (
                    isinstance(child, ast.Attribute)
                    and child.attr == _ATTRIBUTE
                    and isinstance(child.ctx, ast.Load)
                ) or (
                    isinstance(child, ast.Subscript)
                    and isinstance(child.slice, ast.Constant)
                    and child.slice.value == _ATTRIBUTE
                    and isinstance(child.ctx, ast.Load)
                ):
                    found.add(".".join(scope))
                visit(child, scope)

    visit(ast.parse(path.read_text(encoding="utf-8")), ())
    return found


def test_only_the_level_seam_reads_identity_refused_candidates() -> None:
    found: dict[str, str] = {}
    for path in sorted(
        (*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py"))
    ):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(_WHOLE_MODULES):
            continue
        short = relative.removeprefix("src/mechanical_librarian/")
        for function in _readers(path):
            found[f"{short}::{function}"] = _READERS.get(
                f"{short}::{function}", "unreviewed"
            )
    assert found == _READERS


# The modules that may name a level storage table at all (S10, amendment 2).
_LEVEL_TABLES = (
    "level_proposal_sets",
    "level_decision_notes",
    "review_case_level_offers",
    "review_case_level_reconciliation",
)
_LEVEL_TABLE_MODULES = {
    "adapters/persistence/schema.py": "ddl",
    "adapters/persistence/level_reconciliation.py": "level_reconciliation",
}


def test_only_the_level_reconciliation_reads_the_level_tables() -> None:
    found: dict[str, str] = {}
    for path in sorted(
        (*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py"))
    ):
        short = (
            path.relative_to(ROOT).as_posix().removeprefix("src/mechanical_librarian/")
        )
        if any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(table in node.value for table in _LEVEL_TABLES)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        ):
            found[short] = _LEVEL_TABLE_MODULES.get(short, "unreviewed")
    assert found == _LEVEL_TABLE_MODULES


# Who reads a case's stored level offers and composes them (S10 "Offer
# binding" and "Readers", amendment 2). Retained validation and the executor's
# authority check read every stored level through one helper; the review API
# reads the current offer through the decision store; composition is the
# review case's, used by the validator and the review API's read.
_OFFER_HELPERS = frozenset(
    {"stored_level_offers", "stored_level_offer_documents", "current_level_offer"}
)
_OFFER_READERS = {
    # Retained-decision readers, the command's current offer and the review
    # API's current-offer read (`review_case_level_offer`).
    "adapters/persistence/decisions.py": "retained_validation_and_command",
    # Browse and attention projections, their cache key and the detail
    # snapshot's current offer.
    "adapters/persistence/review_projections.py": "review_projections",
    # The executor's authority check (`_authority_source`).
    "adapters/persistence/field_effect_activity.py": "executor_authority",
}
_OFFER_COMPOSERS = {
    "domain/review_case.py": "composition",
    "domain/human_decision.py": "validator",
    "application/review_web.py": "review_api",
}


def _names(path: Path, wanted: frozenset[str]) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id in wanted)
        or (isinstance(node, ast.Attribute) and node.attr in wanted)
        or (
            isinstance(node, ast.alias)
            and (node.asname or node.name).split(".")[-1] in wanted
        )
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )


def test_only_amendment_two_reads_the_stored_level_offers() -> None:
    readers: dict[str, str] = {}
    composers: dict[str, str] = {}
    for path in sorted(
        (*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").rglob("*.py"))
    ):
        short = (
            path.relative_to(ROOT).as_posix().removeprefix("src/mechanical_librarian/")
        )
        if short == "adapters/persistence/level_reconciliation.py":
            continue
        if _names(path, _OFFER_HELPERS):
            readers[short] = _OFFER_READERS.get(short, "unreviewed")
        if _names(path, frozenset({"compose_offer"})):
            composers[short] = _OFFER_COMPOSERS.get(short, "unreviewed")
    assert readers == _OFFER_READERS
    assert composers == _OFFER_COMPOSERS
