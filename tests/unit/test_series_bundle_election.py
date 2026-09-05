"""Series election retains one existing source bundle, never a union."""

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    ProviderClaim,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from tests.support.proposal_evidence import _fields, _report, _result
from tests.unit.test_rich_metadata_preview import _local_series_claims, _series_provider


def _bundle(provider, identity, memberships):
    return tuple(
        ProviderClaim.create(
            provider=provider,
            candidate=identity,
            kind=kind,
            value=value,
            state=FieldState.ABSENT if value is None else FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            raw_order=index * 2 + slot,
        )
        for index, (name, sequence) in enumerate(memberships)
        for slot, (kind, value) in enumerate(
            ((ClaimKind.SERIES, name), (ClaimKind.SERIES_SEQUENCE, sequence))
        )
    )


def test_series_elects_existing_superset_with_exact_order_values_and_own_evidence():
    claims = _bundle(
        "catalogue", "edition", (("Umbrella", "7"), ("Reading Order", "1"))
    )
    report = _report(
        fields=_fields(
            series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING)
        ),
        local_claims=_local_series_claims(sequence="1.0"),
        providers=(_series_provider("catalogue", "edition", "hardcover", claims),),
    )

    result = _result(evaluate_report(report), TargetDestination.SERIES)

    assert result.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert len(result.alternatives) == 1
    selected = result.alternatives[0]
    assert selected.value.to_data() == {
        "kind": "series",
        "value": [
            {"name": "Umbrella", "sequence": "7"},
            {"name": "Reading Order", "sequence": "1"},
        ],
    }
    assert set(selected.evidence_references) == {claim.reference for claim in claims}
    assert len(selected.correlations) == 1


@pytest.mark.parametrize(
    ("smaller", "larger", "elect"),
    (
        (
            (("Reading Order", "0.70"),),
            (("Reading Order", "0.7"), ("Umbrella", None)),
            True,
        ),
        ((("Reading Order", None),), (("Reading Order", "1"),), True),
        (
            (("Reading Order", None),),
            (("Reading Order", None), ("Umbrella", "3")),
            True,
        ),
        (
            (("Reading Order", "1"),),
            (("Reading Order", None), ("Umbrella", "3")),
            False,
        ),
        ((("Reading Order", "1"),), (("Reading Order", "2"), ("Umbrella", "3")), False),
        (
            (("Reading Order", "1e0"),),
            (("Reading Order", "1"), ("Umbrella", "3")),
            False,
        ),
        (
            (("Reading Order", "01"),),
            (("Reading Order", "1"), ("Umbrella", "3")),
            False,
        ),
        ((("Reading Order", "I"),), (("Reading Order", "1"), ("Umbrella", "3")), False),
        ((("Reading Order", "1.0"),), (("Reading Order", "1"),), False),
        ((("Reading Order", "1"),), (("Other Order", "1"),), False),
        (
            (("Reading Order", "1"), ("One", "2")),
            (("Reading Order", "1"), ("Two", "2")),
            False,
        ),
        (
            (("Reading Order", "1"), ("Umbrella", "2")),
            (("Umbrella", "2"), ("Reading Order", "1")),
            False,
        ),
    ),
)
def test_series_bundle_dominance_is_unique_complete_and_position_compatible(
    smaller, larger, elect
):
    from tests.unit.test_rich_metadata_preview import _automatic_series_report

    report = _automatic_series_report(
        _series_provider("first", "a", "hardcover", _bundle("first", "a", smaller)),
        _series_provider("second", "b", "open_library", _bundle("second", "b", larger)),
    )
    result = _result(evaluate_report(report), TargetDestination.SERIES)

    assert result.outcome is (
        ProposalOutcome.PROPOSE_CHANGE if elect else ProposalOutcome.UNRESOLVED_CHOICE
    )
    assert len(result.alternatives) == (1 if elect else 2)
    if elect:
        assert [
            (item.name, item.sequence) for item in result.alternatives[0].value.value
        ] == list(larger)
