"""Aggregate-only work-route agreement measurement over persisted state (#1642).

A workbench instrument, deliberately not part of the installed product: run it
from a checkout against a COPY of an application database written by a fresh
run of the same checkout. It opens that copy read-only, writes nothing and
prints one ``work-route-measurement-v1`` JSON document to stdout.

It measures whether an exact work source (``_supported_work_source``) from
Hardcover or Open Library says what the book is, field by field, so that the
owner can decide whether the work route may fill an empty field without
asking. Each provider is measured on its own and pooled:

- ``recording``: a recording-admitted audiobook with an exact work source from
  the provider. Its series is compared with the owner's current series, or
  else the series the recording path elected; its first-publication year with
  the owner's year, or else the other provider's exact work source.
- ``unmatched``: an audiobook no recording admitted, with an exact work source
  from the provider. It is not compared; it counts the fields the work route
  offers that provider's value for while the field is strictly empty.
- ``duplicate``: a provider naming several different works as exact sources
  for one audiobook. It is counted and excluded.

A recording-admitted audiobook whose exact work source disagrees with the
admitted recording on the title key or the ordered author set, or that the
work-identity contradiction rule calls explicit, is a contradiction. It is not
compared, and it counts against every field of that provider.

The description, genres and narrators are measured from the owner's own
decisions on work-route offers made while the field was strictly empty: the
latest decision per item and field, with a withdrawn one excluded. Accepting
the provider's value agrees; rejecting it, entering another value, keeping the
empty field or accepting another source's value disagrees. The cover is not
measured: no identity basis compares a cover yet.

Every field reports compared, agree, disagree, not_comparable (with closed
reasons), contradictions, the point agreement and a 95% Wilson interval for
information. Pooled figures count items: an item is compared when either
provider was, and agrees only when every compared provider agrees. A series
position agrees when the source states none, or when both state the same
number; a source position the reference lacks is not comparable. An owner
series or year this store records the Librarian writing is no reference. Each mark ``N:P:C`` passes when at least N were compared, the
point agreement is at least P and there are at most C contradictions. The
output holds providers, field names, closed reason codes and counts only:
never a title, author, value, path, name or identifier.

Usage:
    uv run python scripts/work_route_measurement.py \
        --database /copy/application.sqlite3 \
        [--mark 15:0.9 ...] [--provenance]

``--provenance`` prints instead the value-free fragment a future
``current_audiobook_work_route_measurement`` provenance section records.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
    recording_identity_basis,
    runtime_corroborated_candidate,
)
from mechanical_librarian.domain.epub_inspection import DateEvent
from mechanical_librarian.domain.field_value_comparison import (
    canonical_text,
    title_article_key,
    values_match,
)
from mechanical_librarian.domain.metadata_report import (
    AUDIOBOOK_PROVIDER_AUTHORITIES,
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderCandidate,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFieldState,
    TargetSeries,
    WorkIdentityContradiction,
    _provider_candidate_work_identity_claims,
    provider_candidate_work_identity_contradiction,
    recording_variant_conflict,
)
from mechanical_librarian.domain.proposal_set import (
    _series_from_claims,
    _supported_work_source,
    _target_explicitly_empty,
    _work_route_offers,
    canonical_target_fields,
    work_route_cover_sources,
)
from mechanical_librarian.domain.reviewer_decision_summary import correlation_identity

WORK_ROUTE_MEASUREMENT_SCHEMA = "work-route-measurement-v1"
PROVENANCE_SECTION = "current_audiobook_work_route_measurement"
PROVIDERS = ("hardcover", "open_library")
RECORDING_FIELDS = ("series", "publishedDate")
OWNER_LABELLED_FIELDS = ("description", "genres", "narrators")
FILL_FIELDS = (*RECORDING_FIELDS, *OWNER_LABELLED_FIELDS, "cover")
_Z_95 = 1.959963984540054

_REPORTS_SQL = (
    "SELECT mr.document,ps.document FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "LEFT JOIN proposal_sets ps ON ps.proposal_set_id=ml.proposal_set_id "
    "WHERE json_extract(mr.document,'$.audio') IS NOT NULL "
    "ORDER BY ml.connection_name,ml.library_name,ml.item_id"
)
# Every owner decision on an owner-labelled field, oldest first, with the
# report and proposals its case was decided on.
_DECISIONS_SQL = (
    "SELECT mr.connection_name,mr.library_name,mr.item_id,hd.destination,"
    "hd.outcome,hd.selected_value_document,mr.document,ps.document "
    "FROM human_decisions hd "
    "JOIN review_cases rc ON rc.review_case_id=hd.review_case_id "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
    "JOIN proposal_sets ps ON ps.proposal_set_id=rc.proposal_set_id "
    "WHERE hd.destination IN ("
    + ",".join(f"'{name}'" for name in OWNER_LABELLED_FIELDS)
    + ") ORDER BY hd.ordinal"
)
# The fields this store records the Librarian writing, or still writing: a
# current value it wrote is no independent reference.
_WRITTEN_SQL = (
    "SELECT DISTINCT connection_name,library_name,work_id,destination "
    "FROM field_effects WHERE state IN ('verified','applying','reconciling') "
    "AND destination IN ('series','publishedDate','publishedYear')"
)
_POSITION = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


@dataclass(frozen=True, slots=True)
class Mark:
    """One pass mark: enough compared, enough agreement, few contradictions."""

    compared: int
    agreement: Fraction
    contradictions: int = 0

    def to_data(self, passed: bool | None = None) -> dict[str, object]:
        data: dict[str, object] = {
            "min_compared": self.compared,
            "min_agreement": float(self.agreement),
            "max_contradictions": self.contradictions,
        }
        if passed is not None:
            data["pass"] = passed
        return data


DEFAULT_MARKS = (Mark(20, Fraction(95, 100)), Mark(10, Fraction(90, 100)))


@dataclass(slots=True)
class Tally:
    """One field's comparisons for one provider, as closed outcomes only."""

    agree: int = 0
    disagree: int = 0
    not_comparable: Counter[str] = field(default_factory=Counter)
    references: Counter[str] = field(default_factory=Counter)
    position_absent: int = 0


@dataclass(frozen=True, slots=True)
class Outcome:
    """One provider's closed outcome for one field of one item.

    `detail` is the reference a comparison used, or why none was possible.
    """

    result: str
    detail: str
    position_absent: bool = False


def _not_comparable(reason: str) -> Outcome:
    return Outcome("not_comparable", reason)


def _record(
    provider_tallies: dict[str, Tally], pooled: Tally, outcomes: dict[str, Outcome]
) -> None:
    """Count one item per provider, and once in the pooled tally.

    Pooled counts items, not provider rows: an item is compared when any
    provider was, and agrees only when every compared provider agrees.
    """
    for authority, outcome in outcomes.items():
        tally = provider_tallies[authority]
        if outcome.result == "not_comparable":
            tally.not_comparable[outcome.detail] += 1
            continue
        tally.references[outcome.detail] += 1
        if outcome.result == "agree":
            tally.agree += 1
            tally.position_absent += outcome.position_absent
        else:
            tally.disagree += 1
    compared = [
        value for value in outcomes.values() if value.result != "not_comparable"
    ]
    if not compared:
        pooled.not_comparable.update({value.detail for value in outcomes.values()})
        return
    pooled.references.update({value.detail for value in compared})
    if all(value.result == "agree" for value in compared):
        pooled.agree += 1
        pooled.position_absent += any(value.position_absent for value in compared)
    else:
        pooled.disagree += 1


def _open_read_only(path: Path) -> sqlite3.Connection:
    absolute = str(path.absolute())
    return sqlite3.connect(f"file:{quote(absolute, safe='/')}?mode=ro", uri=True)


def _authority_sources(
    report: MetadataReport, authority: str
) -> tuple[ProviderCandidate, ...]:
    return tuple(
        candidate
        for provider in report.providers
        if provider.authority == authority
        for candidate in provider.candidates
        if _supported_work_source(report, provider, candidate)
    )


def _admitted_recordings(report: MetadataReport) -> tuple[ProviderCandidate, ...]:
    """The candidates that admitted this recording, by stated runtime or chapters."""
    title = report.target.current_title or ""
    chosen: list[ProviderCandidate] = []
    for provider in report.providers:
        if (
            provider.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES
            or provider.status is not ProviderStatus.CANDIDATES
        ):
            continue
        selected = runtime_corroborated_candidate(
            tuple(
                candidate
                for candidate in provider.candidates
                if recording_variant_conflict(
                    candidate, report.local_claims, title, report.library_languages
                )
                is None
            ),
            report.local_claims,
        )
        if selected is not None:
            chosen.append(selected[0])
    basis = recording_identity_basis(report)
    if basis is not None:
        chosen.extend(
            candidate
            for provider in report.providers
            if provider.provider == basis["provider"]
            for candidate in (
                *provider.candidates,
                *provider.recording_lookup_candidates,
            )
            if candidate.identity == basis["candidate"]
        )
    return tuple(chosen)


def _authors_key(authors: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(canonical_text(TargetDestination.AUTHORS, value) for value in authors)


def _contradicted(
    sources: tuple[ProviderCandidate, ...],
    recordings: tuple[ProviderCandidate, ...],
    target: tuple[str, tuple[str, ...]] | None,
) -> bool:
    """Whether an exact work source disagrees with the admitted recording."""
    identities = [
        (claims[0], claims[1])
        for claims in map(_provider_candidate_work_identity_claims, recordings)
        if claims is not None
    ]
    if not identities and target is not None:
        identities = [target]
    for source in sources:
        claims = _provider_candidate_work_identity_claims(source)
        for title, authors in identities:
            if (
                claims is not None
                and (
                    title_article_key(claims[0]) != title_article_key(title)
                    or _authors_key(claims[1]) != _authors_key(authors)
                )
            ) or provider_candidate_work_identity_contradiction(
                source, title, authors
            ) is WorkIdentityContradiction.EXPLICIT:
                return True
    return False


def _source_series(
    sources: tuple[ProviderCandidate, ...],
) -> set[tuple[tuple[str, str | None], ...]]:
    values: set[tuple[tuple[str, str | None], ...]] = set()
    for candidate in sources:
        observed = _series_from_claims(
            tuple(
                claim
                for claim in candidate.claims
                if claim.scope is ClaimScope.WORK
                and claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
            )
        )
        if observed is not None:
            values.add(tuple((value.name, value.sequence) for value in observed))
    return values


def _year(value: object) -> str | None:
    if type(value) is str and len(value) >= 4 and value[:4].isdigit():
        return value[:4]
    return None


def _source_years(sources: tuple[ProviderCandidate, ...]) -> set[str]:
    return {
        year
        for candidate in sources
        for claim in candidate.claims
        if claim.kind is ClaimKind.PUBLICATION_DATE
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
        and claim.date_event is DateEvent.WORK_FIRST_PUBLICATION
        for year in (_year(claim.value),)
        if year is not None
    }


def _proposal_result(document: object, destination: str) -> dict[str, object] | None:
    if not isinstance(document, dict):
        return None
    results = cast("dict[str, object]", document).get("results")
    if not isinstance(results, list):
        return None
    for result in cast("list[object]", results):
        if (
            isinstance(result, dict)
            and cast("dict[str, object]", result).get("destination") == destination
        ):
            return cast("dict[str, object]", result)
    return None


def _alternatives(result: dict[str, object] | None) -> list[dict[str, object]]:
    alternatives = None if result is None else result.get("alternatives")
    if not isinstance(alternatives, list):
        return []
    return [
        cast("dict[str, object]", value)
        for value in cast("list[object]", alternatives)
        if isinstance(value, dict)
    ]


def _correlations(alternative: dict[str, object]) -> frozenset[str]:
    values = alternative.get("correlations")
    if not isinstance(values, list):
        return frozenset()
    return frozenset(
        value for value in cast("list[object]", values) if isinstance(value, str)
    )


def _provider_correlation(authority: str) -> str:
    return correlation_identity("provider:" + authority)


def _elected_series(
    proposal: object,
) -> tuple[tuple[tuple[str, str | None], ...], frozenset[str]] | None:
    """The series the recording path elected, with who voted for it."""
    result = _proposal_result(proposal, TargetDestination.SERIES.value)
    if result is None or not isinstance(result.get("selected_alternative_id"), str):
        return None
    for alternative in _alternatives(result):
        if alternative.get("alternative_id") != result["selected_alternative_id"]:
            continue
        value = alternative.get("value")
        members = (
            cast("dict[str, object]", value).get("value")
            if isinstance(value, dict)
            else None
        )
        if not isinstance(members, list) or not members:
            return None
        series: list[tuple[str, str | None]] = []
        for member in cast("list[object]", members):
            if not isinstance(member, dict):
                return None
            record = cast("dict[str, object]", member)
            name, sequence = record.get("name"), record.get("sequence")
            if type(name) is not str or not (sequence is None or type(sequence) is str):
                return None
            series.append((name, sequence))
        return tuple(series), _correlations(alternative)
    return None


def _owner_series(
    report: MetadataReport,
) -> tuple[tuple[str, str | None], ...] | None:
    current = canonical_target_fields(report.target.fields)[TargetDestination.SERIES]
    if (
        current.state is not TargetFieldState.VALUE
        or type(current.value) is not tuple
        or not current.value
        or any(type(value) is not TargetSeries for value in current.value)
    ):
        return None
    return tuple(
        (value.name, value.sequence)
        for value in cast("tuple[TargetSeries, ...]", current.value)
    )


def _positions_equal(left: str, right: str) -> bool:
    """Known positions agree exactly or as unsigned decimals, as the product rules."""
    return left == right or (
        _POSITION.fullmatch(left) is not None
        and _POSITION.fullmatch(right) is not None
        and Decimal(left) == Decimal(right)
    )


def _compare_series(
    source: tuple[tuple[str, str | None], ...],
    reference: tuple[tuple[str, str | None], ...],
    detail: str,
) -> Outcome:
    """Names compare as values do; absent numbers stay absent.

    A source with no position agrees and says so; a source position the
    reference lacks cannot be checked; known positions must be equal.
    """
    if not values_match(
        TargetDestination.SERIES,
        tuple(TargetSeries(name) for name, _ in reference),
        tuple(TargetSeries(name) for name, _ in source),
    ):
        return Outcome("disagree", detail)
    absent = unverifiable = False
    for (_name, left), (_other, right) in zip(source, reference, strict=True):
        if left is None:
            absent = True
        elif right is None:
            unverifiable = True
        elif not _positions_equal(left, right):
            return Outcome("disagree", detail)
    if unverifiable:
        return _not_comparable("reference_position_absent")
    return Outcome("agree", detail, absent)


@dataclass(slots=True)
class Measurement:
    """Every closed outcome the report needs, read from one store."""

    audiobooks: int = 0
    recording_admitted: int = 0
    populations: dict[str, Counter[str]] = field(
        default_factory=lambda: {name: Counter() for name in PROVIDERS}
    )
    fill: dict[str, Counter[str]] = field(
        default_factory=lambda: {name: Counter() for name in PROVIDERS}
    )
    fields: dict[str, dict[str, Tally]] = field(
        default_factory=lambda: {
            name: {provider: Tally() for provider in PROVIDERS}
            for name in (*RECORDING_FIELDS, *OWNER_LABELLED_FIELDS)
        }
    )
    pooled: dict[str, Tally] = field(
        default_factory=lambda: {
            name: Tally() for name in (*RECORDING_FIELDS, *OWNER_LABELLED_FIELDS)
        }
    )
    written: frozenset[tuple[object, ...]] = frozenset()
    cross_provider_year: Tally = field(default_factory=Tally)
    owner_labels: dict[str, Counter[str]] = field(
        default_factory=lambda: {name: Counter() for name in OWNER_LABELLED_FIELDS}
    )


def _measure_recording(
    measurement: Measurement,
    report: MetadataReport,
    proposal: object,
    sources: dict[str, tuple[ProviderCandidate, ...]],
) -> None:
    measurement.recording_admitted += 1
    context = None
    title = report.target.current_title
    authors = canonical_target_fields(report.target.fields)[
        TargetDestination.AUTHORS
    ].value
    if type(title) is str and type(authors) is tuple:
        context = (title, cast("tuple[str, ...]", authors))
    recordings = _admitted_recordings(report)
    compared: dict[str, tuple[ProviderCandidate, ...]] = {}
    for authority, found in sources.items():
        if _contradicted(found, recordings, context):
            measurement.populations[authority]["contradictions"] += 1
        else:
            compared[authority] = found
    years = {authority: _source_years(found) for authority, found in compared.items()}
    if all(len(years.get(authority, ())) == 1 for authority in PROVIDERS):
        cross = measurement.cross_provider_year
        if years["hardcover"] == years["open_library"]:
            cross.agree += 1
        else:
            cross.disagree += 1
    owner_year = _year(
        canonical_target_fields(report.target.fields)[
            TargetDestination.PUBLISHED_DATE
        ].value
    )
    target = report.target
    written = {
        destination
        for connection, library, item, destination in measurement.written
        if (connection, library, item)
        == (target.connection, target.library, target.item)
    }
    owner_series = _owner_series(report)
    elected = _elected_series(proposal)
    series: dict[str, Outcome] = {}
    dates: dict[str, Outcome] = {}
    for authority, found in compared.items():
        stated = _source_series(found)
        if len(stated) != 1:
            series[authority] = _not_comparable(
                "no_source_value" if not stated else "source_values_differ"
            )
        elif owner_series is not None and "series" in written:
            series[authority] = _not_comparable("reference_written_by_librarian")
        elif owner_series is not None:
            series[authority] = _compare_series(
                next(iter(stated)), owner_series, "owner"
            )
        elif elected is None:
            series[authority] = _not_comparable("no_reference")
        elif elected[1] <= {_provider_correlation(authority)}:
            # Only this provider voted for the elected series: no reference.
            series[authority] = _not_comparable("reference_from_same_provider")
        else:
            series[authority] = _compare_series(
                next(iter(stated)), elected[0], "elected"
            )
        own = years[authority]
        others = [
            years[other]
            for other in PROVIDERS
            if other != authority and len(years.get(other, ())) == 1
        ]
        if len(own) != 1:
            dates[authority] = _not_comparable(
                "no_source_value" if not own else "source_values_differ"
            )
        elif owner_year is not None and written & {"publishedDate", "publishedYear"}:
            dates[authority] = _not_comparable("reference_written_by_librarian")
        elif owner_year is not None:
            dates[authority] = Outcome(
                "agree" if next(iter(own)) == owner_year else "disagree", "owner"
            )
        elif others:
            dates[authority] = Outcome(
                "agree" if own == others[0] else "disagree", "other_source"
            )
        else:
            dates[authority] = _not_comparable("no_reference")
    for name, outcomes in (("series", series), ("publishedDate", dates)):
        _record(measurement.fields[name], measurement.pooled[name], outcomes)


def _measure_unmatched(
    measurement: Measurement,
    report: MetadataReport,
    proposal: object,
    sources: dict[str, tuple[ProviderCandidate, ...]],
) -> None:
    targets = canonical_target_fields(report.target.fields)
    offers = _work_route_offers(report, targets)
    covers = work_route_cover_sources(report)
    names = {
        provider.provider: provider.authority
        for provider in report.providers
        if provider.authority is not None
    }
    for authority in sources:
        correlation = _provider_correlation(authority)
        for name in FILL_FIELDS:
            destination = TargetDestination(name)
            if destination not in offers or not _target_explicitly_empty(
                targets[destination]
            ):
                continue
            if destination is TargetDestination.COVER:
                offered = any(
                    source.provenance is not None
                    and names.get(source.provenance.provider) == authority
                    for source in covers
                )
            else:
                offered = any(
                    correlation in _correlations(alternative)
                    for alternative in _alternatives(_proposal_result(proposal, name))
                )
            if offered:
                measurement.fill[authority][name] += 1


def _classify(
    measurement: Measurement, report: MetadataReport, proposal: object
) -> None:
    measurement.audiobooks += 1
    admitted = admitted_audiobook_identity(report)
    population = "recording" if admitted else "unmatched"
    sources: dict[str, tuple[ProviderCandidate, ...]] = {}
    for authority in PROVIDERS:
        found = _authority_sources(report, authority)
        if not found:
            continue
        if len({candidate.work_identity for candidate in found}) > 1:
            measurement.populations[authority]["duplicate_" + population] += 1
            continue
        measurement.populations[authority][population] += 1
        sources[authority] = found
    if admitted:
        _measure_recording(measurement, report, proposal, sources)
    else:
        _measure_unmatched(measurement, report, proposal, sources)


def _qualifies(destination: str, report: MetadataReport) -> bool:
    """Whether a decision was on a work-route offer into a strictly empty field."""
    targets = canonical_target_fields(report.target.fields)
    target = TargetDestination(destination)
    return (
        report.audio is not None
        and not admitted_audiobook_identity(report)
        and target in _work_route_offers(report, targets)
        and _target_explicitly_empty(targets[target])
    )


def _label(
    measurement: Measurement,
    destination: str,
    outcome: str,
    selected: object,
    report: MetadataReport,
    proposal: object,
) -> None:
    """Count one item's latest qualifying owner decision for one field."""
    labels = measurement.owner_labels[destination]
    if outcome == "withdraw":
        labels["withdrawn"] += 1
        return
    alternatives = _alternatives(_proposal_result(proposal, destination))
    accepted = next(
        (
            _correlations(alternative)
            for alternative in alternatives
            if outcome == "accept" and alternative.get("value") == selected
        ),
        frozenset[str](),
    )
    labels["labelled"] += 1
    outcomes: dict[str, Outcome] = {}
    for authority in PROVIDERS:
        correlation = _provider_correlation(authority)
        if not any(correlation in _correlations(value) for value in alternatives):
            continue
        found = _authority_sources(report, authority)
        if not found:
            outcomes[authority] = _not_comparable("no_exact_source")
        elif len({candidate.work_identity for candidate in found}) > 1:
            outcomes[authority] = _not_comparable("duplicate_sources")
        else:
            outcomes[authority] = Outcome(
                "agree" if correlation in accepted else "disagree", "owner"
            )
    _record(measurement.fields[destination], measurement.pooled[destination], outcomes)


def _load_report(document: object) -> MetadataReport:
    if not isinstance(document, str):
        raise ReportValueError("metadata report is unreadable")
    return MetadataReport.from_json(document)


def _load_json(document: object) -> object:
    return json.loads(document) if isinstance(document, str) else None


def measure(connection: sqlite3.Connection) -> Measurement:
    """Read every closed outcome inside one deferred read transaction."""
    connection.execute("BEGIN")
    try:
        measurement = Measurement(
            written=frozenset(tuple(row) for row in connection.execute(_WRITTEN_SQL))
        )
        for report_document, proposal_document in connection.execute(_REPORTS_SQL):
            _classify(
                measurement,
                _load_report(report_document),
                _load_json(proposal_document),
            )
        # The latest decision per item and field is chosen only among those
        # that qualify, so a later decision elsewhere cannot hide a label.
        latest: dict[
            tuple[object, ...], tuple[str, object, MetadataReport, object]
        ] = {}
        for row in connection.execute(_DECISIONS_SQL):
            destination = cast("str", row[3])
            report = _load_report(row[6])
            if not _qualifies(destination, report):
                measurement.owner_labels[destination]["outside_work_route_fill"] += 1
                continue
            latest[tuple(row[:4])] = (
                cast("str", row[4]),
                _load_json(row[5]),
                report,
                _load_json(row[7]),
            )
        for key, (outcome, selected, report, proposal) in latest.items():
            _label(
                measurement, cast("str", key[3]), outcome, selected, report, proposal
            )
    finally:
        connection.execute("COMMIT")
    return measurement


def wilson_interval(agree: int, compared: int) -> tuple[float, float] | None:
    """The 95% Wilson score interval for agree out of compared."""
    if compared <= 0:
        return None
    share = agree / compared
    scale = 1 + _Z_95**2 / compared
    centre = (share + _Z_95**2 / (2 * compared)) / scale
    spread = (
        _Z_95
        * math.sqrt(share * (1 - share) / compared + _Z_95**2 / (4 * compared**2))
        / scale
    )
    return max(0.0, centre - spread), min(1.0, centre + spread)


def passes(mark: Mark, agree: int, compared: int, contradictions: int) -> bool:
    """A mark passes on the point estimate, never on the interval."""
    return (
        compared >= mark.compared
        and compared > 0
        and Fraction(agree, compared) >= mark.agreement
        and contradictions <= mark.contradictions
    )


def _statistics(
    tally: Tally, contradictions: int, marks: tuple[Mark, ...], method: str
) -> dict[str, object]:
    compared = tally.agree + tally.disagree
    interval = wilson_interval(tally.agree, compared)
    data: dict[str, object] = {
        "compared": compared,
        "agree": tally.agree,
        "disagree": tally.disagree,
        "not_comparable": sum(tally.not_comparable.values()),
        "not_comparable_reasons": dict(sorted(tally.not_comparable.items())),
        "contradictions": contradictions,
        "agreement": None if not compared else round(tally.agree / compared, 4),
        "wilson_low": None if interval is None else round(interval[0], 4),
        "wilson_high": None if interval is None else round(interval[1], 4),
        "marks": [
            mark.to_data(passes(mark, tally.agree, compared, contradictions))
            for mark in marks
        ],
    }
    if method == "recording":
        data["references"] = dict(sorted(tally.references.items()))
    if tally.position_absent or method == "recording":
        data["position_absent"] = tally.position_absent
    return data


def assemble_report(
    measurement: Measurement, marks: tuple[Mark, ...] = DEFAULT_MARKS
) -> dict[str, object]:
    """Build the aggregate-only document from closed outcomes alone."""
    contradictions = {
        authority: measurement.populations[authority]["contradictions"]
        for authority in PROVIDERS
    }
    fields: dict[str, object] = {}
    for name, method in (
        *((value, "recording") for value in RECORDING_FIELDS),
        *((value, "owner_labelled") for value in OWNER_LABELLED_FIELDS),
    ):
        pooled = measurement.pooled[name]
        providers: dict[str, object] = {}
        for authority in PROVIDERS:
            tally = measurement.fields[name][authority]
            providers[authority] = _statistics(
                tally, contradictions[authority], marks, method
            )
        entry: dict[str, object] = {
            "method": method,
            "providers": providers,
            "pooled": _statistics(pooled, sum(contradictions.values()), marks, method),
        }
        if name == "publishedDate":
            cross = measurement.cross_provider_year
            compared = cross.agree + cross.disagree
            interval = wilson_interval(cross.agree, compared)
            entry["cross_provider"] = {
                "compared": compared,
                "agree": cross.agree,
                "disagree": cross.disagree,
                "agreement": None if not compared else round(cross.agree / compared, 4),
                "wilson_low": None if interval is None else round(interval[0], 4),
                "wilson_high": None if interval is None else round(interval[1], 4),
            }
        if method == "owner_labelled":
            entry["labels"] = {
                key: measurement.owner_labels[name][key]
                for key in ("labelled", "outside_work_route_fill", "withdrawn")
            }
        fields[name] = entry
    fields["cover"] = {"method": "not_measured", "reason": "no_identity_basis"}
    return {
        "schema": WORK_ROUTE_MEASUREMENT_SCHEMA,
        "marks": [mark.to_data() for mark in marks],
        "populations": {
            "audiobooks": measurement.audiobooks,
            "recording_admitted": measurement.recording_admitted,
            "providers": {
                authority: {
                    "recording": measurement.populations[authority]["recording"],
                    "unmatched": measurement.populations[authority]["unmatched"],
                    "duplicate": {
                        "recording": measurement.populations[authority][
                            "duplicate_recording"
                        ],
                        "unmatched": measurement.populations[authority][
                            "duplicate_unmatched"
                        ],
                    },
                    "contradictions": contradictions[authority],
                    "unmatched_fill": {
                        name: measurement.fill[authority][name] for name in FILL_FIELDS
                    },
                }
                for authority in PROVIDERS
            },
        },
        "fields": fields,
    }


def provenance_fragment(report: dict[str, object]) -> dict[str, object]:
    """The counts and mark results a provenance section records, nothing else."""
    fields = cast("dict[str, dict[str, object]]", report["fields"])
    summary: dict[str, object] = {}
    for name, entry in fields.items():
        if entry["method"] == "not_measured":
            summary[name] = {"method": entry["method"], "reason": entry["reason"]}
            continue
        scopes = {
            **cast("dict[str, dict[str, object]]", entry["providers"]),
            "pooled": cast("dict[str, object]", entry["pooled"]),
        }
        summary[name] = {
            "method": entry["method"],
            **{
                scope: {
                    "compared": values["compared"],
                    "agree": values["agree"],
                    "contradictions": values["contradictions"],
                    "passes": [
                        cast("dict[str, object]", mark)["pass"]
                        for mark in cast("list[object]", values["marks"])
                    ],
                }
                for scope, values in scopes.items()
            },
        }
    populations = cast("dict[str, object]", report["populations"])
    return {
        PROVENANCE_SECTION: {
            "schema": report["schema"],
            "marks": report["marks"],
            "audiobooks": populations["audiobooks"],
            "recording_admitted": populations["recording_admitted"],
            "fields": summary,
        }
    }


def _mark(value: str) -> Mark:
    parts = value.split(":")
    try:
        if len(parts) not in {2, 3}:
            raise ValueError
        compared = int(parts[0])
        agreement = Fraction(parts[1])
        contradictions = int(parts[2]) if len(parts) == 3 else 0
    except (ValueError, ZeroDivisionError):
        raise argparse.ArgumentTypeError(
            f"invalid mark {value!r}; expected N:P[:C]"
        ) from None
    if compared < 1 or not 0 < agreement <= 1 or contradictions < 0:
        raise argparse.ArgumentTypeError(f"invalid mark {value!r}; expected N:P[:C]")
    return Mark(compared, agreement, contradictions)


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--mark",
        action="append",
        default=[],
        type=_mark,
        metavar="N:P[:C]",
        help="add a pass mark: N compared, P agreement, at most C contradictions",
    )
    parser.add_argument(
        "--provenance",
        action="store_true",
        help="print the value-free provenance fragment instead of the report",
    )
    arguments = parser.parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    if not arguments.database.is_file():
        raise SystemExit("--database names no application database")
    connection = _open_read_only(arguments.database)
    try:
        measurement = measure(connection)
    except (sqlite3.Error, ValueError) as error:
        raise SystemExit(
            "--database is not an application store this checkout can read"
        ) from error
    finally:
        connection.close()
    report = assemble_report(
        measurement, (*DEFAULT_MARKS, *cast("list[Mark]", arguments.mark))
    )
    document = provenance_fragment(report) if arguments.provenance else report
    output.write(json.dumps(document, sort_keys=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
