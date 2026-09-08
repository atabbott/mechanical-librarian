from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
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
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _automatic_decision_value,
    _validate_automatic_decision_authority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    cover_reviewer_profile_contract_mismatch,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.contained_reviewer_contract import COVER_MODEL
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.cover_consideration import (
    cover_consideration_is_current,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.field_effects import (
    GuardedFieldApplication,
    TargetObservation,
)
from mechanical_librarian.application.live_updates import LiveUpdateChannel
from mechanical_librarian.application.metadata_policy import (
    MAX_SHADOW_SAMPLE_RESPONSE_BYTES,
    PolicyNotebook,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
    RecordHumanDecision,
)
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.application.reviewer_contracts import CodexDeviceAuthState
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.activity_records import readable_activity_event
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
    qualification_manifest_is_current,
)
from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSourceState,
)
from mechanical_librarian.domain.deterministic_cover_election import (
    strictly_better_cover_rank,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
    field_value_version,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    decision_actions,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    IdentifierRelation,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    comparison_links,
    default_target_fields,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase, ReviewTransition
from mechanical_librarian.domain.reviewer_access import (
    CodexTaskSelection,
    ReviewerAccessDocument,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    compile_rich_metadata_preview,
)
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.interfaces.web.app import (
    _HistoryResponse,
    _ReviewField,
)
from mechanical_librarian.interfaces.web.live_stream import stream_frames
from tests.support.audiobook_reports import admitted_audiobook_report
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.proposal_evidence import (
    _claim,
    _exact_edition_cover,
    _fields,
    _local_creator,
    _local_subtitle,
    _provider,
    _publication_local_isbn,
    _publication_provider,
    _target_work_candidate,
    _target_work_report,
)
from tests.support.proposal_evidence import (
    _report as _proposal_report,
)
from tests.support.proposal_evidence import (
    consensus_local_title as _consensus_local_title,
)
from tests.support.proposal_evidence import (
    consensus_provider_title as _consensus_provider_title,
)
from tests.support.review_cases import _report
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source,
)
from tests.unit.test_cover_packets import (
    _observer_result as _cover_observer_result,
)
from tests.unit.test_cover_packets import (
    _preview as _cover_preview,
)
from tests.unit.test_cover_packets import (
    _report as _cover_report,
)
from tests.unit.test_cover_packets import (
    _source as _cover_source,
)
from tests.unit.test_provider_recovery import _job as _provider_recovery_job
from tests.unit.test_rich_metadata_preview import (
    _automatic_series_report,
    _series_claims,
    _series_provider,
)


class _UsableCodexAuth:
    def activate_lease(self) -> None:
        return None

    def _usable(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("usable")

    start = status = passive_status = current_credential_status = _usable
    recheck_credential_status = _usable

    def cancel(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("cancelled")

    def clear(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("ready")

    def close(self) -> None:
        return None


def _test_reviewer_access(
    repository, review: ReviewWebConfiguration | None = None
) -> ReviewerAccessOperations:
    selected_review = review or _pipeline_configuration().review
    assert selected_review is not None
    if selected_review.cover_reviewer_profile_file is None:
        return composition._compose_reviewer_access(repository, selected_review)
    store = repository.reviewer_access_store()
    document, _configured = store.read()
    if document.cover_observation is None:
        store.replace(
            ReviewerAccessDocument(
                schema_version="reviewer-access-v1",
                revision=document.revision + 1,
                cover_observation=CodexTaskSelection(engine="codex"),
            ),
            document.revision,
        )

    def identity() -> tuple[str, bytes]:
        payload = composition.read_contained_reviewer_profile(
            Path(selected_review.cover_reviewer_profile_file)
        )
        mismatch = cover_reviewer_profile_contract_mismatch(payload)
        if mismatch is not None:
            raise RuntimeError(mismatch)
        return COVER_MODEL, payload

    def binding():
        model, payload = identity()
        return model, payload, composition.run_cover_packet_reviewer

    return ReviewerAccessOperations(
        store,
        codex_auth=_UsableCodexAuth(),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_identity=identity,
        codex_binding=binding,
        openrouter_factory=lambda _key, _model: (_ for _ in ()).throw(
            AssertionError("OpenRouter is not selected")
        ),
    )


def _case(repository):
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    return admitted["review_case_id"]


def test_a_recording_is_never_offered_to_the_automatic_lane() -> None:
    """A6: the projection abstains for an audiobook, whatever its fields say.

    F47 owns audiobook writes, so nothing automatic may act on a recording.
    The work still completes -- abstaining is an answer -- and no shadow
    outcome exists for a later automatic decision to be minted from (#966).
    """
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        report = admitted_audiobook_report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        repository.enter_shadow_preview(1)

        assert "audiobook" == proposal.media_kind
        assert "inactive" == repository.persist_shadow_outcome(
            admitted["review_case_id"], 2
        )
        for table in ("shadow_decision_outcomes", "automatic_decisions"):
            assert (0,) == repository._connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
        repository.close()


def test_a_recorded_decision_is_not_pending_write_work() -> None:
    """P2-1: what F47 will write is not work the owner is asked to permit.

    An accepted audiobook decision is durable and complete in itself. Left in
    the pending queries it would be counted as prepared and offered for consent
    forever, so both the document query and the count exclude it at the SQL
    seam; the same row with `apply` is pending, which is what makes this the
    filter rather than a missing lineage (#966).
    """
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        report = admitted_audiobook_report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        case = ReviewCase.create(proposal, report)
        assert admitted["review_case_id"] == case.review_case_id
        field = next(
            value
            for value in case.proposals
            if value["destination"] == TargetDestination.ASIN.value
        )
        result = field["result"]
        assert type(result) is dict
        alternatives = result["alternatives"]
        assert type(alternatives) is list
        decision = HumanDecision.create(
            case,
            proposal_id=str(field["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=str(field["destination"]),
            actor="owner",
            outcome="accept",
            selected_value=alternatives[0]["value"],
            idempotency_key="audiobook-pending-0001",
            occurred_at=10,
        )
        repository.record_human_decision(decision)
        ebook_report = _report()
        repository.publish(ebook_report, lambda: True)
        ebook_proposal = evaluate_report(ebook_report)
        repository.publish_proposal(ebook_proposal)
        repository.admit_review_case(ebook_proposal)
        ebook_case = ReviewCase.create(ebook_proposal, ebook_report)
        actionable = {value["destination"] for value in ebook_case.actionable}
        ebook_field = next(
            value
            for value in ebook_case.proposals
            if value["destination"] in actionable
        )
        ebook_decision = HumanDecision.create(
            ebook_case,
            proposal_id=str(ebook_field["proposal_id"]),
            target_fingerprint=ebook_case.target_fingerprint,
            destination=str(ebook_field["destination"]),
            actor="owner",
            outcome="accept",
            selected_value=ebook_field["result"]["alternatives"][0]["value"],
            idempotency_key="ebook-pending-0001",
            occurred_at=11,
        )
        repository.record_human_decision(ebook_decision)

        assert "recorded_only" == decision.application
        assert "apply" == ebook_decision.application
        # The ebook decision is the control: the queries reach both lineages,
        # and only the recorded one is withheld.
        pending = repository.pending_field_effect_decision_documents(10)
        assert 1 == len(pending)
        assert ebook_decision.decision_id in pending[0]
        assert decision.decision_id not in pending[0]
        counts = repository.pending_field_effect_decision_counts()
        assert 1 == len(counts)
        assert ebook_decision.destination == counts[0]["destination"]
        repository.close()


def _running_projection(repository, case_id: str) -> str:
    case = json.loads(repository.get_review_case(case_id))["case"]
    scan_id = "review_scan_" + ("1" * 64)
    work_id = "review_work_" + ("2" * 64)
    repository._connection.execute(
        "INSERT INTO review_preparation_scans(scan_id,connection_name,library_name,"
        "library_id,producer_fingerprint,state,requested_at,updated_at,"
        "catalogue_generation,catalogue_fingerprint,total) "
        "VALUES(?,?,?,?,?,'processing',1,1,1,?,1)",
        (scan_id, "primary", "ebooks", "library-1", "3" * 64, "4" * 64),
    )
    repository._connection.execute(
        "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,"
        "library_name,library_id,item_id,file_identity,source_revision,"
        "source_fingerprint,producer_fingerprint,catalogue_generation,"
        "catalogue_fingerprint,stage,state,attempt,recovery_attempt,report_id,"
        "proposal_set_id,review_case_id,failure_code,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'projection','running',1,0,?,?,?,NULL,1)",
        (
            work_id,
            scan_id,
            0,
            "primary",
            "ebooks",
            "library-1",
            "item-1",
            case["file_identity"],
            1,
            "5" * 64,
            "3" * 64,
            1,
            "4" * 64,
            case["report_id"],
            case["proposal_set_id"],
            case_id,
        ),
    )
    repository._connection.commit()
    return work_id


def _staged_projection_action(repository, work_id: str) -> PipelineAction:
    row = repository._connection.execute(
        "SELECT work_id,scan_id,stage,attempt,connection_name,library_name,library_id,"
        "producer_fingerprint,catalogue_generation,catalogue_fingerprint,item_id,"
        "file_identity,source_revision,report_id,proposal_set_id,review_case_id,"
        "source_fingerprint FROM review_preparation_work WHERE work_id=?",
        (work_id,),
    ).fetchone()
    assert row is not None
    checkpoint = repository._connection.execute(
        "SELECT checkpoint FROM current_review_case_lineages WHERE "
        "connection_name=? AND library_name=? AND item_id=?",
        (row[4], row[5], row[10]),
    ).fetchone()
    assert checkpoint is not None
    generation = repository._connection.execute(
        "SELECT generation FROM metadata_policy_state WHERE singleton=1"
    ).fetchone()
    assert generation is not None
    return PipelineAction(
        *cast(
            "tuple[str, str, str, int, str, str, str, str, int | None, str | None, str | None, str | None, int | None, str | None, str | None, str | None, str | None]",
            row,
        ),
        lineage_checkpoint=checkpoint[0],
        policy_generation=generation[0],
    )


def _digest(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _preview_automatic_profile() -> MetadataPolicyProfile:
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations.update(
        title_layout="automatic",
        publisher_preference="automatic",
        tags="ignore",
    )
    return MetadataPolicyProfile.create(
        "Preview automatic intent", dict(DEFAULT_OPTIONS), destinations
    )


def _pipeline_configuration(
    *, cover_reviewer_profile_file: str | None = None
) -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/run/target-token"
            ),
        ),
        (LibraryConfiguration("ebooks", "primary", "library-1", "book", None, ()),),
        (),
        (),
        review=ReviewWebConfiguration(
            "https://review.example.invalid",
            "/run/owner-password",
            cover_reviewer_profile_file=cover_reviewer_profile_file,
        ),
    )


def _maximum_reference_outcome(repository) -> ShadowOutcomeSet:
    case_id = _case(repository)
    repository.enter_shadow_preview(1)
    base = ShadowOutcomeSet.from_json(repository.persist_shadow_outcome(case_id, 2))
    citations = tuple(f"local_{index:064x}" for index in range(256))
    combinations = tuple(
        f"bibliographic_combination_{index:064x}" for index in range(256)
    )
    fields = []
    for field in base.outcomes:
        identity = field._identity_data()
        identity["citation_ids"] = list(citations)
        identity["combination_ids"] = list(combinations)
        identity["qualification_candidate_id"] = "qualification_candidate_" + _digest(
            {
                "action_id": field.action_id,
                "candidate_id": field.candidate_id,
                "catalogue_sha256": base.catalogue_sha256,
                "citation_ids": list(citations),
                "combination_ids": list(combinations),
                "rule_id": field.rule_id,
            }
        )
        fields.append(
            replace(
                field,
                outcome_id="shadow_outcome_" + _digest(identity),
                citation_ids=citations,
                combination_ids=combinations,
                qualification_candidate_id=identity["qualification_candidate_id"],
            )
        )
    identity = base._identity_data()
    identity["outcomes"] = [field.to_data() for field in fields]
    return replace(
        base,
        outcome_set_id="shadow_outcome_set_" + _digest(identity),
        outcomes=tuple(fields),
    )


def _synthetic_automatic_tuple(repository, case_id: str, category: str):
    """A value-free qualified projection packet for persistence race coverage.

    The production projection is separately tested from real reports above.  This
    small packet isolates the append-only admission boundary without importing a
    corpus value or manufacturing a metadata write.
    """
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    members, option = (
        (("title",), "title_and_subtitle")
        if category == "title_layout"
        else (("publishedDate", "publishedYear"), "full_date")
    )
    shared = (
        ("bibliographic_combination_" + "a" * 64,) if category == "title_layout" else ()
    )
    fields = tuple(
        SimpleNamespace(
            disposition="candidate",
            policy_category=category,
            policy_option=option,
            policy_destination="automatic",
            destination=destination,
            citation_ids=("local_" + "a" * 64,),
            combination_ids=shared,
            proposal_id="review_proposal_" + f"{index:064x}",
            candidate_id="alternative_" + f"{index + 16:064x}",
            rule_id="shadow_rule_" + f"{index + 32:064x}",
            outcome_id="shadow_outcome_" + f"{index + 48:064x}",
        )
        for index, destination in enumerate(members)
    )
    policy_id = repository.current_resolved_policy_id(case.target["library"])
    assert policy_id is not None
    generation = repository._connection.execute(
        "SELECT generation FROM metadata_policy_state WHERE singleton=1"
    ).fetchone()[0]
    return SimpleNamespace(
        outcomes=fields,
        review_case_id=case_id,
        report_id=case.report_id,
        proposal_set_id=case.proposal_set_id,
        target_fingerprint=case.target_fingerprint,
        resolved_policy_id=policy_id,
        policy_generation=generation,
    )


def _human_title_decision(repository, case_id: str, occurred_at: int) -> HumanDecision:
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "title"
    )
    result = proposal["result"]
    assert isinstance(result, dict)
    alternatives = result.get("alternatives")
    assert isinstance(alternatives, list) and alternatives
    selected = alternatives[0]["value"]
    assert isinstance(selected, dict)
    return HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="synthetic-human-title-key-0001",
        occurred_at=occurred_at,
    )


def _qualified_cover_report(
    root: Path,
    *,
    incumbent_index: int = 1,
    candidate_index: int = 2,
    incumbent_size: tuple[int, int] | None = None,
    incumbent_duplicate_size: tuple[int, int] | None = None,
    candidate_size: tuple[int, int] | None = None,
    candidate_designation: CoverDesignation = CoverDesignation.EXACT_EDITION,
    typed_timeout: bool = False,
) -> tuple[MetadataReport, object]:
    incumbent_payload = _cover_preview(incumbent_index)
    candidate_payload = _cover_preview(candidate_index)
    incumbent_dimensions = incumbent_size or (
        300 + incumbent_index,
        450 + incumbent_index,
    )
    candidate_dimensions = candidate_size or (
        300 + candidate_index,
        450 + candidate_index,
    )
    incumbent_artifact = CoverArtifact.create(
        f"original-{incumbent_index}".encode(),
        incumbent_payload,
        "image/png",
        *incumbent_dimensions,
    )
    incumbent_duplicate_artifact = (
        CoverArtifact.create(
            b"duplicate-original",
            incumbent_payload,
            "image/png",
            *incumbent_duplicate_size,
        )
        if incumbent_duplicate_size is not None
        else incumbent_artifact
    )
    candidate_artifact = CoverArtifact.create(
        f"original-{candidate_index}".encode(),
        candidate_payload,
        "image/png",
        *candidate_dimensions,
    )
    incumbent = replace(
        _cover_source(1, incumbent_payload, incumbent=True),
        artifact=incumbent_artifact,
    )
    incumbent_corroboration = replace(
        _cover_source(1, incumbent_payload, embedded=True),
        artifact=incumbent_duplicate_artifact,
    )
    candidate = replace(
        _cover_source(3, candidate_payload),
        artifact=candidate_artifact,
        provenance=CoverProvenance(
            "hardcover",
            candidate_designation,
            "work-3",
            "edition-3"
            if candidate_designation is CoverDesignation.EXACT_EDITION
            else None,
        ),
    )
    local = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    bare = ProviderClaim.create(
        provider="hardcover",
        candidate="edition-3",
        kind=ClaimKind.ISBN,
        value="9780306406157",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    provider_claim = replace(bare, links=comparison_links(bare, (local,)))
    fields = tuple(
        TargetField(
            value.destination,
            TargetFieldState.VALUE,
            incumbent_artifact.artifact_id,
        )
        if value.destination is TargetDestination.COVER
        else value
        for value in default_target_fields("Synthetic title")
    )
    sources = [incumbent, incumbent_corroboration, candidate]
    if typed_timeout:
        sources.append(
            replace(
                _cover_source(103, candidate_payload),
                state=CoverSourceState.TIMEOUT,
                artifact=None,
                timeout_scope="attempted",
            )
        )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(local,),
        local_reasons=("opening_matter_isbn",),
        providers=(
            ProviderObservation(
                "hardcover",
                ProviderStatus.CANDIDATES,
                candidates=(
                    ProviderCandidate("edition-3", "work-3", (provider_claim,)),
                ),
            ),
        ),
        cover_sources=tuple(sorted(sources, key=lambda value: value.source_id)),
    )
    artifacts = CoverArtifactStore(root / "artifacts")
    staged = [
        (incumbent, incumbent_payload, f"original-{incumbent_index}".encode()),
        (candidate, candidate_payload, f"original-{candidate_index}".encode()),
    ]
    if incumbent_duplicate_size is not None:
        staged.append(
            (incumbent_corroboration, incumbent_payload, b"duplicate-original")
        )
    for source, payload, original in staged:
        assert source.artifact is not None
        artifacts.stage(source.artifact, original, payload)
    return report, evaluate_report(report)


def _run_qualified_cover_pipeline(
    repository, root: Path, report: MetadataReport, proposal, supervisor
) -> ReviewPreparationPipeline:
    database = root / "reports.sqlite"

    def extract(bound, action, claim_next):
        bound.publish(
            report,
            lambda: True,
            after_publication=lambda current, published: (
                current.complete_review_extraction_publication(
                    action,
                    published.report_id,
                    10,
                    0,
                    None,
                    claim_next,
                    None,
                )
            ),
        )
        return report.report_id, True

    def project(bound, action):
        outcome = bound.persist_shadow_outcome(
            action.review_case_id or "",
            10,
            staged_action=action,
        )
        return "stale" if outcome == "stale" else "complete"

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(
            database, authority=repository.authority
        ),
        libraries=(("primary", "ebooks", "lib-1", "f" * 64),),
        discover=lambda *_: (
            1,
            report.file.content_digest,
            (
                CatalogueEntry(
                    "primary",
                    "ebooks",
                    "lib-1",
                    "item-1",
                    1,
                    "Synthetic cover",
                    (),
                    True,
                    False,
                    "singular-supported",
                    report.file.identity,
                    "/item-1.epub",
                    "epub",
                    "books",
                ),
            ),
        ),
        extract=extract,
        providers=lambda *_: (),
        propose=lambda bound, _: (
            bound.publish_proposal(proposal),
            proposal.proposal_set_id,
        )[1],
        admit=lambda bound, _: (
            lambda value: (value["status"], value.get("review_case_id"))
        )(json.loads(bound.admit_review_case(proposal))),
        project=project,
        before_projection=supervisor._pipeline._before_projection,
        after_advance=supervisor._pipeline._after_advance,
        clock=lambda: 10,
    )
    pipeline.request("primary", "ebooks")
    return pipeline


@pytest.mark.parametrize(
    ("has_incumbent", "expected_rows", "expected_status"),
    ((False, 1, "recommended"), (True, 0, "abstained")),
)
def test_first_automatic_cover_compares_the_actual_target_after_typed_timeout(
    has_incumbent: bool,
    expected_rows: int,
    expected_status: str,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        candidate_payload = _cover_preview(3)
        candidate = _cover_source(3, candidate_payload, embedded=True)
        retained = [candidate]
        artifacts_to_stage = [(candidate, b"original-3", candidate_payload)]
        target_fields = default_target_fields("Synthetic title")
        local_claims: tuple[LocalClaim, ...] = ()
        local_reasons = ("no_opening_claims",)
        providers: tuple[ProviderObservation, ...] = ()

        if has_incumbent:
            incumbent_payload = _cover_preview(1)
            incumbent_artifact = CoverArtifact.create(
                b"original-1", incumbent_payload, "image/png", 3000, 4500
            )
            replacement_artifact = CoverArtifact.create(
                b"original-3", candidate_payload, "image/png", 300, 450
            )
            incumbent = replace(
                _cover_source(1, incumbent_payload, incumbent=True),
                artifact=incumbent_artifact,
            )
            incumbent_corroboration = replace(
                _cover_source(1, incumbent_payload, embedded=True),
                artifact=incumbent_artifact,
            )
            candidate = replace(
                _cover_source(3, candidate_payload),
                artifact=replacement_artifact,
                provenance=CoverProvenance(
                    "hardcover", CoverDesignation.WORK, "work-3"
                ),
            )
            local = LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780306406157",
                canonical="9780306406157",
                excerpt="ISBN 9780306406157",
                applicability="ebook",
                basis="opening_matter",
                checksum="valid_isbn_13",
                applicability_basis="ebook",
                scope=ClaimScope.EDITION,
            )
            bare = ProviderClaim.create(
                provider="hardcover",
                candidate="edition-3",
                kind=ClaimKind.ISBN,
                value="9780306406157",
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="ebook",
            )
            provider_claim = replace(bare, links=comparison_links(bare, (local,)))
            providers = (
                ProviderObservation(
                    "hardcover",
                    ProviderStatus.CANDIDATES,
                    candidates=(
                        ProviderCandidate("edition-3", "work-3", (provider_claim,)),
                    ),
                ),
            )
            local_claims = (local,)
            local_reasons = ("opening_matter_isbn",)
            retained = [incumbent, incumbent_corroboration, candidate]
            artifacts_to_stage = [
                (incumbent, b"original-1", incumbent_payload),
                (candidate, b"original-3", candidate_payload),
            ]
            target_fields = tuple(
                TargetField(
                    value.destination,
                    TargetFieldState.VALUE,
                    incumbent_artifact.artifact_id,
                )
                if value.destination is TargetDestination.COVER
                else value
                for value in target_fields
            )

        timeout = replace(
            _cover_source(103, candidate_payload),
            state=CoverSourceState.TIMEOUT,
            artifact=None,
            timeout_scope="attempted",
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", target_fields),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=local_claims,
            local_reasons=local_reasons,
            providers=providers,
            cover_sources=tuple(
                sorted((*retained, timeout), key=lambda value: value.source_id)
            ),
        )
        if has_incumbent:
            provider_evidence = next(
                row
                for row in report.identifier_evidence
                if row.reference == provider_claim.reference
            )
            assert provider_evidence.relation is IdentifierRelation.ACCEPTED
        artifacts = CoverArtifactStore(root / "artifacts")
        for source, original, payload in artifacts_to_stage:
            assert source.artifact is not None
            artifacts.stage(source.artifact, original, payload)

        repository = open_repository(root / "reports.sqlite")
        options = dict(DEFAULT_OPTIONS)
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
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        cover_proposal = next(
            item for item in case.proposals if item["destination"] == "cover"
        )
        considerations = composition._compose_cover_considerations(
            repository,
            root / "artifacts",
            _pipeline_configuration().review,
            _test_reviewer_access(repository),
        )
        consideration = considerations.ensure(
            case_id,
            report.to_json(),
            {"proposal_id": cover_proposal["proposal_id"]},
        )

        assert consideration["ready"] is True
        assert consideration["policy"]["status"] == expected_status
        assert consideration["policy"]["actual_target_state"] == (
            "confirmed" if has_incumbent else "known_empty"
        )
        confirmed = [
            row
            for row in consideration["candidates"]
            if row["identity"]["outcome"]
            in {"verified_manifestation_match", "verified_work_match"}
        ]
        assert len(confirmed) == (2 if has_incumbent else 1)
        assert all(
            isinstance(row["deterministic_rank_key"], list)
            and len(row["deterministic_rank_key"]) == 7
            for row in confirmed
        )
        if has_incumbent:
            incumbent_row = next(row for row in confirmed if row["incumbent"])
            replacement_row = next(row for row in confirmed if not row["incumbent"])
            assert incumbent_row["deterministic_rank_key"][:6] == [
                0,
                0,
                -13_500_000,
                -3000,
                -4500,
                0,
            ]
            assert replacement_row["deterministic_rank_key"][:6] == [
                1,
                0,
                -135_000,
                -300,
                -450,
                1,
            ]
            assert not strictly_better_cover_rank(
                replacement_row["deterministic_rank_key"],
                incumbent_row["deterministic_rank_key"],
            )
        assert any(row["selectable"] for row in consideration["candidates"])
        assert {row["reason"] for row in consideration["omissions"]} == (
            {"duplicate_preview", "source_timeout"}
            if has_incumbent
            else {"source_timeout"}
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (expected_rows,)
        repository.close()


def _assert_no_cover_effect_authority(repository) -> None:
    assert (0,) == repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone()


def _assert_manual_cover_surface(
    repository, case_id: str, considerations, reviewer_status: str
) -> None:
    detail = InspectCurrentReviewCase(repository, considerations).run(case_id)
    cover = next(field for field in detail["fields"] if field["destination"] == "cover")
    consideration = cover["cover_consideration"]
    assert consideration is not None
    assert consideration is not None
    assert reviewer_status == consideration["reviewer_status"]
    assert any(candidate["selectable"] for candidate in consideration["candidates"])
    assert cover["editable"]
    assert cover["needs_attention"]
    assert cover["human_decision"] is None


@pytest.mark.parametrize("source_timeout", (False, True), ids=("complete", "timeout"))
def test_ask_retains_and_records_the_sole_cover_choice_after_timeout(
    source_timeout: bool,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        payload = _cover_preview(2)
        candidate = _cover_source(2, payload, embedded=True)
        timeout = replace(
            _cover_source(102, payload),
            state=CoverSourceState.TIMEOUT,
            artifact=None,
            timeout_scope="attempted",
        )
        base = _cover_report((candidate, timeout) if source_timeout else (candidate,))
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", base.target.fields),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=base.cover_sources,
        )
        assert candidate.artifact is not None
        CoverArtifactStore(root / "artifacts").stage(
            candidate.artifact, b"original-2", payload
        )
        proposal = evaluate_report(report)
        cover_result = next(
            result
            for result in proposal.results
            if result.destination is TargetDestination.COVER
        )
        assert cover_result.outcome is (
            ProposalOutcome.UNRESOLVED_CHOICE
            if source_timeout
            else ProposalOutcome.PROPOSE_CHANGE
        )
        assert cover_result.reasons == (
            ("cover_gallery_incomplete", "owner_cover_choice_required")
            if source_timeout
            else ("unique_safe_cover",)
        )
        assert len(cover_result.alternatives) == 1

        repository = open_repository(root / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        cover_proposal = next(
            item for item in case.proposals if item["destination"] == "cover"
        )
        considerations = composition._compose_cover_considerations(
            repository,
            root / "artifacts",
            _pipeline_configuration().review,
            _test_reviewer_access(repository),
        )
        consideration = considerations.ensure(
            case_id,
            report.to_json(),
            {"proposal_id": cover_proposal["proposal_id"]},
        )
        selectable = [row for row in consideration["candidates"] if row["selectable"]]
        assert len(selectable) == 1
        detail = InspectCurrentReviewCase(repository, considerations).run(case_id)
        cover = next(
            field for field in detail["fields"] if field["destination"] == "cover"
        )
        assert cover["needs_attention"] is True
        assert cover["editable"] is True
        browse = BrowseReviewLibrary(repository)
        library = browse.library(lens="all", search=None, limit=10, cursor=None)
        library_item = next(
            item for item in library["items"] if item["review_case_id"] == case_id
        )
        attention = browse.attention(search=None, limit=10, cursor=None)
        assert library_item["attention_count"] == 1
        assert library["counts"]["needs_attention"] == 1
        assert attention["total"] == 1
        assert [
            (item["review_case_id"], item["destination"]) for item in attention["items"]
        ] == [(case_id, "cover")]

        acknowledgement = RecordHumanDecision(repository, clock=lambda: 10).run(
            case_id,
            proposal_id=cover_proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination="cover",
            actor="owner",
            outcome="accept",
            selected_value={"kind": "cover", "value": selectable[0]["artifact_id"]},
            idempotency_key="sole-cover-choice-after-timeout-0001",
            consideration_id=consideration["consideration_id"],
        )
        recorded = InspectCurrentReviewCase(repository, considerations).run(case_id)
        recorded_cover = next(
            field for field in recorded["fields"] if field["destination"] == "cover"
        )
        assert (
            recorded_cover["human_decision"]["decision_id"]
            == acknowledgement["decision_id"]
        )
        assert recorded_cover["needs_attention"] is False
        repository.close()


def test_activity_projects_cover_reviewer_terminal_state_without_private_payloads() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]

        def record(status: str, suffix: str, kind: str = "automatic_prepared") -> None:
            consideration_id = "cover_consideration_" + suffix * 64
            consideration = json.dumps(
                {
                    "candidate": "private-candidate",
                    "document": "private-document",
                    "image": "private-image",
                    "path": "/private/profile",
                    "profile": "private-profile",
                    "recommendation": "private-recommendation",
                    "reviewer_status": status,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            repository._connection.execute(
                "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
                (
                    consideration_id,
                    review_case_id,
                    report_id,
                    suffix * 64,
                    hashlib.sha256(consideration.encode()).hexdigest(),
                    None,
                    10,
                    consideration,
                ),
            )
            event = json.dumps(
                {
                    "consideration_id": consideration_id,
                    "decision_id": None,
                    "kind": kind,
                    "occurred_at": 10,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-event-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            event_id = (
                "cover_consideration_event_"
                + hashlib.sha256(event.encode()).hexdigest()
            )
            repository._connection.execute(
                "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
                (event_id, consideration_id, review_case_id, kind, None, 10, event),
            )

        record("completed", "a", "automatic_prepared")
        record("unavailable", "b", "automatic_prepared")
        record("failed", "c", "automatic_prepared")
        record("completed", "1", "owner_retry_prepared")
        record("unavailable", "2", "owner_retry_prepared")
        record("failed", "3", "owner_retry_prepared")
        record("failed", "d", "stale")
        record("failed", "e", "superseded")
        record("completed", "6", "selected")
        record("completed", "7", "kept_incumbent")
        record("completed", "8", "no_action")
        repository._connection.commit()
        page = repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        cover_events = [
            event for event in page["events"] if event["destination"] == "cover"
        ]
        assert {
            "completed",
            "unavailable",
            "failed",
            "stale",
            "superseded",
            "selected",
            "kept_incumbent",
            "no_action",
        } == {event["state"] for event in cover_events}
        assert {"completed", "unavailable", "failed"} == {
            event["state"]
            for event in cover_events
            if event["origin"] == "automatic_pipeline"
        }
        assert {"completed", "unavailable", "failed"} == {
            event["state"] for event in cover_events if event["origin"] == "owner_retry"
        }
        assert {"review"} == {
            event["event_type"]
            for event in cover_events
            if event["origin"] == "owner_retry"
        }
        assert "safety" == next(
            event["event_type"] for event in cover_events if event["state"] == "stale"
        )
        reviews = repository.field_effect_activity_page(
            search=None,
            event_type="review",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        review_cover_events = [
            event for event in reviews["events"] if event["destination"] == "cover"
        ]
        assert {
            "completed",
            "unavailable",
            "failed",
            "selected",
            "kept_incumbent",
            "no_action",
        } == {event["state"] for event in review_cover_events}
        assert {"human"} == {
            event["origin"]
            for event in review_cover_events
            if event["state"] in {"selected", "kept_incumbent", "no_action"}
        }
        serialized = json.dumps(page)
        for value in (
            "private-candidate",
            "private-document",
            "private-image",
            "/private/profile",
            "private-profile",
            "private-recommendation",
        ):
            assert value not in serialized
        repository.close()


def test_activity_fails_closed_for_malformed_cover_consideration_projection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]
        consideration_id = "cover_consideration_" + "f" * 64
        repository._connection.execute(
            "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
            (
                consideration_id,
                review_case_id,
                report_id,
                "f" * 64,
                "0" * 64,
                None,
                10,
                "{malformed",
            ),
        )
        event = json.dumps(
            {
                "consideration_id": consideration_id,
                "decision_id": None,
                "kind": "automatic_prepared",
                "occurred_at": 10,
                "review_case_id": review_case_id,
                "schema_version": "cover-consideration-event-v1",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        repository._connection.execute(
            "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
            (
                "cover_consideration_event_"
                + hashlib.sha256(event.encode()).hexdigest(),
                consideration_id,
                review_case_id,
                "automatic_prepared",
                None,
                10,
                event,
            ),
        )
        repository._connection.commit()
        with pytest.raises(FieldEffectError, match="^persistence$"):
            repository.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )
        repository.close()


def test_activity_accepts_the_cover_document_cap_and_rejects_larger_or_malformed_values() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]

        def record(document: str, suffix: str) -> None:
            consideration_id = "cover_consideration_" + suffix * 64
            event = json.dumps(
                {
                    "consideration_id": consideration_id,
                    "decision_id": None,
                    "kind": "automatic_prepared",
                    "occurred_at": 10,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-event-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            repository._connection.execute(
                "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
                (
                    consideration_id,
                    review_case_id,
                    report_id,
                    suffix * 64,
                    hashlib.sha256(document.encode()).hexdigest(),
                    None,
                    10,
                    document,
                ),
            )
            repository._connection.execute(
                "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
                (
                    "cover_consideration_event_"
                    + hashlib.sha256(event.encode()).hexdigest(),
                    consideration_id,
                    review_case_id,
                    "automatic_prepared",
                    None,
                    10,
                    event,
                ),
            )

        for size, suffix in ((2_097_153, "a"), (12_582_912, "b")):
            prefix = '{"padding":"'
            suffix_text = '","reviewer_status":"completed"}'
            document = (
                prefix + "x" * (size - len(prefix) - len(suffix_text)) + suffix_text
            )
            record(document, suffix)
            repository._connection.commit()
            page = repository.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )
            assert (1 if suffix == "a" else 2) == len(
                [item for item in page["events"] if item["destination"] == "cover"]
            )

        repository._connection.execute("PRAGMA ignore_check_constraints=ON")
        record('{"padding":"' + "private" + "x" * 12_582_912 + '"}', "c")
        repository._connection.execute("PRAGMA ignore_check_constraints=OFF")
        repository._connection.commit()
        with pytest.raises(FieldEffectError, match="^persistence$"):
            repository.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )
        repository.close()


def test_activity_page_does_not_materialize_repeated_near_limit_cover_documents() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]
        consideration_id = "cover_consideration_" + "a" * 64
        prefix = '{"candidate":"private-candidate","document":"private-document","image":"private-image","padding":"'
        suffix = '","profile":"private-profile","recommendation":"private-recommendation","reviewer_status":"completed"}'
        document = prefix + "x" * (12_582_912 - len(prefix) - len(suffix)) + suffix
        repository._connection.execute(
            "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
            (
                consideration_id,
                review_case_id,
                report_id,
                "a" * 64,
                hashlib.sha256(document.encode()).hexdigest(),
                None,
                10,
                document,
            ),
        )
        for occurred_at in range(100, 200):
            event = json.dumps(
                {
                    "consideration_id": consideration_id,
                    "decision_id": None,
                    "kind": "automatic_prepared",
                    "occurred_at": occurred_at,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-event-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            repository._connection.execute(
                "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
                (
                    "cover_consideration_event_"
                    + hashlib.sha256(event.encode()).hexdigest(),
                    consideration_id,
                    review_case_id,
                    "automatic_prepared",
                    None,
                    occurred_at,
                    event,
                ),
            )
        repository._connection.commit()
        original_loads = json.loads

        def reject_large_json(value: object, *args: object, **kwargs: object) -> object:
            if type(value) is str and len(value.encode("utf-8")) > 65_536:
                raise AssertionError("activity materialized a private cover document")
            return original_loads(value, *args, **kwargs)

        with patch(
            "mechanical_librarian.adapters.persistence.metadata_reports.json.loads",
            side_effect=reject_large_json,
        ):
            page = repository.field_effect_activity_page(
                search=None,
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=100,
            )
        cover_events = [
            event for event in page["events"] if event["destination"] == "cover"
        ]
        # The raw immutable ledger keeps every lifecycle observation while the
        # page still must not materialise its near-limit private consideration
        # document.
        assert 100 == len(cover_events)
        assert {"completed"} == {event["state"] for event in cover_events}
        serialized = json.dumps(page)
        assert len(serialized) < 100_000
        for value in (
            "private-candidate",
            "private-document",
            "private-image",
            "private-profile",
            "private-recommendation",
        ):
            assert value not in serialized
        repository.close()


def test_atomic_cover_preparation_excludes_sibling_drift_and_rolls_back_every_pointer() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]
        consideration_id = "cover_consideration_" + "d" * 64
        document = json.dumps(
            {
                "consideration_id": consideration_id,
                "replay_digest": "e" * 64,
                "report_id": report_id,
                "review_case_id": review_case_id,
                "schema_version": "cover-consideration-v4",
                "supersedes_id": None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        repository._connection.execute(
            "CREATE TEMP TRIGGER abort_cover_event BEFORE INSERT ON cover_consideration_events BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        package_count = repository._connection.execute(
            "SELECT count(*) FROM current_review_packages"
        ).fetchone()[0]

        def finalize() -> object:
            sibling = sqlite3.connect(path, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError):
                    sibling.execute(
                        "UPDATE current_review_case_lineages SET checkpoint=checkpoint"
                    )
            finally:
                sibling.close()
            return repository.record_cover_consideration(
                document,
                review_case_id=review_case_id,
                report_id=report_id,
                replay_digest="e" * 64,
                supersedes_id=None,
                occurred_at=10,
            )

        with pytest.raises(MetadataReportError, match="unavailable"):
            repository.atomic_cover_preparation(finalize)
        assert (0, 0, 0, package_count) == repository._connection.execute(
            "SELECT (SELECT count(*) FROM cover_considerations), (SELECT count(*) FROM cover_consideration_events), (SELECT count(*) FROM current_cover_considerations), (SELECT count(*) FROM current_review_packages)"
        ).fetchone()
        repository.close()


def test_cover_persistence_rejects_prior_consideration_contract() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]
        document = json.dumps(
            {
                "consideration_id": "cover_consideration_" + "c" * 64,
                "replay_digest": "d" * 64,
                "report_id": report_id,
                "review_case_id": review_case_id,
                "schema_version": "cover-consideration-v3",
                "supersedes_id": None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        with pytest.raises(MetadataReportError, match="cover consideration is invalid"):
            repository.record_cover_consideration(
                document,
                review_case_id=review_case_id,
                report_id=report_id,
                replay_digest="d" * 64,
                supersedes_id=None,
                occurred_at=10,
            )
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone() == (0,)
        repository.close()


def test_manual_cover_predecessor_drift_rolls_back_and_releases_the_writer_lock() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]

        def document(suffix: str, supersedes_id: str | None) -> str:
            return json.dumps(
                {
                    "consideration_id": "cover_consideration_" + suffix * 64,
                    "replay_digest": suffix * 64,
                    "report_id": report_id,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-v4",
                    "supersedes_id": supersedes_id,
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        first = document("a", None)
        repository.record_cover_consideration(
            first,
            review_case_id=review_case_id,
            report_id=report_id,
            replay_digest="a" * 64,
            supersedes_id=None,
            occurred_at=10,
        )
        with pytest.raises(MetadataReportError, match="cover consideration changed"):
            repository.record_cover_consideration(
                document("b", None),
                review_case_id=review_case_id,
                report_id=report_id,
                replay_digest="b" * 64,
                supersedes_id=None,
                occurred_at=11,
            )
        assert not repository._connection.in_transaction
        sibling = sqlite3.connect(path, timeout=0)
        try:
            sibling.execute("BEGIN IMMEDIATE")
            sibling.execute("ROLLBACK")
        finally:
            sibling.close()
        assert (1, 1, 1) == repository._connection.execute(
            "SELECT (SELECT count(*) FROM cover_considerations), (SELECT count(*) FROM cover_consideration_events), (SELECT count(*) FROM current_cover_considerations)"
        ).fetchone()
        repository.close()


def test_year_current_reaches_one_durable_publication_keep_and_read_models() -> None:
    """One retained Year projects as Date through the real automatic pipeline."""
    local_date = LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed="2001",
        canonical="2001",
        excerpt="2001",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.YEAR,
    )
    report = _proposal_report(
        fields=_fields(
            published_year=TargetField(
                TargetDestination.PUBLISHED_YEAR, TargetFieldState.VALUE, "2001"
            ),
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
            ),
        ),
        local_claims=(_publication_local_isbn(), local_date),
        providers=(
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.ISBN,
                        "9780306406157",
                        scope=ClaimScope.EDITION,
                        applicability="unknown",
                    ),
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.PUBLICATION_DATE,
                        "2001",
                        scope=ClaimScope.EDITION,
                    ),
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.SUBTITLE,
                        "A retained manual field",
                        scope=ClaimScope.EDITION,
                    ),
                ),
                authority="hardcover",
            ),
            _publication_provider(
                "beta", "beta-edition", ("2001",), authority="open_library"
            ),
            _source(
                "catalogue",
                "manual-edition",
                titles=("Current title",),
                subtitle="A retained manual field",
            ),
        ),
    )
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        options = dict(DEFAULT_OPTIONS)
        options["date_precision"] = "year"
        destinations = {
            name: "unsupported" if name == "explicit" else "ask"
            for name in OPTION_CATALOGUE
        }
        destinations["date_precision"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "Publication keep", options, destinations
        )
        resolved = resolve_policy(profile, report.target.library)
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        assert type(case_id) is str
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        subtitle = next(
            value for value in case.proposals if value["destination"] == "subtitle"
        )
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=subtitle["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination="subtitle",
                actor="owner",
                outcome="accept",
                selected_value=subtitle["result"]["alternatives"][0]["value"],
                idempotency_key="year-current-subtitle-0001",
                occurred_at=4,
            )
        )
        persisted = repository.persist_shadow_outcome(case_id, 4)
        outcome = ShadowOutcomeSet.from_json(persisted)
        assert [("publishedDate", "keep_current")] == [
            (value.destination, value.disposition)
            for value in outcome.outcomes
            if value.destination == "publishedDate"
        ]
        assert ("publishedDate", "keep_current") == repository._connection.execute(
            "SELECT destination,document ->> '$.outcome' FROM automatic_decisions"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions WHERE destination='publishedYear'"
        ).fetchone()
        assert 0 == repository.count_current_attention_fields(None)
        detail = InspectCurrentReviewCase(repository).run(case_id)
        publication = [
            value
            for value in detail["fields"]
            if value["destination"] == "publishedDate"
        ]
        assert 1 == len(publication)
        assert "2001" == publication[0]["current"]["value"]
        assert not publication[0]["needs_attention"]
        isbn = [value for value in detail["fields"] if value["destination"] == "isbn"]
        assert 1 == len(isbn)
        assert not isbn[0]["needs_attention"]
        assert not [
            value
            for value in detail["fields"]
            if value["destination"] == "publishedYear"
        ]
        assert 0 == repository.read_review_attention(None, 50, 0).total
        activity = repository.field_effect_activity_page(
            search=None, event_type="metadata", occurred_after=0, page=1, page_size=50
        )
        assert 1 == activity["total"]
        automatic_event = activity["events"][0]
        assert "publishedDate" == automatic_event["destination"]
        assert "keep_current" == automatic_event["record"]["outcome"]
        assert automatic_event["record"]["selected_value"] is None
        readable_activity_event(
            automatic_event,
            MetadataReport.from_json(activity["report_documents"][report.report_id]),
        )
        evidence = {
            fact["label"]: fact["value"] for fact in automatic_event["evidence"]
        }
        assert "2001" == evidence["Previous value"]
        assert "2001" == evidence["New value"]
        repository.close()

        reopened = open_repository(path, create=False)
        assert persisted == reopened.persist_shadow_outcome(case_id, 5)
        assert ("publishedDate", "keep_current") == reopened._connection.execute(
            "SELECT destination,document ->> '$.outcome' FROM automatic_decisions"
        ).fetchone()
        reopened.close()


def test_full_date_reaches_one_durable_publication_candidate_and_read_models() -> None:
    """One corroborated full Date reaches the real qualified automatic lane."""
    local_date = LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed="2001-02-03",
        canonical="2001-02-03",
        excerpt="2001-02-03",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.DAY,
    )
    report = _proposal_report(
        fields=_fields(
            published_year=TargetField(
                TargetDestination.PUBLISHED_YEAR, TargetFieldState.MISSING
            ),
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
            ),
        ),
        local_claims=(_publication_local_isbn(), local_date),
        providers=(
            _publication_provider(
                "alpha", "alpha-edition", ("2001-02-03",), authority="hardcover"
            ),
            _publication_provider(
                "beta", "beta-edition", ("2001-02-03",), authority="open_library"
            ),
        ),
    )
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        options = dict(DEFAULT_OPTIONS)
        options["date_precision"] = "full_date"
        destinations = {
            name: "unsupported" if name == "explicit" else "ask"
            for name in OPTION_CATALOGUE
        }
        destinations["date_precision"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "Full publication date", options, destinations
        )
        resolved = resolve_policy(profile, report.target.library)
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        assert type(case_id) is str

        persisted = repository.persist_shadow_outcome(case_id, 4)
        outcome = ShadowOutcomeSet.from_json(persisted)
        publication = next(
            value for value in outcome.outcomes if value.destination == "publishedDate"
        )
        proposal_result = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.PUBLISHED_DATE
        )
        assert publication.disposition == "candidate"
        assert publication.candidate_id == proposal_result.selected_alternative_id
        assert (
            publication.citation_ids
            == proposal_result.alternatives[0].evidence_references
        )
        assert 3 == len(publication.citation_ids)

        document = repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='publishedDate'"
        ).fetchone()
        assert document is not None
        decision = AutomaticDecision.from_json(document[0])
        assert decision.outcome == "candidate"
        assert decision.candidate_id == publication.candidate_id
        assert decision.policy_category == "date_precision"
        assert decision.policy_option == "full_date"
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        assert {"kind": "text", "value": "2001-02-03"} == (
            _automatic_decision_value(decision, case, report, proposal, outcome)
        )

        detail = InspectCurrentReviewCase(repository).run(case_id)
        projected = next(
            value
            for value in detail["fields"]
            if value["destination"] == "publishedDate"
        )
        assert projected["automatic_decision"] is not None
        assert projected["automatic_decision"]["selected_value"] == {
            "kind": "text",
            "value": "2001-02-03",
        }
        assert not projected["needs_attention"]
        assert (0,) == repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone()
        history = repository.get_review_case_history(case_id, 10)
        assert any(
            event.get("kind") == "automatic_decision_recorded"
            and event.get("destination") == "publishedDate"
            for event in history
        )
        repository.close()

        reopened = open_repository(path, create=False)
        assert persisted == reopened.persist_shadow_outcome(case_id, 5)
        reopened_decision = next(
            AutomaticDecision.from_json(document)
            for document in reopened.get_review_case_decisions(case_id)
            if AutomaticDecision.from_json(document).destination == "publishedDate"
        )
        assert reopened_decision.to_json() == decision.to_json()
        assert (0,) == reopened._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone()
        reopened.close()


@pytest.mark.parametrize(
    "scenario",
    (
        "exact_title",
        "canonical_title",
        "exact_authors",
        "canonical_authors",
        "formatted_isbn",
        "series_source_order",
        "series_all_qualified",
        "series_primary_narrative",
        "genres_fill_empty",
        "genres_add",
        "genres_replace",
        "tags_fill_empty",
        "tags_add",
        "tags_replace",
        "description_preserved",
        "subtitle_preserved",
    ),
)
def test_automatic_keep_current_is_a_single_no_change_fact_without_guards(
    scenario: str,
) -> None:
    report, category, option, destination = _automatic_no_change_inputs(scenario)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        options = dict(DEFAULT_OPTIONS)
        options[category] = option
        destinations = {
            name: "unsupported" if name == "explicit" else "ask"
            for name in OPTION_CATALOGUE
        }
        destinations[category] = "automatic"
        if category == "contributor_names":
            destinations["isbn_form"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "No-change policy", options, destinations
        )
        resolved = resolve_policy(profile, report.target.library)
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal_set = evaluate_report(
            report,
            resolved.resolved_id,
            human_review_destinations(resolved),
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        assert case_id is not None
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        persisted = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        outcome = next(
            value for value in persisted.outcomes if value.destination == destination
        )
        assert "keep_current" == outcome.disposition
        decisions = tuple(
            AutomaticDecision.from_json(value)
            for value in repository.get_review_case_decisions(case.review_case_id)
        )
        expected_decisions = (
            ("authors", "isbn") if category == "contributor_names" else (destination,)
        )
        assert set(expected_decisions) == {value.destination for value in decisions}
        decision = next(
            value for value in decisions if value.destination == destination
        )
        assert "keep_current" == decision.outcome
        if category in {"series", "genres", "tags", "description_fill", "subtitle"}:
            assert outcome.citation_ids == outcome.combination_ids == ()
            assert (
                repository.field_effect_authority_source(decision)["decision_value"]
                is None
            )
        if scenario == "exact_title":
            corrupt_case = SimpleNamespace(
                proposal_set_id=case.proposal_set_id,
                report_id=case.report_id,
                review_case_id=case.review_case_id,
                target=case.target,
                target_fingerprint=case.target_fingerprint,
                proposals=json.loads(json.dumps(case.proposals)),
            )
            proposal = next(
                value
                for value in corrupt_case.proposals
                if value["proposal_id"] == decision.proposal_id
            )
            alternatives = proposal["result"]["alternatives"]
            preservation = [
                value
                for value in alternatives
                if value["assessment"] == "preserves_target"
            ]
            assert 1 == len(preservation)
            preservation[0]["assessment"] = "replaces_target"
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision, corrupt_case, report, proposal_set, persisted
                )
            preservation[0]["assessment"] = "preserves_target"
            preservation[0]["value"] = {"kind": "text", "value": "Different Title"}
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision, corrupt_case, report, proposal_set, persisted
                )
            preservation[0]["value"] = {"kind": "text", "value": "Secret History"}
            preservation[0]["evidence_references"] = []
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision, corrupt_case, report, proposal_set, persisted
                )
            preservation[0]["evidence_references"] = list(
                next(
                    value
                    for value in proposal_set.results
                    if value.destination.value == destination
                )
                .alternatives[0]
                .evidence_references
            )

            def source_with(**changes: object) -> ShadowOutcomeSet:
                corrupted = persisted.to_data()
                field = next(
                    value
                    for value in corrupted["outcomes"]
                    if value["outcome_id"] == decision.source_outcome_id
                )
                field.update(changes)
                field["qualification_candidate_id"] = (
                    "qualification_candidate_"
                    + _digest(
                        {
                            "action_id": field["action_id"],
                            "candidate_id": field["candidate_id"],
                            "catalogue_sha256": corrupted["catalogue_sha256"],
                            "citation_ids": field["citation_ids"],
                            "combination_ids": field["combination_ids"],
                            "rule_id": field["rule_id"],
                        }
                    )
                )
                identity = dict(field)
                del identity["outcome_id"]
                field["outcome_id"] = "shadow_outcome_" + _digest(identity)
                outcome_identity = dict(corrupted)
                del outcome_identity["outcome_set_id"]
                corrupted["outcome_set_id"] = "shadow_outcome_set_" + _digest(
                    outcome_identity
                )
                return ShadowOutcomeSet.from_json(
                    json.dumps(corrupted, separators=(",", ":"), sort_keys=True)
                )

            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision,
                    case,
                    report,
                    proposal_set,
                    source_with(citation_ids=[]),
                )
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision,
                    case,
                    report,
                    proposal_set,
                    source_with(combination_ids=[]),
                )
        assert repository.persist_shadow_outcome(case_id, 5) == persisted.to_json()
        expected_attention = repository.count_current_attention_fields(None)
        if category in {"title_layout", "contributor_names", "isbn_form"}:
            assert expected_attention == 0

        class UnexpectedAuthority:
            def resolve(self, _decision):
                raise AssertionError("keep-current decision reached authority")

        scheduler = ContinuousGuardedApplication(
            repository,
            {("primary", "ebooks"): cast(GuardedFieldApplication, SimpleNamespace())},
            {("primary", "ebooks"): SimpleNamespace()},
            UnexpectedAuthority(),
            (("primary", "ebooks"),),
            lambda: 10,
            lambda *_: None,
        )
        assert scheduler.schedule() == 0
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (len(expected_decisions),)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_authorities"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_consents"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_actions"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions"
        ).fetchone() == (len(expected_decisions),)
        assert repository.pending_field_effect_decision_documents(100) == ()
        assert repository.count_current_attention_fields(None) == expected_attention
        with patch.object(
            ReviewCase, "from_json", wraps=ReviewCase.from_json
        ) as parse_case:
            page = repository.field_effect_activity_page(
                search="item-1",
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=25,
            )
        # Contributor-name cases record two automatic fields from the same
        # review aggregate. Its private authority is parsed once for the page,
        # not once per decision.
        assert parse_case.call_count == 1
        no_change = next(
            event for event in page["events"] if event["state"] == "no_change_required"
        )
        assert no_change["report_id"] == report.report_id
        repository.close()
        reopened = open_repository(path, create=False)
        assert reopened.persist_shadow_outcome(case_id, 11) == persisted.to_json()
        assert (
            reopened.field_effect_activity_page(
                search="item-1",
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=25,
            )["events"][0]["state"]
            == "no_change_required"
        )
        assert reopened.pending_field_effect_decision_documents(100) == ()
        assert reopened._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (len(expected_decisions),)
        if scenario == "exact_title":
            corrupted = persisted.to_data()
            field = next(
                value
                for value in corrupted["outcomes"]
                if value["outcome_id"] == decision.source_outcome_id
            )
            field["disposition"] = "abstention"
            field["reason_code"] = "no_eligible_action"
            identity = dict(field)
            del identity["outcome_id"]
            field["outcome_id"] = "shadow_outcome_" + _digest(identity)
            outcome_identity = dict(corrupted)
            del outcome_identity["outcome_set_id"]
            corrupted["outcome_set_id"] = "shadow_outcome_set_" + _digest(
                outcome_identity
            )
            trigger = reopened._connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='shadow_decision_outcomes_no_update'"
            ).fetchone()
            assert trigger is not None and type(trigger[0]) is str
            reopened._connection.execute(
                "DROP TRIGGER shadow_decision_outcomes_no_update"
            )
            reopened._connection.execute(
                "UPDATE shadow_decision_outcomes SET document=?",
                (json.dumps(corrupted, separators=(",", ":"), sort_keys=True),),
            )
            reopened._connection.execute(trigger[0])
            reopened._connection.commit()
        reopened.close()
        if scenario == "exact_title":
            with pytest.raises(
                MetadataReportError, match="database is unavailable or incompatible"
            ) as raised:
                open_repository(path, create=False)
            assert isinstance(raised.value.__cause__, FieldEffectError)
            assert "authority_binding" == raised.value.__cause__.code


def test_every_f20_automatic_option_reaches_persisted_effect_authority() -> None:
    isbn10 = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="0306406152",
        canonical="0306406152",
        excerpt="0306406152",
        applicability="ebook",
        checksum="valid_isbn_10",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    isbn_family_provider = _provider(
        "catalogue",
        "edition-1",
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="ebook",
            ),
        ),
    )
    isbn_family_report = _proposal_report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(isbn10,),
        providers=(isbn_family_provider,),
        covers=(_exact_edition_cover(),),
    )
    series_claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.TITLE,
            "Book",
            scope=ClaimScope.WORK,
            order=0,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Reading Order",
            scope=ClaimScope.WORK,
            order=1,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES_SEQUENCE,
            "2",
            scope=ClaimScope.WORK,
            order=2,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Publication Order",
            scope=ClaimScope.WORK,
            order=3,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES_SEQUENCE,
            "4",
            scope=ClaimScope.WORK,
            order=4,
        ),
    )
    series_report = _automatic_series_report(
        _series_provider(
            "catalogue",
            "edition-1",
            "hardcover",
            series_claims[1:],
            current_title="Book",
        ),
        _series_provider(
            "search",
            "edition-2",
            "open_library",
            tuple(
                _claim(
                    "search",
                    "edition-2",
                    claim.kind,
                    claim.value,
                    scope=claim.scope,
                    order=claim.raw_order,
                )
                for claim in series_claims[1:]
            ),
            current_title="Book",
        ),
        current_title="Book",
    )
    genre_claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.GENRE,
            value,
            scope=ClaimScope.WORK,
            order=index,
        )
        for index, value in enumerate(("Fantasy", "Adventure"))
    )
    genre_report = _proposal_report(
        fields=_fields(
            genres=TargetField(
                TargetDestination.GENRES,
                TargetFieldState.VALUE,
                ("Existing",),
            )
        ),
        providers=(_provider("catalogue", "edition-1", genre_claims),),
    )
    genre_empty_report = _proposal_report(
        fields=_fields(
            genres=TargetField(
                TargetDestination.GENRES,
                TargetFieldState.EMPTY,
            )
        ),
        providers=(_provider("catalogue", "edition-1", genre_claims),),
    )
    work_report = _target_work_report(
        (
            (
                "catalogue",
                "hardcover",
                (_target_work_candidate("catalogue", "edition-1", "work-1"),),
                True,
            ),
        )
    )
    tag_empty_report = work_report
    tag_value_report = MetadataReport.create(
        target=replace(
            work_report.target,
            fields=tuple(
                TargetField(
                    TargetDestination.TAGS,
                    TargetFieldState.VALUE,
                    ("Existing",),
                )
                if field.destination is TargetDestination.TAGS
                else field
                for field in work_report.target.fields
            ),
        ),
        file=work_report.file,
        local_claims=work_report.local_claims,
        local_reasons=work_report.local_reasons,
        providers=work_report.providers,
    )
    description_report = work_report
    subtitle_report = _bibliographic_report(
        current_title="Book",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Book",),
                subtitle="A Corroborated Subtitle",
            ),
        ),
        local_claims=(_local_subtitle("A Corroborated Subtitle", ClaimScope.WORK),),
    )
    cases = (
        *(
            (
                "series",
                option,
                series_report,
                "series",
                {
                    "kind": "series",
                    "value": [
                        {"name": "Reading Order", "sequence": "2"},
                    ],
                },
            )
            for option in ("primary_narrative", "source_order", "all_qualified")
        ),
        (
            "subtitle",
            "fill_empty",
            subtitle_report,
            "subtitle",
            {"kind": "text", "value": "A Corroborated Subtitle"},
        ),
        (
            "genres",
            "fill_empty",
            genre_empty_report,
            "genres",
            {"kind": "list", "value": ["Fantasy", "Adventure"]},
        ),
        (
            "genres",
            "add",
            genre_report,
            "genres",
            {
                "kind": "list",
                "value": [
                    "Existing",
                    "Fantasy",
                    "Adventure",
                ],
            },
        ),
        (
            "genres",
            "replace",
            genre_report,
            "genres",
            {"kind": "list", "value": ["Fantasy", "Adventure"]},
        ),
        (
            "tags",
            "fill_empty",
            tag_empty_report,
            "tags",
            {"kind": "list", "value": ["Espionage"]},
        ),
        (
            "tags",
            "add",
            tag_value_report,
            "tags",
            {
                "kind": "list",
                "value": [
                    "Existing",
                    "Espionage",
                ],
            },
        ),
        (
            "tags",
            "replace",
            tag_value_report,
            "tags",
            {"kind": "list", "value": ["Espionage"]},
        ),
        (
            "description_fill",
            "safe_plain_text",
            description_report,
            "description",
            {"kind": "text", "value": "A qualified description."},
        ),
        (
            "isbn_form",
            "preserve_exact",
            isbn_family_report,
            "isbn",
            {"kind": "text", "value": "0306406152"},
        ),
        (
            "isbn_form",
            "prefer_isbn13",
            isbn_family_report,
            "isbn",
            {"kind": "text", "value": "9780306406157"},
        ),
    )
    for category, option, report, destination, expected in cases:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            options = dict(DEFAULT_OPTIONS)
            options[category] = option
            destinations = {
                name: "unsupported" if name == "explicit" else "ask"
                for name in OPTION_CATALOGUE
            }
            destinations[category] = "automatic"
            profile = MetadataPolicyProfile.create(
                "F20 automatic reachability", options, destinations
            )
            resolved = resolve_policy(profile, report.target.library)
            repository.enter_shadow_preview(1)
            repository.create_metadata_policy_draft(profile.canonical_json)
            repository.activate_metadata_policy(
                profile.policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                (
                    (
                        report.target.library,
                        resolved.resolved_id,
                        resolved.canonical_json,
                    ),
                ),
                2,
            )
            repository.activate_qualified_automatic(3)
            proposal = evaluate_report(
                report,
                resolved.resolved_id,
                human_review_destinations(resolved),
            )
            if category == "series":
                series_result = next(
                    value
                    for value in proposal.results
                    if value.destination is TargetDestination.SERIES
                )
                assert 2 == len(series_result.alternatives[0].value.value)
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            case_id = json.loads(repository.admit_review_case(proposal))[
                "review_case_id"
            ]
            outcome = ShadowOutcomeSet.from_json(
                repository.persist_shadow_outcome(case_id, 4)
            )
            field = next(
                value for value in outcome.outcomes if value.destination == destination
            )
            assert (category, option, "automatic", "candidate") == (
                field.policy_category,
                field.policy_option,
                field.policy_destination,
                field.disposition,
            )
            rows = repository._connection.execute(
                "SELECT document FROM automatic_decisions WHERE "
                "review_case_id=? AND destination=?",
                (case_id, destination),
            ).fetchall()
            assert 1 == len(rows)
            decision = AutomaticDecision.from_json(rows[0][0])
            authority = repository.field_effect_authority_source(decision)
            assert expected == authority["decision_value"]
            assert destination == authority["destination"]
            assert case_id == authority["review_case_id"]
            activity = repository.field_effect_activity_page(
                search=None,
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=25,
            )
            automatic_event = next(
                event
                for event in activity["events"]
                if event["event_id"] == decision.decision_id
            )
            assert "candidate" == automatic_event["record"]["outcome"]
            assert expected == automatic_event["record"]["selected_value"]
            if category == "series":
                wrong_source = next(
                    value
                    for value in outcome.outcomes
                    if value.outcome_id != decision.source_outcome_id
                )
                mismatched = AutomaticDecision.create(
                    review_case_id=decision.review_case_id,
                    report_id=decision.report_id,
                    proposal_set_id=decision.proposal_set_id,
                    proposal_id=decision.proposal_id,
                    target_fingerprint=decision.target_fingerprint,
                    destination=decision.destination,
                    outcome=decision.outcome,
                    candidate_id=decision.candidate_id,
                    resolved_policy_id=decision.resolved_policy_id,
                    policy_generation=decision.policy_generation,
                    rule_id=decision.rule_id,
                    policy_category=decision.policy_category,
                    policy_option=decision.policy_option,
                    qualification_manifest_sha256=(
                        decision.qualification_manifest_sha256
                    ),
                    source_outcome_id=wrong_source.outcome_id,
                    bundle_id=decision.bundle_id,
                    occurred_at=decision.occurred_at,
                )
                case = ReviewCase.create_from_data(
                    json.loads(repository.get_review_case(case_id))["case"]
                )
                with pytest.raises(FieldEffectError, match="^authority_binding$"):
                    _validate_automatic_decision_authority(
                        mismatched, case, report, proposal, outcome
                    )
            repository.grant_field_effect_consents(
                authority["connection"],
                (authority["library"],),
                (destination,),
                5,
            )
            target = FakeTargetService()
            service = GuardedFieldApplication(
                repository,
                target,
                FakeBackupReadiness(),
                FakeCapabilityObserver("c" * 64),
                PersistedFieldEffectAuthority(repository),
                FakeDependencyCurrentness(),
                PersistedEffectConsent(repository),
                SimpleNamespace(),
                FakeForwardReevaluation(),
                lambda: 5,
            )
            plan = service.plan(
                decision,
                connection=authority["connection"],
                library=authority["library"],
                work_id=authority["work_id"],
                destination=destination,
                pre_value=authority["pre_value"],
                desired_value=expected,
                target_fingerprint=authority["target_fingerprint"],
                target_version=authority["target_version"],
                dependency_digests=authority["dependency_digests"],
                capability_digest="c" * 64,
            )
            assert plan is not None
            assert plan is not None
            assert expected["value"] == plan.plan.desired_value
            target.seed(plan.plan)
            assert "verified" == service.apply(plan.plan.plan_id).state
            assert expected["value"] == target.read(plan.plan).value
            repository.close()


@pytest.mark.parametrize(
    "consumer", ("repository", "scheduler", "run_cycle", "background_recovery")
)
def test_rich_no_change_storage_failure_retries_without_a_wake(
    tmp_path: Path, consumer: str
) -> None:
    report, category, option, _ = _automatic_no_change_inputs("genres_fill_empty")
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        (decision,) = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            category,
            option,
            qualified=True,
        )

        class UnexpectedAuthority:
            def resolve(self, _decision):
                raise AssertionError("keep-current decision reached authority")

        scheduler = ContinuousGuardedApplication(
            repository,
            {
                ("primary", "ebooks"): cast(
                    GuardedFieldApplication,
                    SimpleNamespace(
                        run_pending_cleanups=lambda **_: 0,
                        resume_admission=lambda: None,
                        begin_drain=lambda: None,
                    ),
                )
            },
            {("primary", "ebooks"): FakeCapabilityObserver("c" * 64)},
            PersistedFieldEffectAuthority(repository)
            if consumer == "background_recovery"
            else UnexpectedAuthority(),
            (("primary", "ebooks"),),
            lambda: 10,
            lambda *_: None,
            polling_seconds=0.1,
        )
        # Exercise the real SQLite write boundary, not an injected domain error.
        repository._connection.execute("PRAGMA query_only=ON")
        if consumer == "background_recovery":
            transitions: list[object] = []
            announcements: list[str] = []
            restored = False
            scheduler.attach_updates(lambda topic, _: announcements.append(topic))

            def serialize(call: Callable[[], object]) -> object:
                nonlocal restored
                if announcements and not restored:
                    assert repository.pending_field_effect_decision_documents(10) == (
                        decision.to_json(),
                    )
                    repository._connection.execute("PRAGMA query_only=OFF")
                    restored = True
                    transitions.append(scheduler.activity()["last_failure"])
                result = call()
                if repository.pending_field_effect_decision_documents(10) == ():
                    scheduler.close_admission()
                return result

            scheduler.attach_serialization(serialize)
            scheduler.admit()
            scheduler._run()
            transitions.append(scheduler.activity()["last_failure"])
            assert transitions == ["persistence", None]
            assert announcements == ["activity", "activity"]
            assert repository.record_current_field_effect_no_change(decision, 11)
            assert repository._connection.execute(
                "SELECT count(*) FROM field_effect_no_changes"
            ).fetchone() == (1,)
            return
        with pytest.raises(FieldEffectError, match="^persistence$") as raised:
            if consumer == "repository":
                repository.record_current_field_effect_no_change(decision, 10)
            elif consumer == "run_cycle":
                scheduler.run_cycle()
            else:
                scheduler.schedule()
        assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
        assert not repository._connection.in_transaction
        assert repository.pending_field_effect_decision_documents(10) == (
            decision.to_json(),
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (0,)

        repository._connection.execute("PRAGMA query_only=OFF")
        # Retry this same scheduler without wake/reset: a storage fault cannot
        # advance its cursor past the still-pending no-change decision.
        assert scheduler.schedule() == 0
        assert repository.pending_field_effect_decision_documents(10) == ()
        assert repository.record_current_field_effect_no_change(decision, 11)
        assert scheduler.schedule() == 0
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (1,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize("field", ("citation_ids", "combination_ids"))
def test_rich_no_change_rejects_corrupted_explicit_proof(
    tmp_path: Path, field: str
) -> None:
    report, category, option, _ = _automatic_no_change_inputs("series_source_order")
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        (decision,) = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            category,
            option,
            qualified=True,
        )
        document = json.loads(repository.list_shadow_outcomes(1)[0][0])
        source = next(
            value for value in document["outcomes"] if value["destination"] == "series"
        )
        source[field] = (
            ["provider_" + "a" * 64]
            if field == "citation_ids"
            else ["combination_" + "b" * 64]
        )
        # A hostile stored document with altered proof must not acquire a
        # no-change fact. No alternate authority chain is fabricated here.
        repository._connection.execute(
            "DROP TRIGGER shadow_decision_outcomes_no_update"
        )
        repository._connection.execute(
            "UPDATE shadow_decision_outcomes SET document=?", (json.dumps(document),)
        )
        repository._connection.commit()
        with pytest.raises(FieldEffectError, match="authority_binding"):
            repository.record_current_field_effect_no_change(decision, 5)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (0,)
    finally:
        repository.close()


def test_rich_no_change_rejects_a_superseded_report(tmp_path: Path) -> None:
    report, category, option, _ = _automatic_no_change_inputs("series_source_order")
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        (decision,) = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            category,
            option,
            qualified=True,
        )
        replacement = _proposal_report(
            fields=_fields("Changed title"), providers=report.providers
        )
        repository.publish(replacement, lambda: True, expected_current=report.report_id)
        with pytest.raises(FieldEffectError, match="authority_binding"):
            repository.record_current_field_effect_no_change(decision, 5)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_populated_series_records_one_no_change_fact(
    tmp_path: Path,
    option: str,
) -> None:
    report, category, _, _ = _automatic_no_change_inputs("series_source_order")
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        (decision,) = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            category,
            option,
            qualified=True,
        )
        assert decision.outcome == "keep_current"
        source = next(
            value
            for value in ShadowOutcomeSet.from_json(
                repository.list_shadow_outcomes(1)[0][0]
            ).outcomes
            if value.destination == "series"
        )
        assert source.disposition == "keep_current"
        assert source.citation_ids == ()
        assert source.combination_ids == ()
        repository.record_current_field_effect_no_change(decision, 5)
        repository.record_current_field_effect_no_change(decision, 6)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (1,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_persistence_rejects_fresh_populated_series_from_faulty_preview(
    tmp_path: Path,
    option: str,
) -> None:
    report, category, _, _ = _automatic_no_change_inputs("series_source_order")

    def faulty_preview(policy, report, proposal):
        preview = compile_rich_metadata_preview(policy, report, proposal)
        result = next(
            item for item in proposal.results if item.destination.value == "series"
        )
        candidate = result.alternatives[0]
        # Model a faulty upstream preview at the collaborator boundary. The
        # persistence consumer must independently reject populated replacements
        # even when the decision and its source carry fresh, matching identities.
        return replace(
            preview,
            fields=tuple(
                replace(
                    field,
                    outcome="projected",
                    reason=None,
                    desired_value=candidate.value.value,
                    qualified_value=candidate.value.value,
                    source_candidate_id=candidate.alternative_id,
                    source_evidence_references=candidate.evidence_references,
                )
                if field.field == "series"
                else field
                for field in preview.fields
            ),
        )

    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        with patch(
            "mechanical_librarian.domain.shadow_outcome.compile_rich_metadata_preview",
            side_effect=faulty_preview,
        ):
            (decision,) = _automatic_decisions(
                SimpleNamespace(repository=repository),
                report,
                category,
                option,
                qualified=True,
            )
        assert decision.outcome == "candidate"
        assert qualification_manifest_is_current(
            "series", option, decision.qualification_manifest_sha256
        )
        with pytest.raises(FieldEffectError, match="authority_binding") as rejected:
            repository.field_effect_authority_source(decision)
        assert isinstance(rejected.value.__cause__, RichMetadataPreviewError)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize(
    "consumer", ("schedule", "run_cycle", "background", "owner_boundary")
)
def test_unbindable_isbn_does_not_starve_a_later_human_acceptance(
    tmp_path: Path,
    consumer: str,
) -> None:
    first, category, option, _ = _automatic_no_change_inputs("unmatched_isbn")
    second_base = _proposal_report(
        providers=(
            _provider(
                "catalogue",
                "edition-2",
                (
                    _claim(
                        "catalogue",
                        "edition-2",
                        ClaimKind.TITLE,
                        "Accepted title",
                        scope=ClaimScope.WORK,
                    ),
                ),
            ),
        )
    )
    second = MetadataReport.create(
        target=TargetFacts("primary", "later", "item-2", second_base.target.fields),
        file=second_base.file,
        local_claims=second_base.local_claims,
        local_reasons=second_base.local_reasons,
        providers=second_base.providers,
    )
    options = dict(DEFAULT_OPTIONS)
    options[category] = option
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations[category] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Cross-library no-change", options, destinations
    )
    resolutions = tuple(
        resolve_policy(profile, report.target.library) for report in (first, second)
    )
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            tuple(
                (report.target.library, policy.resolved_id, policy.canonical_json)
                for report, policy in zip((first, second), resolutions, strict=True)
            ),
            2,
        )
        repository.activate_qualified_automatic(3)
        cases = []
        for report, policy in zip((first, second), resolutions, strict=True):
            proposal = evaluate_report(
                report, policy.resolved_id, human_review_destinations(policy)
            )
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            cases.append(
                json.loads(repository.admit_review_case(proposal))["review_case_id"]
            )
        repository.persist_shadow_outcome(cases[0], 4)
        decision = AutomaticDecision.from_json(
            repository.get_review_case_decisions(cases[0])[0]
        )
        assert decision.outcome == "keep_current"
        # ISBN still has to prove its exact representation: a populated target
        # alone cannot admit this no-op, unlike the canonical rich categories.
        with pytest.raises(FieldEffectError, match="authority_binding"):
            repository.record_current_field_effect_no_change(decision, 5)
        human = _human_title_decision(repository, cases[1], 6)
        repository.record_human_decision(human)
        repository.grant_field_effect_consents("primary", ("later",), ("title",), 7)
        authority = PersistedFieldEffectAuthority(repository)
        capabilities = FakeCapabilityObserver("c" * 64)

        class SeededTarget(FakeTargetService):
            def __init__(self) -> None:
                super().__init__()
                self.seen: set[str] = set()

            def read(self, plan: FieldEffectPlan) -> TargetObservation:
                if plan.plan_id not in self.seen:
                    self.seed(plan)
                    self.seen.add(plan.plan_id)
                return super().read(plan)

        target = SeededTarget()
        application = GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            capabilities,
            authority,
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            SimpleNamespace(),
            FakeForwardReevaluation(),
            lambda: 10,
        )
        scheduler = ContinuousGuardedApplication(
            repository,
            {("primary", library): application for library in ("ebooks", "later")},
            {("primary", library): capabilities for library in ("ebooks", "later")},
            authority,
            (("primary", "ebooks"), ("primary", "later")),
            lambda: 10,
            lambda *_: None,
        )
        if consumer == "owner_boundary":
            owner_calls = []
            gate_calls = 0

            def serialize(call: Callable[[], object]) -> object:
                nonlocal gate_calls
                gate_calls += 1
                assert gate_calls <= 3
                result = call()
                if scheduler.activity()["last_failure"] != "authority_binding":
                    return result
                # This callback's return is the real owner-gate release seam.
                # A progress correction must not drain the entire queue while
                # an owner operation is waiting outside this critical section.
                assert repository._connection.execute(
                    "SELECT count(*) FROM field_effects"
                ).fetchone() == (0,)
                owner_calls.append("stop")
                scheduler.close_admission()
                return result

            scheduler.attach_serialization(serialize)
            scheduler.admit()
            scheduler._run()
            assert owner_calls == ["stop"]
            assert len(repository.pending_field_effect_decision_documents(10)) == 2
            return
        if consumer == "run_cycle":
            assert scheduler.run_cycle(), "a skipped row is progress, not queue idle"
        if consumer == "background":
            scheduler._run_until_idle()
            rows = repository._connection.execute(
                "SELECT plan_id FROM field_effects"
            ).fetchall()
            assert len(rows) == 1, "background drain stranded the later human decision"
            operation = repository.get_field_effect(rows[0][0])
            assert operation.plan.authority.decision_id == human.decision_id
            assert operation.state == "verified"
            assert target.read(operation.plan).value == "Accepted title"
            return
        assert scheduler.schedule() == 1
        assert scheduler.activity()["last_failure"] == "authority_binding"
        pending = repository.next_field_effect_plan_id(None)
        assert pending is not None
        plan_id, library = pending
        assert library == "later"
        operation = repository.get_field_effect(plan_id)
        assert operation.plan.authority.decision_id == human.decision_id
        target.seed(operation.plan)
        assert scheduler.apply(plan_id).state == "verified"
        assert target.read(operation.plan).value == "Accepted title"
        assert repository.pending_field_effect_decision_documents(10) == (
            decision.to_json(),
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize("observation", ("announcement", "next_poll"))
def test_lone_unbindable_decision_reaches_the_activity_stream(
    tmp_path: Path, observation: str
) -> None:
    report, category, option, _ = _automatic_no_change_inputs("unmatched_isbn")
    repository = open_repository(tmp_path / "reports.sqlite")
    channel = LiveUpdateChannel()
    subscription = channel.subscribe(channel.sequence)
    try:
        (decision,) = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            category,
            option,
            qualified=True,
        )
        scheduler = ContinuousGuardedApplication(
            repository,
            {
                ("primary", "ebooks"): cast(
                    GuardedFieldApplication,
                    SimpleNamespace(
                        run_pending_cleanups=lambda **_: 0,
                        resume_admission=lambda: None,
                        begin_drain=lambda: None,
                    ),
                )
            },
            {("primary", "ebooks"): SimpleNamespace()},
            PersistedFieldEffectAuthority(repository),
            (("primary", "ebooks"),),
            lambda: 10,
            lambda *_: None,
        )

        def announce(topic: str, subject: str | None) -> None:
            channel.publish(topic, subject)

        scheduler.attach_updates(announce)
        scheduler.run_cycle()
        assert scheduler.activity()["last_failure"] == "authority_binding"
        assert repository.pending_field_effect_decision_documents(10) == (
            decision.to_json(),
        )
        if observation == "next_poll":

            def serialize(call: Callable[[], object]) -> object:
                result = call()
                scheduler.close_admission()
                return result

            scheduler.attach_serialization(serialize)
            scheduler.admit()
            scheduler._run()
            assert scheduler.activity()["last_failure"] == "authority_binding", (
                "an idle poll is not recovery of the still-rejected decision"
            )
        channel.close()

        async def receive() -> list[bytes]:
            return [
                chunk
                async for chunk in stream_frames(
                    subscription, "a" * 32, still_authenticated=lambda: True
                )
            ]

        frames = b"".join(asyncio.run(receive()))
        assert b"event: activity\n" in frames, (
            "connected Activity has no invalidation to refetch"
        )
        assert b"authority_binding" not in frames  # The stream carries no state.
    finally:
        channel.close()
        repository.close()


def test_uncorroborated_subtitle_under_automatic_mints_no_decision() -> None:
    report = _bibliographic_report(
        current_title="Book",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Book",),
                subtitle="An Uncorroborated Subtitle",
            ),
        ),
    )
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        options = dict(DEFAULT_OPTIONS)
        options["subtitle"] = "fill_empty"
        destinations = {
            name: "unsupported" if name == "explicit" else "ask"
            for name in OPTION_CATALOGUE
        }
        destinations["subtitle"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "F20 uncorroborated subtitle", options, destinations
        )
        resolved = resolve_policy(profile, report.target.library)
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (
                (
                    report.target.library,
                    resolved.resolved_id,
                    resolved.canonical_json,
                ),
            ),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report,
            resolved.resolved_id,
            human_review_destinations(resolved),
        )
        assert any(
            value.destination is TargetDestination.SUBTITLE
            and value.outcome is ProposalOutcome.PROPOSE_CHANGE
            for value in proposal.results
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        subtitle = next(
            value for value in outcome.outcomes if value.destination == "subtitle"
        )
        assert ("subtitle", "fill_empty", "automatic", "abstention") == (
            subtitle.policy_category,
            subtitle.policy_option,
            subtitle.policy_destination,
            subtitle.disposition,
        )
        assert subtitle.candidate_id is None
        rows = repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions WHERE "
            "review_case_id=? AND destination='subtitle'",
            (case_id,),
        ).fetchone()
        assert 0 == rows[0]
        repository.close()


def test_outcome_accepts_packet_valid_configured_provider_citations() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        base = ShadowOutcomeSet.from_json(repository.persist_shadow_outcome(case_id, 2))
        repository.close()
    field = base.outcomes[0]
    citations = ("hardcover_" + "a" * 64,)
    qualification_candidate_id = "qualification_candidate_" + _digest(
        {
            "action_id": field.action_id,
            "candidate_id": field.candidate_id,
            "catalogue_sha256": base.catalogue_sha256,
            "citation_ids": list(citations),
            "combination_ids": list(field.combination_ids),
            "rule_id": field.rule_id,
        }
    )
    identity = field._identity_data()
    identity["citation_ids"] = list(citations)
    identity["qualification_candidate_id"] = qualification_candidate_id
    projected = replace(
        field,
        outcome_id="shadow_outcome_" + _digest(identity),
        citation_ids=citations,
        qualification_candidate_id=qualification_candidate_id,
    )
    assert citations == projected.citation_ids


def test_preview_automatic_matrix_preserves_human_policy_and_records_outcomes() -> None:
    publisher = LocalClaim.create(
        kind=ClaimKind.PUBLISHER,
        observed="Selected EPUB Press",
        canonical="Selected EPUB Press",
        excerpt="Selected EPUB Press",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    local_title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Secret History",
        canonical="Secret History",
        excerpt="Secret History",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    source = _source(
        "catalogue",
        "edition-1",
        titles=("Secret History",),
        subtitle="A Mistborn Novella",
    )
    reports = (
        _report(),
        _proposal_report(
            title=None,
            fields=_fields(None),
            local_claims=(publisher,),
        ),
        _bibliographic_report(current_title="secret history", providers=(source,)),
        _bibliographic_report(
            current_title="secret history",
            providers=(source,),
            local_claims=(local_title,),
        ),
    )
    recorded: set[str] = set()
    for report in reports:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            profile = _preview_automatic_profile()
            repository.enter_shadow_preview(1)
            repository.create_metadata_policy_draft(profile.canonical_json)
            resolved = resolve_policy(profile, report.target.library)
            repository.activate_metadata_policy(
                profile.policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                (
                    (
                        report.target.library,
                        resolved.resolved_id,
                        resolved.canonical_json,
                    ),
                ),
                2,
            )
            effective = human_review_destinations(resolved)
            assert "ask" == effective["title_layout"]
            assert "ignore" == effective["tags"]
            assert "unsupported" == effective["explicit"]
            manual_profile = MetadataPolicyProfile.create(
                "Equivalent human review",
                dict(profile.options),
                effective,
            )
            equivalent = resolve_policy(manual_profile, report.target.library)
            proposal = evaluate_report(report, resolved.resolved_id, effective)
            manual_proposal = evaluate_report(
                report,
                equivalent.resolved_id,
                dict(equivalent.destinations),
            )
            assert proposal.results == manual_proposal.results
            assert (
                proposal.bibliographic_combinations
                == manual_proposal.bibliographic_combinations
            )
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))
            assert "admitted" == admitted["status"]
            default_packet = repository.preview_shadow_packet(
                admitted["review_case_id"]
            )
            preview_packet = repository._persisted_shadow_packet_source(
                admitted["review_case_id"], include_policy_ignored=True
            )[1]
            assert "tags" not in {
                value["destination"] for value in default_packet.proposals
            }
            assert "tags" in {
                value["destination"] for value in preview_packet.proposals
            }
            outcome = ShadowOutcomeSet.from_json(
                repository.persist_shadow_outcome(admitted["review_case_id"], 3)
            )
            recorded.update(value.disposition for value in outcome.outcomes)
            assert "none" == outcome.authority
            abstentions = [
                value for value in outcome.outcomes if value.disposition == "abstention"
            ]
            for abstention in abstentions:
                detail = InspectCurrentReviewCase(repository).run(
                    admitted["review_case_id"]
                )
                field = next(
                    value
                    for value in detail["fields"]
                    if value["destination"] == abstention.destination
                )
                assert "manual_fallback" == field["classification"]
                assert field["needs_attention"]
                assert 1 == repository.count_current_attention_fields(None)
                assert (1, 1) == repository.count_current_review_cases(None)
                attention = repository.read_review_attention(None, 50, 0)
                assert 1 == attention.total
                assert [abstention.destination] == [row[2] for row in attention.rows]
            repository.close()
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        report = _report()
        profile = MetadataPolicyProfile.create(
            "Manual review",
            dict(DEFAULT_OPTIONS),
            {
                category: "unsupported" if category == "explicit" else "ask"
                for category in OPTION_CATALOGUE
            },
        )
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.publish(report, lambda: True)
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(admitted["review_case_id"], 3)
        )
        recorded.update(value.disposition for value in outcome.outcomes)
        repository.close()
    assert {
        "candidate",
        "keep_current",
        "conflict",
        "abstention",
        "unsupported",
        "ignore",
        "manual",
    } == recorded


def test_composed_projection_succeeds_replays_and_reports_stale() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        provider_recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            _test_reviewer_access(repository),
            database_authority=repository.authority,
            provider_recovery=provider_recovery,
        )

        assert "complete" == supervisor._pipeline._project(repository, case_id)
        assert "complete" == supervisor._pipeline._project(repository, case_id)
        current = _report("Current title")
        prior_report_id = json.loads(repository.get_review_case(case_id))["case"][
            "report_id"
        ]
        repository.publish(current, lambda: True, prior_report_id)
        assert "stale" == supervisor._pipeline._project(repository, case_id)
        assert 1 == len(repository.list_shadow_outcomes(50))
        supervisor.stop()
        repository.close()


def test_composed_projection_prepares_one_qualified_cover_advice_before_f22() -> None:
    """Ordinary F22 composition persists advice before its package projection.

    The synthetic runner is the only observer boundary in this test.  The
    pipeline still composes its real consideration service and repository,
    so replay can prove that neither restart nor projection replay dispatches
    a second observer or gains cover-effect authority.
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        payloads = (_cover_preview(1), _cover_preview(2))
        sources = (
            _cover_source(1, payloads[0], incumbent=True),
            _cover_source(2, payloads[1], embedded=True),
        )
        base = _cover_report(sources)
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", base.target.fields),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=base.cover_sources,
        )
        artifacts = CoverArtifactStore(root / "artifacts")
        for index, (source, payload) in enumerate(
            zip(sources, payloads, strict=True), 1
        ):
            assert source.artifact is not None
            artifacts.stage(source.artifact, f"original-{index}".encode(), payload)
        proposal = evaluate_report(report)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        calls: list[object] = []

        def reviewer(*args):
            calls.append(args)
            return _cover_observer_result(*args)

        with (
            patch.object(
                composition,
                "read_contained_reviewer_profile",
                return_value=b"synthetic-profile",
            ),
            patch(
                "tests.unit.test_shadow_outcome_persistence.cover_reviewer_profile_contract_mismatch",
                return_value=None,
            ),
            patch.object(
                composition, "run_cover_packet_reviewer", side_effect=reviewer
            ),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/synthetic-profile"
                ),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _test_reviewer_access(
                    repository,
                    _pipeline_configuration(
                        cover_reviewer_profile_file="/run/synthetic-profile"
                    ).review,
                ),
                database_authority=repository.authority,
                provider_recovery=recovery,
            )

            def extract(bound, action, claim_next):
                bound.publish(
                    report,
                    lambda: True,
                    after_publication=lambda current, published: (
                        current.complete_review_extraction_publication(
                            action, published.report_id, 10, 0, None, claim_next, None
                        )
                    ),
                )
                return report.report_id, True

            def project(bound, action):
                assert (0,) == bound._connection.execute(
                    "SELECT count(*) FROM current_review_packages"
                ).fetchone()
                document = bound.current_cover_consideration_document(
                    action.review_case_id or ""
                )
                assert document is not None
                assert document is not None
                assert "completed" == json.loads(document)["reviewer_status"]
                outcome = bound.persist_shadow_outcome(
                    action.review_case_id or "", 10, staged_action=action
                )
                return "stale" if outcome == "stale" else "complete"

            pipeline = ReviewPreparationPipeline(
                repository_factory=lambda: open_repository(
                    database, authority=repository.authority
                ),
                libraries=(("primary", "ebooks", "lib-1", "f" * 64),),
                discover=lambda *_: (
                    1,
                    report.file.content_digest,
                    (
                        CatalogueEntry(
                            "primary",
                            "ebooks",
                            "lib-1",
                            "item-1",
                            1,
                            "Synthetic cover",
                            (),
                            True,
                            False,
                            "singular-supported",
                            report.file.identity,
                            "/item-1.epub",
                            "epub",
                            "books",
                        ),
                    ),
                ),
                extract=extract,
                providers=lambda *_: (),
                propose=lambda bound, _: (
                    bound.publish_proposal(proposal),
                    proposal.proposal_set_id,
                )[1],
                admit=lambda bound, _: (
                    lambda value: (value["status"], value.get("review_case_id"))
                )(json.loads(bound.admit_review_case(proposal))),
                project=project,
                before_projection=supervisor._pipeline._before_projection,
                after_advance=supervisor._pipeline._after_advance,
                clock=lambda: 10,
            )
            pipeline.request("primary", "ebooks")
            advanced = [pipeline.run_next(1) for _ in range(6)]
            assert [True, True, True, True, True, False] == advanced, (
                repository._connection.execute(
                    "SELECT stage,state,failure_code FROM review_preparation_work"
                ).fetchone()
            )
            case_id = repository._connection.execute(
                "SELECT review_case_id FROM current_review_packages"
            ).fetchone()[0]
            first = repository.current_cover_consideration_document(case_id)
            assert first is not None
            assert (1,) == repository._connection.execute(
                "SELECT count(*) FROM current_review_packages"
            ).fetchone()
            assert (1,) == repository._connection.execute(
                "SELECT count(*) FROM provider_recovery_outbox"
            ).fetchone()
            assert "completed" == json.loads(first)["reviewer_status"]
            assert 1 == len(calls)
            assert not pipeline.run_next(1)
            assert first == repository.current_cover_consideration_document(case_id)
            assert 1 == len(calls)
            assert (
                0
                == repository._connection.execute(
                    "SELECT count(*) FROM field_effects"
                ).fetchone()[0]
            )
            supervisor.stop()
        repository.close()


def test_composed_projection_without_reviewer_persists_unavailable_before_f22() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
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
        with (
            patch.object(
                recovery, "cover_fetching", wraps=recovery.cover_fetching
            ) as cover_fetching,
            patch.object(composition, "run_cover_packet_reviewer") as dispatch,
        ):
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        document = repository.current_cover_consideration_document(case_id)
        assert document is not None
        assert "unavailable" == json.loads(document)["reviewer_status"]
        dispatch.assert_not_called()
        cover_fetching.assert_called_with(
            target_connection="primary",
            library="ebooks",
            item="item-1",
            source_report_id=report.report_id,
        )
        considerations = composition._compose_cover_considerations(
            repository,
            root / "artifacts",
            _pipeline_configuration().review,
            _test_reviewer_access(repository),
        )
        _assert_manual_cover_surface(repository, case_id, considerations, "unavailable")
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM provider_recovery_outbox"
        ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


@pytest.mark.parametrize("effect_state", ("planned", "claimed", "applying", "applied"))
def test_qualified_cover_automatic_is_reviewerless_replay_safe_and_owner_final(
    effect_state: str,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, _ = _qualified_cover_report(
            root,
            incumbent_size=(300, 450),
            incumbent_duplicate_size=(2_000, 3_000),
            candidate_size=(1_000, 1_500),
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
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
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
        with patch.object(composition, "run_cover_packet_reviewer") as dispatch:
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
        dispatch.assert_not_called()
        selection_rows = repository._connection.execute(
            "SELECT selection.document FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id"
        ).fetchall()
        assert 1 == len(selection_rows)
        selection = json.loads(selection_rows[0][0])
        automatic = CoverAutomaticDecision.from_json(selection["decision_document"])
        assert automatic.rank_key == tuple(selection["rank_key"])
        assert (selection["decision_document"],) == (
            repository.pending_field_effect_decision_documents(1)[0],
        )
        assert repository.pending_field_effect_decision_counts() == (
            {
                "connection": "primary",
                "library": "ebooks",
                "origin": "automatic",
                "destination": "cover",
                "count": 1,
            },
        )
        repository.close()
        repository = open_repository(database)
        library = BrowseReviewLibrary(repository).library(
            lens="all", search=None, limit=50, cursor=None
        )
        projected_work = next(
            item
            for item in library["items"]
            if item["review_case_id"] == automatic.review_case_id
        )
        assert projected_work["detail_url"] == f"/review/{automatic.review_case_id}"
        assert projected_work["attention_count"] == 0
        assert library["counts"]["needs_attention"] == 0
        assert (
            BrowseReviewLibrary(repository).attention(
                search=None, limit=50, cursor=None
            )["items"]
            == []
        )
        detail = InspectCurrentReviewCase(repository).run(automatic.review_case_id)
        projected_cover = next(
            field for field in detail["fields"] if field["destination"] == "cover"
        )
        assert projected_cover["automatic_decision"] == {
            "candidate_id": automatic.candidate_id,
            "decision_id": automatic.decision_id,
            "destination": "cover",
            "occurred_at": automatic.occurred_at,
            "outcome": "candidate",
            "proposal_id": automatic.proposal_id,
            "review_case_id": automatic.review_case_id,
            "selected_value": {"kind": "cover", "value": automatic.artifact_id},
            "supersedes_decision_id": None,
            "target_fingerprint": automatic.target_fingerprint,
        }
        transport_cover = {**projected_cover, "effect_capability": "report_only"}
        _ReviewField.model_validate(transport_cover)
        malformed_cover = json.loads(json.dumps(transport_cover))
        del malformed_cover["automatic_decision"]["supersedes_decision_id"]
        with pytest.raises(ValueError):
            _ReviewField.model_validate(malformed_cover)
        assert repository.field_effect_authority_source(automatic)[
            "decision_value"
        ] == {
            "kind": "cover",
            "value": automatic.artifact_id,
        }
        activity = repository.field_effect_activity_page(
            search=None,
            event_type="metadata",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        automatic_event = next(
            event
            for event in activity["events"]
            if event["event_id"] == automatic.decision_id
        )
        assert automatic_event["record"]["selected_value"] == {
            "kind": "cover",
            "value": automatic.artifact_id,
        }
        all_activity = repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        assert [
            event["record"]["source_kind"]
            for event in all_activity["events"]
            if event["event_id"] == automatic.decision_id
        ] == ["automatic_decision"]
        activity_service = ContinuousGuardedApplication(
            repository,
            {},
            {},
            PersistedFieldEffectAuthority(repository),
            (("primary", "ebooks"),),
            lambda: 2_000,
            lambda *_: None,
            activity_only=True,
        )
        readable_activity = activity_service.activity(search=None)["events"]
        automatic_activity = [
            event
            for event in readable_activity
            if event["event_id"] == automatic.decision_id
        ]
        assert len(automatic_activity) == 1
        assert automatic_activity[0]["category"] == "automatic"
        assert automatic_activity[0]["origin"] == "automatic_policy"
        assert not [
            event
            for event in activity_service.activity(search="Library owner")["events"]
            if event["event_id"] == automatic.decision_id
        ]

        authority_adapter = PersistedFieldEffectAuthority(repository)
        snapshot = authority_adapter.resolve(automatic)
        repository.grant_field_effect_consents(
            snapshot.connection,
            (snapshot.library,),
            (snapshot.destination,),
            10,
        )
        target = FakeTargetService()
        field_application = GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            FakeCapabilityObserver("c" * 64),
            authority_adapter,
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            PrivateCoverRecoveryStore(
                root / "recovery", lambda _plan: b"incumbent-original"
            ),
            FakeForwardReevaluation(),
            lambda: 10,
        )
        operation = field_application.plan(
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
            capability_digest="c" * 64,
        )
        assert operation is not None
        if effect_state == "applied":
            target.seed(operation.plan)
            assert field_application.apply(operation.plan.plan_id).state == "verified"
            assert target.read(operation.plan).value == automatic.artifact_id
            supervisor.stop()
            repository.close()
            return
        if effect_state in {"claimed", "applying"}:
            repository.claim_field_effect(
                operation.plan.plan_id, "planned", "a" * 64, 10
            )
        if effect_state == "applying":
            repository.transition_field_effect(
                operation.plan.plan_id, "claimed", "applying", 10
            )

        consideration_document = repository.current_cover_consideration_document(
            automatic.review_case_id
        )
        assert consideration_document is not None
        consideration = json.loads(consideration_document)
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(automatic.review_case_id))["case"]
        )
        cover_proposal = next(
            item for item in case.proposals if item["destination"] == "cover"
        )
        owner = HumanDecision.create(
            case,
            proposal_id=cover_proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination="cover",
            actor="owner",
            outcome="accept",
            selected_value={"kind": "cover", "value": automatic.artifact_id},
            idempotency_key="synthetic-owner-cover-final-0001",
            occurred_at=11,
        )
        if effect_state != "planned":
            with pytest.raises(HumanDecisionError, match="already_decided"):
                repository.record_human_decision(
                    owner, cover_consideration_id=consideration["consideration_id"]
                )
            supervisor.stop()
            repository.close()
            return
        repository.record_human_decision(
            owner, cover_consideration_id=consideration["consideration_id"]
        )
        assert not authority_adapter.current(operation.plan.authority)
        assert repository.field_effect_manual_hold_context(
            automatic.review_case_id, "cover"
        ) == (snapshot.connection, snapshot.library)
        current = repository._connection.execute(
            "SELECT selection.kind FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id"
        ).fetchone()
        assert current == ("owner",)
        assert (
            2
            == repository._connection.execute(
                "SELECT count(*) FROM cover_selection_authorities"
            ).fetchone()[0]
        )
        repository.close()
        repository = open_repository(database)
        reopened_covers = composition._compose_cover_considerations(
            repository,
            root / "artifacts",
            _pipeline_configuration().review,
            _test_reviewer_access(repository),
        )
        reopened_web = InspectCurrentReviewCase(repository, reopened_covers)
        owner_detail = reopened_web.run(automatic.review_case_id)
        owner_cover = next(
            field for field in owner_detail["fields"] if field["destination"] == "cover"
        )
        assert owner_cover["human_decision"]["decision_id"] == owner.decision_id
        assert owner_cover["automatic_decision"] is None
        reopened_activity_service = ContinuousGuardedApplication(
            repository,
            {},
            {},
            PersistedFieldEffectAuthority(repository),
            (("primary", "ebooks"),),
            lambda: 2_000,
            lambda *_: None,
            activity_only=True,
        )
        reopened_activity = reopened_activity_service.activity(search=None)["events"]
        assert [
            (event["category"], event["origin"])
            for event in reopened_activity
            if event["event_id"] == automatic.decision_id
        ] == [("automatic", "automatic_policy")]
        assert [
            event["origin"]
            for event in reopened_activity
            if event["category"] == "owner"
            and event["destination"] == "cover"
            and event["state"] == "selected"
        ] == ["human"]
        history = reopened_web.history(automatic.review_case_id)
        transport_history = _HistoryResponse.model_validate(history).model_dump(
            mode="json"
        )
        automatic_history = next(
            item
            for item in transport_history["items"]
            if type(item.get("decision")) is dict
            and item["decision"].get("decision_id") == automatic.decision_id
        )
        assert automatic_history["decision"] == {
            "candidate_id": automatic.candidate_id,
            "decision_id": automatic.decision_id,
            "destination": "cover",
            "occurred_at": automatic.occurred_at,
            "outcome": "candidate",
            "proposal_id": automatic.proposal_id,
            "review_case_id": automatic.review_case_id,
            "selected_value": None,
            "supersedes_decision_id": None,
            "target_fingerprint": automatic.target_fingerprint,
        }
        malformed_history = json.loads(json.dumps(history))
        malformed_automatic = next(
            item
            for item in malformed_history["items"]
            if type(item.get("decision")) is dict
            and item["decision"].get("decision_id") == automatic.decision_id
        )
        del malformed_automatic["decision"]["supersedes_decision_id"]
        with pytest.raises(ValueError):
            _HistoryResponse.model_validate(malformed_history)
        repository.record_cover_consideration(
            consideration_document,
            review_case_id=automatic.review_case_id,
            report_id=automatic.report_id,
            replay_digest=consideration["replay_digest"],
            supersedes_id=consideration["supersedes_id"],
            occurred_at=12,
        )
        assert (
            2
            == repository._connection.execute(
                "SELECT count(*) FROM cover_selection_authorities"
            ).fetchone()[0]
        )
        pending = repository.pending_field_effect_decision_documents(1)
        assert len(pending) == 1
        assert HumanDecision.from_json(pending[0]).decision_id == owner.decision_id
        for index, outcome in enumerate(("keep_current", "reject"), 2):
            repository.record_human_decision(
                HumanDecision.create(
                    case,
                    proposal_id=cover_proposal["proposal_id"],
                    target_fingerprint=case.target_fingerprint,
                    destination="cover",
                    actor="owner",
                    outcome=outcome,
                    selected_value=None,
                    idempotency_key=f"synthetic-owner-cover-final-000{index}",
                    occurred_at=11 + index,
                ),
                cover_consideration_id=consideration["consideration_id"],
            )
        supervisor.stop()
        repository.close()
        reopened = open_repository(database)
        assert reopened._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (4,)
        reopened.close()
        lineage_database = root / "lineage.sqlite"
        source = sqlite3.connect(database)
        destination = sqlite3.connect(lineage_database)
        source.backup(destination)
        source.close()
        destination.close()
        hostile = sqlite3.connect(lineage_database)
        hostile.execute("DROP TRIGGER cover_selection_authorities_no_update")
        automatic_row = hostile.execute(
            "SELECT selection_id,document FROM cover_selection_authorities "
            "WHERE kind='automatic'"
        ).fetchone()
        owner_row = hostile.execute(
            "SELECT selection_id FROM cover_selection_authorities "
            "WHERE kind='owner' ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
        assert automatic_row is not None
        assert owner_row is not None
        authority_document = json.loads(automatic_row[1])
        authority_document["supersedes_selection_id"] = owner_row[0]
        hostile.execute(
            "UPDATE cover_selection_authorities SET supersedes_selection_id=?,document=? "
            "WHERE selection_id=?",
            (
                owner_row[0],
                json.dumps(authority_document, sort_keys=True, separators=(",", ":")),
                automatic_row[0],
            ),
        )
        hostile.commit()
        hostile.close()
        with pytest.raises(
            MetadataReportError, match="database is unavailable or incompatible"
        ):
            open_repository(lineage_database)
        hostile = sqlite3.connect(database)
        hostile.execute("DROP TRIGGER cover_selection_authorities_no_update")
        hostile.execute(
            "UPDATE cover_selection_authorities SET occurred_at=occurred_at+1 "
            "WHERE kind='automatic'"
        )
        hostile.commit()
        hostile.close()
        with pytest.raises(
            MetadataReportError, match="database is unavailable or incompatible"
        ):
            open_repository(database)


@pytest.mark.parametrize("authority_change", ("policy", "replay"))
def test_new_owner_ready_cover_case_is_attentive_after_automatic_predecessor(
    authority_change: str,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        profile_reader = None
        profile_contract = None
        repository = open_repository(database)
        report, _ = _qualified_cover_report(root)
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
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
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
                advances = 0
                while pipeline.run_next(1):
                    advances += 1
                    assert advances < 10
            dispatch.assert_not_called()
            selected = repository._connection.execute(
                "SELECT selection.document FROM current_cover_selection_authorities current "
                "JOIN cover_selection_authorities selection "
                "ON selection.selection_id=current.selection_id"
            ).fetchone()
            assert selected is not None
            automatic = CoverAutomaticDecision.from_json(
                json.loads(selected[0])["decision_document"]
            )

            candidate_sources = tuple(
                source
                for source in report.cover_sources
                if source.artifact is not None
                and source.artifact.artifact_id == automatic.artifact_id
            )
            assert len(candidate_sources) == 1
            successor_report = MetadataReport.create(
                target=report.target,
                file=FileFacts(1, "c" * 64, "d" * 64),
                local_claims=report.local_claims,
                local_reasons=report.local_reasons,
                providers=report.providers,
                cover_sources=candidate_sources,
            )
            successor_proposal = evaluate_report(
                successor_report,
                resolved.resolved_id,
                human_review_destinations(resolved),
            )
            with patch.object(composition, "run_cover_packet_reviewer") as dispatch:
                pipeline = _run_qualified_cover_pipeline(
                    repository,
                    root,
                    successor_report,
                    successor_proposal,
                    supervisor,
                )
                advances = 0
                while pipeline.run_next(1):
                    advances += 1
                    assert advances < 10
            dispatch.assert_not_called()

            assert repository._connection.execute(
                "SELECT count(*) FROM cover_selection_authorities"
            ).fetchone() == (1,)
            reviewer_access = _test_reviewer_access(repository)
            cover_considerations = composition._compose_cover_considerations(
                repository,
                root / "artifacts",
                _pipeline_configuration().review,
                reviewer_access,
            )
            library = BrowseReviewLibrary(repository).library(
                lens="all", search=None, limit=50, cursor=None
            )
            assert library["counts"]["needs_attention"] == 1
            assert len(library["items"]) == 1
            current_work = library["items"][0]
            assert current_work["review_case_id"] != automatic.review_case_id
            assert current_work["attention_count"] == 1
            attention = BrowseReviewLibrary(repository).attention(
                search=None, limit=50, cursor=None
            )
            assert attention["total"] == 1
            assert len(attention["items"]) == 1
            attention_item = attention["items"][0]
            assert (
                attention_item["review_case_id"],
                attention_item["destination"],
                attention_item["detail_url"],
            ) == (
                current_work["review_case_id"],
                "cover",
                f"/review/{current_work['review_case_id']}?field=cover",
            )
            detail = InspectCurrentReviewCase(repository, cover_considerations).run(
                current_work["review_case_id"]
            )
            cover = next(
                field for field in detail["fields"] if field["destination"] == "cover"
            )
            assert cover["automatic_decision"] is None
            assert cover["cover_consideration"]["ready"] is True
            assert cover["cover_consideration"]["policy"]["status"] == "abstained"
            assert cover["cover_consideration"]["policy"]["reasons"] == [
                "actual_target_unverified"
            ]

            if authority_change == "policy":
                cover_considerations.update_policy(
                    CoverPolicy(avoid_promotional_text=True)
                )
            else:
                profile_reader = patch.object(
                    composition,
                    "read_contained_reviewer_profile",
                    return_value=b"profile-1",
                )
                profile_contract = patch(
                    "tests.unit.test_shadow_outcome_persistence.cover_reviewer_profile_contract_mismatch",
                    return_value=None,
                )
                profile_reader.start()
                profile_contract.start()
                replay_configuration = _pipeline_configuration(
                    cover_reviewer_profile_file="/run/synthetic-profile"
                )
                reviewer_access = _test_reviewer_access(
                    repository, replay_configuration.review
                )
                cover_considerations = composition._compose_cover_considerations(
                    repository,
                    root / "artifacts",
                    replay_configuration.review,
                    reviewer_access,
                )

            stale_library = BrowseReviewLibrary(repository).library(
                lens="all", search="item", limit=1, cursor=None
            )
            assert stale_library["counts"]["needs_attention"] == 0
            assert len(stale_library["items"]) == 1
            assert stale_library["items"][0]["attention_count"] == 0
            assert (
                BrowseReviewLibrary(repository).library(
                    lens="needs_attention", search=None, limit=1, cursor=None
                )["items"]
                == []
            )
            assert (
                len(
                    BrowseReviewLibrary(repository).library(
                        lens="no_attention_needed", search=None, limit=1, cursor=None
                    )["items"]
                )
                == 1
            )
            stale_attention = BrowseReviewLibrary(repository).attention(
                search="item", limit=1, cursor=None
            )
            assert stale_attention["items"] == []
            assert stale_attention["next_cursor"] is None
            assert stale_attention["total"] == 0
            stale_detail = InspectCurrentReviewCase(
                repository, cover_considerations
            ).run(current_work["review_case_id"])
            stale_cover = next(
                field
                for field in stale_detail["fields"]
                if field["destination"] == "cover"
            )
            assert stale_cover["needs_attention"] is False
            assert stale_cover["cover_consideration"] is None
            assert (
                repository.suspend_field_effect_capability(
                    scope="destination",
                    connection=successor_report.target.connection,
                    destination="cover",
                    reason="field_unavailable",
                    changed_at=20,
                )
                == ()
            )
            repository.clear_field_effect_capability_suspension(
                scope="destination",
                connection=successor_report.target.connection,
                destination="cover",
                reason="field_unavailable",
                observed_at=21,
            )

            current_case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(current_work["review_case_id"]))[
                    "case"
                ]
            )
            cover_proposal = next(
                item
                for item in current_case.proposals
                if item["destination"] == "cover"
            )
            refreshed = cover_considerations.ensure(
                current_work["review_case_id"],
                successor_report.to_json(),
                {"proposal_id": cover_proposal["proposal_id"]},
                retry=True,
            )
            assert refreshed["ready"] is True
            assert (
                BrowseReviewLibrary(repository).library(
                    lens="needs_attention", search=None, limit=1, cursor=None
                )["counts"]["needs_attention"]
                == 1
            )
            assert (
                BrowseReviewLibrary(repository).attention(
                    search=None, limit=1, cursor=None
                )["total"]
                == 1
            )
            assert repository.suspend_field_effect_capability(
                scope="destination",
                connection=successor_report.target.connection,
                destination="cover",
                reason="field_unavailable",
                changed_at=22,
            ) == (current_work["review_case_id"],)
        finally:
            supervisor.stop()
            repository.close()
            if profile_contract is not None:
                profile_contract.stop()
            if profile_reader is not None:
                profile_reader.stop()


def test_live_cover_fetching_currentness_drives_attention_without_lock_inversion() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repository = open_repository(root / "reports.sqlite", cross_thread=True)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        report, _qualified = _qualified_cover_report(root)
        proposal = evaluate_report(report)
        repository.enter_shadow_preview(1)
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        review_case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        cover_proposal = next(
            value for value in review_case.proposals if value["destination"] == "cover"
        )
        recovery.enqueue_jobs(
            (
                replace(
                    _provider_recovery_job("target"),
                    target_connection=report.target.connection,
                    library=report.target.library,
                    item=report.target.item,
                    source_report_id=report.report_id,
                    package_report_id=report.report_id,
                ),
            ),
            enqueued_at=3,
        )
        considerations = composition._compose_cover_considerations(
            repository,
            root / "artifacts",
            _pipeline_configuration().review,
            _test_reviewer_access(repository),
            recovery,
        )
        considerations.ensure(case_id, report.to_json(), cover_proposal)

        def suspension_membership(expected: bool) -> None:
            transaction = repository._field_effects._transaction(
                "cover_currentness_probe"
            )
            transaction.begin()
            try:
                changed = repository._field_effects.suspend_field_effect_capability(
                    scope="destination",
                    connection=report.target.connection,
                    destination="cover",
                    reason="field_unavailable",
                    changed_at=15,
                )
                assert (case_id in changed) is expected
            finally:
                transaction.rollback()

        suspension_membership(True)
        claimed = recovery.claim_one(generation=1, now=7203, token="owner")
        assert claimed is not None
        suspension_membership(False)
        considerations.ensure(case_id, report.to_json(), cover_proposal)
        suspension_membership(False)
        recovery.complete(
            token="owner",
            recovery=claimed,
            now=7204,
            failure="transient",
        )
        suspension_membership(False)
        considerations.ensure(case_id, report.to_json(), cover_proposal)
        suspension_membership(True)

        inside_sql = threading.Event()
        setup_locked = threading.Event()
        finished: list[str] = []
        errors: list[BaseException] = []

        def coordinated_provider_fetching(value: MetadataReport) -> bool:
            inside_sql.set()
            if not setup_locked.wait(2):
                raise AssertionError("setup lock was not acquired")
            return recovery.cover_fetching(
                target_connection=value.target.connection,
                library=value.target.library,
                item=value.target.item,
                source_report_id=value.report_id,
            )

        repository.bind_cover_consideration_currentness(
            lambda document, replay_reference: cover_consideration_is_current(
                document,
                replay_reference,
                coordinated_provider_fetching,
            )
        )

        def read_attention() -> None:
            try:
                assert repository.count_current_attention_fields(None) >= 1
                finished.append("read")
            except BaseException as error:
                errors.append(error)

        def read_reviewer_authority() -> None:
            try:
                if not inside_sql.wait(2):
                    raise AssertionError("currentness callback was not entered")
                with repository._setup_serialization_lock:
                    setup_locked.set()
                    repository.reviewer_access_store().snapshot()
                    finished.append("reviewer")
            except BaseException as error:
                errors.append(error)

        reader = threading.Thread(target=read_attention, daemon=True)
        reviewer = threading.Thread(target=read_reviewer_authority, daemon=True)
        reader.start()
        reviewer.start()
        reader.join(2)
        reviewer.join(2)

        assert not reader.is_alive()
        assert not reviewer.is_alive()
        assert errors == []
        assert sorted(finished) == ["read", "reviewer"]
        recovery.close()
        repository.close()


@pytest.mark.parametrize(
    ("later_candidate_index", "owner_final", "expected_kind", "expected_rows"),
    (
        (3, False, "automatic", 2),
        # A later candidate must be better on the technical vector to
        # supersede. Two reports of one item never share a rank key -- its last
        # component is a reference derived from the report id -- so the
        # not-better case has to be a genuinely smaller cover rather than an
        # equal one, whose order would rest on report-id hashing alone.
        (1, False, "automatic", 1),
        (3, True, "owner", 2),
    ),
)
def test_later_cover_case_only_supersedes_automatic_with_a_strictly_better_rank(
    later_candidate_index: int,
    owner_final: bool,
    expected_kind: str,
    expected_rows: int,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        first_report, _ = _qualified_cover_report(root)
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
        resolved = resolve_policy(profile, first_report.target.library)
        first_proposal = evaluate_report(
            first_report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (
                (
                    first_report.target.library,
                    resolved.resolved_id,
                    resolved.canonical_json,
                ),
            ),
            2,
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
        with patch.object(composition, "run_cover_packet_reviewer") as dispatch:
            pipeline = _run_qualified_cover_pipeline(
                repository, root, first_report, first_proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
        dispatch.assert_not_called()
        prior_document = repository._connection.execute(
            "SELECT selection.document FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id"
        ).fetchone()
        assert prior_document is not None
        prior_selection = json.loads(prior_document[0])
        prior = CoverAutomaticDecision.from_json(prior_selection["decision_document"])

        owner_decision_id = None
        if owner_final:
            first_consideration = repository.current_cover_consideration_document(
                prior.review_case_id
            )
            assert first_consideration is not None
            first_case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(prior.review_case_id))["case"]
            )
            cover_proposal = next(
                item for item in first_case.proposals if item["destination"] == "cover"
            )
            owner = HumanDecision.create(
                first_case,
                proposal_id=cover_proposal["proposal_id"],
                target_fingerprint=first_case.target_fingerprint,
                destination="cover",
                actor="owner",
                outcome="accept",
                selected_value={"kind": "cover", "value": prior.artifact_id},
                idempotency_key="synthetic-owner-cover-later-final-0001",
                occurred_at=11,
            )
            repository.record_human_decision(
                owner,
                cover_consideration_id=json.loads(first_consideration)[
                    "consideration_id"
                ],
            )
            owner_decision_id = owner.decision_id

        later_report, _ = _qualified_cover_report(
            root, incumbent_index=2, candidate_index=later_candidate_index
        )
        later_proposal = evaluate_report(
            later_report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish(
            later_report, lambda: True, expected_current=first_report.report_id
        )
        repository.publish_proposal(later_proposal)
        admission = json.loads(repository.admit_review_case(later_proposal))
        later_case_id = admission["review_case_id"]
        later_case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(later_case_id))["case"]
        )
        later_cover_proposal = next(
            item for item in later_case.proposals if item["destination"] == "cover"
        )
        considerations = composition._compose_cover_considerations(
            repository,
            root / "artifacts",
            _pipeline_configuration().review,
            _test_reviewer_access(repository),
        )
        considerations.ensure(
            later_case_id,
            later_report.to_json(),
            {"proposal_id": later_cover_proposal["proposal_id"]},
        )

        current_document = repository._connection.execute(
            "SELECT selection.document FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id"
        ).fetchone()
        assert current_document is not None
        current = json.loads(current_document[0])
        assert current["kind"] == expected_kind
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (expected_rows,)
        pending = repository.pending_field_effect_decision_documents(1)

        if owner_final:
            assert pending == (current["decision_document"],)
            assert current["decision_id"] == owner_decision_id
        elif later_candidate_index == 3:
            assert pending == (current["decision_document"],)
            successor = CoverAutomaticDecision.from_json(current["decision_document"])
            assert successor.review_case_id == later_case_id
            assert successor.supersedes_decision_id == prior.decision_id
            assert tuple(current["rank_key"]) < prior.rank_key
            assert current["supersedes_selection_id"] == prior_selection["selection_id"]
            automatic_history = next(
                item
                for item in InspectCurrentReviewCase(
                    repository, considerations
                ).history(later_case_id)["items"]
                if type(item.get("decision")) is dict
                and item["decision"].get("decision_id") == successor.decision_id
            )
            assert automatic_history["decision"]["supersedes_decision_id"] == (
                prior.decision_id
            )
            with pytest.raises(FieldEffectError, match="authority_binding"):
                repository.field_effect_authority_source(prior)
        else:
            assert pending == ()
            assert current["decision_id"] == prior.decision_id

        supervisor.stop()
        repository.close()
        if later_candidate_index == 3 and not owner_final:
            reopened = open_repository(database)
            reopened_document = reopened._connection.execute(
                "SELECT selection.document FROM current_cover_selection_authorities current "
                "JOIN cover_selection_authorities selection "
                "ON selection.selection_id=current.selection_id"
            ).fetchone()
            assert reopened_document is not None
            reopened_selection = json.loads(reopened_document[0])
            reopened_successor = CoverAutomaticDecision.from_json(
                reopened_selection["decision_document"]
            )
            assert reopened_successor.supersedes_decision_id == prior.decision_id
            assert (
                reopened_selection["supersedes_selection_id"]
                == prior_selection["selection_id"]
            )
            reopened.close()


def test_cover_automatic_rank_comparison_is_complete_and_reference_stable() -> None:
    earlier_reference = [0, 0, -960000, -800, -1200, 4, "cover_ref_a"]
    later_reference = [0, 0, -960000, -800, -1200, 4, "cover_ref_b"]
    assert strictly_better_cover_rank(earlier_reference, later_reference)
    assert not strictly_better_cover_rank(later_reference, earlier_reference)
    assert not strictly_better_cover_rank(earlier_reference, earlier_reference)
    assert not strictly_better_cover_rank(earlier_reference[:-1], later_reference)


def test_cover_automatic_admission_abstains_before_unready_authority() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository._cover_considerations._admit_automatic_cover(
            json.dumps(
                {
                    "candidates": [],
                    "policy_report_document": json.dumps(
                        {
                            "actual_target_state": "known_empty",
                            "owner_ready": True,
                            "recommended_candidate_reference": "cover_ref_prior",
                            "schema_version": "cover-policy-report-v1",
                            "status": "recommended",
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    "provider_fetching": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            1,
        )
        repository._cover_considerations._admit_automatic_cover(
            json.dumps(
                {
                    "provider_fetching": True,
                    "policy_report_document": "not-json",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            1,
        )
        repository._cover_considerations._admit_automatic_cover(
            json.dumps(
                {
                    "provider_fetching": False,
                    "policy_report_document": "",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            1,
        )
        repository._cover_considerations._admit_automatic_cover(
            json.dumps(
                {
                    "candidates": [
                        {
                            "deterministic_citations": [],
                            "deterministic_rank": None,
                            "deterministic_rank_key": None,
                            "incumbent": False,
                            "reference": "cover_ref_unconfirmed",
                            "selectable": False,
                        }
                    ],
                    "policy_report_document": json.dumps(
                        {
                            "recommended_candidate_reference": "cover_ref_unconfirmed",
                            "status": "recommended",
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    "provider_fetching": False,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            1,
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_selection_authorities"
        ).fetchone() == (0,)
        repository.close()


def test_composed_projection_reports_selected_profile_refusal_before_dispatch() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        profile = root / "profile.json"
        profile.write_text(
            json.dumps(
                {
                    "auth_store_path": str(root / "auth"),
                    "codex": {
                        "path": str(root / "codex"),
                        "sha256": "a" * 64,
                        "version": "1",
                    },
                    "job_root_path": str(root / "jobs"),
                    "limits": {
                        "file_bytes": 131_072,
                        "memory_bytes": 536_870_912,
                        "output_bytes": 65_536,
                        "processes": 32,
                        "timeout_seconds": 30,
                    },
                    "model": COVER_MODEL,
                    "reasoning": "medium",
                    "sandbox": {
                        "path": str(root / "sandbox"),
                        "sha256": "b" * 64,
                        "version": "1",
                    },
                    "schema_version": "contained-reviewer-profile-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        profile.chmod(0o600)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(cover_reviewer_profile_file=str(profile)),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            _test_reviewer_access(
                repository,
                _pipeline_configuration(
                    cover_reviewer_profile_file=str(profile)
                ).review,
            ),
            database_authority=repository.authority,
            provider_recovery=recovery,
        )
        with patch.object(composition, "run_cover_packet_reviewer") as dispatch:
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        document = repository.current_cover_consideration_document(case_id)
        assert document is not None
        value = json.loads(document)
        assert value["reviewer_status"] == "unavailable"
        assert value["reviewer_failure"] == {
            "attempts": [],
            "code": "credential_unavailable",
        }
        projected = composition._compose_cover_considerations(
            repository,
            root / "artifacts",
            _pipeline_configuration(cover_reviewer_profile_file=str(profile)).review,
            _test_reviewer_access(
                repository,
                _pipeline_configuration(
                    cover_reviewer_profile_file=str(profile)
                ).review,
            ),
        ).current(case_id)
        assert projected is not None
        assert projected["reviewer_status"] == "unavailable"
        dispatch.assert_not_called()
        supervisor.stop()
        repository.close()


@pytest.mark.parametrize(
    "reviewer",
    (
        pytest.param(lambda *_: '{"status":"refused"}', id="refusal"),
        pytest.param(lambda *_: "not-json", id="malformed"),
        pytest.param(
            lambda *_: (_ for _ in ()).throw(RuntimeError("contained")),
            id="exception",
        ),
    ),
)
def test_composed_projection_contains_reviewer_failures_before_f22(reviewer) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        calls: list[object] = []

        def dispatch(*args):
            calls.append(args)
            return reviewer(*args)

        with (
            patch.object(
                composition, "read_contained_reviewer_profile", return_value=b"profile"
            ),
            patch(
                "tests.unit.test_shadow_outcome_persistence.cover_reviewer_profile_contract_mismatch",
                return_value=None,
            ),
            patch.object(
                composition, "run_cover_packet_reviewer", side_effect=dispatch
            ),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(cover_reviewer_profile_file="/run/profile"),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _test_reviewer_access(
                    repository,
                    _pipeline_configuration(
                        cover_reviewer_profile_file="/run/profile"
                    ).review,
                ),
                database_authority=repository.authority,
                provider_recovery=recovery,
            )
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        document = repository.current_cover_consideration_document(case_id)
        assert document is not None
        assert "failed" == json.loads(document)["reviewer_status"]
        assert 1 == len(calls)
        with (
            patch.object(
                composition,
                "read_contained_reviewer_profile",
                return_value=b"profile",
            ),
            patch(
                "tests.unit.test_shadow_outcome_persistence.cover_reviewer_profile_contract_mismatch",
                return_value=None,
            ),
        ):
            considerations = composition._compose_cover_considerations(
                repository,
                root / "artifacts",
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/profile"
                ).review,
                _test_reviewer_access(
                    repository,
                    _pipeline_configuration(
                        cover_reviewer_profile_file="/run/profile"
                    ).review,
                ),
            )
            _assert_manual_cover_surface(repository, case_id, considerations, "failed")
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM provider_recovery_outbox"
        ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_stale_after_observation_does_not_publish() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )

        def drift(*_args):
            repository._connection.execute(
                "UPDATE metadata_policy_state SET generation=generation+1 WHERE singleton=1"
            )
            repository._connection.commit()
            return _cover_observer_result(*_args)

        with (
            patch.object(
                composition, "read_contained_reviewer_profile", return_value=b"profile"
            ),
            patch(
                "tests.unit.test_shadow_outcome_persistence.cover_reviewer_profile_contract_mismatch",
                return_value=None,
            ),
            patch.object(composition, "run_cover_packet_reviewer", side_effect=drift),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(cover_reviewer_profile_file="/run/profile"),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _test_reviewer_access(
                    repository,
                    _pipeline_configuration(
                        cover_reviewer_profile_file="/run/profile"
                    ).review,
                ),
                database_authority=repository.authority,
                provider_recovery=recovery,
            )
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True] == [pipeline.run_next(1) for _ in range(4)]
            assert pipeline.run_next(1)
        assert ("queued", "stale") == repository._connection.execute(
            "SELECT state,failure_code FROM review_preparation_work "
            "WHERE stage='projection'"
        ).fetchone()
        for table in (
            "cover_considerations",
            "current_cover_considerations",
            "current_review_packages",
            "provider_recovery_outbox",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_cover_persistence_failure_does_not_publish() -> None:
    """A store fault in the cover stage is durably readable as one (#734)."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
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
        pipeline = _run_qualified_cover_pipeline(
            repository, root, report, proposal, supervisor
        )
        assert [True, True, True, True] == [pipeline.run_next(1) for _ in range(4)]
        with patch.object(
            type(repository),
            "record_cover_consideration",
            side_effect=MetadataReportError("persistence"),
        ):
            assert pipeline.run_next(1)
        assert ("failed", "persistence", None) == repository._connection.execute(
            "SELECT state,failure_code,failure_root FROM review_preparation_work "
            "WHERE stage='projection'"
        ).fetchone()
        for table in (
            "cover_considerations",
            "current_cover_considerations",
            "current_review_packages",
            "provider_recovery_outbox",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_records_a_broken_factory_without_cover_effects() -> None:
    """A broken consideration factory fails the stage and leaves nothing behind.

    The factory `create_review_pipeline` binds is
    `_compose_cover_considerations`, which writes through the repository
    twice while it builds the service -- `bind_current_cover_policy` before
    construction and `bind_current_cover_replay` after it. A fault it raises
    is therefore not separable from the rest of the cover stage, and the
    stage answers `persistence` for it like the rest of its body. That code
    is now in `review_preparation_work.failure_code`'s allow-list, so the
    durable record names the side that failed instead of the anonymous
    `failed` every unlisted code normalised into, and this read is what
    proves it survives the shipped adapter's normalisation and its CHECK
    (#698, #734). No consideration, package, or outbox row survives the
    failure either way.
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
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
        pipeline = _run_qualified_cover_pipeline(
            repository, root, report, proposal, supervisor
        )
        assert [True, True, True, True] == [pipeline.run_next(1) for _ in range(4)]
        with patch.object(
            composition,
            "_compose_cover_considerations",
            side_effect=TypeError("consideration service is broken"),
        ):
            assert pipeline.run_next(1)
        assert ("failed", "persistence", None) == repository._connection.execute(
            "SELECT state,failure_code,failure_root FROM review_preparation_work "
            "WHERE stage='projection'"
        ).fetchone()
        for table in (
            "cover_considerations",
            "current_cover_considerations",
            "current_review_packages",
            "provider_recovery_outbox",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_cover_replay_reuses_exact_binding_and_profile_change_supersedes_once() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        calls: list[object] = []
        profile_payload = [b"profile-1"]

        def reviewer(*args):
            calls.append(args)
            return _cover_observer_result(*args)

        with (
            patch.object(
                composition,
                "read_contained_reviewer_profile",
                side_effect=lambda _path: profile_payload[0],
            ),
            patch(
                "tests.unit.test_shadow_outcome_persistence.cover_reviewer_profile_contract_mismatch",
                return_value=None,
            ),
            patch.object(
                composition, "run_cover_packet_reviewer", side_effect=reviewer
            ),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(cover_reviewer_profile_file="/run/profile-1"),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                _test_reviewer_access(
                    repository,
                    _pipeline_configuration(
                        cover_reviewer_profile_file="/run/profile-1"
                    ).review,
                ),
                database_authority=repository.authority,
                provider_recovery=recovery,
            )
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
            case_id = repository._connection.execute(
                "SELECT review_case_id FROM current_review_packages"
            ).fetchone()[0]
            first = repository.current_cover_consideration_document(case_id)
            cover_proposal_id = json.loads(first)["proposal_id"]
            replay = composition._compose_cover_considerations(
                repository,
                root / "artifacts",
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/profile-1"
                ).review,
                _test_reviewer_access(
                    repository,
                    _pipeline_configuration(
                        cover_reviewer_profile_file="/run/profile-1"
                    ).review,
                ),
            ).ensure(case_id, report.to_json(), {"proposal_id": cover_proposal_id})
            profile_payload[0] = b"profile-2"
            successor = composition._compose_cover_considerations(
                repository,
                root / "artifacts",
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/profile-2"
                ).review,
                _test_reviewer_access(
                    repository,
                    _pipeline_configuration(
                        cover_reviewer_profile_file="/run/profile-2"
                    ).review,
                ),
            ).ensure(case_id, report.to_json(), {"proposal_id": cover_proposal_id})
        assert 2 == len(calls)
        assert json.loads(first)["consideration_id"] == replay["consideration_id"]
        assert json.loads(first)["consideration_id"] == successor["supersedes_id"]
        assert (2,) == repository._connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_without_cover_skips_advice_and_completes_f22() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report = _report("No cover proposal")
        proposal = evaluate_report(report)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(cover_reviewer_profile_file="/run/profile"),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            _test_reviewer_access(repository),
            database_authority=repository.authority,
            provider_recovery=recovery,
        )

        def extract(bound, action, claim_next):
            bound.publish(
                report,
                lambda: True,
                after_publication=lambda current, published: (
                    current.complete_review_extraction_publication(
                        action,
                        published.report_id,
                        10,
                        0,
                        None,
                        claim_next,
                        None,
                    )
                ),
            )
            return report.report_id, True

        def project(bound, action):
            assert (
                bound.current_cover_consideration_document(action.review_case_id or "")
                is None
            )
            outcome = bound.persist_shadow_outcome(
                action.review_case_id or "",
                10,
                staged_action=action,
            )
            return "stale" if outcome == "stale" else "complete"

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(
                database, authority=repository.authority
            ),
            libraries=(("primary", "ebooks", "lib-1", "f" * 64),),
            discover=lambda *_: (
                1,
                report.file.content_digest,
                (
                    CatalogueEntry(
                        "primary",
                        "ebooks",
                        "lib-1",
                        "item-1",
                        1,
                        "No cover proposal",
                        (),
                        True,
                        False,
                        "singular-supported",
                        report.file.identity,
                        "/item-1.epub",
                        "epub",
                        "books",
                    ),
                ),
            ),
            extract=extract,
            providers=lambda *_: (),
            propose=lambda bound, _: (
                bound.publish_proposal(proposal),
                proposal.proposal_set_id,
            )[1],
            admit=lambda bound, _: (
                lambda value: (value["status"], value.get("review_case_id"))
            )(json.loads(bound.admit_review_case(proposal))),
            project=project,
            before_projection=supervisor._pipeline._before_projection,
            after_advance=supervisor._pipeline._after_advance,
            clock=lambda: 10,
        )
        pipeline.request("primary", "ebooks")
        assert [True, True, True, True, True, False] == [
            pipeline.run_next(1) for _ in range(6)
        ]
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM current_review_packages"
        ).fetchone()
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM provider_recovery_outbox"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone()
        assert not pipeline.run_next(1)
        supervisor.stop()
        repository.close()


def test_review_posture_composed_stale_projection_restarts_instead_of_completing() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        provider_recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        old_case_id = _case(repository)
        old_case = json.loads(repository.get_review_case(old_case_id))["case"]
        work_id = _running_projection(repository, old_case_id)
        current_report = _report("Current title")
        repository.publish(current_report, lambda: True, old_case["report_id"])
        current_proposal = evaluate_report(current_report)
        repository.publish_proposal(current_proposal)
        repository.admit_review_case(current_proposal)
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            _test_reviewer_access(repository),
            database_authority=repository.authority,
            provider_recovery=provider_recovery,
        )

        assert "automatic" == repository.shadow_posture()
        assert "stale" == supervisor._pipeline._project(repository, old_case_id)
        assert "stale" == supervisor._pipeline._project(repository, old_case_id)
        assert (
            "proposal",
            "queued",
            current_report.report_id,
            None,
            None,
        ) == repository._connection.execute(
            "SELECT stage,state,report_id,proposal_set_id,review_case_id "
            "FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert () == repository.list_shadow_outcomes(50)
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_work"
        ).fetchone()
        supervisor.stop()
        repository.close()


def test_automatic_bootstrap_and_preview_replay_one_exact_set() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        case_id = _case(repository)

        assert "automatic" == repository.shadow_posture()
        bootstrap = repository.metadata_policy_transition()
        assert bootstrap is not None
        assert bootstrap is not None
        assert not bootstrap.acknowledged
        assert not hasattr(repository, "record_automatic_decision")
        automatic = repository.persist_shadow_outcome(case_id, 10)
        assert automatic.startswith('{"authority":"none"')
        assert ((automatic, 10),) == repository.list_shadow_outcomes(50)

        assert "preview" == repository.enter_shadow_preview(11)
        first = repository.persist_shadow_outcome(case_id, 12)
        second = repository.persist_shadow_outcome(case_id, 13)
        assert first == second
        outcome = ShadowOutcomeSet.from_json(first)
        assert "none" == outcome.authority
        assert "preview" == outcome.posture
        assert case_id == outcome.review_case_id
        assert {value.disposition for value in outcome.outcomes}.issubset(
            {
                "candidate",
                "keep_current",
                "abstention",
                "conflict",
                "manual",
                "unsupported",
            }
        )
        assert all(
            value.qualification_candidate_id.startswith("qualification_candidate_")
            for value in outcome.outcomes
        )
        assert len(outcome.outcomes) == len(
            {value.qualification_candidate_id for value in outcome.outcomes}
        )
        assert ((first, 10),) == repository.list_shadow_outcomes(50)
        history = repository.get_review_case_history(case_id, 50)
        assert "shadow_preview_recorded" == history[0]["kind"]
        assert "none" == history[0]["authority"]
        repository.close()

        connection = sqlite3.connect(path)
        assert (1,) == connection.execute(
            "SELECT COUNT(*) FROM shadow_decision_outcomes"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE shadow_decision_outcomes SET evaluated_at=99")
        connection.close()


def test_preview_reconciliation_failure_rolls_back_the_posture_and_automatic_rows() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
        )
        with patch.object(
            repository,
            "activate_metadata_policy",
            side_effect=MetadataReportError("reconciliation failed"),
        ):
            with pytest.raises(MetadataReportError, match="reconciliation failed"):
                repository.enter_shadow_preview(3)
        assert "automatic" == repository.shadow_posture()
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        repository.close()


def test_automatic_safety_update_preserves_a_caller_transaction_and_restart() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        repository._connection.execute("BEGIN IMMEDIATE")
        repository.set_automatic_safety_state(
            AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),)),
            9,
        )
        assert repository._connection.in_transaction
        repository._connection.commit()
        repository.close()
        reopened = open_repository(path)
        assert (
            AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),))
            == reopened.automatic_safety_state()
        )
        reopened.close()


def test_automatic_authority_binding_failure_is_global_and_keeps_shadow_projection() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        case_id = _case(repository)
        # The construction seam reports malformed manifest/source/identity
        # bindings as an authority error. It must fail closed globally,
        # while the report-only observation survives the savepoint rollback.
        with patch.object(
            repository,
            "_record_automatic_outcomes",
            side_effect=AutomaticDecisionError("source_binding_invalid"),
        ):
            persisted = repository.persist_shadow_outcome(case_id, 9)
        assert persisted.startswith('{"authority":"none"')
        assert (
            "automatic_authority_failed"
            == repository.automatic_safety_state().global_suspension
        )
        assert () == repository.automatic_safety_state().category_suspensions
        assert ((persisted, 9),) == repository.list_shadow_outcomes(50)
        repository.close()
        reopened = open_repository(path)
        assert (
            "automatic_authority_failed"
            == reopened.automatic_safety_state().global_suspension
        )
        reopened.close()


def test_explicit_bundle_safety_failure_is_category_scoped() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        # Category is reserved for the bundle-coherence classifier; it
        # must not become the fallback for authority-binding failures.
        with patch.object(
            repository,
            "_record_automatic_outcomes",
            side_effect=AutomaticDecisionError("category:title_layout"),
        ) as admission:
            repository.persist_shadow_outcome(case_id, 10)
        assert 2 == admission.call_count
        safety = repository.automatic_safety_state()
        assert safety.global_suspension is None
        assert (
            ("title_layout", "automatic_admission_failed"),
        ) == safety.category_suspensions
        repository.close()


def test_shadow_history_orders_human_and_preview_events_by_time() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        repository.persist_shadow_outcome(case_id, 5)
        # A second deterministic set is a replay, so it cannot manufacture a
        # newer chronology merely because delivery repeats.
        repository.persist_shadow_outcome(case_id, 50)
        events = repository.get_review_case_history(case_id, 50)
        shadow = [
            value for value in events if value["kind"] == "shadow_preview_recorded"
        ]
        assert [5] == [value["occurred_at"] for value in shadow]
        repository.close()


def test_history_orders_same_timestamp_human_and_cross_kind_events_stably() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        report = _bibliographic_report(
            current_title="Secret History",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Secret History: Mistborn",),
                    subtitle="Mistborn novella",
                    series="Mistborn",
                ),
            ),
        )
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposals = {
            value["destination"]: value
            for value in case.proposals
            if decision_actions(value)
        }
        assert {"title", "subtitle"}.issubset(proposals)
        decisions = []
        for index, destination in enumerate(("title", "subtitle"), 1):
            proposal = proposals[destination]
            alternative = next(
                value
                for value in proposal["result"]["alternatives"]
                if value["assessment"] != "preserves_target"
            )
            decision = HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="accept",
                selected_value=alternative["value"],
                idempotency_key=f"same-timestamp-history-{index:04d}",
                occurred_at=10,
            )
            repository.record_human_decision(decision)
            decisions.append(decision)
        first = repository.get_review_case_history(case_id, 10)
        second = repository.get_review_case_history(case_id, 10)
        assert first == second
        assert [event["decision_id"] for event in first[:2]] == [
            decisions[1].decision_id,
            decisions[0].decision_id,
        ]
        repository.close()

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.enter_shadow_preview(9)
        repository.persist_shadow_outcome(case_id, 10)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 10
        )
        first = repository.get_review_case_history(case_id, 10)
        second = repository.get_review_case_history(case_id, 10)
        assert first == second
        assert {
            "automatic_decision_recorded",
            "shadow_preview_recorded",
        }.issubset({event["kind"] for event in first})
        assert [event["kind"] for event in first[:2]] == [
            "shadow_preview_recorded",
            "automatic_decision_recorded",
        ]
        repository.close()


def test_sample_bounds_limit_fifty_at_maximum_reference_cardinality() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        outcome = _maximum_reference_outcome(repository)
        repository.close()

    class SampleRepository:
        def list_shadow_outcomes(self, limit):
            return tuple((outcome.to_json(), index) for index in range(limit))

    sample = PolicyNotebook(SampleRepository(), (), {}).shadow_sample(50)
    serialized = json.dumps(
        sample, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert len(sample["items"]) > 0
    assert len(sample["items"]) < 50
    assert len(serialized) <= MAX_SHADOW_SAMPLE_RESPONSE_BYTES
    first = sample["items"][0]["outcomes"][0]
    assert 256 == len(first["citation_ids"])
    assert 256 == len(first["combination_ids"])


def test_canonical_hostile_shadow_row_fails_closed_without_exposure() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        persisted = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 2)
        ).to_data()
        field = dict(persisted["outcomes"][0])
        field.update(
            {
                "destination": "/private/owner/library",
                "policy_category": None,
                "policy_destination": "unsupported",
                "policy_option": None,
                "disposition": "unsupported",
            }
        )
        field["rule_id"] = "shadow_rule_" + _digest(
            {
                "category": None,
                "decision_policy_sha256": persisted["decision_policy_sha256"],
                "decision_policy_version": persisted["decision_policy_version"],
                "destination": "unsupported",
                "option": None,
            }
        )
        field["qualification_candidate_id"] = "qualification_candidate_" + _digest(
            {
                "action_id": field["action_id"],
                "candidate_id": field["candidate_id"],
                "catalogue_sha256": persisted["catalogue_sha256"],
                "citation_ids": field["citation_ids"],
                "combination_ids": field["combination_ids"],
                "rule_id": field["rule_id"],
            }
        )
        field_identity = dict(field)
        del field_identity["outcome_id"]
        field["outcome_id"] = "shadow_outcome_" + _digest(field_identity)
        persisted["outcomes"][0] = field
        outcome_identity = dict(persisted)
        del outcome_identity["outcome_set_id"]
        persisted["outcome_set_id"] = "shadow_outcome_set_" + _digest(outcome_identity)
        hostile = json.dumps(
            persisted,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        repository._connection.execute(
            "DROP TRIGGER shadow_decision_outcomes_no_update"
        )
        repository._connection.execute(
            "UPDATE shadow_decision_outcomes SET document=?",
            (hostile,),
        )
        repository._connection.commit()

        with pytest.raises(
            Exception, match="shadow sample is unavailable"
        ) as sample_error:
            PolicyNotebook(repository, (), {}).shadow_sample(50)
        with pytest.raises(
            Exception, match="review history is unavailable"
        ) as history_error:
            repository.get_review_case_history(case_id, 50)
        assert "/private/owner/library" not in str(sample_error.value)
        assert "/private/owner/library" not in str(history_error.value)
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM shadow_decision_outcomes"
        ).fetchone()
        repository.close()


def test_report_only_currentness_race_restarts_the_same_projection_row() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        old_case_id = _case(repository)
        old_case = json.loads(repository.get_review_case(old_case_id))["case"]
        work_id = _running_projection(repository, old_case_id)
        current_report = _report("Current title")
        repository.publish(current_report, lambda: True, old_case["report_id"])
        connection = repository._connection
        repository.enter_shadow_preview(2)

        assert (old_case["report_id"], old_case_id) == connection.execute(
            "SELECT report_id,review_case_id FROM current_review_case_lineages"
        ).fetchone()
        assert "stale" == repository.persist_shadow_outcome(old_case_id, 3)
        assert "stale" == repository.persist_shadow_outcome(old_case_id, 4)
        assert () == repository.list_shadow_outcomes(50)
        assert (
            "proposal",
            "queued",
            current_report.report_id,
            None,
            None,
        ) == connection.execute(
            "SELECT stage,state,report_id,proposal_set_id,review_case_id FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert (1,) == connection.execute(
            "SELECT COUNT(*) FROM review_preparation_work"
        ).fetchone()
        repository.close()


def test_old_claim_cannot_project_same_ids_at_newer_lineage_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        work_id = _running_projection(repository, case_id)
        action = _staged_projection_action(repository, work_id)
        pointer = repository._connection.execute(
            "SELECT proposal_set_id,report_id,transition_id,checkpoint FROM "
            "current_review_case_lineages WHERE connection_name='primary' "
            "AND library_name='ebooks' AND item_id='item-1'"
        ).fetchone()
        assert pointer is not None
        successor = ReviewTransition(
            "primary",
            "ebooks",
            "item-1",
            pointer[3] + 1,
            pointer[0],
            pointer[1],
            "superseded",
            case_id,
            case_id,
            "actionable",
        )
        repository._connection.execute(
            "INSERT INTO review_case_transitions(transition_id,connection_name,"
            "library_name,item_id,checkpoint,actor,from_review_case_id,"
            "to_review_case_id,proposal_set_id,report_id,kind,reason) "
            "VALUES(?,?,?,?,?,'review-admission-v1',?,?,?,?,?,'actionable')",
            (
                successor.transition_id,
                "primary",
                "ebooks",
                "item-1",
                pointer[3] + 1,
                case_id,
                case_id,
                pointer[0],
                pointer[1],
                "superseded",
            ),
        )
        repository._connection.execute(
            "UPDATE current_review_case_lineages SET transition_id=?,checkpoint=?,outcome='superseded' "
            "WHERE connection_name='primary' AND library_name='ebooks' AND item_id='item-1'",
            (successor.transition_id, pointer[3] + 1),
        )
        repository._connection.execute(
            "UPDATE current_review_packages SET checkpoint=?,outcome='superseded' "
            "WHERE connection_name='primary' AND library_name='ebooks' AND item_id='item-1'",
            (pointer[3] + 1,),
        )
        repository._connection.execute(
            "DELETE FROM current_review_packages WHERE connection_name='primary' "
            "AND library_name='ebooks' AND item_id='item-1'"
        )
        repository._connection.commit()
        repository.enter_shadow_preview(2)

        # The old claim is tied to generation one.  Reconcile the active
        # policy before the package exists, so an old projection cannot
        # create a generation-two observation or automatic decision.
        profile = _preview_automatic_profile()
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, "ebooks")
        transition = repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (("ebooks", resolved.resolved_id, resolved.canonical_json),),
            3,
            reconcile_current=True,
        )
        assert transition is not None
        assert 2 == transition.generation

        assert "stale" == repository.persist_shadow_outcome(
            case_id, 4, staged_action=action
        )
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM shadow_decision_outcomes"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        with pytest.raises(
            MetadataReportError, match="review preparation state changed"
        ):
            repository.advance_review_pipeline_action(action, None, 5)
        assert ("projection", "running") == repository._connection.execute(
            "SELECT stage,state FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM current_review_packages"
        ).fetchone()
        repository.fail_review_pipeline_action(action, "stale", True, 5)
        assert ("queued", "stale") == repository._connection.execute(
            "SELECT state,failure_code FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_policy_transition_items"
        ).fetchone()
        repository.close()


def test_stale_recovery_currentness_failure_rolls_back_owned_transaction() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        old_case_id = _case(repository)
        old_case = json.loads(repository.get_review_case(old_case_id))["case"]
        work_id = _running_projection(repository, old_case_id)
        current_report = _report("Current title")
        repository.publish(current_report, lambda: True, old_case["report_id"])
        repository._connection.execute(
            "UPDATE current_metadata_reports SET report_id=?",
            (old_case["report_id"],),
        )
        repository._connection.commit()

        with pytest.raises(
            Exception, match="shadow outcome currentness is unavailable"
        ):
            repository.persist_shadow_outcome(old_case_id, 3)
        assert not repository._connection.in_transaction
        assert (
            "projection",
            "running",
            old_case["report_id"],
            old_case["proposal_set_id"],
            old_case_id,
        ) == repository._connection.execute(
            "SELECT stage,state,report_id,proposal_set_id,review_case_id "
            "FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        repository._connection.execute("BEGIN IMMEDIATE")
        repository._connection.rollback()
        repository.close()


def test_human_authority_supersedes_automatic_without_global_suspension() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.record_human_decision(_human_title_decision(repository, case_id, 1))
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
        )
        assert (1, 0) == tuple(
            repository._connection.execute(
                "SELECT (SELECT COUNT(*) FROM human_decisions),"
                "(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone()
        )
        assert (
            AutomaticSafetyState(False, None, ()) == repository.automatic_safety_state()
        )
        repository.close()

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
        )
        repository.record_human_decision(_human_title_decision(repository, case_id, 3))
        assert (1, 1) == tuple(
            repository._connection.execute(
                "SELECT (SELECT COUNT(*) FROM human_decisions),(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone()
        )
        assert ("primary", "ebooks") == repository.field_effect_manual_hold_context(
            case_id, "title"
        )
        assert (
            AutomaticSafetyState(False, None, ()) == repository.automatic_safety_state()
        )
        repository.close()


def test_history_uses_a_verified_automatic_predecessor_for_a_human_override() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
        )
        automatic = next(
            AutomaticDecision.from_json(document)
            for document in repository.get_review_case_decisions(case_id)
            if AutomaticDecision.from_json(document).destination == "title"
        )
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        current = next(
            field["value"]
            for field in case.target["fields"]
            if field["destination"] == "title" and field["state"] == "value"
        )
        automatic_value = "Automatic title"
        plan = FieldEffectPlan.create(
            connection=case.target["connection"],
            library=case.target["library"],
            work_id=case.target["item"],
            destination="title",
            pre_value=current,
            desired_value=automatic_value,
            target_fingerprint=case.target_fingerprint,
            target_version=field_value_version("title", current),
            dependency_digests=(case.target_fingerprint,),
            authority=EffectAuthority(
                automatic.decision_id,
                automatic.review_case_id,
                automatic.target_fingerprint,
                "persisted_review_case",
                "automatic_policy",
                "candidate",
                automatic.resolved_policy_id,
                automatic.qualification_manifest_sha256,
            ),
            consent=EffectConsent.create(
                connection=case.target["connection"],
                library=case.target["library"],
                destination="title",
                envelope_identity=repository.field_effect_consent_envelope_identity(
                    case.target["library"]
                ),
                granted_at=3,
            ),
            capability_digest="d" * 64,
            created_at=3,
        )
        repository.record_field_effect(FieldEffectOperation(plan, updated_at=3))
        repository.claim_field_effect(plan.plan_id, "planned", "a" * 64, 4)
        repository.transition_field_effect(plan.plan_id, "claimed", "applying", 5)
        repository.transition_field_effect(plan.plan_id, "applying", "verified", 6)

        human = _human_title_decision(repository, case_id, 7)
        repository.record_human_decision(human)
        history = repository.get_review_case_history(case_id, 10)
        override = next(
            event
            for event in history
            if event.get("kind") == "human_decision_recorded"
            and event.get("decision_id") == human.decision_id
        )
        assert override["previous_value"] == {
            "kind": "text",
            "value": automatic_value,
        }
        repository.close()


def test_automatic_tuple_replay_is_exact_and_date_and_title_bundles_are_atomic() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        case_id = _case(repository)
        title = _synthetic_automatic_tuple(repository, case_id, "title_layout")
        dates = _synthetic_automatic_tuple(repository, case_id, "date_precision")
        repository._record_automatic_outcomes(title, 7)
        first = repository.get_review_case_decisions(case_id)
        repository._record_automatic_outcomes(title, 99)
        repository._record_automatic_outcomes(dates, 8)
        documents = tuple(
            json.loads(value) for value in repository.get_review_case_decisions(case_id)
        )
        assert first == repository.get_review_case_decisions(case_id)[:1]
        assert 2 == len(documents)
        assert "publishedYear" not in {value["destination"] for value in documents}
        for destinations in (("title",), ("publishedDate",)):
            bundle = [
                value for value in documents if value["destination"] in destinations
            ]
            assert set(destinations) == {value["destination"] for value in bundle}
            assert 1 == len({value["bundle_id"] for value in bundle})
        repository.close()
        reopened = open_repository(path)
        assert tuple(value["decision_id"] for value in documents) == tuple(
            json.loads(value)["decision_id"]
            for value in reopened.get_review_case_decisions(case_id)
        )
        reopened.close()


def test_incoherent_automatic_tuple_suspends_only_its_category_and_safety_recovery_restores_admission() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        incoherent = _synthetic_automatic_tuple(repository, case_id, "title_layout")
        incoherent.outcomes[0].combination_ids = ()
        with pytest.raises(AutomaticDecisionError, match="category:title_layout"):
            repository._record_automatic_outcomes(incoherent, 3)
        repository.set_automatic_safety_state(
            AutomaticSafetyState(
                False, None, (("title_layout", "automatic_admission_failed"),)
            ),
            4,
        )
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 5
        )
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        repository.set_automatic_safety_state(AutomaticSafetyState(False, None, ()), 6)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 7
        )
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        repository.close()


def test_suspended_bundle_does_not_block_an_unrelated_automatic_category() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        outcome = _synthetic_automatic_tuple(repository, case_id, "date_precision")
        outcome.outcomes += (
            SimpleNamespace(
                disposition="candidate",
                policy_category="language_rendering",
                policy_option="display_name",
                policy_destination="automatic",
                destination="language",
                citation_ids=("local_" + "9" * 64,),
                combination_ids=(),
                proposal_id="review_proposal_" + "9" * 64,
                candidate_id="alternative_" + "8" * 64,
                rule_id="shadow_rule_" + "7" * 64,
                outcome_id="shadow_outcome_" + "6" * 64,
            ),
        )
        repository.set_automatic_safety_state(
            AutomaticSafetyState(
                False,
                None,
                (("date_precision", "automatic_admission_failed"),),
            ),
            3,
        )
        repository._record_automatic_outcomes(outcome, 4)
        assert ("language",) == tuple(
            value[0]
            for value in repository._connection.execute(
                "SELECT destination FROM automatic_decisions"
            ).fetchall()
        )
        repository.close()


def test_nonautomatic_policy_destination_never_constructs_an_automatic_decision() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        ignored = SimpleNamespace(
            outcomes=(
                SimpleNamespace(
                    disposition="candidate",
                    policy_category="tags",
                    policy_option="catalogue",
                    policy_destination="ignore",
                    destination="tags",
                    combination_ids=(),
                    proposal_id="review_proposal_" + "a" * 64,
                    candidate_id="alternative_" + "b" * 64,
                    rule_id="shadow_rule_" + "c" * 64,
                    outcome_id="shadow_outcome_" + "d" * 64,
                ),
            ),
            review_case_id=case_id,
            report_id=ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            ).report_id,
            proposal_set_id=ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            ).proposal_set_id,
            target_fingerprint=ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            ).target_fingerprint,
            resolved_policy_id=repository.current_resolved_policy_id("ebooks"),
            policy_generation=1,
        )
        repository._record_automatic_outcomes(ignored, 2)
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        assert (
            AutomaticSafetyState(False, None, ()) == repository.automatic_safety_state()
        )
        repository.close()


def test_generic_subtitle_preservation_records_only_the_title_no_op() -> None:
    report = _bibliographic_report(
        current_title="Current title",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Current title",),
                subtitle="A Novel",
            ),
        ),
    )
    profile = _preview_automatic_profile()

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository.set_automatic_safety_state(AutomaticSafetyState(False, None, ()), 2)

        persisted = repository.persist_shadow_outcome(case_id, 3)
        outcome = ShadowOutcomeSet.from_json(persisted)

        assert persisted == repository.persist_shadow_outcome(case_id, 4)
        assert [("title", "keep_current")] == [
            (value.destination, value.disposition)
            for value in outcome.outcomes
            if value.destination == "title"
        ]
        assert ("title", "keep_current") == repository._connection.execute(
            "SELECT destination,document ->> '$.outcome' FROM automatic_decisions"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions WHERE destination='subtitle'"
        ).fetchone()
        assert 0 == repository.count_current_attention_fields(None)
        detail = InspectCurrentReviewCase(repository).run(case_id)
        title = next(
            value for value in detail["fields"] if value["destination"] == "title"
        )
        subtitle = next(
            value for value in detail["fields"] if value["destination"] == "subtitle"
        )
        assert not title["needs_attention"]
        assert not subtitle["needs_attention"]
        assert 0 == repository.read_review_attention(None, 50, 0).total
        activity = repository.field_effect_activity_page(
            search=None, event_type="metadata", occurred_after=0, page=1, page_size=50
        )
        assert 1 == activity["total"]
        assert "title" == activity["events"][0]["destination"]
        repository.close()

        reopened = open_repository(path, create=False)
        assert persisted == reopened.persist_shadow_outcome(case_id, 5)
        assert (1,) == reopened._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        newer = _bibliographic_report(
            current_title="New current title",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("New current title",),
                    subtitle="A Novel",
                ),
            ),
        )
        reopened.publish(newer, lambda: True, report.report_id)
        assert "stale" == reopened.persist_shadow_outcome(case_id, 6)
        reopened.close()


def test_provider_consensus_title_persists_the_exact_qualified_automatic_tranche() -> (
    None
):
    local = "Plain Work [retail]"
    report = _proposal_report(
        fields=_fields(
            local,
            title=TargetField(
                TargetDestination.TITLE,
                TargetFieldState.VALUE,
                local,
            ),
        ),
        local_claims=(_consensus_local_title(local),),
        providers=(
            _consensus_provider_title("open_library", "Plain Work"),
            _consensus_provider_title("hardcover", "Plain Work"),
        ),
    )
    profile = _preview_automatic_profile()

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]

        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        title = next(
            value for value in outcome.outcomes if value.destination == "title"
        )
        document = repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='title'"
        ).fetchone()

        assert title.candidate_id is not None
        assert title.policy_category == "title"
        assert title.policy_option == "replace_target:provider_consensus_noise"
        assert document is not None
        decision = AutomaticDecision.from_json(document[0])
        assert decision.candidate_id == title.candidate_id
        assert decision.policy_category == "title"
        assert decision.policy_option == "replace_target:provider_consensus_noise"
        assert (
            decision.qualification_manifest_sha256 == F19A_QUALIFICATION_MANIFEST_SHA256
        )
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        assert {"kind": "text", "value": "Plain Work"} == (
            _automatic_decision_value(decision, case, report, proposal, outcome)
        )
        detail = InspectCurrentReviewCase(repository).run(case_id)
        projected = next(
            value for value in detail["fields"] if value["destination"] == "title"
        )
        assert projected["automatic_decision"] is not None
        assert projected["automatic_decision"]["selected_value"] == {
            "kind": "text",
            "value": "Plain Work",
        }
        assert projected["notices"] == ["provider_consensus_title_bracket_source_tag"]
        authority = repository.field_effect_authority_source(decision)
        repository.grant_field_effect_consents(
            authority["connection"],
            (authority["library"],),
            ("title",),
            5,
        )
        service = GuardedFieldApplication(
            repository,
            FakeTargetService(),
            FakeBackupReadiness(),
            FakeCapabilityObserver("c" * 64),
            PersistedFieldEffectAuthority(repository),
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            SimpleNamespace(),
            FakeForwardReevaluation(),
            lambda: 5,
        )
        plan = service.plan(
            decision,
            connection=authority["connection"],
            library=authority["library"],
            work_id=authority["work_id"],
            destination="title",
            pre_value=authority["pre_value"],
            desired_value=authority["decision_value"],
            target_fingerprint=authority["target_fingerprint"],
            target_version=authority["target_version"],
            dependency_digests=authority["dependency_digests"],
            capability_digest="c" * 64,
        )
        assert plan is not None
        repository.set_automatic_safety_state(
            AutomaticSafetyState(False, None, (("title_layout", "test"),)), 6
        )
        with pytest.raises(FieldEffectError, match="^authority$"):
            repository.claim_field_effect(plan.plan.plan_id, "planned", "a" * 64, 7)
        assert "planned" == repository.get_field_effect(plan.plan.plan_id).state
        repository.close()


def test_decoration_tidy_title_persists_its_own_qualified_automatic_option() -> None:
    local = "Plain Work [retail]"
    report = _proposal_report(
        fields=_fields(
            local,
            title=TargetField(
                TargetDestination.TITLE,
                TargetFieldState.VALUE,
                local,
            ),
        ),
        local_claims=(_consensus_local_title(local),),
        providers=(
            _consensus_provider_title("open_library", "Plain Work"),
            _consensus_provider_title("hardcover", "Plain Work [retail]"),
        ),
    )
    profile = _preview_automatic_profile()

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]

        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        title = next(
            value for value in outcome.outcomes if value.destination == "title"
        )
        document = repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='title'"
        ).fetchone()

        assert title.candidate_id is not None
        assert title.policy_category == "title"
        assert title.policy_option == "replace_target:decoration_tidy"
        assert document is not None
        decision = AutomaticDecision.from_json(document[0])
        assert decision.policy_option == "replace_target:decoration_tidy"
        assert (
            decision.qualification_manifest_sha256 == F19A_QUALIFICATION_MANIFEST_SHA256
        )
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        assert {"kind": "text", "value": "Plain Work"} == (
            _automatic_decision_value(decision, case, report, proposal, outcome)
        )
        detail = InspectCurrentReviewCase(repository).run(case_id)
        projected = next(
            value for value in detail["fields"] if value["destination"] == "title"
        )
        assert projected["notices"] == ["decoration_tidy_bracket_source_tag"]
        repository.close()


def test_decoration_equivalent_title_keeps_the_current_value_without_a_write() -> None:
    local = "Plain Work"
    report = _proposal_report(
        fields=_fields(
            local,
            title=TargetField(
                TargetDestination.TITLE,
                TargetFieldState.VALUE,
                local,
            ),
        ),
        local_claims=(_consensus_local_title(local),),
        providers=(
            _consensus_provider_title("open_library", "Plain Work"),
            _consensus_provider_title("hardcover", "Plain Work [retail]"),
        ),
    )
    profile = _preview_automatic_profile()

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        title = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.TITLE
        )
        rows = repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE destination='title'"
        ).fetchone()

        assert ProposalOutcome.KEEP is title.outcome
        assert ("decoration_equivalent_bracket_source_tag",) == title.reasons
        assert title.selected_alternative_id is None
        assert (0,) == rows
        repository.close()


def test_provider_consensus_contributor_display_preserves_local_provenance_and_owner_finality() -> (
    None
):
    local = "Example, Ada"
    local_claims = (_local_creator(local),)
    providers = tuple(
        ProviderObservation(
            name,
            ProviderStatus.CANDIDATES,
            candidates=(
                finalize_candidate_comparisons(
                    ProviderCandidate(
                        f"{name}-edition",
                        f"{name}-work",
                        (
                            _claim(
                                name,
                                f"{name}-edition",
                                ClaimKind.CREATOR,
                                "Ada Example",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                        ),
                    ),
                    local_claims,
                ),
            ),
            authority=name,
        )
        for name in ("open_library", "hardcover")
    )
    report = _proposal_report(
        fields=_fields(
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                (local,),
            ),
        ),
        local_claims=local_claims,
        providers=providers,
    )
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["contributor_names"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Contributor display automatic intent", dict(DEFAULT_OPTIONS), destinations
    )

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        authors = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.AUTHORS
        )
        assert authors.reasons == ("provider_consensus_contributor_display",)
        assert local_claims[0].reference in authors.alternatives[0].evidence_references
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]

        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        authors_outcome = next(
            value for value in outcome.outcomes if value.destination == "authors"
        )
        automatic = next(
            AutomaticDecision.from_json(value)
            for value in repository.get_review_case_decisions(case_id)
            if AutomaticDecision.from_json(value).destination == "authors"
        )
        assert authors_outcome.policy_category == "contributor_names"
        assert automatic.policy_category == F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY
        assert automatic.policy_option == F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION
        assert (
            automatic.qualification_manifest_sha256 == F38_QUALIFICATION_MANIFEST_SHA256
        )
        assert {"kind": "list", "value": ["Ada Example"]} == (
            _automatic_decision_value(
                automatic,
                ReviewCase.create_from_data(
                    json.loads(repository.get_review_case(case_id))["case"]
                ),
                report,
                proposal,
                outcome,
            )
        )

        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        author_proposal = next(
            value for value in case.proposals if value["destination"] == "authors"
        )
        owner = HumanDecision.create(
            case,
            proposal_id=author_proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination="authors",
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="provider-consensus-contributor-owner-final-0001",
            occurred_at=5,
        )
        repository.record_human_decision(owner)
        detail = InspectCurrentReviewCase(repository).run(case_id)
        projected = next(
            value for value in detail["fields"] if value["destination"] == "authors"
        )
        assert projected["human_decision"]["decision_id"] == owner.decision_id
        assert projected["automatic_decision"]["decision_id"] == automatic.decision_id
        repository.close()


def test_provider_consensus_contributor_display_preserve_option_stays_manual() -> None:
    local = "Example, Ada"
    local_claims = (_local_creator(local),)
    providers = tuple(
        ProviderObservation(
            name,
            ProviderStatus.CANDIDATES,
            candidates=(
                finalize_candidate_comparisons(
                    ProviderCandidate(
                        f"{name}-edition",
                        f"{name}-work",
                        (
                            _claim(
                                name,
                                f"{name}-edition",
                                ClaimKind.CREATOR,
                                "Ada Example",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                        ),
                    ),
                    local_claims,
                ),
            ),
            authority=name,
        )
        for name in ("open_library", "hardcover")
    )
    report = _proposal_report(
        fields=_fields(
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                (local,),
            ),
        ),
        local_claims=local_claims,
        providers=providers,
    )
    options = dict(DEFAULT_OPTIONS)
    options["contributor_names"] = "preserve"
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations["contributor_names"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Contributor preserve automatic intent", options, destinations
    )

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]

        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        authors = next(
            value for value in outcome.outcomes if value.destination == "authors"
        )
        assert authors.policy_option == "preserve"
        assert authors.disposition == "manual"
        assert not repository.get_review_case_decisions(case_id)
        repository.close()


def test_provider_consensus_title_respects_title_layout_suspension_at_admission() -> (
    None
):
    local = "Plain Work [retail]"
    report = _proposal_report(
        fields=_fields(
            local,
            title=TargetField(TargetDestination.TITLE, TargetFieldState.VALUE, local),
        ),
        local_claims=(_consensus_local_title(local),),
        providers=(
            _consensus_provider_title("open_library", "Plain Work"),
            _consensus_provider_title("hardcover", "Plain Work"),
        ),
    )
    profile = _preview_automatic_profile()

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository.set_automatic_safety_state(
            AutomaticSafetyState(False, None, (("title_layout", "test"),)), 4
        )

        repository.persist_shadow_outcome(case_id, 5)

        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions WHERE destination='title'"
        ).fetchone()
        repository.close()


@pytest.mark.parametrize(
    "option", ("primary_narrative", "source_order", "all_qualified")
)
def test_absent_series_positions_reach_effect_authority_after_persistence(
    option,
) -> None:
    report = _automatic_series_report(
        _series_provider(
            "hardcover",
            "edition-1",
            "hardcover",
            _series_claims("hardcover", "edition-1")[:1],
        ),
        _series_provider(
            "open_library",
            "edition-2",
            "open_library",
            _series_claims("open_library", "edition-2")[:1],
        ),
    )
    report = _proposal_report(
        fields=tuple(
            TargetField(TargetDestination.ISBN, TargetFieldState.VALUE, "9780306406157")
            if field.destination is TargetDestination.ISBN
            else field
            for field in report.target.fields
        ),
        local_claims=report.local_claims,
        providers=report.providers,
    )
    base = _preview_automatic_profile()
    profile = MetadataPolicyProfile.create(
        "Automatic series preserves absent positions",
        {**dict(base.options), "series": option},
        {**dict(base.destinations), "series": "automatic"},
    )

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository.set_automatic_safety_state(AutomaticSafetyState(False, None, ()), 2)

        repository.persist_shadow_outcome(case_id, 3)
        assert 0 == repository.count_current_attention_fields(None)
        detail = InspectCurrentReviewCase(repository).run(case_id)
        series = next(
            value for value in detail["fields"] if value["destination"] == "series"
        )
        assert not series["needs_attention"]
        assert series["editable"]
        assert series["human_decision"] is None
        assert series["automatic_selection_sources"] == ["Hardcover", "Open Library"]
        assert "propose_change" == series["deterministic_disposition"]
        assert 0 == repository.read_review_attention(None, 50, 0).total
        decision = next(
            AutomaticDecision.from_json(document)
            for document in repository.get_review_case_decisions(case_id)
            if json.loads(document)["destination"] == "series"
        )
        assert repository.field_effect_authority_source(decision)["decision_value"] == {
            "kind": "series",
            "value": [{"name": "Reading Order", "sequence": None}],
        }
        assert qualification_manifest_is_current(
            "series", option, decision.qualification_manifest_sha256
        )
        documents = repository.get_review_case_decisions(case_id)
        repository.persist_shadow_outcome(case_id, 4)
        assert repository.get_review_case_decisions(case_id) == documents
        repository.close()


def test_paused_or_category_suspended_actionless_tuple_stays_context_only() -> None:
    local_title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Secret History",
        canonical="Secret History",
        excerpt="Secret History",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    source = _source(
        "catalogue",
        "edition-1",
        titles=("Secret History",),
        subtitle="A Mistborn Novella",
    )
    report = _bibliographic_report(
        current_title="secret history", providers=(source,), local_claims=(local_title,)
    )
    profile = _preview_automatic_profile()

    def admit(repository):
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        title = next(
            value for value in proposal.results if value.destination.value == "title"
        )
        assert "unresolved_choice" != title.to_data()["outcome"]
        # `to_data()` is a `dict[str, object]`, so the membership test needs the
        # declared shape of `reasons` -- `ProposalResult.to_data` writes
        # `list(self.reasons)`, a list of strings.
        assert "no_safe_bibliographic_combination" not in cast(
            list[str], title.to_data()["reasons"]
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        return json.loads(repository.admit_review_case(proposal))["review_case_id"]

    with tempfile.TemporaryDirectory() as temporary:
        baseline = open_repository(Path(temporary) / "reports.sqlite")
        case_id = admit(baseline)
        title = next(
            field
            for field in InspectCurrentReviewCase(baseline).run(case_id)["fields"]
            if field["destination"] == "title"
        )
        assert not title["needs_attention"]
        baseline.close()
    for safety in (
        AutomaticSafetyState(True, None, ()),
        AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),)),
        AutomaticSafetyState(False, "authority_failed", ()),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = admit(repository)
            repository.set_automatic_safety_state(safety, 1)
            repository.persist_shadow_outcome(case_id, 2)
            assert (0,) == repository._connection.execute(
                "SELECT COUNT(*) FROM automatic_decisions"
            ).fetchone()
            detail = InspectCurrentReviewCase(repository).run(case_id)
            title = next(
                field for field in detail["fields"] if field["destination"] == "title"
            )
            # Automatic-safety context remains inspectable, but an equivalent
            # title has no owner action and cannot enter Attention.
            assert title["decision_actions"] == []
            assert not title["needs_attention"]
            assert (
                "Automatic handling is suspended pending a safety recovery."
                == title["classification_reason"]
            )
            library = repository.read_review_library(None, 50, 0, True)
            assert 1 == library.total
            assert 1 == library.attention_total
            attention = repository.read_review_attention(None, 50, 0)
            # The subtitle has an exact eligible alternative, so a suspended
            # automatic tuple remains in every Attention projection when an
            # owner decision is actually available.
            subtitle = next(
                field
                for field in detail["fields"]
                if field["destination"] == "subtitle"
            )
            assert subtitle["decision_actions"]
            assert subtitle["needs_attention"]
            assert 1 == attention.total
            assert ("subtitle",) == tuple(row[2] for row in attention.rows)
            assert "automatic_safety_suspended" == attention.rows[0][5]
            projected = BrowseReviewLibrary(repository).attention(
                search=None,
                limit=50,
                cursor=None,
            )
            assert attention.total == projected["total"]
            assert ["subtitle"] == [item["destination"] for item in projected["items"]]
            assert "Automatic handling is suspended pending a safety recovery." == next(
                item["classification_reason"]
                for item in projected["items"]
                if item["destination"] == "subtitle"
            )
            repository.close()


def test_owner_activation_acknowledgement_is_durable() -> None:
    """Dismissing a completed activation survives a restart.

    This once acknowledged the seeded bootstrap row, because that row was what
    a fresh container reported. The interface now names generation 1 as the
    active baseline and never offers it for dismissal, so the durability this
    proves is bound to the transition an owner can actually dismiss.
    """

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        report = _report()
        repository.publish(report, lambda: True)
        profile = MetadataPolicyProfile.create(
            "Owner policy",
            dict(DEFAULT_OPTIONS),
            {
                name: "unsupported" if name == "explicit" else "ask"
                for name in OPTION_CATALOGUE
            },
        )
        resolved = resolve_policy(profile, report.target.library)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        transition = repository.metadata_policy_transition()
        assert transition is not None
        assert transition.origin == "activation"
        acknowledged = repository.acknowledge_metadata_policy_transition(
            transition.transition_id, 4
        )
        assert acknowledged.acknowledged
        repository.close()
        reopened = open_repository(path)
        persisted = reopened.metadata_policy_transition()
        assert persisted is not None
        assert (persisted.acknowledged, persisted.origin) == (True, "activation")
        reopened.close()
