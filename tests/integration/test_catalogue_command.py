from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _detail(library: str, item: str, *, audio: bool) -> dict[str, object]:
    media: dict[str, object] = {"metadata": {"title": item, "authors": [{"name": "Ada"}]}, "ebookFile": {"ebookFormat": "epub", "ino": item, "metadata": {"relPath": f"{item}.epub", "size": 1}}}
    if audio:
        media["audioFiles"] = [{"ino": "audio"}]
    return {"id": item, "libraryId": library, "updatedAt": 1, "media": media}


class CatalogueCommandTests(unittest.TestCase):
    def test_installed_cli_refreshes_separate_and_mixed_book_libraries_without_list_paths(self) -> None:
        self.assertIsNotNone(shutil.which("mechanical-librarian"))
        details = {("one", "one-item"): _detail("one", "one-item", audio=True), ("two", "two-item"): _detail("two", "two-item", audio=False)}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.headers.get("Authorization") != "Bearer token": self.send_response(403); self.end_headers(); return
                route = self.path.split("?", 1)[0]
                if route == "/api/libraries": value = {"libraries": [{"id": "one", "mediaType": "book"}, {"id": "two", "mediaType": "book"}, {"id": "audio", "mediaType": "audio"}]}
                elif route.endswith("/items"):
                    library = route.split("/")[3]; item = f"{library}-item"; value = {"total": 1, "results": [{"id": item, "libraryId": library, "updatedAt": 1}]}
                else:
                    item = route.split("/")[-1]; value = next(value for (library, candidate), value in details.items() if candidate == item)
                body = json.dumps(value).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args: object) -> None: pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); secret = root / "token"; secret.write_text("token"); secret.chmod(0o600); database = root / "catalogue.sqlite"
                config = root / "config.json"; config.write_text(json.dumps({"schema_version":"config-v5", "connections":[{"name":"primary","adapter":"audiobookshelf","endpoint":f"http://127.0.0.1:{server.server_port}","secret_file":str(secret)}], "libraries":[{"name":"first","connection":"primary","library_id":"one","mount":None,"provider_connections":[]},{"name":"second","connection":"primary","library_id":"two","mount":None,"provider_connections":[]}], "mounts":[],"providers":[]}))
                base = [shutil.which("mechanical-librarian"), "catalogue", "--config", str(config), "--database", str(database)]
                refresh = subprocess.run([*base, "refresh"], capture_output=True, text=True, timeout=20)
                listed = subprocess.run([*base, "list"], capture_output=True, text=True, timeout=20)
                ebooks = subprocess.run([*base, "list", "--filter", "ebook"], capture_output=True, text=True, timeout=20)
                audio = subprocess.run([*base, "list", "--filter", "audio"], capture_output=True, text=True, timeout=20)
                exact = subprocess.run([*base, "get", "--connection", "primary", "--library", "first", "--library-id", "one", "--item", "one-item"], capture_output=True, text=True, timeout=20)
                self.assertEqual(refresh.returncode, 0, refresh.stderr); rows = json.loads(listed.stdout); self.assertEqual(len(rows), 2); self.assertNotIn("service_path", listed.stdout); self.assertEqual({row["library_id"] for row in rows}, {"one", "two"}); self.assertEqual(next(row for row in rows if row["library_id"] == "one")["ebook"], True); self.assertEqual(next(row for row in rows if row["library_id"] == "one")["audio"], True); self.assertEqual(len(json.loads(ebooks.stdout)), 2); self.assertEqual([row["library_id"] for row in json.loads(audio.stdout)], ["one"]); self.assertFalse(rows[0]["eligible"]); self.assertIn("service_path", json.loads(exact.stdout)[0]); self.assertEqual(exact.returncode, 0, exact.stderr)
        finally:
            server.shutdown(); thread.join(); server.server_close()
