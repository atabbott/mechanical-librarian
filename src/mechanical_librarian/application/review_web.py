"""Authenticated current-case queries and one exact human decision command."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import unicodedata
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlencode

from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.metadata_report import recording_identity_basis
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.application.review_pipeline import (
    preparation_failure_reference,
)
from mechanical_librarian.domain.audio_health import (
    derive_audio_health,
    media_unreadable_observation,
)
from mechanical_librarian.domain.audio_inspection import MAX_AUDIO_TRACKS
from mechanical_librarian.domain.audiobook_corroboration import (
    candidate_recording_runtime_veto,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    CoverAutomaticDecision,
    parse_automatic_authority,
)
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceBinding,
)
from mechanical_librarian.domain.field_effect import decision_value_kind
from mechanical_librarian.domain.field_outcomes import language_is_unknown
from mechanical_librarian.domain.human_decision import (
    RECORDED_ONLY_REASON,
    HumanDecision,
    HumanDecisionError,
    decision_actions,
)
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FieldState,
    MetadataReport,
    ProviderLaneObservation,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    recording_variant_conflict,
)
from mechanical_librarian.domain.owner_settings import PROVIDER_LABELS
from mechanical_librarian.domain.proposal_set import (
    canonical_target_fields,
)
from mechanical_librarian.domain.review_browse import browse_report
from mechanical_librarian.domain.review_case import ReviewCase, ReviewCaseValueError
from mechanical_librarian.domain.review_field_classification import (
    ReviewFieldCause,
    classify_review_field,
)
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

_AUTOMATIC_REFUSAL_TEXT = {
    "no_candidate": "No eligible value was found in the retained evidence.",
    "ambiguous_evidence": "The retained evidence does not select one value for automatic handling.",
    "target_work_contradicted": "The retained evidence contradicts applicability to this work.",
    "target_work_applicability_unproved": "The retained evidence does not establish that this value describes this work.",
    "target_state_unusable": "The current field value cannot be used safely for this automatic operation.",
    "automatic_unqualified": "This policy option is not qualified for automatic handling.",
    "manifestation_verification_required": "The retained evidence does not verify the ISBN for this edition.",
    "description_quality_floor_not_met": "Retained source text does not meet the automatic synopsis rules.",
    "series_membership_incomplete": "Series membership or position evidence is incomplete or malformed.",
    "series_conflicting_membership": "The retained evidence does not resolve series membership for automatic handling.",
    "series_provider_evidence_required": "Provider evidence supporting this series value is required.",
    "subtitle_local_corroboration_required": "Independent evidence does not corroborate this subtitle.",
    "subtitle_segment_provider_dissent": "Provider subtitle evidence disagrees with the proposed title split.",
    "subtitle_segment_decorated_consensus": "The retained title evidence does not support separating this text into a subtitle.",
    "atomic_bundle_unavailable": "The required linked field selection is unavailable.",
    "corroborated_split_unavailable": "The corroborated title and subtitle do not match the current combined title.",
    "option_evidence_unavailable": "Evidence required by the selected representation is unavailable.",
    "policy_option_unavailable": "The selected policy option is unavailable for this projection.",
    "target_projection_oversized": "The resulting field would exceed the supported size limit.",
    "policy_off": "Tag handling is off.",
    "explicit_unsupported": "Automatic explicit-content handling is unavailable for this option.",
    "source_value_malformed_or_incomplete": "ISBN evidence is malformed or incomplete.",
    "source_values_conflict": "Conflicting ISBN values remain.",
    "source_value_insufficient_association": "Association with this recording is insufficient.",
    "source_unavailable": "Source evidence is unavailable.",
    "source_value_absent": "No ISBN value was supplied.",
    "series_provider_failure_isbn": "A contributing ISBN lookup did not supply usable evidence.",
    "series_provider_failure_work": "A contributing work lookup did not supply usable evidence.",
    "series_provider_failure_asin": "A contributing ASIN lookup did not supply usable evidence.",
}

CoverReadObservation = tuple[tuple[str, str], frozenset[tuple[str, str, str, str]]]


class BrowseObservationOwner(Protocol):
    """External capture surrounds an exclusively owned local browse operation."""

    def capture_browse_observation(self) -> CoverReadObservation | None: ...

    def install_browse_observation(
        self, observation: CoverReadObservation | None
    ) -> AbstractContextManager[None]: ...

    def recheck_browse_observation(
        self, observation: CoverReadObservation | None
    ) -> None: ...


class ReviewWebError(RuntimeError):
    """A fixed, value-free review-browser failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _automatic_decision_projection(document: str) -> dict[str, object] | None:
    """Project either closed automatic authority without leaking its bindings."""
    raw = json.loads(document)
    if type(raw) is not dict:
        raise ValueError
    if raw.get("schema_version") not in {
        "automatic-decision-v1",
        "cover-automatic-decision-v3",
    }:
        return None
    decision = parse_automatic_authority(document)
    projection: dict[str, object] = {
        "candidate_id": decision.candidate_id,
        "decision_id": decision.decision_id,
        "destination": decision.destination,
        "occurred_at": decision.occurred_at,
        "outcome": decision.outcome,
        "proposal_id": decision.proposal_id,
        "review_case_id": decision.review_case_id,
        "selected_value": None,
        "target_fingerprint": decision.target_fingerprint,
    }
    if type(decision) is CoverAutomaticDecision:
        projection["supersedes_decision_id"] = decision.supersedes_decision_id
    return projection


@dataclass(frozen=True, slots=True)
class SessionTokens:
    session_token: str
    csrf_token: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class CurrentReviewCaseSnapshot:
    case_document: str
    report_document: str
    decision_documents: tuple[str, ...]
    history: tuple[dict[str, object], ...]
    final_outcome_document: str | None = None
    automatic_source_outcome_documents: tuple[str, ...] = ()
    automatic_selected_values: tuple[tuple[str, dict[str, object]], ...] = ()
    automatic_attention_destinations: tuple[str, ...] = ()
    policy_attention_destinations: tuple[str, ...] = ()
    automatic_safety_suspended_destinations: tuple[str, ...] = ()
    shadow_abstention_destinations: tuple[str, ...] = ()
    override_blocked_destinations: tuple[str, ...] = ()
    suspended_destinations: tuple[str, ...] = ()
    suspension_causes: tuple[tuple[str, ReviewFieldCause], ...] = ()
    cover_consideration_document: str | None = None
    cover_history: tuple[dict[str, object], ...] = ()
    current_cover_automatic_decision_id: str | None = None
    resolved_policy_document: str | None = None
    description_advice: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ReviewLibrarySnapshot:
    rows: tuple[
        tuple[
            str | None,
            str,
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            str | None,
            int,
        ],
        ...,
    ]
    total: int
    attention_total: int


@dataclass(frozen=True, slots=True)
class ReviewAttentionSnapshot:
    rows: tuple[
        tuple[str, str, str, int, str | None, ReviewFieldCause | None, bool], ...
    ]
    total: int


class ReviewSessionStore(Protocol):
    def begin_login(
        self, now: int, window_seconds: int, maximum_failures: int
    ) -> bool: ...
    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
    ) -> None: ...
    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]: ...
    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str: ...
    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str: ...


class CurrentReviewCaseStore(Protocol):
    def review_case_exists(self, review_case_id: str) -> bool: ...
    def get_review_case(self, review_case_id: str) -> str: ...
    def review_case_ready_at(self, review_case_id: str) -> int | None: ...
    def current_review_case_successor(self, review_case_id: str) -> str | None: ...
    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot: ...


# One active-settings snapshot: connection, stable library name, display label.
LibraryLabels = tuple[tuple[str, str, str], ...]


class ReviewLibraryStore(Protocol):
    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
        library_labels: LibraryLabels = (),
    ) -> ReviewLibrarySnapshot: ...
    def read_review_attention(
        self,
        search: str | None,
        limit: int,
        offset: int,
        library_labels: LibraryLabels = (),
    ) -> ReviewAttentionSnapshot: ...
    def read_current_report_document(
        self, connection: str, library: str, item: str
    ) -> str | None: ...


class HumanDecisionStore(CurrentReviewCaseStore, Protocol):
    def request_description_retry(
        self, review_case_id: str, now: int
    ) -> tuple[str, str]: ...

    def record_authored_description_decision(
        self,
        candidate_id: str,
        binding: DescriptionAdviceBinding,
        idempotency_digest: str,
        now: int,
    ) -> str: ...

    def find_human_decision(self, idempotency_digest: str) -> str | None: ...
    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
    ) -> str: ...


class OwnerSessionService:
    """Owner login, authentication, rotation, and revocation over one store.

    ``ReviewSessionStore`` is a protocol the composition root binds to an
    arbitrary adapter, and the password verifier is an injected callable, so
    neither declares an error type and no call below has an enumerable raisable
    set. Every handler in this class therefore stays broad over the one call it
    guards and converts the failure into a ``ReviewWebError`` with the cause
    chained: none discards a failure, and none reads a fault as an answer the
    store or the verifier gave. The two that answer with something other than
    ``persistence`` say why where they stand.
    """

    def __init__(
        self,
        repository: ReviewSessionStore,
        verify_password: Callable[[str], bool],
        session_digest: Callable[[str], str],
        clock: Callable[[], int],
        lifetime_seconds: int,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        self._repository = repository
        self._verify_password = verify_password
        self._session_digest = session_digest
        self._clock = clock
        self._lifetime_seconds = lifetime_seconds
        self._token_factory = token_factory

    def login(self, password: str) -> SessionTokens:
        if type(password) is not str or not 1 <= len(password.encode("utf-8")) <= 1024:
            raise ReviewWebError("invalid_credentials")
        now = self._clock()
        try:
            permitted = self._repository.begin_login(now, 300, 5)
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if permitted is not True:
            raise ReviewWebError("throttled")
        try:
            verified = self._verify_password(password)
        except Exception as error:
            # The verifier answers with a bool, and a wrong password is
            # ``False`` rather than a raise. A raise is therefore a fault in the
            # hasher or the stored digest, and reporting it as
            # ``invalid_credentials`` told the owner their password was wrong
            # for a condition no password can satisfy.
            raise ReviewWebError("runtime") from error
        if not verified:
            raise ReviewWebError("invalid_credentials")
        session_token = self._token_factory(32)
        csrf_token = self._token_factory(32)
        if not 32 <= len(session_token) <= 128 or not 32 <= len(csrf_token) <= 128:
            raise ReviewWebError("runtime")
        expires_at = now + self._lifetime_seconds
        try:
            self._repository.create_review_session(
                self._session_digest(session_token),
                _digest(csrf_token),
                now,
                expires_at,
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        return SessionTokens(session_token, csrf_token, expires_at)

    def authenticate(self, session_token: str, csrf_token: str | None = None) -> str:
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        try:
            actor, expected_csrf, _ = self._repository.authenticate_review_session(
                self._session_digest(session_token), self._clock()
            )
        except Exception as error:
            # Authentication is fail-closed, so the departure from this class's
            # persistence invariant is deliberate: no answer from the store is
            # not a session, whatever withheld the answer. The cause stays
            # chained, so a store fault remains legible in the traceback while
            # never being read as an authenticated actor.
            raise ReviewWebError("unauthorized") from error
        if csrf_token is not None and (
            not _bounded_token(csrf_token)
            or not secrets.compare_digest(expected_csrf, _digest(csrf_token))
        ):
            raise ReviewWebError("csrf")
        return actor

    def issue_browser_csrf(self, session_token: str) -> str:
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        csrf_token = self._token_factory(32)
        if not 32 <= len(csrf_token) <= 128:
            raise ReviewWebError("runtime")
        try:
            outcome = self._repository.rotate_review_session_csrf(
                self._session_digest(session_token), _digest(csrf_token), self._clock()
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if outcome != "rotated":
            raise ReviewWebError("unauthorized")
        return csrf_token

    def logout(self, session_token: str, csrf_token: str) -> None:
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        if not _bounded_token(csrf_token):
            raise ReviewWebError("csrf")
        try:
            outcome = self._repository.revoke_review_session(
                self._session_digest(session_token), _digest(csrf_token), self._clock()
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if outcome != "revoked":
            raise ReviewWebError(
                outcome if outcome in {"unauthorized", "csrf"} else "persistence"
            )


class InspectCurrentReviewCase:
    def __init__(
        self,
        repository: CurrentReviewCaseStore,
        cover_considerations: CoverConsiderationService | None = None,
    ) -> None:
        self._repository = repository
        self._cover_considerations = cover_considerations

    def _read(
        self, review_case_id: str, history_limit: int | None = None
    ) -> tuple[dict[str, object], MetadataReport, CurrentReviewCaseSnapshot]:
        try:
            snapshot = self._repository.read_current_review_case(
                review_case_id, history_limit
            )
            raw = json.loads(snapshot.case_document)
            if (
                type(raw) is not dict
                or raw.get("current") is not True
                or type(raw.get("case")) is not dict
            ):
                raise ReviewWebError("stale")
            case = ReviewCase.create_from_data(raw["case"])
            report = MetadataReport.from_json(snapshot.report_document)
            if report.report_id != case.report_id:
                raise ReviewWebError("unavailable")
            decisions: dict[str, HumanDecision] = {}
            automatic: dict[str, dict[str, object]] = {}
            automatic_source_outcome_ids: dict[str, str] = {}
            automatic_sources: dict[str, tuple[str, ...]] = {}
            for document in snapshot.automatic_source_outcome_documents:
                outcome_set = ShadowOutcomeSet.from_json(document)
                if outcome_set.review_case_id != review_case_id:
                    raise ReviewWebError("unavailable")
                for outcome in outcome_set.outcomes:
                    previous = automatic_sources.setdefault(
                        outcome.outcome_id, outcome.citation_ids
                    )
                    if previous != outcome.citation_ids:
                        raise ReviewWebError("unavailable")
            # Declared once: the parse loop below binds a decision, while the
            # per-field projection later looks one up and may find none.
            decision: HumanDecision | None
            for document in snapshot.decision_documents:
                if (
                    json.loads(document).get("schema_version")
                    == "authored-description-decision-v1"
                ):
                    AuthoredDescriptionDecision.from_json(document)
                    continue
                projected = _automatic_decision_projection(document)
                if projected is None:
                    decision = HumanDecision.from_json(document)
                    decisions[decision.destination] = decision
                else:
                    destination = str(projected["destination"])
                    if (
                        destination == "cover"
                        and projected["decision_id"]
                        != snapshot.current_cover_automatic_decision_id
                    ):
                        continue
                    automatic[destination] = projected
                    automatic_source_outcome_ids[destination] = (
                        parse_automatic_authority(document).source_outcome_id
                    )
        except ReviewWebError:
            raise
        except Exception as error:
            # The block spans an undeclared repository read, the domain parsers
            # that reject the documents it returns, and this layer's own
            # projection of those documents into the read model, so nothing
            # reaching here has an enumerable origin. Every one of them is the
            # same outcome: one snapshot the surface cannot show. The codes
            # chosen above are re-raised untouched first, and the cause is
            # chained.
            raise ReviewWebError("unavailable") from error
        target_fields = {
            destination.value: value.to_data()
            for destination, value in canonical_target_fields(
                report.target.fields
            ).items()
        }
        if self._cover_considerations is None:
            current_cover_consideration = None
            cover_currentness = "inactive"
        else:
            (
                current_cover_consideration,
                cover_currentness,
            ) = self._cover_considerations.project_for_review(
                snapshot.cover_consideration_document,
                review_case_id,
                report.to_json(),
            )
        cover_is_current = (
            current_cover_consideration is not None
            and current_cover_consideration["current"] is True
        )
        cover_refresh_required = cover_currentness == "refresh_required"
        cover_undecided = "cover" not in decisions and "cover" not in automatic
        if not cover_is_current:
            # A stale or replay-obsolete cover decision remains immutable history,
            # but it no longer resolves the current owner interaction.
            decisions.pop("cover", None)
        fields = []
        suspension_causes = dict(snapshot.suspension_causes)
        # One index per case, not one per field. This read is what next-field
        # authority waits on after a decision, and the index is a pure function
        # of the report, so rebuilding it inside the loop multiplied the only
        # cost on that path by the number of fields under review.
        evidence = _evidence_by_reference(report)
        publication_date_present = any(
            value["destination"] == "publishedDate" for value in case.proposals
        )
        for proposal in case.proposals:
            result = proposal["result"]
            assert type(result) is dict
            destination = cast(str, proposal["destination"])
            all_alternatives = result["alternatives"]
            assert type(all_alternatives) is list
            eligible_alternatives = [
                alternative
                for alternative in all_alternatives
                if alternative["assessment"] != "preserves_target"
            ]
            alternatives = all_alternatives
            references = sorted(
                {
                    reference
                    for alternative in alternatives
                    for reference in alternative["evidence_references"]
                }
            )
            reasons = result["reasons"]
            assert type(reasons) is list
            decision = decisions.get(destination)
            automatic_decision = automatic.get(destination)
            automatic_selection_sources: list[str] = []
            if automatic_decision is not None:
                automatic_selection_sources = _provider_source_labels(
                    report,
                    list(
                        automatic_sources.get(
                            automatic_source_outcome_ids[destination], ()
                        )
                    ),
                )
                decision_id = automatic_decision["decision_id"]
                assert type(decision_id) is str
                selected = (
                    next(
                        (
                            alternative["value"]
                            for alternative in alternatives
                            if alternative["alternative_id"]
                            == automatic_decision["candidate_id"]
                        ),
                        None,
                    )
                    if destination == "cover"
                    else dict(snapshot.automatic_selected_values).get(decision_id)
                )
                if automatic_decision["outcome"] == "candidate" and selected is None:
                    raise ReviewWebError("unavailable")
                automatic_decision = {
                    **automatic_decision,
                    "selected_value": selected,
                }
            choice_contexts = _choice_contexts(
                # Direct choices pair projected values with proposed contexts by
                # position. Keep their exact eligible order first, while
                # retaining preservation lineage after it for inspection.
                [
                    *eligible_alternatives,
                    *(
                        alternative
                        for alternative in all_alternatives
                        if alternative["assessment"] == "preserves_target"
                    ),
                ],
                result,
                report,
                target_fields[destination],
                automatic_decision,
                evidence,
            )
            selected_advice = (
                snapshot.description_advice.get("selected_advice")
                if snapshot.description_advice is not None
                else None
            )
            authored_selection = (
                snapshot.description_advice.get("selection")
                if destination == "description"
                and snapshot.description_advice is not None
                and isinstance(selected_advice, dict)
                and selected_advice.get("current") is True
                else None
            )
            if authored_selection is not None:
                automatic_decision = None
            classification = classify_review_field(
                attention_candidate=(
                    (
                        (
                            destination != "cover"
                            or snapshot.cover_consideration_document is None
                            or cover_is_current
                            or (
                                cover_refresh_required
                                and result["outcome"] == "unresolved_choice"
                                and cover_undecided
                            )
                        )
                        and _needs_attention(
                            proposal,
                            snapshot.automatic_attention_destinations,
                            policy_attention_destinations=snapshot.policy_attention_destinations,
                            owner_only=case.media_kind == "audiobook"
                            and automatic_decision is None,
                        )
                    )
                    or (
                        destination == "cover"
                        and cover_is_current
                        and current_cover_consideration is not None
                        and current_cover_consideration["ready"] is True
                    )
                ),
                automatic_outcome=(
                    "candidate"
                    if authored_selection is not None
                    else "abstention"
                    if destination in snapshot.shadow_abstention_destinations
                    else None
                    if automatic_decision is None
                    else str(automatic_decision["outcome"])
                ),
                override_available=(
                    destination not in snapshot.override_blocked_destinations
                ),
                has_human_decision=decision is not None
                or (
                    type(authored_selection) is dict
                    and authored_selection.get("origin") == "human"
                ),
                suspended=destination in snapshot.suspended_destinations,
                cause=(
                    "automatic_safety_suspended"
                    if destination in snapshot.automatic_safety_suspended_destinations
                    else suspension_causes.get(destination)
                ),
                current_value=target_fields[destination]["value"],
                proposed_values=tuple(
                    alternative["value"] for alternative in alternatives
                ),
            )
            cover_preparation_available = (
                destination == "cover"
                and self._cover_considerations is not None
                and not cover_is_current
                and (
                    snapshot.cover_consideration_document is None
                    or (
                        cover_refresh_required
                        and result["outcome"] == "unresolved_choice"
                        and cover_undecided
                    )
                )
            )
            actions = () if cover_preparation_available else decision_actions(proposal)
            # Classification retains the field's owner context. Action
            # availability is a separate exact-proposal contract; an empty
            # action list is context-only, never an implied choice envelope.
            has_owner_action = bool(actions) or cover_preparation_available
            needs_attention = classification.needs_attention and has_owner_action
            inventory_role = _field_inventory_role(
                classification.state,
                destination,
                report.file.format,
                alternatives,
                publication_date_present,
            )
            if inventory_role == "omitted":
                continue
            field = {
                "current": target_fields[destination],
                "deterministic_disposition": result["outcome"],
                "destination": destination,
                "manual_required": proposal["manual_required"],
                "human_decision": (
                    None if decision is None else _decision_projection(decision)
                ),
                "automatic_decision": automatic_decision,
                "automatic_withheld_reason": None,
                "automatic_selection_sources": automatic_selection_sources,
                "manual_hold": decision is not None and automatic_decision is not None,
                "classification": classification.state,
                "classification_reason": classification.reason,
                "editable": classification.editable and has_owner_action,
                "navigation_role": classification.navigation_role,
                "needs_attention": needs_attention,
                "decision_actions": list(actions),
                "inventory_role": inventory_role,
                "preservation_only": classification.preservation_only,
                "notices": reasons,
                "proposed_values": [
                    alternative["value"] for alternative in eligible_alternatives
                ],
                "choice_contexts": choice_contexts,
                "provenance": references,
                "corroboration_sources": _corroboration_source_labels(
                    report,
                    references,
                    reasons,
                ),
                "proposal_id": proposal["proposal_id"],
                "target_fingerprint": case.target_fingerprint,
                "uncertainty": (
                    reasons
                    if result["outcome"] == "unresolved_choice"
                    or proposal["manual_required"] is True
                    else []
                ),
            }
            if destination == "cover" and self._cover_considerations is not None:
                field["cover_consideration"] = current_cover_consideration
            if destination == "description":
                field["description_advice"] = snapshot.description_advice
            fields.append(field)
        fields.sort(key=lambda value: not value["needs_attention"])
        return (
            {
                "comparison_notices": _provider_comparison_notices(report),
                "current": True,
                "fields": fields,
                # An admitted audiobook opens its case, not its report, so the
                # media-health section has to reach the owner here too -- and
                # it is derived from the same retained report the fields above
                # were read from, never from a second source.
                "media_health": _media_health(report),
                "recording_identity_basis": recording_identity_basis(report),
                "review_case_id": case.review_case_id,
                "schema_version": "review-case-detail-v4",
                # Read models never learn write authority. The composition root
                # stamps the truth before the view leaves the application, and
                # the default keeps a surface that never asks honest.
                "writes_consented": False,
                "target": {
                    "authors": _report_authors(report),
                    "connection": report.target.connection,
                    "item": report.target.item,
                    "library": report.target.library,
                    "media_kind": report.file.format,
                    "title": report.target.current_title or "Untitled",
                },
            },
            report,
            snapshot,
        )

    def successor(self, review_case_id: str) -> str | None:
        """Name the current replacement for one stale case, if it has one."""
        try:
            return self._repository.current_review_case_successor(review_case_id)
        except Exception as error:
            raise ReviewWebError("unavailable") from error

    def run(self, review_case_id: str) -> dict[str, object]:
        detail, report, snapshot = self._read(review_case_id)
        if snapshot.resolved_policy_document is None:
            return detail
        try:
            policy = ResolvedMetadataPolicy.from_data(
                json.loads(snapshot.resolved_policy_document)
            )
            case = ReviewCase.create_from_data(
                json.loads(snapshot.case_document)["case"]
            )
            outcome_set = (
                None
                if snapshot.final_outcome_document is None
                else ShadowOutcomeSet.from_json(snapshot.final_outcome_document)
            )
            if outcome_set is not None and (
                outcome_set.review_case_id != case.review_case_id
                or outcome_set.report_id != case.report_id
                or outcome_set.proposal_set_id != case.proposal_set_id
                or outcome_set.target_fingerprint != case.target_fingerprint
                or outcome_set.resolved_policy_id != policy.resolved_id
                or outcome_set.policy_profile_id != policy.profile_id
            ):
                raise ReviewWebError("unavailable")
            recorded = (
                {}
                if outcome_set is None
                else {value.destination: value for value in outcome_set.outcomes}
            )
            fields = detail["fields"]
            assert type(fields) is list
            for field in fields:
                assert type(field) is dict
                destination = str(field["destination"])
                outcome = recorded.get(destination)
                category = {
                    "description": "description_fill",
                    "isbn": "isbn_form",
                    "title": "title_layout",
                    "authors": "contributor_names",
                    "narrators": "contributor_names",
                    "language": "language_rendering",
                    "publishedDate": "date_precision",
                    "publisher": "publisher_preference",
                    "cover": "cover_order",
                }.get(destination, destination)
                if outcome is not None and outcome.policy_category is not None:
                    category = (
                        "title_layout"
                        if outcome.policy_category == "title"
                        else outcome.policy_category
                    )
                if (
                    dict(policy.destinations).get(category) != "automatic"
                    or field["human_decision"] is not None
                    or field["automatic_decision"] is not None
                ):
                    continue
                if outcome is None:
                    message = (
                        "No recorded automatic evaluation is available for this field."
                    )
                elif outcome.disposition in {
                    "abstention",
                    "conflict",
                    "manual",
                    "unsupported",
                }:
                    message = _AUTOMATIC_REFUSAL_TEXT.get(
                        outcome.reason_code,
                        "The recorded evaluation did not select an automatic action; no more specific reason was recorded.",
                    )
                else:
                    continue
                if destination in (
                    *snapshot.automatic_safety_suspended_destinations,
                    *snapshot.suspended_destinations,
                ):
                    message = (
                        "Automatic handling is currently suspended. Recorded evaluation: "
                        + message
                    )
                field["automatic_withheld_reason"] = message
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        return detail

    def evidence(
        self,
        review_case_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        detail, report, _ = self._read(review_case_id)
        fields = detail["fields"]
        assert type(fields) is list
        destinations_by_reference: dict[str, list[str]] = {}
        for field in fields:
            if type(field) is not dict:
                raise ReviewWebError("unavailable")
            destination = field.get("destination")
            provenance = field.get("provenance")
            if type(destination) is not str or type(provenance) is not list:
                raise ReviewWebError("unavailable")
            for reference in provenance:
                if type(reference) is not str:
                    raise ReviewWebError("unavailable")
                destinations = destinations_by_reference.setdefault(reference, [])
                if destination not in destinations:
                    destinations.append(destination)
        observations = _review_unselectable_evidence(
            report, set(destinations_by_reference)
        )
        for reference, observation in observations.items():
            destinations_by_reference[reference] = cast(
                list[str], observation["destinations"]
            )
        wanted = set(destinations_by_reference)
        evidence = _evidence_by_reference(report)
        missing_lineage_count = len(wanted.difference(evidence, observations))
        reference_set_digest = hashlib.sha256(
            json.dumps(
                sorted(
                    (reference, sorted(destinations))
                    for reference, destinations in destinations_by_reference.items()
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        ordered = [
            reference
            for reference in sorted(wanted)
            if reference in evidence or reference in observations
        ]
        bounded_limit = min(max(limit, 1), 50)
        offset = _decode_cursor(
            cursor,
            "evidence",
            review_case_id,
            reference_set_digest,
        )
        bounded = ordered[offset : offset + bounded_limit]
        return {
            "items": [
                observations[reference]
                if reference in observations
                else _choice_evidence(
                    evidence[reference], destinations_by_reference[reference]
                )
                for reference in bounded
            ],
            "missing_lineage_count": missing_lineage_count,
            "next_cursor": (
                _encode_cursor(
                    offset + bounded_limit,
                    "evidence",
                    review_case_id,
                    reference_set_digest,
                )
                if offset + bounded_limit < len(ordered)
                else None
            ),
            "review_case_id": review_case_id,
            "schema_version": "review-case-evidence-v4",
            "source_ledger": _provider_outcomes(report),
        }

    def history(self, review_case_id: str, limit: int = 50) -> dict[str, object]:
        _detail, report, snapshot = self._read(review_case_id, limit)
        case = ReviewCase.create_from_data(json.loads(snapshot.case_document)["case"])
        target_fields = canonical_target_fields(report.target.fields)
        automatic_values: dict[
            str, tuple[dict[str, object] | None, dict[str, object] | None]
        ] = {}
        decisions: dict[str, dict[str, object]] = {}
        for document in snapshot.decision_documents:
            if (
                json.loads(document).get("schema_version")
                == "authored-description-decision-v1"
            ):
                AuthoredDescriptionDecision.from_json(document)
                continue
            automatic = _automatic_decision_projection(document)
            if automatic is not None:
                decision_id = automatic["decision_id"]
                destination = automatic["destination"]
                assert type(decision_id) is str
                decisions[decision_id] = automatic
                if destination == "cover":
                    continue
                historical_decision = parse_automatic_authority(document)
                if (
                    historical_decision.review_case_id != case.review_case_id
                    or historical_decision.report_id != case.report_id
                    or historical_decision.report_id != report.report_id
                    or historical_decision.proposal_set_id != case.proposal_set_id
                    or historical_decision.target_fingerprint != case.target_fingerprint
                    or historical_decision.target_fingerprint
                    != report.target.fingerprint
                ):
                    continue
                proposal = next(
                    (
                        value
                        for value in case.proposals
                        if value["proposal_id"] == historical_decision.proposal_id
                        and value["destination"] == historical_decision.destination
                    ),
                    None,
                )
                if proposal is None:
                    continue
                current = target_fields[
                    TargetDestination(historical_decision.destination)
                ].to_data()
                previous = (
                    {
                        "kind": decision_value_kind(historical_decision.destination),
                        "value": current["value"],
                    }
                    if current["state"] == "value"
                    else None
                )
                selected = (
                    dict(snapshot.automatic_selected_values).get(decision_id)
                    if historical_decision.outcome == "candidate"
                    else previous
                    if historical_decision.outcome == "keep_current"
                    else None
                )
                if historical_decision.outcome == "candidate" and selected is None:
                    continue
                automatic_values[decision_id] = (previous, selected)
                continue
            try:
                decision = HumanDecision.from_json(document)
            except HumanDecisionError:
                continue
            decisions[decision.decision_id] = _decision_projection(decision)
        history_items = []
        for raw in snapshot.history:
            item = dict(raw)
            if item.get("kind") == "human_decision_recorded":
                decision_id = item.get("decision_id")
                projected = (
                    decisions.get(decision_id) if type(decision_id) is str else None
                )
                # The owner reads the history, so a decision that will never be
                # written says so where it is read rather than only where it is
                # stored (#966).
                item["application"] = (
                    projected.get("application") if projected is not None else None
                )
                item["application_reason"] = (
                    projected.get("application_reason")
                    if projected is not None
                    else None
                )
            if item.get("kind") == "automatic_decision_recorded":
                decision_id = item.get("decision_id")
                joined = (
                    automatic_values.get(decision_id)
                    if type(decision_id) is str
                    and item.get("review_case_id") == case.review_case_id
                    and item.get("destination")
                    == decisions.get(decision_id, {}).get("destination")
                    else None
                )
                item["previous_value"], item["selected_value"] = joined or (
                    None,
                    None,
                )
            history_items.append(item)
        cover_history = []
        for raw in snapshot.cover_history:
            item = dict(raw)
            consideration_document = item.pop("consideration_document", None)
            if (
                type(consideration_document) is not str
                or self._cover_considerations is None
            ):
                raise ReviewWebError("history")
            item["consideration"] = self._cover_considerations.project_historical(
                consideration_document
            )
            decision_id = item.get("decision_id")
            item["decision"] = (
                decisions.get(decision_id) if type(decision_id) is str else None
            )
            cover_history.append(item)
        items = sorted(
            (*history_items, *cover_history),
            key=lambda item: int(cast(int, item.get("occurred_at", 0))),
            reverse=True,
        )[:limit]
        return {
            "items": list(items),
            "next_cursor": None,
            "review_case_id": review_case_id,
            "schema_version": "review-case-history-v2",
        }


class RecordHumanDecision:
    """Record one explicit owner decision without granting apply authority.

    ``HumanDecisionStore`` is a protocol with no declared error type, so the
    handlers over its individual calls stay broad and convert to a
    ``ReviewWebError("persistence")`` with the cause chained. Every store call
    now carries one, which is what makes ``persistence`` a true statement about
    where the fault was rather than this command's answer for any cause it
    cannot name. ``HumanDecisionError`` and the codes chosen here are re-raised
    ahead of every conversion so a domain refusal is never relabelled as
    storage. Two handlers depart from that shape and say so where they stand:
    the advisory readiness read, the only one that discards its failure, and the
    last resort over the whole command, which is over no store call at all and
    answers ``unavailable`` for this layer's own projection.
    """

    def __init__(
        self,
        repository: HumanDecisionStore,
        clock: Callable[[], int],
        timing: StageTimer | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._timing = timing or StageTimer(None)

    def retry_description(self, review_case_id: str) -> tuple[str, str]:
        try:
            return self._repository.request_description_retry(
                review_case_id, self._clock()
            )
        except ValueError as error:
            raise ReviewWebError("stale") from error
        except Exception as error:
            raise ReviewWebError("persistence") from error

    def select_description(
        self,
        review_case_id: str,
        candidate_id: str,
        binding: DescriptionAdviceBinding,
        idempotency_key: str,
    ) -> dict[str, object]:
        if binding.review_case_id != review_case_id:
            raise ReviewWebError("stale")
        digest = HumanDecision.idempotency_digest_for(idempotency_key)
        try:
            document = self._repository.record_authored_description_decision(
                candidate_id, binding, digest, self._clock()
            )
        except ValueError as error:
            raise ReviewWebError("stale") from error
        except Exception as error:
            raise ReviewWebError("persistence") from error
        decision = AuthoredDescriptionDecision.from_json(document)
        return {"decision_id": decision.decision_id, "advised": True}

    def run(
        self,
        review_case_id: str,
        *,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        actor: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_key: str,
        consideration_id: str | None = None,
    ) -> dict[str, object]:
        try:
            idempotency_digest = HumanDecision.idempotency_digest_for(idempotency_key)
            try:
                replay_document = self._repository.find_human_decision(
                    idempotency_digest
                )
                replay = (
                    None
                    if replay_document is None
                    else HumanDecision.from_json(replay_document)
                )
            except Exception as error:
                raise ReviewWebError("persistence") from error
            if replay is not None:
                if not replay.matches_request(
                    review_case_id=review_case_id,
                    proposal_id=proposal_id,
                    target_fingerprint=target_fingerprint,
                    destination=destination,
                    actor=actor,
                    outcome=outcome,
                    selected_value=selected_value,
                    idempotency_key=idempotency_key,
                ):
                    raise ReviewWebError("idempotency_conflict")
                try:
                    stored_replay = HumanDecision.from_json(
                        self._repository.record_human_decision(
                            replay,
                            cover_consideration_id=consideration_id,
                        )
                    )
                except HumanDecisionError:
                    raise
                except Exception as error:
                    raise ReviewWebError("persistence") from error
                return {
                    **_decision_projection(stored_replay),
                    "schema_version": "human-decision-ack-v1",
                }
            try:
                case_document = self._repository.get_review_case(review_case_id)
            except Exception as error:
                # The store cannot say why the read failed, so a second read
                # settles absence against fault. A failure of that second read
                # proves nothing about the case and stays ``persistence``
                # rather than being resolved as ``not_found``.
                try:
                    exists = self._repository.review_case_exists(review_case_id)
                except Exception as persistence_error:
                    raise ReviewWebError("persistence") from persistence_error
                raise ReviewWebError(
                    "persistence" if exists else "not_found"
                ) from error
            raw = json.loads(case_document)
            if (
                type(raw) is not dict
                or raw.get("current") is not True
                or type(raw.get("case")) is not dict
            ):
                raise ReviewWebError("stale")
            case = ReviewCase.create_from_data(raw["case"])
            try:
                ready_at = self._repository.review_case_ready_at(review_case_id)
            except Exception:
                # This timestamp is advisory and cannot gate an owner decision,
                # so every failure of the read -- including a fault the store
                # cannot name -- must leave the decision itself unaffected. The
                # only consequence is that no ``human_wait`` sample is recorded.
                ready_at = None
            decision = HumanDecision.create(
                case,
                proposal_id=proposal_id,
                target_fingerprint=target_fingerprint,
                destination=destination,
                actor=actor,
                outcome=outcome,
                selected_value=selected_value,
                idempotency_key=idempotency_key,
                occurred_at=self._clock(),
            )
            try:
                stored = HumanDecision.from_json(
                    self._repository.record_human_decision(
                        decision,
                        cover_consideration_id=consideration_id,
                    )
                )
            except HumanDecisionError:
                raise
            except Exception as error:
                raise ReviewWebError("persistence") from error
            if stored.request_digest != decision.request_digest:
                raise ReviewWebError("persistence")
            if (
                type(ready_at) is int
                and ready_at >= 0
                and stored.occurred_at >= ready_at
            ):
                self._timing.record(
                    "human_wait",
                    "owner",
                    (stored.occurred_at - ready_at) * 1_000,
                    recorded_at=stored.occurred_at,
                )
            return {
                **_decision_projection(stored),
                "schema_version": "human-decision-ack-v1",
            }
        except ReviewWebError:
            raise
        except HumanDecisionError as error:
            raise ReviewWebError(error.code) from None
        except Exception as error:
            # Last resort over the whole command. Every store call now carries
            # its own ``persistence`` handler, so what reaches here touched no
            # store: the idempotency digest, the case parse, the clock reading,
            # the decision the domain builds from them, and the acknowledgement
            # projection. Reporting those as ``persistence`` named a store that
            # had answered correctly, or had not been asked at all. They are
            # ``unavailable`` instead -- still one code for a cause this layer
            # cannot name, but no longer a false finding about where the fault
            # was. Domain refusals and the codes chosen above keep their own
            # identity, and the cause is chained, so the origin stays legible.
            raise ReviewWebError("unavailable") from error


class BrowseReviewLibrary:
    def __init__(self, repository: ReviewLibraryStore) -> None:
        self._repository = repository

    def library(
        self,
        *,
        lens: str,
        search: str | None,
        limit: int,
        cursor: str | None,
        library_labels: LibraryLabels = (),
    ) -> dict[str, object]:
        normalized = _review_search(search)
        if lens not in {"all", "needs_attention", "no_attention_needed"}:
            raise ReviewWebError("request")
        bounded_limit = _review_limit(limit)
        offset = _decode_cursor(cursor, "library", lens, normalized)
        attention = None if lens == "all" else lens == "needs_attention"
        try:
            snapshot = self._repository.read_review_library(
                normalized, bounded_limit + 1, offset, attention, library_labels
            )
            items = []
            for row in snapshot.rows[:bounded_limit]:
                (
                    case_document,
                    report_document,
                    decision_documents,
                    automatic_attention_destinations,
                    suspended_destinations,
                    shadow_abstention_destinations,
                    terminal_document,
                    attention_count,
                ) = row
                items.append(
                    _library_item(
                        case_document,
                        report_document,
                        decision_documents,
                        automatic_attention_destinations,
                        suspended_destinations,
                        shadow_abstention_destinations,
                        terminal_document,
                        attention_count,
                    )
                )
        except Exception as error:
            # The undeclared page read and the row projection are one outcome:
            # a page this surface cannot render. The row is consumed at its
            # declared width, so a shape the adapter no longer supplies fails
            # the page rather than silently dropping a work from the library.
            raise ReviewWebError("unavailable") from error
        return {
            "counts": {
                "all": snapshot.total,
                "needs_attention": snapshot.attention_total,
                "no_attention_needed": snapshot.total - snapshot.attention_total,
            },
            "items": items,
            "lens": lens,
            "next_cursor": (
                _encode_cursor(offset + bounded_limit, "library", lens, normalized)
                if len(snapshot.rows) > bounded_limit
                else None
            ),
            # Browsing is only reversible if the page before this one can be
            # named. The cursor already carries the offset and its lens/query
            # binding, so the backward step is the same encoding, clamped at the
            # first page.
            "previous_cursor": (
                _encode_cursor(
                    max(offset - bounded_limit, 0), "library", lens, normalized
                )
                if offset > 0
                else None
            ),
            "page": offset // bounded_limit + 1,
            "query": normalized,
            "schema_version": "review-library-v2",
        }

    def attention(
        self,
        *,
        search: str | None,
        limit: int,
        cursor: str | None,
        library_labels: LibraryLabels = (),
    ) -> dict[str, object]:
        normalized = _review_search(search)
        bounded_limit = _review_limit(limit)
        offset = _decode_cursor(cursor, "attention", "needs_attention", normalized)
        try:
            # A row may become contradictory after the SQL snapshot's durable
            # membership check. Read at most two existing bounded repository
            # pages to replace it; the authoritative raw total proves whether
            # that bounded scan reached terminal exhaustion.
            scan_offset = offset
            consumed = 0
            items: list[dict[str, object]] = []
            has_more = False
            snapshot: ReviewAttentionSnapshot | None = None
            raw_total: int | None = None
            for batch in range(2):
                snapshot = self._repository.read_review_attention(
                    normalized, bounded_limit + 1, scan_offset, library_labels
                )
                raw_rows = snapshot.rows
                if raw_total is None:
                    raw_total = snapshot.total
                for row in raw_rows:
                    (
                        case_document,
                        report_document,
                        destination,
                        work_attention_count,
                        automatic_outcome,
                        cause,
                        policy_attention,
                    ) = row
                    item = _attention_item(
                        case_document,
                        report_document,
                        destination,
                        work_attention_count,
                        automatic_outcome=automatic_outcome,
                        cause=cause,
                        policy_attention=policy_attention,
                    )
                    if len(items) < bounded_limit:
                        consumed += 1
                        if item is not None:
                            items.append(item)
                        continue
                    if item is not None:
                        # Leave the visible successor at the next cursor. A
                        # contradictory lookahead row is safe to consume, but
                        # this row must remain available to the next page.
                        has_more = True
                        break
                    consumed += 1
                if has_more:
                    break
                if scan_offset + len(raw_rows) >= raw_total:
                    break
                if batch == 1:
                    # The bounded race-defense scan cannot prove that a later
                    # raw page is empty.  Keep the cursor truthful rather
                    # than claiming terminal exhaustion.
                    has_more = True
                    break
                scan_offset += len(raw_rows)
            if snapshot is None:
                raise ValueError
        except Exception as error:
            # As in ``library``: the undeclared page reads and the row
            # projection they feed are one outcome. The ``ValueError`` above is
            # this scan's own unreachable-state guard and is deliberately
            # caught here rather than escaping as a bare fault.
            raise ReviewWebError("unavailable") from error
        return {
            "items": items,
            "next_cursor": (
                _encode_cursor(
                    offset + consumed,
                    "attention",
                    "needs_attention",
                    normalized,
                )
                if has_more
                else None
            ),
            "query": normalized,
            "schema_version": "review-attention-v1",
            "total": raw_total,
        }

    def report_detail(
        self, connection: str, library: str, item: str
    ) -> dict[str, object]:
        """Project one item's recorded evidence, read-only and complete.

        A work with no review case has no case to open, and until this existed
        its evidence was recorded, retained, and unreachable.  Nothing here
        decides anything: it is the report the library row already stands for,
        stated in the vocabulary the owner reads.
        """
        if any(
            type(value) is not str or not 1 <= len(value) <= 512
            for value in (connection, library, item)
        ):
            raise ReviewWebError("request")
        try:
            document = self._repository.read_current_report_document(
                connection, library, item
            )
        except Exception as error:
            # A store that could not answer is not a work that is not there.
            # Collapsing the two told an owner their audiobook had vanished
            # whenever the database was busy or unreadable.
            raise ReviewWebError("unavailable") from error
        if document is None:
            raise ReviewWebError("missing")
        try:
            # The domain report first: `browse_report` closes the envelope but
            # takes each member on trust, so a shaped-but-corrupt document
            # would have projected arbitrary evidence as recorded fact. This
            # is the same reconstruction `_library_item` performs before it
            # projects a caseless row.
            recorded = MetadataReport.from_json(document)
            report = browse_report(document)
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        target = recorded.target
        if (target.connection, target.library, target.item) != (
            connection,
            library,
            item,
        ):
            # The package row is a pointer, and a pointer can be wrong. Serving
            # what it reached would attribute another work's evidence to this
            # one under the identity the owner asked about.
            raise ReviewWebError("unavailable")
        return _report_detail(report, recorded)


def _report_detail_url(target: TargetFacts) -> str:
    """Address one item's recorded evidence where a case identity does not exist.

    A work without a review case has nothing to open under `/review`, so its
    evidence is addressed by the identity the package row already carries.

    The identity travels in the query rather than in path segments. A library
    server's item id may contain a slash, and both the catalogue and
    `TargetFacts` accept one; percent-encoded in a path it is decoded before
    the router matches, so such a work answered 404 at exactly the address its
    own row handed out.
    """
    return "/api/v1/library/report?" + urlencode(
        {
            "connection": target.connection,
            "library": target.library,
            "item": target.item,
        }
    )


# The same bound the report's own chapter section carries, for the same
# reason: a page an owner reads is bounded, and what it left out is stated.
_EVIDENCE_CEILING = 1_024
# The provider authorities an owner-facing surface may name. A configured
# connection's own name is the owner's private label for a service, so a public
# projection uses the authority instead -- and every authority the report admits
# is public, ebook and audiobook alike.
_PUBLIC_PROVIDER_AUTHORITIES = frozenset(
    {
        "audible_catalog",
        "audiosilo",
        "audnexus",
        "google_books",
        "hardcover",
        "inventaire",
        "itunes_search",
        "open_library",
    }
)


def _media_health(recorded: MetadataReport) -> list[dict[str, object]]:
    """Say what is wrong with one report's media, in the wire's closed shape.

    Every surface that carries the section derives it here, from the same
    retained report through the same rule, so the case an owner opens and the
    evidence view behind it can never state different things about one
    recording. A report with nothing wrong with it, or nothing audio about it,
    states an empty list rather than an absent member.
    """
    return [value.to_data() for value in derive_audio_health(recorded)]


def _report_detail(
    report: dict[str, object], recorded: MetadataReport
) -> dict[str, object]:
    """Read the closed report envelope into the owner-facing evidence view.

    An audiobook now dispatches providers, so this view carries the same two
    provider surfaces the ebook detail does: the source ledger, which says what
    every configured provider was asked and how it ended, and the claims the
    admitted candidates actually stated. Without them an owner reading a
    read-only audiobook would see the local evidence and have no way to tell a
    provider that matched from one that was never reachable.
    """
    target = report["target"]
    file = report["file"]
    local = report["local"]
    audio = report["audio"]
    if (
        type(target) is not dict
        or type(file) is not dict
        or type(local) is not dict
        or (audio is not None and type(audio) is not dict)
    ):
        raise ReviewWebError("unavailable")
    claims = local.get("claims")
    if type(claims) is not list:
        raise ReviewWebError("unavailable")
    chapters = [] if audio is None else audio.get("chapters")
    cover = None if audio is None else audio.get("embedded_cover")
    if type(chapters) is not list or (cover is not None and type(cover) is not dict):
        raise ReviewWebError("unavailable")
    recorded_claims = [value for value in claims if type(value) is dict]
    provider_claims = _report_detail_provider_claims(report)
    return {
        "chapters": [
            {
                "end_milliseconds": value["end_milliseconds"],
                "ordinal": value["ordinal"],
                "start_milliseconds": value["start_milliseconds"],
                "title": value["title"],
            }
            for value in chapters
            if type(value) is dict
        ],
        "chapters_truncated": bool(
            audio is not None and audio.get("chapters_truncated")
        ),
        # Presence and the grammar that showed it, and deliberately no more:
        # this build records that a file carries art and claims no authority
        # over those bytes.
        "embedded_cover": {
            "present": bool(cover is not None and cover.get("present")),
            "surface": None if cover is None else cover.get("surface"),
        },
        # Truncated rather than bounded by a count the domain does not impose:
        # nothing caps a report's claims but the report's own byte ceiling, so
        # a claim-rich work would otherwise exceed whatever number the
        # transport declared and fail the whole read.  The order is the
        # report's, so the kept prefix is the same one every time, and the
        # flag says so rather than letting the page imply completeness -- the
        # idiom the chapter section beside it already uses.
        "evidence": [
            {
                "basis": value["basis"],
                "excerpt": value["excerpt"],
                "kind": value["kind"],
                "role": value["role"],
                # A claim the extraction marked malformed or oversized reads
                # exactly like a valid one without this, which is the one
                # thing an owner reading evidence must not be shown.
                "state": value["state"],
                "value": value["canonical"] or value["observed"],
            }
            for value in recorded_claims[:_EVIDENCE_CEILING]
        ],
        "evidence_truncated": len(recorded_claims) > _EVIDENCE_CEILING,
        "item": target["item"],
        "library": target["library"],
        "connection": target["connection"],
        "media_kind": file["format"],
        # What is wrong with the media itself, as distinct from what the
        # evidence says about the work. Derived from what this report already
        # retains rather than persisted, so the section exists for every report
        # already published and nothing was rebuilt to obtain it.
        "media_health": _media_health(recorded),
        "recording_identity_basis": recording_identity_basis(recorded),
        # What every configured provider was asked, and how each ended.
        "provider_sources": _provider_outcomes(recorded),
        # What the admitted candidates stated, under the same ceiling and the
        # same truncation flag the local evidence above uses.
        "provider_evidence": provider_claims[:_EVIDENCE_CEILING],
        "provider_evidence_truncated": len(provider_claims) > _EVIDENCE_CEILING,
        "report_id": report["report_id"],
        "schema_version": "review-report-detail-v4",
        "title": _report_title(report),
    }


def _report_detail_provider_claims(
    report: dict[str, object],
) -> list[dict[str, object]]:
    """Project every admitted candidate's stated claims, authority-labelled.

    A configured connection's name is the owner's private label for a service,
    so the authority is what an owner-facing surface names. Candidate identity
    travels with each claim because one observation may admit more than one
    candidate and a merged list would attribute one product's claims to
    another.
    """
    values: list[dict[str, object]] = []
    providers = report.get("providers")
    if type(providers) is not list:
        return values
    for provider in cast("list[object]", providers):
        if type(provider) is not dict:
            continue
        authority = provider.get("authority")
        label = _provider_label(authority if type(authority) is str else None)
        candidates = provider.get("candidates")
        if type(candidates) is not list:
            continue
        for candidate in candidates:
            if type(candidate) is not dict:
                continue
            identity = candidate.get("identity")
            claims = candidate.get("claims")
            if type(identity) is not str or type(claims) is not list:
                continue
            for claim in claims:
                if type(claim) is not dict:
                    continue
                values.append(
                    {
                        "candidate": identity,
                        "kind": claim.get("kind"),
                        "provider": label,
                        "role": claim.get("role"),
                        # A malformed or oversized provider statement reads
                        # exactly like a valid one without this.
                        "state": claim.get("state"),
                        "value": claim.get("value"),
                    }
                )
    return values


def _report_title(report: dict[str, object]) -> str:
    target = report["target"]
    fields = target["fields"] if type(target) is dict else None
    if type(fields) is list:
        for field in fields:
            if type(field) is dict and field.get("kind") == "title":
                value = field.get("value")
                if type(value) is str and value:
                    return value
    return "Untitled"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decision_projection(decision: HumanDecision) -> dict[str, object]:
    return {
        "actor": decision.actor,
        "application": decision.application,
        "application_reason": (
            RECORDED_ONLY_REASON if decision.application == "recorded_only" else None
        ),
        "decision_id": decision.decision_id,
        "destination": decision.destination,
        "occurred_at": decision.occurred_at,
        "outcome": decision.outcome,
        "proposal_id": decision.proposal_id,
        "review_case_id": decision.review_case_id,
        "selected_value": decision.selected_value,
        "target_fingerprint": decision.target_fingerprint,
    }


def _corroboration_source_labels(
    report: MetadataReport,
    references: list[str],
    reasons: list[object],
) -> list[str]:
    """Project only closed provider authorities for #416 creator corroboration."""
    if reasons != ["explicit_author_corroborates_untyped_creator"]:
        return []
    return _provider_source_labels(report, references)


def _provider_source_labels(
    report: MetadataReport,
    references: list[str],
) -> list[str]:
    """Project provider labels for exact evidence references."""
    by_reference = {
        claim.reference: _provider_label(provider.authority)
        for provider in report.providers
        for candidate in (*provider.candidates, *provider.recording_lookup_candidates)
        for claim in candidate.claims
    }
    labels = {
        by_reference[reference] for reference in references if reference in by_reference
    }
    return sorted(labels)


def _choice_contexts(
    alternatives: list[dict[str, object]],
    result: dict[str, object],
    report: MetadataReport,
    target: dict[str, object],
    automatic_decision: dict[str, object] | None = None,
    evidence: dict[str, dict[str, str | None]] | None = None,
) -> list[dict[str, object]]:
    """Project retained proposal lineage into owner-readable choice context."""
    local_value_present = target.get("state") == "value" and _current_value_present(
        target.get("value")
    )
    preserves = [
        item for item in alternatives if item.get("assessment") == "preserves_target"
    ]
    evidence = _evidence_by_reference(report) if evidence is None else evidence
    current_rows = [
        _choice_evidence(row)
        for item in preserves
        for reference in cast("list[object]", item.get("evidence_references", []))
        if type(reference) is str and reference in evidence
        for row in [evidence[reference]]
    ]
    current_recommended = automatic_decision is not None and automatic_decision.get(
        "outcome"
    ) in {"keep_current", "preserves_target"}
    contexts: list[dict[str, object]] = [
        {
            "alternative_id": None,
            "evidence": (
                [
                    {
                        "observation": "This value is currently present in the local library.",
                        "role": "local_presence",
                        "source": "Local library record",
                    }
                ]
                if local_value_present
                else []
            )
            + current_rows,
            "limitations": _choice_limitations(preserves[0], result)
            if preserves
            else [],
            "rationale": (
                (
                    "Deterministic policy retained the local value."
                    if current_recommended
                    else "Keep the value currently held by the local library."
                )
                if local_value_present
                else "The local library has no retained value for this field."
            ),
            "recommendation": "recommended"
            if current_recommended
            else "no_recommendation",
            "recommendation_reason": (
                "Deterministic policy retained the local value."
                if current_recommended
                else "No deterministic recommendation is retained for the current value."
            ),
            "selection": "current",
        }
    ]
    for alternative in alternatives:
        references = alternative["evidence_references"]
        assert type(references) is list
        rows = [
            _choice_evidence(evidence[reference])
            for reference in references
            if reference in evidence
        ]
        alternative_id = alternative["alternative_id"]
        assert type(alternative_id) is str
        automatic_candidate = (
            automatic_decision.get("candidate_id")
            if automatic_decision is not None
            and automatic_decision.get("outcome") == "candidate"
            else None
        )
        recommended = alternative_id == automatic_candidate
        contexts.append(
            {
                "alternative_id": alternative_id,
                "evidence": rows,
                "limitations": _choice_limitations(alternative, result),
                "rationale": _choice_rationale(alternative, bool(rows)),
                "recommendation": "recommended" if recommended else "no_recommendation",
                "recommendation_reason": (
                    "Deterministic policy selected this retained alternative."
                    if recommended
                    else "No deterministic recommendation is retained for this alternative."
                ),
                "selection": "proposed",
            }
        )
    return contexts


def _choice_limitations(
    alternative: dict[str, object], result: dict[str, object]
) -> list[str]:
    """Keep material comparison constraints with their retained alternative lineage."""
    alternatives = result.get("alternatives")
    alternative_id = alternative.get("alternative_id")
    if (
        not isinstance(alternatives, list)
        or len(alternatives) != 1
        or alternative_id is None
    ):
        return []
    only = alternatives[0]
    if not isinstance(only, dict) or only.get("alternative_id") != alternative_id:
        return []
    reasons = result.get("reasons")
    if not isinstance(reasons, list):
        return []
    material = {
        "insufficient_independent_corroboration",
        "populated_subjective_field",
        "date_refinement_requires_corroboration",
        "date_year_conflict",
    }
    return [
        reason for reason in reasons if isinstance(reason, str) and reason in material
    ][:3]


def _current_value_present(value: object) -> bool:
    if value is None or value == "":
        return False
    return not (type(value) is list and not value)


def _choice_rationale(alternative: dict[str, object], supported: bool) -> str:
    assessment = cast(
        "dict[object, str]",
        {
            "fills_empty": "fills an empty local value",
            "preserves_target": "preserves the local value",
            "replaces_target": "replaces the local value",
        },
    ).get(alternative.get("assessment"), "retains a proposal value")
    scope = cast(
        "dict[object, str]",
        {
            "cover": "cover-specific",
            "edition": "edition-specific",
            "work": "work-level",
        },
    ).get(alternative.get("scope"), "retained")
    evidence = (
        "with retained supporting evidence"
        if supported
        else "from the retained proposal"
    )
    return f"This alternative {assessment} using {scope} evidence {evidence}."


def _choice_evidence(
    evidence: dict[str, str | None], destinations: list[str] | None = None
) -> dict[str, object]:
    return {
        **({"destinations": sorted(destinations)} if destinations is not None else {}),
        "observation": _choice_observation(evidence),
        "role": "local_presence"
        if _choice_is_local(evidence)
        else "external_observation"
        if evidence["source"] == "identifier"
        or evidence["provider"] not in PROVIDER_LABELS
        else "external_corroboration",
        "source": _choice_source_label(evidence),
    }


def _choice_source_label(evidence: dict[str, str | None]) -> str:
    provider = evidence["provider"]
    if provider in PROVIDER_LABELS:
        return PROVIDER_LABELS[cast("str", provider)]
    if _choice_is_local(evidence):
        return "Local library record"
    return "Retained observation (provider name unavailable)"


def _choice_is_local(evidence: dict[str, str | None]) -> bool:
    """Retain local identifier observations as local presence, not provider claims."""
    return evidence["source"] == "local" or (
        evidence["source"] == "identifier"
        and type(evidence.get("reference")) is str
        and cast(str, evidence["reference"]).startswith("local_")
    )


def _choice_observation(evidence: dict[str, str | None]) -> str:
    try:
        fact = json.loads(evidence["fact"] or "{}")
    except (TypeError, ValueError):
        return "The retained observation has no readable value."
    if type(fact) is not dict:
        return "The retained observation has no readable value."
    if evidence["source"] == "identifier" and "relation" in fact:
        return "Identifier relationship retained; no value observation is available."
    kind = cast(
        "dict[object, str]",
        {
            "authors": "Contributors",
            "contributor": "Narration credit",
            "description": "Description",
            "duration": "Running time",
            "isbn": "ISBN",
            "asin": "ASIN",
            "performance_type": "Performance",
            "publication_date": "Publication date",
            "series": "Series membership",
            "title": "Title",
        },
    ).get(fact.get("kind"), "Metadata")
    value = fact.get("value", fact.get("observed"))
    if type(value) in {str, int, float}:
        return f"{kind}: {_choice_text(cast('str | int | float', value))}"
    return f"{kind} observation retained."


def _choice_text(value: str | int | float) -> str:
    text = str(value)
    cleaned = "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith("C")
    )
    normalized = " ".join(cleaned.split())
    if not normalized:
        return "Value retained"
    return normalized[:160] + ("…" if len(normalized) > 160 else "")


def _bounded_token(value: object) -> bool:
    return type(value) is str and 32 <= len(value) <= 128 and value.strip() == value


def _review_unselectable_evidence(
    report: MetadataReport, linked: set[str]
) -> dict[str, dict[str, object]]:
    """Keep excluded language and recording facts readable without choices."""
    observations: dict[str, dict[str, object]] = {}
    for local_claim in report.local_claims:
        if (
            local_claim.kind is not ClaimKind.LANGUAGE
            or local_claim.reference in linked
        ):
            continue
        unknown = language_is_unknown(local_claim.canonical or local_claim.observed)
        observations[local_claim.reference] = {
            "destinations": ["language"],
            "role": "local_presence",
            "source": "Local metadata",
            "observation": (
                f"Language: {_choice_text(local_claim.observed)}. "
                + (
                    "Undetermined language; "
                    if unknown
                    else "Retained for review only; "
                )
                + "not an eligible choice."
            ),
        }
    for provider in report.providers:
        for candidate in (*provider.candidates, *provider.recording_lookup_candidates):
            for claim in candidate.claims:
                language = (
                    claim.kind is ClaimKind.LANGUAGE
                    and claim.state is FieldState.VALID
                    and type(claim.value) is str
                )
                if report.audio is None and not language:
                    continue
                narrator = (
                    report.audio is not None
                    and claim.kind is ClaimKind.CONTRIBUTOR
                    and claim.role == "narrator"
                    and (
                        recording_variant_conflict(
                            candidate,
                            report.local_claims,
                            report.target.current_title or "",
                        )
                        is not None
                        or candidate_recording_runtime_veto(
                            candidate, report.local_claims, report.audio
                        )
                    )
                )
                if (
                    claim.kind not in {ClaimKind.ISBN, ClaimKind.ASIN}
                    and not narrator
                    and not language
                    or claim.reference in linked
                ):
                    continue
                label = (
                    "Language"
                    if language
                    else "Narrator"
                    if narrator
                    else claim.kind.value.upper()
                )
                destination = "narrators" if narrator else claim.kind.value
                conflict = (
                    recording_variant_conflict(
                        candidate,
                        report.local_claims,
                        report.target.current_title or "",
                    )
                    if narrator
                    else None
                )
                reason = (
                    "Undetermined language. "
                    if language
                    and type(claim.value) is str
                    and language_is_unknown(claim.value)
                    else "Recording language conflicts with this item. "
                    if conflict == "recording_language_conflict"
                    else "Recording variant conflicts with this item. "
                    if conflict is not None
                    else "Recording timing is invalid or conflicts with this item. "
                    if narrator
                    else ""
                )
                value = (
                    _choice_text(claim.value)
                    if claim.state is FieldState.VALID and type(claim.value) is str
                    else "No valid narrator retained"
                    if narrator
                    else "No valid identifier retained"
                )
                observations.setdefault(
                    claim.reference,
                    {
                        "destinations": [destination],
                        "role": "external_observation",
                        "source": _provider_label(provider.authority),
                        "observation": (
                            f"{label}: {value}. Retained for review only; "
                            f"not an eligible choice. {reason}"
                            f"Scope: {claim.scope.value}; state: {claim.state.value}; "
                            f"applicability: {claim.applicability}."
                        ),
                    },
                )
    return observations


def _evidence_by_reference(
    report: MetadataReport,
) -> dict[str, dict[str, str | None]]:
    data = report.to_data()
    values: dict[str, dict[str, str | None]] = {}

    def add(
        source: str,
        fact: object,
        *,
        reference_key: str = "reference",
        provider: str | None = None,
        candidate: str | None = None,
    ) -> None:
        if type(fact) is not dict or type(fact.get(reference_key)) is not str:
            return
        reference = fact[reference_key]
        values[reference] = {
            "candidate": candidate,
            "fact": json.dumps(
                fact, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
            "kind": str(fact.get("kind", "evidence")),
            "provider": provider,
            "reference": reference,
            "source": source,
        }

    local = data["local"]
    assert type(local) is dict
    for fact in local["claims"]:
        add("local", fact)
    public_authority_by_provider = {
        provider["provider"]: authority
        for provider in cast("list[object]", data["providers"])
        if type(provider) is dict
        and type(provider.get("provider")) is str
        and type(provider.get("authority")) is str
        and provider["authority"] in _PUBLIC_PROVIDER_AUTHORITIES
        for authority in [cast(str, provider["authority"])]
    }

    def public_provider(value: object) -> str | None:
        if type(value) is not str:
            return None
        if value in _PUBLIC_PROVIDER_AUTHORITIES:
            return value
        return public_authority_by_provider.get(value)

    for provider in cast("list[object]", data["providers"]):
        assert type(provider) is dict
        provider_name = provider["provider"]
        assert type(provider_name) is str
        authority = provider.get("authority")
        public_authority = public_provider(authority)
        for candidate in [
            *provider["recording_lookup_candidates"],
            *provider["candidates"],
        ]:
            assert type(candidate) is dict
            candidate_identity = candidate["identity"]
            assert type(candidate_identity) is str
            for fact in candidate["claims"]:
                add(
                    "provider_claim",
                    fact,
                    provider=public_authority or provider_name,
                    candidate=candidate_identity,
                )
            for fact in candidate["evidence"]:
                assert type(fact) is dict
                reference = (
                    "evidence_"
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "candidate": candidate_identity,
                                "evidence": fact,
                                "provider": provider_name,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                )
                rich_fact = dict(fact)
                rich_fact["reference"] = reference
                add(
                    "provider_evidence",
                    rich_fact,
                    provider=public_authority or provider_name,
                    candidate=candidate_identity,
                )
    for fact in cast("list[object]", data["identifier_evidence"]):
        assert type(fact) is dict
        if fact.get("reference") in values:
            # Identifier relationships annotate an admitted claim; they do not
            # replace the actual value at that same retained reference.
            continue
        add(
            "identifier",
            fact,
            provider=public_provider(fact.get("provider")),
            candidate=(
                fact.get("candidate_identity")
                if type(fact.get("candidate_identity")) is str
                else None
            ),
        )
    for fact in cast("list[object]", data["covers"]):
        assert type(fact) is dict
        provenance = fact.get("provenance")
        add(
            "cover",
            fact,
            reference_key="source_id",
            provider=public_provider(provenance.get("provider"))
            if type(provenance) is dict
            else None,
        )
    return values


def _review_search(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = " ".join(value.split())
    if len(normalized) > 128:
        raise ReviewWebError("request")
    return normalized


def _review_limit(value: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 50:
        raise ReviewWebError("request")
    return value


def _cursor_binding(kind: str, lens: str, search: str | None) -> str:
    return hashlib.sha256(
        json.dumps(
            [kind, lens, search],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]


def _encode_cursor(offset: int, kind: str, lens: str, search: str | None) -> str:
    if (
        type(offset) is not int
        or isinstance(offset, bool)
        or not 0 <= offset <= 100_000
    ):
        raise ReviewWebError("request")
    payload = json.dumps(
        [offset, _cursor_binding(kind, lens, search)],
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None, kind: str, lens: str, search: str | None) -> int:
    if cursor is None:
        return 0
    try:
        if (
            type(cursor) is not str
            or not 1 <= len(cursor) <= 128
            or cursor.strip() != cursor
        ):
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        raw = json.loads(
            base64.b64decode(cursor + padding, altchars=b"-_", validate=True).decode(
                "ascii"
            )
        )
        if (
            type(raw) is not list
            or len(raw) != 2
            or type(raw[0]) is not int
            or isinstance(raw[0], bool)
            or not 0 <= raw[0] <= 100_000
            or raw[1] != _cursor_binding(kind, lens, search)
        ):
            raise ValueError
        return raw[0]
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ReviewWebError("request") from error


def _case_and_report(
    case_document: str, report_document: str
) -> tuple[ReviewCase, MetadataReport]:
    try:
        case = ReviewCase.from_json(case_document)
        report = MetadataReport.from_json(report_document)
    except (ReviewCaseValueError, ReportValueError) as error:
        # Both parsers name their malformed-document outcome, so this matches
        # those two types rather than the ``ValueError`` they happen to share
        # with the rest of this projection. The agreement check below is this
        # layer's own and refuses in its own right.
        raise ReviewWebError("unavailable") from error
    if (
        case.report_id != report.report_id
        or case.target != report.target.to_data()
        or case.target_fingerprint != report.target.fingerprint
        or case.file_identity != report.file.identity
    ):
        raise ReviewWebError("unavailable")
    return case, report


def _needs_attention(
    proposal: dict[str, object],
    automatic_attention_destinations: tuple[str, ...] = (),
    *,
    owner_only: bool = False,
    policy_attention_destinations: tuple[str, ...] = (),
) -> bool:
    """Whether one proposal is the owner's to answer rather than settled.

    A settled `propose_change` waits unless its bound policy routes it to Ask.
    On a recording an undecided field has nothing to wait for, so it is the
    owner's; a field an F46 record did decide leaves the attention lens
    because the membership query excludes it, and reads exactly as an ebook's
    does elsewhere. The action list beside this decides whether a choice
    exists at all (#966, #967).
    """
    result = proposal["result"]
    assert type(result) is dict
    reasons = result["reasons"]
    assert type(reasons) is list
    return (
        owner_only
        or proposal["destination"] in policy_attention_destinations
        or proposal["destination"] in automatic_attention_destinations
        or (proposal["destination"] == "cover" and "current_cover_verified" in reasons)
        or result["outcome"] == "unresolved_choice"
        or proposal["manual_required"] is True
        or "no_safe_bibliographic_combination" in reasons
    )


def _report_authors(report: MetadataReport) -> list[str]:
    """Derive the bounded current author display value without persistence."""
    value = next(
        (
            field.value
            for field in report.target.fields
            if field.destination is TargetDestination.AUTHORS
        ),
        (),
    )
    return (
        list(cast("tuple[str, ...]", value))
        if type(value) is tuple and all(type(author) is str for author in value)
        else []
    )


# One owner-readable consequence for every closed ``ProviderStatus``. The lane
# state alone told the owner nothing they could act on: "candidate limit
# exceeded" and "malformed" both arrived as "a bounded terminal status". The
# text says what the source did and what followed from it, and never a query,
# header, credential, payload or path.
_NON_LIMITING_PROVIDER_STATUSES = frozenset(
    {"candidates", "disabled", "insufficient_input"}
)
_PROVIDER_OUTCOME_REASONS: dict[str, str] = {
    "candidates": "Returned metadata candidates for comparison.",
    "no_match": "Returned no matching metadata.",
    "disabled": "Not enabled, so this source was never asked.",
    "insufficient_input": "This work lacks the identifiers this source needs.",
    "authentication": "Refused the credentials it was given; nothing was retained.",
    "authorization": "Refused this request; nothing was retained.",
    "rate_limited": "Limited the request rate.",
    "unavailable": "Could not be reached.",
    "incomplete": "Answered only partially, so nothing was retained.",
    "malformed": "Returned an unreadable answer, so nothing was retained.",
    "oversized": "Returned more than the accepted bound, so nothing was retained.",
    "candidate_limit_exceeded": "Returned too many candidates to compare safely.",
    "query_limit_exceeded": "Reached the bounded query allowance for this work.",
}


def _provider_outcomes(report: MetadataReport) -> list[dict[str, object]]:
    """Expose only bounded lane state, never diagnostics or queries.

    Every lane is reported, matches included, so the selected work can show one
    complete source ledger. Suppressing the successful ones left the owner
    unable to tell a source that matched from a source that was never asked.
    """
    outcomes: list[dict[str, object]] = []
    for provider in report.providers:
        lanes = provider.lanes or (provider,)
        for lane in lanes:
            status = lane.status.value
            kind = (
                cast(ProviderLaneObservation, lane).kind
                if provider.lanes
                else provider.query_kind
            ).value
            outcomes.append(
                {
                    "provider_requests": provider.request_accounting.logical_requests,
                    "lane_kind": kind,
                    "provider": _provider_label(provider.authority),
                    "reason": _PROVIDER_OUTCOME_REASONS.get(
                        status, "Ended in a bounded state this build cannot name."
                    ),
                    "status": status,
                }
            )
    return outcomes


def _provider_label(authority: str | None) -> str:
    if authority is None or authority not in PROVIDER_LABELS:
        return "Provider (name unavailable)"
    return PROVIDER_LABELS[authority]


def _provider_comparison_notices(report: MetadataReport) -> list[str]:
    """Keep provider limitations with affected choices, never as case banners."""
    del report
    return []


_TERMINAL_PREPARATION_GUIDANCE: dict[str, tuple[str, str]] = {
    "provider": (
        "A provider ended without a usable result.",
        "No owner action is currently available.",
    ),
    "configuration": (
        "Provider configuration needs owner attention.",
        "Update the configured provider, then scan this library again.",
    ),
    "authentication": (
        "Provider credentials need owner attention.",
        "Update the configured provider credentials, then scan this library again.",
    ),
    "stale": (
        "The item changed while preparation was in progress.",
        "Automatic recovery is pending for the current item.",
    ),
    "transient": (
        "Bounded automatic recovery attempts were exhausted.",
        "When this library finishes with failures, open Background activity and choose Retry.",
    ),
    "projection": (
        "Review projection could not be completed.",
        "No owner action is currently available.",
    ),
    "failed": (
        "Preparation failed; no safe detail is available.",
        "No owner action is currently available.",
    ),
    "currentness": (
        "The source changed before preparation could be completed.",
        "Automatic recovery is pending for the current source.",
    ),
    "persistence": (
        "Preparation state could not be durably recorded.",
        "No owner action is currently available.",
    ),
    "unsupported_media": (
        "This media type is not supported for review preparation.",
        "Use a supported media file, then scan this library again.",
    ),
    "unreadable_media": (
        "The local media file could not be read.",
        "Restore a readable media file in the library, then scan this library again.",
    ),
    "parser_refused": (
        "The media file could not be safely read within supported parsing limits.",
        "Use a media file that meets the supported parsing limits, then scan this library again.",
    ),
    "multi_file_budget_exceeded": (
        "This audiobook's file set is larger than preparation can read as one item.",
        "Reduce the item to the supported number, size, or reading time of audio "
        "files, then scan this library again.",
    ),
    "missing_required_input": (
        "Required preparation input is unavailable.",
        "Provide the required supported item input, then scan this library again.",
    ),
    "malformed_retained_data": (
        "Retained preparation data could not be safely reopened.",
        "No owner action is currently available.",
    ),
    "internal_failure": (
        "Preparation stopped before a usable review package was produced.",
        "No owner action is currently available.",
    ),
    "unknown": (
        "Preparation failed; no safe detail is available.",
        "No owner action is currently available.",
    ),
}
# The terminal statuses one file of a composed audiobook can end a whole item
# on. Their recorded root is a track ordinal rather than a provider name, so it
# is read into the owner's reason and never into the provider field.
_TRACK_ROOT_STATUSES = frozenset(
    (
        "unsupported_media",
        "unreadable_media",
        "parser_refused",
        "multi_file_budget_exceeded",
    )
)
# The writer's own grammar, transcribed: `review_pipeline_stages` records
# `track_{ordinal}` from an integer ordinal that is never zero and never
# zero-padded. Reading a wider grammar than the writer produces would let a
# corrupt row state an ordinal no preparation ever wrote -- `track_007` and
# `track_0` are not files this build named -- so the reader admits exactly what
# the writer emits and the refusing-file sentence and the media-health
# observation agree by construction.
_TRACK_ROOT = re.compile(r"track_([1-9]\d{0,2})")


def _refusing_track_sentence(status: str, root: str | None) -> str:
    """Name the one file that ended a composed item, when one is recorded.

    An owner holding a three-hundred-file audiobook cannot act on "a file
    could not be read"; the ordinal is the whole difference between that and
    a file they can open. A single-file item records no root and reads
    exactly as it did before.
    """
    if status not in _TRACK_ROOT_STATUSES or root is None:
        return ""
    matched = _TRACK_ROOT.fullmatch(root)
    if matched is None:
        return ""
    return f" The refusing file is track {matched.group(1)} of this item's audio files."


def _terminal_media_health(status: str, root: str | None) -> list[dict[str, object]]:
    """Say what the terminal record proves about the media, and nothing else.

    A terminal item published no report, so nothing can be derived for it; the
    one media-health statement its record supports is that a file could not be
    read, with the ordinal the parent recorded. Every other terminal status is
    about preparation rather than about the bytes and states nothing here.

    Only an absent root is an item-wide statement. A root the writer's track
    grammar does not admit -- or an ordinal past the composition bound -- is
    malformed retained content, and reading it as "no track was named" would
    turn a corrupt row into a statement about every file of the item. The
    record supports nothing then, so nothing is stated.
    """
    if status != "unreadable_media":
        return []
    if root is None:
        return [media_unreadable_observation(None).to_data()]
    matched = _TRACK_ROOT.fullmatch(root)
    if matched is None:
        return []
    ordinal = int(matched.group(1))
    if not 1 <= ordinal <= MAX_AUDIO_TRACKS:
        return []
    return [media_unreadable_observation(ordinal).to_data()]


def _library_item(
    case_document: str | None,
    report_document: str,
    decision_documents: tuple[str, ...],
    automatic_attention_destinations: tuple[str, ...] = (),
    suspended_destinations: tuple[str, ...] = (),
    shadow_abstention_destinations: tuple[str, ...] = (),
    terminal_document: str | None = None,
    attention_count: int | None = None,
) -> dict[str, object]:
    if case_document is None:
        try:
            terminal = json.loads(report_document)
            if (
                type(terminal) is dict
                and terminal.get("schema_version") == "review-terminal-work-v1"
            ):
                status = terminal["status"]
                lane = terminal["lane"]
                root = terminal["failure_root"]
                if (
                    type(status) is not str
                    or type(lane) is not str
                    or (root is not None and type(root) is not str)
                ):
                    raise ValueError
                guidance = _TERMINAL_PREPARATION_GUIDANCE.get(status)
                if guidance is None:
                    raise ValueError
                reason, recovery = guidance
                reason += _refusing_track_sentence(status, root)
                media_health = _terminal_media_health(status, root)
                work_id, item, library = (
                    terminal["work_id"],
                    terminal["item"],
                    terminal["library"],
                )
                if (
                    type(work_id) is not str
                    or type(item) is not str
                    or type(library) is not str
                ):
                    raise ValueError
                title = terminal.get("title", "Preparation unavailable")
                authors = terminal.get("authors", [])
                media_kind = terminal.get("media_kind", "unknown")
                if (
                    type(title) is not str
                    or type(authors) is not list
                    or any(type(author) is not str for author in authors)
                    or type(media_kind) is not str
                ):
                    raise ValueError
                return {
                    "authors": authors,
                    "attention_count": 0,
                    "attention_state": "no_attention_needed",
                    "detail_url": None,
                    "detail_kind": None,
                    "dispositions": ["terminal_failure"],
                    "item": item,
                    "library": library,
                    "media_kind": media_kind,
                    "review_case_id": None,
                    "title": title,
                    "work_id": work_id,
                    "preparation": {
                        "attempt": terminal["attempt"],
                        "diagnostic_reference": preparation_failure_reference(
                            work_id,
                            lane,
                            terminal["attempt"],
                            terminal["recovery_attempt"],
                            status,
                        ),
                        # The same closed shape the report detail carries, so
                        # a reader holds one media-health vocabulary whether
                        # the item produced a report or stopped before one.
                        "media_health": media_health,
                        "provider": (
                            "provider"
                            if status in _TRACK_ROOT_STATUSES
                            else (root or "provider")
                        ),
                        "reason": reason,
                        "recovery": recovery,
                        "recovery_attempt": terminal["recovery_attempt"],
                        "stage": lane,
                        "status": status,
                    },
                    "preparation_outcomes": [],
                    "summary": {
                        "fields": [],
                        "reason": reason,
                        "state": "preparation_failed",
                    },
                }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReviewWebError("unavailable") from error
    if case_document is None:
        try:
            report = MetadataReport.from_json(report_document)
        except ReportValueError as error:
            # ``from_json`` now funnels every malformed-document outcome, the
            # length guard's codec failure included, into its one declared type.
            raise ReviewWebError("unavailable") from error
        case = None
    else:
        case, report = _case_and_report(case_document, report_document)
    decisions: set[str] = set()
    automatic_outcomes: dict[str, str] = {}
    for document in decision_documents:
        if (
            json.loads(document).get("schema_version")
            == "authored-description-decision-v1"
        ):
            authored = AuthoredDescriptionDecision.from_json(document)
            decisions.add(authored.destination)
            automatic_outcomes[authored.destination] = "candidate"
            continue
        automatic = _automatic_decision_projection(document)
        if automatic is None:
            decision = HumanDecision.from_json(document)
            assert case is not None
            decision.validate_for(case)
            decisions.add(decision.destination)
        else:
            destination = str(automatic["destination"])
            automatic_outcomes[destination] = str(automatic["outcome"])
            if automatic["outcome"] != "abstention":
                decisions.add(destination)
    attention = (
        []
        if case is None
        else [
            value
            for value in case.proposals
            if (
                not _omitted_for_epub(
                    cast(str, value["destination"]), report.file.format
                )
                and value["destination"] not in decisions
                and value["destination"] not in suspended_destinations
                and classify_review_field(
                    attention_candidate=_needs_attention(
                        value,
                        automatic_attention_destinations,
                    ),
                    automatic_outcome=(
                        "abstention"
                        if value["destination"] in shadow_abstention_destinations
                        else automatic_outcomes.get(cast(str, value["destination"]))
                    ),
                    has_human_decision=value["destination"] in decisions,
                    suspended=value["destination"] in suspended_destinations,
                    cause=(
                        "automatic_safety_suspended"
                        if value["destination"] in automatic_attention_destinations
                        else None
                    ),
                    current_value=None,
                    proposed_values=(),
                ).needs_attention
            )
        ]
    )
    outcomes = (
        []
        if case is None
        else sorted(
            {
                value["result"]["outcome"]  # type: ignore[index]
                for value in case.proposals
            }
        )
    )
    material = (
        f"review-library-work-v1\0{report.target.connection}\0"
        f"{report.target.library}\0{report.target.item}"
    )
    work_id = "library_work_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
    current_attention_count = (
        len(attention) if attention_count is None else attention_count
    )
    value = {
        "authors": _report_authors(report),
        "attention_count": current_attention_count,
        "attention_state": (
            "needs_attention" if current_attention_count else "no_attention_needed"
        ),
        "detail_url": f"/review/{case.review_case_id}"
        if case is not None
        else _report_detail_url(report.target),
        # What the address is, said outright rather than left to be inferred
        # from its shape. A reader that guessed from the URL prefix would be
        # re-orphaned by a route rename, which is exactly how this evidence
        # became unreachable in the first place.
        "detail_kind": "case" if case is not None else "report",
        "dispositions": outcomes,
        "item": report.target.item,
        "library": report.target.library,
        "media_kind": report.file.format,
        "review_case_id": None if case is None else case.review_case_id,
        "title": report.target.current_title or "Untitled",
        "work_id": work_id,
        "preparation": None
        if terminal_document is None
        else _library_item(None, terminal_document, ())["preparation"],
        "preparation_outcomes": _provider_outcomes(report),
        # A work with no current review case has no case identity to open, so
        # its current values were unreachable from Library. The bounded target
        # projection travels with the row the owner already has, which keeps
        # every published work inspectable without a second authority.
        "summary": None
        if case is not None
        else {
            "fields": cast(
                "list[dict[str, object]]", report.target.to_data()["fields"]
            ),
            "reason": (
                "No review decision is recorded for this work. Its current values "
                "are shown exactly as Mechanical Librarian last read them."
            ),
            "state": "no_decision_recorded",
        },
    }
    return value


def _field_inventory_role(
    classification: str,
    destination: str,
    media_kind: str,
    alternatives: list[object],
    publication_date_present: bool,
) -> str:
    """Project one canonical field into the owner's finite field inventory."""
    if destination == "publishedYear" and publication_date_present:
        return "omitted"
    if _omitted_for_epub(destination, media_kind):
        return "omitted"
    if classification == "unsupported" and not alternatives:
        return "contextual"
    return "primary"


def _omitted_for_epub(destination: str, media_kind: str) -> bool:
    """Keep EPUB-inapplicable fields out of every owner-facing projection."""
    return media_kind == "epub" and destination in {"narrators", "explicit"}


def _attention_item(
    case_document: str,
    report_document: str,
    destination: str,
    work_attention_count: int,
    automatic_outcome: str | None = None,
    cause: ReviewFieldCause | None = None,
    policy_attention: bool = False,
) -> dict[str, object] | None:
    case, report = _case_and_report(case_document, report_document)
    if _omitted_for_epub(destination, report.file.format):
        return None
    if destination == "publishedYear" and any(
        value["destination"] == "publishedDate" for value in case.proposals
    ):
        return None
    proposal = next(
        (value for value in case.proposals if value["destination"] == destination),
        None,
    )
    if proposal is None:
        return None
    classification = classify_review_field(
        attention_candidate=(
            # The lens mirrors the query rather than second-guessing it: the
            # membership predicate already excludes every field a durable
            # automatic decision settled, so a row that reached here has none
            # and a recording's proposal is the owner's. Re-deciding that here
            # from a shadow disposition would page short of the count the same
            # query reports (#966, #967).
            _needs_attention(proposal, owner_only=case.media_kind == "audiobook")
            or policy_attention
            or cause == "automatic_safety_suspended"
            or (destination == "cover" and work_attention_count > 0)
        ),
        automatic_outcome=("abstention" if automatic_outcome == "abstention" else None),
        has_human_decision=False,
        suspended=False,
        cause=cause,
        current_value=None,
        proposed_values=(),
    )
    if not classification.needs_attention:
        # Query membership can race a durable automatic or human decision.  A
        # stale browse row is omitted; it is never an outage for other work.
        return None
    result = proposal["result"]
    assert type(result) is dict
    return {
        "authors": _report_authors(report),
        "classification_reason": classification.reason,
        "destination": destination,
        "detail_url": f"/review/{case.review_case_id}?field={destination}",
        "item": report.target.item,
        "library": report.target.library,
        "media_kind": report.file.format,
        "notices": result["reasons"],
        "provenance": sorted(
            {
                reference
                for alternative in result["alternatives"]
                for reference in alternative["evidence_references"]
            }
        ),
        "review_case_id": case.review_case_id,
        "title": report.target.current_title or "Untitled",
        "work_attention_count": work_attention_count,
    }
