"""Immutable owner-facing cover consideration over one current review case."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.application.cover_identity import (
    CLASSIFIER_REFERENCE,
    SCHEMA_VERSION as IDENTITY_SCHEMA_VERSION,
    classify_cover_identity,
)
from mechanical_librarian.application.cover_observation import (
    INSTRUCTION,
    OUTPUT_SCHEMA_VERSION,
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
    TAXONOMY_REFERENCE,
    observe_cover_packet,
)
from mechanical_librarian.application.cover_packets import (
    PreparedCoverPacket,
    SCHEMA_VERSION as PACKET_SCHEMA_VERSION,
    cover_source_reference,
    prepare_cover_packet,
)
from mechanical_librarian.application.cover_policy import (
    POLICY_SCHEMA_VERSION,
    SCHEMA_VERSION as POLICY_REPORT_SCHEMA_VERSION,
    CoverPolicy,
    adjudicate_cover_policy,
)
from mechanical_librarian.application.cover_packet_semantics import canonical_packet_json
from mechanical_librarian.domain.cover_evidence import CoverSourceKind, CoverSourceState
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFieldState,
)


SCHEMA_VERSION = "cover-consideration-v1"
_IDENTITY_ELIGIBLE = frozenset(
    {"plausible_match", "verified_manifestation_match", "verified_work_match"}
)


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
    ) -> str: ...

    def current_cover_consideration_document(self, review_case_id: str) -> str | None: ...

    def latest_cover_consideration_document(self, review_case_id: str) -> str | None: ...

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


ReviewRunner = Callable[[bytes, bytes, bytes, bytes, tuple[tuple[str, str, bytes], ...]], str]


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
    ) -> None:
        self._store = store
        self._read_preview = read_preview
        self._read_preview_digest = read_preview_digest
        self._clock = clock
        self._policy = policy
        self._reviewer_profile = reviewer_profile
        self._reviewer = reviewer

    def ensure(
        self,
        review_case_id: str,
        report_document: str,
        cover_proposal: dict[str, object],
        *,
        retry: bool = False,
    ) -> dict[str, object]:
        report = MetadataReport.from_json(report_document)
        replay_digest = hashlib.sha256(
            canonical_packet_json(
                {
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
                    "report": report.report_id,
                    "review_case_id": review_case_id,
                    "schema_version": SCHEMA_VERSION,
                }
            ).encode()
        ).hexdigest()
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
        )
        persisted = self._store.record_cover_consideration(
            document,
            review_case_id=review_case_id,
            report_id=report.report_id,
            replay_digest=replay_digest,
            supersedes_id=previous_id,
            occurred_at=self._clock(),
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
    ) -> str:
        packet: PreparedCoverPacket | None
        try:
            packet = prepare_cover_packet(report_document, self._read_preview)
        except ValueError as error:
            if str(error) != "cover packet has no retrieved previews":
                raise
            packet = None

        observation_document: str | None = None
        reviewer_status = "unavailable"
        reviewer_failure: dict[str, object] | None = None
        if packet is not None:
            image_payloads = tuple(
                (image.reference, image.digest, image.payload) for image in packet.images
            )
            identity_document = classify_cover_identity(
                report_document,
                packet.document,
                image_payloads,
                read_preview=self._read_preview,
            ).document
            if self._reviewer_profile is not None and self._reviewer is not None:
                try:
                    observation_document = observe_cover_packet(
                        self._reviewer_profile,
                        packet.document.encode(),
                        image_payloads,
                        self._reviewer,
                    ).document
                    reviewer_status = "completed"
                except Exception as error:
                    reviewer_status = "failed"
                    attempts = getattr(error, "attempts", ())
                    reviewer_failure = {
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
            policy_report_document = adjudicate_cover_policy(
                report_document,
                packet.document,
                image_payloads,
                observation_document,
                self._policy,
                profile_payload=self._reviewer_profile,
                read_preview=self._read_preview,
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
            "proposal_id": str(cover_proposal["proposal_id"]),
            "report_document": report_document,
            "report_id": report.report_id,
            "replay_digest": replay_digest,
            "review_case_id": review_case_id,
            "reviewer_failure": reviewer_failure,
            "reviewer_status": reviewer_status,
            "schema_version": SCHEMA_VERSION,
            "supersedes_id": supersedes_id,
            "target_fingerprint": report.target.fingerprint,
        }
        identity = dict(body)
        identity.pop("created_at")
        consideration_id = "cover_consideration_" + hashlib.sha256(
            canonical_packet_json(identity).encode()
        ).hexdigest()
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
        observation = None if observation_document is None else json.loads(observation_document)
        identity_rows = {
            row["candidate_reference"]: row for row in identity["candidates"]
        }
        observation_rows = (
            {}
            if observation is None
            else {row["candidate_reference"]: row for row in observation["observations"]}
        )
        acceptable = set(policy["acceptable_candidate_references"])
        recommendation = policy["recommended_candidate_reference"]
        source_by_reference = {
            cover_source_reference(report.report_id, source.source_id): source
            for source in report.cover_sources
            if source.state is CoverSourceState.RETRIEVED and source.artifact is not None
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
                selected = max(
                    sources,
                    key=lambda source: (
                        source.artifact.width * source.artifact.height,
                        source.artifact.width,
                        source.artifact.height,
                        source.artifact.artifact_id,
                    ),
                )
            artifact = selected.artifact
            assert artifact is not None
            identity_row = identity_rows[reference]
            identity_eligible = identity_row["outcome"] in _IDENTITY_ELIGIBLE
            observation_row = observation_rows.get(reference)
            definitely_excluded = False
            if observation_row is not None:
                definitely_excluded = bool(
                    self._policy.avoid_promotional_text
                    and observation_row["promotional_text"] == "present"
                    or self._policy.avoid_tie_in_branding
                    and observation_row["tie_in_branding"] == "present"
                )
            selectable = identity_eligible and not definitely_excluded
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
                    "incumbent": bool(candidate["incumbent"]),
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
    def _omissions(report: MetadataReport, packet_document: str | None) -> list[dict[str, object]]:
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
        return {
            "candidates": [dict(item) for item in value["candidates"]],
            "consideration_id": value["consideration_id"],
            "current": current,
            "incumbent": dict(value["incumbent"]),
            "omissions": list(value["omissions"]),
            "policy": policy,
            "policy_preferences": json.loads(value["policy_document"]),
            "policy_reference": value["policy_reference"],
            "reviewer_status": value["reviewer_status"],
            "schema_version": SCHEMA_VERSION,
            "supersedes_id": value["supersedes_id"],
            "target_fingerprint": value["target_fingerprint"],
        }

    def image(self, review_case_id: str, consideration_id: str, reference: str) -> bytes:
        document = self._store.current_cover_consideration_document(review_case_id)
        if document is None:
            raise CoverConsiderationError("not_found")
        value = json.loads(document)
        if value["consideration_id"] != consideration_id:
            raise CoverConsiderationError("stale")
        candidate = next(
            (item for item in value["candidates"] if item["reference"] == reference),
            None,
        )
        if candidate is None:
            raise CoverConsiderationError("not_found")
        payload = self._read_preview_digest(str(candidate["preview_sha256"]))
        if hashlib.sha256(payload).hexdigest() != candidate["preview_sha256"]:
            raise CoverConsiderationError("unavailable")
        return payload

    def current(self, review_case_id: str) -> dict[str, object] | None:
        document = self._store.current_cover_consideration_document(review_case_id)
        return None if document is None else self._project(document, current=True)

    def project(self, document: str | None) -> dict[str, object] | None:
        """Project a consideration captured inside its caller's read snapshot."""
        return None if document is None else self._project(document, current=True)

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
        if value["consideration_id"] != consideration_id:
            raise CoverConsiderationError("stale")
        if outcome == "accept":
            selected = next(
                (
                    candidate
                    for candidate in value["candidates"]
                    if selected_value == {"kind": "cover", "value": candidate["artifact_id"]}
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
        return self._store.cover_consideration_history(review_case_id)


__all__ = [
    "CoverConsideration",
    "CoverConsiderationError",
    "CoverConsiderationService",
    "CoverConsiderationStore",
    "SCHEMA_VERSION",
]
