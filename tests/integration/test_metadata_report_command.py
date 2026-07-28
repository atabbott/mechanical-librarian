from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

import mechanical_librarian.bootstrap.composition as composition
import mechanical_librarian.adapters.providers.open_library_metadata as open_library_metadata
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    UnsupportedMediaError,
)
from mechanical_librarian.bootstrap.composition import reopen_metadata_report, run_metadata_report
from mechanical_librarian.bootstrap.composition import reopen_cover_preview
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.cover_evidence import PREVIEW_VARIANT
from mechanical_librarian.infrastructure.security.credentials import (
    hardcover_client as real_hardcover_client,
)
from tests.support.epub_packages import (
    _opening_epub,
    _opening_package,
    _xhtml,
    opening_no_applicable,
    opening_title_isbn_applicability,
)


class _Server:
    def __init__(self, payload: dict[str, object], cover: bytes | None = None) -> None:
        self.payload = payload
        self.cover = cover
        self.requests = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.requests += 1
                if self.path == "/api/items/item-1/cover?raw=1" and owner.cover is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(owner.cover)))
                    self.end_headers()
                    self.wfile.write(owner.cover)
                    return
                if self.path != "/api/items/item-1?expanded=1" or self.headers.get("Authorization") != "Bearer target-token":
                    self.send_response(403)
                    self.end_headers()
                    return
                body = json.dumps(owner.payload, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()


def _item(size: int) -> dict[str, object]:
    return {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": {"title": "Current service title"},
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "opaque-inode",
                "metadata": {
                    "relPath": "book.epub",
                    "path": "/library/book.epub",
                    "size": size,
                },
            },
        },
    }


def _config(endpoint: str, media: Path, target_secret: Path, *, audiobook: bool = False, provider_secret: str | None = None) -> dict[str, object]:
    return {
        "schema_version": "config-v5",
        "connections": [
            {
                "name": "primary",
                "adapter": "audiobookshelf",
                "endpoint": endpoint,
                "secret_file": str(target_secret),
            }
        ],
        "libraries": [
            {
                "name": "books",
                "connection": "primary",
                "library_id": "library-id",
                "mount": None if audiobook else "books",
                "provider_connections": ["catalogue"],
            }
        ],
        "mounts": [] if audiobook else [
            {
                "name": "books",
                "container_path": str(media),
                "service_path": "/library",
                "read_only": True,
            }
        ],
        "providers": [
            {
                "name": "catalogue",
                "adapter": "hardcover",
                "enabled": provider_secret is not None,
                "cover_enabled": provider_secret is not None,
                "secret_file": provider_secret,
            }
        ],
    }


def _installed_cli(
    config: Path,
    database: Path,
    artifact_store: Path | None = None,
    *,
    library: str = "books",
    file_index: int = 0,
) -> tuple[int, str, str]:
    artifact_store = database.parent / "artifacts" if artifact_store is None else artifact_store
    artifact_store.mkdir(mode=0o700, exist_ok=True)
    output = io.StringIO()
    errors = io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        result = composition.main(
            [
                "metadata-report",
                "--config",
                str(config),
                "--database",
                str(database),
                "--artifact-store",
                str(artifact_store),
                "--connection",
                "primary",
                "--library",
                library,
                "--item",
                "item-1",
                "--file-index",
                str(file_index),
            ]
        )
    return result, output.getvalue(), errors.getvalue()


class MetadataReportCommandTests(unittest.TestCase):
    def test_cover_artifact_replay_successor_and_historical_preview(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); media = root / "media"; media.mkdir()
            source = media / "book.epub"; source.write_bytes(opening_no_applicable())
            secret = root / "target.secret"; secret.write_text("target-token"); secret.chmod(0o600)
            image = io.BytesIO(); Image.new("RGB", (10, 20), "red").save(image, "PNG")
            payload = _item(source.stat().st_size); payload["coverPath"] = "/ignored-cover-route"
            database = root / "reports.sqlite"; artifacts = root / "artifacts"; artifacts.mkdir(mode=0o700)
            with _Server(payload, image.getvalue()) as server:
                config = root / "config.json"; config.write_text(json.dumps(_config(server.endpoint, media, secret)))
                first = MetadataReport.from_json(run_metadata_report(config, database, artifacts, "primary", "books", "item-1", 0))
                replay = MetadataReport.from_json(run_metadata_report(config, database, artifacts, "primary", "books", "item-1", 0))
                payload["media"]["metadata"]["title"] = "Successor"  # type: ignore[index]
                successor = MetadataReport.from_json(run_metadata_report(config, database, artifacts, "primary", "books", "item-1", 0))
            self.assertEqual(first.report_id, replay.report_id)
            self.assertNotEqual(first.report_id, successor.report_id)
            artifact = first.cover_sources[0].artifact
            self.assertIsNotNone(artifact)
            preview = reopen_cover_preview(database, artifacts, first.report_id, artifact.artifact_id, PREVIEW_VARIANT)
            self.assertTrue(preview.startswith(b"\x89PNG\r\n\x1a\n"))
    def test_hostile_target_title_is_rejected_before_database_with_value_free_error(self) -> None:
        hostile = "Authorization: Bearer abcd1234"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            source.write_bytes(opening_no_applicable())
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            payload = _item(source.stat().st_size)
            payload["media"]["metadata"]["title"] = hostile
            with _Server(payload) as server:
                config = root / "config.json"
                config.write_text(
                    json.dumps(_config(server.endpoint, media, target_secret)),
                    encoding="utf-8",
                )
                database = root / "reports.sqlite"
                artifact_store = root / "artifacts"
                artifact_store.mkdir(mode=0o700)
                with self.assertRaises(MetadataReportError) as raised:
                    run_metadata_report(
                        config, database, artifact_store, "primary", "books", "item-1", 0
                    )
            self.assertFalse(database.exists())
            self.assertNotIn(hostile, str(raised.exception))

    def test_installed_command_requires_explicit_locations_and_emits_persisted_bytes(self) -> None:
        executable = shutil.which("mechanical-librarian")
        self.assertIsNotNone(executable)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            original_media = opening_title_isbn_applicability()
            source.write_bytes(original_media)
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            database = root / "reports.sqlite"
            artifact_store = root / "artifacts"
            artifact_store.mkdir(mode=0o700)
            exact_title = "Cafe\u0301  Story"
            payload = _item(source.stat().st_size)
            payload["media"]["metadata"]["title"] = exact_title
            image = io.BytesIO()
            from PIL import Image
            Image.new("RGB", (10, 20), "red").save(image, "PNG")
            payload["coverPath"] = "/ignored-cover-route"
            with _Server(payload, image.getvalue()) as server:
                config = root / "explicit-config.json"
                config.write_text(json.dumps(_config(server.endpoint, media, target_secret)), encoding="utf-8")
                command = [
                    executable,
                    "metadata-report",
                    "--config", str(config),
                    "--database", str(database),
                    "--artifact-store", str(artifact_store),
                    "--connection", "primary",
                    "--library", "books",
                    "--item", "item-1",
                    "--file-index", "0",
                ]
                completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, timeout=20)
                replay = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, timeout=20)
                missing = subprocess.run(
                    [executable, "metadata-report", "--connection", "primary", "--library", "books", "--item", "item-1", "--file-index", "0"],
                    cwd=root,
                    env={**os.environ, "MECHANICAL_LIBRARIAN_CONFIG": str(config), "MECHANICAL_LIBRARIAN_DATABASE": str(database)},
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=20,
                )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertTrue(completed.stdout.endswith("\n"))
            self.assertNotEqual(completed.stdout, "\n")
            self.assertEqual(completed.stdout, replay.stdout)
            emitted = MetadataReport.from_json(completed.stdout.removesuffix("\n"))
            self.assertEqual(emitted.target.current_title, exact_title)
            target_title = next(
                claim
                for claim in emitted.local_claims
                if claim.kind.value == "title" and claim.basis == "target_metadata"
            )
            self.assertEqual(target_title.observed, exact_title)
            self.assertEqual(target_title.canonical, "Café Story")
            connection = sqlite3.connect(database)
            persisted = connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
            connection.close()
            self.assertEqual(completed.stdout, persisted + "\n")
            artifact = next(source.artifact for source in emitted.cover_sources if source.artifact is not None)
            self.assertIsNotNone(artifact)
            preview = subprocess.run(
                [
                    executable,
                    "metadata-report-cover-preview",
                    "--database", str(database),
                    "--artifact-store", str(artifact_store),
                    "--report-id", emitted.report_id,
                    "--artifact-id", artifact.artifact_id,
                    "--preview-variant", PREVIEW_VARIANT,
                ],
                cwd=root,
                capture_output=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(0, preview.returncode, preview.stderr.decode())
            self.assertTrue(preview.stdout.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing.stdout, "")
            self.assertEqual(missing.stderr, "Metadata report failed: invalid invocation.\n")
            self.assertEqual(source.read_bytes(), original_media)

    def test_installed_cli_invalid_file_index_publishes_nothing(self) -> None:
        self.assertIsNotNone(shutil.which("mechanical-librarian"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            source.write_bytes(opening_no_applicable())
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            database = root / "reports.sqlite"
            activity: list[str] = []

            class Inspector:
                def inspect(self, *args, **kwargs):
                    activity.append("parser")
                    raise AssertionError("invalid selection reached parser")

            with _Server(_item(source.stat().st_size)) as server:
                config = root / "config.json"
                config.write_text(
                    json.dumps(_config(server.endpoint, media, target_secret)),
                    encoding="utf-8",
                )
                with patch.object(
                    composition, "_IsolatedEpubInspector", return_value=Inspector()
                ), patch.object(
                    composition,
                    "hardcover_client",
                    side_effect=lambda *args, **kwargs: activity.append("provider"),
                ):
                    result, output, errors = _installed_cli(
                        config, database, file_index=1
                    )
            self.assertEqual(result, 4)
            self.assertEqual(output, "")
            self.assertEqual(errors, "Metadata report failed: unsupported media.\n")
            self.assertEqual(activity, [])
            self.assertFalse(database.exists())
            self.assertEqual(server.requests, 1)

    def test_installed_cli_selected_non_epub_has_zero_downstream_activity(self) -> None:
        self.assertIsNotNone(shutil.which("mechanical-librarian"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.pdf"
            original = b"%PDF-1.4\nsynthetic\n"
            source.write_bytes(original)
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            payload = _item(source.stat().st_size)
            payload["media"]["ebookFile"]["ebookFormat"] = "pdf"
            payload["media"]["ebookFile"]["metadata"].update(
                {"relPath": "book.pdf", "path": "/library/book.pdf"}
            )
            database = root / "reports.sqlite"
            activity: list[str] = []

            class Inspector:
                def inspect(self, *args, **kwargs):
                    activity.append("parser")
                    raise AssertionError("non-EPUB reached parser")

            with _Server(payload) as server:
                config = root / "config.json"
                config.write_text(
                    json.dumps(
                        _config(
                            server.endpoint,
                            media,
                            target_secret,
                            provider_secret="/definitely/absent/provider.secret",
                        )
                    ),
                    encoding="utf-8",
                )
                with patch.object(
                    composition, "_IsolatedEpubInspector", return_value=Inspector()
                ), patch.object(
                    composition,
                    "hardcover_client",
                    side_effect=lambda *args, **kwargs: activity.append("provider"),
                ):
                    result, output, errors = _installed_cli(config, database)
            self.assertEqual(result, 4)
            self.assertEqual(output, "")
            self.assertEqual(errors, "Metadata report failed: unsupported media.\n")
            self.assertEqual(activity, [])
            self.assertFalse(database.exists())
            self.assertEqual(server.requests, 1)
            self.assertEqual(source.read_bytes(), original)

    def test_installed_cli_referenced_media_unsupported_provider_is_absent(self) -> None:
        self.assertIsNotNone(shutil.which("mechanical-librarian"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            source = _config(
                "http://127.0.0.1:9",
                root,
                root / "absent-target.secret",
                audiobook=True,
                provider_secret="/definitely/absent/provider.secret",
            )
            self.assertEqual(
                source["libraries"][0]["provider_connections"], ["catalogue"]
            )
            config.write_text(json.dumps(source), encoding="utf-8")
            database = root / "reports.sqlite"
            activity: list[str] = []
            with patch.object(
                composition,
                "target_client",
                side_effect=lambda *args, **kwargs: activity.append("target"),
            ), patch.object(
                composition,
                "hardcover_client",
                side_effect=lambda *args, **kwargs: activity.append("provider"),
            ), patch.object(
                composition,
                "_IsolatedEpubInspector",
                side_effect=lambda: activity.append("parser"),
            ):
                result, output, errors = _installed_cli(config, database)
            self.assertEqual(result, 4)
            self.assertEqual(output, "")
            self.assertEqual(errors, "Metadata report failed: unsupported media.\n")
            self.assertEqual(activity, [])
            self.assertFalse(database.exists())

    def test_installed_cli_reuses_one_provider_credential_across_libraries(self) -> None:
        self.assertIsNotNone(shutil.which("mechanical-librarian"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            source.write_bytes(
                _opening_epub(
                    package=_opening_package(
                        items=(("opening", "opening.xhtml", "application/xhtml+xml", ""),),
                        spine=("opening",),
                    ),
                    entries=(
                        (
                            "EPUB/opening.xhtml",
                            _xhtml(
                                '<section epub:type="titlepage"><h1>Two ISBN Clock</h1></section>'
                                '<section epub:type="copyright-page"><p>'
                                'ISBN 9780306406157 (eBook)'
                                '</p></section>'
                            ),
                            zipfile.ZIP_STORED,
                        ),
                    ),
                )
            )
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            provider_secret = root / "provider.secret"
            provider_secret.write_text("provider-token", encoding="ascii")
            provider_secret.chmod(0o600)
            provider_requests: list[httpx.Request] = []
            provider_secret_uses: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                provider_requests.append(request)
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "data": {
                            "editions": [
                                {"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157", "asin": None, "title": None, "subtitle": None, "release_date": None, "publisher": None, "language": None, "contributions": []},
                            ],
                            "books": [
                                {"id": 1, "title": "Current service title", "description": None, "release_date": None, "image": None, "contributions": [], "book_series": [], "taggings": []},
                            ],
                            "tags": [],
                        }
                    },
                )

            def client(secret_file: str, timeout_seconds: int) -> httpx.Client:
                provider_secret_uses.append(secret_file)
                return real_hardcover_client(
                    secret_file,
                    timeout_seconds,
                    transport=httpx.MockTransport(handler),
                )

            database = root / "reports.sqlite"
            with _Server(_item(source.stat().st_size)) as server:
                source_config = _config(
                    server.endpoint,
                    media,
                    target_secret,
                    provider_secret=str(provider_secret),
                )
                source_config["libraries"].append(
                    {
                        "name": "other",
                        "connection": "primary",
                        "library_id": "library-id",
                        "mount": "books",
                        "provider_connections": ["catalogue"],
                    }
                )
                self.assertEqual(len(source_config["providers"]), 1)
                self.assertEqual(
                    source_config["providers"][0]["secret_file"],
                    str(provider_secret),
                )
                config = root / "config.json"
                config.write_text(json.dumps(source_config), encoding="utf-8")
                with patch.object(composition, "hardcover_client", side_effect=client):
                    first = _installed_cli(config, database, library="books")
                    replay = _installed_cli(config, database, library="books")
                    second = _installed_cli(config, database, library="other")

            self.assertEqual([first[0], replay[0], second[0]], [0, 0, 0])
            self.assertEqual([first[2], replay[2], second[2]], ["", "", ""])
            self.assertEqual(first[1], replay[1])
            reports = [
                MetadataReport.from_json(value[1].removesuffix("\n"))
                for value in (first, replay, second)
            ]
            self.assertEqual(
                [report.target.library for report in reports], ["books", "books", "other"]
            )
            self.assertEqual(
                [report.providers[0].status.value for report in reports],
                ["candidates", "candidates", "candidates"],
            )
            self.assertEqual(
                [candidate.work_identity for candidate in reports[0].providers[0].candidates],
                [
                    "hardcover-work-0000000001",
                ],
            )
            self.assertEqual(
                provider_secret_uses,
                [str(provider_secret), str(provider_secret), str(provider_secret)],
            )
            self.assertEqual(len(provider_requests), 3)
            self.assertTrue(
                all(
                    json.loads(request.content)["variables"]["isbns"]
                    == ["9780306406157"]
                    for request in provider_requests
                )
            )
            self.assertTrue(
                all(
                    request.headers.get("authorization")
                    == "Bearer provider-token"
                    for request in provider_requests
                )
            )
            self.assertEqual(server.requests, 6)
            public_output = "".join(
                value
                for result in (first, replay, second)
                for value in result[1:]
            )
            self.assertNotIn("provider-token", public_output)
            self.assertNotIn("target-token", public_output)
            self.assertNotIn(str(provider_secret), public_output)
            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM metadata_reports"
                ).fetchone(),
                (2,),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM current_metadata_reports"
                ).fetchone(),
                (2,),
            )
            connection.close()

    def test_installed_cli_uses_no_isbn_work_fallback_and_exact_id_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            source.write_bytes(opening_no_applicable())
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            provider_secret = root / "provider.secret"
            provider_secret.write_text("provider-token", encoding="ascii")
            provider_secret.chmod(0o600)
            target = _item(source.stat().st_size)
            target["media"]["metadata"]["authors"] = [  # type: ignore[index]
                {"name": "Ada Example"}
            ]
            provider_requests: list[httpx.Request] = []
            provider_payload = {
                "data": {
                    "editions": [
                        {
                            "id": 1,
                            "book_id": 1,
                            "isbn_10": None,
                            "isbn_13": None,
                            "asin": None,
                            "title": None,
                            "subtitle": None,
                            "release_date": None,
                            "publisher": None,
                            "language": None,
                            "contributions": [],
                        }
                    ],
                    "books": [
                        {
                            "id": 1,
                            "title": "Current service title",
                            "description": "Work description",
                            "release_date": "2001-02-03",
                            "image": None,
                            "contributions": [
                                {
                                    "id": 1,
                                    "contribution": "Author",
                                    "author": {
                                        "id": 7,
                                        "name": "Ada Example",
                                    },
                                }
                            ],
                            "book_series": [],
                            "taggings": [],
                        }
                    ],
                    "tags": [],
                }
            }

            def hardcover_client(
                secret_file: str,
                timeout_seconds: int,
            ) -> httpx.Client:
                return real_hardcover_client(
                    secret_file,
                    timeout_seconds,
                    transport=httpx.MockTransport(
                        lambda request: provider_requests.append(request)
                        or httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=provider_payload,
                        )
                    ),
                )

            database = root / "reports.sqlite"
            with _Server(target) as server:
                config = root / "config.json"
                config.write_text(
                    json.dumps(
                        _config(
                            server.endpoint,
                            media,
                            target_secret,
                            provider_secret=str(provider_secret),
                        )
                    ),
                    encoding="utf-8",
                )
                with patch.object(
                    composition,
                    "hardcover_client",
                    side_effect=hardcover_client,
                ):
                    result, output, errors = _installed_cli(config, database)
            self.assertEqual((0, ""), (result, errors))
            report = MetadataReport.from_json(output.removesuffix("\n"))
            self.assertEqual(16, len(report.target.fields))
            self.assertEqual("work", report.providers[0].query_kind.value)
            self.assertEqual("candidates", report.providers[0].status.value)
            self.assertEqual(
                {"title", "creator", "description"},
                {
                    claim.kind.value
                    for claim in report.providers[0].candidates[0].claims
                    if claim.state.value == "valid"
                },
            )
            request = json.loads(provider_requests[0].content)
            self.assertEqual("MetadataEvidenceByWork", request["operationName"])
            self.assertEqual(
                {
                    "authors": ["Ada Example"],
                    "title": "Current service title",
                },
                request["variables"],
            )

            reopened_output = io.StringIO()
            reopened_errors = io.StringIO()
            with redirect_stdout(reopened_output), redirect_stderr(reopened_errors):
                reopened_result = composition.main(
                    [
                        "metadata-report-get",
                        "--database",
                        str(database),
                        "--report-id",
                        report.report_id,
                    ]
                )
            self.assertEqual((0, ""), (reopened_result, reopened_errors.getvalue()))
            self.assertEqual(output, reopened_output.getvalue())

    def test_two_provider_order_contact_privacy_and_failure_isolation(self) -> None:
        class NoWaitPacer:
            def wait(self, deadline: float | None = None) -> bool:
                return True

        hardcover_payload = {
            "data": {
                "editions": [
                    {
                        "id": 1,
                        "book_id": 1,
                        "isbn_10": "0306406152",
                        "isbn_13": "9780306406157",
                        "asin": None,
                        "title": "Edition title",
                        "subtitle": None,
                        "release_date": None,
                        "publisher": None,
                        "language": None,
                        "contributions": [],
                    }
                ],
                "books": [
                    {
                        "id": 1,
                        "title": "Current service title",
                        "description": None,
                        "release_date": None,
                        "image": None,
                        "contributions": [],
                        "book_series": [],
                        "taggings": [],
                    }
                ],
                "tags": [],
            }
        }
        open_payloads = {
            "/search.json": {
                "docs": [
                    {
                        "key": "/works/OL5W",
                        "editions": {
                            "docs": [
                                {
                                    "key": "/books/OL1M",
                                    "isbn": ["9780306406157"],
                                }
                            ]
                        },
                    }
                ]
            },
            "/books/OL1M.json": {
                "key": "/books/OL1M",
                "works": [{"key": "/works/OL5W"}],
                "isbn_13": ["9780306406157"],
                "title": "Edition title",
                "covers": [100],
            },
            "/works/OL5W.json": {
                "key": "/works/OL5W",
                "title": "Current service title",
                "covers": [200],
            },
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            source.write_bytes(opening_title_isbn_applicability())
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            provider_secret = root / "provider.secret"
            provider_secret.write_text("provider-token", encoding="ascii")
            provider_secret.chmod(0o600)

            with _Server(_item(source.stat().st_size)) as server:
                base = _config(
                    server.endpoint,
                    media,
                    target_secret,
                    provider_secret=str(provider_secret),
                )
                base["providers"][0]["cover_enabled"] = False  # type: ignore[index]
                base["libraries"][0]["provider_connections"] = [  # type: ignore[index]
                    "catalogue",
                    "open",
                ]
                base["providers"].append(  # type: ignore[union-attr]
                    {
                        "name": "open",
                        "adapter": "open_library",
                        "enabled": True,
                        "cover_enabled": True,
                        "secret_file": None,
                        "contact_email": "first@example.invalid",
                    }
                )
                reverse = json.loads(json.dumps(base))
                reverse["libraries"][0]["provider_connections"].reverse()
                reverse["providers"].reverse()
                reverse["providers"][0][
                    "contact_email"
                ] = "second@example.invalid"
                configs = []
                for name, value in (("first", base), ("second", reverse)):
                    path = root / f"{name}.json"
                    path.write_text(json.dumps(value), encoding="utf-8")
                    configs.append(path)

                def hardcover_client(
                    secret_file: str,
                    timeout_seconds: int,
                ) -> httpx.Client:
                    return real_hardcover_client(
                        secret_file,
                        timeout_seconds,
                        transport=httpx.MockTransport(
                            lambda request: httpx.Response(
                                200,
                                headers={"content-type": "application/json"},
                                json=hardcover_payload,
                            )
                        ),
                    )

                def open_client(
                    contact_email: str,
                    timeout_seconds: int,
                ) -> httpx.Client:
                    return httpx.Client(
                        headers={
                            "User-Agent": (
                                f"Mechanical-Librarian ({contact_email})"
                            )
                        },
                        transport=httpx.MockTransport(
                            lambda request: httpx.Response(
                                200,
                                headers={"content-type": "application/json"},
                                json=open_payloads[request.url.path],
                            )
                        ),
                    )

                cover_user_agents: list[str | None] = []

                def cover_failure(
                    url: str,
                    **kwargs: object,
                ) -> object:
                    cover_user_agents.append(
                        kwargs.get("user_agent")  # type: ignore[arg-type]
                    )
                    raise composition.RemoteCoverError(
                        "remote cover unavailable"
                    )

                with (
                    patch.object(
                        composition,
                        "hardcover_client",
                        side_effect=hardcover_client,
                    ),
                    patch.object(
                        composition,
                        "open_library_client",
                        side_effect=open_client,
                    ),
                    patch.object(
                        open_library_metadata,
                        "OpenLibraryPacer",
                        return_value=NoWaitPacer(),
                    ),
                    patch.object(
                        composition,
                        "fetch_remote_cover",
                        side_effect=cover_failure,
                    ),
                ):
                    first = _installed_cli(
                        configs[0],
                        root / "first.sqlite",
                        root / "first-artifacts",
                    )
                    second = _installed_cli(
                        configs[1],
                        root / "second.sqlite",
                        root / "second-artifacts",
                    )

                self.assertEqual((0, "", 0, ""), (first[0], first[2], second[0], second[2]))
                self.assertEqual(first[1], second[1])
                self.assertNotIn("first@example.invalid", first[1])
                self.assertNotIn("second@example.invalid", second[1])
                report = MetadataReport.from_json(first[1].removesuffix("\n"))
                self.assertEqual(
                    [("catalogue", "candidates"), ("open", "candidates")],
                    [
                        (provider.provider, provider.status.value)
                        for provider in report.providers
                    ],
                )
                self.assertEqual(
                    [
                        ("open_library", "unavailable"),
                        ("open_library", "unavailable"),
                    ],
                    [
                        (source.kind.value, source.state.value)
                        for source in report.cover_sources
                        if source.kind.value == "open_library"
                    ],
                )
                self.assertEqual(
                    [
                        "Mechanical-Librarian (first@example.invalid)",
                        "Mechanical-Librarian (first@example.invalid)",
                        "Mechanical-Librarian (second@example.invalid)",
                        "Mechanical-Librarian (second@example.invalid)",
                    ],
                    cover_user_agents,
                )

                def failed_open_client(
                    contact_email: str,
                    timeout_seconds: int,
                ) -> httpx.Client:
                    return httpx.Client(
                        headers={
                            "User-Agent": (
                                f"Mechanical-Librarian ({contact_email})"
                            )
                        },
                        transport=httpx.MockTransport(
                            lambda request: httpx.Response(503)
                        ),
                    )

                with (
                    patch.object(
                        composition,
                        "hardcover_client",
                        side_effect=hardcover_client,
                    ),
                    patch.object(
                        composition,
                        "open_library_client",
                        side_effect=failed_open_client,
                    ),
                    patch.object(
                        open_library_metadata,
                        "OpenLibraryPacer",
                        return_value=NoWaitPacer(),
                    ),
                ):
                    failed = _installed_cli(
                        configs[0],
                        root / "failed.sqlite",
                        root / "failed-artifacts",
                    )
                self.assertEqual((0, ""), (failed[0], failed[2]))
                failed_report = MetadataReport.from_json(
                    failed[1].removesuffix("\n")
                )
                self.assertEqual(
                    [("catalogue", "candidates"), ("open", "unavailable")],
                    [
                        (provider.provider, provider.status.value)
                        for provider in failed_report.providers
                    ],
                )

    def test_real_child_clean_database_exact_replay_and_successor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            original_media = opening_title_isbn_applicability()
            source.write_bytes(original_media)
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            database = root / "reports.sqlite"
            artifact_store = root / "artifacts"
            artifact_store.mkdir(mode=0o700)
            with _Server(_item(source.stat().st_size)) as server:
                config = root / "config.json"
                config.write_text(json.dumps(_config(server.endpoint, media, target_secret)), encoding="utf-8")
                first = run_metadata_report(config, database, artifact_store, "primary", "books", "item-1", 0)
                replay = run_metadata_report(config, database, artifact_store, "primary", "books", "item-1", 0)
                server.payload["media"]["metadata"]["title"] = "Changed service title"
                successor = run_metadata_report(config, database, artifact_store, "primary", "books", "item-1", 0)
                successor_report = MetadataReport.from_json(successor)
                stale_output = io.StringIO()
                stale_errors = io.StringIO()
                with redirect_stdout(stale_output), redirect_stderr(stale_errors):
                    stale_result = composition.main(
                        [
                            "proposal-set-create",
                            "--config",
                            str(config),
                            "--database",
                            str(database),
                            "--artifact-store",
                            str(artifact_store),
                            "--report-id",
                            MetadataReport.from_json(first).report_id,
                        ]
                    )
                proposal_output = io.StringIO()
                proposal_errors = io.StringIO()
                with (
                    patch.object(composition, "hardcover_client") as provider_client,
                    redirect_stdout(proposal_output),
                    redirect_stderr(proposal_errors),
                ):
                    proposal_result = composition.main(
                        [
                            "proposal-set-create",
                            "--config",
                            str(config),
                            "--database",
                            str(database),
                            "--artifact-store",
                            str(artifact_store),
                            "--report-id",
                            successor_report.report_id,
                        ]
                    )
                provider_client.assert_not_called()

            first_report = MetadataReport.from_json(first)
            proposal_set = ProposalSet.from_json(
                proposal_output.getvalue().removesuffix("\n")
            )
            reopened_output = io.StringIO()
            reopened_errors = io.StringIO()
            with redirect_stdout(reopened_output), redirect_stderr(reopened_errors):
                reopened_result = composition.main(
                    [
                        "proposal-set-get",
                        "--database",
                        str(database),
                        "--proposal-set-id",
                        proposal_set.proposal_set_id,
                    ]
                )
            self.assertEqual(first, replay)
            self.assertNotEqual(first_report.report_id, successor_report.report_id)
            self.assertEqual((1, ""), (stale_result, stale_output.getvalue()))
            self.assertEqual(
                "Proposal set failed safely.\n", stale_errors.getvalue()
            )
            self.assertEqual((0, ""), (proposal_result, proposal_errors.getvalue()))
            self.assertEqual(16, len(proposal_set.results))
            self.assertEqual(successor_report.report_id, proposal_set.report_id)
            self.assertEqual((0, ""), (reopened_result, reopened_errors.getvalue()))
            self.assertEqual(proposal_output.getvalue(), reopened_output.getvalue())
            self.assertEqual(
                {claim.kind.value for claim in first_report.local_claims},
                {"title", "isbn", "language", "date_event"},
            )
            isbn_claims = [claim for claim in first_report.local_claims if claim.kind.value == "isbn"]
            self.assertEqual({claim.checksum for claim in isbn_claims}, {"valid_isbn_13"})
            self.assertEqual({claim.applicability for claim in isbn_claims}, {"ebook", "print"})
            self.assertEqual({claim.applicability_basis for claim in isbn_claims}, {"ebook", "paperback"})
            self.assertEqual(first_report.providers[0].status.value, "disabled")
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM metadata_reports").fetchone(), (2,))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM current_metadata_reports").fetchone(), (1,))
            connection.close()
            self.assertEqual(server.requests, 8)
            self.assertEqual(source.read_bytes(), original_media)

    def test_rear_limit_warning_survives_real_child_report_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            package = _opening_package(
                items=(
                    ("opening", "opening.xhtml", "application/xhtml+xml", ""),
                    ("rear", "rear.xhtml", "application/xhtml+xml", ""),
                ),
                spine=("opening", "rear"),
            )
            source.write_bytes(
                _opening_epub(
                    package=package,
                    entries=(
                        ("EPUB/opening.xhtml", _xhtml('<h1 epub:type="fulltitle">Synthetic Opening Title</h1><main epub:type="bodymatter">Synthetic body boundary</main>'), zipfile.ZIP_STORED),
                        ("EPUB/rear.xhtml", _xhtml("<p>Copyright 2026</p><p>" + "x" * 262_145 + "</p>"), zipfile.ZIP_STORED),
                    ),
                )
            )
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            database = root / "reports.sqlite"
            artifact_store = root / "artifacts"
            artifact_store.mkdir(mode=0o700)
            with _Server(_item(source.stat().st_size)) as server:
                config = root / "config.json"
                config.write_text(json.dumps(_config(server.endpoint, media, target_secret)), encoding="utf-8")
                report = MetadataReport.from_json(run_metadata_report(config, database, artifact_store, "primary", "books", "item-1", 0))
            reopened = MetadataReport.from_json(reopen_metadata_report(database, report.report_id))
            self.assertIn("rear_resource_limit", report.local_reasons)
            self.assertEqual(reopened, report)
            self.assertFalse(any(claim.rear_provenance is not None for claim in report.local_claims))

    def test_six_metadata_only_providers_do_not_consume_cover_source_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            source.write_bytes(opening_no_applicable())
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            with _Server(_item(source.stat().st_size)) as server:
                source_config = _config(server.endpoint, media, target_secret)
                provider_names = [f"catalogue-{index}" for index in range(6)]
                source_config["libraries"][0]["provider_connections"] = provider_names
                source_config["providers"] = [
                    {
                        "name": name,
                        "adapter": "hardcover",
                        "enabled": False,
                        "cover_enabled": False,
                        "secret_file": None,
                    }
                    for name in provider_names
                ]
                config = root / "config.json"
                config.write_text(json.dumps(source_config), encoding="utf-8")
                database = root / "reports.sqlite"
                artifact_store = root / "artifacts"
                artifact_store.mkdir(mode=0o700)
                report = MetadataReport.from_json(
                    run_metadata_report(
                        config,
                        database,
                        artifact_store,
                        "primary",
                        "books",
                        "item-1",
                        0,
                    )
                )
            self.assertEqual(6, len(report.providers))
            self.assertEqual({"disabled"}, {value.status.value for value in report.providers})
            self.assertEqual(
                {"abs", "epub"},
                {value.kind.value for value in report.cover_sources},
            )
            connection = sqlite3.connect(database)
            self.assertEqual(
                (1,),
                connection.execute("SELECT COUNT(*) FROM metadata_reports").fetchone(),
            )
            connection.close()

    def test_no_candidate_and_overlapping_item_ids_remain_explicitly_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            source = media / "book.epub"
            source.write_bytes(opening_no_applicable())
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
            with _Server(_item(source.stat().st_size)) as server:
                source_config = _config(server.endpoint, media, target_secret)
                source_config["connections"].append(
                    {
                        "name": "secondary",
                        "adapter": "audiobookshelf",
                        "endpoint": server.endpoint,
                        "secret_file": str(target_secret),
                    }
                )
                source_config["libraries"].append(
                    {
                        "name": "other",
                        "connection": "secondary",
                        "library_id": "library-id",
                        "mount": "books",
                        "provider_connections": [],
                    }
                )
                source_config["providers"].append(
                    {
                        "name": "unreferenced",
                        "adapter": "hardcover",
                        "enabled": True,
                        "cover_enabled": True,
                        "secret_file": "/definitely/absent/unreferenced.secret",
                    }
                )
                config = root / "config.json"
                config.write_text(json.dumps(source_config), encoding="utf-8")
                database = root / "reports.sqlite"
                artifact_store = root / "artifacts"
                artifact_store.mkdir(mode=0o700)
                primary = MetadataReport.from_json(
                    run_metadata_report(config, database, artifact_store, "primary", "books", "item-1", 0)
                )
                secondary = MetadataReport.from_json(
                    run_metadata_report(config, database, artifact_store, "secondary", "other", "item-1", 0)
                )
            self.assertEqual(primary.local_status, "no_candidate")
            self.assertEqual(
                primary.local_reasons,
                (
                    "package_metadata_date_event",
                    "package_metadata_language",
                    "package_metadata_title",
                ),
            )
            self.assertEqual(primary.providers[0].status.value, "disabled")
            self.assertEqual(secondary.local_status, "no_candidate")
            self.assertEqual(secondary.providers, ())
            self.assertNotEqual(primary.report_id, secondary.report_id)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM current_metadata_reports").fetchone(), (2,))
            connection.close()

    def test_audiobook_rejection_has_no_parser_provider_or_database_activity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_secret = root / "absent-target.secret"
            payload = _item(0)
            payload["media"].pop("ebookFile")
            payload["media"]["audioFiles"] = []
            with _Server(payload) as server:
                config = root / "config.json"
                config.write_text(json.dumps(_config(server.endpoint, root, target_secret, audiobook=True, provider_secret="/definitely/absent/provider.secret")), encoding="utf-8")
                database = root / "reports.sqlite"
                with self.assertRaises(UnsupportedMediaError):
                    run_metadata_report(config, database, root / "artifacts", "primary", "books", "item-1", 0)
            self.assertFalse(database.exists())
            self.assertEqual(server.requests, 0)


if __name__ == "__main__":
    unittest.main()
