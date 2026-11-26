"""The upgrade notice and a carried answer over HTTP after a real
regeneration (#2039, T-N and T-H; ADR-0108).

A book's title answer is held across the real `regenerate-derived`, the book
is read again at the new epoch, and the question check carries the answer or
asks it again on the book's first new case. The served application reads
the ledger's `RegenerationNoticeStore` through the repository, as the
product composes it: the notice's counts are the store's, its one dismissal
is idempotent, and the case's detail and history carry C3's fields.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.review_web import (
    REVIEW_CASE_DETAIL_SCHEMA,
    REVIEW_CASE_HISTORY_SCHEMA,
)
from mechanical_librarian.domain.upgrade_notice import UPGRADE_NOTICE_SCHEMA
from tests.integration.test_owner_field_record import _regenerated
from tests.integration.test_screens_across_regeneration import _AFTER as _SCREENS_AFTER
from tests.integration.test_screens_across_regeneration import (
    _B_AGAIN,
    _CONFIGURED,
    _answer_six_fields,
)
from tests.integration.test_screens_across_regeneration import (
    _client as _screens_client,
)
from tests.integration.test_screens_across_regeneration import _store as _screens_store
from tests.support.owner_values import OwnerStore, rewritten
from tests.support.review_web_doubles import idle_preparation
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from tests.unit.test_level_offer_binding import field_of
from tests.unit.test_owner_answer_holds import _store

_ORIGIN = "https://review.test"
# `regenerate` finishes the run at 1_700_000_000; the owner
# answers after it.
_AFTER_REGENERATION = 1_700_000_100


@contextmanager
def _owner_client(store: OwnerStore, root: Path) -> Iterator[tuple[TestClient, str]]:
    """A logged-in review client over the store's repository, and its CSRF
    token, at a time after the regeneration finished."""
    password = root / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"))
    password.chmod(0o600)
    # The answers' library is configured and preparation is idle, as the
    # server is between scans.
    ((connection, library),) = store.repository._connection.execute(
        "SELECT DISTINCT connection_name, library_name FROM owner_field_answers"
    ).fetchall()
    app = create_transport_test_app(
        store.repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin=_ORIGIN,
            owner_password_hash_file=str(password),
        ),
        clock=lambda: _AFTER_REGENERATION,
        review_preparation=idle_preparation(connection, library),
    )
    with TestClient(app, base_url=_ORIGIN) as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": _ORIGIN},
        )
        assert login.status_code == 200
        yield client, str(login.json()["csrf_token"])


def _notice(client: TestClient) -> dict[str, object] | None:
    response = client.get("/api/v1/upgrade-notice")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == UPGRADE_NOTICE_SCHEMA
    return body["notice"]


@pytest.mark.parametrize(
    ("read", "kept", "asked_again"),
    (("unchanged", 1, 0), ("title_changed", 0, 1)),
)
def test_the_notice_reads_the_ledger_after_a_real_regeneration(
    tmp_path: Path, read: str, kept: int, asked_again: int
) -> None:
    """T-N against the real store: before the book is read again its answer
    is not yet checked and nothing is counted; after its first new case the
    notice counts the carry or the asking again, as the store reads it.
    Dismissing it is 204 and again 204, recorded once; an unknown
    regeneration is 409 stale."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    store.decide("keep_current")
    store = _regenerated(store)
    try:
        notices = store.repository.regeneration_notice_store()
        configured = frozenset({(report.target.connection, report.target.library)})
        with _owner_client(store, tmp_path) as (client, csrf):
            before = notices.read_regeneration_notice(configured)
            assert before is not None
            assert (before.kept, before.asked_again, before.not_yet_checked) == (
                0,
                0,
                1,
            )
            # Not yet checked is no preparation: nothing runs here.
            assert _notice(client) == {
                "regeneration_id": str(before.regeneration_id),
                "preparing": False,
                "kept": 0,
                "asked_again": 0,
                "not_yet_checked": 1,
            }

            store.publish(
                report
                if read == "unchanged"
                else rewritten(report, title="Another Title")
            )
            after = notices.read_regeneration_notice(configured)
            assert after is not None
            assert (after.kept, after.asked_again, after.not_yet_checked) == (
                kept,
                asked_again,
                0,
            )
            assert _notice(client) == {
                "regeneration_id": str(after.regeneration_id),
                "preparing": False,
                "kept": kept,
                "asked_again": asked_again,
                "not_yet_checked": 0,
            }

            headers = {
                "Origin": _ORIGIN,
                "x-csrf-token": csrf,
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            }
            path = f"/api/v1/upgrade-notice/{after.regeneration_id}/dismiss"
            first = client.post(path, headers=headers)
            assert first.status_code == 204, first.text
            assert _notice(client) is None
            second = client.post(path, headers=headers)
            assert second.status_code == 204, second.text
            for unknown in (str(after.regeneration_id + 1), "0", "regeneration_1"):
                stale = client.post(
                    f"/api/v1/upgrade-notice/{unknown}/dismiss", headers=headers
                )
                assert stale.status_code == 409, unknown
                assert stale.json()["error"]["code"] == "stale"
        assert store.repository._connection.execute(
            "SELECT regeneration_id,dismissed_at FROM regeneration_notice_dismissals"
        ).fetchall() == [(after.regeneration_id, _AFTER_REGENERATION)]
    finally:
        store.close()


def test_a_carried_answer_after_a_real_regeneration_reads_over_the_wire(
    tmp_path: Path,
) -> None:
    """T-H over HTTP: the answer carried to the book's first new case after
    the real `regenerate-derived` is the field's owner decision in a detail
    that validates as v17, with the record's `asked_again` and
    `resume_failed`; its history item is `human_decision_carried` with
    `kept_from`, the day of the choice it kept."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    original = store.decide("keep_current")
    store = _regenerated(store)
    try:
        later = store.publish(report).review_case_id
        ((carried_id, carried_from),) = store.repository._connection.execute(
            "SELECT decision_id,carried_from FROM human_decisions "
            "WHERE carried_from IS NOT NULL"
        ).fetchall()
        assert carried_from == original.decision_id

        with _owner_client(store, tmp_path) as (client, _csrf):
            detail = client.get(f"/api/v1/review-cases/{later}")
            history = client.get(f"/api/v1/review-cases/{later}/history")
        assert detail.status_code == 200, detail.text
        assert history.status_code == 200, history.text

        body = detail.json()
        assert body["schema_version"] == REVIEW_CASE_DETAIL_SCHEMA
        title = field_of(body, "title")
        assert title["human_decision"]["decision_id"] == carried_id
        assert title["human_decision"]["application"] == "settled"
        # Carried, so not asked again, and no Resume of it failed.
        assert {"asked_again", "resume_failed"} <= title.keys()
        assert title["asked_again"] is None
        assert title["resume_failed"] is False

        page = history.json()
        assert page["schema_version"] == REVIEW_CASE_HISTORY_SCHEMA
        (item,) = (
            value for value in page["items"] if value.get("decision_id") == carried_id
        )
        assert (item["kind"], item["actor"], item["outcome"]) == (
            "human_decision_carried",
            "owner",
            "keep_current",
        )
        assert item["kept_from"] == original.occurred_at
    finally:
        store.close()


def test_an_answer_asked_again_after_a_real_regeneration_says_why_on_the_wire(
    tmp_path: Path,
) -> None:
    """The title answer meets a changed title on the book's first new case
    after the real regeneration: the field reads `asked_again` with its
    reason and the earlier answer, and no Resume failed."""
    store, report, _ = _store(tmp_path, posture="preview")
    store.publish(report)
    store.decide("keep_current")
    store = _regenerated(store)
    try:
        later = store.publish(rewritten(report, title="Another Title")).review_case_id
        with _owner_client(store, tmp_path) as (client, _csrf):
            detail = client.get(f"/api/v1/review-cases/{later}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["schema_version"] == REVIEW_CASE_DETAIL_SCHEMA
        title = field_of(body, "title")
        assert title["human_decision"] is None
        assert title["asked_again"] is not None
        assert title["asked_again"]["reason"] == "target_changed"
        assert title["resume_failed"] is False
    finally:
        store.close()


def test_an_idle_notice_with_answers_not_yet_checked_sends_their_count(
    tmp_path: Path,
) -> None:
    """T-N against the real store: after only one of three books is read
    again, with no preparation running, the notice counts that book's kept
    and asked-again answers and sends the answers still unchecked, so the
    browser says no total."""
    store = _screens_store(tmp_path)
    _answer_six_fields(store)
    store = _regenerated(store)
    store.ticks = _SCREENS_AFTER
    try:
        store.publish(_B_AGAIN)
        partial = store.repository.regeneration_notice_store().read_regeneration_notice(
            _CONFIGURED
        )
        assert partial is not None
        # B's two answers were checked (one kept, one asked again); A's and
        # C's four wait for their books.
        assert (partial.kept, partial.asked_again, partial.not_yet_checked) == (
            1,
            1,
            4,
        )
        with _screens_client(store, tmp_path) as client:
            assert _notice(client) == {
                "regeneration_id": str(partial.regeneration_id),
                "preparing": False,
                "kept": 1,
                "asked_again": 1,
                "not_yet_checked": 4,
            }
    finally:
        store.close()
