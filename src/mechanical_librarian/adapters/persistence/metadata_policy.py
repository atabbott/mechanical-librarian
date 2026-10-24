"""The metadata-policy aggregate.

Owns the policy drafts, the singleton policy state, the per-library
resolutions, and the transition and transition-item tables that record one
activation working its way through the affected libraries.

Three seams cross this aggregate's boundary, and each is resolved differently:

- `_restart_metadata_policy_work` writes only `review_preparation_work`, so it
  belongs to review preparation and is injected as one narrow typed callable
  rather than moved here.
- `activate_metadata_policy` writes `current_review_case_lineages`,
  `review_case_transitions` and `review_preparation_scans` inside its own
  transaction. Those writes travel with it until the aggregates that own those
  tables are extracted; the transaction is this method's own, so they stay
  atomic with the activation either way.
- `_carry_field_effect_consents` writes `field_effect_consents`, which the
  field-effect aggregate owns, so activation hands it each library's incoming
  resolved policy inside this transaction rather than writing consent SQL here
  (#1370).
- `enter_shadow_preview` writes `metadata_policy_state` and now lives here. It
  calls `activate_metadata_policy` to reconcile the automatic cohort, and a
  unit test replaces that call on the composing repository to prove the posture
  rolls back when reconciliation fails; the call therefore travels as an
  injected callable resolved through that repository at call time, so the
  substitution point is still real. Calling this class's own method directly
  would have silently disarmed the assertion.

The shared current-review join lives in `review_sql.py` and the browse-row
validator in `schema.py`, so neither is copied here.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from typing import cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.proposal_sets import (
    _validate_proposal_row,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_REVIEW_FROM_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _validate_review_browse_row,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.metadata_policy import (
    PolicyContentionError,
    PolicyPreviewCapture,
    PolicyPreviewLibraryCapture,
    PolicyTransition,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.cover_qualification_policy import (
    cover_aware_qualified_policy_options,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
    ReviewTransition,
)


class SQLiteMetadataPolicyRepository(ConnectionScoped):
    """Draft, resolve, activate and settle the exact metadata policy."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        restart_metadata_policy_work: Callable[[str, str, str, int], None],
        activate_metadata_policy: Callable[..., PolicyTransition | None],
        carry_field_effect_consents: Callable[
            [tuple[tuple[str, str], ...], str, int], None
        ],
    ) -> None:
        super().__init__(connection, binding)
        # Review preparation owns `review_preparation_work`, so the restart it
        # performs is injected under the name the moved call sites already use.
        self._restart_metadata_policy_work = restart_metadata_policy_work
        # `enter_shadow_preview` reconciles through the composing repository
        # rather than calling this class's own `activate_metadata_policy`,
        # because a unit test replaces that call on the repository to prove the
        # posture rolls back when reconciliation fails. The hook cannot carry
        # the public name: binding it there would shadow the definition below
        # and make the composing repository's delegating stub recurse.
        self._activate_metadata_policy = activate_metadata_policy
        self._carry_field_effect_consents = carry_field_effect_consents

    @local_operation
    def shadow_posture(self) -> str:
        """Return the closed global posture without implying authority."""
        try:
            row = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("shadow posture is unavailable")
            return cast(str, row[0])
        except sqlite3.Error as error:
            raise MetadataReportError("shadow posture is unavailable") from error

    @local_operation
    def enter_shadow_preview(self, updated_at: int) -> str:
        """Enter Preview and reconcile the exact-current cohort when leaving automatic."""
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("shadow posture request is invalid")
        transaction = self._transaction("enter_shadow_preview")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT active_policy_id,generation,posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if row is None or row[2] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("shadow posture is unavailable")
            # Automatic decisions must never survive a posture change without
            # re-projecting the exact-current cohort through the established
            # F09 transition/work protocol.  A fresh generation makes both the
            # bounded progress signal and the later confirmation check durable.
            if row[2] == "automatic":
                active_document = self._connection.execute(
                    "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                    (row[0],),
                ).fetchone()
                if active_document is None or type(active_document[0]) is not str:
                    raise MetadataReportError(
                        "shadow posture reconciliation is unavailable"
                    )
                active_profile = MetadataPolicyProfile.from_data(
                    json.loads(active_document[0])
                )
                # A policy with no automatic destination has no affected
                # automatic cohort.  Preserve the ordinary Preview workflow.
                affected = (
                    self._connection.execute(
                        "SELECT 1 FROM automatic_decisions LIMIT 1"
                    ).fetchone()
                    if any(
                        value == "automatic" for _, value in active_profile.destinations
                    )
                    else None
                )
                # There is no prior automatic authority to reconcile until a
                # current cohort has actually received an automatic decision.
                # Initial Preview remains a harmless report-only entry.
                if affected is not None:
                    resolutions = tuple(
                        self._connection.execute(
                            "SELECT library_name,resolved_policy_id,document FROM metadata_policy_resolutions WHERE generation=? ORDER BY library_name",
                            (row[1],),
                        ).fetchall()
                    )
                    if not resolutions:
                        profile = active_profile
                        libraries = self._connection.execute(
                            "SELECT DISTINCT c.library_name "
                            + _CURRENT_REVIEW_FROM_SQL
                            + " ORDER BY c.library_name"
                        ).fetchall()
                        resolutions = tuple(
                            (library[0], resolved.resolved_id, resolved.canonical_json)
                            for library in libraries
                            for resolved in (resolve_policy(profile, library[0]),)
                        )
                    self._activate_metadata_policy(
                        row[0],
                        row[0],
                        resolutions,
                        updated_at,
                        expected_active_generation=row[1],
                        reconcile_current=True,
                    )
            self._connection.execute(
                "UPDATE metadata_policy_state SET posture='preview' WHERE singleton=1"
            )
            transaction.commit()
            return "preview"
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("shadow posture persistence failed") from error

    @local_operation
    def activate_qualified_automatic(self, updated_at: int) -> str:
        """Confirm Preview only when every requested automatic option is sealed."""
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("automatic posture request is invalid")
        transaction = self._transaction("activate_qualified_automatic")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT s.posture,d.document FROM metadata_policy_state s JOIN metadata_policy_drafts d ON d.policy_id=s.active_policy_id WHERE s.singleton=1"
            ).fetchone()
            if row is None or row[0] != "preview":
                raise MetadataReportError(
                    "automatic posture confirmation is unavailable"
                )
            policy = MetadataPolicyProfile.from_data(json.loads(row[1]))
            requested = {
                f"{field}:{option}"
                for field, option in policy.options
                if dict(policy.destinations)[field] == "automatic"
            }
            if not requested or not requested.issubset(
                cover_aware_qualified_policy_options()
            ):
                raise MetadataReportError(
                    "automatic posture qualification is unavailable"
                )
            transition = self.metadata_policy_transition()
            if (
                transition is None
                or transition.generation
                != self._connection.execute(
                    "SELECT generation FROM metadata_policy_state WHERE singleton=1"
                ).fetchone()[0]
                or transition.state != "completed"
                or transition.completed != transition.total
                or transition.failed != 0
            ):
                raise MetadataReportError(
                    "automatic posture confirmation is unavailable"
                )
            self._connection.execute(
                "UPDATE metadata_policy_state SET posture='automatic' WHERE singleton=1"
            )
            transaction.commit()
            return "automatic"
        except (
            sqlite3.Error,
            MetadataReportError,
            MetadataPolicyError,
            ValueError,
            TypeError,
        ) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("automatic posture persistence failed") from error

    @local_operation
    def create_metadata_policy_draft(self, document: str) -> str:
        """Persist an exact canonical draft; there is intentionally no update."""
        transaction = self._transaction("create_metadata_policy_draft")
        try:
            profile = MetadataPolicyProfile.from_data(json.loads(document))
            if profile.canonical_json != document:
                raise MetadataPolicyError("policy document is not canonical")
            transaction.begin()
            posture = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if posture is None or posture[0] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("metadata policy posture is unavailable")
            requested_automatic = {
                f"{category}:{dict(profile.options)[category]}"
                for category, destination in profile.destinations
                if destination == "automatic"
            }
            if not requested_automatic.issubset(cover_aware_qualified_policy_options()):
                raise MetadataReportError(
                    "metadata policy automatic intent is unqualified"
                )
            self._connection.execute(
                "INSERT OR IGNORE INTO metadata_policy_drafts(policy_id,document) VALUES(?,?)",
                (profile.policy_id, document),
            )
            row = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                (profile.policy_id,),
            ).fetchone()
            if row != (document,):
                raise MetadataReportError("metadata policy draft is unavailable")
            transaction.commit()
            return profile.policy_id
        except (
            MetadataReportError,
            MetadataPolicyError,
            ValueError,
            TypeError,
            sqlite3.Error,
        ) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("metadata policy draft is unavailable") from error

    @local_operation
    def get_metadata_policy_draft(self, policy_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                (policy_id,),
            ).fetchone()
            if row is None or type(row[0]) is not str:
                raise MetadataReportError("metadata policy draft is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(row[0]))
            if profile.policy_id != policy_id or profile.canonical_json != row[0]:
                raise MetadataReportError("metadata policy draft is unavailable")
            return row[0]
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError("metadata policy draft is unavailable") from error

    @local_operation
    def active_metadata_policy(self) -> str:
        try:
            row = self._connection.execute(
                "SELECT d.document FROM metadata_policy_state s JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id WHERE s.singleton=1"
            ).fetchone()
            if row is None or type(row[0]) is not str:
                raise MetadataReportError("active metadata policy is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(row[0]))
            if profile.canonical_json != row[0]:
                raise MetadataReportError("active metadata policy is unavailable")
            return row[0]
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError(
                "active metadata policy is unavailable"
            ) from error

    @local_operation
    def current_resolved_policy_id(self, library: str) -> str:
        if type(library) is not str or not library:
            raise MetadataReportError("active metadata policy is unavailable")
        try:
            row = self._connection.execute(
                "SELECT s.generation,r.resolved_policy_id,d.document FROM metadata_policy_state s "
                "JOIN metadata_policy_drafts d ON d.policy_id=s.active_policy_id "
                "LEFT JOIN metadata_policy_resolutions r ON r.generation=s.generation AND r.library_name=? "
                "WHERE s.singleton=1",
                (library,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("active metadata policy is unavailable")
            if row[1] is not None:
                resolved = self.current_resolved_metadata_policy(library)
                if resolved is None:
                    raise MetadataReportError(
                        "active metadata policy resolution is unavailable"
                    )
                return resolved.resolved_id
            if row[0] != 1:
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            profile = MetadataPolicyProfile.from_data(json.loads(row[2]))
            return resolve_policy(profile, library).resolved_id
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError(
                "active metadata policy is unavailable"
            ) from error

    @local_operation
    def current_resolved_metadata_policy(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        return self._metadata_policy_resolution(library, allow_missing=False)

    @local_operation
    def metadata_policy_resolution_for_preview(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        return self._metadata_policy_resolution(library, allow_missing=True)

    @local_operation
    def capture_policy_preview(
        self, libraries: tuple[str, ...], limit: int
    ) -> PolicyPreviewCapture:
        """Capture policy, resolutions and ordered inputs in one deferred snapshot."""
        if type(limit) is not int or not 1 <= limit <= 20:
            raise MetadataReportError("policy preview is unavailable")
        transaction = self._transaction("capture_policy_preview", deferred=True)
        try:
            transaction.begin()
            document, resolutions = self.metadata_policy_preview_baseline(libraries)
            profile = MetadataPolicyProfile.from_data(json.loads(document))
            row = self._connection.execute(
                "SELECT generation FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 1:
                raise MetadataReportError("policy preview is unavailable")
            captured = []
            remaining = limit
            for library, resolution in resolutions:
                total, pairs = (
                    self.current_policy_impact_pairs(library, remaining)
                    if remaining
                    else (self.current_unapplied_count(library), ())
                )
                if resolution is not None and any(
                    proposal.resolved_policy_id != resolution.resolved_id
                    for _, proposal in pairs
                ):
                    raise MetadataReportError("policy preview is unavailable")
                captured.append(
                    PolicyPreviewLibraryCapture(library, resolution, total, pairs)
                )
                remaining -= len(pairs)
            result = PolicyPreviewCapture(profile, row[0], tuple(captured))
            transaction.commit()
            return result
        except Exception:
            transaction.rollback()
            raise

    @local_operation
    def metadata_policy_preview_baseline(
        self, libraries: tuple[str, ...]
    ) -> tuple[str, tuple[tuple[str, ResolvedMetadataPolicy | None], ...]]:
        if (
            type(libraries) is not tuple
            or libraries != tuple(sorted(set(libraries)))
            or any(type(library) is not str or not library for library in libraries)
        ):
            raise MetadataReportError("active metadata policy is unavailable")
        try:
            join_filter = "0"
            parameters: tuple[str, ...] = ()
            if libraries:
                join_filter = (
                    "r.library_name IN (" + ",".join("?" for _ in libraries) + ")"
                )
                parameters = libraries
            rows = self._connection.execute(
                "SELECT s.active_policy_id,d.document,r.library_name,r.resolved_policy_id,r.document "
                "FROM metadata_policy_state s JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id LEFT JOIN metadata_policy_resolutions r "
                f"ON r.generation=s.generation AND {join_filter} "
                "WHERE s.singleton=1 ORDER BY r.library_name",
                parameters,
            ).fetchall()
            if not rows or type(rows[0][0]) is not str or type(rows[0][1]) is not str:
                raise MetadataReportError("active metadata policy is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(rows[0][1]))
            if (
                profile.policy_id != rows[0][0]
                or profile.canonical_json != rows[0][1]
                or any(row[:2] != rows[0][:2] for row in rows)
            ):
                raise MetadataReportError("active metadata policy is unavailable")
            resolutions: dict[str, ResolvedMetadataPolicy | None] = {
                library: None for library in libraries
            }
            for _, _, library, resolved_id, document in rows:
                if library is None:
                    continue
                resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
                if (
                    library not in resolutions
                    or resolved.profile_id != profile.policy_id
                    or resolved.library != library
                    or resolved.resolved_id != resolved_id
                    or resolved.canonical_json != document
                ):
                    raise MetadataReportError(
                        "active metadata policy resolution is unavailable"
                    )
                resolutions[library] = resolved
            return rows[0][1], tuple(sorted(resolutions.items()))
        except MetadataReportError:
            raise
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError(
                "active metadata policy is unavailable"
            ) from error

    def _metadata_policy_resolution(
        self, library: str, *, allow_missing: bool
    ) -> ResolvedMetadataPolicy | None:
        if type(library) is not str or not library:
            raise MetadataReportError("active metadata policy is unavailable")
        try:
            row = self._connection.execute(
                "SELECT s.generation,r.resolved_policy_id,r.document "
                "FROM metadata_policy_state s LEFT JOIN metadata_policy_resolutions r "
                "ON r.generation=s.generation AND r.library_name=? WHERE s.singleton=1",
                (library,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("active metadata policy is unavailable")
            if row[1] is None:
                if row[0] == 1 or allow_missing:
                    return None
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            resolved = ResolvedMetadataPolicy.from_data(json.loads(row[2]))
            if (
                resolved.library != library
                or resolved.resolved_id != row[1]
                or resolved.canonical_json != row[2]
            ):
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            return resolved
        except MetadataReportError:
            raise
        except (MetadataPolicyError, ValueError, TypeError, sqlite3.Error) as error:
            raise MetadataReportError(
                "active metadata policy is unavailable"
            ) from error

    @local_operation
    def bind_initial_metadata_policy_resolution(self, document: str) -> str:
        """Bind one exact configured resolution while generation one is active."""
        transaction = self._transaction("bind_initial_metadata_policy_resolution")
        try:
            resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
            if resolved.canonical_json != document:
                raise MetadataPolicyError("policy resolution is not canonical")
            transaction.begin()
            state = self._connection.execute(
                "SELECT s.generation,s.active_policy_id,d.document "
                "FROM metadata_policy_state s JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id WHERE s.singleton=1"
            ).fetchone()
            if state is None or state[0] != 1 or resolved.profile_id != state[1]:
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            profile = MetadataPolicyProfile.from_data(json.loads(state[2]))
            if profile.policy_id != resolved.profile_id:
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            requested = {
                f"{category}:{dict(resolved.options)[category]}"
                for category, destination in resolved.destinations
                if destination == "automatic"
            }
            if (
                state[0] == 1
                and state[1] == profile.policy_id
                and requested
                and not requested.issubset(cover_aware_qualified_policy_options())
            ):
                raise MetadataReportError(
                    "active metadata policy resolution is unqualified"
                )
            self._connection.execute(
                "INSERT OR IGNORE INTO metadata_policy_resolutions("
                "generation,library_name,resolved_policy_id,document) VALUES(1,?,?,?)",
                (resolved.library, resolved.resolved_id, document),
            )
            stored = self._connection.execute(
                "SELECT resolved_policy_id,document FROM metadata_policy_resolutions "
                "WHERE generation=1 AND library_name=?",
                (resolved.library,),
            ).fetchone()
            if stored != (resolved.resolved_id, document):
                raise MetadataReportError(
                    "active metadata policy resolution is unavailable"
                )
            transaction.commit()
            return resolved.resolved_id
        except (
            MetadataReportError,
            MetadataPolicyError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "active metadata policy resolution is unavailable"
            ) from error

    @local_operation
    def activate_metadata_policy(
        self,
        policy_id: str,
        expected_active_policy_id: str,
        resolutions: tuple[tuple[str, str, str], ...],
        activated_at: int,
        *,
        expected_active_generation: int,
        reconcile_current: bool = False,
    ) -> PolicyTransition | None:
        if (
            type(policy_id) is not str
            or type(expected_active_policy_id) is not str
            or type(expected_active_generation) is not int
            or expected_active_generation < 1
            or type(resolutions) is not tuple
            or resolutions != tuple(sorted(resolutions))
            or len(resolutions) != len({library for library, _, _ in resolutions})
            or any(
                type(library) is not str
                or not library
                or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", resolved_id) is None
                or type(document) is not str
                or not 2 <= len(document.encode("utf-8")) <= 16_384
                for library, resolved_id, document in resolutions
            )
            or type(activated_at) is not int
            or activated_at < 0
            or type(reconcile_current) is not bool
        ):
            raise MetadataReportError("metadata policy activation is invalid")
        transaction = self._transaction("activate_metadata_policy")
        try:
            transaction.begin()
            state = self._connection.execute(
                "SELECT active_policy_id,generation,posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if state is None or state[2] not in {"review", "preview", "automatic"}:
                raise PolicyContentionError(
                    "stale", "metadata policy activation is stale"
                )
            if (
                state[0] != expected_active_policy_id
                or state[1] != expected_active_generation
            ):
                replay = self._connection.execute(
                    "SELECT transition_id,from_policy_id,to_policy_id,generation,state,total,acknowledged "
                    "FROM metadata_policy_transitions WHERE generation=?",
                    (state[1],),
                ).fetchone()
                if (
                    state[0] == policy_id
                    and state[1] == expected_active_generation + 1
                    and (policy_id != expected_active_policy_id or reconcile_current)
                    and replay is not None
                    and replay[1] == expected_active_policy_id
                    and replay[2] == policy_id
                    and replay[3] == expected_active_generation + 1
                ):
                    result = self.metadata_policy_transition()
                    if result is None or result.transition_id != replay[0]:
                        raise MetadataReportError(
                            "metadata policy transition is unavailable"
                        )
                    transaction.commit()
                    return result
                raise PolicyContentionError(
                    "stale", "metadata policy activation is stale"
                )
            if policy_id == state[0] and not reconcile_current:
                transaction.commit()
                return None
            active_transition = self._connection.execute(
                "SELECT state,acknowledged FROM metadata_policy_transitions WHERE generation=?",
                (state[1],),
            ).fetchone()
            if active_transition is not None and active_transition[0] == "processing":
                raise PolicyContentionError(
                    "processing", "metadata policy transition is processing"
                )
            # A new activation supersedes a finished transition the owner has
            # not dismissed, so its notice ends here. Its items and time stay
            # as recorded: Activity dates the transition by updated_at (#1369).
            self._connection.execute(
                "UPDATE metadata_policy_transitions SET acknowledged=1 "
                "WHERE generation=? AND acknowledged=0 AND state IN ('completed','completed_with_failures')",
                (state[1],),
            )
            draft = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                (policy_id,),
            ).fetchone()
            if draft is None:
                raise MetadataReportError("metadata policy draft is unavailable")
            profile = MetadataPolicyProfile.from_data(json.loads(draft[0]))
            if profile.policy_id != policy_id or profile.canonical_json != draft[0]:
                raise MetadataReportError("metadata policy draft is unavailable")
            automatic_options = {
                f"{category}:{dict(profile.options)[category]}"
                for category, destination in profile.destinations
                if destination == "automatic"
            }
            if automatic_options and (
                state[2] not in {"preview", "automatic"}
                or not automatic_options.issubset(
                    cover_aware_qualified_policy_options()
                )
            ):
                raise PolicyContentionError(
                    "unqualified",
                    "metadata policy automatic intent is unqualified outside preview",
                )
            generation = state[1] + 1
            material = (
                f"metadata-policy-transition-v1\0{state[0]}\0{policy_id}\0{generation}"
            )
            transition_id = (
                "policy_transition_" + hashlib.sha256(material.encode()).hexdigest()
            )
            cohort = self._connection.execute(
                "SELECT c.connection_name,c.library_name,c.item_id,c.proposal_set_id,c.report_id,"
                "c.review_case_id,c.checkpoint "
                + _CURRENT_REVIEW_FROM_SQL
                + " WHERE 1 ORDER BY c.connection_name,c.library_name,c.item_id"
            ).fetchall()
            parsed_resolutions: list[tuple[str, str, str]] = []
            for library, resolved_id, document in resolutions:
                resolved = ResolvedMetadataPolicy.from_data(json.loads(document))
                if (
                    resolved.profile_id != policy_id
                    or resolved.library != library
                    or resolved.resolved_id != resolved_id
                    or resolved.canonical_json != document
                    or (
                        any(value == "automatic" for _, value in resolved.destinations)
                        and (
                            state[2] not in {"preview", "automatic"}
                            or not {
                                f"{category}:{dict(resolved.options)[category]}"
                                for category, destination in resolved.destinations
                                if destination == "automatic"
                            }.issubset(cover_aware_qualified_policy_options())
                        )
                    )
                ):
                    raise MetadataReportError("metadata policy resolution is invalid")
                parsed_resolutions.append((library, resolved_id, document))
            configured = {library for library, _, _ in parsed_resolutions}
            if any(row[1] not in configured for row in cohort):
                raise MetadataReportError("metadata policy resolution scope changed")
            self._connection.execute(
                "INSERT INTO metadata_policy_transitions(transition_id,from_policy_id,to_policy_id,generation,state,total,acknowledged,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    transition_id,
                    state[0],
                    policy_id,
                    generation,
                    "processing",
                    len(cohort),
                    0,
                    activated_at,
                    activated_at,
                ),
            )
            self._connection.executemany(
                "INSERT INTO metadata_policy_resolutions(generation,library_name,resolved_policy_id,document) VALUES(?,?,?,?)",
                (
                    (generation, library, resolved_id, document)
                    for library, resolved_id, document in parsed_resolutions
                ),
            )
            for (
                connection,
                library,
                item,
                proposal_id,
                report_id,
                case_id,
                checkpoint,
            ) in cohort:
                transition = ReviewTransition(
                    connection,
                    library,
                    item,
                    checkpoint + 1,
                    proposal_id,
                    report_id,
                    "retired",
                    case_id,
                    None,
                    "policy_changed",
                    "policy-activation-v1",
                )
                self._connection.execute(
                    "INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        transition.transition_id,
                        connection,
                        library,
                        item,
                        checkpoint + 1,
                        transition.actor,
                        case_id,
                        None,
                        proposal_id,
                        report_id,
                        "retired",
                        "policy_changed",
                    ),
                )
                updated = self._connection.execute(
                    "UPDATE current_review_case_lineages SET review_case_id=NULL,transition_id=?,checkpoint=?,outcome='retired' "
                    "WHERE connection_name=? AND library_name=? AND item_id=? AND review_case_id=? AND checkpoint=?",
                    (
                        transition.transition_id,
                        checkpoint + 1,
                        connection,
                        library,
                        item,
                        case_id,
                        checkpoint,
                    ),
                ).rowcount
                # The current-review join reads the package, so an item that
                # fails reconciliation must not keep its old-policy case there
                # (#1369).
                retired = self._connection.execute(
                    "UPDATE current_review_packages SET review_case_id=NULL,checkpoint=?,outcome='retired' "
                    "WHERE connection_name=? AND library_name=? AND item_id=? AND review_case_id=? AND checkpoint=?",
                    (checkpoint + 1, connection, library, item, case_id, checkpoint),
                ).rowcount
                if updated != 1 or retired != 1:
                    raise MetadataReportError("metadata policy cohort changed")
                work = self._connection.execute(
                    "SELECT work_id,scan_id,stage,state,report_id FROM review_preparation_work WHERE connection_name=? AND library_name=? AND item_id=?",
                    (connection, library, item),
                ).fetchone()
                item_state = "failed" if work is None else "pending"
                failure = "pipeline_unavailable" if work is None else None
                work_id = None if work is None else work[0]
                if work is not None:
                    if work[2] == "provider" and work[4] != report_id:
                        item_state = "failed"
                        failure = "stale"
                    elif work[2] not in {"extraction", "provider"} or work[3] in {
                        "failed",
                        "stale",
                    }:
                        if work[4] != report_id and not (
                            work[2] == "extraction" and work[4] is None
                        ):
                            raise MetadataReportError(
                                "metadata policy cohort evidence changed"
                            )
                        self._restart_metadata_policy_work(
                            work[0], work[2], report_id, activated_at
                        )
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='processing',updated_at=? WHERE scan_id=?",
                        (activated_at, work[1]),
                    )
                self._connection.execute(
                    "INSERT INTO metadata_policy_transition_items(transition_id,connection_name,library_name,item_id,report_id,work_id,state,failure_code,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        transition_id,
                        connection,
                        library,
                        item,
                        report_id,
                        work_id,
                        item_state,
                        failure,
                        activated_at,
                    ),
                )
            # Before the state moves, so each library's outgoing resolution is
            # still the active one: the owner's write permission follows the
            # policy they activate, and nothing else carries it (#1370).
            self._carry_field_effect_consents(
                tuple(
                    (library, resolved_id)
                    for library, resolved_id, _ in parsed_resolutions
                ),
                transition_id,
                activated_at,
            )
            changed = self._connection.execute(
                "UPDATE metadata_policy_state SET active_policy_id=?,generation=?,activated_at=? WHERE singleton=1 AND active_policy_id=? AND generation=?",
                (policy_id, generation, activated_at, state[0], state[1]),
            ).rowcount
            if changed != 1:
                raise PolicyContentionError(
                    "stale", "metadata policy activation is stale"
                )
            self._refresh_metadata_policy_transition(transition_id, activated_at)
            result = self.metadata_policy_transition()
            if result is None or result.transition_id != transition_id:
                raise MetadataReportError("metadata policy transition is unavailable")
            transaction.commit()
            return result
        except (
            MetadataReportError,
            MetadataPolicyError,
            ReviewCaseValueError,
            sqlite3.Error,
            ValueError,
            TypeError,
        ) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "metadata policy activation is unavailable"
            ) from error

    def _refresh_metadata_policy_transition(
        self, transition_id: str, updated_at: int
    ) -> None:
        counts = self._connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(state='complete'),0),COALESCE(SUM(state='failed'),0) FROM metadata_policy_transition_items WHERE transition_id=?",
            (transition_id,),
        ).fetchone()
        state = (
            "processing"
            if counts[1] + counts[2] < counts[0]
            else ("completed_with_failures" if counts[2] else "completed")
        )
        self._connection.execute(
            "UPDATE metadata_policy_transitions SET state=?,updated_at=? WHERE transition_id=?",
            (state, updated_at, transition_id),
        )

    @local_operation
    def metadata_policy_transition(self) -> PolicyTransition | None:
        try:
            row = self._connection.execute(
                "SELECT t.transition_id,t.from_policy_id,t.to_policy_id,t.generation,t.state,t.total,t.acknowledged,"
                "COALESCE(SUM(i.state='complete'),0),COALESCE(SUM(i.state='failed'),0) "
                "FROM metadata_policy_transitions t LEFT JOIN metadata_policy_transition_items i ON i.transition_id=t.transition_id "
                "GROUP BY t.transition_id ORDER BY t.generation DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return PolicyTransition(
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[7],
                row[5],
                row[8],
                bool(row[6]),
                "baseline" if row[3] == 1 else "activation",
            )
        except sqlite3.Error as error:
            raise MetadataReportError(
                "metadata policy transition is unavailable"
            ) from error

    @local_operation
    def retry_metadata_policy_transition(
        self, transition_id: str, retried_at: int
    ) -> PolicyTransition:
        transaction = self._transaction("retry_metadata_policy_transition")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT generation,state FROM metadata_policy_transitions WHERE transition_id=?",
                (transition_id,),
            ).fetchone()
            current = self._connection.execute(
                "SELECT generation FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if row is None or current != (row[0],):
                raise PolicyContentionError(
                    "stale", "metadata policy transition is stale"
                )
            failed = self._connection.execute(
                "SELECT connection_name,library_name,item_id,report_id,work_id FROM metadata_policy_transition_items WHERE transition_id=? AND state='failed'",
                (transition_id,),
            ).fetchall()
            for connection, library, item, report_id, work_id in failed:
                found = self._connection.execute(
                    "SELECT work_id,stage,state,report_id FROM review_preparation_work WHERE "
                    + (
                        "connection_name=? AND library_name=? AND item_id=?"
                        if work_id is None
                        else "work_id=?"
                    ),
                    (connection, library, item) if work_id is None else (work_id,),
                ).fetchone()
                if found is None:
                    # No preparation work exists to restart, so the item stays
                    # failed with its typed code.
                    continue
                work_id, work_stage, work_state, work_report = found
                if work_report not in (
                    {None, report_id} if work_stage == "extraction" else {report_id}
                ):
                    # The work moved on to a newer report, which prepares under
                    # the current policy: this item is superseded, not
                    # skipped, and keeps the stale code that says why (#1369).
                    self._connection.execute(
                        "UPDATE metadata_policy_transition_items SET work_id=?,state='complete',failure_code='stale',updated_at=? WHERE transition_id=? AND connection_name=? AND library_name=? AND item_id=?",
                        (work_id, retried_at, transition_id, connection, library, item),
                    )
                    continue
                if work_state in {"failed", "stale"} or work_stage not in {
                    "extraction",
                    "provider",
                }:
                    self._restart_metadata_policy_work(
                        work_id, work_stage, report_id, retried_at
                    )
                self._connection.execute(
                    "UPDATE metadata_policy_transition_items SET work_id=?,state='pending',failure_code=NULL,updated_at=? WHERE transition_id=? AND connection_name=? AND library_name=? AND item_id=?",
                    (work_id, retried_at, transition_id, connection, library, item),
                )
                # Pending work is claimed only from a processing scan, and an
                # idle claim may already have closed this one (#1369).
                self._connection.execute(
                    "UPDATE review_preparation_scans SET state='processing',updated_at=? "
                    "WHERE scan_id=(SELECT scan_id FROM review_preparation_work WHERE work_id=?) "
                    "AND state IN ('completed','completed_with_failures')",
                    (retried_at, work_id),
                )
            self._refresh_metadata_policy_transition(transition_id, retried_at)
            result = self.metadata_policy_transition()
            if result is None or result.transition_id != transition_id:
                raise MetadataReportError("metadata policy transition is unavailable")
            transaction.commit()
            return result
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "metadata policy transition is unavailable"
            ) from error

    @local_operation
    def acknowledge_metadata_policy_transition(
        self, transition_id: str, acknowledged_at: int
    ) -> PolicyTransition:
        transaction = self._transaction("acknowledge_metadata_policy_transition")
        try:
            transaction.begin()
            updated = self._connection.execute(
                "UPDATE metadata_policy_transitions SET acknowledged=1,updated_at=? WHERE transition_id=? AND state IN ('completed','completed_with_failures')",
                (acknowledged_at, transition_id),
            ).rowcount
            if updated != 1:
                raise MetadataReportError(
                    "metadata policy transition acknowledgement is unavailable"
                )
            result = self.metadata_policy_transition()
            if result is None or result.transition_id != transition_id:
                raise MetadataReportError("metadata policy transition is unavailable")
            transaction.commit()
            return result
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "metadata policy transition is unavailable"
            ) from error

    @local_operation
    def current_unapplied_count(self, library: str) -> int:
        """Count only current, undecided review cases for one configured library."""
        if type(library) is not str or not library:
            raise MetadataReportError("policy preview is unavailable")
        try:
            row = self._connection.execute(
                "SELECT count(*) "
                + _CURRENT_REVIEW_FROM_SQL
                + " WHERE c.library_name=?",
                (library,),
            ).fetchone()
            if row is None or type(row[0]) is not int or row[0] < 0:
                raise MetadataReportError("policy preview is unavailable")
            return row[0]
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError("policy preview is unavailable") from error

    @local_operation
    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]:
        """Return bounded exact current review pairs for a no-effect policy preview."""
        if (
            type(library) is not str
            or not library
            or type(limit) is not int
            or not 1 <= limit <= 20
        ):
            raise MetadataReportError("policy preview is unavailable")
        transaction = self._transaction("current_policy_impact_pairs", deferred=True)
        try:
            transaction.begin()
            total_row = self._connection.execute(
                "SELECT count(*) "
                + _CURRENT_REVIEW_FROM_SQL
                + " WHERE c.library_name=?",
                (library,),
            ).fetchone()
            if total_row is None or type(total_row[0]) is not int or total_row[0] < 0:
                raise MetadataReportError("policy preview is unavailable")
            rows = self._connection.execute(
                "SELECT rc.document,mr.document,p.proposal_set_id,p.report_id,"
                "p.policy_version,p.document "
                + _CURRENT_REVIEW_FROM_SQL
                + " JOIN proposal_sets p ON p.proposal_set_id=rc.proposal_set_id "
                "AND p.report_id=rc.report_id" + " WHERE c.library_name=? "
                "ORDER BY c.connection_name,c.item_id,c.review_case_id LIMIT ?",
                (library, limit),
            ).fetchall()
            pairs_list: list[tuple[MetadataReport, ProposalSet]] = []
            for row in rows:
                case = ReviewCase.from_json(row[0])
                report = MetadataReport.from_json(row[1])
                proposal = ProposalSet.from_json(case.proposal_document)
                case_document, report_document = _validate_review_browse_row(
                    row[:2], self._policy_destinations_for(report, proposal)
                )
                case = ReviewCase.from_json(case_document)
                report = MetadataReport.from_json(report_document)
                proposal = ProposalSet.from_json(case.proposal_document)
                persisted_proposal = ProposalSet.from_json(
                    _validate_proposal_row(row[2:])
                )
                if persisted_proposal.to_json() != proposal.to_json():
                    raise MetadataReportError("policy preview is unavailable")
                pairs_list.append((report, proposal))
            pairs = tuple(pairs_list)
            if len(pairs) > total_row[0]:
                raise MetadataReportError("policy preview is unavailable")
            transaction.commit()
            return total_row[0], pairs
        except MetadataReportError:
            transaction.rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as error:
            transaction.rollback()
            raise MetadataReportError("policy preview is unavailable") from error

    def _policy_destinations_for(
        self, report: MetadataReport, proposal: ProposalSet
    ) -> dict[str, str] | None:
        rows = self._connection.execute(
            "SELECT document FROM metadata_policy_resolutions "
            "WHERE library_name=? AND resolved_policy_id=? ORDER BY generation",
            (report.target.library, proposal.resolved_policy_id),
        ).fetchall()
        if not rows:
            # Generation 1 did not persist resolutions and used the original
            # evaluator semantics, irrespective of its identity-only binding.
            return None
        if any(len(row) != 1 or type(row[0]) is not str for row in rows):
            raise MetadataReportError("proposal policy resolution is unavailable")
        documents = {row[0] for row in rows}
        if len(documents) != 1:
            raise MetadataReportError("proposal policy resolution is unavailable")
        resolved = ResolvedMetadataPolicy.from_data(json.loads(next(iter(documents))))
        if (
            resolved.library != report.target.library
            or resolved.resolved_id != proposal.resolved_policy_id
        ):
            raise MetadataReportError("proposal policy resolution is unavailable")
        return human_review_destinations(resolved)


__all__ = ["SQLiteMetadataPolicyRepository"]
