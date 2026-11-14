"""Each versioned document's current label is written once, and pinned once.

A document version (for example `owner-settings-v6`) is defined by one constant
in the module that owns the document; product code, scripts and tests import
it. `PINS` spells each literal a second time, here only, so a changed constant
fails a test instead of passing silently. Bumping a version edits the owning
constant and its line in `PINS`, then regenerates the compatibility page
(`uv run python scripts/compatibility_versions.py --write`). Stored documents'
versions never reach the browser (#1847), so the OpenAPI document and the
browser fixtures move only when an API response version does.

The guard keeps it that way: no other Python string literal under `src/`,
`scripts/` or `tests/` may spell a pinned label. Docstrings and comments are
prose and are not scanned. Older or next versions (refusal tests, predecessor
fixtures) are not current and are not pinned. Data fixtures (JSON, SQL) and the
browser's wire fixtures are not Python and keep their literals: the wire
fixtures are compared with the real server, so a response version move shows
there too.

`PINS` covers every document whose current version is v2 or later. A v1
document joins when it first changes version.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

# (owning module, constant, current literal)
PINS: tuple[tuple[str, str, str], ...] = (
    ("mechanical_librarian.domain.proposal_set", "PROPOSAL_SCHEMA", "proposal-set-v8"),
    (
        "mechanical_librarian.domain.proposal_set",
        "POLICY_VERSION",
        "fixed-conservative-v47",
    ),
    (
        "mechanical_librarian.domain.metadata_policy",
        "POLICY_SCHEMA_VERSION",
        "metadata-policy-profile-v3",
    ),
    ("mechanical_librarian.application.configuration", "CONFIG_SCHEMA", "config-v11"),
    (
        "mechanical_librarian.domain.owner_settings",
        "OWNER_SETTINGS_SCHEMA",
        "owner-settings-v6",
    ),
    (
        "mechanical_librarian.domain.reviewer_cost",
        "REVIEWER_COST_EVIDENCE_SCHEMA",
        "reviewer-cost-evidence-v2",
    ),
    ("mechanical_librarian.domain.review_case", "REVIEW_CASE_SCHEMA", "review-case-v2"),
    (
        "mechanical_librarian.application.cover_consideration",
        "SCHEMA_VERSION",
        "cover-consideration-v9",
    ),
    (
        "mechanical_librarian.application.review_application",
        "REVIEW_PREPARATION_SCHEMA",
        "review-preparation-v2",
    ),
    (
        "mechanical_librarian.infrastructure.settings_authority",
        "REVIEW_BOOTSTRAP_SCHEMA",
        "review-bootstrap-v2",
    ),
    (
        "mechanical_librarian.application.reviewer_access",
        "REVIEWER_ACCESS_VIEW_SCHEMA",
        "reviewer-access-view-v3",
    ),
    (
        "mechanical_librarian.application.review_web",
        "REVIEW_CASE_DETAIL_SCHEMA",
        "review-case-detail-v8",
    ),
    (
        "mechanical_librarian.application.review_web",
        "REVIEW_CASE_EVIDENCE_SCHEMA",
        "review-case-evidence-v5",
    ),
    (
        "mechanical_librarian.application.review_web",
        "REVIEW_CASE_HISTORY_SCHEMA",
        "review-case-history-v4",
    ),
    (
        "mechanical_librarian.application.metadata_policy",
        "PREVIEW_OUTCOME_SAMPLE_SCHEMA",
        "preview-outcome-sample-v2",
    ),
    (
        "mechanical_librarian.interfaces.web.app",
        "METADATA_POLICY_NOTEBOOK_SCHEMA",
        "metadata-policy-notebook-v2",
    ),
    (
        "mechanical_librarian.application.review_web",
        "REVIEW_LIBRARY_SCHEMA",
        "review-library-v5",
    ),
    (
        "mechanical_librarian.application.review_web",
        "REVIEW_REPORT_DETAIL_SCHEMA",
        "review-report-detail-v6",
    ),
    (
        "mechanical_librarian.domain.shadow_review_actions",
        "ACTION_POLICY_VERSION",
        "shadow-review-action-policy-v5",
    ),
    (
        "mechanical_librarian.domain.human_decision",
        "COVER_SELECTION_AUTHORITY_SCHEMA",
        "cover-selection-authority-v3",
    ),
    (
        "mechanical_librarian.domain.cover_automatic_decision",
        "SCHEMA_VERSION",
        "cover-automatic-decision-v5",
    ),
    (
        "mechanical_librarian.application.continuous_field_effects",
        "CONTINUOUS_FIELD_ACTIVITY_SCHEMA",
        "continuous-field-activity-v3",
    ),
    (
        "mechanical_librarian.domain.reviewer_access",
        "REVIEWER_ACCESS_SCHEMA",
        "reviewer-access-v3",
    ),
    (
        "mechanical_librarian.application.cover_packet_semantics",
        "COVER_REVIEW_PACKET_SCHEMA",
        "cover-review-packet-v2",
    ),
    (
        "mechanical_librarian.application.cover_policy",
        "POLICY_SCHEMA_VERSION",
        "cover-policy-v2",
    ),
    (
        "mechanical_librarian.application.cover_policy",
        "SCHEMA_VERSION",
        "cover-policy-report-v5",
    ),
    (
        "mechanical_librarian.domain.shadow_outcome",
        "SHADOW_OUTCOME_SCHEMA",
        "shadow-outcome-set-v11",
    ),
    ("mechanical_librarian.domain.setup_state", "SETUP_STATE_SCHEMA", "setup-state-v5"),
    (
        "mechanical_librarian.domain.human_decision",
        "HUMAN_DECISION_SCHEMA",
        "human-decision-v3",
    ),
    (
        "mechanical_librarian.adapters.persistence.field_effect_validation",
        "FIELD_EFFECT_CONSENT_STATE_SCHEMA",
        "field-effect-consent-state-v2",
    ),
    (
        "mechanical_librarian.adapters.persistence.field_effect_validation",
        "FIELD_EFFECT_MANUAL_HOLD_SCHEMA",
        "field-effect-manual-hold-v2",
    ),
    (
        "mechanical_librarian.application.batch",
        "BATCH_MANIFEST_SCHEMA",
        "batch-manifest-v5",
    ),
    (
        "mechanical_librarian.application.cover_identity",
        "SCHEMA_VERSION",
        "cover-identity-report-v2",
    ),
    (
        "mechanical_librarian.domain.audio_inspection",
        "AUDIO_INSPECTION_SCHEMA",
        "audio-inspection-v6",
    ),
    (
        "mechanical_librarian.domain.field_effect",
        "FIELD_EFFECT_CONSENT_SCHEMA",
        "field-effect-consent-v2",
    ),
    (
        "mechanical_librarian.domain.metadata_report",
        "REPORT_SCHEMA",
        "metadata-report-v80",
    ),
    (
        "mechanical_librarian.domain.metadata_report",
        "COMPARISON_VERSION",
        "closed-title-creator-publication-work-identity-v6",
    ),
    (
        "mechanical_librarian.domain.epub_opening",
        "EPUB_OPENING_EXTRACTOR_VERSION",
        "epub-opening-v5",
    ),
    (
        "mechanical_librarian.domain.epub_opening",
        "EPUB_OPENING_REPORT_SCHEMA",
        "epub-opening-report-v2",
    ),
    (
        "mechanical_librarian.domain.shadow_decision",
        "DECISION_POLICY_VERSION",
        "deterministic-shadow-decision-policy-v27",
    ),
    (
        "mechanical_librarian.application.cover_observation",
        "SCHEMA_VERSION",
        "cover-observation-report-v3",
    ),
    (
        "mechanical_librarian.application.cover_observation",
        "OUTPUT_SCHEMA_VERSION",
        "cover-observer-output-v2",
    ),
    (
        "mechanical_librarian.domain.shadow_review",
        "PACKET_SCHEMA",
        "shadow-review-packet-v9",
    ),
    (
        "mechanical_librarian.domain.epub_inspection",
        "EPUB_INSPECTION_SCHEMA",
        "epub-inspection-v3",
    ),
    (
        "mechanical_librarian.domain.cover_qualification",
        "COVER_ELECTION_QUALIFICATION_SCHEMA",
        "cover-election-qualification-v2",
    ),
    (
        "mechanical_librarian.adapters.persistence.catalogue",
        "CATALOGUE_SCHEMA",
        "catalogue-v2",
    ),
    ("scripts.cold_start_report", "COLD_START_REPORT_SCHEMA", "cold-start-report-v10"),
    (
        "scripts.qualify_exact_artifact",
        "EXACT_ARTIFACT_EVIDENCE_SCHEMA",
        "exact-artifact-evidence-v4",
    ),
    ("scripts.recoverability_evaluator", "INPUT_SCHEMA", "recoverability-evaluator-v4"),
    (
        "scripts.recoverability_evaluator",
        "OUTPUT_SCHEMA",
        "recoverability-aggregate-v4",
    ),
    (
        "scripts.verify_qualification_records",
        "QUALIFICATION_CURRENT_SCHEMA",
        "qualification-current-v2",
    ),
    ("scripts.admission_rule_ledger", "LEDGER_SCHEMA", "admission-rule-ledger-v2"),
    (
        "scripts.qualify_audiobookshelf_field_effect",
        "AUDIOBOOKSHELF_FIELD_EFFECT_SCHEMA",
        "audiobookshelf-field-effect-v3",
    ),
    (
        "scripts.qualify_container_lifecycle",
        "CONTAINER_LIFECYCLE_EVIDENCE_SCHEMA",
        "container-lifecycle-evidence-v6",
    ),
    (
        "scripts.workbench_contained_reviewer",
        "CONTAINED_REVIEWER_WORKBENCH_RESULT_SCHEMA",
        "contained-reviewer-workbench-result-v2",
    ),
)


def _owner_path(module: str) -> Path:
    base = ROOT / "src" if module.startswith("mechanical_librarian.") else ROOT
    return base.joinpath(*module.split(".")).with_suffix(".py")


@pytest.mark.parametrize(
    ("module", "constant", "literal"),
    PINS,
    ids=[f"{module.rsplit('.', 1)[-1]}.{constant}" for module, constant, _ in PINS],
)
def test_current_version_is_pinned(module: str, constant: str, literal: str) -> None:
    current = getattr(importlib.import_module(module), constant)
    # A `SchemaVersion` wraps its label in `.value`; a plain constant is the label.
    assert getattr(current, "value", current) == literal


def test_pins_name_each_document_once() -> None:
    families = [literal.rsplit("-v", 1)[0] for _, _, literal in PINS]
    assert len(families) == len(set(families))
    assert all(_owner_path(module).is_file() for module, _, _ in PINS)


def _string_literals(tree: ast.AST) -> list[tuple[int, str]]:
    prose: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                prose.add(id(first.value))
    return [
        (
            node.lineno,
            node.value
            if isinstance(node.value, str)
            else node.value.decode("utf-8", "replace"),
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str | bytes)
        and id(node) not in prose
    ]


def test_no_other_python_string_spells_a_current_version() -> None:
    owners = {literal: _owner_path(module) for module, _, literal in PINS}
    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])("
        + "|".join(re.escape(label) for label in sorted(owners, key=len, reverse=True))
        + r")(?![0-9A-Za-z_])"
    )
    this = Path(__file__).resolve()
    found: list[str] = []
    for top in ("src", "scripts", "tests"):
        for path in sorted((ROOT / top).rglob("*.py")):
            if path.resolve() == this:
                continue
            text = path.read_text(encoding="utf-8")
            if not pattern.search(text):
                continue
            for line, value in _string_literals(ast.parse(text, filename=str(path))):
                for match in pattern.finditer(value):
                    if owners[match.group(1)].resolve() != path.resolve():
                        found.append(
                            f"{path.relative_to(ROOT)}:{line}: {match.group(1)}"
                        )
    assert not found, "import the owning constant instead:\n" + "\n".join(found)
