"""A choice binds the offer its field showed (#1355, S10 "Offer binding").

Contract tests 21 (validation and versions half), 17a (the write and readback
digest refusal), 17d (`digest_mismatch`) and 22 (server half). The fixture is
an ebook whose description a live answer proposes and whose refused answer
the configured level admits, so the field's current offer holds one more
description than the live proposal does.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.level_reconciliation import (
    current_level_offer,
    stored_level_offer_documents,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    provider_observation,
)
from mechanical_librarian.domain import human_decision
from mechanical_librarian.domain.human_decision import (
    HUMAN_DECISION_SCHEMA,
    HumanDecision,
    HumanDecisionError,
    offer_digest,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderClaim,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetFacts,
)
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    current_offer,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.support.owner_values import OwnerStore
from tests.unit import test_identity_refused_retention as retained
from tests.unit.review_app_fixture import create_transport_test_app

LEVEL = WorkIdentityLevel.TITLE_AND_CREATOR
SYNOPSIS = "A synopsis of the example book."
LIVE_SYNOPSIS = "A live synopsis of the example book."
_ORIGIN = "https://review.test"


def _description(provider: str, identity: str, value: str) -> ProviderClaim:
    return ProviderClaim.create(
        provider=provider,
        candidate=identity,
        kind=ClaimKind.DESCRIPTION,
        value=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=2,
    )


def level_description_report(*, live_description: bool = True) -> MetadataReport:
    """An ebook by "R. F. Example" whose description a live identifier
    answer proposes, and whose refused WORK answer (the author's initials
    respelled) offers another description at a wider level.

    Without ``live_description`` no live answer states a description, so the
    live field is refused (``no_supported_evidence``) and offers nothing.
    """
    live = retained._candidate("live-1", provider="open_library")
    if live_description:
        live = replace(
            live,
            claims=(
                *live.claims,
                _description("open_library", "live-1", LIVE_SYNOPSIS),
            ),
        )
    refused = retained._candidate("refused-1")
    refused = replace(
        refused,
        claims=(
            *refused.claims,
            _description(retained.PROVIDER, "refused-1", SYNOPSIS),
        ),
    )
    opening = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Example Book",
        canonical="Example Book",
        excerpt="Example Book",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", retained._fields()),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(*retained._plan().local_claims, opening),
        local_reasons=("opening_matter_title",),
        providers=(
            provider_observation(
                retained.PROVIDER,
                (retained._refused_lane(refused),),
                authority=retained.PROVIDER,
            ),
            ProviderObservation(
                "open_library",
                ProviderStatus.CANDIDATES,
                (),
                (live,),
                ProviderQueryKind.ISBN,
                "open_library",
            ),
        ),
    )
    return MetadataReport.from_json(report.to_json())


def level_store(
    root: Path, level: WorkIdentityLevel = LEVEL, *, live_description: bool = True
) -> OwnerStore:
    """A store configured at ``level`` holding the fixture's current case."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = OwnerStore(root, None)
    store.repository.bind_current_cover_authority(
        "cover_policy_" + "1" * 64, "cover_replay_" + "2" * 64, level.value
    )
    store.repository.align_book_matching(level.value, 0)
    store.publish(level_description_report(live_description=live_description))
    return store


def offer_of(store: OwnerStore) -> tuple[dict[str, Any], ...]:
    """The field offers the case shows now, as the command reads them."""
    return current_offer(
        store.case,
        current_level_offer(store.repository._connection, store.case.review_case_id),
    )


def _proposal(offer: tuple[dict[str, Any], ...], destination: str) -> dict[str, Any]:
    return next(value for value in offer if value["destination"] == destination)


def _value(proposal: dict[str, Any], text: str) -> dict[str, object]:
    return next(
        alternative["value"]
        for alternative in proposal["result"]["alternatives"]
        if alternative["value"]["value"] == text
    )


def choose(
    store: OwnerStore,
    text: str,
    *,
    offer: tuple[dict[str, Any], ...] | None = None,
    digest: str | None = None,
    key: str = "level-choice-0001",
) -> HumanDecision:
    shown = offer_of(store) if offer is None else offer
    proposal = _proposal(shown, "description")
    return HumanDecision.create(
        store.case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=store.case.target_fingerprint,
        destination="description",
        actor="owner",
        outcome="accept",
        selected_value=_value(proposal, text),
        idempotency_key=key,
        occurred_at=store.clock(),
        offer=shown,
        offered_alternatives_digest=digest,
    )


def resealed(decision: HumanDecision, **changes: object) -> HumanDecision:
    """The decision's document with ``changes``, sealed again as the domain
    seals it, so only the changed facts can make it invalid."""
    document = decision.to_data() | changes
    document["request_digest"] = human_decision._sha256(
        human_decision._canonical(
            {
                key: document[key]
                for key in (
                    "actor",
                    "application",
                    "cause",
                    "destination",
                    "idempotency_digest",
                    "offered_alternatives_digest",
                    "outcome",
                    "proposal_id",
                    "review_case_id",
                    "selected_value",
                    "target_fingerprint",
                )
            }
        )
    )
    document["decision_id"] = "human_decision_" + human_decision._sha256(
        human_decision._canonical(
            {
                "request_digest": document["request_digest"],
                "occurred_at": document["occurred_at"],
            }
        )
    )
    return HumanDecision.from_json(human_decision._canonical(document))


def seed(store: OwnerStore, decision: HumanDecision) -> None:
    """Insert a retained row as an earlier writer left it."""
    store.repository._connection.execute(
        "INSERT INTO human_decisions("
        "decision_id,review_case_id,proposal_id,target_fingerprint,"
        "destination,actor,outcome,selected_value_document,"
        "idempotency_digest,request_digest,occurred_at,document) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            decision.decision_id,
            decision.review_case_id,
            decision.proposal_id,
            decision.target_fingerprint,
            decision.destination,
            decision.actor,
            decision.outcome,
            json.dumps(decision.selected_value, sort_keys=True),
            decision.idempotency_digest,
            decision.request_digest,
            decision.occurred_at,
            decision.to_json(),
        ),
    )
    store.repository._connection.commit()


def counts(store: OwnerStore) -> tuple[int, ...]:
    return tuple(
        store.repository._connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()[0]
        for table in ("human_decisions", "human_decision_events", "field_effects")
    )


@contextmanager
def owner_client(store: OwnerStore, root: Path) -> Iterator[tuple[TestClient, str]]:
    """A logged-in review client over the store, and its CSRF token."""
    password = root / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"))
    password.chmod(0o600)
    app = create_transport_test_app(
        store.repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin=_ORIGIN,
            owner_password_hash_file=str(password),
        ),
        clock=lambda: 5_000,
        activity_projection=ContinuousGuardedApplication(
            store.repository,
            {},
            {},
            PersistedFieldEffectAuthority(store.repository),
            (("primary", "ebooks"),),
            lambda: 5_000,
            lambda *_: None,
            activity_only=True,
        ),
    )
    with TestClient(app, base_url=_ORIGIN) as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": _ORIGIN},
        )
        assert login.status_code == 200
        yield client, str(login.json()["csrf_token"])


def field_of(detail: dict[str, Any], destination: str) -> dict[str, Any]:
    return next(
        value for value in detail["fields"] if value["destination"] == destination
    )


def post_choice(
    client: TestClient,
    csrf: str,
    case_id: str,
    field: dict[str, Any],
    text: str,
    digest: str,
    key: str,
) -> Any:
    from tests.unit.review_app_fixture import TEST_REACT_BUILD_IDENTITY

    return client.post(
        f"/api/v1/review-cases/{case_id}/decisions",
        json={
            "proposal_id": field["proposal_id"],
            "target_fingerprint": field["target_fingerprint"],
            "offered_alternatives_digest": digest,
            "destination": field["destination"],
            "outcome": "accept",
            "selected_value": {"kind": "text", "value": text},
        },
        headers={
            "Origin": _ORIGIN,
            "Idempotency-Key": key,
            "X-CSRF-Token": csrf,
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        },
    )


# --- The fixture's premise ----------------------------------------------------


def test_the_level_offer_holds_a_description_the_live_proposal_does_not(
    tmp_path: Path,
) -> None:
    store = level_store(tmp_path)
    try:
        live = _proposal(store.case.proposals, "description")
        offered = _proposal(offer_of(store), "description")
        assert [a["value"]["value"] for a in live["result"]["alternatives"]] == [
            LIVE_SYNOPSIS
        ]
        assert {a["value"]["value"] for a in offered["result"]["alternatives"]} == {
            SYNOPSIS,
            LIVE_SYNOPSIS,
        }
        assert offered["proposal_id"] == live["proposal_id"]
        assert offer_digest(store.case.proposals, "description") != offer_digest(
            offer_of(store), "description"
        )
    finally:
        store.repository.close()


# --- Test 21: validation and versions -----------------------------------------


def test_a_level_alternative_is_accepted_only_when_the_current_offer_holds_it(
    tmp_path: Path,
) -> None:
    """At the configured level the choice records and reads back; with the
    live proposal alone (identifier) the same value is not offered."""
    store = level_store(tmp_path / "level")
    try:
        level_offer = offer_of(store)
        decision = choose(store, SYNOPSIS)
        assert decision.offered_alternatives_digest == offer_digest(
            level_offer, "description"
        )
        store.repository.record_human_decision(decision)
        assert store.repository.get_review_case_decisions(
            store.case.review_case_id
        ) == (decision.to_json(),)
        # The same value against the live proposal alone is not offered.
        with pytest.raises(HumanDecisionError) as live_only:
            HumanDecision.create(
                store.case,
                proposal_id=decision.proposal_id,
                target_fingerprint=store.case.target_fingerprint,
                destination="description",
                actor="owner",
                outcome="accept",
                selected_value=decision.selected_value,
                idempotency_key="live-only-choice-0001",
                occurred_at=store.clock(),
                offer=store.case.proposals,
            )
        assert live_only.value.code == "invalid_selection"
    finally:
        store.repository.close()
    identifier = level_store(tmp_path / "identifier", WorkIdentityLevel.IDENTIFIER)
    try:
        assert (
            current_level_offer(
                identifier.repository._connection, identifier.case.review_case_id
            )
            is None
        )
        assert offer_of(identifier) == identifier.case.proposals
        # Formed in the level's offer (the same case, as another store
        # showed it), the command reads this store's current offer and refuses.
        shown = choose(identifier, SYNOPSIS, offer=level_offer)
        assert shown.review_case_id == identifier.case.review_case_id
        with pytest.raises(HumanDecisionError) as refused:
            identifier.repository.record_human_decision(shown)
        assert refused.value.code == "offer_changed"
        assert refused.value.reason == "digest_mismatch"
        assert counts(identifier) == (0, 0, 0)
    finally:
        identifier.repository.close()


def test_v4_reads_back_and_v3_is_refused(tmp_path: Path) -> None:
    store = level_store(tmp_path)
    try:
        decision = choose(store, SYNOPSIS)
        document = decision.to_data()
        # The literal is pinned once, in tests/architecture/test_document_versions.py.
        assert document["schema_version"] == HUMAN_DECISION_SCHEMA
        assert document["cause"] is None
        assert HumanDecision.from_json(decision.to_json()) == decision
        v3 = {
            key: value
            for key, value in document.items()
            if key not in {"cause", "offered_alternatives_digest"}
        } | {"schema_version": "human-decision-v3"}
        with pytest.raises(HumanDecisionError) as old:
            HumanDecision.from_json(human_decision._canonical(v3))
        assert old.value.code == "invalid"
        for changed in ({"cause": "owner_asked"}, {"offered_alternatives_digest": "x"}):
            with pytest.raises(HumanDecisionError):
                resealed(decision, **changed)
    finally:
        store.repository.close()


# --- 17a: the digest is refused at the write and at readback ------------------


def test_a_digest_not_the_current_offer_is_refused_at_the_write(
    tmp_path: Path,
) -> None:
    store = level_store(tmp_path)
    try:
        live_digest = offer_digest(store.case.proposals, "description")
        with pytest.raises(HumanDecisionError) as created:
            choose(store, LIVE_SYNOPSIS, digest=live_digest)
        assert (created.value.code, created.value.reason) == (
            "offer_changed",
            "digest_mismatch",
        )
        # Made against the live proposal, valid there, and refused by the
        # command that reads the current offer inside its transaction.
        stale = choose(store, LIVE_SYNOPSIS, offer=store.case.proposals)
        with pytest.raises(HumanDecisionError) as recorded:
            store.repository.record_human_decision(stale)
        assert (recorded.value.code, recorded.value.reason) == (
            "offer_changed",
            "digest_mismatch",
        )
        assert counts(store) == (0, 0, 0)
    finally:
        store.repository.close()


def test_a_digest_no_offer_holds_is_refused_at_readback(tmp_path: Path) -> None:
    store = level_store(tmp_path)
    try:
        decision = choose(store, SYNOPSIS)
        connection = store.repository._connection
        offers = stored_level_offer_documents(connection, store.case.review_case_id)
        assert offers
        decision.validate_for(store.case, offers)
        # The level value outside every stored offer is not a retained fact.
        with pytest.raises(HumanDecisionError) as live_only:
            decision.validate_for(store.case)
        assert live_only.value.code == "invalid"
        # A level value bound to a digest no stored offer has.
        rebound = resealed(decision, offered_alternatives_digest="e" * 64)
        with pytest.raises(HumanDecisionError) as unbound:
            rebound.validate_for(store.case, offers)
        assert unbound.value.code == "invalid"
        seed(store, rebound)
        with pytest.raises(MetadataReportError):
            store.repository.get_review_case_decisions(store.case.review_case_id)
        # A live value is retained under the live digest as before.
        live = choose(
            store, LIVE_SYNOPSIS, offer=store.case.proposals, key="live-choice-0001"
        )
        live.validate_for(store.case, offers)
    finally:
        store.repository.close()


# --- 17d: the API refuses before any write ------------------------------------


@pytest.mark.parametrize(
    ("reason", "stale_digest"),
    [
        ("digest_mismatch", True),
        ("unreconciled_case", False),
        ("unreconciled_case", True),
    ],
)
def test_choice_refused_offer_changed_before_any_write(
    tmp_path: Path, reason: str, stale_digest: bool
) -> None:
    """17d: a choice made in an offer that no longer stands is refused before
    any write. ``digest_mismatch`` names a page that showed another offer;
    ``unreconciled_case`` a case still waiting for its Book matching check,
    which refuses even the current digest, every decision command included,
    until the check reconciles it. A case that is both waiting and shown in
    an older offer answers ``unreconciled_case``: that check runs first."""
    store = level_store(tmp_path)
    try:
        case_id = store.case.review_case_id
        with owner_client(store, tmp_path) as (client, csrf):
            detail = client.get(f"/api/v1/review-cases/{case_id}").json()
            field = field_of(detail, "description")
            live_digest = offer_digest(store.case.proposals, "description")
            assert field["offer_digest"] != live_digest
            shown = live_digest if stale_digest else field["offer_digest"]
            if reason == "unreconciled_case":
                store.repository.align_book_matching(
                    WorkIdentityLevel.TITLE_AND_CREATOR_EXACT.value, 4_000
                )
                assert (
                    client.get(f"/api/v1/review-cases/{case_id}").json()["checking"]
                    is True
                )
            refused = post_choice(
                client, csrf, case_id, field, LIVE_SYNOPSIS, shown, "stale-choice-0001"
            )
            assert refused.status_code == 409, refused.text
            assert refused.json()["error"] == {
                "code": "offer_changed",
                "reason": reason,
            }
            assert counts(store) == (0, 0, 0)
            if reason == "unreconciled_case":
                # The record command refuses on its own too, before any write.
                with pytest.raises(HumanDecisionError) as raised:
                    store.repository.record_human_decision(
                        choose(store, LIVE_SYNOPSIS, key="direct-choice-0001")
                    )
                assert raised.value.reason == "unreconciled_case"
                assert counts(store) == (0, 0, 0)
                assert store.repository.reconcile_case_level(case_id, 4_001) == (
                    "reconciled"
                )
                detail = client.get(f"/api/v1/review-cases/{case_id}").json()
                assert detail["checking"] is False
                field = field_of(detail, "description")
            accepted = post_choice(
                client,
                csrf,
                case_id,
                field,
                LIVE_SYNOPSIS,
                field["offer_digest"],
                "current-choice-0001",
            )
            assert accepted.status_code == 200, accepted.text
            assert counts(store)[0] == 1
    finally:
        store.repository.close()


# --- The review API composes the current offer --------------------------------


def test_the_review_api_composes_a_case_with_its_offer_row(tmp_path: Path) -> None:
    for name, level in (("level", LEVEL), ("identifier", WorkIdentityLevel.IDENTIFIER)):
        store = level_store(tmp_path / name, level)
        try:
            case_id = store.case.review_case_id
            with owner_client(store, tmp_path / name) as (client, _csrf):
                field = field_of(
                    client.get(f"/api/v1/review-cases/{case_id}").json(), "description"
                )
            offer = offer_of(store)
            assert field["offer_digest"] == offer_digest(offer, "description")
            values = [value["value"] for value in field["proposed_values"]]
            bases = {
                context["alternative_id"]: context["match_basis"]
                for context in field["choice_contexts"]
            }
            assert bases.pop(None) is None
            if level is LEVEL:
                assert set(values) == {SYNOPSIS, LIVE_SYNOPSIS}
                lineage = _proposal(offer, "description")["lineage"]
                assert bases == {
                    alternative: "title_and_author" if alternative in lineage else None
                    for alternative in bases
                }
                assert "title_and_author" in bases.values()
                # The refused answer's own evidence is cited for its choice.
                (level_context,) = (
                    context
                    for context in field["choice_contexts"]
                    if context["match_basis"] == "title_and_author"
                    and SYNOPSIS
                    in " ".join(row["observation"] for row in context["evidence"])
                )
                assert level_context["selection"] == "proposed"
            else:
                assert values == [LIVE_SYNOPSIS]
                assert set(bases.values()) == {None}
        finally:
            store.repository.close()


def _with_automatic_description(store: OwnerStore) -> dict[str, dict[str, Any]]:
    """The case detail under a policy that sets Description to Automatic."""
    from unittest.mock import patch

    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )

    repository = store.repository
    snapshot = repository.read_current_review_case(store.case.review_case_id, None)
    builtin = MetadataPolicyProfile.builtin()
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Automatic description",
            dict(builtin.options) | {"description_fill": "safe_plain_text"},
            dict(builtin.destinations) | {"description_fill": "automatic"},
        ),
        snapshot.report.target.library,
    )
    with patch.object(
        repository,
        "read_current_review_case",
        return_value=replace(snapshot, resolved_policy_document=policy.canonical_json),
    ):
        detail = InspectCurrentReviewCase(repository).run(store.case.review_case_id)
    return {field["destination"]: field for field in detail["fields"]}


def test_a_substituted_choosable_field_gives_no_live_withheld_reason(
    tmp_path: Path,
) -> None:
    """Why Automatic withheld the live value is untrue beside a level value the
    owner can choose, so the substituted field gives none; every field the
    level does not substitute, and the live case, keep theirs (#1355)."""
    withheld = "No recorded automatic evaluation is available for this field."
    store = level_store(tmp_path / "level", live_description=False)
    try:
        fields = _with_automatic_description(store)
        description = fields["description"]
        assert [value["value"] for value in description["proposed_values"]] == [
            SYNOPSIS
        ]
        assert description["manual_required"] is True
        assert description["decision_actions"] == ["keep_current", "accept"]
        assert description["automatic_withheld_reason"] is None
        assert description["automatic_withheld_condition"] is None
        substituted = {
            value["destination"]
            for value in offer_of(store)
            if "work_identity_level" in value
        }
        # A field the level offer leaves alone keeps its reason.
        assert "genres" not in substituted
        assert fields["genres"]["automatic_withheld_reason"] == withheld
        # So does one the offer carries but leaves with nothing to choose.
        assert "subtitle" in substituted
        assert fields["subtitle"]["decision_actions"] == []
        assert fields["subtitle"]["automatic_withheld_reason"] == withheld
    finally:
        store.repository.close()
    live = level_store(
        tmp_path / "identifier", WorkIdentityLevel.IDENTIFIER, live_description=False
    )
    try:
        fields = _with_automatic_description(live)
        assert fields["description"]["automatic_withheld_reason"] == withheld
        assert fields["title"]["decision_actions"] == ["keep_current", "accept"]
        assert fields["title"]["automatic_withheld_reason"] == withheld
    finally:
        live.repository.close()


# --- Test 22, server half: no view carries the level's notes ------------------


def test_no_api_view_carries_would_admit_or_notes(tmp_path: Path) -> None:
    store = level_store(tmp_path)
    try:
        assert store.repository._connection.execute(
            "SELECT count(*) FROM level_decision_notes"
        ).fetchone()[0]
        case_id = store.case.review_case_id
        with owner_client(store, tmp_path) as (client, _csrf):
            views = [
                client.get(path)
                for path in (
                    f"/api/v1/review-cases/{case_id}",
                    f"/api/v1/review-cases/{case_id}/history",
                    f"/api/v1/review-cases/{case_id}/evidence",
                    "/api/v1/library?lens=all",
                    "/api/v1/attention",
                    "/api/v1/activity",
                )
            ]
        for view in views:
            assert view.status_code == 200, (view.url, view.text)
            assert "would_admit" not in view.text
            assert "decision_note" not in view.text
            assert "level_proposal_set" not in view.text
    finally:
        store.repository.close()


# --- AC2: a cause row is no human decision ------------------------------------


def cause_row(store: OwnerStore, earlier: HumanDecision) -> HumanDecision:
    """The valueless row P4's reconciliation writes (amendment 3), formed by
    the domain's own constructor."""
    row = HumanDecision.matching_changed(
        store.case, earlier, LEVEL.value, offer_of(store), earlier.occurred_at + 1
    )
    assert (row.outcome, row.selected_value, row.cause) == (
        "withdraw",
        None,
        "matching_changed",
    )
    assert row.idempotency_digest == human_decision._sha256(
        f"matching-changed-v1:{earlier.decision_id}:{LEVEL.value}"
    )
    assert row.offered_alternatives_digest == offer_digest(
        offer_of(store), "description"
    )
    return row


def test_a_cause_row_reads_as_no_human_decision(tmp_path: Path) -> None:
    store = level_store(tmp_path)
    try:
        earlier = choose(store, SYNOPSIS)
        store.repository.record_human_decision(earlier)
        row = cause_row(store, earlier)
        # Description cannot otherwise be withdrawn; a cause row is exempt.
        assert "description" in human_decision.UNREVERSIBLE_DESTINATIONS
        row.validate_for(
            store.case,
            stored_level_offer_documents(
                store.repository._connection, store.case.review_case_id
            ),
        )
        with pytest.raises(HumanDecisionError):
            resealed(row, cause=None).validate_for(store.case)
        seed(store, row)
        case_id = store.case.review_case_id
        with owner_client(store, tmp_path) as (client, _csrf):
            field = field_of(
                client.get(f"/api/v1/review-cases/{case_id}").json(), "description"
            )
            history = client.get(f"/api/v1/review-cases/{case_id}/history").json()
        assert field["human_decision"] is None
        (asked_again,) = (
            item
            for item in history["items"]
            if item.get("schema_version") == "matching-changed-history-event-v1"
        )
        assert asked_again["kind"] == "asked_again_matching"
        assert asked_again["destination"] == "description"
        assert asked_again["decision_id"] == row.decision_id
        assert not [
            item
            for item in history["items"]
            if item.get("decision_id") == row.decision_id
            and item.get("outcome") == "withdraw"
        ]
    finally:
        store.repository.close()


def test_a_reconstructed_case_reads_the_same_offer(tmp_path: Path) -> None:
    """The stored case is the case the offer was composed for."""
    store = level_store(tmp_path)
    try:
        envelope = json.loads(
            store.repository.get_review_case(store.case.review_case_id)
        )
        assert ReviewCase.create_from_data(envelope["case"]) == store.case
        assert _proposal(offer_of(store), "description")["work_identity_level"] == (
            LEVEL.value
        )
    finally:
        store.repository.close()
