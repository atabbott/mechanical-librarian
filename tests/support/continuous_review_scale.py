"""The installed-wheel continuous-review scale qualification driver.

`qualify_installed_pipeline` builds an installed wheel, serves a synthetic
Audiobookshelf over TLS, runs the pipeline across a restart and asserts the
cohort reconciles fairly. Two tiers drive it at different sizes -- the
integration tier at a representative cohort, the slow tier at a thousand
works -- so it lives here rather than in either tier's module: a test module
never imports another test module's fixtures, because that makes one cohort
a dependency of another (`docs/development/testing-strategy.md`).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from argon2 import PasswordHasher

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    _entry as catalogue_entry,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.batch import BatchSelection, batch_fingerprint
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    ReviewPreparationPipeline,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from tests.support.tls import synthetic_tls_identity


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
                    value = {"total": len(rows), "results": rows[page * 100 : (page + 1) * 100]}
                elif route.startswith("/api/items/"):
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

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


def qualify_installed_pipeline(
    counts: dict[str, int],
    *,
    expected_total: int,
    memory_ceiling_kib: int | None = None,
) -> None:
    # `closing` is entered first so it exits last, which is the order
    # `addCleanup(fixture.close)` gave the fixture under `TestCase`: the
    # working directory is removed and only then is the fixture closed.
    fixture = _AudiobookshelfFixture(counts)
    fixture.start()
    with closing(fixture), tempfile.TemporaryDirectory() as directory:
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
        review_port = _unused_port()
        origin = f"https://127.0.0.1:{review_port}"
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
        database = root / "application.sqlite3"
        stored_repository = open_repository(database)
        repository = _SharedRepository(stored_repository)
        clock = [100]
        reports: dict[str, MetadataReport] = {}
        proposals = {}
        report_items: dict[str, str] = {}
        provider_attempts: dict[str, int] = {}
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
                local_claims=(),
                local_reasons=("no_opening_claims",),
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
                        else "configuration" if configuration_failure else None
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
            result = json.loads(
                repository.admit_review_case(proposals[proposal_id])
            )
            return result["status"], result.get("review_case_id")

        def pipeline() -> ReviewPreparationPipeline:
            return ReviewPreparationPipeline(
                repository_factory=lambda: repository,
                libraries=tuple(
                    ("primary", name, name, producers[name])
                    for name in entries
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
            "SELECT retry_not_before FROM review_preparation_work "
            "WHERE item_id=?",
            (transient_item,),
        ).fetchone()[0]
        assert circuit == 0
        assert retry_at > clock[0]

        restarted = pipeline()
        restarted.recover()
        assert not (run_next(restarted))
        clock[0] = retry_at
        while True:
            if cast(
                PipelineProgress, restarted.progress("primary", "fiction")
            ).state == "completed":
                break
            advanced = run_next(restarted)
            if not advanced:
                assert cast(
                    PipelineProgress,
                    restarted.progress("primary", "fiction"),
                ).state == "completed"

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
        assert provider_attempts[transient_item] == 2
        assert provider_attempts[credential_item] == 2
        stored_repository.close()
        catalogue = root / "catalogue.sqlite3"
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
            str(review_port),
        ]
        fixture.hold_discovery()
        first_process = subprocess.Popen(
            arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            _wait_for_https(first_process, origin)
            assert fixture.discovery_started.wait(timeout=15), "first-start reconciliation was not observed"
            with closing(sqlite3.connect(database)) as connection:
                states = {
                    row[0]
                    for row in connection.execute(
                        "SELECT state FROM review_preparation_scans"
                    )
                }
            assert "discovering" in states
        finally:
            if first_process.poll() is None:
                first_process.kill()
            first_process.wait(timeout=10)
            fixture.release_discovery()

        second_process = subprocess.Popen(
            arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        peak_rss_kib = 0
        try:
            _wait_for_https(second_process, origin)
            with httpx.Client(verify=False, trust_env=False) as client:
                login = client.post(
                    origin + "/api/v1/session/login",
                    headers={"Origin": origin},
                    json={"password": "synthetic password"},
                )
                assert login.status_code == 200, login.text
                deadline = time.monotonic() + 60
                while True:
                    if second_process.poll() is not None:
                        pytest.fail("installed review server exited during reconciliation")
                    peak_rss_kib = max(
                        peak_rss_kib, _rss_kib(second_process.pid)
                    )
                    status = client.get(origin + "/api/v1/review-preparation")
                    if status.status_code == 503:
                        if time.monotonic() >= deadline:
                            with closing(sqlite3.connect(database)) as connection:
                                observed = connection.execute(
                                    "SELECT library_name,state,total FROM review_preparation_scans "
                                    "ORDER BY library_name"
                                ).fetchall()
                            pytest.fail("installed reconciliation status remained unavailable: "
                            + repr(observed))
                        time.sleep(0.02)
                        continue
                    assert status.status_code == 200, status.text
                    projection = status.json()
                    if (
                        not projection["active"]
                        and len(projection["libraries"]) == 3
                        and all(
                            item["state"] == "completed"
                            for item in projection["libraries"]
                        )
                    ):
                        break
                    if time.monotonic() >= deadline:
                        pytest.fail("installed reconciliation did not complete")
                    time.sleep(0.02)
            assert sum(item["total"] for item in projection["libraries"]) == expected_total
            assert sum(item["checked"] for item in projection["libraries"]) == expected_total
            assert sum(item["blocked"] for item in projection["libraries"]) == 0
            assert peak_rss_kib < 524_288
        finally:
            if second_process.poll() is None:
                second_process.terminate()
            try:
                second_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                second_process.kill()
                second_process.wait(timeout=10)

        with closing(sqlite3.connect(database)) as connection:
            work_count = connection.execute(
                "SELECT COUNT(*) FROM review_preparation_work"
            ).fetchone()[0]
        assert work_count == expected_total
        assert tuple(media.iterdir()) == ()
        assert tuple(artifacts.iterdir()) == ()


def _wait_for_https(process: subprocess.Popen[bytes], origin: str) -> None:
    deadline = time.monotonic() + 15
    with httpx.Client(verify=False, trust_env=False) as client:
        while True:
            if process.poll() is not None:
                raise AssertionError("installed review server did not start")
            try:
                response = client.get(origin + "/health/live")
                if response.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline:
                raise AssertionError("installed review server did not start")
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


def _unused_port() -> int:
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]
