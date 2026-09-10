"""Real report constructors for synthetic description advice proofs."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    canonical_json,
)
from mechanical_librarian.application.description_advice import DescriptionAdviceService
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.description_advice import (
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
    DescriptionAdviceSettings,
    description_digest,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    ProviderCandidate,
)
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.proposal_evidence import _claim, _target_work_candidate
from tests.unit.test_reviewer_access_application import _Auth


def _description_candidate(
    provider: str,
    identity: str,
    work_identity: str,
    description: str,
) -> ProviderCandidate:
    candidate = _target_work_candidate(provider, identity, work_identity)
    return replace(
        candidate,
        claims=tuple(
            _claim(
                provider,
                identity,
                ClaimKind.DESCRIPTION,
                description,
                scope=ClaimScope.WORK,
                order=claim.raw_order,
            )
            if claim.kind is ClaimKind.DESCRIPTION
            else claim
            for claim in candidate.claims
        ),
    )


def _prepared(
    tmp_path: Path,
    *,
    automatic: bool = True,
    disposition: str = "ready",
    same_current: bool = False,
    pipeline_probe=None,
    output_transform=None,
    during_dispatch=None,
    report_override=None,
    contained_identity=None,
    result_current=True,
):
    repository = open_repository(tmp_path / "reports.sqlite")
    report, category, option, _ = _automatic_no_change_inputs("description_preserved")
    if report_override is not None:
        report = report_override
    existing = _automatic_decisions(
        SimpleNamespace(repository=repository), report, category, option, qualified=True
    )
    deterministic = next(
        decision for decision in existing if decision.destination == "description"
    )
    calls = []
    identity = contained_identity or (
        lambda: ("synthetic-model", b"synthetic-contained-profile")
    )

    def runner(profile, instruction, inputs, schema, images):
        assert profile == identity()[1]
        assert images == ()
        request = ContainedReviewRequest(
            instruction.decode(), inputs.decode(), schema.decode()
        )
        calls.append(request)
        if during_dispatch is not None:
            during_dispatch(repository, access, binding, deterministic)
        response = canonical_json(
            {
                "request_sha256": request.digest,
                "result": {
                    "description": next(
                        field.value
                        for field in report.target.fields
                        if field.destination.value == "description"
                    )
                    if same_current
                    else "Authored synthetic description.",
                    "disposition": disposition,
                },
            }
        )

        return response if output_transform is None else output_transform(response)

    access = ReviewerAccessOperations(
        repository.reviewer_access_store(),
        codex_auth=_Auth("usable"),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_identity=identity,
        codex_binding=lambda: (
            *identity(),
            runner,
        ),
        codex_description_binding=lambda: (
            *identity(),
            runner,
        ),
        openrouter_factory=lambda *_: pytest.fail("unexpected external transport"),
    )
    access.select_codex(expected_revision=1, task="description_rewrite")
    settings = DescriptionAdviceSettings(automatic_application=automatic)
    store = repository.description_advice_store()
    store.bind_settings(settings)
    store.bind_identity(
        lambda engine, _model: (
            (identity()[0], hashlib.sha256(identity()[1]).hexdigest())
            if engine == "codex"
            else None
        )
    )
    binding = DescriptionAdviceBinding(
        review_case_id=deterministic.review_case_id,
        report_id=report.report_id,
        proposal_set_id=deterministic.proposal_set_id,
        target_fingerprint=report.target.fingerprint,
        policy_generation=repository._connection.execute(
            "SELECT generation FROM metadata_policy_state"
        ).fetchone()[0],
        access_revision=2,
        preferences_digest=description_digest(settings.model_dump()),
    )
    if pipeline_probe is not None:
        pipeline_probe(repository, report, deterministic, access, calls)
    expected_calls = max(1, len(calls))
    service = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 10
    )
    candidate = service.prepare(
        report, binding, lambda: store.binding_is_current(binding)
    )
    assert (candidate is not None) == (output_transform is None and result_current)
    assert len(calls) == expected_calls
    assert (
        service.prepare(report, binding, lambda: store.binding_is_current(binding))
        == candidate
    )
    assert len(calls) == expected_calls
    if not result_current:
        historical = store.read(binding.review_case_id)["candidate"]
        candidate = DescriptionAdviceCandidate.model_validate(historical)
    return repository, store, binding, candidate, deterministic


def _guarded_application(repository, root, authority):
    from mechanical_librarian.adapters.field_effect_fakes import (
        FakeBackupReadiness,
        FakeCapabilityObserver,
        FakeDependencyCurrentness,
        FakeForwardReevaluation,
        FakePreclaimHook,
        FakeTargetService,
        PrivateCoverRecoveryStore,
    )
    from mechanical_librarian.adapters.field_effect_runtime import (
        PersistedEffectConsent,
    )
    from mechanical_librarian.application.field_effects import GuardedFieldApplication
    from tests.unit.test_field_effects import CAPABILITY, Clock

    repository.grant_field_effect_consents("primary", ("ebooks",), ("description",), 12)
    from mechanical_librarian.domain.field_effect import field_value_version

    class CanonicalTarget(FakeTargetService):
        def _write(self, plan, value):
            super()._write(plan, value)
            self._versions[self._key(plan)] = field_value_version(
                plan.destination, value
            )

    target = CanonicalTarget()
    application = GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver(CAPABILITY),
        authority,
        FakeDependencyCurrentness(),
        PersistedEffectConsent(repository),
        PrivateCoverRecoveryStore(root / "recovery", lambda _plan: b"synthetic"),
        FakeForwardReevaluation(),
        Clock(),
        FakePreclaimHook(),
        FakePreclaimHook(),
        FakePreclaimHook(),
    )
    return application, target


def _contained_profile(root: Path) -> Path:
    """A valid passive profile whose executable paths must never be opened."""
    profile = root / "description-profile.json"
    profile.write_text(
        canonical_json(
            {
                "auth_store_path": str(root / "auth"),
                "codex": {
                    "path": str(root / "missing-codex"),
                    "sha256": "a" * 64,
                    "version": "codex 1",
                },
                "job_root_path": str(root / "jobs"),
                "limits": {
                    "file_bytes": 131072,
                    "memory_bytes": 1073741824,
                    "output_bytes": 65536,
                    "processes": 32,
                    "timeout_seconds": 30,
                },
                "model": "synthetic-model",
                "reasoning": "medium",
                "sandbox": {
                    "path": str(root / "missing-bwrap"),
                    "sha256": "b" * 64,
                    "version": "bubblewrap 1",
                },
                "schema_version": "contained-reviewer-profile-v1",
            }
        )
    )
    profile.chmod(0o600)
    return profile
