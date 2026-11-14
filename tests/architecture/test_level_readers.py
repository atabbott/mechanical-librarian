"""Who reads the refused WORK answers a report keeps (#1355, S11).

`ProviderObservation.identity_refused_candidates` holds products set admission
refused. They are never live evidence: only the level seam reads them, through
its set rule, and their producers carry them. Every function that reads the
attribute is listed with its role, so a new reader fails here and is reviewed
against S11 before it lands. The report module itself, which validates,
serializes and bounds them, and the provider adapters, which only copy their
lanes' records into the lane result, are allowed whole.
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
    # Producers: a lane result's own validation, a lane's ladder union and
    # the observation's aggregation.
    "application/metadata_report.py::ProviderLaneResult.__post_init__": "producer",
    "application/metadata_report.py::QualifiedLaneObservation."
    "identity_refused_candidates": "producer",
    "application/metadata_report.py::_rung_union": "producer",
    "application/metadata_report.py::_aggregate_identity_refused": "producer",
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
