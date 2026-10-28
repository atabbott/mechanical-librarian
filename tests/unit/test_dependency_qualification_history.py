from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
)

ROOT = Path(__file__).parents[1]
_POLICY_IMPACT_PREDECESSORS = (
    ("before-cover-acquisition.json", "8fb3f78a8103ab9635fbbaf144f982a79dac1462"),
    (
        "before-browse-snapshot-recovery.json",
        "07bc660cb2f883602267b400220ac6738a87ccb7",
    ),
    (
        "before-incumbent-title-preservation.json",
        "148ad97f1d40524014f5aa179ca1590466adabfb",
    ),
    (
        "before-audiobook-series-strip-guard.json",
        "cd82592055ecaf9fdf727cc2f9c5b607e7f1f92c",
    ),
    ("before-preparation-diagnostics.json", "a3ef9277b620c254a7e2390a276f71dfd8fa5e17"),
    (
        "before-audiobook-title-admission.json",
        "15cca5c071008183ef2013154bef7042433d4772",
    ),
    ("before-cover-cli-confirmation.json", "7d9d6f7c1719b101d1cf9dfe0172b1e0a55589df"),
    ("before-cover-retained-keep.json", "840171891c1cf29f0f9632da62bfc362941eadaf"),
    ("before-cover-reviewer-refresh.json", "a0524f913de8282fff74ffd967c3bd1521e1b32a"),
    ("before-reviewer-setup.json", "d706d4b3d761f4711e8c14df2b78fe25e5bef2ef"),
    ("before-reviewer-clear.json", "d5751c7723cc3bcb17bc2d560c9b74ae7e0f56e8"),
    ("before-reviewer-inventory.json", "6ba4beb4ea27794517693b8b034295031c9b29cd"),
    ("before-generation-cas.json", "b1b2799ba031d198ac3c49bb8099560afc984656"),
    ("before-policy-impact.json", "2fb536784394f578d38b90fc8c35bde2fa2ecf62"),
    ("before-policy-summary.json", "252f1577047c4d64ac2c18cbf4f725b36814edc5"),
    ("before-cover-option-binding.json", "9867cdafd5f42f7282980e20b607189826498c11"),
)


def _assert_rejected(documents: list[str]) -> None:
    """A decision minted under any earlier digest no longer decodes (ADR-0087)."""
    assert documents
    for raw in documents:
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.from_json(raw)


def test_base_produced_decisions_are_rejected() -> None:
    fixture = json.loads(
        (
            ROOT
            / "support/automatic_decision_predecessors/before-dependency-provenance.json"
        ).read_bytes()
    )
    assert fixture["source_sha"] == "5b4b62952368d9d4a188adcd677955e8b082ce7f"
    assert len(fixture["AutomaticDecision"]) == 8
    _assert_rejected(fixture["AutomaticDecision"])


def test_actual_v45_author_decisions_are_rejected():
    path = ROOT / "fixtures/metadata-report-v45/automatic-v45.json"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "fa6ecffeb0016c548e31ced5b32de09c390266b65b3d515725dd82337f36c174"
    )
    fixture = json.loads(path.read_bytes())
    assert fixture["source_sha"] == "f9ca341c651732dead04ed9f88948ed129753757"
    _assert_rejected(fixture["AutomaticDecision"])


@pytest.mark.parametrize(
    "name",
    (
        "language-policy-predecessor/automatic-v47.json",
        "genre-applicability-predecessor/automatic-v48.json",
    ),
)
def test_actual_single_predecessor_decisions_are_rejected(name):
    _assert_rejected([(ROOT / "fixtures" / name).read_text().strip()])


@pytest.mark.parametrize(
    "name,source_sha",
    (
        ("fixtures/shadow-outcome-v9.json", "08ae544e20ea79037f7e4a1d5f66b482554ed1dc"),
        (
            "fixtures/owner-cover-predecessor/automatic.json",
            "0b9dc854e730b329e68410f09a53f2f9103f617f",
        ),
        (
            "fixtures/stable-cover-reviewer-predecessor/automatic.json",
            "3a7d15aad11c6dc24aa52eb78a25b5d0cbf9bee8",
        ),
        *(
            ("support/automatic_decision_predecessors/" + name, source_sha)
            for name, source_sha in _POLICY_IMPACT_PREDECESSORS
        ),
    ),
)
@pytest.mark.parametrize("family", ("F19A", "F20", "F46"))
def test_family_predecessor_decisions_are_rejected(family, name, source_sha):
    fixture = json.loads((ROOT / name).read_text())
    assert fixture["source_sha"] == source_sha
    decisions = fixture["decisions"][family]
    _assert_rejected([decisions] if type(decisions) is str else decisions)
