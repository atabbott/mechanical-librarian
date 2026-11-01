"""Closed work-query shaping and exact work-identity admission."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from mechanical_librarian.domain.audio_inspection import MAX_OBSERVED_CHARACTERS
from mechanical_librarian.domain.evidence_bounds import (
    _FORBIDDEN_CODEPOINTS,
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.validation import exact_type, nonempty_text


class WorkDispatchRung(StrEnum):
    NORMALIZED = "normalized"
    SUBTITLE_STRIPPED = "subtitle_stripped"
    PUNCTUATION_FOLDED = "punctuation_folded"
    # An audiobook's bare "NN Title" with the number stripped, as the asserted
    # lane's last rung (#1307).
    LEADING_NUMBER_STRIPPED = "leading_number_stripped"


class WorkTitleTransformationFamily(StrEnum):
    LOCAL_AUDIOBOOK_UNABRIDGED = "local_audiobook_unabridged"
    PROVIDER_ASSOCIATED_SERIES_SUFFIX = "provider_associated_series_suffix"
    FILENAME_UNDERSCORES = "filename_underscores"
    FILENAME_AUTHOR_TITLE_PREFIX = "filename_author_title_prefix"
    BRACKET_SOURCE_TAG = "bracket_source_tag"
    EXPLICIT_IDENTIFIER = "explicit_identifier"
    EDITION_LABEL = "edition_label"
    PARENTHESIZED_SERIES_NAME = "parenthesized_series_name"
    PARENTHESIZED_BARE_NUMBER = "parenthesized_bare_number"
    PARENTHESIZED_YEAR = "parenthesized_year"
    TRAILING_SERIES_SEQUENCE = "trailing_series_sequence"
    ORDER_INVERTED_SERIES_PREFIX = "order_inverted_series_prefix"
    LEADING_SEQUENCE_TOKEN = "leading_sequence_token"
    RIGHT_HAND_SEGMENT = "right_hand_segment"
    SUBTITLE_SEGMENT = "subtitle_segment"
    PUNCTUATION_AMPERSAND_FOLD = "punctuation_ampersand_fold"
    LATIN_COMBINING_MARKS = "latin_combining_marks"
    INITIALS_FOLD = "initials_fold"
    LEADING_NUMBER = "leading_number"
    CORROBORATED_TITLE_RELATION = "corroborated_title_relation"


class WorkTitleTransformationSide(StrEnum):
    LOCAL = "local"
    PROVIDER = "provider"


class WorkTitleTransformationField(StrEnum):
    TITLE = "title"
    AUTHORS = "authors"


@dataclass(frozen=True, slots=True)
class WorkTitleTransformationApplication:
    """One exact field value changed by a closed transformation family."""

    family: WorkTitleTransformationFamily
    side: WorkTitleTransformationSide
    field: WorkTitleTransformationField
    ordinal: int | None

    def __post_init__(self) -> None:
        if (
            not exact_type(WorkTitleTransformationFamily)(self.family)
            or not exact_type(WorkTitleTransformationSide)(self.side)
            or not exact_type(WorkTitleTransformationField)(self.field)
            or self.field is WorkTitleTransformationField.TITLE
            and self.ordinal is not None
            or self.field is WorkTitleTransformationField.AUTHORS
            and (type(self.ordinal) is not int or self.ordinal < 0)
        ):
            raise ValueError("work title transformation application is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "family": self.family.value,
            "field": self.field.value,
            "ordinal": self.ordinal,
            "side": self.side.value,
        }


@dataclass(frozen=True, slots=True)
class WorkTitleElectionMatch:
    """One closed, reversible local title decoration for consensus review."""

    family: WorkTitleTransformationFamily
    clean_title: str
    preserved_component: str | None = None

    def __post_init__(self) -> None:
        if (
            not exact_type(WorkTitleTransformationFamily)(self.family)
            or not nonempty_text(self.clean_title)
            or self.preserved_component is not None
            and not nonempty_text(self.preserved_component)
        ):
            raise ValueError("work title election match is invalid")


@dataclass(frozen=True, slots=True)
class WorkTitleTransformationRule:
    family: WorkTitleTransformationFamily
    query_enabled: bool
    admission_enabled: bool
    rung: WorkDispatchRung
    # An audiobook-only row shapes an audiobook's query alone, so an ebook
    # ladder is exactly the ladder it was before the row existed (#1255).
    audiobook_only: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.family) is not WorkTitleTransformationFamily
            or type(self.query_enabled) is not bool
            or type(self.admission_enabled) is not bool
            or type(self.rung) is not WorkDispatchRung
            or type(self.audiobook_only) is not bool
            or self.admission_enabled
            and not self.query_enabled
        ):
            raise ValueError("work title transformation rule is invalid")


@dataclass(frozen=True, slots=True)
class WorkTitleCompoundProduction:
    """One closed, explicitly reviewed composition of transformation families.

    Composition is never implicit: only a production named here admits, and a
    production admitting never admits any of its constituent families alone.
    """

    name: str
    families: tuple[WorkTitleTransformationFamily, ...]
    admission_enabled: bool

    def __post_init__(self) -> None:
        if (
            not nonempty_text(self.name)
            or type(self.families) is not tuple
            or not self.families
            or len(set(self.families)) != len(self.families)
            or any(
                type(value) is not WorkTitleTransformationFamily
                or value not in _RULES_BY_FAMILY
                for value in self.families
            )
            or type(self.admission_enabled) is not bool
        ):
            raise ValueError("work title compound production is invalid")


WORK_TITLE_TRANSFORMATION_RULES = (
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.LOCAL_AUDIOBOOK_UNABRIDGED,
        False,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.PROVIDER_ASSOCIATED_SERIES_SUFFIX,
        False,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.EDITION_LABEL,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.PARENTHESIZED_YEAR,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.TRAILING_SERIES_SEQUENCE,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.ORDER_INVERTED_SERIES_PREFIX,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.LEADING_SEQUENCE_TOKEN,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
        audiobook_only=True,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.RIGHT_HAND_SEGMENT,
        True,
        False,
        WorkDispatchRung.NORMALIZED,
        audiobook_only=True,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.SUBTITLE_SEGMENT,
        True,
        False,
        WorkDispatchRung.SUBTITLE_STRIPPED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,
        True,
        False,
        WorkDispatchRung.PUNCTUATION_FOLDED,
    ),
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
        True,
        False,
        WorkDispatchRung.PUNCTUATION_FOLDED,
    ),
    # An authors-only fold that shapes no query and admits only through the
    # band-corroborated `band_initials_fold` production (#1260).
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.INITIALS_FOLD,
        False,
        False,
        WorkDispatchRung.NORMALIZED,
        audiobook_only=True,
    ),
    # A bare leading number with no separator ("12 Title"), stripped only on
    # its own last rung of an audiobook's asserted ladder (#1307).
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.LEADING_NUMBER,
        True,
        False,
        WorkDispatchRung.LEADING_NUMBER_STRIPPED,
        audiobook_only=True,
    ),
    # A title relation that shapes no query and admits only through the
    # `band_corroborated_title_relation` production: a tight product whose
    # title is related and whose narrator or series number agrees (#1307).
    WorkTitleTransformationRule(
        WorkTitleTransformationFamily.CORROBORATED_TITLE_RELATION,
        False,
        False,
        WorkDispatchRung.NORMALIZED,
        audiobook_only=True,
    ),
)


@dataclass(frozen=True, slots=True)
class WorkQuery:
    title: str
    authors: tuple[str, ...]
    rung: WorkDispatchRung
    transformations: tuple[WorkTitleTransformationFamily, ...] = ()
    candidate_limit_narrowing_retry: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.title) is not str
            or not self.title
            or type(self.authors) is not tuple
            or not self.authors
            or any(type(value) is not str or not value for value in self.authors)
            or type(self.rung) is not WorkDispatchRung
            or type(self.transformations) is not tuple
            or any(
                type(value) is not WorkTitleTransformationFamily
                for value in self.transformations
            )
            or len(set(self.transformations)) != len(self.transformations)
            or type(self.candidate_limit_narrowing_retry) is not bool
        ):
            raise ValueError("work query is invalid")


@dataclass(frozen=True, slots=True)
class WorkDispatchLineage:
    rung: WorkDispatchRung
    transformations: tuple[WorkTitleTransformationFamily, ...] = ()
    candidate_limit_narrowing_retry: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.rung) is not WorkDispatchRung
            or type(self.transformations) is not tuple
            or any(
                type(value) is not WorkTitleTransformationFamily
                for value in self.transformations
            )
            or len(set(self.transformations)) != len(self.transformations)
            or type(self.candidate_limit_narrowing_retry) is not bool
            or not _dispatch_transformations_are_canonical(
                self.rung, self.transformations
            )
        ):
            raise ValueError("work dispatch lineage is invalid")

    @classmethod
    def from_query(cls, query: WorkQuery) -> WorkDispatchLineage:
        return cls(
            query.rung,
            query.transformations,
            query.candidate_limit_narrowing_retry,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "candidate_limit_narrowing_retry": self.candidate_limit_narrowing_retry,
            "rung": self.rung.value,
            "transformations": [value.value for value in self.transformations],
        }


@dataclass(frozen=True, slots=True)
class WorkTitleTransformationLineage:
    """Canonical exact transformation applications which admitted one work."""

    applications: tuple[WorkTitleTransformationApplication, ...]

    def __post_init__(self) -> None:
        if (
            type(self.applications) is not tuple
            or not self.applications
            or any(
                type(value) is not WorkTitleTransformationApplication
                for value in self.applications
            )
            or len(set(self.applications)) != len(self.applications)
            or tuple(sorted(self.applications, key=_application_ordinal))
            != self.applications
            or _lineage_production(self.applications) is None
        ):
            raise ValueError("work title transformation lineage is invalid")

    @property
    def production(self) -> str:
        production = _lineage_production(self.applications)
        assert production is not None
        return production

    def to_data(self) -> dict[str, object]:
        return {
            "applications": [value.to_data() for value in self.applications],
            "production": self.production,
        }


@dataclass(frozen=True, slots=True)
class WorkIdentityAdmission:
    admitted: bool
    transformation: WorkTitleTransformationLineage | None = None

    def __post_init__(self) -> None:
        if type(self.admitted) is not bool or (
            self.transformation is not None
            and (
                type(self.transformation) is not WorkTitleTransformationLineage
                or not self.admitted
            )
        ):
            raise ValueError("work identity admission is invalid")


_RULES_BY_FAMILY = {value.family: value for value in WORK_TITLE_TRANSFORMATION_RULES}
if len(_RULES_BY_FAMILY) != len(WorkTitleTransformationFamily):
    raise RuntimeError("work title transformation table is incomplete")
if any(rule.admission_enabled for rule in WORK_TITLE_TRANSFORMATION_RULES):
    raise RuntimeError("work title admission subset requires a schema decision")
_RULE_ORDINAL = {
    rule.family: ordinal for ordinal, rule in enumerate(WORK_TITLE_TRANSFORMATION_RULES)
}

# The one decoration each band-first title production strips (#1260).
# A trailing series sequence strips a position and no series name, so no
# product series can corroborate it, and it is not a band production.
_BAND_TITLE_FAMILIES = (
    WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
    WorkTitleTransformationFamily.ORDER_INVERTED_SERIES_PREFIX,
)

# The productions the explicit-contradiction guard applies to (#1260).
BAND_TITLE_PRODUCTIONS = frozenset(
    f"band_{family.value}" for family in _BAND_TITLE_FAMILIES
)

WORK_TITLE_COMPOUND_PRODUCTIONS = (
    WorkTitleCompoundProduction(
        "local_audiobook_unabridged",
        (WorkTitleTransformationFamily.LOCAL_AUDIOBOOK_UNABRIDGED,),
        True,
    ),
    WorkTitleCompoundProduction(
        "provider_associated_series_suffix",
        (WorkTitleTransformationFamily.PROVIDER_ASSOCIATED_SERIES_SUFFIX,),
        True,
    ),
    WorkTitleCompoundProduction(
        "author_prefix_then_underscores",
        (
            WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
            WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
        ),
        True,
    ),
    WorkTitleCompoundProduction(
        "latin_combining_marks",
        (WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,),
        True,
    ),
    # Band-corroborated productions (ADR-0067, #1260): each admits only inside
    # the tight runtime band on an audiobook plan's asserted WORK lane.
    *(
        WorkTitleCompoundProduction(f"band_{family.value}", (family,), True)
        for family in (
            *_BAND_TITLE_FAMILIES,
            WorkTitleTransformationFamily.INITIALS_FOLD,
            WorkTitleTransformationFamily.CORROBORATED_TITLE_RELATION,
        )
    ),
)
# The production the corroborated admission rule records (ADR-0067, #1307).
CORROBORATED_TITLE_PRODUCTION = (
    f"band_{WorkTitleTransformationFamily.CORROBORATED_TITLE_RELATION.value}"
)
_ADMITTED_PRODUCTIONS_BY_FAMILIES = {
    production.families: production
    for production in WORK_TITLE_COMPOUND_PRODUCTIONS
    if production.admission_enabled
}


def _application_ordinal(
    value: WorkTitleTransformationApplication,
) -> tuple[int, int, int, int]:
    return (
        0 if value.side is WorkTitleTransformationSide.LOCAL else 1,
        0 if value.field is WorkTitleTransformationField.TITLE else 1,
        -1 if value.ordinal is None else value.ordinal,
        {
            WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX: 0,
            WorkTitleTransformationFamily.FILENAME_UNDERSCORES: 1,
        }.get(value.family, _RULE_ORDINAL[value.family] + 2),
    )


CORROBORATED_TITLE_APPLICATION = WorkTitleTransformationApplication(
    WorkTitleTransformationFamily.CORROBORATED_TITLE_RELATION,
    WorkTitleTransformationSide.LOCAL,
    WorkTitleTransformationField.TITLE,
    None,
)


def _lineage_production(
    applications: tuple[WorkTitleTransformationApplication, ...],
) -> str | None:
    if (
        len(applications) == 1
        and applications[0].family
        is WorkTitleTransformationFamily.LOCAL_AUDIOBOOK_UNABRIDGED
        and applications[0].side is WorkTitleTransformationSide.LOCAL
        and applications[0].field is WorkTitleTransformationField.TITLE
        and applications[0].ordinal is None
    ):
        return "local_audiobook_unabridged"
    if (
        len(applications) == 1
        and applications[0].family
        is WorkTitleTransformationFamily.PROVIDER_ASSOCIATED_SERIES_SUFFIX
        and applications[0].field is WorkTitleTransformationField.TITLE
        and applications[0].ordinal is None
    ):
        return "provider_associated_series_suffix"
    compound = (
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.TITLE,
            None,
        ),
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.TITLE,
            None,
        ),
    )
    if applications == compound:
        return "author_prefix_then_underscores"
    if all(
        value.family is WorkTitleTransformationFamily.LATIN_COMBINING_MARKS
        for value in applications
    ):
        return "latin_combining_marks"
    if (
        len(applications) == 1
        and applications[0].family in _BAND_TITLE_FAMILIES
        and applications[0].side is WorkTitleTransformationSide.LOCAL
        and applications[0].field is WorkTitleTransformationField.TITLE
    ):
        return f"band_{applications[0].family.value}"
    if all(
        value.family is WorkTitleTransformationFamily.INITIALS_FOLD
        and value.field is WorkTitleTransformationField.AUTHORS
        for value in applications
    ):
        return "band_initials_fold"
    if applications == (CORROBORATED_TITLE_APPLICATION,):
        return CORROBORATED_TITLE_PRODUCTION
    return None


if len({production.name for production in WORK_TITLE_COMPOUND_PRODUCTIONS}) != len(
    WORK_TITLE_COMPOUND_PRODUCTIONS
):
    raise RuntimeError("work title compound production table is ambiguous")

_ASCII_SPACE = re.compile(r"[\t\n\r\f\v ]+")
# A recording-format term in the local title keeps a band-first title
# production from admitting, alongside the `_VARIANT` vocabulary (#1260).
_RECORDING_FORMAT = re.compile(
    r"\b(?:dramati[sz](?:ed|ations?)|(?:un)?abridged|abridge?ment|full[- ]cast"
    r"|radio)\b",
    re.IGNORECASE,
)
# A whitespace token of single letters, each followed by a full stop.
_DOTTED_INITIALS = re.compile(r"(?:[^\W\d_]\.)+")
_BRACKET_SOURCE = re.compile(r"[\t\n\r\f\v ]*\[[^\[\]]{1,80}\][\t\n\r\f\v ]*$")
_ELECTION_BRACKET_SOURCE = re.compile(
    r"[\t\n\r\f\v ]*\[(?P<tag>[^\[\]]{1,80})\][\t\n\r\f\v ]*$"
)
_ELECTION_BRACKET_SOURCE_TAGS = frozenset({"retail"})
_EDITION = re.compile(
    r"[\t\n\r\f\v ]*(?:[-,:][\t\n\r\f\v ]*)?"
    r"(?:\d+(?:st|nd|rd|th)[\t\n\r\f\v ]+)?"
    r"(?:(?:anniversary|revised|updated|special|collector'?s)[\t\n\r\f\v ]+)?"
    r"(?:edition|ed\.)[\t\n\r\f\v ]*$",
    re.IGNORECASE,
)
_PAREN = re.compile(r"[\t\n\r\f\v ]*\(([^()]*)\)[\t\n\r\f\v ]*$")
# The one closed series-marker vocabulary every series position grammar reads.
_SERIES_WORD = r"(?:book|volume|vol\.?|part)"
_SERIES_MARKER = re.compile(
    r"[\t\n\r\f\v ]*(?:[-,:][\t\n\r\f\v ]*)?"
    rf"{_SERIES_WORD}[\t\n\r\f\v ]*#?[\t\n\r\f\v ]*"
    r"(?:\d+(?:\.\d+)?|[ivxlcdm]+)[\t\n\r\f\v ]*$",
    re.IGNORECASE,
)
_INVERTED_SERIES = re.compile(
    rf"^(?P<series>.+?)\b(?:{_SERIES_WORD}|#)[\t\n\r\f\v ]*"
    r"(?P<sequence>\d+(?:\.\d+)?)"
    r"[\t\n\r\f\v ]+[-–—][\t\n\r\f\v ]+(?P<title>.+)$",
    re.IGNORECASE,
)
# A parenthetical ending in a position, "Saga 3" or "Saga, Book 3": the series
# name, then a comma or whitespace, an optional marker, and a decimal number.
_PAREN_SERIES_POSITION = re.compile(
    r"(?P<series>.+?)(?:[\t\n\r\f\v ]*,[\t\n\r\f\v ]*|[\t\n\r\f\v ]+)"
    rf"(?:(?:{_SERIES_WORD}|#)[\t\n\r\f\v ]*)?(?P<sequence>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# A leading position token: "3. Title", "Book 2: Title", "#4 - Title".  At
# most three digits, so a year-shaped or four-digit title ("1984", "2001: A
# Space Odyssey") never matches, and a colon or stop must be followed by a
# space, so a time ("3:10 to Yuma") never matches either.
_LEADING_SEQUENCE = re.compile(
    r"(?:(?:book|vol(?:ume)?|part|no)\.? ?)?#?[0-9]{1,3}(?:\.[0-9]{1,2})?"
    r"(?:[-–—:.)] | [-–—] )(?P<rest>.+)",
    re.IGNORECASE,
)
# A bare leading number with no separator: "12 Title" (#1307).  At most three
# digits, so a year-shaped title never matches, and only a space follows it,
# so every form `_LEADING_SEQUENCE` reads is left to that family.
_LEADING_NUMBER = re.compile(r"[0-9]{1,3} (?P<rest>[^\W\d_].*)")
# A position the shared series grammar above does not spell: "Saga #3", "No. 4".
_POSITION_NUMBER = re.compile(
    r"(?:^|[\t\n\r\f\v ])(?:#|no\.?[\t\n\r\f\v ]*#?)[\t\n\r\f\v ]*"
    r"[0-9]{1,3}(?:\.[0-9]{1,2})?$",
    re.IGNORECASE,
)
_SPACED_DASHES = (" - ", " – ", " — ")
# A path leaf's leading track number: "03 - Title", "1. Title".
_LEADING_TRACK_NUMBER = re.compile(r"[0-9]{1,3} ?[-–—.] ")
_ROMAN_NUMERAL = re.compile(
    r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})", re.IGNORECASE
)
# The one closed stop list both audiobook title grammars read: a segment made
# only of these is decoration, and a trailing " - Name" holding one is not a
# name.  Roman numerals and digit-bearing tokens are stop tokens as well.
_DISCOVERY_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "book",
        "part",
        "volume",
        "vol",
        "chapter",
        "episode",
        "edition",
        "unabridged",
        "abridged",
        "dramatized",
        "collection",
        "omnibus",
        "trilogy",
        "series",
        "saga",
        "chronicles",
    }
)
_MAX_DISCOVERY_SOURCE_CHARACTERS = 255
_MAX_DISCOVERY_STRIP_PASSES = 4
_IDENTIFIER = re.compile(
    r"\b(?P<label>isbn(?:-1[03])?|asin)[\t\n\r\f\v ]*[:#-]?[\t\n\r\f\v ]*"
    r"(?P<value>[0-9A-Z][0-9A-Z -]{7,20})\b",
    re.IGNORECASE,
)
# An identifier may be printed as a bracketed suffix, "[ISBN 978...]" or
# "(ISBN 978...)"; the brackets go with a valid identifier they enclose whole
# (#1496).
_BRACKETED_IDENTIFIER = re.compile(
    r"\[[\t\n\r\f\v ]*(?P<square>[^\[\]]*?)[\t\n\r\f\v ]*\]"
    r"|\([\t\n\r\f\v ]*(?P<round>[^()]*?)[\t\n\r\f\v ]*\)"
)
_ELECTION_BRACKETED_ISBN = re.compile(
    r"(?P<title>.*?\S)[\t\n\r\f\v ]+"
    r"(?:\[(?P<square>[^\[\]]*)\]|\((?P<round>[^()]*)\))[\t\n\r\f\v ]*"
)
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_VARIANT = re.compile(
    r"\b(?:abridg(?:e?ment|ed)|adapt(?:ation|ed)|translat(?:ion|ed)|variant)\b",
    re.IGNORECASE,
)
_TYPOGRAPHIC_TRANSLATION = str.maketrans(
    {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-"}
)
_ADMISSION_TYPOGRAPHIC_TRANSLATION = str.maketrans({"\u00a0": " ", "–": "-", "—": "-"})


def work_query_ladder(
    title: str,
    authors: tuple[str, ...],
    *,
    audiobook: bool = False,
    leading_number: bool = False,
) -> tuple[WorkQuery, ...]:
    """Return the distinct, deterministic WORK dispatches, in ladder order.

    An ebook ladder has at most three rungs: normalized, subtitle-stripped and
    punctuation-folded. An audiobook ladder shapes those rungs with its own
    rows, and an asserted Audible lane may add a fourth (below).

    `audiobook` admits the audiobook-only rows; an ebook ladder never reads them.
    `leading_number` appends one fourth rung, the normalized title with a bare
    leading number stripped (#1307); only an audiobook plan's asserted lane at
    Audible asks for it, so every other ladder is the ladder it was before.
    """
    if (
        type(title) is not str
        or type(authors) is not tuple
        or not authors
        or any(type(value) is not str for value in authors)
        or type(audiobook) is not bool
        or type(leading_number) is not bool
        or leading_number
        and not audiobook
    ):
        raise ValueError("work query input is invalid")
    shaped_title = _normalized_query_text(title)
    shaped_authors = tuple(_normalized_query_text(value) for value in authors)
    latin_inputs_applicable = _latin_combining_marks_fold(
        shaped_title
    ) is not None and all(
        _latin_combining_marks_fold(value) is not None for value in shaped_authors
    )
    applied: list[WorkTitleTransformationFamily] = []
    for rule in WORK_TITLE_TRANSFORMATION_RULES:
        if (
            rule.rung is not WorkDispatchRung.NORMALIZED
            or not rule.query_enabled
            or (rule.audiobook_only and not audiobook)
        ):
            continue
        transformed = apply_work_title_transformation(
            rule.family, shaped_title, shaped_authors
        )
        if transformed != shaped_title:
            shaped_title = transformed
            applied.append(rule.family)
    candidates: list[WorkQuery] = []
    if shaped_title and all(shaped_authors):
        candidates.append(
            WorkQuery(
                shaped_title,
                shaped_authors,
                WorkDispatchRung.NORMALIZED,
                tuple(applied),
            )
        )
    subtitle = apply_work_title_transformation(
        WorkTitleTransformationFamily.SUBTITLE_SEGMENT,
        shaped_title,
        shaped_authors,
    )
    subtitle_applied = tuple(applied)
    if subtitle != shaped_title:
        subtitle_applied += (WorkTitleTransformationFamily.SUBTITLE_SEGMENT,)
    if subtitle and all(shaped_authors):
        candidates.append(
            WorkQuery(
                subtitle,
                shaped_authors,
                WorkDispatchRung.SUBTITLE_STRIPPED,
                subtitle_applied,
            )
        )
    folded_title = apply_work_title_transformation(
        WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,
        subtitle,
        shaped_authors,
    )
    folded_authors = tuple(_punctuation_fold(value) for value in shaped_authors)
    folded_applied = subtitle_applied
    if folded_title != subtitle or folded_authors != shaped_authors:
        folded_applied += (WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,)
    latin_title = _latin_combining_marks_fold(folded_title)
    latin_authors = tuple(
        _latin_combining_marks_fold(value) for value in folded_authors
    )
    if (
        latin_inputs_applicable
        and latin_title is not None
        and all(value is not None for value in latin_authors)
    ):
        if latin_title != folded_title or latin_authors != folded_authors:
            folded_title = latin_title
            folded_authors = tuple(
                value for value in latin_authors if value is not None
            )
            folded_applied += (WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,)
    if folded_title and all(folded_authors):
        candidates.append(
            WorkQuery(
                folded_title,
                folded_authors,
                WorkDispatchRung.PUNCTUATION_FOLDED,
                folded_applied,
            )
        )
    if leading_number:
        stripped = apply_work_title_transformation(
            WorkTitleTransformationFamily.LEADING_NUMBER, shaped_title, shaped_authors
        )
        if stripped != shaped_title and all(shaped_authors):
            candidates.append(
                WorkQuery(
                    stripped,
                    shaped_authors,
                    WorkDispatchRung.LEADING_NUMBER_STRIPPED,
                    (*applied, WorkTitleTransformationFamily.LEADING_NUMBER),
                )
            )
    distinct: list[WorkQuery] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for value in candidates:
        key = (value.title.casefold(), tuple(item.casefold() for item in value.authors))
        if key not in seen:
            seen.add(key)
            distinct.append(value)
    return tuple(distinct)


def _dispatch_transformations_are_canonical(
    rung: WorkDispatchRung,
    transformations: tuple[WorkTitleTransformationFamily, ...],
) -> bool:
    """Reject persisted lineage combinations the bounded ladder cannot emit."""
    if any(not _RULES_BY_FAMILY[family].query_enabled for family in transformations):
        return False
    if tuple(sorted(transformations, key=_RULE_ORDINAL.__getitem__)) != transformations:
        return False
    if rung is WorkDispatchRung.NORMALIZED:
        return all(
            _RULES_BY_FAMILY[family].rung is WorkDispatchRung.NORMALIZED
            for family in transformations
        )
    if rung is WorkDispatchRung.LEADING_NUMBER_STRIPPED:
        return bool(transformations) and (
            transformations[-1] is WorkTitleTransformationFamily.LEADING_NUMBER
            and all(
                _RULES_BY_FAMILY[family].rung is WorkDispatchRung.NORMALIZED
                for family in transformations[:-1]
            )
        )
    if rung is WorkDispatchRung.SUBTITLE_STRIPPED:
        return bool(transformations) and (
            transformations[-1] is WorkTitleTransformationFamily.SUBTITLE_SEGMENT
            and all(
                _RULES_BY_FAMILY[family].rung is WorkDispatchRung.NORMALIZED
                for family in transformations[:-1]
            )
        )
    return bool(transformations) and (
        transformations[-1]
        in {
            WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
        }
        and all(
            _RULES_BY_FAMILY[family].rung is WorkDispatchRung.NORMALIZED
            or (
                family is WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD
                and transformations[-1]
                is WorkTitleTransformationFamily.LATIN_COMBINING_MARKS
            )
            for family in transformations[:-1]
            if family is not WorkTitleTransformationFamily.SUBTITLE_SEGMENT
        )
        and transformations[:-1].count(WorkTitleTransformationFamily.SUBTITLE_SEGMENT)
        <= 1
        and transformations.count(
            WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD
        )
        <= 1
    )


def narrowing_work_query(value: WorkQuery) -> WorkQuery:
    """Return the single narrowed retry while preserving its base rung."""
    if type(value) is not WorkQuery or value.candidate_limit_narrowing_retry:
        raise ValueError("work query narrowing retry is invalid")
    return WorkQuery(
        value.title,
        value.authors,
        value.rung,
        value.transformations,
        True,
    )


def admit_provider_work_identity(
    *,
    local_title: str,
    local_authors: tuple[str, ...],
    provider_title: str,
    provider_authors: tuple[str, ...],
    provider_work_identity: str,
    variant_excluded: bool = False,
    allow_reordered_authors: bool = False,
    audiobook: bool = False,
    runtime_tight: bool = False,
) -> WorkIdentityAdmission:
    """Apply the one closed work-title/author identity decision.

    `runtime_tight` says the candidate is inside the tight runtime band on an
    audiobook plan's asserted lane; only then may the initials fold admit.
    """
    if (
        type(local_title) is not str
        or type(provider_title) is not str
        or type(local_authors) is not tuple
        or type(provider_authors) is not tuple
        or not local_authors
        or len(local_authors) != len(provider_authors)
        or any(type(value) is not str for value in (*local_authors, *provider_authors))
        or type(provider_work_identity) is not str
        or not provider_work_identity
        or type(variant_excluded) is not bool
        or type(audiobook) is not bool
        or type(allow_reordered_authors) is not bool
        or type(runtime_tight) is not bool
        or variant_excluded
        or work_title_has_variant_marker(local_title)
        or work_title_has_variant_marker(provider_title)
    ):
        return WorkIdentityAdmission(False)
    local_latin_title = _latin_combining_marks_fold(local_title)
    provider_latin_title = _latin_combining_marks_fold(provider_title)
    if (
        local_latin_title is not None
        and provider_latin_title is not None
        and (
            work_title_has_variant_marker(local_latin_title)
            or work_title_has_variant_marker(provider_latin_title)
        )
    ):
        return WorkIdentityAdmission(False)
    if _closed_equal(local_title, provider_title) and all(
        _closed_equal(local, provider)
        for local, provider in zip(local_authors, provider_authors, strict=True)
    ):
        return WorkIdentityAdmission(True)
    if (
        allow_reordered_authors
        and _closed_equal(local_title, provider_title)
        and _closed_author_multiset_equal(local_authors, provider_authors)
    ):
        return WorkIdentityAdmission(True)
    suffix = " (Unabridged)"
    if (
        audiobook
        and local_title.endswith(suffix)
        and local_title.count(suffix) == 1
        and _comparison_text(local_title[: -len(suffix)])
        and _closed_equal(local_title[: -len(suffix)], provider_title)
        and _closed_author_multiset_equal(local_authors, provider_authors)
        and all(
            not _closed_equal(author, other)
            for authors in (local_authors, provider_authors)
            for index, author in enumerate(authors)
            for other in authors[index + 1 :]
        )
    ):
        return WorkIdentityAdmission(
            True,
            WorkTitleTransformationLineage(
                (
                    WorkTitleTransformationApplication(
                        WorkTitleTransformationFamily.LOCAL_AUDIOBOOK_UNABRIDGED,
                        WorkTitleTransformationSide.LOCAL,
                        WorkTitleTransformationField.TITLE,
                        None,
                    ),
                )
            ),
        )
    latin = _latin_admission_lineage(
        local_title, local_authors, provider_title, provider_authors
    )
    if latin is not None:
        return WorkIdentityAdmission(True, latin)
    if audiobook and runtime_tight and _closed_equal(local_title, provider_title):
        initials = _initials_fold_lineage(local_authors, provider_authors)
        if initials is not None:
            return WorkIdentityAdmission(True, initials)
    remainder = _filename_author_title_prefix_remainder(local_title, local_authors)
    if (
        remainder is None
        or "_" not in remainder
        or any(
            not _closed_equal(local, provider)
            for local, provider in zip(local_authors, provider_authors, strict=True)
        )
    ):
        # Prefix-only and underscores-only inputs stay query shaping: neither
        # constituent family admits outside the reviewed compound production.
        return WorkIdentityAdmission(False)
    normalized = _clean(remainder.replace("_", " "))
    if not _comparison_text(normalized) or not _closed_equal(
        normalized, provider_title
    ):
        return WorkIdentityAdmission(False)
    return WorkIdentityAdmission(
        True,
        WorkTitleTransformationLineage(
            (
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
                    WorkTitleTransformationSide.LOCAL,
                    WorkTitleTransformationField.TITLE,
                    None,
                ),
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
                    WorkTitleTransformationSide.LOCAL,
                    WorkTitleTransformationField.TITLE,
                    None,
                ),
            ),
        ),
    )


def replay_provider_work_identity_admission(
    *,
    local_title: str,
    local_authors: tuple[str, ...],
    provider_title: str,
    provider_authors: tuple[str, ...],
    provider_work_identity: str,
    dispatch_lineage: WorkDispatchLineage | None,
    variant_excluded: bool = False,
    provider_series: tuple[tuple[str, str], ...] = (),
    provider_series_memberships: tuple[tuple[str, str | None], ...] = (),
    provider_series_names: tuple[str, ...] = (),
    audiobook: bool = False,
    runtime_tight: bool = False,
    leading_number: bool = False,
) -> WorkIdentityAdmission:
    """Recompute admission only for a dispatch the exact local ladder can emit.

    `leading_number` permits the `leading_number_stripped` rung, which only
    Audible's asserted audiobook lane dispatches (#1307); every other lane
    replays the ladder it can actually ask.

    `runtime_tight` enables the band-corroborated productions (#1260); the
    caller passes it only for a tight candidate on an audiobook plan's asserted
    lane from an audiobook authority. A band title production also needs one
    of `provider_series_memberships`, the product's own stated series names and
    positions, to state the series the rung stripped. An ebook's series-name
    production also reads `provider_series_names`, every series name the
    product states with or without a position, so a stated series always has
    to be this one (#1499).
    """
    admission = admit_provider_work_identity(
        local_title=local_title,
        local_authors=local_authors,
        provider_title=provider_title,
        provider_authors=provider_authors,
        provider_work_identity=provider_work_identity,
        variant_excluded=variant_excluded,
        allow_reordered_authors=True,
        audiobook=audiobook,
        runtime_tight=runtime_tight,
    )
    if not admission.admitted and len(provider_series) == 1:
        series, sequence = provider_series[0]
        suffix = f": {series}, Book {sequence}"
        for decorated, side in (
            (provider_title, WorkTitleTransformationSide.PROVIDER),
            (local_title, WorkTitleTransformationSide.LOCAL),
        ):
            if not decorated.endswith(suffix):
                continue
            clean = decorated[: -len(suffix)]
            exact = admit_provider_work_identity(
                local_title=clean
                if side is WorkTitleTransformationSide.LOCAL
                else local_title,
                local_authors=local_authors,
                provider_title=clean
                if side is WorkTitleTransformationSide.PROVIDER
                else provider_title,
                provider_authors=provider_authors,
                provider_work_identity=provider_work_identity,
                variant_excluded=variant_excluded,
                allow_reordered_authors=True,
            )
            if exact.admitted and exact.transformation is None:
                admission = WorkIdentityAdmission(
                    True,
                    WorkTitleTransformationLineage(
                        (
                            WorkTitleTransformationApplication(
                                WorkTitleTransformationFamily.PROVIDER_ASSOCIATED_SERIES_SUFFIX,
                                side,
                                WorkTitleTransformationField.TITLE,
                                None,
                            ),
                        )
                    ),
                )
                break
    if (
        not admission.admitted
        and audiobook
        and runtime_tight
        and dispatch_lineage is not None
        and dispatch_lineage.rung is WorkDispatchRung.NORMALIZED
        and len(dispatch_lineage.transformations) == 1
        and dispatch_lineage.transformations[0] in _BAND_TITLE_FAMILIES
        and not work_title_has_variant_marker(local_title)
        and _RECORDING_FORMAT.search(local_title) is None
        and _band_decoration_corroborated(
            dispatch_lineage.transformations[0],
            local_title,
            provider_series_memberships,
        )
    ):
        # The rung's own shaped title is what this band-first production
        # compares: exactly one ladder decoration away from the local title.
        rung = next(
            (
                query
                for query in work_query_ladder(
                    local_title, local_authors, audiobook=True
                )
                if query.rung is dispatch_lineage.rung
                and query.transformations == dispatch_lineage.transformations
            ),
            None,
        )
        if (
            rung is not None
            and (
                exact := admit_provider_work_identity(
                    local_title=rung.title,
                    local_authors=local_authors,
                    provider_title=provider_title,
                    provider_authors=provider_authors,
                    provider_work_identity=provider_work_identity,
                    variant_excluded=variant_excluded,
                )
            ).admitted
            and exact.transformation is None
        ):
            admission = WorkIdentityAdmission(
                True,
                WorkTitleTransformationLineage(
                    (
                        WorkTitleTransformationApplication(
                            dispatch_lineage.transformations[0],
                            WorkTitleTransformationSide.LOCAL,
                            WorkTitleTransformationField.TITLE,
                            None,
                        ),
                    )
                ),
            )
    if (
        not admission.admitted
        and not audiobook
        and dispatch_lineage is not None
        and dispatch_lineage.rung is WorkDispatchRung.NORMALIZED
        and dispatch_lineage.transformations
        == (WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,)
        and not work_title_has_variant_marker(local_title)
        and _RECORDING_FORMAT.search(local_title) is None
        and _ebook_series_decoration(
            local_title, provider_series_memberships, provider_series_names
        )
    ):
        # An ebook titled "Title (Series Name)" is the work its provider
        # calls "Title" by the same authors: the rung that stripped the
        # series name alone is compared exactly, and nothing else may differ
        # (#1499).
        rung = next(
            (
                query
                for query in work_query_ladder(local_title, local_authors)
                if query.rung is dispatch_lineage.rung
                and query.transformations == dispatch_lineage.transformations
            ),
            None,
        )
        if (
            rung is not None
            and (
                exact := admit_provider_work_identity(
                    local_title=rung.title,
                    local_authors=local_authors,
                    provider_title=provider_title,
                    provider_authors=provider_authors,
                    provider_work_identity=provider_work_identity,
                    variant_excluded=variant_excluded,
                    allow_reordered_authors=True,
                )
            ).admitted
            and exact.transformation is None
        ):
            admission = WorkIdentityAdmission(
                True,
                WorkTitleTransformationLineage(
                    (
                        WorkTitleTransformationApplication(
                            WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
                            WorkTitleTransformationSide.LOCAL,
                            WorkTitleTransformationField.TITLE,
                            None,
                        ),
                    )
                ),
            )
    if not admission.admitted or dispatch_lineage is None:
        return WorkIdentityAdmission(False)
    queries = work_query_ladder(
        local_title,
        local_authors,
        audiobook=audiobook,
        leading_number=audiobook and leading_number,
    )
    permitted = {WorkDispatchLineage.from_query(query) for query in queries} | {
        WorkDispatchLineage.from_query(narrowing_work_query(query)) for query in queries
    }
    return admission if dispatch_lineage in permitted else WorkIdentityAdmission(False)


_RELATION_ARTICLES = frozenset({"the", "a", "an"})
# The token overlap at or above which two titles are related (#1307).
TITLE_RELATION_OVERLAP = 0.5


def _relation_tokens(value: str) -> tuple[str, ...]:
    decomposed = unicodedata.normalize("NFKD", value)
    folded = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()
    folded = re.sub(r"['‘’`]", "", folded.replace("&", " and "))
    folded = re.sub(r"[^\w\s]|_", " ", folded)
    return tuple(token for token in folded.split() if token not in _RELATION_ARTICLES)


def _contains_tokens(outer: tuple[str, ...], inner: tuple[str, ...]) -> bool:
    return bool(inner) and any(
        outer[index : index + len(inner)] == inner
        for index in range(len(outer) - len(inner) + 1)
    )


def work_title_related(
    local_title: str, provider_title: str, provider_subtitle: str | None = None
) -> bool:
    """Whether a provider title is related to the local one (#1307).

    The mandatory title guard of the corroborated admission rule. Both sides
    fold to word tokens without marks, case, apostrophes, punctuation or a
    leading article. They are related when the tokens are equal, when the local
    title is the provider title joined to its subtitle, when either side's
    tokens run whole inside the other's (the provider side with its subtitle
    joined, or its title before a colon), or when the local tokens and the
    joined provider tokens overlap by at least one half. Relation alone admits
    nothing: it only keeps a different work out of the rule.
    """
    if (
        type(local_title) is not str
        or type(provider_title) is not str
        or provider_subtitle is not None
        and type(provider_subtitle) is not str
    ):
        raise ValueError("work title relation input is invalid")
    local = _relation_tokens(local_title)
    title = _relation_tokens(provider_title)
    joined = _relation_tokens(f"{provider_title} {provider_subtitle or ''}")
    main = _relation_tokens(provider_title.split(":", 1)[0])
    if not local or not title:
        return False
    if (
        local in {title, joined}
        or _contains_tokens(joined, local)
        or _contains_tokens(local, title)
        or _contains_tokens(local, main)
    ):
        return True
    left, right = set(local), set(joined)
    return len(left & right) >= TITLE_RELATION_OVERLAP * len(left | right)


# Words that decorate a title rather than name a work: the stop list both
# audiobook title grammars share, plus the generic provider subtitles (#1307).
_TITLE_DECORATION_TOKENS = _DISCOVERY_STOP_WORDS | frozenset({"novel", "stories"})


# A roman numeral two to six letters long, up to 39: "ii", "xiv". A single
# letter ("i", "v", "x") and a word that only spells one ("mix", "mi", "di")
# are words, not numerals.
_TITLE_ROMAN_NUMERAL = re.compile(r"x{0,3}(?:ix|iv|v?i{0,3})")


def _number_token(token: str) -> bool:
    return any(character.isdigit() for character in token) or (
        2 <= len(token) <= 6 and _TITLE_ROMAN_NUMERAL.fullmatch(token) is not None
    )


def work_title_extends(
    local_title: str,
    provider_title: str,
    provider_subtitle: str | None = None,
    *,
    decorations: tuple[str, ...] = (),
    sequences: tuple[str, ...] = (),
) -> bool:
    """Whether one title runs on past the whole other with a new word (#1307).

    The provider title is read joined to its subtitle, and either side may be
    the longer: "Example Journey Home" and "Example Journey: The Return" both
    extend "Example Journey", whichever side holds them. That is the shape of
    a sequel or companion in the same series, which title relation alone
    cannot tell from the same book. Words that only decorate a title -- the
    shared stop list ("book", "edition", "unabridged", ...), "a novel" and the
    words of `decorations`, the series names either side states -- do not
    extend it, so "Example Journey: A Novel" does not.

    A number or roman numeral is a sequel's mark rather than decoration when
    it follows the shorter title ("Example Journey 2", "Example Journey II",
    "Example Journey: Volume 2") or equals one of `sequences`, the product's
    own series positions. A number before the shorter title, as in "12
    Example Journey" against "Example Journey", stays decoration unless it is
    that position. A roman numeral is two to six letters up to 39, so "mix",
    "mi" and "i" are words.
    """
    if (
        type(local_title) is not str
        or type(provider_title) is not str
        or provider_subtitle is not None
        and type(provider_subtitle) is not str
        or type(decorations) is not tuple
        or any(type(value) is not str for value in decorations)
        or type(sequences) is not tuple
        or any(type(value) is not str for value in sequences)
    ):
        raise ValueError("work title relation input is invalid")
    local = _relation_tokens(local_title)
    joined = _relation_tokens(f"{provider_title} {provider_subtitle or ''}")
    if len(local) == len(joined):
        return False
    longer, shorter = (joined, local) if len(joined) > len(local) else (local, joined)
    if not shorter:
        return False
    index = next(
        (
            start
            for start in range(len(longer) - len(shorter) + 1)
            if longer[start : start + len(shorter)] == shorter
        ),
        None,
    )
    if index is None:
        return False
    named = frozenset(
        token for value in decorations for token in _relation_tokens(value)
    )
    positions = frozenset(
        token for value in sequences for token in _relation_tokens(value)
    )
    before = longer[:index]
    after = longer[index + len(shorter) :]
    for token in (*before, *after):
        if _number_token(token):
            if token in positions or token in after:
                return True
            continue
        if token not in _TITLE_DECORATION_TOKENS and token not in named:
            return True
    return False


def work_title_has_variant_marker(value: str) -> bool:
    """Return whether the closed work-title variant vocabulary excludes a value."""
    if type(value) is not str:
        raise ValueError("work title is invalid")
    return _VARIANT.search(value) is not None


def work_dispatch_lineage_from_data(value: object) -> WorkDispatchLineage:
    if type(value) is not dict or set(value) != {
        "candidate_limit_narrowing_retry",
        "rung",
        "transformations",
    }:
        raise ValueError("work dispatch lineage is invalid")
    transformations = value["transformations"]
    if type(transformations) is not list:
        raise ValueError("work dispatch lineage is invalid")
    return WorkDispatchLineage(
        WorkDispatchRung(value["rung"]),
        tuple(WorkTitleTransformationFamily(item) for item in transformations),
        value["candidate_limit_narrowing_retry"],
    )


def work_title_transformation_lineage_from_data(
    value: object,
) -> WorkTitleTransformationLineage:
    if type(value) is not dict or set(value) != {"applications", "production"}:
        raise ValueError("work title transformation lineage is invalid")
    applications = value["applications"]
    if type(applications) is not list:
        raise ValueError("work title transformation lineage is invalid")
    lineage = WorkTitleTransformationLineage(
        tuple(
            _work_title_transformation_application_from_data(item)
            for item in applications
        ),
    )
    if lineage.production != value["production"]:
        raise ValueError("work title transformation lineage is invalid")
    return lineage


def _work_title_transformation_application_from_data(
    value: object,
) -> WorkTitleTransformationApplication:
    if type(value) is not dict or set(value) != {"family", "field", "ordinal", "side"}:
        raise ValueError("work title transformation application is invalid")
    return WorkTitleTransformationApplication(
        WorkTitleTransformationFamily(value["family"]),
        WorkTitleTransformationSide(value["side"]),
        WorkTitleTransformationField(value["field"]),
        value["ordinal"],
    )


def apply_work_title_transformation(
    family: WorkTitleTransformationFamily,
    title: str,
    authors: tuple[str, ...],
) -> str:
    """Apply one row of the shared closed work-title transformation table."""
    if (
        type(family) is not WorkTitleTransformationFamily
        or type(title) is not str
        or type(authors) is not tuple
        or any(type(value) is not str for value in authors)
    ):
        raise ValueError("work title transformation input is invalid")
    if family is WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX:
        return _filename_author_title_prefix_query(title, authors) or title
    if family is WorkTitleTransformationFamily.FILENAME_UNDERSCORES:
        return _clean(title.replace("_", " "))
    if family is WorkTitleTransformationFamily.BRACKET_SOURCE_TAG:
        return _clean(_BRACKET_SOURCE.sub("", title))
    if family is WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER:
        return _clean(
            _IDENTIFIER.sub(
                _remove_valid_identifier,
                _BRACKETED_IDENTIFIER.sub(_remove_bracketed_identifier, title),
            )
        )
    if family is WorkTitleTransformationFamily.EDITION_LABEL:
        return _clean(_EDITION.sub("", title))
    if family in {
        WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
        WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER,
        WorkTitleTransformationFamily.PARENTHESIZED_YEAR,
    }:
        match = _PAREN.search(title)
        if match is None:
            return title
        inside = _clean(match.group(1))
        if family is WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER:
            applies = bool(re.fullmatch(r"(?:[1-9]\d{0,2}|0)", inside)) and not _year(
                inside
            )
        elif family is WorkTitleTransformationFamily.PARENTHESIZED_YEAR:
            applies = _year(inside)
        else:
            applies = bool(inside) and not inside.isdigit()
        return _clean(title[: match.start()]) if applies else title
    if family is WorkTitleTransformationFamily.TRAILING_SERIES_SEQUENCE:
        return _clean(_SERIES_MARKER.sub("", title))
    if family is WorkTitleTransformationFamily.ORDER_INVERTED_SERIES_PREFIX:
        match = _INVERTED_SERIES.fullmatch(title)
        return _clean(match.group("title")) if match is not None else title
    if family is WorkTitleTransformationFamily.LEADING_NUMBER:
        match = _LEADING_NUMBER.fullmatch(title)
        rest = "" if match is None else _clean(match.group("rest"))
        return rest if _has_letter(rest) else title
    if family is WorkTitleTransformationFamily.CORROBORATED_TITLE_RELATION:
        return title
    if family is WorkTitleTransformationFamily.LEADING_SEQUENCE_TOKEN:
        match = _LEADING_SEQUENCE.fullmatch(title)
        rest = "" if match is None else _clean(match.group("rest"))
        return rest if _has_letter(rest) else title
    if family is WorkTitleTransformationFamily.RIGHT_HAND_SEGMENT:
        if sum(title.count(separator) for separator in _SPACED_DASHES) != 1:
            return title
        separator = next(value for value in _SPACED_DASHES if value in title)
        left, right = (_clean(value) for value in title.split(separator, 1))
        if (
            not _has_letter(left)
            or not _has_letter(right)
            or _SERIES_MARKER.fullmatch(right) is not None
            or all(_discovery_stop_token(token) for token in right.split())
        ):
            return title
        return right
    if family is WorkTitleTransformationFamily.SUBTITLE_SEGMENT:
        if ":" in title:
            return _clean(title.split(":", 1)[0])
        for separator in (" - ", " – ", " — "):
            if separator in title:
                return _clean(title.split(separator, 1)[0])
        return title
    if family is WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD:
        return _punctuation_fold(title)
    if family is WorkTitleTransformationFamily.LATIN_COMBINING_MARKS:
        folded = _latin_combining_marks_fold(title)
        return title if folded is None else folded
    raise RuntimeError("work title transformation table is incomplete")


def discovery_title_candidate(text: str) -> str | None:
    """Shape one folder leaf or title tag into query-only discovery text.

    The input is hostile: a folder name or a container tag the owner never
    vetted.  Anything that could be a path, carries a control, a bidirectional
    override or a noncharacter, or runs past one filesystem component is
    refused whole rather than repaired.  What survives is stripped of trailing
    bracket tags, a trailing "(YYYY)" and a trailing " - Name" credit, in at
    most four bounded passes, and of a leading "NN - " track number, and is
    returned only if it is still public-safe projection text naming a letter.
    It is the same NFC, whitespace-collapsed form `normalize_title` returns.
    """
    if (
        type(text) is not str
        or len(text) > _MAX_DISCOVERY_SOURCE_CHARACTERS
        or any(character in text for character in "/\\\x00")
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in text
        )
        or _FORBIDDEN_CODEPOINTS.search(text) is not None
    ):
        return None
    value = " ".join(unicodedata.normalize("NFC", text).split())
    name_stripped = False
    for _ in range(_MAX_DISCOVERY_STRIP_PASSES):
        before = value
        value = _clean(_BRACKET_SOURCE.sub("", value))
        value = apply_work_title_transformation(
            WorkTitleTransformationFamily.PARENTHESIZED_YEAR, value, ()
        )
        if not name_stripped:
            stripped = _trailing_name_stripped(value)
            name_stripped = stripped != value
            value = stripped
        if value == before:
            break
    track = _LEADING_TRACK_NUMBER.match(value)
    value = _clean(value if track is None else value[track.end() :])
    if not _has_letter(value):
        return None
    try:
        return validate_projection_text(
            value, "discovery title", MAX_OBSERVED_CHARACTERS
        )
    except EvidenceValueError:
        return None


def _trailing_name_stripped(value: str) -> str:
    """Strip one trailing " - Name" credit of one to four capitalised tokens."""
    index, separator = max(
        (value.rfind(separator), separator) for separator in _SPACED_DASHES
    )
    if index < 0:
        return value
    left = _clean(value[:index])
    tokens = value[index + len(separator) :].split(" ")
    if (
        not _has_letter(left)
        or not 1 <= len(tokens) <= 4
        or any(
            not token[:1].isupper()
            or any(not (c.isalpha() or c in ".'’-") for c in token)
            or _discovery_stop_token(token)
            for token in tokens
        )
    ):
        return value
    return left


def _discovery_stop_token(token: str) -> bool:
    word = token.strip(".,:;!?'\"()[]").casefold()
    return (
        not word
        or word in _DISCOVERY_STOP_WORDS
        or any(character.isdigit() for character in word)
        or _ROMAN_NUMERAL.fullmatch(word) is not None
    )


def _has_letter(value: str) -> bool:
    return any(character.isalpha() for character in value)


def series_label_segment(segment: str) -> bool:
    """Whether one title segment reads as a series label by grammar alone.

    The grammar is the transformation table's own: a segment ending in a series
    position ("Saga, Book 1", "Vol. 2", "Saga #3", "No. 4"), the series prefix
    of an order-inverted title ("Saga Book 2" before " - Title"), or a whole
    leading sequence token ("Book 2", "#4"). A series named without a position
    is not a label here; only the caller's series evidence can say so (#1252).
    """
    text = _clean(segment)
    if not text:
        return False
    # Each title-shaped grammar is asked about this segment as the whole of
    # its own part, so a longer segment that only starts with one is not read.
    inverted = _INVERTED_SERIES.fullmatch(f"{text} - x")
    leading = _LEADING_SEQUENCE.fullmatch(f"{text}: x")
    return (
        _SERIES_MARKER.search(text) is not None
        or _POSITION_NUMBER.search(text) is not None
        or (inverted is not None and inverted.group("title") == "x")
        or (leading is not None and leading.group("rest") == "x")
    )


def work_title_election_matches(
    title: str, authors: tuple[str, ...]
) -> tuple[WorkTitleElectionMatch, ...]:
    """Return every closed one-step decoration match for consensus election.

    Query shaping stays deliberately broader.  This grammar is only the small
    reversible subset whose removed component can be shown to the owner.
    """
    if (
        type(title) is not str
        or type(authors) is not tuple
        or any(type(value) is not str for value in authors)
    ):
        raise ValueError("work title election input is invalid")
    matches: list[WorkTitleElectionMatch] = []
    pending = [title]
    seen = {title}
    while pending:
        source = pending.pop()
        direct = direct_work_title_election_matches(source, authors)
        matches.extend(direct)
        for value in direct:
            if value.clean_title not in seen:
                seen.add(value.clean_title)
                pending.append(value.clean_title)
    return tuple(matches)


def direct_work_title_election_matches(
    title: str, authors: tuple[str, ...]
) -> tuple[WorkTitleElectionMatch, ...]:
    """Return only the one-step decoration matches of a title.

    `work_title_election_matches` takes the closure; equivalence-class
    formation needs the single step so stacked decorations stay competing.
    """
    if (
        type(title) is not str
        or type(authors) is not tuple
        or any(type(value) is not str for value in authors)
    ):
        raise ValueError("work title election input is invalid")
    isbn = _election_isbn_component(title)
    if isbn is not None:
        isbn_clean_title, identifier_component = isbn
        return (
            WorkTitleElectionMatch(
                WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER,
                isbn_clean_title,
                identifier_component,
            ),
        )
    matches: list[WorkTitleElectionMatch] = []
    for family in (
        WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,
        WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER,
        WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
        WorkTitleTransformationFamily.PARENTHESIZED_YEAR,
        WorkTitleTransformationFamily.SUBTITLE_SEGMENT,
    ):
        if family is WorkTitleTransformationFamily.BRACKET_SOURCE_TAG:
            bracket_source = _ELECTION_BRACKET_SOURCE.search(title)
            if (
                bracket_source is None
                or bracket_source.group("tag") not in _ELECTION_BRACKET_SOURCE_TAGS
            ):
                continue
        clean_title = apply_work_title_transformation(family, title, authors)
        if clean_title == title or not clean_title:
            continue
        component = _election_preserved_component(family, title)
        if (
            family is WorkTitleTransformationFamily.SUBTITLE_SEGMENT
            and component is None
        ):
            continue
        matches.append(WorkTitleElectionMatch(family, clean_title, component))
    return tuple(matches)


def _election_preserved_component(
    family: WorkTitleTransformationFamily, title: str
) -> str | None:
    if family in {
        WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER,
        WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
        WorkTitleTransformationFamily.PARENTHESIZED_YEAR,
    }:
        match = _PAREN.search(title)
        return None if match is None else _clean(match.group(1)) or None
    if family is WorkTitleTransformationFamily.SUBTITLE_SEGMENT:
        # Election deliberately excludes the query ladder's dash splitters.
        if title.count(":") != 1:
            return None
        prefix, suffix = title.split(":", 1)
        return _clean(suffix) if _clean(prefix) and _clean(suffix) else None
    return None


def _election_isbn_component(title: str) -> tuple[str, str] | None:
    bracketed = _ELECTION_BRACKETED_ISBN.fullmatch(title)
    if bracketed is not None:
        inner = bracketed.group("square")
        identifier = _IDENTIFIER.fullmatch(
            (inner if inner is not None else bracketed.group("round")).strip()
        )
        if identifier is None or identifier.group("label").casefold() == "asin":
            return None
        compact = re.sub(r"[^0-9X]", "", identifier.group("value").upper())
        prefix = bracketed.group("title")
        # One step only: a title carrying another identifier, bracketed or
        # bare, stays competing (ADR-0067).
        if _IDENTIFIER.search(prefix) is not None:
            return None
        clean_title = _clean(prefix)
        return (
            (clean_title, compact)
            if _valid_isbn(compact) and clean_title and clean_title != title
            else None
        )
    match = re.fullmatch(
        r"(?P<title>.+?)[\\t\\n\\r\\f\\v ]+"
        r"(?P<label>isbn(?:-1[03])?)[\\t\\n\\r\\f\\v ]*[:#-]?"
        r"[\\t\\n\\r\\f\\v ]*(?P<value>[0-9Xx][0-9Xx -]{7,20})"
        r"[\\t\\n\\r\\f\\v ]*",
        title,
        re.IGNORECASE,
    )
    if match is None:
        return None
    compact = re.sub(r"[^0-9X]", "", match.group("value").upper())
    clean_title = apply_work_title_transformation(
        WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER, title, ()
    )
    return (clean_title, compact) if _valid_isbn(compact) and clean_title else None


def _filename_author_title_prefix_query(
    value: str, authors: tuple[str, ...]
) -> str | None:
    """Return the query form of a complete first-author filename prefix.

    The closed separator set lives in the remainder helper alone, so the query
    shaping and the compound admission can never read different grammars.
    """
    remainder = _filename_author_title_prefix_remainder(value, authors)
    if remainder is None:
        return None
    title = _clean(remainder.replace("_", " "))
    return title if _comparison_text(title) else None


def _filename_author_title_prefix_remainder(
    value: str, authors: tuple[str, ...]
) -> str | None:
    """Return the raw remainder after a complete first-author filename prefix."""
    if not authors:
        return None
    normalized = unicodedata.normalize("NFC", value)
    match = re.fullmatch(
        r"(?P<prefix>.+?)(?:[ _]+-[ _]+|[ _]*[–—][ _]*|[ _]*:[ _]*)(?P<title>.+)",
        normalized,
    )
    if match is None:
        return None
    if _identity_tokens(match.group("prefix")) != _identity_tokens(authors[0]):
        return None
    return match.group("title")


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return " ".join(normalized.split())


def _normalized_query_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    translated = _balanced_typographic_preserving_case(normalized)
    return _clean(translated if translated is not None else normalized)


def _clean(value: str) -> str:
    return _ASCII_SPACE.sub(" ", value).strip(" -,:;")


def _closed_equal(left: str, right: str) -> bool:
    if left == right:
        return True
    if _comparison_text(left) == _comparison_text(right):
        return True
    left_typographic = _balanced_typographic(left)
    right_typographic = _balanced_typographic(right)
    return (
        left_typographic is not None
        and right_typographic is not None
        and left_typographic == right_typographic
    )


def _closed_author_multiset_equal(
    local_authors: tuple[str, ...], provider_authors: tuple[str, ...]
) -> bool:
    """Match each author once through the closed lexical equality relation."""
    unmatched = list(provider_authors)
    for local in local_authors:
        for index, provider in enumerate(unmatched):
            if _closed_equal(local, provider):
                unmatched.pop(index)
                break
        else:
            return False
    return not unmatched


_MIDDLE_INITIAL = re.compile(r"([A-Za-z])\.?")


def _creator_display_parts(value: str) -> tuple[str, str | None, str] | None:
    parts = tuple(part for part in _ASCII_SPACE.split(value.strip()) if part)
    if len(parts) not in {2, 3}:
        return None
    given, family = parts[0], parts[-1]
    normalized_given = unicodedata.normalize("NFC", given)
    normalized_family = unicodedata.normalize("NFC", family)
    if (
        len(normalized_given) < 2
        or not normalized_given.isalpha()
        or len(normalized_family) < 2
        or not normalized_family.isalpha()
    ):
        return None
    if len(parts) == 2:
        return given, None, family
    middle = _MIDDLE_INITIAL.fullmatch(parts[1])
    return None if middle is None else (given, middle.group(1).casefold(), family)


def _creator_display_pair_possible(left: str, right: str) -> bool:
    if _closed_equal(left, right):
        return True
    left_parts = _creator_display_parts(left)
    right_parts = _creator_display_parts(right)
    if left_parts is None or right_parts is None:
        return False
    left_given, left_middle, left_family = left_parts
    right_given, right_middle, right_family = right_parts
    return (
        _closed_equal(left_given, right_given)
        and _closed_equal(left_family, right_family)
        and (left_middle is None or right_middle is None or left_middle == right_middle)
    )


def closed_creator_display_possibility(
    left_creators: tuple[str, ...], right_creators: tuple[str, ...]
) -> bool:
    """Find a complete negative-only match across closed creator displays."""
    if (
        type(left_creators) is not tuple
        or type(right_creators) is not tuple
        or not left_creators
        or len(left_creators) != len(right_creators)
        or any(
            type(value) is not str or not value
            for value in (*left_creators, *right_creators)
        )
        or len({_comparison_text(value) for value in left_creators})
        != len(left_creators)
        or len({_comparison_text(value) for value in right_creators})
        != len(right_creators)
    ):
        return False

    matched_left: dict[int, int] = {}

    def assign(right_index: int, seen_left: set[int]) -> bool:
        for left_index, left in enumerate(left_creators):
            if left_index in seen_left or not _creator_display_pair_possible(
                left, right_creators[right_index]
            ):
                continue
            seen_left.add(left_index)
            previous_right = matched_left.get(left_index)
            if previous_right is None or assign(previous_right, seen_left):
                matched_left[left_index] = right_index
                return True
        return False

    return all(assign(index, set()) for index in range(len(right_creators)))


def _balanced_typographic(value: str) -> str | None:
    translated = _balanced_typographic_preserving_case(value, admission=True)
    return None if translated is None else _comparison_text(translated)


def _balanced_typographic_preserving_case(
    value: str, *, admission: bool = False
) -> str | None:
    pairs = {"‘": ("'", True), "’": ("'", False), "“": ('"', True), "”": ('"', False)}
    stack: list[str] = []
    result: list[str] = []
    translation = (
        _ADMISSION_TYPOGRAPHIC_TRANSLATION if admission else _TYPOGRAPHIC_TRANSLATION
    )
    if admission:
        value = fold_lone_apostrophes(value)
    for character in value:
        paired = pairs.get(character)
        if paired is None:
            result.append(character.translate(translation))
            continue
        mark, opening = paired
        if opening:
            stack.append(mark)
        elif not stack or stack.pop() != mark:
            return None
        result.append(mark)
    return None if stack else "".join(result)


# A typographic apostrophe with a letter on each side: "Journey’s" (#1307).
_LONE_APOSTROPHE = re.compile(r"(?<=[^\W\d_])\u2019(?=[^\W\d_])")


def fold_lone_apostrophes(value: str) -> str:
    """Fold each right single quotation mark between two letters to U+0027.

    ADR-0018 step 4 as amended by #1307: between letters the mark is an
    apostrophe, never a closing quotation mark, so it needs no partner. A mark
    anywhere else still pairs, and a lone or reversed one still disagrees.
    """
    return _LONE_APOSTROPHE.sub("'", value)


def _identity_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", value).replace("_", " ")
    balanced = _balanced_typographic_preserving_case(normalized, admission=True)
    return tuple(
        token.casefold()
        for token in _ASCII_SPACE.split((balanced or normalized).strip())
        if token
    )


def _punctuation_fold(value: str) -> str:
    folded = re.sub(r"\b(?:and|&)\b", " and ", value, flags=re.IGNORECASE)
    folded = folded.replace("&", " and ")
    return _clean(
        _PUNCTUATION.sub(
            lambda match: (
                match.group(0)
                if unicodedata.category(match.group(0)).startswith("M")
                else " "
            ),
            folded,
        )
    )


def _latin_combining_marks_fold(value: str) -> str | None:
    """Return the one closed Latin-mark fold, or no application.

    NFD exposes only canonical marks. A mark is removable only in the cluster
    following a Latin-named base letter; orphan marks and non-Latin letters make
    that field inapplicable. Compatibility decomposition and substitutions are
    deliberately absent.
    """
    decomposed = unicodedata.normalize("NFD", value)
    result: list[str] = []
    latin_cluster = False
    for character in decomposed:
        if unicodedata.category(character).startswith("L"):
            if not unicodedata.name(character, "").startswith("LATIN "):
                return None
            latin_cluster = True
            result.append(character)
        elif unicodedata.category(character).startswith("M"):
            if not latin_cluster:
                return None
        else:
            latin_cluster = False
            result.append(character)
    folded = unicodedata.normalize("NFC", "".join(result))
    return folded


def _latin_admission_lineage(
    local_title: str,
    local_authors: tuple[str, ...],
    provider_title: str,
    provider_authors: tuple[str, ...],
) -> WorkTitleTransformationLineage | None:
    applications: list[WorkTitleTransformationApplication] = []

    def fold(
        local: str,
        provider: str,
        field: WorkTitleTransformationField,
        ordinal: int | None,
    ) -> bool:
        local_folded = _latin_combining_marks_fold(local)
        provider_folded = _latin_combining_marks_fold(provider)
        if local_folded is None or provider_folded is None:
            return False
        if not _closed_equal(local_folded, provider_folded):
            return False
        if local_folded != local:
            applications.append(
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
                    WorkTitleTransformationSide.LOCAL,
                    field,
                    ordinal,
                )
            )
        if provider_folded != provider:
            applications.append(
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
                    WorkTitleTransformationSide.PROVIDER,
                    field,
                    ordinal,
                )
            )
        return True

    if not fold(local_title, provider_title, WorkTitleTransformationField.TITLE, None):
        return None
    if not all(
        fold(local, provider, WorkTitleTransformationField.AUTHORS, ordinal)
        for ordinal, (local, provider) in enumerate(
            zip(local_authors, provider_authors, strict=True)
        )
    ):
        return None
    return (
        WorkTitleTransformationLineage(
            tuple(sorted(applications, key=_application_ordinal))
        )
        if applications
        else None
    )


# Words that make a bracketed decoration a volume, part or collection of a
# work rather than its series name (#1499).
_EBOOK_PART_WORDS = frozenset(
    {
        "book",
        "box",
        "collected",
        "collection",
        "companion",
        "complete",
        "guide",
        "omnibus",
        "part",
        "set",
        "summary",
        "trilogy",
        "vol",
        "volume",
        "workbook",
    }
)


def _ebook_series_decoration(
    local_title: str,
    memberships: tuple[tuple[str, str | None], ...],
    names: tuple[str, ...] = (),
) -> bool:
    """Whether an ebook's bracketed decoration reads as its series name (#1499).

    A product that states its series must state this one, as the band path
    requires. A product that states none admits only a decoration that names
    no volume, part or collection and does not end in a number: "(Volume 2)",
    "(Part Two)" and "(Box Set)" are another work than the bare title.
    """
    if memberships:
        return _band_decoration_corroborated(
            WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
            local_title,
            memberships,
        )
    paren = _PAREN.search(_normalized_query_text(local_title))
    inside = "" if paren is None else _clean(paren.group(1))
    position = _PAREN_SERIES_POSITION.fullmatch(inside)
    name = inside if position is None else _clean(position.group("series"))
    if names:
        # A series stated without a position must still be this one.
        return bool(name) and any(_closed_equal(stated, name) for stated in names)
    tokens = _PUNCTUATION.sub(" ", name).casefold().split()
    return bool(tokens) and not (
        any(token in _EBOOK_PART_WORDS for token in tokens)
        or (position is None and tokens[-1].isdigit())
        or (position is None and _ROMAN_NUMERAL.fullmatch(tokens[-1]) is not None)
        or (position is None and tokens[-1] in _NUMBER_WORDS)
    )


_NUMBER_WORDS = frozenset(
    {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
)


def _band_decoration_corroborated(
    family: WorkTitleTransformationFamily,
    local_title: str,
    memberships: tuple[tuple[str, str | None], ...],
) -> bool:
    """Whether the product's own series states what the rung stripped (#1260).

    One membership must state both halves: its name closed-equals the stripped
    series name, and where the rung stripped a position (an inverted prefix,
    or a parenthetical ending in one) its position equals that position as a
    decimal number.
    """
    title = _normalized_query_text(local_title)
    match: re.Match[str] | None
    if family is WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME:
        paren = _PAREN.search(title)
        inside = "" if paren is None else _clean(paren.group(1))
        match = _PAREN_SERIES_POSITION.fullmatch(inside)
        if match is None:
            name, position = inside, None
    else:
        match = _INVERTED_SERIES.fullmatch(title)
        if match is None:
            return False
    if match is not None:
        name = _clean(match.group("series"))
        position = Decimal(match.group("sequence"))
    return bool(name) and any(
        _closed_equal(stated_name, name)
        and (
            position is None
            or stated_sequence is not None
            and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", stated_sequence.strip())
            is not None
            and Decimal(stated_sequence.strip()) == position
        )
        for stated_name, stated_sequence in memberships
    )


def _initials_fold(value: str) -> str:
    """Fold each token of dotted single letters to the letters, space-separated.

    Tokens split on the whitespace `_closed_equal` collapses, no-break and
    thin spaces included. "J.R.R." and "J. R. R." both become "j r r"; "JRR"
    and "St." are unchanged but for case.
    """
    return " ".join(
        " ".join(token[::2]) if _DOTTED_INITIALS.fullmatch(token) else token
        for token in _comparison_text(value).split(" ")
        if token
    )


def _initials_fold_lineage(
    local_authors: tuple[str, ...], provider_authors: tuple[str, ...]
) -> WorkTitleTransformationLineage | None:
    """Admit ordered authors equal once dotted initials fold (#1260).

    Every pair is closed-equal, or closed-equal once folded with a folded name
    that keeps a token longer than one letter; at least one pair needs the fold.
    """
    applications: list[WorkTitleTransformationApplication] = []
    for ordinal, (local, provider) in enumerate(
        zip(local_authors, provider_authors, strict=True)
    ):
        if _closed_equal(local, provider):
            continue
        local_folded, provider_folded = _initials_fold(local), _initials_fold(provider)
        if not _closed_equal(local_folded, provider_folded) or all(
            len(token) == 1 for token in local_folded.split(" ")
        ):
            return None
        applications.extend(
            WorkTitleTransformationApplication(
                WorkTitleTransformationFamily.INITIALS_FOLD,
                side,
                WorkTitleTransformationField.AUTHORS,
                ordinal,
            )
            for side, original, folded in (
                (WorkTitleTransformationSide.LOCAL, local, local_folded),
                (WorkTitleTransformationSide.PROVIDER, provider, provider_folded),
            )
            if _comparison_text(folded) != _comparison_text(original)
        )
    if not applications:
        return None
    return WorkTitleTransformationLineage(
        tuple(sorted(applications, key=_application_ordinal))
    )


def _remove_valid_identifier(match: re.Match[str]) -> str:
    label = match.group("label").casefold()
    compact = re.sub(r"[^0-9A-Z]", "", match.group("value").upper())
    if label == "asin":
        valid = bool(re.fullmatch(r"B[0-9A-Z]{9}", compact))
    else:
        valid = _valid_isbn(compact)
    return " " if valid else match.group(0)


def _remove_bracketed_identifier(match: re.Match[str]) -> str:
    inner = match.group("square")
    identifier = _IDENTIFIER.fullmatch(
        inner if inner is not None else match.group("round")
    )
    return (
        " "
        if identifier is not None and _remove_valid_identifier(identifier) == " "
        else match.group(0)
    )


def _valid_isbn(value: str) -> bool:
    if re.fullmatch(r"\d{13}", value):
        return (
            sum(
                (1 if index % 2 == 0 else 3) * int(char)
                for index, char in enumerate(value)
            )
            % 10
            == 0
        )
    if re.fullmatch(r"\d{9}[\dX]", value):
        return (
            sum(
                (10 - index) * (10 if char == "X" else int(char))
                for index, char in enumerate(value)
            )
            % 11
            == 0
        )
    return False


def _year(value: str) -> bool:
    return bool(re.fullmatch(r"(?:1[5-9]\d{2}|20\d{2}|2100)", value))


__all__ = [
    "BAND_TITLE_PRODUCTIONS",
    "CORROBORATED_TITLE_APPLICATION",
    "CORROBORATED_TITLE_PRODUCTION",
    "WORK_TITLE_COMPOUND_PRODUCTIONS",
    "WORK_TITLE_TRANSFORMATION_RULES",
    "WorkDispatchLineage",
    "WorkDispatchRung",
    "WorkIdentityAdmission",
    "WorkQuery",
    "WorkTitleCompoundProduction",
    "WorkTitleTransformationFamily",
    "WorkTitleTransformationApplication",
    "WorkTitleTransformationField",
    "WorkTitleElectionMatch",
    "WorkTitleTransformationLineage",
    "WorkTitleTransformationRule",
    "WorkTitleTransformationSide",
    "apply_work_title_transformation",
    "admit_provider_work_identity",
    "closed_creator_display_possibility",
    "direct_work_title_election_matches",
    "discovery_title_candidate",
    "fold_lone_apostrophes",
    "narrowing_work_query",
    "replay_provider_work_identity_admission",
    "series_label_segment",
    "work_dispatch_lineage_from_data",
    "work_query_ladder",
    "work_title_has_variant_marker",
    "work_title_extends",
    "work_title_related",
    "work_title_election_matches",
    "work_title_transformation_lineage_from_data",
]
