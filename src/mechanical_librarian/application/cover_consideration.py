"""Immutable owner-facing cover consideration over one current review case."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, cast

from mechanical_librarian.application.contained_reviewer_contract import (
    is_cover_profile_contract_reason,
)
from mechanical_librarian.application.cover_identity import (
    CLASSIFIER_REFERENCE,
    classify_cover_identity,
)
from mechanical_librarian.application.cover_identity import (
    SCHEMA_VERSION as IDENTITY_SCHEMA_VERSION,
)
from mechanical_librarian.application.cover_observation import (
    INSTRUCTION,
    OUTPUT_SCHEMA_VERSION,
    TAXONOMY_REFERENCE,
    observe_cover_packet,
)
from mechanical_librarian.application.cover_observation import (
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
)
from mechanical_librarian.application.cover_packet_semantics import (
    canonical_packet_json,
)
from mechanical_librarian.application.cover_packets import (
    SCHEMA_VERSION as PACKET_SCHEMA_VERSION,
)
from mechanical_librarian.application.cover_packets import (
    PreparedCoverPacket,
    cover_source_reference,
    prepare_cover_packet,
)
from mechanical_librarian.application.cover_policy import (
    POLICY_SCHEMA_VERSION,
    CoverPolicy,
    adjudicate_cover_policy,
)
from mechanical_librarian.application.cover_policy import (
    SCHEMA_VERSION as POLICY_REPORT_SCHEMA_VERSION,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.deterministic_cover_election import (
    CoverElectionCandidate,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFieldState,
)

SCHEMA_VERSION = "cover-consideration-v2"
_IDENTITY_ELIGIBLE = frozenset({"verified_manifestation_match", "verified_work_match"})


class CoverConsiderationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CoverConsiderationStore(Protocol):
    def current_cover_consideration(
        self, review_case_id: str, replay_digest: str
    ) -> str | None: ...

    def record_cover_consideration(
        self,
        document: str,
        *,
        review_case_id: str,
        report_id: str,
        replay_digest: str,
        supersedes_id: str | None,
        occurred_at: int,
        preparation_kind: str = "automatic_prepared",
    ) -> str: ...

    def current_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None: ...

    def latest_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None: ...

    def cover_consideration_history(
        self, review_case_id: str, limit: int = 50
    ) -> tuple[dict[str, object], ...]: ...

    def record_cover_consideration_outcome(
        self,
        consideration_id: str,
        kind: str,
        occurred_at: int,
        decision_id: str | None = None,
    ) -> None: ...


ReviewRunner = Callable[
    [bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]], str
]


@dataclass(frozen=True, slots=True)
class _ReviewerEvidence:
    replay_digest: str
    observation_document: str | None
    reviewer_status: str
    reviewer_failure: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class _ObservationRequest:
    replay_digest: str
    packet_payload: bytes | None
    image_payloads: tuple[tuple[str, str, bytes], ...]


@dataclass(frozen=True, slots=True)
class CoverConsideration:
    document: str

    @property
    def data(self) -> dict[str, object]:
        value = json.loads(self.document)
        if type(value) is not dict:
            raise CoverConsiderationError("persistence")
        return value

    @property
    def consideration_id(self) -> str:
        return str(self.data["consideration_id"])


class CoverConsiderationService:
    """Build, persist, project, and authorize one visual owner decision."""

    def __init__(
        self,
        store: CoverConsiderationStore,
        *,
        read_preview: Callable[[str, str], bytes],
        read_preview_digest: Callable[[str], bytes],
        clock: Callable[[], int],
        policy: CoverPolicy = CoverPolicy(),
        reviewer_profile: bytes | None = None,
        reviewer: ReviewRunner | None = None,
        reviewer_unavailable_reason: str | None = None,
        bind_policy: Callable[[str, str], None] | None = None,
        provider_fetching: Callable[[MetadataReport], bool] | None = None,
    ) -> None:
        self._store = store
        self._read_preview = read_preview
        self._read_preview_digest = read_preview_digest
        self._clock = clock
        self._policy = policy
        self._reviewer_profile = reviewer_profile
        self._reviewer = reviewer
        if not is_cover_profile_contract_reason(reviewer_unavailable_reason):
            raise ValueError("invalid reviewer unavailable reason")
        self._reviewer_unavailable_reason = reviewer_unavailable_reason
        self._bind_policy = bind_policy or (lambda _policy, _replay: None)
        self._provider_fetching = provider_fetching or (lambda _report: False)

    def update_policy(self, policy: CoverPolicy) -> None:
        """Replace the live preference snapshot and its authority references."""
        if type(policy) is not CoverPolicy:
            raise CoverConsiderationError("policy")
        previous = self._policy
        self._policy = policy
        try:
            self._bind_policy(policy.reference, self.replay_reference)
        except Exception:
            self._policy = previous
            raise

    @contextmanager
    def policy_update(self, policy: CoverPolicy) -> Iterator[None]:
        """Keep one live policy change only when its enclosing store commit succeeds."""
        previous = self._policy
        self.update_policy(policy)
        try:
            yield
        except BaseException:
            self.update_policy(previous)
            raise

    @property
    def replay_reference(self) -> str:
        value: dict[str, object] = {
            "policy": self._policy.document,
            "contracts": {
                "classifier_reference": CLASSIFIER_REFERENCE,
                "identity_schema": IDENTITY_SCHEMA_VERSION,
                "instruction_sha256": hashlib.sha256(
                    INSTRUCTION.encode("utf-8")
                ).hexdigest(),
                "observation_schema": OBSERVATION_SCHEMA_VERSION,
                "observer_output_schema": OUTPUT_SCHEMA_VERSION,
                "packet_schema": PACKET_SCHEMA_VERSION,
                "policy_report_schema": POLICY_REPORT_SCHEMA_VERSION,
                "policy_schema": POLICY_SCHEMA_VERSION,
                "taxonomy_reference": TAXONOMY_REFERENCE,
            },
            "profile_sha256": (
                None
                if self._reviewer_profile is None
                else hashlib.sha256(self._reviewer_profile).hexdigest()
            ),
            "schema_version": SCHEMA_VERSION,
        }
        if self._reviewer_unavailable_reason is not None:
            value["reviewer_unavailable_reason"] = self._reviewer_unavailable_reason
        identity = canonical_packet_json(value)
        return "cover_replay_" + hashlib.sha256(identity.encode()).hexdigest()

    def _replay_digest(
        self, review_case_id: str, report_id: str, *, provider_fetching: bool
    ) -> str:
        return hashlib.sha256(
            canonical_packet_json(
                {
                    "replay_reference": self.replay_reference,
                    "report": report_id,
                    "review_case_id": review_case_id,
                    "provider_fetching": provider_fetching,
                }
            ).encode()
        ).hexdigest()

    def existing(
        self, review_case_id: str, report_document: str
    ) -> dict[str, object] | None:
        report = MetadataReport.from_json(report_document)
        provider_fetching = self._provider_fetching(report)
        document = self._store.current_cover_consideration(
            review_case_id,
            self._replay_digest(
                review_case_id,
                report.report_id,
                provider_fetching=provider_fetching,
            ),
        )
        return None if document is None else self._project(document, current=True)

    def latest_id(self, review_case_id: str) -> str | None:
        document = self._store.latest_cover_consideration_document(review_case_id)
        return (
            None if document is None else str(json.loads(document)["consideration_id"])
        )

    def prepare_observation(
        self, review_case_id: str, report_document: str
    ) -> _ObservationRequest:
        """Capture bounded observer inputs while mutable product state is serialized."""
        report = MetadataReport.from_json(report_document)
        replay_digest = self._replay_digest(
            review_case_id,
            report.report_id,
            provider_fetching=self._provider_fetching(report),
        )
        try:
            packet = prepare_cover_packet(report_document, self._read_preview)
        except ValueError as error:
            if str(error) != "cover packet has no retrieved previews":
                raise
            packet = None
        if packet is None:
            return _ObservationRequest(replay_digest, None, ())
        return _ObservationRequest(
            replay_digest,
            packet.document.encode(),
            tuple(
                (image.reference, image.digest, image.payload)
                for image in packet.images
            ),
        )

    def observe(self, request: _ObservationRequest) -> _ReviewerEvidence:
        """Run optional external advice over an immutable captured request."""
        if request.packet_payload is None:
            return _ReviewerEvidence(request.replay_digest, None, "unavailable", None)
        return self._observe_payloads(
            request.replay_digest,
            request.packet_payload,
            request.image_payloads,
        )

    def _observe_packet(
        self, replay_digest: str, packet: PreparedCoverPacket
    ) -> _ReviewerEvidence:
        return self._observe_payloads(
            replay_digest,
            packet.document.encode(),
            tuple(
                (image.reference, image.digest, image.payload)
                for image in packet.images
            ),
        )

    def _observe_payloads(
        self,
        replay_digest: str,
        packet_payload: bytes,
        image_payloads: tuple[tuple[str, str, bytes], ...],
    ) -> _ReviewerEvidence:
        if self._reviewer_profile is None or self._reviewer is None:
            return _ReviewerEvidence(replay_digest, None, "unavailable", None)
        reviewer = self._reviewer
        dispatched = False
        observed = False

        def dispatch(
            profile: bytes,
            instruction: bytes,
            packet: bytes,
            schema: bytes,
            images: tuple[tuple[str, str, bytes], ...],
        ) -> str:
            # Marks the boundary between this layer's own projection and the
            # contained process, so the handler below can tell which of the two
            # failed. Set before the call, so a reviewer that raises counts as
            # dispatched.
            nonlocal dispatched
            dispatched = True
            return reviewer(profile, instruction, packet, schema, images)

        def settle() -> None:
            # Marks the far edge of the contained process's contribution: the
            # result document is back and has been validated against the
            # request, so nothing after this point is an outcome of the
            # reviewer.
            nonlocal observed
            observed = True

        try:
            observation_document = observe_cover_packet(
                self._reviewer_profile,
                packet_payload,
                image_payloads,
                dispatch,
                observed=settle,
            ).document
            return _ReviewerEvidence(
                replay_digest, observation_document, "completed", None
            )
        except Exception as error:
            if not dispatched or observed:
                # Two ends of the contained process, and this layer owns both.
                # Before dispatch, ``observe_cover_packet`` binds the packet,
                # loads it and builds the request schema, all from inputs we
                # produced. After ``observed``, it assembles the report --
                # binding rows to candidates from our own packet, sorting,
                # hashing the instruction and schema, and canonicalising -- from
                # inputs we produced and a result already validated. A fault at
                # either end is a fault in our own projection, not an outcome of
                # the contained process, so it must surface instead of being
                # recorded as reviewer failure evidence the owner is asked to
                # read.
                raise
            # Between those two marks this is a last resort, and it narrows no
            # further. The reviewer is a contained process reached through an
            # injected profile, so its failures are transport, container, and
            # protocol faults it does not declare, and a result document that
            # will not parse or validate against the request is the same
            # reviewable outcome: no observation, recorded as failure evidence
            # the owner can see. Nothing is discarded -- the attempts and the
            # failure code carried by the error are read off it and persisted
            # below.
            attempts = getattr(error, "attempts", ())
            failure: dict[str, object] = {
                "attempts": [
                    {
                        "diagnostic_bytes": getattr(item, "diagnostic_bytes", 0),
                        "diagnostic_sha256": getattr(item, "diagnostic_sha256", None),
                        "dispatched": getattr(item, "dispatched", False),
                        "failure_code": getattr(item, "failure_code", "runtime"),
                    }
                    for item in attempts
                ],
                "code": str(getattr(error, "code", "runtime")),
            }
            return _ReviewerEvidence(replay_digest, None, "failed", failure)

    def ensure(
        self,
        review_case_id: str,
        report_document: str,
        cover_proposal: dict[str, object],
        *,
        retry: bool = False,
        reviewer_evidence: _ReviewerEvidence | None = None,
    ) -> dict[str, object]:
        report = MetadataReport.from_json(report_document)
        provider_fetching = self._provider_fetching(report)
        replay_digest = self._replay_digest(
            review_case_id,
            report.report_id,
            provider_fetching=provider_fetching,
        )
        if (
            reviewer_evidence is not None
            and reviewer_evidence.replay_digest != replay_digest
        ):
            raise CoverConsiderationError("stale")
        if not retry:
            existing = self._store.current_cover_consideration(
                review_case_id, replay_digest
            )
            if existing is not None:
                return self._project(existing, current=True)

        previous = self._store.latest_cover_consideration_document(review_case_id)
        previous_id = None
        if previous is not None:
            previous_value = json.loads(previous)
            previous_id = str(previous_value["consideration_id"])
        document = self._build(
            review_case_id,
            report,
            report_document,
            cover_proposal,
            replay_digest,
            previous_id,
            reviewer_evidence,
            provider_fetching,
        )
        persisted = self._store.record_cover_consideration(
            document,
            review_case_id=review_case_id,
            report_id=report.report_id,
            replay_digest=replay_digest,
            supersedes_id=previous_id,
            occurred_at=self._clock(),
            preparation_kind=(
                "owner_retry_prepared" if retry else "automatic_prepared"
            ),
        )
        return self._project(persisted, current=True)

    def _build(
        self,
        review_case_id: str,
        report: MetadataReport,
        report_document: str,
        cover_proposal: dict[str, object],
        replay_digest: str,
        supersedes_id: str | None,
        reviewer_evidence: _ReviewerEvidence | None,
        provider_fetching: bool,
    ) -> str:
        packet: PreparedCoverPacket | None
        try:
            packet = prepare_cover_packet(report_document, self._read_preview)
        except ValueError as error:
            if str(error) != "cover packet has no retrieved previews":
                raise
            packet = None

        evidence = reviewer_evidence
        if packet is not None:
            image_payloads = tuple(
                (image.reference, image.digest, image.payload)
                for image in packet.images
            )
            identity_document = classify_cover_identity(
                report_document,
                packet.document,
                image_payloads,
                read_preview=self._read_preview,
            ).document
            if evidence is None:
                evidence = self._observe_packet(replay_digest, packet)
            observation_document = evidence.observation_document
            reviewer_status = evidence.reviewer_status
            reviewer_failure = evidence.reviewer_failure
            policy_report_document = adjudicate_cover_policy(
                report_document,
                packet.document,
                image_payloads,
                observation_document,
                self._policy,
                profile_payload=self._reviewer_profile,
                read_preview=self._read_preview,
                provider_fetching=provider_fetching,
            ).document
            candidates = self._candidates(
                report,
                packet.document,
                identity_document,
                observation_document,
                policy_report_document,
            )
            packet_document = packet.document
        else:
            observation_document = None
            reviewer_status = "unavailable"
            reviewer_failure = None
            identity_document = None
            policy_report_document = None
            packet_document = None
            candidates = []

        incumbent = next((item for item in candidates if item["incumbent"]), None)
        target_cover = next(
            field
            for field in report.target.fields
            if field.destination is TargetDestination.COVER
        )
        incumbent_state = (
            "available"
            if incumbent is not None
            else "unavailable"
            if target_cover.state is TargetFieldState.VALUE
            else "absent"
        )
        body: dict[str, object] = {
            "candidates": candidates,
            "created_at": self._clock(),
            "identity_document": identity_document,
            "incumbent_digest": (
                None if incumbent is None else incumbent["original_sha256"]
            ),
            "incumbent": {
                "reference": None if incumbent is None else incumbent["reference"],
                "state": incumbent_state,
            },
            "observation_document": observation_document,
            "omissions": self._omissions(report, packet_document),
            "packet_document": packet_document,
            "policy_document": self._policy.document,
            "policy_report_document": policy_report_document,
            "policy_reference": self._policy.reference,
            "provider_fetching": provider_fetching,
            "proposal_id": str(cover_proposal["proposal_id"]),
            "report_document": report_document,
            "report_id": report.report_id,
            "replay_digest": replay_digest,
            "replay_reference": self.replay_reference,
            "review_case_id": review_case_id,
            "reviewer_failure": reviewer_failure,
            "reviewer_status": reviewer_status,
            "schema_version": SCHEMA_VERSION,
            "supersedes_id": supersedes_id,
            "target_fingerprint": report.target.fingerprint,
        }
        identity = dict(body)
        identity.pop("created_at")
        consideration_id = (
            "cover_consideration_"
            + hashlib.sha256(canonical_packet_json(identity).encode()).hexdigest()
        )
        return canonical_packet_json({"consideration_id": consideration_id, **body})

    def _candidates(
        self,
        report: MetadataReport,
        packet_document: str,
        identity_document: str,
        observation_document: str | None,
        policy_document: str,
    ) -> list[dict[str, object]]:
        packet = json.loads(packet_document)
        identity = json.loads(identity_document)
        policy = json.loads(policy_document)
        observation = (
            None if observation_document is None else json.loads(observation_document)
        )
        identity_rows = {
            row["candidate_reference"]: row for row in identity["candidates"]
        }
        observation_rows = (
            {}
            if observation is None
            or observation.get("schema_version") != OBSERVATION_SCHEMA_VERSION
            else {
                row["candidate_reference"]: row for row in observation["observations"]
            }
        )
        acceptable = set(policy["acceptable_candidate_references"])
        ranked_references = list(policy["acceptable_candidate_references"])
        recommendation = policy["recommended_candidate_reference"]
        source_by_reference = {
            cover_source_reference(report.report_id, source.source_id): source
            for source in report.cover_sources
            if source.state is CoverSourceState.RETRIEVED
            and source.artifact is not None
        }
        result: list[dict[str, object]] = []
        for ordinal, candidate in enumerate(packet["candidates"]):
            reference = candidate["reference"]
            sources = [
                source_by_reference[binding["reference"]]
                for binding in candidate["source_bindings"]
            ]
            incumbent_sources = [
                source for source in sources if source.kind is CoverSourceKind.ABS
            ]
            if candidate["incumbent"]:
                if len(incumbent_sources) != 1:
                    raise CoverConsiderationError("incumbent_binding")
                selected = incumbent_sources[0]
            else:
                # ``source_by_reference`` only retains RETRIEVED sources whose
                # artifact is not None.
                selected = max(
                    sources,
                    key=lambda source: (
                        cast("CoverArtifact", source.artifact).width
                        * cast("CoverArtifact", source.artifact).height,
                        cast("CoverArtifact", source.artifact).width,
                        cast("CoverArtifact", source.artifact).height,
                        cast("CoverArtifact", source.artifact).artifact_id,
                    ),
                )
            artifact = selected.artifact
            assert artifact is not None
            identity_row = identity_rows[reference]
            identity_eligible = identity_row["outcome"] in _IDENTITY_ELIGIBLE
            observation_row = observation_rows.get(reference)
            selectable = identity_eligible and candidate["incumbent"] is False
            rank_key = (
                CoverElectionCandidate(
                    reference=reference,
                    identity=str(identity_row["outcome"]),
                    identity_citations=tuple(identity_row["evidence_references"]),
                    source_citations=tuple(
                        binding["reference"] for binding in candidate["source_bindings"]
                    ),
                    incumbent=bool(candidate["incumbent"]),
                    width=artifact.width,
                    height=artifact.height,
                    packet_ordinal=ordinal,
                ).rank_key
                if identity_eligible
                else None
            )
            state = (
                "excluded"
                if not selectable
                else "recommended"
                if reference == recommendation
                else "acceptable"
                if reference in acceptable
                else "available"
            )
            result.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "height": artifact.height,
                    "identity": identity_row,
                    "deterministic_citations": list(
                        dict.fromkeys(
                            [*identity_row["evidence_references"]]
                            + [
                                binding["reference"]
                                for binding in candidate["source_bindings"]
                            ]
                        )
                    ),
                    "deterministic_rank": (
                        ranked_references.index(reference) + 1
                        if reference in acceptable
                        else None
                    ),
                    "deterministic_rank_key": (
                        None if rank_key is None else list(rank_key)
                    ),
                    "incumbent": bool(candidate["incumbent"]),
                    "match_state": {
                        "verified_manifestation_match": "same_edition",
                        "verified_work_match": "same_book",
                    }.get(str(identity_row["outcome"]), "match_not_confirmed"),
                    "observation": observation_row,
                    "original_sha256": artifact.original_digest,
                    "ordinal": ordinal,
                    "preview_sha256": artifact.preview_digest,
                    "reference": reference,
                    "selectable": selectable,
                    "source_kinds": sorted({source.kind.value for source in sources}),
                    "state": state,
                    "transport_sha256": candidate["transport_sha256"],
                    "width": artifact.width,
                }
            )
        return result

    @staticmethod
    def _omissions(
        report: MetadataReport, packet_document: str | None
    ) -> list[dict[str, object]]:
        if packet_document is not None:
            return list(json.loads(packet_document)["omissions"])
        return [
            {
                "reason": "source_" + source.state.value,
                "reference": cover_source_reference(report.report_id, source.source_id),
            }
            for source in report.cover_sources
        ]

    def _project(self, document: str, *, current: bool) -> dict[str, object]:
        value = json.loads(document)
        policy = (
            None
            if value["policy_report_document"] is None
            else json.loads(value["policy_report_document"])
        )
        candidates = [dict(item) for item in value["candidates"]]
        return {
            "candidates": candidates,
            "choice_contexts": _cover_choice_contexts(value, candidates, policy),
            "consideration_id": value["consideration_id"],
            "current": current,
            "incumbent": dict(value["incumbent"]),
            "omissions": list(value["omissions"]),
            "policy": policy,
            "policy_preferences": json.loads(value["policy_document"]),
            "policy_reference": value["policy_reference"],
            "provider_fetching": value["provider_fetching"],
            "ready": policy is not None and policy["status"] == "recommended",
            "reviewer_status": value["reviewer_status"],
            "reviewer_unavailable_reason": (
                self._reviewer_unavailable_reason
                if current and value["reviewer_status"] == "unavailable"
                else None
            ),
            "schema_version": SCHEMA_VERSION,
            "supersedes_id": value["supersedes_id"],
            "target_fingerprint": value["target_fingerprint"],
        }

    def image(
        self, review_case_id: str, consideration_id: str, reference: str
    ) -> bytes:
        document = self._store.current_cover_consideration_document(review_case_id)
        if document is None:
            raise CoverConsiderationError("not_found")
        value = json.loads(document)
        if value["consideration_id"] != consideration_id or not self._document_current(
            value
        ):
            raise CoverConsiderationError("stale")
        candidate = next(
            (item for item in value["candidates"] if item["reference"] == reference),
            None,
        )
        if candidate is None:
            raise CoverConsiderationError("not_found")
        try:
            payload = self._read_preview_digest(str(candidate["preview_sha256"]))
        except Exception as error:
            # An injected artifact reader over the cover store, with no declared
            # error type. A preview that could not be read is unavailable
            # whatever stopped it, and the digest is still verified below, so
            # breadth here never lets an unverified payload through.
            raise CoverConsiderationError("unavailable") from error
        if hashlib.sha256(payload).hexdigest() != candidate["preview_sha256"]:
            raise CoverConsiderationError("unavailable")
        return payload

    def current(self, review_case_id: str) -> dict[str, object] | None:
        document = self._store.current_cover_consideration_document(review_case_id)
        return self.project(document)

    def project(self, document: str | None) -> dict[str, object] | None:
        """Project a consideration captured inside its caller's read snapshot."""
        if document is None:
            return None
        value = json.loads(document)
        return (
            None
            if not self._document_current(value)
            else self._project(document, current=True)
        )

    def _document_current(self, value: dict[str, object]) -> bool:
        review_case_id = value.get("review_case_id")
        report_id = value.get("report_id")
        return (
            type(review_case_id) is str
            and type(report_id) is str
            and value.get("replay_reference") == self.replay_reference
            and type(value.get("provider_fetching")) is bool
            and value.get("replay_digest")
            == self._replay_digest(
                review_case_id,
                report_id,
                provider_fetching=bool(value["provider_fetching"]),
            )
            and value["provider_fetching"]
            == self._provider_fetching(
                MetadataReport.from_json(str(value.get("report_document")))
            )
        )

    def project_historical(self, document: str) -> dict[str, object]:
        """Project immutable consideration evidence without granting current authority."""
        return self._project(document, current=False)

    def validate_decision(
        self,
        review_case_id: str,
        consideration_id: str,
        outcome: str,
        selected_value: dict[str, object] | None,
    ) -> None:
        document = self._store.current_cover_consideration_document(review_case_id)
        if document is None:
            raise CoverConsiderationError("stale")
        value = json.loads(document)
        if value["consideration_id"] != consideration_id or not self._document_current(
            value
        ):
            raise CoverConsiderationError("stale")
        if outcome == "accept":
            selected = next(
                (
                    candidate
                    for candidate in value["candidates"]
                    if selected_value
                    == {"kind": "cover", "value": candidate["artifact_id"]}
                ),
                None,
            )
            if selected is None or selected["incumbent"] or not selected["selectable"]:
                raise CoverConsiderationError("invalid_choice")
        elif outcome not in {"keep_current", "reject"} or selected_value is not None:
            raise CoverConsiderationError("invalid_choice")

    def record_outcome(
        self, consideration_id: str, outcome: str, decision_id: str
    ) -> None:
        kind = {
            "accept": "selected",
            "keep_current": "kept_incumbent",
            "reject": "no_action",
        }[outcome]
        self._store.record_cover_consideration_outcome(
            consideration_id, kind, self._clock(), decision_id
        )

    def history(self, review_case_id: str) -> tuple[dict[str, object], ...]:
        items = []
        for raw in self._store.cover_consideration_history(review_case_id):
            item = dict(raw)
            document = item.pop("consideration_document", None)
            if type(document) is not str:
                raise CoverConsiderationError("history")
            item["consideration"] = self.project_historical(document)
            items.append(item)
        return tuple(items)


def _cover_choice_contexts(
    value: dict[str, object],
    candidates: list[dict[str, object]],
    policy: dict[str, object] | None,
) -> list[dict[str, object]]:
    """Expose exact retained cover binding provenance without identifiers."""
    report = MetadataReport.from_json(str(value["report_document"]))
    packet = (
        json.loads(str(value["packet_document"]))
        if value["packet_document"] is not None
        else {"candidates": []}
    )
    bindings = {
        item["reference"]: item["source_bindings"] for item in packet["candidates"]
    }
    sources = {
        cover_source_reference(report.report_id, source.source_id): source
        for source in report.cover_sources
    }
    recommended = (
        None
        if policy is None or policy.get("status") != "recommended"
        else policy.get("recommended_candidate_reference")
    )
    # A policy report always carries a list of acceptable candidate references.
    acceptable = (
        set()
        if policy is None
        else set(
            cast("list[object]", policy.get("acceptable_candidate_references", []))
        )
    )

    def evidence(reference: str, incumbent: bool) -> list[dict[str, str]]:
        rows = []
        for binding in bindings.get(reference, []):
            source = sources.get(binding.get("reference"))
            if source is None:
                continue
            label, role, observation = {
                CoverSourceKind.ABS: (
                    "Local library record",
                    "local_presence",
                    "Local library cover retained.",
                ),
                CoverSourceKind.EPUB: (
                    "Local EPUB package",
                    "local_presence",
                    "Local EPUB cover retained.",
                ),
                CoverSourceKind.HARDCOVER: (
                    "Hardcover",
                    "external_corroboration",
                    "Retained catalogue cover supports this choice.",
                ),
                CoverSourceKind.OPEN_LIBRARY: (
                    "Open Library",
                    "external_corroboration",
                    "Retained catalogue cover supports this choice.",
                ),
            }.get(
                source.kind,
                (
                    "Provider evidence",
                    "external_corroboration",
                    "Retained cover evidence supports this choice.",
                ),
            )
            rows.append({"source": label, "role": role, "observation": observation})
        return rows or (
            [
                {
                    "source": "Local library record",
                    "role": "local_presence",
                    "observation": "Current cover preview is unavailable; local presence is retained.",
                }
            ]
            if incumbent
            else []
        )

    incumbent = next(
        (item for item in candidates if item.get("incumbent") is True), None
    )
    current_reference = None if incumbent is None else incumbent.get("reference")
    current_recommended = (
        current_reference == recommended
        and current_reference in acceptable
        and incumbent is not None
        and incumbent.get("selectable") is True
    )
    contexts: list[dict[str, object]] = [
        {
            "alternative_id": None,
            "selection": "current",
            "evidence": evidence(str(current_reference), True)
            if type(current_reference) is str
            else evidence("", True),
            "limitations": (
                ["Preview unavailable for this retained cover."]
                if cast("dict[str, object]", value["incumbent"]).get("state")
                == "unavailable"
                else []
            ),
            "rationale": "Keep the cover currently held by the local library.",
            "recommendation": "recommended"
            if current_recommended
            else "no_recommendation",
            "recommendation_reason": "Authoritative cover policy recommends this retained cover."
            if current_recommended
            else "No recommendation retained.",
        }
    ]
    for candidate in candidates:
        reference = candidate.get("reference")
        if (
            candidate.get("incumbent") is True
            or candidate.get("selectable") is not True
            or type(reference) is not str
        ):
            continue
        is_recommended = reference == recommended and reference in acceptable
        contexts.append(
            {
                "alternative_id": reference,
                "selection": "proposed",
                "evidence": evidence(reference, False),
                "limitations": [],
                "rationale": "This retained cover candidate is selectable for owner review.",
                "recommendation": "recommended"
                if is_recommended
                else "no_recommendation",
                "recommendation_reason": "Authoritative cover policy recommends this retained cover."
                if is_recommended
                else "No recommendation retained.",
            }
        )
    return contexts


__all__ = [
    "CoverConsideration",
    "CoverConsiderationError",
    "CoverConsiderationService",
    "CoverConsiderationStore",
    "SCHEMA_VERSION",
]
