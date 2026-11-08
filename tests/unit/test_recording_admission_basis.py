"""How the matched recording was admitted when its title differs (#1723).

Each lane that kept a product as the corroborated recording records the route
that admitted it, and the report replays that value against the lane's own
plan. These cases prove the value end to end at the plan and report seams:
what each lane records, how one candidate's lanes derive its basis, what the
report refuses, and what the read model tells the owner. Every title, name and
identifier here is synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    _lane_lookup,
)
from mechanical_librarian.application import metadata_report as app
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    ProviderLaneResult,
    TargetSnapshot,
    audiobook_carried_plan,
    audiobook_discovery_plan,
    provider_observation,
    qualify_provider_lane,
)
from mechanical_librarian.domain import metadata_report as dom
from mechanical_librarian.domain.audiobook_corroboration import (
    carried_asin_precedence,
    recording_admission_basis,
    recording_identity_basis,
)
from mechanical_librarian.domain.metadata_report import (
    ALTERNATIVE_ADMISSION_BASES,
    RECORDING_ADMISSION_BASES,
    AudioDiscoveryTitle,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    CompanionEbook,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    WorkDispatchLineage,
    candidate_admission_basis,
    companion_stated_isbn,
    default_target_fields,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import audiobook_review_scope
from mechanical_librarian.domain.work_identity import work_query_ladder
from tests.support.audiobook_reports import recorded_dispatch
from tests.support.metadata_report_doubles import single_track_audio_facts
from tests.support.report_schema_version import CURRENT_REPORT_SCHEMA
from tests.unit import test_companion_epub_matching as companion_fixture

_TITLE = "Ember Tide Protocol"
_AUTHOR = "Corin Halloway"
_NARRATOR = "Ilsa Brandt"
_LEAF = "The Leaf Title"
_TAG = "The Tag Title"
_CARRIED = "B0SYNE7P1K"
_SIBLING = "B0SIBLING1"
_RUNTIME = "58244.201"
_TIGHT = "58200.000"
_LOOSE = "57000.000"
_DIGEST = "b" * 64

# The companion scenario: an EPUB beside the audiobook whose ISBN names a
# Hardcover book listing one audio edition counted against this file.
_C_TITLE = "Asserted Title"
_C_AUTHOR = "Ada Author"
_C_NARRATOR = "Nia Narrator"
_C_SECONDS = companion_fixture._LOCAL_SECONDS
_LINKED = companion_fixture._ASIN
_ISBN = companion_fixture._ISBN


# -- builders ------------------------------------------------------------------


def _local(
    kind: ClaimKind,
    value: str,
    occurrence: int,
    *,
    basis: str = "embedded_tag",
    role: str | None = None,
    scope: ClaimScope | None = None,
) -> LocalClaim:
    return LocalClaim.create(
        kind=kind,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis=basis,
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role=role,
        occurrence=occurrence,
        scope=scope,
    )


def _claims(
    *,
    title: str | None = _TITLE,
    carried: str | None = _CARRIED,
    runtime: str = _RUNTIME,
    narrator: str | None = _NARRATOR,
) -> tuple[LocalClaim, ...]:
    return (
        *(
            ()
            if title is None
            else (
                _local(
                    ClaimKind.TITLE,
                    title,
                    0,
                    basis="target_metadata",
                    scope=ClaimScope.WORK,
                ),
            )
        ),
        *(
            ()
            if carried is None
            else (
                _local(
                    ClaimKind.ASIN,
                    carried,
                    0,
                    basis="prior_admission",
                    scope=ClaimScope.EDITION,
                ),
            )
        ),
        _local(ClaimKind.DURATION, runtime, 1),
        *(
            ()
            if narrator is None
            else (_local(ClaimKind.CONTRIBUTOR, narrator, 2, role="narrator"),)
        ),
    )


def _fields(
    title: str | None = _TITLE,
    authors: tuple[str, ...] = (_AUTHOR,),
    narrators: tuple[str, ...] = (),
    asin: str | None = None,
) -> tuple[TargetField, ...]:
    overrides = {
        TargetDestination.AUTHORS: (
            TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, authors)
            if authors
            else TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
        )
    }
    if narrators:
        # The asserted narrators the corroborated rule hears (#1307).
        overrides[TargetDestination.NARRATORS] = TargetField(
            TargetDestination.NARRATORS, TargetFieldState.VALUE, narrators
        )
    if asin is not None:
        overrides[TargetDestination.ASIN] = TargetField(
            TargetDestination.ASIN, TargetFieldState.VALUE, asin
        )
    return tuple(
        overrides.get(field.destination, field)
        for field in default_target_fields(title)
    )


def _audio(*titles: tuple[str, str]) -> AudioFacts:
    return replace(
        single_track_audio_facts(_DIGEST),
        discovery_titles=tuple(AudioDiscoveryTitle(*value) for value in titles),
    )


def _plan(
    claims: tuple[LocalClaim, ...],
    audio: AudioFacts,
    *,
    title: str | None = _TITLE,
    authors: tuple[str, ...] = (_AUTHOR,),
    narrators: tuple[str, ...] = (),
    asin: str | None = None,
    companion: CompanionEbook | None = None,
) -> MetadataDiscoveryPlan:
    return audiobook_discovery_plan(
        TargetSnapshot(
            "audiobook",
            title,
            (),
            fields=_fields(title, authors, narrators, asin),
        ),
        claims,
        audio=audio,
        companion=companion,
    )


def _candidate(
    provider: str,
    asins: tuple[str, ...],
    *,
    title: str | None = _TITLE,
    subtitle: str | None = None,
    author: str = _AUTHOR,
    narrator: str | None = _NARRATOR,
    duration: str | None = _TIGHT,
    marketplace: bool = True,
) -> ProviderCandidate:
    """One product as the audiobook adapters state it."""
    identity = f"{provider}-{asins[0]}"
    specifications: list[tuple[ClaimKind, str, ClaimScope, str | None]] = [
        (ClaimKind.ASIN, asin, ClaimScope.EDITION, None) for asin in asins
    ]
    if title is not None:
        specifications.append((ClaimKind.TITLE, title, ClaimScope.WORK, None))
        if subtitle is not None:
            specifications.append((ClaimKind.SUBTITLE, subtitle, ClaimScope.WORK, None))
        specifications.append((ClaimKind.CREATOR, author, ClaimScope.WORK, "author"))
    if narrator is not None:
        specifications.append(
            (ClaimKind.CONTRIBUTOR, narrator, ClaimScope.WORK, "narrator")
        )
    if duration is not None:
        specifications.append((ClaimKind.DURATION, duration, ClaimScope.EDITION, None))
    evidence = [
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION, "unresolved", ClaimScope.EDITION
        )
    ]
    if marketplace:
        evidence.append(
            ProviderEvidence(
                ProviderEvidenceKind.ASIN_MARKETPLACE, "us", ClaimScope.EDITION
            )
        )
    return ProviderCandidate(
        identity,
        "unresolved",
        tuple(
            ProviderClaim.create(
                provider=provider,
                candidate=identity,
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=scope,
                applicability="not_applicable",
                role=role,
                raw_order=order,
            )
            for order, (kind, value, scope, role) in enumerate(specifications)
        ),
        tuple(sorted(evidence, key=lambda e: (e.scope.value, e.kind.value, e.value))),
    )


def _answer(
    plan: MetadataDiscoveryPlan,
    lane: MetadataDiscoveryLane,
    provider: str,
    authority: str,
    candidates: tuple[ProviderCandidate, ...] = (),
) -> ProviderLaneResult:
    """Answer one lane and qualify it exactly as the adapters do."""
    if not candidates:
        return ProviderLaneResult(lane, ProviderStatus.NO_MATCH)
    work = lane.kind is ProviderQueryKind.WORK
    qualified = qualify_provider_lane(
        ProviderObservation(
            provider,
            ProviderStatus.CANDIDATES,
            (),
            tuple(
                finalize_candidate_comparisons(value, plan.local_claims, provider)
                for value in candidates
            ),
            lane.kind,
            authority,
            dispatch_lineage=(
                WorkDispatchLineage.from_query(
                    work_query_ladder(lane.title or "", lane.authors)[0]
                )
                if work
                else None
            ),
        ),
        _lane_lookup(lane, plan),
    )
    observation = qualified.observation
    return ProviderLaneResult(
        lane,
        observation.status,
        observation.candidates,
        dispatch_lineage=observation.dispatch_lineage,
        transformation_lineage=observation.transformation_lineage,
        recording_lookup_candidates=observation.recording_lookup_candidates,
        refused_products=observation.refused_products,
        admission_bases=qualified.admission_bases,
    )


def _observe(
    plan: MetadataDiscoveryPlan,
    *,
    provider: str = "audible",
    authority: str = "audible_catalog",
    work: dict[str | None, tuple[ProviderCandidate, ...]] | None = None,
    asin: dict[str, tuple[ProviderCandidate, ...]] | None = None,
) -> ProviderObservation:
    """One provider's answer to every lane of `plan` it serves.

    WORK lanes are named by their title basis (`None` for the asserted lane)
    and ASIN lanes by their ASIN; a lane named in neither answers no match.
    """
    work = work or {}
    asin = asin or {}
    results = tuple(
        _answer(
            plan,
            lane,
            provider,
            authority,
            (
                work.get(lane.title_basis, ())
                if lane.kind is ProviderQueryKind.WORK
                else asin.get(lane.canonical_asin or "", ())
            ),
        )
        for lane in plan.lanes
        if lane.kind in {ProviderQueryKind.WORK, ProviderQueryKind.ASIN}
    )
    return provider_observation(provider, results, authority=authority)


def _report(
    claims: tuple[LocalClaim, ...],
    observations: tuple[ProviderObservation, ...],
    audio: AudioFacts,
    *,
    title: str | None = _TITLE,
    authors: tuple[str, ...] = (_AUTHOR,),
    narrators: tuple[str, ...] = (),
    asin: str | None = None,
    companion: CompanionEbook | None = None,
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item-1", _fields(title, authors, narrators, asin)
        ),
        file=FileFacts(0, "a" * 64, _DIGEST, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=tuple(
            sorted(
                (recorded_dispatch(value) for value in observations),
                key=lambda value: value.provider,
            )
        ),
        audio=audio,
        companion=companion,
    )


def _carried(
    product: ProviderCandidate,
    audio: AudioFacts,
    *,
    search: dict[str | None, tuple[ProviderCandidate, ...]] | None = None,
    title: str = _TITLE,
    narrators: tuple[str, ...] = (),
    rule: bool = True,
) -> MetadataReport:
    """Audible's carried lookup of `product`, beside its WORK lanes' answers.

    `narrators` are the target's asserted narrators, which the corroborated
    rule hears; `rule` is whether a retry left that rule on (#1307).
    """
    claims = _claims(title=title)
    plan = audiobook_carried_plan(
        _plan(claims, audio, title=title, narrators=narrators)
    )
    plan = replace(plan, corroborated_rule=rule)
    return _report(
        claims,
        (_observe(plan, work=search, asin={_CARRIED: (product,)}),),
        audio,
        title=title,
        narrators=narrators,
    )


def _searched(
    product: ProviderCandidate, audio: AudioFacts, basis: str | None
) -> MetadataReport:
    """Audible's search, where the lane of `basis` keeps `product`."""
    claims = _claims(carried=None)
    plan = _plan(claims, audio)
    return _report(claims, (_observe(plan, work={basis: (product,)}),), audio)


def _bases(report: MetadataReport) -> dict[tuple[str, str], str | None]:
    """Every lane value, by lane kind and title basis or ASIN."""
    values: dict[tuple[str, str], str | None] = {}
    for observation in report.providers:
        for lane in observation.lanes:
            for member in lane.candidates:
                key = (lane.kind.value, member.edition_identity)
                values[key] = member.admission_basis
    return values


def _derived(report: MetadataReport) -> dict[str, str | None]:
    return {
        candidate.identity: candidate_admission_basis(observation, candidate)
        for observation in report.providers
        for candidate in observation.candidates
    }


def _replayed(report: MetadataReport) -> MetadataReport:
    restored = MetadataReport.from_json(report.to_json())
    assert restored == report
    return restored


def _assert_scope_invariant(report: MetadataReport) -> None:
    """The note is said only where the chapter note is not (settlement 5)."""
    if recording_admission_basis(report) is not None:
        assert audiobook_review_scope(report) == "recording"
        assert recording_identity_basis(report) is None


def _c_claims(*, title: bool = True) -> tuple[LocalClaim, ...]:
    return (
        *((companion_fixture._target_title_claim(_C_TITLE),) if title else ()),
        companion_fixture._duration_claim(),
        companion_fixture._narrator_claim(_C_NARRATOR),
    )


def _c_product(
    *,
    title: str = "A Mangled Folder Title",
    creator: str = "Zed Stranger",
    provider: str = "enrichment",
) -> ProviderCandidate:
    """The linked edition's product as a provider states it, title and all."""
    return _candidate(
        provider,
        (_LINKED,),
        title=title,
        author=creator,
        narrator=_C_NARRATOR,
        duration=f"{_C_SECONDS}.000",
    )


def _companion_report(
    product: ProviderCandidate | None = None,
    *,
    linked: bool = True,
    target: bool = True,
    work: dict[str | None, tuple[ProviderCandidate, ...]] | None = None,
    companion: CompanionEbook | None = None,
    audio: AudioFacts | None = None,
) -> MetadataReport:
    """An audiobook with a companion EPUB, and Audible's answers.

    Hardcover's book for the EPUB's ISBN lists `_LINKED` as an audio edition
    counted against this file when `linked`, and the feed asks Audnexus for
    that product. Without it the EPUB states no ISBN and the target asserts
    the ASIN instead, so
    the same lane is asked with no ISBN to link by. A target
    with no title or authors is searched with the companion's own work.
    """
    companion = companion or companion_fixture._companion(
        isbns=(_ISBN,) if linked else ()
    )
    audio = audio or single_track_audio_facts(_DIGEST)
    title, authors = (_C_TITLE, (_C_AUTHOR,)) if target else (None, ())
    claims = _c_claims(title=target)
    if not linked:
        claims = (
            *claims,
            _local(
                ClaimKind.ASIN,
                _LINKED,
                0,
                basis="target_metadata",
                scope=ClaimScope.EDITION,
            ),
        )
    edition = companion_fixture._edition(1, _C_SECONDS, _C_NARRATOR)
    hardcover = (companion_fixture._hardcover(companion, (edition,)),) if linked else ()
    fields = _fields(title, authors)
    if not linked:
        fields = tuple(
            TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, _LINKED)
            if field.destination is TargetDestination.ASIN
            else field
            for field in fields
        )
    snapshot = TargetSnapshot("audiobook", title, (), fields=fields)
    plan = audiobook_discovery_plan(snapshot, claims, audio=audio, companion=companion)
    feed = app.audiobook_asin_feed_plan(snapshot, plan, hardcover)
    assert feed is not None
    audible = _observe(plan, work=work)
    enrichment = _observe(
        feed,
        provider="enrichment",
        authority="audnexus",
        asin={_LINKED: (product or _c_product(),)},
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, _DIGEST, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=tuple(
            sorted(
                (*hardcover, recorded_dispatch(audible), recorded_dispatch(enrichment)),
                key=lambda value: value.provider,
            )
        ),
        audio=audio,
        companion=companion,
    )


def _marketplace(candidate: ProviderCandidate, region: str) -> ProviderCandidate:
    """The same product as resolved in another marketplace (#1316)."""
    return replace(
        candidate,
        evidence=tuple(
            sorted(
                (
                    *(
                        value
                        for value in candidate.evidence
                        if value.kind is not ProviderEvidenceKind.ASIN_MARKETPLACE
                    ),
                    ProviderEvidence(
                        ProviderEvidenceKind.ASIN_MARKETPLACE,
                        region,
                        ClaimScope.EDITION,
                    ),
                ),
                key=lambda e: (e.scope.value, e.kind.value, e.value),
            )
        ),
    )


_REGION = "B0REGION01"


def _regional_report() -> MetadataReport:
    """One recording reached through two regional ASINs by two routes.

    The carried edition `_REGION` and the companion-linked `_LINKED` are two
    marketplaces' ASINs of one product. Its carried lookup admits it against
    the companion's title; the linked lookup admits it as the book's listed
    audio edition.
    """
    companion = companion_fixture._companion()
    claims = (
        *_c_claims(),
        _local(
            ClaimKind.ASIN,
            _REGION,
            0,
            basis="prior_admission",
            scope=ClaimScope.EDITION,
        ),
    )
    fields = _fields(_C_TITLE, (_C_AUTHOR,))
    snapshot = TargetSnapshot("audiobook", _C_TITLE, (), fields=fields)
    audio = single_track_audio_facts(_DIGEST)
    plan = audiobook_discovery_plan(snapshot, claims, audio=audio, companion=companion)
    hardcover = companion_fixture._hardcover(
        companion, (companion_fixture._edition(1, _C_SECONDS, _C_NARRATOR),)
    )
    feed = app.audiobook_asin_feed_plan(snapshot, plan, (hardcover,))
    assert feed is not None
    assert {_LINKED, _REGION} == {lane.canonical_asin for lane in feed.lanes}
    product = _candidate(
        "enrichment",
        (_LINKED, _REGION),
        title="Companion Title",
        author=_C_AUTHOR,
        narrator=_C_NARRATOR,
        duration=f"{_C_SECONDS}.000",
    )
    enrichment = provider_observation(
        "enrichment",
        tuple(
            _answer(
                feed,
                lane,
                "enrichment",
                "audnexus",
                (
                    _marketplace(
                        product, "us" if lane.canonical_asin == _LINKED else "uk"
                    ),
                ),
            )
            for lane in feed.lanes
        ),
        authority="audnexus",
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, _DIGEST, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=tuple(
            sorted(
                (hardcover, recorded_dispatch(enrichment)),
                key=lambda value: value.provider,
            )
        ),
        audio=audio,
        companion=companion,
    )


def _asserted_leaf_report() -> MetadataReport:
    """The target asserts the ASIN; the path-leaf WORK lane keeps its product.

    The asserted ASIN lane is not the carried one, so no discovery title
    admits there and it holds the product's stub (A1 path B).
    """
    audio = _audio(("path_leaf", _LEAF))
    claims = (
        *_claims(carried=None),
        _local(
            ClaimKind.ASIN,
            _CARRIED,
            0,
            basis="target_metadata",
            scope=ClaimScope.EDITION,
        ),
    )
    plan = _plan(claims, audio, asin=_CARRIED)
    feed = app.audiobook_asin_feed_plan(
        TargetSnapshot("audiobook", _TITLE, (), fields=_fields(asin=_CARRIED)),
        plan,
        (),
    )
    assert feed is not None
    product = _candidate("audible", (_CARRIED,), title=_LEAF)
    results = [
        _answer(
            plan,
            lane,
            "audible",
            "audible_catalog",
            (product,) if lane.title_basis == "path_leaf" else (),
        )
        for lane in plan.lanes
    ]
    results += [
        _answer(feed, lane, "audible", "audible_catalog", (product,))
        for lane in feed.lanes
    ]
    observation = provider_observation(
        "audible",
        tuple(sorted(results, key=lambda value: value.lane.identity)),
        authority="audible_catalog",
    )
    return _report(claims, (observation,), audio, asin=_CARRIED)


# -- building: each route on its own lane --------------------------------------

_LEAF_AUDIO = _audio(("path_leaf", _LEAF))
_TAG_AUDIO = _audio(("path_leaf", _TITLE), ("title_tag", _TAG))


@pytest.mark.parametrize(
    ("audio", "title", "basis"),
    [
        (_LEAF_AUDIO, _LEAF, "path_leaf"),
        # The leaf repeats the target's title, so the lane asks the tag title.
        (_TAG_AUDIO, _TAG, "title_tag"),
    ],
)
def test_an_alternative_work_lane_records_its_title_basis(
    audio: AudioFacts, title: str, basis: str
) -> None:
    report = _searched(_candidate("audible", (_CARRIED,), title=title), audio, basis)

    assert {("work", f"audible-{_CARRIED}"): basis} == _bases(report)
    assert {f"audible-{_CARRIED}": basis} == _derived(report)
    assert recording_admission_basis(_replayed(report)) == {
        "basis": basis,
        "searched": {"title": title, "authors": None, "isbn": None},
        "provider": "audible",
        "candidate": f"audible-{_CARRIED}",
    }
    _assert_scope_invariant(report)


def test_the_asserted_work_lane_records_target_and_says_nothing() -> None:
    report = _searched(_candidate("audible", (_CARRIED,)), _LEAF_AUDIO, None)

    assert {("work", f"audible-{_CARRIED}"): "target"} == _bases(report)
    assert recording_admission_basis(report) is None


@pytest.mark.parametrize(
    ("target", "basis", "searched"),
    [
        (
            True,
            "companion_title",
            {"title": "Companion Title", "authors": None, "isbn": None},
        ),
        (
            False,
            "companion_work",
            {"title": "Companion Title", "authors": [_C_AUTHOR], "isbn": None},
        ),
    ],
)
def test_a_companion_work_lane_records_its_companion_basis(
    target: bool, basis: str, searched: dict[str, Any]
) -> None:
    kept = _c_product(title="Companion Title", creator=_C_AUTHOR, provider="audible")
    report = _companion_report(
        _c_product(title="Another Work Entirely"),
        linked=False,
        target=target,
        work={basis: (kept,)},
    )

    assert basis == _bases(report)[("work", kept.identity)]
    assert basis == _derived(report)[kept.identity]
    selected = recording_admission_basis(_replayed(report))
    assert selected is not None
    assert (selected["basis"], selected["searched"]) == (basis, searched)
    _assert_scope_invariant(report)


def test_a_carried_lookup_records_the_discovery_title_it_admitted_by() -> None:
    report = _carried(_candidate("audible", (_CARRIED,), title=_LEAF), _LEAF_AUDIO)

    assert {("asin", f"audible-{_CARRIED}"): "path_leaf"} == _bases(report)
    selected = recording_admission_basis(_replayed(report))
    assert selected is not None and selected["basis"] == "path_leaf"
    _assert_scope_invariant(report)


@pytest.mark.parametrize(
    ("target", "basis", "searched"),
    [
        (
            True,
            "companion_title",
            {"title": "Companion Title", "authors": None, "isbn": None},
        ),
        # With no target work the lookup admits against the companion's own.
        (
            False,
            "companion_work",
            {"title": "Companion Title", "authors": [_C_AUTHOR], "isbn": None},
        ),
    ],
)
def test_a_fed_lookup_records_the_companion_identity_it_admitted_by(
    target: bool, basis: str, searched: dict[str, Any]
) -> None:
    report = _companion_report(
        _c_product(title="Companion Title", creator=_C_AUTHOR),
        linked=False,
        target=target,
    )

    assert {("asin", f"enrichment-{_LINKED}"): basis} == _bases(report)
    selected = recording_admission_basis(_replayed(report))
    assert selected is not None
    assert (selected["basis"], selected["searched"]) == (basis, searched)
    _assert_scope_invariant(report)


def test_a_linked_lookup_records_the_companion_isbn() -> None:
    report = _companion_report()

    assert "companion_isbn_work" == _bases(report)[("asin", f"enrichment-{_LINKED}")]
    assert recording_admission_basis(_replayed(report)) == {
        "basis": "companion_isbn_work",
        "searched": {"title": None, "authors": None, "isbn": _ISBN},
        "provider": "enrichment",
        "candidate": f"enrichment-{_LINKED}",
    }
    _assert_scope_invariant(report)


_DECORATED = "Synthetic Journey: Into the Fold"
_RELATED = _candidate(
    "audible", (_CARRIED,), title="Synthetic Journey", subtitle="Into the Fold"
)


def test_a_carried_lookup_the_corroborated_rule_admits_records_target() -> None:
    report = _carried(_RELATED, _audio(), title=_DECORATED, narrators=(_NARRATOR,))

    assert {("asin", f"audible-{_CARRIED}"): "target"} == _bases(report)
    assert recording_admission_basis(_replayed(report)) is None
    # The product's title is not the target's, so only the rule admitted it.
    off = _carried(
        _RELATED, _audio(), title=_DECORATED, narrators=(_NARRATOR,), rule=False
    )
    assert {("asin", f"audible-{_CARRIED}"): None} == _bases(off)


def test_a_stub_records_nothing() -> None:
    loose = _candidate("audible", (_CARRIED,), title=_LEAF, duration=_LOOSE)
    report = _carried(loose, _LEAF_AUDIO)

    (stub,) = report.providers[0].candidates
    assert {ClaimKind.ASIN} == {claim.kind for claim in stub.claims}
    assert {("asin", f"audible-{_CARRIED}"): None} == _bases(report)


def _silo_carried(product: ProviderCandidate) -> MetadataReport:
    claims = _claims()
    plan = audiobook_carried_plan(_plan(claims, _LEAF_AUDIO))
    return _report(
        claims,
        (
            _observe(
                plan,
                provider="silo",
                authority="audiosilo",
                asin={_CARRIED: (product,)},
            ),
        ),
        _LEAF_AUDIO,
    )


def test_an_audiosilo_recording_without_a_marketplace_records_nothing() -> None:
    """AudioSilo states a marketplace only for a region it maps (R4)."""
    report = _silo_carried(_candidate("silo", (_CARRIED,), marketplace=False))

    (kept,) = report.providers[0].candidates
    assert ClaimKind.TITLE in {claim.kind for claim in kept.claims}
    assert {("asin", f"silo-{_CARRIED}"): None} == _bases(report)
    assert _replayed(report) == report


# -- several lanes, one candidate ----------------------------------------------


def _with_values(
    observation: ProviderObservation, values: Mapping[str, str | None]
) -> ProviderObservation:
    """The observation with each lane's values replaced, by lane identity."""
    return replace(
        observation,
        lanes=tuple(
            replace(
                lane,
                candidates=tuple(
                    replace(member, admission_basis=values.get(lane.identity))
                    for member in lane.candidates
                ),
            )
            for lane in observation.lanes
        ),
    )


def test_two_lanes_with_different_values_derive_one_basis_in_either_order() -> None:
    report = _regional_report()
    observation = next(p for p in report.providers if p.provider == "enrichment")
    (product,) = observation.candidates
    first, second = (lane.identity for lane in observation.lanes)
    stated = {
        lane.identity: lane.candidates[0].admission_basis for lane in observation.lanes
    }
    assert {"companion_isbn_work", "companion_title"} == set(stated.values())
    for values in (
        {first: "companion_title", second: "companion_isbn_work"},
        {first: "companion_isbn_work", second: "companion_title"},
        {first: "path_leaf", second: "title_tag"},
        {first: "title_tag", second: "path_leaf"},
        {first: "target", second: "companion_isbn_work"},
        {first: "companion_work", second: "target"},
    ):
        derived = candidate_admission_basis(_with_values(observation, values), product)
        expected = (
            "target"
            if "target" in values.values()
            else next(b for b in ALTERNATIVE_ADMISSION_BASES if b in values.values())
        )
        assert expected == derived


def test_regional_siblings_with_different_values_validate() -> None:
    report = _replayed(_regional_report())

    enrichment = next(p for p in report.providers if p.provider == "enrichment")
    assert {"companion_isbn_work", "companion_title"} == {
        lane.candidates[0].admission_basis for lane in enrichment.lanes
    }
    selected = recording_admission_basis(report)
    assert selected is not None and selected["basis"] == "companion_isbn_work"


def test_a_carried_target_beside_the_alternative_work_lane_derives_target() -> None:
    """R2: the product the path-leaf lane kept, the carried lane target-admits."""
    report = _carried(
        _RELATED,
        _audio(("path_leaf", "Synthetic Journey")),
        title=_DECORATED,
        narrators=(_NARRATOR,),
        search={"path_leaf": (_RELATED,)},
    )

    assert {"path_leaf", "target"} == {
        member.admission_basis
        for lane in report.providers[0].lanes
        for member in lane.candidates
    }
    assert {f"audible-{_CARRIED}": "target"} == _derived(_replayed(report))
    assert recording_admission_basis(report) is None


def test_a_lane_without_marketplace_evidence_leaves_the_value_to_another() -> None:
    """A1 path A: the asserted lane's copy kept no marketplace; the carried did."""
    claims = _claims()
    plan = audiobook_carried_plan(_plan(claims, _LEAF_AUDIO))
    report = _report(
        claims,
        (
            _observe(
                plan,
                work={None: (_candidate("audible", (_CARRIED,), marketplace=False),)},
                asin={_CARRIED: (_candidate("audible", (_CARRIED,)),)},
            ),
        ),
        _LEAF_AUDIO,
    )

    assert {None, "target"} == {
        member.admission_basis
        for lane in report.providers[0].lanes
        for member in lane.candidates
    }
    assert {f"audible-{_CARRIED}": "target"} == _derived(_replayed(report))


def test_a_stub_on_one_lane_leaves_the_value_to_the_lane_that_kept_it() -> None:
    """A1 path B: one lane holds the product's stub, another keeps it in full."""
    report = _asserted_leaf_report()

    assert {None, "path_leaf"} == {
        member.admission_basis
        for lane in report.providers[0].lanes
        for member in lane.candidates
    }
    assert {f"audible-{_CARRIED}": "path_leaf"} == _derived(_replayed(report))


# -- what the report refuses ---------------------------------------------------


def _forged(
    report: MetadataReport,
    value: str | None,
    *,
    provider: str | None = None,
    kind: str | None = None,
    asin: str | None = None,
) -> str:
    """The report's JSON with chosen lane members stating `value`.

    Members are chosen by provider name, lane kind and, for an ASIN lane,
    the ASIN it asked for; an omitted filter matches every member.
    """
    data = json.loads(report.to_json())
    touched = 0
    for observation, source in zip(data["providers"], report.providers, strict=True):
        if provider is not None and observation["provider"] != provider:
            continue
        for lane, lane_source in zip(observation["lanes"], source.lanes, strict=True):
            if kind is not None and lane["kind"] != kind:
                continue
            if asin is not None and lane_source.identity != _asin_lane_identity(
                report, asin
            ):
                continue
            for member in lane["candidates"]:
                member["admission_basis"] = value
                touched += 1
    assert touched, "the forgery touched no lane member"
    return json.dumps(data)


def _asin_lane_identity(report: MetadataReport, asin: str) -> str:
    from mechanical_librarian.domain.audiobook_corroboration import lane_queried_asin

    return next(
        lane.identity
        for observation in report.providers
        for lane in observation.lanes
        if lane.kind is ProviderQueryKind.ASIN
        and lane_queried_asin(lane, (asin,)) is not None
    )


def _refused(text: str, reason: str) -> None:
    with pytest.raises(ReportValueError, match=f"^provider admission basis {reason}$"):
        MetadataReport.from_json(text)


def _carried_leaf() -> MetadataReport:
    return _carried(_candidate("audible", (_CARRIED,), title=_LEAF), _LEAF_AUDIO)


def test_a_value_outside_the_closed_set_is_refused() -> None:
    assert "target" in RECORDING_ADMISSION_BASES
    _refused(_forged(_carried_leaf(), "folder_name"), "is invalid")


def test_an_ebook_lane_value_is_refused() -> None:
    from tests.support.proposal_evidence import _real_tigers_corroboration_report

    report = _real_tigers_corroboration_report()
    assert report.audio is None
    _refused(_forged(report, "target", provider="hardcover"), "is invalid")


def test_an_isbn_lane_value_is_refused() -> None:
    _refused(
        _forged(_companion_report(), "companion_isbn_work", kind="isbn"), "is invalid"
    )


def test_a_stub_value_is_refused() -> None:
    loose = _candidate("audible", (_CARRIED,), title=_LEAF, duration=_LOOSE)
    _refused(_forged(_carried(loose, _LEAF_AUDIO), "target"), "is invalid")


def test_a_value_on_a_recording_without_a_marketplace_is_refused() -> None:
    report = _silo_carried(_candidate("silo", (_CARRIED,), marketplace=False))
    _refused(_forged(report, "target"), "is invalid")


def test_an_alternative_work_lane_without_its_value_is_refused() -> None:
    report = _searched(
        _candidate("audible", (_CARRIED,), title=_LEAF), _LEAF_AUDIO, "path_leaf"
    )
    _refused(_forged(report, None), "is missing")


@pytest.mark.parametrize("value", ["title_tag", "target", "companion_title"])
def test_an_alternative_work_lane_with_another_value_is_refused(value: str) -> None:
    report = _searched(
        _candidate("audible", (_CARRIED,), title=_LEAF), _LEAF_AUDIO, "path_leaf"
    )
    _refused(_forged(report, value), "does not replay")


def test_the_asserted_work_lane_states_only_target() -> None:
    report = _searched(_candidate("audible", (_CARRIED,)), _LEAF_AUDIO, None)
    _refused(_forged(report, "path_leaf"), "does not replay")


def test_an_asin_lane_completeness_gap_is_refused() -> None:
    report = _carried(_candidate("audible", (_CARRIED,)), _LEAF_AUDIO)
    _refused(_forged(report, None), "is missing")


def test_an_asin_lane_target_that_no_target_route_admits_is_refused() -> None:
    _refused(_forged(_carried_leaf(), "target"), "does not replay")


@pytest.mark.parametrize("value", ["companion_title", "companion_work", "title_tag"])
def test_an_asin_lane_alternative_that_does_not_replay_is_refused(value: str) -> None:
    _refused(_forged(_carried_leaf(), value), "does not replay")


def test_an_asin_lane_alternative_that_is_not_the_first_to_replay_is_refused() -> None:
    """On the linked lane the companion ISBN outranks the companion title."""
    report = _regional_report()
    assert _replayed(report) == report
    _refused(
        _forged(report, "companion_title", provider="enrichment", asin=_LINKED),
        "does not replay",
    )


def test_a_companion_title_ahead_of_the_folder_name_replays_first() -> None:
    """Discovery titles that start at the path leaf still follow the companion."""
    report = _companion_report(
        _c_product(title="Companion Title", creator=_C_AUTHOR),
        linked=False,
        audio=_audio(("path_leaf", _LEAF)),
    )

    assert {("asin", f"enrichment-{_LINKED}"): "companion_title"} == _bases(report)
    assert _replayed(report) == report
    _refused(_forged(report, "path_leaf", kind="asin"), "does not replay")


def test_a_discovery_title_replays_only_on_the_carried_lane() -> None:
    """The same product and lane replay `path_leaf` only for a carried ASIN."""
    from mechanical_librarian.domain.audiobook_corroboration import (
        asin_lane_admission_basis_replays,
    )

    report = _carried_leaf()
    (observation,) = report.providers
    lane = next(
        value for value in observation.lanes if value.kind is ProviderQueryKind.ASIN
    )
    (candidate,) = observation.candidates

    def replays(local_claims: tuple[LocalClaim, ...]) -> bool:
        return asin_lane_admission_basis_replays(
            "path_leaf",
            lane,
            candidate,
            target=report.target,
            local_claims=local_claims,
            audio=report.audio,
            providers=report.providers,
            companion=report.companion,
            library_languages=report.library_languages,
        )

    assert replays(report.local_claims)
    # The same ASIN asserted by the target rather than carried.
    asserted = tuple(
        _local(
            ClaimKind.ASIN,
            _CARRIED,
            0,
            basis="target_metadata",
            scope=ClaimScope.EDITION,
        )
        if claim.kind is ClaimKind.ASIN
        else claim
        for claim in report.local_claims
    )
    assert not replays(asserted)


def test_a_companion_isbn_value_for_an_asin_it_did_not_link_is_refused() -> None:
    """The carried `_REGION` lane asked for an ASIN the book does not list."""
    _refused(
        _forged(
            _regional_report(),
            "companion_isbn_work",
            provider="enrichment",
            asin=_REGION,
        ),
        "does not replay",
    )


def test_a_current_round_trip_keeps_every_lane_value() -> None:
    for report in (_carried_leaf(), _regional_report(), _companion_report()):
        data = json.loads(report.to_json())
        assert CURRENT_REPORT_SCHEMA == data["schema_version"]
        stated = {
            member["admission_basis"]
            for observation in data["providers"]
            for lane in observation["lanes"]
            for member in lane["candidates"]
        }
        assert stated & set(ALTERNATIVE_ADMISSION_BASES)
        assert _bases(_replayed(report)) == _bases(report)


def test_a_lane_member_without_the_field_is_refused() -> None:
    data = json.loads(_carried_leaf().to_json())
    for lane in data["providers"][0]["lanes"]:
        for member in lane["candidates"]:
            del member["admission_basis"]
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(json.dumps(data))


# -- what the owner is told ----------------------------------------------------


def _two_carried_lookups(audible_title: str) -> MetadataReport:
    """Audible's and AudioSilo's carried lookups of one recording."""
    claims = _claims()
    plan = audiobook_carried_plan(_plan(claims, _LEAF_AUDIO))
    return _report(
        claims,
        (
            _observe(
                plan,
                asin={
                    _CARRIED: (_candidate("audible", (_CARRIED,), title=audible_title),)
                },
            ),
            _observe(
                plan,
                provider="silo",
                authority="audiosilo",
                asin={_CARRIED: (_candidate("silo", (_CARRIED,), title=_LEAF),)},
            ),
        ),
        _LEAF_AUDIO,
    )


def test_a_recording_any_provider_kept_on_the_target_says_nothing() -> None:
    mixed = _two_carried_lookups(_TITLE)
    assert {"target", "path_leaf"} == set(_bases(mixed).values())
    assert recording_admission_basis(mixed) is None

    alternative = _two_carried_lookups(_LEAF)
    assert recording_admission_basis(alternative) == {
        "basis": "path_leaf",
        "searched": {"title": _LEAF, "authors": None, "isbn": None},
        "provider": "audible",
        "candidate": f"audible-{_CARRIED}",
    }


def test_the_strongest_route_is_named_with_the_first_product_it_found() -> None:
    """Audible comes first in report order, but its route is the weaker one."""
    kept = _c_product(title="Companion Title", creator=_C_AUTHOR, provider="audible")
    report = _companion_report(work={"companion_title": (kept,)})

    assert "companion_title" == _derived(report)[kept.identity]
    assert [value.provider for value in report.providers][:2] == [
        "audible",
        "catalogue",
    ]
    assert recording_admission_basis(report) == {
        "basis": "companion_isbn_work",
        "searched": {"title": None, "authors": None, "isbn": _ISBN},
        "provider": "enrichment",
        "candidate": f"enrichment-{_LINKED}",
    }
    _assert_scope_invariant(report)


def test_a_product_the_carried_edition_withholds_does_not_speak() -> None:
    """The asserted lane's target-kept sibling is the carried recording relisted."""
    report = _carried(
        _candidate("audible", (_CARRIED,), title=_LEAF),
        _LEAF_AUDIO,
        search={None: (_candidate("audible", (_SIBLING,)),)},
    )

    assert "target" == _derived(report)[f"audible-{_SIBLING}"]
    assert {("audible", f"audible-{_SIBLING}")} == carried_asin_precedence(report)
    selected = recording_admission_basis(report)
    assert selected is not None
    assert (selected["basis"], selected["candidate"]) == (
        "path_leaf",
        f"audible-{_CARRIED}",
    )
    _assert_scope_invariant(report)


def test_the_note_is_said_only_for_a_recording_scope_without_a_chapter_basis() -> None:
    from tests.support.audiobook_reports import chapter_runtime_report

    said = (
        _carried_leaf(),
        _regional_report(),
        _companion_report(),
        _asserted_leaf_report(),
        _two_carried_lookups(_LEAF),
    )
    for report in said:
        assert recording_admission_basis(report) is not None
        _assert_scope_invariant(report)
    chapters = chapter_runtime_report()
    assert recording_identity_basis(chapters) is not None
    assert recording_admission_basis(chapters) is None
    # An ebook has neither note.
    from tests.support.proposal_evidence import _real_tigers_corroboration_report

    assert recording_admission_basis(_real_tigers_corroboration_report()) is None


def test_the_companion_isbn_shown_is_the_one_the_epub_states() -> None:
    """ISBN-13 first, else ISBN-10, compacted, keeping a final X (A8, A11)."""

    def claim(value: str) -> LocalClaim:
        compact = value.replace("-", "")
        return LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed=value,
            canonical=compact,
            excerpt=value,
            applicability="ebook",
            basis="package_metadata",
            checksum=f"valid_isbn_{len(compact)}",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )

    def isbn(*values: str) -> str | None:
        return companion_stated_isbn(
            CompanionEbook(
                "e" * 64,
                "c" * 64,
                (
                    companion_fixture._companion_claim(
                        ClaimKind.TITLE, "Companion Title"
                    ),
                    *(claim(value) for value in values),
                ),
            )
        )

    assert "9780306406157" == isbn("978-0-306-40615-7")
    # One book stated both ways: the ISBN-13 is shown even when listed second.
    assert "9780306406157" == isbn("0-306-40615-2", "978-0-306-40615-7")
    assert "0306406152" == isbn("0-306-40615-2")
    assert "080442957X" == isbn("0-8044-2957-X")
    assert isbn() is None


# -- cold start ----------------------------------------------------------------


def test_the_cold_start_report_counts_each_admission_basis() -> None:
    import scripts.cold_start_report as cold_start

    loose = _candidate("audible", (_CARRIED,), title=_LEAF, duration=_LOOSE)
    assert "path_leaf" == cold_start._admission_basis(_carried_leaf())
    assert "target" == cold_start._admission_basis(
        _carried(_candidate("audible", (_CARRIED,)), _LEAF_AUDIO)
    )
    assert "companion_isbn_work" == cold_start._admission_basis(_companion_report())
    assert cold_start._admission_basis(_carried(loose, _LEAF_AUDIO)) is None

    def row(basis: str | None) -> tuple[object, ...]:
        outcome = "evidence_only" if basis is None else "recording_admitted"
        use = ("none", False, False, None)
        return (None, None, None, outcome, None, None, False, "no_edition", use, basis)

    rows = [
        row(value)
        for value in (
            "target",
            "target",
            "companion_isbn_work",
            "path_leaf",
            "path_leaf",
            "path_leaf",
            "title_tag",
            None,
        )
    ]
    assert cold_start._audiobook_outcomes(rows)["recording_admission_bases"] == {
        "target": 2,
        "companion_isbn_work": 1,
        "companion_work": 0,
        "companion_title": 0,
        "path_leaf": 3,
        "title_tag": 1,
    }


# -- a retry successor ---------------------------------------------------------


def _merged(
    plan: MetadataDiscoveryPlan,
    original: ProviderObservation,
    selected: tuple[MetadataDiscoveryLane, ...],
    replacement: ProviderObservation,
) -> ProviderObservation:
    from mechanical_librarian.application.provider_retry_successor import (
        GenerateProviderRetrySuccessor,
    )

    return GenerateProviderRetrySuccessor._merged(
        cast("Any", GenerateProviderRetrySuccessor),
        plan,
        original,
        selected,
        replacement,
    )


def _retried_leaf_lane() -> tuple[MetadataReport, ProviderObservation, MetadataReport]:
    """The path-leaf lane failed in phase one and its retry kept the product.

    The carried lane kept the same product on the target's identity (the
    corroborated rule), so the successor's product is said nothing of (A5).
    """
    audio = _audio(("path_leaf", "Synthetic Journey"))
    claims = _claims(title=_DECORATED)
    plan = audiobook_carried_plan(
        _plan(claims, audio, title=_DECORATED, narrators=(_NARRATOR,))
    )
    (leaf,) = (lane for lane in plan.lanes if lane.title_basis == "path_leaf")
    observed = {
        lane.identity: _answer(
            plan,
            lane,
            "audible",
            "audible_catalog",
            (_RELATED,) if lane.kind is ProviderQueryKind.ASIN else (),
        )
        for lane in plan.lanes
    }
    observed[leaf.identity] = ProviderLaneResult(leaf, ProviderStatus.UNAVAILABLE)
    original = recorded_dispatch(
        provider_observation(
            "audible", tuple(observed.values()), authority="audible_catalog"
        )
    )

    def report(observation: ProviderObservation) -> MetadataReport:
        return _report(
            claims,
            (observation,),
            audio,
            title=_DECORATED,
            narrators=(_NARRATOR,),
        )

    source = report(original)
    replacement = recorded_dispatch(
        provider_observation(
            "audible",
            (_answer(plan, leaf, "audible", "audible_catalog", (_RELATED,)),),
            authority="audible_catalog",
        )
    )
    merged = _merged(plan, original, (leaf,), replacement)
    return source, merged, report(merged)


def test_a_retried_alternative_lane_beside_a_carried_target_derives_target() -> None:
    source, _merged_observation, successor = _retried_leaf_lane()

    assert {f"audible-{_CARRIED}": "target"} == _derived(source)
    assert {"path_leaf", "target"} == {
        member.admission_basis
        for lane in successor.providers[0].lanes
        for member in lane.candidates
    }
    assert {f"audible-{_CARRIED}": "target"} == _derived(_replayed(successor))
    assert recording_admission_basis(successor) is None


def test_an_unselected_lane_keeps_its_values_verbatim() -> None:
    source, merged, _successor = _retried_leaf_lane()

    (carried,) = (
        lane
        for lane in source.providers[0].lanes
        if lane.kind is ProviderQueryKind.ASIN
    )
    after = next(lane for lane in merged.lanes if lane.identity == carried.identity)
    assert "target" == carried.candidates[0].admission_basis
    assert carried.to_data() == after.to_data()


def test_a_retry_with_the_rule_off_keeps_the_discovery_title_it_replays() -> None:
    """A12: the rule and the path leaf both admit; with the rule off, the leaf."""
    on = _carried(
        _RELATED,
        _audio(("path_leaf", "Synthetic Journey")),
        title=_DECORATED,
        narrators=(_NARRATOR,),
    )
    off = _carried(
        _RELATED,
        _audio(("path_leaf", "Synthetic Journey")),
        title=_DECORATED,
        narrators=(_NARRATOR,),
        rule=False,
    )

    assert {("asin", f"audible-{_CARRIED}"): "target"} == _bases(_replayed(on))
    assert {("asin", f"audible-{_CARRIED}"): "path_leaf"} == _bases(_replayed(off))
    selected = recording_admission_basis(off)
    assert selected is not None and selected["basis"] == "path_leaf"


def _hardcover(*asins: str) -> ProviderObservation:
    """Hardcover's companion answer, its book listing these audio editions."""
    return companion_fixture._hardcover(
        companion_fixture._companion(),
        tuple(
            companion_fixture._edition(index, _C_SECONDS, _C_NARRATOR, asin=asin)
            for index, asin in enumerate(asins, 1)
        ),
    )


def _with_hardcover(
    report: MetadataReport, hardcover: ProviderObservation
) -> tuple[ProviderObservation, ...]:
    return tuple(
        hardcover if value.authority == "hardcover" else value
        for value in report.providers
    )


def test_a_retry_that_shrinks_the_linked_set_fails_closed() -> None:
    from mechanical_librarian.application.provider_retry_successor import (
        ProviderRetrySuccessorError,
        _refuse_moved_companion_links,
    )

    source = _companion_report()
    assert "companion_isbn_work" in _bases(source).values()
    # The same answer publishes; one that no longer lists the edition cannot.
    _refuse_moved_companion_links(source, _with_hardcover(source, _hardcover(_LINKED)))
    with pytest.raises(
        ProviderRetrySuccessorError, match="moves a companion-linked admission"
    ):
        _refuse_moved_companion_links(source, _with_hardcover(source, _hardcover()))


def test_a_retry_that_grows_the_linked_set_fails_closed() -> None:
    """The carried `_REGION` lane chose the companion title against the old set."""
    from mechanical_librarian.application.provider_retry_successor import (
        ProviderRetrySuccessorError,
        _refuse_moved_companion_links,
    )

    source = _regional_report()
    with pytest.raises(
        ProviderRetrySuccessorError, match="moves a companion-linked admission"
    ):
        _refuse_moved_companion_links(
            source, _with_hardcover(source, _hardcover(_LINKED, _REGION))
        )


def test_a_moved_link_no_alternative_value_rests_on_publishes() -> None:
    """A linked set may move under a lane that kept its product on the target."""
    from mechanical_librarian.application.provider_retry_successor import (
        _refuse_moved_companion_links,
    )

    source = _companion_report(
        _c_product(title=_C_TITLE, creator=_C_AUTHOR), linked=True
    )
    assert "target" == _bases(source)[("asin", f"enrichment-{_LINKED}")]
    _refuse_moved_companion_links(source, _with_hardcover(source, _hardcover()))


# -- the companion ISBN row in Sources -----------------------------------------


def test_the_companion_isbn_lane_is_named_in_both_lane_readers() -> None:
    from mechanical_librarian.application.review_web import _provider_outcomes
    from mechanical_librarian.domain.review_browse import (
        library_report_facts,
        provider_lane_facts,
    )

    for report, status in ((_companion_report(), "candidates"),):
        for facts in (
            provider_lane_facts(report),
            library_report_facts(report.to_data()).providers,
        ):
            flagged = [value for value in facts if value.companion_isbn]
            assert [("hardcover", "isbn", status)] == [
                (value.authority, value.kind, value.status) for value in flagged
            ]
            assert all(
                not value.companion_isbn
                for value in facts
                if value.authority != "hardcover"
            )
    outcomes = _provider_outcomes(provider_lane_facts(_companion_report()))
    (companion,) = (value for value in outcomes if value["companion_isbn"])
    assert companion["reason"] == (
        "Looked up the book and its audio editions by the EPUB's ISBN."
    )
    assert not any(
        value["companion_isbn"]
        for value in _provider_outcomes(provider_lane_facts(_carried_leaf()))
    )


def test_a_companion_isbn_that_led_nowhere_says_so() -> None:
    from mechanical_librarian.application.review_web import _provider_outcomes
    from mechanical_librarian.domain.review_browse import provider_lane_facts

    report = _companion_report()
    facts = tuple(
        replace(value, status="no_match") if value.companion_isbn else value
        for value in provider_lane_facts(report)
    )
    (companion,) = (
        value for value in _provider_outcomes(facts) if value["companion_isbn"]
    )
    assert companion["reason"] == "The EPUB's ISBN did not lead to this audiobook."
    unavailable = tuple(
        replace(value, status="unavailable") if value.companion_isbn else value
        for value in facts
    )
    (generic,) = (
        value for value in _provider_outcomes(unavailable) if value["companion_isbn"]
    )
    assert "EPUB" not in str(generic["reason"])


# -- one alternative identity for the build and the replay (A4) ----------------


@pytest.mark.parametrize(
    ("titles", "companion", "target"),
    [
        ((("path_leaf", _LEAF),), False, True),
        ((("path_leaf", _TITLE), ("title_tag", _TAG)), False, True),
        ((("path_leaf", _TITLE),), False, True),
        ((), True, True),
        ((("path_leaf", _LEAF),), True, True),
        ((("title_tag", _TAG),), True, False),
        ((), False, True),
    ],
)
def test_the_build_and_the_replay_read_one_alternative_identity(
    titles: tuple[tuple[str, str], ...], companion: bool, target: bool
) -> None:
    """The plan's alternative lane, the lookup's first title and the replay agree."""
    epub = companion_fixture._companion(isbns=()) if companion else None
    audio = _audio(*titles)
    title, authors = (_TITLE, (_AUTHOR,)) if target else (None, ())
    plan = _plan(
        _claims(title=title, carried=None),
        audio,
        title=title,
        authors=authors,
        companion=epub,
    )
    work = (_TITLE, (_AUTHOR,)) if target else None
    first = dom.plan_alternative_discovery_title(
        _fields(title, authors), audio.discovery_titles, epub
    )
    assert plan.audio is not None
    planned_first = (
        plan.audio.discovery_titles[0] if plan.audio.discovery_titles else None
    )
    assert planned_first == first
    replayed = dom.alternative_lane_identity(first, work, epub)
    lanes = [lane for lane in plan.lanes if lane.title_basis is not None]
    if replayed is None:
        assert not lanes
        return
    (lane,) = lanes
    assert (lane.title, lane.authors, lane.title_basis) == replayed


def test_a_retry_keeps_a_work_lane_value_its_own_copy_never_earned() -> None:
    """A1 path A through a retry: the asserted lane's copy had no marketplace.

    The merged product holds the carried lane's marketplace evidence, so a
    WORK lane's value read again off it would turn the asserted lane's `None`
    into `target`. The unselected lane is carried over byte for byte instead.
    """
    claims = _claims()
    plan = audiobook_carried_plan(_plan(claims, _LEAF_AUDIO))
    (leaf,) = (lane for lane in plan.lanes if lane.title_basis == "path_leaf")
    (asserted,) = (
        lane
        for lane in plan.lanes
        if lane.kind is ProviderQueryKind.WORK and lane.title_basis is None
    )
    bare = _candidate("audible", (_CARRIED,), marketplace=False)
    full = _candidate("audible", (_CARRIED,))
    results = []
    for lane in plan.lanes:
        if lane.identity == leaf.identity:
            results.append(ProviderLaneResult(lane, ProviderStatus.UNAVAILABLE))
        elif lane.identity == asserted.identity:
            results.append(_answer(plan, lane, "audible", "audible_catalog", (bare,)))
        else:
            results.append(_answer(plan, lane, "audible", "audible_catalog", (full,)))
    original = recorded_dispatch(
        provider_observation("audible", tuple(results), authority="audible_catalog")
    )
    source = _report(claims, (original,), _LEAF_AUDIO)
    before = next(lane for lane in original.lanes if lane.identity == asserted.identity)
    assert before.candidates[0].admission_basis is None
    replacement = recorded_dispatch(
        provider_observation(
            "audible",
            (ProviderLaneResult(leaf, ProviderStatus.NO_MATCH),),
            authority="audible_catalog",
        )
    )

    merged = _merged(plan, original, (leaf,), replacement)

    after = next(lane for lane in merged.lanes if lane.identity == asserted.identity)
    assert before.to_data() == after.to_data()
    successor = _report(claims, (merged,), _LEAF_AUDIO)
    assert _derived(_replayed(successor)) == _derived(source)
