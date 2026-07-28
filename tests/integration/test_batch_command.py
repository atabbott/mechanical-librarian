from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.support.epub_packages import opening_no_applicable


class BatchCommandTests(unittest.TestCase):
    def test_installed_preview_inspect_and_run_two_reports(self) -> None:
        executable = shutil.which("mechanical-librarian")
        self.assertIsNotNone(executable)
        class Handler(BaseHTTPRequestHandler):
            stopped = False
            def do_GET(self) -> None:
                if self.headers.get("Authorization") != "Bearer token" or Handler.stopped and self.path.startswith("/api/items/"):
                    self.send_response(403); self.end_headers(); return
                route = self.path.split("?", 1)[0]
                if route == "/api/libraries": value = {"libraries":[{"id":"library-id","mediaType":"book"}]}
                elif route.endswith("/items"): value = {"total":2,"results":[{"id":"item-1","libraryId":"library-id","updatedAt":1},{"id":"item-2","libraryId":"library-id","updatedAt":1}]}
                else:
                    item = route.rsplit("/", 1)[-1]; value = {"id":item,"libraryId":"library-id","updatedAt":1,"isInvalid":False,"isMissing":False,"mediaType":"book","media":{"metadata":{"title":item,"authors":[]},"ebookFile":{"ebookFormat":"epub","ino":item,"metadata":{"relPath":item+".epub","path":"/library/"+item+".epub","size":sizes[item]}}}}
                body=json.dumps(value).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory); media=root/"media"; media.mkdir(mode=0o700); sizes={}
                for item in ("item-1","item-2"):
                    data=opening_no_applicable(); (media/(item+".epub")).write_bytes(data); sizes[item]=len(data)
                secret=root/"token"; secret.write_text("token"); secret.chmod(0o600); artifacts=root/"artifacts"; artifacts.mkdir(mode=0o700)
                config=root/"config.json"; config.write_text(json.dumps({"schema_version":"config-v5","connections":[{"name":"primary","adapter":"audiobookshelf","endpoint":f"http://127.0.0.1:{server.server_port}","secret_file":str(secret)}],"libraries":[{"name":"books","connection":"primary","library_id":"library-id","mount":"books","provider_connections":[]}],"mounts":[{"name":"books","container_path":str(media),"service_path":"/library","read_only":True}],"providers":[]}))
                catalogue=root/"catalogue.sqlite"; manifest=root/"batch.json"; reports=root/"reports.sqlite"; base=[executable]
                self.assertEqual(subprocess.run([*base,"catalogue","--config",str(config),"--database",str(catalogue),"refresh"],capture_output=True,text=True,timeout=30).returncode,0)
                preview=subprocess.run([*base,"batch","preview","--config",str(config),"--catalogue",str(catalogue),"--manifest",str(manifest),"--all-eligible"],capture_output=True,text=True,timeout=30); self.assertEqual(preview.returncode,0,preview.stderr); identity=json.loads(preview.stdout)["id"]
                self.assertEqual(subprocess.run([*base,"batch","inspect","--manifest",str(manifest)],capture_output=True,text=True,timeout=30).returncode,0)
                explicit=root/"explicit.json"
                selected=subprocess.run([*base,"batch","preview","--config",str(config),"--catalogue",str(catalogue),"--manifest",str(explicit),"--select","primary","books","library-id","item-1","--select","primary","books","library-id","item-2"],capture_output=True,text=True,timeout=30)
                self.assertEqual((selected.returncode,json.loads(selected.stdout)["entries"]),(0,2),selected.stderr)
                run=subprocess.run([*base,"batch","run","--config",str(config),"--catalogue",str(catalogue),"--manifest",str(manifest),"--manifest-id",identity,"--database",str(reports),"--artifact-store",str(artifacts)],capture_output=True,text=True,timeout=60)
                lines=[json.loads(line) for line in run.stdout.splitlines()]; self.assertEqual(run.returncode,0,run.stderr); self.assertEqual([line["item"] for line in lines[:-1]],["item-1","item-2"]); self.assertIn("summary",lines[-1]); self.assertTrue(reports.exists())
        finally:
            server.shutdown(); thread.join(); server.server_close()
