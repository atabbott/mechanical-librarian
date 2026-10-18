"""Immutable owner-facing cover consideration over one current review case."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping, Protocol, cast

from mechanical_librarian.application.contained_reviewer_contract import (
    is_cover_profile_contract_reason,
)
from mechanical_librarian.application.cover_identity import (
    CLASSIFIER_REFERENCE,
    _work_target_reference,
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
    read_cover_packet_document,
)
from mechanical_librarian.application.cover_packets import (
    PRODUCER_REVISION as PACKET_PRODUCER_REVISION,
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
    _identity_rows,
    _load_object,
    _observation_document_rows,
    adjudicate_cover_policy,
    read_cover_policy_report,
)
from mechanical_librarian.application.cover_policy import (
    SCHEMA_VERSION as POLICY_REPORT_SCHEMA_VERSION,
)
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
)
from mechanical_librarian.application.reviewer_contracts import ReviewerDispatch
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.deterministic_cover_election import (
    CoverElectionCandidate,
)
from mechanical_librarian.domain.human_decision import manual_cover_eligible
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    MetadataReport,
    TargetDestination,
    TargetFieldState,
)
from mechanical_librarian.domain.owner_settings import PROVIDER_LABELS
from mechanical_librarian.domain.proposal_set import (
    provider_candidate_represents_target_recording,
)

SCHEMA_VERSION = "cover-consideration-v4"
_IDENTITY_ELIGIBLE = frozenset({"verified_manifestation_match", "verified_work_match"})
CoverConsiderationCurrentness = Literal["current", "refresh_required", "inactive"]
_CONSIDERATION_FIELDS = frozenset(
    {
        "candidates",
        "consideration_id",
        "created_at",
        "identity_document",
        "incumbent",
        "incumbent_digest",
        "observation_document",
        "omissions",
        "packet_document",
        "policy_document",
        "policy_reference",
        "policy_report_document",
        "proposal_id",
        "provider_fetching",
        "replay_digest",
        "replay_reference",
        "report_document",
        "report_id",
        "review_case_id",
        "reviewer_access_revision",
        "reviewer_engine",
        "reviewer_failure",
        "reviewer_model",
        "reviewer_status",
        "schema_version",
        "supersedes_id",
        "target_fingerprint",
    }
)


def cover_consideration_replay_digest(
    review_case_id: str,
    report_id: str,
    *,
    provider_fetching: bool,
    replay_reference: str,
) -> str:
    """Bind one consideration replay to its captured producer authority."""
    return hashlib.sha256(
        canonical_packet_json(
            {
                "replay_reference": replay_reference,
                "report": report_id,
                "review_case_id": review_case_id,
                "provider_fetching": provider_fetching,
            }
        ).encode()
    ).hexdigest()


def _cover_incumbent(
    report: MetadataReport,
    candidates: list[dict[str, object]],
) -> tuple[dict[str, object], str | None]:
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
    return (
        {
            "reference": None if incumbent is None else incumbent["reference"],
            "state": incumbent_state,
        },
        None if incumbent is None else cast("str", incumbent["original_sha256"]),
    )


def _validated_cover_consideration(
    document: str,
    parse_report: Callable[[str], MetadataReport] | None = None,
) -> tuple[dict[str, object], MetadataReport]:
    """Read one canonical v4 consideration and its bound report."""
    value = _load_object(document, "cover consideration")
    consideration_id = value.get("consideration_id")
    created_at = value.get("created_at")
    review_case_id = value.get("review_case_id")
    report_id = value.get("report_id")
    report_document = value.get("report_document")
    policy_document = value.get("policy_document")
    policy_reference = value.get("policy_reference")
    policy_report_document = value.get("policy_report_document")
    incumbent = value.get("incumbent")
    if (
        set(value) != _CONSIDERATION_FIELDS
        or value.get("schema_version") != SCHEMA_VERSION
        or type(consideration_id) is not str
        or re.fullmatch(r"cover_consideration_[0-9a-f]{64}", consideration_id) is None
        or type(created_at) is not int
        or created_at < 0
        or type(review_case_id) is not str
        or type(report_id) is not str
        or type(report_document) is not str
        or type(policy_document) is not str
        or type(policy_reference) is not str
        or type(value.get("candidates")) is not list
        or any(
            type(candidate) is not dict
            for candidate in cast("list[object]", value.get("candidates"))
        )
        or type(value.get("omissions")) is not list
        or any(
            type(omission) is not dict
            for omission in cast("list[object]", value.get("omissions"))
        )
        or type(value.get("proposal_id")) is not str
        or type(value.get("provider_fetching")) is not bool
        or type(value.get("reviewer_status")) is not str
        or value.get("reviewer_status") not in {"completed", "failed", "unavailable"}
        or type(value.get("target_fingerprint")) is not str
        or any(
            item is not None and type(item) is not str
            for item in (
                value.get("identity_document"),
                value.get("observation_document"),
                value.get("packet_document"),
                value.get("reviewer_engine"),
                value.get("reviewer_model"),
            )
        )
        or (
            value.get("reviewer_failure") is not None
            and type(value.get("reviewer_failure")) is not dict
        )
        or (
            value.get("reviewer_access_revision") is not None
            and type(value.get("reviewer_access_revision")) is not int
        )
        or (
            value.get("supersedes_id") is not None
            and (
                type(value.get("supersedes_id")) is not str
                or re.fullmatch(
                    r"cover_consideration_[0-9a-f]{64}",
                    cast(str, value.get("supersedes_id")),
                )
                is None
            )
        )
        or type(incumbent) is not dict
        or set(incumbent) != {"reference", "state"}
        or incumbent.get("state") not in {"absent", "available", "unavailable"}
        or (
            incumbent.get("reference") is not None
            and type(incumbent.get("reference")) is not str
        )
        or (
            value.get("incumbent_digest") is not None
            and (
                type(value.get("incumbent_digest")) is not str
                or re.fullmatch(
                    r"[0-9a-f]{64}", cast(str, value.get("incumbent_digest"))
                )
                is None
            )
        )
        or (
            policy_report_document is not None
            and type(policy_report_document) is not str
        )
    ):
        raise ValueError("cover consideration document is invalid")
    identity = dict(value)
    identity.pop("consideration_id")
    identity.pop("created_at")
    if (
        consideration_id
        != "cover_consideration_"
        + hashlib.sha256(canonical_packet_json(identity).encode()).hexdigest()
    ):
        raise ValueError("cover consideration identity is invalid")
    report = (parse_report or MetadataReport.from_json)(report_document)
    if report.report_id != report_id or report.target.fingerprint != value.get(
        "target_fingerprint"
    ):
        raise ValueError("cover consideration report is invalid")
    policy_value = _load_object(policy_document, "cover policy")
    avoid_promotional_text = policy_value.get("avoid_promotional_text")
    avoid_tie_in_branding = policy_value.get("avoid_tie_in_branding")
    prefer_minimal_composition = policy_value.get("prefer_minimal_composition")
    if any(
        type(option) is not bool
        for option in (
            avoid_promotional_text,
            avoid_tie_in_branding,
            prefer_minimal_composition,
        )
    ):
        raise ValueError("cover consideration policy is invalid")
    policy = CoverPolicy(
        avoid_promotional_text=cast(bool, avoid_promotional_text),
        avoid_tie_in_branding=cast(bool, avoid_tie_in_branding),
        prefer_minimal_composition=cast(bool, prefer_minimal_composition),
    )
    if policy.document != policy_document or policy.reference != policy_reference:
        raise ValueError("cover consideration policy is invalid")
    packet_document = cast("str | None", value.get("packet_document"))
    identity_document = cast("str | None", value.get("identity_document"))
    observation_document = cast("str | None", value.get("observation_document"))
    try:
        if packet_document is None:
            if (
                identity_document is not None
                or observation_document is not None
                or policy_report_document is not None
            ):
                raise ValueError
            expected_candidates: list[dict[str, object]] = []
        else:
            if identity_document is None or policy_report_document is None:
                raise ValueError
            packet = read_cover_packet_document(packet_document)
            packet_candidates = cast("list[dict[str, object]]", packet["candidates"])
            references = tuple(
                cast("str", candidate["reference"]) for candidate in packet_candidates
            )
            identity_report = _identity_rows(
                identity_document,
                packet["packet_reference"],
                packet_candidates,
                references,
            )
            if identity_report[
                "report_reference"
            ] != report.report_id or identity_report[
                "work_target_reference"
            ] != _work_target_reference(report):
                raise ValueError
            observation_taxonomy_reference = TAXONOMY_REFERENCE
            if observation_document is not None:
                (
                    _observation_report,
                    _instruction_digest,
                    _schema_digest,
                    observation_taxonomy_reference,
                ) = _observation_document_rows(
                    observation_document,
                    packet["packet_reference"],
                    packet_candidates,
                    references,
                )
            read_cover_policy_report(
                policy_report_document,
                packet_reference=cast("str", packet["packet_reference"]),
                classifier_reference=cast(
                    "str", identity_report["classifier_reference"]
                ),
                policy=policy,
                identity_document=identity_document,
                observation_document=observation_document,
                observation_taxonomy_reference=observation_taxonomy_reference,
                candidate_references=references,
            )
            expected_candidates = CoverConsiderationService._candidates(
                report,
                packet_document,
                identity_document,
                observation_document,
                policy_report_document,
            )
        expected_omissions = CoverConsiderationService._omissions(
            report, packet_document
        )
        expected_incumbent, expected_incumbent_digest = _cover_incumbent(
            report, expected_candidates
        )
        if (
            value["candidates"] != expected_candidates
            or value["omissions"] != expected_omissions
            or value["incumbent"] != expected_incumbent
            or value["incumbent_digest"] != expected_incumbent_digest
        ):
            raise ValueError
    except (
        CoverConsiderationError,
        KeyError,
        StopIteration,
        TypeError,
        ValueError,
    ) as error:
        raise ValueError("cover consideration evidence is invalid") from error
    return value, report


# One statement per retained cover source. The provider rows read the one
# label table every other surface reads, so a source is named with the same
# word wherever the owner meets it (#967).
_COVER_EVIDENCE_STATEMENTS: Final[Mapping[CoverSourceKind, tuple[str, str, str]]] = (
    MappingProxyType(
        {
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
                PROVIDER_LABELS["hardcover"],
                "external_corroboration",
                "Retained catalogue cover supports this choice.",
            ),
            CoverSourceKind.OPEN_LIBRARY: (
                PROVIDER_LABELS["open_library"],
                "external_corroboration",
                "Retained catalogue cover supports this choice.",
            ),
            CoverSourceKind.INVENTAIRE: (
                PROVIDER_LABELS["inventaire"],
                "external_corroboration",
                "Retained catalogue cover supports this choice.",
            ),
            CoverSourceKind.AUDIBLE_CATALOG: (
                PROVIDER_LABELS["audible_catalog"],
                "external_corroboration",
                "Retained catalogue cover supports this choice.",
            ),
            CoverSourceKind.ITUNES_SEARCH: (
                PROVIDER_LABELS["itunes_search"],
                "external_corroboration",
                "Retained catalogue cover supports this choice.",
            ),
        }
    )
)
_UNNAMED_COVER_EVIDENCE_STATEMENT: Final = (
    "Provider evidence",
    "external_corroboration",
    "Retained cover evidence supports this choice.",
)


def cover_consideration_currentness_document(
    document: str,
    parse_report: Callable[[str], MetadataReport] | None = None,
) -> str:
    """Derive immutable collection facts through the complete producer validator.

    An inactive projection is canonical even for earlier or malformed producers;
    it grants no collection authority. Detail and effects still read full evidence.
    """
    try:
        value, report = _validated_cover_consideration(document, parse_report)
        replay = value.get("replay_reference")
        fetching = value.get("provider_fetching")
        if (
            type(replay) is not str
            or re.fullmatch(r"cover_replay_[0-9a-f]{64}", replay) is None
            or type(fetching) is not bool
            or value.get("replay_digest")
            != cover_consideration_replay_digest(
                cast(str, value["review_case_id"]),
                report.report_id,
                provider_fetching=fetching,
                replay_reference=replay,
            )
        ):
            return "null"
        return canonical_packet_json(
            {
                "connection": report.target.connection,
                "library": report.target.library,
                "item": report.target.item,
                "report_id": report.report_id,
                "provider_fetching": fetching,
                "manual_ready": not fetching
                and any(
                    candidate["incumbent"] is True or manual_cover_eligible(candidate)
                    for candidate in cast(
                        "list[dict[str, object]]", value["candidates"]
                    )
                ),
                "replay_reference": replay,
            }
        )
    except (KeyError, TypeError, ValueError):
        return "null"


# What one currentness document validates as: the target and report it names,
# whether it was derived while the provider was fetching, and the replay
# reference it was derived under. It is a function of the document's bytes
# alone, so it may be reused for those exact bytes (#1237).
CoverCurrentnessFacts = tuple[tuple[str, str, str, str], bool, str]


def cover_currentness_facts(document: str) -> CoverCurrentnessFacts | None:
    """Validate one currentness document, or None where it grants no authority."""
    try:
        value = json.loads(document)
        if (
            type(value) is not dict
            or set(value)
            != {
                "connection",
                "library",
                "item",
                "report_id",
                "provider_fetching",
                "manual_ready",
                "replay_reference",
            }
            or any(
                type(value[key]) is not str or not value[key]
                for key in (
                    "connection",
                    "library",
                    "item",
                    "report_id",
                    "replay_reference",
                )
            )
            or type(value["manual_ready"]) is not bool
            or type(value["provider_fetching"]) is not bool
            or re.fullmatch(r"cover_replay_[0-9a-f]{64}", value["replay_reference"])
            is None
            or canonical_packet_json(value) != document
        ):
            return None
        return (
            (value["connection"], value["library"], value["item"], value["report_id"]),
            value["provider_fetching"],
            value["replay_reference"],
        )
    except Exception:
        return None


def cover_currentness_from_facts(
    document: str,
    replay_reference: str,
    provider_fetching: Callable[[str, str, str, str], bool],
    facts: Callable[[str], CoverCurrentnessFacts | None] = cover_currentness_facts,
) -> CoverConsiderationCurrentness:
    """Read only validated immutable facts at the collection boundary.

    ``facts`` may reuse what the same bytes validated as; fetching and the
    replay reference are read on every call.
    """
    if (
        type(document) is not str
        or type(replay_reference) is not str
        or re.fullmatch(r"cover_replay_[0-9a-f]{64}", replay_reference) is None
    ):
        return "inactive"
    try:
        validated = facts(document)
        if validated is None:
            return "inactive"
        fetching = provider_fetching(*validated[0])
        if type(fetching) is not bool or fetching != validated[1]:
            return "inactive"
        return "current" if validated[2] == replay_reference else "refresh_required"
    except Exception:
        return "inactive"


def cover_consideration_currentness(
    document: str,
    replay_reference: str,
    provider_fetching: Callable[[MetadataReport], bool],
) -> CoverConsiderationCurrentness:
    """Classify one persisted consideration against captured live authority."""
    return _validated_currentness(document, replay_reference, provider_fetching)[0]


def _validated_currentness(
    document: str,
    replay_reference: str,
    provider_fetching: Callable[[MetadataReport], bool],
) -> tuple[
    CoverConsiderationCurrentness, tuple[dict[str, object], MetadataReport] | None
]:
    """Classify one consideration, keeping what it validated for the caller."""
    if (
        type(document) is not str
        or type(replay_reference) is not str
        or re.fullmatch(r"cover_replay_[0-9a-f]{64}", replay_reference) is None
    ):
        return "inactive", None
    validated = None
    try:
        validated = value, report = _validated_cover_consideration(document)
        review_case_id = value.get("review_case_id")
        report_id = value.get("report_id")
        fetching = value.get("provider_fetching")
        saved_replay_reference = value.get("replay_reference")
        if (
            type(review_case_id) is not str
            or type(report_id) is not str
            or type(fetching) is not bool
            or type(saved_replay_reference) is not str
            or re.fullmatch(r"cover_replay_[0-9a-f]{64}", saved_replay_reference)
            is None
            or value.get("replay_digest")
            != cover_consideration_replay_digest(
                review_case_id,
                report_id,
                provider_fetching=fetching,
                replay_reference=saved_replay_reference,
            )
        ):
            return "inactive", validated
    except (KeyError, TypeError, ValueError):
        return "inactive", validated
    try:
        current_fetching = provider_fetching(report)
    except Exception:
        return "inactive", validated
    if type(current_fetching) is not bool or fetching != current_fetching:
        return "inactive", validated
    return (
        "current" if saved_replay_reference == replay_reference else "refresh_required"
    ), validated


def cover_consideration_is_current(
    document: str,
    replay_reference: str,
    provider_fetching: Callable[[MetadataReport], bool],
) -> bool:
    """Check one persisted consideration against captured live authority."""
    return (
        cover_consideration_currentness(document, replay_reference, provider_fetching)
        == "current"
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
        preparation_kind: str = "automatic_prepared",
        reviewer_access_revision: int | None = None,
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
    reviewer_engine: str | None = None
    reviewer_model: str | None = None
    reviewer_profile: bytes | None = None
    reviewer_access_revision: int | None = None
    replay_reference: str | None = None


@dataclass(frozen=True, slots=True)
class _ObservationRequest:
    replay_digest: str
    packet_payload: bytes | None
    image_payloads: tuple[tuple[str, str, bytes], ...]
    dispatch: ReviewerDispatch | None = None
    reviewer_failure: dict[str, object] | None = None
    reviewer_access_revision: int | None = None
    replay_reference: str | None = None
    reviewer_engine: str | None = None
    reviewer_model: str | None = None
    reviewer_profile: bytes | None = None


@dataclass(frozen=True, slots=True)
class CoverConsideration:
    document: str

    @property
    def data(self) -> dict[str, object]:
        try:
            return _validated_cover_consideration(self.document)[0]
        except (KeyError, TypeError, ValueError) as error:
            raise CoverConsiderationError("persistence") from error

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
        require_artifact: Callable[[CoverArtifact], None] | None = None,
        clock: Callable[[], int],
        policy: CoverPolicy = CoverPolicy(),
        reviewer_profile: bytes | None = None,
        reviewer: ReviewRunner | None = None,
        reviewer_dispatch: Callable[[], ReviewerDispatch] | None = None,
        reviewer_identity: (
            Callable[[], tuple[int, str, str, bytes] | None] | None
        ) = None,
        reviewer_revision_current: Callable[[int], bool] | None = None,
        reviewer_unavailable_reason: str | None = None,
        bind_policy: Callable[[str, str], None] | None = None,
        provider_fetching: Callable[[MetadataReport], bool] | None = None,
    ) -> None:
        self._store = store
        self._require_artifact = require_artifact
        self._read_preview = read_preview
        self._read_preview_digest = read_preview_digest
        self._clock = clock
        self._policy = policy
        self._reviewer_profile = reviewer_profile
        self._reviewer = reviewer
        self._reviewer_dispatch = reviewer_dispatch
        self._reviewer_identity = reviewer_identity
        self._reviewer_revision_current = reviewer_revision_current
        if reviewer_dispatch is not None and (
            reviewer is not None or reviewer_profile is not None
        ):
            raise ValueError("ambiguous reviewer authority")
        if not is_cover_profile_contract_reason(reviewer_unavailable_reason):
            raise ValueError("invalid reviewer unavailable reason")
        self._reviewer_unavailable_reason = reviewer_unavailable_reason
        self._bind_policy = bind_policy or (lambda _policy, _replay: None)
        self._provider_fetching = provider_fetching or (lambda _report: False)

    def update_policy(self, policy: CoverPolicy) -> None:
        """Bind a validated policy before publishing its immutable snapshot."""
        if type(policy) is not CoverPolicy:
            raise CoverConsiderationError("policy")
        identity = (
            None if self._reviewer_identity is None else self._reviewer_identity()
        )
        self._bind_policy(policy.reference, self._replay_reference(identity, policy))
        self._policy = policy

    def policy_update(self, policy: CoverPolicy) -> AbstractContextManager[None]:
        """Prepare externally, then publish only after the admitted store commit."""
        if type(policy) is not CoverPolicy:
            raise CoverConsiderationError("policy")
        previous = self._policy
        identity = (
            None if self._reviewer_identity is None else self._reviewer_identity()
        )
        previous_replay = self._replay_reference(identity, previous)
        candidate_replay = self._replay_reference(identity, policy)
        if self._policy is not previous:
            raise CoverConsiderationError("stale")

        @contextmanager
        def publish() -> Iterator[None]:
            if self._policy is not previous:
                raise CoverConsiderationError("stale")
            self._bind_policy(policy.reference, candidate_replay)
            try:
                yield
            except BaseException:
                self._bind_policy(previous.reference, previous_replay)
                raise
            else:
                self._policy = policy

        return publish()

    def refresh_authority(self) -> None:
        """Bind a fresh reviewer snapshot before entering a decision transaction."""
        self._bind_policy(*self.current_authority())

    def current_authority(self) -> tuple[str, str]:
        """Capture one published policy with its externally observed reviewer identity."""
        policy = self._policy
        identity = (
            None if self._reviewer_identity is None else self._reviewer_identity()
        )
        result = policy.reference, self._replay_reference(identity, policy)
        if self._policy is not policy:
            raise CoverConsiderationError("stale")
        return result

    @property
    def replay_reference(self) -> str:
        return self.current_authority()[1]

    def _replay_reference(
        self, dynamic_identity: tuple[int, str, str, bytes] | None, policy: CoverPolicy
    ) -> str:
        value: dict[str, object] = {
            "policy": policy.document,
            "contracts": {
                "classifier_reference": CLASSIFIER_REFERENCE,
                "identity_schema": IDENTITY_SCHEMA_VERSION,
                "instruction_sha256": hashlib.sha256(
                    INSTRUCTION.encode("utf-8")
                ).hexdigest(),
                "observation_schema": OBSERVATION_SCHEMA_VERSION,
                "observer_output_schema": OUTPUT_SCHEMA_VERSION,
                "packet_producer_revision": PACKET_PRODUCER_REVISION,
                "packet_schema": PACKET_SCHEMA_VERSION,
                "policy_report_schema": POLICY_REPORT_SCHEMA_VERSION,
                "policy_schema": POLICY_SCHEMA_VERSION,
                "taxonomy_reference": TAXONOMY_REFERENCE,
            },
            "profile_sha256": (
                hashlib.sha256(dynamic_identity[3]).hexdigest()
                if dynamic_identity is not None
                else None
                if self._reviewer_profile is None
                else hashlib.sha256(self._reviewer_profile).hexdigest()
            ),
            "schema_version": SCHEMA_VERSION,
        }
        if dynamic_identity is not None:
            value["reviewer_access_revision"] = dynamic_identity[0]
            value["reviewer_engine"] = dynamic_identity[1]
            value["reviewer_model"] = dynamic_identity[2]
        if self._reviewer_unavailable_reason is not None:
            value["reviewer_unavailable_reason"] = self._reviewer_unavailable_reason
        identity = canonical_packet_json(value)
        return "cover_replay_" + hashlib.sha256(identity.encode()).hexdigest()

    def _replay_digest(
        self,
        review_case_id: str,
        report_id: str,
        *,
        provider_fetching: bool,
        replay_reference: str | None = None,
    ) -> str:
        return cover_consideration_replay_digest(
            review_case_id,
            report_id,
            provider_fetching=provider_fetching,
            replay_reference=replay_reference or self.replay_reference,
        )

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

    def _packet(
        self, report_document: str, report: MetadataReport
    ) -> PreparedCoverPacket | None:
        """Build a bounded packet; audiobook evaluation stays deterministic."""
        if report.file.format in AUDIO_FILE_FORMATS and not any(
            provider_candidate_represents_target_recording(report, provider, candidate)
            for provider in report.providers
            for candidate in provider.candidates
        ):
            return None
        try:
            return prepare_cover_packet(report_document, self._read_preview)
        except ValueError as error:
            if str(error) != "cover packet has no retrieved previews":
                raise
            return None

    def prepare_observation(
        self, review_case_id: str, report_document: str
    ) -> _ObservationRequest:
        """Capture bounded observer inputs while mutable product state is serialized."""
        report = MetadataReport.from_json(report_document)
        packet = self._packet(report_document, report)
        if packet is None or report.file.format in AUDIO_FILE_FORMATS:
            replay_digest = self._replay_digest(
                review_case_id,
                report.report_id,
                provider_fetching=self._provider_fetching(report),
            )
            return _ObservationRequest(replay_digest, None, ())
        binding: ReviewerDispatch | None = None
        failure: dict[str, object] | None = None
        try:
            if self._reviewer_dispatch is not None:
                binding = self._reviewer_dispatch()
        except Exception as error:
            failure = {
                "attempts": [],
                "code": str(getattr(error, "code", "unavailable")),
            }
            dynamic_identity = (
                error.identity
                if isinstance(error, ReviewerAccessApplicationError)
                else None
            )
        else:
            dynamic_identity = (
                None
                if binding is None
                else (
                    binding.access_revision,
                    binding.engine,
                    binding.model,
                    binding.profile,
                )
            )
        replay_reference = self._replay_reference(dynamic_identity, self._policy)
        replay_digest = self._replay_digest(
            review_case_id,
            report.report_id,
            provider_fetching=self._provider_fetching(report),
            replay_reference=replay_reference,
        )
        return _ObservationRequest(
            replay_digest,
            packet.document.encode(),
            tuple(
                (image.reference, image.digest, image.payload)
                for image in packet.images
            ),
            binding,
            failure,
            None if dynamic_identity is None else dynamic_identity[0],
            replay_reference,
            None if dynamic_identity is None else dynamic_identity[1],
            None if dynamic_identity is None else dynamic_identity[2],
            None if dynamic_identity is None else dynamic_identity[3],
        )

    def observe(self, request: _ObservationRequest) -> _ReviewerEvidence:
        """Run optional external advice over an immutable captured request."""
        if request.packet_payload is None:
            return _ReviewerEvidence(request.replay_digest, None, "unavailable", None)
        if request.reviewer_failure is not None:
            return _ReviewerEvidence(
                request.replay_digest,
                None,
                "unavailable",
                request.reviewer_failure,
                request.reviewer_engine,
                request.reviewer_model,
                request.reviewer_profile,
                reviewer_access_revision=request.reviewer_access_revision,
                replay_reference=request.replay_reference,
            )
        return self._observe_payloads(
            request.replay_digest,
            request.packet_payload,
            request.image_payloads,
            binding=request.dispatch,
            replay_reference=request.replay_reference,
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
        *,
        binding: ReviewerDispatch | None = None,
        replay_reference: str | None = None,
    ) -> _ReviewerEvidence:
        engine: str | None = None
        model: str | None = None
        profile = self._reviewer_profile
        reviewer = self._reviewer
        access_revision: int | None = None
        if binding is not None:
            access_revision = binding.access_revision
            engine = binding.engine
            model = binding.model
            profile = binding.profile
            reviewer = binding.runner
        elif self._reviewer_dispatch is not None:
            try:
                binding = self._reviewer_dispatch()
                access_revision = binding.access_revision
                engine = binding.engine
                model = binding.model
                profile = binding.profile
                reviewer = binding.runner
            except Exception as error:
                identity = (
                    error.identity
                    if isinstance(error, ReviewerAccessApplicationError)
                    else None
                )
                return _ReviewerEvidence(
                    replay_digest,
                    None,
                    "unavailable",
                    {
                        "attempts": [],
                        "code": str(getattr(error, "code", "unavailable")),
                    },
                    None if identity is None else identity[1],
                    None if identity is None else identity[2],
                    None if identity is None else identity[3],
                    None if identity is None else identity[0],
                    (
                        replay_reference
                        if identity is None
                        else self._replay_reference(identity, self._policy)
                    ),
                )
        if profile is None or reviewer is None:
            return _ReviewerEvidence(replay_digest, None, "unavailable", None)
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
                profile,
                packet_payload,
                image_payloads,
                dispatch,
                observed=settle,
            ).document
            return _ReviewerEvidence(
                replay_digest,
                observation_document,
                "completed",
                None,
                engine,
                model,
                profile,
                access_revision,
                replay_reference,
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
            return _ReviewerEvidence(
                replay_digest,
                None,
                "failed",
                failure,
                engine,
                model,
                profile,
                access_revision,
                replay_reference,
            )
        finally:
            if binding is not None:
                binding.close()

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
        if (
            reviewer_evidence is not None
            and reviewer_evidence.reviewer_access_revision is not None
            and (
                self._reviewer_revision_current is None
                or not self._reviewer_revision_current(
                    reviewer_evidence.reviewer_access_revision
                )
            )
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
            reviewer_access_revision=(
                None
                if reviewer_evidence is None
                else reviewer_evidence.reviewer_access_revision
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
        packet = self._packet(report_document, report)

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
            if report.file.format in AUDIO_FILE_FORMATS:
                evidence = _ReviewerEvidence(replay_digest, None, "unavailable", None)
            elif evidence is None:
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
                profile_payload=evidence.reviewer_profile,
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
            evidence = None
            identity_document = None
            policy_report_document = None
            packet_document = None
            candidates = []

        incumbent, incumbent_digest = _cover_incumbent(report, candidates)
        body: dict[str, object] = {
            "candidates": candidates,
            "created_at": self._clock(),
            "identity_document": identity_document,
            "incumbent_digest": incumbent_digest,
            "incumbent": incumbent,
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
            "replay_reference": (
                evidence.replay_reference
                if evidence is not None and evidence.replay_reference is not None
                else self.replay_reference
            ),
            "review_case_id": review_case_id,
            "reviewer_failure": reviewer_failure,
            "reviewer_engine": (None if evidence is None else evidence.reviewer_engine),
            "reviewer_model": None if evidence is None else evidence.reviewer_model,
            "reviewer_access_revision": (
                None if evidence is None else evidence.reviewer_access_revision
            ),
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

    @staticmethod
    def _candidates(
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
                # Packet dimensions identify the representative used by policy;
                # the artifact-id tie break keeps that same representative here.
                selected = max(
                    (
                        source
                        for source in sources
                        if cast("CoverArtifact", source.artifact).width
                        == candidate["width"]
                        and cast("CoverArtifact", source.artifact).height
                        == candidate["height"]
                    ),
                    key=lambda source: (
                        cast("CoverArtifact", source.artifact).artifact_id
                    ),
                )
            artifact = selected.artifact
            assert artifact is not None
            identity_row = identity_rows[reference]
            identity_eligible = identity_row["outcome"] in _IDENTITY_ELIGIBLE
            observation_row = observation_rows.get(reference)
            selectable = (
                identity_eligible
                and candidate["incumbent"] is False
                and policy.get("owner_ready") is True
            )
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
        return self._project_validated(
            *_validated_cover_consideration(document), current=current
        )

    def _project_validated(
        self, value: dict[str, object], report: MetadataReport, *, current: bool
    ) -> dict[str, object]:
        policy_document = cast("str | None", value["policy_report_document"])
        policy = None if policy_document is None else json.loads(policy_document)
        candidates = [
            dict(
                cast("dict[str, object]", item),
                manual_eligible=manual_cover_eligible(cast("dict[str, object]", item)),
            )
            for item in cast("list[object]", value["candidates"])
        ]
        incumbent = cast("dict[str, object]", value["incumbent"])
        omissions = cast("list[object]", value["omissions"])
        saved_policy_document = cast("str", value["policy_document"])
        return {
            "candidates": candidates,
            "choice_contexts": _cover_choice_contexts(
                value, report, candidates, policy
            ),
            "consideration_id": value["consideration_id"],
            "current": current,
            "incumbent": dict(incumbent),
            "omissions": list(omissions),
            "policy": policy,
            "policy_preferences": json.loads(saved_policy_document),
            "policy_reference": value["policy_reference"],
            "provider_fetching": value["provider_fetching"],
            "ready": value["provider_fetching"] is False
            and any(
                candidate["incumbent"] or candidate["manual_eligible"]
                for candidate in candidates
            ),
            "reviewer_access_revision": value["reviewer_access_revision"],
            "reviewer_status": value["reviewer_status"],
            "reviewer_engine": value["reviewer_engine"],
            "reviewer_model": value["reviewer_model"],
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
        return (
            self._project(document, current=True)
            if cover_consideration_currentness(
                document, self.replay_reference, self._provider_fetching
            )
            == "current"
            else None
        )

    def project_for_review(
        self,
        document: str | None,
        review_case_id: str,
        report_document: str,
    ) -> tuple[dict[str, object] | None, CoverConsiderationCurrentness]:
        """Project current choices and identify one safe comparison refresh."""
        if document is None:
            return None, "inactive"
        currentness, validated = _validated_currentness(
            document,
            self.replay_reference,
            self._provider_fetching,
        )
        if currentness == "inactive" or validated is None:
            return None, "inactive"
        value, saved_report = validated
        if (
            value.get("review_case_id") != review_case_id
            or saved_report.to_json() != report_document
            or value.get("policy_reference") != self._policy.reference
        ):
            return None, "inactive"
        projection = self._project_validated(
            value, saved_report, current=currentness == "current"
        )
        if currentness == "refresh_required" and projection["ready"] is not True:
            return None, "inactive"
        return projection, currentness

    def _document_current(self, value: dict[str, object]) -> bool:
        return cover_consideration_is_current(
            canonical_packet_json(value),
            self.replay_reference,
            self._provider_fetching,
        )

    def project_historical(self, document: str) -> dict[str, object]:
        """Project immutable consideration evidence without granting current authority."""
        return self._project(document, current=False)

    def require_owner_cover(self, document: str, artifact_id: str | None) -> None:
        """Recheck retained evidence and both blobs without reviewer dispatch."""
        value, report = _validated_cover_consideration(document)
        try:
            fetching = self._provider_fetching(report)
        except Exception as error:
            raise CoverConsiderationError("unavailable") from error
        if (
            value["provider_fetching"] is not False
            or value["policy_reference"] != self._policy.reference
            or fetching is not False
        ):
            raise CoverConsiderationError("stale")
        if self._require_artifact is None:
            raise CoverConsiderationError("unavailable")
        try:
            for source in report.cover_sources:
                if (
                    source.artifact is not None
                    and source.artifact.artifact_id == artifact_id
                ):
                    self._require_artifact(source.artifact)
        except Exception as error:
            raise CoverConsiderationError("unavailable") from error

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
                    for candidate in cast(
                        "list[dict[str, object]]", value["candidates"]
                    )
                    if selected_value
                    == {"kind": "cover", "value": candidate["artifact_id"]}
                ),
                None,
            )
            if selected is None or not manual_cover_eligible(selected):
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
    report: MetadataReport,
    candidates: list[dict[str, object]],
    policy: dict[str, object] | None,
) -> list[dict[str, object]]:
    """Expose exact retained cover binding provenance without identifiers."""
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
            label, role, observation = _COVER_EVIDENCE_STATEMENTS.get(
                source.kind, _UNNAMED_COVER_EVIDENCE_STATEMENT
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
            or candidate.get("manual_eligible") is not True
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
