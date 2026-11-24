"""Durable taste judgements, their append-only verdicts and the judge's count.

Which cover fields are due is one statement over the currentness facts (J1);
the application confirms each against the full policy report (J2). Every
state change is one short transaction, and none is open while a request is
in flight (#1372 PR 4, J5-J7).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Literal, cast

from mechanical_librarian.adapters.persistence.cover_consideration_documents import (
    expand_cover_consideration,
)
from mechanical_librarian.adapters.persistence.cover_considerations import (
    _UNWRITTEN_SQL,
    SQLiteCoverConsiderationRepository,
    taste_switch_candidate,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.contained_reviewer_contract import (
    canonical_json,
)
from mechanical_librarian.application.cover_taste_judge import (
    JudgeDue,
    TasteJudgeVersion,
)
from mechanical_librarian.application.cover_taste_switching import (
    TastePreviewAnswer,
    TastePreviewSwitch,
    TastePreviewWalk,
)
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_VERDICT_SCHEMA,
    TASTE_JUDGE_FAULTS,
    TASTE_JUDGE_MAX_RULES,
    TASTE_MIN_ARTS,
    TasteJudgeFailure,
    TasteProfile,
)

_TASK = "cover_taste_judge"
_DAY_SECONDS = 86_400
# The newest verdict stored for a consideration, under any version.
_LATEST_VERDICT_SQL = (
    "(SELECT json_extract(verdict.document,'$.decision') FROM cover_taste_verdicts "
    "verdict WHERE verdict.consideration_id=consideration.consideration_id "
    "ORDER BY verdict.created_at DESC,verdict.rowid DESC LIMIT 1)"
)
_LATEST_VERDICT_AT_SQL = (
    "(SELECT verdict.created_at FROM cover_taste_verdicts verdict "
    "WHERE verdict.consideration_id=consideration.consideration_id "
    "ORDER BY verdict.created_at DESC,verdict.rowid DESC LIMIT 1)"
)
# J1: a current, ready, choosable comparison of the work's current case that
# no owner has answered, decided or held and no automatic switch answered,
# under a fault-free keep of a readable cover beside another art (a) or a
# fault the stage would cure among at least two arts (b). J6: never attempted
# under this version, and not one whose newest verdict kept the current cover
# or abstained. `_ELIGIBLE_FROM_SQL` is all but J6's attempt condition.
_ELIGIBLE_FROM_SQL = (
    "FROM current_review_packages ml "
    "JOIN current_cover_considerations pointer "
    "ON pointer.review_case_id=ml.review_case_id "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=pointer.consideration_id "
    "JOIN metadata_report_browse_facts facts "
    "ON facts.report_id=consideration.report_id "
    "WHERE ml.review_case_id IS NOT NULL "
    "AND json_extract(consideration.document,'$.policy_reference')=? "
    "AND ml_cover_consideration_current(consideration.currentness_document,?)=1 "
    "AND json_extract(consideration.currentness_document,'$.manual_ready')=1 "
    "AND json_extract(consideration.currentness_document,'$.provider_fetching')=0 "
    "AND typeof(facts.format)='text' "
    "AND COALESCE((SELECT result.has_owner_decision_actions "
    "FROM review_case_field_facts result "
    "WHERE result.review_case_id=ml.review_case_id "
    "AND result.destination='cover'),0)=1 "
    "AND ((json_extract(consideration.currentness_document,'$.cover_stage.outcome')"
    "='keep' AND json_extract(consideration.currentness_document,"
    "'$.cover_stage.reason') IS NULL "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.arts')>=? "
    "AND EXISTS (SELECT 1 FROM json_each(consideration.document,'$.candidates') "
    "current WHERE json_extract(current.value,'$.incumbent')=1 "
    "AND json_type(current.value,'$.preview_sha256')='text' "
    "AND json_type(current.value,'$.transport_sha256')='text')) "
    "OR (json_extract(consideration.currentness_document,'$.cover_stage.outcome') "
    "IN ('replace','suggest') "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.reason') "
    f"IN ({','.join(repr(fault) for fault in TASTE_JUDGE_FAULTS)}) "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.arts')>=?)) "
    "AND NOT EXISTS (SELECT 1 FROM owner_cover_pointers current "
    "WHERE current.connection_name=ml.connection_name "
    "AND current.library_name=ml.library_name AND current.item_id=ml.item_id) "
    "AND NOT EXISTS (SELECT 1 FROM automatic_cover_pointers current "
    "JOIN automatic_cover_selections answered "
    "ON answered.selection_id=current.selection_id "
    "WHERE current.connection_name=ml.connection_name "
    "AND current.library_name=ml.library_name AND current.item_id=ml.item_id "
    "AND answered.consideration_id=consideration.consideration_id "
    # An unwritten taste switch under an older version no longer answers
    # its comparison: it is judged again (#1372 PR 5, K6).
    "AND NOT (json_type(json_extract("
    "answered.document,'$.decision_document'),'$.taste_version') IS 'integer' "
    "AND json_extract(json_extract("
    "answered.document,'$.decision_document'),'$.taste_version')<"
    "(SELECT max(version) FROM cover_taste_profile_versions) "
    + _UNWRITTEN_SQL.replace("selection.decision_id", "answered.decision_id")
    + ")) "
    "AND NOT EXISTS (SELECT 1 FROM human_decisions decided "
    "WHERE decided.review_case_id=ml.review_case_id AND decided.destination='cover') "
    "AND NOT EXISTS (SELECT 1 FROM field_effect_manual_holds held "
    "WHERE held.review_case_id=ml.review_case_id AND held.destination='cover' "
    "AND held.active=1) "
    f"AND COALESCE({_LATEST_VERDICT_SQL},'choose')='choose' "
)
_DUE_FROM_SQL = (
    _ELIGIBLE_FROM_SQL +
    # Never attempted under this version, or its newest attempt is the
    # owner's Try again, still waiting (#1372 PR 4b).
    "AND (NOT EXISTS (SELECT 1 FROM cover_taste_judgements attempted "
    "WHERE attempted.consideration_id=consideration.consideration_id "
    "AND attempted.version=?) OR EXISTS (SELECT 1 FROM cover_taste_judgements "
    "retried WHERE retried.consideration_id=consideration.consideration_id "
    "AND retried.version=? AND retried.state='requested')) "
)
# The oldest `choose` verdicts under the active version, each its
# comparison's newest, on a current comparison no selection answers: what
# taste admission tries, in order after a cursor (#1372 PR 5, K4).
_SWITCH_CANDIDATES_SQL = (
    "SELECT verdict.consideration_id,verdict.verdict_id,verdict.created_at,"
    "verdict.rowid FROM cover_taste_verdicts verdict "
    "JOIN current_cover_considerations pointer "
    "ON pointer.consideration_id=verdict.consideration_id "
    "JOIN current_review_case_lineages lineage "
    "ON lineage.review_case_id=pointer.review_case_id "
    "WHERE verdict.version=(SELECT max(version) FROM cover_taste_profile_versions) "
    "AND (verdict.created_at,verdict.rowid)>(?,?) "
    "AND json_extract(verdict.document,'$.decision')='choose' "
    "AND verdict.rowid=(SELECT newest.rowid FROM cover_taste_verdicts newest "
    "WHERE newest.consideration_id=verdict.consideration_id "
    "ORDER BY newest.created_at DESC,newest.rowid DESC LIMIT 1) "
    "AND NOT EXISTS ("
    + " UNION ALL ".join(
        f"SELECT 1 FROM {kind}_cover_pointers current "
        f"JOIN {kind}_cover_selections answered "
        "ON answered.selection_id=current.selection_id "
        "WHERE current.connection_name=lineage.connection_name "
        "AND current.library_name=lineage.library_name "
        "AND current.item_id=lineage.item_id "
        "AND answered.consideration_id=verdict.consideration_id"
        for kind in ("owner", "automatic")
    )
    + ") "
    "ORDER BY verdict.created_at,verdict.rowid LIMIT ?"
)
# The preview walk (K7): each current comparison's newest verdict under one
# version, oldest first, read through `cover_taste_verdicts_version`. Only a
# work's current case counts: an earlier case keeps its comparison pointer.
_PREVIEW_WALK_SQL = (
    "SELECT verdict.consideration_id,verdict.document,verdict.created_at,"
    "pointer.review_case_id,consideration.document,report.document,"
    "report.document_digest,consideration.document_digest,consideration.report_id "
    "FROM cover_taste_verdicts verdict "
    "JOIN current_cover_considerations pointer "
    "ON pointer.consideration_id=verdict.consideration_id "
    "JOIN current_review_case_lineages lineage "
    "ON lineage.review_case_id=pointer.review_case_id "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=verdict.consideration_id "
    "JOIN metadata_reports report ON report.report_id=consideration.report_id "
    "WHERE verdict.version=? "
    "AND verdict.rowid=(SELECT newest.rowid FROM cover_taste_verdicts newest "
    "WHERE newest.consideration_id=verdict.consideration_id "
    "ORDER BY newest.created_at DESC,newest.rowid DESC LIMIT 1) "
    "ORDER BY verdict.created_at,verdict.rowid LIMIT ?"
)
# The owner's current cover selection for a case's work, if it is the owner's.
_PREVIEW_ANSWER_SQL = (
    "SELECT selection.consideration_id,selection.occurred_at,"
    "json_extract(json_extract(selection.document,'$.decision_document'),"
    "'$.outcome'),json_extract(selection.document,'$.candidate_reference') "
    "FROM current_review_case_lineages lineage "
    "JOIN owner_cover_pointers current "
    "ON current.connection_name=lineage.connection_name "
    "AND current.library_name=lineage.library_name "
    "AND current.item_id=lineage.item_id "
    "JOIN owner_cover_selections selection "
    "ON selection.selection_id=current.selection_id "
    "WHERE lineage.review_case_id=?"
)
# A judgement under one version in flight on a work's current comparison:
# the preview is still checking until it ends (K7). A waiting Try again is
# counted only while its field is due (`_DUE_SQL`): one whose field an owner
# answer has since closed never runs.
_PREVIEW_IN_FLIGHT_SQL = (
    "SELECT 1 FROM cover_taste_judgements judgement "
    "JOIN current_cover_considerations pointer "
    "ON pointer.consideration_id=judgement.consideration_id "
    "JOIN current_review_case_lineages lineage "
    "ON lineage.review_case_id=pointer.review_case_id "
    "WHERE judgement.version=? AND judgement.state='running' "
    "LIMIT 1"
)
_PREVIEW_RERUN_SQL = (
    "SELECT 1 FROM cover_taste_verdicts earlier WHERE earlier.consideration_id=? "
    "AND earlier.version<? AND json_extract(earlier.document,'$.decision')='choose' "
    "LIMIT 1"
)
# The fields still eligible for a check whose newest attempt under a version
# failed: what Try again sends again, so it never clears a count it does not
# send (#1372 PR 4b).
_FAILED_SQL = (
    "SELECT judgement_id FROM cover_taste_judgements "
    "WHERE state='failed' AND judgement_id IN (SELECT "
    "(SELECT newest.judgement_id FROM cover_taste_judgements newest "
    "WHERE newest.consideration_id=consideration.consideration_id "
    "AND newest.version=? ORDER BY newest.rowid DESC LIMIT 1) "
    + _ELIGIBLE_FROM_SQL
    + ")"
)
# The verdict in force at a selection: the newest stored for its comparison
# in an earlier second than the selection (J9). Both times are whole seconds,
# so a verdict stored in the selection's own second cannot be told apart from
# one that arrived after the owner answered; and the owner cannot have read a
# suggestion in the second it was stored.
VERDICT_IN_FORCE_SQL = (
    "(SELECT verdict.document FROM cover_taste_verdicts verdict "
    "WHERE verdict.consideration_id=selection.consideration_id "
    "AND verdict.created_at<selection.occurred_at "
    "ORDER BY verdict.created_at DESC,verdict.rowid DESC LIMIT 1)"
)
# Each current owner selection whose verdict in force chose a cover, and
# whether the owner chose that cover (J9).
_CONFIRMED_SQL = (
    "SELECT count(*),COALESCE(sum(json_extract(selection.document,"
    "'$.candidate_reference')=json_extract(in_force,'$.candidate_reference')),0) "
    f"FROM (SELECT selection.document,{VERDICT_IN_FORCE_SQL} AS in_force "
    "FROM owner_cover_pointers current "
    "JOIN owner_cover_selections selection "
    "ON selection.selection_id=current.selection_id) selection "
    "WHERE json_extract(in_force,'$.decision')='choose'"
)
# One scan for a batch of due fields: pending suggestions first, oldest
# verdict first, then the rest by when each comparison was prepared.
_DUE_SQL = (
    f"SELECT consideration.consideration_id,{_LATEST_VERDICT_SQL} AS latest "
    + _DUE_FROM_SQL
    + "ORDER BY latest IS NULL,"
    f"CASE WHEN latest IS NULL THEN consideration.occurred_at "
    f"ELSE {_LATEST_VERDICT_AT_SQL} END,pointer.review_case_id LIMIT ?"
)
# The same conditions for one field, read again just before it is judged.
_DUE_ONE_SQL = (
    "SELECT consideration.consideration_id,pointer.review_case_id,"
    "consideration.document,facts.format,"
    "json_extract(consideration.currentness_document,'$.cover_stage.outcome'),"
    "json_extract(consideration.currentness_document,'$.cover_stage.reason') "
    + _DUE_FROM_SQL
    + "AND consideration.consideration_id=?"
)


_VERDICT_KEYS = frozenset(
    {
        "candidate_reference",
        "candidate_set_sha256",
        "confidence",
        "consideration_id",
        "current_marker",
        "decision",
        "engine",
        "instruction_sha256",
        "model",
        "packet_sha256",
        "profile_digest",
        "rules",
        "schema_sha256",
        "schema_version",
        "version",
    }
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _verdict_valid(
    value: object, consideration_id: str, version: int, numbers: frozenset[int]
) -> bool:
    if type(value) is not dict or set(value) != _VERDICT_KEYS:
        return False
    rules = value["rules"]
    candidate = value["candidate_reference"]
    return (
        value["schema_version"] == COVER_TASTE_VERDICT_SCHEMA
        and value["consideration_id"] == consideration_id
        and value["version"] == version
        and value["current_marker"] is True
        and value["decision"] in {"keep_current", "choose", "abstain"}
        and (
            type(candidate) is str and bool(candidate)
            if value["decision"] == "choose"
            else candidate is None
        )
        and type(value["confidence"]) is int
        and 0 <= value["confidence"] <= 100
        and type(rules) is list
        and 1 <= len(rules) <= TASTE_JUDGE_MAX_RULES
        and all(type(rule) is int and rule in numbers for rule in rules)
        and len(set(rules)) == len(rules)
        and value["engine"] in {"codex", "openrouter"}
        and type(value["model"]) is str
        and 1 <= len(value["model"]) <= 256
        and all(
            type(value[key]) is str and _DIGEST.fullmatch(value[key]) is not None
            for key in (
                "candidate_set_sha256",
                "instruction_sha256",
                "packet_sha256",
                "profile_digest",
                "schema_sha256",
            )
        )
    )


def validate_cover_taste_verdicts(connection: sqlite3.Connection) -> None:
    """Refuse a stored verdict no judgement could have written (J4, J5): the
    canonical closed document of a succeeded judgement of the same field and
    version, naming only rule numbers that version holds."""
    numbers: dict[int, frozenset[int]] = {}
    for version, document in connection.execute(
        "SELECT version,document FROM cover_taste_profile_versions"
    ):
        numbers[version] = frozenset(
            rule.number
            for rule in TasteProfile.from_rules(json.loads(document)["rules"]).rules
        )
    for consideration_id, version, document, judged in connection.execute(
        "SELECT verdict.consideration_id,verdict.version,verdict.document,"
        "judgement.state='succeeded' AND judgement.consideration_id="
        "verdict.consideration_id AND judgement.version=verdict.version "
        "FROM cover_taste_verdicts verdict LEFT JOIN cover_taste_judgements "
        "judgement ON judgement.judgement_id=verdict.judgement_id"
    ):
        value = json.loads(document)
        if (
            judged != 1
            or version not in numbers
            or not _verdict_valid(value, consideration_id, version, numbers[version])
            or canonical_json(value) != document
        ):
            raise ValueError("taste verdict is invalid")


def _day(now: int) -> int:
    return now // _DAY_SECONDS


def _failed(
    connection: sqlite3.Connection,
    authority: tuple[str | None, str | None],
    version: int,
) -> list[str]:
    policy, replay = authority
    if policy is None or replay is None:
        return []
    return [
        row[0]
        for row in connection.execute(
            _FAILED_SQL, (version, policy, replay, TASTE_MIN_ARTS, TASTE_MIN_ARTS)
        )
    ]


def taste_judge_facts(
    connection: sqlite3.Connection,
    authority: tuple[str | None, str | None],
    now: int,
    daily_limit: int,
) -> dict[str, object]:
    """What Covers → Taste profile says of the judge: today's stop and when it
    lifts, the works whose check failed under the version in use, and the
    suggestions the owner confirmed (#1372 PR 4b, J9)."""
    row = connection.execute(
        "SELECT requests,stopped FROM reviewer_task_requests WHERE task=? AND day=?",
        (_TASK, _day(now)),
    ).fetchone()
    stopped = (
        None
        if row is None
        else "unknown_charge"
        if row[1] is not None
        else "ceiling"
        if row[0] >= daily_limit
        else None
    )
    version = connection.execute(
        "SELECT max(version) FROM cover_taste_profile_versions"
    ).fetchone()[0]
    suggested, confirmed = connection.execute(_CONFIRMED_SQL).fetchone()
    return {
        "stopped": stopped,
        "retry_after": None if stopped is None else (_day(now) + 1) * _DAY_SECONDS,
        "failed": 0
        if version is None
        else len(_failed(connection, authority, version)),
        "confirmed": {"confirmed": confirmed, "suggested": suggested},
    }


def _preview_answer(
    connection: sqlite3.Connection,
    review_case_id: str,
    consideration_id: str,
    verdict_at: int,
    reference: str,
) -> tuple[bool, TastePreviewAnswer | None]:
    """Whether a switch stays in the preview, and the owner's answer to it.

    An owner answer to that comparison since the verdict shows as itself
    when it took the switch's cover or kept the current one; any other owner
    answer takes the switch out of the preview (K7).
    """
    row = connection.execute(_PREVIEW_ANSWER_SQL, (review_case_id,)).fetchone()
    if row is None:
        return True, None
    answered, occurred_at, outcome, candidate = row
    if answered == consideration_id and occurred_at >= verdict_at:
        if outcome == "accept" and candidate == reference:
            return True, "looks_right"
        if outcome == "keep_current":
            return True, "kept_current"
    return False, None


def taste_preview_walk(
    connection: sqlite3.Connection,
    authority: tuple[str | None, str | None],
    protection: str,
    checked_limit: int,
    switch_limit: int,
) -> TastePreviewWalk:
    """The switches the version in use would make under `protection`, among
    up to `checked_limit` covers it has judged, stopping at `switch_limit`
    (K7). Each current comparison's newest verdict under the version is one
    checked cover; a switch is a `choose` meeting K4 gates 4-6. Posture,
    record, room and holds are not asked (L5)."""
    profile = connection.execute(
        "SELECT version,document FROM cover_taste_profile_versions "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if profile is None:
        return TastePreviewWalk(None, 0, (), False)
    version = cast(int, profile[0])
    rules = {
        rule.number: rule
        for rule in TasteProfile.from_rules(json.loads(profile[1])["rules"]).rules
    }
    checked = 0
    switches: list[TastePreviewSwitch] = []
    for row in connection.execute(_PREVIEW_WALK_SQL, (version, checked_limit)):
        if len(switches) >= switch_limit:
            break
        checked += 1
        consideration_id, verdict_document, verdict_at, case_id = row[:4]
        value = json.loads(expand_cover_consideration(*row[4:]))
        try:
            policy_report = json.loads(value["policy_report_document"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        if type(policy_report) is not dict:
            continue
        candidate = taste_switch_candidate(
            value, policy_report, verdict_document, protection
        )
        if candidate is None:
            continue
        verdict = json.loads(verdict_document)
        if any(number not in rules for number in verdict["rules"]):
            continue
        reference = cast(str, candidate["reference"])
        shown, answer = _preview_answer(
            connection, case_id, consideration_id, verdict_at, reference
        )
        if not shown:
            continue
        switches.append(
            TastePreviewSwitch(
                review_case_id=case_id,
                consideration_id=consideration_id,
                report_document=cast(str, value["report_document"]),
                reference=reference,
                preview_digest=cast("str | None", candidate.get("preview_sha256")),
                width=cast("int | None", candidate.get("width")),
                height=cast("int | None", candidate.get("height")),
                reason=cast("str | None", policy_report["stage"]["reason"]),
                rules=tuple(
                    (number, rules[number].section, rules[number].text)
                    for number in verdict["rules"]
                ),
                answer=answer,
                rerun=connection.execute(
                    _PREVIEW_RERUN_SQL, (consideration_id, version)
                ).fetchone()
                is not None,
            )
        )
    policy, replay = authority
    due = (
        connection.execute(_PREVIEW_IN_FLIGHT_SQL, (version,)).fetchone() is not None
    ) or (
        policy is not None
        and replay is not None
        and connection.execute(
            _DUE_SQL,
            (policy, replay, TASTE_MIN_ARTS, TASTE_MIN_ARTS, version, version, 1),
        ).fetchone()
        is not None
    )
    return TastePreviewWalk(version, checked, tuple(switches), due)


def request_taste_judge_retries(
    connection: sqlite3.Connection,
    authority: tuple[str | None, str | None],
    version: int,
    now: int,
) -> bool:
    """Ask again for each failed check under `version`; False only when it is
    not the version in use. With nothing failed, such as a repeat whose first
    answer was lost, it asks for nothing and still succeeds. The caller holds
    the transaction; the retries are counted only when sent (J7)."""
    newest = connection.execute(
        "SELECT max(version) FROM cover_taste_profile_versions"
    ).fetchone()[0]
    if newest != version:
        return False
    for judgement_id in _failed(connection, authority, version):
        connection.execute(
            "INSERT INTO cover_taste_judgements SELECT ?,consideration_id,version,"
            "'retry',judgement_id,'requested',NULL,NULL,?,?,? "
            "FROM cover_taste_judgements WHERE judgement_id=?",
            (
                "taste_judgement_" + uuid.uuid4().hex,
                _day(now),
                now,
                now,
                judgement_id,
            ),
        )
    return True


class SQLiteCoverTasteJudgeRepository(ConnectionScoped):
    """`cover_read` yields the live cover policy and replay references with
    provider fetching captured once, so the due statements read currentness
    exactly as Covers does without a recovery-store read per row."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        cover_read: Callable[[], AbstractContextManager[tuple[str | None, str | None]]],
        switches: SQLiteCoverConsiderationRepository,
        taste_switching: Callable[[], bool],
    ) -> None:
        super().__init__(connection)
        self._cover_read = cover_read
        self._switches = switches
        self._taste_switching = taste_switching

    @local_operation
    def active(self) -> TasteJudgeVersion | None:
        row = self._connection.execute(
            "SELECT version,created_at,document FROM cover_taste_profile_versions "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return TasteJudgeVersion(
            row[0], row[1], TasteProfile.from_rules(json.loads(row[2])["rules"])
        )

    @local_operation
    def building(self) -> bool:
        """Whether a taste profile build is waiting or in flight."""
        return (
            self._connection.execute(
                "SELECT 1 FROM cover_taste_builds "
                "WHERE state IN ('requested','running') LIMIT 1"
            ).fetchone()
            is not None
        )

    @local_operation
    def refusal(self, now: int, daily_limit: int) -> bool:
        """Whether today's judge requests are at the ceiling or stopped."""
        return self._refused(now, daily_limit)

    def _refused(self, now: int, daily_limit: int) -> bool:
        row = self._connection.execute(
            "SELECT requests,stopped FROM reviewer_task_requests WHERE task=? AND day=?",
            (_TASK, _day(now)),
        ).fetchone()
        return row is not None and (row[1] is not None or row[0] >= daily_limit)

    def due(self, version: int, limit: int) -> tuple[str, ...]:
        """Up to `limit` consideration ids due under `version`, in order (J1, J6)."""
        with self._cover_read() as (policy, replay):
            if policy is None or replay is None:
                return ()
            return self._due(policy, replay, version, limit)

    @local_operation
    def _due(
        self, policy: str, replay: str, version: int, limit: int
    ) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self._connection.execute(
                _DUE_SQL,
                (
                    policy,
                    replay,
                    TASTE_MIN_ARTS,
                    TASTE_MIN_ARTS,
                    version,
                    version,
                    limit,
                ),
            )
        )

    def due_one(self, consideration_id: str, version: int) -> JudgeDue | None:
        """The field of `consideration_id` when it is still due under `version`."""
        with self._cover_read() as (policy, replay):
            if policy is None or replay is None:
                return None
            return self._due_one(policy, replay, consideration_id, version)

    @local_operation
    def _due_one(
        self, policy: str, replay: str, consideration_id: str, version: int
    ) -> JudgeDue | None:
        row = self._connection.execute(
            _DUE_ONE_SQL,
            (
                policy,
                replay,
                TASTE_MIN_ARTS,
                TASTE_MIN_ARTS,
                version,
                version,
                consideration_id,
            ),
        ).fetchone()
        if row is None:
            return None
        consideration, case_id, document, file_format, outcome, reason = row
        return JudgeDue(
            consideration,
            case_id,
            cast("dict[str, object]", json.loads(document)),
            cast(str, file_format),
            cast(str, outcome),
            cast("str | None", reason),
        )

    def _insert(
        self,
        consideration_id: str,
        version: int,
        state: Literal["running", "failed", "skipped"],
        failure: TasteJudgeFailure | None,
        model: str | None,
        now: int,
    ) -> str:
        """Start the pair's waiting Try again in `state`, or else insert its
        cadence attempt; the pair index refuses a second cadence attempt."""
        retry = self._connection.execute(
            "SELECT judgement_id FROM cover_taste_judgements WHERE consideration_id=? "
            "AND version=? AND state='requested'",
            (consideration_id, version),
        ).fetchone()
        if retry is not None:
            self._connection.execute(
                "UPDATE cover_taste_judgements SET state=?,failure=?,model=?,day=?,"
                "updated_at=max(?,requested_at) WHERE judgement_id=?",
                (state, failure, model, _day(now), now, retry[0]),
            )
            return cast(str, retry[0])
        judgement_id = "taste_judgement_" + uuid.uuid4().hex
        self._connection.execute(
            "INSERT INTO cover_taste_judgements VALUES(?,?,?,'cadence',NULL,?,?,?,?,?,?)",
            (
                judgement_id,
                consideration_id,
                version,
                state,
                failure,
                model,
                _day(now),
                now,
                now,
            ),
        )
        return judgement_id

    @local_operation
    def record(
        self,
        consideration_id: str,
        version: int,
        now: int,
        failure: Literal["unavailable"] | None = None,
    ) -> None:
        """Record a field sent nothing for: `skipped` when the full report
        makes it ineligible, else `failed` with `failure`; never counted."""
        transaction = self._transaction("taste_judge_record")
        try:
            transaction.begin()
            self._insert(
                consideration_id,
                version,
                "skipped" if failure is None else "failed",
                failure,
                None,
                now,
            )
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def claim(
        self,
        consideration_id: str,
        version: int,
        now: int,
        model: str,
        daily_limit: int,
    ) -> str | None:
        """Insert the attempt `running` and count it before dispatch (J7), or
        None at the ceiling, on a stopped day or for a pair already tried."""
        transaction = self._transaction("taste_judge_claim")
        try:
            transaction.begin()
            if self._refused(now, daily_limit):
                transaction.commit()
                return None
            try:
                judgement_id = self._insert(
                    consideration_id, version, "running", None, model, now
                )
            except sqlite3.IntegrityError:
                transaction.commit()
                return None
            self._connection.execute(
                "INSERT INTO reviewer_task_requests VALUES(?,?,1,NULL) "
                "ON CONFLICT(task,day) DO UPDATE SET requests=requests+1",
                (_TASK, _day(now)),
            )
            transaction.commit()
            return judgement_id
        except BaseException:
            transaction.rollback()
            raise

    def _stop_day(self, now: int) -> None:
        self._connection.execute(
            "INSERT INTO reviewer_task_requests VALUES(?,?,0,'unknown_charge') "
            "ON CONFLICT(task,day) DO UPDATE SET stopped='unknown_charge'",
            (_TASK, _day(now)),
        )

    @local_operation
    def fail(
        self,
        judgement_id: str,
        now: int,
        failure: TasteJudgeFailure,
        *,
        stop_day: bool = False,
    ) -> None:
        transaction = self._transaction("taste_judge_fail")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE cover_taste_judgements SET state='failed',failure=?,"
                "updated_at=max(?,updated_at) "
                "WHERE judgement_id=? AND state IN ('requested','running')",
                (failure, now, judgement_id),
            )
            if stop_day:
                self._stop_day(now)
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def succeed(
        self,
        judgement_id: str,
        now: int,
        document: str,
        *,
        stop_day: bool = False,
    ) -> bool:
        """Store the admitted verdict for a running judgement; False when it
        was no longer running, so nothing is stored."""
        transaction = self._transaction("taste_judge_succeed")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT consideration_id,version FROM cover_taste_judgements "
                "WHERE judgement_id=? AND state='running'",
                (judgement_id,),
            ).fetchone()
            if row is not None:
                self._connection.execute(
                    "INSERT INTO cover_taste_verdicts VALUES(?,?,?,?,?,?)",
                    (
                        "taste_verdict_" + uuid.uuid4().hex,
                        judgement_id,
                        row[0],
                        row[1],
                        document,
                        now,
                    ),
                )
                self._connection.execute(
                    "UPDATE cover_taste_judgements SET state='succeeded',"
                    "updated_at=max(?,updated_at) WHERE judgement_id=?",
                    (now, judgement_id),
                )
            if stop_day:
                self._stop_day(now)
            transaction.commit()
            return row is not None
        except BaseException:
            transaction.rollback()
            raise

    def taste_switching(self) -> bool:
        """Whether Switching is Automatic in this generation (K3)."""
        return self._taste_switching()

    @local_operation
    def retire_switches(self, now: int, limit: int) -> int:
        """Revoke up to `limit` unwritten taste switches nothing stands
        behind any more, in one transaction (K6)."""
        transaction = self._transaction("taste_switch_retire")
        try:
            transaction.begin()
            retired = self._switches._retire_unwritten_taste_covers(now, limit)
            transaction.commit()
            return retired
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def switch_candidates(
        self, after: tuple[int, int], limit: int
    ) -> tuple[tuple[str, str, int, int], ...]:
        """Up to `limit` (consideration, verdict, created_at, rowid) taste
        admission may try, oldest first after `after` (K4)."""
        return tuple(
            (row[0], row[1], row[2], row[3])
            for row in self._connection.execute(_SWITCH_CANDIDATES_SQL, (*after, limit))
        )

    @local_operation
    def admit_switch(self, consideration_id: str, verdict_id: str, now: int) -> bool:
        """Admit one taste verdict as an automatic decision, or nothing, in
        one short transaction (K4)."""
        transaction = self._transaction("taste_switch_admit")
        try:
            transaction.begin()
            admitted = self._switches._admit_taste_automatic_cover(
                consideration_id, verdict_id, now
            )
            transaction.commit()
            return admitted
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def interrupt_recovered(self, now: int) -> None:
        """A judgement in flight when the process stopped ends `interrupted`;
        a Try again still waiting was never sent, so it stays waiting."""
        transaction = self._transaction("taste_judge_interrupt")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE cover_taste_judgements SET state='failed',"
                "failure='interrupted',updated_at=max(updated_at,?) "
                "WHERE state='running'",
                (now,),
            )
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise


__all__ = [
    "VERDICT_IN_FORCE_SQL",
    "SQLiteCoverTasteJudgeRepository",
    "request_taste_judge_retries",
    "taste_judge_facts",
    "validate_cover_taste_verdicts",
]
