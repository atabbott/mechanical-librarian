from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mechanical_librarian.application.metadata_report import UnsupportedMediaError
from mechanical_librarian.bootstrap.composition import run_metadata_report
from mechanical_librarian.domain.metadata_report import MetadataReport
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


class MetadataReportCommandTests(unittest.TestCase):
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
            with _Server(_item(source.stat().st_size)) as server:
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
            connection = sqlite3.connect(database)
            persisted = connection.execute("SELECT document FROM metadata_reports").fetchone()[0]
            connection.close()
            self.assertEqual(completed.stdout, persisted + "\n")
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing.stdout, "")
            self.assertEqual(missing.stderr, "Metadata report failed: invalid invocation.\n")
            self.assertEqual(source.read_bytes(), original_media)

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
            self.assertEqual({claim.kind.value for claim in first_report.local_claims}, {"title", "isbn"})
            isbn_claims = [claim for claim in first_report.local_claims if claim.kind.value == "isbn"]
            self.assertEqual({claim.checksum for claim in isbn_claims}, {"valid_isbn_13"})
            self.assertEqual({claim.applicability for claim in isbn_claims}, {"ebook", "print"})
            self.assertEqual({claim.applicability_basis for claim in isbn_claims}, {"ebook", "paperback"})
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
            self.assertEqual(primary.local_reasons, ("no_opening_claims",))
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
            target_secret = root / "target.secret"
            target_secret.write_text("target-token", encoding="ascii")
            target_secret.chmod(0o600)
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
            self.assertEqual(server.requests, 1)


if __name__ == "__main__":
    unittest.main()
