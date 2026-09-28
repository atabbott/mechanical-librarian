"""The field-effect query and projection side: activity, pending work, claims.

This module owns no table. Every method here reads across the field-effect,
review-case, decision, cover-consideration, metadata-policy and report
aggregates and projects the result; the one write it performs --
``claim_field_effect`` -- is delegated back out through injected callables
rather than reaching into another aggregate's statements.

That is why it is separate from ``field_effects.py``, which owns the writes and
the tables. Splitting the aggregate on the write/read seam is what keeps
``field_effects.py`` importing nothing from here and this module importing
nothing from there: the graph stays acyclic and both stay under the size
ceiling.

Ten seams cross the boundary and every one arrives as a callable resolved
through the composing repository at call time, never as a bound method captured
at construction. Two of them are load-bearing for tests that substitute on the
composing repository:

- ``current_resolved_policy_id`` is patched on the composing repository's
  *class* by ``test_consent_is_invalidated_when_resolved_policy_semantics_change``
  and two siblings, and drives ``field_effect_consent_envelope_identity`` and
  ``field_effect_authority_is_current`` from here.
- ``field_effect_consent_envelope_identity`` is patched on the composing
  repository's class by
  ``test_semantic_consent_drift_wins_the_atomic_claim_race`` and must reach
  ``claim_field_effect``. Because this module also *defines* that method, the
  injected hook carries the distinct name ``_consent_envelope_identity`` --
  binding the injected callable under the public name would shadow the
  definition and make the composing repository's delegating stub recurse.

The two cover references are properties rather than plain attributes for the
same reason: ``bind_current_cover_policy`` rebinds them on the composing
repository long after this module is constructed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from collections import deque
from collections.abc import Callable
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any, Literal, TypeVar, cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _automatic_decision_value,
    _cover_automatic_decision_value,
    _validate_automatic_decision_authority,
)
from mechanical_librarian.adapters.persistence.schema import _validate_report_row
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    preparation_failure_reference,
)
from mechanical_librarian.domain.activity_records import activity_search_text
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
    automatic_safety_category,
    qualification_manifest_is_current,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    CoverAutomaticDecision,
    parse_automatic_authority,
)
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceCandidate,
)
from mechanical_librarian.domain.field_effect import (
    FIELD_EFFECT_FAILURES,
    SUPPORTED_DESTINATIONS,
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectNoChange,
    FieldEffectOperation,
    decision_value_kind,
    field_value_version,
    project_decision_value,
)
from mechanical_librarian.domain.field_effect import (
    canonical_json as canonical_effect_json,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_REPORT_BYTES,
    MetadataReport,
    ProviderStatus,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    ProposalValueError,
)
from mechanical_librarian.domain.readable_activity import (
    ABSENT,
    AUTOMATIC_AUTHORITY_UNAVAILABLE,
    ISBN_REFUSAL_REASONS,
    destination_label,
    policy_option_label,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
)
from mechanical_librarian.domain.shadow_outcome import (
    ShadowOutcomeError,
    ShadowOutcomeSet,
)

_ACTIVITY_PAGE_BUSY_TIMEOUT_MILLISECONDS = 150
_ACTIVITY_PAGE_BUSY_RETRY_SECONDS = 0.75
# Oversized pages are still served, but the two-entry cache together retains no
# more serialized report text than one maximum-size report document.
_ACTIVITY_PAGE_CACHE_MAX_BYTES = MAX_REPORT_BYTES
_ActivityRead = TypeVar("_ActivityRead")
_ActivityDatabaseIdentity = tuple[int, int, int, int]

# A decision retained from before audiobook writes existed is history, not
# pending work: it must not be offered for consent, counted as prepared, or
# handed to the scheduler at all.  The scheduler skips one too, but a decision
# the owner is told about is decided here (#968).
_RECORDED_ONLY_EXCLUDED_SQL = (
    "json_extract(human.document,'$.application') IS NOT 'recorded_only' AND "
)

_CORRECTIVE_KEEP_CURRENT_SQL = (
    "human.outcome='keep_current' AND EXISTS (SELECT 1 FROM review_cases review, "
    "json_each(review.document,'$.target.fields') target WHERE review.review_case_id=human.review_case_id "
    "AND json_extract(target.value,'$.destination')=human.destination "
    "AND json_extract(target.value,'$.state')='value' AND ((human.destination IN "
    "('authors','genres','narrators','tags','series') AND "
    "json_type(target.value,'$.value')='array' "
    "AND json_array_length(json_extract(target.value,'$.value'))>0) OR "
    "(human.destination='cover' AND json_type(target.value,'$.value')='text' "
    "AND json_extract(target.value,'$.value') GLOB 'cover_[0-9a-f]*') OR "
    "(human.destination NOT IN "
    "('authors','genres','narrators','tags','series','cover') "
    "AND json_type(target.value,'$.value')='text' "
    "AND length(json_extract(target.value,'$.value'))>0)) AND EXISTS (SELECT 1 FROM field_effects prior "
    "JOIN (SELECT decision_id,ordinal FROM human_decisions earlier WHERE "
    "earlier.review_case_id=human.review_case_id AND earlier.destination=human.destination "
    "AND earlier.ordinal<human.ordinal UNION ALL SELECT decision_id,-1 FROM "
    "automatic_decisions earlier WHERE earlier.review_case_id=human.review_case_id "
    "AND earlier.destination=human.destination UNION ALL SELECT decision_id,-1 FROM "
    "description_decisions earlier WHERE earlier.review_case_id=human.review_case_id "
    "AND human.destination='description') recorded ON recorded.decision_id=prior.decision_id "
    "WHERE prior.state='verified'))"
)


def _operational_signal(
    kind: str,
    cause: str,
    scope: str,
    *,
    connection: str | None = None,
    library: str | None = None,
    category: str | None = None,
    work_id: str | None = None,
    field: str | None = None,
    diagnostic_reference: str | None = None,
    stage: str | None = None,
    code: str | None = None,
    attempt: int | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "category": category,
        "cause": cause,
        "connection": connection,
        "field": field,
        "kind": kind,
        "library": library,
        "scope": scope,
        "work_id": work_id,
    }
    if diagnostic_reference is not None:
        value["diagnostic_reference"] = diagnostic_reference
    if stage is not None:
        value["stage"] = stage
    if code is not None:
        value["code"] = code
    if attempt is not None:
        value["attempt"] = attempt
    return value


def _policy_changes(
    profiles: dict[str, MetadataPolicyProfile],
    from_policy_id: object,
    to_policy_id: object,
) -> list[dict[str, str]]:
    """Diff the two immutable profiles one transition names, in owner terms.

    A profile carries two independent settings per field -- how it is reviewed
    (``destinations``) and how its value is represented (``options``) -- so an
    entry names both the field and which of its settings moved. Ordering is by
    field so the same transition always reads the same way.
    """
    before = profiles.get(cast(str, from_policy_id))
    after = profiles.get(cast(str, to_policy_id))
    if before is None or after is None:
        return []
    changes: list[dict[str, str]] = []
    for setting, previous_pairs, next_pairs in (
        ("Review mode", before.destinations, after.destinations),
        ("Representation", before.options, after.options),
    ):
        previous_values, next_values = dict(previous_pairs), dict(next_pairs)
        for field in sorted(set(previous_values) | set(next_values)):
            previous_value = previous_values.get(field)
            next_value = next_values.get(field)
            if previous_value == next_value:
                continue
            changes.append(
                {
                    "field": destination_label(field),
                    "setting": setting,
                    "previous": (
                        ABSENT
                        if previous_value is None
                        else policy_option_label(previous_value, field)
                    ),
                    "next": (
                        ABSENT
                        if next_value is None
                        else policy_option_label(next_value, field)
                    ),
                }
            )
    return changes


def _bounded_fetch(cursor: sqlite3.Cursor, expected_max: int) -> list[tuple[Any, ...]]:
    """Read a bounded dependency query without materialising a larger result."""
    rows = cursor.fetchmany(expected_max + 1)
    if len(rows) > expected_max:
        raise FieldEffectError("persistence")
    return rows


class SQLiteFieldEffectActivityRepository(ConnectionScoped):
    """Project field-effect activity, pending work and operational signals."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        review_case_accepts_authority: Callable[[str], bool],
        get_review_case: Callable[[str], str],
        validate_shadow_outcome_row: Callable[[tuple[object, ...]], tuple[str, int]],
        current_resolved_policy_id: Callable[[str], str],
        field_effect_paused: Callable[[], bool],
        field_effect_capability_suspension: Callable[[str, str], str | None],
        consent_envelope_identity: Callable[[str], str],
        update_field_effect: Callable[[FieldEffectOperation, str], None],
        record_field_effect_event: Callable[[FieldEffectOperation, str, int], None],
        field_effect_event_documents: Callable[[str], tuple[str, ...]],
        current_cover_policy_reference: Callable[[], str | None],
        current_cover_replay_reference: Callable[[], str | None],
        description_authority_value: Callable[[AuthoredDescriptionDecision], object],
    ) -> None:
        super().__init__(connection, binding)
        self._review_case_accepts_authority = review_case_accepts_authority
        self.get_review_case = get_review_case
        self._validate_shadow_outcome_row = validate_shadow_outcome_row
        self.current_resolved_policy_id = current_resolved_policy_id
        self.field_effect_paused = field_effect_paused
        self.field_effect_capability_suspension = field_effect_capability_suspension
        self._consent_envelope_identity = consent_envelope_identity
        self._update_field_effect = update_field_effect
        self._record_field_effect_event = record_field_effect_event
        self._field_effect_event_documents = field_effect_event_documents
        self._cover_policy_reference = current_cover_policy_reference
        self._cover_replay_reference = current_cover_replay_reference
        self._description_authority_value = description_authority_value
        # Activity's page is immutable at one SQLite data revision.  The
        # bounded reader-local cache prevents repeated foreground refreshes
        # from re-running its expensive union while preparation is between
        # commits; it is not a shared cache or an event transport.
        self._activity_page_cache: dict[
            tuple[str | None, str, int, int],
            tuple[
                tuple[int, int],
                int,
                int | None,
                int,
                dict[str, object],
                _ActivityDatabaseIdentity,
            ],
        ] = {}
        database = next(
            (
                row
                for row in connection.execute("PRAGMA database_list").fetchall()
                if len(row) == 3 and row[1] == "main"
            ),
            None,
        )
        self._activity_database_path = (
            None
            if binding is not None
            or database is None
            or type(database[2]) is not str
            or not database[2]
            else Path(database[2])
        )
        self._activity_page_cache_bytes = 0
        self._activity_page_cache_lock = RLock()
        self._backup_ready_cache: tuple[tuple[int, int], bool] | None = None
        self._activity_page_busy_retry_seconds = 0.0

    @property
    def _current_cover_policy_reference(self) -> str | None:
        """Read the composing repository's currently bound cover policy."""
        return self._cover_policy_reference()

    @property
    def _current_cover_replay_reference(self) -> str | None:
        """Read the composing repository's currently bound cover replay."""
        return self._cover_replay_reference()

    @local_operation
    def field_effect_authority_source(
        self,
        decision: HumanDecision
        | AutomaticDecision
        | CoverAutomaticDecision
        | AuthoredDescriptionDecision,
    ) -> dict[str, object]:
        """Resolve one exact persisted decision against its current case lineage."""
        try:
            return self._authority_source(decision)
        except sqlite3.Error as error:
            # Only a storage fault is a storage fault. This is deliberately
            # narrower than the consent resolver, which also reads a decode,
            # key or value failure as `persistence`: here those shapes are how
            # a binding refusal is expressed on purpose -- a document that does
            # not match the decision is a mismatch, not an outage -- so
            # sweeping them in would relabel real refusals as faults, the exact
            # inverse of the untruth this classification exists to remove.
            # Every `FieldEffectError` raised below passes through untouched
            # because nothing but `sqlite3.Error` is caught here.
            raise FieldEffectError("persistence") from error

    def _authority_source(
        self,
        decision: HumanDecision
        | AutomaticDecision
        | CoverAutomaticDecision
        | AuthoredDescriptionDecision,
    ) -> dict[str, object]:
        paired_bundle_id = None
        paired_decision_id = None
        if not self._review_case_accepts_authority(decision.review_case_id):
            raise FieldEffectError("authority_binding")
        if type(decision) is AuthoredDescriptionDecision:
            row = self._connection.execute(
                "SELECT document FROM description_decisions WHERE decision_id=?",
                (decision.decision_id,),
            ).fetchone()
        elif type(decision) is CoverAutomaticDecision:
            row = self._connection.execute(
                "SELECT json_extract(selection.document,'$.decision_document') "
                "FROM current_cover_selection_authorities current "
                "JOIN cover_selection_authorities selection "
                "ON selection.selection_id=current.selection_id "
                "WHERE selection.kind='automatic' AND selection.decision_id=?",
                (decision.decision_id,),
            ).fetchone()
        else:
            table = (
                "human_decisions"
                if type(decision) is HumanDecision
                else "automatic_decisions"
            )
            row = self._connection.execute(
                f"SELECT document FROM {table} WHERE decision_id=?",
                (decision.decision_id,),
            ).fetchone()
        if row is None or row[0] != decision.to_json():
            raise FieldEffectError("authority_binding")
        envelope = json.loads(self.get_review_case(decision.review_case_id))
        if (
            type(envelope) is not dict
            or envelope.get("current") is not True
            or type(envelope.get("case")) is not dict
        ):
            raise FieldEffectError("authority_binding")
        case = ReviewCase.from_json(_canonical_json(envelope["case"]))
        # ``value`` carries either a human selection envelope or the projected
        # automatic envelope, so it is declared at the widest of the two.
        value: object
        if type(decision) is AuthoredDescriptionDecision:
            value = self._description_authority_value(decision)
        elif type(decision) is HumanDecision:
            decision.validate_for(case)
            value = decision.selected_value
        else:
            automatic = cast(AutomaticDecision | CoverAutomaticDecision, decision)
            report_row = self._connection.execute(
                "SELECT document FROM metadata_reports WHERE report_id=?",
                (automatic.report_id,),
            ).fetchone()
            proposal_row = self._connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (automatic.proposal_set_id,),
            ).fetchone()
            if report_row is None or proposal_row is None:
                raise FieldEffectError("authority_binding")
            try:
                if type(automatic) is CoverAutomaticDecision:
                    consideration_row = self._connection.execute(
                        "SELECT consideration.document FROM current_cover_considerations current "
                        "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                        "WHERE current.review_case_id=? AND current.consideration_id=?",
                        (automatic.review_case_id, automatic.consideration_id),
                    ).fetchone()
                    if consideration_row is None:
                        raise FieldEffectError("authority_binding")
                    value = _cover_automatic_decision_value(
                        self._connection,
                        automatic,
                        case,
                        MetadataReport.from_json(report_row[0]),
                        ProposalSet.from_json(proposal_row[0]),
                        consideration_row[0],
                    )
                else:
                    outcome_row = self._connection.execute(
                        "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? "
                        "AND report_id=? AND proposal_set_id=? AND resolved_policy_id=?",
                        (
                            automatic.review_case_id,
                            automatic.report_id,
                            automatic.proposal_set_id,
                            automatic.resolved_policy_id,
                        ),
                    ).fetchone()
                    if outcome_row is None:
                        raise FieldEffectError("authority_binding")
                    value = _automatic_decision_value(
                        cast(AutomaticDecision, automatic),
                        case,
                        MetadataReport.from_json(report_row[0]),
                        ProposalSet.from_json(proposal_row[0]),
                        ShadowOutcomeSet.from_json(outcome_row[0]),
                    )
                    from mechanical_librarian.domain.automatic_decision import (
                        audiobook_title_subtitle_bundle,
                    )

                    members = tuple(
                        field
                        for field in ShadowOutcomeSet.from_json(outcome_row[0]).outcomes
                        if field.destination in {"title", "subtitle"}
                    )
                    if (
                        decision.destination in {"title", "subtitle"}
                        and audiobook_title_subtitle_bundle(members) is not None
                    ):
                        paired_bundle_id = automatic.bundle_id
                        sibling_source = next(
                            field
                            for field in members
                            if field.destination != decision.destination
                        )
                        sibling_row = self._connection.execute(
                            "SELECT document FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                            (decision.review_case_id, sibling_source.destination),
                        ).fetchone()
                        if sibling_row is None:
                            raise FieldEffectError("authority_binding")
                        sibling = AutomaticDecision.from_json(sibling_row[0])
                        if (
                            sibling.bundle_id != paired_bundle_id
                            or sibling.source_outcome_id != sibling_source.outcome_id
                        ):
                            raise FieldEffectError("authority_binding")
                        _automatic_decision_value(
                            sibling,
                            case,
                            MetadataReport.from_json(report_row[0]),
                            ProposalSet.from_json(proposal_row[0]),
                            ShadowOutcomeSet.from_json(outcome_row[0]),
                        )
                        paired_decision_id = sibling.decision_id
            except (
                ReportValueError,
                ProposalValueError,
                RichMetadataPreviewError,
                ShadowOutcomeError,
            ) as error:
                raise FieldEffectError("authority_binding") from error
        lineage = self._connection.execute(
            "SELECT connection_name,library_name,item_id FROM current_review_case_lineages "
            "WHERE review_case_id=?",
            (decision.review_case_id,),
        ).fetchone()
        if lineage is None:
            raise FieldEffectError("authority_binding")
        target_fields = case.target.get("fields")
        if type(target_fields) is not list:
            raise FieldEffectError("authority_binding")
        target_field = next(
            (
                item
                for item in target_fields
                if type(item) is dict
                and item.get("destination") == decision.destination
            ),
            None,
        )
        if type(target_field) is not dict or target_field.get("state") not in {
            "value",
            "null",
            "empty",
            "missing",
        }:
            raise FieldEffectError("authority_binding")
        case_pre_value = (
            target_field.get("value") if target_field["state"] == "value" else None
        )
        pre_value = case_pre_value
        predecessor = None
        if type(decision) is HumanDecision:
            predecessor = self._connection.execute(
                "SELECT effect.document FROM field_effects effect JOIN ("
                "SELECT decision_id,ordinal FROM human_decisions earlier WHERE "
                "earlier.review_case_id=? AND earlier.destination=? AND earlier.ordinal<("
                "SELECT ordinal FROM human_decisions WHERE decision_id=?) UNION ALL "
                "SELECT decision_id,-1 FROM automatic_decisions earlier WHERE "
                "earlier.review_case_id=? AND earlier.destination=?"
                ") recorded ON (recorded.decision_id=effect.decision_id OR recorded.decision_id=json_extract(effect.document,'$.plan.subtitle_companion.authority.decision_id')) "
                "WHERE effect.state='verified' ORDER BY recorded.ordinal DESC LIMIT 1",
                (
                    decision.review_case_id,
                    decision.destination,
                    decision.decision_id,
                    decision.review_case_id,
                    decision.destination,
                ),
            ).fetchone()
            if decision.destination == "description":
                authored_predecessor = self._connection.execute(
                    "SELECT effect.document FROM field_effects effect "
                    "JOIN description_decisions authored ON authored.decision_id=effect.decision_id "
                    "WHERE authored.review_case_id=? AND effect.state='verified' "
                    "ORDER BY effect.updated_at DESC,effect.rowid DESC LIMIT 1",
                    (decision.review_case_id,),
                ).fetchone()
                if authored_predecessor is not None and (
                    predecessor is None
                    or FieldEffectOperation.from_json(
                        authored_predecessor[0]
                    ).updated_at
                    > FieldEffectOperation.from_json(predecessor[0]).updated_at
                ):
                    predecessor = authored_predecessor
        if predecessor is not None:
            try:
                previous = FieldEffectOperation.from_json(predecessor[0])
            except FieldEffectError as error:
                raise FieldEffectError("authority_binding") from error
            previous_plan = (
                previous.plan.subtitle_plan()
                if decision.destination == "subtitle"
                and previous.plan.subtitle_companion is not None
                else previous.plan
            )
            if (
                previous_plan is None
                or previous_plan.authority.review_case_id != decision.review_case_id
                or previous_plan.destination != decision.destination
            ):
                raise FieldEffectError("authority_binding")
            pre_value = previous_plan.desired_value
        if type(decision) is HumanDecision and decision.outcome == "keep_current":
            if predecessor is not None:
                value = self._decision_value_for_target(
                    decision.destination, case_pre_value
                )
        dependency_digests: tuple[str, ...] = (decision.target_fingerprint,)
        if type(decision) is CoverAutomaticDecision:
            dependency_digests = (
                decision.target_fingerprint,
                decision.consideration_digest,
            )
        if type(decision) is HumanDecision and decision.destination == "cover":
            consideration_row = self._connection.execute(
                "SELECT consideration.document FROM current_cover_considerations current "
                "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                "JOIN cover_consideration_events event ON event.consideration_id=consideration.consideration_id "
                "WHERE current.review_case_id=? AND event.kind='selected' AND event.decision_id=?",
                (decision.review_case_id, decision.decision_id),
            ).fetchone()
            if consideration_row is None:
                raise FieldEffectError("authority_binding")
            consideration = json.loads(consideration_row[0])
            selected_artifact = value.get("value") if type(value) is dict else None
            selected_candidate = next(
                (
                    candidate
                    for candidate in consideration.get("candidates", ())
                    if type(candidate) is dict
                    and candidate.get("artifact_id") == selected_artifact
                    and candidate.get("selectable") is True
                    and candidate.get("incumbent") is False
                ),
                None,
            )
            incumbent_digest = consideration.get("incumbent_digest")
            if (
                selected_candidate is None
                or consideration.get("target_fingerprint")
                != decision.target_fingerprint
                or consideration.get("policy_reference")
                != self._current_cover_policy_reference
                or consideration.get("replay_reference")
                != self._current_cover_replay_reference
                or (
                    predecessor is None
                    and incumbent_digest is not None
                    and pre_value != "cover_" + str(incumbent_digest)
                )
            ):
                raise FieldEffectError("authority_binding")
            dependency_digests = (
                decision.target_fingerprint,
                hashlib.sha256(consideration_row[0].encode("utf-8")).hexdigest(),
            )
        return {
            "paired_bundle_id": paired_bundle_id,
            "paired_decision_id": paired_decision_id,
            "connection": lineage[0],
            "library": lineage[1],
            "work_id": lineage[2],
            "destination": decision.destination,
            "review_case_id": decision.review_case_id,
            "target_fingerprint": decision.target_fingerprint,
            "target_version": field_value_version(decision.destination, pre_value),
            "decision_value": value,
            "source": "persisted_review_case",
            "pre_value": pre_value,
            "dependency_digests": dependency_digests,
        }

    @staticmethod
    def _decision_value_for_target(
        destination: str, value: object
    ) -> dict[str, object]:
        if value is None:
            raise FieldEffectError("authority_binding")
        return {"kind": decision_value_kind(destination), "value": value}

    @local_operation
    def field_effect_authority_is_current(self, authority: EffectAuthority) -> bool:
        try:
            return self._authority_is_current(authority)
        except sqlite3.Error as error:
            # `False` here means the authority was revoked, which stales the
            # plan under `authority`. A storage fault knows nothing about the
            # authority, so answering it with `False` would report an outage as
            # a revocation; raising instead reaches the boundary as a mapped
            # refusal rather than escaping this seam untyped. The fail-closed
            # `False` below stays what it always was: scoped to policy
            # resolution, which is a decision, not an outage.
            raise FieldEffectError("persistence") from error

    def _authority_is_current(self, authority: EffectAuthority) -> bool:
        current = (
            self._connection.execute(
                "SELECT current FROM field_effect_authorities WHERE decision_id=?",
                (authority.decision_id,),
            ).fetchone()
            == (1,)
            and self._connection.execute(
                "SELECT 1 FROM current_review_case_lineages WHERE review_case_id=?",
                (authority.review_case_id,),
            ).fetchone()
            == (1,)
            and self._review_case_accepts_authority(authority.review_case_id)
        )
        description = self._connection.execute(
            "SELECT document FROM description_decisions WHERE decision_id=?",
            (authority.decision_id,),
        ).fetchone()
        if description is not None:
            if not current:
                return False
            try:
                from mechanical_librarian.application.field_effects import (
                    authority_from_decision,
                )

                authored = AuthoredDescriptionDecision.from_json(description[0])
                projected = authority_from_decision(authored)
                self._description_authority_value(authored)
                return (
                    projected is not None
                    and replace(projected, source="persisted_review_case") == authority
                )
            except (ValueError, FieldEffectError):
                return False
        if not current or authority.origin != "automatic_policy":
            return current
        cover_selection = self._connection.execute(
            "SELECT selection.decision_id FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection "
            "ON selection.selection_id=current.selection_id "
            "WHERE selection.decision_id=? AND selection.kind='automatic'",
            (authority.decision_id,),
        ).fetchone()
        cover_history = self._connection.execute(
            "SELECT 1 FROM cover_selection_authorities WHERE decision_id=? "
            "AND kind='automatic'",
            (authority.decision_id,),
        ).fetchone()
        if cover_history is not None and cover_selection != (authority.decision_id,):
            return False
        if cover_history is None:
            automatic_row = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE decision_id=?",
                (authority.decision_id,),
            ).fetchone()
            if automatic_row is None:
                return False
            try:
                decision = AutomaticDecision.from_json(automatic_row[0])
            except AutomaticDecisionError:
                return False
            paired_row = (
                self._connection.execute(
                    "SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination='subtitle' AND json_extract(document,'$.bundle_id')=? AND json_extract(document,'$.policy_option')='corroborated_split:audiobook_subtitle_fill'",
                    (decision.review_case_id, decision.bundle_id),
                ).fetchone()
                if decision.bundle_id is not None
                else None
            )
            if paired_row is not None:
                try:
                    self._authority_source(decision)
                except (ValueError, FieldEffectError):
                    return False
                if (
                    self._connection.execute(
                        "SELECT 1 FROM field_effect_manual_holds WHERE review_case_id=? AND destination IN ('title','subtitle') AND active=1",
                        (decision.review_case_id,),
                    ).fetchone()
                    is not None
                ):
                    return False
            if not qualification_manifest_is_current(
                decision.policy_category,
                decision.policy_option,
                authority.qualification_identity,
            ):
                return False
        lineage = self._connection.execute(
            "SELECT library_name FROM current_review_case_lineages "
            "WHERE review_case_id=?",
            (authority.review_case_id,),
        ).fetchone()
        if lineage is None:
            return False
        if self._connection.execute(
            "SELECT 1 FROM field_effect_manual_holds hold "
            "WHERE hold.review_case_id=? AND hold.destination='cover' "
            "AND hold.active=1 AND EXISTS(SELECT 1 FROM cover_selection_authorities "
            "WHERE decision_id=? AND kind='automatic') "
            "UNION ALL SELECT 1 FROM automatic_decisions automatic "
            "JOIN field_effect_manual_holds hold ON hold.review_case_id=automatic.review_case_id "
            "AND hold.destination=automatic.destination AND hold.active=1 "
            "WHERE automatic.decision_id=? LIMIT 1",
            (authority.review_case_id, authority.decision_id, authority.decision_id),
        ).fetchone() == (1,):
            return False
        try:
            return (
                self.current_resolved_policy_id(lineage[0]) == authority.policy_identity
            )
        except MetadataReportError:
            return False

    @local_operation
    def field_effect_consent_envelope_identity(self, library: str) -> str:
        """Bind consent to the exact current automatic policy semantics."""
        if type(library) is not str or not library:
            raise FieldEffectError("invalid_consent")
        return self._field_effect_consent_envelope_identity(
            library, self.current_resolved_policy_id(library)
        )

    @staticmethod
    def _field_effect_consent_envelope_identity(
        library: str,
        resolved_policy_id: str,
    ) -> str:
        document = canonical_effect_json(
            {
                "action": "apply_field",
                "destinations": sorted(SUPPORTED_DESTINATIONS),
                "library": library,
                "resolved_policy_id": resolved_policy_id,
                "schema_version": "field-effect-consent-envelope-v1",
            }
        )
        return hashlib.sha256(document.encode("utf-8")).hexdigest()

    @local_operation
    def pending_field_effect_decision_documents(
        self,
        limit: int,
        after: tuple[int, str] | None = None,
    ) -> tuple[str, ...]:
        return self._pending_field_effect_decision_documents(limit, after, ordered=True)

    @local_operation
    def pending_field_effect_activity_example_document(self) -> str | None:
        """Select one exact-current pending decision for illustration."""
        documents = self._pending_field_effect_decision_documents(
            1, None, ordered=False
        )
        return documents[0] if documents else None

    def _pending_field_effect_decision_documents(
        self,
        limit: int,
        after: tuple[int, str] | None,
        *,
        ordered: bool,
    ) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        if after is not None and (
            type(after) is not tuple
            or len(after) != 2
            or type(after[0]) is not int
            or after[0] < 0
            or type(after[1]) is not str
            or not after[1]
        ):
            raise FieldEffectError("invalid_limit")
        try:
            cursor = ""
            parameters: tuple[object, ...] = ()
            if after is not None:
                cursor = (
                    "AND (pending.occurred_at>? OR "
                    "(pending.occurred_at=? AND pending.decision_id>?)) "
                )
                parameters = (after[0], after[0], after[1])
            rows = self._connection.execute(
                "SELECT pending.document FROM ("
                "SELECT human.decision_id,human.occurred_at,human.document "
                "FROM human_decisions human WHERE NOT EXISTS (SELECT 1 FROM current_description_decisions authored "
                "WHERE authored.review_case_id=human.review_case_id AND human.destination='description') AND (human.outcome='accept' OR ("
                + _CORRECTIVE_KEEP_CURRENT_SQL
                + ")) AND "
                + _RECORDED_ONLY_EXCLUDED_SQL
                + "(EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=human.review_case_id) OR ("
                "human.destination='cover' AND EXISTS (SELECT 1 FROM "
                "cover_consideration_events selected JOIN current_cover_considerations current "
                "ON current.consideration_id=selected.consideration_id "
                "WHERE selected.decision_id=human.decision_id AND selected.kind='selected' "
                "AND current.review_case_id=human.review_case_id) AND NOT EXISTS ("
                "SELECT 1 FROM cover_consideration_events stale WHERE "
                "stale.decision_id=human.decision_id AND stale.kind='stale'))) AND "
                "human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=human.review_case_id AND "
                "returned.destination=human.destination AND returned.active=0) UNION ALL "
                "SELECT automatic.decision_id,automatic.occurred_at,automatic.document "
                "FROM automatic_decisions automatic WHERE NOT EXISTS (SELECT 1 FROM current_description_decisions authored WHERE authored.review_case_id=automatic.review_case_id AND automatic.destination='description') AND "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage JOIN "
                "current_review_packages package ON package.connection_name=lineage.connection_name "
                "AND package.library_name=lineage.library_name AND package.item_id=lineage.item_id "
                "AND package.proposal_set_id=lineage.proposal_set_id AND package.report_id=lineage.report_id "
                "AND package.review_case_id=lineage.review_case_id AND package.checkpoint=lineage.checkpoint "
                "WHERE lineage.review_case_id=automatic.review_case_id) AND "
                "json_extract(automatic.document,'$.outcome') IN ('candidate','keep_current') AND NOT EXISTS ("
                "SELECT 1 FROM human_decisions human WHERE "
                "human.review_case_id=automatic.review_case_id AND "
                "human.destination=automatic.destination AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=automatic.review_case_id AND "
                "returned.destination=automatic.destination AND returned.active=0)) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds hold WHERE "
                "hold.review_case_id=automatic.review_case_id AND "
                "hold.destination=automatic.destination AND hold.active=1)"
                " UNION ALL SELECT selection.decision_id,selection.occurred_at,"
                "json_extract(selection.document,'$.decision_document') "
                "FROM current_cover_selection_authorities current "
                "JOIN cover_selection_authorities selection "
                "ON selection.selection_id=current.selection_id "
                "JOIN cover_considerations consideration "
                "ON consideration.consideration_id=selection.consideration_id "
                "JOIN current_cover_considerations current_consideration "
                "ON current_consideration.consideration_id=consideration.consideration_id "
                "JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=consideration.review_case_id "
                "WHERE selection.kind='automatic'"
                " UNION ALL SELECT d.decision_id,json_extract(d.document,'$.occurred_at'),d.document FROM current_description_decisions p JOIN description_decisions d ON d.decision_id=p.decision_id JOIN current_review_case_lineages l ON l.review_case_id=d.review_case_id"
                ") AS pending WHERE NOT EXISTS (SELECT 1 FROM field_effects effect "
                "WHERE (effect.decision_id=pending.decision_id OR json_extract(effect.document,'$.plan.subtitle_companion.authority.decision_id')=pending.decision_id) AND "
                "effect.state NOT IN ('superseded','abandoned')) AND NOT EXISTS (SELECT 1 FROM field_effect_no_changes no_change WHERE no_change.decision_id=pending.decision_id) "
                + cursor
                + (
                    "ORDER BY pending.occurred_at,pending.decision_id "
                    if ordered
                    else ""
                )
                + "LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return tuple(row[0] for row in rows)
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    @local_operation
    def pending_field_effect_decision_counts(self) -> tuple[dict[str, object], ...]:
        """Return exact prepared counts grouped by library, origin and destination.

        Destination is in the grouping because permission is granted per
        destination: an owner asked to enable writes is owed the count each
        destination in the offer accounts for, and a cover destination held back
        by its own gate must not be represented by a number that includes it
        (#639).
        """
        try:
            rows = self._connection.execute(
                "WITH pending AS ("
                "SELECT human.decision_id,human.review_case_id,human.destination,'human' origin "
                "FROM human_decisions human WHERE NOT EXISTS (SELECT 1 FROM current_description_decisions authored "
                "WHERE authored.review_case_id=human.review_case_id AND human.destination='description') AND (human.outcome='accept' OR ("
                + _CORRECTIVE_KEEP_CURRENT_SQL
                + ")) AND "
                + _RECORDED_ONLY_EXCLUDED_SQL
                + "EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=human.review_case_id) AND "
                "human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=human.review_case_id AND "
                "returned.destination=human.destination AND returned.active=0) UNION ALL "
                "SELECT automatic.decision_id,automatic.review_case_id,automatic.destination,'automatic' origin "
                "FROM automatic_decisions automatic WHERE NOT EXISTS (SELECT 1 FROM current_description_decisions authored WHERE authored.review_case_id=automatic.review_case_id AND automatic.destination='description') AND "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage JOIN "
                "current_review_packages package ON package.connection_name=lineage.connection_name "
                "AND package.library_name=lineage.library_name AND package.item_id=lineage.item_id "
                "AND package.proposal_set_id=lineage.proposal_set_id AND package.report_id=lineage.report_id "
                "AND package.review_case_id=lineage.review_case_id AND package.checkpoint=lineage.checkpoint "
                "WHERE lineage.review_case_id=automatic.review_case_id) AND "
                "json_extract(automatic.document,'$.outcome')='candidate' AND NOT EXISTS ("
                "SELECT 1 FROM human_decisions human WHERE "
                "human.review_case_id=automatic.review_case_id AND "
                "human.destination=automatic.destination AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=automatic.review_case_id AND "
                "returned.destination=automatic.destination AND returned.active=0)) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds hold WHERE "
                "hold.review_case_id=automatic.review_case_id AND "
                "hold.destination=automatic.destination AND hold.active=1) UNION ALL "
                "SELECT selection.decision_id,consideration.review_case_id,'cover','automatic' "
                "FROM current_cover_selection_authorities current "
                "JOIN cover_selection_authorities selection "
                "ON selection.selection_id=current.selection_id "
                "JOIN cover_considerations consideration "
                "ON consideration.consideration_id=selection.consideration_id "
                "JOIN current_cover_considerations current_consideration "
                "ON current_consideration.consideration_id=consideration.consideration_id "
                "JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=consideration.review_case_id "
                "WHERE selection.kind='automatic' UNION ALL SELECT d.decision_id,d.review_case_id,'description',CASE json_extract(d.document,'$.origin') WHEN 'human' THEN 'human' ELSE 'automatic' END FROM current_description_decisions p JOIN description_decisions d ON d.decision_id=p.decision_id) "
                "SELECT lineage.connection_name,lineage.library_name,pending.origin,"
                "pending.destination,count(*) "
                "FROM pending JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=pending.review_case_id JOIN current_review_packages package "
                "ON package.connection_name=lineage.connection_name AND package.library_name=lineage.library_name "
                "AND package.item_id=lineage.item_id AND package.proposal_set_id=lineage.proposal_set_id "
                "AND package.report_id=lineage.report_id AND package.review_case_id=lineage.review_case_id "
                "AND package.checkpoint=lineage.checkpoint "
                "WHERE NOT EXISTS (SELECT 1 FROM field_effects effect "
                "WHERE (effect.decision_id=pending.decision_id OR json_extract(effect.document,'$.plan.subtitle_companion.authority.decision_id')=pending.decision_id) AND "
                "effect.state NOT IN ('superseded','abandoned')) AND NOT EXISTS (SELECT 1 FROM field_effect_no_changes no_change WHERE no_change.decision_id=pending.decision_id) "
                "GROUP BY lineage.connection_name,lineage.library_name,pending.origin,"
                "pending.destination ORDER BY lineage.connection_name,"
                "lineage.library_name,pending.origin,pending.destination"
            ).fetchall()
            return tuple(
                {
                    "connection": connection,
                    "library": library,
                    "origin": origin,
                    "destination": destination,
                    "count": count,
                }
                for connection, library, origin, destination, count in rows
            )
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    @local_operation
    def pending_field_effect_manual_returns(
        self,
        limit: int,
        after: tuple[int, str, str] | None = None,
    ) -> tuple[tuple[str, str, str, str, str, str, int, int], ...]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (
                after is not None
                and (
                    type(after) is not tuple
                    or len(after) != 3
                    or type(after[0]) is not int
                    or after[0] < 0
                    or any(type(value) is not str or not value for value in after[1:])
                )
            )
        ):
            raise FieldEffectError("invalid_manual_hold")
        try:
            cursor_clause = ""
            parameters: tuple[object, ...] = ()
            if after is not None:
                cursor_clause = (
                    "AND (hold.updated_at>? OR "
                    "(hold.updated_at=? AND hold.review_case_id>?) OR "
                    "(hold.updated_at=? AND hold.review_case_id=? "
                    "AND hold.destination>?)) "
                )
                parameters = (
                    after[0],
                    after[0],
                    after[1],
                    after[0],
                    after[1],
                    after[2],
                )
            rows = self._connection.execute(
                "SELECT hold.review_case_id,hold.destination,lineage.connection_name,"
                "lineage.library_name,hold.document,hold.updated_at "
                "FROM field_effect_manual_holds hold "
                "JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=hold.review_case_id "
                "WHERE hold.active=1 AND "
                "json_type(hold.document,'$.pending_return')='object' "
                + cursor_clause
                + "ORDER BY hold.updated_at,hold.review_case_id,hold.destination LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            pending = []
            for (
                review_case_id,
                destination,
                connection,
                library,
                document,
                updated_at,
            ) in rows:
                raw = json.loads(document)
                request = raw["pending_return"]
                pending.append(
                    (
                        review_case_id,
                        destination,
                        connection,
                        library,
                        raw["decision_id"],
                        request["scan_id"],
                        request["requested_at"],
                        updated_at,
                    )
                )
            return tuple(pending)
        except (json.JSONDecodeError, KeyError, sqlite3.Error, TypeError) as error:
            raise FieldEffectError("persistence") from error

    @local_operation
    def field_effect_dependencies_current(self, digests: tuple[str, ...]) -> bool:
        if (
            type(digests) is not tuple
            or not digests
            or any(type(value) is not str or len(value) != 64 for value in digests)
        ):
            return False
        try:
            return all(
                self._connection.execute(
                    "SELECT 1 FROM current_review_case_lineages lineage "
                    "JOIN review_cases review ON review.review_case_id=lineage.review_case_id "
                    "WHERE json_extract(review.document,'$.target_fingerprint')=? LIMIT 1",
                    (digest,),
                ).fetchone()
                == (1,)
                or self._connection.execute(
                    "SELECT 1 FROM current_cover_considerations current "
                    "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                    "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
                    "WHERE consideration.document_digest=? "
                    "AND json_extract(consideration.document,'$.policy_reference')=? "
                    "AND json_extract(consideration.document,'$.replay_reference')=? LIMIT 1",
                    (
                        digest,
                        self._current_cover_policy_reference,
                        self._current_cover_replay_reference,
                    ),
                ).fetchone()
                == (1,)
                for digest in digests
            )
        except sqlite3.Error:
            return False

    def _fresh_database_quick_check(self) -> tuple[object, ...] | None:
        database = self._connection.execute("PRAGMA database_list").fetchone()
        if (
            database is None
            or len(database) != 3
            or database[1] != "main"
            or type(database[2]) is not str
        ):
            raise sqlite3.DatabaseError("invalid main database identity")
        if not database[2]:
            return cast(
                "tuple[object, ...] | None",
                self._connection.execute("PRAGMA quick_check").fetchone(),
            )
        uri = f"{Path(database[2]).resolve().as_uri()}?mode=ro"
        with closing(
            sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                timeout=_ACTIVITY_PAGE_BUSY_TIMEOUT_MILLISECONDS / 1_000,
            )
        ) as verifier:
            return cast(
                "tuple[object, ...] | None",
                verifier.execute("PRAGMA quick_check").fetchone(),
            )

    def _field_effect_backup_ready_once(self, *, fresh: bool) -> bool:
        try:
            with self._activity_page_cache_lock:
                return self._serialized(lambda: self._backup_ready_local(fresh=fresh))
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def _backup_ready_local(self, *, fresh: bool) -> bool:
        """Read under the cache lock, admission and physical connection owner."""
        data_version = self._connection.execute("PRAGMA data_version").fetchone()
        if (
            data_version is None
            or len(data_version) != 1
            or type(data_version[0]) is not int
            or data_version[0] < 0
        ):
            return False
        revision = (data_version[0], self._connection.total_changes)
        if (
            not fresh
            and self._backup_ready_cache is not None
            and self._backup_ready_cache[0] == revision
        ):
            return self._backup_ready_cache[1]
        row = (
            self._fresh_database_quick_check()
            if fresh
            else self._connection.execute("PRAGMA quick_check").fetchone()
        )
        ready = row == ("ok",) and not self._connection.in_transaction
        self._backup_ready_cache = (revision, ready)
        return ready

    def field_effect_backup_ready(self, *, fresh: bool = False) -> bool:
        """Prove the exact-current durable store can form a consistent checkpoint."""
        try:
            return self._retry_activity_busy(
                lambda: self._field_effect_backup_ready_once(fresh=fresh)
            )
        except FieldEffectError:
            return False

    @local_operation
    def field_effect_activity_summary(self) -> dict[str, object]:
        """Project Activity status without reading legacy event/report payloads."""
        return self.field_effect_activity_data(1, include_events=False)

    @local_operation
    def field_effect_activity_data(
        self, limit: int = 50, *, include_events: bool = True
    ) -> dict[str, object]:
        """Project a bounded value-free status and chronological audit ledger."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(include_events) is not bool
        ):
            raise FieldEffectError("invalid_limit")
        try:
            consent_rows = self._connection.execute(
                "SELECT c.consent_id,c.updated_at,c.document,s.generation,"
                "r.resolved_policy_id,r.document,d.document "
                "FROM field_effect_consents c JOIN metadata_policy_state s "
                "ON s.singleton=1 JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id "
                "LEFT JOIN metadata_policy_resolutions r "
                "ON r.generation=s.generation AND r.library_name="
                "json_extract(c.document,'$.consent.library') "
                "WHERE c.current=1 ORDER BY "
                "json_extract(c.document,'$.consent.connection'),"
                "json_extract(c.document,'$.consent.library'),"
                "json_extract(c.document,'$.consent.destination')"
            ).fetchall()
            consented_destinations: list[tuple[str, str, str]] = []
            profile: MetadataPolicyProfile | None = None
            for (
                consent_id,
                updated_at,
                document,
                generation,
                resolved_id,
                resolved_document,
                draft_document,
            ) in consent_rows:
                raw = json.loads(document)
                if (
                    type(raw) is not dict
                    or set(raw)
                    != {"consent", "current", "schema_version", "updated_at"}
                    or raw.get("current") is not True
                    or raw.get("schema_version") != "field-effect-consent-state-v1"
                    or raw.get("updated_at") != updated_at
                    or canonical_effect_json(raw) != document
                ):
                    raise FieldEffectError("persistence")
                consent = EffectConsent.from_data(raw["consent"])
                if consent.consent_id != consent_id:
                    raise FieldEffectError("persistence")
                if resolved_id is not None:
                    resolved = ResolvedMetadataPolicy.from_data(
                        json.loads(resolved_document)
                    )
                    if (
                        resolved.library != consent.library
                        or resolved.resolved_id != resolved_id
                        or resolved.canonical_json != resolved_document
                    ):
                        raise FieldEffectError("persistence")
                    current_resolved_id = resolved.resolved_id
                elif generation == 1:
                    if profile is None:
                        profile = MetadataPolicyProfile.from_data(
                            json.loads(draft_document)
                        )
                    current_resolved_id = resolve_policy(
                        profile, consent.library
                    ).resolved_id
                else:
                    continue
                if (
                    consent.envelope_identity
                    == self._field_effect_consent_envelope_identity(
                        consent.library, current_resolved_id
                    )
                ):
                    consented_destinations.append(
                        (consent.connection, consent.library, consent.destination)
                    )
            counts = {
                state: count
                for state, count in self._connection.execute(
                    "SELECT state,count(*) FROM field_effects GROUP BY state"
                ).fetchall()
            }
            cleanup = self._connection.execute(
                "SELECT count(*),"
                "coalesce(sum(CASE WHEN attempt>=3 THEN 1 ELSE 0 END),0) "
                "FROM field_effect_cleanups WHERE state='pending'"
            ).fetchone()
            event_rows = (
                []
                if not include_events
                else self._connection.execute(
                    "SELECT effect.connection_name,effect.destination,json_extract(event.document,'$.failure'),"
                    "effect.library_name,event.occurred_at,"
                    "json_extract(event.document,'$.origin'),lineage.report_id,event.kind,effect.work_id "
                    "FROM field_effect_events event JOIN field_effects effect "
                    "ON effect.plan_id=event.plan_id LEFT JOIN current_metadata_report_lineages lineage "
                    "ON lineage.connection_name=effect.connection_name AND lineage.library_name=effect.library_name AND lineage.item_id=effect.work_id "
                    "ORDER BY event.occurred_at DESC,event.event_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
            report_ids = sorted({row[6] for row in event_rows if row[6] is not None})
            report_documents = (
                {}
                if not report_ids
                else dict(
                    self._connection.execute(
                        "SELECT report_id,document FROM metadata_reports WHERE report_id IN ("
                        + ",".join("?" for _ in report_ids)
                        + ")",
                        report_ids,
                    ).fetchall()
                )
            )
            events = [
                {
                    "connection": connection,
                    "destination": destination,
                    "failure": failure,
                    "library": library,
                    "occurred_at": occurred_at,
                    "origin": origin,
                    "report_id": report_id,
                    "state": state,
                    "work_id": work_id,
                }
                for connection, destination, failure, library, occurred_at, origin, report_id, state, work_id in event_rows
            ]
            suspensions = [
                {
                    "connection": connection,
                    "destination": destination or None,
                    "reason": reason,
                    "scope": scope,
                    "updated_at": updated_at,
                }
                for scope, connection, destination, reason, updated_at in self._connection.execute(
                    "SELECT scope,connection_name,destination,reason,updated_at "
                    "FROM field_effect_suspensions ORDER BY updated_at DESC,scope,"
                    "connection_name,destination LIMIT 64"
                ).fetchall()
            ]
            return {
                "consented_libraries": [
                    {"connection": connection, "library": library}
                    for connection, library in sorted(
                        {
                            (connection, library)
                            for connection, library, _ in consented_destinations
                        }
                    )
                ],
                "consented_destinations": [
                    {
                        "connection": connection,
                        "library": library,
                        "destination": destination,
                    }
                    for connection, library, destination in consented_destinations
                ],
                "counts": counts,
                "cleanup": {"pending": cleanup[0], "exhausted": cleanup[1]},
                "events": events,
                "report_documents": report_documents,
                "paused": self.field_effect_paused(),
                "suspensions": suspensions,
            }
        except (
            json.JSONDecodeError,
            MetadataPolicyError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            raise FieldEffectError("persistence") from error

    def _project_activity_rows(
        self,
        rows: list[tuple[Any, ...]],
        *,
        document_limit: int,
        scalar_limit: int,
        searched: bool,
    ) -> tuple[list[dict[str, object]], dict[str, str], dict[str, MetadataReport]]:
        """Validate and project one bounded Activity dependency batch.

        This substantial single-caller extraction is intentional: it gives the
        cursor search a directly testable memory-safety and type-validation
        boundary, so immutable report, policy, and authority maps cannot grow
        beyond the batch that names them.
        """
        # Search hands this boundary 64 rows; the established no-search page
        # still has a 100-row public maximum and invokes it once.
        if len(rows) > 100:
            raise FieldEffectError("persistence")
        # A grouped field-effect row stands for its complete immutable
        # lifecycle. Admit it only after the existing bounded canonical
        # validator accepts every transition, rather than trusting the latest
        # row's JSON shape while an earlier transition is inconsistent.
        try:
            for plan_id in {row[15] for row in rows if row[13] == "field_effect"}:
                if type(plan_id) is not str:
                    raise FieldEffectError("persistence")
                self._field_effect_event_documents(plan_id)
        except FieldEffectError as error:
            raise FieldEffectError("persistence") from error
        dependency_limit = 64 if searched else 100
        report_ids = sorted({row[6] for row in rows if row[6] is not None})
        report_rows = (
            []
            if not report_ids
            else _bounded_fetch(
                self._connection.execute(
                    "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id IN ("
                    + ",".join("?" for _ in report_ids)
                    + ")",
                    report_ids,
                ),
                dependency_limit,
            )
        )
        report_documents = {}
        try:
            for report_row in report_rows:
                if (
                    type(report_row[8]) is not str
                    or not 2 <= len(report_row[8].encode("utf-8")) <= document_limit
                ):
                    raise MetadataReportError("persisted report is invalid")
                report_documents[report_row[0]] = _validate_report_row(report_row)
        except MetadataReportError as error:
            raise FieldEffectError("persistence") from error
        # Every report joined to the selected page participates in the
        # response contract.  Validate it here (not through a corpus-wide
        # preflight) so malformed durable data fails closed without making
        # a bounded poll proportional to unrelated history.
        if set(report_documents) != set(report_ids):
            raise FieldEffectError("persistence")
        reports: dict[str, MetadataReport] = {}
        try:
            for report_id, report_document in report_documents.items():
                report = MetadataReport.from_json(report_document)
                if report.report_id != report_id:
                    raise FieldEffectError("persistence")
                reports[report_id] = report
        except ReportValueError as error:
            raise FieldEffectError("persistence") from error
        # A Policy row's exact From/To comes from the two immutable
        # profiles its own transition names, not from whatever policy is
        # active now. At most two per row on a closed page, each capped at
        # 16KB by the schema, so this stays a bounded page read.
        policy_ids = sorted(
            {value for row in rows for value in row[18:20] if value is not None}
        )
        policy_profiles: dict[str, MetadataPolicyProfile] = {}
        if policy_ids:
            try:
                for policy_id, policy_document in _bounded_fetch(
                    self._connection.execute(
                        "SELECT policy_id,document FROM metadata_policy_drafts WHERE policy_id IN ("
                        + ",".join("?" for _ in policy_ids)
                        + ")",
                        policy_ids,
                    ),
                    dependency_limit * 2,
                ):
                    profile = MetadataPolicyProfile.from_data(
                        json.loads(policy_document)
                    )
                    if profile.policy_id != policy_id:
                        raise FieldEffectError("persistence")
                    policy_profiles[policy_id] = profile
            except MetadataPolicyError as error:
                raise FieldEffectError("persistence") from error
            if set(policy_profiles) != set(policy_ids):
                raise FieldEffectError("persistence")
        # An automatic Activity row needs the value the policy selected,
        # not only the policy's name. Reconstruct it from the immutable
        # authority documents the decision names. Several decisions can
        # share one review aggregate, so retrieve and parse each aggregate
        # once rather than duplicating its bounded private documents for
        # every field on the page.
        automatic_decisions: dict[str, AutomaticDecision | CoverAutomaticDecision] = {}
        try:
            for row in rows:
                if row[13] != "automatic_decision":
                    continue
                document = row[9]
                if (
                    row[20] != 1
                    or type(document) is not str
                    or not 2 <= len(document.encode("utf-8")) <= document_limit
                ):
                    raise FieldEffectError("persistence")
                automatic_decision = parse_automatic_authority(document)
                if (
                    automatic_decision.decision_id != row[17]
                    or automatic_decision.decision_id in automatic_decisions
                ):
                    raise FieldEffectError("persistence")
                automatic_decisions[automatic_decision.decision_id] = automatic_decision
        except (AutomaticDecisionError, TypeError, ValueError) as error:
            raise FieldEffectError("persistence") from error
        automatic_identities = sorted(
            {
                (
                    decision.review_case_id,
                    decision.report_id,
                    decision.proposal_set_id,
                    decision.resolved_policy_id,
                )
                for decision in automatic_decisions.values()
                if type(decision) is AutomaticDecision
            }
        )
        automatic_authority: dict[
            tuple[str, str, str, str],
            tuple[ReviewCase, ProposalSet, ShadowOutcomeSet],
        ] = {}
        if automatic_identities:
            authority_rows = _bounded_fetch(
                self._connection.execute(
                    "WITH requested(review_case_id,report_id,proposal_set_id,resolved_policy_id) AS (VALUES "
                    + ",".join("(?,?,?,?)" for _ in automatic_identities)
                    + ") SELECT requested.review_case_id,requested.report_id,"
                    "requested.proposal_set_id,requested.resolved_policy_id,"
                    "review.document,proposal.document,outcome.document "
                    "FROM requested "
                    "JOIN review_cases review "
                    "ON review.review_case_id=requested.review_case_id "
                    "AND review.report_id=requested.report_id "
                    "AND review.proposal_set_id=requested.proposal_set_id "
                    "JOIN proposal_sets proposal "
                    "ON proposal.proposal_set_id=requested.proposal_set_id "
                    "AND proposal.report_id=requested.report_id "
                    "JOIN shadow_decision_outcomes outcome "
                    "ON outcome.review_case_id=requested.review_case_id "
                    "AND outcome.report_id=requested.report_id "
                    "AND outcome.proposal_set_id=requested.proposal_set_id "
                    "AND outcome.resolved_policy_id=requested.resolved_policy_id",
                    tuple(
                        value for identity in automatic_identities for value in identity
                    ),
                ),
                dependency_limit,
            )
            for (
                review_case_id,
                authority_report_id,
                proposal_set_id,
                resolved_policy_id,
                case_document,
                proposal_document,
                outcome_document,
            ) in authority_rows:
                identity = (
                    review_case_id,
                    authority_report_id,
                    proposal_set_id,
                    resolved_policy_id,
                )
                if identity in automatic_authority or any(
                    type(value) is not str
                    or not 2 <= len(value.encode("utf-8")) <= document_limit
                    for value in (
                        case_document,
                        proposal_document,
                        outcome_document,
                    )
                ):
                    raise FieldEffectError("persistence")
                try:
                    automatic_authority[identity] = (
                        ReviewCase.from_json(case_document),
                        ProposalSet.from_json(proposal_document),
                        ShadowOutcomeSet.from_json(outcome_document),
                    )
                except (
                    ReportValueError,
                    ProposalValueError,
                    RichMetadataPreviewError,
                    ShadowOutcomeError,
                    TypeError,
                    ValueError,
                ) as error:
                    raise FieldEffectError("persistence") from error
            # An absent or no-longer-resolvable authority belongs to its
            # one retained automatic event.  Keep the validated event in
            # the page and let the per-event projection below emit the
            # bounded degraded row; do not turn it into a whole-ledger
            # storage failure (#786).
        authored_ids = sorted({row[17] for row in rows if type(row[17]) is str})
        authored_records: dict[
            str, tuple[AuthoredDescriptionDecision, DescriptionAdviceCandidate]
        ] = {}
        if authored_ids:
            for document, candidate_document in self._connection.execute(
                "SELECT d.document,c.document FROM description_decisions d JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id WHERE d.decision_id IN ("
                + ",".join("?" for _ in authored_ids)
                + ")",
                authored_ids,
            ):
                authored = AuthoredDescriptionDecision.from_json(document)
                candidate = DescriptionAdviceCandidate.from_json(candidate_document)
                if candidate.candidate_id != authored.candidate_id:
                    raise FieldEffectError("persistence")
                authored_records[authored.decision_id] = authored, candidate
        projected_events: list[dict[str, object]] = []
        decision: (
            HumanDecision
            | AutomaticDecision
            | CoverAutomaticDecision
            | AuthoredDescriptionDecision
        )
        for (
            event_id,
            connection,
            destination,
            library,
            occurred_at,
            origin,
            report_id,
            state,
            work_id,
            document,
            projected_type,
            title,
            detail,
            source_kind,
            source_document,
            source_parent_id,
            source_sequence,
            source_identity,
            policy_from,
            policy_to,
            source_valid,
        ) in rows:
            if (
                source_valid != 1
                or type(document) is not str
                or not 2 <= len(document.encode("utf-8")) <= document_limit
            ):
                raise FieldEffectError("persistence")
            raw = json.loads(document)
            if type(raw) is not dict:
                raise FieldEffectError("persistence")

            def scalar(
                value: object, maximum: int = 512, *, nullable: bool = False
            ) -> str | None:
                if value is None and nullable:
                    return None
                if type(value) is not str or not value or len(value) > maximum:
                    raise FieldEffectError("persistence")
                return value

            event_id = scalar(event_id, 128)
            scalar(connection, nullable=True)
            destination = scalar(destination)
            library = scalar(library)
            origin = scalar(origin)
            state = scalar(state)
            work_id = scalar(work_id)
            projected_type = scalar(projected_type, 32)
            if projected_type not in {
                "review",
                "policy",
                "metadata",
                "safety",
                "unknown",
            }:
                raise FieldEffectError("persistence")
            source_kind = scalar(source_kind)
            if source_kind not in {
                "field_effect",
                "field_effect_no_change",
                "human_decision",
                "description_decision",
                "description_preparation",
                "automatic_decision",
                "isbn_refusal",
                "cover_safeguard",
                "policy_transition",
            }:
                raise FieldEffectError("persistence")
            change_id = source_parent_id if source_kind == "field_effect" else event_id
            if type(change_id) is not str or not change_id or len(change_id) > 128:
                raise FieldEffectError("persistence")
            if source_document is not None and (
                type(source_document) is not str
                or not 2 <= len(source_document.encode("utf-8")) <= document_limit
            ):
                raise FieldEffectError("persistence")
            try:
                if source_kind == "field_effect":
                    event_keys = {
                        "attempt",
                        "decision_id",
                        "desired_value",
                        "destination",
                        "event_id",
                        "failure",
                        "kind",
                        "occurred_at",
                        "origin",
                        "plan_id",
                        "pre_value",
                        "schema_version",
                        "sequence",
                        "state",
                    }
                    paired_event = (
                        raw.get("schema_version")
                        == "field-effect-title-subtitle-event-v1"
                    )
                    if paired_event:
                        event_keys.add("subtitle_companion")
                        operation_row = self._connection.execute(
                            "SELECT document FROM field_effects WHERE plan_id=?",
                            (source_parent_id,),
                        ).fetchone()
                        operation = (
                            None
                            if operation_row is None
                            else FieldEffectOperation.from_json(operation_row[0])
                        )
                        if (
                            operation is None
                            or operation.plan.subtitle_companion is None
                            or raw.get("subtitle_companion")
                            != operation.plan.subtitle_companion.to_data()
                        ):
                            raise FieldEffectError("persistence")
                    if (
                        set(raw) != event_keys
                        or raw.get("schema_version")
                        != (
                            "field-effect-title-subtitle-event-v1"
                            if paired_event
                            else "field-effect-event-v1"
                        )
                        or raw.get("event_id") != event_id
                        or raw.get("plan_id") != source_parent_id
                        or raw.get("decision_id") != source_identity
                        or raw.get("sequence") != source_sequence
                        or type(raw.get("sequence")) is not int
                        or raw["sequence"] < 1
                        or type(raw.get("attempt")) is not int
                        or not 0 <= raw["attempt"] <= 3
                        or raw.get("failure") not in FIELD_EFFECT_FAILURES
                        or raw.get("destination") != destination
                        or raw.get("kind") != state
                        or raw.get("state") != state
                        or raw.get("occurred_at") != occurred_at
                        or raw.get("origin") != origin
                        or canonical_effect_json(raw) != document
                        or event_id
                        != "effect_event_"
                        + hashlib.sha256(
                            canonical_effect_json(
                                {
                                    key: value
                                    for key, value in raw.items()
                                    if key != "event_id"
                                }
                            ).encode()
                        ).hexdigest()
                    ):
                        raise FieldEffectError("persistence")
                elif source_kind == "field_effect_no_change":
                    if raw or document != "{}" or source_document is not None:
                        raise FieldEffectError("persistence")
                    record = FieldEffectNoChange.create(
                        decision_id=source_identity,
                        review_case_id=source_parent_id,
                        # scalar() without nullable=True raises rather than
                        # returning None, so these are always str here.
                        report_id=report_id,
                        connection=connection,
                        library=cast(str, library),
                        work_id=cast(str, work_id),
                        destination=cast(str, destination),
                        target_fingerprint=self._connection.execute(
                            "SELECT target_fingerprint FROM field_effect_no_changes WHERE decision_id=?",
                            (source_identity,),
                        ).fetchone()[0],
                        target_version=self._connection.execute(
                            "SELECT target_version FROM field_effect_no_changes WHERE decision_id=?",
                            (source_identity,),
                        ).fetchone()[0],
                        occurred_at=occurred_at,
                        origin=cast("Literal['human', 'automatic_policy']", origin),
                    )
                    if (
                        state != "no_change_required"
                        or origin != record.origin
                        or event_id != record.event_id
                    ):
                        raise FieldEffectError("persistence")
                elif source_kind == "description_preparation":
                    if (
                        set(raw)
                        != {
                            "attempt_id",
                            "engine",
                            "model",
                            "state",
                            "failure",
                            "updated_at",
                        }
                        or raw["attempt_id"] != event_id
                        or raw["state"] != state
                        or raw["updated_at"] != occurred_at
                        or raw["engine"] not in {"codex", "openrouter"}
                        or state
                        not in {
                            "requested",
                            "running",
                            "succeeded",
                            "failed",
                            "interrupted",
                        }
                        or raw["failure"]
                        not in {None, "unavailable", "invalid_output", "interrupted"}
                        or destination != "description"
                        or origin != "adviser"
                    ):
                        raise FieldEffectError("persistence")
                    scalar(raw["model"], 256)
                    if source_document is not None:
                        candidate = DescriptionAdviceCandidate.from_json(
                            source_document
                        )
                        if (
                            candidate.attempt_id != event_id
                            or candidate.engine != raw["engine"]
                            or candidate.model != raw["model"]
                        ):
                            raise FieldEffectError("persistence")
                elif source_kind == "description_decision":
                    authored_record = authored_records.get(source_identity)
                    if authored_record is None:
                        raise FieldEffectError("persistence")
                    authored, candidate = authored_record
                    if (
                        authored.to_json() != document
                        or authored.decision_id != event_id
                        or authored.review_case_id != source_parent_id
                        or authored.report_id != report_id
                        or authored.origin != origin
                        or authored.occurred_at != occurred_at
                        or destination != "description"
                        or state != "recorded"
                        or candidate.to_json() != source_document
                    ):
                        raise FieldEffectError("persistence")
                elif source_kind == "human_decision":
                    if source_document is None:
                        raise FieldEffectError("persistence")
                    decision = HumanDecision.from_json(source_document)
                    event_keys = {
                        "actor",
                        "decision_id",
                        "destination",
                        "event_id",
                        "kind",
                        "occurred_at",
                        "outcome",
                        "review_case_id",
                        "schema_version",
                    }
                    if (
                        set(raw) != event_keys
                        or raw.get("schema_version") != "human-decision-event-v1"
                        or raw.get("kind") != "human_decision_recorded"
                        or raw.get("event_id") != event_id
                        or raw.get("decision_id") != decision.decision_id
                        or raw.get("decision_id") != source_identity
                        or raw.get("destination") != destination
                        or raw.get("actor") != decision.actor
                        or raw.get("outcome") != state
                        or raw.get("outcome") != decision.outcome
                        or raw.get("occurred_at") != occurred_at
                        or raw.get("occurred_at") != decision.occurred_at
                        or raw.get("review_case_id") != decision.review_case_id
                        or raw.get("review_case_id") != source_parent_id
                        or decision.destination != destination
                        or decision.to_json() != source_document
                        or _canonical_json(raw) != document
                        or event_id
                        != "human_decision_event_"
                        + hashlib.sha256(
                            ("human-decision-event-v1:" + decision.decision_id).encode(
                                "utf-8"
                            )
                        ).hexdigest()
                    ):
                        raise FieldEffectError("persistence")
                elif source_kind == "automatic_decision":
                    projected_automatic = automatic_decisions.get(
                        cast(str, source_identity)
                    )
                    if (
                        projected_automatic is None
                        or projected_automatic.decision_id != event_id
                        or projected_automatic.decision_id != source_identity
                        or projected_automatic.destination != destination
                        or projected_automatic.occurred_at != occurred_at
                        or origin != "automatic_policy"
                        or projected_automatic.review_case_id != source_parent_id
                        or projected_automatic.report_id != report_id
                        or projected_automatic.to_json() != document
                    ):
                        raise FieldEffectError("persistence")
                elif source_kind == "isbn_refusal":
                    outcome_row = self._connection.execute(
                        "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
                        "resolved_policy_id,source_decision_set_id,evaluated_at,document "
                        "FROM shadow_decision_outcomes WHERE outcome_set_id=?",
                        (source_identity,),
                    ).fetchone()
                    if outcome_row is None:
                        raise FieldEffectError("persistence")
                    checked_document, checked_at = self._validate_shadow_outcome_row(
                        outcome_row
                    )
                    outcome = ShadowOutcomeSet.from_json(checked_document)
                    isbn = next(
                        (
                            field
                            for field in outcome.outcomes
                            if field.destination == "isbn"
                        ),
                        None,
                    )
                    if (
                        checked_document != document
                        or checked_at != occurred_at
                        or event_id != outcome.outcome_set_id + ":isbn"
                        or outcome.review_case_id != source_parent_id
                        or outcome.report_id != report_id
                        or destination != "isbn"
                        or state != "unchanged"
                        or origin != "automatic_policy"
                        or projected_type != "metadata"
                        or isbn is None
                        or isbn.candidate_id is not None
                        or isbn.reason_code not in ISBN_REFUSAL_REASONS
                    ):
                        raise FieldEffectError("persistence")
                elif source_kind == "cover_safeguard":
                    if source_document is None:
                        raise FieldEffectError("persistence")
                    consideration = json.loads(source_document)
                    if type(consideration) is not dict:
                        raise FieldEffectError("persistence")
                    reviewer_status = consideration.get("reviewer_status")
                    event_keys = {
                        "consideration_id",
                        "decision_id",
                        "kind",
                        "occurred_at",
                        "review_case_id",
                        "schema_version",
                    }
                    if (
                        set(raw) != event_keys
                        or raw.get("schema_version") != "cover-consideration-event-v1"
                        or raw.get("consideration_id") != source_identity
                        or raw.get("kind")
                        not in {
                            "automatic_prepared",
                            "owner_retry_prepared",
                            "superseded",
                            "selected",
                            "kept_incumbent",
                            "no_action",
                            "stale",
                        }
                        or (
                            raw.get("kind")
                            in {"automatic_prepared", "owner_retry_prepared"}
                            and reviewer_status
                            not in {"completed", "unavailable", "failed"}
                        )
                        or state
                        != (
                            reviewer_status
                            if raw.get("kind")
                            in {"automatic_prepared", "owner_retry_prepared"}
                            else raw.get("kind")
                        )
                        or (
                            raw.get("kind") == "automatic_prepared"
                            and (origin, projected_type)
                            != ("automatic_pipeline", "metadata")
                        )
                        or (
                            raw.get("kind") == "owner_retry_prepared"
                            and (origin, projected_type) != ("owner_retry", "review")
                        )
                        or (
                            raw.get("kind")
                            in {"selected", "kept_incumbent", "no_action"}
                            and (origin, projected_type) != ("human", "review")
                        )
                        or (
                            raw.get("kind") in {"stale", "superseded"}
                            and (origin, projected_type) != ("unknown", "safety")
                        )
                        or raw.get("occurred_at") != occurred_at
                        or raw.get("review_case_id") != source_parent_id
                        or _canonical_json(raw) != document
                        or event_id
                        != "cover_consideration_event_"
                        + hashlib.sha256(document.encode("utf-8")).hexdigest()
                    ):
                        raise FieldEffectError("persistence")
                elif raw or document != "{}" or source_document is not None:
                    raise FieldEffectError("persistence")
            except (
                AutomaticDecisionError,
                HumanDecisionError,
                TypeError,
                ValueError,
            ) as error:
                raise FieldEffectError("persistence") from error
            event_title = scalar(title, nullable=True)
            failure = scalar(raw.get("failure"), nullable=True)
            if report_id is not None:
                scalar(report_id)
            if source_kind == "field_effect" and report_id is None:
                # An older field-effect event may not have a persisted
                # decision-to-report lineage. Keep that canonical event
                # value-free rather than substituting a later current report.
                connection = None
            # Every fact below leaves this loop from the document that
            # was just checked against its own identity above, so an
            # Activity row is a function of one immutable event and
            # nothing else -- a later catalogue change cannot move it.
            # The owner-facing words are composed one layer out, where the
            # pinned report is already parsed; ``record`` is the closed
            # private carrier the application consumes and removes, the
            # same way ``connection`` and ``report_id`` are consumed there.
            durable: dict[str, object] = {"source_kind": source_kind}
            if source_kind == "description_preparation":
                durable.update(advised=True, engine=raw["engine"], model=raw["model"])
            if source_identity in authored_records:
                authored, candidate = authored_records[source_identity]
                durable["advised"] = True
                durable["engine"] = candidate.engine
                durable["model"] = candidate.model
                durable["target_fingerprint"] = authored.target_fingerprint
                if source_kind == "description_decision":
                    durable["selected_value"] = {
                        "kind": "text",
                        "value": candidate.output.description,
                    }
            if source_kind == "field_effect":
                durable["pre_value"] = raw["pre_value"]
                durable["desired_value"] = raw["desired_value"]
                if "subtitle_companion" in raw:
                    durable["subtitle_companion"] = raw["subtitle_companion"]
            elif source_kind == "human_decision":
                if type(decision) is not HumanDecision:
                    raise FieldEffectError("persistence")
                durable["selected_value"] = decision.selected_value
                durable["target_fingerprint"] = decision.target_fingerprint
            elif source_kind == "isbn_refusal":
                if isbn is None:
                    raise FieldEffectError("persistence")
                durable["reason_code"] = isbn.reason_code
                durable["target_fingerprint"] = outcome.target_fingerprint
            elif source_kind == "automatic_decision":
                if projected_automatic is None:
                    raise FieldEffectError("persistence")
                automatic_report_document = report_documents.get(
                    projected_automatic.report_id
                )
                if automatic_report_document is None:
                    raise FieldEffectError("persistence")
                try:
                    if type(projected_automatic) is CoverAutomaticDecision:
                        cover_rows = self._connection.execute(
                            "SELECT review.document,proposal.document,consideration.document "
                            "FROM review_cases review JOIN proposal_sets proposal "
                            "ON proposal.proposal_set_id=review.proposal_set_id "
                            "JOIN current_cover_considerations current ON current.review_case_id=review.review_case_id "
                            "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                            "WHERE review.review_case_id=? AND consideration.consideration_id=?",
                            (
                                projected_automatic.review_case_id,
                                projected_automatic.consideration_id,
                            ),
                        ).fetchone()
                        if cover_rows is None:
                            raise FieldEffectError("authority_binding")
                        selected_value = _cover_automatic_decision_value(
                            self._connection,
                            projected_automatic,
                            ReviewCase.from_json(cover_rows[0]),
                            MetadataReport.from_json(automatic_report_document),
                            ProposalSet.from_json(cover_rows[1]),
                            cover_rows[2],
                        )
                    else:
                        authority = automatic_authority.get(
                            (
                                projected_automatic.review_case_id,
                                projected_automatic.report_id,
                                projected_automatic.proposal_set_id,
                                projected_automatic.resolved_policy_id,
                            )
                        )
                        if authority is None:
                            raise FieldEffectError("authority_binding")
                        case, proposal, outcome = authority
                        report = MetadataReport.from_json(automatic_report_document)
                        if projected_automatic.outcome in {"candidate", "keep_current"}:
                            selected_value = _automatic_decision_value(
                                cast(AutomaticDecision, projected_automatic),
                                case,
                                report,
                                proposal,
                                outcome,
                            )
                        else:
                            _validate_automatic_decision_authority(
                                cast(AutomaticDecision, projected_automatic),
                                case,
                                report,
                                proposal,
                                outcome,
                            )
                            selected_value = None
                except FieldEffectError as error:
                    # A retained decision can outlive the exact authority
                    # aggregate it names. That is an event-local inability
                    # to disclose its selected value, not a reason to hide
                    # unrelated audit truth. Only the closed authority
                    # binding refusal degrades; corrupt persistence still
                    # reaches the outer fail-closed boundary.
                    if error.code != "authority_binding":
                        raise
                    durable["source_kind"] = "degraded"
                    durable["degraded_reason"] = AUTOMATIC_AUTHORITY_UNAVAILABLE
                    projected_events.append(
                        {
                            "change_id": change_id,
                            "connection": connection,
                            "destination": destination,
                            "event_id": event_id,
                            "event_type": projected_type,
                            "failure": None,
                            "library": library,
                            "occurred_at": occurred_at,
                            "origin": origin,
                            "report_id": report_id,
                            "state": state,
                            "work_id": work_id,
                            **({"work_label": event_title} if event_title else {}),
                            "record": durable,
                        }
                    )
                    continue
                except (
                    ReportValueError,
                    ProposalValueError,
                    RichMetadataPreviewError,
                    ShadowOutcomeError,
                    TypeError,
                    ValueError,
                ) as error:
                    raise FieldEffectError("persistence") from error
                durable["policy_category"] = projected_automatic.policy_category
                durable["policy_option"] = projected_automatic.policy_option
                durable["policy_generation"] = projected_automatic.policy_generation
                durable["outcome"] = projected_automatic.outcome
                durable["selected_value"] = selected_value
                durable["target_fingerprint"] = projected_automatic.target_fingerprint
            elif source_kind == "field_effect_no_change":
                durable["target_fingerprint"] = self._connection.execute(
                    "SELECT target_fingerprint FROM field_effect_no_changes WHERE decision_id=?",
                    (source_identity,),
                ).fetchone()[0]
            elif source_kind == "cover_safeguard":
                durable["kind"] = raw["kind"]
            elif source_kind == "policy_transition":
                durable["generation"] = scalar(detail)
                durable["policy_changes"] = _policy_changes(
                    policy_profiles, policy_from, policy_to
                )
            if failure is not None:
                durable["failure"] = failure
            if type(occurred_at) is not int or not 0 <= occurred_at <= 253_402_300_799:
                raise FieldEffectError("persistence")
            projected_events.append(
                {
                    "change_id": change_id,
                    **(
                        {"transition_id": change_id}
                        if source_kind == "field_effect"
                        else {}
                    ),
                    "connection": connection,
                    "destination": destination,
                    "event_id": event_id,
                    "event_type": projected_type,
                    "failure": failure,
                    "library": library,
                    "occurred_at": occurred_at,
                    "origin": origin,
                    "report_id": report_id,
                    "state": state,
                    "work_id": work_id,
                    **({"work_label": event_title} if event_title else {}),
                    "record": durable,
                }
            )
        return projected_events, report_documents, reports

    @local_operation
    def _activity_projection_revision(self) -> tuple[int, int]:
        """Return the complete local and cross-connection SQLite revision."""
        try:
            row = self._connection.execute("PRAGMA data_version").fetchone()
            if row is None or len(row) != 1 or type(row[0]) is not int or row[0] < 0:
                raise FieldEffectError("persistence")
            # The connection-local change count and cross-connection data
            # version invalidate every SQL mutation, including in-place
            # document changes that preserve row counts and identifiers.
            return row[0], self._connection.total_changes
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    @local_operation
    def _activity_database_identity(self) -> _ActivityDatabaseIdentity:
        """Return a fresh kernel identity for the database bytes."""
        try:
            information = (
                os.fstat(self._binding.descriptor)
                if self._binding is not None
                else os.stat(self._activity_database_path, follow_symlinks=False)
                if self._activity_database_path is not None
                else None
            )
        except OSError as error:
            raise FieldEffectError("persistence") from error
        if information is None or not stat.S_ISREG(information.st_mode):
            raise FieldEffectError("persistence")
        return (
            information.st_dev,
            information.st_ino,
            information.st_size,
            information.st_mtime_ns,
        )

    def configure_activity_page_reader(self) -> None:
        """Bound only this independently owned Activity reader's busy wait."""

        try:
            self._connection.execute(
                f"PRAGMA busy_timeout={_ACTIVITY_PAGE_BUSY_TIMEOUT_MILLISECONDS}"
            )
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error
        self._activity_page_busy_retry_seconds = _ACTIVITY_PAGE_BUSY_RETRY_SECONDS

    @staticmethod
    def _retryable_activity_page_busy(error: FieldEffectError) -> bool:
        cause = error.__cause__
        return (
            type(cause) is sqlite3.OperationalError
            and getattr(cause, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
        )

    def _retry_activity_busy(self, call: Callable[[], _ActivityRead]) -> _ActivityRead:
        deadline = time.monotonic() + self._activity_page_busy_retry_seconds
        while True:
            try:
                return call()
            except FieldEffectError as error:
                if (
                    self._activity_page_busy_retry_seconds == 0
                    or not self._retryable_activity_page_busy(error)
                    or time.monotonic()
                    + _ACTIVITY_PAGE_BUSY_TIMEOUT_MILLISECONDS / 1_000
                    > deadline
                ):
                    raise

    def field_effect_activity_snapshot(
        self, call: Callable[[], dict[str, object]]
    ) -> dict[str, object]:
        """Run one Activity projection in a bounded exact read snapshot."""

        with self._activity_page_cache_lock:
            transaction = self._transaction(
                "field_effect_activity_snapshot", deferred=True
            )

            def begin() -> None:
                try:
                    transaction.begin()
                    self._activity_projection_revision()
                except sqlite3.Error as error:
                    transaction.rollback()
                    raise FieldEffectError("persistence") from error
                except Exception:
                    transaction.rollback()
                    raise

            self._retry_activity_busy(begin)
            try:
                return call()
            finally:
                transaction.rollback()

    def field_effect_activity_page(
        self,
        *,
        search: str | None,
        event_type: str,
        occurred_after: int,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        """Return a current bounded page, retrying only this reader's busy lock."""
        return self._retry_activity_busy(
            lambda: self._field_effect_activity_page_once(
                search=search,
                event_type=event_type,
                occurred_after=occurred_after,
                page=page,
                page_size=page_size,
            )
        )

    def _field_effect_activity_page_once(
        self,
        *,
        search: str | None,
        event_type: str,
        occurred_after: int,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        """Return a bounded page, reusing only an exact-current projection."""
        key = (search, event_type, page, page_size)
        with self._activity_page_cache_lock:
            cached = self._activity_page_cache.get(key)
            if cached is not None:
                current = self._activity_projection_revision()
                cached_cutoff, oldest_included = cached[1:3]
                if (
                    cached[0] == current
                    and cached[5] != self._activity_database_identity()
                ):
                    # A normal commit can land between the revision and file
                    # samples. Recheck only this changed-file path: a newer SQL
                    # revision is a cache miss, while unchanged counters prove
                    # the bytes changed outside SQLite and must fail closed.
                    refreshed = self._activity_projection_revision()
                    if refreshed == current:
                        raise FieldEffectError("persistence")
                    current = refreshed
                if (
                    cached[0] == current
                    and occurred_after >= cached_cutoff
                    and (oldest_included is None or occurred_after <= oldest_included)
                ):
                    return deepcopy(cached[4])
            result, revision, oldest_included, database_identity = (
                self._field_effect_activity_page_uncached(
                    search=search,
                    event_type=event_type,
                    occurred_after=occurred_after,
                    page=page,
                    page_size=page_size,
                )
            )
            report_documents = cast("dict[str, str]", result["report_documents"])
            retained_bytes = sum(
                len(document.encode("utf-8")) for document in report_documents.values()
            )
            replaced = self._activity_page_cache.pop(key, None)
            if replaced is not None:
                self._activity_page_cache_bytes -= replaced[3]
            if retained_bytes <= _ACTIVITY_PAGE_CACHE_MAX_BYTES:
                while self._activity_page_cache and (
                    len(self._activity_page_cache) >= 2
                    or self._activity_page_cache_bytes + retained_bytes
                    > _ACTIVITY_PAGE_CACHE_MAX_BYTES
                ):
                    evicted = self._activity_page_cache.pop(
                        next(iter(self._activity_page_cache))
                    )
                    self._activity_page_cache_bytes -= evicted[3]
                self._activity_page_cache[key] = (
                    revision,
                    occurred_after,
                    oldest_included,
                    retained_bytes,
                    deepcopy(result),
                    database_identity,
                )
                self._activity_page_cache_bytes += retained_bytes
            return result

    @local_operation
    def _field_effect_activity_page_uncached(
        self,
        *,
        search: str | None,
        event_type: str,
        occurred_after: int,
        page: int,
        page_size: int,
    ) -> tuple[
        dict[str, object],
        tuple[int, int],
        int | None,
        _ActivityDatabaseIdentity,
    ]:
        """Return one exact, bounded page from the durable owner activity ledgers."""
        if (
            (
                search is not None
                and (type(search) is not str or not search or len(search) > 128)
            )
            or event_type
            not in {"all", "review", "policy", "metadata", "safety", "unknown"}
            or type(occurred_after) is not int
            or occurred_after < 0
            or type(page) is not int
            or not 1 <= page <= 1_000_000
            or page_size not in {25, 50, 100}
        ):
            raise FieldEffectError("invalid_activity_query")
        document_limit = 12_582_912
        scalar_limit = 512
        # The selected page and its joined report documents are validated
        # below.  Do not rescan every historical document on each poll: that
        # makes a bounded page read proportional to the whole durable corpus.
        transaction = self._transaction("field_effect_activity_page", deferred=True)
        try:
            transaction.begin()
            # Establish the SQLite snapshot before sampling the file identity.
            # A normal writer cannot commit while the resulting SHARED lock is
            # held, while an out-of-band byte write still changes the kernel
            # identity and is refused below.
            self._connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
            database_identity = self._activity_database_identity()
        except sqlite3.Error as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error
        except Exception:
            transaction.rollback()
            raise
        # This is deliberately a read-only union over the existing immutable
        # ledgers.  It neither broadens write authority nor invents a second
        # audit store just for presentation.
        # ``events`` is deliberately rebound below from the union SQL text to
        # the projected event list; both shapes are declared here.
        events: str | list[dict[str, object]] = """
            SELECT event.event_id, effect.connection_name, effect.destination,
                   effect.library_name, event.occurred_at,
                   COALESCE(CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.origin') END, 'unknown'),
                   COALESCE(human_review.report_id,automatic_review.report_id,cover_review.report_id,description_review.report_id), event.kind, effect.work_id, event.document,
                   CASE WHEN event.kind IN ('retryable','stale','superseded','abandoned')
                             OR CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.failure') END IS NOT NULL
                        THEN 'safety'
                        WHEN CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.origin') END='human' THEN 'review'
                        WHEN CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.origin') END='automatic_policy' THEN 'metadata'
                        ELSE 'unknown' END,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'field_effect', NULL, event.plan_id, event.sequence, effect.decision_id, NULL, NULL,
                   CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN 1 ELSE 0 END
              FROM field_effect_events event
              JOIN field_effects effect ON effect.plan_id=event.plan_id
         LEFT JOIN human_decisions human ON human.decision_id=effect.decision_id
         LEFT JOIN review_cases human_review ON human_review.review_case_id=human.review_case_id
         LEFT JOIN automatic_decisions automatic ON automatic.decision_id=effect.decision_id
         LEFT JOIN review_cases automatic_review ON automatic_review.review_case_id=automatic.review_case_id
         LEFT JOIN cover_selection_authorities cover_selection ON cover_selection.decision_id=effect.decision_id AND cover_selection.kind='automatic'
         LEFT JOIN cover_considerations cover_consideration ON cover_consideration.consideration_id=cover_selection.consideration_id
         LEFT JOIN review_cases cover_review ON cover_review.review_case_id=cover_consideration.review_case_id
         LEFT JOIN description_decisions description_decision ON description_decision.decision_id=effect.decision_id
         LEFT JOIN review_cases description_review ON description_review.review_case_id=description_decision.review_case_id
         LEFT JOIN metadata_reports report ON report.report_id=COALESCE(human_review.report_id,automatic_review.report_id,cover_review.report_id,description_review.report_id)
            UNION ALL
            SELECT event.event_id, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.connection') END, event.destination,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.library') END, event.occurred_at, 'human', review.report_id,
                   decision.outcome, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.item') END, event.document, 'review', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'human_decision', decision.document, event.review_case_id, NULL, event.decision_id, NULL, NULL,
                   CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN 1 ELSE 0 END
              FROM human_decision_events event
              JOIN human_decisions decision ON decision.decision_id=event.decision_id
              JOIN review_cases review ON review.review_case_id=event.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
            UNION ALL
            SELECT decision.decision_id, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.connection') END, decision.destination,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.library') END, decision.occurred_at, 'automatic_policy', review.report_id,
                   'recorded', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.item') END, decision.document, 'metadata', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'automatic_decision', NULL, decision.review_case_id, NULL, decision.decision_id, NULL, NULL,
                   CASE WHEN typeof(decision.document)='text' AND length(CAST(decision.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(decision.document) THEN 1 ELSE 0 END
              FROM automatic_decisions decision
              JOIN review_cases review ON review.review_case_id=decision.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
            UNION ALL
            SELECT attempt.attempt_id, json_extract(report.document,'$.target.connection'), 'description',
                   json_extract(report.document,'$.target.library'), attempt.updated_at, 'adviser', review.report_id,
                   attempt.state, json_extract(report.document,'$.target.item'),
                   json_object('attempt_id',attempt.attempt_id,'engine',attempt.engine,'model',attempt.model,'state',attempt.state,'failure',attempt.failure,'updated_at',attempt.updated_at),
                   'metadata', json_extract(report.document,'$.target.fields[0].value'), NULL,
                   'description_preparation', candidate.document, review.review_case_id, NULL, attempt.attempt_id, NULL, NULL, 1
              FROM description_advice_attempts attempt
              JOIN review_cases review ON review.review_case_id=json_extract(attempt.binding_document,'$.review_case_id')
              JOIN metadata_reports report ON report.report_id=review.report_id
              LEFT JOIN description_advice_candidates candidate ON candidate.attempt_id=attempt.attempt_id
            UNION ALL
            SELECT decision.decision_id, json_extract(report.document,'$.target.connection'), 'description',
                   json_extract(report.document,'$.target.library'), json_extract(decision.document,'$.occurred_at'), json_extract(decision.document,'$.origin'), review.report_id,
                   'recorded', json_extract(report.document,'$.target.item'), decision.document,
                   CASE json_extract(decision.document,'$.origin') WHEN 'human' THEN 'review' ELSE 'metadata' END,
                   json_extract(report.document,'$.target.fields[0].value'), NULL, 'description_decision', candidate.document, decision.review_case_id, NULL, decision.decision_id, NULL, NULL,
                   CASE WHEN length(CAST(decision.document AS BLOB)) BETWEEN 2 AND 16384 AND json_valid(decision.document) THEN 1 ELSE 0 END
              FROM description_decisions decision
              JOIN description_advice_candidates candidate ON candidate.candidate_id=decision.candidate_id
              JOIN review_cases review ON review.review_case_id=decision.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
            UNION ALL
            SELECT selection.decision_id, selection.connection_name, 'cover',
                   selection.library_name, selection.occurred_at, 'automatic_policy', review.report_id,
                   'recorded', selection.item_id, json_extract(selection.document,'$.decision_document'), 'metadata', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'automatic_decision', NULL, review.review_case_id, NULL, selection.decision_id, NULL, NULL,
                   CASE WHEN typeof(selection.document)='text' AND length(CAST(selection.document AS BLOB)) BETWEEN 2 AND 131072 AND json_valid(selection.document) AND json_valid(json_extract(selection.document,'$.decision_document')) THEN 1 ELSE 0 END
              FROM cover_selection_authorities selection
              JOIN cover_considerations consideration ON consideration.consideration_id=selection.consideration_id
              JOIN review_cases review ON review.review_case_id=consideration.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
             WHERE selection.kind='automatic'
            UNION ALL
            SELECT no_change.event_id, no_change.connection_name, no_change.destination,
                   no_change.library_name, no_change.occurred_at, no_change.origin,
                   no_change.report_id, 'no_change_required', no_change.work_id, '{}',
                   'metadata', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END,
                   NULL, 'field_effect_no_change', NULL, no_change.review_case_id,
                   NULL, no_change.decision_id, NULL, NULL, 1
              FROM field_effect_no_changes no_change
              JOIN metadata_reports report ON report.report_id=no_change.report_id
            UNION ALL
            SELECT event.event_id, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.connection') END, 'cover', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.library') END,
                   event.occurred_at, CASE event.kind WHEN 'automatic_prepared' THEN 'automatic_pipeline' WHEN 'owner_retry_prepared' THEN 'owner_retry' WHEN 'selected' THEN 'human' WHEN 'kept_incumbent' THEN 'human' WHEN 'no_action' THEN 'human' ELSE 'unknown' END, review.report_id,
                   CASE WHEN event.kind IN ('automatic_prepared','owner_retry_prepared') THEN json_extract(consideration.document, '$.reviewer_status') ELSE event.kind END, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.item') END,
                   event.document,
                   CASE WHEN event.kind IN ('stale','superseded') THEN 'safety' WHEN event.kind='automatic_prepared' THEN 'metadata' WHEN event.kind IN ('owner_retry_prepared','selected','kept_incumbent','no_action') THEN 'review' ELSE 'unknown' END,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'cover_safeguard', CASE WHEN json_extract(consideration.document, '$.reviewer_status') IN ('completed','unavailable','failed') THEN json_object('reviewer_status', json_extract(consideration.document, '$.reviewer_status')) ELSE '{}' END, event.review_case_id, NULL, event.consideration_id, NULL, NULL,
                   CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) AND typeof(consideration.document)='text' AND length(CAST(consideration.document AS BLOB)) BETWEEN 2 AND 12582912 AND json_valid(consideration.document) THEN 1 ELSE 0 END
              FROM cover_consideration_events event
              JOIN cover_considerations consideration ON consideration.consideration_id=event.consideration_id
              JOIN review_cases review ON review.review_case_id=event.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
             WHERE event.kind<>'selected' OR NOT EXISTS (
                   SELECT 1 FROM cover_selection_authorities selection
                    WHERE selection.decision_id=event.decision_id
                      AND selection.kind='automatic')
            UNION ALL
            SELECT transition.transition_id, NULL, 'policy', 'Library policy', transition.updated_at,
                   'policy', NULL, transition.state, 'Library policy', '{}', 'policy',
                   'Library policy', 'Policy generation ' || transition.generation, 'policy_transition', NULL, NULL, NULL, NULL, transition.from_policy_id, transition.to_policy_id, 1
              FROM metadata_policy_transitions transition
        """
        where = ["occurred_at>=?"]
        parameters: list[object] = [occurred_after]
        if event_type != "all":
            where.append("event_type=?")
            parameters.append(event_type)
        predicate = " WHERE " + " AND ".join(where)
        events = (
            cast(str, events)
            + """
            UNION ALL
            SELECT outcome.outcome_set_id || ':isbn', report.connection_name, 'isbn',
                   report.library_name, outcome.evaluated_at, 'automatic_policy', outcome.report_id,
                   'unchanged', report.item_id, outcome.document, 'metadata',
                   CASE WHEN json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END,
                   NULL, 'isbn_refusal', NULL, outcome.review_case_id, NULL,
                   outcome.outcome_set_id, NULL, NULL, 1
              FROM shadow_decision_outcomes outcome
              JOIN metadata_reports report ON report.report_id=outcome.report_id
              JOIN json_each(CASE WHEN json_valid(outcome.document) THEN outcome.document ELSE '{}' END, '$.outcomes') field
             WHERE json_extract(field.value, '$.destination')='isbn'
               AND json_extract(field.value, '$.reason_code') IN (
                   'source_value_absent','source_unavailable','source_value_malformed_or_incomplete',
                   'source_values_conflict','source_value_insufficient_association')
        """
        )
        columns = (
            "event_id,connection,destination,library,occurred_at,origin,report_id,state,"
            "work_id,document,event_type,title,detail,source_kind,source_document,"
            "source_parent_id,source_sequence,source_identity,policy_from,policy_to,source_valid"
        )
        group_identity = (
            "CASE WHEN source_kind='field_effect' THEN source_parent_id "
            "ELSE event_id END"
        )
        projection = (
            "WITH raw_activity_events("
            + columns
            + ") AS ("
            + events
            + "), ranked_activity_events AS (SELECT raw_activity_events.*, "
            "row_number() OVER (PARTITION BY source_kind,"
            + group_identity
            + " ORDER BY CASE WHEN source_kind='field_effect' THEN source_sequence END DESC, "
            "occurred_at DESC,event_id DESC) AS activity_rank, "
            "min(source_valid) OVER (PARTITION BY source_kind,"
            + group_identity
            + ") AS group_valid FROM raw_activity_events), "
            "activity_events("
            + columns
            + ") AS (SELECT "
            + columns.replace(
                "source_valid", "CASE WHEN group_valid=1 THEN source_valid ELSE 0 END"
            )
            + " FROM ranked_activity_events WHERE activity_rank=1) "
        )

        def waiting_scalar(value: object, *, nullable: bool = False) -> str | None:
            if value is None and nullable:
                return None
            if type(value) is not str or not value or len(value) > scalar_limit:
                raise FieldEffectError("persistence")
            return value

        try:
            columns = (
                "SELECT event_id,connection,destination,library,occurred_at,origin,"
                "report_id,state,work_id,document,event_type,title,detail,source_kind,"
                "source_document,source_parent_id,source_sequence,source_identity,"
                "policy_from,policy_to,source_valid FROM activity_events"
            )

            if search is None:
                # The page total is a window over the same immutable ledger
                # projection as the selected rows.  The former count-then-page
                # shape evaluated the expensive union twice for every ordinary
                # Activity refresh.  An out-of-range page is deliberately a
                # rare fallback: only then do we need its total before we know
                # the clamped offset.
                page_rows = self._connection.execute(
                    projection
                    + columns.replace(
                        " FROM activity_events",
                        ",count(*) OVER () AS activity_total,"
                        "min(occurred_at) OVER () AS oldest_included "
                        "FROM activity_events",
                    )
                    + predicate
                    + " ORDER BY occurred_at DESC,event_id DESC LIMIT ? OFFSET ?",
                    (*parameters, page_size, (page - 1) * page_size),
                ).fetchall()
                if page_rows:
                    total = int(page_rows[0][-2])
                    oldest_included = page_rows[0][-1]
                    if type(oldest_included) is not int:
                        raise FieldEffectError("persistence")
                    rows = [row[:-2] for row in page_rows]
                    page_count = max(1, (total + page_size - 1) // page_size)
                    selected_page = page
                else:
                    count_row = self._connection.execute(
                        projection
                        + "SELECT count(*),min(occurred_at) FROM activity_events"
                        + predicate,
                        parameters,
                    ).fetchone()
                    if (
                        count_row is None
                        or type(count_row[0]) is not int
                        or count_row[0] < 0
                        or (count_row[1] is not None and type(count_row[1]) is not int)
                    ):
                        raise FieldEffectError("persistence")
                    total = count_row[0]
                    oldest_included = count_row[1]
                    page_count = max(1, (total + page_size - 1) // page_size)
                    selected_page = min(page, page_count)
                    rows = (
                        []
                        if total == 0
                        else self._connection.execute(
                            projection
                            + columns
                            + predicate
                            + " ORDER BY occurred_at DESC,event_id DESC LIMIT ? OFFSET ?",
                            (*parameters, page_size, (selected_page - 1) * page_size),
                        ).fetchall()
                    )
            else:
                # Searching is allowed to scan the selected immutable corpus.
                # It renders and validates each row below before deciding a
                # match, rather than indexing raw JSON or consulting current
                # library state.
                total = 0
                page_count = 1
                selected_page = page
                oldest_included = None
            if search is None:
                projected_events, report_documents, _ = self._project_activity_rows(
                    rows,
                    document_limit=document_limit,
                    scalar_limit=scalar_limit,
                    searched=False,
                )
            else:
                matched: list[tuple[dict[str, object], str | None]] = []
                trailing: deque[tuple[dict[str, object], str | None]] = deque(
                    maxlen=page_size
                )
                matched_count = 0
                cursor = self._connection.execute(
                    projection
                    + columns
                    + predicate
                    + " ORDER BY occurred_at DESC,event_id DESC",
                    parameters,
                )
                while batch := cursor.fetchmany(64):
                    batch_events, batch_documents, reports = (
                        self._project_activity_rows(
                            batch,
                            document_limit=document_limit,
                            scalar_limit=scalar_limit,
                            searched=True,
                        )
                    )
                    for event in batch_events:
                        report_id = event.get("report_id")
                        report = (
                            reports.get(report_id) if type(report_id) is str else None
                        )
                        if search not in activity_search_text(event, report):
                            continue
                        occurred_at = event.get("occurred_at")
                        if type(occurred_at) is not int:
                            raise FieldEffectError("persistence")
                        oldest_included = (
                            occurred_at
                            if oldest_included is None
                            else min(oldest_included, occurred_at)
                        )
                        report_document = (
                            batch_documents.get(report_id)
                            if type(report_id) is str
                            else None
                        )
                        retained = (event, report_document)
                        if (page - 1) * page_size <= matched_count < page * page_size:
                            matched.append(retained)
                        trailing.append(retained)
                        matched_count += 1
                total = matched_count
                page_count = max(1, (total + page_size - 1) // page_size)
                selected_page = min(page, page_count)
                final_page_size = total % page_size or page_size
                selected = (
                    matched
                    if selected_page == page
                    else list(trailing)[-final_page_size:]
                )
                projected_events = [event for event, _ in selected]
                report_documents = {
                    cast(str, event["report_id"]): document
                    for event, document in selected
                    if type(event.get("report_id")) is str and document is not None
                }
            waiting = self._connection.execute(
                "SELECT effect.connection_name,effect.destination,effect.library_name,effect.work_id,effect.state,"
                "COALESCE(human_review.report_id,automatic_review.report_id,cover_review.report_id,description_review.report_id) FROM field_effects effect "
                "LEFT JOIN human_decisions human ON human.decision_id=effect.decision_id "
                "LEFT JOIN review_cases human_review ON human_review.review_case_id=human.review_case_id "
                "LEFT JOIN automatic_decisions automatic ON automatic.decision_id=effect.decision_id "
                "LEFT JOIN review_cases automatic_review ON automatic_review.review_case_id=automatic.review_case_id "
                "LEFT JOIN cover_selection_authorities cover_selection ON cover_selection.decision_id=effect.decision_id AND cover_selection.kind='automatic' "
                "LEFT JOIN cover_considerations cover_consideration ON cover_consideration.consideration_id=cover_selection.consideration_id "
                "LEFT JOIN review_cases cover_review ON cover_review.review_case_id=cover_consideration.review_case_id "
                "LEFT JOIN description_decisions description_decision ON description_decision.decision_id=effect.decision_id "
                "LEFT JOIN review_cases description_review ON description_review.review_case_id=description_decision.review_case_id "
                "WHERE effect.state IN ('planned','claimed','applying','reconciling','retryable') "
                "ORDER BY effect.updated_at DESC,effect.plan_id DESC LIMIT 1"
            ).fetchone()
            waiting_title = "Recorded work"
            if waiting is not None:
                for value in waiting[:5]:
                    waiting_scalar(value)
                waiting_report_id = waiting_scalar(waiting[5], nullable=True)
                if waiting_report_id is not None:
                    waiting_report_row = self._connection.execute(
                        "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document "
                        "FROM metadata_reports WHERE report_id=?",
                        (waiting_report_id,),
                    ).fetchone()
                    if (
                        waiting_report_row is None
                        or type(waiting_report_row[8]) is not str
                        or not 2
                        <= len(waiting_report_row[8].encode("utf-8"))
                        <= document_limit
                    ):
                        raise FieldEffectError("persistence")
                    try:
                        waiting_report_document = _validate_report_row(
                            waiting_report_row
                        )
                        waiting_report = MetadataReport.from_json(
                            waiting_report_document
                        )
                    except (MetadataReportError, ReportValueError) as error:
                        raise FieldEffectError("persistence") from error
                    if (
                        waiting_report.target.connection != waiting[0]
                        or waiting_report.target.library != waiting[2]
                        or waiting_report.target.item != waiting[3]
                    ):
                        raise FieldEffectError("persistence")
                    waiting_title = waiting_report.target.current_title or "Untitled"
            result = {
                "events": projected_events,
                "page": selected_page,
                "page_count": page_count,
                "page_size": page_size,
                "report_documents": report_documents,
                "current_waiting": None
                if waiting is None
                else {
                    "destination": waiting[1],
                    "library": waiting[2],
                    "work_id": waiting[3],
                    "state": waiting[4],
                    "work_label": waiting_title,
                },
                "total": total,
            }
            revision = self._activity_projection_revision()
            if database_identity != self._activity_database_identity():
                raise FieldEffectError("persistence")
            transaction.commit()
            return result, revision, oldest_included, database_identity
        except (
            json.JSONDecodeError,
            RecursionError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error
        except FieldEffectError:
            transaction.rollback()
            raise

    def field_effect_activity_transitions(
        self, event_id: str, after_sequence: int | None = None, limit: int = 20
    ) -> tuple[dict[str, object], ...]:
        """Return the bounded immutable lifecycle for one grouped effect row."""
        if (
            type(event_id) is not str
            or not event_id
            or len(event_id) > 128
            or (
                after_sequence is not None
                and (type(after_sequence) is not int or after_sequence < 1)
            )
            or limit not in {10, 20}
        ):
            raise FieldEffectError("invalid_activity_query")
        return self._retry_activity_busy(
            lambda: self._field_effect_activity_transitions_once(
                event_id, after_sequence, limit
            )
        )

    @local_operation
    def _field_effect_activity_transitions_once(
        self, event_id: str, after_sequence: int | None, limit: int
    ) -> tuple[dict[str, object], ...]:
        transaction = self._transaction(
            "field_effect_activity_transitions", deferred=True
        )
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT plan_id FROM field_effect_events WHERE event_id=? "
                "UNION SELECT plan_id FROM field_effects WHERE plan_id=?",
                (event_id, event_id),
            ).fetchone()
            if row is None:
                transaction.commit()
                return ()
            documents = self._field_effect_event_documents(row[0])
            transitions = tuple(json.loads(document) for document in documents)
            if any(
                type(item) is not dict
                or type(item.get("event_id")) is not str
                or type(item.get("occurred_at")) is not int
                or type(item.get("state")) is not str
                or type(item.get("sequence")) is not int
                for item in transitions
            ):
                raise FieldEffectError("persistence")
            result = tuple(
                {
                    "event_id": item["event_id"],
                    "occurred_at": item["occurred_at"],
                    "state": item["state"],
                    "sequence": item["sequence"],
                }
                for item in transitions
                if after_sequence is None or item["sequence"] > after_sequence
            )[: limit + 1]
            transaction.commit()
            return result
        except (
            FieldEffectError,
            json.JSONDecodeError,
            RecursionError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error

    @local_operation
    def claim_field_effect(
        self, plan_id: str, expected_state: str, claim_token: str, claimed_at: int
    ) -> FieldEffectOperation:
        transaction = self._transaction("claim_field_effect")
        try:
            transaction.begin()
            if self._connection.execute(
                "SELECT paused FROM field_effect_control WHERE singleton=1"
            ).fetchone() != (0,):
                raise FieldEffectError("paused")
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != expected_state:
                raise FieldEffectError("claim_conflict")
            persisted = FieldEffectOperation.from_json(row[0])
            for member in (persisted.plan, persisted.plan.subtitle_plan()):
                if member is None:
                    continue
                if (
                    self.field_effect_capability_suspension(
                        member.connection, member.destination
                    )
                    is not None
                ):
                    raise FieldEffectError("capability")
                if self._connection.execute(
                    "SELECT current FROM field_effect_authorities WHERE decision_id=?",
                    (member.authority.decision_id,),
                ).fetchone() != (1,):
                    raise FieldEffectError("authority")
                if self._connection.execute(
                    "SELECT current FROM field_effect_consents WHERE consent_id=?",
                    (member.consent.consent_id,),
                ).fetchone() != (1,):
                    raise FieldEffectError("consent")
                if member.consent.envelope_identity != self._consent_envelope_identity(
                    member.library
                ):
                    raise FieldEffectError("consent")
                authority = member.authority
                if authority.source == "persisted_review_case":
                    try:
                        case_envelope = json.loads(
                            self.get_review_case(authority.review_case_id)
                        )
                        case = case_envelope["case"]
                        target = case["target"]
                    except (
                        MetadataReportError,
                        KeyError,
                        TypeError,
                        json.JSONDecodeError,
                    ) as error:
                        raise FieldEffectError("authority") from error
                    if (
                        case_envelope.get("current") is not True
                        or not self._review_case_accepts_authority(
                            authority.review_case_id
                        )
                        or case.get("target_fingerprint")
                        != authority.target_fingerprint
                        or (
                            target.get("connection"),
                            target.get("library"),
                            target.get("item"),
                        )
                        != (
                            member.connection,
                            member.library,
                            member.work_id,
                        )
                    ):
                        raise FieldEffectError("authority")
                    authored_row = self._connection.execute(
                        "SELECT document FROM description_decisions WHERE decision_id=?",
                        (authority.decision_id,),
                    ).fetchone()
                    if authored_row is not None:
                        from mechanical_librarian.application.field_effects import (
                            authority_from_decision,
                        )

                        authored = AuthoredDescriptionDecision.from_json(
                            authored_row[0]
                        )
                        projected = authority_from_decision(authored)
                        source = self._authority_source(authored)
                        if (
                            projected is None
                            or source["source"] != "persisted_review_case"
                            or replace(projected, source="persisted_review_case")
                            != authority
                        ):
                            raise FieldEffectError("authority")
                    else:
                        if authority.origin == "human":
                            decision = self._connection.execute(
                                "SELECT document FROM human_decisions WHERE decision_id=? "
                                "AND review_case_id=? AND destination=?",
                                (
                                    authority.decision_id,
                                    authority.review_case_id,
                                    member.destination,
                                ),
                            ).fetchone()
                        else:
                            decision = self._connection.execute(
                                "SELECT document FROM automatic_decisions WHERE decision_id=? "
                                "AND review_case_id=? AND destination=? UNION ALL "
                                "SELECT json_extract(selection.document,'$.decision_document') "
                                "FROM current_cover_selection_authorities current "
                                "JOIN cover_selection_authorities selection "
                                "ON selection.selection_id=current.selection_id "
                                "WHERE selection.decision_id=? AND selection.kind='automatic' "
                                "AND ?='cover'",
                                (
                                    authority.decision_id,
                                    authority.review_case_id,
                                    member.destination,
                                    authority.decision_id,
                                    member.destination,
                                ),
                            ).fetchone()
                        if (
                            decision is None
                            or json.loads(decision[0]).get("target_fingerprint")
                            != authority.target_fingerprint
                        ):
                            raise FieldEffectError("authority")
                        if authority.origin == "automatic_policy":
                            automatic = json.loads(decision[0])
                            safety = self._connection.execute(
                                "SELECT paused,global_suspension,category_suspensions_document "
                                "FROM automatic_safety_state WHERE singleton=1"
                            ).fetchone()
                            suspended = (
                                {} if safety is None else dict(json.loads(safety[2]))
                            )
                            if (
                                safety is None
                                or safety[0] != 0
                                or safety[1] is not None
                                or automatic_safety_category(
                                    automatic["policy_category"],
                                    automatic["policy_option"],
                                )
                                in suspended
                                or (
                                    member.destination != "cover"
                                    and not qualification_manifest_is_current(
                                        automatic["policy_category"],
                                        automatic["policy_option"],
                                        automatic["qualification_manifest_sha256"],
                                    )
                                )
                            ):
                                raise FieldEffectError("authority")
                if persisted.plan.subtitle_companion is not None:
                    decision_row = self._connection.execute(
                        "SELECT document FROM automatic_decisions WHERE decision_id=?",
                        (member.authority.decision_id,),
                    ).fetchone()
                    if decision_row is None or not self._authority_is_current(
                        member.authority
                    ):
                        raise FieldEffectError("authority")
                    paired_source = self._authority_source(
                        AutomaticDecision.from_json(decision_row[0])
                    )
                    if (
                        paired_source["paired_bundle_id"]
                        != persisted.plan.subtitle_companion.bundle_id
                        or paired_source["pre_value"] != member.pre_value
                        or project_decision_value(
                            member.destination, paired_source["decision_value"]
                        )
                        != member.desired_value
                        or paired_source["target_version"] != member.target_version
                        or cast(tuple[str, ...], paired_source["dependency_digests"])
                        != member.dependency_digests
                    ):
                        raise FieldEffectError("authority_binding")
            operation = persisted.transition(
                "claimed", at=claimed_at, claim_token=claim_token
            )
            self._update_field_effect(operation, expected_state)
            self._record_field_effect_event(operation, "claimed", claimed_at)
            transaction.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    @local_operation
    def operational_snapshot(self) -> dict[str, object]:
        """Return only bounded codes and scopes; never persisted effect values."""
        try:
            signals: list[dict[str, object]] = []
            safety = self._connection.execute(
                "SELECT paused,global_suspension,category_suspensions_document "
                "FROM automatic_safety_state WHERE singleton=1"
            ).fetchone()
            if safety is None:
                raise FieldEffectError("persistence")
            if safety[0]:
                signals.append(
                    _operational_signal("field_suspended", "owner_pause", "runtime")
                )
            if safety[1] is not None:
                signals.append(
                    _operational_signal(
                        "field_suspended", "automatic_suspension", "runtime"
                    )
                )
            categories = json.loads(safety[2])
            if type(categories) is not list:
                raise FieldEffectError("persistence")
            for entry in categories[:64]:
                if (
                    type(entry) is not list
                    or len(entry) != 2
                    or type(entry[0]) is not str
                ):
                    raise FieldEffectError("persistence")
                category = entry[0]
                signals.append(
                    _operational_signal(
                        "category_suspended",
                        "automatic_suspension",
                        "category",
                        category=category,
                    )
                )
            if self.field_effect_paused():
                signals.append(
                    _operational_signal("field_suspended", "owner_pause", "runtime")
                )
            authentication = self._connection.execute(
                "SELECT work.failure_root,work.report_id,report.report_id,"
                "report.document FROM review_preparation_work AS work "
                "LEFT JOIN metadata_reports AS report "
                "ON report.report_id=work.report_id "
                "WHERE work.state='failed' "
                "AND work.failure_code='authentication' "
                "ORDER BY work.updated_at DESC,work.work_id LIMIT 256"
            ).fetchall()
            authentication_roots: set[str] = set()
            for failure_root, work_report_id, report_id, document in authentication:
                if work_report_id is None:
                    if failure_root is None:
                        raise FieldEffectError("persistence")
                    authentication_roots.add(failure_root)
                    continue
                if report_id != work_report_id or type(document) is not str:
                    raise FieldEffectError("persistence")
                try:
                    report = MetadataReport.from_json(document)
                except (ReportValueError, TypeError, ValueError) as error:
                    raise FieldEffectError("persistence") from error
                if report.report_id != report_id:
                    raise FieldEffectError("persistence")
                report_roots = {
                    provider.provider
                    for provider in report.providers
                    if any(
                        status
                        in {
                            ProviderStatus.AUTHENTICATION,
                            ProviderStatus.AUTHORIZATION,
                        }
                        for status in (
                            provider.status,
                            *(lane.status for lane in provider.lanes),
                        )
                    )
                }
                if not report_roots:
                    raise FieldEffectError("persistence")
                authentication_roots.update(report_roots)
            for provider_root in sorted(authentication_roots)[:64]:
                signals.append(
                    _operational_signal(
                        "authentication_failure",
                        "provider_authentication",
                        "connection",
                        connection=provider_root,
                    )
                )
            terminal_failures = self._connection.execute(
                "SELECT work_id,stage,attempt,recovery_attempt,failure_code "
                "FROM review_preparation_work WHERE state='failed' "
                "AND failure_code IS NOT NULL "
                "ORDER BY updated_at DESC,work_id LIMIT 256"
            ).fetchall()
            for work_id, stage, attempt, recovery_attempt, code in terminal_failures:
                if (
                    type(work_id) is not str
                    or type(stage) is not str
                    or type(attempt) is not int
                    or type(recovery_attempt) is not int
                    or type(code) is not str
                ):
                    raise FieldEffectError("persistence")
                signals.append(
                    _operational_signal(
                        "owner_action_required",
                        "preparation_failure",
                        "work",
                        diagnostic_reference=preparation_failure_reference(
                            work_id, stage, attempt, recovery_attempt, code
                        ),
                        stage=stage,
                        code=code,
                        attempt=attempt,
                    )
                )
            rows = self._connection.execute(
                "SELECT connection_name,library_name,work_id,destination,state,document "
                "FROM field_effects WHERE state NOT IN ('verified','superseded','abandoned') "
                "ORDER BY updated_at DESC,plan_id LIMIT 256"
            ).fetchall()
            for connection, library, work_id, destination, state, document in rows:
                operation = FieldEffectOperation.from_json(document)
                failure = operation.failure
                if failure in {"target_currentness", "third_value", "readback"}:
                    kind, cause = "owner_action_required", "currentness_conflict"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library,
                        work_id,
                        destination,
                    )
                elif failure == "backup":
                    kind, cause = "backup_failure", "backup"
                    scope = "library"
                    signal_library, signal_work, signal_field = library, None, None
                elif failure in {"capability", "target"}:
                    kind, cause = "service_incompatibility", "service_incompatibility"
                    scope = "connection"
                    signal_library, signal_work, signal_field = None, None, None
                elif failure in {"authority", "consent", "consent_revoked"}:
                    kind, cause = "field_suspended", "authority_unavailable"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library,
                        work_id,
                        destination,
                    )
                elif state in {"claimed", "applying", "reconciling", "retryable"}:
                    kind, cause = (
                        "automatic_recovery",
                        ("reconciliation" if state == "reconciling" else "in_progress"),
                    )
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library,
                        work_id,
                        destination,
                    )
                else:
                    kind, cause = "healthy_activity", "planned"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library,
                        work_id,
                        destination,
                    )
                signals.append(
                    _operational_signal(
                        kind,
                        cause,
                        scope,
                        connection=connection,
                        library=signal_library,
                        work_id=signal_work,
                        field=signal_field,
                    )
                )
            return {"signals": signals}
        except (json.JSONDecodeError, sqlite3.Error, FieldEffectError) as error:
            raise FieldEffectError("persistence") from error
