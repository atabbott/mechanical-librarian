"""The Covers view's page read, its replaced-cover preview and Keep (#1633),
and the calibration set (#1372 PR 3)."""

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
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.review_covers import (
    COVER_PAGE_SIZE,
    CalibrationRow,
    CoverRow,
    cover_calibration,
    cover_page,
    replaced_cover_preview,
    select_calibration,
)
from mechanical_librarian.application.review_web import ReviewWebError
from mechanical_librarian.domain import field_effect
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.cover_rules import CoverMeasures
from mechanical_librarian.domain.human_decision import COVER_SELECTION_AUTHORITY_SCHEMA
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
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


# A calibration library's base report offers up to three more candidates,
# each its own art, which a work keeps or drops (``_Work.extra``).
_EXTRA_MAX = 3


def _extra_source_id(index: int) -> str:
    return cover_source_id(CoverSourceKind.HARDCOVER, f"extra-{index}")


def _extra_sources(root: Path) -> tuple[CoverSource, ...]:
    artifacts = CoverArtifactStore(root / "artifacts")
    sources = []
    for index in range(_EXTRA_MAX):
        payload = _preview(20 + index)
        original = f"extra-original-{index}".encode()
        artifact = CoverArtifact.create(
            original, payload, "image/png", 320 + index, 480 + index
        )
        artifacts.stage(artifact, original, payload)
        sources.append(
            CoverSource(
                _extra_source_id(index),
                CoverSourceKind.HARDCOVER,
                CoverSourceState.RETRIEVED,
                artifact,
                provenance=CoverProvenance(
                    "hardcover", CoverDesignation.EXACT_EDITION, "work-3", "edition-3"
                ),
            )
        )
    return tuple(sources)


@dataclass(frozen=True)
class _Work:
    """One work's own facts in a calibration library: its authors, its series
    and how many of the library's extra candidates it offers."""

    authors: tuple[str, ...] = ()
    series: tuple[str, ...] = ()
    extra: int = 0


def _for_item(
    report: MetadataReport,
    item: str,
    title: str | None = None,
    work: _Work | None = None,
) -> MetadataReport:
    """The same synthetic report for another work, under its own identity.

    With ``work``, the report names its authors and series, and offers only
    the first ``work.extra`` of the base report's extra candidates."""
    data = json.loads(report.to_json())
    data["target"]["item"] = item
    if title is not None:
        data["target"]["fields"][0]["value"] = title
    if work is not None:
        for field in data["target"]["fields"]:
            if field["destination"] == "authors" and work.authors:
                field.update(state="value", value=list(work.authors))
            if field["destination"] == "series" and work.series:
                field.update(
                    state="value",
                    value=[{"name": name, "sequence": None} for name in work.series],
                )
        dropped = {_extra_source_id(index) for index in range(work.extra, _EXTRA_MAX)}
        data["covers"] = [
            source for source in data["covers"] if source["source_id"] not in dropped
        ]
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
    undecodable: bool = False,
    abs_state: CoverSourceState | None = None,
    abs_digest: str | None = None,
    fetching: bool = False,
    works: dict[str, _Work] | None = None,
    shared_measures: CoverMeasures | None = None,
    small_current: bool = False,
    larger_copy: bool = False,
) -> Iterator[_Library]:
    """With ``fetching``, every report still waits on a provider lookup.
    With ``works``, the base report offers three extra candidates and each
    named work keeps its own authors, series and share of them; with
    ``shared_measures`` every image is one art; with ``small_current`` the current
    cover is under the size floor, and with ``larger_copy`` too a larger copy
    of it is offered.
    With ``put_back``, item-1's current cover is a verified automatic switch
    whose original is listed, and its report offers that original (#1363).
    With ``undecodable`` too, the decoder refused that original, so the
    report offers it by its digest with no preview (#1362)."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    base, _ = _qualified_cover_report(
        root,
        readable_current=readable_current,
        abs_state=abs_state,
        abs_digest=abs_digest,
        shared_measures=shared_measures,
        incumbent_size=(200, 300) if small_current or larger_copy else None,
        incumbent_duplicate_size=(2_000, 3_000) if larger_copy else None,
    )
    if works is not None:
        base = MetadataReport.create(
            target=base.target,
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=tuple(
                sorted(
                    (*base.cover_sources, *_extra_sources(root)),
                    key=lambda value: value.source_id,
                )
            ),
        )
    artifacts = CoverArtifactStore(root / "artifacts")
    repository = open_repository(root / "application.sqlite3", cross_thread=True)
    switch = None
    if put_back:
        # The switch wrote the bytes Audiobookshelf now serves, whatever the
        # target value names (#1853).
        switch = automatic_switch(
            base.target, _ORIGINAL.artifact_id, "cover_" + _abs_digest(base)
        )
        base = MetadataReport.create(
            target=base.target,
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=(
                *base.cover_sources,
                CoverSource(
                    cover_source_id(CoverSourceKind.RETAINED, switch.plan.plan_id),
                    CoverSourceKind.RETAINED,
                    CoverSourceState.DECODE_FAILED,
                    original_digest=_ORIGINAL.original_digest,
                )
                if undecodable
                else retained_source(switch.plan.plan_id, _ORIGINAL),
            ),
        )
        if not undecodable:
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
        provider_fetching=lambda _report: fetching,
    )
    # The live reads composition binds: cover currentness for attention, and
    # the owner cover recheck for a decision.
    repository.bind_cover_consideration_currentness(lambda *_target: False)
    repository.bind_owner_cover_validation(service.require_owner_cover)
    cases: dict[str, str] = {}
    try:
        for item in items:
            work = None if works is None else works.get(item, _Work())
            report = (
                base
                if item == "item-1" and title is None and work is None
                else _for_item(base, item, title, work)
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
    # The decoder refused bytes whose digest was not recorded, so the switch
    # cannot keep them and is only suggested (#1362 S4).
    with _library(
        tmp_path, readable_current=False, abs_state=CoverSourceState.DECODE_FAILED
    ) as library:
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
                    "offered_alternatives_digest": field["offer_digest"],
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


def _post_cover_decision(
    client: TestClient,
    headers: dict[str, str],
    case_id: str,
    key: str,
    *,
    outcome: str = "keep_current",
    kind: str | None = None,
) -> Any:
    """POST an owner cover decision as the book page does.

    An accept names the comparison's one candidate of ``kind``.
    """
    detail = client.get(f"/api/v1/review-cases/{case_id}")
    assert detail.status_code == 200, detail.text
    field = next(
        value for value in detail.json()["fields"] if value["destination"] == "cover"
    )
    consideration = field["cover_consideration"]
    selected = None
    if outcome == "accept":
        (candidate,) = (
            value
            for value in consideration["candidates"]
            if value["incumbent"] is False and value["candidate_kind"] == kind
        )
        selected = {"kind": "cover", "value": candidate["artifact_id"]}
    return client.post(
        f"/api/v1/review-cases/{case_id}/decisions",
        headers={**headers, "Idempotency-Key": key},
        json={
            "destination": "cover",
            "outcome": outcome,
            "proposal_id": field["proposal_id"],
            "selected_value": selected,
            "target_fingerprint": field["target_fingerprint"],
            "offered_alternatives_digest": field["offer_digest"],
            "consideration_id": consideration["consideration_id"],
            "cover_confirmation": {
                "expected_selection_id": field["current_cover_selection_id"],
                "confirm_unverified_match": outcome == "accept",
            },
        },
    )


@pytest.mark.parametrize(
    ("abs_state", "abs_digest"),
    (
        (CoverSourceState.TIMEOUT, None),
        (CoverSourceState.ABSENT, None),
        (CoverSourceState.DECODE_FAILED, "d" * 64),
    ),
    ids=("transient", "404", "decoder_refused"),
)
def test_keeping_a_current_cover_that_could_not_be_read_is_recorded_and_kept(
    tmp_path: Path, abs_state: CoverSourceState, abs_digest: str | None
) -> None:
    """#1865 (a): the page's Keep current cover records in all three states."""
    with _library(
        tmp_path, readable_current=False, abs_state=abs_state, abs_digest=abs_digest
    ) as library:
        case_id = library.cases["item-1"]
        with _client(library) as (client, headers):
            response = _post_cover_decision(
                client, headers, case_id, "keep-unread-cover-01"
            )
            assert response.status_code == 200, response.text
            kept = client.get("/api/v1/covers?view=kept").json()
            assert [item["review_case_id"] for item in kept["items"]] == [case_id]
            assert kept["items"][0]["group"] == "kept"
            assert kept["items"][0]["unreadable_cover"] is True
            assert client.get("/api/v1/covers?view=decide").json()["items"] == []


@pytest.mark.parametrize(
    "answer", ("chosen", "written", "kept", "put_back"), ids=lambda value: value
)
def test_a_chosen_cover_not_yet_written_is_your_choice_under_all_only(
    tmp_path: Path, answer: str
) -> None:
    """#1866 S4: a proposed cover the owner chose is not listed as kept.

    It reads ``chosen`` under All until its write verifies, then kept; a
    keep is kept, and a Put back stays with its switch.
    """
    with _library(tmp_path, put_back=answer == "put_back") as library:
        case_id = library.cases["item-1"]
        with _client(library) as (client, headers):
            response = _post_cover_decision(
                client,
                headers,
                case_id,
                "owner-cover-answer-0001",
                outcome="keep_current" if answer == "kept" else "accept",
                kind="retained" if answer == "put_back" else "candidate",
            )
            assert response.status_code == 200, response.text
            if answer == "written":
                decision_id = library.repository._connection.execute(
                    "SELECT decision_id FROM cover_selection_authorities "
                    "WHERE kind='owner'"
                ).fetchone()[0]
                library.repository._connection.execute(
                    "INSERT INTO field_effects(plan_id,decision_id,connection_name,"
                    "library_name,work_id,destination,state,attempt,updated_at,"
                    "document) VALUES(?,?,'primary','ebooks','item-1','cover',"
                    "'verified',0,300,?)",
                    (
                        "effect_plan_" + "8" * 64,
                        decision_id,
                        json.dumps({"plan": {"authority": {"origin": "owner"}}}),
                    ),
                )
                library.repository._connection.commit()
            every = client.get("/api/v1/covers?view=all").json()
            kept = client.get("/api/v1/covers?view=kept").json()
            decide = client.get("/api/v1/covers?view=decide").json()

    group = {
        "chosen": "chosen",
        "written": "kept",
        "kept": "kept",
        "put_back": "auto",
    }[answer]
    (row,) = every["items"]
    assert row["group"] == group
    assert decide["items"] == []
    assert every["counts"] == {
        "decide": 0,
        "kept": 1 if group == "kept" else 0,
        "switched": 1 if group == "auto" else 0,
        "all": 1,
    }
    assert [item["review_case_id"] for item in kept["items"]] == (
        [case_id] if group == "kept" else []
    )
    if group == "chosen":
        # The thumbnail stays the cover Audiobookshelf holds now (Q5).
        assert str(row["cover"]["url"]).startswith("/api/v1/library/cover?")
        assert row["choosable"] is True


def test_a_chosen_cover_stays_your_choice_after_a_new_comparison(
    tmp_path: Path,
) -> None:
    """#1866 S4: a policy change that prepares a new comparison leaves the
    owner's pending choice current, so the work still reads ``chosen``."""
    with _library(tmp_path) as library:
        case_id = library.cases["item-1"]
        with _client(library) as (client, headers):
            response = _post_cover_decision(
                client,
                headers,
                case_id,
                "owner-cover-answer-0001",
                outcome="accept",
                kind="candidate",
            )
            assert response.status_code == 200, response.text
            before = library.repository._connection.execute(
                "SELECT consideration_id FROM current_cover_considerations "
                "WHERE review_case_id=?",
                (case_id,),
            ).fetchone()[0]
            library.service.update_policy(CoverPolicy(prefer_edition=True))
            case = ReviewCase.create_from_data(
                json.loads(library.repository.get_review_case(case_id))["case"]
            )
            library.service.ensure(
                case_id,
                library.report.to_json(),
                next(v for v in case.proposals if v["destination"] == "cover"),
            )
            after = library.repository._connection.execute(
                "SELECT consideration_id FROM current_cover_considerations "
                "WHERE review_case_id=?",
                (case_id,),
            ).fetchone()[0]
            every = client.get("/api/v1/covers?view=all").json()

    assert after != before
    (row,) = every["items"]
    assert row["group"] == "chosen"
    assert every["counts"]["kept"] == 0


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
                    "offered_alternatives_digest": field["offer_digest"],
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


def test_an_original_that_does_not_decode_shows_no_preview_and_can_be_put_back(
    tmp_path: Path,
) -> None:
    """The kept original the decoder refused (#1362, S11): its card and the
    Switched row's Before have no image, and Put back still records."""
    with _library(tmp_path, put_back=True, undecodable=True) as library:
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
            assert original["original_sha256"] == _ORIGINAL.original_digest
            assert (
                original["height"],
                original["width"],
                original["preview_sha256"],
                original["transport_sha256"],
            ) == (None, None, None, None)
            assert (original["selectable"], original["manual_eligible"]) == (
                True,
                True,
            )
            # Never also listed as a cover not shown (#1362).
            assert original["reference"] not in {
                omission["reference"] for omission in consideration["omissions"]
            }
            image = client.get(
                f"/api/v1/review-cases/{case_id}/cover-considerations/"
                f"{consideration['consideration_id']}/images/{original['reference']}"
            )
            assert image.status_code == 404, image.text

            (listed,) = client.get("/api/v1/covers?view=switched").json()["items"]
            assert listed["switched"]["before"] is None
            assert listed["switched"]["filled"] is False
            assert listed["switched"]["put_back_offered"] is True

            response = client.post(
                f"/api/v1/review-cases/{case_id}/decisions",
                headers={**headers, "Idempotency-Key": "put-back-undecodable-0001"},
                json={
                    "destination": "cover",
                    "outcome": "accept",
                    "proposal_id": field["proposal_id"],
                    "selected_value": {
                        "kind": "cover",
                        "value": original["artifact_id"],
                    },
                    "target_fingerprint": field["target_fingerprint"],
                    "offered_alternatives_digest": field["offer_digest"],
                    "consideration_id": consideration["consideration_id"],
                    "cover_confirmation": {
                        "expected_selection_id": field["current_cover_selection_id"],
                        "confirm_unverified_match": True,
                    },
                },
            )
            assert response.status_code == 200, response.text
            plan_id = library.repository._connection.execute(
                "SELECT plan_id FROM retained_cover_listings"
            ).fetchone()[0]
            owner = library.repository._connection.execute(
                "SELECT json_extract(document,'$.candidate_kind'),"
                "json_extract(document,'$.retained_plan_id'),"
                "json_extract(document,'$.candidate_artifact_id') "
                "FROM cover_selection_authorities WHERE kind='owner'"
            ).fetchall()
            assert owner == [("retained", plan_id, _ORIGINAL.artifact_id)]
            (row,) = client.get("/api/v1/covers?view=switched").json()["items"]
            assert row["switched"]["before"] is None
            assert row["switched"]["put_back"] == {"state": "planned", "failure": None}


def _attention_mirrors(library: _Library) -> tuple[bool, bool]:
    """The cover's attention as SQL counts it, and as the book page reads it."""
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase

    case_id = library.cases["item-1"]
    counted = (
        library.repository.read_review_library(
            None, 25, 0, "needs_attention"
        ).attention_total
        == 1
    )
    detail = InspectCurrentReviewCase(library.repository, library.service).run(case_id)
    cover = next(field for field in detail["fields"] if field["destination"] == "cover")
    return counted, cover["needs_attention"]


@pytest.mark.parametrize(
    ("case", "attention"),
    (
        ("contradicted_with_choices", True),
        ("contradicted_while_fetching", False),
        ("contradicted_without_actions", False),
        ("title_hold_without_actions", False),
        ("kept_unconfirmed", False),
        ("transient_with_choices", False),
    ),
    ids=(
        "contradicted_with_choices",
        "contradicted_while_fetching",
        "contradicted_without_actions",
        "title_hold_without_actions",
        "kept_unconfirmed",
        "transient_with_choices",
    ),
)
def test_python_and_sql_attention_agree_on_holds_and_kept_covers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, attention: bool
) -> None:
    """#1362 S13, R1, R2: a hold asks only while the owner has a choice to make.

    A hold with nothing to choose shows on the book page but is not counted; a
    kept unconfirmed cover is not counted; a read that settled nothing keeps
    the cover with no fault, so even with covers to choose from it does not
    ask (R1 as amended: the owner has no keep to record for it). Each case
    runs both mirrors over the same stored facts.
    """
    from contextlib import nullcontext

    from mechanical_librarian.application import review_web
    from tests.support.cover_producers import incumbent_identity

    identity = (
        incumbent_identity("conflict", ("edition_work_contradiction",))
        if case.startswith("contradicted")
        else incumbent_identity("plausible_match")
        if case == "kept_unconfirmed"
        else nullcontext()
    )
    options: dict[str, Any] = (
        {"readable_current": False, "abs_state": CoverSourceState.TIMEOUT}
        if case == "transient_with_choices"
        # A hold with choices waits while a provider lookup runs: neither
        # mirror counts it until the comparison is ready.
        else {"fetching": True}
        if case == "contradicted_while_fetching"
        else {}
    )
    with identity, _library(tmp_path, **options) as library:
        connection = library.repository._connection
        stage = json.loads(
            connection.execute(
                "SELECT currentness_document FROM cover_considerations"
            ).fetchone()[0]
        )["cover_stage"]
        if case == "title_hold_without_actions":
            # A synthetic title hold over the stored stage both mirrors read.
            connection.execute("DROP TRIGGER cover_considerations_no_update")
            connection.execute(
                "UPDATE cover_considerations SET currentness_document=json_set("
                "currentness_document,'$.cover_stage.outcome','hold',"
                "'$.cover_stage.reason','title_names_other_work')"
            )
            stage = {"outcome": "hold", "reason": "title_names_other_work"}
        if case.endswith("without_actions"):
            # The same proposal with no decision to take, as each mirror reads it.
            connection.execute(
                "UPDATE review_case_field_facts SET has_owner_decision_actions=0 "
                "WHERE destination='cover'"
            )
            monkeypatch.setattr(review_web, "decision_actions", lambda _proposal: ())
        connection.commit()

        mirrors = _attention_mirrors(library)
        listed = _page(library.repository, "all", None)["items"][0]
        held = listed["hold"]
        if case == "transient_with_choices":
            # Not asked, yet its covers stay the owner's optional choice (A1).
            from mechanical_librarian.application.review_web import (
                InspectCurrentReviewCase,
            )

            detail = InspectCurrentReviewCase(library.repository, library.service).run(
                library.cases["item-1"]
            )
            field = next(f for f in detail["fields"] if f["destination"] == "cover")
            assert field["classification"] == "owner_choosable"
            assert listed["group"] == "kept"

    expected_stage = {
        "contradicted_with_choices": ("hold", "current_contradicted"),
        "contradicted_while_fetching": ("hold", "current_contradicted"),
        "contradicted_without_actions": ("hold", "current_contradicted"),
        "title_hold_without_actions": ("hold", "title_names_other_work"),
        "kept_unconfirmed": ("keep", None),
        "transient_with_choices": ("keep", None),
    }[case]
    assert (stage["outcome"], stage["reason"]) == expected_stage
    assert mirrors == (attention, attention)
    # The hold pill names its reason whether or not it is counted, once the
    # comparison is ready; Covers shows no hold while a lookup runs.
    assert held == (
        {"reason": expected_stage[1]}
        if expected_stage[0] == "hold" and case != "contradicted_while_fetching"
        else None
    )


# Calibration (#1372 PR 3): the works whose cover the owner can pick now, where
# every pick counts toward the taste profile.


def _calibrated(library: _Library) -> list[str]:
    """The calibration set's works, in order, by item."""
    by_case = {case: item for item, case in library.cases.items()}
    return [
        by_case[item["review_case_id"]]
        for item in cast(dict[str, Any], cover_calibration(library.repository))["items"]
    ]


def test_calibration_offers_works_with_three_arts_first_then_by_preparation(
    tmp_path: Path,
) -> None:
    works = {
        "item-1": _Work(extra=0),
        "item-2": _Work(extra=1),
        "item-3": _Work(extra=0),
        "item-4": _Work(extra=2),
    }
    with _library(tmp_path, tuple(works), works=works) as library:
        connection = library.repository._connection
        # Each comparison prepared at its own time, later items first.
        connection.execute("DROP TRIGGER cover_considerations_no_update")
        for at, item in enumerate(reversed(works)):
            connection.execute(
                "UPDATE cover_considerations SET occurred_at=? WHERE review_case_id=?",
                (at, library.cases[item]),
            )
        connection.commit()
        before = connection.total_changes
        assert _calibrated(library) == ["item-4", "item-2", "item-3", "item-1"]
        # Computed on each read: nothing is written (S1).
        assert connection.total_changes == before
        document = cast(dict[str, Any], cover_calibration(library.repository))
    assert document["schema_version"] == "review-cover-calibration-v1"
    assert {item["group"] for item in document["items"]} == {"kept"}


def test_calibration_offers_a_suggested_switch_and_an_open_choice(
    tmp_path: Path,
) -> None:
    with _library(
        tmp_path / "proposal", works={"item-1": _Work()}, small_current=True
    ) as library:
        assert _page(library.repository, "decide", None)["items"][0]["group"] == (
            "proposal"
        )
        assert _calibrated(library) == ["item-1"]
    with _library(tmp_path / "choice", works={"item-1": _Work()}) as library:
        # A synthetic attention fact over the case's open cover choice.
        connection = library.repository._connection
        connection.execute(
            "INSERT INTO review_attention_facts(connection_name,library_name,"
            "item_id,report_id,destination) SELECT connection_name,library_name,"
            "item_id,report_id,'cover' FROM current_review_packages"
        )
        connection.commit()
        assert _page(library.repository, "decide", None)["items"][0]["group"] == (
            "choice"
        )
        assert _calibrated(library) == ["item-1"]


def _hold(library: _Library) -> None:
    connection = library.repository._connection
    connection.execute("DROP TRIGGER cover_considerations_no_update")
    connection.execute(
        "UPDATE cover_considerations SET currentness_document=json_set("
        "currentness_document,'$.cover_stage.outcome','hold',"
        "'$.cover_stage.reason','title_names_other_work')"
    )
    connection.commit()


@pytest.mark.parametrize(
    "exclusion",
    (
        "held",
        "fetching",
        "not_current",
        "decided",
        "one_art",
        "switched",
        "larger_copy",
        "retained_original",
        "unreadable_current",
    ),
)
def test_calibration_leaves_out_a_work_whose_pick_would_not_count(
    tmp_path: Path, exclusion: str
) -> None:
    from mechanical_librarian.domain.cover_rules import COLOUR_BINS, LAYOUT_VALUES

    exclusions: dict[str, dict[str, Any]] = {
        "fetching": {"fetching": True},
        "one_art": {
            "shared_measures": CoverMeasures(
                "0" * 16, "0" * 16, (1,) * COLOUR_BINS, (0,) * LAYOUT_VALUES
            )
        },
        "larger_copy": {"larger_copy": True},
        "retained_original": {"put_back": True},
        "unreadable_current": {
            "readable_current": False,
            "abs_state": CoverSourceState.DECODE_FAILED,
            "abs_digest": "d" * 64,
        },
    }
    options = exclusions.get(exclusion, {})
    # Put back offers item-1's own original only.
    items = ("item-1",) if exclusion == "retained_original" else ("item-1", "item-2")
    with _library(tmp_path, items, **options) as library:
        if exclusion == "held":
            _hold(library)
        elif exclusion == "not_current":
            # The live authority no longer admits the comparison.
            library.repository.bind_cover_consideration_currentness(
                lambda *_target: True
            )
        elif exclusion == "decided":
            with _client(library) as (client, headers):
                for item in ("item-1", "item-2"):
                    response = _post_cover_decision(
                        client, headers, library.cases[item], f"keep-{item}-0001"
                    )
                    assert response.status_code == 200, response.text
        elif exclusion == "switched":
            original = _abs_digest(library.report)
            for index, item in enumerate(("item-1", "item-2"), start=1):
                _switch(
                    library,
                    item,
                    plan=str(index),
                    at=100,
                    original=original,
                    sequence=index,
                )
        assert _calibrated(library) == []


def test_without_an_exclusion_the_same_library_is_offered(tmp_path: Path) -> None:
    """The control for each exclusion above: two plain works are both offered."""
    with _library(tmp_path, ("item-1", "item-2")) as library:
        # Prepared at the same time, so in case order.
        assert _calibrated(library) == sorted(
            ("item-1", "item-2"), key=library.cases.__getitem__
        )


def _rows(tmp_path: Path, works: list[_Work]) -> list[CalibrationRow]:
    """Calibration rows as the store reads them, each with its own report."""
    base, _ = _qualified_cover_report(tmp_path)
    return [
        CalibrationRow(
            CoverRow(
                f"review_case_{index:064x}",
                _for_item(base, f"item-{index}", work=work).to_json(),
                "kept",
                True,
                None,
                False,
                None,
                None,
                (),
            ),
            work.authors[0] if work.authors else None,
            work.series,
        )
        for index, work in enumerate(works)
    ]


def _selected(rows: list[CalibrationRow]) -> list[int]:
    return [int(row.review_case_id[-8:], 16) for row, _ in select_calibration(rows)]


def test_calibration_keeps_at_most_two_works_of_one_first_author(
    tmp_path: Path,
) -> None:
    rows = _rows(
        tmp_path,
        [
            _Work(authors=("Ann Author", "Bob")),
            _Work(authors=("ANN AUTHOR",)),
            _Work(authors=("ann author",)),
            # A second author is not capped.
            _Work(authors=("Cat", "Ann Author")),
            _Work(),
        ],
    )
    assert _selected(rows) == [0, 1, 3, 4]


def test_calibration_keeps_at_most_two_works_of_one_series(tmp_path: Path) -> None:
    rows = _rows(
        tmp_path,
        [
            _Work(authors=("A",), series=("Saga", "Cycle")),
            _Work(authors=("B",), series=("saga",)),
            _Work(authors=("C",), series=("SAGA",)),
            # Two works already count against Cycle's cap only once.
            _Work(authors=("D",), series=("Cycle",)),
            _Work(authors=("E",), series=("Cycle",)),
            _Work(authors=("F",)),
        ],
    )
    assert _selected(rows) == [0, 1, 3, 5]


def test_the_store_reads_the_facts_the_caps_count(tmp_path: Path) -> None:
    """The first author and series the store reads are the report's own."""
    works = {
        "item-1": _Work(authors=("Ann", "Bob"), series=("Saga", "Cycle")),
        "item-2": _Work(),
        "item-3": _Work(authors=("ann",), series=("saga",)),
        "item-4": _Work(authors=("ANN",)),
    }
    with _library(tmp_path, tuple(works), works=works) as library:
        facts = {
            row.row.review_case_id: (row.first_author, row.series)
            for row in library.repository.read_cover_calibration()
        }
        assert facts[library.cases["item-1"]] == ("Ann", ("Saga", "Cycle"))
        assert facts[library.cases["item-2"]] == (None, ())
        calibrated = _calibrated(library)
    # A third work of Ann is left out wherever it falls in the order.
    assert len(calibrated) == 3
    assert "item-2" in calibrated


def test_calibration_stops_reading_reports_at_twenty_five(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mechanical_librarian.application import review_covers

    rows = _rows(tmp_path, [_Work(authors=(f"Author {n}",)) for n in range(30)])
    read: list[str] = []
    real = review_covers._report_facts

    def counted(document: str) -> Any:
        read.append(document)
        return real(document)

    monkeypatch.setattr(review_covers, "_report_facts", counted)
    assert _selected(rows) == list(range(25))
    assert len(read) == 25
    assert _selected(rows[:10]) == list(range(10))


def test_the_calibration_route_is_private_and_reads_the_set(tmp_path: Path) -> None:
    works = {"item-1": _Work(extra=1), "item-2": _Work()}
    with _library(tmp_path, tuple(works), works=works) as library:
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
            assert anonymous.get("/api/v1/covers/calibration").status_code == 401
        finally:
            anonymous.close()
        with _client(library) as (client, _headers):
            response = client.get("/api/v1/covers/calibration")
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store, private"
            body = response.json()
    assert set(body) == {"items", "schema_version"}
    assert body["schema_version"] == "review-cover-calibration-v1"
    # Three arts first.
    assert [item["review_case_id"] for item in body["items"]] == [
        library.cases["item-1"],
        library.cases["item-2"],
    ]
    assert str(body["items"][0]["cover"]["url"]).startswith("/api/v1/library/cover?")


def test_every_calibration_pick_counts_toward_the_taste_profile(
    tmp_path: Path,
) -> None:
    """A pick is the work's ordinary decision, and both kinds count (S2)."""
    from mechanical_librarian.adapters.persistence.cover_taste import (
        SQLiteCoverTasteRepository,
    )

    with _library(tmp_path, ("item-1", "item-2")) as library:
        taste = SQLiteCoverTasteRepository(library.repository._connection)
        first, second = _calibrated(library)
        with _client(library) as (client, headers):
            accept = _post_cover_decision(
                client,
                headers,
                library.cases[first],
                "calibration-accept-1",
                outcome="accept",
                kind="candidate",
            )
            assert accept.status_code == 200, accept.text
            assert taste.eligible_count() == 1
            keep = _post_cover_decision(
                client, headers, library.cases[second], "calibration-keep-01"
            )
            assert keep.status_code == 200, keep.text
            assert taste.eligible_count() == 2
        assert _calibrated(library) == []
