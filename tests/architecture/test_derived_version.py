"""The derived version (ADR-0108, "Ledger format and migrations").

`DERIVED_VERSION` names what preparation rebuilds: the derived DDL and seed
rows, and the evaluation constants that decide derived rows. `PINNED` names,
for each version, the digest of those and the issue whose CHANGELOG entry
declared it. A change that moves the digest fails here until it takes a new
`DERIVED_VERSION`, a new `PINNED` entry and a **Derived regeneration
required** entry, so a start of the new build asks the owner to run
`regenerate-derived`. A change of meaning that moves no digest is a manual
revision: a new version whose digest is unchanged. Review catches one that
forgets it; no test can.

Every pinned document version (tests/architecture/test_document_versions.py)
is classified exactly once: a ledger document (the ledger pin covers it), a
derived document or evaluation constant (this pin covers it), or neither (an
API response, a configuration file or a command's output). A new label must
be placed before it can land.
"""

from __future__ import annotations

import hashlib
import importlib
import json

import pytest

from mechanical_librarian.adapters.persistence import schema
from tests.architecture.test_document_versions import PINS
from tests.architecture.test_ledger_pin import declares
from tests.support import ledger_documents

# {derived version: (digest, issue whose CHANGELOG entry declares it)}
PINNED: dict[int, tuple[str, int]] = {
    1: ("af3e3fc67bc6126866413fb7b31f11fd76a58ee9346d312a1deff9a8b8dcc1d9", 2040),
}

_DOMAIN = "mechanical_librarian.domain."
_APPLICATION = "mechanical_librarian.application."
# What decides a derived row: derived documents and evaluation constants.
DERIVED: frozenset[tuple[str, str]] = frozenset(
    {
        (_DOMAIN + "proposal_set", "PROPOSAL_SCHEMA"),
        (_DOMAIN + "proposal_set", "POLICY_VERSION"),
        (_DOMAIN + "review_case", "REVIEW_CASE_SCHEMA"),
        (_DOMAIN + "review_case", "LEVEL_OFFER_SCHEMA"),
        (_DOMAIN + "level_proposal_set", "LEVEL_PROPOSAL_SET_SCHEMA"),
        (_APPLICATION + "cover_consideration", "SCHEMA_VERSION"),
        (_DOMAIN + "shadow_review_actions", "ACTION_POLICY_VERSION"),
        (_DOMAIN + "cover_automatic_decision", "SCHEMA_VERSION"),
        (_APPLICATION + "cover_packet_semantics", "COVER_REVIEW_PACKET_SCHEMA"),
        (_APPLICATION + "cover_policy", "POLICY_SCHEMA_VERSION"),
        (_APPLICATION + "cover_policy", "SCHEMA_VERSION"),
        (_DOMAIN + "shadow_outcome", "SHADOW_OUTCOME_SCHEMA"),
        (_APPLICATION + "cover_identity", "SCHEMA_VERSION"),
        (_DOMAIN + "audio_inspection", "AUDIO_INSPECTION_SCHEMA"),
        (_DOMAIN + "metadata_report", "REPORT_SCHEMA"),
        (_DOMAIN + "metadata_report", "COMPARISON_VERSION"),
        (_DOMAIN + "epub_opening", "EPUB_OPENING_EXTRACTOR_VERSION"),
        (_DOMAIN + "epub_opening", "EPUB_OPENING_REPORT_SCHEMA"),
        (_DOMAIN + "shadow_decision", "DECISION_POLICY_VERSION"),
        (_APPLICATION + "cover_observation", "SCHEMA_VERSION"),
        (_APPLICATION + "cover_observation", "OUTPUT_SCHEMA_VERSION"),
        (_DOMAIN + "shadow_review", "PACKET_SCHEMA"),
        (_DOMAIN + "epub_inspection", "EPUB_INSPECTION_SCHEMA"),
        (_DOMAIN + "cover_qualification", "COVER_ELECTION_QUALIFICATION_SCHEMA"),
    }
)
# Neither partition holds these: API responses, configuration and packaged
# files, documents sent to a reviewer, the batch catalogue's own file, and
# commands' outputs.
NEITHER: frozenset[tuple[str, str]] = frozenset(
    {
        (_APPLICATION + "configuration", "CONFIG_SCHEMA"),
        (_DOMAIN + "reviewer_cost", "REVIEWER_COST_EVIDENCE_SCHEMA"),
        (_APPLICATION + "cover_taste_contract", "COVER_TASTE_BUILD_PACKET_SCHEMA"),
        ("mechanical_librarian.interfaces.web.app", "REVIEW_TASTE_PROFILE_SCHEMA"),
        (_APPLICATION + "review_covers", "REVIEW_COVERS_SCHEMA"),
        (_APPLICATION + "review_covers", "REVIEW_COVER_CALIBRATION_SCHEMA"),
        (_APPLICATION + "review_application", "REVIEW_PREPARATION_SCHEMA"),
        (
            "mechanical_librarian.infrastructure.settings_authority",
            "REVIEW_BOOTSTRAP_SCHEMA",
        ),
        (_APPLICATION + "reviewer_access", "REVIEWER_ACCESS_VIEW_SCHEMA"),
        (_APPLICATION + "review_web", "REVIEW_CASE_DETAIL_SCHEMA"),
        (_APPLICATION + "review_web", "HUMAN_DECISION_ACK_SCHEMA"),
        (_APPLICATION + "review_web", "REVIEW_CASE_EVIDENCE_SCHEMA"),
        (_APPLICATION + "review_web", "REVIEW_CASE_HISTORY_SCHEMA"),
        (_APPLICATION + "review_web", "OWNER_LINK_HISTORY_EVENT_SCHEMA"),
        (_APPLICATION + "review_web", "OWNER_LINK_VIEW_SCHEMA"),
        (_APPLICATION + "metadata_policy", "PREVIEW_OUTCOME_SAMPLE_SCHEMA"),
        ("mechanical_librarian.interfaces.web.app", "METADATA_POLICY_NOTEBOOK_SCHEMA"),
        (
            "mechanical_librarian.interfaces.web.app",
            "METADATA_POLICY_TRANSITION_SCHEMA",
        ),
        (
            "mechanical_librarian.interfaces.web.app",
            "METADATA_POLICY_ACTIVATION_SCHEMA",
        ),
        (_APPLICATION + "review_web", "REVIEW_ATTENTION_SCHEMA"),
        (_APPLICATION + "review_web", "REVIEW_LIBRARY_SCHEMA"),
        (_APPLICATION + "review_web", "REVIEW_REPORT_DETAIL_SCHEMA"),
        (
            _APPLICATION + "continuous_field_effects",
            "CONTINUOUS_FIELD_ACTIVITY_SCHEMA",
        ),
        (_APPLICATION + "batch", "BATCH_MANIFEST_SCHEMA"),
        ("mechanical_librarian.adapters.persistence.catalogue", "CATALOGUE_SCHEMA"),
        (_APPLICATION + "operations", "OPERATIONAL_STATUS_SCHEMA"),
        ("scripts.cold_start_report", "COLD_START_REPORT_SCHEMA"),
        ("scripts.qualify_exact_artifact", "EXACT_ARTIFACT_EVIDENCE_SCHEMA"),
        ("scripts.recoverability_evaluator", "INPUT_SCHEMA"),
        ("scripts.recoverability_evaluator", "OUTPUT_SCHEMA"),
        ("scripts.verify_qualification_records", "QUALIFICATION_CURRENT_SCHEMA"),
        ("scripts.admission_rule_ledger", "LEDGER_SCHEMA"),
        (
            "scripts.qualify_audiobookshelf_field_effect",
            "AUDIOBOOKSHELF_FIELD_EFFECT_SCHEMA",
        ),
        (
            "scripts.qualify_container_lifecycle",
            "CONTAINER_LIFECYCLE_EVIDENCE_SCHEMA",
        ),
        (
            "scripts.workbench_contained_reviewer",
            "CONTAINED_REVIEWER_WORKBENCH_RESULT_SCHEMA",
        ),
    }
)


def _value(module: str, constant: str) -> str:
    current = getattr(importlib.import_module(module), constant)
    return str(getattr(current, "value", current))


def derived_digest() -> str:
    """The digest of what this build's preparation rebuilds."""
    build = schema.build_partition()
    # The browse-fact seed names the Unicode version of the Python running
    # the build, not code: a store folded under another is regenerated at
    # startup (`browse_fact_identity_is_current`), so the pin leaves it out
    # and holds under every Python the build runs on.
    seeds = tuple(
        (
            table,
            tuple(
                (row[0], "<runtime Unicode version>", *row[2:])
                if table == "browse_fact_identity"
                else row
                for row in rows
            ),
        )
        for table, rows in build.derived_seed_rows
    )
    payload = {
        "ddl": build.derived_schema_digest,
        "seeds": repr(seeds),
        "evaluation": sorted(
            [module, constant, _value(module, constant)] for module, constant in DERIVED
        ),
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def test_the_derived_version_is_the_latest_pin() -> None:
    assert schema.DERIVED_VERSION == max(PINNED)


def test_the_pin_names_this_builds_derived_partition() -> None:
    assert PINNED[schema.DERIVED_VERSION][0] == derived_digest(), (
        "what preparation rebuilds changed: give it a new DERIVED_VERSION, pin "
        "it here and declare **Derived regeneration required** (ADR-0108)"
    )


def test_every_pinned_document_version_is_classified_once() -> None:
    ledger = ledger_documents.labels()
    pinned = {(module, constant): literal for module, constant, literal in PINS}
    classes = {
        key: [
            name
            for name, member in (
                ("ledger", literal in ledger),
                ("derived", key in DERIVED),
                ("neither", key in NEITHER),
            )
            if member
        ]
        for key, literal in pinned.items()
    }
    assert {key: found for key, found in classes.items() if len(found) != 1} == {}
    assert (DERIVED | NEITHER) <= pinned.keys()


def test_the_pin_moves_with_an_evaluation_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: a policy version bump moves the derived digest."""
    before = derived_digest()
    module = importlib.import_module(_DOMAIN + "proposal_set")
    monkeypatch.setattr(module, "POLICY_VERSION", "fixed-conservative-v0")
    assert derived_digest() != before


def test_each_pinned_derived_version_is_declared_in_the_changelog() -> None:
    """The cutover's version came with **Clean rebuild required**; every later
    one declares **Derived regeneration required**."""
    for version, (_, issue) in PINNED.items():
        marker = (
            "Clean rebuild required"
            if version == 1
            else "Derived regeneration required"
        )
        assert declares(marker, issue), (version, issue)
