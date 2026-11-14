"""The measured cover rules decide automation through the real seams (#1363).

Each proof runs review preparation, the cover consideration service, the
SQLite admission and the guarded writer over one real store: a Current covers
change stales an admitted switch before it writes; a full retained store, an
owner choice, a hold and live fetching each leave the switch a suggestion; and
none of it reaches an event, a diagnostic or the support bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
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
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import PersistedEffectConsent
from mechanical_librarian.adapters.operational_evidence import SupportBundle
from mechanical_librarian.adapters.persistence import retained_covers
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.retained_covers import RetainedCoverStore
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.cover_retention import CoverRetention
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.operations import support_preparation_incidents
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain import cover_qualification_policy
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.cover_evidence import CoverDesignation
from mechanical_librarian.domain.cover_rules import (
    COLOUR_BINS,
    LAYOUT_VALUES,
    CoverMeasures,
)
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.audiobook_covers import SYNTHETIC_MEASURED_RECORD
from tests.support.field_effects import CAPABILITY, Clock
from tests.unit.test_shadow_outcome_persistence import (
    _pipeline_configuration,
    _qualified_cover_report,
    _run_qualified_cover_pipeline,
    _test_reviewer_access,
)


@dataclass
class _Prepared:
    root: Path
    repository: SQLiteMetadataReportRepository
    report: MetadataReport
    review_case_id: str
    proposal: ProposalSet


def _selections(repository: SQLiteMetadataReportRepository) -> list[dict[str, Any]]:
    return [
        json.loads(row[0])
        for row in repository._connection.execute(
            "SELECT selection.document FROM current_cover_selection_authorities "
            "current JOIN cover_selection_authorities selection "
            "ON selection.selection_id=current.selection_id"
        )
    ]


def _stage(document: str) -> dict[str, Any]:
    policy = json.loads(json.loads(document)["policy_report_document"])
    return cast("dict[str, Any]", policy["stage"])


@contextmanager
def _prepared(
    root: Path,
    *,
    duplicate_size: tuple[int, int] | None = None,
    candidate_size: tuple[int, int] = (3000, 4500),
    shared_measures: CoverMeasures | None = None,
) -> Iterator[_Prepared]:
    """Prepare one ebook whose 200x300 current cover is under the floor.

    ``duplicate_size`` adds an EPUB copy of the current cover's own picture
    from another original at that size.
    """
    os.chmod(root, 0o700)
    database = root / "reports.sqlite"
    repository = open_repository(database)
    report, _ = _qualified_cover_report(
        root,
        incumbent_size=(200, 300),
        incumbent_duplicate_size=duplicate_size,
        candidate_size=candidate_size,
        candidate_designation=CoverDesignation.EXACT_EDITION,
        typed_timeout=True,
        shared_measures=shared_measures,
    )
    options = dict(DEFAULT_OPTIONS)
    options["cover_order"] = "fixed_source_order"
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["cover_order"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Deterministic confirmed covers", options, destinations
    )
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
    recovery = composition.SQLiteProviderRecoveryStore.open(
        root / "provider-recovery.sqlite"
    )
    supervisor = composition.create_review_pipeline(
        _pipeline_configuration(),
        database,
        root / "catalogue.sqlite",
        root / "artifacts",
        _test_reviewer_access(repository),
        database_authority=repository.authority,
        provider_recovery=recovery,
    )
    try:
        with patch.object(composition, "run_cover_packet_reviewer") as dispatch:
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            for _ in range(64):
                if not pipeline.run_next(1):
                    break
            else:
                pytest.fail("cover pipeline did not reach a terminal state")
        dispatch.assert_not_called()
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM review_preparation_work WHERE item_id='item-1'"
        ).fetchone()[0]
        yield _Prepared(root, repository, report, case_id, proposal)
    finally:
        supervisor.stop()
        repository.close()


def _service(
    prepared: _Prepared, *, fetching: bool = False
) -> CoverConsiderationService:
    recovery = (
        SimpleNamespace(cover_fetching=lambda **_identity: True) if fetching else None
    )
    review = _pipeline_configuration().review
    assert review is not None
    return composition._compose_cover_considerations(
        prepared.repository,
        prepared.root / "artifacts",
        review,
        _test_reviewer_access(prepared.repository),
        cast("Any", recovery),
    )


def _reprepare(service: CoverConsiderationService, prepared: _Prepared) -> str:
    """Prepare the work's cover again, as its next preparation would."""
    case = ReviewCase.create_from_data(
        json.loads(prepared.repository.get_review_case(prepared.review_case_id))["case"]
    )
    proposal = next(row for row in case.proposals if row["destination"] == "cover")
    service.ensure(prepared.review_case_id, prepared.report.to_json(), proposal)
    document = prepared.repository.current_cover_consideration_document(
        prepared.review_case_id
    )
    assert document is not None
    return document


def _planned(
    prepared: _Prepared,
) -> tuple[GuardedFieldApplication, FieldEffectOperation, FakeTargetService]:
    (selection,) = _selections(prepared.repository)
    automatic = CoverAutomaticDecision.from_json(selection["decision_document"])
    assert automatic.reason == "below_floor"
    authority = PersistedFieldEffectAuthority(prepared.repository)
    snapshot = authority.resolve(automatic)
    prepared.repository.grant_field_effect_consents(
        snapshot.connection, (snapshot.library,), (snapshot.destination,), 10
    )
    target = FakeTargetService()
    # The recovery copy is the current cover's own original, which the switch
    # keeps for Put back as composition does.
    originals = CoverArtifactStore(prepared.root / "artifacts")
    recovery = PrivateCoverRecoveryStore(
        prepared.root / "recovery",
        lambda plan: originals.read_original_digest(
            str(plan.pre_value).removeprefix("cover_")
        ),
    )
    clock = Clock()
    application = GuardedFieldApplication(
        prepared.repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver(CAPABILITY),
        authority,
        FakeDependencyCurrentness(),
        PersistedEffectConsent(prepared.repository),
        recovery,
        FakeForwardReevaluation(),
        clock,
        FakePreclaimHook(),
        FakePreclaimHook(),
        FakePreclaimHook(),
        cover_retention=CoverRetention(
            RetainedCoverStore(prepared.root / "retained-covers"),
            prepared.repository,
            recovery.read,
            clock,
        ),
    )
    operation = application.plan(
        automatic,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination=snapshot.destination,
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    return application, operation, target


def _settings(revision: int, protection: str) -> OwnerSettingsDocument:
    return OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v6",
            "revision": revision,
            "connections": [
                {"name": "primary", "endpoint": "https://library.example.invalid"}
            ],
            "libraries": [
                {
                    "name": "ebooks",
                    "connection": "primary",
                    "library_id": "library-1",
                    "media_kind": "ebook",
                }
            ],
            "cover_preferences": {"current_protection": protection},
        }
    )


def _owner_settings_application(
    repository: SQLiteMetadataReportRepository, service: CoverConsiderationService
) -> Any:
    """Store protected Current covers settings and serve their owner writes."""
    initial = _settings(1, "protected")
    repository._connection.execute(
        "INSERT INTO owner_settings VALUES(1,?,?)",
        (initial.revision, _canonical_json(initial.model_dump())),
    )
    repository._connection.executemany(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        (
            ("connection.primary.key", "target-secret-1234"),
            ("owner.password.verifier", "$argon2id$synthetic"),
        ),
    )
    repository._connection.commit()
    return compose_review_application(
        state_owner=cast("Any", repository),
        session_store=cast("Any", repository),
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda _: True,
        session_digest=lambda token: hashlib.sha256(token.encode()).hexdigest(),
        client_digest=lambda value: hashlib.sha256(value.encode()).hexdigest(),
        clock=lambda: 20,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        settings=OwnerSettingsOperations(repository.owner_settings_store()),
        cover_considerations=service,
    )


def test_a_current_covers_change_stales_an_admitted_switch_before_it_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        application, operation, target = _planned(prepared)
        before = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert before is not None
        assert _stage(before) == {
            **_stage(before),
            "outcome": "replace",
            "reason": "below_floor",
        }
        service = _service(prepared)
        authority = PersistedFieldEffectAuthority(repository)
        assert authority.current(operation.plan.authority)
        review = _owner_settings_application(repository, service)

        review.write_owner_settings(_settings(2, "proposals_only"), 1, ())

        # The setting is in the cover policy document, so its reference moved
        # and the admitted switch no longer has current authority.
        assert service.current_authority()[0] == (
            CoverPolicy(current_protection="proposals_only").reference
        )
        assert not authority.current(operation.plan.authority)
        target.seed(operation.plan)
        stale = application.apply(operation.plan.plan_id)
        assert (stale.state, stale.failure) == ("stale", "authority")
        assert target.read(operation.plan).value != operation.plan.desired_value
        # Prepared again under the new setting, the fault is only suggested.
        after = _reprepare(service, prepared)
        assert json.loads(after)["policy_reference"] == (
            CoverPolicy(current_protection="proposals_only").reference
        )
        assert (
            json.loads(after)["consideration_id"]
            != json.loads(before)["consideration_id"]
        )
        assert (_stage(after)["outcome"], _stage(after)["reason"]) == (
            "suggest",
            "below_floor",
        )
        # Nothing new is admitted, and the staled switch is not planned again.
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (1,)
        assert not authority.current(operation.plan.authority)
        assert repository.pending_field_effect_decision_documents(1) == ()
        # The unwritten switch is revoked, so the book page and Library no
        # longer show the suggested cover as chosen.
        assert _selections(repository) == []
        assert (
            repository.read_current_review_case(
                prepared.review_case_id, None
            ).current_cover_selection
            is None
        )


def test_a_current_covers_save_prepares_each_cover_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The save asks for a scan, and the scan compares the cover again (#1363)."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    scan_sql = (
        "SELECT state,requested_at FROM review_preparation_scans "
        "WHERE connection_name='primary' AND library_name='ebooks'"
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        before = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert before is not None
        assert (_stage(before)["outcome"], _stage(before)["reason"]) == (
            "replace",
            "below_floor",
        )
        assert repository._connection.execute(scan_sql).fetchall() == [
            ("completed", 10)
        ]
        review = _owner_settings_application(repository, _service(prepared))

        review.write_owner_settings(_settings(2, "proposals_only"), 1, ())

        # The save itself queued a scan of the library with a prepared cover.
        assert repository._connection.execute(scan_sql).fetchall() == [("queued", 20)]
        # Preparation as composition builds it under the saved settings.
        configured = _pipeline_configuration()
        assert configured.review is not None
        configuration = replace(
            configured,
            review=replace(
                configured.review, cover_current_protection="proposals_only"
            ),
        )
        recovery = composition.SQLiteProviderRecoveryStore.open(
            tmp_path / "provider-recovery-2.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            configuration,
            tmp_path / "reports.sqlite",
            tmp_path / "catalogue.sqlite",
            tmp_path / "artifacts",
            _test_reviewer_access(repository),
            database_authority=repository.authority,
            provider_recovery=recovery,
        )
        reference = CoverPolicy(current_protection="proposals_only").reference
        try:
            assert supervisor._pipeline._cover_policy_reference == reference
            with patch.object(composition, "run_cover_packet_reviewer"):
                pipeline = _run_qualified_cover_pipeline(
                    repository,
                    tmp_path,
                    prepared.report,
                    prepared.proposal,
                    supervisor,
                    cover_policy_reference=reference,
                )
                for _ in range(64):
                    if not pipeline.run_next(1):
                        break
                else:
                    pytest.fail("cover pipeline did not reach a terminal state")
        finally:
            supervisor.stop()
            recovery.close()
        after = repository.current_cover_consideration_document(prepared.review_case_id)
        assert after is not None
        assert json.loads(after)["policy_reference"] == reference
        assert (_stage(after)["outcome"], _stage(after)["reason"]) == (
            "suggest",
            "below_floor",
        )
        # The same catalogue facts, so the work was not read again.
        assert repository._connection.execute(
            "SELECT stage,state,review_case_id FROM review_preparation_work"
        ).fetchall() == [("complete", "complete", prepared.review_case_id)]


def _started(
    state: str,
    application: GuardedFieldApplication,
    operation: FieldEffectOperation,
    target: FakeTargetService,
    repository: SQLiteMetadataReportRepository,
) -> None:
    """Leave the switch's write in ``state``."""
    plan_id = operation.plan.plan_id
    target.seed(operation.plan)
    if state == "verified":
        assert application.apply(plan_id).state == "verified"
    elif state == "may_have_landed":
        # The request reached Audiobookshelf and changed the cover, but no
        # answer came back (#1402).
        def lost_answer(_plan: object) -> None:
            raise RuntimeError("synthetic lost answer")

        target.after_apply = lost_answer
        retried = application.apply(plan_id)
        assert (retried.state, retried.failure) == ("retryable", "target_failure")
        assert target.read(operation.plan).value == operation.plan.desired_value
    else:
        repository.claim_field_effect(plan_id, "planned", "c" * 64, 11)
        if state == "reconciling":
            repository.transition_field_effect(plan_id, "claimed", "applying", 12)
            repository.schedule_field_effect_reconciliation(plan_id, "applying", 13)
    assert repository.get_field_effect(plan_id).state == (
        "retryable" if state == "may_have_landed" else state
    )


@pytest.mark.parametrize(
    "state", ("claimed", "reconciling", "may_have_landed", "verified")
)
def test_a_started_write_keeps_its_selection_after_a_current_covers_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    # The control for the revocation above: a switch whose write started may
    # have happened, so preparing the work again under a changed setting keeps
    # its selection.
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        application, operation, target = _planned(prepared)
        service = _service(prepared)
        _started(state, application, operation, target, prepared.repository)
        (written,) = _selections(prepared.repository)
        service.update_policy(CoverPolicy(current_protection="proposals_only"))
        after = _reprepare(service, prepared)
        assert json.loads(after)["policy_reference"] == (
            CoverPolicy(current_protection="proposals_only").reference
        )
        assert _selections(prepared.repository) == [written]


def test_a_write_that_landed_is_verified_not_staled_by_a_current_covers_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live policy moved after a write that may have landed. Its retry
    # reads Audiobookshelf before refusing for authority: the cover changed,
    # so the switch is recorded as verified and its original stays kept for
    # Put back, instead of being staled and released (#1363, #1402).
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        application, operation, target = _planned(prepared)
        service = _service(prepared)
        _started("may_have_landed", application, operation, target, repository)
        service.update_policy(CoverPolicy(current_protection="proposals_only"))
        authority = PersistedFieldEffectAuthority(repository)
        assert not authority.current(operation.plan.authority)
        assert application.apply(operation.plan.plan_id).state == "verified"
        assert repository._connection.execute(
            "SELECT count(*) FROM retained_cover_removals WHERE plan_id=?",
            (operation.plan.plan_id,),
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM retained_cover_listings WHERE plan_id=?",
            (operation.plan.plan_id,),
        ).fetchone() == (1,)
        # A retry that did not land is still refused for authority.
        assert len(_selections(repository)) == 1


@pytest.mark.parametrize(
    ("bound", "room", "admitted"),
    # At either bound the original could not be kept, so the switch is only a
    # suggestion; with room for one more original it is admitted (#1363 Q7).
    # The store is empty: an entries bound of 0 is full and 1 has room, and a
    # byte bound one byte short of the largest original is full.
    (
        ("entries", 0, False),
        ("entries", 1, True),
        ("bytes", -1, False),
        ("bytes", 0, True),
    ),
)
def test_the_retained_store_bound_decides_whether_a_switch_is_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bound: str,
    room: int,
    admitted: bool,
) -> None:
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    if bound == "entries":
        monkeypatch.setattr(retained_covers, "MAXIMUM_RETAINED_ENTRIES", room)
    else:
        monkeypatch.setattr(
            retained_covers,
            "MAXIMUM_RETAINED_BYTES",
            retained_covers.MAXIMUM_ORIGINAL_BYTES + room,
        )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        document = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert document is not None
        # The report still says why; only the admission differs.
        assert (_stage(document)["outcome"], _stage(document)["reason"]) == (
            "replace",
            "below_floor",
        )
        selections = _selections(repository)
        assert [row["kind"] for row in selections] == (
            ["automatic"] if admitted else []
        )
        assert len(repository.pending_field_effect_decision_documents(1)) == int(
            admitted
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)


@pytest.mark.parametrize("condition", ("none", "owner", "hold", "fetching"))
def test_owner_choice_hold_and_fetching_keep_the_measured_switch_a_suggestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str
) -> None:
    # First prepared with no record, so nothing is admitted; the record then
    # arrives and the work is prepared again under a changed policy.
    monkeypatch.setattr(
        cover_qualification_policy, "MEASURED_REPLACEMENT_QUALIFICATION_SHA256", None
    )
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        first = repository.current_cover_consideration_document(prepared.review_case_id)
        assert first is not None
        assert _selections(repository) == []
        if condition == "owner":
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(prepared.review_case_id))["case"]
            )
            proposal = next(
                row for row in case.proposals if row["destination"] == "cover"
            )
            _service(prepared)
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
                cover_consideration_id=json.loads(first)["consideration_id"],
                cover_confirmation={
                    "expected_selection_id": None,
                    "confirm_unverified_match": False,
                },
            )
        if condition == "hold":
            # A held field: the owner took it back after a write elsewhere.
            repository._set_field_effect_manual_hold(
                prepared.review_case_id, "cover", 11, "synthetic-held-decision"
            )
            repository._connection.commit()
        monkeypatch.setattr(
            cover_qualification_policy,
            "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
            SYNTHETIC_MEASURED_RECORD,
        )
        service = _service(prepared, fetching=condition == "fetching")
        service.update_policy(CoverPolicy(prefer_edition=True))
        after = _reprepare(service, prepared)

        assert (
            json.loads(after)["consideration_id"]
            != json.loads(first)["consideration_id"]
        )
        kinds = [row["kind"] for row in _selections(repository)]
        if condition == "none":
            assert kinds == ["automatic"]
            assert _stage(after)["outcome"] == "replace"
        elif condition == "owner":
            assert kinds == ["owner"]
        else:
            assert kinds == []


def _support_export(repository: SQLiteMetadataReportRepository) -> str:
    """The support bundle as composition builds it over this store."""
    return (
        SupportBundle(
            (
                (
                    "operational_incidents",
                    lambda: support_preparation_incidents(
                        repository.operational_snapshot()
                    ),
                ),
                (
                    "browse_failures",
                    lambda: {
                        "exception_classes": (
                            repository.browse_unavailable_exception_classes()
                        )
                    },
                ),
            )
        )
        .export()
        .decode()
    )


def test_a_stage_outcome_and_a_store_full_suggestion_leak_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No title, path, URL or cover bytes reach an event, diagnostic or bundle (A15)."""
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    monkeypatch.setattr(retained_covers, "MAXIMUM_RETAINED_ENTRIES", 0)
    with _prepared(tmp_path) as prepared:
        repository = prepared.repository
        document = repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert document is not None
        assert _stage(document)["outcome"] == "replace"
        assert _selections(repository) == []
        # Control: the private values are in the store these surfaces read.
        assert "Synthetic title" in document
        diagnostics = [
            str(row[0])
            for row in repository._connection.execute(
                "SELECT coalesce(failure_code,'') FROM review_preparation_work"
            )
        ]
        bundle = _support_export(repository)
        images = [
            path.read_bytes()
            for path in (tmp_path / "artifacts").rglob("*")
            if path.is_file()
        ]
    written = capsys.readouterr()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    surfaces = "\n".join((written.out, written.err, logged, bundle, *diagnostics))
    assert diagnostics == [""]
    for leaked in ("Synthetic title", str(tmp_path), "http://", "https://"):
        assert leaked not in surfaces
    assert images
    for image in images:
        assert image[:16].hex() not in surfaces
        assert image[:16].decode("latin-1") not in surfaces


def _larger_copy_case(
    tmp_path: Path,
    duplicate_size: tuple[int, int],
    *,
    candidate_size: tuple[int, int] = (250, 375),
    shared_measures: CoverMeasures | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    """The stage, the candidates and the selections over a 200x300 current cover.

    Beside it the EPUB shows the same picture from another original at
    ``duplicate_size``; the one reviewed alternative is under the floor too,
    so only a larger copy can cure the current cover.
    """
    with _prepared(
        tmp_path,
        duplicate_size=duplicate_size,
        candidate_size=candidate_size,
        shared_measures=shared_measures,
    ) as prepared:
        document = prepared.repository.current_cover_consideration_document(
            prepared.review_case_id
        )
        assert document is not None
        duplicate = next(
            source
            for source in prepared.report.cover_sources
            if source.kind.value == "epub"
        )
        assert duplicate.artifact is not None
        return (
            _stage(document),
            json.loads(document)["candidates"],
            _selections(prepared.repository),
            duplicate.artifact.artifact_id,
        )


def test_a_larger_copy_of_the_current_picture_replaces_an_undersized_cover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex P2: the packet keeps one image per picture, so the 1200x1800 EPUB
    # copy of the 200x300 current cover was bound to it and never a candidate.
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    stage, candidates, selections, artifact_id = _larger_copy_case(
        tmp_path, (1200, 1800)
    )
    larger = [row for row in candidates if row["candidate_kind"] == "larger_copy"]
    assert [
        (row["artifact_id"], row["width"], row["height"], row["state"])
        for row in larger
    ] == [(artifact_id, 1200, 1800, "recommended")]
    assert larger[0]["selectable"] is True
    assert larger[0]["ordinal"] == len(candidates) - 1
    assert (stage["outcome"], stage["reason"], stage["choice"]) == (
        "replace",
        "below_floor",
        larger[0]["reference"],
    )
    assert [row["kind"] for row in selections] == ["automatic"]
    decision = CoverAutomaticDecision.from_json(selections[0]["decision_document"])
    assert decision.artifact_id == artifact_id


def test_without_the_larger_copy_the_undersized_cover_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Negative control: with no larger copy offered, the same case keeps the
    # undersized cover, as before the fix.
    from mechanical_librarian.application import cover_consideration, cover_policy

    monkeypatch.setattr(cover_policy, "larger_copy_source", lambda *_args: None)
    monkeypatch.setattr(cover_consideration, "larger_copy_source", lambda *_args: None)
    stage, candidates, selections, _artifact = _larger_copy_case(tmp_path, (1200, 1800))
    assert (stage["outcome"], stage["reason"], stage["choice"]) == (
        "keep",
        "below_floor",
        None,
    )
    assert all(row["candidate_kind"] != "larger_copy" for row in candidates)
    assert selections == []


@pytest.mark.parametrize("duplicate_size", ((200, 300), (150, 225)))
def test_a_copy_no_larger_than_the_current_cover_is_not_offered(
    tmp_path: Path, duplicate_size: tuple[int, int]
) -> None:
    stage, candidates, selections, _artifact = _larger_copy_case(
        tmp_path, duplicate_size
    )
    assert (stage["outcome"], stage["choice"]) == ("keep", None)
    assert all(row["candidate_kind"] != "larger_copy" for row in candidates)
    assert selections == []


@pytest.mark.parametrize(
    ("candidate_size", "larger_offered"),
    (((1200, 1800), False), ((1500, 2250), False), ((1000, 1500), True)),
)
def test_no_larger_copy_beside_a_reviewed_copy_at_least_as_large(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_size: tuple[int, int],
    larger_offered: bool,
) -> None:
    # Every image has the same measures, so the reviewed candidate is another
    # copy of the current cover's picture. At least as large as the EPUB copy,
    # it is the sharper copy itself and no larger copy is added; smaller, the
    # larger copy is still offered and is the sharper one.
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    stage, candidates, _selections, artifact_id = _larger_copy_case(
        tmp_path,
        (1200, 1800),
        candidate_size=candidate_size,
        shared_measures=CoverMeasures(
            "0" * 16, "0" * 16, (1,) * COLOUR_BINS, (0,) * LAYOUT_VALUES
        ),
    )
    larger = [row for row in candidates if row["candidate_kind"] == "larger_copy"]
    chosen = next(row for row in candidates if row["reference"] == stage["choice"])
    assert (stage["outcome"], stage["reason"]) == ("replace", "below_floor")
    assert stage["issues"] == ["below_floor", "sharper_copy"]
    assert (chosen["width"], chosen["height"]) == (
        (1200, 1800) if larger_offered else candidate_size
    )
    assert [row["artifact_id"] for row in larger] == (
        [artifact_id] if larger_offered else []
    )
