"""An audiobook's sidecars, from the folder to the published report (#1481).

The issue's demonstration, against the real composition seam: the parent
inspector is the real one and opens real files in a temporary mount; a
stand-in child applies the real decoder and grammar (no ffprobe); the target
reader and the providers are the activation suite's fakes.  Every title, name
and identifier here is synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.evidence_extractors import isolated_audio
from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    MetadataProvider,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.integration import test_audiobook_provider_activation as activation
from tests.support.audio_grammar_child import GrammarChild

_TITLE = "Ember Tide Protocol"
_AUTHOR = "Corin Halloway"
_RECORD_ASIN = "B0SYNREC01"
_SIDECAR_ASIN = activation._ASIN
_FOLDER = "Item"
_TRACK = f"{_FOLDER}/book.m4b"
_MAXIMUM = 268_435_456


def _mount(root: Path) -> MediaMountConfiguration:
    return MediaMountConfiguration("audiobooks", str(root / "media"), "/service", True)


def _fields(
    asin: str | None,
    language: str | None = None,
    narrators: tuple[str, ...] = (),
) -> tuple[TargetField, ...]:
    values: dict[TargetDestination, str | tuple[str, ...]] = {}
    if asin is not None:
        values[TargetDestination.ASIN] = asin
    if language is not None:
        values[TargetDestination.LANGUAGE] = language
    if narrators:
        values[TargetDestination.NARRATORS] = narrators
    return tuple(
        TargetField(
            field.destination, TargetFieldState.VALUE, values[field.destination]
        )
        if field.destination in values
        else field
        for field in activation._target_fields()
    )


def _snapshot(
    own: tuple[str, ...],
    asin: str | None,
    language: str | None = None,
    narrators: tuple[str, ...] = (),
) -> TargetSnapshot:
    return TargetSnapshot(
        "audiobook",
        _TITLE,
        (TargetFile(activation._FILE_IDENTITY, _TRACK, "m4b"),),
        fields=_fields(asin, language, narrators),
        own_file_names=own,
    )


def _library(root: Path, documents: dict[str, str]) -> tuple[str, ...]:
    folder = root / "media" / _FOLDER
    folder.mkdir(parents=True)
    (folder / "book.m4b").write_bytes(b"synthetic recording")
    for name, text in documents.items():
        (folder / name).write_text(text, encoding="utf-8")
    return ("book.m4b", *documents)


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch) -> Iterator[GrammarChild]:
    stand_in = GrammarChild()
    monkeypatch.setattr(isolated_audio, "run_audio_inspector_child", stand_in)
    yield stand_in


def _publish(
    root: Path,
    own: tuple[str, ...],
    *,
    asin: str | None = None,
    language: str | None = None,
    narrators: tuple[str, ...] = (),
    enrichment: ProviderStatus = ProviderStatus.NO_MATCH,
) -> tuple[MetadataReport, list[tuple[str, ProviderQueryKind]], AudioInspectionResult]:
    """Inspect the real folder and publish the report through `_run`."""
    snapshot = _snapshot(own, asin, language, narrators)
    inspected = IsolatedAudioInspector().inspect(
        _mount(root),
        snapshot.files,
        _MAXIMUM,
        association=AudioAssociationContext(_TITLE, snapshot.fields, own),
    )
    calls: list[tuple[str, ProviderQueryKind]] = []
    providers: dict[str, MetadataProvider | Callable[[], MetadataProvider]] = {
        "audible_catalog": activation._FakeAudiobookProvider(
            "audible", "audible_catalog", calls, status=ProviderStatus.NO_MATCH
        ),
        "itunes_search": activation._FakeAudiobookProvider(
            "store", "itunes_search", calls, status=ProviderStatus.NO_MATCH
        ),
        "audnexus": activation._FakeAudiobookProvider(
            "enrichment", "audnexus", calls, status=enrichment
        ),
        "audiosilo": activation._FakeAudiobookProvider(
            "silo", "audiosilo", calls, status=ProviderStatus.NO_MATCH
        ),
    }

    class _Inspector:
        """Hand back the real inspection; prove currentness on the real folder."""

        def __init__(self, *_args: object, **_keywords: object) -> None:
            pass

        def inspect(self, *_args: object, **_keywords: object) -> AudioInspectionResult:
            return inspected

        def unchanged(
            self,
            _mount_configuration: object,
            relative_paths: tuple[str, ...],
            expected_digest: str,
            maximum_bytes: int,
            expected_sidecars: tuple[object, ...] = (),
            expected_tracks: tuple[object, ...] = (),
            *,
            own_file_names: tuple[str, ...] | None = None,
        ) -> bool:
            return IsolatedAudioInspector().unchanged(
                _mount(root),
                relative_paths,
                expected_digest,
                maximum_bytes,
                expected_sidecars,  # type: ignore[arg-type]
                expected_tracks,  # type: ignore[arg-type]
                own_file_names=own_file_names,
            )

    with (
        patch.object(activation, "_AudioInspector", _Inspector),
        patch.object(activation, "_snapshot", return_value=snapshot),
    ):
        serialized, repository = activation._run(root, providers)
    repository.close()
    return MetadataReport.from_json(serialized), calls, inspected


def _asin_lane_references(report: MetadataReport) -> set[str]:
    return {
        reference
        for provider in report.providers
        for lane in provider.lanes
        if lane.kind is ProviderQueryKind.ASIN
        for reference in lane.input_references
    }


def _claims(report: MetadataReport, basis: str) -> list[tuple[str, str]]:
    return [
        (claim.kind.value, str(claim.canonical))
        for claim in report.local_claims
        if claim.basis == basis
    ]


def test_audiobookshelfs_own_record_yields_no_evidence_and_opens_no_lane(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I1: the record is present and refused; the target carries its values."""
    record = json.dumps(
        {
            "asin": _RECORD_ASIN,
            "title": _TITLE,
            "authors": [_AUTHOR],
            "series": ["Synthetic Series #3"],
        }
    )
    own = _library(tmp_path, {"metadata.json": record})

    report, _, _ = _publish(tmp_path, own, asin=_RECORD_ASIN)

    assert child.handed == [()]
    assert _claims(report, "sidecar") == []
    assert report.audio is not None
    [entry] = report.audio.sidecars
    assert (entry.kind.value, [value.value for value in entry.refusals]) == (
        "audiobookshelf_record",
        ["target_record"],
    )
    asserted = {
        claim.reference
        for claim in report.local_claims
        if claim.kind is ClaimKind.ASIN and claim.canonical == _RECORD_ASIN
    }
    assert asserted
    assert all(
        claim.basis != "sidecar"
        for claim in report.local_claims
        if claim.reference in asserted
    )
    sidecar_references = {
        claim.reference for claim in report.local_claims if claim.basis == "sidecar"
    }
    assert not _asin_lane_references(report) & sidecar_references


def test_a_sidecar_copied_from_another_book_contributes_nothing(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I2: the manifest records `other_book`, and no claim comes from it."""
    copied = (
        "<package><metadata>"
        "<dc:title>A Different Synthetic Book</dc:title>"
        "<dc:creator>Another Synthetic Author</dc:creator>"
        f'<meta name="calibre:asin" content="{_SIDECAR_ASIN}"/>'
        "</metadata></package>"
    )
    own = _library(tmp_path, {"book.opf": copied})

    report, _, _ = _publish(tmp_path, own)

    assert _claims(report, "sidecar") == []
    assert report.audio is not None
    [entry] = report.audio.sidecars
    assert [value.value for value in entry.refusals] == ["other_book"]
    assert entry.claims == ()


def test_a_libation_export_for_the_track_supplies_its_asin_and_opens_its_lane(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I3: the per-file export is the track's, and its ASIN is evidence."""
    export = json.dumps(
        {
            "asin": _SIDECAR_ASIN,
            "title": _TITLE,
            "authors": [{"name": _AUTHOR}],
            "narrators": [{"name": "Example Narrator"}],
        }
    )
    own = _library(tmp_path, {"book.metadata.json": export})

    report, calls, _ = _publish(tmp_path, own)

    assert ("asin", _SIDECAR_ASIN) in _claims(report, "sidecar")
    [reference] = [
        claim.reference
        for claim in report.local_claims
        if claim.basis == "sidecar" and claim.kind is ClaimKind.ASIN
    ]
    assert report.audio is not None
    [entry] = report.audio.sidecars
    assert (entry.binding.value, entry.refusals) == ("track", ())
    assert reference in entry.claims
    assert reference in _asin_lane_references(report)
    assert ("audible_catalog", ProviderQueryKind.ASIN) in calls


def test_a_foreign_file_added_to_the_folder_makes_the_report_stale(
    tmp_path: Path, child: GrammarChild
) -> None:
    """I4: currentness recomputes the shared folder from the listing."""
    folder_document = (
        "<package><metadata>"
        f"<dc:title>{_TITLE}</dc:title><dc:creator>{_AUTHOR}</dc:creator>"
        "<dc:language>English</dc:language>"
        "</metadata></package>"
    )
    own = _library(tmp_path, {"folder.opf": folder_document})
    report, _, inspected = _publish(tmp_path, own)
    assert report.audio is not None

    def current() -> bool:
        return IsolatedAudioInspector().unchanged(
            _mount(tmp_path),
            (_TRACK,),
            inspected.content_digest,
            _MAXIMUM,
            report.audio.sidecars,  # type: ignore[union-attr]
            report.audio.tracks,  # type: ignore[union-attr]
            own_file_names=own,
        )

    assert [value.refusals for value in report.audio.sidecars] == [()]
    assert current()
    (tmp_path / "media" / _FOLDER / "other.flac").write_bytes(b"another item")
    assert not current()


# Counting with the target (S13, T16a to T16c and T16g, and the refusal
# readers of review B1).  The target here is the activation suite's fake
# reader; an `.opf` that repeats its record is the echo.

_NARRATOR = "Ilsa Brandt"
_OTHER_NARRATOR = "Tamsin Vale"


def _opf(*elements: str) -> str:
    return (
        "<package><metadata>"
        f"<dc:title>{_TITLE}</dc:title><dc:creator>{_AUTHOR}</dc:creator>"
        + "".join(elements)
        + "</metadata></package>"
    )


def _asin_meta(asin: str) -> str:
    return f'<meta name="calibre:asin" content="{asin}"/>'


def _narrator_element(name: str) -> str:
    return f'<dc:contributor opf:role="nrt">{name}</dc:contributor>'


def _echoed(report: MetadataReport) -> list[list[str]]:
    assert report.audio is not None
    return [[field.value for field in entry.echoed] for entry in report.audio.sidecars]


def _in(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(mode=0o700)
    return folder


def test_an_echoed_opf_asin_opens_no_lane_and_the_asserted_one_still_feeds(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T16a: lanes and calls are the item's without the sidecar."""
    echo_root = _in(tmp_path, "echo")
    own = _library(echo_root, {"book.opf": _opf(_asin_meta(_SIDECAR_ASIN))})
    echo, echo_calls, _ = _publish(echo_root, own, asin=_SIDECAR_ASIN)
    bare_root = _in(tmp_path, "bare")
    bare, bare_calls, _ = _publish(
        bare_root, _library(bare_root, {}), asin=_SIDECAR_ASIN
    )

    assert _echoed(echo) == [["asin"]]
    assert _claims(echo, "sidecar") == []
    asserted = {
        claim.reference
        for claim in echo.local_claims
        if claim.kind is ClaimKind.ASIN and claim.basis == "target_metadata"
    }
    assert asserted
    # No phase-one lane: the plan the providers were first handed is the
    # bare item's, and the only ASIN any lane asks is the asserted one.
    assert echo_calls == bare_calls
    assert _asin_lane_references(echo) == _asin_lane_references(bare)
    assert _asin_lane_references(echo) <= asserted
    assert ("audible_catalog", ProviderQueryKind.ASIN) in echo_calls


def _restated_candidate(
    *, title: str | None = None, language: str | None = None
) -> Callable[..., ProviderCandidate]:
    """The activation product, under another title or stating a language."""

    def build(
        provider: str, local_claims: tuple[LocalClaim, ...], *rest: str
    ) -> ProviderCandidate:
        original = _ACTIVATION_CANDIDATE(provider, local_claims, *rest)
        stated = [
            (
                claim.kind,
                title
                if title is not None and claim.kind is ClaimKind.TITLE
                else claim.value,
                claim.scope,
                claim.role,
            )
            for claim in original.claims
        ]
        if language is not None:
            stated.append((ClaimKind.LANGUAGE, language, ClaimScope.EDITION, None))
        claims = tuple(
            ProviderClaim.create(
                provider=provider,
                candidate=original.identity,
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=scope,
                applicability="not_applicable",
                role=role,
                raw_order=order,
            )
            for order, (kind, value, scope, role) in enumerate(stated)
        )
        return replace(original, claims=claims)

    return build


_ACTIVATION_CANDIDATE = activation._enrichment_candidate


def test_an_echoed_opf_asin_makes_no_candidate_represent_the_work(
    tmp_path: Path, child: GrammarChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T16b: through the echo, the product does not represent the work."""
    from mechanical_librarian.domain.proposal_set import (
        provider_candidate_represents_target_work,
    )

    # Its title admits no work, so only an independent local ASIN can make
    # the product represent the target.
    monkeypatch.setattr(
        activation,
        "_enrichment_candidate",
        _restated_candidate(title="An Unrelated Synthetic Work"),
    )

    def represented(report: MetadataReport) -> list[bool]:
        return [
            provider_candidate_represents_target_work(report, provider, candidate)
            for provider in report.providers
            for candidate in provider.candidates
        ]

    echo_root = _in(tmp_path, "echo")
    own = _library(echo_root, {"book.opf": _opf(_asin_meta(_SIDECAR_ASIN))})
    echo, _, _ = _publish(
        echo_root, own, asin=_SIDECAR_ASIN, enrichment=ProviderStatus.CANDIDATES
    )
    read_root = _in(tmp_path, "read")
    own = _library(read_root, {"book.opf": _opf(_asin_meta(_SIDECAR_ASIN))})
    read, _, _ = _publish(read_root, own, enrichment=ProviderStatus.CANDIDATES)

    assert _echoed(echo) == [["asin"]]
    assert represented(echo) == [False]
    # The control: the same document, with no record for it to repeat, is
    # the file's own ASIN, and that alone makes the product represent it.
    assert ("asin", _SIDECAR_ASIN) in _claims(read, "sidecar")
    assert represented(read) == [True]


def test_an_echoed_opf_asin_establishes_no_manifestation_binding(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T16c: `_manifestation_binding` is not "established" through the echo."""
    from mechanical_librarian.domain.reviewer_decision_summary import (
        _known_correlations,
        _manifestation_binding,
    )

    def binding(report: MetadataReport) -> str:
        local, _ = _known_correlations(report)
        references = [
            claim.reference
            for claim in report.local_claims
            if claim.kind is ClaimKind.ASIN
        ]
        assert references
        return _manifestation_binding(
            report,
            {
                "scope": "edition",
                "evidence_references": references,
                "correlations": [local],
            },
        )

    echo_root = _in(tmp_path, "echo")
    own = _library(echo_root, {"book.opf": _opf(_asin_meta(_SIDECAR_ASIN))})
    echo, _, _ = _publish(echo_root, own, asin=_SIDECAR_ASIN)
    read_root = _in(tmp_path, "read")
    own = _library(read_root, {"book.opf": _opf(_asin_meta(_SIDECAR_ASIN))})
    read, _, _ = _publish(read_root, own, asin=_RECORD_ASIN)

    assert _echoed(echo) == [["asin"]]
    assert binding(echo) == "unresolved"
    assert _echoed(read) == [[]]
    assert binding(read) == "established"


@pytest.mark.parametrize(
    ("stated", "conflict"),
    [("English", None), ("French", "recording_language_conflict")],
)
def test_an_echoed_opf_language_still_refuses_a_product_in_another_language(
    tmp_path: Path,
    child: GrammarChild,
    monkeypatch: pytest.MonkeyPatch,
    stated: str,
    conflict: str | None,
) -> None:
    """Review B1: an echo withholds belief, never the language refusal.

    The report's readers and the discovery plan's lookups both hand the
    recording-conflict check the target's language for the echoed field.
    """
    from mechanical_librarian.application.metadata_report import (
        audiobook_discovery_plan,
    )
    from mechanical_librarian.domain import proposal_set
    from mechanical_librarian.domain.metadata_report import recording_variant_conflict

    monkeypatch.setattr(
        activation, "_enrichment_candidate", _restated_candidate(language=stated)
    )
    own = _library(tmp_path, {"book.opf": _opf("<dc:language>English</dc:language>")})
    report, _, _ = _publish(
        tmp_path,
        own,
        asin=_SIDECAR_ASIN,
        language="English",
        enrichment=ProviderStatus.CANDIDATES,
    )

    assert _echoed(report) == [["language"]]
    assert not any(claim.kind is ClaimKind.LANGUAGE for claim in report.local_claims)
    [candidate] = [
        candidate for provider in report.providers for candidate in provider.candidates
    ]
    # Without the target's value the withheld claim leaves nothing to compare,
    # and a product in another language would pass.
    assert (
        recording_variant_conflict(
            candidate, report.local_claims, _TITLE, report.library_languages
        )
        is None
    )
    echoed = proposal_set._echoed_languages(report)
    assert echoed == ("English",)
    plan = audiobook_discovery_plan(
        _snapshot(own, _SIDECAR_ASIN, "English"),
        report.local_claims,
        audio=report.audio,
    )
    assert plan.echoed_languages == echoed
    assert (
        recording_variant_conflict(
            candidate, report.local_claims, _TITLE, report.library_languages, echoed
        )
        == conflict
    )


def test_a_noncanonical_target_language_keeps_the_recognized_opf_veto(
    tmp_path: Path, child: GrammarChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex R1: a case-only match must not trade `en` for an unread `EN`.

    The target's `EN` names no language the refusal reads, so the `.opf`'s
    recognized `en` must still refuse a German product, as it did before
    echo withholding existed.
    """
    from mechanical_librarian.domain import proposal_set
    from mechanical_librarian.domain.metadata_report import recording_variant_conflict

    monkeypatch.setattr(
        activation, "_enrichment_candidate", _restated_candidate(language="German")
    )
    own = _library(tmp_path, {"book.opf": _opf("<dc:language>en</dc:language>")})
    report, _, _ = _publish(
        tmp_path,
        own,
        asin=_SIDECAR_ASIN,
        language="EN",
        enrichment=ProviderStatus.CANDIDATES,
    )
    [candidate] = [
        candidate for provider in report.providers for candidate in provider.candidates
    ]

    assert (
        recording_variant_conflict(
            candidate,
            report.local_claims,
            _TITLE,
            report.library_languages,
            proposal_set._echoed_languages(report),
        )
        == "recording_language_conflict"
    )


def _narrated(root: Path, name: str, narrator: str | None) -> MetadataReport:
    """Publish the item, its target crediting `_NARRATOR`, with one `.opf`."""
    folder = _in(root, name)
    documents = (
        {} if narrator is None else {"book.opf": _opf(_narrator_element(narrator))}
    )
    report, _, _ = _publish(folder, _library(folder, documents), narrators=(_NARRATOR,))
    return report


def test_an_echoed_opf_narrator_still_guards_a_disjoint_replacement(
    tmp_path: Path, child: GrammarChild
) -> None:
    """T16g, as amended by review B1: the `shadow_outcome` guard only refuses.

    With only an echoing `.opf` narrator, a replacement that shares no one
    with the target's narrators still goes to the owner, as it did while the
    echo was a claim. With no sidecar it does not; with an `.opf` narrator
    that differs from the target's, the guard reads that narrator.
    """
    from mechanical_librarian.domain.shadow_outcome import (
        _NARRATORS_REPLACE_IDENTITY,
        _withdrawn_audiobook_identity,
    )

    def withdrawn(report: MetadataReport, desired: list[str]) -> bool:
        return _withdrawn_audiobook_identity(
            _NARRATORS_REPLACE_IDENTITY,
            None,
            [_NARRATOR],
            desired,
            report,
            None,  # type: ignore[arg-type]  # read only by the title guard
            series_preserved=False,
        )

    echo = _narrated(tmp_path, "echo", _NARRATOR)
    bare = _narrated(tmp_path, "bare", None)
    other = _narrated(tmp_path, "other", "Rowan Pell")

    assert _echoed(echo) == [["contributor"]]
    assert _claims(echo, "sidecar") == []
    assert withdrawn(echo, [_OTHER_NARRATOR])
    assert not withdrawn(echo, [_NARRATOR])
    assert not withdrawn(bare, [_OTHER_NARRATOR])
    assert _echoed(other) == [[]]
    assert withdrawn(other, [_OTHER_NARRATOR])
    assert not withdrawn(other, ["Rowan Pell"])


def test_an_echoed_opf_narrator_no_longer_settles_a_single_source_cast(
    tmp_path: Path, child: GrammarChild, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review B1: `_narrator_corroboration_guard` tightens, and says so.

    The guard's other routes are held shut, so only the file's own narrator
    mention can settle a cast one source stated. An echo is not that mention:
    the cast is held for the owner, as for an item with no sidecar, while an
    `.opf` narrator that is not the target's still settles it.
    """
    from mechanical_librarian.domain import proposal_set
    from mechanical_librarian.domain.proposal_set import (
        ProposalAlternative,
        ProposalAssessment,
        ProposalOutcome,
        ProposalResult,
        ProposalScope,
        ProposalValue,
        ProposalValueKind,
    )

    monkeypatch.setattr(proposal_set, "admitted_audiobook_identity", lambda _r: True)
    monkeypatch.setattr(
        proposal_set, "narrator_candidate_set_agrees", lambda *_a, **_k: False
    )
    shape = (
        ProposalValue(ProposalValueKind.LIST, (_OTHER_NARRATOR,)),
        ProposalAssessment.REPLACES_TARGET,
        ("provider_" + "b" * 64,),
        ("correlation_" + "c" * 64,),
        ProposalScope.EDITION,
    )
    alternative = ProposalAlternative(
        proposal_set._alternative_id(*shape, None, None), *shape
    )
    single = ProposalResult(
        TargetDestination.NARRATORS,
        ProposalOutcome.PROPOSE_CHANGE,
        (alternative,),
        alternative.alternative_id,
        ("unique_typed_candidate",),
    )

    def held(report: MetadataReport) -> bool:
        guarded = proposal_set._narrator_corroboration_guard(report, single)
        return guarded.outcome is ProposalOutcome.UNRESOLVED_CHOICE

    assert held(_narrated(tmp_path, "echo", _NARRATOR))
    assert held(_narrated(tmp_path, "bare", None))
    assert not held(_narrated(tmp_path, "other", "Rowan Pell"))


def test_only_the_echoed_narrator_guards_in_its_place(
    tmp_path: Path, child: GrammarChild
) -> None:
    """Codex r5: the guard reads exactly what the echo withheld.

    The target credits two narrators and the `.opf` repeats one of them. The
    withheld claim named only that one, so a replacement by the other
    narrator alone still shares nobody with what the file credited.
    """
    from mechanical_librarian.domain.shadow_outcome import (
        _NARRATORS_REPLACE_IDENTITY,
        _withdrawn_audiobook_identity,
    )

    folder = _in(tmp_path, "subset")
    own = _library(folder, {"book.opf": _opf(_narrator_element(_NARRATOR))})
    report, _, _ = _publish(folder, own, narrators=(_NARRATOR, _OTHER_NARRATOR))

    def withdrawn(desired: list[str]) -> bool:
        return _withdrawn_audiobook_identity(
            _NARRATORS_REPLACE_IDENTITY,
            None,
            [_NARRATOR, _OTHER_NARRATOR],
            desired,
            report,
            None,  # type: ignore[arg-type]  # read only by the title guard
            series_preserved=False,
        )

    assert _echoed(report) == [["contributor"]]
    assert report.audio is not None
    assert [entry.echoed_narrators for entry in report.audio.sidecars] == [(0,)]
    assert withdrawn([_OTHER_NARRATOR])
    assert not withdrawn([_NARRATOR])
    # A position the target's narrators do not hold is refused on reading.
    document = json.loads(report.to_json())
    document["audio"]["sidecars"][0]["echoed_narrators"] = [2]
    with pytest.raises(ValueError, match="echoed narrator is unresolved"):
        MetadataReport.from_json(json.dumps(document))
