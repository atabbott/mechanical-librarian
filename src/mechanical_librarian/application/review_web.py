"""Authenticated current-case queries and one exact human decision command."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import sys
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, Protocol, TypeVar, cast
from urllib.parse import urlencode

from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    recording_admission_basis,
    recording_identity_basis,
)
from mechanical_librarian.application.operations import StageTimer
from mechanical_librarian.application.review_pipeline import (
    preparation_failure_reference,
)
from mechanical_librarian.domain.audio_health import (
    derive_audio_health,
    hours_minutes,
    media_unreadable_observation,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_AUDIO_TRACKS,
    AudioInspectionField,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    candidate_recording_runtime_veto,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    SCHEMA_VERSION as COVER_AUTOMATIC_DECISION_SCHEMA,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    CoverAutomaticDecision,
    parse_automatic_authority,
)
from mechanical_librarian.domain.cover_taste import (
    TASTE_STRONG_CONFIDENCE,
    TasteProfile,
)
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceBinding,
)
from mechanical_librarian.domain.description_formatting import alternative_formatted
from mechanical_librarian.domain.field_effect import (
    CLEARABLE_DESTINATIONS,
    decision_value_kind,
)
from mechanical_librarian.domain.field_outcomes import language_is_unknown
from mechanical_librarian.domain.human_decision import (
    OWNER_NAMES_DESTINATIONS,
    OWNER_NAMES_MAXIMUM,
    OWNER_SUBSET_DESTINATION,
    OWNER_SUBSET_MAXIMUM,
    OWNER_TEXT_DESTINATIONS,
    OWNER_TEXT_MAXIMUM,
    RECORDED_ONLY_REASON,
    UNREVERSIBLE_DESTINATIONS,
    DecisionScope,
    HumanDecision,
    HumanDecisionAction,
    HumanDecisionError,
    decision_actions,
    description_unrestorable,
    genre_subset_available,
    list_union,
    offer_digest,
    owner_entry_admitted,
    owner_text,
    owner_value_suggestions,
    pinned_field_fingerprint,
    preserved_description,
)
from mechanical_librarian.domain.library_visit import ListingPositions
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    echoed_target_values,
    field_evidence_providers,
    owner_link_facts_from_data,
    recording_variant_conflict,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLink,
    OwnerLinkError,
    OwnerLinkScope,
    OwnerLinkTarget,
    link_state,
)
from mechanical_librarian.domain.owner_settings import (
    PROVIDER_LABELS,
    audio_edition_label,
    provider_label,
    provider_source_labels,
)
from mechanical_librarian.domain.proposal_set import (
    audiobook_review_scope,
    canonical_target_fields,
)
from mechanical_librarian.domain.review_browse import (
    CaseBrowseFacts,
    LibraryReportFacts,
    ProviderLaneFacts,
    ReviewBrowseValueError,
    browse_case_facts_and_report,
    browse_report,
    library_report_facts,
    provider_lane_facts,
)
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    _freeze_json_object,
    _thaw_json_object,
    compose_offer,
    current_offer,
    level_offer_destinations,
)
from mechanical_librarian.domain.review_field_classification import (
    ReviewFieldCause,
    ReviewFieldClassification,
    classify_review_field,
)
from mechanical_librarian.domain.shadow_outcome import (
    ShadowFieldOutcome,
    ShadowOutcomeSet,
)
from mechanical_librarian.domain.validation import owner_password
from mechanical_librarian.domain.value_flags import value_flags

# v13: a field says whether its owner accept needs no write (ADR-0105);
# v14: a description's current and proposed values carry their formatted
# copies (#1995).
# v15: a field an earlier decision holds in Preview says so (ADR-0085).
ReviewCaseDetailSchemaVersion = Literal["review-case-detail-v15"]
REVIEW_CASE_DETAIL_SCHEMA: Final[ReviewCaseDetailSchemaVersion] = (
    "review-case-detail-v15"
)

ReviewCaseEvidenceSchemaVersion = Literal["review-case-evidence-v6"]
REVIEW_CASE_EVIDENCE_SCHEMA: Final[ReviewCaseEvidenceSchemaVersion] = (
    "review-case-evidence-v6"
)

# v2: an owner's clear selects the closed empty value (#1523).
HumanDecisionAckSchemaVersion = Literal["human-decision-ack-v2"]
HUMAN_DECISION_ACK_SCHEMA: Final[HumanDecisionAckSchemaVersion] = (
    "human-decision-ack-v2"
)
ReviewAttentionSchemaVersion = Literal["review-attention-v1"]
REVIEW_ATTENTION_SCHEMA: Final[ReviewAttentionSchemaVersion] = "review-attention-v1"
# v5: a keep says which write it went back on (#1523); v6: the book link's
# history events (#1477); v7: a link named by an identifiers file (#1478);
# v8: a description value's formatted copy, and advice as it is written (#2003).
ReviewCaseHistorySchemaVersion = Literal["review-case-history-v8"]
REVIEW_CASE_HISTORY_SCHEMA: Final[ReviewCaseHistorySchemaVersion] = (
    "review-case-history-v8"
)

# One book link History event; v2: a file link's identifier, source and
# import (#1478).
OwnerLinkHistoryEventSchemaVersion = Literal["owner-link-history-event-v2"]
OWNER_LINK_HISTORY_EVENT_SCHEMA: Final[OwnerLinkHistoryEventSchemaVersion] = (
    "owner-link-history-event-v2"
)

# v10: a row's link says how it was made, and a file link names no source (#1478);
# v12: a current description carries its formatted copy (#1995).
ReviewLibrarySchemaVersion = Literal["review-library-v13"]
REVIEW_LIBRARY_SCHEMA: Final[ReviewLibrarySchemaVersion] = "review-library-v13"

ReviewReportDetailSchemaVersion = Literal["review-report-detail-v7"]
REVIEW_REPORT_DETAIL_SCHEMA: Final[ReviewReportDetailSchemaVersion] = (
    "review-report-detail-v7"
)

# A degraded value with nothing proposed beside it is the current one (#1283).
_CURRENT_VALUE_REFUSAL_TEXT = {
    "candidate_credit_prefixed": 'The current value starts with a credit label such as "Narrated by", so it is never confirmed for you.',
    "candidate_promotional_title": 'The current title carries an announcement such as "Now a Major Motion Picture", so it is never confirmed for you.',
}
# An audiobook's regional language tag is never respelled for you (#1496), so
# the field says why instead of "no more specific reason is known" (#1593).
_AUDIOBOOK_LANGUAGE_REGION_TEXT = (
    "The current value names the same language with a region. Dropping the "
    "region is never done for you on audiobooks; choose the plain language "
    "yourself if you want it."
)
_AUTOMATIC_REFUSAL_TEXT = {
    "isbn_title_material_conflict": "The sources disagree about the work or title. Choose the title yourself.",
    "no_candidate": "No source offered a value that can be used here.",
    "ambiguous_evidence": "The sources do not agree on one value.",
    "target_work_contradicted": "The sources suggest this value belongs to a different work.",
    "target_work_applicability_unproved": "The sources do not show that this value describes this work.",
    "target_state_unusable": "The current value cannot be changed for you.",
    "automatic_unqualified": "Automatic is not offered for this kind of change, so it is left to you.",
    "manifestation_verification_required": "The sources do not confirm this ISBN for this edition.",
    "description_quality_floor_not_met": "No source description meets the rules for choosing one for you.",
    "candidate_language_differs": "The value comes from a source in a different language from this book.",
    "candidate_credit_prefixed": 'The proposed value starts with a credit label such as "Narrated by", so it is never written for you.',
    "candidate_strips_marks": "The proposed value drops accents from a name you already have, so it is never written for you.",
    "candidate_promotional_title": 'The proposed title carries an announcement such as "Now a Major Motion Picture", so it is never written for you.',
    "insufficient_support": "Not enough sources agree to choose a value for you.",
    "series_membership_incomplete": "The sources give an incomplete or unreadable series or position.",
    "series_conflicting_membership": "The sources disagree about the series.",
    "series_provider_evidence_required": "No source confirms this series.",
    "subtitle_local_corroboration_required": "No other source confirms this subtitle.",
    "subtitle_restates_series": "The proposed subtitle only repeats the series, so it is never written for you.",
    "subtitle_segment_provider_dissent": "The sources disagree with splitting the title this way.",
    "subtitle_segment_decorated_consensus": "The sources do not support splitting this text into a subtitle.",
    "atomic_bundle_unavailable": "A linked field this choice depends on is unavailable.",
    "corroborated_split_unavailable": "The confirmed title and subtitle do not match the current combined title.",
    "option_evidence_unavailable": "The source details your chosen option needs are unavailable.",
    "policy_option_unavailable": "The option you chose does not apply to this field.",
    "target_projection_oversized": "The result would be longer than the library accepts.",
    "policy_off": "Tag handling is off.",
    "explicit_unsupported": "Explicit content is never marked for you with this setting.",
    "source_value_malformed_or_incomplete": "The sources give an unreadable or incomplete ISBN.",
    "source_values_conflict": "The sources give different ISBNs.",
    "source_value_insufficient_association": "The sources do not tie this ISBN closely enough to this recording.",
    "source_unavailable": "The sources could not be read.",
    "source_value_absent": "No source supplied an ISBN.",
    "series_provider_failure_isbn": "An ISBN lookup did not return a usable answer.",
    "series_provider_failure_work": "A title and author lookup did not return a usable answer.",
    "series_provider_failure_asin": "An ASIN lookup did not return a usable answer.",
    "owner_link_unqualified": "Only the book you linked supports this value, so it is left to you.",
}

CoverReadObservation = tuple[tuple[str, str], frozenset[tuple[str, str, str, str]]]


if TYPE_CHECKING:
    from mechanical_librarian.application.review_covers import (
        CalibrationRow,
        CoverKey,
        CoverPageSnapshot,
        CoverView,
    )


class BrowseFetchingChanged(MetadataReportError):
    """A complete browse observed fetching drift with unchanged authority."""


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
    """A fixed, value-free review-browser failure.

    Only ``offer_changed`` carries a closed ``reason`` (#1355).
    """

    def __init__(self, code: str, reason: str | None = None) -> None:
        self.code = code
        self.reason = reason
        super().__init__(code)


def _automatic_decision_projection(document: str) -> dict[str, object] | None:
    """Project either closed automatic authority without leaking its bindings."""
    raw = json.loads(document)
    if type(raw) is not dict:
        raise ValueError
    if raw.get("schema_version") not in {
        "automatic-decision-v1",
        COVER_AUTOMATIC_DECISION_SCHEMA,
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
    report: MetadataReport
    decision_documents: tuple[str, ...]
    history: tuple[dict[str, object], ...]
    final_outcome_document: str | None = None
    automatic_source_outcome_documents: tuple[str, ...] = ()
    automatic_selected_values: tuple[tuple[str, dict[str, object]], ...] = ()
    # In Preview, the value each field's latest earlier-generation automatic
    # candidate chose, by destination (#1575).
    earlier_automatic_values: tuple[tuple[str, dict[str, object]], ...] = ()
    automatic_attention_destinations: tuple[str, ...] = ()
    policy_attention_destinations: tuple[str, ...] = ()
    # The undecided choices the persisted field facts settle: a work-route
    # choice that only trims or extends (#1253), a populated subjective value
    # a fill-only option keeps, and tags while their option is off (#1310).
    settled_choice_destinations: tuple[str, ...] = ()
    # The destinations the case's evaluated policy keeps, read live through
    # the same predicate: tags while their option is off, and a floor-passing
    # subjective value a fill-only option keeps. A fill's fact is always
    # settled, so only this says a tags fill is kept by the setting (#1661).
    policy_kept_destinations: tuple[str, ...] = ()
    automatic_safety_suspended_destinations: tuple[str, ...] = ()
    shadow_abstention_destinations: tuple[str, ...] = ()
    override_blocked_destinations: tuple[str, ...] = ()
    suspended_destinations: tuple[str, ...] = ()
    suspension_causes: tuple[tuple[str, ReviewFieldCause], ...] = ()
    cover_consideration_document: str | None = None
    # The newest verdict on the current comparison when it chose a cover and
    # nothing has answered the work since: its id, its profile version's
    # document and its own (#1372 PR 4b, J8).
    cover_taste_verdict: tuple[str, str, str, int] | None = None
    cover_history: tuple[dict[str, object], ...] = ()
    current_cover_selection: tuple[str, str, str | None] | None = None
    current_cover_automatic_decision_id: str | None = None
    # The stage would switch the current comparison's cover but for the
    # owner's hold or choice, so cover Resume may show (#1372 PR 5, K9).
    cover_automatic_held: bool = False
    resolved_policy_document: str | None = None
    description_advice: dict[str, object] | None = None
    # F59's owner controls: each latest owner decision's write state, and what
    # each owner-entry field held before its latest verified write (ADR-0085).
    decision_effect_states: tuple[tuple[str, str], ...] = ()
    previous_values: tuple[tuple[str, object], ...] = ()
    # What this case's own latest verified write put in each owner-entry
    # field, which owner entry reads as its current value (#1523, S5).
    written_values: tuple[tuple[str, object], ...] = ()
    # Destinations the record of the owner's answers holds an answer for, on
    # any case of the book, which keeps Automatic off them (ADR-0108).
    held_destinations: tuple[str, ...] = ()
    # Held destinations that offer Resume automatic handling: unpinned, with
    # Automatic on and the field set to Automatic on this case (C0
    # `resume_offer`); a cover, where the owner's cover took it over.
    resume_offer_destinations: tuple[str, ...] = ()
    # Held destinations whose unpinned answer was given on this case in its
    # current episode and not since withdrawn there by the owner: it decides
    # the field here. An answer that is only a pin taken back, on this case
    # or an earlier one, or one Book matching's `matching_changed` row asked
    # again, is read here too: with no decision of the case, it decides the
    # field only where Automatic decided it, and otherwise the field reads as
    # it would without the answer (`_classify_field`, C2b-27, C2b-28). Any other
    # unpinned answer asks the field where the case offers a decision on it,
    # and settles it where none is offered (C2b-15, C2b-18, S16).
    own_answer_destinations: tuple[str, ...] = ()
    # Destinations the owner withdrew on this case's episode (the latest
    # owner row, pins and unpins aside) beside Automatic's active-generation
    # decision: neither it nor Automatic's authored description settles them,
    # and they are asked where the case offers a decision (ADR-0108,
    # Withdrawal and carry eligibility).
    withdrawn_destinations: tuple[str, ...] = ()
    # This case's owner decisions a completed Resume took back: they no
    # longer decide their field, and keep only a recorded line (D3).
    covered_decision_ids: tuple[str, ...] = ()
    # Each pin on the record: its destination, whether it is in force here,
    # and the pinned case's target field as JSON (ADR-0085, Amendment 2).
    pins: tuple[tuple[str, bool, str], ...] = ()
    # Destinations whose change waits to be written, so a pin is refused.
    change_waiting_destinations: tuple[str, ...] = ()
    # Owner accepts recorded with nothing to write: the case's latest write
    # already holds their value (ADR-0105).
    no_change_decisions: tuple[str, ...] = ()
    # Destinations whose change is still to be written; a refused pin on any
    # other waits only for the next scan to read the written value (#1984).
    write_waiting_destinations: tuple[str, ...] = ()
    # Held destinations with a Resume open; the answer holds until the
    # requested checks finish (#1755).
    return_requested_destinations: tuple[str, ...] = ()
    # Held destinations whose open Resume's checks finished with failures.
    # The answer holds and nothing retries those checks, so the owner can
    # ask again.
    return_failed_destinations: tuple[str, ...] = ()
    # The case's level offer at the level it was last reconciled at, or None
    # when its fields offer their live proposals (#1355, S10).
    level_offer_document: str | None = None
    # Whether the case waits for its Book matching check: its reconciled
    # level is not the configured one (#1355, S10).
    checking: bool = False
    # Whether that check gave up: its work is neither queued, delayed nor
    # running, as Policy's count of works that could not be checked reads it.
    check_given_up: bool = False
    # Whether the library is in Preview, so a held field says turning on
    # Automatic leaves it as it is.
    preview: bool = False


# A row's current link and the `owner_link` facts its report records (#1477).
LibraryRowLink = tuple[OwnerLink, dict[str, object] | None]
# Whether Find this book is offered on a Library row, from its connection,
# library, report file format and whether it could not finish (#1477): the
# owner search's own rule, so the row and the link view cannot disagree.
FindOffer = Callable[[str, str, str, bool], bool]


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
            int,
        ],
        ...,
    ]
    total: int
    attention_total: int
    # #1407: the works no provider matched, and each row's reason -- `refused`,
    # `unavailable` or `no_match` -- or None for any other row.
    not_matched_total: int
    match_reasons: tuple[str | None, ...]
    # #1413: the works with nothing open whose latest preparation failed.
    preparation_failed_total: int
    # #1334: the works whose current report holds a value flag.
    flagged_total: int
    # #1351: the works first listed since the owner's last visit, and how far
    # the listing had got when this page was read.
    new_total: int
    listing: ListingPositions
    # The scope each row's human decisions were validated in, when the store
    # projected one, so the row projection need not project the case again.
    decision_scopes: tuple[DecisionScope | None, ...] = dataclass_field(
        default=(), compare=False, repr=False
    )
    # #1477: each row's current book link and its report's link facts, or
    # None for an unlinked work. Empty when the store read none.
    links: tuple[LibraryRowLink | None, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewAttentionSnapshot:
    rows: tuple[
        tuple[str, str, str, int, str | None, ReviewFieldCause | None, bool, bool],
        ...,
    ]
    total: int


@dataclass(frozen=True, slots=True)
class ReviewLibraryProgress:
    """Library's counts and Attention's fields, per library, from one snapshot.

    Computed on request and never stored (#1335). Each library row carries the
    totals Library's page carries, so the works with no attention needed are
    derived the same way.
    """

    # (connection, library, all, needs_attention, not_matched,
    # preparation_failed, flagged, new), one row per library that has works.
    libraries: tuple[tuple[str, str, int, int, int, int, int, int], ...]
    # (connection, library, destination, works whose field needs the owner),
    # one row per field with at least one such work.
    fields: tuple[tuple[str, str, str, int], ...]


class ReviewSessionStore(Protocol):
    def begin_login(
        self,
        now: int,
        client_digest: str,
        window_seconds: int,
        client_maximum: int,
        global_maximum: int,
    ) -> bool: ...
    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
        client_digest: str,
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
    def end_review_sessions_except(self, session_digest: str) -> int: ...
    def end_other_review_sessions(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> tuple[str, int]: ...


class OwnerCredential(Protocol):
    """One owner verifier with the session and client keys derived from it."""

    def verify(self, password: str) -> bool: ...
    def session_digest(self, token: str) -> str: ...
    def client_digest(self, identity: str) -> str: ...


class OwnerPasswordChange(Protocol):
    """The live owner verifier a store deployment can replace in place."""

    def current(self) -> OwnerCredential: ...
    def successor(self, password: str) -> OwnerCredential: ...
    def exclusive(self) -> AbstractContextManager[bool]:
        """Hold off adopt while a session write must not interleave with one."""
        ...

    def adopt(
        self,
        expected: OwnerCredential,
        successor: OwnerCredential,
        committed: Callable[[], None] | None = None,
    ) -> bool:
        """Commit ``successor`` while ``expected`` is live, then make it live.

        ``committed`` runs once the store holds the successor and before any
        request can be checked against it, so what it removes can only belong
        to the replaced verifier.
        """
        ...


class OwnerVerifierStore(Protocol):
    """The durable compare-and-set behind ``OwnerPasswordChange.adopt``."""

    def replace_owner_verifier(self, expected: str, replacement: str) -> bool: ...


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
# The Library lenses a store filters by; None reads every work, the `all` lens.
# A work no provider matched is neither needing attention nor needing none, so
# it has a lens of its own (#1407), and so has a work with nothing open whose
# latest preparation failed (#1413). Looks wrong cuts across all four: a work
# whose current values have a shape that is usually a mistake (#1334). So does
# New: a work first listed since the owner's last visit (#1351).
LibraryLensFilter = Literal[
    "needs_attention",
    "no_attention_needed",
    "not_matched",
    "preparation_failed",
    "flagged",
    "new",
]
_LIBRARY_LENSES: dict[str, LibraryLensFilter | None] = {
    "all": None,
    "new": "new",
    "flagged": "flagged",
    "needs_attention": "needs_attention",
    "no_attention_needed": "no_attention_needed",
    "not_matched": "not_matched",
    "preparation_failed": "preparation_failed",
}

_Validated = TypeVar("_Validated")


def _library_counts(
    total: int,
    needs_attention: int,
    not_matched: int,
    preparation_failed: int,
    flagged: int,
    new: int,
) -> dict[str, int]:
    """Library's lens counts: what is left of All is the no-attention lens.

    Looks wrong and New are each counted on their own, across the four that
    partition All.
    """
    return {
        "all": total,
        "flagged": flagged,
        "new": new,
        "needs_attention": needs_attention,
        "no_attention_needed": total
        - needs_attention
        - not_matched
        - preparation_failed,
        "not_matched": not_matched,
        "preparation_failed": preparation_failed,
    }


@dataclass(frozen=True, slots=True)
class DocumentReuse(Generic[_Validated]):
    """How one kind of validated object is held frozen and handed out detached."""

    kind: str
    freeze: Callable[[_Validated], _Validated]
    thaw: Callable[[_Validated], _Validated]


class ValidatedDocumentCache:
    """Bounded reuse of validated immutable browse objects across requests (#1226).

    An entry is keyed by its kind and the exact document strings it was
    validated from, so it is found only for those same bytes, and holds the
    object frozen: each reader receives a detached copy, so no reader's object
    is shared with another. Only a parse that returned is retained; a refusal
    is never stored, so the same bytes are parsed again and refuse exactly as
    before. Least recently used entries are evicted beyond the entry and
    document-byte bounds (ADR-0083).
    """

    def __init__(
        self, max_entries: int = 512, max_document_bytes: int = 8 * 1024 * 1024
    ) -> None:
        self._max_entries = max_entries
        self._max_document_bytes = max_document_bytes
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, ...], tuple[object, int]] = OrderedDict()
        self._document_bytes = 0

    def reused(
        self, reuse: DocumentReuse[_Validated], *documents: object
    ) -> _Validated | None:
        """A detached copy of what these exact documents validated as, if retained."""
        if any(type(document) is not str for document in documents):
            return None
        key = (reuse.kind, *cast("tuple[str, ...]", documents))
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
        return reuse.thaw(cast("_Validated", entry[0]))

    def retain(
        self, reuse: DocumentReuse[_Validated], value: _Validated, *documents: object
    ) -> None:
        """Hold a frozen copy of what these exact documents just validated as."""
        if any(type(document) is not str for document in documents):
            return
        key = (reuse.kind, *cast("tuple[str, ...]", documents))
        size = sum(len(document.encode("utf-8")) for document in key[1:])
        if size > self._max_document_bytes:
            return
        frozen = reuse.freeze(value)
        with self._lock:
            if key in self._entries:
                return
            self._entries[key] = (frozen, size)
            self._document_bytes += size
            while (
                len(self._entries) > self._max_entries
                or self._document_bytes > self._max_document_bytes
            ):
                self._document_bytes -= self._entries.popitem(last=False)[1][1]


def _case_facts(
    case: CaseBrowseFacts, convert: Callable[[dict[str, object]], dict[str, object]]
) -> CaseBrowseFacts:
    return replace(
        case,
        target=convert(case.target),
        proposals=tuple(convert(value) for value in case.proposals),
    )


def _scope(
    scope: DecisionScope, convert: Callable[[dict[str, object]], dict[str, object]]
) -> DecisionScope:
    return replace(
        scope,
        proposals=tuple(convert(value) for value in scope.proposals),
        offers=tuple(
            tuple(convert(value) for value in offer) for offer in scope.offers
        ),
    )


# A ReviewCase already holds its content frozen and hands out detached copies.
REVIEW_CASE_REUSE = DocumentReuse[ReviewCase](
    "review_case", lambda case: case, lambda case: case
)
BROWSE_FACTS_REUSE = DocumentReuse[tuple[CaseBrowseFacts, LibraryReportFacts]](
    "browse_facts",
    lambda facts: (_case_facts(facts[0], _freeze_json_object), facts[1]),
    lambda facts: (_case_facts(facts[0], _thaw_json_object), facts[1]),
)
BROWSE_SCOPE_REUSE = DocumentReuse[DecisionScope](
    "browse_scope",
    lambda scope: _scope(scope, _freeze_json_object),
    lambda scope: _scope(scope, _thaw_json_object),
)


def _reused_or_parsed(
    validated: ValidatedDocumentCache | None,
    reuse: DocumentReuse[_Validated],
    parse: Callable[[], _Validated],
    *documents: str,
) -> _Validated:
    """Reuse what these exact documents validated as, or parse and retain them."""
    value = None if validated is None else validated.reused(reuse, *documents)
    if value is None:
        value = parse()
        if validated is not None:
            validated.retain(reuse, value, *documents)
    return value


class ReviewLibraryStore(Protocol):
    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        lens: LibraryLensFilter | None,
        library_labels: LibraryLabels = (),
        validated: ValidatedDocumentCache | None = None,
        now: int = 0,
    ) -> ReviewLibrarySnapshot: ...
    def read_review_attention(
        self,
        search: str | None,
        limit: int,
        offset: int,
        library_labels: LibraryLabels = (),
    ) -> ReviewAttentionSnapshot: ...
    def read_review_library_progress(
        self, library_labels: LibraryLabels = (), now: int = 0
    ) -> ReviewLibraryProgress: ...
    def read_current_report_document(
        self, connection: str, library: str, item: str
    ) -> str | None: ...
    def read_review_covers(
        self,
        view: CoverView,
        limit: int,
        after: CoverKey | None,
        review_case_id: str | None = None,
    ) -> CoverPageSnapshot: ...
    def read_switch_cover_previews(self, plan_id: str) -> frozenset[str]: ...
    def read_cover_calibration(self) -> tuple[CalibrationRow, ...]: ...


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
    def review_case_level_offer(self, review_case_id: str) -> str | None: ...
    def require_reconciled_case(self, review_case_id: str, refused_at: int) -> None: ...
    def previous_field_values(
        self, connection: str, library: str, work_id: str
    ) -> tuple[tuple[str, object], ...]: ...
    def written_field_values(
        self, review_case_id: str
    ) -> tuple[tuple[str, object], ...]: ...
    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
        cover_confirmation: dict[str, object] | None = None,
    ) -> str: ...


# Sign-in attempts are counted per client over five minutes (#1384). The
# global ceiling is a backstop twenty times the per-client limit: one client
# can never reach it alone, it takes twenty distinct identities to, and it
# still caps the Argon2 work unauthenticated callers can force at one
# verification every three seconds.
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_CLIENT_ATTEMPTS = 5
_LOGIN_GLOBAL_ATTEMPTS = 100


class OwnerSessionService:
    """Owner login, authentication, rotation, and revocation over one store.

    ``ReviewSessionStore`` is a protocol the composition root binds to an
    arbitrary adapter, and the password verifier is an injected callable, so
    neither declares an error type and no call below has an enumerable raisable
    set. Every handler in this class therefore stays broad over the one call it
    guards and converts the failure into a ``ReviewWebError`` with the cause
    chained: none discards a failure, and none reads a fault as an answer the
    store or the verifier gave. The two that answer with something other than
    ``persistence`` say why where they stand. The one deliberate exception is
    the purge after a committed password change: the change has already
    committed and every purged row is unreachable once the successor is live,
    so its failure is reported in the ``owner_password_change`` event rather
    than as an error.
    """

    def __init__(
        self,
        repository: ReviewSessionStore,
        verify_password: Callable[[str], bool],
        session_digest: Callable[[str], str],
        clock: Callable[[], int],
        lifetime_seconds: int,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
        *,
        client_digest: Callable[[str], str],
        password_change: OwnerPasswordChange | None = None,
    ) -> None:
        self._repository = repository
        self._password_change = password_change
        self._verify_password = verify_password
        self._session_digest = session_digest
        self._client_digest = client_digest
        self._clock = clock
        self._lifetime_seconds = lifetime_seconds
        self._token_factory = token_factory

    def login(self, password: str, client: str) -> SessionTokens:
        """Sign in from ``client``, the interface's trusted-resolution identity.

        The identity reaches the store only as ``client_digest``'s keyed HMAC,
        so no address is persisted, and it never enters an error.
        """
        if type(password) is not str or not 1 <= len(password.encode("utf-8")) <= 1024:
            raise ReviewWebError("invalid_credentials")
        # One snapshot of a replaceable verifier, so a sign-in that straddles a
        # password change cannot prove the old password and receive a session
        # keyed by the new one.
        credential = (
            None if self._password_change is None else self._password_change.current()
        )
        verify, session_digest, client_digest = (
            (self._verify_password, self._session_digest, self._client_digest)
            if credential is None
            else (
                credential.verify,
                credential.session_digest,
                credential.client_digest,
            )
        )
        now = self._clock()
        try:
            client_key = client_digest(client)
            permitted = self._repository.begin_login(
                now,
                client_key,
                _LOGIN_WINDOW_SECONDS,
                _LOGIN_CLIENT_ATTEMPTS,
                _LOGIN_GLOBAL_ATTEMPTS,
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if permitted is not True:
            raise ReviewWebError("throttled")
        try:
            verified = verify(password)
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
                session_digest(session_token),
                _digest(csrf_token),
                now,
                expires_at,
                client_key,
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

    def logout_others(self, session_token: str, csrf_token: str) -> int:
        """End every session but this one, and keep this one and its CSRF token.

        In store mode the call holds off a password change in flight, so it can
        never delete the session that change has just issued. Returns how many
        other session rows were removed.
        """
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        if not _bounded_token(csrf_token):
            raise ReviewWebError("csrf")
        change = self._password_change
        try:
            with nullcontext() if change is None else change.exclusive():
                outcome, ended = self._repository.end_other_review_sessions(
                    self._session_digest(session_token),
                    _digest(csrf_token),
                    self._clock(),
                )
        except Exception as error:
            _owner_sign_out_others_event("persistence", None)
            raise ReviewWebError("persistence") from error
        if outcome != "ended":
            code = outcome if outcome in {"unauthorized", "csrf"} else "persistence"
            _owner_sign_out_others_event(code, None)
            raise ReviewWebError(code)
        _owner_sign_out_others_event("signed_out", ended)
        return ended

    def change_password(
        self,
        session_token: str,
        csrf_token: str,
        current_password: str,
        new_password: str,
        confirmation: str,
        client: str,
    ) -> SessionTokens:
        """Replace the owner password and keep only this browser signed in.

        Every check runs against one snapshot of the live verifier, and the
        replacement commits only while that snapshot is still the stored
        verifier. Nothing after the commit can raise: the old sessions end at
        the swap, because their digests are keyed by the replaced verifier,
        and the purge that deletes their rows is best effort.
        """
        change = self._password_change
        if change is None:
            raise ReviewWebError("settings_managed_by_file")
        if not _bounded_token(session_token):
            raise ReviewWebError("unauthorized")
        now = self._clock()
        credential = change.current()
        try:
            own_session_digest = credential.session_digest(session_token)
            _, expected_csrf, _ = self._repository.authenticate_review_session(
                own_session_digest, now
            )
        except Exception as error:
            # Fail closed, as ``authenticate`` does.
            raise ReviewWebError("unauthorized") from error
        if not _bounded_token(csrf_token) or not secrets.compare_digest(
            expected_csrf, _digest(csrf_token)
        ):
            raise ReviewWebError("csrf")
        if (
            not owner_password(new_password)
            or new_password != confirmation
            or type(current_password) is not str
        ):
            raise ReviewWebError("invalid_password")
        try:
            tokens, ended = self._replace_password(
                change,
                credential,
                own_session_digest,
                current_password,
                new_password,
                client,
                now,
            )
        except ReviewWebError as error:
            _owner_password_change_event(error.code, None)
            raise
        _owner_password_change_event("changed", ended)
        return tokens

    def _replace_password(
        self,
        change: OwnerPasswordChange,
        credential: OwnerCredential,
        own_session_digest: str,
        current_password: str,
        new_password: str,
        client: str,
        now: int,
    ) -> tuple[SessionTokens, int | None]:
        """Spend a sign-in attempt, verify, then commit the successor.

        Returns the new session and how many sessions the purge removed, or
        ``None`` when the purge failed.
        """
        try:
            permitted = self._repository.begin_login(
                now,
                credential.client_digest(client),
                _LOGIN_WINDOW_SECONDS,
                _LOGIN_CLIENT_ATTEMPTS,
                _LOGIN_GLOBAL_ATTEMPTS,
            )
        except Exception as error:
            raise ReviewWebError("persistence") from error
        if permitted is not True:
            raise ReviewWebError("throttled")
        try:
            verified = credential.verify(current_password)
        except Exception as error:
            # As in ``login``: a wrong password is ``False``, so a raise is a
            # hasher or stored-digest fault no password can satisfy.
            raise ReviewWebError("runtime") from error
        if not verified:
            raise ReviewWebError("current_password_invalid")
        try:
            successor = change.successor(new_password)
            session_token = self._token_factory(32)
            csrf_token = self._token_factory(32)
            session_digest = successor.session_digest(session_token)
            client_key = successor.client_digest(client)
        except Exception as error:
            raise ReviewWebError("runtime") from error
        if not 32 <= len(session_token) <= 128 or not 32 <= len(csrf_token) <= 128:
            raise ReviewWebError("runtime")
        expires_at = now + self._lifetime_seconds
        # Held from the new session through the swap, so a sign-out of other
        # browsers cannot delete the session this change has just issued.
        with change.exclusive():
            # The passwords were checked outside the hold. A browser signed out
            # meanwhile changes nothing: its session is read again before
            # anything is written.
            try:
                self._repository.authenticate_review_session(own_session_digest, now)
            except Exception as error:
                raise ReviewWebError("unauthorized") from error
            try:
                self._repository.create_review_session(
                    session_digest, _digest(csrf_token), now, expires_at, client_key
                )
            except Exception as error:
                raise ReviewWebError("persistence") from error
            ended: int | None = None

            def purge() -> None:
                # Runs between the commit and the swap, so every other row is keyed
                # by the replaced verifier: a sign-in or a later change under the
                # successor cannot have written one yet. Best effort, because those
                # rows are unreachable either way.
                nonlocal ended
                try:
                    ended = self._repository.end_review_sessions_except(session_digest)
                except Exception:
                    ended = None

            try:
                adopted = change.adopt(credential, successor, purge)
            except Exception as error:
                self._discard_session(session_digest, csrf_token, now)
                raise ReviewWebError("persistence") from error
            if adopted is not True:
                # Another change replaced the verifier this request was checked
                # against, so its session is gone with the rest.
                self._discard_session(session_digest, csrf_token, now)
                raise ReviewWebError("unauthorized")
        return SessionTokens(session_token, csrf_token, expires_at), ended

    def _discard_session(self, session_digest: str, csrf_token: str, now: int) -> None:
        """Best-effort revoke of a session whose password change did not commit.

        Its digest is keyed by a verifier that was never adopted, so no request
        can reach it even if this revoke fails.
        """
        try:
            self._repository.revoke_review_session(
                session_digest, _digest(csrf_token), now
            )
        except Exception:
            pass


_OWNER_PASSWORD_CHANGE_OUTCOMES: Final = frozenset(
    {
        "changed",
        "current_password_invalid",
        "persistence",
        "runtime",
        "throttled",
        "unauthorized",
    }
)


def _owner_password_change_event(outcome: str, sessions_ended: int | None) -> None:
    """Write one value-free line for a password change that reached the throttle.

    It carries a closed outcome and the number of sessions the purge removed,
    or null when the purge failed or nothing committed. Never a password,
    verifier, digest, token or client. A failing sink cannot fail the request.
    """
    ended = (
        sessions_ended
        if type(sessions_ended) is int and 0 <= sessions_ended <= 2**31
        else None
    )
    known = outcome if outcome in _OWNER_PASSWORD_CHANGE_OUTCOMES else "runtime"
    event = {
        "event": "owner_password_change",
        "level": (
            "info"
            if (known == "changed" and ended is not None)
            or known in {"current_password_invalid", "throttled", "unauthorized"}
            else "warning"
        ),
        "outcome": known,
        "sessions_ended": ended,
    }
    try:
        sys.stderr.write(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        )
    except Exception:
        pass


_OWNER_SIGN_OUT_OTHERS_OUTCOMES: Final = frozenset(
    {"signed_out", "csrf", "unauthorized", "persistence"}
)


def _owner_sign_out_others_event(outcome: str, sessions_ended: int | None) -> None:
    """Write one value-free line for a sign-out of other browsers.

    It carries a closed outcome and the number of other session rows removed,
    or null when nothing was removed. Never a cookie, token, digest, client or
    password. A failing sink cannot fail the request.
    """
    known = outcome if outcome in _OWNER_SIGN_OUT_OTHERS_OUTCOMES else "persistence"
    ended = (
        sessions_ended
        if known == "signed_out"
        and type(sessions_ended) is int
        and 0 <= sessions_ended <= 2**31
        else None
    )
    event = {
        "event": "owner_sign_out_others",
        "level": "warning" if known == "persistence" else "info",
        "outcome": known,
        "sessions_ended": ended,
    }
    try:
        sys.stderr.write(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        )
    except Exception:
        pass


# The policy category each destination's posture is read under; any other
# destination is its own category.
_POLICY_CATEGORY_BY_DESTINATION: Mapping[str, str] = MappingProxyType(
    {
        "description": "description_fill",
        "isbn": "isbn_form",
        "title": "title_layout",
        "authors": "contributor_names",
        "narrators": "contributor_names",
        "language": "language_rendering",
        "publishedDate": "date_precision",
        "publisher": "publisher_preference",
        "cover": "cover_order",
    }
)


def _ask_destinations(
    snapshot: CurrentReviewCaseSnapshot, proposals: tuple[dict[str, object], ...]
) -> frozenset[str]:
    """The proposed destinations the live policy sets to Ask me (#1707)."""
    if snapshot.resolved_policy_document is None:
        return frozenset()
    try:
        postures = dict(
            ResolvedMetadataPolicy.from_data(
                json.loads(snapshot.resolved_policy_document)
            ).destinations
        )
    except Exception as error:
        raise ReviewWebError("unavailable") from error
    destinations = (str(proposal["destination"]) for proposal in proposals)
    return frozenset(
        destination
        for destination in destinations
        if postures.get(_POLICY_CATEGORY_BY_DESTINATION.get(destination, destination))
        == "ask"
    )


@dataclass(frozen=True, slots=True)
class _ReviewCaseReadContext:
    """What every field of one current case is projected from."""

    case: ReviewCase
    report: MetadataReport
    snapshot: CurrentReviewCaseSnapshot
    decisions: dict[str, HumanDecision]
    automatic: dict[str, dict[str, object]]
    automatic_source_outcome_ids: dict[str, str]
    automatic_sources: dict[str, tuple[str, ...]]
    target_fields: dict[str, dict[str, object]]
    evidence: dict[str, dict[str, str | None]]
    suspension_causes: dict[str, ReviewFieldCause]
    proposals: tuple[dict[str, object], ...]
    publication_date_present: bool
    current_cover_consideration: dict[str, object] | None
    cover_is_current: bool
    cover_refresh_required: bool
    cover_undecided: bool
    ask_destinations: frozenset[str]


def _automatic_citations(
    snapshot: CurrentReviewCaseSnapshot, review_case_id: str
) -> dict[str, tuple[str, ...]]:
    """Each automatic source outcome's citations, one set per outcome id."""
    citations: dict[str, tuple[str, ...]] = {}
    for document in snapshot.automatic_source_outcome_documents:
        outcome_set = ShadowOutcomeSet.from_json(document)
        if outcome_set.review_case_id != review_case_id:
            raise ReviewWebError("unavailable")
        for outcome in outcome_set.outcomes:
            if (
                citations.setdefault(outcome.outcome_id, outcome.citation_ids)
                != outcome.citation_ids
            ):
                raise ReviewWebError("unavailable")
    return citations


def _automatic_field_decision(
    context: _ReviewCaseReadContext,
    destination: str,
    alternatives: list[dict[str, object]],
) -> tuple[dict[str, object] | None, list[str]]:
    """The field's current automatic decision with its selected value and sources."""
    report = context.report
    snapshot = context.snapshot
    automatic_sources = context.automatic_sources
    automatic_source_outcome_ids = context.automatic_source_outcome_ids
    automatic_decision = context.automatic.get(destination)
    automatic_selection_sources: list[str] = []
    if automatic_decision is not None:
        automatic_selection_sources = provider_source_labels(
            report,
            list(automatic_sources.get(automatic_source_outcome_ids[destination], ())),
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
    return automatic_decision, automatic_selection_sources


def _cover_admission(
    context: _ReviewCaseReadContext,
    destination: str,
    result: dict[str, object],
    *,
    preparation_possible: bool,
) -> tuple[bool, bool]:
    """Whether a cover's choices count now, and whether a comparison can be prepared."""
    snapshot = context.snapshot
    cover_is_current = context.cover_is_current
    current_cover_consideration = context.current_cover_consideration
    cover_refresh_required = context.cover_refresh_required
    cover_undecided = context.cover_undecided
    cover_admits = (
        destination != "cover"
        or snapshot.cover_consideration_document is None
        or (
            cover_is_current
            and current_cover_consideration is not None
            and current_cover_consideration["ready"] is True
        )
        or (
            cover_refresh_required
            and result["outcome"] == "unresolved_choice"
            and cover_undecided
        )
    )
    cover_preparation_available = (
        destination == "cover"
        and preparation_possible
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
    return cover_admits, cover_preparation_available


def _classify_field(
    context: _ReviewCaseReadContext,
    proposal: dict[str, object],
    *,
    decision: HumanDecision | None,
    automatic_decision: dict[str, object] | None,
    authored_selection: object,
    cover_admits: bool,
    cover_preparation_available: bool,
    actions: tuple[HumanDecisionAction, ...],
    owner_entry: bool,
    pin: Literal["in_force", "changed"] | None,
    automatic_decides: bool,
) -> ReviewFieldClassification:
    """Classify one field from its proposal, decisions and cover state.

    `automatic_decides` says Automatic decided the field on this case, whether
    or not the record's answer lets that decision settle it.
    """
    snapshot = context.snapshot
    current_cover_consideration = context.current_cover_consideration
    cover_is_current = context.cover_is_current
    target_fields = context.target_fields
    result = proposal["result"]
    assert type(result) is dict
    destination = cast(str, proposal["destination"])
    alternatives = result["alternatives"]
    assert type(alternatives) is list
    reasons = result["reasons"]
    assert type(reasons) is list
    # The persisted facts settle an undecided choice its policy's
    # options keep (#1310); an immaterial one is #1253's, not this. A
    # proposed fill the evaluated policy keeps (tags while they are off)
    # is settled the same way on every medium (#1661).
    single_source = _single_source_choice(proposal)
    # A description every source repeats offers its choices only beside
    # description advice (`_field`): optional, never attention (#1905).
    repeated = preserved_description(proposal) and bool(actions)
    # The measured stage's decision over a current comparison: a suggested
    # switch is optional work, never attention, and a hold (a title naming
    # another book, or a current cover whose sources do) asks (#1363 S9,
    # #1362 S13), as `_REVIEW_FIELD_ATTENTION_REASON_SQL` reads it. Like SQL,
    # the field's attention also needs an owner action (`has_owner_action`
    # below), so a hold with nothing to choose shows on the book page only.
    stage_outcome = _cover_stage_outcome(destination, current_cover_consideration)
    cover_suggested = stage_outcome in {"suggest", "replace"}
    # A cover the stage kept with no fault, read or not, is optional too: a
    # read that settled nothing keeps the cover until the next scan and the
    # owner has no keep to record for it (#1362, R1 as amended).
    cover_optional = cover_suggested or _cover_stage_kept(
        destination, current_cover_consideration
    )
    policy_settled = (
        result["outcome"] == "unresolved_choice"
        and "work_alternative_immaterial" not in reasons
        and not _kept_cover(proposal)
        and not single_source
        and destination in snapshot.settled_choice_destinations
    ) or (
        result["outcome"] == "propose_change"
        and destination in snapshot.policy_kept_destinations
    )
    # A human decision other than the case's own answer from the record.
    other_human = (
        decision is not None
        # With nothing to choose, an answer from elsewhere settles its field
        # unless Automatic's decision does, under an open Resume (C2b-18).
        or (
            _record_answered_elsewhere(snapshot, destination)
            and not actions
            and automatic_decision is None
        )
        or (
            destination == "cover"
            and cover_is_current
            and snapshot.current_cover_selection is not None
            and snapshot.current_cover_selection[1] == "owner"
        )
        or (
            type(authored_selection) is dict
            and authored_selection.get("origin") == "human"
        )
    )
    own_answer = (
        destination != "cover" and destination in snapshot.own_answer_destinations
    )
    classify = partial(
        classify_review_field,
        attention_candidate=(
            (
                _record_answered_elsewhere(snapshot, destination)
                or destination in snapshot.withdrawn_destinations
            )
            and bool(actions)
        )
        or not repeated
        and (
            (
                cover_admits
                and _needs_attention(
                    proposal,
                    snapshot.automatic_attention_destinations,
                    policy_attention_destinations=snapshot.policy_attention_destinations,
                    settled_choice_destinations=snapshot.settled_choice_destinations,
                    cover_suggested=cover_optional,
                )
            )
            or (
                destination == "cover"
                and cover_is_current
                and current_cover_consideration is not None
                and current_cover_consideration["ready"] is True
                and (
                    stage_outcome == "hold"
                    or (not _kept_cover(proposal) and not cover_optional)
                )
            )
        ),
        automatic_outcome=(
            "candidate"
            if authored_selection is not None
            else "abstention"
            # A repeated description is optional, never attention, so neither
            # an abstention nor a pause asks about it (#1905).
            if destination in snapshot.shadow_abstention_destinations and not repeated
            else None
            if automatic_decision is None
            else str(automatic_decision["outcome"])
        ),
        override_available=(
            destination not in snapshot.override_blocked_destinations
            and (
                destination != "cover"
                or (
                    current_cover_consideration is not None
                    and current_cover_consideration["current"] is True
                    and current_cover_consideration["ready"] is True
                )
            )
        ),
        suspended=destination in snapshot.suspended_destinations,
        cause=(
            "automatic_safety_suspended"
            if destination in snapshot.automatic_safety_suspended_destinations
            and not repeated
            else context.suspension_causes.get(destination)
        ),
        current_value=target_fields[destination]["value"],
        proposed_values=tuple(alternative["value"] for alternative in alternatives),
        # A recording's undecided fill, a kept readable cover, and a
        # choice the policy's options settle (a populated subjective
        # value a fill-only option keeps, or tags while they are off,
        # including a single proposed list for empty Tags) stay the
        # owner's to choose in their case without joining the queue
        # (#1249, #1310, #1661), and so does a value only one
        # uncorroborated source proposes (#1310). A kept cover with no
        # current comparison is still choosable: the owner can prepare one.
        owner_choosable=automatic_decision is None
        and (
            repeated
            or (
                cover_admits
                and (
                    context.case.media_kind == "audiobook"
                    or policy_settled
                    or single_source
                    # An ebook field set to Ask me that the queue does not ask
                    # about is the owner's optional choice too (#1707).
                    or destination in context.ask_destinations
                    # A suggested switch is the owner's optional choice (S9),
                    # as is a cover kept with no fault (#1362).
                    or cover_optional
                )
                and bool(actions)
            )
            or (
                _kept_cover(proposal)
                and (
                    (cover_admits and bool(actions))
                    or (
                        cover_preparation_available
                        and result["outcome"] == "unresolved_choice"
                    )
                )
            )
        ),
        changeable=bool(actions) or owner_entry,
        # With no alternative to list, an unsupported field is a
        # contextual row, which offers no controls (below).
        owner_entry=owner_entry and bool(alternatives),
        immaterial_alternatives="work_alternative_immaterial" in reasons,
        kept_cover=_kept_cover(proposal) and not cover_suggested,
        kept_by_fill_policy=policy_settled and destination != "tags",
        kept_by_tags_off=policy_settled and destination == "tags",
        single_source=single_source,
        pin=pin,
        repeated_description=repeated,
    )
    decided = classify(has_human_decision=other_human or own_answer)
    # The case's own answer with no decision of the case to show, or an
    # answer that is only a pin taken back on any case, reads as main did
    # (C2b-25, C2b-27, C2b-28): it holds the field only where Automatic
    # decided it, as an ordinary hold (ADR-0085, Amendment 6). Anywhere else
    # the field reads as it would without the answer: asked, abstained,
    # optional or with nothing to decide, and in Preview held beside another
    # offered value, so turning on Automatic leaves it as it is. A requested
    # Resume is already on its way and reads decided. Any other answer from
    # an earlier case or episode reads by its own rules (C2b-15).
    if (
        not own_answer
        or other_human
        or automatic_decides
        or destination in snapshot.return_requested_destinations
    ):
        return decided
    return classify(has_human_decision=False, held_in_preview=snapshot.preview)


def _automatic_settles(snapshot: CurrentReviewCaseSnapshot, destination: str) -> bool:
    """Whether Automatic's decision would settle the field, as Attention reads
    it: the record holds no answer for it, or holds one with a Resume open
    that has not failed, and the owner did not withdraw the field beside it
    on the case, or a Resume took the withdrawal back (ADR-0108,
    `withdrawn_destinations`)."""
    return (
        destination not in snapshot.held_destinations
        or destination in snapshot.return_requested_destinations
    ) and destination not in snapshot.withdrawn_destinations


def _record_answered_elsewhere(
    snapshot: CurrentReviewCaseSnapshot, destination: str
) -> bool:
    """Whether the record holds the field's answer from an earlier case or
    episode, which nothing carried here. As Attention reads it, the field is
    asked where the case offers the owner a decision on it, and the answer
    settles it where none is offered (C2b-15, C2b-18, S16). A pin settles or
    asks its field ahead of this, and an answer that is only a pin taken back
    is read as the case's own (`own_answer_destinations`, C2b-28); a cover's
    answer is the book's own pointer instead."""
    return (
        destination != "cover"
        and destination in snapshot.held_destinations
        and destination not in snapshot.own_answer_destinations
        and all(row[0] != destination for row in snapshot.pins)
    )


def _owner_authored(selection: object) -> bool:
    """Whether the description's current authored selection is the owner's."""
    return type(selection) is dict and selection.get("origin") == "human"


def _resume_offered(snapshot: CurrentReviewCaseSnapshot, destination: str) -> bool:
    """Whether the field shows Resume automatic handling: offered from the
    record (C0 `resume_offer`), where a failed request is asked again, or
    already requested, which shows as requested with nothing to press. A
    failed request outside the offer shows no Resume, since the store would
    refuse it (C0). A cover only where the stage would switch it but for the
    owner, never for a cover chosen in To decide (K9). A pinned field offers
    Unpin instead (ADR-0085, Amendment 2)."""
    if destination == "cover":
        return (
            snapshot.cover_automatic_held
            and destination in snapshot.resume_offer_destinations
        )
    return (
        destination in snapshot.resume_offer_destinations
        or destination in snapshot.return_requested_destinations
    )


def _description_advice_offers(snapshot: CurrentReviewCaseSnapshot) -> bool:
    """Whether description advice is shown for the field to choose against.

    Current advice with a candidate, or a selection the owner can reverse
    after a verified write retired its binding (#1905).
    """
    advice = snapshot.description_advice
    return advice is not None and (
        (advice.get("current") is True and advice.get("candidate") is not None)
        or advice.get("selection") is not None
    )


def _authored_description_selection(
    snapshot: CurrentReviewCaseSnapshot, destination: str
) -> object:
    """The current authored description selection, which displaces automatic."""
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
    return authored_selection


def _pin_control(
    case: ReviewCase,
    destination: str,
    pin_state: str | None,
    *,
    change_waiting: bool,
) -> dict[str, object] | None:
    """Return whether a field offers Pin and Unpin (#1346, J2).

    A readable field other than the cover can have a value pinned, or says
    that a change to it waits to be written.
    """
    pin_offer = (
        None
        if destination == "cover" or pinned_field_fingerprint(case, destination) is None
        else "change_waiting"
        if change_waiting
        else "available"
    )
    if pin_offer is None and pin_state is None:
        return None
    return {"pin": pin_offer, "unpin": pin_state is not None}


def _cover_taste_suggestion(
    verdict: tuple[str, str, str, int] | None,
) -> dict[str, object] | None:
    """The suggested cover with its cited rules, each with its section, as
    that verdict's profile version holds them, shown until the field is
    judged again, and the version in use beside it (J8, Q4)."""
    if verdict is None:
        return None
    verdict_id, profile_document, verdict_document, in_use = verdict
    value = json.loads(verdict_document)
    rules = {
        rule.number: rule
        for rule in TasteProfile.from_rules(json.loads(profile_document)["rules"]).rules
    }
    if value["decision"] != "choose" or any(
        number not in rules for number in value["rules"]
    ):
        raise ReviewWebError("unavailable")
    return {
        "verdict_id": verdict_id,
        "version": value["version"],
        "version_in_use": in_use,
        "candidate_reference": value["candidate_reference"],
        "strength": "strong"
        if value["confidence"] >= TASTE_STRONG_CONFIDENCE
        else "likely",
        "rules": [
            {
                "number": number,
                "section": rules[number].section,
                "text": rules[number].text,
            }
            for number in value["rules"]
        ],
    }


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
        detail, report, snapshot, _ = self._read_cited(review_case_id, history_limit)
        return detail, report, snapshot

    def _read_cited(
        self, review_case_id: str, history_limit: int | None = None
    ) -> tuple[
        dict[str, object],
        MetadataReport,
        CurrentReviewCaseSnapshot,
        dict[str, tuple[str, ...]],
    ]:
        """The case detail, with each automatic source outcome's citations
        as it read them, so History never parses them again (#2003)."""
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
            report = snapshot.report
            if report.report_id != case.report_id:
                raise ReviewWebError("unavailable")
            decisions: dict[str, HumanDecision] = {}
            automatic: dict[str, dict[str, object]] = {}
            automatic_source_outcome_ids: dict[str, str] = {}
            automatic_sources = _automatic_citations(snapshot, review_case_id)
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
                    if decision.outcome in {"pin", "unpin"}:
                        # Never a field's latest decision: a pin acts through
                        # its hold, and the answer under it stands (ADR-0085,
                        # Amendment 2).
                        continue
                    decisions[decision.destination] = decision
                    if decision.outcome == "withdraw":
                        # A withdrawn field is undecided again (ADR-0085). A
                        # Book matching cause row reads the same way: no
                        # human decision (#1355, AC2).
                        decisions.pop(decision.destination, None)
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
            destination.value: _presented_field(value)
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
        # Projected once: each `case.proposals` re-parses the proposal set
        # (#1215). Each field shows its current offer: its live proposal, or
        # the case's level offer where Book matching substitutes it (#1355).
        proposals = case.proposals
        try:
            proposals = compose_offer(
                case.review_case_id, proposals, snapshot.level_offer_document
            )
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        publication_date_present = any(
            value["destination"] == "publishedDate" for value in proposals
        )
        context = _ReviewCaseReadContext(
            case=case,
            report=report,
            snapshot=snapshot,
            decisions=decisions,
            automatic=automatic,
            automatic_source_outcome_ids=automatic_source_outcome_ids,
            automatic_sources=automatic_sources,
            target_fields=target_fields,
            evidence=evidence,
            suspension_causes=suspension_causes,
            proposals=proposals,
            publication_date_present=publication_date_present,
            current_cover_consideration=current_cover_consideration,
            cover_is_current=cover_is_current,
            cover_refresh_required=cover_refresh_required,
            cover_undecided=cover_undecided,
            ask_destinations=_ask_destinations(snapshot, proposals),
        )
        for proposal in proposals:
            projected = self._field(context, proposal)
            if projected is not None:
                fields.append(projected)
        fields.sort(key=lambda value: not value["needs_attention"])
        return (
            {
                "comparison_notices": _provider_comparison_notices(report),
                "current": True,
                # The case waits for its Book matching check: choices are
                # refused and owner writes wait until it is reconciled at the
                # configured level (#1355, S10).
                "checking": snapshot.checking,
                "check_given_up": snapshot.check_given_up,
                "fields": fields,
                # An admitted audiobook opens its case, not its report, so the
                # media-health section has to reach the owner here too -- and
                # it is derived from the same retained report the fields above
                # were read from, never from a second source.
                "media_health": _media_health(report),
                "recording_identity_basis": recording_identity_basis(report),
                "recording_admission_basis": recording_admission_basis(report),
                "audiobook_review_scope": audiobook_review_scope(report),
                "review_case_id": case.review_case_id,
                "schema_version": REVIEW_CASE_DETAIL_SCHEMA,
                # Read models never learn write authority. The composition root
                # stamps the truth before the view leaves the application, and
                # the default keeps a surface that never asks honest.
                "writes_consented": False,
                "target": {
                    "authors": _report_authors(report.target),
                    "connection": report.target.connection,
                    "item": report.target.item,
                    "library": report.target.library,
                    "media_kind": report.file.format,
                    "title": report.target.current_title or "Untitled",
                },
            },
            report,
            snapshot,
            automatic_sources,
        )

    def _field(
        self, context: _ReviewCaseReadContext, proposal: dict[str, object]
    ) -> dict[str, object] | None:
        """Project one proposal into its review field, or None when omitted."""
        report = context.report
        snapshot = context.snapshot
        decisions = context.decisions
        target_fields = context.target_fields
        evidence = context.evidence
        proposals = context.proposals
        publication_date_present = context.publication_date_present
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
        # A decision a completed Resume took back decides nothing now; only
        # its owner control keeps the recorded line (D3, ADR-0108).
        recorded = decisions.get(destination)
        decision = (
            None
            if recorded is not None
            and recorded.decision_id in snapshot.covered_decision_ids
            else recorded
        )
        # A pin on the record and the value it keeps, as the current
        # value is shown: in force, waiting for the owner's standing answer
        # to be read back, or changed (ADR-0085, Amendment 2).
        pin = next((row[1:] for row in snapshot.pins if row[0] == destination), None)
        pin_state: Literal["in_force", "waiting", "changed"] | None = (
            None
            if pin is None
            else "in_force"
            if pin[0]
            else "changed"
            if decision is None
            else "waiting"
        )
        pinned_field: dict[str, object] | None = None
        if pin is not None:
            pinned_field = json.loads(pin[1])
            pinned_field.pop("restorable", None)
        automatic_decision, automatic_selection_sources = _automatic_field_decision(
            context, destination, alternatives
        )
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
            cast("dict[str, object]", proposal.get("lineage", {})),
        )
        authored_selection = _authored_description_selection(snapshot, destination)
        if authored_selection is not None:
            automatic_decision = None
        cover_admits, cover_preparation_available = _cover_admission(
            context,
            destination,
            result,
            preparation_possible=self._cover_considerations is not None,
        )
        actions = () if cover_preparation_available else decision_actions(proposal)
        if (
            not actions
            and preserved_description(proposal)
            and _description_advice_offers(snapshot)
        ):
            # Its sources all repeat it, so only advice offers a change: the
            # adviser's text (an accept) and Keep current (#1905).
            actions = ("keep_current", "accept")
        owner_control = _owner_control(
            proposal,
            target_fields,
            proposals,
            recorded,
            automatic_decision is not None,
            snapshot,
            context.case.media_kind,
            None
            if pinned_field is None or pin_state != "changed"
            else pinned_field["value"],
        )
        # Automatic's decision, or its authored description, decides nothing
        # while the record holds the field's answer against it (ADR-0108,
        # Attention); the page still shows it as the held choice.
        settles = _automatic_settles(snapshot, destination)
        # Automatic decided the field on this case, an automatic authored
        # description among them: main's condition for carrying the owner's
        # hold, which a case's own answer with no decision of the case to show
        # (a pin taken back) needs to decide the field (C2b-27).
        automatic_decides = automatic_decision is not None or (
            type(authored_selection) is dict
            and authored_selection.get("origin") == "automatic_policy"
        )
        owner_entry = owner_control is not None and any(
            owner_control[part] is not None for part in ("entry", "subset", "names")
        )
        classify = partial(
            _classify_field,
            context,
            proposal,
            decision=decision,
            automatic_decision=automatic_decision if settles else None,
            authored_selection=authored_selection
            if settles or _owner_authored(authored_selection)
            else None,
            cover_admits=cover_admits,
            cover_preparation_available=cover_preparation_available,
            actions=actions,
            owner_entry=owner_entry,
            automatic_decides=automatic_decides,
        )
        # A waiting pin leaves the field as its answer reads.
        classification = classify(pin=None if pin_state == "waiting" else pin_state)
        # Classification retains the field's owner context. Action
        # availability is a separate exact-proposal contract; an empty
        # action list is context-only, never an implied choice envelope.
        has_owner_action = (
            bool(actions) or cover_preparation_available or pin_state == "changed"
        )
        needs_attention = classification.needs_attention and has_owner_action
        # A pinned field sits where it would sit without the pin (#1346).
        inventory_role = _field_inventory_role(
            classify(pin=None).state
            if pin_state == "in_force"
            else classification.state,
            destination,
            report.file.format,
            alternatives,
            publication_date_present,
        )
        if inventory_role == "omitted":
            return None
        if inventory_role == "contextual":
            owner_control = None
        return self._field_document(
            context,
            proposal,
            destination=destination,
            result=result,
            reasons=reasons,
            references=references,
            eligible_alternatives=eligible_alternatives,
            choice_contexts=choice_contexts,
            decision=decision,
            automatic_decision=automatic_decision,
            automatic_selection_sources=automatic_selection_sources,
            classification=classification,
            actions=actions,
            owner_control=owner_control,
            inventory_role=inventory_role,
            # Such an answer that Automatic never decided is no hold Resume
            # releases, as on main (C2b-27).
            manual_hold=pin_state is None
            and _resume_offered(snapshot, destination)
            and (
                automatic_decides
                or decision is not None
                or destination not in snapshot.own_answer_destinations
                or destination in snapshot.return_requested_destinations
            ),
            has_owner_action=has_owner_action,
            needs_attention=needs_attention,
            pin=None
            if pin_state is None
            else {"state": pin_state, "value": pinned_field},
            pin_control=_pin_control(
                context.case,
                destination,
                pin_state,
                change_waiting=destination in snapshot.change_waiting_destinations,
            ),
        )

    def _field_document(
        self,
        context: _ReviewCaseReadContext,
        proposal: dict[str, object],
        *,
        destination: str,
        result: dict[Any, Any],
        reasons: list[Any],
        references: list[Any],
        eligible_alternatives: list[Any],
        choice_contexts: list[dict[str, object]],
        decision: HumanDecision | None,
        automatic_decision: dict[str, object] | None,
        automatic_selection_sources: list[str],
        classification: ReviewFieldClassification,
        actions: tuple[HumanDecisionAction, ...],
        owner_control: dict[str, object] | None,
        inventory_role: str,
        manual_hold: bool,
        has_owner_action: bool,
        needs_attention: bool,
        pin: dict[str, object] | None,
        pin_control: dict[str, object] | None,
    ) -> dict[str, object]:
        """The field's wire document, kept apart from the decisions above it."""
        case = context.case
        report = context.report
        snapshot = context.snapshot
        target_fields = context.target_fields
        proposals = context.proposals
        current_cover_consideration = context.current_cover_consideration
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
            "automatic_withheld_condition": None,
            # Shown only on a field that reads No change with no decision of
            # its own; the earlier choice decides nothing now (#1575). Its
            # line says turning Automatic on decides the field again, so the
            # store leaves out one the owner keeps Automatic off (#1996).
            "earlier_automatic_value": (
                dict(snapshot.earlier_automatic_values).get(destination)
                if automatic_decision is None
                and decision is None
                and classification.state == "unsupported"
                and destination != "cover"
                else None
            ),
            "automatic_selection_sources": automatic_selection_sources,
            "manual_hold": manual_hold,
            # A requested return keeps the hold until its checks finish, so
            # the field says it was requested, also after a reload (#1755).
            "manual_return_requested": manual_hold
            and destination in snapshot.return_requested_destinations,
            "manual_return_failed": manual_hold
            and destination in snapshot.return_failed_destinations,
            "classification": classification.state,
            "classification_cause": classification.cause,
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
            # Each proposed description's formatted copy, beside its value
            # (#1995, ADR-0107); every other field's are null.
            "proposed_formatted": [
                alternative_formatted(
                    report,
                    alternative["evidence_references"],
                    alternative["value"].get("value"),
                )
                if destination == "description"
                else None
                for alternative in eligible_alternatives
            ],
            "choice_contexts": choice_contexts,
            "provenance": references,
            "corroboration_sources": _corroboration_source_labels(
                report,
                references,
                reasons,
            ),
            "owner_control": owner_control,
            # This case's own write set the field in its episode, so what it
            # read before is no longer the library's value (#1523).
            "written_by_case": destination in dict(snapshot.written_values),
            # What it wrote over is a description stored with formatting a
            # restore from here would lose, so it can't be gone back to and a
            # keep is refused (#1424).
            "restore_unavailable": destination == "description"
            and destination in dict(snapshot.written_values)
            and description_unrestorable(case),
            "pin": pin,
            "pin_control": pin_control,
            "no_change_required": decision is not None
            and decision.decision_id in snapshot.no_change_decisions,
            # A refused pin waits only for the next scan to read the written
            # value back: the field's change is written (#1984).
            "change_written": destination in snapshot.change_waiting_destinations
            and destination not in snapshot.write_waiting_destinations,
            # The offer a choice binds: its value, proposal and rule (#1355).
            "offer_digest": offer_digest(proposals, destination),
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
            field["current_cover_selection_id"] = (
                None
                if snapshot.current_cover_selection is None
                else snapshot.current_cover_selection[0]
            )
            field["current_cover_selection_artifact_id"] = (
                None
                if snapshot.current_cover_selection is None
                else snapshot.current_cover_selection[2]
            )
            field["cover_taste_suggestion"] = (
                _cover_taste_suggestion(snapshot.cover_taste_verdict)
                if context.cover_is_current
                else None
            )
        if destination == "description":
            field["description_advice"] = snapshot.description_advice
        return field

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
            # The fields Book matching's level offer substitutes: the offer
            # `_read` composed and validated from this snapshot (#1355).
            substituted = level_offer_destinations(snapshot.level_offer_document)
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
                category = _POLICY_CATEGORY_BY_DESTINATION.get(destination, destination)
                if outcome is not None and outcome.policy_category is not None:
                    category = (
                        "title_layout"
                        if outcome.policy_category == "title"
                        else outcome.policy_category
                    )
                advice = field.get("description_advice")
                if (
                    dict(policy.destinations).get(category) != "automatic"
                    or field["human_decision"] is not None
                    or field["automatic_decision"] is not None
                    # An admitted automatic description is its evaluation.
                    or isinstance(advice, dict)
                    and isinstance(advice.get("selection"), dict)
                    and advice["selection"].get("origin") == "automatic_policy"
                    and isinstance(advice.get("selected_advice"), dict)
                    and advice["selected_advice"].get("current") is True
                ):
                    continue
                if outcome is not None and outcome.disposition not in {
                    "abstention",
                    "conflict",
                    "manual",
                    "unsupported",
                }:
                    continue
                # A field the level offer substitutes and makes choosable
                # offers a value; why Automatic withheld the live one is
                # untrue beside it, so the field gives its own reason (#1355).
                if (
                    destination in substituted
                    and field["manual_required"] is True
                    and field["decision_actions"]
                ):
                    continue
                (
                    field["automatic_withheld_reason"],
                    field["automatic_withheld_condition"],
                ) = _automatic_withheld(
                    field,
                    outcome,
                    destination,
                    destination in snapshot.automatic_safety_suspended_destinations
                    or destination in snapshot.suspended_destinations,
                )
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
            "schema_version": REVIEW_CASE_EVIDENCE_SCHEMA,
            "source_ledger": _provider_outcomes(provider_lane_facts(report)),
        }

    def history(self, review_case_id: str, limit: int = 50) -> dict[str, object]:
        _detail, report, snapshot, citations = self._read_cited(review_case_id, limit)
        case = ReviewCase.create_from_data(json.loads(snapshot.case_document)["case"])
        target_fields = canonical_target_fields(report.target.fields)
        automatic_values: dict[
            str,
            tuple[
                dict[str, object] | None,
                dict[str, object] | None,
                str | None,
                str | None,
            ],
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
                # Each description value's own copy: the report's, or the one
                # the decision's own citations agree on (#2003).
                previous_formatted = current.get("formatted") if previous else None
                automatic_values[decision_id] = (
                    previous,
                    selected,
                    cast("str | None", previous_formatted),
                    alternative_formatted(
                        report,
                        citations.get(historical_decision.source_outcome_id, ()),
                        selected.get("value"),
                    )
                    if historical_decision.outcome == "candidate" and selected
                    else cast("str | None", previous_formatted)
                    if selected
                    else None,
                )
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
                previous, selected, previous_formatted, selected_formatted = joined or (
                    None,
                    None,
                    None,
                    None,
                )
                item["previous_value"], item["selected_value"] = previous, selected
                if previous_formatted is not None:
                    item["previous_formatted"] = previous_formatted
                if selected_formatted is not None:
                    item["selected_formatted"] = selected_formatted
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
            "schema_version": REVIEW_CASE_HISTORY_SCHEMA,
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
        except HumanDecisionError as error:
            # `link_changed` (#1477 L9) keeps its own code; every other
            # refusal of the selection is a stale binding, as before.
            raise ReviewWebError(
                "link_changed" if error.code == "link_changed" else "stale"
            ) from error
        except ValueError as error:
            raise ReviewWebError("stale") from error
        except Exception as error:
            raise ReviewWebError("persistence") from error
        decision = AuthoredDescriptionDecision.from_json(document)
        return {"decision_id": decision.decision_id, "advised": True}

    def _keep_refused(
        self,
        review_case_id: str,
        offer: tuple[dict[str, object], ...],
        proposal_id: str,
    ) -> bool:
        """Whether a keep binds a description every source repeats while its
        field, as advice decides, does not offer Keep current (#1905).

        Only that shape reads the page; the store's own checks follow.
        """
        proposal = next(value for value in offer if value["proposal_id"] == proposal_id)
        return preserved_description(proposal) and not any(
            field["destination"] == "description"
            and "keep_current" in cast("list[str]", field["decision_actions"])
            for field in cast(
                "list[dict[str, object]]",
                InspectCurrentReviewCase(self._repository).run(review_case_id)[
                    "fields"
                ],
            )
        )

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
        cover_confirmation: dict[str, object] | None = None,
        offered_alternatives_digest: str | None = None,
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
                    offered_alternatives_digest=offered_alternatives_digest,
                ):
                    raise ReviewWebError("idempotency_conflict")
                try:
                    stored_replay = HumanDecision.from_json(
                        self._repository.record_human_decision(
                            replay,
                            cover_consideration_id=consideration_id,
                            cover_confirmation=cover_confirmation,
                        )
                    )
                except HumanDecisionError:
                    raise
                except Exception as error:
                    raise ReviewWebError("persistence") from error
                return {
                    **_decision_projection(stored_replay),
                    "schema_version": HUMAN_DECISION_ACK_SCHEMA,
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
            # A case waiting for its Book matching check refuses every
            # decision, cover included, and asks for that check again (#1355,
            # amendments 4 and 15); the store checks again inside the write.
            # The clock is this layer's own, so its fault is not the store's.
            refused_at = self._clock()
            try:
                self._repository.require_reconciled_case(review_case_id, refused_at)
            except HumanDecisionError:
                raise
            except Exception as error:
                raise ReviewWebError("persistence") from error
            try:
                ready_at = self._repository.review_case_ready_at(review_case_id)
            except Exception:
                # This timestamp is advisory and cannot gate an owner decision,
                # so every failure of the read -- including a fault the store
                # cannot name -- must leave the decision itself unaffected. The
                # only consequence is that no ``human_wait`` sample is recorded.
                ready_at = None
            previous = None
            written: tuple[object] | None = None
            if outcome == "owner_entered":
                # A contextual row offers no owner entry, so it takes none.
                if any(
                    field["destination"] == destination
                    and field["inventory_role"] == "contextual"
                    for field in cast(
                        "list[dict[str, object]]",
                        InspectCurrentReviewCase(self._repository).run(review_case_id)[
                            "fields"
                        ],
                    )
                ):
                    raise HumanDecisionError("unsupported")
                target = case.target
                try:
                    previous = dict(
                        self._repository.previous_field_values(
                            str(target["connection"]),
                            str(target["library"]),
                            str(target["item"]),
                        )
                    ).get(destination)
                    written_values = dict(
                        self._repository.written_field_values(review_case_id)
                    )
                except Exception as error:
                    raise ReviewWebError("persistence") from error
                if destination in written_values:
                    written = (written_values[destination],)
            try:
                # The field's offer as it stands now; the store reads it again
                # inside the write (#1355, S10).
                offer = current_offer(
                    case, self._repository.review_case_level_offer(review_case_id)
                )
            except Exception as error:
                raise ReviewWebError("persistence") from error
            decision = HumanDecision.create(
                case,
                offer=offer,
                offered_alternatives_digest=offered_alternatives_digest,
                proposal_id=proposal_id,
                target_fingerprint=target_fingerprint,
                destination=destination,
                actor=actor,
                outcome=outcome,
                selected_value=selected_value,
                idempotency_key=idempotency_key,
                occurred_at=self._clock(),
                previous=previous,
                written=written,
            )
            if outcome == "keep_current" and self._keep_refused(
                review_case_id, offer, proposal_id
            ):
                raise HumanDecisionError("unsupported")
            try:
                stored = HumanDecision.from_json(
                    self._repository.record_human_decision(
                        decision,
                        cover_consideration_id=consideration_id,
                        cover_confirmation=cover_confirmation,
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
                "schema_version": HUMAN_DECISION_ACK_SCHEMA,
            }
        except ReviewWebError:
            raise
        except HumanDecisionError as error:
            raise ReviewWebError(error.code, error.reason) from None
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


class ProviderRecoverySchedule(Protocol):
    """Which visible works the provider-recovery store will look up again."""

    def scheduled_recoveries(
        self, reports: frozenset[tuple[str, str, str, str]]
    ) -> frozenset[tuple[str, str, str, str]]:
        """Return those (connection, library, item, report) with a scheduled job."""
        ...


class BrowseReviewLibrary:
    def __init__(
        self,
        repository: ReviewLibraryStore,
        validated: ValidatedDocumentCache | None = None,
        recovery: ProviderRecoverySchedule | None = None,
    ) -> None:
        self._repository = repository
        self._validated = validated
        # Absent, nothing is scheduled, so no row promises a retry (#1407).
        self._recovery = recovery

    def library(
        self,
        *,
        lens: str,
        search: str | None,
        limit: int,
        cursor: str | None,
        library_labels: LibraryLabels = (),
        now: int = 0,
        find_offer: FindOffer | None = None,
    ) -> dict[str, object]:
        """One Library page. `now` is the clock the visit boundary is read at:
        a caller with no clock reads the boundary of the visit in progress.
        `find_offer` says whether Find this book is offered on a row; with
        none, it is offered on no row (#1477).
        """
        normalized = _review_search(search)
        if type(lens) is not str or lens not in _LIBRARY_LENSES:
            raise ReviewWebError("request")
        bounded_limit = _review_limit(limit)
        offset = _decode_cursor(cursor, "library", lens, normalized)
        try:
            snapshot = self._repository.read_review_library(
                normalized,
                bounded_limit + 1,
                offset,
                _LIBRARY_LENSES[lens],
                library_labels,
                self._validated,
                now,
            )
            items = []
            scopes = snapshot.decision_scopes or (None,) * len(snapshot.rows)
            links = snapshot.links or (None,) * len(snapshot.rows)
            visible = tuple(
                zip(snapshot.rows, scopes, snapshot.match_reasons, strict=True)
            )[:bounded_limit]
            # A row promises another lookup only where the recovery store holds
            # a scheduled job for exactly its report, read once for this page
            # and never for the count (#1407).
            unavailable = {
                index: _report_facts(row[1])
                for index, (row, _scope, reason) in enumerate(visible)
                if reason == "unavailable"
            }
            identities = {
                index: (
                    facts.target.connection,
                    facts.target.library,
                    facts.target.item,
                    facts.report_id,
                )
                for index, facts in unavailable.items()
            }
            scheduled: frozenset[tuple[str, str, str, str]] = frozenset()
            if self._recovery is not None and identities:
                try:
                    scheduled = self._recovery.scheduled_recoveries(
                        frozenset(identities.values())
                    )
                except Exception as error:
                    # The promise is display-only: a recovery store that cannot
                    # answer withdraws it and never fails the page (#1407).
                    _recovery_schedule_unavailable(type(error).__name__)
            for index, (row, scope, match_reason) in enumerate(visible):
                (
                    case_document,
                    report_document,
                    decision_documents,
                    automatic_attention_destinations,
                    suspended_destinations,
                    shadow_abstention_destinations,
                    terminal_document,
                    attention_count,
                    pinned_count,
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
                        pinned_count,
                        decision_scope=scope,
                        validated=self._validated,
                        match_state=(
                            "unavailable_scheduled"
                            if identities.get(index) in scheduled
                            else match_reason
                        ),
                        report_facts=unavailable.get(index),
                        link=_library_link(links[index], terminal_document is not None),
                        find_offer=find_offer,
                    )
                )
        except Exception as error:
            # The undeclared page read and the row projection are one outcome:
            # a page this surface cannot render. The row is consumed at its
            # declared width, so a shape the adapter no longer supplies fails
            # the page rather than silently dropping a work from the library.
            raise ReviewWebError("unavailable") from error
        return {
            "counts": _library_counts(
                snapshot.total,
                snapshot.attention_total,
                snapshot.not_matched_total,
                snapshot.preparation_failed_total,
                snapshot.flagged_total,
                snapshot.new_total,
            ),
            "items": items,
            "lens": lens,
            # How far the listing had got at this read (#1351): what the
            # browser hands back when it has shown the New lens.
            "listing": {
                "packages": snapshot.listing.packages,
                "reports": snapshot.listing.reports,
                "works": snapshot.listing.works,
            },
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
            "schema_version": REVIEW_LIBRARY_SCHEMA,
        }

    def progress(
        self, library_labels: LibraryLabels = (), now: int = 0
    ) -> dict[str, object]:
        """Library's counts in total and per library, and Attention's fields.

        Libraries follow the active settings, then any stable name they no
        longer configure alphabetically. Fields come most-needed first.
        """
        try:
            snapshot = self._repository.read_review_library_progress(
                library_labels, now
            )
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        order = {
            (connection, library): index
            for index, (connection, library, _label) in enumerate(library_labels)
        }
        labels = {
            (connection, library): label
            for connection, library, label in library_labels
        }
        libraries = sorted(
            snapshot.libraries,
            key=lambda row: (order.get((row[0], row[1]), len(order)), row[1], row[0]),
        )
        fields: dict[str, int] = {}
        for _connection, _library, destination, works in snapshot.fields:
            fields[destination] = fields.get(destination, 0) + works
        return {
            "totals": _library_counts(
                sum(row[2] for row in libraries),
                sum(row[3] for row in libraries),
                sum(row[4] for row in libraries),
                sum(row[5] for row in libraries),
                sum(row[6] for row in libraries),
                sum(row[7] for row in libraries),
            ),
            "libraries": [
                {
                    "connection": connection,
                    "library": library,
                    "library_display_name": labels.get((connection, library), library),
                    "counts": _library_counts(*counts),
                }
                for connection, library, *counts in libraries
            ],
            "fields": [
                {"destination": destination, "need_you": works}
                for destination, works in sorted(
                    fields.items(), key=lambda item: (-item[1], item[0])
                )
            ],
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
                previous_documents: tuple[str, str] | None = None
                parsed: tuple[CaseBrowseFacts, LibraryReportFacts] | None = None
                for row in raw_rows:
                    (
                        case_document,
                        report_document,
                        destination,
                        work_attention_count,
                        automatic_outcome,
                        cause,
                        policy_attention,
                        pin_changed,
                    ) = row
                    if (
                        parsed is None
                        or type(case_document) is not str
                        or type(report_document) is not str
                        or previous_documents != (case_document, report_document)
                    ):
                        parsed = _reused_or_parsed(
                            self._validated,
                            BROWSE_FACTS_REUSE,
                            partial(_case_and_report, case_document, report_document),
                            case_document,
                            report_document,
                        )
                        previous_documents = (case_document, report_document)
                    item = _attention_item(
                        *parsed,
                        destination,
                        work_attention_count,
                        automatic_outcome=automatic_outcome,
                        cause=cause,
                        policy_attention=policy_attention,
                        pin_changed=pin_changed,
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
            "schema_version": REVIEW_ATTENTION_SCHEMA,
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

    def library_cover(
        self, connection: str, library: str, item: str, preview: str
    ) -> str:
        """Name the library cover preview a work's current report retains.

        A preview is served only by the digest the item's current report binds
        to its ABS cover, so the route reads nothing the row could not name: a
        digest from another work, an older report or a provider is missing.
        """
        if (
            any(
                type(value) is not str or not 1 <= len(value) <= 512
                for value in (connection, library, item)
            )
            or type(preview) is not str
            or _PREVIEW_DIGEST.fullmatch(preview) is None
        ):
            raise ReviewWebError("missing")
        try:
            document = self._repository.read_current_report_document(
                connection, library, item
            )
        except Exception as error:
            raise ReviewWebError("unavailable") from error
        if document is None:
            raise ReviewWebError("missing")
        report = _report_facts(document)
        target = report.target
        if (target.connection, target.library, target.item) != (
            connection,
            library,
            item,
        ):
            raise ReviewWebError("unavailable")
        if report.cover is None or report.cover.preview_digest != preview:
            raise ReviewWebError("missing")
        return preview


_PREVIEW_DIGEST = re.compile(r"[0-9a-f]{64}")


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
    every dispatched provider was asked and how it ended, and the claims the
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
    provider_claims = _report_detail_provider_claims(report, recorded)
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
        "recording_admission_basis": recording_admission_basis(recorded),
        "audiobook_review_scope": audiobook_review_scope(recorded),
        # What every dispatched provider was asked, and how each ended.
        "provider_sources": _provider_outcomes(provider_lane_facts(recorded)),
        # What the admitted candidates stated, under the same ceiling and the
        # same truncation flag the local evidence above uses.
        "provider_evidence": provider_claims[:_EVIDENCE_CEILING],
        "provider_evidence_truncated": len(provider_claims) > _EVIDENCE_CEILING,
        "report_id": report["report_id"],
        "schema_version": REVIEW_REPORT_DETAIL_SCHEMA,
        "title": _report_title(report),
    }


def _report_detail_provider_claims(
    report: dict[str, object], recorded: MetadataReport
) -> list[dict[str, object]]:
    """Project every admitted candidate's stated claims, authority-labelled.

    A configured connection's name is the owner's private label for a service,
    so the authority is what an owner-facing surface names. Candidate identity
    travels with each claim because one observation may admit more than one
    candidate and a merged list would attribute one product's claims to
    another. An audiobook's iTunes listing is its cover alone, so nothing it
    states is evidence here (#1779).
    """
    values: list[dict[str, object]] = []
    providers = report.get("providers")
    if type(providers) is not list:
        return values
    # One report states one observation per provider name.
    evidence = {value.provider for value in field_evidence_providers(recorded)}
    for provider in cast("list[object]", providers):
        if type(provider) is not dict or provider.get("provider") not in evidence:
            continue
        authority = provider.get("authority")
        label = provider_label(authority if type(authority) is str else None)
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


# A choice with one candidate for an empty field shows no disagreement: the
# value it disagrees with is not offered, such as one in another language
# (#1497).
_UNOFFERED_DISSENT_TEXT = (
    "Another source gave a value that isn\u2019t offered here. "
    "Choose whether to use this one."
)


def _lone_candidate_for_empty(field: Mapping[str, Any]) -> bool:
    current = field["current"]
    return (
        len(field["proposed_values"]) == 1
        and isinstance(current, Mapping)
        and current.get("state") in {"missing", "null", "empty"}
    )


# The comparison condition a withheld reason already states. Library leaves out
# the notice with that code rather than comparing the two sentences, so either
# can be reworded alone (#1519, #1802).
AutomaticWithheldCondition = Literal[
    "isbn_title_material_conflict", "competing_evidence"
]


def _automatic_withheld(
    field: Mapping[str, Any],
    outcome: ShadowFieldOutcome | None,
    destination: str,
    paused: bool,
) -> tuple[str | None, AutomaticWithheldCondition | None]:
    """Say why a field was not changed for you, and which condition that states.

    A paused field still states the condition after its prefix, so its notice
    is not repeated either (#1802). A field whose every source value matches
    its current one has no disagreement to state (#2012).
    """
    condition: AutomaticWithheldCondition | None = None
    message: str | None
    if outcome is None:
        message = "No recorded automatic evaluation is available for this field."
    elif (
        destination == "language"
        and "language_display_name" in field.get("notices", ())
        and (outcome.policy_option or "").endswith("audiobook_language_replace")
    ):
        message = _AUDIOBOOK_LANGUAGE_REGION_TEXT
    elif (
        not field["proposed_values"]
        and outcome.reason_code in _CURRENT_VALUE_REFUSAL_TEXT
    ):
        message = _CURRENT_VALUE_REFUSAL_TEXT[outcome.reason_code]
    elif outcome.reason_code == "ambiguous_evidence" and not field["proposed_values"]:
        # No source offered a value other than the current one, so there is
        # no disagreement to state (#2012).
        message = None
    elif outcome.reason_code == "ambiguous_evidence" and _lone_candidate_for_empty(
        field
    ):
        message, condition = _UNOFFERED_DISSENT_TEXT, "competing_evidence"
    else:
        message = _AUTOMATIC_REFUSAL_TEXT.get(
            outcome.reason_code,
            "No value was chosen for you, and no more specific reason is known.",
        )
        if outcome.reason_code == "isbn_title_material_conflict":
            condition = "isbn_title_material_conflict"
    if paused:
        message = "Changes made for you are paused for this field." + (
            "" if message is None else " " + message
        )
    return message, condition


def _presented_field(field: TargetField) -> dict[str, object]:
    """One current field as review shows it, without the store's restore flag.

    Whether a description can be written back is decided where a restore is
    planned (#1424); the owner reads the value, not that flag. A description
    keeps its formatted copy, which its card shows (#1995).
    """
    data = field.to_data()
    data.pop("restorable", None)
    return data


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


def _owner_control(
    proposal: dict[str, object],
    target_fields: dict[str, dict[str, object]],
    proposals: tuple[dict[str, object], ...],
    decision: HumanDecision | None,
    automatic: bool,
    snapshot: CurrentReviewCaseSnapshot,
    media_kind: str,
    pinned: object,
) -> dict[str, object] | None:
    """Project F59's owner controls for one field, or ``None`` when it has none.

    Each part is offered only where the store would accept that command now;
    the store still decides, and refuses a stale panel inline (ADR-0085).
    ``pinned`` is the value a changed pin keeps, suggested like the previous
    one (ADR-0085, Amendment 2). Names entry reaches authors, and narrators on
    an audiobook; a clearable field also takes an empty value (ADR-0105).
    """
    destination = cast(str, proposal["destination"])
    # On the case that wrote the field, what it wrote is current (#1523, S5).
    written = dict(snapshot.written_values)
    current = (
        written[destination]
        if destination in written
        else target_fields[destination]["value"]
    )
    previous = dict(snapshot.previous_values).get(destination)
    open_ = (
        destination not in snapshot.suspended_destinations
        and destination not in snapshot.override_blocked_destinations
        and owner_entry_admitted(proposal, media_kind)
    )
    entry = None
    if open_ and destination in OWNER_TEXT_DESTINATIONS:
        # The union of current and eligible proposed authors, which is what
        # `split_author` may recognise beside a title (ADR-0085).
        authors = next(
            (
                tuple(
                    item
                    for item, *_ in list_union(value, target_fields["authors"]["value"])
                )
                for value in proposals
                if value["destination"] == "authors"
            ),
            (),
        )
        suggestions = [
            {"rule": suggestion.rule, "value": suggestion.value}
            for suggestion in owner_value_suggestions(
                destination,
                current if type(current) is str else None,
                authors,
            )
        ]
        # What the field held before its latest verified write is one more
        # suggestion: it only fills the input, like every other (ADR-0085).
        if (
            type(previous) is str
            and owner_text(previous)
            and previous != current
            and all(previous != item["value"] for item in suggestions)
        ):
            suggestions.append({"rule": "previous", "value": previous})
        if (
            type(pinned) is str
            and owner_text(pinned)
            and pinned != current
            and all(pinned != item["value"] for item in suggestions)
        ):
            suggestions.append({"rule": "pinned", "value": pinned})
        entry = {"max_characters": OWNER_TEXT_MAXIMUM, "suggestions": suggestions}
    subset = None
    names = None
    if open_ and destination in OWNER_NAMES_DESTINATIONS:
        names = {
            "max_items": OWNER_NAMES_MAXIMUM,
            "max_characters": OWNER_TEXT_MAXIMUM,
            "previous": previous
            if type(previous) is list
            and previous != current
            and 1 <= len(previous) <= OWNER_NAMES_MAXIMUM
            and all(owner_text(item) for item in previous)
            else None,
        }
    if (
        open_
        and destination == OWNER_SUBSET_DESTINATION
        and genre_subset_available(proposal, current, previous)
    ):
        subset = {
            "max_items": OWNER_SUBSET_MAXIMUM,
            "options": [
                {
                    "value": value,
                    "current": in_current,
                    "proposed": proposed,
                    "previous": earlier,
                }
                for value, in_current, proposed, earlier in list_union(
                    proposal, current, previous
                )
            ],
        }
    # An answer cannot be withdrawn while its field is pinned (ADR-0085,
    # Amendment 2), nor beside an automatic decision that settles the field,
    # as the store refuses it: once Resume is asked or done, or with no answer
    # held. Beside one the record's answer holds, it follows its write as
    # anywhere else (ADR-0108). A decision a completed Resume took back offers
    # no withdraw either (D3). Either way the field still shows the owner's
    # recorded value with its controls.
    withdraw = None
    resumed = False
    if (
        decision is not None
        and decision.outcome != "reject"
        and destination not in UNREVERSIBLE_DESTINATIONS
        and all(row[0] != destination for row in snapshot.pins)
    ):
        if decision.decision_id not in snapshot.covered_decision_ids and not (
            automatic and _automatic_settles(snapshot, destination)
        ):
            withdraw = dict(snapshot.decision_effect_states).get(
                destination, "available"
            )
        else:
            # Only an entered value has a recorded line to keep showing.
            resumed = decision.outcome == "owner_entered"
    entered = (entry, subset, names) != (None, None, None)
    if not entered and withdraw is None and not resumed:
        return None
    return {
        "entry": entry,
        "subset": subset,
        "names": names,
        "clear": entered and destination in CLEARABLE_DESTINATIONS,
        "current": current if entered else None,
        "withdraw": withdraw,
    }


def _corroboration_source_labels(
    report: MetadataReport,
    references: list[str],
    reasons: list[object],
) -> list[str]:
    """Project only closed provider authorities for #416 creator corroboration."""
    if reasons != ["explicit_author_corroborates_untyped_creator"]:
        return []
    return provider_source_labels(report, references)


def _choice_contexts(
    alternatives: list[dict[str, object]],
    result: dict[str, object],
    report: MetadataReport,
    target: dict[str, object],
    automatic_decision: dict[str, object] | None = None,
    evidence: dict[str, dict[str, str | None]] | None = None,
    lineage: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    """Project retained proposal lineage into owner-readable choice context.

    ``lineage`` names the alternatives Book matching offers (#1355): their
    ``match_basis`` is ``title_and_author``, and every other choice's is null.
    """
    lineage = {} if lineage is None else lineage
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
                        "language": None,
                        "observation": "Your current value.",
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
            # The current value's card shows its value and nothing more (#1519).
            "rationale": "",
            "recommendation": "recommended"
            if current_recommended
            else "no_recommendation",
            "recommendation_reason": (
                "Your settings keep the current value." if current_recommended else ""
            ),
            "selection": "current",
            "match_basis": None,
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
                "rationale": _choice_rationale(rows),
                "recommendation": "recommended" if recommended else "no_recommendation",
                "recommendation_reason": (
                    "Your settings choose this value." if recommended else ""
                ),
                "selection": "proposed",
                "match_basis": "title_and_author"
                if alternative_id in lineage
                else None,
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


def _choice_rationale(rows: list[dict[str, object]]) -> str:
    """Name the sources an alternative came from, or nothing when none is known (#1519)."""
    named = set(PROVIDER_LABELS.values()) | {
        audio_edition_label(authority) for authority in PROVIDER_LABELS
    }
    return ", ".join(
        sorted(
            {
                source
                for row in rows
                if type(source := row["source"]) is str
                and row["role"] != "local_presence"
                and source in named
            }
        )
    )


def _choice_evidence(
    evidence: dict[str, str | None], destinations: list[str] | None = None
) -> dict[str, object]:
    observation, language = _choice_observation(evidence)
    return {
        **({"destinations": sorted(destinations)} if destinations is not None else {}),
        "language": language,
        "observation": observation
        + (f" ({evidence['edition']})" if evidence.get("edition") else ""),
        "role": "local_presence"
        if _choice_is_local(evidence)
        else "external_observation"
        if evidence["source"] == "identifier"
        or evidence["provider"] not in PROVIDER_LABELS
        else "external_corroboration",
        "source": _choice_source_label(evidence),
    }


def _audio_edition_note(edition: dict[str, object]) -> str:
    """Name one audio edition by its catalogue number and runtime (#1642).

    Two editions can credit the same cast, so each evidence row says which
    edition it reads.
    """
    identity = edition.get("identity")
    number = (
        identity.rsplit("-", 1)[-1].lstrip("0") or "0"
        if type(identity) is str and identity.rsplit("-", 1)[-1].isdigit()
        else None
    )
    seconds = next(
        (
            parsed
            for claim in cast("list[object]", edition.get("claims", []))
            if type(claim) is dict
            and claim.get("kind") == "duration"
            and (parsed := _duration_seconds(claim.get("value"))) is not None
        ),
        None,
    )
    parts = (
        *(() if number is None else (f"Edition {number}",)),
        *(() if seconds is None else (hours_minutes(seconds),)),
    )
    return ", ".join(parts) if parts else "Audio edition"


def _choice_source_label(evidence: dict[str, str | None]) -> str:
    provider = evidence["provider"]
    if provider in PROVIDER_LABELS and evidence["source"] == "audio_edition":
        return audio_edition_label(provider)
    if provider in PROVIDER_LABELS:
        return PROVIDER_LABELS[cast("str", provider)]
    if _choice_is_local(evidence):
        return "Local library record"
    return "Unnamed source"


def _choice_is_local(evidence: dict[str, str | None]) -> bool:
    """Retain local identifier observations as local presence, not provider claims."""
    return evidence["source"] == "local" or (
        evidence["source"] == "identifier"
        and type(evidence.get("reference")) is str
        and cast(str, evidence["reference"]).startswith("local_")
    )


_OBSERVATION_KINDS: Mapping[str, str] = MappingProxyType(
    {
        ClaimKind.TITLE: "Title",
        ClaimKind.SUBTITLE: "Subtitle",
        ClaimKind.CREATOR: "Author",
        ClaimKind.CONTRIBUTOR: "Contributor",
        ClaimKind.SERIES: "Series membership",
        ClaimKind.SERIES_SEQUENCE: "Series position",
        ClaimKind.ISBN: "ISBN",
        ClaimKind.ASIN: "ASIN",
        ClaimKind.PUBLICATION_DATE: "Publication date",
        ClaimKind.DATE_EVENT: "Dated event",
        ClaimKind.CONTEXTUAL_CREDIT: "Other credit",
        ClaimKind.PUBLISHER: "Publisher",
        ClaimKind.DESCRIPTION: "Description",
        ClaimKind.LANGUAGE: "Language",
        ClaimKind.SUBJECT: "Subject",
        ClaimKind.IMPRINT: "Imprint",
        ClaimKind.GENRE: "Genre",
        ClaimKind.TAG: "Tag",
        ClaimKind.DURATION: "Running time",
        ClaimKind.PERFORMANCE_TYPE: "Performance",
        ClaimKind.ADULT_PRODUCT: "Adult content",
        # A provider's author list reads as the Library's Author.
        "authors": "Author",
    }
)


def _choice_observation(evidence: dict[str, str | None]) -> tuple[str, str | None]:
    """One evidence sentence, and the language value it states, if any.

    Library names that value where it stands in the sentence, so the sentence
    can be reworded without the browser reading its words (#1802).
    """
    try:
        fact = json.loads(evidence["fact"] or "{}")
    except (TypeError, ValueError):
        return "This source\u2019s value could not be read.", None
    if type(fact) is not dict:
        return "This source\u2019s value could not be read.", None
    if evidence["source"] == "identifier" and "relation" in fact:
        return "Checks an ISBN against this book. It gives no value of its own.", None
    # Every claim kind is named as the Library's recorded evidence names it
    # (#1286); a narrator's credit keeps its own name.
    claim_kind = fact.get("kind")
    if claim_kind == "contributor" and fact.get("role") in {None, "narrator"}:
        kind = "Narration credit"
    elif (
        claim_kind == "publication_date"
        and fact.get("date_event") == "work_first_publication"
    ):
        # A work's date is its first publication, not this edition's (#1642).
        kind = "First published (book)"
    elif type(claim_kind) is str:
        kind = _OBSERVATION_KINDS.get(claim_kind, "Metadata")
    else:
        kind = "Metadata"
    value = fact.get("value", fact.get("observed"))
    if (
        fact.get("kind") == "duration"
        and (seconds := _duration_seconds(value)) is not None
    ):
        # A running time reads in hours and minutes, never as raw seconds (#1286).
        return f"{kind}: {hours_minutes(seconds)}", None
    if type(value) in {str, int, float}:
        text = _choice_text(cast("str | int | float", value))
        # A local record can carry its own label ("ISBN: 978…"); it is named
        # once (#1286).
        if text.casefold().startswith(kind.casefold() + ":"):
            text = text[len(kind) + 1 :].strip() or text
        return f"{kind}: {text}", text if claim_kind == "language" else None
    return f"{kind}: the value cannot be shown.", None


def _duration_seconds(value: object) -> int | None:
    """Whole seconds from a recorded running time, or None when unreadable."""
    if type(value) not in {str, int, float}:
        return None
    try:
        seconds = float(cast("str | int | float", value))
    except ValueError:
        return None
    return round(seconds) if math.isfinite(seconds) and seconds >= 0 else None


def _choice_text(value: str | int | float) -> str:
    text = str(value)
    cleaned = "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith("C")
    )
    normalized = " ".join(cleaned.split())
    if not normalized:
        return "no readable value"
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
        stated = _choice_text(local_claim.observed)
        observations[local_claim.reference] = {
            "destinations": ["language"],
            "language": stated,
            "role": "local_presence",
            "source": "Local metadata",
            "observation": (
                f"Language: {stated}. "
                + "Not offered as a choice."
                + (" The language is not known." if unknown else "")
            ),
        }
    for provider in field_evidence_providers(report):
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
                            report.library_languages,
                            echoed_target_values(
                                report.audio,
                                report.target.fields,
                                AudioInspectionField.LANGUAGE,
                            ),
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
                        report.library_languages,
                        echoed_target_values(
                            report.audio,
                            report.target.fields,
                            AudioInspectionField.LANGUAGE,
                        ),
                    )
                    if narrator
                    else None
                )
                reason = (
                    "The language is not known. "
                    if language
                    and type(claim.value) is str
                    and language_is_unknown(claim.value)
                    else "That recording is in another language. "
                    if conflict == "recording_language_conflict"
                    else "That recording is not in one of this library\u2019s languages. "
                    if conflict == "recording_language_outside_library"
                    else "That recording is a different version of this audiobook. "
                    if conflict is not None
                    else "That recording\u2019s running time is unreadable or does not "
                    "match this audiobook. "
                    if narrator
                    else ""
                )
                value = (
                    _choice_text(claim.value)
                    if claim.state is FieldState.VALID and type(claim.value) is str
                    else "no readable name"
                    if narrator
                    else "no readable identifier"
                )
                observations.setdefault(
                    claim.reference,
                    {
                        "destinations": [destination],
                        "language": value if language else None,
                        "role": "external_observation",
                        "source": provider_label(provider.authority),
                        "observation": (
                            f"{label}: {value}. Not offered as a choice. {reason}"
                            + (
                                "It belongs to one edition."
                                if claim.scope is ClaimScope.EDITION
                                else ""
                            )
                        ).strip(),
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
        edition: str | None = None,
    ) -> None:
        if type(fact) is not dict or type(fact.get(reference_key)) is not str:
            return
        reference = fact[reference_key]
        values[reference] = {
            **({"edition": edition} if edition is not None else {}),
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
            # A level alternative cites a refused answer's claims; only a
            # level offer names them, so live fields never find these (#1355).
            *provider["identity_refused_candidates"],
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
        # An audio edition's narrators may be a work-route choice (#1642).
        for edition in provider["audio_editions"]:
            assert type(edition) is dict
            for fact in edition["claims"]:
                add(
                    "audio_edition",
                    fact,
                    provider=public_authority or provider_name,
                    candidate=edition["identity"],
                    edition=_audio_edition_note(edition),
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
) -> tuple[CaseBrowseFacts, LibraryReportFacts]:
    try:
        return browse_case_facts_and_report(case_document, report_document)
    except ReviewBrowseValueError as error:
        # The bounded readers name their one malformed-document outcome, so
        # this matches it rather than the ``ValueError`` it happens to share
        # with the rest of this projection. Lineage disagreement is one of
        # those outcomes.
        raise ReviewWebError("unavailable") from error


def _recovery_schedule_unavailable(error: str) -> None:
    """Write one value-free warning that the recovery schedule went unread.

    It carries a closed code and at most an exception type name, never a
    message, identity or path. Like the other operational events it bypasses
    `logging`, which nothing configures (#1304), and a failing sink cannot fail
    the page (#1447).
    """
    event = {
        "code": "unavailable",
        "error": (
            error
            if error.isascii() and error.isidentifier() and len(error) <= 128
            else None
        ),
        "event": "provider_recovery_schedule",
        "level": "warning",
    }
    try:
        sys.stderr.write(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        )
    except Exception:
        pass


def _report_facts(report_document: str) -> LibraryReportFacts:
    """Read a report that has no current case through the same bounded reader."""
    try:
        return library_report_facts(browse_report(report_document))
    except ReviewBrowseValueError as error:
        raise ReviewWebError("unavailable") from error


def _cover_stage_outcome(
    destination: str, consideration: dict[str, object] | None
) -> str | None:
    """The measured stage's outcome over a cover's current comparison, if any."""
    if destination != "cover" or consideration is None:
        return None
    policy = consideration.get("policy")
    if type(policy) is not dict:
        return None
    stage = policy.get("stage")
    return cast("str", stage["outcome"]) if type(stage) is dict else None


def _cover_stage_kept(
    destination: str, consideration: dict[str, object] | None
) -> bool:
    """Whether the measured stage kept the current cover with no fault.

    Read or not, such a cover never asks: a read that settled nothing keeps
    the cover until the next scan, and the owner has no keep to record for an
    image nobody could read (#1362, R1 as amended). `_COVER_SUGGESTED_SQL`
    reads the same stage facts.
    """
    if destination != "cover" or consideration is None:
        return False
    policy = consideration.get("policy")
    stage = policy.get("stage") if type(policy) is dict else None
    return (
        type(stage) is dict
        and stage.get("outcome") == "keep"
        and stage.get("reason") is None
    )


def _kept_cover(proposal: dict[str, object]) -> bool:
    """A readable current cover is kept without asking (#1310).

    Its alternatives stay choosable on the work's page; neither its choice nor
    its consideration puts it in the queue, as `_VERIFIED_CURRENT_COVER_SQL`
    reads it.
    """
    result = proposal["result"]
    assert type(result) is dict
    reasons = result["reasons"]
    assert type(reasons) is list
    return proposal["destination"] == "cover" and "current_cover_verified" in reasons


def _single_source_choice(proposal: dict[str, object]) -> bool:
    """One uncorroborated source proposes the only alternative (#1310).

    Residual uncertainty alone does not justify asking, so the choice stays
    the owner's without joining the queue, as `_SINGLE_SOURCE_CHOICE_SQL`
    reads it; an explicit Ask still asks.
    """
    result = proposal["result"]
    assert type(result) is dict
    if result["outcome"] != "unresolved_choice" or result["reasons"] != [
        "insufficient_independent_corroboration"
    ]:
        return False
    alternatives = result["alternatives"]
    assert type(alternatives) is list
    material = [
        alternative
        for alternative in alternatives
        if alternative["assessment"] != "preserves_target"
    ]
    return len(material) == 1 and len(material[0]["correlations"]) < 2


def _needs_attention(
    proposal: dict[str, object],
    automatic_attention_destinations: tuple[str, ...] = (),
    *,
    policy_attention_destinations: tuple[str, ...] = (),
    settled_choice_destinations: tuple[str, ...] = (),
    cover_suggested: bool = False,
) -> bool:
    """Whether one proposal is the owner's to answer rather than settled.

    A settled `propose_change` waits unless its bound policy routes it to Ask,
    on a recording exactly as on an ebook: the attention queue, its lens and
    the case detail hold no medium-specific reason (#1249). The action list
    beside this decides whether a choice exists at all (#967). A work-route
    choice whose alternatives only trim or extend the current value stays
    choosable without asking, as the persisted field facts read it (#1253),
    and so does a cover choice over a readable current cover (#1310). So do
    a populated subjective value a fill-only option keeps and tags while their
    option is off; the facts read those options, so the detail passes their
    settled destinations (#1310). A value only one uncorroborated source
    proposes is not queued either (#1310).
    """
    result = proposal["result"]
    assert type(result) is dict
    reasons = result["reasons"]
    assert type(reasons) is list
    return (
        proposal["destination"] in policy_attention_destinations
        or proposal["destination"] in automatic_attention_destinations
        or (
            result["outcome"] == "unresolved_choice"
            and "work_alternative_immaterial" not in reasons
            and not _kept_cover(proposal)
            and not _single_source_choice(proposal)
            and proposal["destination"] not in settled_choice_destinations
            and not cover_suggested
        )
        or proposal["manual_required"] is True
        or "no_safe_bibliographic_combination" in reasons
    )


def _report_authors(target: TargetFacts) -> list[str]:
    """Derive the bounded current author display value without persistence."""
    value = next(
        (
            field.value
            for field in target.fields
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
    {"candidates", "disabled", "insufficient_input", "not_needed"}
)
_PROVIDER_OUTCOME_REASONS: dict[str, str] = {
    "candidates": "Returned possible matches to compare.",
    "no_match": "Returned no matching metadata.",
    "disabled": "Not enabled, so this source was never asked.",
    "insufficient_input": "This work lacks the identifiers this source needs.",
    "authentication": (
        "Nothing was recorded from it. Check it in Settings → Metadata sources."
    ),
    "authorization": (
        "Nothing was recorded from it. Check it in Settings → Metadata sources."
    ),
    "rate_limited": "Refused more lookups for now, so nothing was recorded.",
    "unavailable": "Did not answer.",
    "incomplete": "Answered only in part, so nothing was recorded.",
    "malformed": "Nothing was recorded from it.",
    "oversized": "Returned more than Mechanical Librarian reads, so nothing was recorded.",
    "candidate_limit_exceeded": "Returned too many possible matches to compare.",
    "query_limit_exceeded": "Used up the lookups allowed for this work.",
    "not_needed": (
        "Never asked: another source had already matched this recording by its "
        "running time."
    ),
}
# A lane whose candidates work-identity admission refused persists as no match,
# so its dispatch says what happened, in the row's own words (#1407).
_REFUSED_PROVIDER_REASON = "Found possible matches, but none was this work."
# A lane the adapter itself failed is not the provider's fault and is never
# retried, so it does not say the source could not be reached (#1498).
_ADAPTER_FAULT_REASON = "Nothing was recorded, and it is not retried automatically."


# The two companion ISBN endings that read differently from a generic ISBN
# lookup (#1723); every other ending keeps its generic reason, so a failure is
# still named under the companion label.
_COMPANION_ISBN_REASONS = {
    "candidates": "Looked up the book and its audio editions by the EPUB's ISBN.",
    "no_match": "The EPUB's ISBN did not lead to this audiobook.",
}


# A cover-only lane (#1779) is iTunes asked for an audiobook's cover alone, so
# its reasons say nothing it returned was compared. As for the companion ISBN
# lane, an adapter fault, a refusal and a source never searched keep their own
# reasons; the failure reason covers only a real failure.
_COVER_ONLY_REASONS = {
    "candidates": "Searched for a cover only. Nothing else it returns is compared.",
    "no_match": "Searched for a cover only and found none.",
}
_COVER_ONLY_FAILURE_REASON = (
    "Searched for a cover only. No cover came back, and it is not retried."
)


# The lanes an owner link was read through (#1477) say what that read found:
# by the link's outcome where the lane found nothing, and that it read the
# record where the lane kept one. A link the source could not answer keeps the
# generic failure reason, which already says so.
_LINK_LANE_REASONS = {
    ("admitted", "candidates"): "Read the linked record.",
    ("missing", "no_match"): "No longer has the linked record.",
    ("merged", "no_match"): "Merged the linked record into another one.",
    ("contradicted", "no_match"): (
        "The linked record no longer matches this work's edition."
    ),
}
# The WORK lane asked with the linked record's title and authors (#1477).
_ANCHORED_WORK_REASONS = {
    "candidates": (
        "Returned possible matches for the linked record's title and authors."
    ),
}


def _provider_outcomes(lanes: tuple[ProviderLaneFacts, ...]) -> list[dict[str, object]]:
    """Expose only bounded lane state, never diagnostics or queries.

    Every lane is reported, matches included, so the selected work can show one
    complete source ledger. Suppressing the successful ones left the owner
    unable to tell a source that matched from a source that was never asked.
    """
    return [
        {
            "provider_requests": lane.logical_requests,
            "lane_kind": lane.kind,
            "provider": provider_label(lane.authority),
            "reason": _ADAPTER_FAULT_REASON
            if lane.adapter_fault
            else _REFUSED_PROVIDER_REASON
            if lane.dispatch == "refused" or lane.identity_refused
            else _LINK_LANE_REASONS[(lane.link_outcome, lane.status)]
            if lane.link_outcome is not None
            and (lane.link_outcome, lane.status) in _LINK_LANE_REASONS
            else _ANCHORED_WORK_REASONS[lane.status]
            if lane.anchored and lane.status in _ANCHORED_WORK_REASONS
            else _COVER_ONLY_REASONS.get(lane.status, _COVER_ONLY_FAILURE_REASON)
            if lane.cover_only
            and (
                lane.status in _COVER_ONLY_REASONS
                or lane.status not in _NON_LIMITING_PROVIDER_STATUSES
            )
            else _COMPANION_ISBN_REASONS[lane.status]
            if lane.companion_isbn and lane.status in _COMPANION_ISBN_REASONS
            else _PROVIDER_OUTCOME_REASONS.get(
                lane.status, "Ended in a way this version cannot name."
            ),
            # Its own status, so Sources never labels it unavailable (#1498).
            "status": "adapter_fault" if lane.adapter_fault else lane.status,
            # The companion EPUB's ISBN lane, so Sources says where the ISBN
            # an audiobook never states came from (#1723).
            "companion_isbn": lane.companion_isbn,
            # A cover-only lane, which Sources labels by its cover and Library
            # leaves out of a work's match and limit (#1779).
            "cover_only": lane.cover_only,
            # A WORK lane asked with the owner link's anchor, which Sources
            # labels by the linked record (#1477).
            "anchored": lane.anchored,
        }
        for lane in lanes
    ]


def _provider_comparison_notices(report: MetadataReport) -> list[str]:
    """Keep provider limitations with affected choices, never as case banners."""
    del report
    return []


# A code Library has no more specific step for says to scan again: the next
# scan queues a failed book again from the step that failed (#1765). That
# retry rereads what the earlier steps saved, so a code that fails the same way
# on that saved data says Library has no step that fixes it instead.
_SCAN_AGAIN = "Scan this library again to retry it."
_NO_LIBRARY_STEP = "Library has no step that fixes this."
_TERMINAL_PREPARATION_GUIDANCE: dict[str, tuple[str, str]] = {
    "provider": ("A source stopped without a usable answer.", _SCAN_AGAIN),
    "configuration": (
        "A source’s settings need changing.",
        "Correct them in Settings → Metadata sources, then scan this library again.",
    ),
    "authentication": (
        "A source refused access.",
        "Check it in Settings → Metadata sources, then scan this library again.",
    ),
    "stale": (
        "The book changed while it was being prepared.",
        "Mechanical Librarian will prepare its current version automatically.",
    ),
    "transient": (
        "The automatic retries are used up.",
        "When this library finishes, open Background activity and choose Retry.",
    ),
    "projection": ("The review could not be put together.", _SCAN_AGAIN),
    "failed": (
        "Preparation stopped, and no more detail can be shown.",
        _SCAN_AGAIN,
    ),
    "currentness": (
        "The source changed before preparation finished.",
        "Mechanical Librarian will prepare its current version automatically.",
    ),
    "persistence": (
        "Mechanical Librarian could not save its progress on this book.",
        _SCAN_AGAIN,
    ),
    "unsupported_media": (
        "Mechanical Librarian cannot read this type of file.",
        "Replace it with a supported file, then scan this library again.",
    ),
    "unreadable_media": (
        "A file of this book could not be read.",
        "Replace it with a readable copy, then scan this library again.",
    ),
    "parser_refused": (
        "The file is too complex for Mechanical Librarian to read.",
        "Replace the file, then scan this library again.",
    ),
    "multi_file_budget_exceeded": (
        "This audiobook has more audio than Mechanical Librarian reads as one book.",
        "Use fewer or smaller audio files, then scan this library again.",
    ),
    "missing_required_input": (
        "Something this book needs for preparation is missing.",
        "Check the book’s files in Audiobookshelf, then scan this library again.",
    ),
    "malformed_retained_data": (
        "Saved preparation data for this book could not be read.",
        _NO_LIBRARY_STEP,
    ),
    "internal_failure": (
        "Preparation stopped before the review was ready.",
        _SCAN_AGAIN,
    ),
    # #2013: Audiobookshelf listed the book but could not give it usably.
    "item_no_media": (
        "Audiobookshelf has no audio or ebook file for this book.",
        "Add its file in Audiobookshelf, then scan this library again.",
    ),
    "item_unreadable": (
        "Mechanical Librarian could not read this book’s details from Audiobookshelf.",
        "Check this book in Audiobookshelf, then scan this library again.",
    ),
    "item_oversized": (
        "This book’s details from Audiobookshelf are over the Audiobookshelf "
        "response limit.",
        "Raise it in Settings → Background work, or check this book in "
        "Audiobookshelf, then scan this library again.",
    ),
    "unknown": (
        "Preparation stopped, and no more detail can be shown.",
        _SCAN_AGAIN,
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
    return f" It is track {matched.group(1)} of its audio files."


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


# Why a work no provider matched, by the owner's precedence (#1407). A retry is
# named only where the recovery store holds a job for it.
_NOT_MATCHED_STATES = frozenset(
    {"refused", "unavailable_scheduled", "unavailable", "adapter_fault", "no_match"}
)


def _terminal_library_item(document: str) -> dict[str, object] | None:
    """The Library row of a work that stopped before a report.

    None for any other document, which is a report and is read as one.
    """
    try:
        terminal = json.loads(document)
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
            # Set by the store on a case's row whose Book matching check
            # gave up, by the rule Policy counts it with (#1355).
            check_given_up = terminal.get("check_given_up", False)
            if type(check_given_up) is not bool:
                raise ValueError
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
                "pinned_count": 0,
                # Failed is its own lens, never counted as needing no
                # attention (#1413).
                "attention_state": "preparation_failed",
                # A work that stopped before a report has no retained cover.
                "cover": None,
                "detail_url": None,
                "detail_kind": None,
                "dispositions": ["terminal_failure"],
                "item": item,
                "library": library,
                # A work with no report could not finish and cannot be
                # searched (S7, L4).
                "find_offered": False,
                # A work with no report was never searched, so never linked.
                "link": None,
                "media_kind": media_kind,
                "review_case_id": None,
                "title": title,
                "work_id": work_id,
                "preparation": {
                    "attempt": terminal["attempt"],
                    "check_given_up": check_given_up,
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
                # A work with no report has no current value to flag.
                "value_flags": [],
            }
        return None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ReviewWebError("unavailable") from error


def _decided_destinations(
    case_document: str | None,
    decision_documents: tuple[str, ...],
    decision_scope: DecisionScope | None,
    validated: ValidatedDocumentCache | None,
) -> tuple[set[str], dict[str, str]]:
    """The destinations a recorded decision settles, and each automatic outcome."""
    decisions: set[str] = set()
    automatic_outcomes: dict[str, str] = {}
    scope: DecisionScope | None = None
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
            assert case_document is not None
            # A human decision binds a proposal identity, which only the
            # canonical case carries, so this row is validated against it.
            if scope is None:
                canonical_case = _reused_or_parsed(
                    validated,
                    REVIEW_CASE_REUSE,
                    partial(ReviewCase.from_json, case_document),
                    case_document,
                )
                # The store's scope is reused only for this exact case. Both
                # identities are the SHA-256 of the same case bytes: the store
                # hashes the row's `case_document` (`browse_case_with_id`), and
                # `from_json` admits only bytes that re-serialize exactly and
                # whose identity is that same hash. Equal identities are then
                # equal proposal inputs, so the scope's projection is the one
                # `canonical_case.proposals` would compute; any other scope is
                # set aside and the canonical case is projected here (#1215).
                scope = (
                    decision_scope
                    if decision_scope is not None
                    and decision_scope.review_case_id == canonical_case.review_case_id
                    else DecisionScope.of(canonical_case)
                )
            decision.validate_in(scope)
            if decision.outcome == "withdraw":
                decisions.discard(decision.destination)
            elif decision.outcome not in {"pin", "unpin"}:
                decisions.add(decision.destination)
        else:
            destination = str(automatic["destination"])
            automatic_outcomes[destination] = str(automatic["outcome"])
            if automatic["outcome"] != "abstention":
                decisions.add(destination)
    return decisions, automatic_outcomes


# v2: a file link (#1478) names no source and keeps no title.
OwnerLinkViewSchemaVersion = Literal["owner-link-view-v3"]
OWNER_LINK_VIEW_SCHEMA: Final[OwnerLinkViewSchemaVersion] = "owner-link-view-v3"


def owner_link_view(
    offer: dict[str, object],
    inputs: tuple[OwnerLink, OwnerLinkTarget, dict[str, object] | None, bool] | None,
    *,
    paused: bool,
    next_retry_at: int | None,
) -> dict[str, object]:
    """The selected work's link view (`owner-link-view-v3`, S7, S29, L4).

    `offer` says whether Find this book is offered, from which sources and
    with what prefill; a Could not finish work is not offered but still
    shows its link, so it can be withdrawn. `inputs` is the current link,
    its display snapshot, the current report's link facts and whether the
    work's latest preparation could not finish. A file link (#1478) has no
    source and no snapshot: `provider` and `title` are null.
    """
    link: dict[str, object] | None = None
    if inputs is not None:
        current, target, facts, preparation_failed = inputs
        try:
            state = link_state(
                current,
                owner_link_facts_from_data(facts),
                paused=paused,
                preparation_failed=preparation_failed,
                next_retry_at=next_retry_at,
            )
        except (OwnerLinkError, ReportValueError) as error:
            raise ReviewWebError("unavailable") from error
        link = {
            "creators": list(target.display_creators),
            "identifier": None
            if target.scope is OwnerLinkScope.WORK
            else target.identity,
            "link_row_id": current.row_id,
            "paused": state.paused,
            "provider": target.provider,
            "reason": state.reason,
            "retry_at": state.retry_at,
            "scope": target.scope.value,
            "source": target.source,
            "state": state.state,
            "title": target.display_title,
        }
    return {**offer, "link": link, "schema_version": OWNER_LINK_VIEW_SCHEMA}


def _library_link(
    entry: LibraryRowLink | None, preparation_failed: bool
) -> dict[str, object] | None:
    """A row's `link: {state, source, provider}` from its current link (S29).

    The row says only the state, how the link was made and the linked source
    (none for a file link, #1478); the selected work reads the full view.
    `preparation_failed` is the row's own Could not finish, so the two never
    disagree (#1941).
    """
    if entry is None:
        return None
    link, facts = entry
    try:
        return {
            "state": link_state(
                link,
                owner_link_facts_from_data(facts),
                paused=False,
                preparation_failed=preparation_failed,
            ).state,
            "source": link.source,
            "provider": link.provider,
        }
    except (OwnerLinkError, ReportValueError) as error:
        raise ReviewWebError("unavailable") from error


def _library_item(
    case_document: str | None,
    report_document: str,
    decision_documents: tuple[str, ...],
    automatic_attention_destinations: tuple[str, ...] = (),
    suspended_destinations: tuple[str, ...] = (),
    shadow_abstention_destinations: tuple[str, ...] = (),
    terminal_document: str | None = None,
    attention_count: int | None = None,
    pinned_count: int = 0,
    *,
    decision_scope: DecisionScope | None = None,
    validated: ValidatedDocumentCache | None = None,
    match_state: str | None = None,
    report_facts: LibraryReportFacts | None = None,
    link: dict[str, object] | None = None,
    find_offer: FindOffer | None = None,
) -> dict[str, object]:
    if case_document is None:
        stopped = _terminal_library_item(report_document)
        if stopped is not None:
            return stopped
    case: CaseBrowseFacts | None
    if match_state is not None and (
        case_document is not None or match_state not in _NOT_MATCHED_STATES
    ):
        raise ReviewWebError("unavailable")
    if case_document is None:
        report, case = (
            (_report_facts(report_document) if report_facts is None else report_facts),
            None,
        )
    else:
        case, report = _reused_or_parsed(
            validated,
            BROWSE_FACTS_REUSE,
            partial(_case_and_report, case_document, report_document),
            case_document,
            report_document,
        )
    decisions, automatic_outcomes = _decided_destinations(
        case_document, decision_documents, decision_scope, validated
    )
    proposals = () if case is None else case.proposals
    attention = (
        []
        if case is None
        else [
            value
            for value in proposals
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
                for value in proposals
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
        "authors": _report_authors(report.target),
        "attention_count": current_attention_count,
        # The fields an owner pin holds on this case (ADR-0085, Amendment 2).
        "pinned_count": pinned_count,
        # The cover the library holds, retained by this report, so the row can
        # show the book it stands for; `library_cover` serves only this preview
        # (#1290). The row names no connection, so the server states the
        # address, as it does `detail_url`.
        "cover": None
        if report.cover is None
        else {
            "height": report.cover.height,
            "preview_sha256": report.cover.preview_digest,
            "url": "/api/v1/library/cover?"
            + urlencode(
                {
                    "connection": report.target.connection,
                    "library": report.target.library,
                    "item": report.target.item,
                    "preview": report.cover.preview_digest,
                }
            ),
            "width": report.cover.width,
        },
        # The lens this work is read under: a later failure outranks the
        # retained report's own state unless a decision is still open (#1413).
        "attention_state": (
            "needs_attention"
            if current_attention_count
            else "preparation_failed"
            if terminal_document is not None
            else "not_matched"
            if match_state is not None
            else "no_attention_needed"
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
        # Whether the header offers Find this book, so it shows its final
        # entry with the row rather than when the link view answers (#1477).
        "find_offered": find_offer is not None
        and find_offer(
            report.target.connection,
            report.target.library,
            report.file.format,
            terminal_document is not None,
        ),
        # The work's book link state, or None when it has none (#1477, S29).
        "link": link,
        "media_kind": report.file.format,
        "review_case_id": None if case is None else case.review_case_id,
        "title": report.target.current_title or "Untitled",
        "work_id": work_id,
        "preparation": None
        if terminal_document is None
        else _library_item(None, terminal_document, ())["preparation"],
        "preparation_outcomes": _provider_outcomes(report.providers),
        # A work with no current review case has no case identity to open, so
        # its current values were unreachable from Library. The bounded target
        # projection travels with the row the owner already has, which keeps
        # every published work inspectable without a second authority.
        "summary": None
        if case is not None
        else {
            "fields": [_presented_field(field) for field in report.target.fields],
            "reason": (
                "Nothing to decide for this work. These are its values as "
                "Mechanical Librarian last read them."
            ),
            # A work no provider matched names why, in a closed state the row
            # words; any other work has nothing more to say (#1407).
            "state": "no_decision_recorded"
            if match_state is None
            else "not_matched_" + match_state,
        },
        # The rule the stored flags were derived by, over the same report, so
        # Looks wrong and the row name the same findings (#1334).
        "value_flags": [
            {"destination": destination, "rule": rule}
            for destination, rule in value_flags(
                report.target.fields, report.source_titles
            )
        ],
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
    case: CaseBrowseFacts,
    report: LibraryReportFacts,
    destination: str,
    work_attention_count: int,
    automatic_outcome: str | None = None,
    cause: ReviewFieldCause | None = None,
    policy_attention: bool = False,
    pin_changed: bool = False,
) -> dict[str, object] | None:
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
            # membership predicate holds the same reasons on every medium, so
            # a recording's settled proposal is no more the owner's here than
            # an ebook's (#1249). Re-deciding membership from a shadow
            # disposition would page short of the count the same query
            # reports (#967).
            _needs_attention(proposal)
            # The policy's Ask me, or the record's answer from elsewhere.
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
        pin="changed" if pin_changed else None,
    )
    if not classification.needs_attention:
        # Query membership can race a durable automatic or human decision.  A
        # stale browse row is omitted; it is never an outage for other work.
        return None
    result = proposal["result"]
    assert type(result) is dict
    return {
        "authors": _report_authors(report.target),
        "classification_reason": classification.reason,
        "destination": destination,
        "detail_url": f"/review/{case.review_case_id}?field={destination}",
        "item": report.target.item,
        "library": report.target.library,
        "media_kind": report.file.format,
        "notices": list(result["reasons"]),
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
