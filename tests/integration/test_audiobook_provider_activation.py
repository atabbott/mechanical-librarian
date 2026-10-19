"""The activated audiobook flow, composed end to end over real stores.

One audiobook item goes from local extraction through the four configured
providers to a published report and on to whichever end its own evidence
earns: the proposal stage when an observation admits the recording's work
identity, and a completed `evidence_only` receipt in the publishing
transaction when none does. The providers are fakes on the real composition
seams -- the adapter classes composition constructs -- so what is proved is
the wiring rather than the adapters, which have their own suites.
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Callable, Mapping
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
    MetadataProvider,
    ProviderCoverDescriptor,
    ProviderLaneResult,
    ProviderResult,
    TargetFile,
    TargetSnapshot,
    provider_observation,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.audio_inspection import (
    AudioEmbeddedCover,
    AudioTrackDigest,
)
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
        AudioFacts(
            embedded_cover=AudioEmbeddedCover(False),
            tracks=(
                AudioTrackDigest(1, _FILE_IDENTITY, _CONTENT_DIGEST, "m4b", "book.m4b"),
            ),
        ),
    )


class _AudioInspector:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def inspect(self, _mount, _tracks, _maximum_bytes) -> AudioInspectionResult:
        return _audio_result()

    def unchanged(
        self,
        _mount,
        _relative_paths,
        expected_digest,
        _maximum_bytes,
        expected_sidecars=(),
        expected_tracks=(),
    ) -> bool:
        return expected_digest == _CONTENT_DIGEST and not expected_sidecars


def _enrichment_candidate(
    provider: str,
    local_claims: tuple[LocalClaim, ...],
    duration: str = _PROVIDER_DURATION,
) -> ProviderCandidate:
    """One resolved product, with the chapter vector the ASIN lane enriches."""
    identity = "audnexus-book-" + _ASIN
    specifications: tuple[tuple[ClaimKind, str, ClaimScope, str | None], ...] = (
        (ClaimKind.ASIN, _ASIN, ClaimScope.EDITION, None),
        (ClaimKind.TITLE, "Project Hail Mary", ClaimScope.WORK, None),
        (ClaimKind.CREATOR, "Andy Weir", ClaimScope.WORK, "author"),
        (ClaimKind.CONTRIBUTOR, "Ray Porter", ClaimScope.WORK, "narrator"),
        (ClaimKind.DURATION, duration, ClaimScope.EDITION, None),
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
        duration: str = _PROVIDER_DURATION,
    ) -> None:
        self.name = name
        self.authority = authority
        self.calls = calls
        self.status = status
        self.lane_kinds = lane_kinds
        self.duration = duration
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
            return ProviderResult(
                composition.ProviderObservation(
                    self.name,
                    ProviderStatus.INSUFFICIENT_INPUT,
                    authority=self.authority,
                ),
                cover_descriptors=self.cover_descriptors,
            )
        if self.status is not ProviderStatus.CANDIDATES:
            self.calls.append((self.authority, ProviderQueryKind.NONE))
            return ProviderResult(
                provider_observation(
                    self.name,
                    tuple(ProviderLaneResult(lane, self.status) for lane in lanes),
                    authority=self.authority,
                ),
                cover_descriptors=self.cover_descriptors,
            )
        return ProviderResult(
            provider_observation(
                self.name,
                tuple(
                    ProviderLaneResult(
                        lane,
                        ProviderStatus.CANDIDATES,
                        (
                            _enrichment_candidate(
                                self.name, plan.local_claims, self.duration
                            ),
                        ),
                    )
                    for lane in lanes
                ),
                authority=self.authority,
            ),
            cover_descriptors=self.cover_descriptors,
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
                ("audible", "books", "enrichment", "inventaire", "silo", "store"),
            ),
        ),
        (MediaMountConfiguration("audiobooks", "/container", "/service", True),),
        (
            ProviderConnectionConfiguration(
                "audible", True, covers, None, "audible_catalog", marketplaces=("uk",)
            ),
            # Google Books and Inventaire are connected and enabled, and outside
            # the audiobook dispatch set (#1247): `_run` fails the test if either
            # is ever built.
            ProviderConnectionConfiguration("books", True, False, None, "google_books"),
            ProviderConnectionConfiguration(
                "enrichment", True, False, None, "audnexus"
            ),
            ProviderConnectionConfiguration(
                "inventaire", True, False, None, "inventaire"
            ),
            ProviderConnectionConfiguration("silo", True, False, None, "audiosilo"),
            ProviderConnectionConfiguration(
                "store", True, covers, None, "itunes_search"
            ),
        ),
        provider_recovery_generation=1,
        review=ReviewWebConfiguration(
            "https://review.example.invalid",
            "/run/owner-password",
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
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
    providers: Mapping[str, MetadataProvider | Callable[[], MetadataProvider]],
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
        value = providers[authority]
        return lambda name, *_args, **_kwargs: value() if callable(value) else value

    def never_dispatched(*_args: object, **_kwargs: object) -> object:
        pytest.fail("an excluded provider was built for an audiobook")

    with (
        patch.object(composition, "AudiobookshelfItemReader", _TargetReader),
        patch.object(composition, "_IsolatedAudioInspector", _AudioInspector),
        patch.object(composition, "audible_catalog_client", return_value=object()),
        patch.object(composition, "audnexus_client", return_value=object()),
        patch.object(composition, "audiosilo_client", return_value=object()),
        patch.object(composition, "itunes_search_client", return_value=object()),
        patch.object(composition, "google_books_client", side_effect=never_dispatched),
        patch.object(composition, "inventaire_client", side_effect=never_dispatched),
        patch.object(
            composition, "GoogleBooksMetadataProvider", side_effect=never_dispatched
        ),
        patch.object(
            composition, "InventaireMetadataProvider", side_effect=never_dispatched
        ),
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
            "SELECT stage,state,failure_code,failure_root,outcome "
            "FROM review_preparation_work"
        ).fetchone()
        progress = repository.review_pipeline_progress("primary", "fiction")
        remaining = repository.claim_review_pipeline_action(15)
        repository.close()

        # The plan was the ASIN one, and every connected provider in the
        # audiobook dispatch set was asked. The store is connected, enabled and
        # outside that set, so it was never asked at all (#1254).
        assert {"audible_catalog", "audnexus", "audiosilo"} == {
            authority for authority, _kind in calls
        }
        assert {ProviderQueryKind.ASIN} == {
            kind for _authority, kind in calls if kind is not ProviderQueryKind.NONE
        }
        assert [] == [value for value in calls if value[0] == "itunes_search"]
        # The report carries one observation per dispatched provider, and none
        # for the store, Google Books or Inventaire (#1247).
        assert 3 == len(published.providers)
        assert {
            "audible": ProviderStatus.CANDIDATES,
            "enrichment": ProviderStatus.CANDIDATES,
            "silo": ProviderStatus.CANDIDATES,
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
        assert "metadata-report-v53" == published.schema_version
        # Three authorities answered the ASIN lane with candidates, so the
        # recording's work identity is admitted and preparation queued the
        # proposal stage exactly as an ebook's does (#966).
        assert ("proposal", "queued", None, None, None) == work
        assert remaining is not None
        assert "proposal" == remaining.stage
        assert progress is not None
        assert 0 == progress.blocked
        assert 0 == progress.recorded
        assert 0 == progress.ready
        assert 1 == dict(progress.stages)["proposals"]


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
            "SELECT stage,state,outcome FROM review_preparation_work"
        ).fetchone()
        repository.close()

        statuses = {value.provider: value.status for value in published.providers}
        assert ProviderStatus.UNAVAILABLE is statuses["audible"]
        assert ProviderStatus.CANDIDATES is statuses["enrichment"]
        assert ProviderStatus.CANDIDATES is statuses["silo"]
        assert 3 == len(published.providers)
        # A degraded primary is still a published report, and the two
        # authorities that did answer still admit the recording, so the work
        # goes on to the proposal stage.
        assert ("proposal", "queued", None) == work


def test_an_unmatched_audiobook_records_its_evidence_and_completes(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    """No admitted identity is a first-class end, not a failure code.

    Nothing here can be compared against a proposal, so the report is the
    outcome: preparation completes on `evidence_only` in the publishing
    transaction, the scan counts the item as recorded rather than as something
    asking the owner to act, and no failure is reported (#940, #966).
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        providers = {
            authority: _FakeAudiobookProvider(
                name, authority, calls, status=ProviderStatus.NO_MATCH
            )
            for name, authority in (
                ("audible", "audible_catalog"),
                ("enrichment", "audnexus"),
                ("silo", "audiosilo"),
                ("store", "itunes_search"),
            )
        }
        serialized, repository = _run(root, providers)
        published = MetadataReport.from_json(serialized)
        work = repository._connection.execute(
            "SELECT stage,state,failure_code,failure_root,outcome "
            "FROM review_preparation_work"
        ).fetchone()
        progress = repository.review_pipeline_progress("primary", "fiction")
        remaining = repository.claim_review_pipeline_action(15)
        repository.close()

        assert 3 == len(published.providers)
        assert not any(observation.candidates for observation in published.providers)
        assert ("complete", "complete", None, None, "evidence_only") == work
        assert remaining is None
        assert progress is not None
        assert 0 == progress.blocked
        assert 0 == progress.ready
        assert 1 == progress.recorded
        assert 1 == dict(progress.stages)["recorded"]
        assert 0 == dict(progress.stages)["failed"]


def test_candidates_outside_the_corroboration_band_admit_no_recording(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    """Answered is not admitted: the band decides, on every lane.

    Each provider resolves the asserted ASIN and states a running time this
    recording does not have. A retained candidate is not an identity, so no
    proposal is formed and preparation completes on the recorded outcome
    exactly as an unanswered recording does (ADR-0061, #966).
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        providers = {
            authority: _FakeAudiobookProvider(
                name, authority, calls, duration="12000.000"
            )
            for name, authority in (
                ("audible", "audible_catalog"),
                ("enrichment", "audnexus"),
                ("silo", "audiosilo"),
                ("store", "itunes_search"),
            )
        }
        serialized, repository = _run(root, providers)
        published = MetadataReport.from_json(serialized)
        work = repository._connection.execute(
            "SELECT stage,state,failure_code,failure_root,outcome "
            "FROM review_preparation_work"
        ).fetchone()
        progress = repository.review_pipeline_progress("primary", "fiction")
        remaining = repository.claim_review_pipeline_action(15)
        repository.close()

        # The evidence is retained and browsable; what it is not is an answer
        # about which product this recording is.
        assert any(observation.candidates for observation in published.providers)
        assert ("complete", "complete", None, None, "evidence_only") == work
        assert remaining is None
        assert progress is not None
        assert 0 == progress.blocked
        assert 0 == progress.ready
        assert 1 == progress.recorded
        assert 0 == dict(progress.stages)["failed"]


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
    audible_url: str | None = _AUDIBLE_ART,
) -> dict[str, _FakeAudiobookProvider]:
    """The four audiobook authorities, with covers on the two that state one.

    The store answers the ASIN lane and states its own art here, so its absence
    from the report is the dispatch set's doing (#1254) rather than a fake
    that had nothing to offer.
    """
    return {
        "audible_catalog": _FakeAudiobookProvider(
            "audible",
            "audible_catalog",
            calls,
            cover_kind=CoverSourceKind.AUDIBLE_CATALOG,
            cover_url=audible_url,
        ),
        "audnexus": _FakeAudiobookProvider("enrichment", "audnexus", calls),
        "audiosilo": _FakeAudiobookProvider("silo", "audiosilo", calls),
        "itunes_search": _FakeAudiobookProvider(
            "store",
            "itunes_search",
            calls,
            cover_kind=CoverSourceKind.ITUNES_SEARCH,
            cover_url=_ITUNES_ART,
        ),
    }


def _published_with_covers(
    root: Path,
    calls: list[tuple[str, ProviderQueryKind]],
    payloads: dict[str, bytes],
    *,
    covers: bool = True,
    audible_url: str | None = _AUDIBLE_ART,
    fetched: list[str] | None = None,
) -> MetadataReport:
    def fetch(url: str, **_kwargs: object) -> RemoteCover:
        if fetched is not None:
            fetched.append(url)
        return RemoteCover(url, "image/jpeg", payloads[url])

    providers = _cover_providers(calls, audible_url=audible_url)
    with patch.object(composition, "fetch_remote_cover", side_effect=fetch):
        serialized, repository = _run(root, providers, covers=covers)
    repository.close()
    return MetadataReport.from_json(serialized)


def test_an_audiobook_acquires_its_provider_covers_with_perceptual_hashes(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    """Audible's art reaches the report hashed; the store's is never fetched.

    The store is connected with covers enabled, but it is outside the audiobook
    dispatch set (#1254), so the Audible catalog is the one cover authority
    acquired. The cross-authority hash rule stays proven in the cover-packet
    unit suite.
    """
    fetched: list[str] = []
    with tempfile.TemporaryDirectory() as temporary:
        published = _published_with_covers(
            Path(temporary),
            calls,
            _one_design_two_authorities(),
            fetched=fetched,
        )

    assert [_AUDIBLE_ART] == fetched
    kinds = {source.kind for source in published.cover_sources}
    assert CoverSourceKind.AUDIBLE_CATALOG in kinds
    assert CoverSourceKind.ITUNES_SEARCH not in kinds
    retrieved = [
        source
        for source in published.cover_sources
        if source.state is CoverSourceState.RETRIEVED
    ]
    assert [CoverSourceKind.AUDIBLE_CATALOG] == [source.kind for source in retrieved]
    hashes = [
        source.artifact.perceptual_hash
        for source in retrieved
        if source.artifact is not None
    ]
    assert 1 == len(hashes)
    assert all(value is not None and 16 == len(value) for value in hashes)


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
    # Audible is the dispatched cover authority, so its refused origin is the
    # one that reaches the report as a typed outcome.
    fetched: list[str] = []
    with tempfile.TemporaryDirectory() as temporary:
        published = _published_with_covers(
            Path(temporary),
            calls,
            {},
            covers=True,
            audible_url=None,
            fetched=fetched,
        )

    assert [] == fetched
    audible = next(
        source
        for source in published.cover_sources
        if source.kind is CoverSourceKind.AUDIBLE_CATALOG
    )
    assert CoverSourceState.UNSAFE_REMOTE is audible.state
    assert audible.artifact is None


@pytest.mark.parametrize(
    "origin,runtime",
    [
        (origin, runtime)
        for origin in ("legacy", "local", "target", "work")
        for runtime in (
            "absent",
            "healthy",
            "malformed",
            "cut",
            "wrong_work",
            "wrong_author",
        )
    ]
    + [("promoted", "healthy")],
)
def test_local_asin_chapter_runtime_fallback_reaches_an_owner_case(
    tmp_path: Path, runtime: str, origin: str
) -> None:
    import json

    import httpx

    from mechanical_librarian.adapters.evidence_extractors import process
    from mechanical_librarian.adapters.providers.audnexus_metadata import (
        AudnexusMetadataProvider,
    )
    from mechanical_librarian.application.metadata_report import (
        recording_identity_basis,
    )
    from mechanical_librarian.domain.audio_inspection import AudioInspectionOutcome
    from mechanical_librarian.domain.human_decision import HumanDecision
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from mechanical_librarian.domain.review_case import ReviewCase
    from tests.support.audiobook_providers import payload
    from tests.support.chapter_runtime import (
        QT_SPANS,
        SPANS,
        media,
        provider_chapters,
        qt_media,
        qt_provider_chapters,
        qt_recorded_probe,
        recorded_probe,
    )

    mount = tmp_path / "media"
    mount.mkdir()
    tagged = _ASIN if origin in {"legacy", "local"} else None
    (mount / "book.m4b").write_bytes(
        media()
        if origin == "legacy"
        else qt_media(asin=tagged, stated=origin == "promoted")
    )
    probe = recorded_probe() if origin == "legacy" else qt_recorded_probe(asin=tagged)
    if origin == "promoted":
        projection = json.loads(probe)
        projection["format"]["duration"] = "1748.000"
        probe = json.dumps(projection).encode()
    with patch.object(
        process,
        "run_probe_child",
        return_value=(
            probe,
            AudioInspectionOutcome.SUCCESS,
        ),
    ):
        inspected = composition._IsolatedAudioInspector().inspect(
            MediaMountConfiguration("audiobooks", str(mount), "/library", True),
            (TargetFile(_FILE_IDENTITY, "book.m4b", "m4b"),),
            268435456,
        )
    assert [
        (c.start_milliseconds, c.end_milliseconds) for c in inspected.audio.chapters
    ] == list(SPANS if origin == "legacy" else QT_SPANS)
    assert inspected.audio.chapter_rows_omitted is False
    book = payload("audnexus_book")
    book.pop("runtimeLengthMin", None)
    if runtime in {"healthy", "malformed"}:
        book["runtimeLengthMin"] = 29 if runtime == "healthy" else "invalid"
    if runtime == "wrong_work":
        book["title"] = "Different Work"
    if runtime == "wrong_author":
        book["authors"] = [{"name": "Different Author"}]
    chapters = provider_chapters() if origin == "legacy" else qt_provider_chapters()
    if runtime == "cut":
        chapters["chapters"][1]["lengthMs"] += 3000
        chapters["chapters"][2]["lengthMs"] -= 3000
    snapshot = _snapshot()
    if origin == "target":
        from dataclasses import replace

        snapshot = replace(
            snapshot,
            fields=tuple(
                TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, _ASIN)
                if field.destination is TargetDestination.ASIN
                else field
                for field in snapshot.fields
            ),
        )
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        import copy

        response = copy.deepcopy(
            chapters if request.url.path.endswith("/chapters") else book
        )
        requested_asin = request.url.path.split("/")[2]
        if request.url.path.endswith("/chapters"):
            if requested_asin != _ASIN:
                response["chapters"][1]["lengthMs"] += 6000
                response["chapters"][2]["lengthMs"] -= 6000
        else:
            response["asin"] = requested_asin
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=response
        )

    def provider() -> MetadataProvider:
        return AudnexusMetadataProvider(
            "enrichment",
            httpx.Client(transport=httpx.MockTransport(handle)),
            262144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )

    calls: list[tuple[str, ProviderQueryKind]] = []
    providers: dict[str, MetadataProvider | Callable[[], MetadataProvider]] = {
        authority: _FakeAudiobookProvider(
            alias, authority, calls, status=ProviderStatus.NO_MATCH
        )
        for authority, alias in (
            ("audible_catalog", "audible"),
            ("audiosilo", "silo"),
            ("itunes_search", "store"),
        )
    }
    providers["audnexus"] = provider
    if origin in {"work", "promoted"}:
        import copy

        from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
            AudibleCatalogMetadataProvider,
        )

        product = copy.deepcopy(payload("audible_search")["products"][0])
        product.pop("runtime_length_min", None)
        if origin == "promoted":
            product["runtime_length_min"] = 29
        product["asin"] = _ASIN
        other = {**product, "asin": "B000000002"}
        search_client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "products": [product, other] if origin == "work" else [product]
                    },
                )
            )
        )
        providers["audible_catalog"] = AudibleCatalogMetadataProvider(
            "audible",
            search_client,
            262144,
            5.0,
            marketplaces=("uk",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
    with (
        patch(__name__ + "._audio_result", return_value=inspected),
        patch(__name__ + "._CONTENT_DIGEST", inspected.content_digest),
        patch(__name__ + "._snapshot", return_value=snapshot),
    ):
        serialized, repository = _run(tmp_path, providers)
    try:
        report = MetadataReport.from_json(serialized)
        expected_asins = (_ASIN, "B000000002") if origin == "work" else (_ASIN,)
        assert set(requests) == {
            path
            for asin in expected_asins
            for path in (f"/books/{asin}", f"/books/{asin}/chapters")
        }
        assert len(requests) == 2 * len(expected_asins)
        if origin not in {"legacy", "promoted"}:
            assert not any(
                claim.kind is ClaimKind.DURATION for claim in report.local_claims
            )
        if origin == "work":
            search = next(
                value for value in report.providers if value.provider == "audible"
            )
            assert search.status is ProviderStatus.NO_MATCH and search.candidates == ()
            assert len(search.recording_lookup_candidates) == 2
            from mechanical_librarian.application.metadata_report import promoted_asins

            assert promoted_asins((search,)) == ()
        if origin == "promoted":
            from mechanical_librarian.application.metadata_report import promoted_asins

            search = next(
                value for value in report.providers if value.provider == "audible"
            )
            assert promoted_asins((search,)) == (_ASIN,)
            assert search.recording_lookup_candidates == ()
        state = repository._connection.execute(
            "SELECT stage,state,outcome FROM review_preparation_work"
        ).fetchone()
        for table in ("automatic_decisions", "field_effects", "field_effect_consents"):
            assert repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
        if runtime in {"wrong_work", "wrong_author"}:
            assert state == ("complete", "complete", "evidence_only")
            assert recording_identity_basis(report) is None
            assert (
                repository._connection.execute(
                    "SELECT count(*) FROM review_cases"
                ).fetchone()[0]
                == 0
            )
            return
        assert state[:2] == ("proposal", "queued")
        basis = recording_identity_basis(report)
        assert (basis is not None) is (
            runtime not in {"malformed", "cut"}
            and (runtime == "absent" or origin not in {"legacy", "promoted"})
        )
        replay = MetadataReport.from_json(
            repository.get_proposal_input(report.report_id)
        )
        assert recording_identity_basis(replay) == basis
        proposal = evaluate_report(replay)
        repository.publish_proposal(proposal)
        admission = json.loads(repository.admit_review_case(proposal))
        assert admission["status"] == "admitted"
        envelope = json.loads(repository.get_review_case(admission["review_case_id"]))
        assert envelope["current"] is True
        case = ReviewCase.create_from_data(envelope["case"])
        work_only = runtime in {"malformed", "cut"}
        destination = "description" if work_only else "narrators"
        if work_only:
            assert {p["destination"] for p in case.actionable} <= {
                "description",
                "genres",
            }
            assert basis is None
        assert destination in {p["destination"] for p in case.actionable}
        narrator = next(p for p in case.proposals if p["destination"] == destination)
        proposal_id = narrator["proposal_id"]
        assert isinstance(proposal_id, str)
        decision = HumanDecision.create(
            case,
            proposal_id=proposal_id,
            target_fingerprint=case.target_fingerprint,
            destination=destination,
            actor="owner",
            outcome="accept",
            selected_value=narrator["result"]["alternatives"][0]["value"],
            idempotency_key="chapter-runtime-owner-0001",
            occurred_at=1000,
        )
        stored = repository.record_human_decision(decision)
        assert HumanDecision.from_json(stored) == decision
        assert len(repository.get_review_case_decisions(case.review_case_id)) == 1
        assert (
            repository._connection.execute(
                "SELECT count(*) FROM automatic_decisions"
            ).fetchone()[0]
            == 0
        )
        repository.close()
        repository = open_repository(tmp_path / "reports.sqlite", create=False)
        assert MetadataReport.from_json(repository.get(report.report_id)) == report
        reopened = json.loads(repository.get_review_case(case.review_case_id))
        assert reopened["current"] is True
        assert ReviewCase.create_from_data(reopened["case"]) == case
        assert len(repository.get_review_case_decisions(case.review_case_id)) == 1
        for table in ("automatic_decisions", "field_effects", "field_effect_consents"):
            assert repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize(
    "origin,known_runtime,collision",
    [
        (origin, known, collision)
        for origin in ("target", "work")
        for known in (False, True)
        for collision in (
            "cross_asin",
            "runtime_band",
            "runtime_tolerance",
            "language",
            "language_unknown",
        )
    ]
    + [("local", False, "cross_asin")],
)
def test_recording_collision_cannot_add_owner_choices(
    tmp_path: Path, collision: str, known_runtime: bool, origin: str
) -> None:
    """A valid sibling admits review without laundering another recording."""
    import json
    from dataclasses import replace

    import httpx

    from mechanical_librarian.adapters.evidence_extractors import process
    from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
        AudibleCatalogMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.audnexus_metadata import (
        AudnexusMetadataProvider,
    )
    from mechanical_librarian.application.metadata_report import (
        recording_identity_basis,
    )
    from mechanical_librarian.domain.audio_inspection import AudioInspectionOutcome
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from mechanical_librarian.domain.review_case import ReviewCase
    from tests.support import chapter_runtime
    from tests.support.audiobook_providers import payload

    good, requested_bad, returned_bad = "B000000001", "B000000002", "B000000003"
    vector = (137, 223, 349, 461, 587, 719)
    spans = []
    endpoint = 0
    for seconds in vector:
        spans.append((endpoint, endpoint + seconds * 1000))
        endpoint += seconds * 1000
    mount = tmp_path / "media"
    mount.mkdir()
    with (
        patch.object(chapter_runtime, "QT_END", endpoint),
        patch.object(chapter_runtime, "QT_SPANS", tuple(spans)),
    ):
        (mount / "book.m4b").write_bytes(
            chapter_runtime.qt_media(
                stated=known_runtime, asin=good if origin == "local" else None
            )
        )
    probe = json.loads(
        chapter_runtime.qt_recorded_probe(asin=good if origin == "local" else None)
    )
    if known_runtime:
        probe["format"]["duration"] = "2476.000"
    if collision in {"language", "language_unknown"}:
        probe["format"].setdefault("tags", {})["language"] = "eng"
    for chapter, (start, end) in zip(probe["chapters"], spans, strict=True):
        chapter["start_time"] = f"{start / 1000:.3f}"
        chapter["end_time"] = f"{end / 1000:.3f}"
    with patch.object(
        process,
        "run_probe_child",
        return_value=(json.dumps(probe).encode(), AudioInspectionOutcome.SUCCESS),
    ):
        inspected = composition._IsolatedAudioInspector().inspect(
            MediaMountConfiguration("audiobooks", str(mount), "/library", True),
            (TargetFile(_FILE_IDENTITY, "book.m4b", "m4b"),),
            268435456,
        )
    assert [
        (c.start_milliseconds, c.end_milliseconds) for c in inspected.audio.chapters
    ] == spans
    assert any(c.kind is ClaimKind.DURATION for c in inspected.claims) is known_runtime
    product = payload("audible_search")["products"][0]
    product.pop("runtime_length_min", None)
    products = [{**product, "asin": value} for value in (good, requested_bad)]
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        response: dict[str, object]
        if request.url.host == "api.audible.com":
            response = (
                {"products": products}
                if request.url.path.endswith("/products")
                else {"product": products[0]}
            )
        else:
            asin = request.url.path.split("/")[2]
            bad = asin == requested_bad or origin == "local"
            if request.url.path.endswith("/chapters"):
                response = {
                    "runtimeLengthSec": 2494
                    if bad and collision in {"runtime_band", "runtime_tolerance"}
                    else 2476,
                    "chapters": [
                        {
                            "lengthMs": v * 1000
                            + (
                                (3900 if collision == "runtime_band" else 2900)
                                if bad
                                and collision in {"runtime_band", "runtime_tolerance"}
                                else 0
                            )
                        }
                        for v in vector
                    ],
                }
            else:
                response = payload("audnexus_book")
                response["asin"] = (
                    returned_bad if bad and collision == "cross_asin" else asin
                )
                response["runtimeLengthMin"] = (
                    43
                    if bad and collision in {"runtime_band", "runtime_tolerance"}
                    else 41
                )
                if collision in {"language", "language_unknown"}:
                    response["language"] = (
                        ("spa" if collision == "language" else "und") if bad else "eng"
                    )
                response["narrators"] = [
                    {
                        "name": "Different Recording Narrator"
                        if bad
                        else "Matching Recording Narrator"
                    }
                ]
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=response
        )

    def audible() -> MetadataProvider:
        return AudibleCatalogMetadataProvider(
            "audible",
            httpx.Client(transport=httpx.MockTransport(handle)),
            262144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )

    def audnexus() -> MetadataProvider:
        return AudnexusMetadataProvider(
            "enrichment",
            httpx.Client(transport=httpx.MockTransport(handle)),
            262144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )

    calls: list[tuple[str, ProviderQueryKind]] = []
    providers: dict[str, MetadataProvider | Callable[[], MetadataProvider]] = {
        "audible_catalog": audible,
        "audnexus": audnexus,
        "audiosilo": _FakeAudiobookProvider(
            "silo", "audiosilo", calls, status=ProviderStatus.NO_MATCH
        ),
        "itunes_search": _FakeAudiobookProvider(
            "store", "itunes_search", calls, status=ProviderStatus.NO_MATCH
        ),
    }
    snapshot = _snapshot()
    if origin == "target":
        snapshot = replace(
            snapshot,
            fields=tuple(
                TargetField(
                    TargetDestination.ASIN,
                    TargetFieldState.VALUE,
                    good,
                )
                if field.destination is TargetDestination.ASIN
                else field
                for field in snapshot.fields
            ),
        )
    with (
        patch(__name__ + "._snapshot", return_value=snapshot),
        patch(__name__ + "._audio_result", return_value=inspected),
        patch(__name__ + "._CONTENT_DIGEST", inspected.content_digest),
    ):
        serialized, repository = _run(tmp_path, providers)
    try:
        report = MetadataReport.from_json(serialized)
        expected_asins = (good,) if origin == "local" else (good, requested_bad)
        assert {
            path
            for asin in expected_asins
            for path in (f"/books/{asin}", f"/books/{asin}/chapters")
        } <= set(requests)
        for table in ("automatic_decisions", "field_effects", "field_effect_consents"):
            assert repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
        if origin == "local":
            assert recording_identity_basis(report) is None
            enrichment = next(o for o in report.providers if o.provider == "enrichment")
            assert enrichment.status is ProviderStatus.MALFORMED
            assert enrichment.candidates == ()
            assert repository._connection.execute(
                "SELECT count(*) FROM review_cases"
            ).fetchone() == (0,)
            return
        assert (recording_identity_basis(report) is not None) is (not known_runtime)
        replay = MetadataReport.from_json(
            repository.get_proposal_input(report.report_id)
        )
        proposal = evaluate_report(replay)
        repository.publish_proposal(proposal)
        admission = json.loads(repository.admit_review_case(proposal))
        assert admission["status"] == "admitted"
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(admission["review_case_id"]))["case"]
        )
        alternatives = json.dumps(
            {p["destination"]: p["result"]["alternatives"] for p in case.proposals}
        )
        assert "Matching Recording Narrator" in alternatives
        permitted = collision in {"runtime_tolerance", "language_unknown"}
        assert ("Different Recording Narrator" in alternatives) is permitted
        assert (
            (returned_bad if collision == "cross_asin" else requested_bad)
            in alternatives
        ) is permitted
        if collision != "cross_asin":
            from mechanical_librarian.domain.audiobook_corroboration import (
                candidate_recording_runtime_veto,
            )

            enrichment = next(p for p in replay.providers if p.provider == "enrichment")
            other = next(
                c
                for c in enrichment.candidates
                if any(
                    c.kind is ClaimKind.ASIN and c.value == requested_bad
                    for c in c.claims
                )
            )
            assert candidate_recording_runtime_veto(
                other, replay.local_claims, replay.audio
            ) is (collision == "runtime_band")
            if collision == "language":
                from mechanical_librarian.domain.metadata_report import (
                    recording_variant_conflict,
                )

                assert (
                    recording_variant_conflict(
                        other, replay.local_claims, replay.target.current_title or ""
                    )
                    == "recording_language_conflict"
                )
            assert any(
                claim.value == "Different Recording Narrator" for claim in other.claims
            )
            assert any(claim.kind is ClaimKind.TITLE for claim in other.claims)
            assert any(claim.kind is ClaimKind.CREATOR for claim in other.claims)

            repository.close()
            repository = open_repository(
                tmp_path / "reports.sqlite", create=False, cross_thread=True
            )
            assert (
                MetadataReport.from_json(
                    repository.get_proposal_input(report.report_id)
                )
                == replay
            )
            from contextlib import nullcontext

            from mechanical_librarian.application.review_web import (
                InspectCurrentReviewCase,
                RecordHumanDecision,
                ReviewWebError,
            )

            narrator = next(
                p for p in case.proposals if p["destination"] == "narrators"
            )
            proposal_id = narrator["proposal_id"]
            assert type(proposal_id) is str
            command = RecordHumanDecision(repository, lambda: 1000)
            with (
                nullcontext()
                if permitted
                else pytest.raises(ReviewWebError, match="invalid_selection")
            ):
                result = command.run(
                    case.review_case_id,
                    proposal_id=proposal_id,
                    target_fingerprint=case.target_fingerprint,
                    destination="narrators",
                    actor="owner",
                    outcome="accept",
                    selected_value={
                        "kind": "list",
                        "value": ["Different Recording Narrator"],
                    },
                    idempotency_key="recording-collision-owner-0001",
                )
                assert result["outcome"] == "accept"
            detail = InspectCurrentReviewCase(repository).run(case.review_case_id)
            projected = next(
                f for f in detail["fields"] if f["destination"] == "narrators"
            )
            assert (
                {"kind": "list", "value": ["Different Recording Narrator"]}
                in projected["proposed_values"]
            ) is permitted
            from argon2 import PasswordHasher
            from fastapi.testclient import TestClient

            from tests.unit.review_app_fixture import create_transport_test_app

            password = tmp_path / "owner.hash"
            password.write_text(PasswordHasher().hash("correct horse"))
            password.chmod(0o600)
            app = create_transport_test_app(
                repository,
                ReviewWebConfiguration(
                    tls_certificate_file="/synthetic/review.crt",
                    tls_private_key_file="/synthetic/review.key",
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(password),
                ),
                clock=lambda: 1000,
            )
            with TestClient(app, base_url="https://review.test") as client:
                assert (
                    client.post(
                        "/api/v1/session/login",
                        json={"password": "correct horse"},
                        headers={"Origin": "https://review.test"},
                    ).status_code
                    == 200
                )
                response = client.get(f"/api/v1/review-cases/{case.review_case_id}")
                assert response.status_code == 200
                api_field = next(
                    field
                    for field in response.json()["fields"]
                    if field["destination"] == "narrators"
                )
                assert api_field["proposed_values"] == projected["proposed_values"]
                evidence = client.get(
                    f"/api/v1/review-cases/{case.review_case_id}/evidence"
                )
                assert evidence.status_code == 200
                evidence_items = evidence.json()["items"]
                cursor = evidence.json()["next_cursor"]
                while cursor is not None:
                    page = client.get(
                        f"/api/v1/review-cases/{case.review_case_id}/evidence",
                        params={"cursor": cursor},
                    )
                    assert page.status_code == 200
                    evidence_items.extend(page.json()["items"])
                    cursor = page.json()["next_cursor"]
                retained = [
                    item
                    for item in evidence_items
                    if "Different Recording Narrator" in item["observation"]
                ]
                assert len(retained) == 1
                if not permitted:
                    claim = next(c for c in other.claims if c.role == "narrator")
                    assert retained[0]["role"] == "external_observation"
                    assert retained[0]["destinations"] == ["narrators"]
                    assert "not an eligible choice" in retained[0]["observation"]
                    explanation = (
                        "Recording language conflicts with this item."
                        if collision == "language"
                        else "Recording timing is invalid or conflicts"
                    )
                    assert explanation in retained[0]["observation"]
                    if collision == "language":
                        assert "Recording timing" not in retained[0]["observation"]
                    assert claim.reference not in retained[0]["observation"]
                    assert other.identity not in retained[0]["observation"]
                    assert (
                        f"Scope: {claim.scope.value}; state: {claim.state.value}"
                        in retained[0]["observation"]
                    )
            if not permitted:
                from types import SimpleNamespace

                from mechanical_librarian.adapters.field_effect_authority import (
                    PersistedFieldEffectAuthority,
                )
                from tests.support.automatic_decisions import _automatic_decisions

                automatic_home = tmp_path / "automatic"
                automatic_home.mkdir(mode=0o700)
                automatic_repository = open_repository(automatic_home / "state.sqlite")
                try:
                    decisions = _automatic_decisions(
                        SimpleNamespace(repository=automatic_repository),
                        replay,
                        "contributor_names",
                        "canonical",
                        qualified=True,
                    )
                    automatic = next(
                        d for d in decisions if d.destination == "narrators"
                    )
                    assert PersistedFieldEffectAuthority(automatic_repository).resolve(
                        automatic
                    ).decision_value == {
                        "kind": "list",
                        "value": ["Matching Recording Narrator"],
                    }
                finally:
                    automatic_repository.close()
        for table in ("automatic_decisions", "field_effects", "field_effect_consents"):
            assert repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone() == (0,)
    finally:
        repository.close()


# #1255: the second title source, over the real Audible and iTunes adapters and
# the real audio inspector. Every name here is synthetic.
_SERIES = "Series Name"
_LEAF_FOLDER = "03 - The Real Title (2019) [B0XXXXXXX] - Narrator Name"
_CONTRACT_FOLDER = "Series Name 03 - The Real Title (2019) [B0XXXXXXX] - Narrator Name"


def _alternative_flow(
    root: Path,
    folder: str,
    *,
    title: str = _SERIES,
    runtime_minutes: int = 970,
    asserted_answers: bool = False,
) -> tuple[MetadataReport, tuple[object, ...], list[str], list[str]]:
    """Publish one single-file audiobook whose folder leaf names its title."""
    import copy

    import httpx

    from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
        AudibleCatalogMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.itunes_search_metadata import (
        ITunesSearchMetadataProvider,
    )
    from mechanical_librarian.domain.audio_inspection import (
        AudioEvidenceSurface,
        AudioInspection,
        AudioInspectionFact,
        AudioInspectionField,
        AudioInspectionOutcome,
    )
    from mechanical_librarian.domain.metadata_report import TargetSeries
    from tests.support.audiobook_providers import payload

    relative = f"{folder}/book.m4b"
    mount = root / "media"
    (mount / folder).mkdir(parents=True)
    (mount / relative).write_bytes(b"synthetic recording")

    def child(_descriptor, source, _limits, **_arguments) -> AudioInspection:
        return AudioInspection(
            AudioInspectionOutcome.SUCCESS,
            source,
            (
                AudioInspectionFact(
                    AudioInspectionField.DURATION,
                    source,
                    _LOCAL_DURATION,
                    _LOCAL_DURATION,
                    _LOCAL_DURATION,
                    AudioEvidenceSurface.CONTAINER_FORMAT,
                ),
            ),
        )

    target_file = TargetFile(_FILE_IDENTITY, relative, "m4b")
    with patch.object(composition, "run_audio_inspector_child", child):
        inspected = composition._IsolatedAudioInspector().inspect(
            MediaMountConfiguration("audiobooks", str(mount), "/library", True),
            (target_file,),
            268435456,
        )
    replaced = {
        TargetDestination.AUTHORS: TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Andy Weir",)
        ),
        TargetDestination.SERIES: TargetField(
            TargetDestination.SERIES,
            TargetFieldState.VALUE,
            (TargetSeries(_SERIES, "3"),),
        ),
    }
    snapshot = TargetSnapshot(
        "audiobook",
        title,
        (target_file,),
        fields=tuple(
            replaced.get(field.destination, field)
            for field in default_target_fields(title)
        ),
    )

    def product(name: str) -> dict[str, object]:
        value = copy.deepcopy(payload("audible_search")["products"][0])
        value["title"] = name
        value["runtime_length_min"] = runtime_minutes
        return value

    audible_titles: list[str] = []

    def audible(request: httpx.Request) -> httpx.Response:
        asked = request.url.params["title"]
        audible_titles.append(asked)
        products = [product(asked)] if asked != title or asserted_answers else []
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"total_results": len(products), "products": products},
        )

    store_terms: list[str] = []

    def store(request: httpx.Request) -> httpx.Response:
        store_terms.append(request.url.params["term"])
        return httpx.Response(
            200,
            headers={"content-type": "text/javascript; charset=utf-8"},
            json={"resultCount": 0, "results": []},
        )

    def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    calls: list[tuple[str, ProviderQueryKind]] = []
    providers: dict[str, MetadataProvider | Callable[[], MetadataProvider]] = {
        "audible_catalog": lambda: AudibleCatalogMetadataProvider(
            "audible",
            client(audible),
            262144,
            5.0,
            marketplaces=("uk",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        ),
        "itunes_search": lambda: ITunesSearchMetadataProvider(
            "store",
            client(store),
            262144,
            5.0,
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        ),
        "audnexus": _FakeAudiobookProvider(
            "enrichment", "audnexus", calls, status=ProviderStatus.NO_MATCH
        ),
        "audiosilo": _FakeAudiobookProvider(
            "silo", "audiosilo", calls, status=ProviderStatus.NO_MATCH
        ),
    }
    with (
        patch(__name__ + "._audio_result", return_value=inspected),
        patch(__name__ + "._CONTENT_DIGEST", inspected.content_digest),
        patch(__name__ + "._snapshot", return_value=snapshot),
    ):
        serialized, repository = _run(root, providers)
    try:
        work = repository._connection.execute(
            "SELECT stage,state,outcome FROM review_preparation_work"
        ).fetchone()
    finally:
        repository.close()
    return MetadataReport.from_json(serialized), work, audible_titles, store_terms


def _audible(report: MetadataReport):
    return next(value for value in report.providers if value.provider == "audible")


def test_a_folder_leaf_title_reaches_an_admitted_recording(tmp_path: Path) -> None:
    from mechanical_librarian.domain.proposal_set import (
        AudiobookReviewScope,
        audiobook_review_scope,
        provider_candidate_represents_target_recording,
    )

    report, work, audible_titles, store_terms = _alternative_flow(
        tmp_path, _LEAF_FOLDER
    )

    # The asserted title is the recorded series name, so only the leaf is asked.
    assert report.audio is not None
    assert [("path_leaf", "The Real Title")] == [
        (value.basis, value.title) for value in report.audio.discovery_titles
    ]
    assert ["The Real Title"] == audible_titles
    assert [] == store_terms
    audible = _audible(report)
    assert ProviderStatus.CANDIDATES is audible.status
    (candidate,) = audible.candidates
    assert provider_candidate_represents_target_recording(report, audible, candidate)
    assert AudiobookReviewScope.RECORDING is audiobook_review_scope(report)
    assert ("proposal", "queued", None) == work


def test_the_same_candidate_outside_the_band_is_refused_and_proposes_nothing(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.domain.failure_diagnostics import FailureCategory

    report, work, audible_titles, _terms = _alternative_flow(
        tmp_path, _LEAF_FOLDER, runtime_minutes=900
    )
    audible = _audible(report)

    assert ["The Real Title"] == audible_titles
    assert ProviderStatus.NO_MATCH is audible.status
    assert () == audible.candidates
    assert FailureCategory.WORK_IDENTITY_REFUSED in {
        value.category for value in audible.diagnostics
    }
    assert ("complete", "complete", "evidence_only") == work


def test_an_asserted_title_that_answers_never_asks_the_alternative(
    tmp_path: Path,
) -> None:
    report, _work, audible_titles, store_terms = _alternative_flow(
        tmp_path, _LEAF_FOLDER, title="Asserted Title", asserted_answers=True
    )

    assert ["Asserted Title"] == audible_titles
    # The store would have searched the asserted title before #1254; it is no
    # longer dispatched for audiobooks, so it is asked nothing.
    assert [] == store_terms
    assert 1 == len(_audible(report).lanes)
    assert ProviderStatus.CANDIDATES is _audible(report).status


@pytest.mark.parametrize(
    "folder",
    (
        "\u202eSeries Name 03 - The Real Title",
        "The Real\u200bTitle",
        "The Real Title\u0007",
        "(2019) [tag]",
    ),
)
def test_a_hostile_folder_name_becomes_no_query_text(
    tmp_path: Path, folder: str
) -> None:
    report, _work, audible_titles, store_terms = _alternative_flow(
        tmp_path, folder, title="Asserted Title"
    )

    assert report.audio is not None and () == report.audio.discovery_titles
    assert ["Asserted Title"] == audible_titles
    assert not any("Real" in value for value in (*audible_titles, *store_terms))


def test_the_contract_folder_shape_asks_its_right_hand_title_but_cannot_admit_it(
    tmp_path: Path,
) -> None:
    """Pinned: the series-prefixed leaf keeps its prefix as the lane's title.

    The audiobook ladder asks "The Real Title", but admission is unchanged and
    replays against the lane's own title, which still carries the series
    prefix, so the product is refused and nothing is proposed.
    """
    report, work, audible_titles, _terms = _alternative_flow(tmp_path, _CONTRACT_FOLDER)

    assert report.audio is not None
    assert ["Series Name 03 - The Real Title"] == [
        value.title for value in report.audio.discovery_titles
    ]
    assert ["The Real Title"] == audible_titles
    assert ProviderStatus.NO_MATCH is _audible(report).status
    assert ("complete", "complete", "evidence_only") == work


def test_an_ebook_plan_carries_no_alternative_and_no_audiobook_family() -> None:
    from mechanical_librarian.application.metadata_report import (
        metadata_discovery_plan,
    )
    from mechanical_librarian.domain.work_identity import (
        WorkTitleTransformationFamily,
        work_query_ladder,
    )

    title = "Series Name - The Real Title"
    target = TargetSnapshot("ebook", title, (), fields=_target_fields())

    assert all(
        lane.title_basis is None for lane in metadata_discovery_plan(target, ()).lanes
    )
    assert not {
        WorkTitleTransformationFamily.LEADING_SEQUENCE_TOKEN,
        WorkTitleTransformationFamily.RIGHT_HAND_SEGMENT,
    } & {
        family
        for query in work_query_ladder(title, ("Andy Weir",))
        for family in query.transformations
    }


# #1257: a refused tight-band product's ASIN reaches the per-ASIN lanes as a
# lookup-only hint, over the real Audible and Audnexus adapters. Every name
# and identifier here is synthetic.
_HINT_ASIN = "B000000011"


def _hint_flow(
    root: Path,
    audible: Callable[[str], list[dict[str, object]]],
    *,
    audnexus_titles: Mapping[str, str] | None = None,
    audnexus_minutes: Mapping[str, int] | None = None,
    alternative: str | None = None,
    target_asin: str | None = None,
) -> tuple[MetadataReport, tuple[object, ...], list[str], dict[str, int]]:
    """Publish one runtime-bearing audiobook, asserting at most a target ASIN."""
    import copy
    from dataclasses import replace

    import httpx

    from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
        AudibleCatalogMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.audnexus_metadata import (
        AudnexusMetadataProvider,
    )
    from mechanical_librarian.domain.metadata_report import AudioDiscoveryTitle
    from tests.support.audiobook_providers import payload

    root.mkdir(mode=0o700, exist_ok=True)
    inspected = AudioInspectionResult(
        _CONTENT_DIGEST,
        (_local_claim(ClaimKind.DURATION, _LOCAL_DURATION, 1),),
        AudioFacts(
            embedded_cover=AudioEmbeddedCover(False),
            tracks=(
                AudioTrackDigest(1, _FILE_IDENTITY, _CONTENT_DIGEST, "m4b", "book.m4b"),
            ),
        ),
    )
    if alternative is not None:
        inspected = replace(
            inspected,
            audio=replace(
                inspected.audio,
                discovery_titles=(AudioDiscoveryTitle("path_leaf", alternative),),
            ),
        )

    def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    def search(request: httpx.Request) -> httpx.Response:
        products = audible(request.url.params["title"])
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"total_results": len(products), "products": products},
        )

    requests: list[str] = []

    def books(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/chapters"):
            return httpx.Response(404, json={})
        asin = request.url.path.split("/")[2]
        book = copy.deepcopy(payload("audnexus_book"))
        book["asin"] = asin
        book["title"] = (audnexus_titles or {}).get(asin, "Project Hail Mary")
        if asin in (audnexus_minutes or {}):
            book["runtimeLengthMin"] = (audnexus_minutes or {})[asin]
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=book
        )

    def adapter(
        kind: type, name: str, handler: Callable
    ) -> Callable[[], MetadataProvider]:
        return lambda: kind(
            name,
            client(handler),
            262144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1700000000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )

    calls: list[tuple[str, ProviderQueryKind]] = []
    providers: dict[str, MetadataProvider | Callable[[], MetadataProvider]] = {
        "audible_catalog": adapter(AudibleCatalogMetadataProvider, "audible", search),
        "audnexus": adapter(AudnexusMetadataProvider, "enrichment", books),
        "audiosilo": _FakeAudiobookProvider(
            "silo", "audiosilo", calls, status=ProviderStatus.NO_MATCH
        ),
        "itunes_search": _FakeAudiobookProvider(
            "store",
            "itunes_search",
            calls,
            status=ProviderStatus.NO_MATCH,
            lane_kinds=frozenset({ProviderQueryKind.WORK}),
        ),
    }
    snapshot = _snapshot()
    if target_asin is not None:
        snapshot = replace(
            snapshot,
            fields=tuple(
                TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, target_asin)
                if field.destination is TargetDestination.ASIN
                else field
                for field in snapshot.fields
            ),
        )
    with (
        patch(__name__ + "._audio_result", return_value=inspected),
        patch(__name__ + "._CONTENT_DIGEST", inspected.content_digest),
        patch(__name__ + "._snapshot", return_value=snapshot),
    ):
        serialized, repository = _run(root, providers)
    try:
        work = repository._connection.execute(
            "SELECT stage,state,outcome FROM review_preparation_work"
        ).fetchone()
        rows = {
            table: repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            for table in ("review_cases", "automatic_decisions", "field_effects")
        }
    finally:
        repository.close()
    return MetadataReport.from_json(serialized), work, requests, rows


def _audible_product(asin: str, title: str, minutes: int = 970) -> dict[str, object]:
    return {
        "asin": asin,
        "title": title,
        "authors": [{"name": "Andy Weir"}],
        "runtime_length_min": minutes,
    }


def _hinted_lane(report: MetadataReport):
    audible = _audible(report)
    return next(
        lane
        for lane in audible.lanes
        if lane.kind is ProviderQueryKind.WORK and lane.recording_lookup_hints
    )


def test_a_hint_whose_mirror_states_an_admissible_title_admits(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.domain.proposal_set import (
        AudiobookReviewScope,
        audiobook_review_scope,
    )

    report, work, requests, _rows = _hint_flow(
        tmp_path, lambda _title: [_audible_product(_HINT_ASIN, "Wrong Title")]
    )

    (hint,) = _hinted_lane(report).recording_lookup_hints
    assert (_HINT_ASIN, 58_200) == (hint.asin, hint.runtime_seconds)
    assert f"/books/{_HINT_ASIN}" in requests
    enrichment = next(
        value for value in report.providers if value.provider == "enrichment"
    )
    assert ProviderStatus.CANDIDATES is enrichment.status
    assert AudiobookReviewScope.RECORDING is audiobook_review_scope(report)
    assert ("proposal", "queued", None) == work


def test_a_hint_whose_mirror_repeats_the_refused_title_leaves_nothing(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.application.metadata_report import (
        recording_identity_basis,
    )

    report, work, requests, rows = _hint_flow(
        tmp_path,
        lambda _title: [_audible_product(_HINT_ASIN, "Wrong Title")],
        audnexus_titles={_HINT_ASIN: "Wrong Title"},
    )

    assert _hinted_lane(report).recording_lookup_hints
    assert f"/books/{_HINT_ASIN}" in requests
    assert ("complete", "complete", "evidence_only") == work
    assert {"review_cases": 0, "automatic_decisions": 0, "field_effects": 0} == rows
    assert recording_identity_basis(report) is None


@pytest.mark.parametrize("runtime", ["in_band", "unstated"])
def test_a_target_fed_asin_naming_another_work_leaves_nothing(
    tmp_path: Path, runtime: str
) -> None:
    from mechanical_librarian.application.metadata_report import (
        recording_identity_basis,
    )
    from mechanical_librarian.domain.proposal_set import audiobook_review_scope

    report, work, requests, rows = _hint_flow(
        tmp_path,
        lambda _title: [],
        audnexus_titles={_HINT_ASIN: "Wrong Title"},
        audnexus_minutes={} if runtime == "in_band" else {_HINT_ASIN: 0},
        target_asin=_HINT_ASIN,
    )

    assert f"/books/{_HINT_ASIN}" in requests
    assert not any(lane.recording_lookup_hints for lane in _audible(report).lanes)
    assert ("complete", "complete", "evidence_only") == work
    assert {"review_cases": 0, "automatic_decisions": 0, "field_effects": 0} == rows
    assert recording_identity_basis(report) is None
    assert audiobook_review_scope(report) is None


def test_a_hint_admitted_on_title_but_out_of_band_stays_work_only(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.application.metadata_report import (
        admitted_audiobook_identity,
        recording_identity_basis,
    )
    from mechanical_librarian.domain.proposal_set import (
        AudiobookReviewScope,
        audiobook_review_scope,
    )

    report, _work, requests, _rows = _hint_flow(
        tmp_path,
        lambda _title: [_audible_product(_HINT_ASIN, "Wrong Title")],
        audnexus_minutes={_HINT_ASIN: 900},
    )

    assert _hinted_lane(report).recording_lookup_hints
    assert f"/books/{_HINT_ASIN}" in requests
    assert not admitted_audiobook_identity(report)
    assert recording_identity_basis(report) is None
    assert AudiobookReviewScope.WORK is audiobook_review_scope(report)


def test_an_out_of_band_refused_product_is_never_looked_up(tmp_path: Path) -> None:
    report, work, requests, _rows = _hint_flow(
        tmp_path, lambda _title: [_audible_product(_HINT_ASIN, "Wrong Title", 900)]
    )

    assert all(
        not lane.recording_lookup_hints
        for provider in report.providers
        for lane in provider.lanes
    )
    assert [] == requests
    assert ("complete", "complete", "evidence_only") == work


def test_a_hint_beside_an_admitted_product_leaves_its_outcome_unchanged(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.domain.proposal_set import (
        audiobook_review_scope,
        evaluate_report,
    )

    admitted = _audible_product(_ASIN, "Project Hail Mary")
    titles = {_HINT_ASIN: "Wrong Title"}
    report, work, requests, _rows = _hint_flow(
        tmp_path / "hinted",
        lambda _title: [admitted, _audible_product(_HINT_ASIN, "Wrong Title")],
        audnexus_titles=titles,
    )
    baseline, base_work, base_requests, _ = _hint_flow(
        tmp_path / "baseline", lambda _title: [admitted], audnexus_titles=titles
    )

    lane = _hinted_lane(report)
    assert ProviderStatus.CANDIDATES is lane.status
    assert (_HINT_ASIN,) == tuple(value.asin for value in lane.recording_lookup_hints)
    assert f"/books/{_HINT_ASIN}" in requests
    assert f"/books/{_HINT_ASIN}" not in base_requests
    assert work == base_work == ("proposal", "queued", None)
    assert audiobook_review_scope(report) == audiobook_review_scope(baseline)
    assert [value.to_data() for value in evaluate_report(report).results] == [
        value.to_data() for value in evaluate_report(baseline).results
    ]


def test_the_alternative_lanes_refused_tight_product_is_no_hint(
    tmp_path: Path,
) -> None:
    report, _work, requests, _rows = _hint_flow(
        tmp_path,
        lambda title: (
            []
            if title == "Project Hail Mary"
            else [_audible_product(_HINT_ASIN, "Wrong")]
        ),
        alternative="The Real Title",
    )

    audible = _audible(report)
    assert 2 == len(audible.lanes)
    assert all(not lane.recording_lookup_hints for lane in audible.lanes)
    assert [] == requests
