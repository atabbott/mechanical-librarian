from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
    qualification_manifest_is_current,
)


@pytest.mark.parametrize("index", range(8))
def test_base_produced_decisions_reopen_but_cannot_mint_authority(index: int) -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "support/automatic_decision_predecessors/before-dependency-provenance.json"
        ).read_bytes()
    )
    assert fixture["source_sha"] == "5b4b62952368d9d4a188adcd677955e8b082ce7f"
    raw = fixture["AutomaticDecision"][index]
    decision = AutomaticDecision.from_json(raw)
    assert decision.to_json() == raw
    assert not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **cast("dict[str, Any]", decision._core()), occurred_at=decision.occurred_at
        )


def test_actual_v45_author_decisions_remain_historical():
    import hashlib

    path = Path(__file__).parents[1] / "fixtures/metadata-report-v45/automatic-v45.json"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "fa6ecffeb0016c548e31ced5b32de09c390266b65b3d515725dd82337f36c174"
    )
    fixture = json.loads(path.read_bytes())
    assert fixture["source_sha"] == "f9ca341c651732dead04ed9f88948ed129753757"
    assert fixture["AutomaticDecision"]
    for raw in fixture["AutomaticDecision"]:
        decision = AutomaticDecision.from_json(raw)
        assert decision.to_json() == raw
        assert not qualification_manifest_is_current(
            decision.policy_category,
            decision.policy_option,
            decision.qualification_manifest_sha256,
        )
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **cast("dict[str, Any]", decision._core()),
                occurred_at=decision.occurred_at,
            )


def test_actual_v47_language_decision_remains_historical():
    path = (
        Path(__file__).parents[1]
        / "fixtures/language-policy-predecessor/automatic-v47.json"
    )
    raw = path.read_text().strip()
    decision = AutomaticDecision.from_json(raw)
    assert decision.to_json() == raw
    assert decision.destination == "language"
    assert not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **cast("dict[str, Any]", decision._core()), occurred_at=decision.occurred_at
        )


def test_actual_v48_genre_decision_remains_historical():
    path = (
        Path(__file__).parents[1]
        / "fixtures/genre-applicability-predecessor/automatic-v48.json"
    )
    raw = path.read_text().strip()
    decision = AutomaticDecision.from_json(raw)
    assert decision.to_json() == raw
    assert decision.destination == "genres"
    assert not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **cast("dict[str, Any]", decision._core()), occurred_at=decision.occurred_at
        )


@pytest.mark.parametrize("family", ("F19A", "F20", "F46"))
def test_revision_three_documents_decode_without_current_authority(family):
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/shadow-outcome-v9.json").read_text()
    )
    assert fixture["source_sha"] == "08ae544e20ea79037f7e4a1d5f66b482554ed1dc"
    raw = fixture["decisions"][family]
    decision = AutomaticDecision.from_json(raw)
    assert decision.to_json() == raw
    assert not qualification_manifest_is_current(
        decision.policy_category,
        decision.policy_option,
        decision.qualification_manifest_sha256,
    )
    with pytest.raises(AutomaticDecisionError):
        AutomaticDecision.create(
            **cast("dict[str, Any]", decision._core()), occurred_at=decision.occurred_at
        )


@pytest.mark.parametrize(
    "filename,source_sha",
    (
        (
            "before-incumbent-title-preservation.json",
            "148ad97f1d40524014f5aa179ca1590466adabfb",
        ),
        (
            "before-preparation-diagnostics.json",
            "a3ef9277b620c254a7e2390a276f71dfd8fa5e17",
        ),
        (
            "before-audiobook-title-admission.json",
            "15cca5c071008183ef2013154bef7042433d4772",
        ),
        (
            "before-cover-cli-confirmation.json",
            "7d9d6f7c1719b101d1cf9dfe0172b1e0a55589df",
        ),
        (
            "before-cover-retained-keep.json",
            "840171891c1cf29f0f9632da62bfc362941eadaf",
        ),
        (
            "before-cover-reviewer-refresh.json",
            "a0524f913de8282fff74ffd967c3bd1521e1b32a",
        ),
        ("before-reviewer-setup.json", "d706d4b3d761f4711e8c14df2b78fe25e5bef2ef"),
        ("before-reviewer-clear.json", "d5751c7723cc3bcb17bc2d560c9b74ae7e0f56e8"),
        ("before-reviewer-inventory.json", "6ba4beb4ea27794517693b8b034295031c9b29cd"),
        ("before-generation-cas.json", "b1b2799ba031d198ac3c49bb8099560afc984656"),
        ("before-policy-impact.json", "2fb536784394f578d38b90fc8c35bde2fa2ecf62"),
        ("before-policy-summary.json", "252f1577047c4d64ac2c18cbf4f725b36814edc5"),
        (
            "before-cover-option-binding.json",
            "9867cdafd5f42f7282980e20b607189826498c11",
        ),
    ),
)
@pytest.mark.parametrize("family", ("F19A", "F20", "F46"))
def test_policy_impact_predecessors_decode_without_current_authority(
    family, filename, source_sha
):
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "support/automatic_decision_predecessors"
            / filename
        ).read_bytes()
    )
    assert fixture["source_sha"] == source_sha
    for raw in fixture["decisions"][family]:
        decision = AutomaticDecision.from_json(raw)
        assert decision.to_json() == raw
        assert not qualification_manifest_is_current(
            decision.policy_category,
            decision.policy_option,
            decision.qualification_manifest_sha256,
        )
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **cast("dict[str, Any]", decision._core()),
                occurred_at=decision.occurred_at,
            )


@pytest.mark.parametrize("family", ("F19A", "F20", "F46"))
def test_owner_cover_base_documents_remain_history(family):
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/owner-cover-predecessor/automatic.json"
        ).read_text()
    )
    assert fixture["source_sha"] == "0b9dc854e730b329e68410f09a53f2f9103f617f"
    for raw in fixture["decisions"][family]:
        decision = AutomaticDecision.from_json(raw)
        assert decision.to_json() == raw
        assert not qualification_manifest_is_current(
            decision.policy_category,
            decision.policy_option,
            decision.qualification_manifest_sha256,
        )
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **cast("dict[str, Any]", decision._core()),
                occurred_at=decision.occurred_at,
            )


@pytest.mark.parametrize("family", ("F19A", "F20", "F46"))
def test_stable_cover_reviewer_predecessor_preserves_history_only(family):
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/stable-cover-reviewer-predecessor/automatic.json"
        ).read_text()
    )
    assert fixture["source_sha"] == "3a7d15aad11c6dc24aa52eb78a25b5d0cbf9bee8"
    for raw in fixture["decisions"][family]:
        decision = AutomaticDecision.from_json(raw)
        assert decision.to_json() == raw
        assert not qualification_manifest_is_current(
            decision.policy_category,
            decision.policy_option,
            decision.qualification_manifest_sha256,
        )
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.create(
                **cast("dict[str, Any]", decision._core()),
                occurred_at=decision.occurred_at,
            )
