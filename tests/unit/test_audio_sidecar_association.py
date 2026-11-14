"""Which sidecar documents speak for an audiobook, and for what (#1481).

The rules are pure, so most cases here build a reading by hand and ask the
association module directly.  The grammar cases feed `interpret` a synthetic
probe document and sidecar text, so the association text is the one the child
would read.  Every title, name and identifier here is synthetic.
"""

from __future__ import annotations

import dataclasses
import json
import time

import pytest

from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    AudioPathContext,
    AudioSidecar,
    interpret,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_ASSOCIATION_CHARACTERS,
    MAX_ASSOCIATION_CREATORS,
    MAX_CHAPTER_TITLE_CHARACTERS,
    MAX_CHAPTERS,
    MAX_PUBLIC_REPORT_BYTES,
    MAX_SIDECAR_NAME_CHARACTERS,
    MAX_SIDECAR_SHADOW,
    PARENT_SIDECAR_REFUSALS,
    TERMINAL_SIDECAR_REFUSALS,
    AudioChapter,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionFact,
    AudioInspectionField,
    AudioInspectionLimits,
    AudioInspectionOutcome,
    AudioSidecarBinding,
    AudioSidecarDigest,
    AudioSidecarKind,
    AudioSidecarReading,
    AudioSidecarRefusal,
    AudioSidecarShadow,
    AudioSidecarShadowField,
)
from mechanical_librarian.domain.audio_sidecar_association import (
    TARGET_SCANNED_EXTENSIONS,
    AudioAssociationContext,
    FileAssociationEvidence,
    SidecarDocument,
    SidecarOutcome,
    associate_sidecars,
    file_association_evidence,
    shared_folder,
    target_echo,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import (
    ReportValueError,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    _audio_sidecar_from_data,
)

_SOURCE = Fingerprint("sha256", SchemaVersion("local-file-v1"), "1" * 64)
_TITLE = "The Synthetic Voyage"
_AUTHOR = "Example Author"
_NARRATOR = "Example Narrator"
_ASIN = "B0SYNTH001"
_OTHER_ASIN = "B0SYNTH002"
_TRACK = "book.m4b"
_OPF = AudioEvidenceSurface.SIDECAR_OPF
_NFO = AudioEvidenceSurface.SIDECAR_NFO
_CUE = AudioEvidenceSurface.SIDECAR_CUE
_LIBATION = AudioEvidenceSurface.SIDECAR_LIBATION
_F = AudioInspectionField
_R = AudioSidecarRefusal


def _fact(
    field: AudioInspectionField,
    value: str,
    surface: AudioEvidenceSurface = _OPF,
) -> AudioInspectionFact:
    return AudioInspectionFact(
        field,
        _SOURCE,
        value,
        value,
        value,
        surface,
        role="narrator" if field is _F.CONTRIBUTOR else None,
    )


def _reading(
    name: str,
    surface: AudioEvidenceSurface,
    *facts: AudioInspectionFact,
    title: str | None = _TITLE,
    creators: tuple[str, ...] = (_AUTHOR,),
    file_reference: str | None = None,
) -> AudioSidecarReading:
    return AudioSidecarReading(
        name, surface, facts, title, creators, file_reference=file_reference
    )


def _document(
    reading: AudioSidecarReading,
    binding: AudioSidecarBinding = AudioSidecarBinding.FOLDER,
    refusal: AudioSidecarRefusal | None = None,
) -> SidecarDocument:
    return SidecarDocument(reading.name, reading.kind, binding, refusal, reading)


def _context(
    *,
    title: str | None = _TITLE,
    authors: tuple[str, ...] = (_AUTHOR,),
    narrators: tuple[str, ...] = (),
    asin: str | None = None,
    language: str | None = None,
    description: str | None = None,
    series: tuple[TargetSeries, ...] = (),
) -> AudioAssociationContext:
    fields: list[TargetField] = []
    for destination, value in (
        (TargetDestination.AUTHORS, authors or None),
        (TargetDestination.NARRATORS, narrators or None),
        (TargetDestination.ASIN, asin),
        (TargetDestination.LANGUAGE, language),
        (TargetDestination.DESCRIPTION, description),
        (TargetDestination.SERIES, series or None),
    ):
        if value is not None:
            fields.append(TargetField(destination, TargetFieldState.VALUE, value))
    return AudioAssociationContext(title, tuple(fields), (_TRACK,))


def _evidence(
    *,
    titles: tuple[str, ...] = (),
    creators: tuple[str, ...] = (),
    asins: frozenset[str] = frozenset(),
) -> FileAssociationEvidence:
    return FileAssociationEvidence(titles, creators, asins, (_TRACK,))


def _one(
    reading: AudioSidecarReading,
    context: AudioAssociationContext | None = None,
    evidence: FileAssociationEvidence | None = None,
) -> SidecarOutcome:
    [outcome] = associate_sidecars(
        _context() if context is None else context,
        _evidence() if evidence is None else evidence,
        (_document(reading),),
    )
    return outcome


def _fields(outcome: SidecarOutcome) -> set[AudioInspectionField]:
    return {fact.field for fact in outcome.facts}


# --- Grammar: association text is read, and never becomes a claim (T8) ------


def _probe(tags: dict[str, str] | None = None) -> str:
    container: dict[str, object] = {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "4.000",
    }
    if tags is not None:
        container["tags"] = tags
    return json.dumps(
        {
            "format": container,
            "streams": [{"codec_type": "audio", "codec_name": "aac"}],
            "chapters": [],
        }
    )


def _interpret(*sidecars: AudioSidecar, tags: dict[str, str] | None = None):
    return interpret(
        _probe(tags),
        sidecars,
        AudioPathContext(_TRACK),
        _SOURCE,
        AudioInspectionLimits(),
    )


_GRAMMARS = (
    AudioSidecar(
        _OPF,
        "book.opf",
        "<package><metadata>"
        f"<dc:title>{_TITLE}</dc:title>"
        f'<dc:creator opf:role="aut">{_AUTHOR}</dc:creator>'
        f'<dc:contributor opf:role="nrt">{_NARRATOR}</dc:contributor>'
        "</metadata></package>",
    ),
    AudioSidecar(
        _NFO,
        "book.nfo",
        f"Title: {_TITLE}\nAuthor: {_AUTHOR}\nNarrator: {_NARRATOR}\n",
    ),
    AudioSidecar(
        _CUE,
        "book.cue",
        f'TITLE "{_TITLE}"\nPERFORMER "{_AUTHOR}"\nFILE "{_TRACK}" MP4\n'
        '  TRACK 01 AUDIO\n    TITLE "Chapter One"\n',
    ),
    AudioSidecar(
        _LIBATION,
        "book.metadata.json",
        json.dumps(
            {
                "asin": _ASIN,
                "title": _TITLE,
                "authors": [{"name": _AUTHOR}],
                "narrators": [{"name": _NARRATOR}],
            }
        ),
    ),
)


@pytest.mark.parametrize("sidecar", _GRAMMARS, ids=("opf", "nfo", "cue", "libation"))
def test_each_grammar_reads_title_and_creators_as_association_text_only(
    sidecar: AudioSidecar,
) -> None:
    """T8: the title and author reach association; no fact carries either."""
    interpretation = _interpret(sidecar)
    [reading] = interpretation.sidecars

    assert reading.title == _TITLE
    assert _AUTHOR in reading.creators
    facts = (*interpretation.facts, *reading.facts)
    assert all(_TITLE not in fact.observed_value for fact in facts)
    # A CUE `PERFORMER` is read as a narrator, as on main; elsewhere an author
    # is association text only.
    if sidecar.surface is not _CUE:
        assert all(
            fact.observed_value != _AUTHOR
            for fact in facts
            if fact.field is _F.CONTRIBUTOR
        )
    assert "title" not in {fact.field.value for fact in facts}


def test_the_cue_sheet_names_its_first_file_and_disc_level_text_only() -> None:
    interpretation = _interpret(_GRAMMARS[2])
    [reading] = interpretation.sidecars
    assert reading.file_reference == _TRACK
    assert reading.title == _TITLE


def test_the_files_album_and_person_tags_are_association_text_only() -> None:
    interpretation = _interpret(
        tags={"album": _TITLE, "artist": _AUTHOR, "narrator": _NARRATOR}
    )
    assert interpretation.album == _TITLE
    assert set(interpretation.creators) >= {_AUTHOR, _NARRATOR}


@pytest.mark.parametrize(
    "title",
    ("T" * (MAX_ASSOCIATION_CHARACTERS + 1), f"{_TITLE}‮", f"{_TITLE}⁦"),
    ids=("256_characters", "bidi_override", "bidi_isolate"),
)
def test_a_refused_title_counts_as_none_and_the_sidecar_is_other_book(
    title: str,
) -> None:
    """S10: a title past 255 characters, or holding a bidi control, is no title."""
    sidecar = AudioSidecar(
        _NFO, "book.nfo", f"Title: {title}\nAuthor: {_AUTHOR}\nNarrator: Someone\n"
    )
    [reading] = _interpret(sidecar).sidecars
    assert reading.title is None
    assert _one(reading).refusals == (_R.OTHER_BOOK,)


def test_a_title_of_exactly_255_characters_is_read() -> None:
    title = "T" * MAX_ASSOCIATION_CHARACTERS
    [reading] = _interpret(
        AudioSidecar(_NFO, "book.nfo", f"Title: {title}\nAuthor: {_AUTHOR}\n")
    ).sidecars
    assert reading.title == title


# --- Hostile input to the grammars (S1, S3, S6, S7) --------------------------


def test_entities_are_decoded_once_and_never_expanded() -> None:
    """S1: no DTD or entity is resolved; `&amp;lt;` is `&lt;`, `&#x41;` stays."""
    laughs = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        + "".join(
            f'<!ENTITY lol{index} "{"&lol" + str(index - 1) + ";" if index else "&lol;"}'
            * 10
            + '">'
            for index in range(1, 10)
        )
        + '<!ENTITY xxe SYSTEM "file:///etc/passwd">'
        + "]>"
    )
    document = (
        laughs
        + "<package><metadata>"
        + "<dc:description>&lol9; &amp;lt; &#x41;</dc:description>"
        + "<dc:language>&xxe;</dc:language>"
        + '<meta name="calibre:series" content="&lol9;"/>'
        + "</metadata></package>"
    )
    started = time.monotonic()
    [reading] = _interpret(AudioSidecar(_OPF, "book.opf", document)).sidecars
    assert time.monotonic() - started < 0.5
    values = {fact.field: fact.observed_value for fact in reading.facts}
    assert values[_F.DESCRIPTION] == "&lol9; &lt; &#x41;"
    assert values[_F.SERIES] == "&lol9;"
    # The external entity is inert text: nothing was fetched or read.
    assert values[_F.LANGUAGE] == "&xxe;"


@pytest.mark.parametrize(
    "document",
    (
        "[" * 200_000 + "]" * 200_000,
        '{"asin": ' + "9" * 5_000 + "}",
    ),
    ids=("deep_nesting", "long_integer"),
)
def test_a_hostile_json_document_is_dropped_whole(document: str) -> None:
    """S6: deep nesting and the integer-digit limit drop the document."""
    interpretation = _interpret(AudioSidecar(_LIBATION, "book.metadata.json", document))
    assert interpretation.outcome is AudioInspectionOutcome.SUCCESS
    assert all(reading.facts == () for reading in interpretation.sidecars)


def test_the_last_duplicate_json_key_wins() -> None:
    document = '{"asin": "%s", "asin": "%s"}' % (_OTHER_ASIN, _ASIN)
    [reading] = _interpret(
        AudioSidecar(_LIBATION, "book.metadata.json", document)
    ).sidecars
    assert [fact.canonical_value for fact in reading.facts] == [_ASIN]


# --- Belonging: title and creator against either reference (S9) --------------


def test_a_sidecar_naming_another_book_is_other_book_and_contributes_nothing() -> None:
    """T9."""
    outcome = _one(
        _reading(
            "book.opf",
            _OPF,
            _fact(_F.ASIN, _OTHER_ASIN),
            title="A Different Synthetic Book",
        )
    )
    assert outcome.refusals == (_R.OTHER_BOOK,)
    assert outcome.facts == ()


def test_a_sidecar_matching_only_the_target_is_associated() -> None:
    """T10, first half."""
    outcome = _one(_reading("book.opf", _OPF, _fact(_F.ASIN, _OTHER_ASIN)))
    assert outcome.refusals == ()
    assert _fields(outcome) == {_F.ASIN}


def test_a_sidecar_matching_only_the_files_tags_is_associated() -> None:
    """T10, second half: the target states another title and no creator."""
    outcome = _one(
        _reading("book.opf", _OPF, _fact(_F.ASIN, _OTHER_ASIN)),
        _context(title="Unrelated Synthetic Title", authors=()),
        _evidence(titles=(_TITLE,), creators=(_AUTHOR,)),
    )
    assert outcome.refusals == ()


def test_a_narrator_counts_as_a_creator_on_both_sides() -> None:
    outcome = _one(
        _reading("book.cue", _CUE, creators=(_NARRATOR,)),
        _context(narrators=(_NARRATOR,)),
    )
    assert outcome.refusals == ()


def test_a_sidecar_stating_no_creator_is_other_book() -> None:
    """T11, first half."""
    outcome = _one(_reading("book.opf", _OPF, _fact(_F.ASIN, _ASIN), creators=()))
    assert outcome.refusals == (_R.OTHER_BOOK,)


def test_a_cue_sheet_naming_another_file_is_other_file() -> None:
    """T11, second half; a CUE naming the track itself is associated."""
    other = _reading("book.cue", _CUE, file_reference="another.m4b")
    own = _reading("book.cue", _CUE, file_reference=_TRACK)
    assert _one(other).refusals == (_R.OTHER_FILE,)
    assert _one(own).refusals == ()


@pytest.mark.parametrize(
    "value",
    ("../book.m4b", "/library/book.m4b", ""),
    ids=("traversal", "absolute", "empty"),
)
def test_a_cue_file_value_that_is_no_bare_name_is_other_file(value: str) -> None:
    """S4: a path is compared, never resolved, and names no track."""
    sidecar = AudioSidecar(
        _CUE,
        "book.cue",
        f'TITLE "{_TITLE}"\nPERFORMER "{_AUTHOR}"\nFILE "{value}" MP4\n',
    )
    [reading] = _interpret(sidecar).sidecars
    assert (reading.file_reference, reading.file_refused) == (None, True)
    assert _one(reading).refusals == (_R.OTHER_FILE,)


def test_a_collection_beside_one_of_its_volumes_is_collection() -> None:
    """T11b: the omnibus's title and a creator match, but it is the collection."""
    outcome = _one(
        _reading(
            "book.opf",
            _OPF,
            _fact(_F.ASIN, _OTHER_ASIN),
            title=f"{_TITLE}: Books 1-3",
        )
    )
    assert outcome.refusals == (_R.COLLECTION,)
    assert outcome.facts == ()


def test_a_collection_failing_the_predicate_is_other_book() -> None:
    outcome = _one(
        _reading(
            "book.opf",
            _OPF,
            title=f"{_TITLE}: Books 1-3",
            creators=("Another Synthetic Author",),
        )
    )
    assert outcome.refusals == (_R.OTHER_BOOK,)


def test_a_volume_beside_an_omnibus_recording_is_other_book() -> None:
    """Amendment r3a: the reverse of a collection is another book."""
    outcome = _one(
        _reading("book.opf", _OPF, title=_TITLE),
        _context(title=f"{_TITLE}: Books 1-3"),
    )
    assert outcome.refusals == (_R.OTHER_BOOK,)


# --- Initials: belonging stays exact, the fold names the refusal (#1780) ----
_INITIALS = "R. F. Example"


@pytest.mark.parametrize(
    "respelled", ("R.F. Example", "R F Example"), ids=("joined_dotted", "undotted")
)
def test_sidecar_with_respelled_initials_refused_as_initials_spelling(
    respelled: str,
) -> None:
    """S6: still refused and terminal; only the code says why."""
    outcome = _one(
        _reading("book.opf", _OPF, _fact(_F.ASIN, _OTHER_ASIN), creators=(respelled,)),
        _context(authors=(_INITIALS,)),
    )
    assert outcome.refusals == (_R.INITIALS_SPELLING,)
    assert outcome.facts == ()


@pytest.mark.parametrize(
    "creator",
    ("R. Example", "F.R. Example", "R. F."),
    ids=("dropped_initial", "reversed_initials", "initials_only"),
)
def test_sidecar_with_different_initials_refused_as_other_book(creator: str) -> None:
    """Guard: the fold never names a different person's initials."""
    outcome = _one(
        _reading("book.opf", _OPF, creators=(creator,)),
        _context(authors=(_INITIALS,)),
    )
    assert outcome.refusals == (_R.OTHER_BOOK,)


def test_initials_spelling_replaces_only_other_book() -> None:
    """S6 precedence: `collection` wins; a fold-only collection stays other_book.

    The first document passes as a collection against the file's tags and
    would be admitted by the fold against the target, which names the same
    collection; it stays `collection`.  The second passes only by the fold,
    and only as a collection, so the fold admits nowhere and it is another
    book.  The third would be admitted by the fold against the file's tags
    alone, while the target names another title.
    """
    omnibus = f"{_TITLE}: Books 1-3"
    collection = _one(
        _reading("book.opf", _OPF, title=omnibus, creators=(_INITIALS,)),
        _context(title=omnibus, authors=("R.F. Example",)),
        _evidence(titles=(_TITLE,), creators=(_INITIALS,)),
    )
    fold_only_collection = _one(
        _reading("book.opf", _OPF, title=omnibus, creators=("R.F. Example",)),
        _context(authors=(_INITIALS,)),
    )
    against_tags = _one(
        _reading("book.opf", _OPF, creators=("R.F. Example",)),
        _context(title="Unrelated Synthetic Title"),
        _evidence(titles=(_TITLE,), creators=(_INITIALS,)),
    )
    assert collection.refusals == (_R.COLLECTION,)
    assert fold_only_collection.refusals == (_R.OTHER_BOOK,)
    assert against_tags.refusals == (_R.INITIALS_SPELLING,)


def test_a_parent_refusal_stands_alone() -> None:
    reading = _reading("folder.opf", _OPF, _fact(_F.ASIN, _ASIN))
    [outcome] = associate_sidecars(
        _context(),
        _evidence(),
        (_document(reading, refusal=_R.SHARED_FOLDER),),
    )
    assert outcome == SidecarOutcome((_R.SHARED_FOLDER,))


# --- Precedence and edition (S10, S14) ---------------------------------------


def test_another_editions_sidecar_loses_its_edition_facts_only() -> None:
    """T12: ASIN, narrator, performance type and language go; the rest stays."""
    outcome = _one(
        _reading(
            "book.opf",
            _OPF,
            _fact(_F.ASIN, _OTHER_ASIN),
            _fact(_F.CONTRIBUTOR, "Another Narrator"),
            _fact(_F.PERFORMANCE_TYPE, "abridged"),
            _fact(_F.LANGUAGE, "French"),
            _fact(_F.SERIES, "Synthetic Series"),
            _fact(_F.SERIES_SEQUENCE, "2"),
            _fact(_F.DESCRIPTION, "A synthetic description."),
        ),
        evidence=_evidence(asins=frozenset({_ASIN})),
    )
    assert outcome.refusals == (_R.OTHER_EDITION,)
    assert _fields(outcome) == {_F.SERIES, _F.SERIES_SEQUENCE, _F.DESCRIPTION}


def test_a_sidecar_asin_equal_to_the_embedded_one_is_kept() -> None:
    """T13 (guard)."""
    outcome = _one(
        _reading("book.opf", _OPF, _fact(_F.ASIN, _ASIN)),
        evidence=_evidence(asins=frozenset({_ASIN})),
    )
    assert outcome.refusals == ()
    assert _fields(outcome) == {_F.ASIN}


def test_without_an_embedded_asin_a_sidecar_asin_unlike_the_asserted_is_kept() -> None:
    """T13 (guard)."""
    outcome = _one(
        _reading("book.opf", _OPF, _fact(_F.ASIN, _OTHER_ASIN)),
        _context(asin=_ASIN),
    )
    assert outcome.refusals == ()
    assert _fields(outcome) == {_F.ASIN}


def test_only_embedded_tag_asins_are_the_files_own() -> None:
    """A path ASIN is not the file's own evidence, so it outranks nothing."""
    path = AudioInspectionFact(
        _F.ASIN,
        _SOURCE,
        _ASIN,
        _ASIN,
        f"[{_ASIN}]",
        AudioEvidenceSurface.PATH_FILE,
    )
    tag = AudioInspectionFact(
        _F.ASIN,
        _SOURCE,
        _OTHER_ASIN,
        _OTHER_ASIN,
        _OTHER_ASIN,
        AudioEvidenceSurface.MP4_FREEFORM,
    )
    evidence = file_association_evidence((None,), None, (), (path, tag), (_TRACK,))
    assert evidence.embedded_asins == frozenset({_OTHER_ASIN})


# --- Disagreement (S11) -------------------------------------------------------


def _many(
    *readings: AudioSidecarReading,
    context: AudioAssociationContext | None = None,
) -> tuple[SidecarOutcome, ...]:
    return associate_sidecars(
        _context() if context is None else context,
        _evidence(),
        tuple(_document(reading) for reading in readings),
    )


def test_two_sidecars_with_different_narrators_both_lose_it() -> None:
    """T15: and agreement on series beside it keeps the series."""
    outcomes = _many(
        _reading(
            "a.opf",
            _OPF,
            _fact(_F.CONTRIBUTOR, "First Narrator"),
            _fact(_F.SERIES, "Synthetic Series"),
        ),
        _reading(
            "b.nfo",
            _NFO,
            _fact(_F.CONTRIBUTOR, "Second Narrator", _NFO),
            _fact(_F.SERIES, "synthetic  series", _NFO),
        ),
    )
    for outcome in outcomes:
        assert outcome.refusals == (_R.SIDECARS_DISAGREE,)
        assert outcome.disputed == (_F.CONTRIBUTOR,)
        assert _fields(outcome) == {_F.SERIES}


def test_three_sidecars_two_agreeing_contribute_nothing_for_the_field() -> None:
    """T15: there is no majority."""
    outcomes = _many(
        _reading("a.opf", _OPF, _fact(_F.LANGUAGE, "English")),
        _reading("b.nfo", _NFO, _fact(_F.LANGUAGE, "English", _NFO)),
        _reading("c.cue", _CUE, _fact(_F.LANGUAGE, "German", _CUE)),
    )
    assert all(_F.LANGUAGE not in _fields(outcome) for outcome in outcomes)
    assert all(outcome.disputed == (_F.LANGUAGE,) for outcome in outcomes)


def test_sequences_that_are_numerically_equal_agree() -> None:
    outcomes = _many(
        _reading("a.opf", _OPF, _fact(_F.SERIES_SEQUENCE, "2")),
        _reading("b.nfo", _NFO, _fact(_F.SERIES_SEQUENCE, "2.0", _NFO)),
    )
    assert all(outcome.refusals == () for outcome in outcomes)


def test_a_refused_document_takes_no_part_in_disagreement() -> None:
    outcomes = _many(
        _reading("a.opf", _OPF, _fact(_F.LANGUAGE, "English")),
        _reading(
            "b.nfo", _NFO, _fact(_F.LANGUAGE, "German", _NFO), title="Another Book"
        ),
    )
    assert outcomes[0].refusals == ()
    assert _fields(outcomes[0]) == {_F.LANGUAGE}
    assert outcomes[1].refusals == (_R.OTHER_BOOK,)


# --- Counting with the target: echo withholding (S13) ------------------------

_SERIES = TargetSeries("Synthetic Series", "3")
_ECHO_CONTEXT = _context(
    narrators=(_NARRATOR,),
    asin=_ASIN,
    language="English",
    description="A synthetic\n\ndescription.",
    series=(_SERIES,),
)


@pytest.mark.parametrize(
    ("field", "value", "series_names", "echo"),
    (
        (_F.ASIN, _ASIN, (), True),
        (_F.ASIN, _OTHER_ASIN, (), False),
        (_F.CONTRIBUTOR, "example  narrator", (), True),
        (_F.CONTRIBUTOR, "Another Narrator", (), False),
        (_F.LANGUAGE, "english", (), True),
        (_F.LANGUAGE, "German", (), False),
        (_F.DESCRIPTION, "A synthetic description.", (), True),
        (_F.DESCRIPTION, "A different description.", (), False),
        (_F.SERIES, "synthetic series", (), True),
        (_F.SERIES, "Another Series", (), False),
        (_F.SERIES_SEQUENCE, "3.0", ("Synthetic Series",), True),
        (_F.SERIES_SEQUENCE, "3", (), False),
        (_F.SERIES_SEQUENCE, "4", ("Synthetic Series",), False),
        (_F.PERFORMANCE_TYPE, "unabridged", (), False),
    ),
    ids=(
        "asin_echo",
        "asin_other",
        "narrator_echo",
        "narrator_other",
        "language_echo",
        "language_other",
        "description_echo",
        "description_other",
        "series_echo",
        "series_other",
        "sequence_echo_with_name",
        "sequence_without_name",
        "sequence_other",
        "performance_type_never",
    ),
)
def test_the_echo_table(
    field: AudioInspectionField,
    value: str,
    series_names: tuple[str, ...],
    echo: bool,
) -> None:
    """T16e: one echo and one non-echo case per kind in the S13 table."""
    assert target_echo(_ECHO_CONTEXT, field, value, series_names) is echo


def test_an_opf_asin_equal_to_the_asserted_one_is_echoed() -> None:
    """T16a: no sidecar ASIN fact survives, and the manifest records why."""
    outcome = _one(
        _reading("book.opf", _OPF, _fact(_F.ASIN, _ASIN)),
        _context(asin=_ASIN),
    )
    assert outcome.facts == ()
    assert outcome.echoed == (_F.ASIN,)
    assert outcome.refusals == ()


def test_echo_is_per_value_and_a_different_value_stays() -> None:
    """T16d: a different OPF ASIN stays; a second NFO narrator stays."""
    asin = _one(
        _reading("book.opf", _OPF, _fact(_F.ASIN, _OTHER_ASIN)),
        _context(asin=_ASIN),
    )
    assert _fields(asin) == {_F.ASIN}
    assert asin.echoed == ()
    narrators = _one(
        _reading(
            "book.nfo",
            _NFO,
            _fact(_F.CONTRIBUTOR, _NARRATOR, _NFO),
            _fact(_F.CONTRIBUTOR, "Second Narrator", _NFO),
        ),
        _context(narrators=(_NARRATOR,)),
    )
    assert [fact.canonical_value for fact in narrators.facts] == ["Second Narrator"]
    assert narrators.echoed == (_F.CONTRIBUTOR,)


@pytest.mark.parametrize("surface", (_LIBATION, _CUE), ids=("libation", "cue"))
def test_only_opf_and_nfo_values_can_be_echoes(surface: AudioEvidenceSurface) -> None:
    name = "book.metadata.json" if surface is _LIBATION else "book.cue"
    outcome = _one(
        _reading(name, surface, _fact(_F.ASIN, _ASIN, surface)),
        _context(asin=_ASIN),
    )
    assert _fields(outcome) == {_F.ASIN}
    assert outcome.echoed == ()


def test_an_echoing_narrator_beside_a_different_one_still_disagrees() -> None:
    """T16f: disagreement runs before echo, so both documents lose the field."""
    outcomes = _many(
        _reading("book.opf", _OPF, _fact(_F.CONTRIBUTOR, _NARRATOR)),
        _reading(
            "book.metadata.json",
            _LIBATION,
            _fact(_F.CONTRIBUTOR, "Another Narrator", _LIBATION),
        ),
        context=_context(narrators=(_NARRATOR,)),
    )
    for outcome in outcomes:
        assert outcome.refusals == (_R.SIDECARS_DISAGREE,)
        assert outcome.facts == ()


def test_a_sequence_echoes_only_beside_its_own_documents_series_name() -> None:
    context = _context(series=(_SERIES,))
    without = _one(_reading("book.opf", _OPF, _fact(_F.SERIES_SEQUENCE, "3")), context)
    assert _fields(without) == {_F.SERIES_SEQUENCE}
    beside = _one(
        _reading(
            "book.opf",
            _OPF,
            _fact(_F.SERIES, "Synthetic Series"),
            _fact(_F.SERIES_SEQUENCE, "3"),
        ),
        context,
    )
    assert beside.facts == ()
    assert beside.echoed == (_F.SERIES, _F.SERIES_SEQUENCE)


# --- Shared folder (S4) --------------------------------------------------------


@pytest.mark.parametrize(
    ("siblings", "own", "shared"),
    (
        (("book.m4b", "other.m4b"), ("book.m4b",), True),
        (("book.m4b", "OTHER.FLAC"), ("book.m4b",), True),
        (("book.m4b", "other.pdf"), ("book.m4b",), True),
        (("book.m4b", "cover.jpg", "notes.txt"), ("book.m4b",), False),
        (("book.m4b", "companion.epub"), ("book.m4b", "companion.epub"), False),
        (("book.m4b",), None, True),
    ),
    ids=("m4b", "flac_upper", "pdf", "not_scanned", "own_ebook", "unknown_list"),
)
def test_shared_folder(
    siblings: tuple[str, ...], own: tuple[str, ...] | None, shared: bool
) -> None:
    assert shared_folder(siblings, own) is shared


def test_the_scanned_extension_pin_holds_audio_and_ebook_types() -> None:
    assert {"m4b", "mp3", "flac", "epub", "pdf", "mobi"} <= TARGET_SCANNED_EXTENSIONS
    assert not {"opf", "nfo", "cue", "json", "jpg", "txt"} & TARGET_SCANNED_EXTENSIONS


# --- The closed manifest vocabulary (T18) and protocol v5 --------------------


def test_the_refusal_vocabulary_is_closed_and_ordered() -> None:
    assert [value.value for value in AudioSidecarRefusal] == [
        "target_record",
        "shared_folder",
        "other_file",
        "other_book",
        "initials_spelling",
        "collection",
        "other_edition",
        "sidecars_disagree",
    ]
    assert TERMINAL_SIDECAR_REFUSALS == frozenset(list(AudioSidecarRefusal)[:6])
    assert PARENT_SIDECAR_REFUSALS == {_R.TARGET_RECORD, _R.SHARED_FOLDER}


def _entry_data(**changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "binding": "folder",
        "byte_order_mark": None,
        "claims": [],
        "digest": "a" * 64,
        "disputed": [],
        "echoed": [],
        "echoed_narrators": [],
        "kind": "opf",
        "name": "folder.opf",
        "refusals": ["other_book"],
        "shadow": [],
    }
    data.update(changes)
    return data


def test_the_manifest_reads_a_closed_entry() -> None:
    entry = _audio_sidecar_from_data(_entry_data())
    assert entry.refusals == (_R.OTHER_BOOK,)
    assert entry.to_data() == _entry_data()


def test_the_manifest_reads_initials_spelling_as_terminal() -> None:
    """#1780: the new code reads back, stands alone and ends evaluation."""
    data = _entry_data(refusals=["initials_spelling"])
    entry = _audio_sidecar_from_data(data)
    assert entry.refusals == (_R.INITIALS_SPELLING,)
    assert entry.refused
    assert entry.to_data() == data
    with pytest.raises(ReportValueError):
        _audio_sidecar_from_data(
            _entry_data(refusals=["other_book", "initials_spelling"])
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"refusals": ["not_a_code"]},
        {"refusals": [{"code": "other_book", "text": "A Different Synthetic Book"}]},
        {"refusals": ["other_book", "shared_folder"]},
        {"refusals": ["target_record"]},
        {"kind": "audiobookshelf_record", "refusals": []},
        {"refusals": ["sidecars_disagree"]},
        {"refusals": [], "echoed": ["asin"], "kind": "cue", "name": "x.cue"},
        {"title": _TITLE},
        {"refusals": [], "echoed": ["contributor"]},
        {"refusals": [], "echoed_narrators": [0]},
        {"refusals": [], "echoed": ["contributor"], "echoed_narrators": [1, 0]},
        {"refusals": [], "echoed": ["contributor"], "echoed_narrators": [True]},
        {"refusals": [], "echoed": ["contributor"], "echoed_narrators": ["Name"]},
    ),
    ids=(
        "unknown_code",
        "refusal_with_text",
        "two_terminals",
        "record_refusal_on_opf",
        "record_without_refusal",
        "disagreement_without_field",
        "echo_on_cue",
        "association_text_key",
        "narrator_echo_without_position",
        "position_without_narrator_echo",
        "positions_out_of_order",
        "position_is_a_bool",
        "position_is_a_name",
    ),
)
def test_the_manifest_refuses_an_entry_outside_the_vocabulary(
    changes: dict[str, object],
) -> None:
    """T18: an unknown code, a refusal carrying text, or an inconsistent entry."""
    with pytest.raises(ReportValueError):
        _audio_sidecar_from_data(_entry_data(**changes))


def _wide(width: int) -> str:
    return "\U0001d11e" * width


def _maximal_reading(index: int, title: str = "") -> AudioSidecarReading:
    return AudioSidecarReading(
        f"{index}{'n' * (MAX_SIDECAR_NAME_CHARACTERS - 15)}.metadata.json",
        _LIBATION,
        (),
        title + _wide(MAX_ASSOCIATION_CHARACTERS - len(title)),
        tuple(
            f"{creator:02d}{_wide(MAX_ASSOCIATION_CHARACTERS - 2)}"
            for creator in range(MAX_ASSOCIATION_CREATORS)
        ),
        shadow=tuple(
            AudioSidecarShadow(
                AudioSidecarShadowField.SERIES_TITLE,
                f"{entry:02d}{_wide(200)}",
                f"{entry:02d}{_wide(200)}",
            )
            for entry in range(MAX_SIDECAR_SHADOW)
        ),
    )


def test_the_worst_case_child_output_survives_its_own_protocol() -> None:
    """T27: maximal chapters plus 8 maximal sidecar readings and file text.

    Every character costs four bytes.  Each reading also carries a maximal
    reading without its byte-order mark (#1796).  The facts budget is
    unchanged by #1481 and is not part of this bound; past it the child drops
    the readings without a mark first (control 4).
    """
    report = AudioInspection(
        AudioInspectionOutcome.SUCCESS,
        _SOURCE,
        (),
        tuple(
            AudioChapter(
                index + 1, index, index + 1, _wide(MAX_CHAPTER_TITLE_CHARACTERS)
            )
            for index in range(MAX_CHAPTERS)
        ),
        AudioEmbeddedCover(True, AudioEvidenceSurface.CONTAINER_STREAM),
        True,
        album=_wide(MAX_ASSOCIATION_CHARACTERS),
        creators=tuple(
            f"{creator:02d}{_wide(MAX_ASSOCIATION_CHARACTERS - 2)}"
            for creator in range(MAX_ASSOCIATION_CREATORS)
        ),
        sidecars=tuple(
            dataclasses.replace(
                _maximal_reading(index),
                byte_order_mark=True,
                stripped=_maximal_reading(index, "s"),
            )
            for index in range(8)
        ),
    )
    serialized = report.to_json()

    assert len(serialized.encode("utf-8")) <= MAX_PUBLIC_REPORT_BYTES
    assert AudioInspection.from_json(serialized) == report


def test_protocol_v6_round_trips_and_refuses_v5() -> None:
    reading = _reading(
        "book.cue",
        _CUE,
        _fact(_F.LANGUAGE, "English", _CUE),
        file_reference=_TRACK,
    )
    report = AudioInspection(
        AudioInspectionOutcome.SUCCESS,
        _SOURCE,
        album=_TITLE,
        creators=(_AUTHOR,),
        sidecars=(reading,),
    )
    serialized = report.to_json()
    assert AudioInspection.from_json(serialized) == report

    old = json.loads(serialized)
    old["schema"] = "audio-inspection-v5"
    with pytest.raises(ValueError, match="protocol is invalid"):
        AudioInspection.from_json(
            json.dumps(old, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
    del old["sidecars"], old["album"], old["creators"]
    with pytest.raises(ValueError, match="protocol"):
        AudioInspection.from_json(json.dumps(old))


def test_a_reading_refuses_association_text_past_its_bounds() -> None:
    with pytest.raises(ValueError):
        _reading("book.opf", _OPF, title="T" * (MAX_ASSOCIATION_CHARACTERS + 1))
    with pytest.raises(ValueError):
        _reading(
            "book.opf",
            _OPF,
            creators=tuple(f"Author {index}" for index in range(17)),
        )
    with pytest.raises(ValueError):
        _reading("book.opf", _OPF, file_reference=_TRACK)


def test_a_digest_carries_no_association_text() -> None:
    """T19: the manifest entry has no field for a title, creator or file."""
    entry = AudioSidecarDigest(
        "book.opf",
        "a" * 64,
        AudioSidecarKind.OPF,
        AudioSidecarBinding.TRACK,
    )
    assert set(entry.to_data()) == {
        "binding",
        "claims",
        "digest",
        "disputed",
        "echoed",
        "echoed_narrators",
        "kind",
        "name",
        "refusals",
        "shadow",
        "byte_order_mark",
    }


@pytest.mark.parametrize(
    "payload",
    (
        "Narrator: Example Narrator\n".encode("utf-16"),
        b"Narrator: Example \xff Narrator\n",
        b"\xff\xfe" + "Narrator: Example Narrator\n".encode("utf-16-le"),
        b"\xfe\xff" + "Narrator: Example Narrator\n".encode("utf-16-be"),
        b"\xff\xfe\x00\x00" + "Narrator: Example Narrator\n".encode("utf-32-le"),
        b"\x00\x00\xfe\xff" + "Narrator: Example Narrator\n".encode("utf-32-be"),
        b"\xef\xbb\xbfNarrator: Example \xff Narrator\n",
    ),
    ids=(
        "utf16",
        "invalid_utf8",
        "utf16le_bom",
        "utf16be_bom",
        "utf32le_bom",
        "utf32be_bom",
        "utf8_bom_then_invalid",
    ),
)
def test_a_document_that_is_not_strict_utf8_is_dropped_whole(
    tmp_path, payload: bytes
) -> None:
    """S3, #1796 control 1: the child's decoder refuses it, so no reading exists.

    A UTF-8 mark followed by invalid bytes, and UTF-16 or UTF-32 with their own
    marks, are still dropped whole before any mark could be removed.
    """
    import os

    from mechanical_librarian.bootstrap import audio_child

    path = tmp_path / "book.nfo"
    path.write_bytes(payload)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        assert audio_child._sidecar_text(descriptor, 1_048_576) is None
    finally:
        os.close(descriptor)


class _Name(str):
    """A ``str`` subclass, which the context's exact-type checks reject."""


@pytest.mark.parametrize(
    ("title", "fields", "own"),
    [
        (_Name("Title"), (), None),
        (1, (), None),
        (None, [], None),
        (None, ("not a field",), None),
        (None, (), ["book.m4b"]),
        (None, (), ("book.m4b", 1)),
        (None, (), (_Name("book.m4b"),)),
    ],
)
def test_the_association_context_refuses_what_it_cannot_read(
    title: object, fields: object, own: object
) -> None:
    with pytest.raises(ValueError, match="audio association context is invalid"):
        AudioAssociationContext(title, fields, own)  # type: ignore[arg-type]


def test_the_association_context_accepts_nullable_title_and_file_list() -> None:
    assert AudioAssociationContext(None, (), None).own_file_names is None
    assert AudioAssociationContext("Title", (), ()).own_file_names == ()


@pytest.mark.parametrize(
    ("target", "stated", "echo"),
    (
        ("EN", "en", False),
        ("en", "EN", False),
        ("en", "en", True),
        ("en", "en-GB", True),
        ("XQ", "xq", True),
    ),
    ids=("unread_target", "unread_sidecar", "equal", "one_class", "both_unread"),
)
def test_a_case_only_language_echo_needs_equal_recognition(
    target: str, stated: str, echo: bool
) -> None:
    """Codex R1: an echo restores the target's value for refusals, so a case-only
    match counts only where both spellings refuse alike."""
    context = _context(language=target)
    assert target_echo(context, _F.LANGUAGE, stated, ()) is echo


def test_a_cue_file_value_keeps_the_whitespace_its_track_name_keeps() -> None:
    """Codex R1: `Book  One.m4b` names the track `Book  One.m4b`, not another."""
    track = "Synthetic  Track One.m4b"
    sidecar = AudioSidecar(
        _CUE,
        "book.cue",
        f'TITLE "{_TITLE}"\nPERFORMER "{_AUTHOR}"\nFILE "{track}" MP4\n',
    )
    [reading] = _interpret(sidecar).sidecars
    assert (reading.file_reference, reading.file_refused) == (track, False)
    evidence = FileAssociationEvidence((), (), frozenset(), (track,))
    assert _one(reading, evidence=evidence).refusals == ()
    collapsed = FileAssociationEvidence((), (), frozenset(), (" ".join(track.split()),))
    assert _one(reading, evidence=collapsed).refusals == (_R.OTHER_FILE,)


def test_sidecar_belonging_unchanged_at_every_level() -> None:
    """S7, test 23: a Book matching level never widens what a sidecar speaks
    for. Belonging takes no configured level and reads none, so a respelled-initials
    sidecar stays refused, with no facts, whatever the configured level."""
    import inspect

    from mechanical_librarian.domain import audio_sidecar_association

    # Belonging applies ADR-0096's fixed `title_and_creator` comparison; it
    # never names the configured level or the level derivation.
    source = inspect.getsource(audio_sidecar_association)
    assert source.count("WorkIdentityLevel.") == source.count(
        "WorkIdentityLevel.TITLE_AND_CREATOR"
    )
    assert "work_identity_level=" not in source
    assert "current_work_identity_level" not in source
    assert "level_evidence" not in source
    assert "level" not in inspect.signature(associate_sidecars).parameters
    outcome = _one(
        _reading(
            "book.opf", _OPF, _fact(_F.ASIN, _OTHER_ASIN), creators=("R.F. Example",)
        ),
        _context(authors=(_INITIALS,)),
    )
    assert outcome.refusals == (_R.INITIALS_SPELLING,)
    assert outcome.facts == ()
