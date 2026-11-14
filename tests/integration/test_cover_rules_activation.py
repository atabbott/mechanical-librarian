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
from dataclasses import dataclass
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
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
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
from mechanical_librarian.domain.proposal_set import evaluate_report
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
def _prepared(root: Path) -> Iterator[_Prepared]:
    """Prepare one ebook whose 200x300 current cover is under the floor."""
    os.chmod(root, 0o700)
    database = root / "reports.sqlite"
    repository = open_repository(database)
    report, _ = _qualified_cover_report(
        root,
        incumbent_size=(200, 300),
        candidate_size=(3000, 4500),
        candidate_designation=CoverDesignation.EXACT_EDITION,
        typed_timeout=True,
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
        yield _Prepared(root, repository, report, case_id)
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
    application = GuardedFieldApplication(
        prepared.repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver(CAPABILITY),
        authority,
        FakeDependencyCurrentness(),
        PersistedEffectConsent(prepared.repository),
        PrivateCoverRecoveryStore(
            prepared.root / "recovery", lambda _plan: b"incumbent-original"
        ),
        FakeForwardReevaluation(),
        Clock(),
        FakePreclaimHook(),
        FakePreclaimHook(),
        FakePreclaimHook(),
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
            "schema_version": "owner-settings-v5",
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
        service = _service(prepared)
        authority = PersistedFieldEffectAuthority(repository)
        assert authority.current(operation.plan.authority)
        review = compose_review_application(
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


@pytest.mark.parametrize(
    ("entries", "admitted"),
    # At the bound the original could not be kept, so the switch is only a
    # suggestion; one below it is admitted (#1363 Q7).
    ((0, False), (1, True)),
)
def test_the_retained_store_bound_decides_whether_a_switch_is_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: int, admitted: bool
) -> None:
    monkeypatch.setattr(
        cover_qualification_policy,
        "MEASURED_REPLACEMENT_QUALIFICATION_SHA256",
        SYNTHETIC_MEASURED_RECORD,
    )
    monkeypatch.setattr(retained_covers, "MAXIMUM_RETAINED_ENTRIES", entries)
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
