"""The Covers view's page read, its replaced-cover preview and Keep (#1633)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, get_args

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.review_covers import (
    COVER_PAGE_SIZE,
    cover_page,
    replaced_cover_preview,
)
from mechanical_librarian.application.review_web import ReviewWebError
from mechanical_librarian.domain import field_effect
from mechanical_librarian.domain.cover_evidence import CoverArtifact
from mechanical_librarian.domain.human_decision import COVER_SELECTION_AUTHORITY_SCHEMA
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web import app
from tests.support.cover_operations import (
    automatic_switch,
    list_switch,
    retained_source,
)
from tests.support.cover_producers import _preview, _qualified_cover_report
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)

ORIGIN = "https://review.test"
# The original an automatic switch replaced, kept so it can be put back.
_ORIGINAL_BYTES = b"replaced original"
_ORIGINAL_PREVIEW = _preview(9)
_ORIGINAL = CoverArtifact.create(
    _ORIGINAL_BYTES, _ORIGINAL_PREVIEW, "image/png", 400, 600
)


@dataclass
class _Library:
    root: Path
    repository: SQLiteMetadataReportRepository
    service: CoverConsiderationService
    report: MetadataReport
    cases: dict[str, str]


def _page(
    store: Any, view: str, cursor: Any, cover: str | None = None
) -> dict[str, Any]:
    """``cover_page``'s document, typed for the assertions below."""
    return cast(dict[str, Any], cover_page(store, view, cursor, cover))


def _for_item(
    report: MetadataReport, item: str, title: str | None = None
) -> MetadataReport:
    """The same synthetic report for another work, under its own identity."""
    data = json.loads(report.to_json())
    data["target"]["item"] = item
    if title is not None:
        data["target"]["fields"][0]["value"] = title
    data.pop("report_id")
    data["report_id"] = report_content_address(data)
    return MetadataReport.from_json(_canonical_json(data))


@contextmanager
def _library(
    root: Path,
    items: tuple[str, ...] = ("item-1",),
    *,
    readable_current: bool = True,
    title: str | None = None,
    put_back: bool = False,
) -> Iterator[_Library]:
    """With ``put_back``, item-1's current cover is a verified automatic switch
    whose original is listed, and its report offers that original (#1363)."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    base, _ = _qualified_cover_report(root, readable_current=readable_current)
    artifacts = CoverArtifactStore(root / "artifacts")
    repository = open_repository(root / "application.sqlite3", cross_thread=True)
    switch = None
    if put_back:
        current = next(
            field.value
            for field in base.target.fields
            if field.destination is TargetDestination.COVER
        )
        switch = automatic_switch(
            base.target, _ORIGINAL.artifact_id, cast(str, current)
        )
        base = MetadataReport.create(
            target=base.target,
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=(
                *base.cover_sources,
                retained_source(switch.plan.plan_id, _ORIGINAL),
            ),
        )
        artifacts.stage_preview(_ORIGINAL, _ORIGINAL_PREVIEW)
        list_switch(
            repository._connection,
            switch,
            _ORIGINAL.original_digest,
            len(_ORIGINAL_BYTES),
        )
    sources = base.cover_sources
    service = CoverConsiderationService(
        repository,
        read_preview=lambda artifact_id, _variant: next(
            artifacts.preview(source.artifact)
            for source in sources
            if source.artifact is not None
            and source.artifact.artifact_id == artifact_id
        ),
        read_preview_digest=artifacts.read_preview_digest,
        require_artifact=artifacts.require_artifact,
        clock=lambda: 10,
        bind_policy=repository.bind_current_cover_authority,
        retained_original=repository.retained_cover_original,
        # The copy itself is checked in test_cover_retention.py.
        require_retained=lambda _digest: None,
    )
    # The live reads composition binds: cover currentness for attention, and
    # the owner cover recheck for a decision.
    repository.bind_cover_consideration_currentness(lambda *_target: False)
    repository.bind_owner_cover_validation(service.require_owner_cover)
    cases: dict[str, str] = {}
    try:
        for item in items:
            report = (
                base
                if item == "item-1" and title is None
                else _for_item(base, item, title)
            )
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            case_id = json.loads(repository.admit_review_case(proposal))[
                "review_case_id"
            ]
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            )
            service.ensure(
                case_id,
                report.to_json(),
                next(v for v in case.proposals if v["destination"] == "cover"),
            )
            cases[item] = case_id
        service.refresh_authority()
        yield _Library(root, repository, service, base, cases)
    finally:
        repository.close()


def _abs_digest(report: MetadataReport) -> str:
    artifact = next(
        source.artifact
        for source in report.cover_sources
        if source.kind.value == "abs" and source.artifact is not None
    )
    return artifact.original_digest


def _switch(
    library: _Library,
    item: str,
    *,
    plan: str,
    at: int,
    original: str | None,
    sequence: int | None = None,
    desired: str | None = None,
) -> str:
    """Record a verified automatic cover write, and its listed binding if any."""
    plan_id = "effect_plan_" + plan * 64
    connection = library.repository._connection
    connection.execute(
        "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
        "work_id,destination,state,attempt,updated_at,document) "
        "VALUES(?,?,?,?,?,'cover','verified',0,?,?)",
        (
            plan_id,
            "automatic_decision_" + plan * 64,
            "primary",
            "ebooks",
            item,
            at,
            json.dumps(
                {
                    "plan": {
                        "authority": {"origin": "automatic_policy"},
                        "pre_value": None if original is None else "cover_" + original,
                        "desired_value": desired,
                    }
                }
            ),
        ),
    )
    if original is not None:
        connection.execute(
            "INSERT INTO retained_cover_bindings(plan_id,decision_id,connection_name,"
            "library_name,work_id,original_digest,byte_count,retained_at) "
            "VALUES(?,?,?,?,?,?,1,?)",
            (
                plan_id,
                "automatic_decision_" + plan * 64,
                "primary",
                "ebooks",
                item,
                original,
                at,
            ),
        )
        connection.execute(
            "INSERT INTO retained_cover_listings(plan_id,sequence) VALUES(?,?)",
            (plan_id, sequence),
        )
    connection.commit()
    return plan_id


def _release(library: _Library, plan_id: str) -> None:
    library.repository._connection.execute(
        "INSERT INTO retained_cover_removals(plan_id,reason,state,attempt,updated_at) "
        "VALUES(?,'released','pending',0,1)",
        (plan_id,),
    )
    library.repository._connection.commit()


def test_a_readable_current_cover_with_choices_is_kept(tmp_path: Path) -> None:
    with _library(tmp_path) as library:
        page = _page(library.repository, "all", None)

    assert page["counts"] == {"decide": 0, "kept": 1, "switched": 0, "all": 1}
    assert page["schema_version"] == "review-covers-v1"
    assert page["next_cursor"] is None
    (item,) = page["items"]
    assert item["group"] == "kept"
    assert item["choosable"] is True
    assert item["switched"] is None
    assert item["review_case_id"] == library.cases["item-1"]
    assert item["unreadable_cover"] is False
    # The current cover is named by its preview through the library route only.
    assert str(item["cover"]["url"]).startswith("/api/v1/library/cover?")


def test_an_unreadable_current_cover_with_a_candidate_is_suggested(
    tmp_path: Path,
) -> None:
    with _library(tmp_path, readable_current=False) as library:
        decide = _page(library.repository, "decide", None)
        kept = _page(library.repository, "kept", None)
        # A suggestion is To decide, never Library's Needs attention (#1363, S9).
        assert (
            library.repository.read_review_library(
                None, 25, 0, "needs_attention"
            ).attention_total
            == 0
        )

    assert decide["counts"]["decide"] == 1
    assert [item["group"] for item in decide["items"]] == ["proposal"]
    assert decide["items"][0]["suggestion"] == {"reason": "unreadable"}
    assert decide["items"][0]["hold"] is None
    # The library holds a cover it could not read, which is not "No cover".
    (item,) = decide["items"]
    assert item["cover"] is None
    assert item["unreadable_cover"] is True
    assert kept["items"] == []


def test_a_suggestion_the_owner_cannot_choose_is_not_to_decide(tmp_path: Path) -> None:
    """With no decision to record, a suggestion is listed only under All (M4)."""
    with _library(tmp_path, readable_current=False) as library:
        library.repository._connection.execute(
            "UPDATE review_case_field_facts SET has_owner_decision_actions=0 "
            "WHERE destination='cover'"
        )
        library.repository._connection.commit()
        decide = _page(library.repository, "decide", None)
        everything = _page(library.repository, "all", None)

    assert decide["items"] == []
    assert everything["counts"] == {"decide": 0, "kept": 0, "switched": 0, "all": 1}
    (item,) = everything["items"]
    assert (item["group"], item["choosable"], item["suggestion"]) == (
        "none",
        False,
        None,
    )


def test_a_held_switch_carries_its_reason(tmp_path: Path) -> None:
    """A hold's reason reaches its row's pill, never as a suggestion (M7)."""
    with _library(tmp_path, readable_current=False) as library:
        # A synthetic hold over the stored stage the page reads.
        library.repository._connection.execute(
            "DROP TRIGGER cover_considerations_no_update"
        )
        library.repository._connection.execute(
            "UPDATE cover_considerations SET currentness_document=json_set("
            "currentness_document,'$.cover_stage.outcome','hold',"
            "'$.cover_stage.reason','title_names_other_work')"
        )
        library.repository._connection.commit()
        everything = _page(library.repository, "all", None)

    (item,) = everything["items"]
    assert item["hold"] == {"reason": "title_names_other_work"}
    assert item["suggestion"] is None
    assert item["group"] != "proposal"


def test_switched_lists_listed_switches_newest_first_and_never_a_released_one(
    tmp_path: Path,
) -> None:
    with _library(tmp_path, ("item-1", "item-2", "item-3")) as library:
        original = _abs_digest(library.report)
        older = _switch(
            library, "item-1", plan="1", at=100, original=original, sequence=1
        )
        # Verified in the same second: the listing sequence orders them.
        newer = _switch(
            library, "item-2", plan="2", at=100, original=original, sequence=2
        )
        _switch(library, "item-3", plan="3", at=50, original=None)

        page = _page(library.repository, "switched", None)
        assert page["counts"]["switched"] == 3
        assert [item["review_case_id"] for item in page["items"]] == [
            library.cases["item-2"],
            library.cases["item-1"],
            library.cases["item-3"],
        ]
        first, _, fill = page["items"]
        assert first["group"] == "auto"
        assert first["switched"]["at"] == 100
        assert first["switched"]["filled"] is False
        # Listed, but its work was not prepared again since the switch, so its
        # comparison lacks the original and Put back is not offered (#1363).
        assert first["switched"]["put_back_offered"] is False
        before = first["switched"]["before"]
        assert str(before["url"]).startswith(f"/api/v1/covers/replaced?plan={newer}")
        # A fill replaced nothing, so it has no Before and offers nothing back.
        assert fill["switched"] == {
            "at": 50,
            "before": None,
            "filled": True,
            "language": None,
            "put_back": None,
            "put_back_offered": False,
            "reason": None,
        }
        assert (
            replaced_cover_preview(library.repository, older, before["preview_sha256"])
            == before["preview_sha256"]
        )

        _release(library, older)
        page = _page(library.repository, "switched", None)
        assert [item["review_case_id"] for item in page["items"]] == [
            library.cases["item-2"],
            library.cases["item-3"],
        ]
        # The released switch's work falls back to its own group.
        assert _page(library.repository, "kept", None)["counts"]["kept"] == 1
        with pytest.raises(ReviewWebError) as missing:
            replaced_cover_preview(library.repository, older, before["preview_sha256"])
        assert missing.value.code == "missing"


def test_a_switch_shows_the_cover_it_wrote_as_now_before_a_scan(
    tmp_path: Path,
) -> None:
    with _library(tmp_path) as library:
        written = next(
            source.artifact
            for source in library.report.cover_sources
            if source.artifact is not None
            and source.artifact.original_digest != _abs_digest(library.report)
        )
        plan = _switch(
            library,
            "item-1",
            plan="1",
            at=100,
            original=_abs_digest(library.report),
            sequence=1,
            desired=written.artifact_id,
        )
        (item,) = _page(library.repository, "switched", None)["items"]
        now, before = item["cover"], item["switched"]["before"]
        # The report still holds the pre-write cover until the next scan, so
        # Now comes from the verified plan, not the report.
        assert str(now["url"]).startswith(f"/api/v1/covers/replaced?plan={plan}")
        assert now["preview_sha256"] != before["preview_sha256"]
        assert item["unreadable_cover"] is False
        for preview in (now, before):
            assert (
                replaced_cover_preview(
                    library.repository, plan, preview["preview_sha256"]
                )
                == preview["preview_sha256"]
            )
        _release(library, plan)
        assert (
            replaced_cover_preview(library.repository, plan, now["preview_sha256"])
            == now["preview_sha256"]
        )
        with pytest.raises(ReviewWebError) as missing:
            replaced_cover_preview(library.repository, plan, before["preview_sha256"])
        assert missing.value.code == "missing"


def test_a_works_newest_listed_switch_is_its_highest_listing_sequence(
    tmp_path: Path,
) -> None:
    with _library(tmp_path) as library:
        original = _abs_digest(library.report)
        # After a clock step the newer switch carries the earlier time.
        _switch(library, "item-1", plan="1", at=200, original=original, sequence=1)
        newer = _switch(
            library, "item-1", plan="2", at=100, original=original, sequence=2
        )
        page = _page(library.repository, "switched", None)

    (item,) = page["items"]
    assert item["switched"]["at"] == 100
    assert str(item["switched"]["before"]["url"]).startswith(
        f"/api/v1/covers/replaced?plan={newer}"
    )


def test_only_the_newest_cover_write_of_a_work_is_listed(tmp_path: Path) -> None:
    with _library(tmp_path) as library:
        original = _abs_digest(library.report)
        _switch(library, "item-1", plan="1", at=100, original=original, sequence=1)
        assert _page(library.repository, "switched", None)["counts"]["switched"] == 1
        # A later write that is not a listed switch replaces it as current.
        connection = library.repository._connection
        connection.execute(
            "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
            "work_id,destination,state,attempt,updated_at,document) "
            "VALUES(?,'human_decision_x','primary','ebooks','item-1','cover',"
            "'verified',0,200,?)",
            (
                "effect_plan_" + "4" * 64,
                json.dumps({"plan": {"authority": {"origin": "human"}}}),
            ),
        )
        connection.commit()
        assert _page(library.repository, "switched", None)["counts"]["switched"] == 0


@pytest.mark.parametrize(
    ("plan", "preview"),
    [
        ("effect_plan_" + "z" * 64, "a" * 64),
        ("plan", "a" * 64),
        ("effect_plan_" + "1" * 64, "A" * 64),
        ("effect_plan_" + "1" * 64, "a" * 64),
    ],
    ids=["bad-plan-digest", "bad-plan-prefix", "bad-preview", "unlisted"],
)
def test_a_replaced_preview_is_missing_unless_listed_and_bound(
    tmp_path: Path, plan: str, preview: str
) -> None:
    with _library(tmp_path) as library:
        with pytest.raises(ReviewWebError) as error:
            replaced_cover_preview(library.repository, plan, preview)
    assert error.value.code == "missing"


def test_pages_hold_fifty_books_and_a_cursor_reads_the_rest(tmp_path: Path) -> None:
    items = tuple(f"item-{index}" for index in range(1, COVER_PAGE_SIZE + 3))
    with _library(tmp_path, items) as library:
        first = _page(library.repository, "kept", None)
        assert len(first["items"]) == COVER_PAGE_SIZE
        assert first["counts"]["kept"] == COVER_PAGE_SIZE + 2
        cursor = first["next_cursor"]
        assert isinstance(cursor, str)
        # A book on the first page leaves the tab, as a decision moves it: the
        # next page resumes after the last book shown, so none is skipped.
        _switch(library, "item-1", plan="1", at=100, original=None)
        second = _page(library.repository, "kept", cursor)
        assert len(second["items"]) == 2
        assert second["next_cursor"] is None
        assert second["counts"]["kept"] == COVER_PAGE_SIZE + 1
        shown = {item["review_case_id"] for item in first["items"] + second["items"]}
        assert shown == set(library.cases.values())
        # A cursor is bound to its tab.
        with pytest.raises(ReviewWebError):
            _page(library.repository, "all", cursor)
    with pytest.raises(ReviewWebError):
        _page(library.repository, "everything", None)


def test_a_linked_book_is_read_on_its_own_wherever_it_falls(tmp_path: Path) -> None:
    items = tuple(f"item-{index}" for index in range(1, COVER_PAGE_SIZE + 3))
    with _library(tmp_path, items) as library:
        # The last book by title, past the first page.
        last = _page(
            library.repository,
            "all",
            _page(library.repository, "all", None)["next_cursor"],
        )["items"][-1]
        page = _page(library.repository, "all", None, last["review_case_id"])
        assert page["items"] == [last]
        assert page["next_cursor"] is None
        assert page["counts"]["all"] == COVER_PAGE_SIZE + 2
        # Only under a tab that holds it, and never with a cursor.
        assert (
            _page(library.repository, "switched", None, last["review_case_id"])["items"]
            == []
        )
        for cursor, cover in (("x", last["review_case_id"]), (None, "review_case_x")):
            with pytest.raises(ReviewWebError) as refused:
                _page(library.repository, "all", cursor, cover)
            assert refused.value.code == "request"


@contextmanager
def _client(library: _Library) -> Iterator[tuple[TestClient, dict[str, str]]]:
    hash_path = library.root / "owner.hash"
    hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(hash_path, 0o600)
    client = TestClient(
        create_transport_test_app(
            library.repository,
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
                canonical_origin=ORIGIN,
                owner_password_hash_file=str(hash_path),
            ),
            clock=lambda: 1_000,
            cover_considerations=library.service,
        ),
        base_url=ORIGIN,
    )
    try:
        login = client.post(
            "/api/v1/session/login",
            headers={"Origin": ORIGIN},
            json={"password": "correct horse"},
        ).json()
        yield (
            client,
            {
                "Origin": ORIGIN,
                "X-CSRF-Token": login["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            },
        )
    finally:
        client.close()


def test_the_covers_route_is_private_bounded_and_serves_previews_only(
    tmp_path: Path,
) -> None:
    with _library(tmp_path, ("item-1", "item-2")) as library:
        original = _abs_digest(library.report)
        plan_id = _switch(
            library, "item-1", plan="1", at=100, original=original, sequence=1
        )
        anonymous = TestClient(
            create_transport_test_app(
                library.repository,
                ReviewWebConfiguration(
                    tls_certificate_file="/synthetic/review.crt",
                    tls_private_key_file="/synthetic/review.key",
                    canonical_origin=ORIGIN,
                    owner_password_hash_file=str(_hash(library.root)),
                ),
                clock=lambda: 1_000,
            ),
            base_url=ORIGIN,
        )
        try:
            assert anonymous.get("/api/v1/covers").status_code == 401
        finally:
            anonymous.close()
        with _client(library) as (client, _headers):
            response = client.get("/api/v1/covers?view=switched")
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store, private"
            body = response.json()
            assert body["counts"] == {"decide": 0, "kept": 1, "switched": 1, "all": 2}
            (item,) = body["items"]
            serialized = json.dumps(body)
            # Previews only: no original digest, artifact identity or path.
            assert original not in serialized
            assert "cover_" not in serialized
            assert "retained" not in serialized
            before = client.get(item["switched"]["before"]["url"])
            assert before.status_code == 200
            assert before.headers["content-type"] == "image/png"
            assert before.headers["cache-control"] == "no-store, private"
            assert client.get("/api/v1/covers?view=everything").status_code == 422
            assert client.get("/api/v1/covers?cursor=bogus").status_code == 400
            linked = client.get(
                f"/api/v1/covers?view=all&cover={library.cases['item-2']}"
            ).json()
            assert [work["review_case_id"] for work in linked["items"]] == [
                library.cases["item-2"]
            ]
            _release(library, plan_id)
            assert client.get(item["switched"]["before"]["url"]).status_code == 404


def _hash(root: Path) -> Path:
    path = root / "anonymous.hash"
    path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def test_keeping_a_switched_cover_releases_its_original_and_leaves_switched(
    tmp_path: Path,
) -> None:
    with _library(tmp_path) as library:
        case_id = library.cases["item-1"]
        with _client(library) as (client, headers):
            detail = client.get(f"/api/v1/review-cases/{case_id}")
            assert detail.status_code == 200, detail.text
            field = next(
                value
                for value in detail.json()["fields"]
                if value["destination"] == "cover"
            )
            # The switch is recorded after the page read the work, as the
            # Covers view's row would be.
            plan_id = _switch(
                library,
                "item-1",
                plan="1",
                at=100,
                original=_abs_digest(library.report),
                sequence=1,
            )
            response = client.post(
                f"/api/v1/review-cases/{case_id}/decisions",
                headers={**headers, "Idempotency-Key": "keep-switched-cover-0001"},
                json={
                    "destination": "cover",
                    "outcome": "keep_current",
                    "proposal_id": field["proposal_id"],
                    "selected_value": None,
                    "target_fingerprint": field["target_fingerprint"],
                    "consideration_id": field["cover_consideration"][
                        "consideration_id"
                    ],
                    "cover_confirmation": {
                        "expected_selection_id": field["current_cover_selection_id"],
                        "confirm_unverified_match": False,
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert library.repository._connection.execute(
                "SELECT plan_id,reason,state FROM retained_cover_removals"
            ).fetchall() == [(plan_id, "released", "pending")]
            switched = client.get("/api/v1/covers?view=switched").json()
            assert switched["items"] == []


def test_a_cursor_carries_the_longest_accepted_title_and_item(
    tmp_path: Path,
) -> None:
    """A page ending on a long title or item id still reads the next page."""
    title = "\u66f8" * 512
    items = tuple(
        f"{index:03d}-" + "i" * 500 for index in range(1, COVER_PAGE_SIZE + 3)
    )
    with _library(tmp_path, items, title=title) as library:
        first = _page(library.repository, "all", None)
        cursor = first["next_cursor"]
        assert isinstance(cursor, str)
        second = _page(library.repository, "all", cursor)
        shown = [item["review_case_id"] for item in first["items"] + second["items"]]
        assert sorted(shown) == sorted(library.cases.values())
        assert len(set(shown)) == len(shown)


def test_a_put_back_reads_in_the_field_effect_vocabulary_exactly() -> None:
    """The wire names a Put back's write by the effect's own closed values (#1363)."""
    fields = app._CoverPutBack.model_fields
    state = fields["state"].annotation
    failure = fields["failure"].annotation
    assert set(get_args(state)) == set(field_effect._STATES)
    (failures, _none) = get_args(failure)
    assert set(get_args(failures)) | {None} == set(field_effect.FIELD_EFFECT_FAILURES)


def _owner_answer(
    library: _Library, item: str, *, retained_plan: str | None, at: int
) -> str:
    """Record the owner's current cover answer for one work, and return its id.

    A Put back's v3 document names the switch it undoes. The consideration
    that would bind it is outside this read, so its foreign key is not
    enforced while it is written.
    """
    decision_id = "human_decision_" + "9" * 64
    selection_id = "cover_selection_" + "9" * 64
    document = {
        "candidate_kind": "candidate" if retained_plan is None else "retained",
        "retained_plan_id": retained_plan,
        "schema_version": COVER_SELECTION_AUTHORITY_SCHEMA,
    }
    connection = library.repository._connection
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute(
            "INSERT INTO cover_selection_authorities(selection_id,connection_name,"
            "library_name,item_id,kind,decision_id,consideration_id,"
            "supersedes_selection_id,occurred_at,document) "
            "VALUES(?,'primary','ebooks',?,'owner',?,?,NULL,?,?)",
            (
                selection_id,
                item,
                decision_id,
                "cover_consideration_" + "9" * 64,
                at,
                json.dumps(document),
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO current_cover_selection_authorities("
            "connection_name,library_name,item_id,selection_id) "
            "VALUES('primary','ebooks',?,?)",
            (item, selection_id),
        )
        connection.commit()
    finally:
        connection.execute("PRAGMA foreign_keys=ON")
    return decision_id


@pytest.mark.parametrize("put_back", (True, False), ids=("put_back", "other_answer"))
def test_a_put_back_keeps_its_switch_listed_with_its_writes_state(
    tmp_path: Path, put_back: bool
) -> None:
    """Only a Put back of the switch keeps it listed, carrying its write (#1363, Q18)."""
    with _library(tmp_path) as library:
        plan_id = _switch(
            library,
            "item-1",
            plan="1",
            at=100,
            original=_abs_digest(library.report),
            sequence=1,
        )
        decision_id = _owner_answer(
            library, "item-1", retained_plan=plan_id if put_back else None, at=200
        )

        page = _page(library.repository, "switched", None)
        if not put_back:
            # Any other answer after the switch takes the work out of Switched.
            assert page["items"] == []
            return
        (row,) = page["items"]
        # Recorded, and its write not yet planned.
        assert row["switched"]["put_back"] == {"state": "planned", "failure": None}

        library.repository._connection.execute(
            "INSERT INTO field_effects(plan_id,decision_id,connection_name,"
            "library_name,work_id,destination,state,attempt,updated_at,document) "
            "VALUES(?,?,'primary','ebooks','item-1','cover','stale',0,300,?)",
            (
                "effect_plan_" + "7" * 64,
                decision_id,
                json.dumps({"failure": "target_changed"}),
            ),
        )
        library.repository._connection.commit()
        (row,) = _page(library.repository, "switched", None)["items"]
        assert row["switched"]["put_back"] == {
            "state": "stale",
            "failure": "target_changed",
        }


@pytest.mark.parametrize("released", (False, True), ids=("listed", "released"))
def test_put_back_records_the_original_only_while_its_copy_is_listed(
    tmp_path: Path, released: bool
) -> None:
    """Put back is the owner's accept of the original (#1363, D3, D5).

    While the copy is listed it records, and the row stays in Switched with
    its write planned. Once released, the same command is refused as stale
    before anything commits (ADR-0091).
    """
    with _library(tmp_path, put_back=True) as library:
        case_id = library.cases["item-1"]
        with _client(library) as (client, headers):
            detail = client.get(f"/api/v1/review-cases/{case_id}")
            assert detail.status_code == 200, detail.text
            field = next(
                value
                for value in detail.json()["fields"]
                if value["destination"] == "cover"
            )
            consideration = field["cover_consideration"]
            (original,) = (
                candidate
                for candidate in consideration["candidates"]
                if candidate["candidate_kind"] == "retained"
            )
            assert original["artifact_id"] == _ORIGINAL.artifact_id
            assert original["selectable"] is True
            # The original's preview is never cached; a reviewed one is
            # immutable (data-privacy, "Prior cover bytes").
            images = f"/api/v1/review-cases/{case_id}/cover-considerations/" + str(
                consideration["consideration_id"]
            )
            for candidate in consideration["candidates"]:
                image = client.get(f"{images}/images/{candidate['reference']}")
                assert image.status_code == 200, image.text
                assert image.headers["Cache-Control"] == (
                    "no-store, private"
                    if candidate["candidate_kind"] == "retained"
                    else "private, max-age=31536000, immutable"
                )
            plan_id = library.repository._connection.execute(
                "SELECT plan_id FROM retained_cover_listings"
            ).fetchone()[0]
            # Its comparison has the original, so Covers offers Put back.
            (listed,) = client.get("/api/v1/covers?view=switched").json()["items"]
            assert listed["switched"]["put_back_offered"] is True
            if released:
                _release(library, plan_id)

            response = client.post(
                f"/api/v1/review-cases/{case_id}/decisions",
                headers={**headers, "Idempotency-Key": "put-back-original-0001"},
                json={
                    "destination": "cover",
                    "outcome": "accept",
                    "proposal_id": field["proposal_id"],
                    "selected_value": {
                        "kind": "cover",
                        "value": original["artifact_id"],
                    },
                    "target_fingerprint": field["target_fingerprint"],
                    "consideration_id": consideration["consideration_id"],
                    "cover_confirmation": {
                        "expected_selection_id": field["current_cover_selection_id"],
                        "confirm_unverified_match": True,
                    },
                },
            )

            owner = library.repository._connection.execute(
                "SELECT json_extract(document,'$.candidate_kind'),"
                "json_extract(document,'$.retained_plan_id') "
                "FROM cover_selection_authorities WHERE kind='owner'"
            ).fetchall()
            switched = client.get("/api/v1/covers?view=switched").json()["items"]
            if released:
                assert response.status_code == 409, response.text
                assert response.json()["error"]["code"] == "stale"
                assert owner == []
                assert switched == []
                return
            assert response.status_code == 200, response.text
            assert owner == [("retained", plan_id)]
            (row,) = switched
            assert row["switched"]["put_back"] == {"state": "planned", "failure": None}
            # The recorded Put back's comparison still offers the original.
            assert row["switched"]["put_back_offered"] is True
