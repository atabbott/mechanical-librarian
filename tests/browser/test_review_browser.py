from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import (
    BrowserContext,
    Page,
    Request,
    expect,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from scripts.verify_playwright_browser import verify_playwright_browser

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from tests.support.ports import ReservedPort
from tests.support.proposal_evidence import _fields as _bibliographic_fields
from tests.support.review_web_doubles import FakeActivityApplication
from tests.unit.review_app_fixture import create_transport_test_app
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)
from tests.unit.test_cover_packets import (
    _preview as _cover_preview,
)
from tests.unit.test_cover_packets import (
    _report as _cover_report,
)
from tests.unit.test_cover_packets import (
    _source as _cover_source,
)

_PASSWORD = "synthetic browser password"


class _BrowserActivityApplication(FakeActivityApplication):
    def wake(self) -> None:
        return None


def _certificate(root: Path) -> tuple[Path, Path]:
    certificate = root / "browser-test.crt"
    key = root / "browser-test.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


def _report():
    return _bibliographic_report(
        current_title="Mistborn <img src=x onerror=alert(1)>",
        providers=(
            _bibliographic_source(
                "catalogue",
                "edition-1",
                titles=("Mistborn: The Final Empire",),
                series="Mistborn",
            ),
        ),
    )


_DEFAULT_SETTINGS_DOCUMENT = OwnerSettingsDocument.model_validate(
    {
        "schema_version": "owner-settings-v1",
        "revision": 1,
        "connections": [{"name": "primary", "endpoint": "https://library.invalid"}],
        "libraries": [
            {
                "name": "ebooks",
                "connection": "primary",
                "library_id": "library",
                "media_kind": "ebook",
            }
        ],
    }
)


@contextmanager
def _server(
    *,
    database: Path,
    hash_path: Path,
    certificate: Path,
    key: Path,
    port: ReservedPort,
    cover: bool = False,
    automatic_fixture: bool = False,
    cover_shapes: tuple[tuple[int, int], ...] = ((1, 1), (1, 1)),
    cohort: int = 0,
    settings_document: OwnerSettingsDocument | None = _DEFAULT_SETTINGS_DOCUMENT,
    on_app: Callable[[Any], None] | None = None,
):
    repository = open_repository(database, create=True, cross_thread=True)
    settings = None
    if settings_document is not None:
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO owner_settings VALUES(1,?,?)",
            (
                settings_document.revision,
                _canonical_json(settings_document.model_dump()),
            ),
        )
        connection.executemany(
            "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?)",
            [
                (f"connection.{item.name}.key", f"synthetic-{item.name}-secret")
                for item in settings_document.connections
            ],
        )
        connection.commit()
        connection.close()
        settings = OwnerSettingsOperations(repository.owner_settings_store())
    cover_considerations = None
    if cover:
        # Real pixel dimensions, because containment is a property of the
        # rendered image's intrinsic ratio against its frame and a 1x1 preview
        # would make every ratio assertion vacuously true (#644).
        payloads = tuple(
            _cover_preview(index, width=width, height=height)
            for index, (width, height) in enumerate(cover_shapes, 1)
        )
        sources = tuple(
            _cover_source(index, payload, incumbent=index == 1, embedded=index == 2)
            for index, payload in enumerate(payloads, 1)
        )
        base = _cover_report(sources)
        incumbent = sources[0].artifact
        assert incumbent is not None
        fields = tuple(
            TargetField(
                field.destination,
                TargetFieldState.VALUE,
                incumbent.artifact_id,
            )
            if field.destination is TargetDestination.COVER
            else field
            for field in base.target.fields
        )
        report = MetadataReport.create(
            target=TargetFacts(
                base.target.connection,
                base.target.library,
                base.target.item,
                fields,
            ),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=(
                _bibliographic_source(
                    "catalogue",
                    "edition-1",
                    titles=("Synthetic title revised",),
                    series="Mistborn",
                ),
            )
            if automatic_fixture
            else base.providers,
            cover_sources=base.cover_sources,
        )
        artifacts = CoverArtifactStore(database.parent / "cover-artifacts")
        for index, (source, payload) in enumerate(
            zip(sources, payloads, strict=True), 1
        ):
            assert source.artifact is not None
            artifacts.stage(source.artifact, f"original-{index}".encode(), payload)
        cover_considerations = CoverConsiderationService(
            repository,
            read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
                next(
                    source.artifact.preview_digest
                    for source in sources
                    if source.artifact is not None
                    and source.artifact.artifact_id == artifact_id
                )
            ),
            read_preview_digest=artifacts.read_preview_digest,
            clock=lambda: 1_000,
        )
    else:
        report = _report()
    repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    repository.publish_proposal(proposal_set)
    case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
    # A cohort large enough that a whole-Library projection is measurably
    # slower than one exact-case read. #635's acceptance is about what the
    # next field waits on, and one work cannot tell those two apart.
    for index in range(cohort):
        extra = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                f"cohort-item-{index:03d}",
                _bibliographic_fields(
                    f"Cohort work {index:03d}",
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE, TargetFieldState.MISSING
                    ),
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    ),
                ),
            ),
            file=FileFacts(0, f"{index:064x}", f"{index + 5_000:064x}"),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                _bibliographic_source(
                    "catalogue",
                    f"edition-{index}",
                    titles=(),
                    series="Mistborn",
                ),
            ),
        )
        repository.publish(extra, lambda: True)
        extra_proposal = evaluate_report(extra)
        repository.publish_proposal(extra_proposal)
        repository.admit_review_case(extra_proposal)
    origin = f"https://127.0.0.1:{port.number}"
    app = create_transport_test_app(
        repository,
        ReviewWebConfiguration(
            canonical_origin=origin,
            owner_password_hash_file=str(hash_path),
        ),
        clock=lambda: 1_000,
        close_repository=True,
        field_application=_BrowserActivityApplication(),
        react_asset_loader=ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
        cover_considerations=cover_considerations,
        settings=settings,
    )
    # The composed application, for a cohort that has to announce something the
    # server would announce rather than assert on what it answered.
    if on_app is not None:
        on_app(app)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port.number,
            log_level="error",
            access_log=False,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
            # Longer than any idle gap a test in this module leaves between
            # requests on one connection, with room to spare. The longest is
            # the 5,500 ms `page.wait_for_timeout` that proves the activity
            # poll has settled; every other gap -- `expect()` retries,
            # `press_sequentially`, viewport changes -- is shorter.
            #
            # It has to exceed them because `route.fetch()` is forwarded by
            # Playwright's Node driver, which keeps its own per-origin
            # keep-alive pool. That pool is not governed by the browser
            # context, is not flushed by `context.close()`, and outlives
            # `page.unroute()`. Under the previous one-second window a pooled
            # socket idled out, uvicorn closed it server-side, and the driver
            # selected that same socket inside the close race, reporting
            # `Route.fetch: socket hang up`. Only the two `route.fetch()`
            # tests ever showed it; the `route.continue_()` test never did,
            # because the browser owns that connection. The container gate's
            # `--cpus=2` widened the window, which is why it failed there
            # while the same commit passed on macOS (#730).
            timeout_keep_alive=30,
        )
    )
    # Everything above -- opening the repository, staging cover artifacts,
    # publishing the report and admitting the case -- ran while the port was
    # still held. It is given up here because uvicorn binds it inside
    # `server.run`, and two live binds on one loopback port is what the
    # operating system exists to refuse.
    port.release()
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("synthetic review server did not start")
    try:
        yield origin, str(case_id)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)


def _login(page: Page, origin: str) -> None:
    page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
    page.goto(f"{origin}/login")
    page.get_by_label("Password").fill(_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{origin}/library")


@contextmanager
def _closing_context(context: BrowserContext) -> Iterator[None]:
    try:
        yield
    finally:
        context.close()


@pytest.fixture(scope="module", autouse=True)
def _verified_playwright_browser() -> None:
    """Refuse the module before its first test, as `setUpClass` did.

    The `browser-payload-evidence-v1` record it returns names the Chromium
    revision, tree digest, Playwright version and platform, which is what
    makes "did the browser change between the failing run and the passing
    re-run" answerable. It was previously computed and discarded on the exact
    path that then fails, so no sighting could answer it (#730).

    It goes to `sys.__stderr__` because that is the only stream that survives
    the tier. `scripts/run_test_tier.py` redirects `sys.stdout` and
    `sys.stderr` to `os.devnull` around `pytest.main`, and `--capture=sys`
    replaces them again, so a bare `print` from a test reaches nothing. The
    tier's own stall breadcrumb uses this same seam for this same reason.
    """

    print(
        json.dumps(verify_playwright_browser(), sort_keys=True, separators=(",", ":")),
        file=sys.__stderr__,
        flush=True,
    )


def test_persistent_workspace_direct_entry_navigation_and_safe_decision() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                try:
                    page.get_by_role("heading", name="Works", exact=True).wait_for(
                        timeout=8_000
                    )
                except PlaywrightTimeoutError:
                    document = page.request.get(page.url)
                    pytest.fail(
                        f"url={page.url!r}; errors={errors!r}; "
                        f"status={document.status}; html={document.text()[:1200]!r}; "
                        f"dom={page.content()[:1200]!r}"
                    )
                page.set_viewport_size({"width": 1440, "height": 900})
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 1440
                )
                page.set_viewport_size({"width": 390, "height": 844})
                brand_box = page.locator(".brand").bounding_box()
                actions_box = page.locator(".shell-actions").bounding_box()
                settings_box = page.get_by_role(
                    "link", name="Settings", exact=True
                ).bounding_box()
                assert (
                    brand_box is not None
                    and actions_box is not None
                    and settings_box is not None
                )
                assert settings_box["width"] >= 44
                assert settings_box["height"] >= 44
                assert abs(settings_box["y"] - brand_box["y"]) <= 0.5
                assert brand_box["x"] + brand_box["width"] <= settings_box["x"]
                assert settings_box["x"] + settings_box["width"] <= actions_box["x"]
                documents = 0

                def count_document(request) -> None:
                    nonlocal documents
                    if request.resource_type == "document":
                        documents += 1

                page.on("request", count_document)
                initial_documents = documents
                page.get_by_role("button", name="Attention queue").click()
                attention_rail = page.locator(".attention-rail")
                attention_rail.wait_for()
                expect(attention_rail).to_be_focused()
                page.get_by_role("link", name="Policy", exact=True).click()
                page.get_by_role("heading", name="Library policy").wait_for()
                page.get_by_role("button", name="Curation").click()
                page.wait_for_url("**/policy?section=curation")
                expect(page.locator("#policy-curation")).to_be_focused()
                # A fresh container activated nothing. The seeded built-in
                # policy is named as the active baseline and never reported as
                # a completed owner activation over zero works.
                page.get_by_text("Active baseline \u00b7", exact=False).wait_for()
                policy_body = page.locator("body").inner_text()
                assert "Activation complete" not in policy_body
                assert "Policy update complete" not in policy_body
                assert "0 of 0" not in policy_body
                assert "Draft is separate from the active policy" not in policy_body
                assert page.locator(".policy-cutover").count() == 0
                page.locator(".cm-policy-finish").get_by_text(
                    "Change any field to create a new draft \u2014 validation always "
                    "covers the whole policy.",
                    exact=True,
                ).wait_for()
                policy_chapters = page.get_by_role("navigation", name="Policy chapters")
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "0",
                    "0",
                ]
                assert (
                    policy_chapters.evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                    )
                    == 2
                )
                assert (
                    policy_chapters.get_by_role(
                        "button", name="Curation"
                    ).get_attribute("aria-describedby")
                    == "policy-curation-changes"
                )
                page.get_by_text(
                    "Choose how metadata is stored and when you want to review it.",
                    exact=True,
                ).wait_for()
                unsupported_description = policy_chapters.get_by_role(
                    "button", name="Unsupported"
                ).get_by_text(
                    "Fields Mechanical Librarian can detect but cannot change yet.",
                    exact=True,
                )
                assert unsupported_description.count() == 1
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                ), page.evaluate(
                    """() => [...document.querySelectorAll('*')]
                        .filter(node => node.scrollWidth > node.clientWidth)
                        .map(node => [node.tagName, node.className, node.textContent?.slice(0, 40), node.clientWidth, node.scrollWidth, getComputedStyle(node).gridTemplateColumns])"""
                )
                genres = page.get_by_role("region", name="Genres")
                representation = genres.get_by_role(
                    "combobox", name="Genres representation"
                )
                representation.focus()
                page.keyboard.press("Enter")
                page.get_by_role("listbox", name="Genres representation").wait_for()
                page.keyboard.press("Escape")
                expect(representation).to_be_focused()
                representation.click()
                menu_box = page.get_by_role("listbox", name="Genres representation")
                expect(menu_box).to_have_css("scale", "none")
                menu_box = menu_box.bounding_box()  # type: ignore[assignment]  # reason: the name is deliberately rebound from the Locator to its measured bounding box, which the next lines assert on.
                assert menu_box is not None
                assert menu_box is not None
                assert menu_box["x"] >= 8
                assert (menu_box["x"] + menu_box["width"]) <= 382
                for option in page.get_by_role("option").all():
                    assert option.bounding_box()["height"] >= 44
                page.keyboard.press("Escape")
                for control in page.locator(
                    ".cm-policy-sidebar nav button, .cm-policy-posture button, "
                    ".cm-dropdown-trigger, .cm-policy-finish-actions button"
                ).all():
                    assert control.bounding_box()["height"] >= 44
                assert (
                    page.locator(".cm-policy-field").first.evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                    )
                    == 1
                )
                assert (
                    page.locator(".cm-policy-finish").evaluate(
                        "node => getComputedStyle(node).position"
                    )
                    == "static"
                )
                page.emulate_media(reduced_motion="reduce")
                assert page.locator(".cm-dropdown-menu").count() == 0
                genres_mode = genres.get_by_role("group", name="Genres review mode")
                original_mode = next(
                    button.inner_text()
                    for button in genres_mode.get_by_role("button").all()
                    if button.get_attribute("aria-pressed") == "true"
                )
                genres.get_by_role("button", name="Ignore").click()
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "1",
                    "0",
                ]
                # An exact revert returns every surface to the clean state: no
                # chapter count, no dirty header, no validation action.
                genres.get_by_role("button", name=original_mode, exact=True).click()
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "0",
                    "0",
                ]
                page.locator(".cm-policy-finish").get_by_text(
                    "Draft matches the active policy", exact=True
                ).wait_for()
                page.get_by_text("Draft matches active policy.", exact=True).wait_for()
                page.get_by_text("draft matches active", exact=False).first.wait_for()
                assert page.get_by_role(
                    "button", name="Validate and preview"
                ).is_disabled()
                genres.get_by_role("button", name="Ignore").click()
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "1",
                    "0",
                ]

                def assert_text_zoom_policy_layout(width: int) -> None:
                    page.set_viewport_size({"width": width, "height": 844})
                    page.wait_for_function(
                        """() => getComputedStyle(document.querySelector(
                            '.cm-policy-sidebar nav')).gridTemplateColumns.split(' ').length === 1"""
                    )
                    assert (
                        page.evaluate("() => document.documentElement.scrollWidth")
                        <= width
                    )
                    assert (
                        page.evaluate(
                            """() => [...document.querySelectorAll(
                            `.masthead nav, .masthead nav a, .cm-policy-route,
                            .cm-policy-editor, .cm-policy-finish,
                            .cm-policy-finish-actions, .cm-policy-finish-actions button`)]
                            .filter(node => node.scrollWidth > node.clientWidth &&
                            getComputedStyle(node).overflowX === 'visible')
                            .map(node => [node.tagName, node.className, node.clientWidth, node.scrollWidth])"""
                        )
                        == []
                    )

                page.locator("html").evaluate(
                    "node => { node.style.fontSize = '200%'; }"
                )
                assert_text_zoom_policy_layout(390)
                assert (
                    page.locator(".cm-policy-sidebar nav").evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                    )
                    == 1
                )
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert page.get_by_role("link", name="Policy", exact=True).is_visible()
                assert page.get_by_role(
                    "link", name="Activity", exact=True
                ).is_visible()
                assert not (
                    page.evaluate(
                        """() => [...document.querySelectorAll('*')].some(node =>
                        node.scrollWidth > node.clientWidth &&
                        !node.classList.contains('brand-mark') &&
                        getComputedStyle(node).overflowX === 'visible')"""
                    )
                )
                assert_text_zoom_policy_layout(430)
                page.get_by_role("button", name="Use dark mode").click()
                page.get_by_role("button", name="Use light mode").wait_for()
                assert_text_zoom_policy_layout(390)
                assert_text_zoom_policy_layout(430)
                page.get_by_role("button", name="Use light mode").click()
                page.get_by_role("button", name="Use dark mode").wait_for()
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("html").evaluate("node => { node.style.fontSize = ''; }")
                page.get_by_role("button", name="Validate and preview").click()
                page.get_by_text(
                    "Impact preview is current. The active policy is unchanged."
                ).wait_for()
                review_activation = page.get_by_role("button", name="Review activation")
                page.locator("html").evaluate(
                    "node => { node.style.fontSize = '200%'; }"
                )
                assert_text_zoom_policy_layout(430)
                page.locator("html").evaluate("node => { node.style.fontSize = ''; }")
                review_activation.click()
                page.get_by_text("Activate this reviewed draft?").wait_for()
                assert page.get_by_role("dialog").count() == 0
                page.locator("html").evaluate(
                    "node => { node.style.fontSize = '200%'; }"
                )
                assert_text_zoom_policy_layout(430)
                page.locator("html").evaluate("node => { node.style.fontSize = ''; }")
                page.get_by_role("button", name="Back to preview").click()
                review_activation.wait_for()
                dark_mode = page.get_by_role("button", name="Use dark mode")
                dark_mode.click()
                page.get_by_role("button", name="Use light mode").wait_for()
                page.set_viewport_size({"width": 1440, "height": 900})
                unsupported_description.wait_for()
                assert page.get_by_role(
                    "navigation", name="Policy chapters"
                ).is_visible()
                # The sticky finish tray spans both columns and never covers a
                # sidebar safety control, with the safety group expanded.
                if not page.locator(".cm-policy-mode").evaluate("node => node.open"):
                    page.locator(".cm-policy-mode summary").click()
                page.get_by_role("button", name="Enter Preview mode").wait_for()
                sidebar_box = page.locator(".cm-policy-sidebar").bounding_box()
                tray_box = page.locator(".cm-policy-finish").bounding_box()
                assert sidebar_box is not None and tray_box is not None
                sidebar_right = sidebar_box["x"] + sidebar_box["width"]
                assert tray_box["x"] < sidebar_right
                assert (tray_box["x"] + tray_box["width"]) > sidebar_right
                covered = """() => {
                    const rail = document.querySelector('.cm-policy-sidebar')
                    const side = rail.getBoundingClientRect()
                    const tray = document.querySelector(
                        '.cm-policy-finish').getBoundingClientRect()
                    return [...rail.querySelectorAll('button, summary')]
                        .map(node => [node, node.getBoundingClientRect()])
                        .filter(([, box]) => {
                            const top = Math.max(box.top, side.top)
                            const bottom = Math.min(box.bottom, side.bottom)
                            return bottom > top && bottom > tray.top &&
                                top < tray.bottom && box.right > tray.left &&
                                box.left < tray.right
                        })
                        .map(([node]) => node.textContent.slice(0, 48))
                    }"""
                assert page.evaluate(covered) == []
                # Anything the bounded rail clips stays reachable by scrolling
                # the rail itself, and is still never under the tray.
                assert (
                    page.locator(".cm-policy-sidebar").evaluate(
                        "node => getComputedStyle(node).overflowY"
                    )
                    != "visible"
                )
                page.locator(".cm-policy-sidebar").evaluate(
                    "node => { node.scrollTop = node.scrollHeight }"
                )
                page.get_by_role("button", name="Pause automatic decisions").click()
                page.get_by_role("button", name="Resume automatic decisions").wait_for()
                assert page.evaluate(covered) == []
                page.get_by_role("button", name="Resume automatic decisions").click()
                page.get_by_role("button", name="Pause automatic decisions").wait_for()
                review_activation.click()
                page.get_by_role("button", name="Back to preview").click()
                review_activation.wait_for()
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 1440
                )
                page.set_viewport_size({"width": 390, "height": 844})
                light_mode = page.get_by_role("button", name="Use light mode")
                light_mode.click()
                page.get_by_role("button", name="Use dark mode").wait_for()
                activity_requests = 0
                held_page_size_response: list[tuple[Any, Any, Any]] = []

                def activity_poll(route) -> None:
                    nonlocal activity_requests
                    activity_requests += 1
                    response = route.fetch()
                    payload = response.json()
                    payload["counts"]["claimed"] = 1
                    query = parse_qs(urlparse(route.request.url).query)
                    payload["ledger"].update(
                        page=int(query.get("page", ["1"])[0]),
                        page_count=3,
                        page_size=int(query.get("page_size", ["25"])[0]),
                        total=75,
                    )
                    if query.get("page_size") == ["50"] and not held_page_size_response:
                        held_page_size_response.append((route, response, payload))
                        return
                    route.fulfill(status=response.status, json=payload)

                page.route("**/api/v1/activity?*", activity_poll)
                page.get_by_role("link", name="Activity", exact=True).click()
                # This harness serves prepared work with no consent yet, and
                # that is what the panel says. It used to claim "No
                # intervention needed" directly above its own
                # `3 Awaiting permission`, which is the claim #639 forbids.
                page.get_by_role(
                    "heading", name="Prepared work needs your permission"
                ).wait_for()
                expect(page.get_by_text("Awaiting permission")).to_be_visible()
                for calm in (
                    "No intervention needed",
                    "No action required",
                    "No action is required.",
                ):
                    expect(page.get_by_text(calm, exact=True)).to_have_count(0)
                assert activity_requests >= 1
                activity_search = page.get_by_role("searchbox", name="Search activity")
                activity_search.click()
                activity_search.press_sequentially("ab")
                assert activity_search.input_value() == "ab"
                page.wait_for_url("**/activity?*q=ab*")
                expect(activity_search).to_be_focused()
                next_page = page.get_by_role("button", name="Next", exact=True)
                next_page.click()
                page.wait_for_url("**/activity?*page=2*")
                expect(next_page).to_be_focused()
                page_number = page.get_by_role(
                    "spinbutton", name="Activity page number"
                )
                expect(page_number).to_have_value("2")
                page_number.fill("99")
                page_number.press("Enter")
                page.wait_for_url("**/activity?*page=3*")
                page_number = page.get_by_role(
                    "spinbutton", name="Activity page number"
                )
                assert page_number.input_value() == "3"
                expect(page_number).to_be_focused()
                page_number.fill("2")
                page_number.press("Escape")
                assert page_number.input_value() == "3"
                expect(page_number).to_be_focused()
                previous_page = page.get_by_role("button", name="Previous", exact=True)
                previous_page.click()
                page.wait_for_url("**/activity?*page=2*")
                previous_page.click()
                page.wait_for_url("**/activity?*page=1*")
                expect(
                    page.get_by_role("spinbutton", name="Activity page number")
                ).to_be_focused()
                page.get_by_role("button", name="Last", exact=True).click()
                page.wait_for_url("**/activity?*page=3*")
                expect(
                    page.get_by_role("spinbutton", name="Activity page number")
                ).to_be_focused()
                rows = page.get_by_role("combobox", name="Rows per activity page")
                rows.click()
                page.get_by_role("option", name="50 per page").click()
                expect(rows).to_be_focused()
                page.set_viewport_size({"width": 1440, "height": 900})
                activity_search = page.get_by_role("searchbox", name="Search activity")
                search_node = activity_search.element_handle()
                assert search_node is not None
                activity_search.fill("")
                assert activity_search.input_value() == ""
                expect(activity_search).to_be_focused()
                assert len(held_page_size_response) == 1
                held_route, held_response, held_payload = held_page_size_response[0]
                held_route.fulfill(status=held_response.status, json=held_payload)
                assert activity_search.input_value() == ""
                expect(activity_search).to_be_focused()
                assert search_node.evaluate("node => node.isConnected")
                activity_search.press_sequentially("desktop")
                assert activity_search.input_value() == "desktop"
                page.wait_for_url("**/activity?*q=desktop*")
                expect(activity_search).to_be_focused()
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("link", name="Library", exact=True).click()
                page.get_by_role("heading", name="Works", exact=True).wait_for()
                settled_activity_requests = activity_requests
                page.wait_for_timeout(5_500)
                assert activity_requests == settled_activity_requests
                page.unroute("**/api/v1/activity?*", activity_poll)
                assert documents == initial_documents
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                page.locator(".workspace-detail").wait_for()
                page.get_by_role(
                    "heading", name="Which title should this work use?"
                ).wait_for()
                expect(page.locator(".selected-work-header")).to_be_focused()
                assert page.locator("img,script:not([src])").count() == 0
                assert "<img" in page.locator("body").inner_text()
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert (
                    page.get_by_role(
                        "button", name="Choose current value:", exact=False
                    ).count()
                    == 1
                )
                assert (
                    page.get_by_role(
                        "button", name="Choose proposed value", exact=False
                    ).count()
                    == 1
                )
                page.get_by_role("button", name="Use dark mode").click()
                page.get_by_role("button", name="Use light mode").wait_for()
                page.get_by_role(
                    "button", name="Choose current value:", exact=False
                ).first.click()
                # The acknowledgement advances to the next field rather than
                # leaking a recorded status across the selected-work surface.
                page.get_by_role(
                    "heading", name="Which series should this work use?"
                ).wait_for()
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                page.get_by_role("heading", name="Title").wait_for()
                # #635: revisiting a recorded field restores the whole choice
                # composition and marks the recorded answer, rather than
                # replacing it with prose.
                recorded_title = page.get_by_role(
                    "button", name="Choose current value:", exact=False
                )
                assert recorded_title.count() == 1
                expect(recorded_title).to_have_attribute("aria-pressed", "true")
                page.get_by_role("link", name="Activity", exact=True).click()
                page.get_by_role("heading", name="Activity and recovery").wait_for()
                page.go_back()
                page.get_by_role("heading", name="Title").wait_for()
                page.go_forward()
                page.get_by_role("heading", name="Activity and recovery").wait_for()
                assert documents == (initial_documents + 2)
                assert errors == []
            browser.close()


def test_protected_cover_round_trip_preserves_explicit_owner_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        responses: list[tuple[str, int]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    responses.append((response.url, response.status))
                    if "/cover-considerations" in response.url
                    or response.url.endswith("/decisions")
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    automatic_fixture=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.get_by_role("heading", name="Works", exact=True).wait_for()

                # This is a closed presentation fixture, not a new automatic
                # authority path: its projections retain the exact candidate
                # values already supplied by the current case.  It protects
                # restoration of recorded automatic choices, including cover
                # values, without widening the qualified-policy envelope.
                read_only_title_candidate = False

                def automatic_projection(route) -> None:
                    response = route.fetch()
                    payload = response.json()
                    fields = payload["fields"]
                    outcomes = {
                        "title": "candidate",
                        "series": "keep_current",
                        "tags": "ignore",
                        "cover": "candidate",
                    }
                    for index, field in enumerate(fields, 1):
                        outcome = outcomes.get(field["destination"])
                        if outcome is None:
                            continue
                        if (
                            field["destination"] == "cover"
                            and field["cover_consideration"] is None
                        ):
                            continue
                        selected = None
                        if outcome == "candidate":
                            selected = (
                                next(
                                    {
                                        "kind": "cover",
                                        "value": candidate["artifact_id"],
                                    }
                                    for candidate in field["cover_consideration"][
                                        "candidates"
                                    ]
                                    if candidate["selectable"]
                                )
                                if field["destination"] == "cover"
                                else field["proposed_values"][0]
                            )
                        read_only_candidate = (
                            field["destination"] == "title"
                            and read_only_title_candidate
                        )
                        if read_only_candidate:
                            field["human_decision"] = None
                            for context in field["choice_contexts"]:
                                context["evidence"] = [
                                    {
                                        "source": (
                                            "Automatic candidate source"
                                            if context["selection"] == "proposed"
                                            else "Automatic current source"
                                        ),
                                        "observation": "Synthetic automatic evidence.",
                                        "role": "external_corroboration",
                                    }
                                ]
                        field.update(
                            automatic_decision={
                                "candidate_id": (
                                    None
                                    if outcome != "candidate"
                                    else "alternative_" + f"{index:064x}"
                                ),
                                "decision_id": "automatic_decision_" + f"{index:064x}",
                                "destination": field["destination"],
                                "occurred_at": 1_000,
                                "outcome": outcome,
                                "proposal_id": field["proposal_id"],
                                "review_case_id": payload["review_case_id"],
                                "selected_value": selected,
                                "target_fingerprint": field["target_fingerprint"],
                            },
                            classification="automatic_recorded",
                            classification_reason=(
                                "Automatic handling recorded this field; "
                                "you may inspect or override it."
                            ),
                            decision_actions=[]
                            if read_only_candidate
                            else field["decision_actions"],
                            editable=not read_only_candidate,
                            needs_attention=False,
                            navigation_role="inspect",
                        )
                    route.fulfill(status=response.status, json=payload)

                page.route(f"**/api/v1/review-cases/{case_id}", automatic_projection)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                title_current = page.get_by_role(
                    "button", name="Choose current value:", exact=False
                )
                title_candidate = page.get_by_role(
                    "button", name="Choose proposed value", exact=False
                )
                expect(title_candidate).to_have_attribute("aria-pressed", "true")
                assert title_candidate.locator("xpath=..").evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                expect(title_current).to_have_attribute("aria-pressed", "false")
                assert not (
                    title_current.locator("xpath=..").evaluate(
                        "node => node.classList.contains('is-selected')"
                    )
                )
                page.reload()
                expect(title_candidate).to_have_attribute("aria-pressed", "true")
                assert title_candidate.locator("xpath=..").evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as override_response:
                    before = page.locator(_CURRENT_FIELD_SELECTOR).first.inner_text()
                    title_current.focus()
                    page.keyboard.press("Enter")
                assert 200 == override_response.value.status
                # Acknowledged exact-case state advances the owner. Reopening
                # remains the place to inspect and revise the recorded title.
                page.wait_for_function(_CURRENT_FIELD_CHANGED, arg=before)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                expect(title_current).to_have_attribute("aria-pressed", "true")
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
                series_current = page.get_by_role(
                    "button", name="Choose current value:", exact=False
                )
                expect(series_current).to_have_attribute("aria-pressed", "true")
                assert series_current.locator("xpath=..").evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                read_only_title_candidate = True
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                page.get_by_role("heading", name="Title", exact=True).wait_for()
                recorded = page.get_by_label("Title recorded outcome")
                recorded.get_by_text("Resolved automatically", exact=True).wait_for()
                expect(recorded.get_by_text("Chosen value", exact=True)).to_be_visible()
                expect(
                    recorded.get_by_text("Synthetic title revised", exact=True)
                ).to_be_visible()
                assert (
                    page.get_by_role(
                        "button", name="Choose proposed value", exact=False
                    ).count()
                    == 0
                )
                recorded.get_by_role("button", name="Evidence", exact=True).click()
                recorded.get_by_text(
                    "Automatic candidate source", exact=True
                ).wait_for()
                assert (
                    recorded.get_by_text("Automatic current source", exact=True).count()
                    == 0
                )
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                page.get_by_role("region", name="Choose a cover").wait_for()
                expect(page.get_by_alt_text("Current cover")).to_be_visible()
                expect(page.get_by_alt_text("Alternative 1 cover")).to_be_visible()
                current_cover = page.locator(".library-cover-card").filter(
                    has_text="Current cover"
                )
                automatic_cover = page.locator(".library-cover-card").filter(
                    has_text="Alternative 1 cover"
                )
                expect(automatic_cover).to_have_attribute("aria-pressed", "true")
                assert automatic_cover.evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                expect(current_cover).to_have_attribute("aria-pressed", "false")
                assert not (
                    current_cover.evaluate(
                        "node => node.classList.contains('is-selected')"
                    )
                )
                page.reload()
                expect(
                    page.locator(".library-cover-card").filter(
                        has_text="Alternative 1 cover"
                    )
                ).to_have_attribute("aria-pressed", "true")
                assert (
                    page.locator(".library-cover-card")
                    .filter(has_text="Alternative 1 cover")
                    .evaluate("node => node.classList.contains('is-selected')")
                )
                with (
                    page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as decision_response,
                ):
                    page.locator(".library-cover-card").filter(
                        has_text="Alternative 1 cover"
                    ).click()
                response = decision_response.value
                assert 200 == response.status, f"decision response: {response.text()}"
                page.get_by_role("heading", name="Review complete").wait_for()
                page.goto(f"{origin}/review/{case_id}?field=cover&subview=history")
                page.get_by_role("heading", name="History").wait_for()
                selected_history = page.get_by_role(
                    "heading", name="Cover selected"
                ).locator("..")
                selected_history.get_by_text("Visual review", exact=False).wait_for()
                selected_history.get_by_text(
                    "Owner outcome: accept", exact=False
                ).wait_for()
                assert "candidate" in selected_history.inner_text().lower()
                for identifier in (
                    "cover_consideration_",
                    "cover_ref_",
                    "human_decision_",
                    "review_transition_",
                ):
                    assert (
                        identifier not in page.locator(".diagnostic-route").inner_text()
                    )
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert responses
                assert all(status == 200 for _, status in responses), responses
                assert errors == []
            browser.close()


# Measure each rendered cover against its own frame's content box. A cover that
# overruns its frame is clipped by `overflow:hidden` and is invisible to
# `document.scrollWidth`, so the document-level check cannot see it at all --
# the same trap `overrunning_rail_controls` exists to avoid (#644).
_COVER_CONTAINMENT = """(selector) => {
  const frames = [...document.querySelectorAll(selector)]
  if (frames.length === 0) return [{ error: 'no cover frames', selector }]
  const inside = (box, content) =>
    box.left >= content.left - 0.5 && box.right <= content.right + 0.5 &&
    box.top >= content.top - 0.5 && box.bottom <= content.bottom + 0.5
  return frames.map((frame) => {
    const style = getComputedStyle(frame)
    const box = frame.getBoundingClientRect()
    const content = {
      left: box.left + parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth),
      right: box.right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth),
      top: box.top + parseFloat(style.paddingTop) + parseFloat(style.borderTopWidth),
      bottom: box.bottom - parseFloat(style.paddingBottom) - parseFloat(style.borderBottomWidth),
    }
    const shared = {
      background: style.backgroundColor,
      frame_ratio: (content.right - content.left) / (content.bottom - content.top),
      frame_top: Math.round(box.top),
      frame_width: Math.round(content.right - content.left),
    }
    const image = frame.querySelector('img')
    if (image === null) {
      const alternative = frame.matches('[role="img"]') ? frame : frame.querySelector('[role="img"]')
      return {
        ...shared,
        kind: 'alternative',
        label: alternative === null ? null : alternative.getAttribute('aria-label'),
        text: (frame.textContent || '').trim(),
        bounded: alternative !== null && inside(alternative.getBoundingClientRect(), content),
      }
    }
    const imageBox = image.getBoundingClientRect()
    const natural = [image.naturalWidth, image.naturalHeight]
    // `object-fit: contain` paints inside the element box; the painted area is
    // what the owner actually sees, so containment is measured on that, not on
    // the element box alone.
    const scale = Math.min(imageBox.width / natural[0], imageBox.height / natural[1])
    const painted = { width: natural[0] * scale, height: natural[1] * scale }
    const paintedBox = {
      left: imageBox.left + (imageBox.width - painted.width) / 2,
      right: imageBox.right - (imageBox.width - painted.width) / 2,
      top: imageBox.top + (imageBox.height - painted.height) / 2,
      bottom: imageBox.bottom - (imageBox.height - painted.height) / 2,
    }
    return {
      ...shared,
      kind: 'image',
      alt: image.alt,
      loaded: image.complete && natural[0] > 0 && natural[1] > 0,
      natural,
      object_fit: getComputedStyle(image).objectFit,
      element_in_frame: inside(imageBox, content),
      painted_in_frame: inside(paintedBox, content),
      ratio_error: Math.abs(painted.width / painted.height - natural[0] / natural[1]),
      painted_ratio: painted.width / painted.height,
    }
  })
}"""

# Very tall, wide, portrait and square, all within the 320x480 preview
# bound the cover packet enforces. Four ratios that no single frame can
# satisfy is the point: containment has to hold for all of them at once.
# The first two are the incumbent and the embedded candidate, which are
# the pair #640's Library track shows without a provider identity, so the
# widest ratio spread lands on the surface with the least room for it.
_COVER_SHAPES = ((120, 480), (320, 140), (200, 300), (280, 280))
_LIBRARY_SHAPES = _COVER_SHAPES[:2]


def test_protected_cover_comparison_contains_every_image_without_cropping() -> None:
    """#644: every cover is shown whole, in a quiet 5:8 frame, at every width.

    The frames are measured against their own content boxes because a cropped
    cover is clipped inside `overflow:hidden` and never reaches the document
    scroll width. Real mixed-ratio PNGs are served so the intrinsic ratio the
    browser reports is the ratio being contained.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    cover_shapes=_COVER_SHAPES,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                page.get_by_role("region", name="Choose a cover").wait_for()

                def measured(selector: str) -> list[dict[str, Any]]:
                    page.wait_for_function(
                        "(selector) => [...document.querySelectorAll(selector)]"
                        ".every((image) => image.complete)",
                        arg=f"{selector} img",
                    )
                    return cast(
                        "list[dict[str, Any]]",
                        page.evaluate(_COVER_CONTAINMENT, selector),
                    )

                def assert_contained(
                    selector: str,
                    viewport: int,
                    *,
                    label: str,
                    shapes: tuple[tuple[int, int], ...],
                ) -> list[dict[str, Any]]:
                    frames = measured(selector)
                    images = [frame for frame in frames if frame["kind"] == "image"]
                    assert len(images) == len(shapes), (label, frames)
                    for frame in images:
                        context_line = (label, frame)
                        assert frame["loaded"], context_line
                        assert frame["object_fit"] == "contain", context_line
                        assert frame["element_in_frame"], context_line
                        assert frame["painted_in_frame"], context_line
                        assert frame["ratio_error"] < 0.02, context_line
                        # A quiet 5:8 comparison frame, not the image's ratio.
                        assert abs(frame["frame_ratio"] - 5 / 8) < 0.02, context_line
                        # Transparent letterboxing paints the choice card's own
                        # surface, so unused frame area cannot read as content.
                        assert frame["background"] == "rgba(0, 0, 0, 0)", context_line
                    # Mixed proportions coexist without being normalised.
                    assert {tuple(frame["natural"]) for frame in images} == {
                        shape for shape in shapes
                    }, (label, images)
                    assert (
                        page.evaluate("() => document.documentElement.scrollWidth")
                        <= viewport
                    ), label
                    return frames

                assert_contained(
                    ".library-cover-image",
                    1440,
                    label="library:desktop",
                    shapes=_LIBRARY_SHAPES,
                )

                # 200% zoom is a halved CSS-pixel viewport at twice the device
                # scale, which is exactly the layout the owner gets when they
                # zoom a 1440x900 window to 200%.
                page.set_viewport_size({"width": 720, "height": 450})
                assert_contained(
                    ".library-cover-image",
                    720,
                    label="library:zoom200",
                    shapes=_LIBRARY_SHAPES,
                )

                page.set_viewport_size({"width": 390, "height": 844})
                compact = assert_contained(
                    ".library-cover-image",
                    390,
                    label="library:compact",
                    shapes=_LIBRARY_SHAPES,
                )
                # Below 900px the current cover joins the same scrollable row
                # as every replacement rather than sitting above them.
                assert len({frame["frame_top"] for frame in compact}) == 1, compact
                track = page.locator(
                    ".library-cover-pinned, .library-cover-layout"
                ).first
                assert track.evaluate(
                    "node => node.scrollWidth > node.clientWidth + 1"
                ), "the compact comparison row provides no scroll affordance"
                assert (
                    track.evaluate("node => getComputedStyle(node).overscrollBehaviorX")
                    == "contain"
                )
                expect(page.locator(".cover-track-hint")).to_be_visible()

                assert errors == []
                # A cover whose bytes never arrive keeps a bounded, labelled
                # alternative inside the same frame. The aborted requests are
                # the only console errors this test tolerates, and they are
                # named rather than admitted by an empty allowance.
                page.route(
                    "**/cover-considerations/**/images/**",
                    lambda route: route.abort(),
                )
                page.reload()
                page.get_by_text("Image unavailable").first.wait_for()
                unavailable = cast(
                    "list[dict[str, Any]]",
                    page.evaluate(_COVER_CONTAINMENT, ".library-cover-image"),
                )
                alternatives = [
                    frame for frame in unavailable if frame["kind"] == "alternative"
                ]
                assert len(alternatives) == len(unavailable), unavailable
                for frame in alternatives:
                    assert frame["bounded"], frame
                    assert frame["text"] == "Image unavailable", frame
                    assert frame["label"] == "Cover unavailable", frame
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert errors and all(
                    "Failed to load resource" in error for error in errors
                ), errors
            browser.close()


_CURRENT_FIELD_SELECTOR = '.field-filmstrip button[aria-current="true"]'
_CURRENT_FIELD_CHANGED = (
    "(previous) => { const cell = document.querySelector("
    f"{_CURRENT_FIELD_SELECTOR!r}"
    "); return cell !== null && cell.innerText !== previous }"
)
_COHORT_WORKS = 50


def test_recorded_decision_advances_without_whole_library_projection() -> None:
    """#635: the next field arrives from exact-case state, not from a Library rebuild.

    Fifty works, because one work cannot tell a whole-Library projection apart
    from a single exact-case read. The ordering assertion is a wall-clock
    threshold's opposite: no Library response may complete before the next
    field is actionable, so a settlement that awaits the projection fails it
    whatever the machine's speed.

    That ordering was the whole observable until #641 gave a decision a second
    reason to rebuild the Library, on a coalescing delay long enough that the
    refetch usually landed after the counter had already been read. So the
    each post-advance Library request is held and checked against the committed
    field before it is released. That makes ordering observable directly rather
    than inferring it from a response landing inside a timing window.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        announced: list[Any] = []
        library_requests: list[str] = []
        library_request_fields: list[str | None] = []
        library_responses: list[float] = []
        decision_requests: list[str | None] = []
        decision_responses: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    library_responses.append(time.monotonic())
                    if "/api/v1/library" in response.url
                    else decision_responses.append(response.url)
                    if response.url.endswith("/decisions")
                    else None
                ),
            )

            def capture_request(request: Request) -> None:
                if request.url.endswith("/decisions"):
                    decision_requests.append(request.headers.get("idempotency-key"))
                elif "/api/v1/library" in request.url:
                    library_requests.append(request.url)
                    field = page.locator(_CURRENT_FIELD_SELECTOR).first
                    library_request_fields.append(
                        field.inner_text() if field.count() else None
                    )

            page.on("request", capture_request)
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cohort=_COHORT_WORKS - 1,
                    on_app=lambda app: announced.append(
                        app.state.review_application.updates
                    ),
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                search = f"?lens=needs_attention&q=Mistborn&preview={case_id}"
                page.goto(f"{origin}/library{search}")
                page.locator(".field-map").wait_for()
                page.wait_for_load_state("networkidle")
                # Exercise an actual middle-field advance after the owner has
                # read down the page. The rail keeps its original visible order
                # and may move horizontally, but must not pull the document up.
                rail_order = [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ]
                page.locator(".field-filmstrip button").nth(1).click()
                page.evaluate("window.scrollTo(0, 220)")
                scroll_before = page.evaluate("() => window.scrollY")
                page.emulate_media(reduced_motion="no-preference")
                held_library_requests: list[Any] = []

                def hold_library_request(route: Any) -> None:
                    held_library_requests.append(route)

                page.route("**/api/v1/library**", hold_library_request)

                def current_field() -> str:
                    return page.locator(_CURRENT_FIELD_SELECTOR).first.inner_text()

                def advance(*, pointer: bool = False) -> tuple[float, int, str, str]:
                    before = current_field()
                    settled = len(library_responses)
                    choice = page.get_by_role(
                        "button", name="Choose current value:", exact=False
                    ).first
                    started = time.monotonic()
                    if pointer:
                        choice.click()
                    else:
                        choice.focus()
                        page.keyboard.press("Enter")
                    page.wait_for_function(
                        _CURRENT_FIELD_CHANGED,
                        arg=before,
                    )
                    return (
                        time.monotonic() - started,
                        len(library_responses) - settled,
                        before,
                        current_field(),
                    )

                def release_aggregate_request(
                    expected: int, *, previous: str, committed: str
                ) -> None:
                    deadline = time.monotonic() + 5
                    while (
                        len(held_library_requests) < expected
                        or len(library_request_fields) < issued + expected
                    ) and time.monotonic() < deadline:
                        page.wait_for_timeout(10)
                    assert len(held_library_requests) == expected, held_library_requests
                    assert len(library_request_fields) == issued + expected
                    field = library_request_fields[issued + expected - 1]
                    assert field == committed
                    assert field != previous
                    held_library_requests[expected - 1].continue_()

                issued = len(library_requests)
                elapsed, projections, first_field, first_next = advance(pointer=True)
                # The aggregate request is held after it starts. The exact
                # case still advances, proving this presentation-ready step
                # does not await a whole-Library read; a request can only see
                # the committed next field, never the recorded one.
                assert projections == 0, (elapsed, projections)
                release_aggregate_request(1, previous=first_field, committed=first_next)
                # A generous ceiling: the measured exact-case read is single
                # figures of milliseconds, and a whole-Library projection over
                # this cohort is two orders of magnitude slower.
                assert elapsed < 1.0, elapsed
                # The exact decision acknowledgement is the authority for this
                # advance; aggregate Library reads remain out of the path.
                assert len(decision_responses) == 1, decision_responses
                assert len(decision_requests) == 1, decision_requests
                assert decision_requests[0]
                assert page.evaluate("() => window.scrollY") == scroll_before
                assert page.evaluate(
                    "() => document.activeElement?.matches('.decision-canvas button:not(:disabled)')"
                )
                page.mouse.move(0, 0)
                pointer_focus = page.evaluate(
                    """() => {
                      const card = document.activeElement?.closest('.metadata-choice')
                      const probe = document.createElement('div')
                      probe.style.background = 'var(--surface-strong)'
                      document.body.append(probe)
                      const result = {
                        background: card && getComputedStyle(card).backgroundColor,
                        base: getComputedStyle(probe).backgroundColor,
                        hover: card?.matches(':hover'),
                        keyboardFocus: card?.matches(':has(:focus-visible)'),
                      }
                      probe.remove()
                      return result
                    }"""
                )
                assert pointer_focus == {
                    "background": pointer_focus["base"],
                    "base": pointer_focus["base"],
                    "hover": False,
                    "keyboardFocus": False,
                }
                assert [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ] == rail_order
                assert (
                    page.locator(".decision-canvas").first.evaluate(
                        "node => getComputedStyle(node).animationName"
                    )
                    == "library-field-arrive"
                )
                page.emulate_media(reduced_motion="reduce")
                # The same advanced state remains, but the live media-query
                # change removes its spatial animation. Chromium serialises
                # that removal as either an empty name or `none`.
                reduced_animation = page.locator(".decision-canvas").first.evaluate(
                    "node => ({ name: getComputedStyle(node).animationName, "
                    "transform: getComputedStyle(node).transform })"
                )
                assert reduced_animation == {"name": "", "transform": "none"} or (
                    reduced_animation == {"name": "none", "transform": "none"}
                )
                # The next field is actionable by keyboard without a pointer.
                assert page.evaluate(
                    "() => document.activeElement !== null &&"
                    " document.activeElement !== document.body"
                )
                # Convergence never changes the active search, lens or work.
                # Only `field` moves, which is the advance itself.
                preserved = parse_qs(urlparse(page.url).query)
                assert preserved["lens"] == ["needs_attention"]
                assert preserved["q"] == ["Mistborn"]
                assert preserved["preview"] == [case_id]
                assert preserved["field"] != []
                advanced_field = preserved["field"][0]

                # The recorded middle field is revisitable before the owner
                # continues, and still shows every eligible option with the
                # recorded one integrated into the choice treatment.
                page.locator(".field-filmstrip button").filter(
                    has_text=first_field.split("\n")[0]
                ).first.click()
                recorded = page.get_by_role(
                    "button", name="Choose current value:", exact=False
                ).first
                expect(recorded).to_have_attribute("aria-pressed", "true")
                assert (
                    page.get_by_role(
                        "button", name="Take no action", exact=True
                    ).count()
                    == 0
                )
                assert page.get_by_role(
                    "button", name="Choose proposed value", exact=False
                ).count()
                assert (
                    "You can select another eligible value"
                    in page.locator(".decision-canvas").first.inner_text()
                )

                # Back and Forward keep the same Library position, then the
                # owner returns to the exact field the first decision opened.
                page.go_back()
                page.wait_for_load_state("networkidle")
                page.go_forward()
                page.wait_for_load_state("networkidle")
                assert page.url.startswith(f"{origin}/library")
                assert "lens=needs_attention" in page.url
                page.locator(".field-filmstrip button").filter(
                    has_text=advanced_field.title()
                ).first.click()

                # A second choice is accepted while the first settlement is
                # still converging, in order, without a reload.
                second_elapsed, second_projections, second_field, second_next = (
                    advance()
                )
                assert second_projections == 0, (second_elapsed, second_projections)
                release_aggregate_request(
                    2, previous=second_field, committed=second_next
                )
                assert second_field != first_field
                deadline = time.monotonic() + 10
                while len(decision_responses) < 2 and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert len(decision_responses) == 2, decision_responses
                assert len(decision_requests) == 2, decision_requests
                assert all(decision_requests)
                assert decision_requests[0] != decision_requests[1]

                # Aggregate refreshes may now converge badges and the work rail,
                # but remain background work: the exact-case acknowledgement
                # above advanced and focused the next field before either one
                # completed.
                page.unroute("**/api/v1/library**", hold_library_request)
                page.wait_for_timeout(600)
                assert len(library_requests[issued:]) == 2, library_requests[issued:]

                # And the other half of the same rule: everything this tab did
                # not cause still reaches it while the owner keeps recording.
                # This is the announcement a work makes when it finishes
                # preparing and becomes browseable, published through the
                # server's own channel, naming a case that is not this one.
                # Suppressing it would be a panel that silently stops updating
                # while a large library is still preparing, which is the whole
                # of what #641 exists to prevent.
                channel = announced[0]
                cutover = len(library_requests)
                channel.publish("library", "review_case_" + "0" * 64)
                deadline = time.monotonic() + 10
                while len(library_requests) == cutover and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert len(library_requests) > cutover, (
                    "a work finishing preparation never reached the Library"
                )

                assert errors == []
            browser.close()


def test_compact_lower_choice_reveals_only_the_next_focused_control() -> None:
    """#635: a lower compact choice reveals, but does not centre, the next action."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 320},
                reduced_motion="reduce",
            )
            page = context.new_page()
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(
                    f"{origin}/library?lens=needs_attention&preview={case_id}&field=title"
                )
                page.locator(".decision-canvas").wait_for()
                rail_order = [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ]
                before = page.locator(_CURRENT_FIELD_SELECTOR).first.inner_text()
                lower_choice = page.get_by_role(
                    "button", name="Choose proposed value", exact=False
                )
                lower_choice.scroll_into_view_if_needed()
                scroll_before = page.evaluate("() => window.scrollY")
                assert scroll_before > 0
                page.evaluate(
                    """() => {
                      const original = window.scrollBy.bind(window)
                      window.scrollBy = (options) => {
                          const control = document.activeElement
                          const bounds = control?.getBoundingClientRect()
                          const masthead = document.querySelector('.masthead')
                          const visibleTop = (masthead?.getBoundingClientRect().bottom ?? 0) + 8
                          window.__compactReveal = {
                            requested: typeof options === 'object' ? options.top : null,
                            beforeTop: bounds?.top ?? null,
                            beforeBottom: bounds?.bottom ?? null,
                            visibleTop,
                            viewport: innerHeight,
                        }
                        original(options)
                      }
                    }"""
                )
                lower_choice.click()
                page.wait_for_function(_CURRENT_FIELD_CHANGED, arg=before)
                page.wait_for_function(
                    "() => document.activeElement?.matches("
                    "'.decision-canvas button:not(:disabled)')"
                )
                result = page.evaluate(
                    """() => {
                      const control = document.activeElement
                      if (!(control instanceof HTMLButtonElement) || control.disabled)
                        return null
                      const bounds = control.getBoundingClientRect()
                      const masthead = document.querySelector('.masthead')
                      const mastheadBottom = masthead?.getBoundingClientRect().bottom ?? 0
                      const hit = document.elementFromPoint(
                        Math.min(innerWidth - 1, Math.max(0, bounds.left + bounds.width / 2)),
                        Math.min(innerHeight - 1, Math.max(0, bounds.top + 1)),
                      )
                      return {
                        focused: control.matches('.decision-canvas button:not(:disabled)'),
                        top: bounds.top,
                        bottom: bounds.bottom,
                        mastheadBottom,
                        occluded: hit !== control && !control.contains(hit),
                        scrollY,
                        reveal: window.__compactReveal ?? null,
                      }
                    }"""
                )
                assert result is not None
                assert result["focused"]
                assert result["top"] >= result["mastheadBottom"] + 7
                assert result["bottom"] <= 321
                assert not result["occluded"]
                assert result["scrollY"] > 0
                reveal = result["reveal"]
                assert reveal is not None
                expected = (
                    reveal["beforeTop"] - reveal["visibleTop"]
                    if reveal["beforeTop"] < reveal["visibleTop"]
                    else max(0, reveal["beforeBottom"] - reveal["viewport"])
                )
                assert abs(reveal["requested"] - expected) <= 1
                assert [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ] == rail_order
            browser.close()


def test_z_compact_uncached_library_detail_keeps_visible_focus() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                held_selected_detail: list[Any] = []

                def hold_selected_detail(route) -> None:
                    if route.request.url.endswith(f"/api/v1/review-cases/{case_id}"):
                        held_selected_detail.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/review-cases/**", hold_selected_detail)
                _login(page, origin)
                page.locator(".library-work-row").first.click()
                page.get_by_text("Loading selected work…").wait_for()
                assert len(held_selected_detail) == 1
                pending_detail = page.locator(".workspace-detail")
                expect(pending_detail).to_be_focused()
                assert pending_detail.bounding_box() is not None
                held_selected_detail[0].continue_()
                page.get_by_role(
                    "button", name="Choose current value:", exact=False
                ).wait_for()
                expect(page.locator(".selected-work-header")).to_be_focused()
                page.unroute("**/api/v1/review-cases/**", hold_selected_detail)
            browser.close()


def test_owner_settings_installed_semantics_responsiveness_and_bounds() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        settings = OwnerSettingsDocument.model_validate(
            {
                "schema_version": "owner-settings-v1",
                "revision": 1,
                "connections": [
                    {
                        "name": "primary",
                        "endpoint": "https://library.invalid/" + "unbroken" * 120,
                    }
                ],
                "providers": [],
                "libraries": [
                    {
                        "name": f"library-{index:03d}",
                        "connection": "primary",
                        "library_id": f"synthetic-{index:03d}",
                        "media_kind": "ebook",
                    }
                    for index in range(128)
                ],
                "pending_restart": ["libraries"],
            }
        )
        errors: list[str] = []
        unavailable_responses: list[tuple[str, int]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.add_init_script(
                """
                window.__settingsLongTasks = [];
                new PerformanceObserver((list) => {
                  window.__settingsLongTasks.push(...list.getEntries().map((entry) => entry.duration));
                }).observe({entryTypes: ['longtask']});
                """
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    unavailable_responses.append((response.url, response.status))
                    if response.url.endswith("/api/v1/settings")
                    and response.status == 503
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    settings_document=settings,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                initial_settings = page.request.get(origin + "/api/v1/settings").json()
                held_initial_settings: list[Any] = []

                def hold_initial_settings(route) -> None:
                    if route.request.method == "GET":
                        held_initial_settings.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/settings", hold_initial_settings)
                page.goto(origin + "/settings?group=libraries")
                expect(
                    page.get_by_role("heading", name="Loading settings…")
                ).to_be_visible()
                assert len(held_initial_settings) == 1
                held_initial_settings[0].fulfill(status=200, json=initial_settings)
                expect(
                    page.get_by_role("heading", name="Libraries", exact=True)
                ).to_be_visible()
                page.unroute("**/api/v1/settings", hold_initial_settings)
                expect(
                    page.get_by_text("Restart required for saved changes")
                ).to_be_visible()
                assert page.locator("*").count() < 2_000

                for theme in ("light", "dark"):
                    requested = "Use dark mode" if theme == "dark" else "Use light mode"
                    toggle = page.get_by_role("button", name=requested)
                    if toggle.count():
                        toggle.click()
                    for width, height in (
                        (1440, 900),
                        (884, 900),
                        (390, 844),
                        (320, 800),
                    ):
                        page.set_viewport_size({"width": width, "height": height})
                        expect(
                            page.get_by_role("heading", name="Libraries", exact=True)
                        ).to_be_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                        if width <= 390:
                            expect(
                                page.locator("label.settings-mobile-group select")
                            ).to_be_visible()
                        else:
                            expect(page.get_by_label("Settings groups")).to_be_visible()

                page.set_viewport_size({"width": 320, "height": 800})
                page.evaluate("document.documentElement.style.fontSize = '200%'")
                expect(
                    page.get_by_role("heading", name="Libraries", exact=True)
                ).to_be_visible()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                expect(
                    page.locator("label.settings-mobile-group select")
                ).to_be_visible()
                page.evaluate("document.documentElement.style.fontSize = ''")
                compact_group = page.locator("label.settings-mobile-group select")
                compact_group.select_option("providers")
                expect(
                    page.get_by_text("No metadata providers configured")
                ).to_be_visible()
                compact_group.select_option("connection")
                expect(page.get_by_text("Configured · ····cret")).to_be_visible()
                page.get_by_role("button", name="Clear primary access key").click()
                expect(
                    page.get_by_role("button", name="Confirm clear primary access key")
                ).to_be_visible()
                page.get_by_role("button", name="Keep primary access key").click()
                page.get_by_role("button", name="Replace primary access key").click()
                expect(
                    page.get_by_label("Replacement primary access key")
                ).to_be_visible()

                compact_group.select_option("automation")
                timeout_input = page.get_by_role("spinbutton", name="Request timeout")
                timeout_input.fill("0")
                expect(page.get_by_role("alert")).to_contain_text(
                    "Request timeout must be"
                )
                expect(timeout_input).to_have_attribute("aria-invalid", "true")
                described_by = timeout_input.get_attribute("aria-describedby")
                assert described_by is not None
                assert "settings-limits-timeout_seconds-error" in described_by
                expect(
                    page.locator("#settings-limits-timeout_seconds-error")
                ).to_contain_text("Request timeout must be from 1 to 60")
                expect(timeout_input).to_be_focused()
                expect(
                    page.get_by_role("button", name="Save settings")
                ).to_be_disabled()
                timeout_input.fill("30")

                compact_group.select_option("connection")
                page.evaluate("window.__settingsLongTasks = []")
                feedback = page.evaluate(
                    """async () => {
                      const input = document.querySelector('#settings-connection-primary-endpoint');
                      const start = performance.now();
                      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                      setter.call(input, 'https://changed.invalid');
                      input.dispatchEvent(new Event('input', {bubbles: true}));
                      await new Promise(requestAnimationFrame);
                      return performance.now() - start;
                    }"""
                )
                assert feedback <= 100
                expect(page.get_by_text("Unsaved settings changes.")).to_be_visible()
                held_save: list[Any] = []

                def hold_save(route) -> None:
                    if route.request.method == "PATCH":
                        held_save.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/settings", hold_save)
                pending_feedback = page.evaluate(
                    """async () => {
                      const button = [...document.querySelectorAll('button')]
                        .find((item) => item.textContent === 'Save settings');
                      const start = performance.now();
                      button.click();
                      await new Promise(requestAnimationFrame);
                      return performance.now() - start;
                    }"""
                )
                assert pending_feedback <= 100
                expect(page.get_by_text("Saving settings…")).to_be_visible()
                assert len(held_save) == 1
                held_save[0].continue_()
                expect(
                    page.get_by_text(
                        "Saved. Server-authoritative settings are current."
                    )
                ).to_be_visible()
                page.unroute("**/api/v1/settings", hold_save)
                assert max(page.evaluate("window.__settingsLongTasks"), default=0) <= 50

                endpoint = page.get_by_role("textbox", name="Audiobookshelf endpoint")
                endpoint.fill("https://failed.invalid")
                assert not errors
                page.route(
                    "**/api/v1/settings",
                    lambda route: (
                        route.fulfill(
                            status=503,
                            json={
                                "schema_version": "review-error-v1",
                                "error": {"code": "settings_unavailable"},
                            },
                        )
                        if route.request.method == "PATCH"
                        else route.continue_()
                    ),
                )
                page.get_by_role("button", name="Save settings").click()
                recovered_status = page.locator(".settings-stale-recovery").filter(
                    has_text="Authoritative Settings reloaded"
                )
                expect(recovered_status).to_be_visible()
                expect(recovered_status).to_be_focused()
                expect(
                    page.get_by_role("button", name="Save settings")
                ).to_be_disabled()
                assert errors == [
                    "Failed to load resource: the server responded with a status of 503 "
                    "(Service Unavailable)"
                ]
                assert unavailable_responses == [(origin + "/api/v1/settings", 503)]
                errors.clear()
                unavailable_responses.clear()
                page.unroute("**/api/v1/settings")

                endpoint.fill("https://stale.invalid")
                page.route(
                    "**/api/v1/settings",
                    lambda route: (
                        route.fulfill(
                            status=409,
                            json={
                                "schema_version": "review-error-v1",
                                "error": {"code": "settings_revision_stale"},
                            },
                        )
                        if route.request.method == "PATCH"
                        else route.continue_()
                    ),
                )
                page.get_by_role("button", name="Save settings").click()
                stale_alert = page.get_by_text("Settings changed elsewhere.")
                expect(stale_alert).to_be_visible()
                expect(stale_alert).to_be_focused()
                assert errors == [
                    "Failed to load resource: the server responded with a status of 409 "
                    "(Conflict)"
                ]
                errors.clear()
                page.unroute("**/api/v1/settings")

                current = page.request.get(origin + "/api/v1/settings").json()
                current["authority"] = "file"
                current["managed_path"] = "/data/config.json"
                for field in current["fields"]:
                    field["managed_by_file"] = True
                    field["managed_path"] = "/data/config.json"
                page.route(
                    "**/api/v1/settings",
                    lambda route: route.fulfill(status=200, json=current),
                )
                page.reload()
                expect(page.get_by_text("Managed by /data/config.json")).to_be_visible()
                assert page.locator(".settings-sheet input").count() == 0
                assert page.get_by_role("button", name="Save settings").count() == 0
                page.unroute("**/api/v1/settings")
                assert not errors

                page.route(
                    "**/api/v1/settings",
                    lambda route: route.fulfill(
                        status=503,
                        json={
                            "schema_version": "review-error-v1",
                            "error": {"code": "settings_unavailable"},
                        },
                    ),
                )
                page.reload()
                expect(
                    page.get_by_role("heading", name="Settings are unavailable.")
                ).to_be_visible()
                expect(page.get_by_role("button", name="Try again")).to_be_visible()
                assert unavailable_responses == [(origin + "/api/v1/settings", 503)]
                assert len(errors) == 1
                assert errors[0] == (
                    "Failed to load resource: the server responded with a status of 503 "
                    "(Service Unavailable)"
                )
                errors.clear()
                page.unroute("**/api/v1/settings")
                page.get_by_role("button", name="Try again").click()
                expect(
                    page.get_by_role("heading", name="Settings", exact=True)
                ).to_be_visible()
                assert not errors
            browser.close()
