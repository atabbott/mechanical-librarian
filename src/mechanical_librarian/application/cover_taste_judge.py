"""Judge eligible cover fields against the active taste profile version.

The preparation runner's background lane calls `CoverTasteBackground.wake`
each second: the builder first, then at most one judgement. A judgement is
counted before dispatch and the model is called with no transaction open;
its verdict is stored append-only and shown nowhere yet (#1372 PR 4, J1-J7).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from mechanical_librarian.application.cover_packet_semantics import (
    normalize_cover_preview,
)
from mechanical_librarian.application.cover_taste_build import CoverTasteBuildService
from mechanical_librarian.application.cover_taste_contract import (
    TasteChargeUnknownError,
)
from mechanical_librarian.application.cover_taste_judge_contract import (
    TasteJudgeContractError,
    TasteJudgeImage,
    TasteJudgeOutputError,
    admit_taste_judge_request,
    admit_taste_verdict,
    taste_verdict_document,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
)
from mechanical_librarian.domain.cover_taste import (
    TASTE_JUDGE_MAX_ALTERNATIVES,
    TASTE_JUDGE_NOTICE_SECONDS,
    TASTE_JUDGE_OUTAGE_PAUSE_SECONDS,
    TASTE_JUDGE_SETTLE_SECONDS,
    TasteJudgeFailure,
    TasteProfile,
)
from mechanical_librarian.domain.human_decision import manual_cover_eligible
from mechanical_librarian.domain.metadata_report import AUDIO_FILE_FORMATS

# With nothing due, the due statement runs again after this, as the
# builder's schedule does.
_IDLE_SECONDS = 60
# Due fields are read in batches of this many, each read again before it is
# judged, so a pass does not scan every package once per field.
_DUE_BATCH = 64
# A fault of the art itself: another copy of the current art cures nothing.
_RESOLUTION_FAULTS = frozenset({"below_floor", "sharper_copy"})


@dataclass(frozen=True, slots=True)
class JudgeDue:
    """One field the due statement selected, with its full consideration."""

    consideration_id: str
    review_case_id: str
    consideration: dict[str, object]
    file_format: str
    outcome: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class TasteJudgeVersion:
    """The active profile version the judge runs under."""

    version: int
    created_at: int
    profile: TasteProfile


@dataclass(frozen=True, slots=True)
class JudgeCandidate:
    """One cover the judge is sent, as the consideration recorded it."""

    reference: str
    current: bool
    width: int
    height: int
    preview_sha256: str
    transport_sha256: str
    # A larger copy records its stored preview as its transport.
    larger_copy: bool = False


def _previewed(candidate: dict[str, object]) -> bool:
    return (
        type(candidate.get("preview_sha256")) is str
        and type(candidate.get("transport_sha256")) is str
        and type(candidate.get("width")) is int
        and type(candidate.get("height")) is int
    )


def _rank(candidate: dict[str, object]) -> tuple[bool, int, int]:
    rank = candidate["deterministic_rank"]
    return rank is None, cast(int, rank or 0), cast(int, candidate["ordinal"])


def _judged(candidate: dict[str, object], current: bool) -> JudgeCandidate:
    return JudgeCandidate(
        cast(str, candidate["reference"]),
        current,
        cast(int, candidate["width"]),
        cast(int, candidate["height"]),
        cast(str, candidate["preview_sha256"]),
        cast(str, candidate["transport_sha256"]),
        candidate.get("candidate_kind") == "larger_copy",
    )


def judge_candidates(due: JudgeDue) -> tuple[JudgeCandidate, ...] | None:
    """The current cover, when readable, and one cover of each other art (J2).

    The judge picks an art and the stage picks its copy: an art's
    representative, and for a resolution fault the current art's curing
    copy. At most 12 alternatives in rank order; None when the full report
    leaves too few, so the stage's own outcome stands.
    """
    try:
        consideration = due.consideration
        policy = json.loads(cast(str, consideration["policy_report_document"]))
        stage = policy["stage"]
        if (stage["outcome"], stage["reason"]) != (due.outcome, due.reason):
            return None
        excluded = {item["reference"] for item in stage["exclusions"]}
        acceptable = set(policy["acceptable_candidate_references"]) - excluded
        candidates = cast("list[dict[str, object]]", consideration["candidates"])
        art = {
            member: group["representative"]
            for group in stage["groups"]
            for member in group["members"]
        }
        current = None
        if due.reason != "unreadable":
            current = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate["incumbent"] is True
                ),
                None,
            )
            if current is None or not _previewed(current):
                return None
        current_art = (
            None
            if current is None
            else art.get(current["reference"], current["reference"])
        )
        arts: dict[object, list[dict[str, object]]] = {}
        for candidate in sorted(candidates, key=_rank):
            if (
                candidate["reference"] in acceptable
                and candidate.get("candidate_kind") in {"candidate", "larger_copy"}
                and manual_cover_eligible(candidate)
                and _previewed(candidate)
            ):
                key = art.get(candidate["reference"], candidate["reference"])
                arts.setdefault(key, []).append(candidate)
        alternatives = []
        for key, copies in arts.items():
            if key == current_art:
                if due.reason not in _RESOLUTION_FAULTS:
                    continue
                preferred = stage["choice"]
            else:
                preferred = key
            alternatives.append(
                next(
                    (copy for copy in copies if copy["reference"] == preferred),
                    copies[0],
                )
            )
        alternatives = sorted(alternatives, key=_rank)[:TASTE_JUDGE_MAX_ALTERNATIVES]
        if len(alternatives) < (1 if due.outcome == "keep" else 2):
            return None
        return (
            *(() if current is None else (_judged(current, True),)),
            *(_judged(candidate, False) for candidate in alternatives),
        )
    except (KeyError, TypeError, ValueError):
        return None


class CoverTasteJudgeRepository(Protocol):
    def active(self) -> TasteJudgeVersion | None: ...

    def building(self) -> bool: ...

    def refusal(self, now: int, daily_limit: int) -> bool: ...

    def due(self, version: int, limit: int) -> tuple[str, ...]: ...

    def due_one(self, consideration_id: str, version: int) -> JudgeDue | None: ...

    def record(
        self,
        consideration_id: str,
        version: int,
        now: int,
        failure: Literal["unavailable"] | None = None,
    ) -> None: ...

    def claim(
        self,
        consideration_id: str,
        version: int,
        now: int,
        model: str,
        daily_limit: int,
    ) -> str | None: ...

    def fail(
        self,
        judgement_id: str,
        now: int,
        failure: TasteJudgeFailure,
        *,
        stop_day: bool = False,
    ) -> None: ...

    def succeed(
        self, judgement_id: str, now: int, document: str, *, stop_day: bool = False
    ) -> bool: ...

    def interrupt_recovered(self, now: int) -> None: ...


class CoverTasteJudgeService:
    def __init__(
        self,
        repository: Callable[[], CoverTasteJudgeRepository],
        access: ReviewerAccessOperations,
        read_preview: Callable[[str], bytes],
        *,
        daily_limit: int,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._repository = repository
        self._access = access
        self._read_preview = read_preview
        self._daily_limit = daily_limit
        self._clock = clock
        self._recovered = False
        self._idle_until = 0
        self._paused_until = 0
        self._noticed_at: int | None = None
        self._queue: list[str] = []
        self._queue_version: int | None = None

    def wake(self, now: int, changed: Callable[[], object] = lambda: None) -> bool:
        """Judge or skip at most one due field; only the preparation runner
        calls it.

        `changed` is called at most once every 10 seconds while a pass moves
        and once when it ends. Returns whether a field was judged or skipped.
        """
        repository = self._repository()
        if not self._recovered:
            # Judgements run only inside `wake`, so one in flight before this
            # process's first wake was left by an earlier process.
            repository.interrupt_recovered(now)
            self._recovered = True
        if now < self._paused_until or now < self._idle_until:
            return False
        active = repository.active()
        model = self._access.current_taste_judge_model()
        if (
            active is None
            or model is None
            or now - active.created_at < TASTE_JUDGE_SETTLE_SECONDS
            or repository.building()
            or repository.refusal(now, self._daily_limit)
        ):
            return False
        due = self._next_due(repository, active.version)
        if due is None:
            self._idle_until = now + _IDLE_SECONDS
            if self._noticed_at is not None:
                self._noticed_at = None
                changed()
            return False
        candidates = judge_candidates(due)
        if candidates is None:
            # The full report leaves the field ineligible: recorded once,
            # with no request, so it is never selected again (J1). One per
            # wake, so lane 1 keeps preparing between skips.
            repository.record(due.consideration_id, active.version, now)
            return True
        try:
            self._judge(repository, active, due, candidates)
        finally:
            if (
                self._noticed_at is None
                or now - self._noticed_at >= TASTE_JUDGE_NOTICE_SECONDS
            ):
                self._noticed_at = now
                changed()
        return True

    def _next_due(
        self, repository: CoverTasteJudgeRepository, version: int
    ) -> JudgeDue | None:
        """The next field still due: a batch of ids is scanned at most once a
        wake, and each id is read again on its own before it is used."""
        if version != self._queue_version:
            self._queue, self._queue_version = [], version
        scanned = False
        while True:
            if not self._queue:
                if scanned:
                    return None
                self._queue = list(repository.due(version, _DUE_BATCH))
                scanned = True
                if not self._queue:
                    return None
            due = repository.due_one(self._queue.pop(0), version)
            if due is not None:
                return due

    def _seal(
        self, candidates: tuple[JudgeCandidate, ...]
    ) -> tuple[TasteJudgeImage, ...]:
        """Each preview re-admitted as the canonical PNG the consideration
        recorded; raises when any is missing or changed.

        Only a larger copy may record its stored preview as its transport,
        which the source digest binds; every other candidate records the
        canonical PNG.
        """
        images = []
        for candidate in candidates:
            preview = normalize_cover_preview(
                self._read_preview(candidate.preview_sha256),
                source_sha256=candidate.preview_sha256,
            )
            if candidate.transport_sha256 != preview.digest and (
                not candidate.larger_copy
                or candidate.transport_sha256 != candidate.preview_sha256
            ):
                raise ValueError("cover preview changed")
            images.append(
                TasteJudgeImage(
                    candidate.reference,
                    candidate.current,
                    candidate.width,
                    candidate.height,
                    preview.digest,
                    preview.payload,
                )
            )
        return tuple(images)

    def _judge(
        self,
        repository: CoverTasteJudgeRepository,
        active: TasteJudgeVersion,
        due: JudgeDue,
        candidates: tuple[JudgeCandidate, ...],
    ) -> None:
        try:
            engine, model, profile, client = self._access.open_taste_judge()
        except ReviewerAccessApplicationError:
            # Nothing was sent or recorded: the engine is out for now.
            self._paused_until = self._clock() + TASTE_JUDGE_OUTAGE_PAUSE_SECONDS
            return
        judgement_id: str | None = None
        # What a lost write ends the attempt as: after `claim` the request was
        # counted and may be charged (S9).
        failure: TasteJudgeFailure = "unavailable"
        stop_day = False
        try:
            try:
                request = admit_taste_judge_request(
                    "audiobook" if due.file_format in AUDIO_FILE_FORMATS else "ebook",
                    active.version,
                    active.profile,
                    self._seal(candidates),
                )
            except (OSError, ValueError, MetadataReportError, TasteJudgeContractError):
                # A preview failed re-admission, so nothing was sent (J7).
                repository.record(
                    due.consideration_id, active.version, self._clock(), "unavailable"
                )
                return
            judgement_id = repository.claim(
                due.consideration_id,
                active.version,
                self._clock(),
                model,
                self._daily_limit,
            )
            if judgement_id is None:
                return
            failure, stop_day = "unknown_charge", True
            try:
                reply = client.run_taste_judge(profile, request)
            except TasteChargeUnknownError:
                repository.fail(
                    judgement_id, self._clock(), "unknown_charge", stop_day=True
                )
                return
            except Exception:  # noqa: BLE001 - every other outcome sent no verdict
                failure, stop_day = "unavailable", False
                repository.fail(judgement_id, self._clock(), "unavailable")
                self._paused_until = self._clock() + TASTE_JUDGE_OUTAGE_PAUSE_SECONDS
                return
            # A reply arrived; without usage its charge is unknown (S9).
            stop_day = not reply.usage_reported
            failure = "invalid_output" if reply.usage_reported else "unknown_charge"
            try:
                verdict = admit_taste_verdict(request, reply.text)
            except TasteJudgeOutputError:
                repository.fail(judgement_id, self._clock(), failure, stop_day=stop_day)
                return
            # The verdict was admitted: a write lost now leaves no verdict
            # through no fault of the reply, so the attempt ends `unavailable`
            # (or `unknown_charge` without usage).
            if reply.usage_reported:
                failure = "unavailable"
            repository.succeed(
                judgement_id,
                self._clock(),
                taste_verdict_document(
                    request,
                    verdict,
                    consideration_id=due.consideration_id,
                    engine=engine,
                    model=model,
                ),
                stop_day=stop_day,
            )
        except Exception:
            # A failed write never leaves the attempt `running` until restart:
            # it ends once, best effort, and the fault is raised.
            if judgement_id is not None:
                try:
                    repository.fail(
                        judgement_id, self._clock(), failure, stop_day=stop_day
                    )
                except Exception:  # noqa: BLE001 - the original fault is raised
                    pass
            raise
        finally:
            client.close()


class CoverTasteBackground:
    """The taste profile's background work on lane 1: a due build first,
    then, when no build ran, at most one judgement (J6)."""

    def __init__(
        self, build: CoverTasteBuildService, judge: CoverTasteJudgeService
    ) -> None:
        self._build = build
        self._judge = judge

    def wake(self, now: int, changed: Callable[[], object] = lambda: None) -> bool:
        return self._build.wake(now, changed) or self._judge.wake(now, changed)


__all__ = [
    "CoverTasteBackground",
    "CoverTasteJudgeRepository",
    "CoverTasteJudgeService",
    "JudgeCandidate",
    "JudgeDue",
    "TasteJudgeVersion",
    "judge_candidates",
]
