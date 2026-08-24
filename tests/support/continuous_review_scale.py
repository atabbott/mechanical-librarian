"""The installed-wheel continuous-review scale qualification driver.

`qualify_installed_pipeline` builds an installed wheel, serves a synthetic
Audiobookshelf over TLS, reconciles a fresh cohort, then verifies a
completed-state restart. Two tiers drive it at different sizes -- the
integration tier at a representative cohort, the slow tier at a thousand
works -- so it lives here rather than in either tier's module: a test module
never imports another test module's fixtures, because that makes one cohort
a dependency of another (`docs/development/testing-strategy.md`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from argon2 import PasswordHasher

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    _entry as catalogue_entry,
)
from mechanical_librarian.adapters.operational_evidence import (
    LocalTimingStore,
    SupportBundle,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.application.batch import BatchSelection, batch_fingerprint
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ReviewWebConfiguration,
)
from mechanical_librarian.application.operations import (
    StageTimer,
    support_preparation_incidents,
)
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    ReviewPipelineError,
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
)
from mechanical_librarian.bootstrap.composition import (
    _compose_cover_considerations,
    compose_continuous_guarded_application,
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
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.interfaces.web import (
    create_review_app,
    load_owner_password_verifier,
)
from tests.support.ports import ReservedPort
from tests.support.tls import synthetic_tls_identity

# How long the installed server may take to answer `/health/live`. The wait is
# already a readiness poll, so this only bounds it; a server that starts
# normally returns within milliseconds and pays none of it. The previous
# fifteen seconds was reached often enough to fail the fast tier at a measured
# one run in five on an unchanged commit (#730), so it was bounding a loaded
# machine rather than a broken one. Sixty seconds matches the reconciliation
# deadline this qualification already allows below, and costs nothing in the
# fast case. It does not delay a crash: the death branch returns as soon as
# the child exits, whatever this value is.
_REVIEW_SERVER_START_SECONDS = 60

# How many times the installed server is started on a port another process took
# from it, and how long the reservation waits for that process to give it back.
# Both are declared here rather than buried, because a retry that is neither
# bounded nor visible is how a genuine start-up regression gets absorbed
# (#760). Only a demonstrably stolen port retries -- see `_start_review_server`
# for the discrimination -- so these bound a race, never a defect.
_REVIEW_SERVER_PORT_ATTEMPTS = 3
_REVIEW_SERVER_PORT_WAIT_SECONDS = 10
_MEASUREMENT_SAMPLES = 20
_PHASE_BANDS = (
    ("early", 0.05, 0.45),
    ("peak", 0.45, 0.85),
    ("late", 0.85, 1.0),
)
_MINIMUM_INSTALLED_RECONCILIATION_WORKS_PER_SECOND = 5.0
_FOREGROUND_RESPONSE_BUDGET_MS = 2_000


def _validate_receipt_budgets(
    receipt: dict[str, object],
) -> dict[str, dict[str, float]]:
    phases = cast(dict[str, dict[str, dict[str, float | int]]], receipt["phases"])
    budgets: dict[str, dict[str, float]] = {}
    for phase in ("quiescent", "early", "peak", "late"):
        for route, values in phases[phase].items():
            assert values["n"] >= _MEASUREMENT_SAMPLES
            assert values["5xx_or_transport"] == 0
            if phase != "quiescent":
                budget = max(
                    float(phases["quiescent"][route]["p95_ms"]) * 2,
                    _FOREGROUND_RESPONSE_BUDGET_MS,
                )
                budgets.setdefault(phase, {})[route] = budget
                assert float(values["p95_ms"]) <= budget
    return budgets


class _RouteMeasurements:
    """Path-free HTTP timing aggregates for the installed contention control."""

    def __init__(self) -> None:
        self._values: dict[str, dict[str, list[float]]] = {}
        self._errors: dict[str, dict[str, int]] = {}

    def observe(
        self, phase: str, route: str, response: httpx.Response, elapsed: float
    ) -> None:
        self._values.setdefault(phase, {}).setdefault(route, []).append(elapsed * 1_000)
        if response.status_code >= 500:
            errors = self._errors.setdefault(phase, {})
            errors[route] = errors.get(route, 0) + 1

    def receipt(self) -> dict[str, dict[str, dict[str, float | int]]]:
        result: dict[str, dict[str, dict[str, float | int]]] = {}
        for phase, routes in self._values.items():
            result[phase] = {}
            for route, values in routes.items():
                ordered = sorted(values)
                percentile = ordered[round((len(ordered) - 1) * 0.95)]
                result[phase][route] = {
                    "n": len(ordered),
                    "p50_ms": round(ordered[len(ordered) // 2], 3),
                    "p95_ms": round(percentile, 3),
                    "max_ms": round(max(ordered), 3),
                    "5xx_or_transport": self._errors.get(phase, {}).get(route, 0),
                }
        return result


def _route_failure_diagnostic(
    phase: str, route: str, response: httpx.Response
) -> dict[str, object]:
    """Retain the bounded route/error identity, never a failing response body."""

    category = "transport"
    try:
        payload = response.json()
    except (UnicodeError, ValueError):
        payload = None
    if type(payload) is dict:
        error = payload.get("error")
        if type(error) is dict and type(error.get("code")) is str:
            candidate = error["code"]
            if re.fullmatch(r"[a-z_]{1,64}", candidate):
                category = candidate
            else:
                category = "unrecognized"
    return {
        "phase": phase,
        "route": route,
        "status": response.status_code,
        "category": category,
    }


def _sample_routes(
    client: httpx.Client, origin: str, phase: str, measurements: _RouteMeasurements
) -> None:
    route_sets = {
        "route_view_compute": (
            ("library_default", "/api/v1/library?limit=10"),
            ("library_lens", "/api/v1/library?limit=1&lens=all"),
            ("library_search", "/api/v1/library?limit=10&q=synthetic"),
            ("attention", "/api/v1/attention?limit=10"),
            ("activity_default", "/api/v1/activity?page=1&page_size=25"),
            (
                "activity_filter",
                "/api/v1/activity?event_type=all&range_days=30&page=1&page_size=25",
            ),
            ("operations", "/api/v1/operations/status"),
        ),
        "observation_db_contention": (("preparation", "/api/v1/review-preparation"),),
    }
    for _ in range(_MEASUREMENT_SAMPLES):
        with httpx.Client(verify=False, trust_env=False) as disposable:
            started = time.monotonic()
            response = disposable.post(
                origin + "/api/v1/session/login",
                headers={"Origin": origin},
                json={"password": "synthetic password"},
            )
            measurements.observe(
                phase, "foreground_session_gate", response, time.monotonic() - started
            )
            assert response.status_code == 200
        for category, routes in route_sets.items():
            for route, path in routes:
                started = time.monotonic()
                try:
                    response = client.get(origin + path)
                except httpx.TransportError:
                    response = httpx.Response(599)
                measurements.observe(
                    phase, f"{category}:{route}", response, time.monotonic() - started
                )
                assert response.status_code < 500, _route_failure_diagnostic(
                    phase, route, response
                )


class _SharedRepository:
    """One installed single-process repository handle for the bounded qualifier."""

    def __init__(self, repository) -> None:
        self.repository = repository

    def __getattr__(self, name: str):
        return getattr(self.repository, name)

    def close(self) -> None:
        pass


class _AudiobookshelfFixture:
    def __init__(self, counts: dict[str, int]) -> None:
        self.summaries: dict[str, list[dict[str, object]]] = {}
        self.details: dict[str, dict[str, object]] = {}
        self.discovery_started = threading.Event()
        self.discovery_release = threading.Event()
        self.discovery_release.set()
        self.detail_started = threading.Event()
        self.detail_release = threading.Event()
        self.detail_release.set()
        self._detail_hold_armed = False
        self._detail_hold_lock = threading.Lock()
        self.requests: list[tuple[str, str]] = []
        self._requests_lock = threading.Lock()
        offset = 0
        for library, count in counts.items():
            values: list[dict[str, object]] = []
            for index in range(count):
                item = f"{library}-{index:04d}"
                summary, detail = self._values(library, item, offset + index)
                values.append(summary)
                self.details[item] = detail
            self.summaries[library] = values
            offset += count
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                with fixture._requests_lock:
                    fixture.requests.append((self.command, urlsplit(self.path).path))
                if self.headers.get("Authorization") != "Bearer synthetic-token":
                    self.send_response(403)
                    self.end_headers()
                    return
                parsed = urlsplit(self.path)
                route = parsed.path
                if route == "/api/libraries":
                    value: object = {
                        "libraries": [
                            {"id": name, "mediaType": "book"}
                            for name in fixture.summaries
                        ]
                    }
                elif route.startswith("/api/libraries/") and route.endswith("/items"):
                    fixture.discovery_started.set()
                    if not fixture.discovery_release.wait(timeout=15):
                        self.send_response(503)
                        self.end_headers()
                        return
                    library = route.split("/")[3]
                    rows = fixture.summaries[library]
                    page = int(parse_qs(parsed.query).get("page", ["0"])[0])
                    value = {
                        "total": len(rows),
                        "results": rows[page * 100 : (page + 1) * 100],
                    }
                elif route.startswith("/api/items/"):
                    with fixture._detail_hold_lock:
                        hold_detail = fixture._detail_hold_armed
                        fixture._detail_hold_armed = False
                    if hold_detail:
                        fixture.detail_started.set()
                        if not fixture.detail_release.wait(timeout=15):
                            self.send_response(503)
                            self.end_headers()
                            return
                    time.sleep(0.001)
                    value = fixture.details[route.rsplit("/", 1)[-1]]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(value, separators=(",", ":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @staticmethod
    def _values(
        library: str, item: str, index: int
    ) -> tuple[dict[str, object], dict[str, object]]:
        summary: dict[str, object] = {
            "id": item,
            "libraryId": library,
            "updatedAt": index + 1,
        }
        detail = {
            **summary,
            "isInvalid": False,
            "isMissing": False,
            "mediaType": "book",
            "media": {
                "metadata": {"title": item, "authors": [{"name": "Synthetic Author"}]},
                "ebookFile": {
                    "ebookFormat": "epub",
                    "ino": f"inode-{index}",
                    "metadata": {
                        "relPath": f"{item}.epub",
                        "size": 1,
                        "mtimeMs": index + 1,
                    },
                },
            },
        }
        return summary, detail

    def add_new_fiction(self) -> tuple[dict[str, object], dict[str, object]]:
        summary, detail = self._values("fiction", "fiction-new", 999)
        self.summaries["fiction"].append(summary)
        self.details["fiction-new"] = detail
        return summary, detail

    def hold_discovery(self) -> None:
        self.discovery_started.clear()
        self.discovery_release.clear()

    def release_discovery(self) -> None:
        self.discovery_release.set()

    def hold_next_detail(self) -> None:
        self.detail_started.clear()
        self.detail_release.clear()
        with self._detail_hold_lock:
            self._detail_hold_armed = True

    def release_next_detail(self) -> None:
        self.detail_release.set()

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def _assert_phase_fraction(phase: str, fraction: float) -> None:
    """Keep each held-detail sample in its distinct resumed-scan band."""

    lower, upper = next(
        (lower, upper) for name, lower, upper in _PHASE_BANDS if name == phase
    )
    assert lower <= fraction < upper


def _assert_distinct_phase_fractions(actual_fractions: dict[str, float]) -> None:
    """Reject receipt evidence that collapsed two foreground phases together."""

    assert tuple(actual_fractions) == tuple(
        name for name, _lower, _upper in _PHASE_BANDS
    )
    for phase, fraction in actual_fractions.items():
        _assert_phase_fraction(phase, fraction)


def _installed_timeout_diagnostics(
    elapsed: float,
    sampled_phases: set[str],
    libraries: list[dict[str, object]],
) -> str:
    """Format the bounded aggregate state retained by an installed timeout."""

    return repr(
        {
            "elapsed_seconds": round(elapsed, 3),
            "sampled_phases": sorted(sampled_phases),
            "libraries": [
                {
                    "library": library["library"],
                    "state": library["state"],
                    "total": library["total"],
                    "checked": library["checked"],
                    "blocked": library["blocked"],
                }
                for library in libraries
            ],
        }
    )


def _start_deterministic_installed_server(
    *,
    database: Path,
    configuration: ApplicationConfiguration,
    artifacts: Path,
    fixture: _AudiobookshelfFixture,
    entries: dict[str, list[CatalogueEntry]],
    producers: dict[str, str],
    owner_hash: Path,
    certificate: Path,
    private_key: Path,
    reservation: ReservedPort,
    origin: str,
    admit_case: Callable[[str], None],
) -> tuple[uvicorn.Server, threading.Thread, SQLiteProviderRecoveryStore]:
    """Run installed-wheel HTTP over the production persisted pipeline boundary.

    The callbacks stand in only for expensive target/media/provider observation.
    They retain the production action queue, report publication, proposal,
    admission and projection transactions against the server's SQLite store.
    """

    observation = open_repository(database, create=True, cross_thread=True)
    reports: dict[str, MetadataReport] = {}
    proposals: dict[str, object] = {}
    private_root = database.parent / "private"
    private_root.mkdir(mode=0o700)
    review = ReviewWebConfiguration(
        canonical_origin=origin,
        owner_password_hash_file=str(owner_hash),
        tls_certificate_file=str(certificate),
        tls_private_key_file=str(private_key),
    )
    timing_store = LocalTimingStore(
        private_root / "stage-timings.json",
        enabled=review.diagnostic_timings_enabled,
    )
    timing = StageTimer(timing_store)
    recovery = SQLiteProviderRecoveryStore.open(
        private_root / "provider-recovery.sqlite"
    )

    def workers():
        return open_repository(database, create=False, authority=observation.authority)

    def discover(_: str, library: str):
        fixture.discovery_started.set()
        if not fixture.discovery_release.wait(timeout=15):
            raise ReviewPipelineError("transient")
        offset = {"fiction": 1, "history": 2, "science": 3}[library]
        return offset, f"{offset:064x}", tuple(entries[library])

    def extract(repository, action: PipelineAction, claim_next: bool):
        assert action.item_id is not None
        with fixture._detail_hold_lock:
            held = fixture._detail_hold_armed
            fixture._detail_hold_armed = False
        if held:
            fixture.detail_started.set()
            if not fixture.detail_release.wait(timeout=15):
                raise ReviewPipelineError("transient")
        index = int(action.file_identity or "0", 16)
        candidate = (
            "Exact evaluator candidate"
            if action.item_id == "fiction-0000"
            else "Synthetic candidate " + action.item_id
        )
        report = MetadataReport.create(
            target=TargetFacts(
                action.connection,
                action.library,
                action.item_id,
                default_target_fields(action.item_id),
            ),
            file=FileFacts(0, action.file_identity or "", f"{index + 10_001:064x}"),
            local_claims=(
                LocalClaim.create(
                    kind=ClaimKind.TITLE,
                    observed=candidate,
                    canonical=candidate,
                    excerpt=candidate,
                    applicability="not_applicable",
                    checksum="not_applicable",
                    applicability_basis="not_applicable",
                    scope=ClaimScope.WORK,
                ),
            ),
            local_reasons=("opening_matter_title",),
            providers=(),
        )
        reports[report.report_id] = report
        repository.publish(
            report,
            lambda: True,
            None,
            lambda value, published: value.complete_review_extraction_publication(
                action, published.report_id, int(time.time()), 0, None, claim_next
            ),
        )
        return report.report_id, True

    def propose(repository, report_id: str) -> str:
        proposal = evaluate_report(reports[report_id])
        proposals[proposal.proposal_set_id] = proposal
        repository.publish_proposal(proposal)
        return proposal.proposal_set_id

    def admit(repository, proposal_id: str):
        result = json.loads(repository.admit_review_case(proposals[proposal_id]))
        if review_case_id := result.get("review_case_id"):
            admit_case(str(review_case_id))
        return result["status"], result.get("review_case_id")

    pipeline = ReviewPreparationPipeline(
        repository_factory=workers,
        observation_repository_factory=lambda: observation,
        libraries=tuple(
            ("primary", library, library, producers[library]) for library in entries
        ),
        discover=discover,
        extract=extract,
        providers=lambda *_: (),
        propose=propose,
        admit=admit,
        project=lambda *_: True,
        timing=timing,
    )
    supervisor = ReviewPreparationSupervisor(pipeline, polling_interval_seconds=None)
    support_bundle = SupportBundle(
        (
            (
                "runtime",
                lambda: {
                    "deployment": "single_container",
                    "schema_state": "exact_current",
                    "timing_records": len(timing_store.list()),
                },
            ),
            (
                "operational_incidents",
                lambda: support_preparation_incidents(
                    observation.operational_snapshot()
                ),
            ),
        )
    )
    application = compose_review_application(
        state_owner=observation,
        session_store=observation,
        case_store=observation,
        decision_store=observation,
        library_store=observation,
        policy_store=observation,
        verify_password=load_owner_password_verifier(owner_hash),
        clock=lambda: int(time.time()),
        session_lifetime_seconds=review.session_lifetime_seconds,
        graceful_shutdown_seconds=review.graceful_shutdown_seconds,
        close_state_owner=True,
        preparation=supervisor,
        policy_libraries=tuple(
            (library.name, cast("dict[str, object] | None", library.policy_override))
            for library in configuration.libraries
        ),
        operational_timings=timing_store,
        operational_support=support_bundle,
        operational_timer=timing,
        field_application=compose_continuous_guarded_application(
            configuration,
            observation,
            database,
            artifacts,
            supervisor,
            private_root,
            recovery,
        ),
        cover_considerations=_compose_cover_considerations(
            observation, artifacts, review
        ),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_review_app(application, review),
            host="127.0.0.1",
            port=reservation.number,
            log_level="error",
            access_log=False,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(private_key),
        )
    )
    reservation.release()
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + _REVIEW_SERVER_START_SECONDS
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started is True
    return server, thread, recovery


class _InstalledPhaseObserver(Protocol):
    """A persistent owner-facing probe that must close before server shutdown."""

    def observe(self, phase: str, origin: str) -> float | None: ...

    def close(self) -> None: ...


class _InstalledPreparationRun:
    """Own one installed preparation lifecycle and its bounded public observation."""

    def __init__(
        self,
        *,
        database: Path,
        configuration: ApplicationConfiguration,
        artifacts: Path,
        fixture: _AudiobookshelfFixture,
        entries: dict[str, list[CatalogueEntry]],
        producers: dict[str, str],
        owner_hash: Path,
        certificate: Path,
        private_key: Path,
        reservation: ReservedPort,
        origin: str,
        expected_total: int,
        phase_observer: _InstalledPhaseObserver | None,
    ) -> None:
        self._database = database
        self._configuration = configuration
        self._artifacts = artifacts
        self._fixture = fixture
        self._entries = entries
        self._producers = producers
        self._owner_hash = owner_hash
        self._certificate = certificate
        self._private_key = private_key
        self._reservation = reservation
        self._origin = origin
        self._expected_total = expected_total
        self._phase_observer = phase_observer
        self._case_id: str | None = None
        self._case_id_lock = threading.Lock()
        self._case_admitted = threading.Event()
        self._server: uvicorn.Server | None = None
        self._server_thread: threading.Thread | None = None
        self._recovery: SQLiteProviderRecoveryStore | None = None

    def __enter__(self) -> _InstalledPreparationRun:
        self._fixture.hold_discovery()
        self._server, self._server_thread, self._recovery = (
            _start_deterministic_installed_server(
                database=self._database,
                configuration=self._configuration,
                artifacts=self._artifacts,
                fixture=self._fixture,
                entries=self._entries,
                producers=self._producers,
                owner_hash=self._owner_hash,
                certificate=self._certificate,
                private_key=self._private_key,
                reservation=self._reservation,
                origin=self._origin,
                admit_case=self._capture_fixture_case,
            )
        )
        with httpx.Client(verify=False, trust_env=False) as client:
            login = client.post(
                self._origin + "/api/v1/session/login",
                headers={"Origin": self._origin},
                json={"password": "synthetic password"},
            )
            assert login.status_code == 200, login.text
            workspace = client.get(self._origin + "/library")
            assert workspace.status_code == 200, workspace.text
            build_identity = re.search(
                r'data-build-identity="([0-9a-f]{64})"', workspace.text
            )
            browser_csrf = re.search(
                r'data-csrf-token="([A-Za-z0-9_-]{32,128})"', workspace.text
            )
            assert build_identity is not None
            assert browser_csrf is not None
            action_headers = {
                "Origin": self._origin,
                "x-csrf-token": browser_csrf.group(1),
                "x-ml-build-identity": build_identity.group(1),
            }
            for library in ("fiction", "history", "science"):
                requested = client.post(
                    self._origin + f"/api/v1/review-preparation/primary/{library}/scan",
                    headers=action_headers,
                )
                assert requested.status_code == 202, requested.text
            assert self._fixture.discovery_started.wait(timeout=15), (
                "first-start reconciliation was not observed"
            )
            first_status = self.status(client)
        assert first_status["active"] is True
        assert {item["library"] for item in first_status["libraries"]} == {
            "fiction",
            "history",
            "science",
        }
        assert all(
            item["checked"] == 0 and item["state"] in {"queued", "discovering"}
            for item in first_status["libraries"]
        )
        assert any(item["state"] == "discovering" for item in first_status["libraries"])
        return self

    def _capture_fixture_case(self, case_id: str) -> None:
        with self._case_id_lock:
            if self._case_id is None:
                self._case_id = case_id
        self._case_admitted.set()

    def release_discovery(self) -> None:
        self._fixture.release_discovery()

    def status(self, client: httpx.Client) -> dict[str, object]:
        response = client.get(self._origin + "/api/v1/review-preparation")
        assert response.status_code == 200, response.text
        return cast(dict[str, object], response.json())

    def wait_for_fixture_case(self) -> str:
        assert self._case_admitted.wait(timeout=15), (
            "fixture review case was not admitted"
        )
        with self._case_id_lock:
            assert self._case_id is not None
            return self._case_id

    def wait_until_terminal(self) -> dict[str, object]:
        """Wait through the authenticated progress projection without profiling it."""

        assert self._server_thread is not None
        with httpx.Client(verify=False, trust_env=False) as client:
            login = client.post(
                self._origin + "/api/v1/session/login",
                headers={"Origin": self._origin},
                json={"password": "synthetic password"},
            )
            assert login.status_code == 200, login.text
            deadline = time.monotonic() + _REVIEW_SERVER_START_SECONDS
            while True:
                if not self._server_thread.is_alive():
                    pytest.fail("installed review server exited during terminal wait")
                response = client.get(self._origin + "/api/v1/review-preparation")
                if response.status_code == 503:
                    if time.monotonic() >= deadline:
                        pytest.fail("installed preparation status remained unavailable")
                    time.sleep(0.02)
                    continue
                assert response.status_code == 200, response.text
                projection = cast(dict[str, object], response.json())
                libraries = cast(list[dict[str, object]], projection["libraries"])
                if (
                    not projection["active"]
                    and len(libraries) == 3
                    and all(item["state"] == "completed" for item in libraries)
                ):
                    assert (
                        sum(cast(int, item["total"]) for item in libraries)
                        == self._expected_total
                    )
                    assert (
                        sum(cast(int, item["checked"]) for item in libraries)
                        == self._expected_total
                    )
                    assert sum(cast(int, item["blocked"]) for item in libraries) == 0
                    return projection
                if time.monotonic() >= deadline:
                    pytest.fail("installed preparation did not complete")
                time.sleep(0.02)

    def _wait_until_profiled_terminal(
        self,
    ) -> tuple[
        dict[str, object],
        _RouteMeasurements,
        dict[str, float],
        int,
        float,
        float | None,
    ]:
        assert self._server_thread is not None
        with httpx.Client(verify=False, trust_env=False) as client:
            login = client.post(
                self._origin + "/api/v1/session/login",
                headers={"Origin": self._origin},
                json={"password": "synthetic password"},
            )
            assert login.status_code == 200, login.text
            assert self._fixture.discovery_release.is_set() is False
            started = time.monotonic()
            self.release_discovery()
            sampled_phases: set[str] = set()
            actual_fractions: dict[str, float] = {}
            active_observed = False
            decision_response_ms: float | None = None
            peak_rss_kib = 0
            measurements = _RouteMeasurements()
            deadline = started + (
                self._expected_total
                / _MINIMUM_INSTALLED_RECONCILIATION_WORKS_PER_SECOND
            )
            while True:
                if not self._server_thread.is_alive():
                    pytest.fail("installed review server exited during reconciliation")
                peak_rss_kib = max(peak_rss_kib, _rss_kib(os.getpid()))
                response = client.get(self._origin + "/api/v1/review-preparation")
                if response.status_code == 503:
                    if time.monotonic() >= deadline:
                        with closing(sqlite3.connect(self._database)) as connection:
                            observed = connection.execute(
                                "SELECT library_name,state,total,checked,blocked "
                                "FROM review_preparation_scans ORDER BY library_name"
                            ).fetchall()
                        pytest.fail(
                            "installed reconciliation status remained unavailable: "
                            + _installed_timeout_diagnostics(
                                time.monotonic() - started,
                                sampled_phases,
                                [
                                    {
                                        "library": library,
                                        "state": state,
                                        "total": total,
                                        "checked": checked,
                                        "blocked": blocked,
                                    }
                                    for library, state, total, checked, blocked in observed
                                ],
                            )
                        )
                    time.sleep(0.02)
                    continue
                assert response.status_code == 200, response.text
                projection = cast(dict[str, object], response.json())
                libraries = cast(list[dict[str, object]], projection["libraries"])
                active_observed = active_observed or bool(projection["active"])
                total = sum(cast(int, item["total"]) for item in libraries)
                assert total <= self._expected_total
                fraction = (
                    sum(cast(int, item["checked"]) for item in libraries)
                    / self._expected_total
                )
                phase = next(
                    (
                        name
                        for name, threshold, _upper in _PHASE_BANDS
                        if name not in sampled_phases and fraction >= threshold
                    ),
                    None,
                )
                if phase is not None:
                    _assert_phase_fraction(phase, fraction)
                    self._fixture.hold_next_detail()
                    try:
                        assert self._fixture.detail_started.wait(15), (
                            f"{phase} detail hold was not reached"
                        )
                        active = self.status(client)
                        assert active["active"] is True
                        actual_fractions[phase] = fraction
                        _sample_routes(client, self._origin, phase, measurements)
                        if self._phase_observer is not None:
                            observed_decision = self._phase_observer.observe(
                                phase, self._origin
                            )
                            if phase == "peak":
                                assert observed_decision is not None
                                decision_response_ms = observed_decision
                    finally:
                        self._fixture.release_next_detail()
                    sampled_phases.add(phase)
                if (
                    not projection["active"]
                    and len(libraries) == 3
                    and all(item["state"] == "completed" for item in libraries)
                ):
                    break
                if time.monotonic() >= deadline:
                    pytest.fail(
                        "installed reconciliation did not complete: "
                        + _installed_timeout_diagnostics(
                            time.monotonic() - started, sampled_phases, libraries
                        )
                    )
                time.sleep(0.02)
            assert sampled_phases == {"early", "peak", "late"}
            _assert_distinct_phase_fractions(actual_fractions)
            assert active_observed is True
            if self._phase_observer is not None:
                assert decision_response_ms is not None
                assert decision_response_ms <= _FOREGROUND_RESPONSE_BUDGET_MS
            _sample_routes(client, self._origin, "quiescent", measurements)
            return (
                projection,
                measurements,
                actual_fractions,
                peak_rss_kib,
                time.monotonic() - started,
                decision_response_ms,
            )

    def close(self) -> None:
        if self._phase_observer is not None:
            self._phase_observer.close()
        self._fixture.release_discovery()
        if self._server is not None and self._server_thread is not None:
            self._server.should_exit = True
            self._server_thread.join(timeout=30)
            if self._server_thread.is_alive():
                self._server.force_exit = True
                self._server_thread.join(timeout=10)
            assert self._server_thread.is_alive() is False
        if self._recovery is not None:
            self._recovery.close()


@contextmanager
def _running_installed_preparation(
    *,
    database: Path,
    configuration: ApplicationConfiguration,
    artifacts: Path,
    fixture: _AudiobookshelfFixture,
    entries: dict[str, list[CatalogueEntry]],
    producers: dict[str, str],
    owner_hash: Path,
    certificate: Path,
    private_key: Path,
    reservation: ReservedPort,
    origin: str,
    expected_total: int,
    phase_observer: _InstalledPhaseObserver | None,
) -> Iterator[_InstalledPreparationRun]:
    """Close a phase observer before the installed server drains its lifecycle."""

    run = _InstalledPreparationRun(
        database=database,
        configuration=configuration,
        artifacts=artifacts,
        fixture=fixture,
        entries=entries,
        producers=producers,
        owner_hash=owner_hash,
        certificate=certificate,
        private_key=private_key,
        reservation=reservation,
        origin=origin,
        expected_total=expected_total,
        phase_observer=phase_observer,
    )
    try:
        yield run.__enter__()
    finally:
        run.close()


def qualify_installed_pipeline(
    counts: dict[str, int],
    *,
    expected_total: int,
    memory_ceiling_kib: int | None = None,
    receipt_path: Path | None = None,
    phase_observer: _InstalledPhaseObserver | None = None,
) -> None:
    # `closing` is entered first so it exits last, which is the order
    # `addCleanup(fixture.close)` gave the fixture under `TestCase`: the
    # working directory is removed and only then is the fixture closed.
    fixture = _AudiobookshelfFixture(counts)
    fixture.start()
    with (
        closing(fixture),
        tempfile.TemporaryDirectory() as directory,
        # The port is held from here rather than picked and dropped: the
        # window between allocation and the installed server's own `bind()`
        # spans writing `config.json`, the entire in-process pipeline over
        # the whole cohort, `Popen`, and the child's interpreter start-up,
        # which at the slow tier's two thousand works is minutes wide. That is
        # far wider than any other caller of `ReservedPort`, and it is why
        # this one alone cannot stop at holding: the child binds a number it
        # is handed on its command line, and there is no channel for giving
        # it a descriptor instead, so its start-up stays a window that
        # `_start_review_server` closes by detection rather than by hope.
        closing(ReservedPort()) as review_port,
    ):
        root = Path(directory)
        secret = root / "target.secret"
        secret.write_text("synthetic-token", encoding="utf-8")
        secret.chmod(0o600)
        media = root / "media"
        media.mkdir(mode=0o700)
        artifacts = root / "artifacts"
        artifacts.mkdir(mode=0o700)
        owner_hash = root / "owner.hash"
        owner_hash.write_text(
            PasswordHasher().hash("synthetic password"), encoding="utf-8"
        )
        owner_hash.chmod(0o600)
        certificate, private_key = synthetic_tls_identity(root)
        origin = f"https://127.0.0.1:{review_port.number}"
        config_path = root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "config-v6",
                    "provider_recovery_generation": 1,
                    "connections": [
                        {
                            "name": "primary",
                            "adapter": "audiobookshelf",
                            "endpoint": f"http://127.0.0.1:{fixture.server.server_port}",
                            "secret_file": str(secret),
                        }
                    ],
                    "libraries": [
                        {
                            "name": name,
                            "connection": "primary",
                            "library_id": name,
                            "mount": "books",
                            "provider_connections": [],
                        }
                        for name in fixture.summaries
                    ],
                    "mounts": [
                        {
                            "name": "books",
                            "container_path": str(media),
                            "service_path": "/library",
                            "read_only": True,
                        }
                    ],
                    "providers": [],
                    "review": {
                        "canonical_origin": origin,
                        "owner_password_hash_file": str(owner_hash),
                        "tls_certificate_file": str(certificate),
                        "tls_private_key_file": str(private_key),
                        "library_polling_interval_seconds": 60,
                    },
                }
            ),
            encoding="utf-8",
        )
        configuration = load_configuration_file(config_path)
        mount = configuration.mounts[0]
        libraries = {value.name: value for value in configuration.libraries}
        entries = {
            name: [
                catalogue_entry(
                    "primary",
                    libraries[name],
                    mount,
                    summary,
                    fixture.details[str(summary["id"])],
                )
                for summary in summaries
            ]
            for name, summaries in fixture.summaries.items()
        }
        producers = {
            name: batch_fingerprint(
                configuration,
                (
                    BatchSelection(
                        "primary", name, name, "producer", None, "producer", False
                    ),
                ),
            )
            for name in entries
        }
        direct_database = root / "direct-control.sqlite3"
        stored_repository = open_repository(direct_database)
        repository = _SharedRepository(stored_repository)
        clock = [100]
        reports: dict[str, MetadataReport] = {}
        proposals = {}
        report_items: dict[str, str] = {}
        provider_attempts: dict[str, int] = {}
        review_case_ids: list[str] = []
        admission_results: dict[str, tuple[str, str | None]] = {}
        extracted: list[str] = []
        transient_item = "history-0000"
        credential_item = "science-0000"
        credentials_valid = [False]
        processing_started = time.monotonic()
        processing_peak_rss_kib = [0]

        def run_next(value: ReviewPreparationPipeline) -> bool:
            advanced = value.run_next()
            if memory_ceiling_kib is not None:
                processing_peak_rss_kib[0] = max(
                    processing_peak_rss_kib[0], _rss_kib(os.getpid())
                )
            return advanced

        def discover(_: str, library: str):
            offset = {"fiction": 1, "history": 2, "science": 3}[library]
            return offset, f"{offset:064x}", tuple(entries[library])

        def extract(_, action: PipelineAction, claim_next: bool):
            assert action.item_id is not None
            extracted.append(action.item_id)
            index = int(action.file_identity or "0", 16)
            local_claims = (
                (
                    LocalClaim.create(
                        kind=ClaimKind.TITLE,
                        observed="Exact evaluator candidate",
                        canonical="Exact evaluator candidate",
                        excerpt="Exact evaluator candidate",
                        applicability="not_applicable",
                        checksum="not_applicable",
                        applicability_basis="not_applicable",
                        scope=ClaimScope.WORK,
                    ),
                )
                if action.item_id == "fiction-0000"
                else ()
            )
            report = MetadataReport.create(
                target=TargetFacts(
                    action.connection,
                    action.library,
                    action.item_id,
                    default_target_fields(action.item_id),
                ),
                file=FileFacts(
                    0,
                    action.file_identity or "",
                    f"{index + 10_001:064x}",
                ),
                local_claims=local_claims,
                local_reasons=(
                    ("opening_matter_title",)
                    if local_claims
                    else ("no_opening_claims",)
                ),
                providers=(),
            )
            reports[report.report_id] = report
            report_items[report.report_id] = action.item_id
            attempt = provider_attempts.get(action.item_id, 0)
            provider_attempts[action.item_id] = attempt + 1
            transient = action.item_id == transient_item and attempt == 0
            configuration_failure = (
                action.item_id == credential_item and not credentials_valid[0]
            )
            delay = 7 if transient else 0
            repository.publish(
                report,
                lambda: True,
                None,
                lambda value, published: value.complete_review_extraction_publication(
                    action,
                    published.report_id,
                    clock[0],
                    delay,
                    (
                        "transient"
                        if transient
                        else "configuration"
                        if configuration_failure
                        else None
                    ),
                    claim_next,
                ),
            )
            return report.report_id, True

        def providers(_, report_id: str):
            raise AssertionError("provider disposition belongs to publication")

        def propose(_, report_id: str) -> str:
            proposal = evaluate_report(reports[report_id])
            proposals[proposal.proposal_set_id] = proposal
            repository.publish_proposal(proposal)
            return proposal.proposal_set_id

        def admit(_, proposal_id: str):
            result = json.loads(repository.admit_review_case(proposals[proposal_id]))
            item_id = report_items[proposals[proposal_id].report_id]
            if review_case_id := result.get("review_case_id"):
                review_case_ids.append(str(review_case_id))
            admission = result["status"], result.get("review_case_id")
            admission_results[item_id] = admission
            return admission

        def pipeline() -> ReviewPreparationPipeline:
            return ReviewPreparationPipeline(
                repository_factory=lambda: repository,
                libraries=tuple(
                    ("primary", name, name, producers[name]) for name in entries
                ),
                discover=discover,
                extract=extract,
                providers=providers,
                propose=propose,
                admit=admit,
                project=lambda *_: True,
                clock=lambda: clock[0],
            )

        first = pipeline()
        for _, library, _ in first.libraries:
            first.request("primary", library)
        while run_next(first):
            pass
        circuit = repository._connection.execute(
            "SELECT circuit_open_until FROM review_preparation_control"
        ).fetchone()[0]
        retry_at = repository._connection.execute(
            "SELECT retry_not_before FROM review_preparation_work WHERE item_id=?",
            (transient_item,),
        ).fetchone()[0]
        assert circuit == 0
        assert retry_at > clock[0]

        restarted = pipeline()
        restarted.recover()
        assert not (run_next(restarted))
        clock[0] = retry_at
        while True:
            if (
                cast(PipelineProgress, restarted.progress("primary", "fiction")).state
                == "completed"
            ):
                break
            advanced = run_next(restarted)
            if not advanced:
                assert (
                    cast(
                        PipelineProgress,
                        restarted.progress("primary", "fiction"),
                    ).state
                    == "completed"
                )

        before_new = len(extracted)
        initial_extracted = tuple(extracted)
        for library in counts:
            first_library_position = next(
                index
                for index, item_id in enumerate(initial_extracted)
                if item_id.startswith(library + "-")
            )
            assert (first_library_position + 1) <= 6
        new_summary, new_detail = fixture.add_new_fiction()
        entries["fiction"].append(
            catalogue_entry(
                "primary",
                libraries["fiction"],
                mount,
                new_summary,
                new_detail,
            )
        )
        restarted.request("primary", "fiction")
        for _ in range(40):
            assert run_next(restarted)
            if "fiction-new" in extracted[before_new:]:
                break
        assert "fiction-new" in extracted[before_new:]
        assert (extracted[before_new:].index("fiction-new") + 1) <= 4

        while run_next(restarted):
            pass
        science = restarted.progress("primary", "science")
        assert science is not None
        assert science.state == "completed_with_failures"
        credentials_valid[0] = True
        restarted.request("primary", "science")
        while run_next(restarted):
            pass

        processing_elapsed = time.monotonic() - processing_started
        # This measures the real report/proposal/admission workload, not the
        # later no-change console restart.
        assert (expected_total / processing_elapsed) >= 5.0
        if memory_ceiling_kib is not None:
            assert processing_peak_rss_kib[0] < memory_ceiling_kib

        final = [
            restarted.progress("primary", library)
            for library in ("fiction", "history", "science")
        ]
        assert all(value is not None for value in final)
        assert sum(value.total for value in final if value) == expected_total
        assert sum(value.checked for value in final if value) == expected_total
        assert sum(value.blocked for value in final if value) == 0
        assert all(value.state == "completed" for value in final if value)
        discrepancy_admission = admission_results["fiction-0000"]
        assert discrepancy_admission[0] == "admitted"
        assert discrepancy_admission[1] is not None
        assert all(
            status == "no_case"
            for item_id, (status, _case_id) in admission_results.items()
            if item_id != "fiction-0000"
        )
        assert provider_attempts[transient_item] == 2
        assert provider_attempts[credential_item] == 2
        stored_repository.close()

        # The installed child starts from a fresh state. The direct pipeline
        # above remains a control for fairness and recovery only; carrying its
        # scan rows into this child would turn the installed qualifier into a
        # no-change restart rather than a real 2,000-work reconciliation.
        database = root / "installed.sqlite3"
        catalogue = root / "installed-catalogue.sqlite3"
        command = shutil.which("mechanical-librarian")
        assert command is not None
        arguments = [
            str(command),
            "review-serve",
            "--config",
            str(config_path),
            "--database",
            str(database),
            "--catalogue",
            str(catalogue),
            "--artifacts",
            str(artifacts),
            "--host",
            "127.0.0.1",
            "--port",
            str(review_port.number),
        ]
        with _running_installed_preparation(
            database=database,
            configuration=configuration,
            artifacts=artifacts,
            fixture=fixture,
            entries=entries,
            producers=producers,
            owner_hash=owner_hash,
            certificate=certificate,
            private_key=private_key,
            reservation=review_port,
            origin=origin,
            expected_total=expected_total,
            phase_observer=phase_observer,
        ) as installed:
            (
                projection,
                measurements,
                actual_fractions,
                peak_rss_kib,
                installed_reconciliation_elapsed,
                decision_response_ms,
            ) = installed._wait_until_profiled_terminal()
            installed.wait_for_fixture_case()
            terminal_libraries = cast(list[dict[str, object]], projection["libraries"])
            assert (
                sum(cast(int, item["total"]) for item in terminal_libraries)
                == expected_total
            )
            assert (
                sum(cast(int, item["checked"]) for item in terminal_libraries)
                == expected_total
            )
            assert sum(cast(int, item["blocked"]) for item in terminal_libraries) == 0
            assert peak_rss_kib < 524_288

        # An unclean stop is reported as itself rather than as whatever the
        # completed-state restart makes of it.
        assert not _database_sidecars(database), (
            "the installed server left recovery sidecars before completed-state "
            "restart: " + ", ".join(path.name for path in _database_sidecars(database))
        )

        with closing(sqlite3.connect(database)) as connection:
            installed_reports = connection.execute(
                "SELECT COUNT(*) FROM metadata_reports"
            ).fetchone()[0]
            installed_proposals = connection.execute(
                "SELECT COUNT(*) FROM proposal_sets"
            ).fetchone()[0]
            installed_cases = connection.execute(
                "SELECT COUNT(*) FROM review_cases"
            ).fetchone()[0]
        assert installed_reports == expected_total
        assert installed_proposals == expected_total
        assert installed_cases == expected_total
        second_process = _start_review_server(review_port, arguments, origin)
        try:
            with httpx.Client(verify=False, trust_env=False) as client:
                login = client.post(
                    origin + "/api/v1/session/login",
                    headers={"Origin": origin},
                    json={"password": "synthetic password"},
                )
                assert login.status_code == 200, login.text
                completed_status = client.get(origin + "/api/v1/review-preparation")
                assert completed_status.status_code == 200, completed_status.text
                completed_projection = completed_status.json()
            assert completed_projection["active"] is False
            assert {item["library"] for item in completed_projection["libraries"]} == {
                "fiction",
                "history",
                "science",
            }
            assert all(
                item["state"] == "completed" and item["checked"] == item["total"]
                for item in completed_projection["libraries"]
            )
            assert (
                sum(item["total"] for item in completed_projection["libraries"])
                == expected_total
            )
            assert (
                sum(item["checked"] for item in completed_projection["libraries"])
                == expected_total
            )
            assert (
                sum(item["blocked"] for item in completed_projection["libraries"]) == 0
            )
        finally:
            if second_process.poll() is None:
                second_process.terminate()
            try:
                second_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                second_process.kill()
                second_process.wait(timeout=10)

        assert not _database_sidecars(database), (
            "the completed-state restart left recovery sidecars: "
            + ", ".join(path.name for path in _database_sidecars(database))
        )

        assert tuple(artifacts.iterdir()) == ()
        assert all(method == "GET" for method, _path in fixture.requests)
        installed_reconciliation_rate = (
            expected_total / installed_reconciliation_elapsed
        )
        receipt = {
            "schema": "m27-828-candidate-v4",
            "installed_execution": "installed-wheel-in-process-deterministic-stages",
            "works": expected_total,
            "phases": measurements.receipt(),
            "direct_pipeline_control_works_per_second": round(
                expected_total / processing_elapsed, 3
            ),
            "installed_reconciliation_works_per_second": round(
                installed_reconciliation_rate, 3
            ),
            "installed_reconciliation_elapsed_seconds": round(
                installed_reconciliation_elapsed, 3
            ),
            "rss_kib_max": peak_rss_kib,
            "actual_phase_fractions": actual_fractions,
            "installed_successful_transitions": {
                "reports": installed_reports,
                "proposals": installed_proposals,
                "admitted_cases": installed_cases,
            },
            "peak_decision_response_ms": decision_response_ms,
            "installed_terminal": {
                "state": "completed",
                "total": expected_total,
                "checked": expected_total,
                "blocked": 0,
            },
            "fairness": {
                "first_library_positions": {
                    library: initial_extracted.index(
                        next(
                            item
                            for item in initial_extracted
                            if item.startswith(library + "-")
                        )
                    )
                    + 1
                    for library in counts
                },
                "new_work_position": extracted[before_new:].index("fiction-new") + 1,
            },
            "restart": {"completed_state_restart": True},
        }
        receipt["p95_budgets_ms"] = _validate_receipt_budgets(receipt)
        assert (
            installed_reconciliation_rate
            >= _MINIMUM_INSTALLED_RECONCILIATION_WORKS_PER_SECOND
        )
        assert peak_rss_kib < 524_288
        if receipt_path is not None:
            receipt_path.write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
            )


class _ServerExited(AssertionError):
    """The installed server exited before it served, carrying its exit code.

    An `AssertionError` so the message and the failure kind #730 established
    are unchanged for a reader; a named subclass so `_start_review_server` can
    catch the one exit it is allowed to reason about without catching the
    unrelated timeout beside it.
    """


def _wait_for_https(process: subprocess.Popen[bytes], origin: str) -> None:
    """Poll the installed server until it is live, or say which way it failed.

    The two ways out are unrelated and used to raise one string, so three
    sightings of the intermittent `installed review server did not start`
    produced no evidence about which had happened (#730). A death now reports
    the child's exit code, which is the discriminating fact and is not
    sensitive; the child's own output stays discarded, because it inherits an
    environment carrying secret-named values and this qualification is not on
    a credentials boundary.
    """

    deadline = time.monotonic() + _REVIEW_SERVER_START_SECONDS
    with httpx.Client(verify=False, trust_env=False) as client:
        while True:
            code = process.poll()
            if code is not None:
                raise _ServerExited(
                    f"installed review server exited before serving, exit code {code}"
                )
            try:
                response = client.get(origin + "/health/live")
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "installed review server stayed alive without serving for "
                    f"{_REVIEW_SERVER_START_SECONDS} seconds"
                )
            time.sleep(0.02)


def _rss_kib(process_id: int) -> int:
    value = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(process_id)],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    ).stdout.strip()
    return int(value)


def _database_sidecars(database: Path) -> tuple[Path, ...]:
    """The recovery files a supported database basename must not carry.

    `open_repository` rejects all three before it connects, so their presence
    is the difference between a restart the product supports and one it fails
    closed on.
    """

    return tuple(
        sidecar
        for suffix in ("-journal", "-wal", "-shm")
        for sidecar in [database.with_name(database.name + suffix)]
        if sidecar.exists()
    )


def _start_review_server(
    reservation: ReservedPort, arguments: list[str], origin: str
) -> subprocess.Popen[bytes]:
    """Start the installed server on the reserved port, re-taking a lost one.

    Called at both start sites, so the restart gets the same protection as the
    first start: the port is free for as long as the first child takes to die,
    and that gap was never covered either.

    A stolen port and a genuine start-up regression both exit the child, so
    they are told apart by who holds the port the moment it has. A dead child
    holds nothing, so a port still occupied then is occupied by whoever took
    it, and only that case retries. A port that is free was free when the
    child died and did not kill it, so that exit is reported immediately with
    the child's own exit code -- the discriminating fact #730 added -- and no
    regression is ever absorbed by a retry (#760).
    """

    for attempt in range(_REVIEW_SERVER_PORT_ATTEMPTS):
        # Holding the port is a protection, not a precondition, so failing to
        # take it is not itself a failure. The server sets `SO_REUSEADDR` and
        # binds straight over a `TIME_WAIT` its own predecessor left, which
        # this deliberately reuse-free probe cannot; refusing to start there
        # would trade one intermittent failure for another.
        reservation.take(_REVIEW_SERVER_PORT_WAIT_SECONDS)
        reservation.release()
        process = subprocess.Popen(
            arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            _wait_for_https(process, origin)
        except _ServerExited:
            if reservation.take() or attempt == _REVIEW_SERVER_PORT_ATTEMPTS - 1:
                raise
            continue
        return process
    raise AssertionError("unreachable: every attempt returns or raises")
