"""Item identity binds from the recording's embedded-tag ASIN only (#1481).

A sidecar's ASIN is evidence about the recording when its document belongs to
the item, but it is not the recording's own identifier: a folder can hold a
copy from another book.  These pin `recording_association_references` to the
embedded-tag basis.  Every value here is synthetic.
"""

from __future__ import annotations

import inspect

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldSource,
    FieldState,
    LocalClaim,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
    recording_association_references,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_ASIN = "B0SYNTH001"
_PROVIDER = "audible"


def _candidate() -> ProviderCandidate:
    identity = f"{_PROVIDER}-{_ASIN}"
    return ProviderCandidate(
        identity,
        "unresolved",
        (
            ProviderClaim.create(
                provider=_PROVIDER,
                candidate=identity,
                kind=ClaimKind.ASIN,
                value=_ASIN,
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="not_applicable",
                raw_order=0,
            ),
        ),
        (
            ProviderEvidence(
                ProviderEvidenceKind.ASIN_MARKETPLACE, "us", ClaimScope.EDITION
            ),
        ),
    )


def _local(basis: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed=_ASIN,
        canonical=_ASIN,
        excerpt=_ASIN,
        applicability="not_applicable",
        basis=basis,
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=1,
    )


def _references(basis: str) -> tuple[str, ...]:
    candidate = _candidate()
    # No author, so the target carries no work-identity context and only the
    # item-identity branch can bind.
    target = TargetFacts(
        "primary",
        "audio",
        "item-1",
        tuple(
            TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("The Synthetic Voyage")
        ),
    )
    return recording_association_references(
        (_local(basis),),
        (
            ProviderObservation(
                _PROVIDER,
                ProviderStatus.CANDIDATES,
                (),
                (candidate,),
                ProviderQueryKind.ASIN,
                "audible_catalog",
            ),
        ),
        target,
        single_track_audio_facts(),
        _PROVIDER,
        candidate,
    )


def test_an_embedded_tag_asin_binds_item_identity() -> None:
    """The control: the same shape with the file's own ASIN binds."""
    assert _references("embedded_tag")


@pytest.mark.parametrize("basis", ("sidecar", "path"))
def test_a_sidecar_or_path_asin_binds_nothing(basis: str) -> None:
    """T23 (guard): without work-identity context nothing else can bind."""
    assert _references(basis) == ()


def test_the_dead_filename_basis_is_gone() -> None:
    """T23: no claim basis is called `filename`, so naming one hid nothing."""
    assert '"filename"' not in inspect.getsource(recording_association_references)


def test_the_files_own_tags_come_before_sidecars() -> None:
    """T14 (guard): the per-basis source order puts `embedded_tag` first."""
    order = list(FieldSource)
    assert order.index(FieldSource.AUDIO_TAG) < order.index(FieldSource.AUDIO_SIDECAR)
