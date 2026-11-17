"""The live automatic policy's rich fields are shadow-first under a link (#1477).

`_rich_field_outcome` is where every rich destination (series, genres, tags,
description, ISBN, ASIN, subtitle fill) gets its automatic disposition, and so
where `automatic_decisions` rows come from. A projected value that cites only
records the owner's link or its anchor reached abstains there with
`owner_link_unqualified` and ADR-0092's would-admit note; the same value
without that basis is a candidate, and Ask is untouched.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_policy import DEFAULT_OPTIONS
from mechanical_librarian.domain.rich_metadata_preview import RichFieldPreview
from mechanical_librarian.domain.shadow_outcome import (
    OWNER_LINK_WOULD_ADMIT_RULE,
    _rich_field_outcome,
)

_CANDIDATE = "alternative_" + "a" * 64
_CITATION = "hardcover_" + "b" * 64
_RICH = [
    ("series", "series"),
    ("genres", "genres"),
    ("tags", "tags"),
    ("description_fill", "description"),
    ("isbn_form", "isbn"),
    ("asin", "asin"),
    ("subtitle", "subtitle"),
]


def _outcome(category: str, destination: str, *, policy: str, link_only: bool):
    preview = RichFieldPreview(
        category,
        DEFAULT_OPTIONS[category],
        "empty",
        None,
        "projected",
        ["Value"],
        None,
        source_candidate_id=_CANDIDATE,
        source_evidence_references=(_CITATION,),
    )
    return _rich_field_outcome(
        preview,
        category=category,
        destination=destination,
        policy_destination=policy,
        proposal_id="review_proposal_" + "c" * 64,
        catalogue_sha256="d" * 64,
        link_only=link_only,
    )


@pytest.mark.parametrize(("category", "destination"), _RICH)
def test_a_link_only_rich_value_is_not_decided_and_names_its_alternative(
    category: str, destination: str
) -> None:
    gated = _outcome(category, destination, policy="automatic", link_only=True)
    assert (gated.disposition, gated.reason_code) == (
        "abstention",
        "owner_link_unqualified",
    )
    assert gated.candidate_id is None
    assert gated.would_admit == _CANDIDATE
    assert gated.to_data()["would_admit"] == {
        "alternative_reference": _CANDIDATE,
        "rule": OWNER_LINK_WOULD_ADMIT_RULE,
    }
    # Control: the same projected value with an ordinary basis decides.
    control = _outcome(category, destination, policy="automatic", link_only=False)
    assert (control.disposition, control.candidate_id) == ("candidate", _CANDIDATE)
    assert control.would_admit is None
    assert control.outcome_id != gated.outcome_id


@pytest.mark.parametrize("policy", ["ask", "ignore"])
def test_the_gate_leaves_every_other_destination_as_it_was(policy: str) -> None:
    gated = _outcome("genres", "genres", policy=policy, link_only=True)
    plain = _outcome("genres", "genres", policy=policy, link_only=False)
    assert gated == plain
    assert gated.would_admit is None
