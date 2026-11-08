"""Which of an audiobook's sidecar documents speak for it, and for what (#1481).

Every rule here is pure.  The parent inspector hands it the target's record,
the file's own association text and each document the child read, and it
returns, per document, the closed refusals that withheld it or part of it and
the facts that may become claims.  Nothing here opens a file or reads a
setting.

The rules, in the order they run:

1. A document the parent refused from the listing (`target_record`,
   `shared_folder`) contributes nothing; neither does a CUE sheet whose `FILE`
   names none of the item's tracks (`other_file`).
2. A document belongs only when ADR-0096's `title_and_creator` level admits it
   against the file's own evidence or against the target's record, with
   narrators among the creators on both sides.  Otherwise it describes a
   collection the audiobook is one volume of (`collection`) or another book
   (`other_book`).
3. Where a document states an ASIN that the file's embedded tags do not, and
   the tags state one, the tags stand: its ASIN, narrator, performance type and
   language are withheld (`other_edition`).
4. Documents that state different values for a field all lose it
   (`sidecars_disagree`); there is no majority.
5. An `.opf` or `.nfo` value that repeats the target's own record is withheld
   last, because Audiobookshelf reads those files into its record itself, so
   agreement is a copy rather than a second source.  This order only ever
   withholds more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from mechanical_librarian.domain.audio_inspection import (
    MAX_ASSOCIATION_CREATORS,
    AudioInspectionFact,
    AudioInspectionField,
    AudioSidecarBinding,
    AudioSidecarKind,
    AudioSidecarReading,
    AudioSidecarRefusal,
    association_text,
)
from mechanical_librarian.domain.field_outcomes import (
    language_identity,
    language_outcomes_compatible,
)
from mechanical_librarian.domain.metadata_report import (
    ReportValueError,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    normalize_title,
)
from mechanical_librarian.domain.validation import (
    exact_type,
    optional,
    tuple_of,
)
from mechanical_librarian.domain.validation import text as exact_text
from mechanical_librarian.domain.work_identity_levels import (
    IdentityReference as _Reference,
)
from mechanical_librarian.domain.work_identity_levels import (
    WorkIdentityLevel,
    level_admission,
    omnibus_against_volume,
    title_and_creator,
)

# The audio and ebook file types Audiobookshelf scans into library items:
# `SupportedAudioTypes` and `SupportedEbookTypes` in its `server/utils/globals.js`
# at v2.26.0, the oldest release the compatibility page names.  A file of one of
# these types beside an item that is not one of the item's own files belongs to
# another item, so the folder speaks for neither (#1481).
TARGET_SCANNED_EXTENSIONS = frozenset(
    {
        # SupportedAudioTypes
        "m4b",
        "mp3",
        "m4a",
        "flac",
        "opus",
        "ogg",
        "oga",
        "mp4",
        "aac",
        "wma",
        "aiff",
        "aif",
        "wav",
        "webm",
        "webma",
        "mka",
        "awb",
        "caf",
        "mpeg",
        "mpg",
        # SupportedEbookTypes
        "epub",
        "pdf",
        "mobi",
        "azw3",
        "cbr",
        "cbz",
    }
)
# What `other_edition` withholds from a document whose ASIN is not the file's.
_EDITION_FIELDS = frozenset(
    {
        AudioInspectionField.ASIN,
        AudioInspectionField.CONTRIBUTOR,
        AudioInspectionField.PERFORMANCE_TYPE,
        AudioInspectionField.LANGUAGE,
    }
)
_ECHO_KINDS = frozenset({AudioSidecarKind.OPF, AudioSidecarKind.NFO})
_ASIN_SHAPE = re.compile(r"[A-Z0-9]{10}")
_MAX_FILE_TITLES = 16


@dataclass(frozen=True, slots=True)
class AudioAssociationContext:
    """What association knows about the target item, read from its snapshot.

    ``own_file_names`` is the item's full file list as bare names, or ``None``
    when the target did not state one this build could read, which makes every
    folder-level document `shared_folder`.
    """

    title: str | None
    fields: tuple[TargetField, ...]
    own_file_names: tuple[str, ...] | None

    def __post_init__(self) -> None:
        if not (
            optional(exact_text)(self.title)
            and tuple_of(exact_type(TargetField))(self.fields)
            and optional(tuple_of(exact_text))(self.own_file_names)
        ):
            raise ValueError("audio association context is invalid")

    def _values(self, destination: TargetDestination) -> object:
        return next(
            (
                field.value
                for field in self.fields
                if field.destination is destination
                and field.state is TargetFieldState.VALUE
            ),
            None,
        )

    def _names(self, destination: TargetDestination) -> tuple[str, ...]:
        value = self._values(destination)
        if type(value) is not tuple:
            return ()
        return tuple(item for item in value if type(item) is str and item)

    @property
    def authors(self) -> tuple[str, ...]:
        return self._names(TargetDestination.AUTHORS)

    @property
    def narrators(self) -> tuple[str, ...]:
        return self._names(TargetDestination.NARRATORS)

    @property
    def language(self) -> str | None:
        value = self._values(TargetDestination.LANGUAGE)
        return value if type(value) is str else None

    @property
    def description(self) -> str | None:
        value = self._values(TargetDestination.DESCRIPTION)
        return value if type(value) is str else None

    @property
    def series(self) -> tuple[TargetSeries, ...]:
        value = self._values(TargetDestination.SERIES)
        if type(value) is not tuple:
            return ()
        return tuple(item for item in value if type(item) is TargetSeries)

    @property
    def asserted_asins(self) -> frozenset[str]:
        value = self._values(TargetDestination.ASIN)
        if type(value) is not str or _ASIN_SHAPE.fullmatch(value.upper()) is None:
            return frozenset()
        return frozenset({value.upper()})


@dataclass(frozen=True, slots=True)
class FileAssociationEvidence:
    """The file's own association text and identity, across the item's tracks.

    ``titles`` are the `album` tags, and a single-file item's `title` tag;
    ``creators`` are the person tags; ``embedded_asins`` are the valid ASINs
    the embedded tags state; ``track_names`` are the tracks' bare file names.
    """

    titles: tuple[str, ...]
    creators: tuple[str, ...]
    embedded_asins: frozenset[str]
    track_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SidecarDocument:
    """One recognised document as the parent opened it and the child read it.

    ``refusal`` is the parent's own, from the listing.  ``reading`` is ``None``
    when the document was never handed to a child, or a child dropped it.
    """

    name: str
    kind: AudioSidecarKind
    binding: AudioSidecarBinding
    refusal: AudioSidecarRefusal | None = None
    reading: AudioSidecarReading | None = None


@dataclass(frozen=True, slots=True)
class SidecarOutcome:
    """What association decided for one document."""

    refusals: tuple[AudioSidecarRefusal, ...] = ()
    facts: tuple[AudioInspectionFact, ...] = ()
    disputed: tuple[AudioInspectionField, ...] = ()
    echoed: tuple[AudioInspectionField, ...] = ()
    # Positions, in the target's narrator list, of the narrators the document
    # repeated, so a refuse-only reader is handed exactly what was withheld.
    echoed_narrators: tuple[int, ...] = ()


def shared_folder(
    sibling_names: tuple[str, ...], own_file_names: tuple[str, ...] | None
) -> bool:
    """Whether a sibling of a scanned type is not one of the item's own files.

    An unknown own-file list fails closed: the folder is shared.
    """
    if own_file_names is None:
        return True
    own = frozenset(own_file_names)
    return any(
        "." in name
        and name.rsplit(".", 1)[1].lower() in TARGET_SCANNED_EXTENSIONS
        and name not in own
        for name in sibling_names
    )


def file_association_evidence(
    albums: tuple[str | None, ...],
    single_title: str | None,
    creators: tuple[str, ...],
    facts: tuple[AudioInspectionFact, ...],
    track_names: tuple[str, ...],
) -> FileAssociationEvidence:
    """Gather the file's association text and embedded ASINs for the rules."""
    titles: list[str] = []
    for value in (*albums, single_title):
        text = association_text(value)
        if text is not None and text not in titles and len(titles) < _MAX_FILE_TITLES:
            titles.append(text)
    people: list[str] = []
    for value in creators:
        text = association_text(value)
        if text is not None and text not in people:
            people.append(text)
    return FileAssociationEvidence(
        tuple(titles),
        tuple(people),
        frozenset(
            fact.canonical_value
            for fact in facts
            if fact.field is AudioInspectionField.ASIN
            and fact.basis == "embedded_tag"
            and fact.state == "valid"
            and fact.canonical_value is not None
        ),
        track_names,
    )


def associate_sidecars(
    context: AudioAssociationContext,
    evidence: FileAssociationEvidence,
    documents: tuple[SidecarDocument, ...],
) -> tuple[SidecarOutcome, ...]:
    """Decide, per document, what it may contribute.  Aligned with ``documents``."""
    refusals: list[set[AudioSidecarRefusal]] = [set() for _ in documents]
    kept: list[list[AudioInspectionFact]] = [[] for _ in documents]
    for index, document in enumerate(documents):
        refusal = _terminal_refusal(context, evidence, document)
        if refusal is not None:
            refusals[index].add(refusal)
        elif document.reading is not None:
            kept[index] = list(document.reading.facts)
    associated = [
        index
        for index, document in enumerate(documents)
        if not refusals[index] and document.reading is not None
    ]
    # S10: the file's embedded ASINs outrank a document's.
    if evidence.embedded_asins:
        for index in associated:
            if any(
                _valid(fact)
                and fact.field is AudioInspectionField.ASIN
                and fact.canonical_value not in evidence.embedded_asins
                for fact in kept[index]
            ):
                refusals[index].add(AudioSidecarRefusal.OTHER_EDITION)
                kept[index] = [
                    fact for fact in kept[index] if fact.field not in _EDITION_FIELDS
                ]
    # S11: documents that disagree on a field all lose it.
    disputed: list[set[AudioInspectionField]] = [set() for _ in documents]
    for field in AudioInspectionField:
        parties = [
            index
            for index in associated
            if any(_valid(fact) and fact.field is field for fact in kept[index])
        ]
        if len({_value_set(kept[index], field) for index in parties}) <= 1:
            continue
        for index in parties:
            refusals[index].add(AudioSidecarRefusal.SIDECARS_DISAGREE)
            disputed[index].add(field)
            kept[index] = [fact for fact in kept[index] if fact.field is not field]
    # S13: an `.opf` or `.nfo` value repeating the target's record is withheld.
    echoed: list[set[AudioInspectionField]] = [set() for _ in documents]
    narrators: list[set[int]] = [set() for _ in documents]
    for index in associated:
        document = documents[index]
        if document.kind not in _ECHO_KINDS or document.reading is None:
            continue
        names = document_series_names(document.reading.facts)
        retained: list[AudioInspectionFact] = []
        for fact in kept[index]:
            if (
                fact.canonical_value is not None
                and fact.state == "valid"
                and target_echo(context, fact.field, fact.canonical_value, names)
            ):
                echoed[index].add(fact.field)
                if fact.field is AudioInspectionField.CONTRIBUTOR:
                    narrators[index].update(
                        echoed_narrator_positions(context, fact.canonical_value)
                    )
            else:
                retained.append(fact)
        kept[index] = retained
    order = list(AudioSidecarRefusal)
    fields = list(AudioInspectionField)
    return tuple(
        SidecarOutcome(
            tuple(sorted(refusals[index], key=order.index)),
            tuple(kept[index]),
            tuple(sorted(disputed[index], key=fields.index)),
            tuple(sorted(echoed[index], key=fields.index)),
            tuple(sorted(narrators[index])),
        )
        for index in range(len(documents))
    )


def _terminal_refusal(
    context: AudioAssociationContext,
    evidence: FileAssociationEvidence,
    document: SidecarDocument,
) -> AudioSidecarRefusal | None:
    if document.refusal is not None:
        return document.refusal
    reading = document.reading
    if reading is None:
        return None
    if document.kind is AudioSidecarKind.CUE and (
        reading.file_refused
        or (
            reading.file_reference is not None
            and reading.file_reference not in evidence.track_names
        )
    ):
        return AudioSidecarRefusal.OTHER_FILE
    return _belonging_refusal(context, evidence, reading)


def _belonging_refusal(
    context: AudioAssociationContext,
    evidence: FileAssociationEvidence,
    reading: AudioSidecarReading,
) -> AudioSidecarRefusal | None:
    """Apply ADR-0096's `title_and_creator` level against both references.

    Narrators join both creator sets as an input choice: a CUE `PERFORMER` or
    a `composer` tag is often the narrator, and the title must still match.
    """
    references = [
        (title, _creators(evidence.creators)) for title in evidence.titles
    ] + [(context.title, _creators((*context.authors, *context.narrators)))]
    record = _Reference(reading.title, reading.creators)
    passing_collection: list[bool] = []
    for title, creators in references:
        reference = _Reference(title, creators)
        collection = _omnibus(reading.title, title)
        volume = _omnibus(title, reading.title)
        if (
            level_admission(
                WorkIdentityLevel.TITLE_AND_CREATOR,
                title,
                creators,
                reading.title,
                reading.creators,
                omnibus_against_volume=collection or volume,
                contradicted=False,
            )
            is not None
        ):
            return None
        if title_and_creator(reference, record):
            passing_collection.append(collection)
    if passing_collection and all(passing_collection):
        return AudioSidecarRefusal.COLLECTION
    return AudioSidecarRefusal.OTHER_BOOK


def _omnibus(collection: str | None, volume: str | None) -> bool:
    return (
        collection is not None
        and volume is not None
        and omnibus_against_volume(collection, volume)
    )


def _creators(values: tuple[str, ...]) -> tuple[str, ...]:
    people: list[str] = []
    for value in values:
        text = association_text(value)
        if text is not None and text not in people:
            people.append(text)
    return tuple(people[: 2 * MAX_ASSOCIATION_CREATORS])


def _valid(fact: AudioInspectionFact) -> bool:
    return fact.state == "valid" and fact.canonical_value is not None


def _comparison_key(field: AudioInspectionField, canonical: str) -> str:
    """One canonical value's comparison form for the disagreement rule."""
    if field is AudioInspectionField.SERIES_SEQUENCE:
        number = _number(canonical)
        return canonical if number is None else str(number.normalize())
    return " ".join(canonical.split()).casefold()


def _value_set(
    facts: list[AudioInspectionFact], field: AudioInspectionField
) -> frozenset[str]:
    return frozenset(
        _comparison_key(field, fact.canonical_value)
        for fact in facts
        if fact.field is field and fact.canonical_value is not None and _valid(fact)
    )


def _number(value: str) -> Decimal | None:
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _text_key(value: str) -> str | None:
    """The proposal evaluator's comparison form for a name (`canonical_text`)."""
    try:
        return normalize_title(value).casefold()
    except ReportValueError:
        return None


def _recognized_language(value: str) -> bool:
    """Whether the recording-conflict refusal reads this language at all."""
    identity = language_identity(value)
    return identity is not None and identity.base != "und"


def document_series_names(facts: tuple[AudioInspectionFact, ...]) -> tuple[str, ...]:
    """The series names one document states, which its sequences go with."""
    return tuple(
        fact.canonical_value
        for fact in facts
        if fact.field is AudioInspectionField.SERIES
        and fact.canonical_value is not None
        and fact.state == "valid"
    )


def echoed_narrator_positions(
    context: AudioAssociationContext, canonical: str
) -> tuple[int, ...]:
    """Where in the target's narrators one echoed narrator stands."""
    key = _text_key(canonical)
    return tuple(
        position
        for position, value in enumerate(context.narrators)
        if key is not None and key == _text_key(value)
    )


def target_echo(
    context: AudioAssociationContext,
    field: AudioInspectionField,
    canonical: str,
    series_names: tuple[str, ...],
) -> bool:
    """Whether one `.opf` or `.nfo` value repeats the target's record (S13).

    The closed table: an ASIN equal to the asserted one; a narrator equal to
    one of the target's narrators under the evaluator's name comparison; the
    target's language; its description after whitespace collapse; a series
    name equal to one of its series; a sequence equal, numerically, to the
    sequence of a target series whose name the same document states.
    Performance type is never an echo: no target field holds it.
    """
    if field is AudioInspectionField.ASIN:
        return canonical.upper() in context.asserted_asins
    if field is AudioInspectionField.CONTRIBUTOR:
        key = _text_key(canonical)
        return key is not None and any(
            key == _text_key(value) for value in context.narrators
        )
    if field is AudioInspectionField.LANGUAGE:
        language = context.language
        # The target's value stands in for a withheld echo wherever a reader
        # can only refuse, so a case-only match counts only when both spellings
        # are equally recognized: a target `EN` must not replace an `.opf`'s
        # recognized `en`, which refuses where `EN` does not (#1481).
        return language is not None and (
            language_outcomes_compatible(language, canonical)
            or (
                language.casefold() == canonical.casefold()
                and _recognized_language(language) == _recognized_language(canonical)
            )
        )
    if field is AudioInspectionField.DESCRIPTION:
        description = context.description
        return description is not None and " ".join(description.split()) == " ".join(
            canonical.split()
        )
    if field is AudioInspectionField.SERIES:
        key = _text_key(canonical)
        return key is not None and any(
            key == _text_key(value.name) for value in context.series
        )
    if field is AudioInspectionField.SERIES_SEQUENCE:
        number = _number(canonical)
        stated = {key for name in series_names if (key := _text_key(name)) is not None}
        return number is not None and any(
            value.sequence is not None
            and _number(value.sequence) == number
            and _text_key(value.name) in stated
            for value in context.series
        )
    return False


__all__ = [
    "TARGET_SCANNED_EXTENSIONS",
    "AudioAssociationContext",
    "FileAssociationEvidence",
    "SidecarDocument",
    "SidecarOutcome",
    "associate_sidecars",
    "document_series_names",
    "file_association_evidence",
    "shared_folder",
    "target_echo",
]
