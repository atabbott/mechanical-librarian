"""Closed work-query shaping and exact work-identity admission."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.validation import exact_type, nonempty_text


class WorkDispatchRung(StrEnum):
    NORMALIZED = "normalized"
    SUBTITLE_STRIPPED = "subtitle_stripped"
    PUNCTUATION_FOLDED = "punctuation_folded"


class WorkTitleTransformationFamily(StrEnum):
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
    SUBTITLE_SEGMENT = "subtitle_segment"
    PUNCTUATION_AMPERSAND_FOLD = "punctuation_ampersand_fold"
    LATIN_COMBINING_MARKS = "latin_combining_marks"


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
            type(self.family) is not WorkTitleTransformationFamily
            or type(self.side) is not WorkTitleTransformationSide
            or type(self.field) is not WorkTitleTransformationField
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

    def __post_init__(self) -> None:
        if (
            type(self.family) is not WorkTitleTransformationFamily
            or type(self.query_enabled) is not bool
            or type(self.admission_enabled) is not bool
            or type(self.rung) is not WorkDispatchRung
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

WORK_TITLE_COMPOUND_PRODUCTIONS = (
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


def _lineage_production(
    applications: tuple[WorkTitleTransformationApplication, ...],
) -> str | None:
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
    return None


if len({production.name for production in WORK_TITLE_COMPOUND_PRODUCTIONS}) != len(
    WORK_TITLE_COMPOUND_PRODUCTIONS
):
    raise RuntimeError("work title compound production table is ambiguous")

_ASCII_SPACE = re.compile(r"[\t\n\r\f\v ]+")
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
_SERIES_MARKER = re.compile(
    r"[\t\n\r\f\v ]*(?:[-,:][\t\n\r\f\v ]*)?"
    r"(?:book|volume|vol\.?|part)[\t\n\r\f\v ]*#?[\t\n\r\f\v ]*"
    r"(?:\d+(?:\.\d+)?|[ivxlcdm]+)[\t\n\r\f\v ]*$",
    re.IGNORECASE,
)
_INVERTED_SERIES = re.compile(
    r"^.+?\b(?:book|volume|vol\.?|part|#)[\t\n\r\f\v ]*\d+(?:\.\d+)?"
    r"[\t\n\r\f\v ]+[-–—][\t\n\r\f\v ]+(.+)$",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(
    r"\b(?P<label>isbn(?:-1[03])?|asin)[\t\n\r\f\v ]*[:#-]?[\t\n\r\f\v ]*"
    r"(?P<value>[0-9A-Z][0-9A-Z -]{7,20})\b",
    re.IGNORECASE,
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


def work_query_ladder(title: str, authors: tuple[str, ...]) -> tuple[WorkQuery, ...]:
    """Return at most three distinct, deterministic WORK dispatches."""
    if (
        type(title) is not str
        or type(authors) is not tuple
        or not authors
        or any(type(value) is not str for value in authors)
    ):
        raise ValueError("work query input is invalid")
    shaped_title = _normalized_query_text(title)
    shaped_authors = tuple(_normalized_query_text(value) for value in authors)
    applied: list[WorkTitleTransformationFamily] = []
    for rule in WORK_TITLE_TRANSFORMATION_RULES:
        if rule.rung is not WorkDispatchRung.NORMALIZED or not rule.query_enabled:
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
    if latin_title is not None or any(value is not None for value in latin_authors):
        folded_title = latin_title or folded_title
        folded_authors = tuple(
            folded if latin is None else latin
            for folded, latin in zip(folded_authors, latin_authors, strict=True)
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
    if tuple(sorted(transformations, key=_RULE_ORDINAL.__getitem__)) != transformations:
        return False
    if rung is WorkDispatchRung.NORMALIZED:
        return all(
            _RULES_BY_FAMILY[family].rung is WorkDispatchRung.NORMALIZED
            for family in transformations
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
) -> WorkIdentityAdmission:
    """Apply the one closed work-title/author identity decision."""
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
        or variant_excluded
        or work_title_has_variant_marker(local_title)
        or work_title_has_variant_marker(provider_title)
    ):
        return WorkIdentityAdmission(False)
    if _closed_equal(local_title, provider_title) and all(
        _closed_equal(local, provider)
        for local, provider in zip(local_authors, provider_authors, strict=True)
    ):
        return WorkIdentityAdmission(True)
    latin = _latin_admission_lineage(
        local_title, local_authors, provider_title, provider_authors
    )
    if latin is not None:
        return WorkIdentityAdmission(True, latin)
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
) -> WorkIdentityAdmission:
    """Recompute admission only for a dispatch the exact local ladder can emit."""
    admission = admit_provider_work_identity(
        local_title=local_title,
        local_authors=local_authors,
        provider_title=provider_title,
        provider_authors=provider_authors,
        provider_work_identity=provider_work_identity,
        variant_excluded=variant_excluded,
    )
    if not admission.admitted or dispatch_lineage is None:
        return WorkIdentityAdmission(False)
    queries = work_query_ladder(local_title, local_authors)
    permitted = {WorkDispatchLineage.from_query(query) for query in queries} | {
        WorkDispatchLineage.from_query(narrowing_work_query(query)) for query in queries
    }
    return admission if dispatch_lineage in permitted else WorkIdentityAdmission(False)


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
        return _clean(_IDENTIFIER.sub(_remove_valid_identifier, title))
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
        return _clean(match.group(1)) if match is not None else title
    if family is WorkTitleTransformationFamily.SUBTITLE_SEGMENT:
        if ":" in title:
            return _clean(title.split(":", 1)[0])
        for separator in (" - ", " – ", " — "):
            if separator in title:
                return _clean(title.split(separator, 1)[0])
        return title
    if family is WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD:
        return _punctuation_fold(title)
    raise RuntimeError("work title transformation table is incomplete")


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
    return _clean(_PUNCTUATION.sub(" ", folded))


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
    removed = False
    for character in decomposed:
        if unicodedata.category(character).startswith("L"):
            if not unicodedata.name(character, "").startswith("LATIN "):
                return None
            latin_cluster = True
            result.append(character)
        elif unicodedata.category(character).startswith("M"):
            if not latin_cluster:
                return None
            removed = True
        else:
            latin_cluster = False
            result.append(character)
    return unicodedata.normalize("NFC", "".join(result)) if removed else None


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
        if not _closed_equal(local_folded or local, provider_folded or provider):
            return False
        if local_folded is not None:
            applications.append(
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
                    WorkTitleTransformationSide.LOCAL,
                    field,
                    ordinal,
                )
            )
        if provider_folded is not None:
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


def _remove_valid_identifier(match: re.Match[str]) -> str:
    label = match.group("label").casefold()
    compact = re.sub(r"[^0-9A-Z]", "", match.group("value").upper())
    if label == "asin":
        valid = bool(re.fullmatch(r"B[0-9A-Z]{9}", compact))
    else:
        valid = _valid_isbn(compact)
    return " " if valid else match.group(0)


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
    "direct_work_title_election_matches",
    "narrowing_work_query",
    "replay_provider_work_identity_admission",
    "work_dispatch_lineage_from_data",
    "work_query_ladder",
    "work_title_has_variant_marker",
    "work_title_election_matches",
    "work_title_transformation_lineage_from_data",
]
