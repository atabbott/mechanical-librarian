#!/usr/bin/env python3
"""Qualify F05a/F05b against a disposable digest-pinned Audiobookshelf container."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mechanical_librarian.adapters.library_services.audiobookshelf_inventory import (
    _file_descriptor,
    _file_identity,
)


IMAGE = (
    "ghcr.io/advplyr/audiobookshelf@"
    "sha256:1eef6716183c52abafe5405e7d6be8390248ecd59c7488c44af871757ac8fc4d"
)
ROOT = Path(__file__).parents[1]


def request(url: str, method: str = "GET", payload=None, token=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    operation = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(operation, timeout=15) as response:
        body = response.read()
        if not body:
            value = None
        else:
            try:
                value = json.loads(body)
            except (UnicodeError, ValueError):
                value = None
        return response.status, value


def wait_for(
    base: str,
    token: str | None = None,
    library: str | None = None,
    *,
    expected_total: int = 2,
) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if library is None:
                _, value = request(base + "/status")
                if isinstance(value, dict):
                    return value
            else:
                _, value = request(
                    base + f"/api/libraries/{library}/items?limit=10&page=0",
                    token=token,
                )
                if isinstance(value, dict) and value.get("total") == expected_total:
                    return value
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError("disposable Audiobookshelf did not reach expected state")


class TraceProxy(BaseHTTPRequestHandler):
    upstream = ""
    trace: list[tuple[str, str, bool, bool]] = []
    scenario = "pass"
    page_zero_count = 0
    page_zero_payload: dict | None = None

    def _forward(self) -> None:
        type(self).trace.append(
            (
                self.command,
                self.path,
                bool(self.headers.get("Authorization")),
                bool(self.headers.get("Cookie")),
            )
        )
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if self.headers.get("Authorization"):
            headers["Authorization"] = self.headers["Authorization"]
        upstream = urllib.request.Request(
            type(self).upstream + self.path,
            data=b"" if self.command == "POST" else None,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(upstream, timeout=15) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as error:
            payload = error.read()
            status = error.code
            content_type = "application/json"
        route, _, query = self.path.partition("?")
        if (
            self.command == "GET"
            and route.startswith("/api/items/")
            and type(self).scenario == "partial"
        ):
            status = 503
            payload = b'{"error":"synthetic partial item"}'
            content_type = "application/json"
        if (
            self.command == "GET"
            and route == "/status"
            and type(self).scenario == "rate"
        ):
            status = 429
            payload = b'{"error":"synthetic rate limit"}'
            content_type = "application/json"
        if (
            self.command == "GET"
            and route.startswith("/api/items/")
            and type(self).scenario == "transport"
        ):
            partial = payload[: max(1, len(payload) // 2)]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload) + 10))
            self.end_headers()
            self.wfile.write(partial)
            self.close_connection = True
            return
        if self.command == "GET" and route.endswith("/items") and status == 200:
            parameters = urllib.parse.parse_qs(query, keep_blank_values=True)
            page = parameters.get("page", [""])[0]
            value = json.loads(payload)
            if page == "0":
                if type(self).scenario in {"repeated", "cyclic"}:
                    value["total"] = 2
                type(self).page_zero_count += 1
                type(self).page_zero_payload = value
            if type(self).scenario == "partial" and page == "1":
                status = 503
                value = {"error": "synthetic partial page"}
            elif type(self).scenario == "repeated" and page == "1":
                value["page"] = 0
            elif (
                type(self).scenario == "cyclic"
                and page == "1"
                and type(self).page_zero_payload is not None
            ):
                value["results"] = type(self).page_zero_payload["results"]
            elif type(self).scenario == "malformed" and page == "0":
                value["page"] = False
            elif (
                type(self).scenario == "unstable"
                and page == "0"
                and type(self).page_zero_count == 2
            ):
                value = {"results": [], "page": 0, "limit": 1, "total": 0}
            payload = json.dumps(value, separators=(",", ":")).encode()
            content_type = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if type(self).scenario == "rate" and status == 429:
            self.send_header("Retry-After", "2")
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _forward
    do_POST = _forward

    def log_message(self, _format, *_args) -> None:
        return


def make_media(root: Path) -> None:
    audio = root / "audio" / "Synthetic Audiobook"
    ebook = root / "ebook" / "Synthetic Ebook"
    audio.mkdir(parents=True)
    ebook.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-metadata",
            "title=Synthetic Audiobook",
            "-metadata",
            "artist=Example Author",
            "-c:a",
            "aac",
            str(audio / "Synthetic Audiobook.m4b"),
        ],
        check=True,
    )
    with zipfile.ZipFile(ebook / "Synthetic Ebook.epub", "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "book.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:uuid:01234567-89ab-4def-8123-456789abcdef</dc:identifier><dc:title>Synthetic Ebook</dc:title><dc:language>en</dc:language></metadata><manifest/></package>',
        )


def run_inventory(configuration: Path, database: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "mechanical-librarian"),
            "audiobookshelf-inventory",
            "--config",
            str(configuration),
            "--database",
            str(database),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def inspect_inventory(
    configuration: Path, database: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "mechanical-librarian"),
            "inventory-inspect",
            "--config",
            str(configuration),
            "--database",
            str(database),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def run_poll(
    configuration: Path, database: Path, trigger: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "mechanical-librarian"),
            "audiobookshelf-poll",
            "--trigger",
            trigger,
            "--config",
            str(configuration),
            "--database",
            str(database),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def inspect_poll(database: Path, poll_id: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "mechanical-librarian"),
            "inventory-poll-inspect",
            "--poll-id",
            poll_id,
            "--database",
            str(database),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def set_scenario(name: str) -> None:
    TraceProxy.scenario = name
    TraceProxy.page_zero_count = 0
    TraceProxy.page_zero_payload = None


def expanded_file_facts(detail: dict[str, object], *, audio: bool) -> dict[str, object]:
    media = detail.get("media", {})
    if not isinstance(media, dict):
        return {}
    if audio:
        files = media.get("audioFiles", [])
        file = files[0] if isinstance(files, list) and files else None
        top_level = ("ino", "index", "format", "duration", "codec", "bitRate")
    else:
        file = media.get("ebookFile")
        top_level = ("ino", "ebookFormat")
    if not isinstance(file, dict):
        return {}
    facts = {key: file[key] for key in top_level if key in file}
    metadata = file.get("metadata")
    if isinstance(metadata, dict):
        facts["metadata"] = {
            key: metadata[key]
            for key in ("ext", "size", "mtimeMs", "ctimeMs", "birthtimeMs")
            if key in metadata
        }
    return facts


def wait_for_file_change(
    base: str,
    token: str,
    item: str,
    prior: dict[str, object],
    *,
    audio: bool,
) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        _, detail = request(base + f"/api/items/{item}?expanded=1", token=token)
        current = expanded_file_facts(detail, audio=audio)
        if current and current != prior:
            return
        time.sleep(0.25)
    raise RuntimeError("disposable Audiobookshelf did not observe the changed item")


def _allowed(entry: tuple[str, str, bool, bool], library_id: str) -> bool:
    method, path, authenticated, cookie = entry
    if cookie:
        return False
    if method == "POST":
        return path == "/api/authorize" and authenticated
    if method != "GET":
        return False
    route, _, query = path.partition("?")
    if route == "/status":
        return not query and not authenticated
    if route == "/api/libraries":
        return not query and authenticated
    if route == f"/api/libraries/{library_id}/items":
        parts = dict(item.split("=", 1) for item in query.split("&"))
        page = parts.pop("page", None)
        return (
            authenticated
            and page in {"0", "1"}
            and parts
            == {
                "limit": "1",
                "sort": "addedAt",
                "desc": "0",
                "minified": "1",
                "collapseseries": "0",
            }
        )
    if route.startswith("/api/items/"):
        return authenticated and query == "expanded=1"
    return False


def main() -> int:
    name = "ml-f05a-" + uuid.uuid4().hex[:10]
    proxy = None
    with tempfile.TemporaryDirectory(prefix="ml-f05a-") as directory:
        root = Path(directory)
        for child in ("config", "metadata", "media"):
            (root / child).mkdir()
        make_media(root / "media")
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    name,
                    "-p",
                    "127.0.0.1::80",
                    "-v",
                    f"{root / 'config'}:/config",
                    "-v",
                    f"{root / 'metadata'}:/metadata",
                    "-v",
                    f"{root / 'media'}:/media:ro",
                    IMAGE,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            port = (
                subprocess.run(
                    ["docker", "port", name, "80/tcp"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                .stdout.strip()
                .rsplit(":", 1)[1]
            )
            base = f"http://127.0.0.1:{port}"
            if wait_for(base).get("serverVersion") != "2.35.1":
                raise RuntimeError("disposable Audiobookshelf version mismatch")
            request(
                base + "/init",
                "POST",
                {
                    "newRoot": {
                        "username": "fixture-root",
                        "password": "fixture-password",
                    }
                },
            )
            _, login = request(
                base + "/login",
                "POST",
                {"username": "fixture-root", "password": "fixture-password"},
            )
            token = login["user"]["accessToken"]
            user_id = login["user"]["id"]
            _, library = request(
                base + "/api/libraries",
                "POST",
                {
                    "name": "Synthetic mixed books",
                    "mediaType": "book",
                    "folders": [{"path": "/media"}],
                },
                token,
            )
            library_id = library["id"]
            request(base + f"/api/libraries/{library_id}/scan", "POST", token=token)
            page = wait_for(base, token, library_id)
            if len(page.get("results", [])) != 2:
                raise RuntimeError("disposable mixed library was not scanned")
            _, created = request(
                base + "/api/api-keys",
                "POST",
                {"name": "f05a-disposable", "userId": user_id, "isActive": True},
                token,
            )
            api_key = created["apiKey"]["apiKey"]

            TraceProxy.upstream = base
            TraceProxy.trace = []
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), TraceProxy)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            secret = root / "api-key"
            secret.write_text(api_key + "\n")
            secret.chmod(0o600)
            configuration = root / "config-v1.json"
            configuration.write_text(
                json.dumps(
                    {
                        "schema_version": "config-v1",
                        "instance_name": "f05a-disposable",
                        "connections": [
                            {
                                "id": "abs-disposable",
                                "adapter": "audiobookshelf",
                                "endpoint": f"http://127.0.0.1:{proxy.server_port}",
                                "secret_reference": str(secret),
                                "enabled_libraries": [library_id],
                                "capability_overrides": [],
                            }
                        ],
                        "inventory": {
                            "page_size": 1,
                            "stability_delay_seconds": 0,
                            "initial_backoff_seconds": 1,
                            "max_backoff_seconds": 2,
                        },
                    }
                )
            )
            database = root / "state.sqlite"
            _, before_libraries = request(base + "/api/libraries", token=token)
            _, before_items = request(
                base + f"/api/libraries/{library_id}/items?limit=10&page=0",
                token=token,
            )
            completed = run_inventory(configuration, database)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"F05a qualification failed: {completed.stderr.strip()} trace={TraceProxy.trace}"
                )
            disallowed = [
                entry for entry in TraceProxy.trace if not _allowed(entry, library_id)
            ]
            if disallowed:
                raise RuntimeError("F05a runtime exceeded its route/method contract")
            if api_key in completed.stdout + completed.stderr:
                raise RuntimeError("F05a runtime exposed credential material")
            _, after_libraries = request(base + "/api/libraries", token=token)
            _, after_items = request(
                base + f"/api/libraries/{library_id}/items?limit=10&page=0",
                token=token,
            )
            if before_libraries != after_libraries or before_items != after_items:
                raise RuntimeError("F05a read changed target inventory state")

            duplicate = run_inventory(configuration, database)
            if duplicate.returncode != 0:
                raise RuntimeError("F05a restart/duplicate scan failed")

            poll = run_poll(configuration, database, "qualification-poll")
            if poll.returncode != 0 or "state=succeeded" not in poll.stdout:
                raise RuntimeError("F05b durable poll failed")
            poll_id = next(
                (
                    part.split("=", 1)[1]
                    for part in poll.stdout.split()
                    if part.startswith("poll=")
                ),
                None,
            )
            if not isinstance(poll_id, str):
                raise RuntimeError("F05b poll evidence was unavailable")
            trace_count = len(TraceProxy.trace)
            poll_duplicate = run_poll(
                configuration, database, "qualification-poll"
            )
            if poll_duplicate.returncode != 0 or len(TraceProxy.trace) != trace_count:
                raise RuntimeError("F05b duplicate poll performed target I/O")
            poll_inspection = inspect_poll(database, poll_id)
            if (
                poll_inspection.returncode != 0
                or "state=succeeded" not in poll_inspection.stdout
            ):
                raise RuntimeError("F05b credential-free poll inspection failed")

            audiobook_item = next(
                (
                    item
                    for item in page["results"]
                    if item.get("media", {}).get("metadata", {}).get("title")
                    == "Synthetic Audiobook"
                ),
                None,
            )
            if not isinstance(audiobook_item, dict) or not isinstance(
                audiobook_item.get("id"), str
            ):
                raise RuntimeError("disposable audiobook item was not identifiable")
            _, audio_detail = request(
                base + f"/api/items/{audiobook_item['id']}?expanded=1", token=token
            )
            prior_file = expanded_file_facts(audio_detail, audio=True)
            if not prior_file:
                raise RuntimeError("disposable audiobook file identity was unavailable")
            audio_path = (
                root
                / "media"
                / "audio"
                / "Synthetic Audiobook"
                / "Synthetic Audiobook.m4b"
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=550:duration=2",
                    "-metadata",
                    "title=Synthetic Audiobook",
                    "-metadata",
                    "artist=Example Author",
                    "-c:a",
                    "aac",
                    str(audio_path),
                ],
                check=True,
            )
            request(
                base + f"/api/libraries/{library_id}/scan?force=1",
                "POST",
                token=token,
            )
            wait_for_file_change(
                base, token, audiobook_item["id"], prior_file, audio=True
            )
            changed = run_inventory(configuration, database)
            if changed.returncode != 0:
                raise RuntimeError("F05a changed-observation reconciliation failed")

            ebook_item = next(
                (
                    item
                    for item in page["results"]
                    if item.get("media", {}).get("metadata", {}).get("title")
                    == "Synthetic Ebook"
                ),
                None,
            )
            if not isinstance(ebook_item, dict) or not isinstance(
                ebook_item.get("id"), str
            ):
                raise RuntimeError("disposable ebook item was not identifiable")
            _, ebook_detail = request(
                base + f"/api/items/{ebook_item['id']}?expanded=1", token=token
            )
            ebook_file = ebook_detail.get("media", {}).get("ebookFile")
            if not isinstance(ebook_file, dict) or not isinstance(
                ebook_file.get("metadata"), dict
            ):
                raise RuntimeError("disposable ebook file shape was unavailable")
            ebook_metadata = ebook_file["metadata"]
            if "ebookFormat" not in ebook_file or any(
                key not in ebook_metadata for key in ("ext", "path", "size", "mtimeMs")
            ):
                raise RuntimeError("disposable ebook file contract was incomplete")
            if type(ebook_metadata["size"]) not in {int, float} or type(
                ebook_metadata["mtimeMs"]
            ) not in {int, float}:
                raise RuntimeError("disposable ebook stat contract was malformed")
            changed_ebook_file = copy.deepcopy(ebook_file)
            changed_ebook_file["metadata"]["size"] += 1
            changed_ebook_file["metadata"]["mtimeMs"] += 1
            original_identity = _file_identity(ebook_file)
            changed_identity = _file_identity(changed_ebook_file)
            original_descriptor = _file_descriptor(ebook_file)
            changed_descriptor = _file_descriptor(changed_ebook_file)
            if (
                original_identity == changed_identity
                or original_descriptor == changed_descriptor
            ):
                raise RuntimeError(
                    "disposable ebook nested facts did not affect identity"
                )
            safe_diagnostic = " ".join(
                (
                    original_identity,
                    changed_identity,
                    original_descriptor,
                    changed_descriptor,
                )
            )
            for private_key in ("path", "relPath"):
                private_value = ebook_metadata.get(private_key)
                if isinstance(private_value, str) and private_value in safe_diagnostic:
                    raise RuntimeError("disposable ebook private path escaped hashing")
            ebook = (
                root / "media" / "ebook" / "Synthetic Ebook" / "Synthetic Ebook.epub"
            )
            ebook.unlink()
            request(
                base + f"/api/items/{ebook_item['id']}",
                "DELETE",
                token=token,
            )
            wait_for(base, token, library_id, expected_total=1)
            disappeared = run_inventory(configuration, database)
            if disappeared.returncode != 0:
                raise RuntimeError("F05a disappearance reconciliation failed")
            stable_absence = run_inventory(configuration, database)
            if stable_absence.returncode != 0:
                raise RuntimeError("F05a retained-removal reconciliation failed")
            inspected = inspect_inventory(configuration, database)
            if (
                inspected.returncode != 0
                or "items=2 active=1 removed=1" not in inspected.stdout
                or "history=4" not in inspected.stdout
            ):
                raise RuntimeError("F05a credential-free inspection failed")

            def advance_poll_generation() -> None:
                config_value = json.loads(configuration.read_text())
                config_value["inventory"]["poll_generation"] = (
                    config_value["inventory"].get("poll_generation", 0) + 1
                )
                configuration.write_text(json.dumps(config_value))

            for scenario, expected_state in (
                ("repeated", "incomplete"),
                ("cyclic", "unstable"),
                ("malformed", "incomplete"),
                ("unstable", "unstable"),
            ):
                advance_poll_generation()
                set_scenario(scenario)
                failed = run_poll(
                    configuration, database, f"qualification-{scenario}"
                )
                failed_output = failed.stdout + failed.stderr
                if failed.returncode == 0:
                    raise RuntimeError(
                        f"F05b {scenario} sequence unexpectedly completed"
                    )
                if (
                    scenario == "malformed"
                    and "compatibility=contract_drift" not in failed_output
                ):
                    raise RuntimeError(
                        "F05b malformed sequence did not record contract drift"
                    )
                preserved = inspect_inventory(configuration, database)
                preserved_output = preserved.stdout + preserved.stderr
                if (
                    preserved.returncode == 0
                    or f"state={expected_state}" not in preserved_output
                    or "items=2 active=1 removed=1" not in preserved_output
                    or "history=4" not in preserved_output
                ):
                    raise RuntimeError(
                        f"F05b {scenario} sequence did not preserve durable current state: "
                        f"rc={preserved.returncode} out={preserved.stdout.strip()} "
                        f"err={preserved.stderr.strip()}"
                    )

            for scenario in ("partial", "transport", "rate"):
                trigger = f"qualification-{scenario}-recovery"
                advance_poll_generation()
                set_scenario(scenario)
                failed = run_poll(configuration, database, trigger)
                failed_output = failed.stdout + failed.stderr
                if failed.returncode == 0 or "state=retry_wait" not in failed_output:
                    raise RuntimeError(
                        f"F05b {scenario} failure did not persist retry-wait: "
                        f"rc={failed.returncode} out={failed.stdout.strip()} "
                        f"err={failed.stderr.strip()}"
                    )
                trace_count = len(TraceProxy.trace)
                deferred = run_poll(configuration, database, trigger)
                if deferred.returncode == 0 or len(TraceProxy.trace) != trace_count:
                    raise RuntimeError(
                        f"F05b {scenario} early redelivery performed target I/O"
                    )
                set_scenario("pass")
                time.sleep(2.1 if scenario == "rate" else 1.1)
                repaired = run_poll(configuration, database, trigger)
                if repaired.returncode != 0 or "state=succeeded" not in repaired.stdout:
                    raise RuntimeError(
                        f"F05b {scenario} persisted retry did not recover"
                    )

            original_secret = secret.read_text()
            advance_poll_generation()
            secret.write_text("invalid-disposable-key\n")
            secret.chmod(0o600)
            authentication = run_poll(
                configuration, database, "qualification-authentication-loss"
            )
            authentication_output = authentication.stdout + authentication.stderr
            if (
                authentication.returncode == 0
                or "state=blocked" not in authentication_output
                or "compatibility=unknown" not in authentication_output
            ):
                raise RuntimeError("F05b authentication loss did not block safely")
            secret.write_text(original_secret)
            secret.chmod(0o600)
            advance_poll_generation()
            restored = run_poll(
                configuration, database, "qualification-authentication-restored"
            )
            if restored.returncode != 0 or "state=succeeded" not in restored.stdout:
                raise RuntimeError(
                    "F05b governing credential repair did not restore polling"
                )
            set_scenario("pass")
            if any(not _allowed(entry, library_id) for entry in TraceProxy.trace):
                raise RuntimeError("F05b failure cohort exceeded route/method contract")
            print(completed.stdout, end="")
            print(
                "qualification=abs-2.35.1 mixed=audio,ebook pages=multi-pass "
                "restart=duplicate,persisted-retry poll=durable,fenced,deduplicated update=audio ebook=nested-contract "
                "disappearance=removed,retained "
                "failures=authentication,rate,transport,partial,repeated,cyclic,malformed,unstable "
                "repair=governing-change,backoff authority=get-only"
            )
            return 0
        finally:
            if proxy is not None:
                proxy.shutdown()
                proxy.server_close()
            subprocess.run(
                ["docker", "rm", "-f", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    raise SystemExit(main())
