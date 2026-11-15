"""Automatic description advice only suggests until a shipped record names it.

ADR-0099 Decision 10: automatic admission and the automatic write need a
shipped qualification record naming the advice's engine, model, instruction
digest, output-schema digest and cleanup choices. These proofs patch the
record only where they say so.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
import scripts.description_advice_labels as labels

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.application.description_advice import (
    admit_staged_description,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain import description_advice as domain
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    DescriptionAdviceCandidate,
    DescriptionAdviserIdentity,
    DescriptionPreferences,
    description_digest,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from tests.support.description_advice import (
    SYNTHETIC_DESCRIPTION_SCORE_SHA256,
    _prepared,
    qualify_description_advice,
)

_UNAVAILABLE = "description automatic admission unavailable"


def _identity(candidate: DescriptionAdviceCandidate) -> DescriptionAdviserIdentity:
    return DescriptionAdviserIdentity(
        candidate.engine,
        candidate.model,
        candidate.instruction_digest,
        candidate.schema_digest,
        candidate.preferences_digest,
    )


def _record(
    monkeypatch: pytest.MonkeyPatch, identity: DescriptionAdviserIdentity
) -> None:
    monkeypatch.setattr(
        domain,
        "QUALIFIED_DESCRIPTION_ADVISERS",
        MappingProxyType({identity: SYNTHETIC_DESCRIPTION_SCORE_SHA256}),
        raising=False,
    )


def _decisions(repository) -> int:
    return repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone()[0]


@pytest.mark.parametrize("automatic", [True, False], ids=["on", "off"])
def test_automatic_advice_is_refused_until_a_record_names_its_adviser(
    tmp_path: Path, automatic: bool
) -> None:
    repository, store, binding, candidate, _ = _prepared(tmp_path, automatic=automatic)
    case_id = binding.review_case_id
    with pytest.raises(ValueError, match=_UNAVAILABLE):
        store.admit(candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11)
    assert _decisions(repository) == 0
    advice = store.read(case_id)
    assert advice["current"] is True
    assert advice["candidate"]["candidate_id"] == candidate.candidate_id
    assert advice["selection"] is None
    # The switch on only proposes: the field offers the advice as with it off.
    description = next(
        field
        for field in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        if field["destination"] == "description"
    )
    assert description["editable"] is True
    assert description["decision_actions"] == ["keep_current", "accept"]
    assert description["description_advice"]["selection"] is None
    decision = store.admit(candidate.candidate_id, binding, "human", "b" * 64, 12)
    assert decision.origin == "human"
    repository.close()


@pytest.mark.parametrize(
    "change",
    ["exact", "other_model", "other_engine", "other_cleanup", "other_instruction"],
)
def test_a_record_names_one_exact_adviser_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    identity = _identity(candidate)
    recorded = {
        "exact": identity,
        "other_model": identity._replace(model="other-model"),
        "other_engine": identity._replace(engine="openrouter"),
        "other_cleanup": identity._replace(
            preferences_digest=description_digest(
                DescriptionPreferences(remove_praise=False).model_dump()
            )
        ),
        "other_instruction": identity._replace(instruction_sha256="f" * 64),
    }[change]
    _record(monkeypatch, recorded)
    if change == "exact":
        decision = store.admit(
            candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
        )
        assert decision.origin == "automatic_policy"
    else:
        with pytest.raises(ValueError, match=_UNAVAILABLE):
            store.admit(
                candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
            )
        assert _decisions(repository) == 0
    repository.close()


@pytest.mark.parametrize("origin", ["human", "automatic_policy"])
def test_a_stored_automatic_decision_writes_only_while_a_record_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    _record(monkeypatch, _identity(candidate))
    decision = store.admit(candidate.candidate_id, binding, origin, "a" * 64, 11)
    authority = PersistedFieldEffectAuthority(repository)
    written = {"kind": "text", "value": "Authored synthetic description."}
    assert authority.resolve(decision).decision_value == written
    # A release without the record, or a decision stored before the check.
    monkeypatch.undo()
    if origin == "automatic_policy":
        with pytest.raises(FieldEffectError, match="authority_binding"):
            authority.resolve(decision)
    else:
        assert authority.resolve(decision).decision_value == written
    repository.close()


def test_the_pipeline_decides_no_description_without_a_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _, binding, _, _ = _prepared(tmp_path)
    action = SimpleNamespace(review_case_id=binding.review_case_id, report_id=None)
    admit_staged_description(repository, action)
    assert _decisions(repository) == 0
    qualify_description_advice(monkeypatch)
    admit_staged_description(repository, action)
    assert repository._connection.execute(
        "SELECT json_extract(document,'$.origin') FROM description_decisions"
    ).fetchall() == [("automatic_policy",)]
    repository.close()


def test_a_record_copied_from_the_score_json_admits_the_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    tally = labels.Labels()
    tally.group(tuple(_identity(candidate))).right = 35
    document = labels._group_document(
        tuple(_identity(candidate)), tally.groups[tuple(_identity(candidate))]
    )
    copied = DescriptionAdviserIdentity(
        **{name: document[name] for name in DescriptionAdviserIdentity._fields}
    )
    _record(monkeypatch, copied)
    decision = store.admit(
        candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
    )
    assert decision.origin == "automatic_policy"
    repository.close()


def test_every_shipped_record_names_this_checkouts_adviser_contract() -> None:
    cleanups = {
        description_digest(
            DescriptionPreferences(
                remove_marketing=marketing,
                remove_sales_boasts=boasts,
                remove_praise=praise,
            ).model_dump()
        )
        for marketing, boasts, praise in itertools.product((False, True), repeat=3)
    }
    for identity, score in domain.QUALIFIED_DESCRIPTION_ADVISERS.items():
        # An instruction or schema change drops or requalifies the record.
        assert identity.engine in {"codex", "openrouter"}
        assert identity.model
        assert (
            identity.instruction_sha256
            == hashlib.sha256(DESCRIPTION_INSTRUCTION.encode()).hexdigest()
        )
        assert (
            identity.output_schema_sha256
            == hashlib.sha256(DESCRIPTION_OUTPUT_SCHEMA.encode()).hexdigest()
        )
        assert identity.preferences_digest in cleanups
        assert re.fullmatch(r"[0-9a-f]{64}", score)


def test_no_adviser_is_recorded_yet() -> None:
    # The first record must also change the Policy -> Curation line in
    # frontend/src/components/DescriptionAdviceSettings.tsx, which says ready
    # advice is only suggested, to name the advisers that write.
    assert dict(domain.QUALIFIED_DESCRIPTION_ADVISERS) == {}
