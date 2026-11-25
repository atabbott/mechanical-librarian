"""Every place Automatic is decided checks exactly the conditions it declares.

`AUTOMATIC_SITE_CONDITIONS` names, per site, the conditions under which
Automatic may act there. Each site is driven through its real call over the
posture and safety matrix: three postures, paused or not, a global suspension
or none, and a category document suspending the site's own lane, another lane
or none. Conditions a site reads beyond those are varied where a seam allows,
and held true by the fixture elsewhere. Each row runs inside one outer
transaction that the repository's own writes nest in as savepoints, and rolls
back with them. A missing or malformed state row is answered per site, outside
the declared sets; those answers are frozen as main gives them (#2036).
"""

from __future__ import annotations

import itertools
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import PersistedEffectConsent
from mechanical_librarian.adapters.persistence import (
    description_advice,
    field_effect_activity,
    review_sql,
    schema,
)
from mechanical_librarian.adapters.persistence.cover_considerations import (
    expand_cover_consideration,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.description_advice import (
    DescriptionAdviceService,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.automatic_decision import (
    AUTOMATIC_SITE_CONDITIONS,
    AutomaticCondition,
    automatic_safety_category,
    automatic_safety_row_permits,
)
from mechanical_librarian.domain.cover_evidence import CoverDesignation
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.support.author_evidence import author_report
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.description_advice import _prepared, qualify_description_advice
from tests.support.prepared_library import accept as _accept
from tests.unit.test_cover_activation import (
    _measured_record,
    _pipeline_configuration,
    _qualified_cover_report,
    _run_qualified_cover_pipeline,
    _test_reviewer_access,
)
from tests.unit.test_description_advice_persistence import _publish_successor

C = AutomaticCondition
_REPOSITORY = Path(__file__).resolve().parents[2]
_POSTURES = ("review", "preview", "automatic")
_OTHER_LANE = "date_precision"
# An edge row: the safety row missing, the posture row missing, or a
# malformed category document while paused (a trigger refuses one otherwise).
_EDGES = ("no_safety", "no_posture", "malformed")


@contextmanager
def _state(
    connection: sqlite3.Connection,
    posture: str,
    paused: int,
    suspension: str | None,
    document: str,
) -> Iterator[None]:
    connection.execute("BEGIN")
    try:
        connection.execute("UPDATE metadata_policy_state SET posture=?", (posture,))
        connection.execute(
            "UPDATE automatic_safety_state SET paused=?,global_suspension=?,"
            "category_suspensions_document=?",
            (paused, suspension, document),
        )
        yield
    finally:
        connection.execute("ROLLBACK")


@contextmanager
def _edge(connection: sqlite3.Connection, edge: str) -> Iterator[None]:
    connection.execute("BEGIN")
    try:
        connection.execute("UPDATE metadata_policy_state SET posture='automatic'")
        if edge == "no_safety":
            connection.execute("DELETE FROM automatic_safety_state")
        elif edge == "no_posture":
            connection.execute("DELETE FROM metadata_policy_state")
        else:
            connection.execute(
                "UPDATE automatic_safety_state SET paused=1,"
                "category_suspensions_document='not json'"
            )
        yield
    finally:
        connection.execute("ROLLBACK")


def _truth(
    posture: str, paused: int, suspension: str | None, lanes: tuple[str, ...], own: str
) -> dict[AutomaticCondition, bool]:
    truth = dict.fromkeys(AutomaticCondition, True)
    truth[C.POSTURE] = posture == "automatic"
    truth[C.NOT_PAUSED] = not paused
    truth[C.NOT_SUSPENDED] = suspension is None
    truth[C.CATEGORY_NOT_SUSPENDED] = own not in lanes
    return truth


def _mismatches(
    site: str,
    connection: sqlite3.Connection,
    own: str,
    acts: Callable[[], bool],
    held: dict[AutomaticCondition, bool] | None = None,
) -> list[tuple[object, ...]]:
    """Rows where the site's observed answer differs from its declared set."""
    found: list[tuple[object, ...]] = []
    for posture, paused, suspension, lanes in itertools.product(
        _POSTURES,
        (0, 1),
        (None, "automatic_authority_failed"),
        ((), (own,), (_OTHER_LANE,)),
    ):
        truth = _truth(posture, paused, suspension, lanes, own) | (held or {})
        expected = all(truth[c] for c in AUTOMATIC_SITE_CONDITIONS[site])
        document = json.dumps([[lane, "reason"] for lane in lanes])
        with _state(connection, posture, paused, suspension, document):
            observed = acts()
        if observed != expected:
            found.append((posture, paused, suspension, lanes, held, observed))
    return found


def _edges(connection: sqlite3.Connection, acts: Callable[[], bool]) -> list[str]:
    answers = []
    for edge in _EDGES:
        with _edge(connection, edge):
            try:
                answers.append("acts" if acts() else "refuses")
            except Exception as error:  # the answer is the type raised
                answers.append(type(error).__name__)
    return answers


def _store(tmp_path: Path, *, qualified: bool) -> SQLiteMetadataReportRepository:
    os.chmod(tmp_path, 0o700)
    repository = open_repository(tmp_path / "reports.sqlite", cross_thread=True)
    _automatic_decisions(
        SimpleNamespace(repository=repository),
        author_report(tmp_path / "media", "supported"),
        "contributor_names",
        "canonical",
        qualified=qualified,
    )
    return repository


def _schedule(repository: SQLiteMetadataReportRepository, root: Path) -> None:
    repository.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    authority = PersistedFieldEffectAuthority(repository)
    capability = FakeCapabilityObserver("c" * 64)
    service = GuardedFieldApplication(
        repository,
        FakeTargetService(),
        FakeBackupReadiness(),
        capability,
        authority,
        FakeDependencyCurrentness(),
        PersistedEffectConsent(repository),
        PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old"),
        FakeForwardReevaluation(),
        lambda: 13,
    )
    ContinuousGuardedApplication(
        repository,
        {("primary", "ebooks"): service},
        {("primary", "ebooks"): capability},
        authority,
        (("primary", "ebooks"),),
        lambda: 13,
        lambda *_: None,
    ).schedule()


def test_the_shared_sql_terms_equal_the_shared_predicates(tmp_path: Path) -> None:
    """P14a: the generated fragments answer as the Python definition does."""
    repository = _store(tmp_path, qualified=False)
    connection = repository._connection
    blocked = (
        "SELECT " + review_sql._AUTOMATIC_SAFETY_BLOCKED_SQL + " FROM "
        "(SELECT ? AS policy_category) outcome,automatic_safety_state safety"
    )
    attention = (
        "SELECT result.destination,"
        + review_sql._AUTOMATIC_SAFETY_ATTENTION_SQL
        + " FROM review_cases rc JOIN review_case_field_facts result "
        "ON result.review_case_id=rc.review_case_id ORDER BY 1"
    )
    facts = {
        destination: (automatic == 1, category)
        for destination, automatic, category in connection.execute(
            "SELECT destination,automatic_destination,policy_category "
            "FROM shadow_outcome_field_facts"
        )
    }
    rows = 0
    for posture, paused, suspension, lanes in itertools.product(
        _POSTURES,
        (0, 1),
        (None, "automatic_authority_failed"),
        ((), ("title_layout",), ("contributor_names",), ("title_layout", "x")),
    ):
        document = json.dumps([[lane, "reason"] for lane in lanes])
        with _state(connection, posture, paused, suspension, document):
            row = (paused, suspension, document)
            assert connection.execute(
                "SELECT " + schema._AUTOMATIC_POSTURE_SQL
            ).fetchone() == (posture == "automatic",)
            for category in ("title", "title_layout", "contributor_names", "tags"):
                lane = automatic_safety_category(category, "")
                assert connection.execute(blocked, (category,)).fetchone() == (
                    not automatic_safety_row_permits(row, lane),
                )
            for destination, value in connection.execute(attention):
                automatic, category = facts.get(destination, (False, ""))
                lane = automatic_safety_category(category, "")
                assert value == (
                    automatic and not automatic_safety_row_permits(row, lane)
                ), destination
            rows += 1
    assert rows == 48 and facts["authors"][0]
    repository.close()


def test_field_sites_check_their_declared_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P14b: the mint, pending, held-back, deferral, authority and claim sites."""
    repository = _store(tmp_path, qualified=True)
    connection = repository._connection
    (document,) = connection.execute(
        "SELECT document FROM automatic_decisions WHERE destination='authors'"
    ).fetchone()
    decision_id = json.loads(document)["decision_id"]
    case_id = json.loads(document)["review_case_id"]
    (outcome_document,) = connection.execute(
        "SELECT document FROM shadow_decision_outcomes"
    ).fetchone()
    outcome = ShadowOutcomeSet.from_json(outcome_document)
    activity = repository._field_effect_activity
    own = "contributor_names"
    minted: list[int] = []
    monkeypatch.setattr(
        repository, "_record_automatic_outcomes", lambda _o, at: minted.append(at)
    )

    def mints() -> bool:
        minted.clear()
        repository.persist_shadow_outcome(case_id, 60)
        return bool(minted)

    def decides() -> bool:
        validated = repository._decisions._validated_automatic_decisions(outcome, 60)
        return "authors" in {value.destination for value in validated}

    observed: dict[str, Callable[[], bool]] = {
        "mint_posture": mints,
        "mint_field": decides,
        "pending_counts": lambda: any(
            row["origin"] == "automatic"
            for row in repository.pending_field_effect_decision_counts()
        ),
        "pending_documents": lambda: (
            decision_id
            in {
                json.loads(value)["decision_id"]
                for value in repository.pending_field_effect_decision_documents(100)
            }
        ),
        "held_back_note": lambda: (
            not activity._activity_page_notes_once([], ["effect_event_x"])
        ),
    }
    for site, acts in observed.items():
        assert _mismatches(site, connection, own, acts) == [], site
    assert {site: _edges(connection, acts) for site, acts in observed.items()} == {
        "mint_posture": ["acts", "MetadataReportError", "acts"],
        "mint_field": ["MetadataReportError", "acts", "MetadataReportError"],
        "pending_counts": ["acts", "refuses", "acts"],
        "pending_documents": ["acts", "refuses", "acts"],
        "held_back_note": ["acts", "acts", "acts"],
    }

    _schedule(repository, tmp_path)
    (plan,) = (
        FieldEffectOperation.from_json(value).plan
        for (value,) in connection.execute("SELECT document FROM field_effects")
    )

    def claims() -> bool:
        try:
            repository.claim_field_effect(plan.plan_id, "planned", "c" * 64, 14)
        except FieldEffectError as error:
            if str(error) != "authority":
                raise
            return False
        return True

    planned: dict[str, Callable[[], bool]] = {
        "authority_current": lambda: repository.field_effect_authority_is_current(
            plan.authority
        ),
        "deferred": lambda: not repository.field_effect_deferred(plan.plan_id),
        "claim": claims,
    }
    for site, acts in planned.items():
        assert _mismatches(site, connection, own, acts) == [], site
        with monkeypatch.context() as stale:
            stale.setattr(
                field_effect_activity,
                "qualification_manifest_is_current",
                lambda *_: False,
            )
            held = {C.QUALIFICATION_CURRENT: False}
            assert _mismatches(site, connection, own, acts, held) == [], site
    assert {site: _edges(connection, acts) for site, acts in planned.items()} == {
        "authority_current": ["acts", "refuses", "acts"],
        "deferred": ["acts", "refuses", "acts"],
        "claim": ["refuses", "MetadataReportError", "JSONDecodeError"],
    }

    # The held note reads an answer the record holds: the owner's own, on
    # this case's authors (ADR-0108).
    repository.record_human_decision(
        _accept(_case(repository, case_id), "authors", "held-note-answer", 61)
    )

    def held_note() -> bool:
        notes = activity._activity_page_notes_once([], [], (), [decision_id])
        return notes.get(decision_id) == "held"

    assert _mismatches("held_note_field", connection, own, held_note) == []
    assert _edges(connection, held_note) == ["acts", "refuses", "acts"]
    repository.close()


def _case(repository: SQLiteMetadataReportRepository, case_id: str) -> ReviewCase:
    return ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )


def _resume_offer_acts(
    repository: SQLiteMetadataReportRepository,
    case_id: str,
    answer: str,
    scan: str,
    destination: str = "authors",
) -> Callable[[], bool]:
    """Whether the case offers Resume for the field, and the request agrees:
    asked inside the row's transaction, it is accepted exactly then."""

    def acts() -> bool:
        offered = (
            destination
            in repository._review_projections._record_states(case_id)[
                "resume_offer_destinations"
            ]
        )
        requested = repository.request_owner_field_resume(
            case_id, destination, answer, scan, 62, 62
        )
        assert requested == offered
        return offered

    return acts


def _resume_offer_store(
    tmp_path: Path,
) -> tuple[SQLiteMetadataReportRepository, Callable[[], bool]]:
    """Automatic authors the owner answered, with a scan to ask Resume on."""
    repository = _store(tmp_path, qualified=True)
    (case_id,) = repository._connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages"
    ).fetchone()
    answer = _accept(_case(repository, case_id), "authors", "resume-offer-answer", 61)
    repository.record_human_decision(answer)
    scan = repository.request_review_scan(
        "primary", "ebooks", "library-ebooks", "a" * 64, 61
    ).scan_id
    return repository, _resume_offer_acts(repository, case_id, answer.decision_id, scan)


def test_the_resume_offer_checks_its_declared_set(tmp_path: Path) -> None:
    """C0 `resume_offer`: the page offers Resume, and the request is taken,
    exactly while Automatic is on and the case sets the field to Automatic;
    a pause, a suspension or a stale qualification hides neither."""
    repository, acts = _resume_offer_store(tmp_path)
    connection = repository._connection
    assert _mismatches("resume_offer", connection, "contributor_names", acts) == []

    def field_not_automatic() -> bool:
        connection.execute(
            "UPDATE shadow_outcome_field_facts SET automatic_destination=0 "
            "WHERE destination='authors'"
        )
        return acts()

    held = {C.FIELD_AUTOMATIC: False}
    assert (
        _mismatches(
            "resume_offer", connection, "contributor_names", field_not_automatic, held
        )
        == []
    )
    assert _edges(connection, acts) == ["acts", "refuses", "acts"]
    repository.close()


def test_attention_sites_check_their_declared_sets(tmp_path: Path) -> None:
    """P14b: the persisted Attention facts and the case's held destinations.

    In Preview nothing is decided, so the Automatic authors field asks for the
    owner exactly while Automatic is held there.
    """
    repository = _store(tmp_path, qualified=False)
    connection = repository._connection
    (case_id,) = connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages"
    ).fetchone()
    held = repository._review_projections._automatic_attention_destinations

    def asked() -> set[object]:
        return {row[0] for row in connection.execute(_FACT_DESTINATIONS_SQL)}

    acts = {
        "attention": lambda: "authors" not in asked(),
        "attention_destinations": lambda: "authors" not in held(case_id),
    }
    for site, observed in acts.items():
        assert _mismatches(site, connection, "contributor_names", observed) == []
    assert {site: _edges(connection, observed) for site, observed in acts.items()} == {
        "attention": ["acts", "acts", "refuses"],
        "attention_destinations": ["acts", "acts", "refuses"],
    }
    # A field Automatic does not own is never held, whatever the safety state.
    lanes = json.dumps([["language_rendering", "r"], ["title_layout", "r"]])
    for posture, paused in itertools.product(_POSTURES, (0, 1)):
        with _state(connection, posture, paused, "automatic_authority_failed", lanes):
            assert held(case_id) == ("authors",)
    repository.close()


_FACT_DESTINATIONS_SQL = "SELECT DISTINCT destination FROM review_attention_facts"


class _ApplicationOff:
    """Settings as bound, but with automatic application turned off."""

    automatic_application = False

    def __init__(self, settings: object) -> None:
        self._settings = settings

    def __getattr__(self, name: str) -> Any:
        return getattr(self._settings, name)


def test_description_sites_check_their_declared_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P14b: description advice admission, its authority and its held note."""
    qualify_description_advice(monkeypatch)
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    connection = repository._connection
    settings = store.current_settings

    def admits() -> bool:
        try:
            store.admit(
                candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
            )
        except ValueError as error:
            if str(error) != "description automatic admission unavailable":
                raise
            return False
        return True

    variations: tuple[tuple[str, object, AutomaticCondition], ...] = (
        (
            "description_advice_qualified",
            lambda _candidate: False,
            C.QUALIFICATION_CURRENT,
        ),
        ("automatic_description_floor_met", lambda _text: False, C.DESCRIPTION_FLOOR),
    )

    def check(site: str, acts: Callable[[], bool]) -> None:
        assert _mismatches(site, connection, "description", acts) == [], site
        for name, value, condition in variations:
            with monkeypatch.context() as varied:
                varied.setattr(description_advice, name, value)
                held = {condition: False}
                assert _mismatches(site, connection, "description", acts, held) == []
        with monkeypatch.context() as varied:
            varied.setattr(
                store, "current_settings", lambda: _ApplicationOff(settings())
            )
            held = {C.DESCRIPTION_APPLICATION: False}
            assert _mismatches(site, connection, "description", acts, held) == []

    check("description_admission", admits)
    with monkeypatch.context() as bypassed:
        bypassed.setattr(
            description_advice, "automatic_safety_row_permits", lambda *_: True
        )
        skipped = _mismatches(
            "description_admission", connection, "description", admits
        )
    # The negative control: admission that skips safety admits each Automatic
    # row safety refuses.
    assert {row[0] for row in skipped} == {"automatic"} and len(skipped) == 10
    assert _edges(connection, admits) == ["refuses", "ValueError", "refuses"]

    decision = store.admit(
        candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
    )
    authority = PersistedFieldEffectAuthority(repository)

    def resolves() -> bool:
        try:
            authority.resolve(decision)
        except FieldEffectError as error:
            if str(error) != "authority_binding":
                raise
            return False
        return True

    check("description_authority", resolves)
    assert _edges(connection, resolves) == ["refuses", "refuses", "refuses"]

    repository.close()


def test_the_held_description_note_checks_its_declared_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`held_note_description`: Automatic's advice admitted beside the owner's
    description the record holds reads held exactly while the posture and the
    field's Automatic setting stand (ADR-0108)."""
    qualify_description_advice(monkeypatch)
    access: list[Any] = []
    repository, store, binding, original, _ = _prepared(
        tmp_path, pipeline_probe=lambda *values: access.append(values[3])
    )
    connection = repository._connection
    store.admit(original.candidate_id, binding, "human", "e" * 64, 11)
    report, fresh = _publish_successor(repository, binding)
    repository.persist_shadow_outcome(fresh.review_case_id, 12)
    candidate = DescriptionAdviceService(
        store, access[0], store.current_settings, lambda: 12
    ).prepare(report, fresh, lambda: store.binding_is_current(fresh))
    assert candidate is not None
    decision = store.admit(
        candidate.candidate_id, fresh, "automatic_policy", "a" * 64, 15
    )

    def held_note() -> bool:
        notes = repository._field_effect_activity._activity_page_notes_once(
            [], [], (), [decision.decision_id]
        )
        return notes.get(decision.decision_id) == "held"

    assert (
        _mismatches("held_note_description", connection, "description", held_note) == []
    )
    assert _edges(connection, held_note) == ["acts", "refuses", "acts"]
    repository.close()


def test_cover_admission_checks_its_declared_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P14b: the basis an automatic cover fill or replacement is minted on."""
    _measured_record(monkeypatch)
    os.chmod(tmp_path, 0o700)
    database = tmp_path / "reports.sqlite"
    repository = open_repository(database)
    report, _ = _qualified_cover_report(
        tmp_path,
        incumbent_size=(200, 300),
        candidate_size=(3000, 4500),
        candidate_designation=CoverDesignation.EXACT_EDITION,
        typed_timeout=True,
    )
    options = dict(DEFAULT_OPTIONS) | {"cover_order": "fixed_source_order"}
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    } | {"cover_order": "automatic"}
    profile = MetadataPolicyProfile.create("Covers automatic", options, destinations)
    resolved = resolve_policy(profile, report.target.library)
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.enter_shadow_preview(1, ())
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    repository.activate_qualified_automatic(3)
    supervisor = composition.create_review_pipeline(
        _pipeline_configuration(),
        database,
        tmp_path / "catalogue.sqlite",
        tmp_path / "artifacts",
        _test_reviewer_access(repository),
        database_authority=repository.authority,
        provider_recovery=composition.SQLiteProviderRecoveryStore.open(
            tmp_path / "provider-recovery.sqlite"
        ),
    )
    with patch.object(composition, "run_cover_packet_reviewer"):
        pipeline = _run_qualified_cover_pipeline(
            repository, tmp_path, report, proposal, supervisor
        )
        while pipeline.run_next(1):
            pass
    connection = repository._connection
    row = connection.execute(
        "SELECT consideration.document,report.document,report.document_digest,"
        "consideration.document_digest,consideration.report_id "
        "FROM current_cover_considerations current JOIN cover_considerations "
        "consideration ON consideration.consideration_id=current.consideration_id "
        "LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id"
    ).fetchone()
    document = expand_cover_consideration(*row)
    considerations = repository._cover_considerations

    def admits() -> bool:
        return considerations._cover_admission_basis(document) is not None

    assert _mismatches("cover_admission", connection, "cover_order", admits) == []
    assert _edges(connection, admits) == ["refuses", "refuses", "refuses"]
    repository.close()


def test_a_site_that_skips_a_declared_condition_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control: bypassing one site's posture term is a mismatch."""
    repository = _store(tmp_path, qualified=True)
    connection = repository._connection

    def pending() -> bool:
        return any(
            json.loads(value)["destination"] == "authors"
            for value in repository.pending_field_effect_decision_documents(100)
        )

    assert (
        _mismatches("pending_documents", connection, "contributor_names", pending) == []
    )
    # The documents statement composes the posture term when it is called.
    monkeypatch.setattr(field_effect_activity, "_AUTOMATIC_POSTURE_SQL", "1")
    bypassed = _mismatches(
        "pending_documents", connection, "contributor_names", pending
    )
    assert {row[0] for row in bypassed} == {"review", "preview"}
    assert len(bypassed) == 24
    repository.close()


def test_the_description_resume_offer_reads_its_advice_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C0 `resume_offer` for the description: the field is Automatic by the
    owner's Automatic description advice setting, which admits Automatic's
    description, not by the policy's category (C2b-13). Here the policy sets
    no Automatic description, the owner kept the description on the work's
    earlier case, and Automatic's advice is recorded on its next case: Resume
    is offered and taken exactly while Automatic is on and the setting is,
    saved or, before any owner settings are saved, bound."""
    qualify_description_advice(monkeypatch)
    access: list[Any] = []
    repository, store, binding, _, _ = _prepared(
        tmp_path,
        automatic_policy=("cover_order", "fixed_source_order"),
        pipeline_probe=lambda *values: access.append(values[3]),
    )
    connection = repository._connection
    case = _case(repository, binding.review_case_id)
    proposal = next(
        value for value in case.proposals if value["destination"] == "description"
    )
    owner = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="description",
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="owner-keeps-description",
        occurred_at=11,
    )
    repository.record_human_decision(owner)
    report, fresh = _publish_successor(repository, binding)
    candidate = DescriptionAdviceService(
        store, access[0], store.current_settings, lambda: 12
    ).prepare(report, fresh, lambda: store.binding_is_current(fresh))
    assert candidate is not None
    store.admit(candidate.candidate_id, fresh, "automatic_policy", "a" * 64, 15)
    assert connection.execute(
        "SELECT automatic_destination FROM shadow_outcome_field_facts "
        "WHERE review_case_id=? AND destination='description'",
        (fresh.review_case_id,),
    ).fetchall() in ([], [(0,)])
    (connection_name, library_name) = connection.execute(
        "SELECT connection_name,library_name FROM current_review_case_lineages "
        "WHERE review_case_id=?",
        (fresh.review_case_id,),
    ).fetchone()
    scan = repository.request_review_scan(
        connection_name, library_name, f"library-{library_name}", "a" * 64, 16
    ).scan_id
    acts = _resume_offer_acts(
        repository, fresh.review_case_id, owner.decision_id, scan, "description"
    )
    bound = store.current_settings()
    assert bound.automatic_application is True

    def saved(application: bool) -> Callable[[], bool]:
        def with_saved() -> bool:
            connection.execute(
                "INSERT INTO owner_settings(singleton,revision,document) VALUES(1,1,?)",
                (
                    json.dumps(
                        {"description_advice": {"automatic_application": application}}
                    ),
                ),
            )
            return acts()

        return with_saved

    def unbound() -> bool:
        store.bind_settings(bound.model_copy(update={"automatic_application": False}))
        try:
            return acts()
        finally:
            store.bind_settings(bound)

    off = {C.FIELD_AUTOMATIC: False}
    assert _mismatches("resume_offer", connection, "description", acts) == []
    assert _mismatches("resume_offer", connection, "description", unbound, off) == []
    assert (
        _mismatches("resume_offer", connection, "description", saved(False), off) == []
    )

    def saved_over_unbound() -> bool:
        store.bind_settings(bound.model_copy(update={"automatic_application": False}))
        try:
            return saved(True)()
        finally:
            store.bind_settings(bound)

    assert (
        _mismatches("resume_offer", connection, "description", saved_over_unbound) == []
    )
    assert _edges(connection, acts) == ["acts", "refuses", "acts"]
    repository.close()


def test_a_resume_offer_that_skips_its_posture_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control at `resume_offer`: the offer and the request's
    re-check compose the posture term when called, so bypassing it offers and
    takes Resume in Review and Preview, which the declared set refuses."""
    repository, acts = _resume_offer_store(tmp_path)
    connection = repository._connection
    assert _mismatches("resume_offer", connection, "contributor_names", acts) == []
    monkeypatch.setattr(schema, "_AUTOMATIC_POSTURE_SQL", "1")
    bypassed = _mismatches("resume_offer", connection, "contributor_names", acts)
    assert {row[0] for row in bypassed} == {"review", "preview"}
    assert len(bypassed) == 24
    repository.close()


# A spelling of a shared condition, and the sources allowed to keep one.
_SPELLINGS = (
    "posture='automatic'",
    "SELECT paused,global_suspension,category_suspensions_document",
    "THEN 'title_layout'",
    "safety.paused=1",
    "json.loads(safety[2])",
)
_ALLOWED = {
    # The shared definitions themselves.
    (
        "adapters/persistence/review_sql.py",
        "SELECT paused,global_suspension,category_suspensions_document",
    ),
    ("adapters/persistence/review_sql.py", "safety.paused=1"),
    # The posture write, not a condition.
    ("adapters/persistence/metadata_policy.py", "posture='automatic'"),
    # The claim parses the category document before it tests paused, so a
    # malformed one raises even while paused (R6, #2036).
    ("adapters/persistence/field_effect_activity.py", "json.loads(safety[2])"),
}


def test_no_site_spells_a_shared_condition_again() -> None:
    """P14c: the literal spellings occur only in the shared definition."""
    source = _REPOSITORY / "src" / "mechanical_librarian"
    found = {
        (str(path.relative_to(source)), spelling)
        for path in source.rglob("*.py")
        for spelling in _SPELLINGS
        if spelling in path.read_text(encoding="utf-8")
    }
    assert found == _ALLOWED
