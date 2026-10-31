"""Hardcover author names arrive in display order (#1497)."""

from __future__ import annotations

import httpx
import pytest

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
    _author_name,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
    metadata_discovery_plan,
    observe_provider,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    ProviderObservation,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.hardcover_evidence import hardcover_envelope
from tests.support.proposal_evidence import _fields, _report

_TITLE = "Synthetic Voyage"


def _local(
    kind: ClaimKind,
    value: str,
    role: str | None = None,
    basis: str = "package_metadata",
) -> LocalClaim:
    return LocalClaim.create(
        kind=kind,
        observed=value,
        canonical=value,
        excerpt=value,
        basis=basis,
        scope=ClaimScope.WORK,
        role=role,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )


def _observe(
    names: tuple[str, ...], local_authors: tuple[str, ...]
) -> tuple[ProviderObservation, tuple[LocalClaim, ...], tuple[TargetField, ...]]:
    claims = (
        _local(ClaimKind.TITLE, _TITLE, basis="target_metadata"),
        *(_local(ClaimKind.CREATOR, name, "author") for name in local_authors),
    )
    fields = _fields(
        _TITLE,
        authors=TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            local_authors or ("Ada Example",),
        ),
    )
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", _TITLE, (), fields=fields), claims
    )
    payload = hardcover_envelope(
        [{"id": 1, "book_id": 10}],
        [
            {
                "id": 10,
                "title": _TITLE,
                "contributions": [
                    {
                        "id": index + 1,
                        "contribution": None,
                        "author": {"id": index + 1, "name": name},
                    }
                    for index, name in enumerate(names)
                ],
            }
        ],
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        adapter = HardcoverMetadataProvider(
            "hardcover",
            client,
            65536,
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
        )
        observation = observe_provider("hardcover", "hardcover", adapter, plan)
    return observation.observation, claims, fields


def _authors(names: tuple[str, ...], local: tuple[str, ...]) -> list[str]:
    observation, _, _ = _observe(names, local)
    return [
        str(claim.value)
        for candidate in observation.candidates
        for claim in sorted(candidate.claims, key=lambda value: value.raw_order)
        if claim.kind is ClaimKind.CREATOR
    ]


@pytest.mark.parametrize(
    ("names", "local", "expected"),
    (
        (("Ada   Example",), ("Ada Example",), ["Ada Example"]),
        (("Example  Ada",), ("Ada Example",), ["Ada Example"]),
        (("Example,  Ada",), (), ["Ada Example"]),
        (
            ("Example Ada", "Writer  Other"),
            ("Ada Example", "Writer Other"),
            ["Ada Example", "Writer Other"],
        ),
    ),
    ids=("spaces", "inverted_matching_package", "comma_inverted", "each_name"),
)
def test_hardcover_payload_author_names_arrive_in_display_order(names, local, expected):
    assert expected == _authors(names, local)


@pytest.mark.parametrize(
    ("value", "local", "expected"),
    (
        ("Ada \t  Example", (), "Ada Example"),
        ("Le Guin, Ursula K.", (), "Ursula K. Le Guin"),
        ("Example Ada", (), "Example Ada"),
        ("Example Ada", ("Bea Example",), "Example Ada"),
        ("Example Ada", ("Ada Example", "Example Ada"), "Example Ada"),
        ("Ada Example", ("Ada Example",), "Ada Example"),
        ("Example, Jr.", (), "Example, Jr."),
        ("Example, Ada, Bea", (), "Example, Ada, Bea"),
        ("Example,", (), "Example,"),
        ("Le Guin Ursula K.", ("Ursula K. Le Guin",), "Ursula K. Le Guin"),
    ),
    ids=(
        "whitespace",
        "comma_initial",
        "no_other_source",
        "other_name",
        "both_orders_present",
        "already_display",
        "suffix",
        "two_commas",
        "comma_alone",
        "rotation_of_three",
    ),
)
def test_an_author_name_is_reordered_only_when_marked_or_matched(
    value, local, expected
):
    claims = tuple(_local(ClaimKind.CREATOR, name, "author") for name in local)

    assert expected == _author_name(value, claims)


def test_other_values_pass_through():
    assert _author_name(None, ()) is None
    assert _author_name(7, ()) == 7


def test_a_tidied_name_is_no_longer_a_competing_author():
    observation, claims, fields = _observe(("Example   Ada",), ("Ada Example",))
    report = _report(
        title=_TITLE, fields=fields, local_claims=claims, providers=(observation,)
    )
    result = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.AUTHORS
    )

    assert [("Ada Example",)] == [value.value.value for value in result.alternatives]
