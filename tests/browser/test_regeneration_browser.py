"""What the owner reads after an upgrade's regeneration, in the built shell
(#2039, T-B; ADR-0108).

Each server here serves a real store: the asked-again sentence, the upgrade
notice and the History kept-from line come from the real `regenerate-derived`
and the question check on each book's first new case; the failed-Resume line
and the Policy page's Retry come from the cheapest real store that reaches
them. Dates are read in en-GB and UTC, so the earlier answers' day, which is
not this year, reads with its year.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest
import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import Page, expect, sync_playwright
from scripts.verify_playwright_browser import verify_playwright_browser

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from tests.browser.test_review_browser import (
    _DEFAULT_SETTINGS_DOCUMENT,
    _PASSWORD,
    _certificate,
    _login,
    _owner_search,
)
from tests.integration.test_owner_field_record import _regenerated
from tests.integration.test_screens_across_regeneration import (
    _A,
    _AFTER,
    _B,
    _B_AGAIN,
    _C,
    _CONFIGURED,
    _NOW,
    _answer_six_fields,
    _proposed_title,
    _store,
)
from tests.support.ledger_partition import regenerate
from tests.support.owner_field_record import request_resume
from tests.support.ports import ReservedPort
from tests.support.review_web_doubles import idle_preparation
from tests.unit.review_app_fixture import create_transport_test_app
from tests.unit.test_metadata_policy_activation import _draft
from tests.unit.test_review_pipeline import _report as _policy_report

# 1_699_990_000 and the few seconds after it, in en-GB and UTC.
_EARLIER_DAY = "Tue, 14 Nov 2023"
_NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


@pytest.fixture(scope="module", autouse=True)
def _verified_playwright_browser() -> None:
    """Refuse the module before its first test, naming the browser it ran
    (as tests/browser/test_review_browser.py does)."""
    print(
        json.dumps(verify_playwright_browser(), sort_keys=True, separators=(",", ":")),
        file=sys.__stderr__,
        flush=True,
    )


@contextmanager
def _served(
    root: Path, *, policy: bool = False, prepared: bool = False
) -> Iterator[str]:
    """Serve the store at ``root/owner.sqlite`` as `_server` serves its own,
    at the time after the regeneration, and yield the origin. With
    ``prepared``, an idle preparation lane reads the store's library, so the
    upgrade notice counts its answers not yet checked as the server does."""
    database = root / "owner.sqlite"
    hash_path = root / "owner.hash"
    if not hash_path.exists():
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
    certificate, key = _certificate(root)
    repository = open_repository(database, cross_thread=True)
    connection = repository._connection
    if connection.execute("SELECT count(*) FROM owner_settings").fetchone() == (0,):
        with connection:
            connection.execute(
                "INSERT INTO owner_settings VALUES(1,?,?)",
                (
                    _DEFAULT_SETTINGS_DOCUMENT.revision,
                    _canonical_json(_DEFAULT_SETTINGS_DOCUMENT.model_dump()),
                ),
            )
            connection.executemany(
                "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?)",
                [
                    (f"connection.{item.name}.key", f"synthetic-{item.name}-secret")
                    for item in _DEFAULT_SETTINGS_DOCUMENT.connections
                ],
            )
    activity = ContinuousGuardedApplication(
        repository,
        {},
        {},
        PersistedFieldEffectAuthority(repository),
        (("primary", "ebooks"),),
        lambda: _NOW,
        lambda *_: None,
        activity_only=True,
    )
    sessions = SQLiteReviewSessionRepository.open(root / "review-sessions.sqlite")
    port = ReservedPort()
    origin = f"https://127.0.0.1:{port.number}"
    app = create_transport_test_app(
        repository,
        ReviewWebConfiguration(
            tls_certificate_file=str(certificate),
            tls_private_key_file=str(key),
            canonical_origin=origin,
            owner_password_hash_file=str(hash_path),
        ),
        session_store=sessions,
        clock=lambda: _NOW,
        close_repository=True,
        owner_search=_owner_search(repository),
        policy_libraries=(("ebooks", None),) if policy else (),
        activity_projection=activity,
        review_preparation=idle_preparation("primary", "ebooks") if prepared else None,
        react_asset_loader=ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
        settings=OwnerSettingsOperations(repository.owner_settings_store()),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port.number,
            log_level="error",
            access_log=False,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
            proxy_headers=False,
            timeout_keep_alive=30,
        )
    )
    port.release()
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("synthetic review server did not start")
    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        sessions.close()


@contextmanager
def _page(width: int = 1440) -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                ignore_https_errors=True,
                locale="en-GB",
                timezone_id="UTC",
                viewport={"width": width, "height": 900},
            )
            yield context.new_page()
            context.close()
        finally:
            browser.close()


def _regenerated_library(root: Path) -> dict[str, str]:
    """Book A's kept ISBN is carried to its first new case; book B's accepted
    title meets other choices there and is asked again. Returns the books'
    new cases."""
    store = _store(root)
    store.publish(_A)
    store.decide("keep_current", destination="isbn")
    store.publish(_B)
    store.decide("accept", _proposed_title(store))
    store = _regenerated(store)
    store.ticks = _AFTER
    try:
        cases = {
            report.target.item: store.publish(report).review_case_id
            for report in (_A, _B_AGAIN)
        }
        notice = store.repository.regeneration_notice_store().read_regeneration_notice(
            frozenset({(_A.target.connection, _A.target.library)})
        )
        assert notice is not None
        assert (notice.kept, notice.asked_again, notice.not_yet_checked) == (1, 1, 0)
    finally:
        store.close()
    return cases


_ASKED_AGAIN = f"The choices changed since you chose “Birch Title” on {_EARLIER_DAY}."


@pytest.mark.parametrize("width", (1440, 390))
def test_an_answer_asked_again_says_why_under_the_question_and_in_attention(
    width: int,
) -> None:
    """V20: the case page says why under the title's question, and the
    Attention rail's row says the same sentence, with the earlier answer and
    its day."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cases = _regenerated_library(root)
        with _served(root) as origin, _page(width) as page:
            _login(page, origin)
            page.goto(
                f"{origin}/library?lens=all&preview={cases['item-b']}&field=title"
            )
            question = page.get_by_role(
                "heading", name="Which title should this work use?"
            )
            expect(question).to_be_visible()
            # The one line under the question.
            reason = page.locator(
                "header:has(h3.decision-question) p", has_text=_ASKED_AGAIN
            )
            expect(reason).to_have_count(1)
            expect(reason).to_have_text(_ASKED_AGAIN)
            assert page.evaluate(_NO_SIDEWAYS_SCROLL), width

            page.goto(f"{origin}/library?lens=all&attention=open")
            rail = page.get_by_label("Attention queue", exact=True)
            row = rail.locator("button.attention-queue-item", has_text=_ASKED_AGAIN)
            expect(row).to_have_count(1)
            expect(row).to_contain_text(f"Title · {_ASKED_AGAIN}")
            assert page.evaluate(_NO_SIDEWAYS_SCROLL), width


_NOTICE = "1 of your answers is asked again after the upgrade. The other one was kept."
# The Attention rail says only the first sentence, with Dismiss beside it.
_NOTICE_BRIEF = "1 of your answers is asked again after the upgrade."
_BESIDE_DISMISS = """(notice) => {
  const text = notice.querySelector('p').getBoundingClientRect()
  const button = notice.querySelector('button').getBoundingClientRect()
  return button.top < text.bottom && text.top < button.bottom
    && button.left >= text.right
}"""


def test_the_upgrade_notice_shows_on_activity_and_in_attention_until_dismissed() -> (
    None
):
    """V21: the notice is under the Activity heading and in the Attention
    rail; Dismiss in one place hides it in both, and it stays dismissed."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _regenerated_library(root)
        with _served(root) as origin, _page() as page:
            _login(page, origin)
            page.goto(f"{origin}/activity")
            heading = page.get_by_role("heading", name="Activity", level=1)
            expect(heading).to_be_visible()
            notice = page.locator(".cm-upgrade-notice")
            expect(notice).to_have_count(1)
            expect(notice).to_contain_text(_NOTICE)
            # Directly under the heading's header.
            assert page.evaluate(
                """() => {
                  const notice = document.querySelector('.cm-upgrade-notice')
                  const heading = [...document.querySelectorAll('h1')]
                    .find(node => node.textContent.trim() === 'Activity')
                  return heading.getBoundingClientRect().bottom
                    <= notice.getBoundingClientRect().top
                }"""
            )

            page.goto(f"{origin}/library?lens=all&attention=open")
            rail = page.get_by_label("Attention queue", exact=True)
            in_rail = rail.locator(".cm-upgrade-notice")
            expect(in_rail.locator("p")).to_have_text(_NOTICE_BRIEF)
            assert in_rail.evaluate(_BESIDE_DISMISS)
            in_rail.get_by_role("button", name="Dismiss", exact=True).click()
            expect(in_rail).to_have_count(0)
            # Focus goes to the rail's heading, not to the page.
            expect(rail.get_by_role("heading", name="Attention")).to_be_focused()

            # In the same page, Activity no longer shows it either.
            page.get_by_role("navigation", name="Primary workspace").get_by_role(
                "link", name="Activity"
            ).click()
            expect(heading).to_be_visible()
            expect(page.locator(".cm-upgrade-notice")).to_have_count(0)
            # Read again from the server, with the page's session: the
            # dismissal stands. (The body of the page's own read after a
            # reload was not always still available to the test in CI.)
            page.reload()
            expect(heading).to_be_visible()
            expect(page.locator(".cm-upgrade-notice")).to_have_count(0)
            answer = page.request.get(f"{origin}/api/v1/upgrade-notice")
            assert answer.ok
            assert answer.json()["notice"] is None
        with closing(sqlite3.connect(root / "owner.sqlite")) as connection:
            assert connection.execute(
                "SELECT count(*) FROM regeneration_notice_dismissals"
            ).fetchone() == (1,)


@pytest.mark.parametrize("width", (1440, 390))
def test_an_idle_notice_with_answers_not_yet_checked_says_no_total(
    width: int,
) -> None:
    """Only book B is read again and nothing is preparing: one answer was
    kept, one is asked again and four are not yet checked. Activity says the
    two counts with no total; the rail says the same one sentence beside
    Dismiss, and Dismiss on Activity leaves focus on its heading."""
    sentence = "1 of your answers is asked again after the upgrade and 1 was kept."
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = _store(root)
        _answer_six_fields(store)
        store = _regenerated(store)
        store.ticks = _AFTER
        try:
            store.publish(_B_AGAIN)
            notice = (
                store.repository.regeneration_notice_store().read_regeneration_notice(
                    _CONFIGURED
                )
            )
            assert notice is not None
            assert (notice.kept, notice.asked_again, notice.not_yet_checked) == (
                1,
                1,
                4,
            )
        finally:
            store.close()
        with _served(root, prepared=True) as origin, _page(width) as page:
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&attention=open")
            rail = page.get_by_label("Attention queue", exact=True)
            in_rail = rail.locator(".cm-upgrade-notice")
            expect(in_rail.locator("p")).to_have_text(sentence)
            assert in_rail.evaluate(_BESIDE_DISMISS), width
            assert page.evaluate(_NO_SIDEWAYS_SCROLL), width

            page.goto(f"{origin}/activity")
            heading = page.get_by_role("heading", name="Activity", level=1)
            expect(heading).to_be_visible()
            on_activity = page.locator(".cm-upgrade-notice")
            expect(on_activity.locator("p")).to_have_text(sentence)
            assert page.evaluate(_NO_SIDEWAYS_SCROLL), width
            on_activity.get_by_role("button", name="Dismiss", exact=True).click()
            expect(on_activity).to_have_count(0)
            expect(page.locator("header[data-route-focus]")).to_be_focused()


def test_a_carried_answer_says_in_history_which_choice_it_kept() -> None:
    """V25: the carried ISBN's History item names the day of the choice it
    kept."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cases = _regenerated_library(root)
        with _served(root) as origin, _page() as page:
            _login(page, origin)
            page.goto(f"{origin}/review/{cases['item-a']}?subview=history")
            line = page.get_by_text(
                f"Kept from your choice on {_EARLIER_DAY}, because the same choices "
                "were offered.",
                exact=True,
            )
            expect(line).to_have_count(1)
            expect(line).to_be_visible()
            entry = page.locator("article.diagnostic-entry", has=line)
            expect(entry).to_contain_text("ISBN")
            expect(entry).to_contain_text("You kept the current value.")


_RESUME_FAILED = (
    "Resume automatic handling did not finish. The fresh checks ended with failures."
)


def test_a_failed_resume_is_said_outside_the_resume_offer() -> None:
    """V22: a Resume that failed, read in Preview where Resume is not
    offered, says so once as a status line, with no Resume control."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = _store(root)
        try:
            case = store.publish(_C).review_case_id
            store.decide("keep_current")
            request = request_resume(store.repository, case, "title", store.clock())
            assert request is not None
            (scan,) = store.repository._connection.execute(
                "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
            ).fetchone()
            assert store.repository._fail_open_resumes_for_scan(scan, store.clock())
            store.repository._connection.commit()
            assert (
                store.repository.enter_shadow_preview(store.clock(), ("ebooks",))
                == "preview"
            )
            # Leaving Automatic takes the case out of the lineage until the
            # book is read again, which admits the same case.
            assert store.publish(_C).review_case_id == case
            field = next(
                value
                for value in InspectCurrentReviewCase(store.repository).run(case)[
                    "fields"
                ]
                if value["destination"] == "title"
            )
            assert (field["manual_hold"], field["resume_failed"]) == (False, True)
        finally:
            store.close()
        with _served(root) as origin, _page() as page:
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case}&field=title")
            page.locator(".workspace-detail").wait_for()
            line = page.get_by_role("status").filter(has_text=_RESUME_FAILED)
            expect(line).to_have_count(1)
            expect(line).to_have_text(_RESUME_FAILED)
            expect(page.locator(".automatic-handling-panel")).to_have_count(0)
            expect(
                page.get_by_role("button", name="Resume automatic handling")
            ).to_have_count(0)


def _policy_store(root: Path) -> None:
    """One policy activation whose one work failed, as
    tests/unit/test_metadata_policy_activation.py makes it."""
    repository = open_repository(root / "owner.sqlite", create=True)
    try:
        report = _policy_report()
        repository.publish(report, lambda: True)
        old = evaluate_report(report)
        repository.publish_proposal(old)
        repository.admit_review_case(old)
        draft = _draft()
        repository.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)
        transition = repository.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            40,
            expected_active_generation=1,
        )
        assert transition is not None
        assert (transition.failed, transition.items_present) == (1, True)
    finally:
        repository.close()


def test_policy_hides_retry_failed_works_once_no_failed_work_is_left() -> None:
    """V-list 7.2: the failed count stays after a regeneration, but its
    per-work rows are gone, so Retry failed works is offered before and not
    after."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _policy_store(root)

        def read(expect_retry: bool) -> None:
            with _served(root, policy=True) as origin, _page() as page:
                _login(page, origin)
                page.goto(f"{origin}/policy?section=identity")
                expect(
                    page.get_by_role(
                        "heading", name="Activation completed with failures"
                    )
                ).to_be_visible()
                retry = page.get_by_role("button", name="Retry failed works")
                if expect_retry:
                    expect(retry).to_be_visible()
                else:
                    expect(retry).to_have_count(0)

        read(expect_retry=True)
        regenerate(root / "owner.sqlite")
        read(expect_retry=False)
