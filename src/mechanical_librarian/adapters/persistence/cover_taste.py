"""Durable taste profile builds, append-only versions and the daily request count.

Every state change is one short transaction; nothing here holds a transaction
while a request is in flight (#1372 S8, S9).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Literal, cast

from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.contained_reviewer_contract import canonical_json
from mechanical_librarian.application.cover_taste_inputs import (
    TasteCandidateSource,
    TasteDecisionSource,
)
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_PROFILE_SCHEMA,
    TASTE_FIRST_BUILD_DECISIONS,
    TASTE_MIN_ARTS,
    TASTE_REBUILD_DECISIONS,
    TASTE_SCHEDULED_BUILDS_PER_DAY,
    TasteBuildFailure,
    TasteProfile,
)
from mechanical_librarian.domain.metadata_report import AUDIO_FILE_FORMATS

_TASK = "cover_taste_build"
_DAY_SECONDS = 86_400
VERSIONS_SHOWN = 50
# S2: the newest owner selection per item, naming a previewed cover that is
# not a retained original, on a consideration offering at least two arts.
_ELIGIBLE = (
    "FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities selection "
    "ON selection.selection_id=current.selection_id "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=selection.consideration_id "
    "WHERE selection.kind='owner' "
    "AND json_extract(selection.document,'$.preview_sha256') IS NOT NULL "
    "AND json_extract(selection.document,'$.candidate_kind') IS NOT 'retained' "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.arts')>=?"
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


def validate_cover_taste_versions(connection: sqlite3.Connection) -> None:
    """Refuse a stored version that a build could not have written."""
    for version, document in connection.execute(
        "SELECT version,document FROM cover_taste_profile_versions ORDER BY version"
    ):
        value = json.loads(document)
        if (
            type(value) is not dict
            or set(value)
            != {"build_id", "input", "model", "rules", "schema_version", "version"}
            or value["schema_version"] != COVER_TASTE_PROFILE_SCHEMA
            or value["version"] != version
            or type(value["input"]) is not dict
            or set(value["input"]) != _RECORD_KEYS
            or canonical_json(value) != document
        ):
            raise ValueError("taste profile version is invalid")
        TasteProfile.from_rules(value["rules"])


class SQLiteCoverTasteRepository(ConnectionScoped):
    """Each update keeps `updated_at` from going back, so a wall clock that
    steps backwards never fails a build's own write."""

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
            "SELECT coalesce(max(rowid),0) FROM cover_selection_authorities"
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
    def schedule(self, now: int, configured: bool, daily_limit: int) -> None:
        """Request a cadence build when one is due (#1372 S9)."""
        if not configured:
            return
        transaction = self._transaction("taste_schedule")
        try:
            transaction.begin()
            last = self._connection.execute(
                "SELECT state,selection_watermark FROM cover_taste_builds "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            scheduled = self._connection.execute(
                "SELECT count(*) FROM cover_taste_builds WHERE trigger='cadence' AND day=?",
                (_day(now),),
            ).fetchone()[0]
            if (last is not None and last[0] in {"requested", "running"}) or (
                scheduled >= TASTE_SCHEDULED_BUILDS_PER_DAY
            ):
                transaction.commit()
                return
            total = self._connection.execute(
                f"SELECT count(*) {_ELIGIBLE}", (TASTE_MIN_ARTS,)
            ).fetchone()[0]
            new = (
                total
                if last is None
                else self._connection.execute(
                    f"SELECT count(*) {_ELIGIBLE} AND selection.rowid>?",
                    (TASTE_MIN_ARTS, last[1]),
                ).fetchone()[0]
            )
            if total >= TASTE_FIRST_BUILD_DECISIONS and (
                last is None or new >= TASTE_REBUILD_DECISIONS
            ):
                self._insert(now, "cadence", None, daily_limit)
            transaction.commit()
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

    @local_operation
    def succeed(
        self,
        build_id: str,
        now: int,
        profile: TasteProfile,
        record: dict[str, object],
        *,
        stop_day: bool = False,
    ) -> int | None:
        """Append the next version for a running build; None when it was not running."""
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
            version = (
                self._connection.execute(
                    "SELECT coalesce(max(version),0) FROM cover_taste_profile_versions"
                ).fetchone()[0]
                + 1
            )
            self._connection.execute(
                "INSERT INTO cover_taste_profile_versions VALUES(?,?,?,?)",
                (
                    version,
                    build_id,
                    now,
                    canonical_json(
                        {
                            "build_id": build_id,
                            "input": record,
                            "model": row[0],
                            "rules": profile.rules_data(),
                            "schema_version": COVER_TASTE_PROFILE_SCHEMA,
                            "version": version,
                        }
                    ),
                ),
            )
            self._connection.execute(
                "UPDATE cover_taste_builds SET state='succeeded',"
                "updated_at=max(?,updated_at) WHERE build_id=?",
                (now, build_id),
            )
            if stop_day:
                self._stop_day(now)
            transaction.commit()
            return cast(int, version)
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
        rows = self._connection.execute(
            "SELECT selection.selection_id,selection.document,consideration.document,"
            "json_extract(report.document,'$.file.format') "
            f"{_ELIGIBLE.replace('WHERE', 'LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id WHERE', 1)} "
            "ORDER BY selection.rowid",
            (TASTE_MIN_ARTS,),
        ).fetchall()
        sources = (_decision_source(*row) for row in rows)
        return tuple(source for source in sources if source is not None)

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
        return TasteDecisionSource(
            selection_id,
            "audiobook" if file_format in AUDIO_FILE_FORMATS else "ebook",
            chosen,
            candidates,
        )
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["SQLiteCoverTasteRepository", "validate_cover_taste_versions"]
