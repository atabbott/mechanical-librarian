"""Durable taste profile builds, append-only versions and the daily request count.

Every state change is one short transaction; nothing here holds a transaction
while a request is in flight (#1372 S8, S9).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Literal, cast

from mechanical_librarian.adapters.persistence.cover_taste_judge import (
    VERDICT_IN_FORCE_SQL,
    request_taste_judge_retries,
    taste_judge_facts,
    taste_preview_walk,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.application.cover_taste_inputs import (
    TasteCandidateSource,
    TasteDecisionSource,
)
from mechanical_librarian.application.cover_taste_switching import (
    PREVIEW_CHECKED,
    PREVIEW_SWITCHES,
    TastePreviewWalk,
)
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_PROFILE_SCHEMA,
    TASTE_FIRST_BUILD_DECISIONS,
    TASTE_MIN_ARTS,
    TASTE_REBUILD_DECISIONS,
    TASTE_SCHEDULED_BUILDS_PER_DAY,
    CoverTasteError,
    TasteBuildFailure,
    TasteEditAction,
    TasteProfile,
    TasteRule,
    TasteSection,
    apply_taste_edit,
    merge_taste_rebuild,
    taste_budget,
    taste_removed_valid,
)
from mechanical_librarian.domain.metadata_report import AUDIO_FILE_FORMATS

_TASK = "cover_taste_build"
_DAY_SECONDS = 86_400
VERSIONS_SHOWN = 50
# S2: the newest owner selection per item, naming a previewed cover that is
# not a retained original, on a consideration offering at least two arts. The
# chosen cover must be one the build sends (a previewed `candidate`, never a
# larger copy) and the report must name a file format, so the count shown and
# the 25/10 thresholds read exactly the decisions `_decision_source` keeps.
_ELIGIBLE = (
    "FROM owner_cover_pointers current "
    "JOIN owner_cover_selections selection "
    "ON selection.selection_id=current.selection_id "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=selection.consideration_id "
    "JOIN metadata_report_browse_facts facts "
    "ON facts.report_id=consideration.report_id "
    "WHERE json_extract(selection.document,'$.preview_sha256') IS NOT NULL "
    "AND json_extract(selection.document,'$.candidate_kind') IS NOT 'retained' "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.arts')>=? "
    "AND typeof(facts.format)='text' "
    "AND EXISTS (SELECT 1 FROM json_each(consideration.document,'$.candidates') "
    "offered WHERE json_extract(offered.value,'$.reference')="
    "json_extract(selection.document,'$.candidate_reference') "
    "AND json_extract(offered.value,'$.candidate_kind')='candidate' "
    "AND json_type(offered.value,'$.preview_sha256')='text' "
    "AND json_type(offered.value,'$.transport_sha256')='text' "
    "AND json_type(offered.value,'$.width')='integer' "
    "AND json_type(offered.value,'$.height')='integer')"
)
_RECORD_KEYS = frozenset(
    {
        "corrections",
        "dropped",
        "eligible",
        "images",
        "included",
        "input_sha256",
        "truncated",
    }
)


def _day(now: int) -> int:
    return now // _DAY_SECONDS


# The keys a version's document closes over, and those an edit version
# copies from its predecessor so "Built" and "Decisions" stay the build's.
_VERSION_KEYS = frozenset(
    {
        "build_id",
        "built_at",
        "input",
        "left_out",
        "model",
        "removed",
        "rules",
        "schema_version",
        "source",
        "version",
    }
)
_INHERITED = ("build_id", "built_at", "input", "left_out", "model")


def validate_cover_taste_versions(connection: sqlite3.Connection) -> None:
    """Refuse a stored version that no build or owner edit could have written."""
    previous: dict[str, object] | None = None
    for version, build_id, created_at, document in connection.execute(
        "SELECT version,build_id,created_at,document "
        "FROM cover_taste_profile_versions ORDER BY version"
    ):
        value = json.loads(document)
        if (
            type(value) is not dict
            or set(value) != _VERSION_KEYS
            or value["schema_version"] != COVER_TASTE_PROFILE_SCHEMA
            or value["version"] != version
            or value["source"] not in {"build", "edit"}
            or type(value["input"]) is not dict
            or set(value["input"]) != _RECORD_KEYS
            or type(value["left_out"]) is not int
            or value["left_out"] < 0
            or type(value["built_at"]) is not int
            or type(value["removed"]) is not list
            or not taste_removed_valid(value["removed"])
            or canonical_json(value) != document
        ):
            raise ValueError("taste profile version is invalid")
        profile = TasteProfile.from_rules(value["rules"])
        if value["source"] == "build":
            # A build numbers its rules 1..N in section order (S2, S7).
            if (
                value["build_id"] != build_id
                or value["built_at"] != created_at
                or [rule.number for rule in profile.rules]
                != list(range(1, len(profile.rules) + 1))
            ):
                raise ValueError("taste profile version is invalid")
        elif previous is None or any(value[key] != previous[key] for key in _INHERITED):
            # An edit's base is always version - 1, never a first version.
            raise ValueError("taste profile version is invalid")
        previous = value


class SQLiteCoverTasteRepository(ConnectionScoped):
    """Each update keeps `updated_at` from going back, so a wall clock that
    steps backwards never fails a build's own write. `cover_read` yields the
    live cover authority, so a failed check counts only while it could be
    sent again (#1372 PR 4b)."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        cover_read: Callable[[], AbstractContextManager[tuple[str | None, str | None]]],
    ) -> None:
        super().__init__(connection)
        self._cover_read = cover_read

    @local_operation
    def eligible_count(self) -> int:
        return cast(
            int,
            self._connection.execute(
                f"SELECT count(*) {_ELIGIBLE}", (TASTE_MIN_ARTS,)
            ).fetchone()[0],
        )

    def _requests_today(self, now: int) -> tuple[int, str | None]:
        row = self._connection.execute(
            "SELECT requests,stopped FROM reviewer_task_requests WHERE task=? AND day=?",
            (_TASK, _day(now)),
        ).fetchone()
        return (0, None) if row is None else (row[0], row[1])

    def _refusal(self, now: int, daily_limit: int) -> TasteBuildFailure | None:
        requests, stopped = self._requests_today(now)
        if stopped is not None:
            return "unknown_charge"
        return "ceiling" if requests >= daily_limit else None

    def _insert(
        self,
        now: int,
        trigger: Literal["cadence", "retry"],
        retry_of: str | None,
        daily_limit: int,
    ) -> str:
        build_id = "taste_build_" + uuid.uuid4().hex
        watermark = self._connection.execute(
            "SELECT coalesce(max(rowid),0) FROM owner_cover_selections"
        ).fetchone()[0]
        refusal = self._refusal(now, daily_limit)
        self._connection.execute(
            "INSERT INTO cover_taste_builds VALUES(?,?,?,?,?,NULL,?,?,?,?)",
            (
                build_id,
                trigger,
                retry_of,
                "requested" if refusal is None else "failed",
                refusal,
                _day(now),
                watermark,
                now,
                now,
            ),
        )
        return build_id

    @local_operation
    def schedule(self, now: int, configured: bool, daily_limit: int) -> bool:
        """Request a cadence build when one is due (#1372 S9); say if one was."""
        if not configured:
            return False
        transaction = self._transaction("taste_schedule")
        try:
            transaction.begin()
            last = self._connection.execute(
                "SELECT state FROM cover_taste_builds ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            # A build that failed only because no model was chosen sent nothing,
            # so new choices count from the last build that was not one.
            counted = self._connection.execute(
                "SELECT selection_watermark FROM cover_taste_builds "
                "WHERE failure IS NOT 'unconfigured' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            scheduled = self._connection.execute(
                "SELECT count(*) FROM cover_taste_builds WHERE trigger='cadence' AND day=?",
                (_day(now),),
            ).fetchone()[0]
            # S9: kept rules that leave no rule or word to write make a
            # cadence build pointless, though it would still cost a request.
            budget = taste_budget(tuple(rule.text for rule in self._newest()[1]))
            if (
                (last is not None and last[0] in {"requested", "running"})
                or scheduled >= TASTE_SCHEDULED_BUILDS_PER_DAY
                or budget["rules"] <= 0
                or budget["words"] <= 0
            ):
                transaction.commit()
                return False
            total = self._connection.execute(
                f"SELECT count(*) {_ELIGIBLE}", (TASTE_MIN_ARTS,)
            ).fetchone()[0]
            new = (
                total
                if counted is None
                else self._connection.execute(
                    f"SELECT count(*) {_ELIGIBLE} AND selection.rowid>?",
                    (TASTE_MIN_ARTS, counted[0]),
                ).fetchone()[0]
            )
            due = total >= TASTE_FIRST_BUILD_DECISIONS and (
                counted is None or new >= TASTE_REBUILD_DECISIONS
            )
            if due:
                self._insert(now, "cadence", None, daily_limit)
            transaction.commit()
            return due
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def request_retry(self, build_id: str, now: int, daily_limit: int) -> str | None:
        """Retry the latest failed build once; a repeat returns the same retry."""
        transaction = self._transaction("taste_retry")
        try:
            transaction.begin()
            existing = self._connection.execute(
                "SELECT build_id FROM cover_taste_builds WHERE retry_of=?", (build_id,)
            ).fetchone()
            if existing is not None:
                transaction.commit()
                return cast(str, existing[0])
            last = self._connection.execute(
                "SELECT build_id,state FROM cover_taste_builds ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if last is None or last[0] != build_id or last[1] != "failed":
                transaction.commit()
                return None
            retry = self._insert(now, "retry", build_id, daily_limit)
            transaction.commit()
            return retry
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def pending(self) -> str | None:
        row = self._connection.execute(
            "SELECT build_id FROM cover_taste_builds WHERE state='requested' "
            "ORDER BY rowid LIMIT 1"
        ).fetchone()
        return None if row is None else cast(str, row[0])

    @local_operation
    def claim(
        self, build_id: str, now: int, model: str, daily_limit: int
    ) -> TasteBuildFailure | None:
        """Start one build and count its request before dispatch, or refuse it."""
        transaction = self._transaction("taste_claim")
        try:
            transaction.begin()
            refusal = self._refusal(now, daily_limit)
            changed = self._connection.execute(
                "UPDATE cover_taste_builds SET state=?,failure=?,model=?,"
                "updated_at=max(?,requested_at) WHERE build_id=? AND state='requested'",
                (
                    "running" if refusal is None else "failed",
                    refusal,
                    model,
                    now,
                    build_id,
                ),
            ).rowcount
            if changed != 1:
                transaction.commit()
                return "interrupted"
            if refusal is None:
                self._connection.execute(
                    "INSERT INTO reviewer_task_requests VALUES(?,?,1,NULL) "
                    "ON CONFLICT(task,day) DO UPDATE SET requests=requests+1",
                    (_TASK, _day(now)),
                )
            transaction.commit()
            return refusal
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def fail(
        self,
        build_id: str,
        now: int,
        failure: TasteBuildFailure,
        *,
        stop_day: bool = False,
    ) -> None:
        transaction = self._transaction("taste_fail")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE cover_taste_builds SET state='failed',failure=?,"
                "updated_at=max(?,updated_at) "
                "WHERE build_id=? AND state IN ('requested','running')",
                (failure, now, build_id),
            )
            if stop_day:
                self._stop_day(now)
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise

    def _stop_day(self, now: int) -> None:
        self._connection.execute(
            "INSERT INTO reviewer_task_requests VALUES(?,?,0,'unknown_charge') "
            "ON CONFLICT(task,day) DO UPDATE SET stopped='unknown_charge'",
            (_TASK, _day(now)),
        )

    def _newest(
        self,
    ) -> tuple[dict[str, object] | None, tuple[TasteRule, ...], tuple[str, ...]]:
        """The newest version's document, its kept rules and its removed texts."""
        row = self._connection.execute(
            "SELECT document FROM cover_taste_profile_versions "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None, (), ()
        document = cast("dict[str, object]", json.loads(row[0]))
        return (
            document,
            TasteProfile.from_rules(document["rules"]).kept(),
            tuple(cast("list[str]", document["removed"])),
        )

    def _append(
        self,
        build_id: str | None,
        now: int,
        document: dict[str, object],
    ) -> int:
        version = cast(
            int,
            self._connection.execute(
                "SELECT coalesce(max(version),0) FROM cover_taste_profile_versions"
            ).fetchone()[0]
            + 1,
        )
        self._connection.execute(
            "INSERT INTO cover_taste_profile_versions VALUES(?,?,?,?)",
            (
                version,
                build_id,
                now,
                canonical_json(
                    {
                        **document,
                        "schema_version": COVER_TASTE_PROFILE_SCHEMA,
                        "version": version,
                    }
                ),
            ),
        )
        return version

    @local_operation
    def kept(self) -> tuple[tuple[TasteRule, ...], tuple[str, ...]]:
        """The newest version's owner and edited rules and its removed texts,
        which a build sends as `kept` and `left_out` (S8)."""
        _document, kept, removed = self._newest()
        return kept, removed

    @local_operation
    def succeed(
        self,
        build_id: str,
        now: int,
        built: TasteProfile,
        record: dict[str, object],
        *,
        failure: TasteBuildFailure,
        stop_day: bool = False,
    ) -> int | TasteBuildFailure | None:
        """Append the next version for a running build, merged against the
        newest version as it stands now, since an owner edit may have landed
        while the build ran (S7). A merge over a bound records `failure` and
        returns it; None when the build was not running."""
        if set(record) != _RECORD_KEYS:
            raise ValueError("taste input record is invalid")
        transaction = self._transaction("taste_succeed")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT model FROM cover_taste_builds WHERE build_id=? AND state='running'",
                (build_id,),
            ).fetchone()
            if row is None:
                transaction.commit()
                return None
            document, _kept, removed = self._newest()
            try:
                profile, left_out = merge_taste_rebuild(
                    None
                    if document is None
                    else TasteProfile.from_rules(document["rules"]),
                    removed,
                    built,
                )
            except CoverTasteError:
                self._connection.execute(
                    "UPDATE cover_taste_builds SET state='failed',failure=?,"
                    "updated_at=max(?,updated_at) WHERE build_id=?",
                    (failure, now, build_id),
                )
                if stop_day:
                    self._stop_day(now)
                transaction.commit()
                return failure
            version = self._append(
                build_id,
                now,
                {
                    "build_id": build_id,
                    "built_at": now,
                    "input": record,
                    "left_out": left_out,
                    "model": row[0],
                    "removed": list(removed),
                    "rules": profile.rules_data(),
                    "source": "build",
                },
            )
            self._connection.execute(
                "UPDATE cover_taste_builds SET state='succeeded',"
                "updated_at=max(?,updated_at) WHERE build_id=?",
                (now, build_id),
            )
            if stop_day:
                self._stop_day(now)
            transaction.commit()
            return version
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def edit(
        self,
        version: int,
        action: TasteEditAction,
        number: int | None,
        section: TasteSection | None,
        text: str | None,
        now: int,
    ) -> int | None:
        """Apply one owner edit to the newest version, named by `version`, as
        the next version; None when the text is unchanged. Raises
        `TasteEditError` with its closed code, writing nothing (S10)."""
        transaction = self._transaction("taste_edit")
        try:
            transaction.begin()
            document, _kept, removed = self._newest()
            current = (
                None
                if document is None or document["version"] != version
                else TasteProfile.from_rules(document["rules"])
            )
            edited = apply_taste_edit(current, removed, action, number, section, text)
            if edited is None:
                transaction.commit()
                return None
            assert document is not None
            profile, listed = edited
            written = self._append(
                None,
                now,
                {
                    **{key: document[key] for key in _INHERITED},
                    "removed": list(listed),
                    "rules": profile.rules_data(),
                    "source": "edit",
                },
            )
            transaction.commit()
            return written
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def interrupt_recovered(self, now: int) -> None:
        """A build running when the process stopped ends `interrupted`, never resent."""
        transaction = self._transaction("taste_interrupt")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE cover_taste_builds SET state='failed',failure='interrupted',"
                "updated_at=max(updated_at,?) WHERE state='running'",
                (now,),
            )
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def decisions(self) -> tuple[TasteDecisionSource, ...]:
        """Each eligible decision in the order recorded, and whether it
        corrected the suggestion in force when it was made (J9)."""
        rows = self._connection.execute(
            "SELECT selection.selection_id,selection.document,consideration.document,"
            f"facts.format,{VERDICT_IN_FORCE_SQL} {_ELIGIBLE} ORDER BY selection.rowid",
            (TASTE_MIN_ARTS,),
        ).fetchall()
        sources = (_decision_source(*row) for row in rows)
        return tuple(source for source in sources if source is not None)

    def judge_read(self, now: int, daily_limit: int) -> dict[str, object]:
        """The judge's facts Covers → Taste profile shows (#1372 PR 4b)."""
        with self._cover_read() as authority:
            return self._judge_read(authority, now, daily_limit)

    @local_operation
    def _judge_read(
        self, authority: tuple[str | None, str | None], now: int, daily_limit: int
    ) -> dict[str, object]:
        return taste_judge_facts(self._connection, authority, now, daily_limit)

    def preview(self, protection: str) -> TastePreviewWalk:
        """The switches the version in use would make under `protection`,
        among the covers it has judged so far (K7); a read only."""
        with self._cover_read() as authority:
            return self._preview(authority, protection)

    @local_operation
    def _preview(
        self, authority: tuple[str | None, str | None], protection: str
    ) -> TastePreviewWalk:
        return taste_preview_walk(
            self._connection, authority, protection, PREVIEW_CHECKED, PREVIEW_SWITCHES
        )

    def request_judge_retry(self, version: int, now: int) -> bool:
        """The owner's Try again for every failed check under `version`;
        False only when `version` is no longer in use."""
        with self._cover_read() as authority:
            return self._request_judge_retry(authority, version, now)

    @local_operation
    def _request_judge_retry(
        self, authority: tuple[str | None, str | None], version: int, now: int
    ) -> bool:
        transaction = self._transaction("taste_judge_retry")
        try:
            transaction.begin()
            requested = request_taste_judge_retries(
                self._connection, authority, version, now
            )
            transaction.commit()
            return requested
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def read(self) -> dict[str, object]:
        """The facts Covers → Taste profile shows; never any operational output."""
        build = self._connection.execute(
            "SELECT build_id,state,failure,day FROM cover_taste_builds "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        versions = [
            (version, created_at, json.loads(document))
            for version, created_at, document in self._connection.execute(
                "SELECT version,created_at,document FROM cover_taste_profile_versions "
                "ORDER BY version DESC LIMIT ?",
                (VERSIONS_SHOWN,),
            )
        ]
        return {
            "eligible": self.eligible_count(),
            "build": None
            if build is None or build[1] == "succeeded"
            else {
                "build_id": build[0],
                "failure": build[2],
                "retry_after": (build[3] + 1) * _DAY_SECONDS
                if build[2] in {"ceiling", "unknown_charge"}
                else None,
            },
            "versions": versions,
        }


def _decision_source(
    selection_id: str,
    selection_document: str,
    consideration_document: str,
    file_format: object,
    verdict_document: str | None,
) -> TasteDecisionSource | None:
    """One recorded decision's previewed covers and stage facts, or None to skip."""
    try:
        selection = json.loads(selection_document)
        consideration = json.loads(consideration_document)
        stage = json.loads(consideration["policy_report_document"])["stage"]
        art = {
            member: group["representative"]
            for group in stage["groups"]
            for member in group["members"]
        }
        wrong_shape = {
            item["reference"]
            for item in stage["exclusions"]
            if item["exclusion"] == "wrong_shape"
        }
        candidates = tuple(
            TasteCandidateSource(
                reference=candidate["reference"],
                preview_sha256=candidate["preview_sha256"],
                transport_sha256=candidate["transport_sha256"],
                width=candidate["width"],
                height=candidate["height"],
                current=candidate["incumbent"] is True,
                rank=candidate["deterministic_rank"],
                ordinal=candidate["ordinal"],
                wrong_shape=candidate["reference"] in wrong_shape
                or (
                    candidate["incumbent"] is True and "wrong_shape" in stage["issues"]
                ),
                art=art.get(candidate["reference"], candidate["reference"]),
            )
            for candidate in consideration["candidates"]
            if candidate.get("candidate_kind") == "candidate"
            and type(candidate.get("preview_sha256")) is str
            and type(candidate.get("transport_sha256")) is str
            and type(candidate.get("width")) is int
            and type(candidate.get("height")) is int
        )
        chosen = selection["candidate_reference"]
        if type(file_format) is not str or chosen not in {
            candidate.reference for candidate in candidates
        }:
            return None
        # A keep names the current cover's reference, so a keep under a
        # suggestion, or another cover under a keep, is a correction.
        verdict = None if verdict_document is None else json.loads(verdict_document)
        suggested = (
            None
            if verdict is None
            else verdict["candidate_reference"]
            if verdict["decision"] == "choose"
            else next(
                (
                    candidate["reference"]
                    for candidate in consideration["candidates"]
                    if candidate["incumbent"] is True
                ),
                None,
            )
            if verdict["decision"] == "keep_current"
            else None
        )
        return TasteDecisionSource(
            selection_id,
            "audiobook" if file_format in AUDIO_FILE_FORMATS else "ebook",
            chosen,
            candidates,
            correction=suggested is not None and suggested != chosen,
        )
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["SQLiteCoverTasteRepository", "validate_cover_taste_versions"]
