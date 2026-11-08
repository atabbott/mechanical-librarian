"""The work-route measurement counts exactly what a real store holds (#1642).

Every population and comparator is read from reports, proposals and owner
decisions published through the real repository, as the cold-start report's
tests do; the mark and interval tests pin the pure arithmetic; the entry-point
tests bound the read-only, aggregate-only invocation.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import cast

import httpx
import pytest
import scripts.work_route_measurement as measurement

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    observe_provider,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.reviewer_decision_summary import correlation_identity
from tests.support.audiobook_providers import (
    audio_target,
    local_runtime_claim,
    work_plan,
)
from tests.support.audiobook_reports import recorded_dispatch
from tests.support.hardcover_evidence import hardcover_answer
from tests.support.metadata_report_doubles import single_track_audio_facts
from tests.unit.test_carried_recording_admission import _candidate, _catalogue

TITLE = "Ember Tide Protocol"
AUTHOR = "Corin Halloway"
NARRATOR = "Imra Vossel"
_HARDCOVER = correlation_identity("provider:hardcover")
_Current = str | tuple[TargetSeries, ...]
_OPEN_LIBRARY_PAYLOAD = (
    Path(__file__).parents[1]
    / "support"
    / "audiobook_provider_payloads"
    / "open_library_original_date.json"
)


def _plan() -> MetadataDiscoveryPlan:
    return replace(work_plan(local_runtime_claim()), audio=single_track_audio_facts())


def _hardcover(*books: dict[str, object]) -> ProviderObservation:
    """Hardcover's answer for the audiobook WORK lane, through the real adapter.

    Each book may set `id`, `series`, `position`, `release_date`, `genre`
    and `description`; every book states this target's title and author.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["operationName"] == "AudioEditions":
            return httpx.Response(200, json={"data": {"editions": []}})
        return httpx.Response(
            200,
            json=hardcover_answer(
                request,
                [],
                [
                    {
                        "id": book.get("id", 10),
                        "title": TITLE,
                        "description": book.get("description"),
                        "release_date": book.get("release_date"),
                        "image": None,
                        "contributions": [
                            {
                                "id": 100,
                                "contribution": None,
                                "author": {"id": 7, "name": AUTHOR},
                            }
                        ],
                        "book_series": (
                            [
                                {
                                    "id": 1,
                                    "position": book.get("position"),
                                    "series": {"id": 3, "name": book["series"]},
                                }
                            ]
                            if "series" in book
                            else []
                        ),
                        "taggings": (
                            [
                                {
                                    "id": 1,
                                    "tag": {
                                        "id": 5,
                                        "tag": book["genre"],
                                        "tag_category": {"id": 2, "category": "Genre"},
                                    },
                                }
                            ]
                            if "genre" in book
                            else []
                        ),
                    }
                    for book in books
                ],
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = HardcoverMetadataProvider(
            "hardcover",
            client,
            262_144,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda _seconds: None,
            jitter=lambda: 0.0,
        )
        return recorded_dispatch(
            observe_provider("hardcover", "hardcover", provider, _plan()).observation
        )


def _open_library(first_publish: str) -> ProviderObservation:
    """Open Library's linked work for this target, through the real adapter."""
    text = (
        _OPEN_LIBRARY_PAYLOAD.read_text()
        .replace("Synthetic Journey", TITLE)
        .replace("Example Author", AUTHOR)
    )
    responses = json.loads(text)
    responses["/works/OL5W.json"]["first_publish_date"] = first_publish

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenLibraryMetadataProvider(
            "open_library",
            client,
            262_144,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
        )
        return recorded_dispatch(
            observe_provider(
                "open_library", "open_library", provider, _plan()
            ).observation
        )


def _with_claims(
    candidate: ProviderCandidate, *claims: tuple[ClaimKind, str, str | None]
) -> ProviderCandidate:
    return replace(
        candidate,
        claims=candidate.claims
        + tuple(
            ProviderClaim.create(
                provider="audible",
                candidate=candidate.identity,
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=role,
                raw_order=len(candidate.claims) + index,
            )
            for index, (kind, value, role) in enumerate(claims)
        ),
    )


def _recording(series: tuple[str, str] | None = None) -> ProviderObservation:
    """An Audible product whose stated runtime admits this recording."""
    product = _candidate("audible", ("B0RECORDED",), narrator=NARRATOR)
    if series is not None:
        product = _with_claims(
            product,
            (ClaimKind.SERIES, series[0], None),
            (ClaimKind.SERIES_SEQUENCE, series[1], None),
        )
    return recorded_dispatch(_catalogue(_plan().local_claims, search=(product,)))


def _report(
    item: str,
    *providers: ProviderObservation,
    current: Mapping[TargetDestination, _Current] | None = None,
) -> MetadataReport:
    values = dict(current or {})
    fields = tuple(
        TargetField(
            field.destination, TargetFieldState.VALUE, values[field.destination]
        )
        if field.destination in values
        else field
        for field in audio_target(TITLE, (AUTHOR,)).fields
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", item, fields),
        file=FileFacts(0, hashlib.sha256(item.encode()).hexdigest(), "b" * 64, "m4b"),
        local_claims=_plan().local_claims,
        local_reasons=("target_metadata_title",),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        audio=single_track_audio_facts(),
    )
    return MetadataReport.from_json(report.to_json())


def _publish(
    repository: SQLiteMetadataReportRepository,
    report: MetadataReport,
    replacing: MetadataReport | None = None,
) -> str:
    repository.publish(
        report, lambda: True, None if replacing is None else replacing.report_id
    )
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    return cast(
        "str", json.loads(repository.admit_review_case(proposal))["review_case_id"]
    )


def _series(name: str, sequence: str | None) -> tuple[TargetSeries, ...]:
    return (TargetSeries(name, sequence),)


def _recording_reports() -> tuple[MetadataReport, ...]:
    owner_series = {TargetDestination.SERIES: _series("Tidewater", "2")}
    return (
        # Series and year agree with the owner's.
        _report(
            "r1",
            _recording(),
            _hardcover(
                {"series": "Tidewater", "position": "2", "release_date": "2001-05-01"}
            ),
            current={**owner_series, TargetDestination.PUBLISHED_DATE: "2001-03-04"},
        ),
        # The series agrees without a position; the year disagrees.
        _report(
            "r2",
            _recording(),
            _hardcover({"series": "Tidewater", "release_date": "2001"}),
            current={**owner_series, TargetDestination.PUBLISHED_DATE: "1998"},
        ),
        # Only Hardcover voted for the elected series; the years agree.
        _report(
            "r3",
            _recording(),
            _hardcover(
                {"series": "Saltmarsh", "position": "1", "release_date": "2001"}
            ),
            _open_library("2001"),
        ),
        # Another series name; the two providers' years disagree.
        _report(
            "r4",
            _recording(),
            _hardcover(
                {"series": "Saltmarsh", "position": "1", "release_date": "2001"}
            ),
            _open_library("1999"),
            current=owner_series,
        ),
        # The recording path elected the series both sources state.
        _report(
            "r5",
            _recording(("Tidewater", "2")),
            _hardcover({"series": "Tidewater", "position": "2"}),
        ),
        # Another position.
        _report(
            "r6",
            _recording(),
            _hardcover({"series": "Tidewater", "position": "3"}),
            current=owner_series,
        ),
        # Two different Hardcover works: excluded.
        _report("r7", _recording(), _hardcover({"id": 10}, {"id": 11})),
    )


def _measure(database: Path, *marks: measurement.Mark) -> dict[str, object]:
    connection = measurement._open_read_only(database)
    try:
        measured = measurement.measure(connection)
    finally:
        connection.close()
    return measurement.assemble_report(measured, (*measurement.DEFAULT_MARKS, *marks))


def _fields(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast("dict[str, dict[str, object]]", report["fields"])


def _provider(
    report: dict[str, object], field: str, provider: str
) -> dict[str, object]:
    providers = cast(
        "dict[str, dict[str, object]]", _fields(report)[field]["providers"]
    )
    return providers[provider]


def _counts(values: dict[str, object]) -> tuple[object, ...]:
    return (
        values["compared"],
        values["agree"],
        values["disagree"],
        values["not_comparable_reasons"],
        values.get("references"),
        values.get("position_absent"),
    )


def test_recording_comparators_count_every_agreement_and_disagreement(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    for value in _recording_reports():
        _publish(repository, value)
    repository.close()

    report = _measure(database)

    populations = cast("dict[str, object]", report["populations"])
    providers = cast("dict[str, dict[str, object]]", populations["providers"])
    assert (populations["audiobooks"], populations["recording_admitted"]) == (7, 7)
    assert providers["hardcover"]["recording"] == 6
    assert providers["hardcover"]["duplicate"] == {"recording": 1, "unmatched": 0}
    assert providers["open_library"]["recording"] == 2
    assert providers["hardcover"]["contradictions"] == 0
    assert _counts(_provider(report, "series", "hardcover")) == (
        5,
        3,
        2,
        {"reference_from_same_provider": 1},
        {"elected": 1, "owner": 4},
        1,
    )
    assert _counts(_provider(report, "series", "open_library")) == (
        0,
        0,
        0,
        {"no_source_value": 2},
        {},
        0,
    )
    assert _counts(_provider(report, "publishedDate", "hardcover")) == (
        4,
        2,
        2,
        {"no_source_value": 2},
        {"other_source": 2, "owner": 2},
        0,
    )
    assert _counts(_provider(report, "publishedDate", "open_library")) == (
        2,
        1,
        1,
        {},
        {"other_source": 2},
        0,
    )
    date = _fields(report)["publishedDate"]
    assert date["method"] == "recording"
    cross = cast("dict[str, object]", date["cross_provider"])
    assert (cross["compared"], cross["agree"], cross["disagree"]) == (2, 1, 1)
    pooled = cast("dict[str, object]", date["pooled"])
    # Pooled counts books: r3's and r4's two providers compare one pair once.
    assert (pooled["compared"], pooled["agree"], pooled["agreement"]) == (4, 2, 0.5)
    assert _fields(report)["cover"] == {
        "method": "not_measured",
        "reason": "no_identity_basis",
    }


def _decide(
    repository: SQLiteMetadataReportRepository,
    review_case_id: str,
    outcome: str,
    key: str,
    *,
    destination: str = "description",
) -> None:
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(review_case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == destination
    )
    result = cast("dict[str, object]", proposal["result"])
    alternatives = cast("list[dict[str, object]]", result["alternatives"])
    selected = next(
        cast("dict[str, object]", alternative["value"])
        for alternative in alternatives
        if _HARDCOVER in cast("list[str]", alternative["correlations"])
    )
    repository.record_human_decision(
        HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=destination,
            actor="owner",
            outcome=outcome,
            selected_value=selected if outcome == "accept" else None,
            idempotency_key=f"work-route-measurement-{key}",
            occurred_at=1_000,
        )
    )


def _unmatched(item: str, **current: str) -> MetadataReport:
    return _report(
        item,
        _hardcover(
            {
                "description": "A tide that keeps time.",
                "release_date": "2001",
                "genre": "Sea Fantasy",
            }
        ),
        _open_library("2001"),
        current={TargetDestination(key): value for key, value in current.items()},
    )


def test_unmatched_fill_and_owner_labels_count_work_route_offers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    accepted = _publish(repository, _unmatched("u1"))
    kept = _publish(repository, _unmatched("u2"))
    withdrawn = _publish(repository, _unmatched("u3"))
    filled = _publish(repository, _unmatched("u4", description="Already written."))
    _publish(repository, _report("u5", _hardcover({"id": 10}, {"id": 11})))
    _decide(repository, accepted, "accept", "accept")
    _decide(repository, kept, "keep_current", "keep")
    _decide(repository, withdrawn, "accept", "genre", destination="genres")
    _decide(repository, withdrawn, "withdraw", "ungenre", destination="genres")
    _decide(repository, filled, "accept", "filled")
    repository.close()

    report = _measure(database)

    populations = cast("dict[str, object]", report["populations"])
    providers = cast("dict[str, dict[str, object]]", populations["providers"])
    assert (populations["audiobooks"], populations["recording_admitted"]) == (5, 0)
    assert providers["hardcover"]["unmatched"] == 4
    assert providers["hardcover"]["duplicate"] == {"recording": 0, "unmatched": 1}
    assert providers["open_library"]["unmatched"] == 4
    fill = {"series": 0, "publishedDate": 4, "description": 3, "narrators": 0}
    assert providers["hardcover"]["unmatched_fill"] == {
        **fill,
        "genres": 4,
        "cover": 0,
    }
    assert providers["open_library"]["unmatched_fill"] == {
        **fill,
        "genres": 0,
        "cover": 0,
    }
    description = _fields(report)["description"]
    assert description["method"] == "owner_labelled"
    assert description["labels"] == {
        "labelled": 2,
        "outside_work_route_fill": 1,
        "withdrawn": 0,
    }
    assert _fields(report)["genres"]["labels"] == {
        "labelled": 0,
        "outside_work_route_fill": 0,
        "withdrawn": 1,
    }
    # Accepting Hardcover's value agrees with it and disagrees with Open
    # Library's; keeping the empty field disagrees with both.
    assert _counts(_provider(report, "description", "hardcover"))[:4] == (2, 1, 1, {})
    assert _counts(_provider(report, "description", "open_library"))[:4] == (
        2,
        0,
        2,
        {},
    )
    for field in ("genres", "narrators"):
        assert _counts(_provider(report, field, "hardcover"))[:4] == (0, 0, 0, {})


@pytest.mark.parametrize(
    "recording",
    [
        _with_claims(
            _candidate("audible", ("B0RECORDED",)),
            (ClaimKind.CREATOR, "Second Writer", "author"),
        ),
        _candidate("audible", ("B0RECORDED",), title="A Different Book"),
    ],
    ids=("author_set", "title_key"),
)
def test_a_contradiction_is_not_compared_and_fails_every_field(
    monkeypatch: pytest.MonkeyPatch, recording: ProviderCandidate
) -> None:
    """A persisted report cannot retain such a recording beside an exact work
    source (the lineage invariant refuses it), so the admitted recording is
    substituted at the seam that names it."""
    report = _report(
        "r1",
        _recording(),
        _hardcover({"series": "Tidewater", "position": "2", "release_date": "2001"}),
        current={
            TargetDestination.SERIES: _series("Tidewater", "2"),
            TargetDestination.PUBLISHED_DATE: "2001",
        },
    )
    monkeypatch.setattr(
        measurement, "_admitted_recordings", lambda _report: (recording,)
    )
    measured = measurement.Measurement()
    for _ in range(25):
        measurement._classify(measured, report, evaluate_report(report).to_data())
    clean = measurement.Measurement()
    measured.fields["description"]["hardcover"].agree = 25

    assembled = measurement.assemble_report(measured)

    assert measured.populations["hardcover"]["contradictions"] == 25
    assert measured.fields["series"]["hardcover"] == clean.fields["series"]["hardcover"]
    for field in ("series", "publishedDate", "description", "genres", "narrators"):
        for scope in (
            _provider(assembled, field, "hardcover"),
            cast("dict[str, object]", _fields(assembled)[field]["pooled"]),
        ):
            assert scope["contradictions"] == 25
            assert not any(
                cast("dict[str, object]", mark)["pass"]
                for mark in cast("list[object]", scope["marks"])
            )
    assert _provider(assembled, "description", "hardcover")["agreement"] == 1.0


def test_the_wilson_interval_is_informational() -> None:
    assert measurement.wilson_interval(0, 0) is None
    low, high = cast("tuple[float, float]", measurement.wilson_interval(20, 20))
    assert round(low, 3) == 0.839
    assert high == 1.0
    low, high = cast("tuple[float, float]", measurement.wilson_interval(0, 20))
    assert low == 0.0
    assert round(high, 3) == 0.161


@pytest.mark.parametrize(
    ("agree", "compared", "contradictions", "expected"),
    [
        (19, 19, 0, (False, True)),
        (19, 20, 0, (True, True)),
        (18, 20, 0, (False, True)),
        (9, 10, 0, (False, True)),
        (8, 10, 0, (False, False)),
        (9, 9, 0, (False, False)),
        (20, 20, 1, (False, False)),
        (0, 0, 0, (False, False)),
    ],
)
def test_marks_pass_on_the_point_estimate(
    agree: int, compared: int, contradictions: int, expected: tuple[bool, bool]
) -> None:
    assert (
        tuple(
            measurement.passes(mark, agree, compared, contradictions)
            for mark in measurement.DEFAULT_MARKS
        )
        == expected
    )


def test_a_looser_mark_shows_what_it_would_grant() -> None:
    measured = measurement.Measurement()
    tally = measured.fields["description"]["hardcover"]
    tally.agree, tally.disagree = 14, 1
    measured.populations["hardcover"]["contradictions"] = 1

    report = measurement.assemble_report(
        measured,
        (
            *measurement.DEFAULT_MARKS,
            measurement._mark("15:0.9"),
            measurement._mark("15:0.9:1"),
        ),
    )

    marks = cast(
        "list[dict[str, object]]",
        _provider(report, "description", "hardcover")["marks"],
    )
    assert [mark["pass"] for mark in marks] == [False, False, False, True]
    assert marks[3] == {
        "min_compared": 15,
        "min_agreement": 0.9,
        "max_contradictions": 1,
        "pass": True,
    }


@pytest.mark.parametrize(
    "value", ["20", "x:0.9", "0:0.9", "20:0", "20:1.5", "20:0.9:-1", "1:2:3:4"]
)
def test_a_malformed_mark_is_refused(value: str, tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    with pytest.raises(SystemExit):
        measurement.main(["--database", str(database), "--mark", value], io.StringIO())


def _strings(value: object) -> set[str]:
    if isinstance(value, dict):
        return {
            *(str(key) for key in cast("dict[object, object]", value)),
            *(
                item
                for child in cast("dict[object, object]", value).values()
                for item in _strings(child)
            ),
        }
    if isinstance(value, list):
        return {
            item for child in cast("list[object]", value) for item in _strings(child)
        }
    return {value} if isinstance(value, str) else set()


_VOCABULARY = frozenset(
    {
        *measurement.PROVIDERS,
        *measurement.FILL_FIELDS,
        measurement.WORK_ROUTE_MEASUREMENT_SCHEMA,
        measurement.PROVENANCE_SECTION,
        "agree",
        "agreement",
        "audiobooks",
        "compared",
        "contradictions",
        "cross_provider",
        "disagree",
        "duplicate",
        "duplicate_sources",
        "elected",
        "fields",
        "labelled",
        "labels",
        "marks",
        "max_contradictions",
        "method",
        "min_agreement",
        "min_compared",
        "no_identity_basis",
        "no_reference",
        "no_source_value",
        "not_comparable",
        "not_comparable_reasons",
        "not_measured",
        "other_source",
        "outside_work_route_fill",
        "owner",
        "owner_labelled",
        "pass",
        "passes",
        "pooled",
        "populations",
        "position_absent",
        "providers",
        "reason",
        "recording",
        "recording_admitted",
        "reference_from_same_provider",
        "references",
        "schema",
        "source_values_differ",
        "unmatched",
        "unmatched_fill",
        "wilson_high",
        "wilson_low",
        "withdrawn",
    }
)


def test_main_is_read_only_and_prints_no_title_path_value_or_identifier(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    for report in _recording_reports():
        _publish(repository, report)
    accepted = _publish(repository, _unmatched("u1"))
    _decide(repository, accepted, "accept", "accept")
    repository.close()
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    outputs = []
    for extra in ([], ["--provenance"], ["--mark", "5:0.5:2"]):
        stdout = io.StringIO()
        assert measurement.main(["--database", str(database), *extra], stdout) == 0
        outputs.append(stdout.getvalue())

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    connection = measurement._open_read_only(database)
    try:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE probe(value)")
    finally:
        connection.close()
    for output in outputs:
        assert _strings(json.loads(output)) <= _VOCABULARY
        for secret in (
            TITLE,
            AUTHOR,
            NARRATOR,
            "Tidewater",
            "Saltmarsh",
            "A tide that keeps time.",
            "2001",
            "1999",
            '"primary"',
            '"audio"',
            "B0RECORDED",
            "hardcover-work",
            "open-library",
            "report_",
            "review_case_",
            "correlation_",
            str(tmp_path),
        ):
            assert secret not in output
    provenance = json.loads(outputs[1])
    assert set(provenance) == {measurement.PROVENANCE_SECTION}
    section = provenance[measurement.PROVENANCE_SECTION]
    assert section["fields"]["series"]["hardcover"] == {
        "compared": 5,
        "agree": 3,
        "contradictions": 0,
        "passes": [False, False],
    }
    assert section["fields"]["cover"] == {
        "method": "not_measured",
        "reason": "no_identity_basis",
    }
    assert len(json.loads(outputs[2])["marks"]) == 3


def test_main_refuses_a_missing_or_unreadable_store(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="names no application database"):
        measurement.main(["--database", str(tmp_path / "absent.sqlite3")])
    foreign = tmp_path / "foreign.sqlite3"
    sqlite3.connect(foreign).close()
    with pytest.raises(SystemExit, match="cannot read|can read"):
        measurement.main(["--database", str(foreign)], io.StringIO())


def test_the_schema_is_pinned_at_its_first_version() -> None:
    assert measurement.WORK_ROUTE_MEASUREMENT_SCHEMA == "work-route-measurement-v1"
    assert [mark.to_data() for mark in measurement.DEFAULT_MARKS] == [
        {"min_compared": 20, "min_agreement": 0.95, "max_contradictions": 0},
        {"min_compared": 10, "min_agreement": 0.9, "max_contradictions": 0},
    ]
    assert measurement.DEFAULT_MARKS[0].agreement == Fraction(19, 20)
    assert Counter(measurement.FILL_FIELDS)["cover"] == 1


def test_pooled_counts_one_book_once_when_both_providers_compare_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    # Both providers compare with the owner's year: Hardcover agrees, Open
    # Library does not, so the one book disagrees.
    _publish(
        repository,
        _report(
            "split",
            _recording(),
            _hardcover({"release_date": "2001"}),
            _open_library("1999"),
            current={TargetDestination.PUBLISHED_DATE: "2001-02-03"},
        ),
    )
    repository.close()

    date = _fields(_measure(database))["publishedDate"]

    assert _counts(_provider(_measure(database), "publishedDate", "hardcover"))[:3] == (
        1,
        1,
        0,
    )
    pooled = cast("dict[str, object]", date["pooled"])
    assert (pooled["compared"], pooled["agree"], pooled["disagree"]) == (1, 0, 1)
    assert pooled["references"] == {"owner": 1}


def test_the_cross_provider_date_pair_counts_once_in_pooled(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    _publish(
        repository,
        _report(
            "pair",
            _recording(),
            _hardcover({"release_date": "2001"}),
            _open_library("2001"),
        ),
    )
    repository.close()

    date = _fields(_measure(database))["publishedDate"]

    for provider in ("hardcover", "open_library"):
        assert (
            cast("dict[str, dict[str, object]]", date["providers"])[provider][
                "compared"
            ]
            == 1
        )
    pooled = cast("dict[str, object]", date["pooled"])
    assert (pooled["compared"], pooled["agree"]) == (1, 1)
    assert cast("dict[str, object]", date["cross_provider"])["compared"] == 1


@pytest.mark.parametrize(
    ("source", "reference", "expected"),
    [
        ("3", None, measurement.Outcome("not_comparable", "reference_position_absent")),
        ("2", "2.0", measurement.Outcome("agree", "owner")),
        ("2", "3", measurement.Outcome("disagree", "owner")),
        (None, "2", measurement.Outcome("agree", "owner", True)),
    ],
    ids=("reference_absent", "unsigned_decimal", "different", "source_absent"),
)
def test_series_positions_follow_the_products_rule(
    source: str | None, reference: str | None, expected: measurement.Outcome
) -> None:
    assert (
        measurement._compare_series(
            (("Tidewater", source),), (("tidewater", reference),), "owner"
        )
        == expected
    )


def test_a_later_unqualified_decision_cannot_hide_an_owner_label(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    first = _unmatched("u1")
    empty = _publish(repository, first)
    # Description takes no owner-entered value, so keeping it empty is the
    # disagreement; a later decision against the filled field must not hide it.
    _decide(repository, empty, "keep_current", "kept-empty")
    filled = _publish(repository, _unmatched("u1", description="My own words."), first)
    _decide(repository, filled, "keep_current", "kept-filled")
    repository.close()

    report = _measure(database)

    assert _fields(report)["description"]["labels"] == {
        "labelled": 1,
        "outside_work_route_fill": 1,
        "withdrawn": 0,
    }
    assert _counts(_provider(report, "description", "hardcover"))[:3] == (1, 0, 1)
    pooled = cast("dict[str, object]", _fields(report)["description"]["pooled"])
    assert (pooled["compared"], pooled["disagree"]) == (1, 1)


def test_an_owner_label_for_a_provider_without_an_exact_source_is_not_compared() -> (
    None
):
    report = _report("u1", _hardcover({"description": "A tide that keeps time."}))
    value = {"kind": "text", "value": "A tide that keeps time."}
    proposal = {
        "results": [
            {
                "destination": "description",
                "alternatives": [
                    {
                        "value": value,
                        "correlations": [
                            _HARDCOVER,
                            correlation_identity("provider:open_library"),
                        ],
                    }
                ],
            }
        ]
    }
    measured = measurement.Measurement()

    measurement._label(measured, "description", "accept", value, report, proposal)

    assert measured.fields["description"]["hardcover"].agree == 1
    assert measured.fields["description"]["open_library"].not_comparable == Counter(
        {"no_exact_source": 1}
    )
    assert (
        measured.pooled["description"].agree,
        measured.pooled["description"].disagree,
    ) == (1, 0)


def test_an_owner_value_the_librarian_wrote_is_no_reference(tmp_path: Path) -> None:
    """The store's own write record marks the owner's series as the Librarian's.

    Driving a verified Audiobookshelf write is out of scope here, so the
    effect row is inserted beside the reports the repository published.
    """
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    owner: dict[TargetDestination, _Current] = {
        TargetDestination.SERIES: _series("Tidewater", "2"),
        TargetDestination.PUBLISHED_DATE: "2001",
    }
    for item in ("written", "untouched"):
        _publish(
            repository,
            _report(
                item,
                _recording(),
                _hardcover(
                    {"series": "Tidewater", "position": "2", "release_date": "2001"}
                ),
                current=owner,
            ),
        )
    repository.close()
    connection = sqlite3.connect(database)
    with connection:
        for index, (destination, state) in enumerate(
            (("series", "verified"), ("publishedDate", "stale"))
        ):
            connection.execute(
                "INSERT INTO field_effects(plan_id,decision_id,connection_name,"
                "library_name,work_id,destination,state,attempt,updated_at,document) "
                "VALUES(?,?,?,?,?,?,?,0,1,'{}')",
                (
                    "effect_plan_" + f"{index:x}" * 64,
                    "decision",
                    "primary",
                    "audio",
                    "written",
                    destination,
                    state,
                ),
            )
    connection.close()

    report = _measure(database)

    assert _counts(_provider(report, "series", "hardcover"))[:4] == (
        1,
        1,
        0,
        {"reference_written_by_librarian": 1},
    )
    # A stale effect never wrote, so the owner's year still counts.
    assert _counts(_provider(report, "publishedDate", "hardcover"))[:4] == (
        2,
        2,
        0,
        {},
    )


def test_a_possibly_landed_librarian_write_is_no_reference(tmp_path: Path) -> None:
    """A retryable write, or one whose history reached applying, may have landed.

    A write that lands but raises is persisted as retryable, and a later stale
    or abandoned state does not prove an earlier applying write never landed.
    """
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    owner: dict[TargetDestination, _Current] = {
        TargetDestination.SERIES: _series("Tidewater", "2"),
        TargetDestination.PUBLISHED_DATE: "2001",
    }
    for item in ("written", "untouched"):
        _publish(
            repository,
            _report(
                item,
                _recording(),
                _hardcover(
                    {"series": "Tidewater", "position": "2", "release_date": "2001"}
                ),
                current=owner,
            ),
        )
    repository.close()
    connection = sqlite3.connect(database)
    with connection:
        for index, (destination, state) in enumerate(
            (("series", "retryable"), ("publishedDate", "abandoned"))
        ):
            plan_id = "effect_plan_" + f"{index:x}" * 64
            connection.execute(
                "INSERT INTO field_effects(plan_id,decision_id,connection_name,"
                "library_name,work_id,destination,state,attempt,updated_at,document) "
                "VALUES(?,?,?,?,?,?,?,1,1,'{}')",
                (
                    plan_id,
                    "decision",
                    "primary",
                    "audio",
                    "written",
                    destination,
                    state,
                ),
            )
        connection.execute(
            "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,"
            "occurred_at,document) VALUES(?,?,1,'applying',1,'{}')",
            ("effect_event_" + "1" * 64, "effect_plan_" + "1" * 64),
        )
    connection.close()

    report = _measure(database)

    assert _counts(_provider(report, "series", "hardcover"))[:4] == (
        1,
        1,
        0,
        {"reference_written_by_librarian": 1},
    )
    assert _counts(_provider(report, "publishedDate", "hardcover"))[3] == {
        "reference_written_by_librarian": 1
    }


def test_a_pooled_book_with_two_reasons_is_one_not_comparable_book() -> None:
    """Each provider can give its own reason, but the book counts once pooled."""
    providers = {"hardcover": measurement.Tally(), "open_library": measurement.Tally()}
    pooled = measurement.Tally()

    measurement._record(
        providers,
        pooled,
        {
            "hardcover": measurement._not_comparable("reference_position_absent"),
            "open_library": measurement._not_comparable("no_source_value"),
        },
    )

    data = measurement._statistics(pooled, 0, measurement.DEFAULT_MARKS, "recording")
    assert data["not_comparable"] == 1
    assert data["not_comparable_reasons"] == {
        "no_source_value": 1,
        "reference_position_absent": 1,
    }
    assert providers["hardcover"].not_comparable_items == 1
