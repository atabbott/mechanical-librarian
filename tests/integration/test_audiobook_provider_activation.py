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

import contextlib
import io
import json
import tempfile
from collections.abc import Callable, Iterator, Mapping
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
_ASIN = "B0SYNE7P1K"
_PRODUCER = "f" * 64
# The container's own running time, and the whole minute a provider states for
# the same recording. Forty-four seconds apart, which is inside the band.
_LOCAL_DURATION = "58244.201"
_PROVIDER_DURATION = "58200.000"


def _target_fields() -> tuple[TargetField, ...]:
    populated = TargetField(
        TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Corin Halloway",)
    )
    return tuple(
        populated if field.destination is TargetDestination.AUTHORS else field
        for field in default_target_fields("Ember Tide Protocol")
    )


def _snapshot() -> TargetSnapshot:
    return TargetSnapshot(
        "audiobook",
        "Ember Tide Protocol",
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
        (ClaimKind.TITLE, "Ember Tide Protocol", ClaimScope.WORK, None),
        (ClaimKind.CREATOR, "Corin Halloway", ClaimScope.WORK, "author"),
        (ClaimKind.CONTRIBUTOR, "Ilsa Brandt", ClaimScope.WORK, "narrator"),
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
        "Ember Tide Protocol",
        ("Corin Halloway",),
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
        assert "metadata-report-v64" == published.schema_version
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
        # No provider matched it, so it is not matched rather than recorded
        # (#1407).
        assert 0 == progress.recorded
        assert 1 == progress.not_matched
        assert 1 == dict(progress.stages)["not_matched"]
        assert 0 == dict(progress.stages)["failed"]


def test_candidates_outside_the_corroboration_band_admit_no_recording(
    calls: list[tuple[str, ProviderQueryKind]],
) -> None:
    """Answered is not admitted: the band decides, on every lane.

    Each provider resolves the asserted ASIN and states a running time this
    recording does not have. A retained candidate is not an identity, so no
    proposal is formed and preparation completes, not matched, exactly as an
    unanswered recording does (ADR-0061, #966, #1407).
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
        # No work identity was admitted, so it is not matched (#1407).
        assert 0 == progress.recorded
        assert 1 == progress.not_matched
        assert 0 == dict(progress.stages)["failed"]


_AUDIBLE_ART = "https://m.media-amazon.com/images/I/00SynthEmbr._SL500_.jpg"
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
                    # Internal scope, state and applicability codes are not
                    # owner words (#1286).
                    assert "Scope:" not in retained[0]["observation"]
                    assert "applicability" not in retained[0]["observation"]
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
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Corin Halloway",)
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
        for query in work_query_ladder(title, ("Corin Halloway",))
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
    title: str = "Ember Tide Protocol",
    authors: tuple[str, ...] = ("Corin Halloway",),
) -> tuple[MetadataReport, tuple[object, ...], list[str], dict[str, int]]:
    """Publish one runtime-bearing audiobook, asserting at most a target ASIN.

    `title` and `authors` are the target's own, which the asserted lane asks.
    """
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
        book["title"] = (audnexus_titles or {}).get(asin, "Ember Tide Protocol")
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
    snapshot = replace(
        _snapshot(),
        title=title,
        fields=tuple(
            TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, authors)
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields(title)
        ),
    )
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


def _audible_product(
    asin: str,
    title: str,
    minutes: int = 970,
    authors: tuple[str, ...] = ("Corin Halloway",),
    series: tuple[tuple[str, str], ...] = (),
) -> dict[str, object]:
    return {
        "asin": asin,
        "title": title,
        "authors": [{"name": value} for value in authors],
        "runtime_length_min": minutes,
        **(
            {"series": [{"title": name, "sequence": at} for name, at in series]}
            if series
            else {}
        ),
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

    admitted = _audible_product(_ASIN, "Ember Tide Protocol")
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
            if title == "Ember Tide Protocol"
            else [_audible_product(_HINT_ASIN, "Wrong")]
        ),
        alternative="The Real Title",
    )

    audible = _audible(report)
    assert 2 == len(audible.lanes)
    assert all(not lane.recording_lookup_hints for lane in audible.lanes)
    assert [] == requests


# #1260: band-corroborated work admission over the real Audible and Audnexus
# adapters. 970 minutes is inside the 90 s tight band of the item's runtime and
# 900 outside the 600 s loose band. Every name here is synthetic.
_BAND_TITLE = "Plain Work (Example Saga)"
_BAND_AUTHORS = ("Ada Example",)
# The product's own series, which states the decoration the rung strips.
_BAND_SERIES = (("Example Saga", "1"),)


def _band_flow(
    root: Path,
    products: list[dict[str, object]],
    *,
    title: str = _BAND_TITLE,
    authors: tuple[str, ...] = _BAND_AUTHORS,
) -> tuple[MetadataReport, tuple[object, ...], list[str], dict[str, int]]:
    return _hint_flow(
        root,
        lambda _title: products,
        audnexus_titles={str(value["asin"]): "Wrong Title" for value in products},
        title=title,
        authors=authors,
    )


def test_a_decorated_title_admits_its_plain_tight_recording(tmp_path: Path) -> None:
    from mechanical_librarian.application.metadata_report import (
        recording_identity_basis,
    )
    from mechanical_librarian.domain.proposal_set import (
        AudiobookReviewScope,
        audiobook_review_scope,
    )

    report, work, _requests, _rows = _band_flow(
        tmp_path,
        [
            _audible_product(
                _HINT_ASIN, "Plain Work", authors=_BAND_AUTHORS, series=_BAND_SERIES
            )
        ],
    )

    (lane,) = (
        value
        for value in _audible(report).lanes
        if value.kind is ProviderQueryKind.WORK
    )
    assert ProviderStatus.CANDIDATES is lane.status
    assert lane.transformation_lineage is not None
    assert "band_parenthesized_series_name" == lane.transformation_lineage.production
    assert not lane.recording_lookup_hints
    assert AudiobookReviewScope.RECORDING is audiobook_review_scope(report)
    assert recording_identity_basis(report) is None
    assert ("proposal", "queued", None) == work


@pytest.mark.parametrize(
    ("title", "product", "hinted"),
    (
        (_BAND_TITLE, ("Plain Work", 900), False),
        (_BAND_TITLE, ("Wrong Title", 970), True),
        ("Plain Work Dramatized (Example Saga)", ("Plain Work Dramatized", 970), True),
    ),
    ids=("out_of_band", "wrong_title_tight", "dramatized"),
)
def test_a_band_first_refusal_stays_evidence_only(
    tmp_path: Path, title: str, product: tuple[str, int], hinted: bool
) -> None:
    from mechanical_librarian.application.metadata_report import (
        recording_identity_basis,
    )

    report, work, requests, rows = _band_flow(
        tmp_path,
        [
            _audible_product(
                _HINT_ASIN, product[0], product[1], _BAND_AUTHORS, _BAND_SERIES
            )
        ],
        title=title,
    )

    assert ProviderStatus.NO_MATCH is _audible(report).status
    assert hinted is any(lane.recording_lookup_hints for lane in _audible(report).lanes)
    assert hinted is (f"/books/{_HINT_ASIN}" in requests)
    assert ("complete", "complete", "evidence_only") == work
    assert {"review_cases": 0, "automatic_decisions": 0, "field_effects": 0} == rows
    assert recording_identity_basis(report) is None


@pytest.mark.parametrize(
    "series", ((), (("Another Saga", "1"),)), ids=("no_series", "another_series")
)
def test_a_decorated_title_needs_the_product_s_own_series(
    tmp_path: Path, series: tuple[tuple[str, str], ...]
) -> None:
    """The rung's stripped series name is a decoration only if the product says so."""
    report, work, _requests, rows = _band_flow(
        tmp_path,
        [
            _audible_product(
                _HINT_ASIN, "Plain Work", authors=_BAND_AUTHORS, series=series
            )
        ],
    )

    assert ProviderStatus.NO_MATCH is _audible(report).status
    assert not any(lane.transformation_lineage for lane in _audible(report).lanes)
    assert ("complete", "complete", "evidence_only") == work
    assert {"review_cases": 0, "automatic_decisions": 0, "field_effects": 0} == rows


@pytest.mark.parametrize(
    ("provider_author", "admitted"), (("A. B. Example", True), ("AB Example", False))
)
def test_dotted_initials_admit_against_the_spaced_form_only(
    tmp_path: Path, provider_author: str, admitted: bool
) -> None:
    report, work, _requests, _rows = _band_flow(
        tmp_path,
        [_audible_product(_HINT_ASIN, "Plain Work", authors=(provider_author,))],
        title="Plain Work",
        authors=("A.B. Example",),
    )

    lanes = tuple(
        value for value in _audible(report).lanes if value.transformation_lineage
    )
    assert admitted is (ProviderStatus.CANDIDATES is _audible(report).status)
    assert (("band_initials_fold",) if admitted else ()) == tuple(
        value.transformation_lineage.production
        for value in lanes
        if value.transformation_lineage is not None
    )
    assert (
        ("proposal", "queued", None)
        if admitted
        else ("complete", "complete", "evidence_only")
    ) == work


def _ebook_flow(root: Path) -> tuple[MetadataReport, tuple[object, ...], list[str]]:
    """Publish one decorated-title ebook against a real Open Library adapter.

    Open Library returns the plain-titled work, and the ebook's own claims
    include a running time inside the band, which no ebook is ever compared on.
    """
    import contextlib
    from dataclasses import replace

    import httpx

    from mechanical_librarian.adapters.providers.open_library_metadata import (
        OpenLibraryMetadataProvider,
        OpenLibraryPacer,
    )
    from mechanical_librarian.application.metadata_report import InspectionResult

    root.mkdir(mode=0o700, exist_ok=True)
    work = {
        "key": "/works/OL5W",
        "title": "Plain Work",
        "authors": [{"author": {"key": "/authors/OL7A"}}],
    }
    edition = {
        "key": "/books/OL1M",
        "title": "Plain Work",
        "works": [{"key": "/works/OL5W"}],
    }
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search.json":
            asked.append(request.url.params["title"])
            body: object = {
                "docs": [
                    {
                        "key": "/works/OL5W",
                        "title": "Plain Work",
                        "author_name": list(_BAND_AUTHORS),
                        "author_key": ["OL7A"],
                        "edition_key": ["OL1M"],
                        "editions": {"docs": [{"key": "/books/OL1M"}]},
                    }
                ]
            }
        else:
            body = edition if request.url.path == "/books/OL1M.json" else work
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=body
        )

    class Inspector:
        def __init__(self, _artifacts: object) -> None:
            pass

        def inspect(self, *_args: object) -> InspectionResult:
            return InspectionResult(
                _CONTENT_DIGEST, (_local_claim(ClaimKind.DURATION, _LOCAL_DURATION, 1),)
            )

        def unchanged(self, *_args: object) -> bool:
            return True

    base = _configuration()
    configuration = replace(
        base,
        libraries=(
            LibraryConfiguration(
                "fiction", "primary", "lib-1", "ebook", "books", ("openlibrary",)
            ),
        ),
        mounts=(MediaMountConfiguration("books", "/container", "/service", True),),
        providers=(
            ProviderConnectionConfiguration(
                "openlibrary",
                True,
                False,
                None,
                "open_library",
                contact_email="owner@example.invalid",
            ),
        ),
    )
    snapshot = TargetSnapshot(
        "ebook",
        _BAND_TITLE,
        (TargetFile(_FILE_IDENTITY, "book.epub", "epub"),),
        fields=tuple(
            TargetField(
                TargetDestination.AUTHORS, TargetFieldState.VALUE, _BAND_AUTHORS
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields(_BAND_TITLE)
        ),
    )
    entry = replace(
        _catalogue_entry(),
        title=_BAND_TITLE,
        authors=_BAND_AUTHORS,
        has_ebook=True,
        has_audio=False,
        service_path=f"/{_ITEM}.epub",
        format="epub",
        mount="books",
        source_fingerprint=None,
    )
    provider = OpenLibraryMetadataProvider(
        "openlibrary",
        httpx.Client(transport=httpx.MockTransport(handle)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    with contextlib.ExitStack() as stack:
        for target, value in (
            (__name__ + "._configuration", configuration),
            (__name__ + "._catalogue_entry", entry),
            (__name__ + "._snapshot", snapshot),
        ):
            stack.enter_context(patch(target, return_value=value))
        for name, double in (
            ("open_library_client", object()),
            ("OpenLibraryMetadataProvider", provider),
        ):
            stack.enter_context(patch.object(composition, name, return_value=double))
        stack.enter_context(
            patch.object(composition, "_IsolatedEpubInspector", Inspector)
        )
        serialized, repository = _run(
            root,
            {
                authority: lambda: pytest.fail("an ebook built an audiobook provider")
                for authority in (
                    "audible_catalog",
                    "audnexus",
                    "audiosilo",
                    "itunes_search",
                )
            },
        )
    try:
        state = repository._connection.execute(
            "SELECT stage,state,outcome FROM review_preparation_work"
        ).fetchone()
    finally:
        repository.close()
    return MetadataReport.from_json(serialized), state, asked


def test_an_ebook_with_a_decorated_title_stays_unchanged(tmp_path: Path) -> None:
    """The ladder still asks the plain title, and its work is still refused."""
    report, work, asked = _ebook_flow(tmp_path)

    assert report.audio is None
    assert ["Plain Work"] == asked
    (provider,) = report.providers
    assert ProviderStatus.NO_MATCH is provider.status
    assert not provider.candidates
    assert all(lane.transformation_lineage is None for lane in provider.lanes)
    assert ("proposal", "queued", None) == work


# #1258: one audiobook whose lanes end in every dispatch outcome, over the real
# adapters. The asserted Audible lane is refused with a near-miss and its
# in-band refusals become hints, so the per-ASIN providers answer fed lanes:
# Audnexus admits one and fails on another, and AudioSilo finds nothing. Every
# name and identifier here is synthetic.
_DISPATCH_ADMITTED = "B000000001"
_DISPATCH_FAILING = "B000000002"


def _dispatch_flow(
    root: Path, *, recorded: bool = True
) -> tuple[str, tuple[object, ...], dict[str, list[str]], dict[str, int], object]:
    """Publish the every-outcome item, optionally with no lane record at all.

    Returns the published document, the work row, each provider's request
    URLs, the decision rows, and what the published report admits.
    """
    import contextlib
    import copy
    from dataclasses import replace
    from typing import Any

    import httpx

    from mechanical_librarian.adapters.providers import audible_catalog_metadata
    from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
        AudibleCatalogMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.audiosilo_metadata import (
        AudioSiloMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.audnexus_metadata import (
        AudnexusMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.hardcover_metadata import (
        HardcoverMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.open_library_metadata import (
        OpenLibraryMetadataProvider,
    )
    from mechanical_librarian.application import metadata_report as application
    from mechanical_librarian.domain.metadata_report import AudioDiscoveryTitle
    from mechanical_librarian.domain.proposal_set import (
        audiobook_review_scope,
        evaluate_report,
    )
    from tests.support.audiobook_providers import payload
    from tests.support.hardcover_evidence import hardcover_envelope

    root.mkdir(mode=0o700, exist_ok=True)
    seen: dict[str, list[str]] = {
        name: [] for name in ("audible", "enrichment", "hardcover", "silo")
    }

    def logged(
        name: str, handler: Callable[[httpx.Request], httpx.Response]
    ) -> Callable[[httpx.Request], httpx.Response]:
        def handle(request: httpx.Request) -> httpx.Response:
            seen[name].append(str(request.url))
            return handler(request)

        return handle

    def page(title: str) -> list[dict[str, object]]:
        if title != "The Real Title":
            # The title match is 950 minutes against 970 locally, outside the
            # loose band, beside five in-band products of other works.
            return [
                *(
                    _audible_product(f"B{index:09d}", f"Another Work {index}")
                    for index in range(1, 6)
                ),
                _audible_product("B000000099", "Ember Tide Protocol", 950),
            ]
        return [_audible_product(f"B{index:09d}", "Other") for index in range(11, 18)]

    def search(request: httpx.Request) -> httpx.Response:
        products = page(request.url.params["title"])
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"total_results": len(products), "products": products},
        )

    def books(request: httpx.Request) -> httpx.Response:
        asin = request.url.path.split("/")[2]
        if asin == _DISPATCH_FAILING:
            return httpx.Response(500, json={})
        if asin != _DISPATCH_ADMITTED or request.url.path.endswith("/chapters"):
            return httpx.Response(404, json={})
        book = copy.deepcopy(payload("audnexus_book"))
        book["asin"] = asin
        book["title"] = "Ember Tide Protocol"
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=book
        )

    def unreached(_request: httpx.Request) -> httpx.Response:
        pytest.fail("a starved provider sent a request")

    def client(name: str, handler: Callable) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(logged(name, handler)))

    fixed: dict[str, Any] = {
        "wall_clock": lambda: 1700000000.0,
        "sleep": lambda _: None,
        "jitter": lambda: 0.0,
    }
    providers: dict[str, MetadataProvider | Callable[[], MetadataProvider]] = {
        "audible_catalog": lambda: AudibleCatalogMetadataProvider(
            "audible",
            client("audible", search),
            262144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            **fixed,
        ),
        "audnexus": lambda: AudnexusMetadataProvider(
            "enrichment",
            client("enrichment", books),
            262144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            **fixed,
        ),
        "audiosilo": lambda: AudioSiloMetadataProvider(
            "silo",
            client("silo", lambda _request: httpx.Response(404, json={})),
            262144,
            5.0,
            clock=lambda: 0.0,
            **fixed,
        ),
        "itunes_search": _FakeAudiobookProvider("store", "itunes_search", []),
    }
    hardcover = HardcoverMetadataProvider(
        "hardcover",
        client(
            "hardcover",
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=hardcover_envelope([], []),
            ),
        ),
        262144,
        5.0,
        clock=lambda: 0.0,
        **fixed,
    )
    # Its own clock already stands past any deadline the preparation hands it,
    # so it stops before its first request: starved, not timed out.
    open_library = OpenLibraryMetadataProvider(
        "openlibrary",
        client("openlibrary", unreached),
        262144,
        5.0,
        clock=lambda: 1.0e12,
        **fixed,
    )
    base = _configuration()
    configuration = replace(
        base,
        libraries=(
            replace(
                base.libraries[0],
                provider_connections=(
                    "audible",
                    "backup",
                    "books",
                    "enrichment",
                    "hardcover",
                    "inventaire",
                    "openlibrary",
                    "silo",
                    "store",
                ),
            ),
        ),
        providers=tuple(
            sorted(
                (
                    *base.providers,
                    ProviderConnectionConfiguration(
                        "backup", False, False, None, "audible_catalog"
                    ),
                    ProviderConnectionConfiguration(
                        "hardcover", True, False, "/run/hardcover-token", "hardcover"
                    ),
                    ProviderConnectionConfiguration(
                        "openlibrary",
                        True,
                        False,
                        None,
                        "open_library",
                        contact_email="owner@example.invalid",
                    ),
                ),
                key=lambda value: value.name,
            )
        ),
    )
    inspected = AudioInspectionResult(
        _CONTENT_DIGEST,
        (_local_claim(ClaimKind.DURATION, _LOCAL_DURATION, 1),),
        AudioFacts(
            embedded_cover=AudioEmbeddedCover(False),
            tracks=(
                AudioTrackDigest(1, _FILE_IDENTITY, _CONTENT_DIGEST, "m4b", "book.m4b"),
            ),
            discovery_titles=(AudioDiscoveryTitle("path_leaf", "The Real Title"),),
        ),
    )
    with contextlib.ExitStack() as stack:
        for target, value in (
            (__name__ + "._audio_result", inspected),
            (__name__ + "._configuration", configuration),
        ):
            stack.enter_context(patch(target, return_value=value))
        for name, double in (
            ("hardcover_client", object()),
            ("open_library_client", object()),
            ("HardcoverMetadataProvider", hardcover),
            ("OpenLibraryMetadataProvider", open_library),
        ):
            stack.enter_context(patch.object(composition, name, return_value=double))
        if not recorded:
            # The same preparation with the record gone: no lane is given one,
            # Audible never measures a near-miss, and nothing checks for it.
            stack.enter_context(
                patch.object(
                    application,
                    "with_lane_dispatch",
                    lambda observation, _result, *, audio: observation,
                )
            )
            stack.enter_context(
                patch.object(
                    audible_catalog_metadata, "loose_band_near_miss", lambda *_: None
                )
            )
            stack.enter_context(
                patch.object(
                    MetadataReport, "_validate_lane_dispatch", lambda *_args: None
                )
            )
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
            stored = repository.get(MetadataReport.from_json(serialized).report_id)
        finally:
            repository.close()
        published = MetadataReport.from_json(stored)
        admitted = (
            audiobook_review_scope(published),
            [value.to_data() for value in evaluate_report(published).results],
        )
    assert serialized == stored
    return serialized, work, seen, rows, admitted


def test_one_audiobook_reaches_every_dispatch_outcome(tmp_path: Path) -> None:
    from mechanical_librarian.domain.metadata_report import (
        DispatchOutcome,
        ProviderLaneDispatch,
        provider_dispatch_class,
    )

    serialized, work, seen, _rows, _admitted = _dispatch_flow(tmp_path)
    report = MetadataReport.from_json(serialized)

    assert serialized == report.to_json()
    lanes = {
        provider.provider: sorted(
            ((lane.kind.value, lane.dispatch) for lane in provider.lanes), key=str
        )
        for provider in report.providers
    }
    record = ProviderLaneDispatch
    outcome = DispatchOutcome
    assert {
        "audible": [
            ("work", record(outcome.OVERFLOW, 1)),
            ("work", record(outcome.REFUSED, 1, 57_000 - 58_244)),
        ],
        "backup": [("work", record(outcome.NOT_DISPATCHED))],
        "enrichment": [
            ("asin", record(outcome.ADMITTED)),
            ("asin", record(outcome.UNAVAILABLE)),
        ],
        "hardcover": [("work", record(outcome.EMPTY_OR_REFUSED))],
        "openlibrary": [("work", record(outcome.STARVED))],
        "silo": [("asin", record(outcome.EMPTY))] * 2,
    } == lanes
    assert set(DispatchOutcome) == {
        dispatch.outcome
        for values in lanes.values()
        for _kind, dispatch in values
        if dispatch is not None
    }
    assert {
        "audible": outcome.OVERFLOW,
        "backup": outcome.NOT_DISPATCHED,
        "enrichment": outcome.ADMITTED,
        "hardcover": outcome.EMPTY_OR_REFUSED,
        "openlibrary": outcome.STARVED,
        "silo": outcome.EMPTY,
    } == {value.provider: provider_dispatch_class(value) for value in report.providers}
    # The two in-band refusals Audible handed on are the only products asked.
    assert {_DISPATCH_ADMITTED, _DISPATCH_FAILING} == {
        url.split("/")[4] for url in seen["enrichment"]
    }
    assert ("proposal", "queued", None) == work


def _without_dispatch(serialized: str) -> object:
    document = json.loads(serialized)
    document.pop("report_id")
    for provider in document["providers"]:
        for lane in provider["lanes"]:
            lane.pop("dispatch")
    return document


def test_the_dispatch_record_changes_no_request_or_decision(tmp_path: Path) -> None:
    recorded = _dispatch_flow(tmp_path / "recorded")
    bare = _dispatch_flow(tmp_path / "bare", recorded=False)

    assert recorded[0] != bare[0]
    assert _without_dispatch(recorded[0]) == _without_dispatch(bare[0])
    assert recorded[1:] == bare[1:]


_CATALOGUES = ("hardcover", "openlibrary")


@contextlib.contextmanager
def _catalogues_connected() -> Iterator[dict[str, int]]:
    """Connect real Hardcover and Open Library adapters that may send nothing.

    Yields how many times composition built each one, so a test can tell the
    ASIN feed reached them as well as the discovery plan.
    """
    from dataclasses import replace

    import httpx

    from mechanical_librarian.adapters.providers.hardcover_metadata import (
        HardcoverMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.open_library_metadata import (
        OpenLibraryMetadataProvider,
    )

    def unreached(_request: httpx.Request) -> httpx.Response:
        pytest.fail("a catalogue with no ASIN lookup sent a request")

    built = dict.fromkeys(_CATALOGUES, 0)

    def factory(name: str, adapter_type: type) -> Callable[..., object]:
        def build(*_args: object, **_kwargs: object) -> object:
            built[name] += 1
            return adapter_type(
                name,
                httpx.Client(transport=httpx.MockTransport(unreached)),
                262144,
                5.0,
                clock=lambda: 0.0,
                wall_clock=lambda: 1700000000.0,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )

        return build

    base = _configuration()
    configuration = replace(
        base,
        libraries=(
            replace(
                base.libraries[0],
                provider_connections=tuple(
                    sorted((*base.libraries[0].provider_connections, *_CATALOGUES))
                ),
            ),
        ),
        providers=tuple(
            sorted(
                (
                    *base.providers,
                    ProviderConnectionConfiguration(
                        "hardcover", True, False, "/run/hardcover-token", "hardcover"
                    ),
                    ProviderConnectionConfiguration(
                        "openlibrary",
                        True,
                        False,
                        None,
                        "open_library",
                        contact_email="owner@example.invalid",
                    ),
                ),
                key=lambda value: value.name,
            )
        ),
    )
    with (
        patch(__name__ + "._configuration", return_value=configuration),
        patch.object(composition, "hardcover_client", return_value=object()),
        patch.object(composition, "open_library_client", return_value=object()),
        patch.object(
            composition,
            "HardcoverMetadataProvider",
            side_effect=factory("hardcover", HardcoverMetadataProvider),
        ),
        patch.object(
            composition,
            "OpenLibraryMetadataProvider",
            side_effect=factory("openlibrary", OpenLibraryMetadataProvider),
        ),
    ):
        yield built


def _catalogue_consequences(
    root: Path, report: MetadataReport
) -> tuple[str, int, set[str], set[str]]:
    """Read what the published report drives, exactly as preparation does.

    Admits the report's proposal, then returns the admission status, the
    report's provider-limited browse fact, the connections it records a
    provider-health failure for, and those it enrolls recovery jobs for --
    through the same application calls the pipeline makes. Called inside
    `_catalogues_connected`, whose configuration it reads.
    """
    from mechanical_librarian.adapters.persistence.provider_recovery import (
        SQLiteProviderRecoveryStore,
    )
    from mechanical_librarian.application.provider_recovery_evidence import (
        record_provider_recovery_observations,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report

    configuration = _configuration()
    repository = open_repository(root / "reports.sqlite")
    try:
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admission = json.loads(repository.admit_review_case(proposal))["status"]
        (limited,) = repository._connection.execute(
            "SELECT provider_limited FROM metadata_report_browse_facts "
            "WHERE report_id=?",
            (report.report_id,),
        ).fetchone()
        package = repository.current_review_package(
            report.target.connection, report.target.library, report.target.item
        )
    finally:
        repository.close()
    assert package is not None
    recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
    try:
        record_provider_recovery_observations(
            recovery,
            report,
            adapters={value.name: value.adapter for value in configuration.providers},
            scope=report.target.library,
            generation=configuration.provider_recovery_generation,
            now=0,
        )
        jobs = composition._provider_recovery_jobs(
            report,
            package=package,
            proposal=proposal,
            configuration=configuration,
            scope=report.target.library,
        )
        if jobs:
            recovery.enqueue_jobs(jobs, enqueued_at=0)
        failing = {
            connection
            for (connection,) in recovery._connection.execute(
                "SELECT connection FROM provider_recovery_health"
            )
        }
        enrolled = {
            connection
            for (connection,) in recovery._connection.execute(
                "SELECT connection FROM provider_recovery_jobs"
            )
        }
    finally:
        recovery.close()
    return admission, limited, failing, enrolled


def _catalogue_observations(report: MetadataReport) -> dict[str, tuple[object, ...]]:
    return {
        value.provider: (value.status, value.lanes, value.input_references)
        for value in report.providers
        if value.provider in _CATALOGUES
    }


def test_an_embedded_asin_audiobook_asks_the_catalogues_nothing(
    tmp_path: Path, calls: list[tuple[str, ProviderQueryKind]]
) -> None:
    """Hardcover and Open Library answer an ASIN-only plan without failing.

    Neither has an ASIN lookup, so each states that it had nothing to ask:
    not a failure, so the report is not provider-limited, records no
    provider-health failure and enrolls no recovery job (#1306).
    """
    providers = {
        "audible_catalog": _FakeAudiobookProvider("audible", "audible_catalog", calls),
        "audnexus": _FakeAudiobookProvider("enrichment", "audnexus", calls),
        "audiosilo": _FakeAudiobookProvider("silo", "audiosilo", calls),
        "itunes_search": _FakeAudiobookProvider("store", "itunes_search", calls),
    }
    with _catalogues_connected() as built:
        serialized, repository = _run(tmp_path, providers)
        work = repository._connection.execute(
            "SELECT stage,state,outcome FROM review_preparation_work"
        ).fetchone()
        repository.close()
        report = MetadataReport.from_json(serialized)
        consequences = _catalogue_consequences(tmp_path, report)

    assert {"hardcover": 1, "openlibrary": 1} == built
    assert {
        name: (ProviderStatus.INSUFFICIENT_INPUT, (), ()) for name in _CATALOGUES
    } == _catalogue_observations(report)
    assert ("admitted", 0, set(), set()) == consequences
    # The three ASIN authorities still admit the recording, as they do with no
    # catalogue connected.
    assert ("proposal", "queued", None) == work


def test_a_fed_asin_asks_the_catalogues_nothing(tmp_path: Path) -> None:
    """The ASIN feed reaches the catalogues too, and changes nothing there.

    Only the folder leaf is asked, so the catalogues, which never ask an
    alternative title, answer no lane in discovery. Audible's admitted product
    then feeds its ASIN to exactly those providers, and the catalogues answer
    that plan as they answer an embedded ASIN (#1306).
    """
    with _catalogues_connected() as built:
        report, work, audible_titles, _terms = _alternative_flow(tmp_path, _LEAF_FOLDER)
        consequences = _catalogue_consequences(tmp_path, report)

    assert ["The Real Title"] == audible_titles
    assert {"hardcover": 2, "openlibrary": 2} == built
    assert {
        name: (ProviderStatus.INSUFFICIENT_INPUT, (), ()) for name in _CATALOGUES
    } == _catalogue_observations(report)
    assert ("admitted", 0, set(), set()) == consequences
    assert ("proposal", "queued", None) == work
