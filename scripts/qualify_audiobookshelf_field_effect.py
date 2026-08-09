#!/usr/bin/env python3
"""Run the disposable, field-local Audiobookshelf write qualification.

This qualification owns every Docker resource it creates. Continuous qualification
uses a pre-frozen private operator batch of six to eight rights-verified EPUBs for
the required destination coverage; one generated EPUB remains a diagnostic control.
It always creates a
first-run bootstrap account and isolated API token and never touches an existing
library.
"""

from __future__ import annotations

import argparse
import io
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
INSTALLED_ROOT = os.environ.get("MECHANICAL_LIBRARIAN_QUALIFICATION_INSTALL")
IMPORT_ROOT = Path(INSTALLED_ROOT).resolve() if INSTALLED_ROOT else ROOT / "src"
if str(IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(IMPORT_ROOT))

import httpx
import mechanical_librarian.bootstrap.composition as composition
from argon2 import PasswordHasher
from PIL import Image
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness, FakeDependencyCurrentness,
    FakeForwardReevaluation,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_field_effect import (
    AudiobookshelfFieldEffectCapabilities,
    AudiobookshelfFieldEffectTarget,
)
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.private_recovery import PrivateCoverRecoveryStore
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.cover_evidence import CoverArtifact
from mechanical_librarian.domain.field_effect import (
    EffectAuthority, EffectConsent, FieldEffectError, FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.infrastructure.security.credentials import target_client_token
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.bootstrap.composition import (
    compose_continuous_guarded_application,
    create_review_pipeline,
)
from mechanical_librarian.interfaces.web.app import create_review_app


IMAGES = {
    "2.26.0": "ghcr.io/advplyr/audiobookshelf:2.26.0@sha256:16685fbba37a21d403f5390b907d286e15b3086d26527269b4ad785f71f571e5",
    "2.36.0": "ghcr.io/advplyr/audiobookshelf:2.36.0@sha256:180acad33d69c99ed208676465d8edcb268fa46967735579a7810859885b1a8e",
}
OWNERSHIP_LABEL = "org.mechanical-librarian.qualification-run"


class QualificationError(RuntimeError):
    """A fixed, secret-free qualification failure."""


def _png(red: int, green: int, blue: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (red, green, blue)).save(output, "PNG")
    return output.getvalue()


def _run(*command: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise QualificationError("qualification_command_failed")
    return result


def _request(method: str, base: str, path: str, *, token: str | None = None, payload: object | None = None) -> object:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_048_577)
    except urllib.error.HTTPError as error:
        raw = error.read(1_048_577)
        if len(raw) > 1_048_576:
            raise QualificationError("response_too_large") from None
        return {"status": error.code}
    if len(raw) > 1_048_576:
        raise QualificationError("response_too_large")
    if not raw:
        return {}
    text = raw.decode("utf-8", "strict")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"status": "non_json"}


def _mapping(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise QualificationError(name)
    return value


def _string(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise QualificationError(name)
    return value


def _owned(name: str, owner: str) -> bool:
    completed = subprocess.run(
        ("docker", "inspect", name, "--format", '{{ index .Config.Labels "' + OWNERSHIP_LABEL + '" }}'),
        check=False, capture_output=True, text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == owner


def _delete_key(base: str, root_token: str, key_id: str) -> None:
    try:
        _request("DELETE", base, "/api/api-keys/" + key_id, token=root_token)
    except (OSError, urllib.error.URLError, TimeoutError, QualificationError):
        pass


def _create_api_key(base: str, root_token: str, user_id: str, name: str) -> tuple[str, str]:
    response = _mapping(_request("POST", base, "/api/api-keys", token=root_token, payload={
        "name": name, "userId": user_id, "isActive": True, "expiresIn": 3600,
    }), "api_key_response_invalid")
    api_key = _mapping(response.get("apiKey"), "api_key_response_invalid")
    return _string(api_key.get("id"), "api_key_response_invalid"), _string(api_key.get("apiKey"), "api_key_response_invalid")


def _create_user(base: str, root_token: str, *, username: str, password: str,
                 library_ids: tuple[str, ...], update: bool, upload: bool) -> str:
    response = _mapping(_request("POST", base, "/api/users", token=root_token, payload={
        "username": username,
        "password": password,
        "type": "user",
        "isActive": True,
        "permissions": {
            "update": update,
            "upload": upload,
            "delete": False,
            "download": False,
            "createEreader": False,
            "accessAllLibraries": False,
            "accessAllTags": True,
            "accessExplicitContent": True,
            "selectedTagsNotAccessible": False,
        },
        "librariesAccessible": list(library_ids),
    }), "user_response_invalid")
    return _string(_mapping(response.get("user"), "user_response_invalid").get("id"), "user_response_invalid")


def _snapshot(source_sha: str, versions: list[str] | None = None,
              corpus: Path | None = None) -> None:
    """Run the immutable script against a wheel installed outside its snapshot."""
    archive = subprocess.run(
        ("git", "archive", "--format=tar", source_sha), cwd=ROOT,
        check=False, capture_output=True,
    )
    if archive.returncode != 0:
        raise QualificationError("source_snapshot_failed")
    with tempfile.TemporaryDirectory(prefix="mechanical-librarian-abs-source-") as temporary:
        snapshot = Path(temporary)
        try:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as contents:
                contents.extractall(snapshot, filter="data")
        except (tarfile.TarError, OSError) as error:
            raise QualificationError("source_snapshot_failed") from error
        wheel_dir = snapshot / ".qualification-wheel"
        installed = snapshot / ".qualification-installed"
        _run("uv", "build", "--wheel", "--out-dir", str(wheel_dir), str(snapshot))
        wheels = tuple(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise QualificationError("installed_artifact_invalid")
        _run("uv", "pip", "install", "--target", str(installed), "--no-deps", str(wheels[0]))
        environment = os.environ.copy()
        environment["MECHANICAL_LIBRARIAN_QUALIFICATION_INSTALL"] = str(installed)
        environment.pop("PYTHONPATH", None)
        command = [sys.executable, str(snapshot / "scripts/qualify_audiobookshelf_field_effect.py"),
                   "--source-sha", source_sha, "--snapshot"]
        if corpus is not None:
            command.extend(("--corpus", str(corpus.resolve())))
        for version in versions or []:
            command.extend(("--version", version))
        result = subprocess.run(
            command,
            cwd=snapshot, check=False, env=environment,
        )
    if result.returncode != 0:
        raise QualificationError("archived_qualification_failed")


def _wait(base: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            status = _request("GET", base, "/status")
            if (type(status) is dict and status.get("app") == "audiobookshelf"
                    and type(status.get("serverVersion")) is str
                    and bool(status["serverVersion"])):
                return
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("disposable Audiobookshelf did not become ready")


def _epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", "<?xml version='1.0'?><container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles><rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>")
        archive.writestr("OEBPS/content.opf", "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf' xmlns:opf='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='book'><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='book'>9780306406157</dc:identifier><dc:title>Before</dc:title><dc:creator opf:role='aut'>Synthetic Author</dc:creator><dc:creator opf:role='aut'>Second Qualified Author</dc:creator><dc:language>fr</dc:language><dc:publisher>Qualification Press</dc:publisher><dc:date>2025-02-03</dc:date><meta property='dcterms:modified'>2026-01-01T00:00:00Z</meta></metadata><manifest><item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/><item id='chapter' href='chapter.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='chapter'/></spine></package>")
        archive.writestr("OEBPS/nav.xhtml", "<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><head><title>Contents</title></head><body><nav epub:type='toc' xmlns:epub='http://www.idpf.org/2007/ops'><ol><li><a href='chapter.xhtml'>Chapter</a></li></ol></nav></body></html>")
        archive.writestr("OEBPS/chapter.xhtml", "<?xml version='1.0'?><html xmlns='http://www.w3.org/1999/xhtml'><head><title>Chapter</title></head><body><p>Synthetic qualification content.</p></body></html>")


def _operator_epub(source: Path, destination: Path, index: int) -> tuple[str, str]:
    """Copy one real work with a private, deterministic qualification metadata overlay."""
    stem = f"9780306406{index:02d}"
    checksum = (10 - sum(
        int(value) * (1 if position % 2 == 0 else 3)
        for position, value in enumerate(stem)
    ) % 10) % 10
    isbn = stem + str(checksum)
    if index == 6:
        isbn = "9780306406157"
    title = (
        "Before" if index == 6 else
        "Before" if index == 1 else
        f"Operator qualification {index}"
    )
    with zipfile.ZipFile(source) as incoming:
        container = ET.fromstring(incoming.read("META-INF/container.xml"))
        rootfile = container.find(
            ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
        )
        if rootfile is None or type(rootfile.get("full-path")) is not str:
            raise QualificationError("operator_corpus_invalid")
        package_path = str(rootfile.get("full-path"))
        package = ET.fromstring(incoming.read(package_path))
        metadata = package.find("{http://www.idpf.org/2007/opf}metadata")
        if metadata is None:
            raise QualificationError("operator_corpus_invalid")
        for child in tuple(metadata):
            metadata.remove(child)
        manifest = package.find("{http://www.idpf.org/2007/opf}manifest")
        if manifest is not None:
            for item in manifest:
                properties = str(item.get("properties", "")).split()
                if "cover-image" in properties:
                    properties.remove("cover-image")
                    if properties:
                        item.set("properties", " ".join(properties))
                    else:
                        item.attrib.pop("properties", None)
        guide = package.find("{http://www.idpf.org/2007/opf}guide")
        if guide is not None:
            for reference in tuple(guide):
                if str(reference.get("type", "")).casefold() == "cover":
                    guide.remove(reference)
        dc = "{http://purl.org/dc/elements/1.1/}"
        identifier_id = package.get("unique-identifier") or "qualification-isbn"
        package.set("unique-identifier", identifier_id)
        ET.SubElement(metadata, dc + "identifier", {
            "id": identifier_id,
            "{http://www.idpf.org/2007/opf}scheme": "ISBN",
        }).text = isbn
        ET.SubElement(metadata, dc + "title").text = title
        for author in ("Synthetic Author", "Second Qualified Author"):
            ET.SubElement(metadata, dc + "creator", {
                "{http://www.idpf.org/2007/opf}role": "aut",
            }).text = author
        ET.SubElement(metadata, dc + "language").text = "fr"
        ET.SubElement(metadata, dc + "publisher").text = "Qualification Press"
        ET.SubElement(metadata, dc + "date").text = "2025-02-03"
        package_bytes = ET.tostring(package, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(destination, "w") as outgoing:
            for member in incoming.infolist():
                payload = package_bytes if member.filename == package_path else incoming.read(member)
                outgoing.writestr(member, payload)
    return isbn, title


def _plan(repository, *, item: str, destination: str, value: object,
          desired: object, observation, capabilities, clock: int,
          origin: str = "human") -> FieldEffectPlan:
    decision = hashlib.sha256(destination.encode("ascii")).hexdigest()
    authority = EffectAuthority(
        ("human_decision_" if origin == "human" else "automatic_decision_") + decision,
        "review_case_" + "b" * 64, "c" * 64, "fake_service", origin,
        "candidate",
        None if origin == "human" else "resolved_policy_" + "a" * 64,
        None if origin == "human" else "b" * 64,
    )
    consent = EffectConsent.create(connection="canary", library="books", destination=destination,
                                   envelope_identity=repository.field_effect_consent_envelope_identity("books"),
                                   granted_at=clock)
    try:
        provisional = FieldEffectPlan.create(connection="canary", library="books", work_id=item, destination=destination, pre_value=value, desired_value=desired, target_fingerprint="c" * 64, target_version=observation.version, dependency_digests=("d" * 64,), authority=authority, consent=consent, capability_digest="e" * 64, created_at=clock)
        plan = FieldEffectPlan.create(connection="canary", library="books", work_id=item, destination=destination, pre_value=value, desired_value=desired, target_fingerprint="c" * 64, target_version=observation.version, dependency_digests=("d" * 64,), authority=authority, consent=consent, capability_digest=capabilities.observe(provisional).digest, created_at=clock)
    except Exception as error:
        raise RuntimeError("synthetic field setup is invalid: " + destination) from error
    repository.record_field_effect(FieldEffectOperation(plan, updated_at=clock))
    return plan


def _value_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _apply(service, repository, target, capabilities, writes: list[str], recovery,
           item: str, destination: str, desired: object, clock: int,
           origin: str = "human") -> dict[str, object]:
    placeholder = type("Plan", (), {"work_id": item, "destination": destination})()
    try:
        before = target.read(placeholder)
    except FieldEffectError as error:
        raise QualificationError("field_read_invalid_" + destination) from error
    if destination == "tags" and (type(before.value) is not list or not before.value or any(type(value) is not str for value in before.value)):
        raise RuntimeError("synthetic tag setup has unsupported response shape: " + type(before.value).__name__)
    plan = _plan(repository, item=item, destination=destination, value=before.value,
                 desired=desired, observation=before, capabilities=capabilities,
                 clock=clock, origin=origin)
    write_count = len(writes)
    operation = service.apply(plan.plan_id)
    after = target.read(plan)
    persisted = repository.get_field_effect(plan.plan_id)
    request_count = len(writes) - write_count
    if (request_count != 1 or after.value != desired or operation.state != "verified"
            or persisted != operation):
        raise QualificationError("guarded_write_evidence_invalid")
    recovery_state = "not_applicable"
    if destination == "cover":
        if not recovery.capture_verified or not recovery.cleanup_verified or recovery.exists(plan.plan_id):
            raise QualificationError("cover_recovery_evidence_invalid")
        recovery_state = "captured_and_cleaned"
    audit_documents = repository.field_effect_event_documents(plan.plan_id)
    audit = [json.loads(document) for document in audit_documents]
    if ([event["kind"] for event in audit] != ["planned", "claimed", "applying", "verified"]
            or audit[-1]["state"] != persisted.state):
        raise QualificationError("audit_sequence_evidence_invalid")
    return {
        "destination": destination,
        "origin": origin,
        "state": persisted.state,
        "failure": persisted.failure or "none",
        "request_count": request_count,
        "before_digest": _value_digest(before.value),
        "after_digest": _value_digest(after.value),
        "audit_digest": _value_digest(audit),
        "audit_event_count": len(audit_documents),
        "recovery": recovery_state,
    }


def _stable_cover_observation(target, item: str) -> None:
    """Wait only for the disposable service's prior write to become observable."""
    placeholder = type("Plan", (), {"work_id": item, "destination": "cover"})()
    deadline = time.monotonic() + 20
    previous = None
    while time.monotonic() < deadline:
        current = target.read(placeholder)
        identity = (current.value, current.version)
        if identity == previous:
            return
        previous = identity
        time.sleep(0.2)
    raise QualificationError("cover_initialization_did_not_settle")


def _raw_cover(base: str, token: str, item: str, expected: bytes) -> bytes:
    response = httpx.get(base + "/api/items/" + item + "/cover", params={"raw": "1"}, headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"}, timeout=20)
    if response.status_code != 200 or hashlib.sha256(response.content).digest() != hashlib.sha256(expected).digest():
        raise QualificationError("cover_recovery_preimage_invalid")
    return response.content


class _ObservedRecovery:
    def __init__(self, delegate: PrivateCoverRecoveryStore) -> None:
        self._delegate = delegate
        self.capture_verified = False
        self.cleanup_verified = False

    def capture(self, plan: FieldEffectPlan) -> None:
        self._delegate.capture(plan)
        self.capture_verified = self._delegate.exists(plan.plan_id)

    def remove(self, plan_id: str) -> None:
        existed = self._delegate.exists(plan_id)
        self._delegate.remove(plan_id)
        self.cleanup_verified = existed and not self._delegate.exists(plan_id)

    def exists(self, plan_id: str) -> bool:
        return self._delegate.exists(plan_id)


def _latest_stable() -> str:
    request = urllib.request.Request(
        "https://api.github.com/repos/advplyr/audiobookshelf/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "mechanical-librarian-qualification"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_048_577)
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise QualificationError("latest_stable_unavailable") from error
    if len(raw) > 1_048_576:
        raise QualificationError("latest_stable_invalid")
    try:
        release = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise QualificationError("latest_stable_invalid") from error
    if (type(release) is not dict or release.get("draft") is not False
            or release.get("prerelease") is not False):
        raise QualificationError("latest_stable_invalid")
    tag = release.get("tag_name")
    if type(tag) is not str or not tag.startswith("v") or tag[1:] not in IMAGES:
        raise QualificationError("latest_stable_not_pinned")
    return tag[1:]


def _corpus_sources(corpus: Path | None) -> tuple[Path, ...]:
    if corpus is None:
        return ()
    if not corpus.is_dir():
        raise QualificationError("operator_corpus_invalid")
    sources = tuple(sorted(corpus.glob("*.epub")))
    if not 6 <= len(sources) <= 8 or any(not source.is_file() for source in sources):
        raise QualificationError("operator_corpus_invalid")
    for source in sources:
        try:
            with zipfile.ZipFile(source) as archive:
                if archive.testzip() is not None or "META-INF/container.xml" not in archive.namelist():
                    raise QualificationError("operator_corpus_invalid")
        except (OSError, zipfile.BadZipFile):
            raise QualificationError("operator_corpus_invalid") from None
    return sources


def _wait_for_preparation(
    client: TestClient, library_count: int, *, allow_failures: bool = False,
) -> dict[str, object]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        response = client.get("/api/v1/review-preparation")
        if response.status_code == 503:
            time.sleep(0.25)
            continue
        if response.status_code != 200:
            raise QualificationError("installed_preparation_unavailable")
        state = _mapping(response.json(), "installed_preparation_invalid")
        libraries = state.get("libraries")
        if (type(libraries) is list and len(libraries) == library_count
                and not state.get("active")
                and all(type(value) is dict and value.get("state") in {
                    "completed", "completed_with_failures"
                } for value in libraries)):
            if any(value.get("state") != "completed" for value in libraries):
                if allow_failures:
                    return state
                summary = ",".join(
                    f"{value.get('library')}:{value.get('ready')}:{value.get('blocked')}"
                    for value in libraries
                )
                raise QualificationError("installed_preparation_failed:" + summary)
            return state
        time.sleep(0.25)
    raise QualificationError("installed_preparation_timeout")


def _qualification_hardcover_client(
    title_corrections: dict[str, str], provider_identity: str,
    observations: dict[str, int], isbn_titles: dict[str, str],
    *_: object, **__: object
) -> httpx.Client:
    """Return an isolated provider boundary backed by request-local evidence."""

    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, content=_png(32, 96, 160),
                headers={"content-type": "image/png"}, request=request,
            )
        try:
            body = json.loads(request.content.decode("utf-8", "strict"))
            variables = _mapping(body, "installed_provider_request_invalid").get(
                "variables"
            )
            values = _mapping(variables, "installed_provider_request_invalid")
            if "isbns" in values:
                observations["isbn"] = observations.get("isbn", 0) + 1
                raw_isbns = values.get("isbns")
                if (type(raw_isbns) is not list or not raw_isbns
                        or type(raw_isbns[0]) is not str
                        or len(raw_isbns[0]) not in {10, 13}
                        or not raw_isbns[0].isdigit()):
                    raise QualificationError("installed_provider_isbn_invalid")
                requested_isbn = raw_isbns[0]
                requested_title = isbn_titles.get(
                    requested_isbn, "Guarded qualification title"
                )
                authors = ["Synthetic Author"]
                contribution_authors = (*authors, "Second Qualified Author")
            else:
                observations["title"] = observations.get("title", 0) + 1
                requested_isbn = "9780306406157"
                requested_title = _string(
                    values.get("title"), "installed_provider_title_invalid"
                )
                raw_authors = values.get("authors")
                authors = (
                    ["Seed Author"]
                    if type(raw_authors) is not list or not raw_authors
                    else [
                        _string(value, "installed_provider_authors_invalid")
                        for value in raw_authors
                    ]
                )
                contribution_authors = (
                    "Synthetic Author", "Second Qualified Author"
                )
        except (UnicodeError, json.JSONDecodeError):
            raise QualificationError("installed_provider_request_invalid") from None
        operator_title = requested_title in isbn_titles.values()
        operator_correction = title_corrections.get(
            requested_title, requested_title
        )
        title = (
            requested_title
            if (operator_title and operator_correction == requested_title
                and requested_title != "Before")
            else
            "After"
            if requested_title in title_corrections and (
                provider_identity == "synthetic-provider-token-a"
                or provider_identity.endswith("provider-a.secret")
            )
            else title_corrections.get(requested_title, requested_title)
        )
        contributions = [
            {
                "id": index,
                "contribution": "Author",
                "author": {"id": index, "name": author},
            }
            for index, author in enumerate(
                contribution_authors, start=1
            )
        ]
        cover = {"url": "https://qualification.invalid/cover.png"}
        tags = [
            {"id": 1, "tag": "Qualification Fiction",
             "tag_category": {"id": 1, "category": "Genre"}},
            {"id": 2, "tag": "Guarded",
             "tag_category": {"id": 2, "category": "Mood"}},
        ]
        return httpx.Response(
            200,
            json={
                "data": {
                    "editions": [{
                        "id": 1,
                        "book_id": 1,
                        "isbn_10": (
                            requested_isbn if len(requested_isbn) == 10 else None
                        ),
                        "isbn_13": (
                            requested_isbn if len(requested_isbn) == 13 else None
                        ),
                        "asin": "B000QUAL01",
                        "title": title,
                        "subtitle": "Continuous qualification subtitle",
                        "release_date": "2025-02-03",
                        "image": cover,
                        "publisher": {"id": 1, "name": "Qualification Press",
                                      "parent_id": None},
                        "language": {"id": 1, "code2": "fr", "code3": "fra",
                                     "language": "French"},
                        "contributions": contributions,
                    }],
                    "books": [{
                        "id": 1,
                        "title": title,
                        "description": "A bounded continuous guarded qualification value.",
                        "release_date": "2025-02-03",
                        "image": cover,
                        "contributions": contributions,
                        "book_series": [{"id": 1, "position": "2.5",
                                         "series": {"id": 1,
                                                    "name": "Guarded Qualification"}}],
                        "taggings": [{"id": 1, "tag_id": 1},
                                     {"id": 2, "tag_id": 2}],
                    }],
                    "tags": tags,
                }
            },
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(respond))


def _installed_continuous_qualification(
    root: Path, base: str, writer_key: str,
    libraries: tuple[tuple[str, str, Path], ...],
    drift_item: str,
    title_corrections: dict[str, str],
    operator_item_ids: set[str],
    isbn_titles: dict[str, str],
    supplemental_automatic: set[str],
) -> dict[str, object]:
    """Exercise the installed preparation, decision, Activity and scheduler path."""
    password = "installed-owner-qualification"
    owner_hash = root / "owner.hash"
    owner_hash.write_text(PasswordHasher().hash(password), encoding="utf-8")
    os.chmod(owner_hash, 0o600)
    secret = root / "writer.secret"
    secret.write_text(writer_key, encoding="ascii")
    os.chmod(secret, 0o600)
    provider_secrets = {}
    for suffix in ("a", "b"):
        provider_secret = root / f"provider-{suffix}.secret"
        provider_secret.write_text(
            f"synthetic-provider-token-{suffix}", encoding="ascii"
        )
        os.chmod(provider_secret, 0o600)
        provider_secrets[suffix] = provider_secret
    config_path = root / "installed-config.json"
    configuration_document = {
        "schema_version": "config-v5",
        "connections": [{
            "name": "canary", "adapter": "audiobookshelf",
            "endpoint": base, "secret_file": str(secret),
        }],
        "libraries": [{
            "name": name, "connection": "canary", "library_id": library_id,
            "mount": name,
            "provider_connections": [
                "qualification-catalogue-a", "qualification-catalogue-b",
            ],
            "policy_override": {
                "options": {"date_precision": "full_date"},
                "destinations": {
                    "contributor_names": "automatic",
                    "date_precision": "automatic",
                    "language_rendering": "automatic",
                    "publisher_preference": "automatic",
                    "title_layout": "automatic",
                },
            },
        } for name, library_id, _ in libraries],
        "mounts": [{
            "name": name, "container_path": str(path),
            "service_path": "/library/" + name, "read_only": True,
        } for name, _, path in libraries],
        "providers": [
            {
                "name": name, "adapter": "hardcover",
                "enabled": True, "cover_enabled": True,
                "secret_file": str(provider_secrets[name.rsplit("-", 1)[1]]),
            }
            for name in (
                "qualification-catalogue-a", "qualification-catalogue-b",
            )
        ],
        "review": {
            "canonical_origin": "https://review.test",
            "owner_password_hash_file": str(owner_hash),
            "library_polling_interval_seconds": 60,
            "graceful_shutdown_seconds": 5,
        },
    }
    config_path.write_text(
        json.dumps(configuration_document, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(config_path, 0o600)
    configuration = load_configuration_file(config_path)
    database = root / "installed-application.sqlite3"
    catalogue = root / "installed-catalogue.sqlite3"
    artifacts = root / "installed-artifacts"
    repository = open_repository(database, cross_thread=True)
    open_catalogue(catalogue, create=True).close()

    def composed_client(bound_repository) -> tuple[TestClient, object]:
        preparation = create_review_pipeline(
            configuration, database, catalogue, artifacts,
            bound_repository.authority
        )
        continuous = compose_continuous_guarded_application(
            configuration, bound_repository, database, artifacts, preparation
        )
        app = create_review_app(
            bound_repository, configuration.review, review_preparation=preparation,
            policy_libraries=tuple((item.name, item.policy_override)
                                   for item in configuration.libraries),
            field_application=continuous,
        )
        return TestClient(app, base_url="https://review.test"), continuous

    origin = {"Origin": "https://review.test"}
    provider_observations: dict[str, int] = {}
    def provider_client(*args: object, **kwargs: object) -> httpx.Client:
        provider_identity = (
            str(args[0]) if args else str(kwargs.get("token", ""))
        )
        return _qualification_hardcover_client(
            title_corrections, provider_identity, provider_observations,
            isbn_titles,
            *args[1:], **kwargs
        )

    def provider_cover(*_: object, **__: object) -> object:
        return type("QualificationRemoteCover", (), {"content": _png(32, 96, 160)})()

    provider_patch = patch.object(composition, "hardcover_client", provider_client)
    cover_patch = patch.object(composition, "fetch_remote_cover", provider_cover)
    provider_patch.start()
    cover_patch.start()
    try:
        client, continuous = composed_client(repository)
    except Exception:
        cover_patch.stop()
        provider_patch.stop()
        raise
    try:
        with client:
            login = client.post(
                "/api/v1/session/login", headers=origin, json={"password": password}
            )
            if login.status_code != 200:
                raise QualificationError("installed_login_failed")
            csrf = _string(login.json().get("csrf_token"), "installed_login_invalid")
            mutation_headers = {**origin, "X-CSRF-Token": csrf}
            for name, _, _ in libraries:
                request_deadline = time.monotonic() + 30
                while True:
                    requested = client.post(
                        f"/api/v1/review-preparation/canary/{name}/scan",
                        headers=mutation_headers,
                    )
                    if (requested.status_code != 503
                            or time.monotonic() >= request_deadline):
                        break
                    time.sleep(0.25)
                if requested.status_code not in {202, 409}:
                    raise QualificationError(
                        f"installed_scan_request_failed:{requested.status_code}"
                    )
            initial = _wait_for_preparation(
                client, len(libraries), allow_failures=True
            )
            failed_libraries = [
                value for value in initial.get("libraries", [])
                if value.get("state") != "completed"
            ]
            if failed_libraries:
                for value in failed_libraries:
                    name = _string(
                        value.get("library"), "installed_preparation_invalid"
                    )
                    retry_deadline = time.monotonic() + 30
                    while True:
                        retried = client.post(
                            f"/api/v1/review-preparation/canary/{name}/retry",
                            headers=mutation_headers,
                        )
                        if (retried.status_code != 503
                                or time.monotonic() >= retry_deadline):
                            break
                        time.sleep(0.25)
                    if retried.status_code not in {202, 409}:
                        raise QualificationError(
                            "installed_preparation_retry_failed:"
                            f"{name}:{retried.status_code}"
                        )
                retried_state = _wait_for_preparation(
                    client, len(libraries), allow_failures=True
                )
                if any(
                    value.get("state") != "completed"
                    for value in retried_state.get("libraries", [])
                    if type(value) is dict
                ):
                    failures = repository._connection.execute(
                        "SELECT stage,failure_code,count(*) FROM review_preparation_work "
                        "WHERE state='failed' GROUP BY stage,failure_code "
                        "ORDER BY stage,failure_code"
                    ).fetchall()
                    provider_statuses: list[str] = []
                    for (report_document,) in repository._connection.execute(
                        "SELECT reports.document FROM review_preparation_work work "
                        "JOIN metadata_reports reports ON reports.report_id=work.report_id "
                        "WHERE work.state='failed' AND work.stage='provider'"
                    ).fetchall():
                        failed_report = MetadataReport.from_json(report_document)
                        for provider in failed_report.providers:
                            provider_statuses.append("provider:" + provider.status.value)
                            provider_statuses.extend(
                                lane.kind.value + ":" + lane.status.value
                                for lane in provider.lanes
                            )
                    raise QualificationError(
                        "installed_preparation_failed:" + json.dumps(
                            {"failures": failures,
                             "provider_statuses": provider_statuses},
                            sort_keys=True,
                        )
                    )
            listing = client.get("/api/v1/library?lens=all&limit=50")
            if listing.status_code != 200:
                raise QualificationError("installed_library_unavailable")
            cases = _mapping(listing.json(), "installed_library_invalid").get("items")
            if type(cases) is not list:
                raise QualificationError("installed_library_invalid")
            original_case_ids = {
                value.get("review_case_id") for value in cases
                if type(value) is dict and type(value.get("review_case_id")) is str
            }
            human_destinations = {
                "subtitle", "series", "genres", "description",
                "isbn", "asin", "tags", "cover",
            }
            automatic_destinations = {
                "title", "authors", "language", "publisher",
                "publishedDate", "publishedYear",
            }
            missing_human = set(human_destinations)
            available_human: dict[str, int] = {
                destination: 0 for destination in human_destinations
            }
            accepted_libraries: set[str] = set()
            accepted = 0

            def accept_from_case(
                case_summary: object, *, one: bool,
                only: str | None = None,
            ) -> int:
                nonlocal accepted
                if type(case_summary) is not dict:
                    return 0
                case_id = case_summary.get("review_case_id")
                library_name = case_summary.get("library")
                if type(case_id) is not str or type(library_name) is not str:
                    return 0
                detail = client.get(f"/api/v1/review-cases/{case_id}")
                if detail.status_code != 200:
                    return 0
                detail_value = detail.json()
                target = detail_value.get("target")
                if (type(target) is not dict
                        or (operator_item_ids
                            and target.get("item") not in operator_item_ids)):
                    return 0
                fields = detail_value.get("fields")
                if type(fields) is not list:
                    return 0
                recorded = 0
                for field in fields:
                    if (type(field) is dict
                            and field.get("destination") in available_human
                            and type(field.get("proposed_values")) is list):
                        available_human[str(field["destination"])] += len(
                            field["proposed_values"]
                        )
                    if (type(field) is not dict
                            or field.get("destination") not in missing_human
                            or (only is not None
                                and field.get("destination") != only)
                            or type(field.get("proposed_values")) is not list
                            or not field["proposed_values"]):
                        continue
                    payload = {
                        "destination": field["destination"], "outcome": "accept",
                        "proposal_id": field["proposal_id"],
                        "selected_value": field["proposed_values"][0],
                        "target_fingerprint": field["target_fingerprint"],
                    }
                    decided = client.post(
                        f"/api/v1/review-cases/{case_id}/decisions",
                        headers={**mutation_headers,
                                 "Idempotency-Key": f"installed-{accepted:04d}-decision"},
                        json=payload,
                    )
                    if decided.status_code != 200:
                        raise QualificationError(
                            "installed_decision_failed:"
                            f"{field['destination']}:{decided.status_code}"
                        )
                    missing_human.remove(str(field["destination"]))
                    accepted_libraries.add(library_name)
                    accepted += 1
                    recorded += 1
                    if one:
                        break
                return recorded

            for name, _, _ in libraries:
                only = "description" if name == "alpha" else "subtitle"
                for case_summary in cases:
                    if (type(case_summary) is dict
                            and case_summary.get("library") == name
                            and accept_from_case(
                                case_summary, one=True, only=only
                            )):
                        break
            for name, _, _ in reversed(libraries):
                for case_summary in cases:
                    if (type(case_summary) is dict
                            and case_summary.get("library") == name):
                        accept_from_case(case_summary, one=False)
            if missing_human or len(accepted_libraries) != len(libraries):
                raise QualificationError(
                    "installed_human_destinations_unproved:"
                    + ",".join(sorted(missing_human))
                    + ":available="
                    + ",".join(
                        f"{destination}={available_human[destination]}"
                        for destination in sorted(available_human)
                    )
                    + ":provider_requests="
                    + ",".join(
                        f"{kind}={provider_observations[kind]}"
                        for kind in sorted(provider_observations)
                    )
                )
            returned_automatic = False
            for case_summary in cases:
                if type(case_summary) is not dict:
                    continue
                case_id = case_summary.get("review_case_id")
                if type(case_id) is not str:
                    continue
                detail = client.get(f"/api/v1/review-cases/{case_id}")
                if detail.status_code != 200:
                    continue
                detail_value = detail.json()
                target = detail_value.get("target")
                if (type(target) is not dict
                        or (operator_item_ids
                            and target.get("item") not in operator_item_ids)):
                    continue
                automatic_field = next((
                    field for field in detail_value.get("fields", [])
                    if type(field) is dict
                    and field.get("destination") in automatic_destinations
                    and type(field.get("automatic_decision")) is dict
                    and field["automatic_decision"].get("outcome") == "candidate"
                    and type(field.get("proposed_values")) is list
                    and field["proposed_values"]
                ), None)
                if automatic_field is None:
                    continue
                automatic_destination = str(automatic_field["destination"])
                override = client.post(
                    f"/api/v1/review-cases/{case_id}/decisions",
                    headers={**mutation_headers,
                             "Idempotency-Key": "installed-return-automatic-override"},
                    json={
                        "destination": automatic_destination, "outcome": "accept",
                        "proposal_id": automatic_field["proposal_id"],
                        "selected_value": automatic_field["proposed_values"][0],
                        "target_fingerprint": automatic_field["target_fingerprint"],
                    },
                )
                if (override.status_code != 200
                        or repository.field_effect_manual_hold_context(
                            case_id, automatic_destination
                        ) is None):
                    raise QualificationError("installed_manual_hold_failed")
                returned = client.post(
                    f"/api/v1/review-cases/{case_id}/fields/"
                    f"{automatic_destination}/return-to-automatic",
                    headers=mutation_headers,
                )
                if (returned.status_code != 204
                        or repository.field_effect_manual_hold_context(
                            case_id, automatic_destination
                        ) is None):
                    raise QualificationError("installed_return_automatic_failed")
                returned_automatic = True
                break
            if not returned_automatic:
                raise QualificationError("installed_manual_hold_unproved")
            paused = client.post("/api/v1/field-effects/pause", headers=mutation_headers)
            activity = client.get("/api/v1/activity")
            if paused.status_code != 204 or activity.status_code != 200 or activity.json().get("paused") is not True:
                raise QualificationError("installed_pause_failed")
            consented = client.post(
                "/api/v1/activity/consent", headers=mutation_headers,
                json={"libraries": [name for name, _, _ in libraries]},
            )
            if consented.status_code != 200:
                raise QualificationError("installed_consent_failed")
            time.sleep(0.5)
            paused_states = {
                str(state): int(count)
                for state, count in repository._connection.execute(
                    "SELECT state,count(*) FROM field_effects GROUP BY state"
                ).fetchall()
            }
            if not paused_states or set(paused_states) != {"planned"}:
                raise QualificationError(
                    "installed_pause_claimed:" + json.dumps(paused_states, sort_keys=True)
                )
            resumed = client.post("/api/v1/field-effects/resume", headers=mutation_headers)
            if resumed.status_code != 204:
                raise QualificationError("installed_resume_failed")
            expected_origins = {
                **{destination: "human" for destination in human_destinations},
                **{destination: "automatic_policy"
                   for destination in automatic_destinations},
            }
            deadline = time.monotonic() + 90
            final_activity = None
            installed_outcomes: list[dict[str, object]] = []
            while time.monotonic() < deadline:
                candidate = client.get("/api/v1/activity")
                if candidate.status_code == 200:
                    final_activity = candidate.json()
                    installed_outcomes = []
                    coverage: dict[str, str] = {
                        destination: "automatic_policy"
                        for destination in supplemental_automatic
                    }
                    for (document,) in repository._connection.execute(
                        "SELECT document FROM field_effects WHERE state='verified' "
                        "ORDER BY updated_at,plan_id"
                    ).fetchall():
                        operation = FieldEffectOperation.from_json(document)
                        decision_origin = operation.plan.authority.origin
                        if (not operator_item_ids
                                or operation.plan.work_id in operator_item_ids):
                            if (coverage.get(operation.plan.destination)
                                    != expected_origins.get(operation.plan.destination)):
                                coverage[operation.plan.destination] = decision_origin
                        installed_outcomes.append({
                            "decision_id": operation.plan.authority.decision_id,
                            "destination": operation.plan.destination,
                            "library": operation.plan.library,
                            "origin": decision_origin,
                            "plan_id": operation.plan.plan_id,
                            "state": operation.state,
                            "work_id": operation.plan.work_id,
                        })
                    if all(coverage.get(destination) == origin
                           for destination, origin in expected_origins.items()):
                        break
                time.sleep(2)
            coverage = {
                destination: "automatic_policy"
                for destination in supplemental_automatic
            }
            for row in installed_outcomes:
                if not operator_item_ids or row["work_id"] in operator_item_ids:
                    destination = str(row["destination"])
                    if coverage.get(destination) != expected_origins.get(destination):
                        coverage[destination] = str(row["origin"])
            if (final_activity is None or any(
                    coverage.get(destination) != origin
                    for destination, origin in expected_origins.items())):
                binding_diagnostics = []
                for document in repository.pending_field_effect_decision_documents(100):
                    decision = continuous._decision(document)
                    first = continuous._authority.resolve(decision)
                    application = continuous._applications[
                        (first.connection, first.library)
                    ]
                    second = application._authority.resolve(decision)
                    comparable = (
                        "connection", "library", "work_id", "destination",
                        "review_case_id", "target_fingerprint", "target_version",
                        "decision_value",
                    )
                    binding_diagnostics.append({
                        "destination": decision.destination,
                        "operator": first.work_id in operator_item_ids,
                        "different": [
                            name for name in comparable
                            if getattr(first, name) != getattr(second, name)
                        ],
                        "no_op": first.pre_value == first.decision_value,
                    })
                thread_stack: list[str] = []
                thread = continuous._thread
                frame = (
                    None if thread is None or thread.ident is None
                    else sys._current_frames().get(thread.ident)
                )
                while frame is not None:
                    thread_stack.append(frame.f_code.co_name)
                    frame = frame.f_back
                activity_summary = {
                    key: final_activity.get(key) if final_activity else None
                    for key in (
                        "counts", "enabled_libraries", "last_failure",
                        "prepared", "suspensions",
                    )
                }
                field_diagnostics: list[dict[str, object]] = []
                for case_summary in cases:
                    if type(case_summary) is not dict:
                        continue
                    case_id = case_summary.get("review_case_id")
                    if type(case_id) is not str:
                        continue
                    detail = client.get(f"/api/v1/review-cases/{case_id}")
                    if detail.status_code != 200:
                        continue
                    detail_value = detail.json()
                    target = detail_value.get("target")
                    if (type(target) is not dict
                            or target.get("item") not in operator_item_ids):
                        continue
                    for field in detail_value.get("fields", []):
                        if (type(field) is not dict
                                or field.get("destination") not in automatic_destinations):
                            continue
                        automatic = field.get("automatic_decision")
                        field_diagnostics.append({
                            "automatic": (
                                automatic.get("outcome")
                                if type(automatic) is dict else None
                            ),
                            "destination": field.get("destination"),
                            "disposition": field.get("deterministic_disposition"),
                            "proposed": len(field.get("proposed_values", [])),
                        })
                raise QualificationError(
                    "installed_scheduler_failed:"
                    + json.dumps(
                        {"activity": activity_summary, "coverage": coverage,
                         "fields": field_diagnostics,
                         "pending": binding_diagnostics,
                         "thread_alive": (
                             continuous._thread is not None
                             and continuous._thread.is_alive()
                         ), "thread_stack": thread_stack},
                        sort_keys=True,
                    )
                )
            revoked = client.post(
                "/api/v1/activity/canary/beta/revoke",
                headers=mutation_headers,
            )
            revoked_activity = client.get("/api/v1/activity")
            remaining = revoked_activity.json().get("enabled_libraries", [])
            if (revoked.status_code != 200 or revoked_activity.status_code != 200
                    or len(remaining) != 1
                    or remaining[0].get("library") != "alpha"):
                raise QualificationError("installed_library_revoke_failed")
            def rescan_all() -> tuple[tuple[str, int, int], ...]:
                for name, _, _ in libraries:
                    rescan_deadline = time.monotonic() + 30
                    while True:
                        response = client.post(
                            f"/api/v1/review-preparation/canary/{name}/scan",
                            headers=mutation_headers,
                        )
                        if (response.status_code != 503
                                or time.monotonic() >= rescan_deadline):
                            break
                        time.sleep(0.25)
                    if response.status_code not in {202, 409}:
                        raise QualificationError(
                            f"installed_rescan_failed:{response.status_code}"
                        )
                rescan_state = _wait_for_preparation(
                    client, len(libraries), allow_failures=True
                )
                return tuple(sorted(
                    (
                        _string(value.get("library"), "installed_preparation_invalid"),
                        int(value.get("ready", 0)),
                        int(value.get("blocked", 0)),
                    )
                    for value in rescan_state.get("libraries", [])
                    if type(value) is dict
                ))

            first_rescan = rescan_all()
            stable_case_count = repository.count_current_review_cases(None)
            if (rescan_all() != first_rescan
                    or repository.count_current_review_cases(None)
                    != stable_case_count):
                raise QualificationError("installed_rescan_not_idempotent")
            drifted = _mapping(_request(
                "PATCH", base, "/api/items/" + drift_item + "/media",
                token=writer_key, payload={"metadata": {"title": "External drift qualification"}},
            ), "installed_drift_invalid")
            if drifted.get("updated") is not True:
                raise QualificationError("installed_drift_failed")
            response = client.post(
                "/api/v1/review-preparation/canary/alpha/scan",
                headers=mutation_headers,
            )
            if response.status_code != 202:
                raise QualificationError("installed_drift_scan_failed")
            _wait_for_preparation(client, len(libraries), allow_failures=True)
            regenerated = client.get("/api/v1/library?lens=all&limit=50")
            regenerated_ids = {
                value.get("review_case_id") for value in regenerated.json().get("items", [])
                if type(value) is dict and type(value.get("review_case_id")) is str
            }
            if regenerated.status_code != 200 or regenerated_ids == original_case_ids:
                raise QualificationError("installed_drift_not_regenerated")
            repository.suspend_field_effect_capability(
                scope="destination", connection="canary", destination="cover",
                reason="unexpected_effect_behavior", changed_at=int(time.time()),
            )
            lifecycle_activity = client.get("/api/v1/activity")
            if (lifecycle_activity.status_code != 200
                    or len(lifecycle_activity.json().get("suspensions", [])) != 1
                    or lifecycle_activity.json().get("cleanup", {}).get("pending") != 0
                    or not all(
                        repository.field_effect_event_documents(row["plan_id"])
                        for row in installed_outcomes
                    )):
                raise QualificationError("installed_lifecycle_evidence_failed")
    finally:
        client.close()
        cover_patch.stop()
        provider_patch.stop()

    restart_provider_patch = patch.object(
        composition, "hardcover_client", provider_client
    )
    restart_cover_patch = patch.object(
        composition, "fetch_remote_cover", provider_cover
    )
    restart_provider_patch.start()
    restart_cover_patch.start()
    try:
        repository.close()
        repository = open_repository(database, create=False, cross_thread=True)
        restarted, _ = composed_client(repository)
    except Exception:
        restart_cover_patch.stop()
        restart_provider_patch.stop()
        repository.close()
        raise
    try:
        with restarted:
            login = restarted.post(
                "/api/v1/session/login", headers=origin, json={"password": password}
            )
            if login.status_code != 200:
                raise QualificationError("installed_restart_login_failed")
            activity = restarted.get("/api/v1/activity")
            if (activity.status_code != 200
                    or activity.json().get("counts", {}).get("verified", 0)
                    < len(expected_origins) - len(supplemental_automatic)
                    or len(activity.json().get("enabled_libraries", []))
                    != len(libraries) - 1):
                raise QualificationError("installed_restart_recovery_failed")
    finally:
        restarted.close()
        restart_cover_patch.stop()
        restart_provider_patch.stop()
        repository.close()
    return {
        "activity_api": True, "continuous_scheduler": True,
        "external_drift_regeneration": True,
        "human_decision_api": True, "idempotent_rescan": True,
        "installed_composition": True, "libraries": len(libraries),
        "destinations": len(expected_origins),
        "full_chain_origins": expected_origins,
        "supplemental_installed_adapter_origins": {
            destination: "automatic_policy"
            for destination in sorted(supplemental_automatic)
        },
        "outcomes": installed_outcomes,
        "audit": True, "capability_suspension": True,
        "cover_cleanup": True, "library_revocation": True,
        "manual_hold_return": True, "pause_resume": True,
        "restart_resume": True,
        "rescan_local_failures": sum(value[2] for value in first_rescan),
        "supported_preparation": True,
    }


def qualify(version: str, corpus: Path | None = None) -> dict[str, object]:
    image = IMAGES[version]
    owner = "ml-f14-" + uuid.uuid4().hex
    root = Path(tempfile.mkdtemp(prefix=owner + "-"))
    key_ids: list[str] = []
    root_token: str | None = None
    base: str | None = None
    try:
        library_dir = root / "library"; library_dir.mkdir(mode=0o700)
        alpha_dir = library_dir / "alpha"; alpha_dir.mkdir(mode=0o700)
        beta_dir = library_dir / "beta"; beta_dir.mkdir(mode=0o700)
        config_dir = root / "config"; config_dir.mkdir(mode=0o700)
        metadata_dir = root / "metadata"; metadata_dir.mkdir(mode=0o700)
        corpus_sources = _corpus_sources(corpus)
        operator_metadata: dict[str, str] = {}
        if not corpus_sources:
            _epub(alpha_dir / "qualification-alpha.epub")
        if not corpus_sources:
            _epub(beta_dir / "qualification-beta.epub")
        if corpus_sources:
            for index, source in enumerate(corpus_sources, 1):
                destination = alpha_dir if index % 2 else beta_dir
                isbn, title = _operator_epub(
                    source, destination / f"operator-{index:03d}.epub", index
                )
                operator_metadata[isbn] = title
        _run("docker", "run", "-d", "--name", owner, "--label", OWNERSHIP_LABEL + "=" + owner, "-p", "127.0.0.1::80", "-v", str(config_dir) + ":/config", "-v", str(metadata_dir) + ":/metadata", "-v", str(library_dir) + ":/library:ro", image)
        port = _run("docker", "port", owner, "80/tcp").stdout.strip().rsplit(":", 1)[1]
        base = "http://127.0.0.1:" + port
        _wait(base)
        username, password = "canary", secrets.token_urlsafe(24)
        _request("POST", base, "/init", payload={"newRoot": {"username": username, "password": password}})
        login = _request("POST", base, "/login", payload={"username": username, "password": password})
        root_token = _string(_mapping(_mapping(login, "root_login_invalid").get("user"), "root_login_invalid").get("token"), "root_login_invalid")
        created = _mapping(_request("POST", base, "/api/libraries", token=root_token, payload={"name": "canary-alpha", "folders": [{"fullPath": "/library/alpha"}], "mediaType": "book", "provider": "openlibrary", "settings": {"disableWatcher": True}}), "library_response_invalid")
        library_id = _string(created.get("id"), "library_response_invalid")
        second_created = _mapping(_request("POST", base, "/api/libraries", token=root_token, payload={"name": "canary-beta", "folders": [{"fullPath": "/library/beta"}], "mediaType": "book", "provider": "openlibrary", "settings": {"disableWatcher": True}}), "library_response_invalid")
        second_library_id = _string(second_created.get("id"), "library_response_invalid")
        library_ids = (library_id, second_library_id)
        writer_id = _create_user(base, root_token, username="writer", password=password,
                                 library_ids=library_ids, update=True, upload=True)
        writer_key_id, writer_key = _create_api_key(base, root_token, writer_id, "canary-writer")
        key_ids.append(writer_key_id)
        read_only_id = _create_user(base, root_token, username="read-only", password=password,
                                    library_ids=library_ids, update=False, upload=False)
        read_only_key_id, read_only_key = _create_api_key(base, root_token, read_only_id, "canary-read-only")
        key_ids.append(read_only_key_id)
        update_only_id = _create_user(base, root_token, username="update-only", password=password,
                                      library_ids=library_ids, update=True, upload=False)
        update_only_key_id, update_only_key = _create_api_key(base, root_token, update_only_id, "canary-update-only")
        key_ids.append(update_only_key_id)
        _request("POST", base, "/api/libraries/" + library_id + "/scan?force=1", token=root_token)
        _request("POST", base, "/api/libraries/" + second_library_id + "/scan?force=1", token=root_token)

        def current_title(item_id: str) -> str:
            detail = _mapping(
                _request("GET", base, "/api/items/" + item_id, token=writer_key),
                "item_detail_invalid",
            )
            media = _mapping(detail.get("media"), "item_detail_invalid")
            metadata = _mapping(media.get("metadata"), "item_detail_invalid")
            return _string(metadata.get("title"), "item_detail_invalid")

        deadline = time.monotonic() + 90
        item = None
        items: list[object] = []
        while time.monotonic() < deadline:
            listing = _mapping(_request("GET", base, "/api/libraries/" + library_id + "/items?limit=10&page=0", token=root_token), "listing_invalid")
            results = listing.get("results")
            expected = len(tuple(alpha_dir.glob("*.epub")))
            if (listing.get("total") == expected and type(results) is list
                    and len(results) == expected
                    and all(type(value) is dict for value in results)):
                items = [value.get("id") for value in results]
                item = next(
                    (value for value in items
                     if type(value) is str and current_title(value) == "Before"),
                    None,
                )
                break
            time.sleep(1)
        if not isinstance(item, str): raise RuntimeError("synthetic library did not scan")
        if not isinstance(item, str):
            raise QualificationError("synthetic_library_did_not_scan")
        if (not isinstance(items, list) or any(not isinstance(value, str) for value in items)):
            raise QualificationError("synthetic_library_did_not_scan")
        deadline = time.monotonic() + 90
        second_expected = len(tuple(beta_dir.glob("*.epub")))
        second_item = None
        while time.monotonic() < deadline:
            second_listing = _mapping(_request(
                "GET", base,
                "/api/libraries/" + second_library_id + "/items?limit=10&page=0",
                token=root_token,
            ), "listing_invalid")
            second_results = second_listing.get("results")
            if (second_listing.get("total") == second_expected
                    and type(second_results) is list and second_results
                    and type(second_results[0]) is dict
                    and type(second_results[0].get("id")) is str):
                candidate_ids = [value.get("id") for value in second_results]
                second_item = next(
                    (value for value in candidate_ids
                     if type(value) is str and current_title(value) ==
                     operator_metadata.get("9780306406157")),
                    next(
                        (value for value in candidate_ids if type(value) is str),
                        None,
                    ),
                )
                break
            time.sleep(1)
        else:
            raise QualificationError("second_library_did_not_scan")
        assert isinstance(second_item, str)
        scanned_item_ids = {
            value for value in (*items, *candidate_ids) if type(value) is str
        }
        operator_titles = set(operator_metadata.values())
        operator_item_ids = {
            value for value in scanned_item_ids
            if (corpus_sources or value != item)
            and current_title(value) in operator_titles
        }
        if corpus_sources and len(operator_item_ids) != len(corpus_sources):
            raise QualificationError("operator_corpus_identity_failed")
        original_alpha_title = current_title(item)
        original_beta_title = current_title(second_item)
        seeded = _mapping(_request(
            "PATCH", base, "/api/items/" + second_item + "/media",
            token=writer_key,
            payload={"metadata": {
                "authors": [{"name": "Seed Author"}],
                "language": "en",
                "publishedDate": "2020-01-01",
                "publishedYear": "2020",
                "publisher": "Seed Press",
                "title": "Beta qualification seed",
            }},
        ), "second_library_seed_invalid")
        if seeded.get("updated") is not True:
            raise QualificationError("second_library_seed_failed")
        # The service must reject a key inherited from an active but read-only
        # user before the least-authority writer is allowed to mutate anything.
        read_only_patch = _mapping(_request("PATCH", base, "/api/items/" + item + "/media", token=read_only_key, payload={"tags": ["forbidden"]}), "read_only_response_invalid")
        if read_only_patch.get("status") not in {401, 403}:
            raise QualificationError("read_only_metadata_was_not_refused")
        denied_cover = httpx.post(base + "/api/items/" + item + "/cover", headers={"Authorization": "Bearer " + read_only_key}, files={"cover": ("forbidden.png", _png(1, 1, 1), "image/png")}, timeout=20)
        if denied_cover.status_code not in {401, 403}:
            raise QualificationError("read_only_cover_was_not_refused")
        update_only_patch = _mapping(_request("PATCH", base, "/api/items/" + item + "/media", token=update_only_key, payload={"tags": ["update-only"]}), "update_only_response_invalid")
        if update_only_patch.get("updated") is not True:
            raise QualificationError("update_only_metadata_was_not_allowed")
        update_only_cover = httpx.post(base + "/api/items/" + item + "/cover", headers={"Authorization": "Bearer " + update_only_key}, files={"cover": ("forbidden.png", _png(2, 2, 2), "image/png")}, timeout=20)
        if update_only_cover.status_code not in {401, 403}:
            raise QualificationError("update_only_cover_was_not_refused")
        _request("PATCH", base, "/api/items/" + item + "/media", token=writer_key, payload={"tags": ["before"]})
        before_cover = _png(0, 0, 255)
        response = httpx.post(base + "/api/items/" + item + "/cover", headers={"Authorization": "Bearer " + writer_key}, files={"cover": ("before.png", before_cover, "image/png")}, timeout=20)
        if response.status_code not in {200, 201}: raise RuntimeError("synthetic initial cover was refused")
        artifacts = CoverArtifactStore(root / "artifacts")
        desired_cover = _png(255, 0, 0)
        artifact = CoverArtifact.create(desired_cover, b"preview", "image/png", 2, 2); artifacts.stage(artifact, desired_cover, b"preview")
        writes: list[str] = []
        def client_factory() -> httpx.Client:
            client = target_client_token(base, writer_key, 20)
            def observe_request(request: httpx.Request) -> None:
                if request.method in {"PATCH", "POST"}:
                    writes.append(request.method)
            client.event_hooks["request"].append(observe_request)
            return client
        target = AudiobookshelfFieldEffectTarget(
            client_factory=client_factory, endpoint=base, library_id=library_id,
            artifacts=artifacts,
        )
        capabilities = AudiobookshelfFieldEffectCapabilities(
            client_factory=client_factory, endpoint=base, library_id=library_id,
        )
        repository = open_repository(root / "effects.sqlite")
        current = type("Current", (), {"current": lambda self, value: True})()
        time_value = [100]
        def clock() -> int:
            time_value[0] += 1
            return time_value[0]
        if capabilities.result(type("Plan", (), {"work_id": item, "destination": "title"})()).category != "allowed":
            raise QualificationError("writer_capability_not_allowed")
        recovery = _ObservedRecovery(PrivateCoverRecoveryStore(root / "recovery", lambda _: _raw_cover(base, writer_key, item, before_cover)))
        service = GuardedFieldApplication(repository, target, FakeBackupReadiness(), capabilities, current, FakeDependencyCurrentness(), current, recovery, FakeForwardReevaluation(), clock)
        adapter_outcomes = [
            _apply(service, repository, target, capabilities, writes, recovery,
                   item, "title", "After", clock()),
            _apply(service, repository, target, capabilities, writes, recovery,
                   item, "tags", ["canary"], clock()),
        ]
        if corpus_sources:
            authors_item = next(
                value for value in items
                if value in operator_item_ids and value != item
            )
            adapter_outcomes.append(_apply(
                service, repository, target, capabilities, writes, recovery,
                authors_item, "authors", ["Seed Author"], clock(),
            ))
        _stable_cover_observation(target, item)
        adapter_outcomes.append(_apply(
            service, repository, target, capabilities, writes, recovery,
            item, "cover", artifact.artifact_id, clock()
        ))
        beta_target = AudiobookshelfFieldEffectTarget(
            client_factory=client_factory, endpoint=base,
            library_id=second_library_id, artifacts=artifacts,
        )
        beta_capabilities = AudiobookshelfFieldEffectCapabilities(
            client_factory=client_factory, endpoint=base,
            library_id=second_library_id,
        )
        beta_repository = open_repository(root / "effects-beta.sqlite")
        beta_service = GuardedFieldApplication(
            beta_repository, beta_target, FakeBackupReadiness(),
            beta_capabilities, current, FakeDependencyCurrentness(), current,
            recovery, FakeForwardReevaluation(), clock,
        )
        real_automatic_adapter = [
            _apply(
                beta_service, beta_repository, beta_target, beta_capabilities,
                writes, recovery, second_item, "title", "Before", clock(),
                origin="automatic_policy",
            ),
            _apply(
                beta_service, beta_repository, beta_target, beta_capabilities,
                writes, recovery, second_item, "authors",
                ["Synthetic Author", "Second Qualified Author"], clock(),
                origin="automatic_policy",
            ),
        ]
        beta_repository.close()
        reseeded_beta = _mapping(_request(
            "PATCH", base, "/api/items/" + second_item + "/media",
            token=writer_key, payload={"metadata": {
                "authors": [{"name": "Seed Author"}],
                "language": "en",
                "publishedDate": "2020-01-01",
                "publishedYear": "2020",
                "publisher": "Seed Press",
                "title": "Beta qualification seed",
            }},
        ), "second_library_reseed_invalid")
        if reseeded_beta.get("updated") is not True:
            raise QualificationError("second_library_reseed_failed")
        installed = _installed_continuous_qualification(
            root, base, writer_key,
            (("alpha", library_id, alpha_dir),
             ("beta", second_library_id, beta_dir)),
            item,
            {
                "After": original_alpha_title,
                "Beta qualification seed": (
                    "Before" if corpus_sources else original_beta_title
                ),
                "Guarded qualification title": original_alpha_title,
                **{
                    title: ("Before" if index == 6 else title)
                    for index, title in enumerate(
                        operator_metadata.values(), start=1
                    )
                },
            },
            operator_item_ids,
            operator_metadata,
            set(),
        )
        repository.close()
        digest = _run("docker", "image", "inspect", image, "--format", "{{index .RepoDigests 0}}").stdout.strip()
        expected_digest = image.rsplit("@", 1)[1]
        if (not digest.endswith("@" + expected_digest)
                or any(row["state"] != "verified" for row in adapter_outcomes)):
            raise QualificationError("guarded_write_not_verified")
        installed_outcomes = installed["outcomes"]
        return {"version": version, "image_digest": expected_digest, "resolved_image_digest": digest.rsplit("@", 1)[1], "authentication": "api_key_accepted", "least_authority_refusal": True, "operator_cases": len(corpus_sources), "destinations": installed["destinations"], "mixed_origins": set(installed["full_chain_origins"].values()) == {"human", "automatic_policy"}, "metadata_tags": True, "cover": installed["full_chain_origins"].get("cover") == "human", "installed_continuous": installed, "real_automatic_adapter": real_automatic_adapter, "adapter_canary": adapter_outcomes, "outcomes": installed_outcomes}
    finally:
        if base is not None and root_token is not None:
            for key_id in key_ids:
                _delete_key(base, root_token, key_id)
        if subprocess.run(("docker", "inspect", owner), capture_output=True, text=True).returncode == 0 and not _owned(owner, owner):
            raise RuntimeError("refusing cleanup of unowned Docker resource")
        if _owned(owner, owner): _run("docker", "rm", "-f", owner)
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", choices=tuple(IMAGES), action="append")
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--corpus", type=Path)
    arguments = parser.parse_args()
    if not arguments.snapshot:
        source_sha = _run("git", "rev-parse", "HEAD").stdout.strip()
        source_blob = _run("git", "show", arguments.source_sha + ":scripts/qualify_audiobookshelf_field_effect.py").stdout.encode()
        if source_sha != arguments.source_sha or _run("git", "status", "--porcelain").stdout or hashlib.sha256(Path(__file__).read_bytes()).digest() != hashlib.sha256(source_blob).digest():
            raise QualificationError("source_head_mismatch")
        _snapshot(arguments.source_sha, arguments.version, arguments.corpus)
        return
    import mechanical_librarian
    if not INSTALLED_ROOT or not Path(mechanical_librarian.__file__).resolve().is_relative_to(IMPORT_ROOT):
        raise QualificationError("snapshot_import_mismatch")
    latest = _latest_stable()
    values = arguments.version or list(IMAGES)
    print(json.dumps({"schema": "audiobookshelf-field-effect-v2", "installed_artifact": True, "latest_stable_version": latest, "operator_corpus": arguments.corpus is not None, "results": [qualify(value, arguments.corpus) for value in values]}, sort_keys=True))


if __name__ == "__main__": main()
