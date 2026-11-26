"""Every ledger document, its decoder and encoder, and the closed vocabularies a
ledger document may hold (ADR-0108, "Ledger format and migrations").

`DOCUMENTS` maps each ledger column that holds a JSON document to how the
product reads and writes it: `decode` is the product's reader where one exists
(otherwise the JSON parse open validation starts from, which then checks the
row itself), and `encode` is the writer's own encoding of what `decode`
returned, so a stored document re-encodes byte for byte. `missing_documents`
names every ledger column the schema declares as a document (`document`,
`*_document`, or a JSON-checked copy such as `display_authors`) that is not
registered, so a new ledger document cannot be left out.

`versions` and `VOCABULARIES` are what the ledger pin hashes besides the
ledger DDL. Two consumers: tests/architecture/test_ledger_pin.py and
tests/unit/test_ledger_fixture.py.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from mechanical_librarian.adapters.persistence import regeneration, schema
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    FIELD_EFFECT_CONSENT_STATE_SCHEMA,
)
from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.domain import (
    decision_question,
    field_effect,
    human_decision,
    metadata_policy,
    owner_field_answer,
    question_check,
)
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_PROFILE_SCHEMA,
    TasteProfile,
)
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceCandidate,
)
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
)
from mechanical_librarian.domain.owner_settings import (
    OWNER_SETTINGS_SCHEMA,
    OwnerSettingsDocument,
)
from mechanical_librarian.domain.reviewer_access import (
    REVIEWER_ACCESS_SCHEMA,
    ReviewerAccessDocument,
)
from mechanical_librarian.domain.setup_state import (
    SETUP_STATE_SCHEMA,
    SetupStateDocument,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel


@dataclass(frozen=True, slots=True)
class LedgerDocument:
    decode: Callable[[str], object]
    encode: Callable[[object], str]
    # The schema version labels the document may carry.
    labels: tuple[str, ...] = ()
    # A pydantic document's models, whose JSON schemas name its settings and
    # their closed values too (see `versions`).
    models: tuple[type[BaseModel], ...] = ()


def _json(document: str) -> object:
    return json.loads(document)


def _labelled(label: str) -> Callable[[str], object]:
    """The JSON parse, refusing a document of another schema version."""

    def decode(document: str) -> object:
        value = json.loads(document)
        if type(value) is not dict or value.get("schema_version") != label:
            raise ValueError(f"not a {label} document")
        return value

    return decode


def _names(document: str) -> object:
    """A copy of a work's authors or creators: a list of names."""
    value = json.loads(document)
    if type(value) is not list or any(type(name) is not str for name in value):
        raise ValueError("not a list of names")
    return value


def _counts(document: str) -> object:
    value = json.loads(document)
    if type(value) is not dict or sorted(value) != sorted(regeneration._COUNT_KEYS):
        raise ValueError("not a regeneration's counts")
    return value


def _compact(value: object) -> str:
    """SQLite's `json()` form, which the copies are made with."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _counts_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _to_json(value: object) -> str:
    return value.to_json()


def _model_json(value: object) -> str:
    assert isinstance(value, BaseModel)
    return _canonical_json(value.model_dump())


def _canonical(value: object) -> str:
    return value.canonical_json


def _taste_version(document: str) -> object:
    """A taste profile version: its rules through the product's reader
    (`TasteProfile.from_rules`), beside the rest of the version."""
    value = _labelled(COVER_TASTE_PROFILE_SCHEMA)(document)
    assert type(value) is dict
    return (
        {key: item for key, item in value.items() if key != "rules"},
        TasteProfile.from_rules(value["rules"]),
    )


def _taste_version_json(value: object) -> str:
    """The version as its writer stores it (`canonical_json`), with the rules
    the profile gives back."""
    assert type(value) is tuple
    rest, profile = value
    assert type(rest) is dict and type(profile) is TasteProfile
    return canonical_json({**rest, "rules": profile.rules_data()})


def _settings(document: str) -> object:
    """The owner's settings, or first run's state before it is promoted."""
    if json.loads(document).get("schema_version") == SETUP_STATE_SCHEMA:
        return SetupStateDocument.model_validate_json(document)
    return OwnerSettingsDocument.model_validate_json(document)


_EFFECT = field_effect.canonical_json
_NAMES = LedgerDocument(_names, _compact)

DOCUMENTS: dict[tuple[str, str], LedgerDocument] = {
    ("automatic_safety_state", "category_suspensions_document"): LedgerDocument(
        _json, _canonical_json
    ),
    ("cover_taste_profile_versions", "document"): LedgerDocument(
        _taste_version, _taste_version_json, (COVER_TASTE_PROFILE_SCHEMA,)
    ),
    ("derived_regenerations", "counts_document"): LedgerDocument(_counts, _counts_json),
    ("field_effect_actions", "document"): LedgerDocument(
        _labelled("field-effect-action-v1"), _EFFECT, ("field-effect-action-v1",)
    ),
    ("field_effect_authorities", "document"): LedgerDocument(
        _labelled("field-effect-authority-state-v1"),
        _EFFECT,
        ("field-effect-authority-state-v1",),
    ),
    ("field_effect_cleanups", "document"): LedgerDocument(
        _labelled("field-effect-cleanup-v1"), _EFFECT, ("field-effect-cleanup-v1",)
    ),
    ("field_effect_consents", "document"): LedgerDocument(
        _labelled(FIELD_EFFECT_CONSENT_STATE_SCHEMA),
        _EFFECT,
        (FIELD_EFFECT_CONSENT_STATE_SCHEMA, field_effect.FIELD_EFFECT_CONSENT_SCHEMA),
    ),
    ("field_effect_events", "document"): LedgerDocument(
        _labelled(field_effect.FIELD_EFFECT_EVENT_SCHEMA),
        _EFFECT,
        (field_effect.FIELD_EFFECT_EVENT_SCHEMA,),
    ),
    ("field_effect_no_changes", "display_authors"): _NAMES,
    ("field_effect_no_changes", "kept_value_document"): LedgerDocument(_json, _compact),
    ("field_effect_suspensions", "document"): LedgerDocument(
        _labelled("field-effect-suspension-v1"),
        _EFFECT,
        ("field-effect-suspension-v1",),
    ),
    ("field_effects", "display_authors"): _NAMES,
    ("field_effects", "document"): LedgerDocument(
        FieldEffectOperation.from_json,
        lambda value: _EFFECT(value.to_data()),
        (
            field_effect.FIELD_EFFECT_OPERATION_SCHEMA,
            field_effect.FIELD_EFFECT_PLAN_SCHEMA,
        ),
    ),
    ("human_decision_events", "document"): LedgerDocument(
        _labelled(human_decision.HUMAN_DECISION_EVENT_SCHEMA),
        _canonical_json,
        (human_decision.HUMAN_DECISION_EVENT_SCHEMA,),
    ),
    ("human_decisions", "display_authors"): _NAMES,
    ("human_decisions", "document"): LedgerDocument(
        HumanDecision.from_json,
        _to_json,
        (
            human_decision.HUMAN_DECISION_SCHEMA,
            decision_question.DECISION_QUESTION_SCHEMA,
        ),
    ),
    ("human_decisions", "selected_value_document"): LedgerDocument(
        _json, _canonical_json
    ),
    ("human_decisions", "target_field_document"): LedgerDocument(
        _json, _canonical_json
    ),
    ("metadata_policy_drafts", "document"): LedgerDocument(
        lambda document: MetadataPolicyProfile.from_data(json.loads(document)),
        _canonical,
        (metadata_policy.POLICY_SCHEMA_VERSION,),
    ),
    ("metadata_policy_resolutions", "document"): LedgerDocument(
        lambda document: ResolvedMetadataPolicy.from_data(json.loads(document)),
        _canonical,
        (metadata_policy.POLICY_SCHEMA_VERSION,),
    ),
    ("owner_authored_descriptions", "candidate_document"): LedgerDocument(
        DescriptionAdviceCandidate.from_json,
        _to_json,
        ("description-advice-candidate-v1",),
        (DescriptionAdviceCandidate,),
    ),
    ("owner_authored_descriptions", "display_authors"): _NAMES,
    ("owner_authored_descriptions", "document"): LedgerDocument(
        AuthoredDescriptionDecision.from_json,
        _to_json,
        ("authored-description-decision-v1",),
        (AuthoredDescriptionDecision,),
    ),
    ("owner_cover_selections", "document"): LedgerDocument(
        _labelled(human_decision.COVER_SELECTION_AUTHORITY_SCHEMA),
        _canonical_json,
        (human_decision.COVER_SELECTION_AUTHORITY_SCHEMA,),
    ),
    ("owner_field_answers", "value_document"): LedgerDocument(_json, _canonical_json),
    ("owner_links", "display_creators"): _NAMES,
    ("owner_settings", "document"): LedgerDocument(
        _settings,
        _model_json,
        (OWNER_SETTINGS_SCHEMA, SETUP_STATE_SCHEMA),
        (OwnerSettingsDocument, SetupStateDocument),
    ),
    ("reviewer_access", "document"): LedgerDocument(
        ReviewerAccessDocument.model_validate_json,
        _model_json,
        (REVIEWER_ACCESS_SCHEMA,),
        (ReviewerAccessDocument,),
    ),
}

# The closed vocabularies a ledger document may hold, besides those the
# ledger DDL spells in its CHECKs (retained-cover reasons, plan states, the
# carry outcomes' column) and those a registered pydantic document's schema
# names (setting enumerations, reviewer selections).
VOCABULARIES: dict[str, object] = {
    "OPTION_CATALOGUE": metadata_policy.OPTION_CATALOGUE,
    "DESTINATIONS": metadata_policy.DESTINATIONS,
    "FIELD_EFFECT_STATES": sorted(field_effect._STATES),
    "FIELD_EFFECT_FAILURES": sorted(field_effect.FIELD_EFFECT_FAILURES, key=repr),
    "DECISION_OUTCOMES": sorted(human_decision._OUTCOMES),
    "DECISION_APPLICATIONS": human_decision.APPLICATIONS,
    "DECISION_CAUSES": sorted(human_decision.DECISION_CAUSES),
    "OFFER_CHANGED_REASONS": sorted(human_decision.OFFER_CHANGED_REASONS),
    "HUMAN_DECISION_EVENT_KINDS": human_decision.HUMAN_DECISION_EVENT_KINDS,
    "WORK_IDENTITY_LEVELS": [level.value for level in WorkIdentityLevel],
    "OWNER_FIELD_EVENT_KINDS": sorted(owner_field_answer.OWNER_FIELD_EVENT_KINDS),
    "OWNER_FIELD_EVENT_SCHEMA": owner_field_answer.OWNER_FIELD_EVENT_SCHEMA,
    "CARRY_OUTCOMES": question_check.CARRY_OUTCOMES,
    "PROVISIONAL_CARRY_OUTCOMES": sorted(question_check.PROVISIONAL_CARRY_OUTCOMES),
}


def versions() -> dict[str, object]:
    """Each registered document's schema version labels and its models' JSON
    schemas, by `table.column`."""
    return {
        f"{table}.{column}": {
            "labels": list(document.labels),
            "models": [model.model_json_schema() for model in document.models],
        }
        for (table, column), document in sorted(DOCUMENTS.items())
    }


def labels() -> frozenset[str]:
    """Every schema version label a ledger document carries or names."""
    return frozenset(
        label for document in DOCUMENTS.values() for label in document.labels
    ) | {owner_field_answer.OWNER_FIELD_EVENT_SCHEMA}


def missing_documents(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every ledger document column of the schema at ``connection`` that
    `DOCUMENTS` does not register."""
    found: list[tuple[str, str]] = []
    for table in sorted(schema.LEDGER_TABLES):
        (sql,) = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        checked = set(re.findall(r"json_valid\(\s*\"?(\w+)", sql))
        for _, column, *_ in connection.execute(f'PRAGMA table_info("{table}")'):
            if (
                column == "document"
                or column.endswith("_document")
                or column in checked
            ) and (table, column) not in DOCUMENTS:
                found.append((table, column))
    return found
