"""Shared guarded field-effect fixture and synthetic authority builders."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeAuthorityCurrentness,
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    FieldEffectOperation,
    decision_value_kind,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)

CAPABILITY = "c" * 64
DEPENDENCY = "d" * 64


class Clock:
    def __init__(self) -> None:
        self.value = 10

    def __call__(self) -> int:
        self.value += 1
        return self.value


def decision(
    destination: str, selected: dict[str, object] | None, suffix: str = "a"
) -> HumanDecision:
    return HumanDecision(
        "human_decision_" + suffix * 64,
        "review_case_" + "b" * 64,
        "review_proposal_" + "c" * 64,
        "d" * 64,
        destination,
        "owner",
        "accept",
        selected,
        "e" * 64,
        "f" * 64,
        1,
        "apply",
    )


def envelope(destination: str, value: object) -> dict[str, object]:
    return {"kind": decision_value_kind(destination), "value": value}


def metadata_report(
    title: str,
    content: str,
    file_identity: str,
    item_id: str = "1234567890abcdef",
    library: str = "books",
) -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Exact evaluator candidate",
        canonical="Exact evaluator candidate",
        excerpt="Exact evaluator candidate",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", library, item_id, default_target_fields(title)),
        file=FileFacts(0, content * 64, file_identity * 64),
        local_claims=(claim,),
        local_reasons=("opening_matter_title",),
        providers=(),
    )


@dataclass
class _EffectEnvironment:
    """The guarded field-effect application under test, with its doubles.

    Declared rather than a `SimpleNamespace` so every member keeps the type
    the `setUp` attributes carried; an untyped namespace would silently make
    `service` and `repository` `Any` and retire the site suppressions that
    depend on their real signatures.
    """

    temporary: tempfile.TemporaryDirectory[str]
    database_path: Path
    repository: SQLiteMetadataReportRepository
    target: FakeTargetService
    authority: FakeAuthorityCurrentness
    dependencies: FakeDependencyCurrentness
    backup: FakeBackupReadiness
    capabilities: FakeCapabilityObserver
    consent: FakeConsentCurrentness
    preclaim: FakePreclaimHook
    claim_hook: FakePreclaimHook
    recovery_hook: FakePreclaimHook
    reevaluation: FakeForwardReevaluation
    recovery: PrivateCoverRecoveryStore
    clock: Clock
    service: GuardedFieldApplication


@pytest.fixture
def effects():
    """One guarded field-effect application with its doubles and store."""

    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    os.chmod(root, 0o700)
    database_path = root / "effects.sqlite"
    repository = open_repository(database_path, cross_thread=True)
    target = FakeTargetService()
    authority = FakeAuthorityCurrentness()
    dependencies = FakeDependencyCurrentness()
    backup = FakeBackupReadiness()
    capabilities = FakeCapabilityObserver(CAPABILITY)
    consent = FakeConsentCurrentness(repository.field_effect_consent_envelope_identity)
    preclaim = FakePreclaimHook()
    claim_hook = FakePreclaimHook()
    recovery_hook = FakePreclaimHook()
    reevaluation = FakeForwardReevaluation()
    recovery = PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover")
    clock = Clock()
    environment = _EffectEnvironment(
        temporary,
        database_path,
        repository,
        target,
        authority,
        dependencies,
        backup,
        capabilities,
        consent,
        preclaim,
        claim_hook,
        recovery_hook,
        reevaluation,
        recovery,
        clock,
        GuardedFieldApplication(
            repository,
            target,
            backup,
            capabilities,
            authority,
            dependencies,
            consent,
            recovery,
            reevaluation,
            clock,
            preclaim,
            claim_hook,
            recovery_hook,
        ),
    )
    yield environment
    environment.repository.close()
    temporary.cleanup()


def _plan(
    effects: _EffectEnvironment,
    destination: str = "title",
    pre: object = "old",
    desired: object = "new",
    suffix: str = "a",
) -> FieldEffectOperation:
    selected = envelope(destination, desired)
    source = decision(destination, selected, suffix)
    _authorize(effects, source)
    result = effects.service.plan(
        source,
        connection="primary",
        library="books",
        work_id="book-1",
        destination=destination,
        pre_value=pre,
        desired_value=selected,
        target_fingerprint="d" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert result is not None
    effects.target.seed(result.plan)
    return result


def _authorize(
    effects: _EffectEnvironment,
    source: HumanDecision | AutomaticDecision,
    *,
    work_id: str = "book-1",
    target_version: str = "version-1",
) -> None:
    effects.authority.bind(
        source,
        connection="primary",
        library="books",
        work_id=work_id,
        target_version=target_version,
    )
    effects.consent.allow(
        connection="primary", library="books", destination=source.destination
    )
