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
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx

import mechanical_librarian.bootstrap.composition as composition
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    UnsupportedMediaError,
)
from mechanical_librarian.bootstrap.composition import run_metadata_report
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.infrastructure.security.credentials import (
    hardcover_client as real_hardcover_client,
)
from tests.support.epub_packages import opening_no_applicable, opening_title_isbn_applicability


class _Server:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.requests += 1
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
        "schema_version": "config-v2",
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
                "media_kind": "audiobook" if audiobook else "ebook",
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
                "secret_file": provider_secret,
            }
        ],
    }


def _installed_cli(
    config: Path,
    database: Path,
    *,
    library: str = "books",
    file_index: int = 0,
) -> tuple[int, str, str]:
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
                with self.assertRaises(MetadataReportError) as raised:
                    run_metadata_report(
                        config, database, "primary", "books", "item-1", 0
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
            exact_title = "Cafe\u0301  Story"
            payload = _item(source.stat().st_size)
            payload["media"]["metadata"]["title"] = exact_title
            with _Server(payload) as server:
                config = root / "explicit-config.json"
                config.write_text(json.dumps(_config(server.endpoint, media, target_secret)), encoding="utf-8")
                command = [
                    executable,
                    "metadata-report",
                    "--config", str(config),
                    "--database", str(database),
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
            source.write_bytes(opening_title_isbn_applicability())
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
                                {
                                    "id": 1,
                                    "isbn_10": None,
                                    "isbn_13": "9780306406157",
                                    "book": {
                                        "id": 1,
                                        "title": "Current service title",
                                    },
                                }
                            ]
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
                        "media_kind": "ebook",
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
                    second = _installed_cli(config, database, library="other")

            self.assertEqual([first[0], second[0]], [0, 0])
            self.assertEqual([first[2], second[2]], ["", ""])
            reports = [
                MetadataReport.from_json(value[1].removesuffix("\n"))
                for value in (first, second)
            ]
            self.assertEqual(
                [report.target.library for report in reports], ["books", "other"]
            )
            self.assertEqual(
                [report.providers[0].status.value for report in reports],
                ["candidates", "candidates"],
            )
            self.assertEqual(
                provider_secret_uses,
                [str(provider_secret), str(provider_secret)],
            )
            self.assertEqual(len(provider_requests), 2)
            self.assertTrue(
                all(
                    request.headers.get("authorization")
                    == "Bearer provider-token"
                    for request in provider_requests
                )
            )
            self.assertEqual(server.requests, 4)
            public_output = "".join(value for result in (first, second) for value in result[1:])
            self.assertNotIn("provider-token", public_output)
            self.assertNotIn("target-token", public_output)
            self.assertNotIn(str(provider_secret), public_output)
            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM metadata_reports").fetchone(),
                (2,),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM current_metadata_reports"
                ).fetchone(),
                (2,),
            )
            connection.close()

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
            with _Server(_item(source.stat().st_size)) as server:
                config = root / "config.json"
                config.write_text(json.dumps(_config(server.endpoint, media, target_secret)), encoding="utf-8")
                first = run_metadata_report(config, database, "primary", "books", "item-1", 0)
                replay = run_metadata_report(config, database, "primary", "books", "item-1", 0)
                server.payload["media"]["metadata"]["title"] = "Changed service title"
                successor = run_metadata_report(config, database, "primary", "books", "item-1", 0)

            first_report = MetadataReport.from_json(first)
            successor_report = MetadataReport.from_json(successor)
            self.assertEqual(first, replay)
            self.assertNotEqual(first_report.report_id, successor_report.report_id)
            self.assertEqual({claim.kind.value for claim in first_report.local_claims}, {"title", "isbn", "language"})
            isbn_claims = [claim for claim in first_report.local_claims if claim.kind.value == "isbn"]
            self.assertEqual({claim.checksum for claim in isbn_claims}, {"valid_isbn_13", "invalid"})
            self.assertEqual({claim.applicability for claim in isbn_claims}, {"ebook", "print", "unknown"})
            self.assertEqual({claim.applicability_basis for claim in isbn_claims}, {"ebook", "paperback", "unlabelled"})
            self.assertEqual(first_report.providers[0].status.value, "disabled")
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM metadata_reports").fetchone(), (2,))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM current_metadata_reports").fetchone(), (1,))
            connection.close()
            self.assertEqual(server.requests, 6)
            self.assertEqual(source.read_bytes(), original_media)

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
                        "media_kind": "ebook",
                        "mount": "books",
                        "provider_connections": [],
                    }
                )
                source_config["providers"].append(
                    {
                        "name": "unreferenced",
                        "adapter": "hardcover",
                        "enabled": True,
                        "secret_file": "/definitely/absent/unreferenced.secret",
                    }
                )
                config = root / "config.json"
                config.write_text(json.dumps(source_config), encoding="utf-8")
                database = root / "reports.sqlite"
                primary = MetadataReport.from_json(
                    run_metadata_report(config, database, "primary", "books", "item-1", 0)
                )
                secondary = MetadataReport.from_json(
                    run_metadata_report(config, database, "secondary", "other", "item-1", 0)
                )
            self.assertEqual(primary.local_status, "no_candidate")
            self.assertEqual(
                primary.local_reasons,
                ("package_metadata_isbn", "package_metadata_language", "package_metadata_title"),
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
                    run_metadata_report(config, database, "primary", "books", "item-1", 0)
            self.assertFalse(database.exists())
            self.assertEqual(server.requests, 0)


if __name__ == "__main__":
    unittest.main()
