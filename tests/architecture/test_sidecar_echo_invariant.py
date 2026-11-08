"""No audiobook report counts the target twice through an echoing sidecar.

A1' (#1481).  `MetadataReport` refuses a hand-built report carrying an `.opf`
or `.nfo` claim equal to a target value.  And over generated synthetic
targets and documents -- every kind in the S13 table, echoes written with
other case and spacing, beside values that differ -- the audiobook inspection
path never produces such a report: the report it builds always validates, and
no `.opf` or `.nfo` claim it keeps repeats the target.  This is the guarantee
the A1'' inventory only prompts review against.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mechanical_librarian.adapters.evidence_extractors import isolated_audio
from mechanical_librarian.adapters.evidence_extractors.isolated_audio import (
    IsolatedAudioInspector,
)
from mechanical_librarian.application.configuration import MediaMountConfiguration
from mechanical_librarian.application.metadata_report import (
    AudioInspectionResult,
    TargetFile,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioInspectionField,
    AudioSidecarBinding,
    AudioSidecarDigest,
    AudioSidecarKind,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    AudioAssociationContext,
    target_echo,
)
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
    default_target_fields,
)
from tests.support.audio_grammar_child import GrammarChild
from tests.support.metadata_report_doubles import single_track_audio_facts

_TITLE = "The Synthetic Voyage"
_AUTHOR = "Example Author"


def _fields(
    narrators: tuple[str, ...],
    language: str,
    description: str,
    series: TargetSeries,
    asin: str,
) -> tuple[TargetField, ...]:
    replaced: dict[
        TargetDestination, str | tuple[str, ...] | tuple[TargetSeries, ...]
    ] = {
        TargetDestination.AUTHORS: (_AUTHOR,),
        TargetDestination.NARRATORS: narrators,
        TargetDestination.LANGUAGE: language,
        TargetDestination.DESCRIPTION: description,
        TargetDestination.SERIES: (series,),
        TargetDestination.ASIN: asin,
    }
    return tuple(
        TargetField(
            field.destination, TargetFieldState.VALUE, replaced[field.destination]
        )
        if field.destination in replaced
        else field
        for field in default_target_fields(_TITLE)
    )


def _report(
    fields: tuple[TargetField, ...], result: AudioInspectionResult
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, result.content_digest, "m4b"),
        local_claims=result.claims,
        local_reasons=tuple(
            sorted({f"{claim.basis}_{claim.kind.value}" for claim in result.claims})
        )
        or ("no_local_audio_claims",),
        providers=(),
        audio=result.audio,
    )


def test_a_hand_built_report_with_an_echoing_opf_claim_is_refused() -> None:
    fields = _fields(
        ("Example Narrator",),
        "English",
        "A synthetic description.",
        TargetSeries("Synthetic Series", "2"),
        "B0SYNTH001",
    )
    claim = LocalClaim.create(
        kind=ClaimKind.CONTRIBUTOR,
        observed="Example Narrator",
        canonical="Example Narrator",
        excerpt="Example Narrator",
        applicability="not_applicable",
        basis="sidecar",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role="narrator",
        occurrence=1,
    )
    audio = single_track_audio_facts("b" * 64)
    forged = _with_sidecar(audio, claim.reference)
    with pytest.raises(ReportValueError, match="echoes the target"):
        MetadataReport.create(
            target=TargetFacts("primary", "audio", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=(claim,),
            local_reasons=("sidecar_contributor",),
            providers=(),
            audio=forged,
        )


def _with_sidecar(audio: AudioFacts, reference: str) -> AudioFacts:
    return replace(
        audio,
        sidecars=(
            AudioSidecarDigest(
                "book.opf",
                "c" * 64,
                AudioSidecarKind.OPF,
                AudioSidecarBinding.TRACK,
                claims=(reference,),
            ),
        ),
    )


def _inspected(
    monkeypatch: pytest.MonkeyPatch,
    fields: tuple[TargetField, ...],
    opf: str,
    nfo: str,
) -> MetadataReport:
    monkeypatch.setattr(isolated_audio, "run_audio_inspector_child", GrammarChild())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "Item").mkdir()
        (root / "Item" / "book.m4b").write_bytes(b"synthetic audio")
        (root / "Item" / "book.opf").write_text(opf, encoding="utf-8")
        (root / "Item" / "book.nfo").write_text(nfo, encoding="utf-8")
        result = IsolatedAudioInspector().inspect(
            MediaMountConfiguration("audiobooks", str(root), "/library", True),
            (TargetFile("1" * 64, "Item/book.m4b", "m4b"),),
            268_435_456,
            association=AudioAssociationContext(
                _TITLE, fields, ("book.m4b", "book.opf", "book.nfo")
            ),
        )
    return _report(fields, result)


_TARGET = (
    ("Example Narrator",),
    "English",
    "A synthetic description.",
    TargetSeries("Synthetic Series", "2"),
    "B0SYNTH001",
)


def test_the_path_withholds_echoes_and_keeps_what_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property below is not vacuous: both outcomes occur on this path."""
    fields = _fields(*_TARGET)
    echo = _inspected(
        monkeypatch,
        fields,
        _opf(
            (
                "Example Narrator",
                "English",
                "A synthetic description.",
                "Synthetic Series",
                "2",
                "B0SYNTH001",
                False,
                False,
            )
        ),
        _nfo(("Example Narrator", "Synthetic Series", "2", False)),
    )
    assert echo.audio is not None
    assert all(entry.claims == () for entry in echo.audio.sidecars)
    assert all(entry.echoed for entry in echo.audio.sidecars)
    differs = _inspected(
        monkeypatch,
        fields,
        _opf(
            (
                "Second Narrator",
                "German",
                "Another synthetic summary.",
                "Other Series",
                "3",
                "B0SYNTH002",
                False,
                False,
            )
        ),
        _nfo(("Second Narrator", "Other Series", "3", False)),
    )
    assert differs.audio is not None
    assert all(entry.claims for entry in differs.audio.sidecars)


_NAMES = st.sampled_from(("Example Narrator", "Second Narrator", "Third Voice"))
_LANGUAGES = st.sampled_from(("English", "German", "French"))
_DESCRIPTIONS = st.sampled_from(
    ("A synthetic description.", "Another synthetic summary.")
)
_SERIES = st.sampled_from(("Synthetic Series", "Other Series"))
_SEQUENCES = st.sampled_from(("1", "2", "2.0", "3"))
_ASINS = st.sampled_from(("B0SYNTH001", "B0SYNTH002"))


def _variant(value: str, case: bool, spacing: bool) -> str:
    text = value.upper() if case else value
    return text.replace(" ", "  ", 1) if spacing else text


@st.composite
def _scenario(draw: st.DrawFn) -> dict[str, object]:
    return {
        "target": (
            (draw(_NAMES),),
            draw(_LANGUAGES),
            draw(_DESCRIPTIONS),
            TargetSeries(draw(_SERIES), draw(_SEQUENCES)),
            draw(_ASINS),
        ),
        "opf": (
            draw(_NAMES),
            draw(_LANGUAGES),
            draw(_DESCRIPTIONS),
            draw(_SERIES),
            draw(_SEQUENCES),
            draw(_ASINS),
            draw(st.booleans()),
            draw(st.booleans()),
        ),
        "nfo": (draw(_NAMES), draw(_SERIES), draw(_SEQUENCES), draw(st.booleans())),
    }


def _opf(values: tuple[object, ...]) -> str:
    narrator, language, description, series, sequence, asin, case, spacing = values
    assert isinstance(case, bool) and isinstance(spacing, bool)
    return (
        "<package><metadata>"
        f"<dc:title>{_TITLE}</dc:title><dc:creator>{_AUTHOR}</dc:creator>"
        f'<dc:contributor opf:role="nrt">{_variant(str(narrator), case, spacing)}'
        "</dc:contributor>"
        f"<dc:language>{language}</dc:language>"
        f"<dc:description>{_variant(str(description), False, spacing)}"
        "</dc:description>"
        f'<meta name="calibre:series" content="{_variant(str(series), case, False)}"/>'
        f'<meta name="calibre:series_index" content="{sequence}"/>'
        f'<meta name="calibre:asin" content="{asin}"/>'
        "</metadata></package>"
    )


def _nfo(values: tuple[object, ...]) -> str:
    narrator, series, sequence, case = values
    assert isinstance(case, bool)
    return (
        f"Title: {_TITLE}\nAuthor: {_AUTHOR}\n"
        f"Narrator: {_variant(str(narrator), case, False)}\n"
        f"Series: {series}\nSeries Part: {sequence}\n"
    )


@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(_scenario())
def test_the_audiobook_path_never_keeps_an_echo(
    monkeypatch: pytest.MonkeyPatch, scenario: dict[str, object]
) -> None:
    target = scenario["target"]
    assert isinstance(target, tuple)
    fields = _fields(*target)
    report = _inspected(
        monkeypatch,
        fields,
        _opf(scenario["opf"]),  # type: ignore[arg-type]
        _nfo(scenario["nfo"]),  # type: ignore[arg-type]
    )

    context = AudioAssociationContext(None, fields, None)
    claims = {claim.reference: claim for claim in report.local_claims}
    assert report.audio is not None
    for entry in report.audio.sidecars:
        if entry.kind not in {AudioSidecarKind.OPF, AudioSidecarKind.NFO}:
            continue
        kept = tuple(claims[reference] for reference in entry.claims)
        names = tuple(
            str(claim.canonical) for claim in kept if claim.kind is ClaimKind.SERIES
        )
        for claim in kept:
            assert not target_echo(
                context,
                AudioInspectionField(claim.kind.value),
                str(claim.canonical),
                names,
            ), (entry.name, claim.kind, claim.canonical)
