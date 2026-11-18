"""Taste switches decided, written and refused through the real seams (#1372 PR 5).

Works are prepared by the real producers under Policy Automatic; verdicts are
stored by the real judge (a fake transport) or the judge repository's real
`succeed`; the taste record is patched as #1418 will set it; and a switch is
planned, written, read back and put back by the real guarded writer.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.operational_evidence import SupportBundle
from mechanical_librarian.adapters.persistence import retained_covers
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.cover_consideration_documents import (
    expand_cover_consideration,
)
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _cover_automatic_decision_value,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.cover_taste_build import CoverTasteBuildService
from mechanical_librarian.application.cover_taste_judge import CoverTasteJudgeService
from mechanical_librarian.application.cover_taste_judge_contract import (
    AdmittedTasteJudgeRequest,
    TasteJudgeReply,
)
from mechanical_librarian.application.cover_taste_switching import (
    CoverTasteSwitching,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.operations import support_preparation_incidents
from mechanical_librarian.application.review_covers import cover_page
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    RecordHumanDecision,
)
from mechanical_librarian.domain import cover_qualification_policy
from mechanical_librarian.domain.automatic_decision import AutomaticDecisionError
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.cover_taste import COVER_TASTE_VERDICT_SCHEMA
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.integration.test_cover_rules_activation import (
    _planned,
    _prepared,
    _reprepare,
    _selections,
    _service,
)
from tests.support.audiobook_covers import (
    SYNTHETIC_MEASURED_RECORD,
    SYNTHETIC_TASTE_RECORD,
)
from tests.unit.test_cover_taste_judge import WORKS, _Library, _report, _version
from tests.unit.test_cover_taste_suggestions import _detail_suggestion

NOW = 1_000_000
TASTE = "cover_order:fixed_source_order:taste_replacement"


class _Client:
    """Chooses, for each planned item, an art other than the stage's choice."""

    def __init__(self, library: _Library, plan: set[str]) -> None:
        self.library = library
        self.plan = plan

    def run_taste_judge(
        self, _profile: bytes, request: AdmittedTasteJudgeRequest
    ) -> TasteJudgeReply:
        real = {reference for _opaque, reference in request.references}
        item = next(
            item
            for item, consideration in self.library.considerations.items()
            if real
            <= {candidate["reference"] for candidate in consideration["candidates"]}
        )
        # Another art than the stage's own choice, when there is one.
        recommended = json.loads(
            self.library.considerations[item]["policy_report_document"]
        )["recommended_candidate_reference"]
        chosen = sorted(
            (
                opaque
                for opaque, real in request.references
                if opaque != request.current
            ),
            key=lambda opaque: dict(request.references)[opaque] == recommended,
        )[0]
        return TasteJudgeReply(
            canonical_json(
                {
                    "candidate": chosen if item in self.plan else None,
                    "confidence": 80,
                    "decision": "choose" if item in self.plan else "keep_current",
                    "rules": [1, 2],
                }
            ),
            True,
        )

    def close(self) -> None:
        pass


class _Access:
    def __init__(self, client: _Client) -> None:
        self.client = client

    def current_taste_judge_model(self) -> str | None:
        return "example/vision"

    def open_taste_judge(self) -> tuple[str, str, bytes, _Client]:
        return "openrouter", "example/vision", b"profile", self.client


def _automatic_policy(repository: Any, library: str) -> tuple[str, dict[str, str]]:
    """Route cover order to Automatic with fixed source order, as Policy does."""
    options = dict(DEFAULT_OPTIONS)
    options["cover_order"] = "fixed_source_order"
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["cover_order"] = "automatic"
    profile = MetadataPolicyProfile.create("Taste switching", options, destinations)
    resolved = resolve_policy(profile, library)
    repository.enter_shadow_preview(1, ())
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((library, resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    repository.activate_qualified_automatic(3)
    return resolved.resolved_id, human_review_destinations(resolved)


@contextmanager
def _switching(
    root: Path,
    works: tuple[str, ...] = ("keep", "wrong_shape"),
    *,
    protection: str = "unprotected",
    judged: tuple[str, ...] = ("keep", "wrong_shape"),
) -> Iterator[_Library]:
    """Prepare `works` under Policy Automatic and `protection`, then judge
    each once, choosing another art for each `judged` item."""
    root.mkdir(exist_ok=True)
    root.chmod(0o700)
    repository = open_repository(root / "application.sqlite3", cross_thread=True)
    artifacts = CoverArtifactStore(root / "artifacts")
    reports = {item: _report(root, item, WORKS[item]) for item in works}
    previews = {
        source.artifact.artifact_id: artifacts.preview(source.artifact)
        for report in reports.values()
        for source in report.cover_sources
        if source.artifact is not None
    }
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact_id, _variant: previews[artifact_id],
        read_preview_digest=artifacts.read_preview_digest,
        require_artifact=artifacts.require_artifact,
        clock=lambda: 10,
        policy=CoverPolicy(current_protection=protection),
        bind_policy=repository.bind_current_cover_authority,
        retained_original=repository.retained_cover_original,
        require_retained=lambda _digest: None,
        provider_fetching=lambda _report: False,
    )
    repository.bind_cover_consideration_currentness(lambda *_facts: False)
    repository.bind_owner_cover_validation(service.require_owner_cover)
    library = _Library(repository, artifacts, service, {})
    resolved_id, destinations = _automatic_policy(
        repository, next(iter(reports.values())).target.library
    )
    for item, report in reports.items():
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report, resolved_id, destinations)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        _ensure(library, item, case_id, report)
    service.refresh_authority()
    _version(repository)
    repository._connection.commit()
    _judge(library, set(judged))
    try:
        yield library
    finally:
        repository.close()


def _ensure(library: _Library, item: str, case_id: str, report: MetadataReport) -> None:
    repository = library.repository
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(row for row in case.proposals if row["destination"] == "cover")
    ensured = library.service.ensure(case_id, report.to_json(), proposal)
    library.considerations[item] = json.loads(
        expand_cover_consideration(
            *repository._connection.execute(
                "SELECT consideration.document,report.document,report.document_digest,"
                "consideration.document_digest,consideration.report_id "
                "FROM cover_considerations consideration LEFT JOIN metadata_reports "
                "report ON report.report_id=consideration.report_id "
                "WHERE consideration.consideration_id=?",
                (ensured["consideration_id"],),
            ).fetchone()
        )
    )
    library.cases[item] = case_id


def _judge(library: _Library, plan: set[str], now: int = NOW) -> None:
    store = library.repository.cover_taste_judge_store()
    service = CoverTasteJudgeService(
        lambda: store,
        _Access(_Client(library, plan)),  # type: ignore[arg-type]  # reason: the methods the service reads
        library.artifacts.read_preview_digest,
        daily_limit=50,
        clock=lambda: now,
    )
    for _ in range(len(library.cases) + 1):
        if not service.wake(now):
            return
    raise AssertionError("the pass did not end")


def _wake(library: _Library, now: int = NOW + 1) -> bool:
    """One switching step of lane 1, as the background lane runs it."""
    store = library.repository.cover_taste_judge_store()
    return CoverTasteSwitching(lambda: store).wake(now)


def _wakes(library: _Library, now: int = NOW + 1) -> int:
    switching = CoverTasteSwitching(
        lambda: library.repository.cover_taste_judge_store()
    )
    count = 0
    while switching.wake(now):
        count += 1
    return count


def _verdict(library: _Library, item: str) -> tuple[str, str]:
    row = library.repository._connection.execute(
        "SELECT verdict_id,json_extract(document,'$.candidate_reference') "
        "FROM cover_taste_verdicts WHERE consideration_id=? "
        "ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (library.considerations[item]["consideration_id"],),
    ).fetchone()
    return cast(str, row[0]), cast(str, row[1])


def _stage(library: _Library, item: str) -> dict[str, Any]:
    policy = json.loads(library.considerations[item]["policy_report_document"])
    return cast("dict[str, Any]", policy["stage"])


def _taste_selections(library: _Library) -> list[CoverAutomaticDecision]:
    return [
        CoverAutomaticDecision.from_json(row["decision_document"])
        for row in _selections(library.repository)
        if row["kind"] == "automatic"
    ]


@pytest.fixture
def record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cover_qualification_policy,
        "TASTE_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_TASTE_RECORD,
    )


def _automatic(library: _Library) -> None:
    library.repository.bind_cover_taste_switching("automatic")


def test_a_fault_free_cover_switches_only_without_protection(
    tmp_path: Path, record: None
) -> None:
    """Branch a: a fault-free keep switches under "When another is
    preferred" (`unprotected`) and never under `protected` (K4 gate 5)."""
    with _switching(tmp_path / "u", ("keep",)) as library:
        assert (
            _stage(library, "keep")["outcome"],
            _stage(library, "keep")["reason"],
        ) == (
            "keep",
            None,
        )
        _automatic(library)
        assert _wake(library)
        (decision,) = _taste_selections(library)
        verdict_id, chosen = _verdict(library, "keep")
        assert (decision.qualification_identity, decision.reason) == (TASTE, None)
        assert (decision.verdict_id, decision.candidate_reference) == (
            verdict_id,
            chosen,
        )
        assert decision.taste_version == 1
        assert decision.qualification_manifest_sha256 == SYNTHETIC_TASTE_RECORD
        # A second wake is idempotent: nothing more is minted.
        assert not _wake(library)
        assert len(_taste_selections(library)) == 1
    with _switching(tmp_path / "p", ("keep",), protection="protected") as library:
        _automatic(library)
        assert not _wake(library)
        assert _selections(library.repository) == []


def test_a_fault_is_cured_with_the_judges_art(tmp_path: Path, record: None) -> None:
    """Branch b: a stage `replace` mints the art the judge chose, which need
    not be the stage's own recommendation, with the stage's fault."""
    with _switching(tmp_path, ("wrong_shape",), protection="protected") as library:
        stage = _stage(library, "wrong_shape")
        assert (stage["outcome"], stage["reason"]) == ("replace", "wrong_shape")
        _automatic(library)
        assert _wake(library)
        (decision,) = _taste_selections(library)
        _verdict_id, chosen = _verdict(library, "wrong_shape")
        policy = json.loads(
            library.considerations["wrong_shape"]["policy_report_document"]
        )
        assert decision.candidate_reference == chosen
        assert chosen != policy["recommended_candidate_reference"]
        assert decision.reason == "wrong_shape"


def test_a_suggest_stage_under_never_switches_nothing(
    tmp_path: Path, record: None
) -> None:
    """Under Current covers Never the stage only suggests: no taste switch."""
    with _switching(tmp_path, ("wrong_shape",), protection="proposals_only") as library:
        assert _stage(library, "wrong_shape")["outcome"] == "suggest"
        _automatic(library)
        assert not _wake(library)
        assert _selections(library.repository) == []


@pytest.mark.parametrize("missing", ("record", "switching", "posture"))
def test_nothing_switches_without_record_switching_or_automatic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Gates 1-3: Suggest only, no record or Policy not Automatic mint nothing."""
    if missing != "record":
        monkeypatch.setattr(
            cover_qualification_policy,
            "TASTE_REPLACEMENT_QUALIFICATION_SHA256",
            SYNTHETIC_TASTE_RECORD,
        )
    with _switching(tmp_path) as library:
        if missing != "switching":
            _automatic(library)
        if missing == "posture":
            library.repository._connection.execute(
                "UPDATE metadata_policy_state SET posture='review'"
            )
            library.repository._connection.commit()
        assert _wakes(library) == 0
        assert _selections(library.repository) == []


def test_a_full_retained_store_or_a_hold_switches_nothing(
    tmp_path: Path, record: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gates 7 and 8: no room to keep the original, or a hold, mints nothing."""
    monkeypatch.setattr(retained_covers, "MAXIMUM_RETAINED_ENTRIES", 0)
    with _switching(tmp_path / "full", ("keep",)) as library:
        _automatic(library)
        assert _wakes(library) == 0
    monkeypatch.undo()
    monkeypatch.setattr(
        cover_qualification_policy,
        "TASTE_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_TASTE_RECORD,
    )
    with _switching(tmp_path / "held", ("keep",)) as library:
        _automatic(library)
        library.repository._set_field_effect_manual_hold(
            library.cases["keep"], "cover", NOW, "human_decision_" + "0" * 64
        )
        library.repository._connection.commit()
        assert _wakes(library) == 0
        assert _selections(library.repository) == []


def _stored_choose(library: _Library, item: str, now: int = NOW + 5) -> str:
    """A `choose` verdict stored through the judge repository's real
    `succeed`, bypassing J1 (which never judges an owner-answered field)."""
    store = library.repository.cover_taste_judge_store()
    consideration = library.considerations[item]
    version = library.repository._connection.execute(
        "SELECT max(version) FROM cover_taste_profile_versions"
    ).fetchone()[0]
    judgement = store.claim(
        consideration["consideration_id"], version, now, "example/vision", 50
    )
    if judgement is None:
        # Already judged under this version: a retry attempt, running.
        judgement = "taste_judgement_" + uuid.uuid4().hex
        library.repository._connection.execute(
            "INSERT INTO cover_taste_judgements SELECT ?,consideration_id,version,"
            "'retry',judgement_id,'running',NULL,'example/vision',0,?,? "
            "FROM cover_taste_judgements WHERE consideration_id=? AND version=? "
            "ORDER BY rowid DESC LIMIT 1",
            (judgement, now, now, consideration["consideration_id"], version),
        )
        library.repository._connection.commit()
    chosen = [
        candidate["reference"]
        for candidate in consideration["candidates"]
        if not candidate["incumbent"] and candidate["selectable"]
    ][-1]
    assert store.succeed(
        judgement,
        now,
        canonical_json(
            {
                "candidate_reference": chosen,
                "candidate_set_sha256": "1" * 64,
                "confidence": 90,
                "consideration_id": consideration["consideration_id"],
                "current_marker": True,
                "decision": "choose",
                "engine": "openrouter",
                "instruction_sha256": "2" * 64,
                "model": "example/vision",
                "packet_sha256": "3" * 64,
                "profile_digest": "4" * 64,
                "rules": [1],
                "schema_sha256": "5" * 64,
                "schema_version": COVER_TASTE_VERDICT_SCHEMA,
                "version": version,
            }
        ),
    )
    return chosen


def _owner_choice(
    library: _Library, item: str, *, keep: bool = False, at: int = NOW + 2
) -> None:
    """The owner answers the work's cover in To decide (or in Switched)."""
    repository = library.repository
    case_id = library.cases[item]
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(row for row in case.proposals if row["destination"] == "cover")
    consideration = library.considerations[item]
    chosen = next(
        candidate
        for candidate in consideration["candidates"]
        if not candidate["incumbent"] and candidate["selectable"]
    )
    current = _selections(repository)
    RecordHumanDecision(repository, lambda: at).run(
        case_id,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="cover",
        actor="owner",
        outcome="keep_current" if keep else "accept",
        selected_value=None
        if keep
        else {"kind": "cover", "value": chosen["artifact_id"]},
        idempotency_key=f"owner-choice-{item}-{at}",
        consideration_id=str(consideration["consideration_id"]),
        cover_confirmation={
            "expected_selection_id": current[0]["selection_id"] if current else None,
            "confirm_unverified_match": not keep,
        },
    )


@pytest.mark.parametrize(
    "when", ("to_decide", "earlier_comparison", "earlier_case", "kept_switch")
)
def test_taste_automatic_never_replaces_an_owner_cover_choice(
    tmp_path: Path, record: None, when: str
) -> None:
    """Amendment 3 (K5): an owner's cover choice holds. A `choose` verdict
    for that work, stored past J1, mints nothing: the pointer stays the
    owner's and nothing is planned."""
    with _switching(tmp_path, ("keep",), judged=()) as library:
        _automatic(library)
        repository = library.repository
        if when == "kept_switch":
            _stored_choose(library, "keep")
            assert _wake(library)
            _owner_choice(library, "keep", keep=True)
        else:
            _owner_choice(library, "keep")
        if when == "earlier_comparison":
            # The work is compared again under another cover policy.
            library.service = replace_policy(library, "keep")
        if when == "earlier_case":
            # The work changes in the library and is reviewed as a new case.
            before = library.cases["keep"]
            _successor_case(library, "keep")
            assert library.cases["keep"] != before
        _stored_choose(library, "keep", NOW + 20)
        # The premise: but for the owner, every gate before the mint passes.
        assert (
            _stage(library, "keep")["outcome"],
            _stage(library, "keep")["reason"],
        ) == (
            "keep",
            None,
        )
        verdict_id, _chosen = _verdict(library, "keep")
        store = repository.cover_taste_judge_store()
        # The mint is the seam: asked directly, it refuses.
        assert not store.admit_switch(
            library.considerations["keep"]["consideration_id"], verdict_id, NOW + 21
        )
        assert _wakes(library, NOW + 21) == 0
        (selection,) = _selections(repository)
        assert selection["kind"] == "owner"
        # Taste alone never offers Resume (K9).
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_automatic_held"
        ).fetchone() == (0,)
        # Only the owner's own answer waits to be written.
        assert all(
            json.loads(document)["actor"] == "owner"
            for document in repository.pending_field_effect_decision_documents(9)
        )


def _successor_case(library: _Library, item: str) -> None:
    """Publish the work again with another publisher, so it is reviewed as
    a new case, and compare its cover under the same policy."""
    case_id, successor = _republished(
        library.repository, _current_report(library, item)
    )
    _ensure(library, item, case_id, successor)
    library.service.refresh_authority()


def _republished(repository: Any, report: MetadataReport) -> tuple[str, MetadataReport]:
    """Publish `report`'s work again with another publisher and admit it as
    a new review case; return that case and the report it reviews."""
    target = report.target
    fields = tuple(
        TargetField(field.destination, TargetFieldState.VALUE, "Another Publisher")
        if field.destination.value == "publisher"
        else field
        for field in target.fields
    )
    successor = MetadataReport.create(
        target=replace(target, fields=fields),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        cover_sources=report.cover_sources,
    )
    row = repository._connection.execute(
        "SELECT resolution.document FROM metadata_policy_state state "
        "JOIN metadata_policy_resolutions resolution "
        "ON resolution.generation=state.generation AND resolution.library_name=? "
        "WHERE state.singleton=1",
        (target.library,),
    ).fetchone()
    resolved = ResolvedMetadataPolicy.from_data(json.loads(row[0]))
    repository.publish(successor, lambda: True, expected_current=report.report_id)
    proposal_set = evaluate_report(
        successor, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish_proposal(proposal_set)
    case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
    return case_id, successor


def replace_policy(library: _Library, item: str) -> CoverConsiderationService:
    """Prepare `item` again under a changed cover policy: a new comparison."""
    repository = library.repository
    service = CoverConsiderationService(
        repository,
        read_preview=library.service._read_preview,
        read_preview_digest=library.artifacts.read_preview_digest,
        require_artifact=library.artifacts.require_artifact,
        clock=lambda: 11,
        policy=CoverPolicy(current_protection="unprotected", prefer_edition=True),
        bind_policy=repository.bind_current_cover_authority,
        retained_original=repository.retained_cover_original,
        require_retained=lambda _digest: None,
        provider_fetching=lambda _report: False,
    )
    repository.bind_owner_cover_validation(service.require_owner_cover)
    library.service = service
    report = _current_report(library, item)
    before = library.considerations[item]["consideration_id"]
    _ensure(library, item, library.cases[item], report)
    assert library.considerations[item]["consideration_id"] != before
    service.refresh_authority()
    return service


def _current_report(library: _Library, item: str) -> MetadataReport:
    row = library.repository._connection.execute(
        "SELECT document FROM metadata_reports WHERE report_id=?",
        (library.considerations[item]["report_id"],),
    ).fetchone()
    return MetadataReport.from_json(row[0])


@pytest.mark.parametrize("newer_version", [False, True])
def test_admission_refuses_a_verdict_under_an_earlier_version(
    tmp_path: Path, record: None, newer_version: bool
) -> None:
    """Review N2: admission's own version gate. The verdict is still the
    comparison's newest, but once a later profile version is in use it was
    chosen under one that no longer stands, so admitting it directly mints
    nothing. Without the later version the same call switches."""
    with _switching(tmp_path, ("keep",)) as library:
        _automatic(library)
        if newer_version:
            _version(library.repository, NOW + 2)
            library.repository._connection.commit()
        verdict_id, _chosen = _verdict(library, "keep")
        store = library.repository.cover_taste_judge_store()
        admitted = store.admit_switch(
            library.considerations["keep"]["consideration_id"], verdict_id, NOW + 3
        )
        assert admitted is not newer_version
        assert len(_taste_selections(library)) == (0 if newer_version else 1)


def test_a_newer_verdict_supersedes_an_unwritten_switch(
    tmp_path: Path, record: None
) -> None:
    """A newer version retires the unwritten switch, judges it again first,
    and a new `choose` supersedes it; the suggestion shows meanwhile (K6)."""
    with _switching(tmp_path, ("keep",)) as library:
        _automatic(library)
        assert _wake(library)
        (first,) = _taste_selections(library)
        _version(library.repository, NOW + 2)
        library.repository._connection.commit()
        # Retirement runs first: the unwritten switch is revoked.
        assert _wake(library, NOW + 3)
        assert _selections(library.repository) == []
        store = library.repository.cover_taste_judge_store()
        authority = library.service.current_authority()
        assert store.due(2, 64) == (
            library.considerations["keep"]["consideration_id"],
        ), authority
        _judge(library, {"keep"}, NOW + 200)
        assert _wake(library, NOW + 201)
        (second,) = _taste_selections(library)
        assert (second.taste_version, second.verdict_id) != (
            first.taste_version,
            first.verdict_id,
        )
        assert second.taste_version == 2


def test_a_written_switch_stays_and_is_not_judged_again(
    tmp_path: Path, record: None
) -> None:
    """A taste switch whose write started stays after a new version, and
    still answers its comparison, so it is not due (K6)."""
    with _switching(tmp_path, ("keep",)) as library:
        _automatic(library)
        assert _wake(library)
        application, operation, target = _plan(library, tmp_path)
        target.seed(operation.plan)
        assert application.apply(operation.plan.plan_id).state == "verified"
        _version(library.repository, NOW + 2)
        library.repository._connection.commit()
        assert not _wake(library, NOW + 3)
        assert len(_taste_selections(library)) == 1
        assert library.repository.cover_taste_judge_store().due(2, 64) == ()


def test_suggest_only_retires_unwritten_switches(tmp_path: Path, record: None) -> None:
    """Back to Suggest only, each unwritten switch is revoked (K6, L3)."""
    with _switching(tmp_path) as library:
        _automatic(library)
        assert _wakes(library) == 2
        assert len(_taste_selections(library)) == 2
        # While switched, To decide shows no suggestion for the work.
        assert _detail_suggestion(library, "keep") is None
        library.repository.bind_cover_taste_switching("suggest_only")
        assert _wake(library, NOW + 5)
        assert _selections(library.repository) == []
        assert not _wake(library, NOW + 6)
        # The suggestion shows again.
        assert _detail_suggestion(library, "keep") is not None


def _plan(library: _Library, root: Path) -> Any:
    """Plan the work's one switch through the real guarded writer."""
    return _planned(
        cast(Any, SimpleNamespace(root=root, repository=library.repository)),
        cast(str, None),
    )


def test_a_taste_switch_writes_reads_back_lists_and_puts_back(
    tmp_path: Path, record: None
) -> None:
    """End to end: the switch plans, writes, reads back and lists in
    Switched, and Put back is offered (dry proof a)."""
    with _switching(tmp_path, ("keep",)) as library:
        _automatic(library)
        assert _wake(library)
        (decision,) = _taste_selections(library)
        application, operation, target = _plan(library, tmp_path)
        authority = PersistedFieldEffectAuthority(library.repository)
        assert authority.current(operation.plan.authority)
        target.seed(operation.plan)
        applied = application.apply(operation.plan.plan_id)
        assert applied.state == "verified", applied.failure
        assert target.read(operation.plan).value == operation.plan.desired_value
        assert operation.plan.desired_value == decision.artifact_id
        page = cover_page(library.repository, "switched", None)
        assert [row["review_case_id"] for row in page["items"]] == [
            library.cases["keep"]
        ]
        # Switched names the version and the rules the verdict cited (K10).
        taste = cast("list[dict[str, Any]]", page["items"])[0]["switched"]["taste"]
        assert taste["version"] == 1
        assert [rule["number"] for rule in taste["rules"]] == [1, 2]
        assert all(rule["section"] and rule["text"] for rule in taste["rules"])
        # Activity names the version only (K10).
        events = library.repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )["events"]
        records = [
            event["record"]
            for event in cast("list[dict[str, Any]]", events)
            if event["record"].get("source_kind") == "automatic_decision"
        ]
        assert [record.get("taste_version") for record in records] == [1]


@pytest.mark.parametrize("change", ("version", "suggest_only", "owner"))
def test_a_taste_switch_is_refused_at_write(
    tmp_path: Path, record: None, change: str
) -> None:
    """After a new profile version, after Suggest only, or after an owner
    answer, a planned taste switch is stale before it writes (K5, K6)."""
    with _switching(tmp_path, ("keep",)) as library:
        _automatic(library)
        assert _wake(library)
        application, operation, target = _plan(library, tmp_path)
        authority = PersistedFieldEffectAuthority(library.repository)
        assert authority.current(operation.plan.authority)
        if change == "version":
            _version(library.repository, NOW + 2)
            library.repository._connection.commit()
        elif change == "suggest_only":
            library.repository.bind_cover_taste_switching("suggest_only")
        else:
            _owner_choice(library, "keep", keep=True)
        assert not authority.current(operation.plan.authority)
        target.seed(operation.plan)
        stale = application.apply(operation.plan.plan_id)
        # The owner's answer supersedes the plan; the others stale it.
        assert (stale.state, stale.failure) == (
            ("superseded", "authority_superseded")
            if change == "owner"
            else ("stale", "authority")
        )
        assert target.read(operation.plan).value != operation.plan.desired_value


@pytest.mark.parametrize("tamper", ("candidate", "verdict", "target_state", "reason"))
def test_the_write_binding_reads_the_verdict_row(
    tmp_path: Path, record: None, tamper: str
) -> None:
    """The binding reads the stored verdict, never the decision's copy: a
    candidate or verdict the row does not name, a target state outside the
    identity's two and a changed reason are each refused (K1, K6)."""
    with _switching(tmp_path, ("wrong_shape",), protection="protected") as library:
        _automatic(library)
        assert _wake(library)
        (decision,) = _taste_selections(library)
        connection = library.repository._connection
        case = ReviewCase.create_from_data(
            json.loads(
                library.repository.get_review_case(library.cases["wrong_shape"])
            )["case"]
        )
        report = _current_report(library, "wrong_shape")
        proposal = ProposalSet.from_json(
            connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (case.proposal_set_id,),
            ).fetchone()[0]
        )
        document = json.dumps(
            library.considerations["wrong_shape"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert _cover_automatic_decision_value(
            connection, decision, case, report, proposal, document
        ) == {"kind": "cover", "value": decision.artifact_id}
        values = {
            name: getattr(decision, name)
            for name in CoverAutomaticDecision.create.__code__.co_varnames[
                1 : CoverAutomaticDecision.create.__code__.co_kwonlyargcount + 1
            ]
        }
        if tamper == "candidate":
            # Another selectable candidate, bound in every member but the
            # verdict, which names this decision's own.
            other = next(
                candidate
                for candidate in library.considerations["wrong_shape"]["candidates"]
                if candidate["reference"] != decision.candidate_reference
                and not candidate["incumbent"]
                and candidate["selectable"]
                and candidate["deterministic_citations"]
            )
            values.update(
                candidate_reference=other["reference"],
                artifact_id=other["artifact_id"],
                candidate_id=next(
                    alternative.alternative_id
                    for result in proposal.results
                    if result.destination.value == "cover"
                    for alternative in result.alternatives
                    if alternative.value.value == other["artifact_id"]
                ),
                rank_key=tuple(other["deterministic_rank_key"]),
                citation_ids=tuple(sorted(set(other["deterministic_citations"]))),
            )
        elif tamper == "verdict":
            values.update(verdict_id="taste_verdict_" + "e" * 32)
        elif tamper == "reason":
            values.update(reason="below_floor")
        tampered = (
            CoverAutomaticDecision.create(**values)
            if tamper != "candidate"
            else decision
        )
        policy = json.loads(
            library.considerations["wrong_shape"]["policy_report_document"]
        )
        if tamper == "target_state":
            policy["actual_target_state"] = "known_empty"
        consideration = dict(library.considerations["wrong_shape"])
        consideration["policy_report_document"] = canonical_json(policy)
        if tamper == "candidate":
            verdict = json.loads(
                connection.execute(
                    "SELECT document FROM cover_taste_verdicts WHERE verdict_id=?",
                    (decision.verdict_id,),
                ).fetchone()[0]
            )
            assert verdict["candidate_reference"] == decision.candidate_reference
            tampered = CoverAutomaticDecision.create(**values)
        with pytest.raises(FieldEffectError, match="authority_binding"):
            _cover_automatic_decision_value(
                connection,
                tampered,
                case,
                report,
                proposal,
                json.dumps(
                    consideration,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if tamper == "target_state"
                else document,
            )


def test_an_owner_chosen_cover_is_never_judged_sentinel(tmp_path: Path) -> None:
    """J1 still never judges an owner-answered field (K5 sentinel)."""
    with _switching(tmp_path, ("keep",), judged=()) as library:
        _owner_choice(library, "keep")
        _version(library.repository, NOW + 2)
        library.repository._connection.commit()
        assert library.repository.cover_taste_judge_store().due(2, 64) == ()


# K9: cover Resume, once the owner has taken a cover over from Automatic.


def _cover_field(prepared: Any, service: CoverConsiderationService) -> dict[str, Any]:
    detail = InspectCurrentReviewCase(prepared.repository, service).run(
        prepared.review_case_id
    )
    return next(
        row
        for row in cast("list[dict[str, Any]]", detail["fields"])
        if row["destination"] == "cover"
    )


def _keep_cover(prepared: Any, expected_selection_id: str | None) -> None:
    """The owner keeps the current cover, over a switch when one is named."""
    repository = prepared.repository
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(prepared.review_case_id))["case"]
    )
    proposal = next(row for row in case.proposals if row["destination"] == "cover")
    document = repository.current_cover_consideration_document(prepared.review_case_id)
    repository.record_human_decision(
        HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination="cover",
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="synthetic-owner-keeps-cover-0001",
            occurred_at=11,
        ),
        cover_consideration_id=json.loads(document)["consideration_id"],
        cover_confirmation={
            "expected_selection_id": expected_selection_id,
            "confirm_unverified_match": False,
        },
    )


def _marks(prepared: Any) -> list[tuple[str, str]]:
    return prepared.repository._connection.execute(
        "SELECT review_case_id,consideration_id FROM cover_automatic_held"
    ).fetchall()


@pytest.mark.parametrize("over_switch", [True, False])
def test_cover_resume_shows_only_where_the_stage_would_switch_but_for_the_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, over_switch: bool
) -> None:
    """Dry proof c's case: a Keep over a switch shows no Resume on the
    deciding comparison, and does on a later one the stage would switch. A
    cover kept in To decide, with no switch to keep over, never shows it."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD if over_switch else None,
    )
    with _prepared(tmp_path) as prepared:
        service = _service(prepared)
        selections = _selections(prepared.repository)
        assert [row["kind"] for row in selections] == (
            ["automatic"] if over_switch else []
        )
        _keep_cover(prepared, selections[0]["selection_id"] if selections else None)
        assert _cover_field(prepared, service)["manual_hold"] is False
        assert _marks(prepared) == []
        monkeypatch.setattr(
            cover_qualification_policy,
            "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
            SYNTHETIC_MEASURED_RECORD,
        )
        service.update_policy(CoverPolicy(prefer_edition=True))
        later = json.loads(_reprepare(service, prepared))
        assert json.loads(later["policy_report_document"])["stage"]["outcome"] == (
            "replace"
        )
        # The stage would switch it but for the owner, either way.
        assert _marks(prepared) == [
            (prepared.review_case_id, later["consideration_id"])
        ]
        assert [row["kind"] for row in _selections(prepared.repository)] == ["owner"]
        assert _cover_field(prepared, service)["manual_hold"] is over_switch


def test_cover_resume_hands_the_cover_back_to_automatic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the fresh checks complete and the hold clears, the owner's
    pointer is released and the stage admits its switch in that one
    transaction, with nothing left open after it."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        service = _service(prepared)
        (switch,) = _selections(repository)
        _keep_cover(prepared, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        later = json.loads(_reprepare(service, prepared))
        token = repository.field_effect_manual_hold_token(
            prepared.review_case_id, "cover"
        )
        assert token is not None
        connection = repository._connection
        considerations = repository._cover_considerations
        admit = considerations._admit_automatic_cover
        inside: list[bool] = []

        def observed(document: str, occurred_at: int) -> None:
            inside.append(connection.in_transaction)
            admit(document, occurred_at)

        monkeypatch.setattr(considerations, "_admit_automatic_cover", observed)
        assert (
            repository.clear_field_effect_manual_hold(
                prepared.review_case_id, "cover", 20, token
            )
            is not None
        )
        assert inside == [True]
        assert not connection.in_transaction
        (selection,) = _selections(repository)
        assert selection["kind"] == "automatic"
        assert selection["consideration_id"] == later["consideration_id"]
        assert _marks(prepared) == []
        field = _cover_field(prepared, service)
        assert field["manual_hold"] is False


@pytest.mark.parametrize("error", (MetadataReportError, AutomaticDecisionError))
def test_a_resume_that_cannot_admit_leaves_everything_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    """Review N1: when admission refuses stored state during Resume, the
    whole release rolls back. The hold, the owner's cover and the mark
    stay, and no transaction is left open."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        service = _service(prepared)
        (switch,) = _selections(repository)
        _keep_cover(prepared, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        _reprepare(service, prepared)
        marks = _marks(prepared)
        assert len(marks) == 1
        (kept,) = _selections(repository)
        token = repository.field_effect_manual_hold_token(
            prepared.review_case_id, "cover"
        )
        assert token is not None

        def refused(_document: str, _occurred_at: int) -> None:
            raise error("synthetic refusal")

        monkeypatch.setattr(
            repository._cover_considerations, "_admit_automatic_cover", refused
        )
        with pytest.raises(FieldEffectError):
            repository.clear_field_effect_manual_hold(
                prepared.review_case_id, "cover", 20, token
            )
        assert not repository._connection.in_transaction
        assert (
            repository.field_effect_manual_hold_token(prepared.review_case_id, "cover")
            == token
        )
        assert _selections(repository) == [kept]
        assert _marks(prepared) == marks


@pytest.mark.parametrize("outcome", ("accept", "keep_current", "reject"))
def test_an_answer_after_resume_was_asked_is_not_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Review B1: the owner presses Resume, then answers the cover in To
    decide before the fresh checks complete. The answer takes the hold over,
    so the pending return drops and the completed checks, naming the old
    token, release nothing: the owner's new choice stays. Put back is an
    accept naming a retained candidate, so it takes this same branch."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        service = _service(prepared)
        (switch,) = _selections(repository)
        _keep_cover(prepared, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        later = json.loads(_reprepare(service, prepared))
        assert len(_marks(prepared)) == 1
        token = repository.field_effect_manual_hold_token(
            prepared.review_case_id, "cover"
        )
        assert token is not None
        target = prepared.report.target
        scan = repository.request_review_scan(
            target.connection, target.library, "library-ebooks", "a" * 64, 12
        )
        assert repository.request_field_effect_manual_return(
            prepared.review_case_id, "cover", token, scan.scan_id, 12, 12
        )
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(prepared.review_case_id))["case"]
        )
        proposal = next(row for row in case.proposals if row["destination"] == "cover")
        (kept,) = _selections(repository)
        chosen = next(
            candidate
            for candidate in later["candidates"]
            if not candidate["incumbent"] and candidate["selectable"]
        )
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=str(proposal["proposal_id"]),
                target_fingerprint=case.target_fingerprint,
                destination="cover",
                actor="owner",
                outcome=outcome,
                selected_value={"kind": "cover", "value": chosen["artifact_id"]}
                if outcome == "accept"
                else None,
                idempotency_key="synthetic-owner-answers-after-resume",
                occurred_at=15,
            ),
            cover_consideration_id=later["consideration_id"],
            cover_confirmation={
                "expected_selection_id": kept["selection_id"],
                "confirm_unverified_match": outcome == "accept",
            },
        )
        (answered,) = _selections(repository)
        assert answered["kind"] == "owner"
        assert answered["selection_id"] != kept["selection_id"]
        hold = json.loads(
            repository._connection.execute(
                "SELECT document FROM field_effect_manual_holds "
                "WHERE review_case_id=? AND destination='cover' AND active=1",
                (prepared.review_case_id,),
            ).fetchone()[0]
        )
        assert hold["decision_id"] != token
        assert hold["pending_return"] is None
        # The checks Resume asked for complete and clear the hold they named.
        assert (
            repository.clear_field_effect_manual_hold(
                prepared.review_case_id, "cover", 20, token
            )
            is None
        )
        assert _selections(repository) == [answered]


@pytest.mark.parametrize("paused", [False, True])
def test_an_admission_that_stops_at_its_basis_removes_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, paused: bool
) -> None:
    """Review N4: admitting the marked comparison again works the mark out
    again. While safety is paused the stage can act on nothing, so the mark
    goes and Resume does not show; otherwise it is derived again."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        service = _service(prepared)
        (switch,) = _selections(repository)
        _keep_cover(prepared, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        later = json.loads(_reprepare(service, prepared))
        marks = [(prepared.review_case_id, later["consideration_id"])]
        assert _marks(prepared) == marks
        connection = repository._connection
        if paused:
            connection.execute(
                "UPDATE automatic_safety_state SET paused=1 WHERE singleton=1"
            )
        document = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert document is not None
        repository._cover_considerations._admit_automatic_cover(document, 30)
        connection.commit()
        assert _marks(prepared) == ([] if paused else marks)
        assert [row["kind"] for row in _selections(repository)] == ["owner"]


def test_a_resume_on_an_earlier_case_keeps_an_answer_on_its_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S2: Resume is asked on case A, the work is reviewed again as
    case B, and the owner answers the cover on B. When A's hold then clears
    under its token, B's newer answer stays current: the clear refuses the
    earlier case, and the release frees only the selection A's hold names."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        service = _service(prepared)
        (switch,) = _selections(repository)
        _keep_cover(prepared, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        _reprepare(service, prepared)
        earlier = prepared.review_case_id
        token = repository.field_effect_manual_hold_token(earlier, "cover")
        assert token is not None
        (kept,) = _selections(repository)
        report = MetadataReport.from_json(
            repository._connection.execute(
                "SELECT document FROM metadata_reports WHERE report_id=?",
                (json.loads(repository.get_review_case(earlier))["case"]["report_id"],),
            ).fetchone()[0]
        )
        case_id, successor = _republished(repository, report)
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(row for row in case.proposals if row["destination"] == "cover")
        service.ensure(case_id, successor.to_json(), proposal)
        later = repository.current_cover_consideration_document(case_id)
        assert later is not None
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=str(proposal["proposal_id"]),
                target_fingerprint=case.target_fingerprint,
                destination="cover",
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="synthetic-owner-answers-successor",
                occurred_at=15,
            ),
            cover_consideration_id=json.loads(later)["consideration_id"],
            cover_confirmation={
                "expected_selection_id": kept["selection_id"],
                "confirm_unverified_match": False,
            },
        )
        (answered,) = _selections(repository)
        assert answered["kind"] == "owner"
        assert answered["selection_id"] != kept["selection_id"]
        # A's checks complete. The clear itself refuses a case that is no
        # longer the work's current one, so it releases nothing ...
        assert (
            repository.clear_field_effect_manual_hold(earlier, "cover", 20, token)
            is None
        )
        assert _selections(repository) == [answered]
        # ... and the release it would run, asked directly for A's hold,
        # frees only the selection that hold names, never B's answer.
        repository._connection.execute("BEGIN")
        repository._cover_considerations._resume_automatic_cover(earlier, token, 20)
        repository._connection.commit()
        assert _selections(repository) == [answered]


def test_a_later_comparison_with_no_switch_clears_the_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        service = _service(prepared)
        (switch,) = _selections(prepared.repository)
        _keep_cover(prepared, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        _reprepare(service, prepared)
        assert len(_marks(prepared)) == 1
        # While a provider is still fetching, the stage admits nothing: the
        # later comparison has no mark and no Resume.
        service = _service(prepared, fetching=True)
        service.update_policy(CoverPolicy(prefer_edition=False))
        _reprepare(service, prepared)
        assert _marks(prepared) == []
        assert _cover_field(prepared, service)["manual_hold"] is False
        # Review B2: Resume completes with nothing to switch. The owner's
        # pointer is still released, so the cover stays as it is with no
        # owner choice standing over it ...
        token = prepared.repository.field_effect_manual_hold_token(
            prepared.review_case_id, "cover"
        )
        assert (
            prepared.repository.clear_field_effect_manual_hold(
                prepared.review_case_id, "cover", 20, token
            )
            is not None
        )
        assert _selections(prepared.repository) == []
        # ... and a later check that finds a fault switches it.
        service = _service(prepared)
        service.update_policy(CoverPolicy(prefer_edition=True))
        later = json.loads(_reprepare(service, prepared))
        (selection,) = _selections(prepared.repository)
        assert selection["kind"] == "automatic"
        assert selection["consideration_id"] == later["consideration_id"]


def test_a_resume_while_safety_is_paused_switches_on_a_later_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review B2: safety pauses while Resume's checks run, and the marked
    comparison is admitted again, so its mark goes. The completed Resume
    still releases the owner's cover; once safety resumes, a later
    comparison switches it."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        connection = repository._connection
        service = _service(prepared)
        (switch,) = _selections(repository)
        _keep_cover(prepared, switch["selection_id"])
        service.update_policy(CoverPolicy(prefer_edition=True))
        _reprepare(service, prepared)
        assert len(_marks(prepared)) == 1
        token = repository.field_effect_manual_hold_token(
            prepared.review_case_id, "cover"
        )
        assert token is not None
        connection.execute(
            "UPDATE automatic_safety_state SET paused=1 WHERE singleton=1"
        )
        document = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert document is not None
        repository._cover_considerations._admit_automatic_cover(document, 15)
        connection.commit()
        assert _marks(prepared) == []
        assert (
            repository.clear_field_effect_manual_hold(
                prepared.review_case_id, "cover", 20, token
            )
            is not None
        )
        # Paused, Automatic switches nothing yet, but the owner's choice no
        # longer stands over the cover.
        assert _selections(repository) == []
        connection.execute(
            "UPDATE automatic_safety_state SET paused=0 WHERE singleton=1"
        )
        connection.commit()
        service.update_policy(CoverPolicy(prefer_edition=False))
        later = json.loads(_reprepare(service, prepared))
        (selection,) = _selections(repository)
        assert selection["kind"] == "automatic"
        assert selection["consideration_id"] == later["consideration_id"]


def test_switching_content_never_reaches_operational_output(
    tmp_path: Path,
    record: None,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Admission, retirement, the preview and Switched carry no rule text,
    reference or verdict into stderr, logs, failure history, status or the
    support bundle (S12, A16)."""
    caplog.set_level(logging.DEBUG)
    with _switching(tmp_path) as library:
        repository = library.repository
        _automatic(library)
        assert _wakes(library) == 2
        taste = CoverTasteBuildService(
            repository.cover_taste_store,
            cast("Any", SimpleNamespace()),
            lambda _digest: b"",
            daily_limit=8,
            clock=lambda: NOW + 2,
        )
        preview = taste.preview("unprotected")
        assert preview["found"]
        assert cover_page(repository, "all", None)["items"]
        repository.bind_cover_taste_switching("suggest_only")
        assert _wakes(library, NOW + 3) >= 1
        connection = repository._connection
        texts = [
            rule["text"]
            for (document,) in connection.execute(
                "SELECT document FROM cover_taste_profile_versions"
            )
            for rule in json.loads(document)["rules"]
        ]
        verdicts = [
            cast(str, document)
            for (document,) in connection.execute(
                "SELECT document FROM cover_taste_verdicts"
            )
        ]
        references = {
            cast(str, json.loads(document)["candidate_reference"])
            for document in verdicts
        }
        assert texts and verdicts and references
        bundle = (
            SupportBundle(
                (
                    (
                        "operational_incidents",
                        lambda: support_preparation_incidents(
                            repository.operational_snapshot()
                        ),
                    ),
                )
            )
            .export()
            .decode()
        )
        failures = json.dumps(
            connection.execute("SELECT * FROM cover_taste_judgements").fetchall()
        )
        status = json.dumps(repository.operational_snapshot(), default=str)
    captured = capsys.readouterr()
    for name, text in {
        "stdout": captured.out,
        "stderr": captured.err,
        "log": caplog.text,
        "support bundle": bundle,
        "failure history": failures,
        "status": status,
    }.items():
        for rule in texts:
            assert rule not in text, name
        for reference in references:
            assert reference not in text, name
        for verdict in verdicts:
            assert verdict not in text, name
