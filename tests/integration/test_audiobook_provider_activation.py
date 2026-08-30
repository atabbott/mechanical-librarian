"""The activated audiobook flow, composed end to end over real stores.

One audiobook item goes from local extraction through the four configured
providers to a published report, a terminal `evidence_only` receipt in the same
transaction, and a scan the item leaves clean. The providers are fakes on the
real composition seams -- the adapter classes composition constructs -- so what
is proved is the wiring rather than the adapters, which have their own suites.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.remote_cover import RemoteCover
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    ProviderCoverDescriptor,
    ProviderLaneResult,
    TargetFile,
    TargetSnapshot,
    provider_observation,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.audio_inspection import AudioEmbeddedCover
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderChapterStructure,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    default_target_fields,
)

_ITEM = "item-audio"
_FILE_IDENTITY = f"{10:064x}"
_CONTENT_DIGEST = f"{20:064x}"
_ASIN = "B08G9PRS1K"
_PRODUCER = "f" * 64
# The container's own running time, and the whole minute a provider states for
# the same recording. Forty-four seconds apart, which is inside the band.
_LOCAL_DURATION = "58244.201"
_PROVIDER_DURATION = "58200.000"


def _target_fields() -> tuple[TargetField, ...]:
    populated = TargetField(
        TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Andy Weir",)
    )
    return tuple(
        populated if field.destination is TargetDestination.AUTHORS else field
        for field in default_target_fields("Project Hail Mary")
    )


def _snapshot() -> TargetSnapshot:
    return TargetSnapshot(
        "audiobook",
        "Project Hail Mary",
        (TargetFile(_FILE_IDENTITY, "book.m4b", "m4b"),),
        fields=_target_fields(),
    )


class _TargetReader:
    def __init__(self, **_kwargs) -> None:
        pass

    def read(self, _item: str) -> TargetSnapshot:
        return _snapshot()


def _local_claim(
    kind: ClaimKind, value: str, occurrence: int, *, role: str | None = None
) -> LocalClaim:
    return LocalClaim.create(
        kind=kind,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role=role,
        occurrence=occurrence,
    )


def _audio_result() -> AudioInspectionResult:
    return AudioInspectionResult(
        _CONTENT_DIGEST,
        (
            _local_claim(ClaimKind.ASIN, _ASIN, 1),
            _local_claim(ClaimKind.DURATION, _LOCAL_DURATION, 2),
        ),
        AudioFacts(embedded_cover=AudioEmbeddedCover(False)),
    )


class _AudioInspector:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def inspect(self, _mount, _relative_path, _maximum_bytes) -> AudioInspectionResult:
        return _audio_result()

    def unchanged(
        self,
        _mount,
        _relative_path,
        expected_digest,
        _maximum_bytes,
        expected_sidecars=(),
    ) -> bool:
        return expected_digest == _CONTENT_DIGEST and not expected_sidecars


def _enrichment_candidate(
    provider: str, local_claims: tuple[LocalClaim, ...]
) -> ProviderCandidate:
    """One resolved product, with the chapter vector the ASIN lane enriches."""
    identity = "audnexus-book-" + _ASIN
    specifications: tuple[tuple[ClaimKind, str, ClaimScope, str | None], ...] = (
        (ClaimKind.ASIN, _ASIN, ClaimScope.EDITION, None),
        (ClaimKind.TITLE, "Project Hail Mary", ClaimScope.WORK, None),
        (ClaimKind.CREATOR, "Andy Weir", ClaimScope.WORK, "author"),
        (ClaimKind.CONTRIBUTOR, "Ray Porter", ClaimScope.WORK, "narrator"),
        (ClaimKind.DURATION, _PROVIDER_DURATION, ClaimScope.EDITION, None),
    )
    claims = tuple(
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
    )
    chapters = ProviderChapterStructure(2, 58_200, (29_100, 29_100), False)
    evidence = tuple(
        sorted(
            (
                ProviderEvidence(
                    ProviderEvidenceKind.EDITION_WORK_RELATION,
                    "unresolved",
                    ClaimScope.EDITION,
                ),
                ProviderEvidence(
                    ProviderEvidenceKind.ASIN_MARKETPLACE, "us", ClaimScope.EDITION
                ),
                ProviderEvidence(
                    ProviderEvidenceKind.CHAPTER_STRUCTURE,
                    str(chapters.runtime_seconds),
                    ClaimScope.EDITION,
                    chapters=chapters,
                ),
            ),
            key=lambda value: (value.scope.value, value.kind.value, value.value),
        )
    )
    del local_claims
    return ProviderCandidate(identity, "unresolved", claims, evidence)


class _FakeAudiobookProvider:
    """One adapter double that answers the lane kinds its authority admits."""

    def __init__(
        self,
        name: str,
        authority: str,
        calls: list[tuple[str, ProviderQueryKind]],
        *,
        status: ProviderStatus = ProviderStatus.CANDIDATES,
        lane_kinds: frozenset[ProviderQueryKind] = frozenset({ProviderQueryKind.ASIN}),
        cover_kind: CoverSourceKind | None = None,
        cover_url: str | None = None,
    ) -> None:
        self.name = name
        self.authority = authority
        self.calls = calls
        self.status = status
        self.lane_kinds = lane_kinds
        self.cover_descriptors: tuple[ProviderCoverDescriptor, ...] = (
            ()
            if cover_kind is None
            else (
                ProviderCoverDescriptor(
                    name,
                    cover_kind,
                    cover_source_id(cover_kind, f"{name}:product:{_ASIN}"),
                    cover_url,
                    CoverProvenance(
                        name,
                        CoverDesignation.EXACT_EDITION,
                        "unresolved",
                        "audnexus-book-" + _ASIN,
                    ),
                    None,
                    False,
                    None,
                    None if cover_url is not None else CoverSourceState.UNSAFE_REMOTE,
                ),
            )
        )

    def observe(self, plan, *, deadline=None):
        del deadline
        lanes = tuple(lane for lane in plan.lanes if lane.kind in self.lane_kinds)
        for lane in lanes:
            self.calls.append((self.authority, lane.kind))
        if not lanes:
            # Invoked, with nothing it could be asked: the same shape the real
            # store adapter states for a plan carrying no work lane.
            self.calls.append((self.authority, ProviderQueryKind.NONE))
            return composition.ProviderObservation(
                self.name,
                ProviderStatus.INSUFFICIENT_INPUT,
                authority=self.authority,
            )
        if self.status is not ProviderStatus.CANDIDATES:
            self.calls.append((self.authority, ProviderQueryKind.NONE))
            return provider_observation(
                self.name,
                tuple(ProviderLaneResult(lane, self.status) for lane in lanes),
                authority=self.authority,
            )
        return provider_observation(
            self.name,
            tuple(
                ProviderLaneResult(
                    lane,
                    ProviderStatus.CANDIDATES,
                    (_enrichment_candidate(self.name, plan.local_claims),),
                )
                for lane in lanes
            ),
            authority=self.authority,
        )


def _configuration(covers: bool = False) -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/run/target-token"
            ),
        ),
        (
            LibraryConfiguration(
                "fiction",
                "primary",
                "lib-1",
                "audiobook",
                "audiobooks",
                ("audible", "enrichment", "silo", "store"),
            ),
        ),
        (MediaMountConfiguration("audiobooks", "/container", "/service", True),),
        (
            ProviderConnectionConfiguration(
                "audible", True, covers, None, "audible_catalog", marketplaces=("uk",)
            ),
            ProviderConnectionConfiguration(
                "enrichment", True, False, None, "audnexus"
            ),
            ProviderConnectionConfiguration("silo", True, False, None, "audiosilo"),
            ProviderConnectionConfiguration(
                "store", True, covers, None, "itunes_search"
            ),
        ),
        provider_recovery_generation=1,
        review=ReviewWebConfiguration(
            "https://review.example.invalid", "/run/owner-password"
        ),
    )


def _catalogue_entry() -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        _ITEM,
        1,
        "Project Hail Mary",
        ("Andy Weir",),
        False,
        False,
        "singular-supported",
        _FILE_IDENTITY,
        f"/{_ITEM}.m4b",
        "m4b",
        "audiobooks",
    )


def _run(
    root: Path,
    providers: dict[str, _FakeAudiobookProvider],
    *,
    covers: bool = False,
) -> tuple[str, object]:
    """Publish one audiobook report through the real composition seam."""
    database = root / "reports.sqlite"
    configuration = _configuration(covers)
    composition.CoverArtifactStore(root / "artifacts", create=True)
    repository = open_repository(database)
    repository.request_review_scan("primary", "fiction", "lib-1", _PRODUCER, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(
        discovery, 1, "a" * 64, (_catalogue_entry(),), 12
    )
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None

    def adapter(authority: str):
        return lambda name, *_args, **_kwargs: providers[authority]

    with (
        patch.object(composition, "AudiobookshelfItemReader", _TargetReader),
        patch.object(composition, "_IsolatedAudioInspector", _AudioInspector),
        patch.object(composition, "audible_catalog_client", return_value=object()),
        patch.object(composition, "audnexus_client", return_value=object()),
        patch.object(composition, "audiosilo_client", return_value=object()),
        patch.object(composition, "itunes_search_client", return_value=object()),
        patch.object(
            composition,
            "AudibleCatalogMetadataProvider",
            side_effect=adapter("audible_catalog"),
        ),
        patch.object(
            composition,
            "AudnexusMetadataProvider",
            side_effect=adapter("audnexus"),
        ),
        patch.object(
            composition,
            "AudioSiloMetadataProvider",
            side_effect=adapter("audiosilo"),
        ),
        patch.object(
            composition,
            "ITunesSearchMetadataProvider",
            side_effect=adapter("itunes_search"),
        ),
    ):
        serialized = composition.run_metadata_report_loaded(
            configuration,
            database,
            root / "artifacts",
            "primary",
            "fiction",
            _ITEM,
            0,
            expected_file_identity=_FILE_IDENTITY,
            review_action=extraction,
            pipeline_repository=repository,
            recover_artifacts=False,
        )
    return serialized, repository


@pytest.fixture
def calls() -> list[tuple[str, ProviderQueryKind]]:
    return []


def test_an_audiobook_publishes_provider_evidence_and_ends_terminally(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        providers = {
            "audible_catalog": _FakeAudiobookProvider(
                "audible", "audible_catalog", calls
            ),
            "audnexus": _FakeAudiobookProvider("enrichment", "audnexus", calls),
            "audiosilo": _FakeAudiobookProvider("silo", "audiosilo", calls),
            # The store searches and resolves nothing, so an ASIN-only plan
            # leaves it with nothing to ask.
            "itunes_search": _FakeAudiobookProvider(
                "store",
                "itunes_search",
                calls,
                lane_kinds=frozenset({ProviderQueryKind.WORK}),
            ),
        }
        serialized, repository = _run(root, providers)
        published = MetadataReport.from_json(serialized)
        work = repository._connection.execute(
            "SELECT stage,state,failure_code,failure_root FROM review_preparation_work"
        ).fetchone()
        progress = repository.review_pipeline_progress("primary", "fiction")
        remaining = repository.claim_review_pipeline_action(15)
        repository.close()

        # The plan was the ASIN one, and every configured provider was asked.
        assert {"audible_catalog", "audnexus", "audiosilo", "itunes_search"} == {
            authority for authority, _kind in calls
        }
        assert {ProviderQueryKind.ASIN} == {
            kind for _authority, kind in calls if kind is not ProviderQueryKind.NONE
        }
        assert [("itunes_search", ProviderQueryKind.NONE)] == [
            value for value in calls if value[0] == "itunes_search"
        ]
        # The report carries one observation per configured provider, and the
        # store's is the honest "nothing here I can answer".
        assert 4 == len(published.providers)
        assert {
            "audible": ProviderStatus.CANDIDATES,
            "enrichment": ProviderStatus.CANDIDATES,
            "silo": ProviderStatus.CANDIDATES,
            "store": ProviderStatus.INSUFFICIENT_INPUT,
        } == {value.provider: value.status for value in published.providers}
        # An ASIN lane is not a work lane, so nothing was filtered: the
        # marketplace, the chapter structure and the narrator all survive.
        enrichment = next(
            value for value in published.providers if value.provider == "enrichment"
        )
        assert ProviderQueryKind.ASIN is enrichment.lanes[0].kind
        assert (
            "audnexus-book-" + _ASIN
            == enrichment.lanes[0].candidates[0].edition_identity
        )
        assert published.audio is not None
        assert "metadata-report-v32" == published.schema_version
        # Preparation ended in the publishing transaction, under the renamed
        # terminal code. Nothing follows it, and the scan counts the item as
        # ready rather than as something asking the owner to unblock it.
        assert ("provider", "failed", "evidence_only", None) == work
        assert remaining is None
        assert progress is not None
        assert 0 == progress.blocked
        assert 1 == dict(progress.stages)["ready"]
        assert 0 == dict(progress.stages)["failed"]


def test_a_degraded_primary_still_binds_the_remaining_observations(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    """The primary going dark changes what is recorded, not what is configured.

    Audible is ADR-0061's primary discovery surface. With it unavailable the
    other two ASIN providers still answer and their evidence is still bound to
    the report, under exactly the same configuration.
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        providers = {
            "audible_catalog": _FakeAudiobookProvider(
                "audible",
                "audible_catalog",
                calls,
                status=ProviderStatus.UNAVAILABLE,
            ),
            "audnexus": _FakeAudiobookProvider("enrichment", "audnexus", calls),
            "audiosilo": _FakeAudiobookProvider("silo", "audiosilo", calls),
            "itunes_search": _FakeAudiobookProvider(
                "store",
                "itunes_search",
                calls,
                lane_kinds=frozenset({ProviderQueryKind.WORK}),
            ),
        }
        serialized, repository = _run(root, providers)
        published = MetadataReport.from_json(serialized)
        work = repository._connection.execute(
            "SELECT stage,state,failure_code FROM review_preparation_work"
        ).fetchone()
        repository.close()

        statuses = {value.provider: value.status for value in published.providers}
        assert ProviderStatus.UNAVAILABLE is statuses["audible"]
        assert ProviderStatus.CANDIDATES is statuses["enrichment"]
        assert ProviderStatus.CANDIDATES is statuses["silo"]
        assert 4 == len(published.providers)
        # A degraded primary is still a published report and still a terminal
        # disposition: nothing about the audiobook path waits on it.
        assert ("provider", "failed", "evidence_only") == work


_AUDIBLE_ART = "https://m.media-amazon.com/images/I/51POf8gOyLL._SL500_.jpg"
_ITUNES_ART = "https://is1-ssl.mzstatic.com/image/thumb/a/100x100bb.jpg"


def _design(seed: int, size: tuple[int, int] = (240, 360)) -> Image.Image:
    """One decodable cover whose brightness varies, so its hash is not flat."""
    image = Image.new("RGB", size)
    image.putdata(
        [
            ((index + seed) % 251, (index * 7) % 241, (index * 13 + seed) % 239)
            for index in range(size[0] * size[1])
        ]
    )
    return image


def _art(image: Image.Image, size: tuple[int, int] | None = None) -> bytes:
    output = io.BytesIO()
    scaled = image if size is None else image.resize(size, Image.Resampling.LANCZOS)
    scaled.save(output, "JPEG", quality=90)
    return output.getvalue()


def _one_design_two_authorities() -> dict[str, bytes]:
    """The same art as two authorities publish it: two sizes, two encodings."""
    design = _design(0)
    return {_AUDIBLE_ART: _art(design), _ITUNES_ART: _art(design, (120, 180))}


def _cover_providers(
    calls: list[tuple[str, ProviderQueryKind]],
    *,
    itunes_url: str | None = _ITUNES_ART,
) -> dict[str, _FakeAudiobookProvider]:
    """The four authorities, with covers on the two that state one.

    The store answers the ASIN lane here because this case proves the cover
    wiring rather than its lane discipline, which its own suite proves.
    """
    return {
        "audible_catalog": _FakeAudiobookProvider(
            "audible",
            "audible_catalog",
            calls,
            cover_kind=CoverSourceKind.AUDIBLE_CATALOG,
            cover_url="https://m.media-amazon.com/images/I/51POf8gOyLL._SL500_.jpg",
        ),
        "audnexus": _FakeAudiobookProvider("enrichment", "audnexus", calls),
        "audiosilo": _FakeAudiobookProvider("silo", "audiosilo", calls),
        "itunes_search": _FakeAudiobookProvider(
            "store",
            "itunes_search",
            calls,
            cover_kind=CoverSourceKind.ITUNES_SEARCH,
            cover_url=itunes_url,
        ),
    }


def _published_with_covers(
    root: Path,
    calls: list[tuple[str, ProviderQueryKind]],
    payloads: dict[str, bytes],
    *,
    covers: bool = True,
    itunes_url: str | None = _ITUNES_ART,
) -> MetadataReport:
    def fetch(url: str, **_kwargs: object) -> RemoteCover:
        return RemoteCover(url, "image/jpeg", payloads[url])

    providers = _cover_providers(calls, itunes_url=itunes_url)
    with patch.object(composition, "fetch_remote_cover", side_effect=fetch):
        serialized, repository = _run(root, providers, covers=covers)
    repository.close()
    return MetadataReport.from_json(serialized)


def test_an_audiobook_acquires_its_provider_covers_with_perceptual_hashes(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    """Two independent authorities' art reaches the report through one pipeline."""
    with tempfile.TemporaryDirectory() as temporary:
        published = _published_with_covers(
            Path(temporary),
            calls,
            _one_design_two_authorities(),
        )

    kinds = {source.kind for source in published.cover_sources}
    assert CoverSourceKind.AUDIBLE_CATALOG in kinds
    assert CoverSourceKind.ITUNES_SEARCH in kinds
    retrieved = [
        source
        for source in published.cover_sources
        if source.state is CoverSourceState.RETRIEVED
    ]
    assert 2 == len(retrieved)
    hashes = [
        source.artifact.perceptual_hash
        for source in retrieved
        if source.artifact is not None
    ]
    assert 2 == len(hashes)
    assert all(value is not None and 16 == len(value) for value in hashes)
    # One design carried by two authorities: the rule's own distance bound.
    left, right = (int(str(value), 16) for value in hashes)
    assert bin(left ^ right).count("1") <= 10


def test_a_disabled_cover_switch_acquires_no_audiobook_cover_bytes(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        published = _published_with_covers(
            Path(temporary),
            calls,
            _one_design_two_authorities(),
            covers=False,
        )

    assert not [
        source
        for source in published.cover_sources
        if source.kind
        in {CoverSourceKind.AUDIBLE_CATALOG, CoverSourceKind.ITUNES_SEARCH}
    ]


def test_a_refused_cover_origin_is_published_as_a_typed_outcome(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        published = _published_with_covers(
            Path(temporary),
            calls,
            {_AUDIBLE_ART: _art(_design(0))},
            covers=True,
            itunes_url=None,
        )

    store = next(
        source
        for source in published.cover_sources
        if source.kind is CoverSourceKind.ITUNES_SEARCH
    )
    assert CoverSourceState.UNSAFE_REMOTE is store.state
    assert store.artifact is None
