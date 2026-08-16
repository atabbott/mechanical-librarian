from __future__ import annotations

from mechanical_librarian.domain.metadata_report import (
    MAX_CANDIDATES,
    MAX_CONTRIBUTIONS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_REPORT_BYTES,
    MAX_SCALAR_CHARACTERS,
    MAX_SERIES,
    MAX_TAGS,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    comparison_links,
    default_target_fields,
)


def test_exact_max_shape_fits_two_mebibytes_and_round_trips() -> None:
    """The admitted provider envelope fits before its parser is widened."""
    local_claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.CONTRIBUTOR,
            observed="c" * 280,
            canonical="c" * 280,
            excerpt="c" * 280,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            role="author",
            occurrence=index,
        )
        for index in range(1, 65)
    )
    target = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Current title",
        canonical="Current title",
        excerpt="Current title",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    local_claims = (target, *local_claims)

    def claim(
        candidate: str,
        kind: ClaimKind,
        value: str,
        raw_order: int,
        role: str | None = None,
    ) -> ProviderClaim:
        bare = ProviderClaim.create(
            provider="catalogue",
            candidate=candidate,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=(ClaimScope.EDITION if kind is ClaimKind.ISBN else ClaimScope.WORK),
            applicability="ebook" if kind is ClaimKind.ISBN else "not_applicable",
            role=role,
            raw_order=raw_order,
        )
        return ProviderClaim.create(
            provider="catalogue",
            candidate=candidate,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=bare.scope,
            applicability=bare.applicability,
            role=role,
            raw_order=raw_order,
            links=comparison_links(bare, local_claims),
        )

    candidates = []
    for number in range(MAX_CANDIDATES):
        identity = f"hardcover-edition-{number:010d}"
        claims = [
            claim(
                identity,
                ClaimKind.DESCRIPTION,
                "d" * MAX_DESCRIPTION_CHARACTERS,
                0,
            )
        ]
        claims += [
            claim(
                identity,
                ClaimKind.CONTRIBUTOR,
                "c" * 512,
                index + 1,
                "author",
            )
            for index in range(MAX_CONTRIBUTIONS)
        ]
        offset = len(claims)
        claims += [
            claim(
                identity,
                ClaimKind.SERIES,
                f"s{number:03d}-{index:03d}" + "x" * (MAX_SCALAR_CHARACTERS - 8),
                offset + index,
            )
            for index in range(MAX_SERIES)
        ]
        offset = len(claims)
        claims += [
            claim(
                identity,
                ClaimKind.TAG,
                f"t{number:03d}-{index:03d}" + "x" * (MAX_SCALAR_CHARACTERS - 8),
                offset + index,
            )
            for index in range(MAX_TAGS)
        ]
        candidates.append(
            ProviderCandidate(identity, f"work-{identity}", tuple(claims))
        )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Current title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=local_claims,
        local_reasons=("opening_matter_contributor",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                tuple(candidates),
            ),
        ),
    )
    assert len(report.to_json().encode("utf-8")) <= MAX_REPORT_BYTES
    assert MetadataReport.from_json(report.to_json()) == report
