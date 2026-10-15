"""Exactness of Activity's candidate reprojection and its total order (#1218).

A changed revision reprojects only affected candidates through the canonical
projector with the candidate predicate pushed into each union arm. These
oracles compare that path with a full canonical projection, and the borrower's
pages with the canonical pages, including facts that tie on time and event id.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.activity_facts import (
    ActivityFactStore,
    Candidate,
    activity_fact_bytes,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.activity_seed import SEED_NOW, _report, seed_activity
from tests.support.audiobook_reports import synthetic_audiobook_isbn_reason_report
from tests.support.description_advice import _prepared
from tests.support.field_effects import (  # noqa: F401
    _EffectEnvironment,
    effects,
)
from tests.unit.test_activity_end_to_end import _qualified_no_change_decision
from tests.unit.test_activity_fact_witness import _outcome, _pages


@dataclass
class _Ledger:
    path: Path
    root: SQLiteMetadataReportRepository
    reader: SQLiteMetadataReportRepository
    canonical: SQLiteMetadataReportRepository
    store: ActivityFactStore

    def assert_pages(self) -> None:
        served = _pages(self.reader)
        assert self.reader._field_effect_activity._activity_facts is not None
        assert served == _pages(self.canonical)

    def fresh(self) -> SQLiteMetadataReportRepository:
        borrower = open_repository(
            self.path, create=False, authority=self.root.authority
        )
        borrower.configure_activity_page_reader(ActivityFactStore())
        return borrower


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[_Ledger]:
    seeded = seed_activity(tmp_path)
    root = seeded.repository
    path = tmp_path / "activity.sqlite"
    reader = open_repository(path, create=False, authority=root.authority)
    canonical = open_repository(path, create=False, authority=root.authority)
    store = ActivityFactStore()
    reader.configure_activity_page_reader(store)
    value = _Ledger(path, root, reader, canonical, store)
    try:
        value.assert_pages()
        yield value
    finally:
        reader.close()
        canonical.close()
        root.close()


def _decision(
    root: SQLiteMetadataReportRepository, index: int
) -> tuple[ReviewCase, HumanDecision]:
    """A new report, case and owner choice, the choice not yet recorded."""
    report = _report(f"Order title {index}", f"order-book-{index}", "3", "4")
    root.publish(report, lambda: True)
    policy = root.current_resolved_metadata_policy(report.target.library)
    assert policy is not None
    proposal_set = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    root.publish_proposal(proposal_set)
    case = ReviewCase.create_from_data(
        json.loads(
            root.get_review_case(
                json.loads(root.admit_review_case(proposal_set))["review_case_id"]
            )
        )["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    return case, HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=str(proposal["destination"]),
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key=f"order-owner-choice-{index:04d}",
        occurred_at=SEED_NOW + index * 90,
    )


def _event_id(decision: HumanDecision) -> str:
    digest = hashlib.sha256(
        ("human-decision-event-v1:" + decision.decision_id).encode()
    ).hexdigest()
    return "human_decision_event_" + digest


def _tied_attempt(ledger: _Ledger, event_id: str, case: ReviewCase, at: int) -> None:
    """An SQL-valid attempt without a candidate, tied with a decision event.

    Product attempt ids are ``description_attempt_<hex>``; neither the schema
    nor a candidate-less attempt's validation requires that form.
    """
    ledger.root._connection.execute(
        "INSERT INTO description_advice_attempts VALUES(?,?,?,'codex','oracle',"
        "'failed','unavailable',?,?)",
        (
            event_id,
            "e" * 64,
            json.dumps({"review_case_id": case.review_case_id}),
            at,
            at,
        ),
    )
    ledger.root._connection.commit()


def _tied_kinds(ledger: _Ledger, event_id: str) -> list[object]:
    page = ledger.canonical.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=100
    )
    return [
        event["record"]["source_kind"]
        for event in page["events"]
        if event["event_id"] == event_id
    ]


@pytest.mark.parametrize("bulk", (False, True), ids=("single", "bulk"))
@pytest.mark.parametrize(
    "attempt_first", (False, True), ids=("attempt_reprojected", "attempt_retained")
)
def test_tied_facts_follow_the_canonical_total_order(
    ledger: _Ledger, attempt_first: bool, bulk: bool
) -> None:
    """Facts tied on time and event id order by kind and identity on every path.

    A changed refresh reprojects one of the pair while the other is retained,
    in either insertion order; ``bulk`` reprojects many other facts with it.
    """
    case, decision = _decision(ledger.root, 1)
    event_id = _event_id(decision)
    if attempt_first:
        _tied_attempt(ledger, event_id, case, decision.occurred_at)
        ledger.assert_pages()
    else:
        ledger.root.record_human_decision(decision)
        ledger.assert_pages()
    if bulk:
        for index in range(2, 40):
            ledger.root.record_human_decision(_decision(ledger.root, index)[1])
    if attempt_first:
        ledger.root.record_human_decision(decision)
    else:
        _tied_attempt(ledger, event_id, case, decision.occurred_at)
    assert _tied_kinds(ledger, event_id) == [
        "description_preparation",
        "human_decision",
    ]
    ledger.assert_pages()
    borrower = ledger.fresh()
    try:
        assert _pages(borrower) == _pages(ledger.canonical)
    finally:
        borrower.close()


def _projected(
    repository: SQLiteMetadataReportRepository, candidates: set[Candidate] | None
) -> tuple[object, list[tuple[object, ...]], str]:
    """The facts or refusal, the raw projected rows and the statement of a call."""
    activity = repository._field_effect_activity
    connection = activity._connection
    statements: list[str] = []
    connection.execute("BEGIN")
    connection.set_trace_callback(statements.append)
    try:
        try:
            outcome: object = activity._project_activity_facts(candidates, 1 << 40)
        except Exception as error:  # noqa: BLE001 - a refusal is a result.
            outcome = ("refused", type(error), str(error))
        connection.set_trace_callback(None)
        (statement,) = (value for value in statements if "raw_activity_events" in value)
        rows = connection.execute(statement).fetchall()
    finally:
        connection.set_trace_callback(None)
        connection.execute("ROLLBACK")
    return outcome, rows, statement


def _candidate(row: tuple[object, ...]) -> tuple[object, object]:
    return row[13], row[15] if row[13] == "field_effect" else row[0]


def _assert_restriction(
    repository: SQLiteMetadataReportRepository, *, facts: bool = True
) -> set[Candidate]:
    """Each candidate set projects exactly the full projection's partitions.

    The raw rows are compared before Python validation, so a row the arm
    prefilter dropped or kept wrongly shows even where validation refuses.
    """
    full, rows, _ = _projected(repository, None)
    existing = {
        cast(Candidate, _candidate(row))
        for row in rows
        if all(type(value) is str for value in _candidate(row))
    }
    if facts:
        assert type(full) is dict and set(full) == existing
    ordered = sorted(existing)
    absent = {
        ("human_decision", "human_decision_event_" + "0" * 64),
        ("isbn_refusal", "shadow_outcome_set_absent"),
        *((kind, identity + "x") for kind, identity in ordered[:3]),
        *(
            (other, identity)
            for (_, identity), (other, _) in zip(ordered, ordered[1:], strict=False)
        ),
    }
    sets = [
        *({value} for value in ordered),
        set(ordered[::2]),
        set(ordered),
        absent,
        absent | set(ordered[1::3]),
    ]
    for candidates in sets:
        outcome, selected, _ = _projected(repository, candidates)
        assert selected == [row for row in rows if _candidate(row) in candidates]
        if type(full) is dict:
            assert outcome == {
                key: value for key, value in full.items() if key in candidates
            }
    return existing


def test_seeded_candidates_project_their_whole_partitions(ledger: _Ledger) -> None:
    """Multi-event plans, owner choices, transitions and a tied attempt."""
    case, decision = _decision(ledger.root, 1)
    ledger.root.record_human_decision(decision)
    _tied_attempt(ledger, _event_id(decision), case, decision.occurred_at)
    kinds = {kind for kind, _ in _assert_restriction(ledger.reader)}
    assert kinds == {
        "description_preparation",
        "field_effect",
        "human_decision",
        "policy_transition",
    }
    plans = ledger.root._connection.execute(
        "SELECT count(*) FROM (SELECT plan_id FROM field_effect_events "
        "GROUP BY plan_id HAVING count(*)>1)"
    ).fetchone()
    assert plans[0] > 1


def test_automatic_and_no_change_candidates_project_exactly(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    root = effects.repository
    decision = _qualified_no_change_decision(effects)
    scheduler = ContinuousGuardedApplication(
        root,
        {("primary", "ebooks"): cast(GuardedFieldApplication, SimpleNamespace())},
        {("primary", "ebooks"): SimpleNamespace()},
        PersistedFieldEffectAuthority(root),
        (("primary", "ebooks"),),
        lambda: 10,
        lambda *_: None,
    )
    assert scheduler.schedule() == 0
    reader = open_repository(
        effects.database_path, create=False, authority=root.authority
    )
    try:
        existing = _assert_restriction(reader)
        assert {kind for kind, _ in existing} == {
            "automatic_decision",
            "field_effect_no_change",
            "policy_transition",
        }
        assert ("automatic_decision", decision.decision_id) in existing
    finally:
        reader.close()


def test_isbn_candidates_keep_blob_rows_of_their_own_partition_only(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    """Concatenation reads a BLOB ``outcome_set_id`` as the text it spells.

    A BLOB row therefore joins a requested text partition and must stay in
    the ISBN arm, while a BLOB row of an unrequested identity must still be
    left out by the candidate predicate.
    """
    root = effects.repository
    report = synthetic_audiobook_isbn_reason_report("conflict")
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    root.enter_shadow_preview(1)
    root.publish(report, lambda: True)
    proposal = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    root.publish_proposal(proposal)
    case_id = json.loads(root.admit_review_case(proposal))["review_case_id"]
    outcome = json.loads(root.persist_shadow_outcome(case_id, 12))
    reader = open_repository(
        effects.database_path, create=False, authority=root.authority
    )
    try:
        assert {kind for kind, _ in _assert_restriction(reader)} == {
            "isbn_refusal",
            "policy_transition",
        }
        row = list(
            root._connection.execute(
                "SELECT * FROM shadow_decision_outcomes WHERE outcome_set_id=?",
                (outcome["outcome_set_id"],),
            ).fetchone()
        )
        # The shipped SQLite's GLOB check refuses a BLOB identifier, so these
        # rows are out-of-band corruption on every supported engine.
        root._connection.execute("PRAGMA ignore_check_constraints=ON")
        twin = "shadow_outcome_set_" + "e" * 64
        for identity, at in (
            # Newer than its text twin, so it is the partition's ranked row.
            (outcome["outcome_set_id"].encode(), row[6] + 1),
            (("shadow_outcome_set_" + "f" * 64).encode(), row[6]),
            # Tied with the text twin written after it: the earliest stored
            # row ranks first, whichever index a projection reads them by.
            (twin.encode(), row[6]),
            (twin, row[6]),
        ):
            row[0], row[6] = identity, at
            root._connection.execute(
                "INSERT INTO shadow_decision_outcomes VALUES ("
                + ",".join("?" * len(row))
                + ")",
                row,
            )
        root._connection.commit()
        root._connection.execute("PRAGMA ignore_check_constraints=OFF")
        unrequested = ("isbn_refusal", "shadow_outcome_set_" + "f" * 64 + ":isbn")
        assert unrequested in _assert_restriction(reader, facts=False)
        _, rows, _ = _projected(reader, None)
        assert {_candidate(value) for value in rows if type(value[17]) is bytes} == {
            ("isbn_refusal", outcome["outcome_set_id"] + ":isbn"),
            ("isbn_refusal", twin + ":isbn"),
            unrequested,
        }
    finally:
        reader.close()


def _early_pages(repository: SQLiteMetadataReportRepository) -> list[object]:
    """Application pages of a ledger recorded at the start of time."""
    application = ContinuousGuardedApplication(
        repository,
        {},
        {},
        PersistedFieldEffectAuthority(repository),
        (("primary", "ebooks"),),
        lambda: 2000,
        lambda *_: None,
        activity_only=True,
    )
    pages = []
    for search, event_type in ((None, "all"), (None, "metadata"), ("title", "all")):
        pages.append(
            _outcome(
                partial(
                    application.activity,
                    search=search,
                    event_type=event_type,
                    range_days=90,
                    page=1,
                    page_size=100,
                )
            )
        )
    return pages


def _cover_ledger(
    repository: SQLiteMetadataReportRepository, case_id: str, tied: str, at: int
) -> str:
    """SQL-valid cover rows: a prepared consideration and automatic selections.

    One selection reuses the automatic decision ``tied`` recorded at ``at``, so
    the two decision ledgers tie in one partition. Returns the other
    selection's decision id, whose ``selected`` event the safeguard arm must
    leave to the selection arm.
    """
    connection = repository._connection
    report_id, digest = connection.execute(
        "SELECT report.report_id,report.document_digest FROM review_cases review "
        "JOIN metadata_reports report ON report.report_id=review.report_id "
        "WHERE review.review_case_id=?",
        (case_id,),
    ).fetchone()
    consideration_id = "cover_consideration_" + "a" * 64
    connection.execute(
        "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,"
        "replay_digest,document_digest,supersedes_id,occurred_at,document,"
        "currentness_document) VALUES(?,?,?,?,?,NULL,12,?,'null')",
        (
            consideration_id,
            case_id,
            report_id,
            "b" * 64,
            "c" * 64,
            json.dumps({"report_document_digest": digest, "reviewer_status": "failed"}),
        ),
    )
    decision_id = "automatic_decision_" + "d" * 64
    for selection, decision, occurred_at in (
        ("e", decision_id, 13),
        ("f", tied, at),
    ):
        connection.execute(
            "INSERT INTO cover_selection_authorities(selection_id,connection_name,"
            "library_name,item_id,kind,decision_id,consideration_id,"
            "supersedes_selection_id,occurred_at,document) "
            "VALUES(?,'primary','ebooks','item-1','automatic',?,?,NULL,?,?)",
            (
                "cover_selection_" + selection * 64,
                decision,
                consideration_id,
                occurred_at,
                json.dumps(
                    {
                        "decision_document": json.dumps(
                            {"schema_version": "cover-automatic-decision-v3"}
                        )
                    }
                ),
            ),
        )
    for index, (kind, event_decision) in enumerate(
        (("automatic_prepared", None), ("selected", decision_id))
    ):
        connection.execute(
            "INSERT INTO cover_consideration_events(event_id,consideration_id,"
            "review_case_id,kind,decision_id,occurred_at,document) "
            "VALUES(?,?,?,?,?,?,'{}')",
            (
                "cover_consideration_event_" + str(index) * 64,
                consideration_id,
                case_id,
                kind,
                event_decision,
                14 + index,
            ),
        )
    connection.commit()
    return decision_id


def test_description_and_cover_candidates_project_exactly(tmp_path: Path) -> None:
    """Both automatic decision arms, cover safeguards and description rows.

    A wrong kind or identity column in any of these arms' restrictions drops
    or admits rows here, before any validation. Where both decision ledgers
    hold one id at one time, the cover selection ranks first on every path.
    """
    repository, store, binding, candidate, deterministic = _prepared(tmp_path)
    store.admit(candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11)
    selected = _cover_ledger(
        repository,
        binding.review_case_id,
        deterministic.decision_id,
        deterministic.occurred_at,
    )
    reader = open_repository(
        tmp_path / "reports.sqlite", create=False, authority=repository.authority
    )
    try:
        existing = _assert_restriction(reader, facts=False)
        kinds = [kind for kind, _ in existing]
        assert {kind: kinds.count(kind) for kind in set(kinds)} == {
            # The deterministic decision and the cover selection.
            "automatic_decision": 2,
            "cover_safeguard": 1,
            "description_decision": 1,
            "description_preparation": 1,
            "policy_transition": 2,
        }
        assert ("automatic_decision", selected) in existing
        _, rows, _ = _projected(reader, None)
        (tied,) = (
            row
            for row in rows
            if _candidate(row) == ("automatic_decision", deterministic.decision_id)
        )
        assert tied[2] == "cover"
    finally:
        reader.close()
        repository.close()


@pytest.mark.parametrize(
    "invalid_first", (False, True), ids=("valid_stored_first", "invalid_stored_first")
)
def test_tied_partition_rows_rank_the_earliest_stored_first(
    tmp_path: Path, invalid_first: bool
) -> None:
    """Two candidates of one attempt tie in its partition, out of band.

    Nothing makes ``attempt_id`` unique. The earliest stored candidate ranks
    first whichever order a plan reads them in, so a reprojected candidate and
    the canonical page agree; an invalid winner refuses on both.
    """
    repository, _, _, _, _ = _prepared(tmp_path)
    path = tmp_path / "reports.sqlite"
    reader = open_repository(path, create=False, authority=repository.authority)
    canonical = open_repository(path, create=False, authority=repository.authority)
    reader.configure_activity_page_reader(ActivityFactStore())
    try:
        assert _early_pages(reader) == _early_pages(canonical)
        attempt, request = repository._connection.execute(
            "SELECT attempt_id,request_identity FROM description_advice_candidates"
        ).fetchone()
        # An invalid document that sorts before the stored candidate's.
        repository._connection.execute(
            "INSERT INTO description_advice_candidates"
            "(rowid,candidate_id,attempt_id,request_identity,document) "
            "VALUES(?,'zzz',?,?,'{\"!\":1}')",
            (0 if invalid_first else None, attempt, request),
        )
        repository._connection.commit()
        served = _early_pages(reader)
        expected = _early_pages(canonical)
        assert [type(page) is tuple for page in expected] == [invalid_first] * 3
        assert served == expected
        _assert_restriction(reader, facts=not invalid_first)
    finally:
        reader.close()
        canonical.close()
        repository.close()


def _letters(pages: list[object]) -> str:
    return "".join(
        cast(type, page[1]).__name__[0] if type(page) is tuple else "."
        for page in pages
    )


@pytest.mark.parametrize("fresh", (False, True), ids=("reprojected", "constructed"))
@pytest.mark.parametrize(
    "identifier", ("\x00z", ""), ids=("nul_identifier", "mismatched_identifier")
)
def test_an_invalid_isbn_outcome_refuses_only_the_reads_that_select_it(
    effects: _EffectEnvironment,  # noqa: F811
    identifier: str,
    fresh: bool,
) -> None:
    """A newer outcome copied under another id fails its own validation.

    A NUL suffix also defeats SQLite's text functions, so no reprojection can
    name its partition. Either way the borrower falls back and serves exactly
    the canonical pages, refusing only where canonical refuses.
    """
    root = effects.repository
    report = synthetic_audiobook_isbn_reason_report("conflict")
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    root.enter_shadow_preview(1)
    root.publish(report, lambda: True)
    proposal = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    root.publish_proposal(proposal)
    case_id = json.loads(root.admit_review_case(proposal))["review_case_id"]
    outcome = json.loads(root.persist_shadow_outcome(case_id, SEED_NOW))
    reader = open_repository(
        effects.database_path, create=False, authority=root.authority
    )
    canonical = open_repository(
        effects.database_path, create=False, authority=root.authority
    )
    reader.configure_activity_page_reader(ActivityFactStore())
    try:
        assert _pages(reader) == _pages(canonical)
        row = list(
            root._connection.execute(
                "SELECT * FROM shadow_decision_outcomes WHERE outcome_set_id=?",
                (outcome["outcome_set_id"],),
            ).fetchone()
        )
        row[0] = "shadow_outcome_set_" + "d" * 64 + identifier
        row[6] = SEED_NOW + 60
        root._connection.execute(
            "INSERT INTO shadow_decision_outcomes VALUES ("
            + ",".join("?" * len(row))
            + ")",
            row,
        )
        root._connection.commit()
        if fresh:
            reader.close()
            reader = open_repository(
                effects.database_path, create=False, authority=root.authority
            )
            reader.configure_activity_page_reader(ActivityFactStore())
        expected = _pages(canonical)
        assert _letters(expected) == "MMM..M..MG"
        assert _pages(reader) == expected
    finally:
        reader.close()
        canonical.close()


def test_candidate_projection_drives_every_arm_from_its_identity(
    ledger: _Ledger,
) -> None:
    """Each union arm searches its own key; nothing scans a source table.

    The field-effect arm is driven from the requested plan, and the ISBN arm
    searches its identifier index (a multi-index OR for the BLOB clause), so
    only the requested partitions are read.
    """
    kinds = (
        "field_effect",
        "human_decision",
        "automatic_decision",
        "description_preparation",
        "description_decision",
        "field_effect_no_change",
        "cover_safeguard",
        "policy_transition",
    )
    candidates = {(kind, kind + "_absent") for kind in kinds}
    candidates.add(("isbn_refusal", "shadow_outcome_set_absent:isbn"))
    _, _, statement = _projected(ledger.reader, candidates)
    plan = [
        str(row[3])
        for row in ledger.reader._field_effect_activity._connection.execute(
            "EXPLAIN QUERY PLAN " + statement
        )
    ]
    index = "SEARCH {} USING INDEX sqlite_autoindex_{} ({}=?)"
    assert {
        index.format("effect", "field_effects_1", "plan_id"),
        index.format("event", "field_effect_events_2", "plan_id"),
        index.format("event", "human_decision_events_1", "event_id"),
        index.format("decision", "automatic_decisions_1", "decision_id"),
        index.format("attempt", "description_advice_attempts_1", "attempt_id"),
        index.format("decision", "description_decisions_1", "decision_id"),
        index.format("selection", "cover_selection_authorities_2", "decision_id"),
        index.format("no_change", "field_effect_no_changes_2", "event_id"),
        index.format("event", "cover_consideration_events_1", "event_id"),
        index.format("transition", "metadata_policy_transitions_1", "transition_id"),
        "MULTI-INDEX OR",
        "SEARCH outcome USING INDEX "
        "sqlite_autoindex_shadow_decision_outcomes_1 (outcome_set_id=?)",
        "SEARCH outcome USING INDEX "
        "sqlite_autoindex_shadow_decision_outcomes_1 (outcome_set_id>?)",
    } <= set(plan)
    scans = {re.sub(r"\(subquery-\d+\)", "(subquery)", step) for step in plan}
    assert {step for step in scans if step.startswith("SCAN ")} <= {
        "SCAN json_each VIRTUAL TABLE INDEX 1:",
        "SCAN requested",
        # The unindexed description candidate join, per requested attempt.
        "SCAN candidate LEFT-JOIN",
        "SCAN field VIRTUAL TABLE INDEX 3:",
        "SCAN raw_activity_events",
        "SCAN (subquery)",
        "SCAN ranked_activity_events",
        # The selected rows, outside the union.
        "SCAN event",
    }
    assert plan.count("SCAN event") == 1


def test_every_activity_order_ends_in_the_total_key(ledger: _Ledger) -> None:
    """Pages, search and collection all order by the canonical total key."""
    connection = ledger.canonical._field_effect_activity._connection
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        for search, page in ((None, 1), (None, 999), ("title", 1)):
            ledger.canonical.field_effect_activity_page(
                search=search,
                event_type="all",
                occurred_after=0,
                page=page,
                page_size=25,
            )
    finally:
        connection.set_trace_callback(None)
    activity = [value for value in statements if "raw_activity_events" in value]
    activity += [
        _projected(ledger.reader, candidates)[2]
        for candidates in ({("human_decision", "absent")}, None)
    ]
    orders = [
        match
        for statement in activity
        for match in re.findall(
            r"occurred_at DESC,(?:event\.)?event_id DESC(.{0,120})", statement
        )
    ]
    tie = re.compile(
        r"^,(event\.)?source_kind,CASE WHEN (event\.)?source_kind='field_effect' "
        r"THEN (event\.)?source_parent_id ELSE (event\.)?event_id END"
    )
    # Within a partition the explicit tiebreak, not scan order, decides.
    ranked = [
        order
        for order in orders
        if order.startswith(",source_tiebreak) AS activity_rank")
    ]
    assert len(ranked) == len(activity)
    assert len(orders) > len(ranked)
    assert all(tie.match(order) for order in orders if order not in ranked)


def test_fact_bytes_stay_the_sum_of_the_facts_through_every_refresh(
    ledger: _Ledger,
) -> None:
    """Retained bytes are carried forward by arithmetic rather than re-summed.

    Insert, change, remove, a new borrower generation's full witness, a
    canonical fallback and its re-admission each leave the carried figure
    equal to a sum over the snapshot's facts, and the facts in total order.
    """

    def check(repository: SQLiteMetadataReportRepository) -> None:
        assert _pages(repository) == _pages(ledger.canonical)
        snapshot = ledger.store.snapshot()
        assert snapshot is not None
        assert snapshot.fact_bytes == sum(
            activity_fact_bytes(key, fact) for key, fact in snapshot.facts.items()
        )
        assert snapshot.ordered == tuple(
            fact
            for _, fact in sorted(
                sorted(snapshot.facts.items(), key=lambda item: item[0]),
                key=lambda item: (item[1].occurred_at, item[1].event_id),
                reverse=True,
            )
        )

    check(ledger.reader)
    case, decision = _decision(ledger.root, 1)
    ledger.root.record_human_decision(decision)
    check(ledger.reader)
    ledger.root._connection.execute(
        "UPDATE metadata_policy_transitions SET updated_at=updated_at+1"
    )
    ledger.root._connection.commit()
    check(ledger.reader)
    _tied_attempt(ledger, _event_id(decision), case, decision.occurred_at)
    check(ledger.reader)
    ledger.root._connection.execute("DELETE FROM description_advice_attempts")
    ledger.root._connection.commit()
    check(ledger.reader)
    generation = ledger.fresh()
    try:
        generation.close()
        generation = open_repository(
            ledger.path, create=False, authority=ledger.root.authority
        )
        generation.configure_activity_page_reader(ledger.store)
        ledger.root.record_human_decision(_decision(ledger.root, 2)[1])
        check(generation)
        # A BLOB attempt identifier refuses the projection, so the borrower
        # falls back to the canonical page; removing it lets a new
        # generation re-admit the retained facts.
        ledger.root._connection.execute(
            "INSERT INTO description_advice_attempts VALUES(?,?,?,'codex','oracle',"
            "'failed','unavailable',?,?)",
            (
                b"description_attempt_" + b"a" * 32,
                "e" * 64,
                json.dumps({"review_case_id": case.review_case_id}),
                SEED_NOW,
                SEED_NOW,
            ),
        )
        ledger.root._connection.commit()
        assert _pages(generation) == _pages(ledger.canonical)
        assert generation._field_effect_activity._activity_facts is None
        ledger.root._connection.execute("DELETE FROM description_advice_attempts")
        ledger.root._connection.commit()
    finally:
        generation.close()
    readmitted = open_repository(
        ledger.path, create=False, authority=ledger.root.authority
    )
    try:
        readmitted.configure_activity_page_reader(ledger.store)
        check(readmitted)
        assert readmitted._field_effect_activity._activity_facts is not None
    finally:
        readmitted.close()
