"""S10's level derivation, its notes, its offers and their storage (#1355, B).

At a wider Book matching level the live ProposalSet and decision set are
derived at `identifier` exactly as before, and alone decide anything. Beside
them a level set is derived, in which the level's admissions count as
representing the work for S7's destinations only. A live manual decision the
level would decide gets ADR-0092's would-admit note, stored beside the set; a
live manual field the level offers other alternatives for is substituted in a
side table, and a field decided automatically keeps its live proposal.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from mechanical_librarian.adapters.persistence.level_reconciliation import (
    case_level_unreconciled,
    current_level_offer,
    read_level_decision_notes,
    read_level_proposal_set,
    stored_level_offers,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    provider_observation,
)
from mechanical_librarian.domain import level_proposal_set, shadow_review
from mechanical_librarian.domain.human_decision import offer_digest
from mechanical_librarian.domain.level_proposal_set import (
    LevelDecisionNote,
    LevelProposalSetError,
    derive_level,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    DateEvent,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    TargetDestination,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    evaluate_report,
    level_evidence,
)
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    current_offer,
    is_actionable,
)
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.support import (
    audiobook_reports,
    audiobook_synopsis,
    ebook_synopsis,
    evidence_led_titles,
)
from tests.unit import test_identity_refused_retention as retained

_IDENTIFIER = WorkIdentityLevel.IDENTIFIER
_WIDER = (
    WorkIdentityLevel.TITLE_AND_CREATOR_EXACT,
    WorkIdentityLevel.TITLE_AND_CREATOR,
)
SYNOPSIS = "A synopsis of the example book."


def _claim(identity: str, kind: ClaimKind, value: str, **kwargs: Any) -> ProviderClaim:
    return ProviderClaim.create(
        provider=retained.PROVIDER,
        candidate=identity,
        kind=kind,
        value=value,
        state=FieldState.VALID,
        scope=kwargs.pop("scope", ClaimScope.WORK),
        date_event=kwargs.pop(
            "date_event",
            DateEvent.MANIFESTATION_PUBLICATION
            if kind is ClaimKind.PUBLICATION_DATE
            else None,
        ),
        applicability=kwargs.pop("applicability", "not_applicable"),
        raw_order=kwargs.pop("raw_order"),
        **kwargs,
    )


def _refused(*claims: ProviderClaim, identity: str = "refused-1") -> ProviderCandidate:
    """A retained refused answer the level admits: the same title, the
    author's initials respelled, so only the fold matches."""
    base = retained._candidate(identity)
    return replace(base, claims=(*base.claims, *claims))


def _synopsis(identity: str = "refused-1") -> ProviderClaim:
    return _claim(identity, ClaimKind.DESCRIPTION, SYNOPSIS, raw_order=2)


def _report(
    *refused: ProviderCandidate, content: str = "a", actionable: bool = True
) -> MetadataReport:
    """An ebook whose title the opening matter disputes, so the case exists,
    and whose only provider answer was refused and kept for the level."""
    opening = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Example Book",
        canonical="Example Book",
        excerpt="Example Book",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", retained._fields()),
        file=FileFacts(0, content * 64, "b" * 64),
        local_claims=(
            *retained._plan().local_claims,
            *((opening,) if actionable else ()),
        ),
        local_reasons=("opening_matter_title",),
        providers=(
            provider_observation(
                retained.PROVIDER,
                (retained._refused_lane(*refused),),
                authority=retained.PROVIDER,
            ),
        ),
    )
    return MetadataReport.from_json(report.to_json())


def _policy(report: MetadataReport) -> Any:
    return resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)


def _sources(report: MetadataReport) -> tuple[ProposalSet, ReviewCase]:
    live = evaluate_report(report, _policy(report).resolved_id)
    return live, ReviewCase.create(live, report)


def _live_decisions(report: MetadataReport, live: ProposalSet, case: ReviewCase) -> str:
    packet = ShadowReviewPacket.create(case, live, report, include_policy_ignored=True)
    return decide_shadow_packet(packet).to_json()


def _result(proposal: ProposalSet, destination: TargetDestination) -> Any:
    return next(value for value in proposal.results if value.destination is destination)


# --- R2 dry proof as a test: the level packet, and the live packet's bytes ---


def test_a_level_packet_cites_a_refused_answer_and_the_live_packet_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The level chain runs end to end over a refused answer's citation, and
    the live packet is byte-identical to one indexing the report as before
    answers were retained (R2)."""
    date = _claim(
        "refused-1",
        ClaimKind.PUBLICATION_DATE,
        "2024-03-05",
        scope=ClaimScope.EDITION,
        raw_order=3,
    )
    report = _report(_refused(_synopsis(), date))
    live, case = _sources(report)
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    level_set = evaluate_report(
        report, live.resolved_policy_id, work_identity_level=level
    )
    level_case = ReviewCase.create(level_set, report)
    packet = ShadowReviewPacket.create(
        level_case,
        level_set,
        report,
        include_policy_ignored=True,
        work_identity_level=level,
    )
    decide_shadow_packet(packet)
    packet.validate_source(report, level_set, None, work_identity_level=level)
    refused_references = {
        claim.reference
        for claim in report.providers[0].identity_refused_candidates[0].claims
    }
    assert refused_references & {value["citation_id"] for value in packet.citations}

    live_packet = ShadowReviewPacket.create(
        case, live, report, include_policy_ignored=True
    )
    monkeypatch.setattr(
        shadow_review, "_packet_report_data", lambda value, _level: value.to_data()
    )
    before_retention = ShadowReviewPacket.create(
        case, live, report, include_policy_ignored=True
    )
    assert live_packet.to_json() == before_retention.to_json()
    assert not refused_references & {
        value["citation_id"] for value in live_packet.citations
    }


def test_indexing_refused_answers_on_the_live_path_changes_the_live_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control (R2): were the live packet to index a level-admitted
    refused answer, its evidence would join the live packet and the live
    decision set's digest would move."""
    date = _claim(
        "refused-1",
        ClaimKind.PUBLICATION_DATE,
        "2024-03-05",
        scope=ClaimScope.EDITION,
        raw_order=3,
    )
    report = _report(_refused(_synopsis(), date))
    live, case = _sources(report)
    expected = _live_decisions(report, live, case)
    original = shadow_review._packet_report_data
    monkeypatch.setattr(
        shadow_review,
        "_packet_report_data",
        lambda value, _level: original(
            value, level_evidence(value, WorkIdentityLevel.TITLE_AND_CREATOR)
        ),
    )
    forced = _live_decisions(report, live, case)
    assert forced != expected


# --- Test 18: live equality ---------------------------------------------------


def _corpus() -> dict[str, MetadataReport]:
    return {
        "refused_synopsis": _report(_refused(_synopsis())),
        "refused_title_only": _report(_refused()),
        "ebook_synopsis": ebook_synopsis.synopsis_report(),
        "isbn_title": evidence_led_titles.isbn_title_report(),
        "audiobook_admitted": audiobook_reports.admitted_audiobook_report(),
        "audiobook_corroborated": audiobook_reports.corroborated_audiobook_report(),
        "audiobook_bibliographic": (
            audiobook_reports.synthetic_audiobook_bibliographic_report()
        ),
        "audiobook_recording_lookup": audiobook_reports.recording_lookup_report(),
        "audiobook_synopsis": audiobook_synopsis.synopsis_report(),
    }


_CORPUS = _corpus()


@pytest.mark.parametrize("level", _WIDER, ids=[value.value for value in _WIDER])
@pytest.mark.parametrize("name", sorted(_CORPUS))
def test_live_decision_set_digest_equal_with_and_without_level(
    name: str, level: WorkIdentityLevel
) -> None:
    """Contract test 18: the live set, the live decision set and its digest
    are those of a build with no level, byte for byte, at every level; the
    derivation reads them and changes nothing, and its notes name the live
    decision set without being members of it."""
    report = _CORPUS[name]
    policy = _policy(report)
    live = evaluate_report(report, policy.resolved_id)
    assert (
        live.to_json()
        == evaluate_report(
            report, policy.resolved_id, work_identity_level=_IDENTIFIER
        ).to_json()
    )
    case = ReviewCase.create(live, report)
    before = _live_decisions(report, live, case)
    derivation = derive_level(report, live, case, policy, 1, level)
    after = _live_decisions(report, live, case)
    assert before == after
    assert live.to_json() == evaluate_report(report, policy.resolved_id).to_json()
    decision_set_id = json.loads(before)["decision_set_id"]
    assert all(note.decision_set_id == decision_set_id for note in derivation.notes)
    assert "work_identity:" not in before
    assert "work_identity:" not in live.to_json()
    assert "work_identity:" not in case.to_json()


# --- Test 19: a refused description becomes a manual level alternative --------


@pytest.mark.parametrize("level", _WIDER, ids=[value.value for value in _WIDER])
def test_refused_description_offered_at_level_with_would_admit_note(
    level: WorkIdentityLevel,
) -> None:
    """Contract test 19: a description refused today as not shown to describe
    the work is offered in review at a wider level, with its lineage; the
    live decision stays manual, and a would-admit note names the level's
    rule and the alternative."""
    report = _report(_refused(_synopsis()))
    live, case = _sources(report)
    assert not _result(live, TargetDestination.DESCRIPTION).alternatives
    derivation = derive_level(report, live, case, _policy(report), 1, level)
    assert derivation.offer is not None
    offer = {
        value["destination"]: value for value in current_offer(case, derivation.offer)
    }
    description = offer["description"]
    (alternative,) = description["result"]["alternatives"]
    assert alternative["value"]["value"] == SYNOPSIS
    assert description["lineage"] == {
        alternative["alternative_id"]: {
            "creator_comparison": "initials_fold",
            "rule": f"work_identity:{level.value}",
        }
    }
    live_description = next(
        value for value in case.proposals if value["destination"] == "description"
    )
    assert description["proposal_id"] == live_description["proposal_id"]
    assert description["result"]["outcome"] == live_description["result"]["outcome"]
    assert description["result"]["reasons"] == live_description["result"]["reasons"]
    (note,) = (value for value in derivation.notes if value.field == "description")
    assert note.rule == f"work_identity:{level.value}"
    assert note.alternative_reference == alternative["alternative_id"]
    assert offer_digest(case.proposals, "description") != offer_digest(
        tuple(offer.values()), "description"
    )


# --- Test 20: substitution touches manual fields only ------------------------


@contextmanager
def _live_dispositions(**dispositions: str) -> Iterator[None]:
    """The live outcome set decides the named destinations as given."""
    original = level_proposal_set._dispositions
    calls: list[int] = []

    def fake(outcome: Any) -> dict[str, str]:
        calls.append(1)
        return {**original(outcome), **dispositions}

    level_proposal_set._dispositions = fake
    try:
        yield
    finally:
        level_proposal_set._dispositions = original
    assert calls


def _offered(derivation: Any) -> set[str]:
    if derivation.offer is None:
        return set()
    return {value["destination"] for value in json.loads(derivation.offer)["fields"]}


def test_automatic_field_keeps_live_proposal_at_every_level() -> None:
    """Contract test 20: a field decided automatically keeps its live
    proposal, though the level offers another alternative for it."""
    report = _report(_refused(_synopsis()))
    live, case = _sources(report)
    for level in _WIDER:
        with _live_dispositions(description="candidate"):
            derivation = derive_level(report, live, case, _policy(report), 1, level)
        assert "description" not in _offered(derivation)
        assert not [value for value in derivation.notes if value.field == "description"]
        offer = current_offer(case, derivation.offer)
        assert next(
            value for value in offer if value["destination"] == "description"
        ) == next(
            value for value in case.proposals if value["destination"] == "description"
        )


def test_combination_group_with_one_automatic_member_keeps_live_proposals() -> None:
    """Contract test 20: the title, subtitle and series group is substituted
    whole or not at all, and not at all when any member is automatic."""
    report = _report(_refused(_synopsis()))
    live, case = _sources(report)
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    manual = derive_level(report, live, case, _policy(report), 1, level)
    assert {"title", "subtitle", "series"} <= _offered(manual)
    with _live_dispositions(series="keep_current"):
        derivation = derive_level(report, live, case, _policy(report), 1, level)
    assert not {"title", "subtitle", "series"} & _offered(derivation)
    assert "description" in _offered(derivation)


def test_manual_field_shows_level_alternatives() -> None:
    """Contract test 20: a manual field shows the level's alternatives, each
    with its lineage, under its live proposal id."""
    report = _report(_refused(_synopsis()))
    live, case = _sources(report)
    derivation = derive_level(
        report, live, case, _policy(report), 1, WorkIdentityLevel.TITLE_AND_CREATOR
    )
    offer = current_offer(case, derivation.offer)
    authors: Any = next(value for value in offer if value["destination"] == "authors")
    assert authors["work_identity_level"] == "title_and_creator"
    assert set(authors["lineage"]) <= {
        value["alternative_id"] for value in authors["result"]["alternatives"]
    }
    assert authors["lineage"]


# --- S7: manifestation fields never read the level ----------------------------

_MANIFESTATION = {
    TargetDestination.ISBN: (ClaimKind.ISBN, "9780306406157", ClaimScope.EDITION),
    TargetDestination.PUBLISHER: (
        ClaimKind.PUBLISHER,
        "Example Press",
        ClaimScope.EDITION,
    ),
    TargetDestination.PUBLISHED_DATE: (
        ClaimKind.PUBLICATION_DATE,
        "2024-03-05",
        ClaimScope.EDITION,
    ),
    TargetDestination.PUBLISHED_YEAR: (
        ClaimKind.PUBLICATION_DATE,
        "2024-03-05",
        ClaimScope.EDITION,
    ),
    TargetDestination.LANGUAGE: (ClaimKind.LANGUAGE, "French", ClaimScope.EDITION),
    TargetDestination.NARRATORS: (ClaimKind.CONTRIBUTOR, "N. Example", ClaimScope.WORK),
    TargetDestination.ASIN: (ClaimKind.ASIN, "B000000011", ClaimScope.EDITION),
    TargetDestination.EXPLICIT: (ClaimKind.ADULT_PRODUCT, "true", ClaimScope.EDITION),
}


@pytest.mark.parametrize(
    "destination", list(_MANIFESTATION), ids=[value.value for value in _MANIFESTATION]
)
def test_manifestation_fields_never_read_level(destination: TargetDestination) -> None:
    """S7: a level-admitted answer's manifestation claim never reaches the
    level set; its description, a work field, does (the control)."""
    kind, value, scope = _MANIFESTATION[destination]
    extra = _claim(
        "refused-1",
        kind,
        value,
        scope=scope,
        raw_order=3,
        **({"role": "narrator"} if kind is ClaimKind.CONTRIBUTOR else {}),
    )
    report = _report(_refused(_synopsis(), extra))
    (refused,) = report.providers[0].identity_refused_candidates
    # A date the answer cannot bind to this edition is kept as its text.
    assert any(claim.kind is kind for claim in refused.claims) or any(
        value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        for value in refused.evidence
    )
    live = evaluate_report(report)
    for level in _WIDER:
        level_set = evaluate_report(report, work_identity_level=level)
        assert _result(level_set, destination) == _result(live, destination)
        assert _result(level_set, TargetDestination.DESCRIPTION).alternatives


# --- Test 21, storage half; test 17f, storage half -----------------------------


@contextmanager
def _store(level: WorkIdentityLevel) -> Iterator[Any]:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        try:
            _bind(repository, level)
            yield repository
        finally:
            repository.close()


def _bind(repository: Any, level: WorkIdentityLevel) -> None:
    repository.bind_current_cover_authority(
        "cover_policy_" + "1" * 64, "cover_replay_" + "2" * 64, level.value
    )
    # The level every case follows is the one the gates read (amendment 5).
    repository.align_book_matching(level.value, 0)


def _admit(repository: Any, report: MetadataReport) -> str:
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    return str(admitted["review_case_id"])


def _rows(repository: Any) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: repository._connection.execute(f"SELECT * FROM {table}").fetchall()
        for table in (
            "level_proposal_sets",
            "level_decision_notes",
            "review_case_level_offers",
            "review_case_level_reconciliation",
        )
    }


def test_level_tables_round_trip() -> None:
    """Contract test 21, storage: the level set, its notes and the case's
    offer read back exactly as derivation gives them."""
    report = _report(_refused(_synopsis()))
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    with _store(level) as repository:
        repository.publish(report, lambda: True)
        case_id = _admit(repository, report)
        live, case = _sources(report)
        assert case.review_case_id == case_id
        derivation = derive_level(report, live, case, _policy(report), 1, level)
        connection = repository._connection
        assert (
            read_level_proposal_set(
                connection, derivation.level_proposal_set_id
            ).to_json()
            == evaluate_report(
                report, live.resolved_policy_id, work_identity_level=level
            ).to_json()
        )
        assert (
            read_level_decision_notes(connection, derivation.level_proposal_set_id)
            == derivation.notes
        )
        assert stored_level_offers(connection, case) == (
            (level.value, derivation.offer),
        )


@pytest.mark.parametrize(
    "column",
    ("decision_policy_version", "decision_policy_sha256", "proposal_policy_version"),
)
def test_level_decision_note_provenance_checked(column: str) -> None:
    """A note from an older decision engine or proposal policy is refused,
    by the table and again at readback."""
    report = _report(_refused(_synopsis()))
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    with _store(level) as repository:
        repository.publish(report, lambda: True)
        _admit(repository, report)
        connection = repository._connection
        (stored,) = connection.execute("SELECT * FROM level_decision_notes").fetchall()
        names = [
            value[0]
            for value in connection.execute(
                "SELECT * FROM level_decision_notes"
            ).description
        ]
        older = dict(zip(names, stored, strict=True))
        older[column] = "older-" + str(older[column])
        older["field"] = "title"
        with pytest.raises(LevelProposalSetError):
            LevelDecisionNote(**older)
        placeholders = ",".join("?" * len(names))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO level_decision_notes({','.join(names)}) "
                f"VALUES({placeholders})",
                tuple(older.values()),
            )
        connection.execute("PRAGMA ignore_check_constraints=ON")
        try:
            connection.execute(
                f"INSERT INTO level_decision_notes({','.join(names)}) "
                f"VALUES({placeholders})",
                tuple(older.values()),
            )
        finally:
            connection.execute("PRAGMA ignore_check_constraints=OFF")
        with pytest.raises(MetadataReportError):
            read_level_decision_notes(connection, older["level_proposal_set_id"])
        connection.rollback()


def _assert_born_reconciled(
    repository: Any, case_id: str, level: WorkIdentityLevel
) -> None:
    rows = _rows(repository)
    assert rows["review_case_level_reconciliation"] == [(case_id, level.value)]
    offers = [row for row in rows["review_case_level_offers"] if row[0] == case_id]
    assert [row[1] for row in offers] == [level.value]
    assert rows["level_decision_notes"]
    assert rows["level_proposal_sets"]
    # Born reconciled: never checking, so neither choices nor owner writes
    # wait (17f).
    assert not case_level_unreconciled(repository._connection, case_id)


@pytest.mark.parametrize(
    "path", ("new_scan", "came_back_for_review", "rescan_successor")
)
def test_case_created_at_wider_level_is_born_reconciled(path: str) -> None:
    """Contract test 17f, storage: every path that creates a case writes its
    level set, notes, offer row and marker with the case at a wider level,
    and nothing at `identifier`."""
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    report = _report(_refused(_synopsis()))
    with _store(_IDENTIFIER) as repository:
        repository.publish(report, lambda: True)
        builtin = MetadataPolicyProfile.builtin()
        if path == "new_scan":
            _bind(repository, level)
            case_id = _admit(repository, report)
        elif path == "came_back_for_review":
            # Admitted at `identifier`, the work's case leaves through another
            # policy and comes back as itself while the level is wider: the
            # case row already exists and starts a new episode.
            case_id = _admit(repository, report)
            assert _rows(repository) == {key: [] for key in _rows(repository)}
            changed = MetadataPolicyProfile.create(
                "Changed",
                {**dict(builtin.options), "date_precision": "full_date"},
                dict(builtin.destinations),
            )
            repository.create_metadata_policy_draft(changed.canonical_json)
            _activate(repository, changed, builtin, 1, report)
            _admit_under(repository, changed, report)
            _activate(repository, builtin, changed, 2, report)
            _bind(repository, level)
            assert _admit_under(repository, builtin, report) == case_id
            assert _episodes(repository, case_id) == (1,)
        else:
            _admit(repository, report)
            _bind(repository, level)
            successor = _report(_refused(_synopsis()), content="c")
            repository.publish(successor, lambda: True)
            case_id = _admit(repository, successor)
            assert _rows(repository)["review_case_level_reconciliation"] == [
                (case_id, level.value)
            ]
            assert [
                row[0] for row in _rows(repository)["review_case_level_offers"]
            ] == [case_id]
            return
        _assert_born_reconciled(repository, case_id, level)


def test_a_case_admitted_again_at_identifier_is_born_reconciled() -> None:
    """The P2 correction (17f, both directions): a case reconciled at a wider
    level and admitted again at `identifier` has its marker written as
    `identifier`, so it is never checking and offers its live proposals; at a
    wider level again it is gated until its check."""
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    report = _report(_refused(_synopsis()))
    with _store(level) as repository:
        repository.publish(report, lambda: True)
        builtin = MetadataPolicyProfile.builtin()
        case_id = _admit_under(repository, builtin, report)
        assert _rows(repository)["review_case_level_reconciliation"] == [
            (case_id, level.value)
        ]
        _activate(repository, builtin, builtin, 1, report)
        _bind(repository, _IDENTIFIER)
        assert case_level_unreconciled(repository._connection, case_id)
        assert _admit_under(repository, builtin, report) == case_id
        assert _rows(repository)["review_case_level_reconciliation"] == [
            (case_id, _IDENTIFIER.value)
        ]
        assert not case_level_unreconciled(repository._connection, case_id)
        assert current_level_offer(repository._connection, case_id) is None
        _bind(repository, level)
        assert case_level_unreconciled(repository._connection, case_id)


def test_a_reactivated_case_continuing_its_episode_is_reconciled() -> None:
    """17f's other `existing` branch: a case retired by an unchanged policy
    and admitted again continues its episode (#1568), and still has its level
    storage written with it."""
    level = WorkIdentityLevel.TITLE_AND_CREATOR
    report = _report(_refused(_synopsis()))
    with _store(_IDENTIFIER) as repository:
        repository.publish(report, lambda: True)
        builtin = MetadataPolicyProfile.builtin()
        case_id = _admit_under(repository, builtin, report)
        _activate(repository, builtin, builtin, 1, report)
        _bind(repository, level)
        assert _admit_under(repository, builtin, report) == case_id
        assert _episodes(repository, case_id) == (0,)
        _assert_born_reconciled(repository, case_id, level)


def test_identifier_admission_writes_no_level_storage() -> None:
    """At `identifier` a case is born with no level row, which reads as
    `identifier` (r3c)."""
    report = _report(_refused(_synopsis()))
    with _store(_IDENTIFIER) as repository:
        repository.publish(report, lambda: True)
        _admit(repository, report)
        assert _rows(repository) == {key: [] for key in _rows(repository)}


def test_a_level_store_keeps_live_rows_byte_identical() -> None:
    """Contract test 18 through admission: the stored live set, case and
    live decision set at a wider level are the `identifier` store's, and the
    notes are only in `level_decision_notes`."""
    report = _report(_refused(_synopsis()))
    stored: dict[str, tuple[object, ...]] = {}
    for level in (_IDENTIFIER, *_WIDER):
        with _store(level) as repository:
            repository.publish(report, lambda: True)
            case_id = _admit(repository, report)
            connection = repository._connection
            stored[level.value] = (
                tuple(connection.execute("SELECT * FROM proposal_sets").fetchall()),
                tuple(connection.execute("SELECT * FROM review_cases").fetchall()),
                hashlib.sha256(
                    _live_decisions(report, *_sources(report)).encode()
                ).hexdigest(),
                case_id,
            )
            notes = connection.execute(
                "SELECT count(*) FROM level_decision_notes"
            ).fetchone()
            assert notes == ((0,) if level is _IDENTIFIER else (1,))
    assert len(set(stored.values())) == 1


def _activate(
    repository: Any,
    profile: Any,
    expected: Any,
    generation: int,
    report: MetadataReport,
) -> None:
    resolved = resolve_policy(profile, report.target.library)
    repository.activate_metadata_policy(
        profile.policy_id,
        expected.policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        generation + 1,
        expected_active_generation=generation,
        reconcile_current=True,
    )


def _admit_under(repository: Any, profile: Any, report: MetadataReport) -> str:
    proposal = evaluate_report(
        report, resolve_policy(profile, report.target.library).resolved_id
    )
    repository.publish_proposal(proposal)
    return str(json.loads(repository.admit_review_case(proposal))["review_case_id"])


def _episodes(repository: Any, case_id: str) -> tuple[int]:
    return tuple(
        repository._connection.execute(
            "SELECT count(*) FROM review_case_episodes WHERE review_case_id=?",
            (case_id,),
        ).fetchone()
    )


def test_the_refused_report_is_actionable_only_through_its_opening_matter() -> None:
    """The fixture's premise: the live case exists because the opening
    matter disputes the title; the refused answer adds nothing live."""
    assert is_actionable(evaluate_report(_report(_refused(_synopsis()))))
    assert not is_actionable(
        evaluate_report(_report(_refused(_synopsis()), actionable=False))
    )
