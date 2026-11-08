"""The admission-rule ledger replay over synthetic snapshots (#1350).

Every fixture is built here in ``tmp_path``; no binary fixture is committed.
Every free-text slot in every synthetic document carries a unique sentinel, so
a leak of any title, creator, identifier, path or configured name shows up as
a sentinel in the output.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import shutil
import sqlite3
import subprocess
from collections.abc import Mapping, Sequence
from itertools import count
from pathlib import Path
from statistics import NormalDist
from typing import Literal, cast

import pytest
import scripts.admission_rule_ledger as ledger
import scripts.admission_rule_series as series_replay
import scripts.cold_start_report as cold_start

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application import metadata_report as application_report
from mechanical_librarian.domain import shadow_decision, shadow_outcome
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.proposal_set import (
    _OUTCOME_REASON_SETS,
    evaluate_report,
)
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)
from tests.unit.test_cold_start_report import _publish_current_cover_consideration

Profile = Literal["v26-like", "v57-like"]
REPOSITORY = Path(__file__).resolve().parents[2]
EBOOK_REVISION = "1" * 40
AUDIO_REVISION = "2" * 40
_SENTINEL = "ZQX-SENTINEL-"


class Sentinels:
    """Hands out unique sentinel strings for every free-text slot."""

    def __init__(self) -> None:
        self._counter = count(1)

    def __call__(self) -> str:
        return f"{_SENTINEL}{next(self._counter):04d}"


def _claim(
    text: Sentinels, kind: str, state: str = "valid", **extra: object
) -> dict[str, object]:
    return {
        "kind": kind,
        "state": state,
        "canonical": text(),
        "excerpt": text(),
        "reference": text(),
        "applicability": "unknown",
        "applicability_basis": "unlabelled",
        "basis": "package_metadata",
        "rear_provenance": None,
        **extra,
    }


class Fixture:
    """One synthetic report, case, shadow outcome set and cover consideration."""

    def __init__(self, text: Sentinels, profile: Profile) -> None:
        self.text = text
        self.profile = profile
        self.provider_names: list[str] = []
        self.report: dict[str, object] = {
            "schema_version": "metadata-report-v57"
            if profile == "v57-like"
            else "metadata-report-v26",
            "file": {"format": "epub", "path": text()},
            "target": {"title": text(), "authors": [text()]},
            "local": {
                "claims": [_claim(text, "title")],
                "reasons": [],
                "status": "complete",
            },
            "field_results": [{"kind": "title", "state": "valid", "value": text()}],
            "identifier_evidence": [],
            "providers": [],
            "covers": [{"kind": "epub", "state": "retrieved", "path": text()}],
        }
        if profile == "v57-like":
            self.report["file"] = {"format": "m4b", "path": text()}
            self.report["audio"] = {
                "chapters": [{"title": text()}],
                "chapters_truncated": False,
                "chapter_rows_omitted": 0,
                "discovery_titles": [text()],
            }
        self.results: list[dict[str, object]] = []
        self.shadow: list[dict[str, object]] = []
        self.consideration: dict[str, object] | None = None
        # The library item the case is for, and the target values a rescan
        # changes (#1748).
        self.item = (text(), text(), text())
        self.target_values = [text()]
        self.current = True
        self.decisions: list[tuple[int, str, str, object]] = []
        self.automatic: list[tuple[str, dict[str, object]]] = []

    def decide(
        self, ordinal: int, destination: str, outcome: str, value: object = None
    ) -> None:
        """Record an owner decision; ``value`` is the selected value document."""
        self.decisions.append((ordinal, destination, outcome, value))

    def provider(self, *, status: str = "candidates") -> int:
        name = self.text()
        self.provider_names.append(name)
        provider: dict[str, object] = {
            "provider": name,
            "status": status,
            "candidates": [],
            "lanes": [],
            "diagnostics": [],
            "request_accounting": {"undispatched_incomplete": 0},
            "edition_enrichment_overflow": None,
        }
        cast("list[object]", self.report["providers"]).append(provider)
        return len(self.provider_names) - 1

    def _providers(self) -> list[dict[str, object]]:
        return cast("list[dict[str, object]]", self.report["providers"])

    def record(
        self, provider: int, work: str, *, lane: str = "isbn"
    ) -> tuple[str, str]:
        """Add a provider record; return its identity and one claim reference."""
        identity, claim = self.text(), self.text()
        self._providers()[provider]["candidates"] = [
            *cast("list[object]", self._providers()[provider]["candidates"]),
            {
                "identity": identity,
                "work_identity": work,
                "claims": [{"reference": claim, "kind": "title", "value": self.text()}],
                "evidence": [],
            },
        ]
        lane_entry: dict[str, object] = {
            "kind": lane,
            "status": "candidates",
            "candidates": [
                {
                    "edition_identity": identity,
                    "work_identity": work,
                    "reference": self.text(),
                }
            ],
            "matched_family": None,
        }
        if self.profile == "v57-like":
            lane_entry["diagnostics"] = []
            lane_entry["dispatch"] = {
                "outcome": "admitted",
                "rungs": [],
                "near_miss_seconds": None,
            }
        self._providers()[provider]["lanes"] = [
            *cast("list[object]", self._providers()[provider]["lanes"]),
            lane_entry,
        ]
        return identity, claim

    def bind(self, provider: int, identity: str, work: str, relation: str) -> None:
        cast("list[object]", self.report["identifier_evidence"]).append(
            {
                "relation": relation,
                "basis": "provider_identifier_agreement",
                "candidate_identity": identity,
                "work_identity": work,
                "provider": self.provider_names[provider],
                "reference": self.text(),
                "related_references": [self.text()],
            }
        )

    def result(
        self,
        destination: str,
        outcome: str,
        reasons: Sequence[str],
        references: Sequence[str] = (),
    ) -> None:
        alternatives = (
            [
                {
                    "alternative_id": self.text(),
                    "value": self.text(),
                    "evidence_references": list(references),
                }
            ]
            if references
            else []
        )
        self.results.append(
            {
                "destination": destination,
                "outcome": outcome,
                "reasons": list(reasons),
                "alternatives": alternatives,
                "selected_alternative_id": None,
            }
        )

    def decide_automatically(
        self,
        destination: str,
        outcome: str,
        candidate: str | None = None,
        *,
        generation: int = 1,
    ) -> None:
        """Store an automatic decision whose free-text slots are sentinels."""
        self.automatic.append(
            (
                destination,
                {
                    "outcome": outcome,
                    "candidate_id": candidate,
                    "destination": destination,
                    "value": self.text(),
                    "rule_id": self.text(),
                    "policy_option": self.text(),
                    "target_fingerprint": self.text(),
                    "policy_generation": generation,
                },
            )
        )

    def abstain(
        self, destination: str, code: str, disposition: str = "abstention"
    ) -> None:
        self.shadow.append(
            {
                "destination": destination,
                "disposition": disposition,
                "reason_code": code,
                "candidate_id": self.text(),
            }
        )

    def cover_candidate(
        self,
        outcome: str,
        provider: int,
        work: str,
        identity: str | None,
        *,
        designation: str | None = None,
    ) -> str:
        """Add a cover candidate; return its report cover source id.

        ``identity`` None is a work-level cover.
        """
        artifact, reference, source = self.text(), self.text(), self.text()
        cast("list[object]", self.report["covers"]).append(
            {
                "source_id": source,
                "kind": "hardcover",
                "state": "retrieved",
                "artifact": {"artifact_id": artifact},
                "provenance": {
                    "provider": self.provider_names[provider],
                    "designation": designation
                    or ("work" if identity is None else "exact_edition"),
                    "work_identity": work,
                    "edition_identity": identity,
                    "context": self.text(),
                },
            }
        )
        if self.consideration is None:
            self.consideration = {
                "identity_document": json.dumps({"candidates": []}),
                "candidates": [],
                "omissions": [],
            }
        identity_document = json.loads(
            cast("str", self.consideration["identity_document"])
        )
        identity_document["candidates"].append(
            {
                "candidate_reference": reference,
                "outcome": outcome,
                "evidence_references": [self.text()],
            }
        )
        self.consideration["identity_document"] = json.dumps(identity_document)
        cast("list[object]", self.consideration["candidates"]).append(
            {"reference": reference, "artifact_id": artifact, "url": self.text()}
        )
        return source


def _database(
    path: Path,
    profile: Profile,
    fixtures: Sequence[Fixture],
    *,
    owner_tables: bool = False,
    floors: Mapping[int, int] | None = None,
    automatic: bool = True,
    generation: int | None = 1,
) -> Path:
    """Write the fixtures as cases ``case-{index}-...``, in index order.

    Owner decisions get a ``human_decisions`` table where any fixture decides
    or ``owner_tables`` asks; ``floors`` (fixture index -> decision floor)
    adds ``review_case_episodes``. ``automatic`` False omits the
    automatic-decision table, and ``generation`` None the policy state naming
    the active generation.
    """
    connection = sqlite3.connect(path)
    currentness = ",currentness_document TEXT" if profile == "v57-like" else ""
    connection.executescript(
        "CREATE TABLE current_review_packages (connection_name TEXT,library_name TEXT,"
        "item_id TEXT,proposal_set_id TEXT,report_id TEXT,review_case_id TEXT,"
        "checkpoint TEXT,outcome TEXT);"
        "CREATE TABLE review_cases (review_case_id TEXT PRIMARY KEY,proposal_set_id TEXT,"
        "report_id TEXT,document TEXT);"
        "CREATE TABLE metadata_reports (report_id TEXT PRIMARY KEY,document TEXT);"
        "CREATE TABLE shadow_decision_outcomes (outcome_set_id TEXT,review_case_id TEXT,"
        "report_id TEXT,proposal_set_id TEXT,resolved_policy_id TEXT,"
        "source_decision_set_id TEXT,evaluated_at INTEGER,document TEXT);"
        "CREATE TABLE cover_considerations (consideration_id TEXT,review_case_id TEXT,"
        "report_id TEXT,replay_digest TEXT,document_digest TEXT,supersedes_id TEXT,"
        f"occurred_at INTEGER{currentness},document TEXT);"
        "CREATE TABLE current_cover_considerations (review_case_id TEXT,consideration_id TEXT);"
        "CREATE TABLE catalogue_entries (title TEXT);"
    )
    if profile == "v57-like":
        connection.execute("CREATE TABLE review_attention_facts (review_case_id TEXT)")
    if owner_tables or any(fixture.decisions for fixture in fixtures):
        connection.execute(
            "CREATE TABLE human_decisions (ordinal INTEGER PRIMARY KEY,"
            "decision_id TEXT,review_case_id TEXT,destination TEXT,outcome TEXT,"
            "selected_value_document TEXT)"
        )
    if floors is not None:
        connection.execute(
            "CREATE TABLE review_case_episodes (review_case_id TEXT PRIMARY KEY,"
            "decision_floor INTEGER,effect_floor INTEGER)"
        )
    if automatic:
        connection.execute(
            "CREATE TABLE automatic_decisions (decision_id TEXT,review_case_id TEXT,"
            "destination TEXT,document TEXT)"
        )
    if generation is not None:
        connection.execute(
            "CREATE TABLE metadata_policy_state (singleton INTEGER,generation INTEGER)"
        )
        connection.execute(
            "INSERT INTO metadata_policy_state VALUES (1,?)", (generation,)
        )
    for ordinal, fixture in enumerate(fixtures):
        text = fixture.text
        case, report_id = f"case-{ordinal}-{text()}", f"report-{text()}"
        connection.execute(
            "INSERT INTO metadata_reports VALUES (?,?)",
            (report_id, json.dumps(fixture.report)),
        )
        connection_name, library, item = fixture.item
        document = {
            "results": fixture.results,
            "target": {
                "connection": connection_name,
                "library": library,
                "item": item,
                "fields": [{"value": value} for value in fixture.target_values],
            },
        }
        connection.execute(
            "INSERT INTO review_cases VALUES (?,?,?,?)",
            (case, text(), report_id, json.dumps(document)),
        )
        if fixture.current:
            connection.execute(
                "INSERT INTO current_review_packages VALUES (?,?,?,?,?,?,?,?)",
                (text(), text(), text(), text(), report_id, case, text(), "review"),
            )
        for number, destination, outcome, value in fixture.decisions:
            connection.execute(
                "INSERT INTO human_decisions VALUES (?,?,?,?,?,?)",
                (number, text(), case, destination, outcome, json.dumps(value)),
            )
        if floors is not None and ordinal in floors:
            connection.execute(
                "INSERT INTO review_case_episodes VALUES (?,?,?)",
                (case, floors[ordinal], 0),
            )
        connection.execute(
            "INSERT INTO shadow_decision_outcomes VALUES (?,?,?,?,?,?,?,?)",
            (
                text(),
                case,
                report_id,
                text(),
                text(),
                text(),
                1,
                json.dumps({"outcomes": fixture.shadow}),
            ),
        )
        if fixture.consideration is not None:
            consideration = text()
            values: tuple[object, ...] = (
                consideration,
                case,
                report_id,
                text(),
                text(),
                None,
                1,
            )
            if profile == "v57-like":
                values += ("{}",)
            connection.execute(
                f"INSERT INTO cover_considerations VALUES ({','.join('?' * (len(values) + 1))})",
                (*values, json.dumps(fixture.consideration)),
            )
            connection.execute(
                "INSERT INTO current_cover_considerations VALUES (?,?)",
                (case, consideration),
            )
        connection.execute("INSERT INTO catalogue_entries VALUES (?)", (text(),))
        assert automatic or not fixture.automatic
        for destination, decision in fixture.automatic:
            connection.execute(
                "INSERT INTO automatic_decisions VALUES (?,?,?,?)",
                (text(), case, destination, json.dumps(decision)),
            )
    # A superseded case the replay must not read: it carries every signal.
    stale = Fixture(Sentinels(), profile)
    stale.result("isbn", "defer", ["identifier_applicability_unresolved"])
    connection.execute(
        "INSERT INTO review_cases VALUES (?,?,?,?)",
        ("stale-case", "x", "stale-report", json.dumps({"results": stale.results})),
    )
    connection.execute(
        "INSERT INTO metadata_reports VALUES (?,?)",
        ("stale-report", json.dumps(stale.report)),
    )
    connection.commit()
    connection.close()
    return path


def _always(_commit: str, _revision: str) -> bool:
    return True


def _no_attention(_root: Path, _database: Path) -> ledger.AttentionProbe:
    return ledger.AttentionProbe((), 0, 0)


def _series_probe(
    w9: Mapping[int, bool] | None = None,
    a8: Sequence[int] = (),
    a9: Sequence[int] = (),
    **counts: int,
) -> series_replay.SeriesProbe:
    return series_replay.SeriesProbe(
        {key: counts.get(key, 0) for key in series_replay.COUNTS},
        dict(w9 or {}),
        frozenset(a8),
        frozenset(a9),
    )


def _no_series(_root: Path, _database: Path, _cases: str) -> series_replay.SeriesProbe:
    return _series_probe()


def _run(
    arguments: Sequence[str],
    *,
    ancestry: ledger.Ancestry = _always,
    attention: ledger.AttentionReader = _no_attention,
    series: series_replay.SeriesReader = _no_series,
) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ledger.main(
        list(arguments),
        stdout=stdout,
        stderr=stderr,
        ancestry=ancestry,
        attention=attention,
        series_replay=series,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _arguments(
    snapshots: Sequence[tuple[str, Path, str]], root: Path, *extra: str
) -> list[str]:
    arguments: list[str] = []
    for label, database, revision in snapshots:
        arguments += [
            "--snapshot",
            label,
            str(database),
            revision,
            "--source-root",
            label,
            str(root),
        ]
    return [*arguments, *extra]


def _rows(output: str) -> dict[str, dict[str, object]]:
    loaded = json.loads(output)
    return {cast("str", row["gate_id"]): row for row in loaded["rows"]}


def _snapshot(row: Mapping[str, object], label: str) -> dict[str, object]:
    return next(
        entry
        for entry in cast("list[dict[str, object]]", row["snapshots"])
        if entry["label"] == label
    )


def _rich_fixture(text: Sentinels, profile: Profile) -> Fixture:
    """A case carrying a signal for most measurable gates."""
    fixture = Fixture(text, profile)
    first = fixture.provider()
    work = text()
    accepted, accepted_claim = fixture.record(first, work)
    _sibling, sibling_claim = fixture.record(first, work, lane="work")
    other_work = text()
    other, other_claim = fixture.record(first, other_work, lane="work")
    fixture.bind(first, accepted, work, "accepted")
    fixture.bind(first, other, text(), "unrelated")
    claims = cast(
        "list[object]", cast("dict[str, object]", fixture.report["local"])["claims"]
    )
    claims += [
        _claim(text, "isbn", "malformed"),
        _claim(text, "description", "oversized"),
    ]
    fixture.result(
        "title",
        "unresolved_choice",
        ["competing_evidence"],
        [accepted_claim, other_claim],
    )
    fixture.result(
        "isbn", "defer", ["identifier_applicability_unresolved"], [sibling_claim]
    )
    fixture.result("description", "ineligible", ["no_supported_evidence"])
    fixture.result("subtitle", "ineligible", ["generic_provider_subtitle"])
    fixture.result(
        "cover",
        "unresolved_choice",
        ["cover_gallery_incomplete", "owner_cover_choice_required"],
    )
    fixture.result("series", "keep", ["positive_preservation_evidence"])
    fixture.abstain("title", "insufficient_support")
    fixture.decide_automatically("title", "candidate", text())
    fixture.cover_candidate("unknown", first, other_work, other)
    if profile == "v57-like":
        lanes = cast("list[dict[str, object]]", fixture._providers()[first]["lanes"])
        lanes[0]["diagnostics"] = [
            {"stage": "identity", "category": "work_identity_refused", "count": 2}
        ]
        lanes[1]["dispatch"] = {
            "outcome": "refused",
            "rungs": [],
            "near_miss_seconds": None,
        }
    return fixture


# --- 1. Aggregates only -------------------------------------------------------


def _strings(value: object, keys: list[str], values: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in cast("dict[str, object]", value).items():
            keys.append(key)
            _strings(item, keys, values)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            _strings(item, keys, values)
    elif isinstance(value, str):
        values.append(value)


def _test_vocabulary(labels: Sequence[str]) -> set[str]:
    vocabulary = {value.value for value in TargetDestination}
    vocabulary |= set(shadow_outcome._REASON_CODES) | set(shadow_decision._REASON_CODES)
    vocabulary |= {"audiobook", "ebook", "other_reason", "automatic_safety_suspended"}
    vocabulary |= {"measurable", "gate_postdates_source", "block_not_retained"}
    vocabulary |= {"cause_not_separable", "informational"}
    vocabulary |= {
        gate.unranked_note
        for gate in ledger.CATALOGUE
        if gate.unranked_note is not None
    }
    vocabulary |= {"alternative", "cover_candidates", "not_identity", "not_retained"}
    vocabulary |= {ledger.LEDGER_SCHEMA, *labels}
    for gate in ledger.CATALOGUE:
        vocabulary |= {
            gate.gate_id,
            gate.family,
            gate.gate,
            gate.location,
            gate.origin,
            gate.corpus,
            gate.introduced_in,
            gate.destinations,
            gate.loosened,
            gate.disposition,
        }
    return vocabulary


@pytest.mark.parametrize(
    "profile", ["v26-like", "v57-like"], ids=["v26_like", "v57_like"]
)
def test_output_is_aggregate_only(tmp_path: Path, profile: Profile) -> None:
    text = Sentinels()
    fixtures = [_rich_fixture(text, profile), _rich_fixture(text, profile)]
    # Owner selected values and correlation identifiers are sentinels (#1748).
    for number, fixture in enumerate(fixtures):
        title = next(item for item in fixture.results if item["destination"] == "title")
        alternatives = cast("list[dict[str, object]]", title["alternatives"])
        for alternative in alternatives:
            alternative["correlations"] = [text(), text()]
        fixture.decide(number + 1, "title", "accept", alternatives[0]["value"])
        fixture.decide(number + 10, "series", "owner_entered", {"value": text()})
    database = _database(tmp_path / "application.sqlite3", profile, fixtures)
    probe = ledger.AttentionProbe(
        ((0, "title", ("competing_evidence",), "ebook"),), 1, 1
    )
    for output_format in ("json", "markdown"):
        code, stdout, stderr = _run(
            _arguments(
                [("snap-a", database, EBOOK_REVISION)],
                tmp_path,
                "--format",
                output_format,
                "--breakdown",
                "T1",
            ),
            attention=lambda _root, _db: probe,
        )
        assert code == 0, stderr
        assert _SENTINEL not in stdout and _SENTINEL not in stderr
        assert str(tmp_path) not in stdout
    assert "## Breakdown: T1" in stdout
    code, stdout, _ = _run(
        _arguments(
            [("snap-a", database, EBOOK_REVISION)], tmp_path, "--breakdown", "T1"
        ),
        attention=lambda _root, _db: probe,
    )
    assert json.loads(stdout)["breakdown"] == "T1"
    assert _rows(stdout)["T1"]["split"] is not None
    # The decisions were read, so the check above covers their values.
    owner = cast("dict[str, object]", _snapshot(_rows(stdout)["T1"], "snap-a")["owner"])
    assert owner["labelled"] == 2
    # The automatic decisions were read, and only their effect is counted.
    assert _snapshot(_rows(stdout)["T1"], "snap-a")["automatic"] == {
        "takes_pick": 0,
        "keeps_current": 0,
        "takes_other": 2,
    }
    keys: list[str] = []
    values: list[str] = []
    _strings(json.loads(stdout), keys, values)
    vocabulary = _test_vocabulary(["snap-a"])
    reasons = {
        reason
        for sets in _OUTCOME_REASON_SETS.values()
        for item in sets
        for reason in item
    }
    assert {
        value for value in values if value not in vocabulary and value not in reasons
    } <= {EBOOK_REVISION}
    assert set(keys) <= ledger._STRUCTURAL_KEYS | vocabulary | reasons


def test_an_attention_string_outside_the_vocabulary_refuses_the_output(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "application.sqlite3", "v26-like", [])
    leaked = ledger.AttentionProbe(((0, f"{_SENTINEL}9999", (), "ebook"),), 1, 1)
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
        attention=lambda _root, _db: leaked,
    )
    assert code == 2 and stdout == ""
    assert _SENTINEL not in stderr and len(stderr.splitlines()) == 1


def test_an_unknown_attention_reason_is_counted_as_other(tmp_path: Path) -> None:
    database = _database(tmp_path / "application.sqlite3", "v26-like", [])
    probe = ledger.AttentionProbe(((0, "title", (f"{_SENTINEL}0001",), "ebook"),), 1, 1)
    code, stdout, _ = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
        attention=lambda _root, _db: probe,
    )
    assert code == 0 and _SENTINEL not in stdout
    attention = json.loads(stdout)["snapshots"][0]["attention"]
    assert attention["by_reason"] == {"other_reason": 1}


# --- 2. and 3. Not measurable ------------------------------------------------


def test_a_gate_the_source_predates_reports_no_counts(tmp_path: Path) -> None:
    text = Sentinels()
    database = _database(
        tmp_path / "a.sqlite3", "v26-like", [_rich_fixture(text, "v26-like")]
    )
    later = next(
        gate for gate in ledger.CATALOGUE if gate.gate_id == "I1"
    ).introduced_in

    def ancestry(commit: str, _revision: str) -> bool:
        return commit != later

    code, stdout, _ = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path), ancestry=ancestry
    )
    assert code == 0
    entry = _snapshot(_rows(stdout)["I1"], "snap-a")
    # The fixture carries I1's malformed-ISBN signal; nothing is computed for it.
    assert entry == {"label": "snap-a", "status": "gate_postdates_source"}
    assert _snapshot(_rows(stdout)["I3"], "snap-a")["status"] == "measurable"


def test_an_unretained_block_is_reported_on_both_profiles(tmp_path: Path) -> None:
    text = Sentinels()
    ebook = _database(
        tmp_path / "e.sqlite3", "v26-like", [_rich_fixture(text, "v26-like")]
    )
    audio = _database(
        tmp_path / "a.sqlite3", "v57-like", [_rich_fixture(text, "v57-like")]
    )
    code, stdout, _ = _run(
        _arguments(
            [("ebook", ebook, EBOOK_REVISION), ("audio", audio, AUDIO_REVISION)],
            tmp_path,
        )
    )
    assert code == 0
    for gate_id in ("I2", "W4"):
        row = _rows(stdout)[gate_id]
        assert [
            entry["status"]
            for entry in cast("list[dict[str, object]]", row["snapshots"])
        ] == [
            "block_not_retained",
            "block_not_retained",
        ]
        assert row["ceiling"] == 0 and row["score"] == 0.0


# --- 4. Empty snapshot -----------------------------------------------------


@pytest.mark.parametrize(
    "profile", ["v26-like", "v57-like"], ids=["v26_like", "v57_like"]
)
def test_an_empty_snapshot_reports_zero_everywhere(
    tmp_path: Path, profile: Profile
) -> None:
    database = _database(tmp_path / "empty.sqlite3", profile, [])
    code, stdout, _ = _run(_arguments([("empty", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    for row in _rows(stdout).values():
        entry = _snapshot(row, "empty")
        if entry["status"] == "measurable":
            assert entry["fields"] == 0 and entry["works"] == 0
        pooled = cast("dict[str, object]", row["pooled"])
        assert pooled["precision"] is None and pooled["wilson_lower_bound"] is None
        assert row["score"] == 0.0


# --- 5. Counting -----------------------------------------------------------


def test_blocked_fields_count_per_gate_destination_and_work(tmp_path: Path) -> None:
    text = Sentinels()
    first = Fixture(text, "v26-like")
    first.result("title", "unresolved_choice", ["competing_evidence"])
    first.result("authors", "unresolved_choice", ["competing_evidence"])
    first.abstain("title", "insufficient_support")
    first.result("isbn", "defer", ["identifier_applicability_unresolved"])
    second = Fixture(text, "v26-like")
    second.result("title", "unresolved_choice", ["competing_evidence"])
    # A settled field is not blocked, whatever reason it states.
    second.result("subtitle", "keep", ["competing_evidence"])
    # The product never shows these on an EPUB.
    second.result("narrators", "unresolved_choice", ["competing_evidence"])
    third = Fixture(text, "v26-like")
    third.result("title", "propose_change", ["unique_typed_candidate"])
    database = _database(tmp_path / "c.sqlite3", "v26-like", [first, second, third])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    rows = _rows(stdout)
    competing = _snapshot(rows["T1"], "snap-a")
    assert (competing["fields"], competing["works"]) == (3, 2)
    assert competing["by_destination"] == {"authors": 1, "title": 2}
    assert competing["by_medium"] == {"ebook": 3}
    # Non-exclusive: the first case's title counts under T1 and A6a alike.
    abstained = _snapshot(rows["A6a"], "snap-a")
    assert (abstained["fields"], abstained["by_destination"]) == (1, {"title": 1})
    unresolved = _snapshot(rows["I3"], "snap-a")
    assert (unresolved["fields"], unresolved["works"]) == (1, 1)
    assert json.loads(stdout)["snapshots"][0]["current_cases"] == {"ebook": 3}


def test_report_level_signals_block_their_destinations(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = _rich_fixture(text, "v57-like")
    database = _database(tmp_path / "r.sqlite3", "v57-like", [fixture])
    code, stdout, _ = _run(_arguments([("audio", database, AUDIO_REVISION)], tmp_path))
    assert code == 0
    rows = _rows(stdout)
    # Malformed local ISBN blocks only the isbn field; the refused work identity
    # and the refused recording ladder block every blocked field the lanes feed.
    assert _snapshot(rows["I1"], "audio")["by_destination"] == {"isbn": 1}
    lane_blocked = {"cover": 1, "description": 1, "isbn": 1, "subtitle": 1, "title": 1}
    assert _snapshot(rows["W1"], "audio")["by_destination"] == lane_blocked
    assert _snapshot(rows["W6"], "audio") == {
        "label": "audio",
        "status": "cause_not_separable",
    }
    assert _snapshot(rows["B1"], "audio")["by_destination"] == {"description": 1}


def test_rules_that_refuse_nothing_or_cannot_be_separated_count_nothing(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    fixture = _rich_fixture(text, "v57-like")
    # Every persisted trace a deadline stop leaves, none of which says whether
    # the provider share caused it.
    provider = fixture._providers()[0]
    provider["request_accounting"] = {"undispatched_incomplete": 2}
    provider["diagnostics"] = [
        {"stage": "request", "category": "timeout", "retryable": False, "count": 1}
    ]
    database = _database(tmp_path / "s.sqlite3", "v57-like", [fixture])
    code, stdout, _ = _run(_arguments([("audio", database, AUDIO_REVISION)], tmp_path))
    assert code == 0
    rows = _rows(stdout)
    deadline = _snapshot(rows["B6"], "audio")
    assert deadline == {"label": "audio", "status": "cause_not_separable"}
    assert rows["B6"]["ceiling"] == 0
    # An unknown audiobook cover is not the corroboration rule's cost: the rule
    # only adds reasons and the classifier never reads them.
    corroboration = _snapshot(rows["C3"], "audio")
    assert corroboration["status"] == "measurable"
    assert (corroboration["fields"], corroboration["actionable_fields"]) == (0, None)
    assert rows["C3"]["control"] == "informational"
    assert _snapshot(rows["C1"], "audio")["fields"] == 1
    markdown = _run(
        _arguments(
            [("audio", database, AUDIO_REVISION)], tmp_path, "--format", "markdown"
        )
    )[1]
    assert "cause not separable" in markdown
    assert "the rule adds reasons and refuses nothing" in markdown


# --- 6. Precision ----------------------------------------------------------


def _wilson_reference(successes: int, total: int) -> float:
    """The Wilson lower limit as the root of its defining quadratic, at z = 1.959964."""
    z = 1.959964
    phat = successes / total
    a = 1 + z * z / total
    b = -(2 * phat + z * z / total)
    c = phat * phat
    return (-b - math.sqrt(b * b - 4 * a * c)) / (2 * a)


def test_controls_give_precision_and_the_wilson_lower_limit(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    bound, unbound = fixture.provider(), fixture.provider()
    work = text()
    accepted, accepted_claim = fixture.record(bound, work)
    _same_work, same_claim = fixture.record(bound, work)
    _third, third_claim = fixture.record(bound, work)
    _other, other_claim = fixture.record(bound, text())
    _unknown, unknown_claim = fixture.record(unbound, text())
    # No work identity at a provider with an accepted work: unlabelled, not
    # negative, because nothing says it is a different work.
    _workless, workless_claim = fixture.record(bound, "")
    fixture.bind(bound, accepted, work, "accepted")
    fixture.result(
        "title",
        "unresolved_choice",
        ["competing_evidence"],
        [
            accepted_claim,
            same_claim,
            third_claim,
            other_claim,
            unknown_claim,
            workless_claim,
            "local_" + text(),
        ],
    )
    database = _database(tmp_path / "p.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    row = _rows(stdout)["T1"]
    entry = _snapshot(row, "snap-a")
    assert (entry["true_positive"], entry["false_positive"], entry["unlabelled"]) == (
        3,
        1,
        2,
    )
    pooled = cast("dict[str, object]", row["pooled"])
    assert pooled["n"] == 4 and pooled["precision"] == 0.75
    assert pooled["wilson_lower_bound"] == round(_wilson_reference(3, 4), 4)
    assert row["score"] == round(1 * _wilson_reference(3, 4), 1)


def _competing(
    fixture: Fixture,
    destination: str,
    cited: Sequence[tuple[str, str | Sequence[str]]],
    *,
    preserves: str | None = None,
    correlations: Mapping[str, Sequence[str]] | None = None,
    outcome: str = "unresolved_choice",
    reasons: Sequence[str] = ("competing_evidence",),
) -> dict[str, dict[str, object]]:
    """A competing result whose alternatives are (alternative_id, claims) pairs,
    stored in the given order; ``preserves`` names the alternative that keeps
    the current value and ``correlations`` each alternative's sources. Returns
    the alternatives by identifier."""
    alternatives: list[dict[str, object]] = [
        {
            "alternative_id": alternative,
            "value": {"kind": "text", "value": fixture.text()},
            "evidence_references": [claims]
            if isinstance(claims, str)
            else list(claims),
            "assessment": "preserves_target"
            if alternative == preserves
            else "replaces_target",
            "correlations": list((correlations or {}).get(alternative, ())),
        }
        for alternative, claims in cited
    ]
    fixture.results.append(
        {
            "destination": destination,
            "outcome": outcome,
            "reasons": list(reasons),
            "alternatives": alternatives,
            "selected_alternative_id": None,
        }
    )
    return {cast("str", item["alternative_id"]): item for item in alternatives}


def test_the_pick_is_the_lowest_identifier_and_is_judged_per_field(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, right_claim = fixture.record(provider, work)
    _same, same_claim = fixture.record(provider, work)
    _wrong, wrong_claim = fixture.record(provider, text())
    fixture.bind(provider, accepted, work, "accepted")
    # Stored out of identifier order: the pick is still the lowest identifier,
    # here the wrong-work alternative, so the controls decide against it.
    _competing(
        fixture,
        "title",
        [("alternative_b" + text(), right_claim), ("alternative_a", wrong_claim)],
    )
    # The lowest identifier is the one right-work alternative: the pick agrees.
    _competing(
        fixture,
        "series",
        [("alternative_a", right_claim), ("alternative_b", wrong_claim)],
    )
    # Two right-work alternatives: the controls cannot choose between them.
    _competing(
        fixture,
        "description",
        [("alternative_a", right_claim), ("alternative_b", same_claim)],
    )
    database = _database(tmp_path / "k.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    row = _rows(stdout)["T1"]
    entry = _snapshot(row, "snap-a")
    # Identity precision counts the picked records: wrong, right, right.
    assert (entry["true_positive"], entry["false_positive"]) == (2, 1)
    assert (entry["decidable_fields"], entry["pick_agrees"], entry["tied_fields"]) == (
        2,
        1,
        1,
    )
    pick = cast("dict[str, object]", row["pick"])
    assert pick["rate"] == 0.5
    assert pick["wilson_lower_bound"] == round(_wilson_reference(1, 2), 4)


def test_t1_leaves_competing_covers_to_cover_election(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    _competing(fixture, "cover", [("alternative_a", text()), ("alternative_b", text())])
    _competing(fixture, "title", [("alternative_a", text()), ("alternative_b", text())])
    database = _database(tmp_path / "t.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    assert _snapshot(_rows(stdout)["T1"], "snap-a")["by_destination"] == {"title": 1}


def test_fields_a_later_election_decides_are_marked_stale(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    _competing(fixture, "title", [("alternative_a", text()), ("alternative_b", text())])
    _competing(
        fixture, "series", [("alternative_a", text()), ("alternative_b", text())]
    )
    database = _database(tmp_path / "e.sqlite3", "v26-like", [fixture])
    title_elections = {
        commit
        for label, commit, destinations in ledger.LATER_ELECTIONS
        if "title" in destinations
    }

    def ancestry(commit: str, _revision: str) -> bool:
        return commit not in title_elections

    arguments = _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path)
    code, stdout, _ = _run(arguments, ancestry=ancestry)
    assert code == 0
    loaded = json.loads(stdout)
    assert loaded["snapshots"][0]["later_elections"] == ["#974", "#981", "#1040"]
    entry = _snapshot(_rows(stdout)["T1"], "snap-a")
    assert (entry["fields"], entry["election_fields"]) == (2, 1)
    markdown = _run([*arguments, "--format", "markdown"], ancestry=ancestry)[1]
    assert "2 / 1 (2 actionable; 1 stale: later election)" in markdown
    assert "- snap-a: source predates #974, #981, #1040." in markdown
    # Every election is in the source by default: nothing is marked.
    default = _snapshot(_rows(_run(arguments)[1])["T1"], "snap-a")
    assert default["election_fields"] == 0


def _engine_probe(scalar: Sequence[str], *, rich_split: bool) -> ledger.AttentionReader:
    def probe(_root: Path, _database: Path) -> ledger.AttentionProbe:
        return ledger.AttentionProbe((), 0, 0, tuple(scalar), rich_split)

    return probe


def _a6_split_fixture(text: Sentinels) -> Fixture:
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, claim = fixture.record(provider, work)
    fixture.bind(provider, accepted, work, "accepted")
    # Scalar, the decision's own reason: A6a.
    fixture.result("title", "defer", ["insufficient_support"], [claim])
    fixture.abstain("title", "insufficient_support")
    # Scalar, an echo of the proposal set's refusal: not A6a (#1745).
    fixture.result("publisher", "defer", ["no_positive_preservation_evidence"])
    fixture.abstain("publisher", "evidence_sufficiency_manual")
    # Not scalar for this source: the rich residual, A6b.
    fixture.result("series", "defer", ["no_supported_evidence"], [claim])
    fixture.abstain("series", "no_eligible_action")
    # A proposed field the decision abstained on is not blocked.
    fixture.result("language", "propose_change", ["unique_typed_candidate"], [claim])
    fixture.abstain("language", "insufficient_support")
    return fixture


@pytest.mark.parametrize(
    ("rich_split", "a6b_status"),
    [(True, "measurable"), (False, "rich_refusals_collapsed")],
    ids=["rich-reasons-split", "rich-reasons-collapsed"],
)
def test_a6_splits_scalar_abstention_from_the_rich_residual(
    tmp_path: Path, rich_split: bool, a6b_status: str
) -> None:
    database = _database(
        tmp_path / "m.sqlite3", "v26-like", [_a6_split_fixture(Sentinels())]
    )
    arguments = _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path)
    probe = _engine_probe(
        ["title", "authors", "publisher", "language"], rich_split=rich_split
    )
    code, stdout, _ = _run(arguments, attention=probe)
    assert code == 0
    rows = _rows(stdout)
    scalar = _snapshot(rows["A6a"], "snap-a")
    # Only the source's scalar destinations, only the decision's own reasons.
    assert (scalar["fields"], scalar["by_destination"]) == (1, {"title": 1})
    assert "echo_fields" not in scalar
    assert scalar["true_positive"] == 1
    residual = _snapshot(rows["A6b"], "snap-a")
    assert residual["status"] == a6b_status
    if rich_split:
        assert residual["by_destination"] == {"series": 1}
    else:
        assert "fields" not in residual
    for gate_id in ("A6a", "A6b"):
        row = rows[gate_id]
        assert (row["rank"], row["score"], row["proposed_follow_on"]) == (
            None,
            0.0,
            False,
        )
        assert row["disposition"] == "kept (ADR-0092)"
    markdown = _run([*arguments, "--format", "markdown"], attention=probe)[1]
    assert "upstream echo" not in markdown
    if not rich_split:
        assert "not separable: rich-preview refusals collapsed" in markdown


def test_a_scalar_destination_set_outside_the_vocabulary_refuses(
    tmp_path: Path,
) -> None:
    database = _database(
        tmp_path / "m.sqlite3", "v26-like", [Fixture(Sentinels(), "v26-like")]
    )
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
        attention=_engine_probe([f"{_SENTINEL}9999"], rich_split=True),
    )
    assert (code, stdout) == (2, "")
    assert (
        stderr
        == "admission-rule-ledger: attention probe returned an unknown vocabulary\n"
    )


def test_wilson_lower_bound_matches_the_closed_form() -> None:
    for successes, total in ((3, 4), (19, 19), (808, 851), (0, 5)):
        bound = ledger.wilson_lower_bound(successes, total)
        assert bound is not None
        assert math.isclose(
            bound, max(0.0, _wilson_reference(successes, total)), abs_tol=1e-9
        )
    assert ledger.wilson_lower_bound(0, 0) is None
    # Two-sided 95%: z is the 97.5th percentile, not the one-sided 95th.
    assert math.isclose(ledger.WILSON_Z, NormalDist().inv_cdf(0.975), abs_tol=1e-6)


def test_the_unresolved_work_sentinel_names_no_shared_work(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    accepted, accepted_claim = fixture.record(provider, "unresolved")
    _sibling, sibling_claim = fixture.record(provider, "unresolved")
    _named, named_claim = fixture.record(provider, text())
    # An explicit binding still labels its own record.
    fixture.bind(provider, accepted, "unresolved", "accepted")
    fixture.result(
        "title",
        "unresolved_choice",
        ["competing_evidence"],
        [accepted_claim, sibling_claim, named_claim],
    )
    database = _database(tmp_path / "u.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    entry = _snapshot(_rows(stdout)["T1"], "snap-a")
    # The other sentinel record is not the accepted work, and a named work is
    # not a different one: both stay unlabelled.
    assert (entry["true_positive"], entry["false_positive"], entry["unlabelled"]) == (
        1,
        0,
        2,
    )


def test_a_cover_binds_its_edition_under_the_unresolved_work_sentinel(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    edition, _ = fixture.record(provider, "unresolved")
    fixture.record(provider, "unresolved")
    fixture.bind(provider, edition, "unresolved", "accepted")
    # The classifier matches the provenance's work identity by equality, the
    # sentinel included, so the cover binds the edition it names (#1350 R2-1).
    fixture.cover_candidate("unknown", provider, "unresolved", edition)
    fixture.result(
        "cover",
        "unresolved_choice",
        ["cover_gallery_incomplete", "owner_cover_choice_required"],
    )
    database = _database(tmp_path / "s.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    classifier = _snapshot(_rows(stdout)["C1"], "snap-a")
    assert (classifier["true_positive"], classifier["false_positive"]) == (1, 0)
    assert (classifier["unlabelled"], classifier["unreached_fields"]) == (0, 0)


def test_a_cover_alternative_reaches_records_through_its_cover_sources(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, _ = fixture.record(provider, work)
    fixture.bind(provider, accepted, work, "accepted")
    source = fixture.cover_candidate("unknown", provider, work, accepted)
    # The proposal's cover alternative cites report cover source ids.
    fixture.result("cover", "unresolved_choice", ["cover_gallery_incomplete"], [source])
    database = _database(tmp_path / "a.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    gallery = _snapshot(_rows(stdout)["A5"], "snap-a")
    assert (gallery["true_positive"], gallery["unreached_fields"]) == (1, 0)


def test_cover_candidates_reach_the_records_their_covers_show(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, _ = fixture.record(provider, work)
    other_work = text()
    other, _ = fixture.record(provider, other_work, lane="work")
    fixture.bind(provider, accepted, work, "accepted")
    fixture.cover_candidate("unknown", provider, work, accepted)
    fixture.cover_candidate("conflict", provider, other_work, other)
    fixture.cover_candidate("verified_work_match", provider, work, accepted)
    # Provenance naming an edition of another work binds nothing, as in the
    # classifier, which matches the work identity before the edition.
    fixture.cover_candidate("unknown", provider, other_work, accepted)
    fixture.result(
        "cover",
        "unresolved_choice",
        ["cover_gallery_incomplete", "owner_cover_choice_required"],
    )
    database = _database(tmp_path / "v.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    classifier = _snapshot(_rows(stdout)["C1"], "snap-a")
    assert (classifier["true_positive"], classifier["false_positive"]) == (1, 1)
    # Only the conflict record came through work search alone, and C2 asks for unknown.
    assert _snapshot(_rows(stdout)["C2"], "snap-a")["fields"] == 0


def test_a_work_level_cover_reaches_every_record_of_its_work(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    second_provider = fixture.provider()
    work = text()
    accepted, _ = fixture.record(provider, work, lane="work")
    fixture.record(provider, work, lane="work")
    fixture.record(provider, text(), lane="work")
    fixture.record(second_provider, work, lane="work")
    fixture.bind(provider, accepted, work, "accepted")
    # A default or work cover names its work and no edition.
    fixture.cover_candidate("unknown", provider, work, None)
    fixture.result(
        "cover",
        "unresolved_choice",
        ["cover_gallery_incomplete", "owner_cover_choice_required"],
    )
    database = _database(tmp_path / "w.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    rows = _rows(stdout)
    # Both records of that work at that provider are reached and positive;
    # the other work and the other provider's record are not reached.
    classifier = _snapshot(rows["C1"], "snap-a")
    assert (classifier["true_positive"], classifier["false_positive"]) == (2, 0)
    assert (classifier["unlabelled"], classifier["unreached_fields"]) == (0, 0)
    # Every record it pictures came by work search alone, so C2 counts it.
    work_search = _snapshot(rows["C2"], "snap-a")
    assert (work_search["fields"], work_search["true_positive"]) == (1, 2)


def test_a_listed_edition_cover_reaches_every_record_of_its_book(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, _ = fixture.record(provider, work, lane="work")
    fixture.record(provider, work, lane="work")
    fixture.record(provider, text(), lane="work")
    fixture.bind(provider, accepted, work, "accepted")
    # A listed edition names an edition that is no record, yet the classifier
    # binds it by its book's work identity alone (#1380).
    fixture.cover_candidate(
        "unknown", provider, work, text(), designation="listed_edition"
    )
    fixture.result(
        "cover",
        "unresolved_choice",
        ["cover_gallery_incomplete", "owner_cover_choice_required"],
    )
    database = _database(tmp_path / "l.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    classifier = _snapshot(_rows(stdout)["C1"], "snap-a")
    assert (classifier["true_positive"], classifier["false_positive"]) == (2, 0)


# --- 7. Ranking ------------------------------------------------------------


def _ranking_row(
    gate_id: str, score: float, ceiling: int, *, ranked: bool = True
) -> dict[str, object]:
    return {
        "gate_id": gate_id,
        "score": score,
        "ceiling": ceiling,
        "ranked": ranked,
        "disposition": "kept",
        "proposed_follow_on": False,
    }


def test_ranking_orders_scored_then_unscored_then_out_of_scope() -> None:
    rows = [
        _ranking_row("A7", 0.0, 900, ranked=False),
        _ranking_row("B1", 0.0, 50),
        _ranking_row("T1", 20.5, 30),
        _ranking_row("A2", 0.0, 50),
        _ranking_row("I3", 99.0, 120),
        _ranking_row("X1", 0.0, 400),
    ]
    ordered = ledger.rank_rows(rows)
    assert [row["gate_id"] for row in ordered] == ["I3", "T1", "X1", "A2", "B1", "A7"]
    assert [row["rank"] for row in ordered] == [1, 2, 3, 4, 5, None]
    assert [row["gate_id"] for row in ordered if row["proposed_follow_on"]] == [
        "I3",
        "T1",
    ]


def test_five_follow_ons_skip_rows_already_filed() -> None:
    rows = [_ranking_row(f"T{index}", float(10 - index), 1) for index in range(1, 8)]
    rows[0]["disposition"] = "already filed as #1355"
    ordered = ledger.rank_rows(rows)
    assert [row["gate_id"] for row in ordered if row["proposed_follow_on"]] == [
        "T2",
        "T3",
        "T4",
        "T5",
        "T6",
    ]


# --- 8. Read-only ----------------------------------------------------------


def test_the_snapshot_is_never_written(tmp_path: Path) -> None:
    text = Sentinels()
    database = _database(
        tmp_path / "ro.sqlite3", "v57-like", [_rich_fixture(text, "v57-like")]
    )
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    siblings = sorted(path.name for path in tmp_path.iterdir())
    code, _, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == siblings


def test_a_non_empty_write_ahead_log_refuses(tmp_path: Path) -> None:
    database = _database(tmp_path / "wal.sqlite3", "v26-like", [])
    (tmp_path / "wal.sqlite3-wal").write_bytes(b"\x00" * 32)
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path)
    )
    assert (code, stdout) == (2, "")
    assert len(stderr.splitlines()) == 1 and str(tmp_path) not in stderr


# --- 9. Refusals -----------------------------------------------------------


def _git_repository(path: Path) -> Path:
    path.mkdir()
    for command in (
        ["init", "-q"],
        [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
    ):
        subprocess.run(
            ["git", "-C", str(path), *command], check=True, capture_output=True
        )
    return path


def _refused(stderr: str, tmp_path: Path) -> None:
    lines = stderr.splitlines()
    assert len(lines) == 1 and lines[0].startswith("admission-rule-ledger: ")
    assert _SENTINEL not in stderr and str(tmp_path) not in stderr


@pytest.mark.parametrize(
    "label",
    ["Upper", "-leading", "a" * 33, "with space"],
    ids=["upper", "leading", "long", "space"],
)
def test_a_bad_label_refuses_before_opening_anything(
    tmp_path: Path, label: str
) -> None:
    code, stdout, stderr = _run(
        _arguments([(label, tmp_path / "missing.sqlite3", EBOOK_REVISION)], tmp_path)
    )
    assert (code, stdout) == (2, "")
    _refused(stderr, tmp_path)


def test_a_missing_database_refuses(tmp_path: Path) -> None:
    code, stdout, stderr = _run(
        _arguments(
            [("snap-a", tmp_path / f"{_SENTINEL}missing.sqlite3", EBOOK_REVISION)],
            tmp_path,
        )
    )
    assert (code, stdout) == (2, "")
    _refused(stderr, tmp_path)


def test_an_unknown_revision_refuses(tmp_path: Path) -> None:
    database = _database(tmp_path / "u.sqlite3", "v26-like", [])
    repository = _git_repository(tmp_path / "repository")
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ledger.main(
        _arguments(
            [("snap-a", database, EBOOK_REVISION)],
            tmp_path,
            "--repository",
            str(repository),
        ),
        stdout=stdout,
        stderr=stderr,
        attention=_no_attention,
    )
    assert (code, stdout.getvalue()) == (2, "")
    assert stderr.getvalue() == "admission-rule-ledger: unknown revision\n"


def test_a_shallow_history_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path / "s.sqlite3", "v26-like", [])

    def shallow(
        arguments: list[str], **_options: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(arguments, 0, "true\n", "")

    monkeypatch.setattr(ledger.subprocess, "run", shallow)
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ledger.main(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
        stdout=stdout,
        stderr=stderr,
        attention=_no_attention,
    )
    assert (code, stdout.getvalue()) == (2, "")
    assert (
        stderr.getvalue()
        == "admission-rule-ledger: repository history is shallow or unreadable\n"
    )


def test_a_short_revision_and_a_missing_source_root_refuse(tmp_path: Path) -> None:
    database = _database(tmp_path / "x.sqlite3", "v26-like", [])
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, "1203cf58")], tmp_path)
    )
    assert (code, stdout) == (2, "")
    _refused(stderr, tmp_path)
    code, stdout, stderr = _run(["--snapshot", "snap-a", str(database), EBOOK_REVISION])
    assert (code, stdout) == (2, "")
    _refused(stderr, tmp_path)


def test_an_unrecognised_database_refuses(tmp_path: Path) -> None:
    database = tmp_path / "other.sqlite3"
    sqlite3.connect(database).close()
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path)
    )
    assert (code, stdout) == (2, "")
    assert stderr == "admission-rule-ledger: snapshot schema unrecognised\n"


# --- 10. Attention child ---------------------------------------------------


def _per_case_reference(database: Path) -> dict[str, dict[str, dict[str, int]]]:
    connection = cold_start._open_read_only(database)
    try:
        rows = cold_start._attention_field_rows(
            connection, sql=cold_start._STORED_ATTENTION_FIELD_ROWS_SQL
        )
    finally:
        connection.close()
    per_case = cold_start._attention_per_case(rows)
    totals: dict[str, dict[str, dict[str, int]]] = {
        medium: {
            basis: {"fields": 0, "cases": 0}
            for basis in ("with_covers", "without_covers")
        }
        for medium in ("audiobook", "ebook")
    }
    for by_medium in per_case.values():
        for medium, bases in cast(
            "dict[str, dict[str, dict[str, int]]]", by_medium
        ).items():
            for basis, counts in bases.items():
                totals[medium][basis]["fields"] += counts["fields"]
                totals[medium][basis]["cases"] += counts["cases"]
    return totals


def test_the_attention_child_matches_the_cold_start_report_on_an_empty_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    probe = ledger.child_attention(REPOSITORY, database)
    summary = ledger.summarise_attention(probe)
    assert summary["media"] == _per_case_reference(database)
    assert (summary["fields"], summary["cases"], probe.report_rows) == (0, 0, 0)


def test_the_attention_child_drops_a_cover_only_case_without_covers(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    attention_work = _bibliographic_report(
        current_title="Quenlorax Secret Ledger",
        providers=(
            _bibliographic_source(
                "catalogue", "edition-1", titles=(), series="Quenlorax"
            ),
        ),
    )
    repository.publish(attention_work, lambda: True)
    proposal = evaluate_report(attention_work)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    _publish_current_cover_consideration(
        repository,
        root=tmp_path / "cover",
        connection="primary",
        library="ebooks",
        item="private-cover-item",
        identity="c",
    )
    repository.close()
    probe = ledger.child_attention(REPOSITORY, database)
    summary = ledger.summarise_attention(probe)
    reference = _per_case_reference(database)
    assert summary["media"] == reference
    assert reference["ebook"] == {
        "with_covers": {"fields": 2, "cases": 2},
        "without_covers": {"fields": 1, "cases": 1},
    }
    assert probe.report_rows == probe.report_rows_excluded == len(probe.rows) == 2
    assert summary["by_destination"] == {"cover": 1, "series": 1}
    serialized = json.dumps(summary)
    assert "private-cover-item" not in serialized and "Quenlorax" not in serialized


def test_a_source_root_without_a_tree_refuses(tmp_path: Path) -> None:
    with pytest.raises(ledger.Refusal) as refused:
        ledger.child_attention(tmp_path, tmp_path / "application.sqlite3")
    assert refused.value.message == "source root is not an exported source tree"


# --- 11. Reconciliation ----------------------------------------------------


def _probe(_root: Path, _database: Path) -> ledger.AttentionProbe:
    rows = (
        (0, "title", ("competing_evidence",), "ebook"),
        (1, "cover", (), "audiobook"),
    )
    # The report query counted one EPUB narrator field the per-case read drops.
    return ledger.AttentionProbe(rows, 3, 2)


def _stored_report(fields: int, works: int) -> dict[str, object]:
    return {
        "libraries": [
            {
                "attention_stored_basis": {
                    "cover_fields_with_owner_actions": 1,
                    "non_cover_fields": {"total": fields - 1},
                    "works": works,
                }
            }
        ]
    }


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (_stored_report(3, 2), 0),
        (
            {
                "libraries": [{"attention_fields": {"total": 3}}],
                "totals": {"attention_works": 2},
            },
            0,
        ),
        (_stored_report(4, 2), 1),
        (_stored_report(3, 3), 1),
    ],
    ids=["stored_agrees", "legacy_agrees", "fields_disagree", "works_disagree"],
)
def test_attention_reconciles_with_the_source_report(
    tmp_path: Path, report: dict[str, object], expected: int
) -> None:
    database = _database(tmp_path / "rec.sqlite3", "v26-like", [])
    source_report = tmp_path / "report.json"
    source_report.write_text(json.dumps(report), encoding="utf-8")
    code, stdout, stderr = _run(
        _arguments(
            [("snap-a", database, EBOOK_REVISION)],
            tmp_path,
            "--source-report",
            "snap-a",
            str(source_report),
        ),
        attention=_probe,
    )
    assert code == expected
    if expected:
        assert stdout == ""
        assert (
            stderr
            == "admission-rule-ledger: attention does not reconcile with the source report\n"
        )
    else:
        snapshot = json.loads(stdout)["snapshots"][0]
        assert snapshot["source_report_reconciled"] is True
        assert snapshot["attention"]["epub_inapplicable_fields"] == 1


def test_a_probe_that_disagrees_with_the_report_query_refuses(tmp_path: Path) -> None:
    database = _database(tmp_path / "d.sqlite3", "v26-like", [])
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
        attention=lambda _root, _db: ledger.AttentionProbe(
            ((0, "title", (), "ebook"),), 3, 2
        ),
    )
    assert (code, stdout) == (2, "")
    assert (
        stderr
        == "admission-rule-ledger: attention probe disagrees with the source's report query\n"
    )


# --- 12. Markdown ----------------------------------------------------------


def test_markdown_renders_rows_in_rank_order(tmp_path: Path) -> None:
    text = Sentinels()
    ebook = _database(
        tmp_path / "e.sqlite3", "v26-like", [_rich_fixture(text, "v26-like")]
    )
    audio = _database(
        tmp_path / "a.sqlite3", "v57-like", [_rich_fixture(text, "v57-like")]
    )
    snapshots = [("ebook", ebook, EBOOK_REVISION), ("audio", audio, AUDIO_REVISION)]
    code, stdout, _ = _run(_arguments(snapshots, tmp_path))
    assert code == 0
    order = [cast("str", row["gate_id"]) for row in json.loads(stdout)["rows"]]
    code, markdown, _ = _run(
        _arguments(
            snapshots, tmp_path, "--format", "markdown", "--follow-on", "T1=4242"
        )
    )
    assert code == 0
    lines = markdown.splitlines()
    header = next(line for line in lines if line.startswith("| Rank |"))
    assert "| ebook fields / works | audio fields / works |" in header
    assert "| Identity precision (Wilson 95% lower) |" in header
    assert "| Pick agrees where controls decide (Wilson 95% lower) |" in header
    table = [
        line
        for line in lines
        if line.startswith("| ") and line.split(" | ")[1] in order
    ]
    assert [line.split(" | ")[1] for line in table] == order
    assert all(line.count(" | ") == header.count(" | ") for line in table)
    assert table[-1].startswith("| out of scope | A7 |")
    t1 = next(line for line in table if line.split(" | ")[1] == "T1")
    assert t1.endswith("| filed as #4242 |")
    assert t1.count("(1 actionable; 1 decided automatically)") == 2
    assert "## Attention per case" in lines


def test_a_bad_follow_on_refuses(tmp_path: Path) -> None:
    database = _database(tmp_path / "f.sqlite3", "v26-like", [])
    for value in ("Z9=12", "T1=0", "T1=x"):
        code, stdout, stderr = _run(
            _arguments(
                [("snap-a", database, EBOOK_REVISION)], tmp_path, "--follow-on", value
            )
        )
        assert (code, stdout) == (2, "")
        assert stderr == "admission-rule-ledger: invalid follow-on\n"


# --- Catalogue -------------------------------------------------------------


def test_every_catalogue_row_has_an_origin_and_a_full_introducing_commit() -> None:
    identifiers = [gate.gate_id for gate in ledger.CATALOGUE]
    assert len(identifiers) == len(set(identifiers))
    for gate in ledger.CATALOGUE:
        assert gate.origin.startswith("#") and gate.corpus
        assert len(gate.introduced_in) == 40 and int(gate.introduced_in, 16) >= 0
        assert gate.introduced_in[:8] in gate.origin
        assert gate.loosened and gate.destinations
    unranked = [gate.gate_id for gate in ledger.CATALOGUE if not gate.ranked]
    assert unranked == ["A6a", "A6b", "A7"]
    # Every unranked row but the out-of-scope one says why.
    assert [
        gate.gate_id for gate in ledger.CATALOGUE if gate.unranked_note is not None
    ] == ["A6a", "A6b"]


def test_a3_stays_ranked_and_kept_with_its_corrected_text() -> None:
    gate = next(gate for gate in ledger.CATALOGUE if gate.gate_id == "A3")
    assert gate.destinations == "title, subtitle and series"
    assert gate.origin == (
        "#429 (cef7644a); ADR-0021; same-name series #1099 (79dbbf4c); "
        "withheld titles #1497 (ce3a172d)"
    )
    assert gate.loosened == (
        "none: the lowest-identifier rule is rejected; closed relaxations in the "
        "A3 analysis"
    )
    assert gate.disposition == "kept (ADR-0097)"
    assert gate.ranked and gate.unranked_note is None
    # A kept row keeps its score but is never proposed as a follow-on.
    row = {**_ranking_row("A3", 315.1, 333), "disposition": gate.disposition}
    (ranked,) = ledger.rank_rows([row])
    assert (ranked["rank"], ranked["proposed_follow_on"]) == (1, False)


def test_mirrored_bounds_match_the_product() -> None:
    assert (
        ledger.MAX_ISBN_DISCOVERY_LANES == application_report.MAX_ISBN_DISCOVERY_LANES
    )


# --- Review fixes ----------------------------------------------------------


def test_a_blocked_field_without_an_alternative_is_not_actionable(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, accepted_claim = fixture.record(provider, work)
    fixture.bind(provider, accepted, work, "accepted")
    fixture.result(
        "title", "unresolved_choice", ["competing_evidence"], [accepted_claim]
    )
    # Blocked by the same gate, but its result kept nothing to admit.
    fixture.result("authors", "unresolved_choice", ["competing_evidence"])
    database = _database(tmp_path / "act.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    row = _rows(stdout)["T1"]
    entry = _snapshot(row, "snap-a")
    assert (entry["fields"], entry["actionable_fields"], entry["unreached_fields"]) == (
        2,
        1,
        1,
    )
    assert (row["ceiling"], row["actionable"]) == (2, 1)
    # The ceiling stays the gate's measured cost; the score counts only the
    # field loosening could unblock.
    assert row["score"] == round(1 * _wilson_reference(1, 1), 1)
    # A row whose refused records were never retained has no actionable count.
    assert _rows(stdout)["X1"]["actionable"] is None
    code, markdown, _ = _run(
        _arguments(
            [("snap-a", database, EBOOK_REVISION)], tmp_path, "--format", "markdown"
        )
    )
    t1 = next(line for line in markdown.splitlines() if line.startswith("| 1 | T1 |"))
    assert "| 2 / 1 (1 actionable) |" in t1


def test_the_attention_child_writes_no_bytecode_into_the_source_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPOSITORY / "src", root / "src", ignore=ignore)
    shutil.copytree(REPOSITORY / "scripts", root / "scripts", ignore=ignore)
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    probe = ledger.child_attention(root, database)
    assert probe.report_rows == 0
    assert not list(root.rglob("__pycache__")) and not list(root.rglob("*.pyc"))


def test_bad_arguments_refuse_without_echoing_them(tmp_path: Path) -> None:
    for arguments in (
        ["--format", f"{_SENTINEL}0001"],
        ["--snapshot", "a", "b", EBOOK_REVISION, f"--{_SENTINEL}0001", str(tmp_path)],
    ):
        code, stdout, stderr = _run(arguments)
        assert (code, stdout) == (2, "")
        assert stderr == "admission-rule-ledger: invalid arguments\n"


def test_an_unexpected_failure_is_a_fixed_line(tmp_path: Path) -> None:
    database = _database(tmp_path / "boom.sqlite3", "v26-like", [])

    def broken(_root: Path, _database: Path) -> ledger.AttentionProbe:
        raise TypeError(f"{_SENTINEL}0002 {tmp_path}")

    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path), attention=broken
    )
    assert (code, stdout) == (2, "")
    assert stderr == "admission-rule-ledger: replay failed\n"


# --- Per-destination and field-level breakdown (#1744) ---------------------

_SINGLE_SOURCE = "insufficient_independent_corroboration"


class _Records:
    """Claims citing records the controls label positive, negative or unlabelled."""

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.bound, self.unbound = fixture.provider(), fixture.provider()
        self.work = fixture.text()
        accepted, self.accepted = fixture.record(self.bound, self.work)
        fixture.bind(self.bound, accepted, self.work, "accepted")

    def positive(self) -> str:
        return self.fixture.record(self.bound, self.work)[1]

    def negative(self) -> str:
        return self.fixture.record(self.bound, self.fixture.text())[1]

    def unlabelled(self) -> str:
        return self.fixture.record(self.unbound, self.fixture.text())[1]


def _split(row: Mapping[str, object], basis: str) -> dict[str, dict[str, object]]:
    split = cast("dict[str, object]", row["split"])
    return cast("dict[str, dict[str, object]]", split[f"by_{basis}"])


def test_destination_and_medium_splits_sum_to_the_row(tmp_path: Path) -> None:
    text = Sentinels()
    ebook, audio = Fixture(text, "v26-like"), Fixture(text, "v57-like")
    for fixture in (ebook, audio):
        records = _Records(fixture)
        _competing(
            fixture,
            "title",
            [
                ("alternative_a", records.negative()),
                ("alternative_b", records.accepted),
            ],
        )
        _competing(
            fixture,
            "series",
            [
                ("alternative_a", [records.positive(), records.unlabelled()]),
                ("alternative_b", records.negative()),
            ],
        )
    # A field whose pick reaches no record, on the ebook side only.
    _competing(
        ebook, "description", [("alternative_a", "local_" + text()), ("b", text())]
    )
    snapshots = [
        (
            "ebook",
            _database(tmp_path / "e.sqlite3", "v26-like", [ebook]),
            EBOOK_REVISION,
        ),
        (
            "audio",
            _database(tmp_path / "a.sqlite3", "v57-like", [audio]),
            AUDIO_REVISION,
        ),
    ]
    code, stdout, _ = _run(_arguments(snapshots, tmp_path))
    assert code == 0
    row = _rows(stdout)["T1"]
    pooled = cast("dict[str, object]", row["pooled"])
    entries = [_snapshot(row, label) for label, _database_path, _revision in snapshots]
    for basis in ("destination", "medium"):
        split = _split(row, basis).values()
        for key, total in (
            ("true_positive", pooled["true_positive"]),
            ("false_positive", pooled["false_positive"]),
            ("unlabelled", pooled["unlabelled"]),
            ("fields", row["ceiling"]),
            ("actionable_fields", row["actionable"]),
            (
                "unreached_fields",
                sum(cast("int", e["unreached_fields"]) for e in entries),
            ),
        ):
            assert sum(cast("int", entry[key]) for entry in split) == total, key
    assert set(_split(row, "medium")) == {"audiobook", "ebook"}
    destinations = _split(row, "destination")
    # Title picks the wrong work on both snapshots; series the accepted one,
    # plus the unlabelled record on the same alternative.
    assert (
        destinations["title"]["true_positive"],
        destinations["title"]["false_positive"],
    ) == (0, 2)
    assert destinations["title"]["precision"] == 0.0
    assert destinations["title"]["wilson_lower_bound"] == 0.0
    series = destinations["series"]
    assert (
        series["true_positive"],
        series["false_positive"],
        series["unlabelled"],
    ) == (
        2,
        0,
        2,
    )
    assert series["precision"] == 1.0
    assert series["wilson_lower_bound"] == round(_wilson_reference(2, 2), 4)
    assert destinations["description"]["n"] == 0
    assert destinations["description"]["precision"] is None
    assert destinations["description"]["wilson_lower_bound"] is None
    assert destinations["description"]["unreached_fields"] == 1
    total = cast("dict[str, object]", cast("dict[str, object]", row["split"])["total"])
    assert total["pick"] == row["pick"]
    # Only decision-level rows split.
    assert _rows(stdout)["C1"]["split"] is None and _rows(stdout)["X1"]["split"] is None
    assert json.loads(stdout)["breakdown"] is None
    markdown = _run([*_arguments(snapshots, tmp_path), "--format", "markdown"])[1]
    assert "## Breakdown" not in markdown


def test_the_breakdown_classes_partition_the_actionable_fields(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    records = _Records(fixture)
    # Tied: two clean right-work alternatives.
    _competing(
        fixture,
        "description",
        [("alternative_a", records.accepted), ("alternative_b", records.positive())],
    )
    # Decidable, and the lowest identifier is the right-work one, although
    # it is stored second.
    _competing(
        fixture,
        "series",
        [("alternative_b", records.negative()), ("alternative_a", records.positive())],
    )
    # Decidable against the pick: the right-work alternative sorts last.
    _competing(
        fixture,
        "title",
        [
            ("alternative_a", records.unlabelled()),
            ("alternative_b", records.positive()),
        ],
    )
    # Contaminated: the only right-work evidence shares an alternative with
    # another work's record.
    _competing(
        fixture,
        "authors",
        [
            ("alternative_a", [records.positive(), records.negative()]),
            ("alternative_b", records.unlabelled()),
        ],
    )
    # No right-work alternative: local and unlabelled evidence only.
    _competing(
        fixture,
        "genres",
        [("alternative_a", "local_" + text()), ("alternative_b", records.unlabelled())],
    )
    # No right-work alternative either: another work's and unlabelled records.
    _competing(
        fixture,
        "subtitle",
        [
            ("alternative_a", records.negative()),
            ("alternative_b", records.unlabelled()),
        ],
    )
    # Single alternative: nothing competes with it, and it keeps the value.
    _competing(
        fixture,
        "tags",
        [("alternative_a", records.positive())],
        preserves="alternative_a",
    )
    # Blocked but not actionable: no alternative at all.
    fixture.result("asin", "unresolved_choice", ["competing_evidence"])
    # An A2 field whose one source is the right work: A2 scores, but is kept.
    _competing(
        fixture,
        "publisher",
        [("alternative_a", records.positive())],
        reasons=[_SINGLE_SOURCE],
    )
    database = _database(tmp_path / "c.sqlite3", "v26-like", [fixture])
    arguments = _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path)
    code, stdout, _ = _run([*arguments, "--breakdown", "T1"])
    assert code == 0
    row = _rows(stdout)["T1"]
    pick = cast("dict[str, object]", row["pick"])
    classes = (
        "decidable_fields",
        "tied_fields",
        "contaminated_fields",
        "no_right_work_fields",
        "single_alternative_fields",
    )
    assert [pick[key] for key in classes] == [2, 1, 1, 2, 1]
    assert (row["ceiling"], row["actionable"]) == (8, 7)
    assert sum(cast("int", pick[key]) for key in classes) == row["actionable"]
    assert (pick["pick_agrees"], pick["rate"]) == (1, 0.5)
    destinations = _split(row, "destination")
    expected = {
        "description": "tied_fields",
        "series": "decidable_fields",
        "title": "decidable_fields",
        "authors": "contaminated_fields",
        "genres": "no_right_work_fields",
        "subtitle": "no_right_work_fields",
        "tags": "single_alternative_fields",
    }
    for destination, key in expected.items():
        counts = cast("dict[str, object]", destinations[destination]["pick"])
        assert [counts[name] for name in classes] == [
            int(name == key) for name in classes
        ], destination
    assert cast("dict[str, object]", destinations["series"]["pick"])["pick_agrees"] == 1
    assert cast("dict[str, object]", destinations["title"]["pick"])["pick_agrees"] == 0
    assert destinations["asin"]["actionable_fields"] == 0
    # A single alternative's pick is that alternative, so it can preserve too.
    assert (
        cast("dict[str, object]", destinations["tags"]["pick"])["pick_preserves"] == 1
    )
    assert pick["pick_preserves"] == 1
    markdown = _run([*arguments, "--format", "markdown", "--breakdown", "T1"])[1]
    lines = markdown.splitlines()
    # The corroborated and owner columns (#1748): no correlations and no
    # owner decisions table in this fixture.
    unmeasured = "| not estimable | not recorded | not recorded |"
    assert lines[lines.index("## Breakdown: T1") + 4 :][:2] == [
        "| asin | 1 | 0 | 0/0/0 | 1 | not estimable | not estimable "
        "| 0 | 0 | 0 | 0 | 0 | 0/0/0 | 0/0 " + unmeasured,
        "| authors | 1 | 1 | 1/1/0 | 0 | 0.500 (0.095) | not estimable "
        "| 0 | 1 | 0 | 0 | 0 | 0/0/0 | 0/1 " + unmeasured,
    ]
    assert lines[-2:] == [
        "| medium: ebook | 8 | 7 | 4/2/1 | 2 | 0.667 (0.300) | 1/2 0.500 (0.095) "
        "| 1 | 1 | 2 | 1 | 1 | 0/0/0 | 0/6 " + unmeasured,
        "| total | 8 | 7 | 4/2/1 | 2 | 0.667 (0.300) | 1/2 0.500 (0.095) "
        "| 1 | 1 | 2 | 1 | 1 | 0/0/0 | 0/6 " + unmeasured,
    ]
    # T1's and A2's decisions are recorded: they are kept, not proposed again.
    ranked = next(line for line in lines if line.split(" | ")[1:2] == ["T1"])
    assert ranked.endswith("| kept (#1744) |")
    assert row["proposed_follow_on"] is False
    a2_row = _rows(stdout)["A2"]
    assert cast("float", a2_row["score"]) > 0
    assert a2_row["proposed_follow_on"] is False
    a2 = next(line for line in lines if line.split(" | ")[1:2] == ["A2"])
    assert a2.endswith("| kept (#1747) |")
    assert (
        "| a populated field other than genres, tags and description; "
        "narrators also when empty |" in a2
    )
    assert "| #351 (d35215e8); narrators #966 (0ce358e9), #1642 (5558b7b3) |" in a2
    assert "| admit the only alternative, which one source states |" in a2


def test_pick_preservation_follows_the_lowest_identifier(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    # The preserving alternative is stored second but has the lowest identifier.
    _competing(
        fixture,
        "title",
        [("alternative_b", text()), ("alternative_a", text())],
        preserves="alternative_a",
    )
    # Stored first, but not the pick: admitting the pick would change the value.
    _competing(
        fixture,
        "series",
        [("alternative_b", text()), ("alternative_a", text())],
        preserves="alternative_b",
    )
    database = _database(tmp_path / "p.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    row = _rows(stdout)["T1"]
    assert cast("dict[str, object]", row["pick"])["pick_preserves"] == 1
    destinations = _split(row, "destination")
    assert (
        cast("dict[str, object]", destinations["title"]["pick"])["pick_preserves"] == 1
    )
    assert (
        cast("dict[str, object]", destinations["series"]["pick"])["pick_preserves"] == 0
    )


def test_a_breakdown_needs_a_decision_level_gate(tmp_path: Path) -> None:
    # The value is refused before any database is opened, and is never echoed.
    for value in ("C1", "X1", "Z9", f"{_SENTINEL}0003"):
        code, stdout, stderr = _run(
            _arguments(
                [("snap-a", tmp_path / "missing.sqlite3", EBOOK_REVISION)],
                tmp_path,
                "--breakdown",
                value,
            )
        )
        assert (code, stdout) == (2, "")
        assert stderr == "admission-rule-ledger: invalid breakdown\n"


# --- Series rows, owner labels and the corroborated pick (#1748) -----------


def _introduced(gate_id: str) -> str:
    return next(
        gate for gate in ledger.CATALOGUE if gate.gate_id == gate_id
    ).introduced_in


def _replay(
    expected: Path | None = None,
    **probe: object,
) -> series_replay.SeriesReader:
    """A replay double: the probe it returns, checking the shared case query."""

    def reader(_root: Path, database: Path, cases: str) -> series_replay.SeriesProbe:
        if expected is not None:
            assert database == expected
        connection = sqlite3.connect(database)
        try:
            assert cases == ledger.series_cases_sql(connection)
        finally:
            connection.close()
        return _series_probe(**probe)  # type: ignore[arg-type]

    return reader


def _series(
    fixture: Fixture, outcome: str, reasons: Sequence[str] = (), claim: str = ""
) -> None:
    fixture.result("series", outcome, reasons, [claim or fixture.text()])


def test_w9_counts_a_series_value_the_replay_refuses(tmp_path: Path) -> None:
    text = Sentinels()
    fixtures = [Fixture(text, "v26-like") for _ in range(4)]
    _series(fixtures[0], "propose_change", ["unique_typed_candidate"])
    _series(fixtures[1], "propose_change", ["unique_typed_candidate"])
    # Flagged, but the outcome keeps the current value: nothing is blocked.
    _series(fixtures[2], "keep", ["positive_preservation_evidence"])
    # The stored outcome refused it with W9's own reason, and the decision step
    # also abstained with the rich residual A6b reads (#1764).
    _series(fixtures[3], "propose_change", ["unique_typed_candidate"])
    fixtures[3].abstain("series", "target_work_applicability_unproved")
    fixtures[3].abstain("series", "no_eligible_action")
    database = _database(tmp_path / "w9.sqlite3", "v26-like", fixtures)
    replay = _replay(
        database,
        w9={0: True, 1: False, 2: True, 3: False},
        replayed=4,
        reached=4,
    )
    arguments = _arguments([("snap-a", database, AUDIO_REVISION)], tmp_path)
    code, stdout, stderr = _run(arguments, series=replay)
    assert code == 0, stderr
    rows = _rows(stdout)
    entry = _snapshot(rows["W9"], "snap-a")
    assert (entry["fields"], entry["works"], entry["actionable_fields"]) == (3, 3, 1)
    assert entry["stored_refused"] == 1
    # Only the actionable field is classed, so the classes still partition it.
    pick = cast("dict[str, object]", rows["W9"]["pick"])
    assert pick["single_alternative_fields"] == 1
    replayed = cast("dict[str, int]", entry["replay"])
    assert (replayed["replayed"], replayed["reached"]) == (4, 4)
    assert rows["W9"]["disposition"] == "kept (#1748)"
    # Overlap with A6b is allowed (ceilings are not exclusive): A6b's outcome
    # rule leaves a proposed value alone, so only W9 counts this field.
    assert _snapshot(rows["A6b"], "snap-a")["fields"] == 0
    markdown = _run(
        [*arguments, "--format", "markdown", "--breakdown", "W9"], series=replay
    )[1]
    assert (
        "| Snapshot | Replayed | Reach the identity match | "
        "Also refused in the stored outcome |" in markdown
    )
    assert "| snap-a | 4 | 4 | 1 |" in markdown


def test_a_disagreeing_series_is_counted_upstream_not_by_w9(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    _competing(
        fixture, "series", [("alternative_a", text()), ("alternative_b", text())]
    )
    fixture.abstain("series", "series_conflicting_membership")
    database = _database(tmp_path / "t1.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(
        _arguments([("snap-a", database, AUDIO_REVISION)], tmp_path),
        series=_replay(database, replayed=1, unreached_no_candidate=1),
    )
    assert code == 0
    rows = _rows(stdout)
    assert _snapshot(rows["T1"], "snap-a")["by_destination"] == {"series": 1}
    for gate_id in ("W9", "A8", "A9"):
        assert _snapshot(rows[gate_id], "snap-a")["fields"] == 0


def _refuse(_root: Path, _database: Path, _cases: str) -> series_replay.SeriesProbe:
    raise AssertionError("the series replay ran on a source without W9")


def test_w9_is_not_replayed_where_the_gate_postdates_the_source(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    _series(fixture, "propose_change", ["unique_typed_candidate"])
    database = _database(tmp_path / "old.sqlite3", "v26-like", [fixture])

    def ancestry(commit: str, _revision: str) -> bool:
        return commit not in {_introduced("W9"), _introduced("A9")}

    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path),
        ancestry=ancestry,
        series=_refuse,
    )
    assert code == 0, stderr
    rows = _rows(stdout)
    assert _snapshot(rows["W9"], "snap-a") == {
        "label": "snap-a",
        "status": "gate_postdates_source",
    }
    assert _snapshot(rows["A9"], "snap-a")["status"] == "gate_postdates_source"
    # A8 predates W9: without the replay it falls back to its stored reason.
    assert _snapshot(rows["A8"], "snap-a")["status"] == "measurable"
    assert _snapshot(rows["A8"], "snap-a")["replay"] is None


def test_series_completeness_and_provider_evidence_rows(tmp_path: Path) -> None:
    text = Sentinels()
    fixtures = [Fixture(text, "v26-like") for _ in range(4)]
    _series(fixtures[0], "propose_change", ["unique_typed_candidate"])
    _series(fixtures[1], "propose_change", ["unique_typed_candidate"])
    # The no-candidate refusal restates the proposal set's: not A8's (R2).
    fixtures[2].result("series", "ineligible", ["no_supported_evidence"])
    fixtures[2].abstain("series", "series_membership_incomplete")
    # A stored completeness refusal of a value the proposal set kept.
    _series(fixtures[3], "defer", ["insufficient_support"])
    fixtures[3].abstain("series", "series_membership_incomplete")
    database = _database(tmp_path / "a8.sqlite3", "v26-like", fixtures)
    replay = _replay(database, a8=[0], a9=[1], replayed=3, reached=2)
    arguments = _arguments([("snap-a", database, AUDIO_REVISION)], tmp_path)
    code, stdout, _ = _run(arguments, series=replay)
    assert code == 0
    rows = _rows(stdout)
    assert _snapshot(rows["A8"], "snap-a")["fields"] == 1
    assert _snapshot(rows["A8"], "snap-a")["stored_refused"] == 0
    assert _snapshot(rows["A9"], "snap-a")["fields"] == 1
    assert _snapshot(rows["W9"], "snap-a")["fields"] == 0
    for gate_id in ("A8", "A9"):
        assert rows[gate_id]["control"] == "not_identity"
        assert _snapshot(rows[gate_id], "snap-a")["actionable_fields"] is None

    # Without the replay, A8 falls back to the stored reason, still only on a
    # field that kept an alternative.
    def no_w9(commit: str, _revision: str) -> bool:
        return commit != _introduced("W9")

    code, stdout, _ = _run(arguments, ancestry=no_w9, series=_refuse)
    assert code == 0
    assert _snapshot(_rows(stdout)["A8"], "snap-a")["fields"] == 1
    # ... and on a source that also collapsed rich refusals it is not separable.
    code, stdout, _ = _run(
        arguments,
        ancestry=no_w9,
        attention=_engine_probe(["title"], rich_split=False),
        series=_refuse,
    )
    assert code == 0
    assert (
        _snapshot(_rows(stdout)["A8"], "snap-a")["status"] == "rich_refusals_collapsed"
    )


def test_w5_leaves_series_to_w9(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    fixture.result("series", "defer", ["target_work_applicability_unproved"], [text()])
    fixture.result("genres", "defer", ["target_work_applicability_unproved"], [text()])
    database = _database(tmp_path / "w5.sqlite3", "v26-like", [fixture])
    code, stdout, _ = _run(_arguments([("snap-a", database, AUDIO_REVISION)], tmp_path))
    assert code == 0
    assert _snapshot(_rows(stdout)["W5"], "snap-a")["by_destination"] == {"genres": 1}


def _labelled(
    tmp_path: Path,
    fixtures: Sequence[Fixture],
    *,
    gate_id: str = "T1",
    floors: Mapping[int, int] | None = None,
    series: series_replay.SeriesReader = _no_series,
) -> tuple[dict[str, object], dict[str, object]]:
    """The row's pooled owner entry and its total split."""
    tmp_path.mkdir(exist_ok=True)
    database = _database(
        tmp_path / "owner.sqlite3", "v26-like", fixtures, floors=floors
    )
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, AUDIO_REVISION)], tmp_path), series=series
    )
    assert code == 0, stderr
    total = cast(
        "dict[str, object]",
        cast("dict[str, object]", _rows(stdout)[gate_id]["split"])["total"],
    )
    return cast("dict[str, object]", total["owner"]), total


def _choice(
    text: Sentinels, *, preserves: str | None = None
) -> tuple[Fixture, dict[str, dict[str, object]]]:
    """A competing title whose pick, the lowest identifier, is alternative_a."""
    fixture = Fixture(text, "v26-like")
    alternatives = _competing(
        fixture,
        "title",
        [("alternative_b", text()), ("alternative_a", text())],
        preserves=preserves,
    )
    return fixture, alternatives


@pytest.mark.parametrize(
    ("decisions", "preserves", "floor", "expected"),
    [
        ([(1, "accept", "alternative_a")], None, None, (1, 1, "accept")),
        ([(1, "accept", "alternative_b")], None, None, (1, 0, "accept")),
        ([(1, "owner_entered", "alternative_a")], None, None, (1, 1, "owner_entered")),
        ([(1, "keep_current", None)], None, None, (1, 0, "keep_current")),
        ([(1, "keep_current", None)], "alternative_a", None, (1, 1, "keep_current")),
        ([(1, "reject", None)], None, None, (1, 0, "reject")),
        (
            [(1, "accept", "alternative_a"), (2, "withdraw", None)],
            None,
            None,
            (0, 0, None),
        ),
        ([(3, "accept", "alternative_a")], None, 3, (0, 0, None)),
        ([(3, "accept", "alternative_a")], None, 2, (1, 1, "accept")),
        (
            [(1, "accept", "alternative_b"), (2, "accept", "alternative_a")],
            None,
            None,
            (1, 1, "accept"),
        ),
    ],
    ids=[
        "accept-the-pick-agrees",
        "accept-another-alternative-disagrees",
        "owner-entered-pick-agrees",
        "keep-current-on-an-empty-target-disagrees",
        "keep-current-with-a-preserving-pick-agrees",
        "reject-disagrees",
        "withdraw-labels-nothing",
        "decision-at-the-episode-floor-labels-nothing",
        "decision-above-the-episode-floor-labels",
        "the-latest-ordinal-wins",
    ],
)
def test_owner_decisions_label_the_pick(
    tmp_path: Path,
    decisions: Sequence[tuple[int, str, str | None]],
    preserves: str | None,
    floor: int | None,
    expected: tuple[int, int, str | None],
) -> None:
    text = Sentinels()
    fixture, alternatives = _choice(text, preserves=preserves)
    for ordinal, outcome, chosen in decisions:
        value = None if chosen is None else alternatives[chosen]["value"]
        fixture.decide(ordinal, "title", outcome, value)
    owner, _total = _labelled(
        tmp_path, [fixture], floors=None if floor is None else {0: floor}
    )
    labelled, agrees, named = expected
    assert (owner["labelled"], owner["pick_agrees"]) == (labelled, agrees)
    by_outcome = cast("dict[str, int]", owner["by_outcome"])
    assert sum(by_outcome.values()) == labelled
    if named is not None:
        assert by_outcome[named] == 1


def test_owner_labels_follow_the_item_across_rescans(tmp_path: Path) -> None:
    text = Sentinels()
    # An accept on a superseded case whose successor is current and undecided
    # still labels the pick; a keep_current on another current case labels too.
    superseded, alternatives = _choice(text)
    superseded.current = False
    superseded.decide(1, "title", "accept", alternatives["alternative_a"]["value"])
    successor, _ = _choice(text)
    successor.item = superseded.item
    other, _ = _choice(text)
    other.decide(2, "title", "keep_current")
    owner, _total = _labelled(tmp_path / "one", [superseded, successor, other])
    assert (owner["labelled"], owner["pick_agrees"], owner["rate"]) == (2, 1, 0.5)
    assert owner["by_outcome"] == {
        "accept": 1,
        "owner_entered": 0,
        "keep_current": 1,
        "reject": 0,
    }
    # A written and rescanned item: different target values, the same item.
    # Decisions on both cases count once, the latest; the replay's W9 verdict
    # on the superseded case keeps its label (RRN2).
    text = Sentinels()
    old = Fixture(text, "v26-like")
    old_values = _competing(
        old,
        "series",
        [("alternative_a", text()), ("alternative_b", text())],
        outcome="propose_change",
        reasons=["unique_typed_candidate"],
    )
    old.current = False
    old.decide(1, "series", "accept", old_values["alternative_b"]["value"])
    new = Fixture(text, "v26-like")
    new.item = old.item
    assert new.target_values != old.target_values
    new_values = _competing(
        new,
        "series",
        [("alternative_a", text()), ("alternative_b", text())],
        outcome="propose_change",
        reasons=["unique_typed_candidate"],
    )
    new.decide(2, "series", "accept", new_values["alternative_a"]["value"])
    owner, _total = _labelled(
        tmp_path / "two",
        [old, new],
        gate_id="W9",
        series=_replay(w9={0: True, 1: True}, replayed=1, reached=1),
    )
    assert (owner["labelled"], owner["pick_agrees"]) == (1, 1)
    # Only the superseded case decided: its label stands, though it is no
    # longer current, and it adds no blocked field to the row.
    new.decisions.clear()
    database = _database(tmp_path / "three.sqlite3", "v26-like", [old, new])
    code, stdout, _ = _run(
        _arguments([("snap-a", database, AUDIO_REVISION)], tmp_path),
        series=_replay(w9={0: True, 1: True}, replayed=1, reached=1),
    )
    assert code == 0
    row = _rows(stdout)["W9"]
    assert _snapshot(row, "snap-a")["fields"] == 1
    owner = cast("dict[str, object]", _snapshot(row, "snap-a")["owner"])
    assert (owner["labelled"], owner["pick_agrees"]) == (1, 0)


def _pick_line(markdown: str, gate_id: str) -> str:
    return next(
        line for line in markdown.splitlines() if line.split(" | ")[1:2] == [gate_id]
    )


def test_owner_labels_without_the_table(tmp_path: Path) -> None:
    text = Sentinels()
    fixture, _ = _choice(text)
    without = _database(tmp_path / "without.sqlite3", "v26-like", [fixture])
    empty = _database(
        tmp_path / "empty.sqlite3", "v26-like", [fixture], owner_tables=True
    )
    code, stdout, _ = _run(_arguments([("snap-a", without, AUDIO_REVISION)], tmp_path))
    assert code == 0
    row = _rows(stdout)["T1"]
    assert _snapshot(row, "snap-a")["owner"] is None
    total = cast("dict[str, object]", cast("dict[str, object]", row["split"])["total"])
    assert total["owner"] is None
    cases = [
        (
            [("snap-a", without, AUDIO_REVISION)],
            "; owner decisions not stored in every snapshot",
        ),
        ([("snap-a", empty, AUDIO_REVISION)], "; no owner decisions"),
        (
            [("snap-a", empty, AUDIO_REVISION), ("snap-b", without, AUDIO_REVISION)],
            "; owner decisions not stored in every snapshot",
        ),
    ]
    for snapshots, suffix in cases:
        markdown = _run([*_arguments(snapshots, tmp_path), "--format", "markdown"])[1]
        assert f"(0 decidable; 0 tied){suffix} |" in _pick_line(markdown, "T1")


def test_markdown_names_the_series_rows_and_the_owner_suffix(tmp_path: Path) -> None:
    text = Sentinels()
    fixture, alternatives = _choice(text)
    fixture.decide(1, "title", "accept", alternatives["alternative_a"]["value"])
    database = _database(tmp_path / "md.sqlite3", "v26-like", [fixture])
    markdown = _run(
        [
            *_arguments([("snap-a", database, AUDIO_REVISION)], tmp_path),
            "--format",
            "markdown",
        ]
    )[1]
    w9 = _pick_line(markdown, "W9")
    assert "Series identity match for automatic series values" in w9
    assert w9.endswith("| kept (#1748) |")
    assert "| admit a series value with incomplete membership claims |" in _pick_line(
        markdown, "A8"
    )
    assert "Series provider evidence" in _pick_line(markdown, "A9")
    assert "; owner 1/1 1.000 (0.206) |" in _pick_line(markdown, "T1")


@pytest.mark.parametrize(
    ("correlations", "picked"),
    [
        ({"alternative_a": ["c1"], "alternative_b": ["c2", "c3"]}, "alternative_b"),
        ({"alternative_a": ["c1", "c2"], "alternative_b": ["c3", "c4"]}, None),
        ({"alternative_a": ["c1"], "alternative_b": ["c2"]}, None),
        ({"alternative_a": ["c1"], "alternative_b": ["c2", "c2"]}, None),
    ],
    ids=[
        "two-sources-against-one",
        "two-against-two",
        "one-against-one",
        "duplicates-count-once",
    ],
)
def test_the_corroborated_pick_needs_a_unique_majority_of_two_sources(
    tmp_path: Path, correlations: Mapping[str, Sequence[str]], picked: str | None
) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    provider = fixture.provider()
    work = text()
    accepted, right = fixture.record(provider, work)
    _other, wrong = fixture.record(provider, text())
    fixture.bind(provider, accepted, work, "accepted")
    stamped = {
        key: [f"{_SENTINEL}{item}" for item in value]
        for key, value in correlations.items()
    }
    alternatives = _competing(
        fixture,
        "title",
        [("alternative_a", wrong), ("alternative_b", right)],
        correlations=stamped,
    )
    fixture.decide(1, "title", "accept", alternatives["alternative_b"]["value"])
    database = _database(tmp_path / "c.sqlite3", "v26-like", [fixture])
    arguments = _arguments(
        [("snap-a", database, AUDIO_REVISION)], tmp_path, "--breakdown", "T1"
    )
    code, stdout, _ = _run(arguments)
    assert code == 0
    total = cast(
        "dict[str, object]",
        cast("dict[str, object]", _rows(stdout)["T1"]["split"])["total"],
    )
    corroborated = cast("dict[str, object]", total["corroborated"])
    chose = int(picked is not None)
    assert (corroborated["fields"], corroborated["picks"]) == (1, chose)
    # The picked alternative is the one right-work alternative and the owner's.
    assert (corroborated["decidable"], corroborated["agrees"]) == (chose, chose)
    assert (corroborated["owner_labelled"], corroborated["owner_agrees"]) == (
        chose,
        chose,
    )
    # The lowest-identifier pick is the wrong-work alternative.
    owner = cast("dict[str, object]", total["owner"])
    assert (owner["labelled"], owner["pick_agrees"]) == (1, 0)
    markdown = _run([*arguments, "--format", "markdown"])[1]
    for header in (
        "| Corroborated picks |",
        "| Corroborated pick agrees where controls decide (lower) |",
        "| Pick agrees with the owner (lower) |",
        "| Corroborated pick agrees with the owner (lower) |",
    ):
        assert header in markdown
    assert _SENTINEL not in stdout + markdown


# --- Fields automatic decisions already settled (#1747) --------------------


def _automatic(entry: Mapping[str, object]) -> tuple[int, int, int] | None:
    counts = cast("dict[str, int] | None", entry["automatic"])
    if counts is None:
        return None
    return counts["takes_pick"], counts["keeps_current"], counts["takes_other"]


def _settled_snapshots(tmp_path: Path) -> list[tuple[str, Path, str]]:
    """Blocked fields an automatic decision took, kept or overrode, and decisions
    that must not count."""
    text = Sentinels()
    first = Fixture(text, "v26-like")
    # A2 on title: the decision takes the one alternative, which is the pick.
    _competing(first, "title", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    first.decide_automatically("title", "candidate", "alternative_a")
    # A2 on series: the decision keeps the current value.
    _competing(first, "series", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    first.decide_automatically("series", "keep_current")
    # T1 on description: the decision takes the alternative that is not the pick.
    _competing(
        first, "description", [("alternative_a", text()), ("alternative_b", text())]
    )
    first.decide_automatically("description", "candidate", "alternative_b")
    # A2 on asin: an ignore leaves the field as it is, so it counts as keep.
    _competing(first, "asin", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    first.decide_automatically("asin", "ignore")
    # A1 on language: blocked with no alternative, so not actionable, but a
    # stored decision still settled it.
    first.result("language", "unresolved_choice", ["no_positive_preservation_evidence"])
    first.decide_automatically("language", "keep_current")
    # A2 on authors, blocked with no decision.
    _competing(first, "authors", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    # A proposed field is not blocked, whatever was decided for it.
    _competing(
        first,
        "publisher",
        [("alternative_a", text())],
        outcome="propose_change",
        reasons=[_SINGLE_SOURCE],
    )
    first.decide_automatically("publisher", "candidate", "alternative_a")
    # A decision for a destination this case has no result on, but another
    # case blocks.
    first.decide_automatically("subtitle", "keep_current")
    second = Fixture(text, "v26-like")
    _competing(
        second, "subtitle", [("alternative_a", text())], reasons=[_SINGLE_SOURCE]
    )
    audio = Fixture(text, "v57-like")
    _competing(audio, "title", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    audio.decide_automatically("title", "candidate", "alternative_a")
    # An outcome that is neither a keep nor the pick counts as other.
    _competing(audio, "series", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    audio.decide_automatically("series", "unrecognised_outcome")
    return [
        (
            "ebook",
            _database(tmp_path / "e.sqlite3", "v26-like", [first, second]),
            EBOOK_REVISION,
        ),
        (
            "audio",
            _database(tmp_path / "a.sqlite3", "v57-like", [audio]),
            AUDIO_REVISION,
        ),
    ]


def test_automatic_decisions_count_by_effect_on_blocked_fields(tmp_path: Path) -> None:
    snapshots = _settled_snapshots(tmp_path)
    code, stdout, stderr = _run(_arguments(snapshots, tmp_path))
    assert code == 0, stderr
    rows = _rows(stdout)
    a2, t1 = rows["A2"], rows["T1"]
    assert a2["ceiling"] == 7
    assert _automatic(_snapshot(a2, "ebook")) == (1, 2, 0)
    assert _automatic(_snapshot(a2, "audio")) == (1, 0, 1)
    assert _automatic(_snapshot(t1, "ebook")) == (0, 0, 1)
    assert _automatic(_snapshot(t1, "audio")) == (0, 0, 0)
    split = cast("dict[str, dict[str, object]]", a2["split"])
    assert _automatic(split["total"]) == (2, 2, 1)
    destinations = _split(a2, "destination")
    assert {key: _automatic(entry) for key, entry in destinations.items()} == {
        "asin": (0, 1, 0),
        "authors": (0, 0, 0),
        "series": (0, 1, 1),
        "subtitle": (0, 0, 0),
        "title": (2, 0, 0),
    }
    assert {key: _automatic(entry) for key, entry in _split(a2, "medium").items()} == {
        "audiobook": (1, 0, 1),
        "ebook": (1, 2, 0),
    }
    # A blocked field with no alternative is counted too.
    a1 = rows["A1"]
    assert _snapshot(a1, "ebook")["actionable_fields"] == 0
    assert _automatic(_snapshot(a1, "ebook")) == (0, 1, 0)
    # Rows that are not decision-level count nothing.
    assert _snapshot(rows["C1"], "ebook")["automatic"] is None
    assert rows["C1"]["split"] is None


def test_markdown_notes_automatic_decisions_only_when_non_zero(tmp_path: Path) -> None:
    snapshots = _settled_snapshots(tmp_path)
    code, markdown, _ = _run(
        [*_arguments(snapshots, tmp_path), "--format", "markdown", "--breakdown", "A2"]
    )
    assert code == 0
    lines = markdown.splitlines()

    def ranked(gate_id: str) -> str:
        return next(line for line in lines if line.split(" | ")[1:2] == [gate_id])

    assert "| 5 / 2 (5 actionable; 3 decided automatically) |" in ranked("A2")
    assert "| 2 / 1 (2 actionable; 2 decided automatically) |" in ranked("A2")
    assert "| 1 / 1 (0 actionable; 1 decided automatically) |" in ranked("A1")
    assert "| 1 / 1 (1 actionable; 1 decided automatically) |" in ranked("T1")
    # Recorded, but nothing settled on the audiobook side: no note.
    assert "| 0 / 0 (0 actionable) |" in ranked("T1")
    assert "decided automatically" not in ranked("A3")
    breakdown = lines[lines.index("## Breakdown: A2") + 2 :]
    # The automatic column follows Pick preserves; the corroborated and owner
    # columns (#1748) follow it.
    assert (
        "| Pick preserves | Automatic pick/keep/other | Corroborated picks |"
        in (breakdown[0])
    )
    assert "| 7 | 0 | 2/2/1 | " in breakdown[-1]


def test_a_snapshot_without_automatic_decisions_reports_not_recorded(
    tmp_path: Path,
) -> None:
    text = Sentinels()
    bare, stored = Fixture(text, "v26-like"), Fixture(text, "v57-like")
    for fixture in (bare, stored):
        _competing(
            fixture, "title", [("alternative_a", text())], reasons=[_SINGLE_SOURCE]
        )
    stored.decide_automatically("title", "keep_current")
    bare_snapshot = (
        "bare",
        _database(tmp_path / "b.sqlite3", "v26-like", [bare], automatic=False),
        EBOOK_REVISION,
    )
    code, stdout, stderr = _run(_arguments([bare_snapshot], tmp_path))
    assert code == 0, stderr
    a2 = _rows(stdout)["A2"]
    assert _snapshot(a2, "bare")["automatic"] is None
    split = cast("dict[str, dict[str, object]]", a2["split"])
    assert split["total"]["automatic"] is None
    markdown = _run(
        [*_arguments([bare_snapshot], tmp_path), "--format", "markdown"]
        + ["--breakdown", "A2"]
    )[1]
    assert "decided automatically" not in markdown
    assert markdown.splitlines()[-1].endswith(
        "| 1 | 0 | not recorded | 0/0 | not estimable | not recorded | not recorded |"
    )
    # Pooled with a snapshot that did record them, the split is still not
    # recorded: a partial pool would understate it.
    stored_snapshot = (
        "stored",
        _database(tmp_path / "s.sqlite3", "v57-like", [stored]),
        AUDIO_REVISION,
    )
    code, stdout, _ = _run(_arguments([bare_snapshot, stored_snapshot], tmp_path))
    assert code == 0
    a2 = _rows(stdout)["A2"]
    assert _automatic(_snapshot(a2, "stored")) == (0, 1, 0)
    assert _snapshot(a2, "bare")["automatic"] is None
    assert _split(a2, "destination")["title"]["automatic"] is None


def test_only_the_active_policy_generation_settles_a_field(tmp_path: Path) -> None:
    text = Sentinels()
    fixture = Fixture(text, "v26-like")
    _competing(fixture, "title", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    # The active generation's decision takes the pick; the earlier
    # generation's keep, stored after it, is history and settles nothing.
    fixture.decide_automatically("title", "candidate", "alternative_a", generation=2)
    fixture.decide_automatically("title", "keep_current", generation=1)
    database = _database(tmp_path / "g.sqlite3", "v26-like", [fixture], generation=2)
    code, stdout, stderr = _run(
        _arguments([("snap-a", database, EBOOK_REVISION)], tmp_path)
    )
    assert code == 0, stderr
    assert _automatic(_snapshot(_rows(stdout)["A2"], "snap-a")) == (1, 0, 0)
    # After a policy change and before the new generation decides, the earlier
    # generation's decision is not the newest active one: nothing is settled.
    stale = Fixture(text, "v26-like")
    _competing(stale, "title", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    stale.decide_automatically("title", "keep_current", generation=1)
    database = _database(tmp_path / "s.sqlite3", "v26-like", [stale], generation=2)
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    assert _automatic(_snapshot(_rows(stdout)["A2"], "snap-a")) == (0, 0, 0)
    # Without a policy state no active generation can be read: the one stored
    # decision counts.
    single = Fixture(text, "v26-like")
    _competing(single, "title", [("alternative_a", text())], reasons=[_SINGLE_SOURCE])
    single.decide_automatically("title", "keep_current", generation=3)
    database = _database(tmp_path / "n.sqlite3", "v26-like", [single], generation=None)
    code, stdout, _ = _run(_arguments([("snap-a", database, EBOOK_REVISION)], tmp_path))
    assert code == 0
    assert _automatic(_snapshot(_rows(stdout)["A2"], "snap-a")) == (0, 1, 0)
