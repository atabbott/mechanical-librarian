"""One real Activity ledger, written the way the product writes one.

The installed Activity surface has until now been served a single hand-written
event literal, so every width measurement, filter chip, search box, date range
and page control on that page was exercised against text no event produces.
This module seeds a real SQLite repository through the real guarded
application -- real durable documents, real timestamps, past a page boundary --
so the surface can be driven and measured against what an owner would actually
see (#636).

Everything here is written through a supported seam. The dedicated Activity
end-to-end proof also drives automatic decisions and no-change facts through
the qualified policy and shadow-evaluation path (#789).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeAuthorityCurrentness,
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase

CAPABILITY = "c" * 64
DEPENDENCY = "d" * 64
CONNECTION = "primary"
LIBRARY = "books"
WORK = "book-1"

# A fixed instant rather than "now" so the seeded ledger is byte-identical on
# every run; the reader's clock is derived from it so the default 30-day range
# includes the recent rows and excludes the deliberately old ones.
SEED_NOW = 1_786_000_000
DAY = 86_400

LONG_DESCRIPTION = (
    "A quiet, unhurried account of the lamplighters of Larkspur Station, who "
    "kept the orbital approach lit for freighters that had long since stopped "
    "coming, and who went on keeping it lit because no one had told them to "
    "stop and because the light was the only thing out there that was theirs. "
    "The narrative moves between the maintenance logs, the letters home that "
    "were never sent, and the slow accumulation of dust on the mirror array."
)
COVER_TOKEN = "cover_" + "3f" * 32


class _SeedClock:
    """Advance in real seconds so durable events carry believable instants."""

    def __init__(self, start: int) -> None:
        self.value = start

    def __call__(self) -> int:
        self.value += 37
        return self.value


@dataclass(frozen=True)
class _Doubles:
    """The three collaborators every seeding step drives, named and typed.

    Declared rather than passed as a tuple so the seeding helpers keep the
    types their calls depend on; an untyped carrier would make every ``bind``,
    ``allow`` and ``seed`` below an unchecked call.
    """

    target: FakeTargetService
    authority: FakeAuthorityCurrentness
    consent: FakeConsentCurrentness


@dataclass
class SeededActivity:
    """A seeded repository and the clock a reader should observe it through."""

    repository: SQLiteMetadataReportRepository
    clock: _SeedClock

    def reader_now(self) -> int:
        return SEED_NOW + 2 * DAY


def _report(title: str, item_id: str, content: str, identity: str) -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Exact evaluator candidate",
        canonical="Exact evaluator candidate",
        excerpt="Exact evaluator candidate",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts(CONNECTION, LIBRARY, item_id, default_target_fields(title)),
        file=FileFacts(0, content * 64, identity * 64),
        local_claims=(claim,),
        local_reasons=("opening_matter_title",),
        providers=(),
    )


def _environment(
    root: Path,
) -> tuple[
    SQLiteMetadataReportRepository, GuardedFieldApplication, _Doubles, _SeedClock
]:
    repository = open_repository(root / "activity.sqlite", cross_thread=True)
    target = FakeTargetService()
    authority = FakeAuthorityCurrentness()
    backup = FakeBackupReadiness()
    capabilities = FakeCapabilityObserver(CAPABILITY)
    consent = FakeConsentCurrentness(repository.field_effect_consent_envelope_identity)
    clock = _SeedClock(SEED_NOW - 3 * DAY)
    service = GuardedFieldApplication(
        repository,
        target,
        backup,
        capabilities,
        authority,
        FakeDependencyCurrentness(),
        consent,
        PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover"),
        FakeForwardReevaluation(),
        clock,
        FakePreclaimHook(),
        FakePreclaimHook(),
        FakePreclaimHook(),
    )
    return repository, service, _Doubles(target, authority, consent), clock


def _decision(
    destination: str, selected: dict[str, object], suffix: str
) -> HumanDecision:
    return HumanDecision(
        "human_decision_" + suffix,
        "review_case_" + "b" * 64,
        "review_proposal_" + "c" * 64,
        "d" * 64,
        destination,
        "owner",
        "accept",
        selected,
        "e" * 64,
        "f" * 64,
        1,
        "apply",
    )


def _envelope(destination: str, value: object) -> dict[str, object]:
    kind = (
        "list"
        if destination in {"authors", "narrators", "genres", "tags"}
        else "series"
        if destination == "series"
        else "cover"
        if destination == "cover"
        else "text"
    )
    return {"kind": kind, "value": value}


def _write(
    service: GuardedFieldApplication,
    doubles: _Doubles,
    *,
    destination: str,
    pre: object,
    desired: object,
    suffix: str,
) -> FieldEffectOperation:
    target, authority, consent = doubles.target, doubles.authority, doubles.consent
    selected = _envelope(destination, desired)
    source = _decision(destination, selected, suffix)
    authority.bind(
        source,
        connection=CONNECTION,
        library=LIBRARY,
        work_id=WORK,
        target_version="version-1",
    )
    consent.allow(connection=CONNECTION, library=LIBRARY, destination=destination)
    operation = service.plan(
        source,
        connection=CONNECTION,
        library=LIBRARY,
        work_id=WORK,
        destination=destination,
        pre_value=pre,
        desired_value=selected,
        target_fingerprint="d" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert operation is not None, destination
    target.seed(operation.plan)
    return operation


# Every value shape the durable vocabulary allows, each reaching the surface at
# least once: scalar, list, contributor list, series with and without an index,
# a date, an absence, the opaque cover identity, and an unbounded description.
_SHAPES: tuple[tuple[str, object, object, str], ...] = (
    ("title", "Paper Moons: Larkspur", "Paper Moons over Larkspur Station", "01"),
    ("authors", ["Ursula K. Le Guin"], ["Ursula K. Le Guin", "雨の地図"], "02"),
    ("tags", None, ["annotated", "signed"], "03"),
    (
        "series",
        None,
        [{"name": "The Larkspur Cycle", "sequence": "2"}],
        "04",
    ),
    (
        "series",
        [{"name": "The Larkspur Cycle", "sequence": "2"}],
        [{"name": "Outer Approach Stories", "sequence": None}],
        "13",
    ),
    ("genres", ["Science Fiction"], ["Science Fiction", "Literary"], "05"),
    ("publishedDate", "1974-01-01", "1974-05-13", "06"),
    ("publisher", None, "Harper & Row", "07"),
    ("publishedYear", "1974", "1975", "14"),
    ("description", "A short note.", LONG_DESCRIPTION, "08"),
    ("cover", "cover_" + "a1" * 32, COVER_TOKEN, "09"),
    ("language", "en", "en-GB", "10"),
    ("subtitle", "A Novel", "A Novel of the Outer Approach", "11"),
    ("isbn", None, "9780060512750", "12"),
    ("title", "Paper Moons over Larkspur Station", "Paper Moons: Revised", "15"),
    ("authors", ["Ursula K. Le Guin", "雨の地図"], ["雨の地図"], "16"),
    ("tags", ["annotated", "signed"], ["annotated", "signed", "archive"], "17"),
    ("description", LONG_DESCRIPTION, "A shorter final catalogue note.", "18"),
)


def _seed_writes(service: GuardedFieldApplication, doubles: _Doubles) -> None:
    """Drive each value shape through the real plan and apply path.

    The first several are carried all the way to ``verified`` so the Result
    column shows a settled write; the rest are deliberately left in flight so
    the same column is seen carrying ``planned`` beside them.
    """
    for index, (destination, pre, desired, suffix) in enumerate(_SHAPES):
        operation = _write(
            service,
            doubles,
            destination=destination,
            pre=pre,
            desired=desired,
            suffix=suffix * 32,
        )
        if index % 3 != 2:
            service.apply(operation.plan.plan_id)


def _seed_stopped_writes(service: GuardedFieldApplication, doubles: _Doubles) -> None:
    """Two writes the library refused, so Result carries a stopped state."""
    target = doubles.target
    refused = _write(
        service,
        doubles,
        destination="genres",
        pre=["Reference"],
        desired=["Reference", "Annotated"],
        suffix="20" * 32,
    )
    target.unavailable_scope_next = "connection"
    service.apply(refused.plan.plan_id)

    raced = _write(
        service,
        doubles,
        destination="asin",
        pre="B000FC1PJI",
        desired="B0007GZQP2",
        suffix="21" * 32,
    )
    target.before_apply = lambda plan: target.drift(plan, "external")
    service.apply(raced.plan.plan_id)
    target.before_apply = None


def _seed_owner_decisions(repository: SQLiteMetadataReportRepository) -> None:
    """Accept, keep and reject, each from one real admitted review case."""
    report = _report("Old title", "case-book", "1", "2")
    repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    repository.publish_proposal(proposal_set)
    case = ReviewCase.create_from_data(
        json.loads(
            repository.get_review_case(
                json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
            )
        )["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    proposal_id, destination = (
        str(proposal["proposal_id"]),
        str(proposal["destination"]),
    )
    selected = cast(
        "dict[str, object] | None", proposal["result"]["alternatives"][0]["value"]
    )
    for index, outcome in enumerate(("accept", "keep_current", "reject")):
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=proposal_id,
                target_fingerprint=case.target_fingerprint,
                destination=destination,
                actor="owner",
                outcome=outcome,
                selected_value=selected if outcome == "accept" else None,
                idempotency_key=f"seeded-owner-choice-{index:04d}",
                occurred_at=SEED_NOW - 10 * DAY + index * 90,
            )
        )


def _seed_policy_chosen_writes(
    repository: SQLiteMetadataReportRepository,
    service: GuardedFieldApplication,
    doubles: _Doubles,
) -> None:
    """Writes a policy chose rather than the owner, so the row names the policy.

    The decision is bound to the authority the same way the automatic pipeline
    binds one. The durable ``automatic_decisions`` document that would let the
    *decision* itself appear as its own row is out of reach here for the reason
    recorded in ``tests/unit/test_activity_end_to_end.py``; this seeds the
    write it authorises, which is a different source kind and does render.
    """
    target, authority, consent = doubles.target, doubles.authority, doubles.consent
    report = _report("Automatic candidate", "auto-book", "5", "6")
    repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    repository.publish_proposal(proposal_set)
    case = ReviewCase.create_from_data(
        json.loads(
            repository.get_review_case(
                json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
            )
        )["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    proposal_id, destination = (
        str(proposal["proposal_id"]),
        str(proposal["destination"]),
    )
    for index, (desired, apply_it) in enumerate(
        (("Automatic candidate title", True), ("A later automatic choice", False))
    ):
        automatic = AutomaticDecision.create(
            review_case_id=case.review_case_id,
            report_id=case.report_id,
            proposal_set_id=case.proposal_set_id,
            proposal_id=proposal_id,
            target_fingerprint=case.target_fingerprint,
            destination=destination,
            outcome="candidate",
            candidate_id="alternative_" + f"{index:x}" * 64,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1,
            rule_id="shadow_rule_" + "1" * 64,
            policy_category="title_layout",
            policy_option="preserve",
            qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            bundle_id="automatic_bundle_" + "4" * 64,
            occurred_at=SEED_NOW - 16 * DAY + index * 120,
        )
        authority.automatic_values[automatic.decision_id] = _envelope("title", desired)
        authority.bind(
            automatic,
            connection=CONNECTION,
            library=LIBRARY,
            work_id="auto-book",
            target_version="version-1",
        )
        consent.allow(connection=CONNECTION, library=LIBRARY, destination=destination)
        operation = service.plan(
            automatic,
            connection=CONNECTION,
            library=LIBRARY,
            work_id="auto-book",
            destination=destination,
            pre_value="Automatic candidate",
            desired_value=_envelope("title", desired),
            target_fingerprint=case.target_fingerprint,
            target_version="version-1",
            dependency_digests=(DEPENDENCY,),
            capability_digest=CAPABILITY,
        )
        assert operation is not None
        target.seed(operation.plan)
        if apply_it:
            service.apply(operation.plan.plan_id)


def _seed_policy(repository: SQLiteMetadataReportRepository) -> None:
    """One real activation, so the policy row carries the settings that moved."""
    builtin = MetadataPolicyProfile.builtin()
    options = dict(builtin.options)
    destinations = dict(builtin.destinations)
    reviewed = sorted(field for field, value in destinations.items() if value == "ask")
    moved_option, replacement_option = next(
        (field, choice)
        for field in reviewed
        for choice in sorted(OPTION_CATALOGUE[field])
        if choice != options[field]
    )
    options[moved_option] = replacement_option
    destinations[next(field for field in reviewed if field != moved_option)] = "ignore"
    replacement = MetadataPolicyProfile.create("Seeded policy", options, destinations)
    repository.create_metadata_policy_draft(replacement.canonical_json)
    resolved = resolve_policy(replacement, LIBRARY)
    repository.activate_metadata_policy(
        replacement.policy_id,
        builtin.policy_id,
        ((LIBRARY, resolved.resolved_id, resolved.canonical_json),),
        SEED_NOW - 20 * DAY,
    )


def seed_activity(root: Path) -> SeededActivity:
    """Seed one real ledger across every kind this path can reach."""
    repository, service, doubles, clock = _environment(root)
    repository.publish(_report("Paper Moons: Larkspur", WORK, "a", "b"), lambda: True)
    _seed_writes(service, doubles)
    _seed_stopped_writes(service, doubles)
    _seed_owner_decisions(repository)
    _seed_policy_chosen_writes(repository, service, doubles)
    _seed_policy(repository)
    return SeededActivity(repository, clock)
