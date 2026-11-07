"""The series replay child over product-built databases (#1748).

Every database is built here in ``tmp_path`` through the product's own
repository: reports, proposal sets, review cases and shadow outcomes, under a
policy that sends series to Automatic. The child replays them under this
checkout, so its verdict can be compared with the checkout's own preview.
"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import scripts.admission_rule_ledger as ledger
import scripts.admission_rule_series as series

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderClaim,
    ProviderLaneObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.proposal_set import (
    evaluate_report,
    provider_candidate_represents_target_series_work,
)
from tests.support.provider_recovery_rail import (
    _automatic_series_report,
    _series_claims,
    _series_provider,
)
from tests.unit.test_admission_rule_ledger import (
    AUDIO_REVISION,
    _arguments,
    _database,
    _no_attention,
)
from tests.unit.test_rich_metadata_preview import _automatic_series_preview

REPOSITORY = Path(__file__).resolve().parents[2]
OPTION = "primary_narrative"
_W9 = "target_work_applicability_unproved"


def _item(report: MetadataReport, item: str) -> MetadataReport:
    """The same evidence for another library item, so each case is its own."""
    return MetadataReport.create(
        target=replace(report.target, item=item),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
    )


def _valued(report: MetadataReport, name: str, sequence: str) -> MetadataReport:
    fields = tuple(
        TargetField(
            TargetDestination.SERIES,
            TargetFieldState.VALUE,
            (TargetSeries(name, sequence),),
        )
        if field.destination is TargetDestination.SERIES
        else field
        for field in report.target.fields
    )
    return MetadataReport.create(
        target=replace(report.target, fields=fields),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
    )


def _unrelated() -> MetadataReport:
    """One series value whose only source is another work's record."""
    return _automatic_series_report(
        _series_provider(
            "catalogue", "edition-1", "hardcover", current_title="Other title"
        )
    )


def _matching() -> MetadataReport:
    return _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover")
    )


def _competing() -> MetadataReport:
    """Two providers state different series: the proposal set leaves it open."""
    return _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover"),
        _series_provider(
            "open_library",
            "edition-2",
            "open_library",
            _series_claims("open_library", "edition-2", name="Elsewhere"),
        ),
    )


def _incomplete() -> MetadataReport:
    """Another work's record whose sequence claim is malformed."""
    malformed = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.SERIES_SEQUENCE,
        value=None,
        state=FieldState.MALFORMED,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=1,
    )
    return _automatic_series_report(
        _series_provider(
            "catalogue",
            "edition-1",
            "hardcover",
            (_series_claims("catalogue", "edition-1")[0], malformed),
            current_title="Other title",
        )
    )


def _failed_lookup() -> MetadataReport:
    """Another work's record, behind an ISBN lookup that failed."""
    base = _unrelated()
    provider = base.providers[0]
    failed = ProviderLaneObservation(
        "lane_" + "b" * 64,
        ProviderQueryKind.ISBN,
        ProviderStatus.UNAVAILABLE,
        (base.local_claims[0].reference,),
        "9780306406157",
    )
    return MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(
            replace(
                provider,
                query_kind=ProviderQueryKind.DISCOVERY,
                lanes=(*provider.lanes, failed),
            ),
        ),
    )


def _build(
    path: Path, reports: dict[str, MetadataReport]
) -> tuple[Path, dict[str, int]]:
    """Publish each report as a current case with a stored shadow outcome.

    Returns the database and each case's ordinal in the shared case query.
    """
    database = path / "application.sqlite3"
    repository = open_repository(database)
    builtin = MetadataPolicyProfile.builtin()
    destinations = dict(builtin.destinations)
    destinations["series"] = "automatic"
    options = dict(builtin.options)
    options["series"] = OPTION
    profile = MetadataPolicyProfile.create("Series replay", options, destinations)
    cases: dict[str, str] = {}
    for number, (name, base) in enumerate(reports.items()):
        report = _item(base, f"item-{name}")
        policy = resolve_policy(profile, report.target.library)
        if number == 0:
            repository.enter_shadow_preview(1, ())
            repository.create_metadata_policy_draft(profile.canonical_json)
            repository.activate_metadata_policy(
                profile.policy_id,
                builtin.policy_id,
                ((report.target.library, policy.resolved_id, policy.canonical_json),),
                2,
                expected_active_generation=1,
            )
        proposal = evaluate_report(
            report, policy.resolved_id, dict(policy.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository.persist_shadow_outcome(case, 3)
        cases[name] = case
    repository.close()
    connection = sqlite3.connect(database)
    try:
        order = [row[0] for row in connection.execute(_cases(database))]
    finally:
        connection.close()
    return database, {name: order.index(case) for name, case in cases.items()}


def _cases(database: Path) -> str:
    connection = sqlite3.connect(database)
    try:
        return ledger.series_cases_sql(connection)
    finally:
        connection.close()


def _replay(database: Path) -> series.SeriesProbe:
    return series.child_series(REPOSITORY, database, _cases(database))


def test_the_series_child_flags_what_the_product_refuses(tmp_path: Path) -> None:
    reports = {
        "unrelated": _unrelated(),
        "matching": _matching(),
        "competing": _competing(),
    }
    database, ordinals = _build(tmp_path, reports)
    probe = _replay(database)
    # The verdict is the checkout's own preview under an automatic series policy.
    for name in ("unrelated", "matching"):
        preview = _automatic_series_preview(reports[name], OPTION)
        assert (ordinals[name] in probe.w9) is (preview.reason == _W9)
    assert probe.w9 == {ordinals["unrelated"]: True}
    assert (probe.a8, probe.a9) == (frozenset(), frozenset())
    # The competing case runs the series check with no single value: replayed,
    # never reached.
    assert _automatic_series_preview(reports["competing"], OPTION).reason == (
        "series_conflicting_membership"
    )
    assert dict(probe.counts) == {
        "replayed": 3,
        "unreplayed": 0,
        "reached": 2,
        "unreached_ask": 0,
        "unreached_earlier": 0,
        "unreached_no_candidate": 1,
    }


def test_an_unresolved_single_alternative_is_not_flagged(tmp_path: Path) -> None:
    report = _valued(_unrelated(), "Elsewhere", "1")
    proposal = evaluate_report(report)
    result = next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.SERIES
    )
    assert (result.outcome.value, len(result.alternatives)) == ("unresolved_choice", 1)
    database, _ordinals = _build(tmp_path, {"open": report})
    probe = _replay(database)
    assert (probe.w9, probe.a8, probe.a9) == ({}, frozenset(), frozenset())
    assert (probe.counts["replayed"], probe.counts["reached"]) == (1, 0)
    assert probe.counts["unreached_earlier"] == 1


def test_an_earlier_refusal_is_attributed_to_its_own_row(tmp_path: Path) -> None:
    reports = {"incomplete": _incomplete(), "failed": _failed_lookup()}
    for report in reports.values():
        # Each also fails the identity match, which the product checks last.
        provider = report.providers[0]
        assert not provider_candidate_represents_target_series_work(
            report, provider, provider.candidates[0]
        )
    assert _automatic_series_preview(reports["incomplete"], OPTION).reason == (
        "series_membership_incomplete"
    )
    assert _automatic_series_preview(reports["failed"], OPTION).reason == (
        "series_provider_failure_isbn"
    )
    database, ordinals = _build(tmp_path, reports)
    probe = _replay(database)
    assert probe.w9 == {}
    assert probe.a9 == frozenset({ordinals["failed"]})
    # The proposal set already refused the incomplete claims, so the series
    # check ran with no value: the restated refusal is upstream's, not A8's
    # (R2).
    assert probe.a8 == frozenset()
    assert probe.counts["unreached_no_candidate"] == 1


_STUB = {
    "__init__.py": "",
    "domain/__init__.py": "",
    "domain/metadata_report.py": """
import enum, json
class TargetDestination(enum.Enum):
    SERIES = "series"
class _Field:
    destination = TargetDestination.SERIES
class _Target:
    fields = (_Field(),)
class MetadataReport:
    target = _Target()
    audio = None
    def __init__(self, data):
        self.data = data
    @classmethod
    def from_json(cls, text):
        return cls(json.loads(text))
""",
    "domain/proposal_set.py": """
from mechanical_librarian.domain.metadata_report import TargetDestination
class _Result:
    destination = TargetDestination.SERIES
class ProposalSet:
    results = (_Result(),)
    @classmethod
    def from_json(cls, _text):
        return cls()
""",
    "domain/rich_metadata_preview.py": """
class _Preview:
    def __init__(self, outcome, reason):
        self.outcome, self.reason = outcome, reason
def provider_candidate_represents_target_series_work(*_arguments):
    return False
def admitted_audiobook_identity(_report):
    return True
def _retained_claims(_report):
    return {}
def _automatic_series_abstention(report, _result, _candidate, _retained):
    reason = report.data["reason"]
    if reason == "target_work_applicability_unproved" and (
        provider_candidate_represents_target_series_work()
    ):
        return None
    return reason
def _project(field, option, destination, target, result, proposal, report,
             retained_claims):
    reason = _automatic_series_abstention(report, result, object(), retained_claims)
    return _Preview("abstain" if reason else "projected", reason)
""",
}


def test_the_series_child_attributes_each_returned_reason(tmp_path: Path) -> None:
    """Every reason the source can return names its own row, on a stub source.

    The proposal set refuses incomplete membership claims before the series
    check gets a value, so A8's own branch is reached here through a stub of
    the source's interface rather than a product-built case.
    """
    root = tmp_path / "source"
    for name, body in _STUB.items():
        path = root / "src" / "mechanical_librarian" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    reasons = [
        "series_membership_incomplete",
        "series_provider_evidence_required",
        "series_provider_failure_work",
        "target_work_applicability_unproved",
        "series_conflicting_membership",
        "",
    ]
    database = tmp_path / "stub.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        "CREATE TABLE review_cases (review_case_id TEXT,proposal_set_id TEXT,"
        "report_id TEXT,document TEXT);"
        "CREATE TABLE proposal_sets (proposal_set_id TEXT,document TEXT);"
        "CREATE TABLE metadata_reports (report_id TEXT,document TEXT);"
        "CREATE TABLE shadow_decision_outcomes (review_case_id TEXT,"
        "evaluated_at INTEGER,document TEXT);"
    )
    outcomes = json.dumps(
        {"outcomes": [{"destination": "series", "policy_option": "source_order:x"}]}
    )
    for number, reason in enumerate(reasons):
        connection.execute(
            "INSERT INTO review_cases VALUES (?,?,?,?)",
            (f"case-{number}", f"set-{number}", f"report-{number}", "{}"),
        )
        connection.execute(
            "INSERT INTO proposal_sets VALUES (?,?)", (f"set-{number}", "{}")
        )
        connection.execute(
            "INSERT INTO metadata_reports VALUES (?,?)",
            (f"report-{number}", json.dumps({"reason": reason})),
        )
        connection.execute(
            "INSERT INTO shadow_decision_outcomes VALUES (?,?,?)",
            (f"case-{number}", 1, outcomes),
        )
    connection.commit()
    connection.close()
    cases = "SELECT review_case_id,1 FROM review_cases ORDER BY review_case_id"
    probe = series.child_series(root, database, cases)
    assert probe.a8 == frozenset({0})
    assert probe.a9 == frozenset({1, 2})
    # W9 projects once the identity match accepts in the stub's namespace.
    assert probe.w9 == {3: True}
    assert (probe.counts["replayed"], probe.counts["reached"]) == (6, 6)


def test_a_valued_target_under_a_fill_option_is_preserved_not_blocked(
    tmp_path: Path,
) -> None:
    # The fill option preserves a valued target before the identity match
    # runs, so the replay neither flags nor counts it as actionable. The same
    # source on an empty target is refused, and projects once the identity
    # match accepts: the patch takes effect in rich_metadata_preview's own
    # namespace (RRN1).
    valued = _valued(_unrelated(), "Unrelated umbrella", "8")
    assert _automatic_series_preview(valued, OPTION).outcome == "no_op"
    database, ordinals = _build(tmp_path, {"valued": valued, "empty": _unrelated()})
    probe = _replay(database)
    assert probe.w9 == {ordinals["empty"]: True}
    assert probe.counts["reached"] == 1


def test_the_series_child_prints_only_counts_and_ordinals(tmp_path: Path) -> None:
    valid: dict[str, object] = {
        **dict.fromkeys(series.COUNTS, 1),
        "W9": [[0, True]],
        "A8": [1],
        "A9": [],
    }
    probe = series.parse_series_probe(json.dumps(valid))
    assert (probe.w9, probe.a8) == ({0: True}, frozenset({1}))
    for broken in (
        {**valid, "value": "ZQX-SENTINEL-0001"},
        {**valid, "A8": ["ZQX-SENTINEL-0002"]},
        {**valid, "W9": [[0, "ZQX-SENTINEL-0003"]]},
        {**valid, "replayed": True},
        {**valid, "reached": -1},
        {key: value for key, value in valid.items() if key != "A9"},
    ):
        with pytest.raises(ledger.Refusal) as refused:
            series.parse_series_probe(json.dumps(broken))
        assert refused.value.message == "series replay failed"
    # A child that fails writes a traceback naming a sentinel; none of it
    # reaches the parent's output.
    root = tmp_path / "source"
    package = root / "src" / "mechanical_librarian"
    (package / "domain").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "domain" / "__init__.py").write_text("")
    (package / "domain" / "rich_metadata_preview.py").write_text(
        "raise RuntimeError('ZQX-SENTINEL-0004')\n"
    )
    database = _database(tmp_path / "ledger.sqlite3", "v26-like", [])
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ledger.main(
        _arguments([("snap-a", database, AUDIO_REVISION)], root),
        stdout=stdout,
        stderr=stderr,
        ancestry=lambda _commit, _revision: True,
        attention=_no_attention,
    )
    assert (code, stdout.getvalue()) == (2, "")
    assert stderr.getvalue() == "admission-rule-ledger: series replay failed\n"


def test_a_series_child_loading_product_code_outside_its_root_refuses(
    tmp_path: Path,
) -> None:
    # The root's package reaches into this checkout for its modules: the
    # child refuses before replaying anything.
    root = tmp_path / "source"
    package = root / "src" / "mechanical_librarian"
    package.mkdir(parents=True)
    outside = REPOSITORY / "src" / "mechanical_librarian"
    (package / "__init__.py").write_text(f"__path__.append({str(outside)!r})\n")
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    cases = _cases(database)
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", series._SERIES_CHILD, str(root)]
        + [str(database), cases],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (completed.returncode, completed.stdout) == (3, "")
    with pytest.raises(ledger.Refusal) as refused:
        series.child_series(root, database, cases)
    assert refused.value.message == "series replay failed"
    # The real tree loads the standard library from outside the root and
    # passes.
    probe = series.child_series(REPOSITORY, database, cases)
    assert probe.counts["replayed"] == 0
