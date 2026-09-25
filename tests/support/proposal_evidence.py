"""Shared proposal-policy evidence builders.

Nine test modules built their reports, claims, and provider
observations by importing these out of another module's tests.
Their names are unchanged so relocating them touches no test body.
"""

from mechanical_librarian.application.metadata_report import (
    ProviderLaneResult,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
)


def _fields(
    current_title: str | None = "Current title",
    **overrides: TargetField,
) -> tuple[TargetField, ...]:
    values = {
        value.destination: value for value in default_target_fields(current_title)
    }
    values.update(
        {TargetDestination[key.upper()]: value for key, value in overrides.items()}
    )
    return tuple(values[destination] for destination in TargetDestination)


def _claim(
    provider: str,
    candidate: str,
    kind: ClaimKind,
    value: str,
    *,
    scope: ClaimScope,
    order: int = 0,
    role: str | None = None,
    applicability: str = "not_applicable",
) -> ProviderClaim:
    return ProviderClaim.create(
        provider=provider,
        candidate=candidate,
        kind=kind,
        value=value,
        state=FieldState.VALID,
        scope=scope,
        applicability=applicability,
        role=role,
        raw_order=order,
    )


def _provider(
    name: str,
    candidate: str,
    claims: tuple[ProviderClaim, ...],
    evidence: tuple[ProviderEvidence, ...] = (),
    authority: str | None = None,
) -> ProviderObservation:
    return ProviderObservation(
        name,
        ProviderStatus.CANDIDATES,
        (),
        (ProviderCandidate(candidate, f"{name}-work", claims, evidence),),
        authority=authority,
    )


def consensus_local_title(value: str, *, occurrence: int = 0) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=occurrence,
    )


def consensus_provider_title(
    name: str,
    title: str,
    *,
    authority: str | None = None,
    subtitle: str | None = None,
    edition_title: str | None = None,
) -> ProviderObservation:
    claims = [
        _claim(name, f"{name}-edition", ClaimKind.TITLE, title, scope=ClaimScope.WORK)
    ]
    if subtitle is not None:
        claims.append(
            _claim(
                name,
                f"{name}-edition",
                ClaimKind.SUBTITLE,
                subtitle,
                scope=ClaimScope.EDITION,
                applicability="ebook",
            )
        )
    if edition_title is not None:
        claims.append(
            _claim(
                name,
                f"{name}-edition",
                ClaimKind.TITLE,
                edition_title,
                scope=ClaimScope.EDITION,
            )
        )
    return _provider(
        name,
        f"{name}-edition",
        tuple(claims),
        authority=name if authority is None else authority,
    )


def _publication_local_isbn() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="unknown",
        checksum="valid_isbn_13",
        applicability_basis="unlabelled",
        scope=ClaimScope.EDITION,
    )


def _explicit_local_ebook_isbn() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def _exact_edition_cover(
    provider: str = "catalogue",
    candidate: str = "edition-1",
    work_identity: str = "catalogue-work",
) -> CoverSource:
    return CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, f"{provider}:{candidate}:isbn"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(b"cover", b"preview", "image/png", 10, 20),
        provenance=CoverProvenance(
            provider,
            CoverDesignation.EXACT_EDITION,
            work_identity,
            candidate,
        ),
    )


def _publication_provider(
    name: str,
    candidate: str,
    values: tuple[str, ...],
    *,
    authority: str | None = None,
) -> ProviderObservation:
    claims = (
        _claim(
            name,
            candidate,
            ClaimKind.ISBN,
            "9780306406157",
            scope=ClaimScope.EDITION,
            applicability="unknown",
        ),
        *(
            _claim(
                name,
                candidate,
                ClaimKind.PUBLICATION_DATE,
                value,
                scope=ClaimScope.EDITION,
                order=index + 1,
            )
            for index, value in enumerate(values)
        ),
    )
    return _provider(name, candidate, claims, authority=authority)


def _manifestation_publisher_provider(
    name: str,
    candidate: str,
    kind: ClaimKind,
    value: str,
    *,
    isbn: str = "9780306406157",
    applicability: str = "ebook",
) -> ProviderObservation:
    return _provider(
        name,
        candidate,
        (
            _claim(
                name,
                candidate,
                ClaimKind.ISBN,
                isbn,
                scope=ClaimScope.EDITION,
                applicability=applicability,
            ),
            _claim(
                name,
                candidate,
                kind,
                value,
                scope=ClaimScope.EDITION,
                order=1,
            ),
        ),
    )


def _contradictory_publication_provider(
    name: str = "catalogue",
    candidate: str = "edition-1",
) -> ProviderObservation:
    return _provider(
        name,
        candidate,
        (
            _claim(
                name,
                candidate,
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="unknown",
            ),
            _claim(
                name,
                candidate,
                ClaimKind.PUBLICATION_DATE,
                "2001-02-03",
                scope=ClaimScope.EDITION,
                order=1,
            ),
        ),
        (
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION,
                f"{name}-work",
                ClaimScope.EDITION,
            ),
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                "contradictory_or_multiple",
                ClaimScope.EDITION,
                source_ordinal=1,
            ),
        ),
    )


def _report(
    *,
    title: str | None = "Current title",
    fields: tuple[TargetField, ...] | None = None,
    local_claims: tuple[LocalClaim, ...] = (),
    providers: tuple[ProviderObservation, ...] = (),
    covers: tuple[CoverSource, ...] = (),
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            _fields(title) if fields is None else fields,
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=local_claims,
        local_reasons=(
            ("opening_matter_creator",) if local_claims else ("no_opening_claims",)
        ),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        cover_sources=covers,
    )


def _target_work_candidate(
    provider: str,
    identity: str,
    work_identity: str,
) -> ProviderCandidate:
    return ProviderCandidate(
        identity,
        work_identity,
        (
            _claim(
                provider,
                identity,
                ClaimKind.TITLE,
                "Real Tigers",
                scope=ClaimScope.WORK,
            ),
            _claim(
                provider,
                identity,
                ClaimKind.CREATOR,
                "Mick Herron",
                scope=ClaimScope.WORK,
                role="author",
            ),
            _claim(
                provider,
                identity,
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="ebook",
            ),
            _claim(
                provider,
                identity,
                ClaimKind.DESCRIPTION,
                "A qualified description.",
                scope=ClaimScope.WORK,
                order=1,
            ),
            _claim(
                provider,
                identity,
                ClaimKind.GENRE,
                "Mystery",
                scope=ClaimScope.WORK,
                order=2,
            ),
            _claim(
                provider,
                identity,
                ClaimKind.TAG,
                "Espionage",
                scope=ClaimScope.WORK,
                order=3,
            ),
            _claim(
                provider,
                identity,
                ClaimKind.SERIES,
                "Slough House",
                scope=ClaimScope.WORK,
                order=4,
            ),
            _claim(
                provider,
                identity,
                ClaimKind.SERIES_SEQUENCE,
                "3",
                scope=ClaimScope.WORK,
                order=5,
            ),
        ),
    )


def _local_isbn(value, checksum):
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="ebook",
        checksum=checksum,
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def _target_work_report(
    candidates_by_provider: tuple[
        tuple[str, str, tuple[ProviderCandidate, ...], bool], ...
    ],
    *,
    target_authors: tuple[str, ...] | None = ("Mick Herron",),
    additional_local_claims: tuple[LocalClaim, ...] = (),
) -> MetadataReport:
    fields = _fields(
        "Real Tigers",
        authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
        if target_authors is None
        else TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, target_authors
        ),
        description=TargetField(
            TargetDestination.DESCRIPTION,
            TargetFieldState.MISSING,
        ),
        series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING),
        genres=TargetField(TargetDestination.GENRES, TargetFieldState.MISSING),
        tags=TargetField(TargetDestination.TAGS, TargetFieldState.MISSING),
    )
    local_isbns = (
        _local_isbn("0306406152", "valid_isbn_10"),
        _local_isbn("9780306406157", "valid_isbn_13"),
    )
    lane = metadata_discovery_plan(
        TargetSnapshot("ebook", "Real Tigers", (), fields=fields),
        local_isbns,
    ).lanes[0]
    providers = tuple(
        provider_observation(
            provider,
            (ProviderLaneResult(lane, ProviderStatus.CANDIDATES, candidates),),
            authority=authority,
        )
        if with_lane
        else ProviderObservation(
            provider,
            ProviderStatus.CANDIDATES,
            candidates=candidates,
            authority=authority,
        )
        for provider, authority, candidates, with_lane in candidates_by_provider
    )
    return _report(
        title="Real Tigers",
        fields=fields,
        local_claims=(*local_isbns, *additional_local_claims),
        providers=providers,
    )


def _local_subtitle(value: str, scope: ClaimScope) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.SUBTITLE,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=scope,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )


def _local_description(value: str, *, occurrence: int = 0) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.DESCRIPTION,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=occurrence,
    )


def _local_creator(
    value: str,
    *,
    basis: str = "package_metadata",
    occurrence: int = 0,
) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.CREATOR,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis=basis,
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role="author",
        occurrence=occurrence,
    )


def _identity_bound_alias_report(*, include_title: bool = False) -> MetadataReport:
    local_claims = tuple(
        _local_creator(value, occurrence=index)
        for index, value in enumerate(("Ada Example", "Bob Example"))
    )
    creator_claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            value,
            scope=ClaimScope.WORK,
            role="author",
            order=index,
        )
        for index, value in enumerate(("A. Example", "Ada Example", "Bob Example"))
    )
    title_claims = (
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.TITLE,
                "Candidate title",
                scope=ClaimScope.WORK,
                order=3,
            ),
        )
        if include_title
        else ()
    )
    bindings = tuple(
        ProviderIdentityBinding(
            claim.reference,
            identity,
            ClaimScope.WORK,
            "author",
            ordinal,
        )
        for claim, identity, ordinal in zip(
            creator_claims,
            ("author-1", "author-1", "author-2"),
            (0, 2, 7),
            strict=True,
        )
    )
    candidate = finalize_candidate_comparisons(
        ProviderCandidate(
            "edition-1",
            "catalogue-work",
            creator_claims + title_claims,
            (),
            bindings,
        ),
        local_claims,
    )
    return _report(
        title=None,
        fields=_fields(
            None,
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=local_claims,
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
            ),
        ),
    )


def _partially_bound_direct_author_report(
    *, include_title: bool = False
) -> MetadataReport:
    local_claims = tuple(
        _local_creator(value, occurrence=index)
        for index, value in enumerate(("Ada Example", "Bob Example"))
    )
    creator_claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            value,
            scope=ClaimScope.WORK,
            role="author",
            order=index,
        )
        for index, value in enumerate(("Ada Example", "Bob Example"))
    )
    title_claims = (
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.TITLE,
                "Candidate title",
                scope=ClaimScope.WORK,
                order=2,
            ),
        )
        if include_title
        else ()
    )
    candidate = finalize_candidate_comparisons(
        ProviderCandidate(
            "edition-1",
            "catalogue-work",
            creator_claims + title_claims,
            (),
            (
                ProviderIdentityBinding(
                    creator_claims[0].reference,
                    "author-1",
                    ClaimScope.WORK,
                    "author",
                    0,
                ),
            ),
        ),
        local_claims,
    )
    return _report(
        title=None,
        fields=_fields(
            None,
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=local_claims,
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
            ),
        ),
    )


def _result(proposal_set: ProposalSet, destination: TargetDestination):
    return next(
        value for value in proposal_set.results if value.destination is destination
    )


def _isbn_lane_observation(
    provider: str,
    candidate: ProviderCandidate,
    authority: str,
    lane,
) -> ProviderObservation:
    return provider_observation(
        provider,
        (ProviderLaneResult(lane, ProviderStatus.CANDIDATES, (candidate,)),),
        authority=authority,
    )


def _real_tigers_corroboration_report() -> MetadataReport:
    fields = _fields(
        "Real Tigers",
        authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
    )
    isbn = _publication_local_isbn()
    lane = metadata_discovery_plan(
        TargetSnapshot("ebook", "Real Tigers", (), fields=fields),
        (isbn,),
    ).lanes[0]
    author_claim = _claim(
        "open-library",
        "author-edition",
        ClaimKind.CREATOR,
        "Mick Herron",
        scope=ClaimScope.WORK,
        role="author",
        order=1,
    )
    neutral_claim = _claim(
        "hardcover",
        "neutral-edition",
        ClaimKind.CREATOR,
        "Mick Herron",
        scope=ClaimScope.WORK,
        role=None,
        order=1,
    )
    author_candidate = ProviderCandidate(
        "author-edition",
        "open-library-work",
        (
            _claim(
                "open-library",
                "author-edition",
                ClaimKind.TITLE,
                "Real Tigers",
                scope=ClaimScope.WORK,
            ),
            author_claim,
        ),
        (),
        (
            ProviderIdentityBinding(
                author_claim.reference,
                "open-library-author-OL1",
                ClaimScope.WORK,
                "author",
            ),
        ),
    )
    neutral_candidate = ProviderCandidate(
        "neutral-edition",
        "hardcover-work",
        (
            _claim(
                "hardcover",
                "neutral-edition",
                ClaimKind.TITLE,
                "Real Tigers",
                scope=ClaimScope.WORK,
            ),
            neutral_claim,
        ),
        (),
        (
            ProviderIdentityBinding(
                neutral_claim.reference,
                "hardcover-author-0000000001",
                ClaimScope.WORK,
                None,
            ),
        ),
    )
    return _report(
        title="Real Tigers",
        fields=fields,
        local_claims=(isbn,),
        providers=(
            _isbn_lane_observation("hardcover", neutral_candidate, "hardcover", lane),
            _isbn_lane_observation(
                "open-library", author_candidate, "open_library", lane
            ),
        ),
    )


def retained_decoration_report(
    component: str,
    *,
    keep: bool = False,
    supported: bool = True,
    state: TargetFieldState = TargetFieldState.MISSING,
    component_text: str | None = None,
) -> MetadataReport:
    """One plain authority and one decorated representation with its component."""
    plain = "Plain Work"
    value = "A Second Look" if component == "subtitle" else "The Sequence"
    if component_text is not None:
        value = component_text
    decorated = f"{plain}: {value}" if component == "subtitle" else f"{plain} ({value})"
    current = plain if keep else decorated
    kind = ClaimKind.SUBTITLE if component == "subtitle" else ClaimKind.SERIES
    scope = ClaimScope.EDITION if component == "subtitle" else ClaimScope.WORK
    provider = "hardcover"
    candidate = "hardcover-edition"
    claims: tuple[ProviderClaim, ...] = (
        _claim(provider, candidate, ClaimKind.TITLE, decorated, scope=ClaimScope.WORK),
        _claim(
            provider,
            candidate,
            kind,
            value,
            scope=scope,
            applicability="ebook" if component == "subtitle" else "not_applicable",
        ),
    )
    local: tuple[LocalClaim, ...] = (consensus_local_title(current),)
    if supported:
        local += (
            LocalClaim.create(
                kind=kind,
                observed=value,
                canonical=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                basis="package_metadata",
                excerpt=value,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=0,
            ),
        )
    fields = _fields(
        current,
        authors=TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada Example",)
        ),
        **{
            component: TargetField(
                TargetDestination(component),
                state,
            )
        },
    )
    source = _provider(provider, candidate, claims, authority=provider)
    if component == "series":
        local += (_local_isbn("9780306406157", "valid_isbn_13"),)
        claims += (
            _claim(
                provider,
                candidate,
                ClaimKind.CREATOR,
                "Ada Example",
                scope=ClaimScope.WORK,
                role="author",
            ),
            _claim(
                provider,
                candidate,
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="ebook",
            ),
        )
        source = _provider(provider, candidate, claims, authority=provider)
        lane = metadata_discovery_plan(
            TargetSnapshot("ebook", current, (), fields=fields), local
        ).lanes[0]
        source = provider_observation(
            provider,
            (ProviderLaneResult(lane, ProviderStatus.CANDIDATES, source.candidates),),
            authority=provider,
        )
    return _report(
        title=current,
        fields=fields,
        local_claims=local,
        providers=(consensus_provider_title("open_library", plain), source),
    )


def _image_unavailable_isbn_report(
    state: TargetFieldState = TargetFieldState.MISSING,
    *,
    review_choice: bool = False,
) -> MetadataReport:
    """Exact ebook identity with both lexical forms and unavailable image bytes."""
    from dataclasses import replace

    local = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="0306406152",
        canonical="0306406152",
        excerpt="0306406152",
        applicability="ebook",
        checksum="valid_isbn_10",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    return _report(
        title="Synthetic ISBN Journey",
        fields=_fields(
            "Synthetic ISBN Journey", isbn=TargetField(TargetDestination.ISBN, state)
        ),
        local_claims=(
            local,
            _local_subtitle("A retained subtitle choice", ClaimScope.WORK),
        )
        if review_choice
        else (local,),
        providers=(
            _provider(
                "catalogue",
                "edition-1",
                (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.ISBN,
                        "9780306406157",
                        scope=ClaimScope.EDITION,
                        applicability="ebook",
                    ),
                ),
            ),
        ),
        covers=(
            replace(
                _exact_edition_cover(),
                state=CoverSourceState.UNAVAILABLE,
                artifact=None,
            ),
        ),
    )
