from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from mechanical_librarian.application.audit import (
    AuditAppendOutcome, AuditAppendResult, AuditEvent, AuditEventKind, AuditQueryOutcome,
    AuditQueryResult, AuditReadOutcome, AuditReadResult, SafeAuditSummary,
)
from mechanical_librarian.application.job_heartbeat import JobClaim, JobHeartbeatRepository as DurableWorkRepository, RunSnapshot
from mechanical_librarian.application.ports import (
    AdapterDiagnostics, BackupReadinessReport, BackupReadinessRequest, BackupState,
    CancellationId, CancellationProbe, Capability, CapabilitySet, CompatibilityReport, CompatibilityStatus,
    CoverCandidate, CurrentFieldState, CurrentFieldStateKind, Deadline, EvidenceExtractor, ExpectedState, ExtractorRequest,
    FailureKind, HealthReport, HealthStatus, LibraryListRequest, LibraryReadRequest,
    MetadataCandidate, MetadataMutation, Notification, OperationContext, Page, PageRequest,
    PortFailure, PortResult, ProviderFact, ProviderFactCode, ProviderProvenance, ProviderQuery,
    RepositoryOutcome, RepositoryResult, ResourceBounds, RetryAdvice, ReviewerDecision, ReviewerRequest, ReviewerResponse,
    SafeSummary, SecretHandle, SecretResolver, SecretScope, SnapshotStoreRequest, Unit,
    JobHeartbeatRepository, TimeProbe, Transaction, WorkRequest, WriteGrant, WriteGrantId, WriteReceipt, WriteRequest, mutation_digest, provider_response_preflight, provider_response_size,
)
from mechanical_librarian.domain.evidence import (
    ConfidenceFact, EvidenceDetail, EvidenceRecord, EvidenceReference, EvidenceRelationship, FieldProposal, FieldValue, ProposalKind,
    ReviewerEvidencePacket,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId, AdapterVersion, ApplyPlanId, AuditEventId, CompatibilityVersion, ConnectionId,
    DecisionAuthorizationId, EvidenceId, ExternalItemId, ExtractorVersion, Fingerprint,
    IdempotencyKey, JobId, LibraryId, ProposalId, SchemaVersion, ServiceVersion, SnapshotId, TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities, ConcurrencyToken, CoverReference, FieldName, FileReference, ItemSnapshot, LibraryConnection, MediaType, MetadataField,
    SecretReference,
)
from mechanical_librarian.domain.workflow import FailureKind as WorkflowFailureKind, OutcomeKind, RunId, StageId, WorkerId, WorkflowDefinition, synthetic_heartbeat_definition
from tests.contract.service_ports import ServicePortContract, standard_family_contracts


class CancellationOwner:
    """Synthetic owner/controller; adapters see only its observation methods."""

    def __init__(self) -> None:
        self._cancelled = False

    @property
    def probe(self) -> CancellationProbe:
        return CancellationProbe(CancellationId("contract-run"), lambda: self._cancelled)

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


class TimeOwner:
    def __init__(self, value: datetime) -> None:
        self.value = value

    @property
    def probe(self) -> TimeProbe:
        return TimeProbe(lambda: self.value)

    def advance(self, duration: timedelta) -> None:
        self.value += duration


class SyntheticWriter:
    def __init__(self, current: Fingerprint | None = None) -> None:
        self.receipts: dict[IdempotencyKey, WriteReceipt] = {}
        self.current = current

    def apply(self, request: WriteRequest) -> PortResult[WriteReceipt]:
        preflight = request.preflight_result()
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        previous = self.receipts.get(request.idempotency_key)
        if previous is not None:
            return PortResult.success(WriteReceipt(previous.idempotency_key, previous.resulting_fingerprint, True))
        if self.current is not None and request.expected.fingerprint != self.current:
            return PortResult.failed(_failure(FailureKind.CONFLICT, False))
        receipt = WriteReceipt(request.idempotency_key, request.expected.fingerprint, False)
        self.receipts[request.idempotency_key] = receipt
        return PortResult.success(receipt)


class SyntheticProvider:
    def __init__(self, candidates: tuple[MetadataCandidate, ...]) -> None:
        self.candidates = candidates

    def lookup(self, query: ProviderQuery, page: PageRequest, context: OperationContext) -> PortResult[Page[MetadataCandidate]]:
        request_preflight = query.preflight_result(page, context)
        if request_preflight.failure is not None:
            return PortResult.failed(request_preflight.failure)
        start = 0 if page.cursor is None else int(page.cursor)
        end = min(start + page.limit, len(self.candidates))
        next_cursor = str(end) if end < len(self.candidates) else None
        output = Page(self.candidates[start:end], next_cursor)
        response_preflight = provider_response_preflight(output, page, context, MetadataCandidate)
        return PortResult.failed(response_preflight.failure) if response_preflight.failure is not None else PortResult.success(output)


class SyntheticCoverProvider:
    def __init__(self, candidates: tuple[CoverCandidate, ...] = ()) -> None:
        self.candidates = candidates

    def lookup_covers(self, query: ProviderQuery, page: PageRequest, context: OperationContext) -> PortResult[Page[CoverCandidate]]:
        request_preflight = query.preflight_result(page, context)
        if request_preflight.failure is not None:
            return PortResult.failed(request_preflight.failure)
        start = 0 if page.cursor is None else int(page.cursor)
        end = min(start + page.limit, len(self.candidates))
        next_cursor = str(end) if end < len(self.candidates) else None
        output = Page(self.candidates[start:end], next_cursor)
        response_preflight = provider_response_preflight(output, page, context, CoverCandidate)
        return PortResult.failed(response_preflight.failure) if response_preflight.failure is not None else PortResult.success(output)


class SyntheticUnsupportedProvider:
    def lookup(self, query: ProviderQuery, page: PageRequest, context: OperationContext) -> PortResult[Page[MetadataCandidate]]:
        return PortResult.failed(_failure(FailureKind.UNSUPPORTED, False))


class SyntheticExtractor:
    def extract(self, request: ExtractorRequest) -> PortResult[EvidenceRecord]:
        preflight = request.preflight_result()
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        return PortResult.success(EvidenceRecord(EvidenceId("evidence-extracted"), request.target, request.snapshot, request.fingerprint, ExtractorVersion("extractor-v1"), SchemaVersion("evidence-v1"), "epub", (ConfidenceFact("title", "Synthetic title"),)))


class SyntheticLibraryReader:
    def discover_capabilities(self, connection: LibraryConnection, context: OperationContext) -> PortResult[AdapterCapabilities]:
        raise NotImplementedError

    def list_snapshots(self, request: LibraryListRequest) -> PortResult[Page[ItemSnapshot]]:
        return PortResult.success(Page((), "next-page" if request.page.cursor is None else None))

    def read_snapshot(self, request: LibraryReadRequest) -> PortResult[ItemSnapshot]:
        return PortResult.failed(_failure(FailureKind.NOT_FOUND, False))


class SyntheticSnapshotRepository:
    def store(self, request: SnapshotStoreRequest) -> RepositoryResult[ItemSnapshot]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED, request.snapshot)

    def read(self, request: LibraryReadRequest) -> RepositoryResult[ItemSnapshot]:
        return RepositoryResult(RepositoryOutcome.NOT_FOUND)

    def list(self, request: LibraryListRequest) -> RepositoryResult[Page[ItemSnapshot]]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED, Page((), None))


class SyntheticTransaction:
    def commit(self, context: OperationContext) -> PortResult[Unit]:
        return PortResult.failed(_failure(FailureKind.CANCELLED, False)) if context.cancelled else PortResult.success(Unit.VALUE)

    def rollback(self, context: OperationContext) -> PortResult[Unit]:
        return PortResult.success(Unit.VALUE)


class SyntheticTransactionManager:
    def begin(self, context: OperationContext) -> PortResult[Transaction]:
        return PortResult.success(SyntheticTransaction())


class SyntheticAudit:
    def append(self, event: AuditEvent) -> AuditAppendResult:
        return AuditAppendResult(AuditAppendOutcome.APPENDED)

    def read(self, event_id: AuditEventId) -> AuditReadResult:
        return AuditReadResult(AuditReadOutcome.NOT_FOUND)

    def recent(self, limit: int) -> AuditQueryResult:
        return AuditQueryResult(AuditQueryOutcome.OK, ())


class SyntheticReviewer:
    def review(self, request: ReviewerRequest) -> PortResult[ReviewerResponse]:
        preflight = request.preflight_result()
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        evidence = request.packet.details[0].reference.evidence
        return PortResult.success(
            ReviewerResponse(
                request,
                tuple(
                    ReviewerDecision(item.id, ReviewerAction.APPROVE, SafeSummary("evidence supports candidate"), (evidence,))
                    for item in request.proposals
                ),
            )
        )


# Imported after class declaration list stays readable.
from mechanical_librarian.application.ports import ReviewerAction


class SyntheticBackup:
    def check(self, request: BackupReadinessRequest) -> PortResult[BackupReadinessReport]:
        preflight = request.context.preflight_result(item_count=1, serialized_bytes=1)
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        return PortResult.success(BackupReadinessReport(BackupState.READY, request.target, request.expected, request.context.observed_at, SafeSummary("backup verified")))


class SyntheticNotifier:
    def notify(self, notification: Notification) -> PortResult[Unit]:
        return PortResult.success(Unit.VALUE)


class SyntheticSecretResolver:
    def configured(self, reference: SecretReference, scope: SecretScope) -> bool:
        return True

    def resolve(self, reference: SecretReference, scope: SecretScope) -> PortResult[SecretHandle]:
        return PortResult.success(SecretHandle(reference, scope))


class SyntheticDiagnostics:
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(frozenset({Capability.METADATA_READ}))

    def compatibility(self, context: OperationContext) -> PortResult[CompatibilityReport]:
        return PortResult.success(CompatibilityReport(CompatibilityVersion("ports-v1"), AdapterVersion("adapter-v1"), ServiceVersion("service-v1"), CompatibilityStatus.COMPATIBLE, SafeSummary("versions compatible")))

    def health(self, context: OperationContext) -> PortResult[HealthReport]:
        return PortResult.success(HealthReport(HealthStatus.HEALTHY, context.observed_at, SafeSummary("service healthy")))


class SyntheticScheduler(JobHeartbeatRepository):
    def schedule(self, request: WorkRequest) -> PortResult[Unit]:
        return PortResult.success(Unit.VALUE)

    def validate_run(self, run_id: RunId, definition: WorkflowDefinition) -> RepositoryResult[None]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED)

    def create_or_resume(self, run_id: RunId, definition: WorkflowDefinition, *, changed_stages: frozenset[StageId], now: datetime) -> RepositoryResult[RunSnapshot]:
        return RepositoryResult(RepositoryOutcome.NOT_FOUND)

    def enqueue_eligible(self, run_id: RunId, *, now: datetime) -> RepositoryResult[int]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED, 0)

    def claim(self, worker_id: WorkerId, *, run_id: RunId | None = None, capacity: int, now: datetime, lease_duration: timedelta) -> RepositoryResult[tuple[JobClaim, ...]]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED, ())

    def start(self, claim: JobClaim, *, now: datetime) -> RepositoryResult[None]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED)

    def renew(self, claim: JobClaim, *, now: datetime, lease_duration: timedelta) -> RepositoryResult[JobClaim]:
        return RepositoryResult(RepositoryOutcome.NOT_FOUND)

    def complete(self, claim: JobClaim, outcome: OutcomeKind, *, now: datetime) -> RepositoryResult[None]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED)

    def fail(self, claim: JobClaim, failure: WorkflowFailureKind, *, now: datetime) -> RepositoryResult[None]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED)

    def cancel_run(self, run_id: RunId, *, now: datetime) -> RepositoryResult[None]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED)

    def cancel_stage(self, run_id: RunId, stage_id: StageId, *, now: datetime) -> RepositoryResult[None]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED)

    def acknowledge_cancellation(self, claim: JobClaim, *, now: datetime) -> RepositoryResult[None]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED)

    def inspect(self, run_id: RunId) -> RepositoryResult[RunSnapshot]:
        return RepositoryResult(RepositoryOutcome.NOT_FOUND)


class BadProvider:
    def lookup(self, snapshot: ItemSnapshot, page: PageRequest, context: OperationContext, vendor: str) -> PortResult[object]:
        raise NotImplementedError


class BadProviderParameterShape:
    def lookup(self, request: ProviderQuery, *, page: PageRequest, context: OperationContext) -> PortResult[Page[MetadataCandidate]]:
        return PortResult.success(Page((), None))


class BadCommitParameterShape:
    def commit(self, *, wrong: OperationContext) -> PortResult[Unit]:
        return PortResult.success(Unit.VALUE)


class FusedTransactionRoles:
    def begin(self, context: OperationContext) -> PortResult[Transaction]:
        return PortResult.success(self)

    def commit(self, context: OperationContext) -> PortResult[Unit]:
        return PortResult.success(Unit.VALUE)

    def rollback(self, context: OperationContext) -> PortResult[Unit]:
        return PortResult.success(Unit.VALUE)


class MissingRollbackTransaction:
    def commit(self, context: OperationContext) -> PortResult[Unit]:
        return PortResult.success(Unit.VALUE)


class MissingBeginManager:
    def begin_transaction(self, context: OperationContext) -> PortResult[Transaction]:
        return PortResult.success(SyntheticTransaction())


class BadWriter:
    def apply(self, snapshot: ItemSnapshot) -> PortResult[Unit]:
        raise NotImplementedError


class BadOutputProvider:
    def lookup(self, query: ProviderQuery, page: PageRequest, context: OperationContext) -> PortResult[Page[MetadataCandidate]]:
        return PortResult.success(Page(("vendor payload",), None))  # type: ignore[arg-type]


class BadNotifier:
    def notify(self, notification: Notification) -> PortResult[Unit]:
        return "sent"  # type: ignore[return-value]


class BadRepository:
    def store(self, request: SnapshotStoreRequest) -> RepositoryResult[ItemSnapshot]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED, request.snapshot)

    def lookup(self, query: ProviderQuery) -> object:
        return query


def _role_nonconformer(name: str) -> object:
    if name == "repository":
        return BadRepository()
    # Each role gets a distinct plausible class that exposes cross-family authority.
    class_name = "Bad" + "".join(part.title() for part in name.split("-"))
    return type(class_name, (), {"apply": lambda self, request: "raw-result"})()


def _target(item: str = "item") -> TargetId:
    return TargetId(AdapterId("synthetic"), ConnectionId("connection"), LibraryId("library"), ExternalItemId(item))


def _fingerprint(value: str = "before") -> Fingerprint:
    return Fingerprint.from_canonical_value({"state": value}, schema=SchemaVersion("contract-v1"))


def _context(*, now: datetime | None = None, owner: CancellationOwner | None = None, time_owner: TimeOwner | None = None, items: int = 8, size: int = 4096, deadline_delta: timedelta = timedelta(seconds=10)) -> OperationContext:
    observed = now or datetime.now(timezone.utc)
    clock_owner = time_owner or TimeOwner(observed)
    return OperationContext(Deadline(observed + deadline_delta), (owner or CancellationOwner()).probe, ResourceBounds(items, size), clock_owner.probe)


def _failure(kind: FailureKind, retryable: bool) -> PortFailure:
    return PortFailure(kind, RetryAdvice(retryable), SafeSummary("bounded operation failed"))


def _write_request(*, context: OperationContext | None = None, mutation: MetadataMutation | None = None, grant_expiry: datetime | None = None) -> WriteRequest:
    target = _target()
    expected = ExpectedState(_fingerprint(), ConcurrencyToken("opaque-token"))
    key = IdempotencyKey.from_canonical_input(operation="metadata-write", target=target, value={"field": "title"})
    use_context = context or _context()
    mutations = (mutation or MetadataMutation(MetadataField(FieldName.TITLE, ("Synthetic title",))),)
    backup = BackupReadinessReport(BackupState.READY, target, expected, use_context.observed_at, SafeSummary("backup verified"))
    grant = WriteGrant(WriteGrantId("write-grant"), target, mutation_digest(mutations), expected, key, DecisionAuthorizationId("decision-auth"), ApplyPlanId("apply-plan"), backup, grant_expiry or use_context.observed_at + timedelta(minutes=1))
    return WriteRequest(target, mutations, grant, key, expected, use_context)


def _review_request() -> ReviewerRequest:
    target, snapshot, fingerprint = _target(), SnapshotId("snapshot-one"), _fingerprint()
    record = EvidenceRecord(EvidenceId("evidence-one"), target, snapshot, fingerprint, ExtractorVersion("extractor-v1"), SchemaVersion("evidence-v1"), "epub", (ConfidenceFact("title", "Synthetic title"),))
    detail = EvidenceDetail.from_record(record)
    packet = ReviewerEvidencePacket(target, snapshot, fingerprint, (detail,))
    proposal = FieldProposal(ProposalId("proposal-one"), target, snapshot, FieldName.TITLE, None, FieldValue(("Synthetic title",)), ProposalKind.FILL_EMPTY, (detail.reference,))
    return ReviewerRequest(packet, (proposal,), (CurrentFieldState(FieldName.TITLE, CurrentFieldStateKind.MISSING),), _context())


class ServicePortContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = ServicePortContract(self)

    def test_reusable_parameterized_suite_accepts_and_rejects_every_family(self) -> None:
        conformers = {
            "repository": SyntheticSnapshotRepository(), "transaction-manager": SyntheticTransactionManager(),
            "transaction": SyntheticTransaction(),
            "durable-scheduler": SyntheticScheduler(), "diagnostics": SyntheticDiagnostics(),
            "library-reader": SyntheticLibraryReader(), "supported-writer": SyntheticWriter(),
            "extractor": SyntheticExtractor(), "metadata-provider": SyntheticProvider(()),
            "cover-provider": SyntheticCoverProvider(), "reviewer": SyntheticReviewer(),
            "secret-resolver": SyntheticSecretResolver(), "backup": SyntheticBackup(),
            "audit": SyntheticAudit(), "notifier": SyntheticNotifier(),
        }
        for family in standard_family_contracts():
            conformer = conformers[family.name]
            with self.subTest(family=family.name, kind="conforming"):
                self.contract.require_family(conformer, family)
            with self.subTest(family=family.name, kind="nonconforming"):
                self.contract.reject_family(_role_nonconformer(family.name), family)

    def test_runtime_result_harness_rejects_notifier_raw_string_despite_annotation(self) -> None:
        request = _write_request()
        notification = Notification("review-requested", request.target, SafeSummary("notification queued"), request.idempotency_key, request.context)
        with self.assertRaises(AssertionError):
            self.contract.require_unit_result(BadNotifier().notify(notification))

    def test_reusable_family_runtime_probes_cover_every_registered_role(self) -> None:
        context, target = _context(), _target()
        read_request = LibraryReadRequest(target, context)
        repository_result = SyntheticSnapshotRepository().read(read_request)
        self.assertIs(type(repository_result), RepositoryResult)
        self.assertIs(repository_result.outcome, RepositoryOutcome.NOT_FOUND)
        transaction = self.contract.require_result_value(SyntheticTransactionManager().begin(context), SyntheticTransaction)
        self.contract.require_unit_result(transaction.commit(context))
        key = IdempotencyKey.from_canonical_input(operation="schedule", target=target, value={"kind": "scan"})
        scheduler = SyntheticScheduler()
        self.contract.require_unit_result(scheduler.schedule(WorkRequest("scan", key, target, context)))
        run_id = RunId("run-one")
        definition = synthetic_heartbeat_definition()
        worker = WorkerId("worker-one")
        claim = JobClaim(JobId("job-one"), run_id, definition.stages[0].id, worker, 1, context.observed_at + timedelta(minutes=1))
        durable_results = (
            scheduler.validate_run(run_id, definition),
            scheduler.create_or_resume(run_id, definition, changed_stages=frozenset(), now=context.observed_at),
            scheduler.enqueue_eligible(run_id, now=context.observed_at),
            scheduler.claim(worker, run_id=run_id, capacity=1, now=context.observed_at, lease_duration=timedelta(minutes=1)),
            scheduler.start(claim, now=context.observed_at),
            scheduler.renew(claim, now=context.observed_at, lease_duration=timedelta(minutes=1)),
            scheduler.complete(claim, OutcomeKind.COMPLETED, now=context.observed_at),
            scheduler.fail(claim, WorkflowFailureKind.SYNTHETIC_TERMINAL, now=context.observed_at),
            scheduler.cancel_run(run_id, now=context.observed_at),
            scheduler.cancel_stage(run_id, definition.stages[0].id, now=context.observed_at),
            scheduler.acknowledge_cancellation(claim, now=context.observed_at),
            scheduler.inspect(run_id),
        )
        self.assertTrue(all(type(result) is RepositoryResult for result in durable_results))
        self.assertIn(RepositoryOutcome.NOT_FOUND, {result.outcome for result in durable_results})
        diagnostics = SyntheticDiagnostics()
        self.contract.require_result_value(diagnostics.health(context), HealthReport)
        self.contract.require_result_value(diagnostics.compatibility(context), CompatibilityReport)
        self.contract.require_failure_result(SyntheticLibraryReader().read_snapshot(read_request), FailureKind.NOT_FOUND)
        self.contract.require_result_value(SyntheticWriter().apply(_write_request(context=context)), WriteReceipt)
        extractor_request = ExtractorRequest(target, SnapshotId("snapshot-one"), _fingerprint(), MediaType.EBOOK, (FileReference("library", "books/item.epub"),), context)
        self.contract.require_result_value(SyntheticExtractor().extract(extractor_request), EvidenceRecord)
        query = ProviderQuery(MediaType.EBOOK, None, (ProviderFact(ProviderFactCode.TITLE, ("Title",)),), SchemaVersion("provider-query-v1"))
        self.contract.require_page_result(SyntheticProvider(()).lookup(query, PageRequest(None, 1), context), MetadataCandidate, PageRequest(None, 1))
        self.contract.require_page_result(SyntheticCoverProvider().lookup_covers(query, PageRequest(None, 1), context), CoverCandidate, PageRequest(None, 1))
        self.contract.require_result_value(SyntheticReviewer().review(_review_request()), ReviewerResponse)
        secret = SyntheticSecretResolver().resolve(SecretReference("providers/catalogue"), SecretScope.METADATA_PROVIDER)
        self.contract.require_result_value(secret, SecretHandle)
        expected = ExpectedState(_fingerprint(), ConcurrencyToken("opaque"))
        backup_request = BackupReadinessRequest(target, expected, context)
        self.contract.require_result_value(SyntheticBackup().check(backup_request), BackupReadinessReport)
        audit_event = AuditEvent(AuditEventId("event-one"), AuditEventKind("contract"), context.observed_at, SafeAuditSummary("job completed"))
        self.assertIs(type(SyntheticAudit().append(audit_event)), AuditAppendResult)
        notification = Notification("job-completed", target, SafeSummary("notification queued"), key, context)
        self.contract.require_unit_result(SyntheticNotifier().notify(notification))

    def test_nonconforming_signatures_and_extra_arguments_fail(self) -> None:
        writer_contract = next(item for item in standard_family_contracts() if item.name == "supported-writer")
        self.contract.reject_family(BadWriter(), writer_contract)
        metadata_contract = next(item for item in standard_family_contracts() if item.name == "metadata-provider")
        self.contract.reject_family(BadProvider(), metadata_contract)
        self.contract.reject_family(BadProviderParameterShape(), metadata_contract)
        transaction_contract = next(item for item in standard_family_contracts() if item.name == "transaction")
        commit_contract = next(item for item in transaction_contract.methods if item.name == "commit")
        with self.assertRaises(AssertionError):
            self.contract.require_method_contract(BadCommitParameterShape(), commit_contract)
        manager_contract = next(item for item in standard_family_contracts() if item.name == "transaction-manager")
        self.contract.reject_family(FusedTransactionRoles(), manager_contract)
        self.contract.reject_family(FusedTransactionRoles(), transaction_contract)
        self.contract.reject_family(MissingBeginManager(), manager_contract)
        self.contract.reject_family(MissingRollbackTransaction(), transaction_contract)

    def test_provider_empty_success_round_trip_and_boundaries(self) -> None:
        query = ProviderQuery(MediaType.EBOOK, FieldName.TITLE, (ProviderFact(ProviderFactCode.TITLE, ("Synthetic title",)),), SchemaVersion("provider-query-v1"))
        empty_request = PageRequest(None, 10)
        empty = SyntheticProvider(()).lookup(query, empty_request, _context(items=16))
        self.assertEqual(self.contract.require_page_result(empty, MetadataCandidate, empty_request), Page((), None))
        provenance = ProviderProvenance("catalogue", "correlation-one", _fingerprint("candidate"))
        candidate = MetadataCandidate(FieldName.TITLE, FieldValue(("Candidate title",)), provenance)
        result = SyntheticProvider((candidate,)).lookup(query, PageRequest(None, 1), _context())
        self.assertEqual(self.contract.require_page_result(result, MetadataCandidate, PageRequest(None, 1)), Page((candidate,), None))
        with self.assertRaises((AssertionError, ValueError)):
            self.contract.require_page_result(BadOutputProvider().lookup(query, PageRequest(None, 1), _context()), MetadataCandidate, PageRequest(None, 1))
        with self.assertRaises(ValueError):
            MetadataCandidate("title", FieldValue(("bad",)), provenance)  # type: ignore[arg-type]

        unicode_query = ProviderQuery(MediaType.EBOOK, FieldName.TITLE, (ProviderFact(ProviderFactCode.TITLE, ("Cien años de soledad", "百年孤独", "L'étranger")),), SchemaVersion("provider-query-v1"))
        self.assertEqual(unicode_query.facts[0].values[1], "百年孤独")
        for unsafe in ("title\x00forged", "/Users/alice/private/book", "/home/alice/private/book", "file:///private/book", "../private/book"):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                ProviderFact(ProviderFactCode.TITLE, (unsafe,))

        cover = CoverCandidate(CoverReference("provider-cover", _fingerprint("cover")), provenance)
        self.assertIs(type(cover), CoverCandidate)

    def test_provider_response_metadata_and_cover_bounds_return_typed_failure(self) -> None:
        query = ProviderQuery(MediaType.EBOOK, FieldName.TITLE, (ProviderFact(ProviderFactCode.TITLE, ("Title",)),), SchemaVersion("provider-query-v1"))
        provenance = ProviderProvenance("catalogue", "correlation", _fingerprint("candidate"))
        metadata = MetadataCandidate(FieldName.TITLE, FieldValue(("x" * 200,)), provenance)
        metadata_result = SyntheticProvider((metadata,)).lookup(query, PageRequest(None, 1), _context(size=100))
        self.assertEqual(metadata_result.failure.kind, FailureKind.RESOURCE_EXHAUSTED)  # type: ignore[union-attr]
        cover = CoverCandidate(CoverReference("x" * 200, _fingerprint("cover")), provenance)
        cover_result = SyntheticCoverProvider((cover,)).lookup_covers(query, PageRequest(None, 1), _context(size=100))
        self.assertEqual(cover_result.failure.kind, FailureKind.RESOURCE_EXHAUSTED)  # type: ignore[union-attr]

    def test_provider_request_complete_envelope_has_one_shared_exact_size(self) -> None:
        query = ProviderQuery(
            MediaType.EBOOK,
            FieldName.TITLE,
            (ProviderFact(ProviderFactCode.TITLE, ("Cien años", "百年孤独")),),
            SchemaVersion("provider-query-v1"),
        )
        plain = PageRequest(None, 2)
        escaped = PageRequest('cursor-"quoted"-\\-tail', 2)
        plain_size = query.canonical_request_size(plain)
        escaped_size = query.canonical_request_size(escaped)
        self.assertGreater(escaped_size, plain_size)
        larger_limit = PageRequest(None, 200)
        self.assertGreater(query.canonical_request_size(larger_limit), plain_size)

        exact = _context(items=4, size=escaped_size)
        query.preflight(escaped, exact)
        self.contract.require_unit_result(query.preflight_result(escaped, exact))
        below = _context(items=4, size=escaped_size - 1)
        with self.assertRaises(ValueError):
            query.preflight(escaped, below)
        self.contract.require_failure_result(query.preflight_result(escaped, below), FailureKind.RESOURCE_EXHAUSTED)

    def test_provider_response_complete_envelope_boundaries_and_originating_limit(self) -> None:
        request = PageRequest(None, 1)
        provenance = ProviderProvenance("catalogue", "correlation", _fingerprint("provenance"))
        metadata = MetadataCandidate(FieldName.TITLE, FieldValue(("Candidate",)), provenance)
        no_cursor_metadata_page = Page((metadata,), None)
        metadata_page = Page((metadata,), 'next-"quoted"-\\-tail')
        no_cursor_size = provider_response_size(no_cursor_metadata_page, MetadataCandidate)
        metadata_size = provider_response_size(metadata_page, MetadataCandidate)
        self.assertGreater(metadata_size, no_cursor_size)
        self.contract.require_unit_result(provider_response_preflight(no_cursor_metadata_page, request, _context(size=no_cursor_size), MetadataCandidate))
        self.contract.require_failure_result(provider_response_preflight(metadata_page, request, _context(size=no_cursor_size), MetadataCandidate), FailureKind.RESOURCE_EXHAUSTED)
        self.contract.require_unit_result(provider_response_preflight(metadata_page, request, _context(size=metadata_size), MetadataCandidate))
        self.contract.require_failure_result(provider_response_preflight(metadata_page, request, _context(size=metadata_size - 1), MetadataCandidate), FailureKind.RESOURCE_EXHAUSTED)

        empty_cursor_page: Page[MetadataCandidate] = Page((), "empty-next")
        empty_size = provider_response_size(empty_cursor_page, MetadataCandidate)
        self.contract.require_unit_result(provider_response_preflight(empty_cursor_page, request, _context(size=empty_size), MetadataCandidate))

        no_content_cover = CoverCandidate(CoverReference("cover-reference"), provenance)
        content_cover = CoverCandidate(CoverReference("cover-reference", _fingerprint("cover-content")), provenance)
        no_content_page = Page((no_content_cover,), None)
        content_page = Page((content_cover,), None)
        self.assertGreater(provider_response_size(content_page, CoverCandidate), provider_response_size(no_content_page, CoverCandidate))
        cover_size = provider_response_size(content_page, CoverCandidate)
        self.contract.require_unit_result(provider_response_preflight(content_page, request, _context(size=cover_size), CoverCandidate))
        self.contract.require_failure_result(provider_response_preflight(content_page, request, _context(size=cover_size - 1), CoverCandidate), FailureKind.RESOURCE_EXHAUSTED)

        over_limit = Page((metadata, metadata), None)
        self.contract.require_failure_result(provider_response_preflight(over_limit, request, _context(items=4, size=64 * 1024), MetadataCandidate), FailureKind.INVALID_RESPONSE)

    def test_both_provider_families_return_typed_response_resource_failure(self) -> None:
        query = ProviderQuery(MediaType.EBOOK, FieldName.TITLE, (ProviderFact(ProviderFactCode.TITLE, ("Title",)),), SchemaVersion("provider-query-v1"))
        request = PageRequest(None, 1)
        provenance = ProviderProvenance("catalogue", "correlation", _fingerprint("provenance"))
        metadata = MetadataCandidate(FieldName.TITLE, FieldValue(("x" * 300,)), provenance)
        metadata_page = Page((metadata,), None)
        metadata_bound = provider_response_size(metadata_page, MetadataCandidate) - 1
        self.assertLessEqual(query.canonical_request_size(request), metadata_bound)
        self.contract.require_failure_result(SyntheticProvider((metadata,)).lookup(query, request, _context(size=metadata_bound)), FailureKind.RESOURCE_EXHAUSTED)

        cover = CoverCandidate(CoverReference("x" * 300, _fingerprint("content")), provenance)
        cover_page = Page((cover,), None)
        cover_bound = provider_response_size(cover_page, CoverCandidate) - 1
        self.assertLessEqual(query.canonical_request_size(request), cover_bound)
        self.contract.require_failure_result(SyntheticCoverProvider((cover,)).lookup_covers(query, request, _context(size=cover_bound)), FailureKind.RESOURCE_EXHAUSTED)

    def test_provider_pagination_traverses_cursor_and_terminates_at_limit(self) -> None:
        query = ProviderQuery(MediaType.EBOOK, FieldName.TITLE, (ProviderFact(ProviderFactCode.TITLE, ("Synthetic",)),), SchemaVersion("provider-query-v1"))
        candidates = tuple(MetadataCandidate(FieldName.TITLE, FieldValue((f"Candidate {index}",)), ProviderProvenance(f"source-{index}", f"correlation-{index}", _fingerprint(f"candidate-{index}"))) for index in range(3))
        provider = SyntheticProvider(candidates)
        cursor: str | None = None
        collected: list[MetadataCandidate] = []
        while True:
            page_request = PageRequest(cursor, 2)
            page = self.contract.require_page_result(provider.lookup(query, page_request, _context()), MetadataCandidate, page_request)
            self.assertLessEqual(len(page.items), 2)
            collected.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual(tuple(collected), candidates)

    def test_cover_provider_pagination_traverses_cursor_and_terminates_at_limit(self) -> None:
        query = ProviderQuery(MediaType.EBOOK, FieldName.TITLE, (ProviderFact(ProviderFactCode.TITLE, ("Synthetic",)),), SchemaVersion("provider-query-v1"))
        provenance = ProviderProvenance("catalogue", "correlation", _fingerprint("provenance"))
        candidates = tuple(CoverCandidate(CoverReference(f"cover-{index}", _fingerprint(f"cover-{index}")), provenance) for index in range(3))
        provider = SyntheticCoverProvider(candidates)
        cursor: str | None = None
        collected: list[CoverCandidate] = []
        while True:
            page_request = PageRequest(cursor, 2)
            page = self.contract.require_page_result(provider.lookup_covers(query, page_request, _context()), CoverCandidate, page_request)
            self.assertLessEqual(len(page.items), 2)
            collected.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual(tuple(collected), candidates)

    def test_local_extractor_round_trip_is_bounded_canonical_evidence(self) -> None:
        request = ExtractorRequest(_target(), SnapshotId("snapshot-one"), _fingerprint(), MediaType.EBOOK, (FileReference("library", "books/item.epub"),), _context())
        record = self.contract.require_result_value(SyntheticExtractor().extract(request), EvidenceRecord)
        self.assertEqual(record.target, request.target)
        self.assertEqual(record.snapshot_fingerprint, request.fingerprint)
        with self.assertRaises(ValueError):
            ExtractorRequest(request.target, request.snapshot, request.fingerprint, request.media_type, ("/private/book.epub",), request.context)  # type: ignore[arg-type]
        many_files = tuple(FileReference("library", f"books/{index}.epub") for index in range(32))
        with self.assertRaises(ValueError):
            ExtractorRequest(request.target, request.snapshot, request.fingerprint, request.media_type, many_files, _context(items=1))
        with self.assertRaises(ValueError):
            ExtractorRequest(request.target, request.snapshot, request.fingerprint, request.media_type, request.files, _context(size=1))

    def test_before_and_inflight_cancellation_deadline_and_resources(self) -> None:
        query = ProviderQuery(MediaType.EBOOK, None, (ProviderFact(ProviderFactCode.TITLE, ("Title",)),), SchemaVersion("provider-query-v1"))
        owner = CancellationOwner()
        context = _context(owner=owner)
        provider = SyntheticProvider(())
        self.assertIsNotNone(provider.lookup(query, PageRequest(None, 1), context).value)
        owner.cancel()
        self.assertEqual(provider.lookup(query, PageRequest(None, 1), context).failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        clock = TimeOwner(datetime.now(timezone.utc))
        live_deadline = _context(now=clock.value, time_owner=clock, deadline_delta=timedelta(seconds=1))
        self.assertIsNotNone(provider.lookup(query, PageRequest(None, 1), live_deadline).value)
        clock.advance(timedelta(seconds=2))
        self.assertEqual(provider.lookup(query, PageRequest(None, 1), live_deadline).failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        expired = _context(deadline_delta=timedelta(seconds=-1))
        self.assertEqual(provider.lookup(query, PageRequest(None, 1), expired).failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        candidate = MetadataCandidate(FieldName.TITLE, FieldValue(("Candidate",)), ProviderProvenance("catalogue", "one", _fingerprint("candidate")))
        exhausted = provider = SyntheticProvider((candidate, candidate))
        self.assertEqual(exhausted.lookup(query, PageRequest(None, 2), _context(items=1)).failure.kind, FailureKind.RESOURCE_EXHAUSTED)  # type: ignore[union-attr]
        self.assertEqual(SyntheticTransaction().commit(context).failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]

    def test_extractor_and_reviewer_repeat_live_preflight_at_invocation(self) -> None:
        extractor_owner = CancellationOwner()
        extractor_request = ExtractorRequest(_target(), SnapshotId("snapshot-one"), _fingerprint(), MediaType.EBOOK, (FileReference("library", "books/item.epub"),), _context(owner=extractor_owner))
        extractor_owner.cancel()
        self.assertEqual(SyntheticExtractor().extract(extractor_request).failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]

        reviewer_owner = CancellationOwner()
        review = _review_request()
        live_review = replace(review, context=_context(owner=reviewer_owner))
        reviewer_owner.cancel()
        self.assertEqual(SyntheticReviewer().review(live_review).failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]

    def test_write_grant_replay_and_tamper_guards(self) -> None:
        request = _write_request()
        writer = SyntheticWriter()
        self.assertFalse(writer.apply(request).value.replayed)  # type: ignore[union-attr]
        self.assertTrue(writer.apply(request).value.replayed)  # type: ignore[union-attr]
        altered = (MetadataMutation(MetadataField(FieldName.TITLE, ("Altered",))),)
        with self.assertRaises(ValueError):
            WriteRequest(request.target, altered, request.grant, request.idempotency_key, request.expected, request.context)
        expired_context = _context()
        with self.assertRaises(ValueError):
            _write_request(context=expired_context, grant_expiry=expired_context.observed_at)
        with self.assertRaises(ValueError):
            _write_request(context=_context(size=1))
        stale_writer = SyntheticWriter(_fingerprint("newer"))
        self.assertEqual(stale_writer.apply(request).failure.kind, FailureKind.CONFLICT)  # type: ignore[union-attr]
        other_key = IdempotencyKey.from_canonical_input(operation="other-write", target=request.target, value={"other": True})
        with self.assertRaises(ValueError):
            WriteRequest(request.target, request.mutations, replace(request.grant, idempotency_key=other_key), request.idempotency_key, request.expected, request.context)
        with self.assertRaises(ValueError):
            replace(request.grant, expected=ExpectedState(_fingerprint("other"), None))
        with self.assertRaises(ValueError):
            replace(request.grant, backup=replace(request.grant.backup, state=BackupState.NOT_READY))

    def test_write_live_preflight_blocks_delayed_cancel_and_expiry_before_io(self) -> None:
        owner = CancellationOwner()
        context = _context(owner=owner)
        cancelled = _write_request(context=context)
        owner.cancel()
        writer = SyntheticWriter()
        self.assertEqual(writer.apply(cancelled).failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        self.assertEqual(writer.receipts, {})

        clock = TimeOwner(datetime.now(timezone.utc))
        live = _context(now=clock.value, time_owner=clock, deadline_delta=timedelta(seconds=1))
        delayed = _write_request(context=live)
        clock.advance(timedelta(seconds=2))
        self.assertEqual(writer.apply(delayed).failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        self.assertEqual(writer.receipts, {})

        grant_clock = TimeOwner(datetime.now(timezone.utc))
        grant_context = _context(now=grant_clock.value, time_owner=grant_clock, deadline_delta=timedelta(minutes=2))
        expired_grant = _write_request(context=grant_context, grant_expiry=grant_clock.value + timedelta(seconds=1))
        grant_clock.advance(timedelta(seconds=2))
        self.assertEqual(writer.apply(expired_grant).failure.kind, FailureKind.AUTHORIZATION)  # type: ignore[union-attr]
        self.assertEqual(writer.receipts, {})

    def test_library_pagination_and_durable_repository_vocabulary(self) -> None:
        reader = SyntheticLibraryReader()
        # The shape is tested without constructing a credential-bearing connection.
        self.assertEqual(Page((), "next-page").next_cursor, "next-page")
        self.assertEqual(Page((), None).items, ())
        self.assertIs(RepositoryOutcome.ACCEPTED, RepositoryOutcome("accepted"))
        self.assertIs(JobHeartbeatRepository, DurableWorkRepository)

    def test_work_request_idempotency_is_target_scoped_not_kind_scoped(self) -> None:
        target = _target("item-one")
        other = _target("item-two")
        key = IdempotencyKey.from_canonical_input(operation="schedule", target=target, value={"kind": "scan"})
        request = WorkRequest("scan", key, target, _context())
        self.assertEqual(request.target, key.target)
        self.contract.require_unit_result(SyntheticScheduler().schedule(request))
        with self.assertRaises(ValueError):
            WorkRequest("scan", key, other, _context())

    def test_reviewer_exact_coverage_and_citations(self) -> None:
        request = _review_request()
        response = SyntheticReviewer().review(request).value
        self.assertEqual(response.decisions[0].proposal, request.proposals[0].id)  # type: ignore[union-attr]
        evidence = request.packet.details[0].reference.evidence
        with self.assertRaises(ValueError):
            ReviewerResponse(request, ())
        with self.assertRaises(ValueError):
            ReviewerResponse(request, (ReviewerDecision(request.proposals[0].id, ReviewerAction.APPROVE, SafeSummary("evidence supports candidate"), (EvidenceId("unknown"),)),))
        self.assertEqual(response.decisions[0].cited_evidence, (evidence,))  # type: ignore[union-attr]
        with self.assertRaises(ValueError):
            CurrentFieldState(FieldName.TITLE, CurrentFieldStateKind.LOADED)
        missing_reference = EvidenceReference(EvidenceId("evidence-missing"), request.packet.target, request.packet.target, EvidenceRelationship.SAME_TARGET)
        missing_proposal = replace(request.proposals[0], supporting_evidence=(missing_reference,))
        with self.assertRaises(ValueError):
            ReviewerRequest(request.packet, (missing_proposal,), request.current_fields, request.context)
        with self.assertRaises(ValueError):
            ReviewerRequest(request.packet, request.proposals, (), request.context)
        replacement = replace(request.proposals[0], current_value=FieldValue(("Existing",)), kind=ProposalKind.REPLACE_POPULATED)
        unavailable = (CurrentFieldState(FieldName.TITLE, CurrentFieldStateKind.UNAVAILABLE),)
        with self.assertRaises(ValueError):
            ReviewerRequest(request.packet, (replacement,), unavailable, request.context)

    def test_secret_and_provider_shapes_carry_no_cross_family_authority(self) -> None:
        result = SyntheticSecretResolver().resolve(SecretReference("providers/catalogue"), SecretScope.METADATA_PROVIDER)
        self.assertNotIn("providers/catalogue", repr(result.value))
        for forbidden in ("secret", "target", "endpoint", "files", "authorization", "writer", "repository"):
            self.assertNotIn(forbidden, ProviderQuery.__dataclass_fields__)
        for forbidden in ("secret", "files", "grant", "writer", "repository"):
            self.assertNotIn(forbidden, ReviewerRequest.__dataclass_fields__)

    def test_diagnostics_return_typed_public_safe_values(self) -> None:
        diagnostics = SyntheticDiagnostics()
        self.assertIn(Capability.METADATA_READ, diagnostics.capabilities().supported)
        self.assertIs(diagnostics.health(_context()).value.status, HealthStatus.HEALTHY)  # type: ignore[union-attr]
        self.assertIs(diagnostics.compatibility(_context()).value.status, CompatibilityStatus.COMPATIBLE)  # type: ignore[union-attr]
        unsupported = _failure(FailureKind.UNSUPPORTED, False)
        self.assertFalse(unsupported.retry.retryable)
        query = ProviderQuery(MediaType.EBOOK, None, (ProviderFact(ProviderFactCode.TITLE, ("Title",)),), SchemaVersion("provider-query-v1"))
        self.contract.require_failure_result(SyntheticUnsupportedProvider().lookup(query, PageRequest(None, 1), _context()), FailureKind.UNSUPPORTED)

    def test_safe_diagnostic_type_is_required_at_every_port_surface(self) -> None:
        request = _write_request()
        review = _review_request()
        evidence = review.packet.details[0].reference.evidence
        notification = Notification("review-requested", request.target, SafeSummary("notification queued"), request.idempotency_key, request.context)
        with self.assertRaises(ValueError):
            PortFailure(FailureKind.TIMEOUT, RetryAdvice(True), "token=secret")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            replace(request.grant.backup, summary="password=secret")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            replace(notification, summary="Cookie: session=secret")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ReviewerDecision(review.proposals[0].id, ReviewerAction.APPROVE, "client_secret=secret", (evidence,))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
