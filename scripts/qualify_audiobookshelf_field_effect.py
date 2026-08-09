#!/usr/bin/env python3
"""Run the disposable, field-local Audiobookshelf write qualification.

This qualification owns every Docker resource it creates. By default it uses a
generated EPUB; continuous qualification may instead supply a pre-frozen private
operator batch of six to eight rights-verified EPUBs. It always creates a
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLED_ROOT = os.environ.get("MECHANICAL_LIBRARIAN_QUALIFICATION_INSTALL")
IMPORT_ROOT = Path(INSTALLED_ROOT).resolve() if INSTALLED_ROOT else ROOT / "src"
if str(IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(IMPORT_ROOT))

import httpx
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
        archive.writestr("OEBPS/content.opf", "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf' version='3.0' unique-identifier='book'><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:identifier id='book'>synthetic-f14</dc:identifier><dc:title>Before</dc:title><dc:language>en</dc:language></metadata><manifest/><spine/></package>")


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
                                   envelope_identity="a" * 64, granted_at=clock)
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


def _wait_for_preparation(client: TestClient, library_count: int) -> dict[str, object]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        response = client.get("/api/v1/review-preparation")
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
                summary = ",".join(
                    f"{value.get('library')}:{value.get('ready')}:{value.get('blocked')}"
                    for value in libraries
                )
                raise QualificationError("installed_preparation_failed:" + summary)
            return state
        time.sleep(0.25)
    raise QualificationError("installed_preparation_timeout")


def _installed_continuous_qualification(
    root: Path, base: str, writer_key: str,
    libraries: tuple[tuple[str, str, Path], ...],
    drift_item: str,
) -> dict[str, object]:
    """Exercise the installed preparation, decision, Activity and scheduler path."""
    password = "installed-owner-qualification"
    owner_hash = root / "owner.hash"
    owner_hash.write_text(PasswordHasher().hash(password), encoding="utf-8")
    os.chmod(owner_hash, 0o600)
    secret = root / "writer.secret"
    secret.write_text(writer_key + "\n", encoding="ascii")
    os.chmod(secret, 0o600)
    config_path = root / "installed-config.json"
    configuration_document = {
        "schema_version": "config-v5",
        "connections": [{
            "name": "canary", "adapter": "audiobookshelf",
            "endpoint": base, "secret_file": str(secret),
        }],
        "libraries": [{
            "name": name, "connection": "canary", "library_id": library_id,
            "mount": name, "provider_connections": ["disabled-catalogue"],
        } for name, library_id, _ in libraries],
        "mounts": [{
            "name": name, "container_path": str(path),
            "service_path": "/library/" + name, "read_only": True,
        } for name, _, path in libraries],
        "providers": [{
            "name": "disabled-catalogue", "adapter": "hardcover",
            "enabled": False, "cover_enabled": False, "secret_file": None,
        }],
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

    def composed_client() -> tuple[TestClient, object]:
        preparation = create_review_pipeline(
            configuration, database, catalogue, artifacts, repository.authority
        )
        continuous = compose_continuous_guarded_application(
            configuration, repository, database, artifacts, preparation
        )
        app = create_review_app(
            repository, configuration.review, review_preparation=preparation,
            policy_libraries=tuple((item.name, item.policy_override)
                                   for item in configuration.libraries),
            field_application=continuous,
        )
        return TestClient(app, base_url="https://review.test"), continuous

    origin = {"Origin": "https://review.test"}
    client, continuous = composed_client()
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
                requested = client.post(
                    f"/api/v1/review-preparation/canary/{name}/scan",
                    headers=mutation_headers,
                )
                if requested.status_code not in {202, 409}:
                    raise QualificationError("installed_scan_request_failed")
            _wait_for_preparation(client, len(libraries))
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
            accepted_libraries: set[str] = set()
            accepted = 0
            for case_summary in cases:
                if type(case_summary) is not dict:
                    continue
                case_id = case_summary.get("review_case_id")
                library_name = case_summary.get("library")
                if (type(case_id) is not str or type(library_name) is not str
                        or library_name in accepted_libraries):
                    continue
                detail = client.get(f"/api/v1/review-cases/{case_id}")
                if detail.status_code != 200:
                    continue
                fields = detail.json().get("fields")
                if type(fields) is not list:
                    continue
                field = next((value for value in fields if type(value) is dict
                              and value.get("needs_attention") is True
                              and type(value.get("proposed_values")) is list
                              and value["proposed_values"]), None)
                if field is None:
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
                    raise QualificationError("installed_decision_failed")
                accepted_libraries.add(library_name)
                accepted += 1
            if accepted != len(libraries):
                raise QualificationError("installed_multiple_libraries_unproved")
            paused = client.post("/api/v1/field-effects/pause", headers=mutation_headers)
            activity = client.get("/api/v1/activity")
            if paused.status_code != 204 or activity.status_code != 200 or activity.json().get("paused") is not True:
                raise QualificationError("installed_pause_failed")
            resumed = client.post("/api/v1/field-effects/resume", headers=mutation_headers)
            consented = client.post(
                "/api/v1/activity/consent", headers=mutation_headers,
                json={"libraries": [name for name, _, _ in libraries]},
            )
            if resumed.status_code != 204 or consented.status_code != 200:
                raise QualificationError("installed_consent_failed")
            deadline = time.monotonic() + 90
            final_activity = None
            while time.monotonic() < deadline:
                candidate = client.get("/api/v1/activity")
                if candidate.status_code == 200:
                    final_activity = candidate.json()
                    if final_activity.get("counts", {}).get("verified", 0) >= accepted:
                        break
                time.sleep(0.25)
            if (final_activity is None
                    or final_activity.get("counts", {}).get("verified", 0) < accepted):
                raise QualificationError("installed_scheduler_failed")
            before_rescan = repository.count_current_review_cases(None)
            for name, _, _ in libraries:
                response = client.post(
                    f"/api/v1/review-preparation/canary/{name}/scan",
                    headers=mutation_headers,
                )
                if response.status_code != 202:
                    raise QualificationError("installed_rescan_failed")
            _wait_for_preparation(client, len(libraries))
            if repository.count_current_review_cases(None) != before_rescan:
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
            _wait_for_preparation(client, len(libraries))
            regenerated = client.get("/api/v1/library?lens=all&limit=50")
            regenerated_ids = {
                value.get("review_case_id") for value in regenerated.json().get("items", [])
                if type(value) is dict and type(value.get("review_case_id")) is str
            }
            if regenerated.status_code != 200 or regenerated_ids == original_case_ids:
                raise QualificationError("installed_drift_not_regenerated")
    finally:
        client.close()

    restarted, _ = composed_client()
    try:
        with restarted:
            login = restarted.post(
                "/api/v1/session/login", headers=origin, json={"password": password}
            )
            if login.status_code != 200:
                raise QualificationError("installed_restart_login_failed")
            activity = restarted.get("/api/v1/activity")
            if (activity.status_code != 200
                    or activity.json().get("counts", {}).get("verified", 0) < len(libraries)
                    or len(activity.json().get("enabled_libraries", [])) != len(libraries)):
                raise QualificationError("installed_restart_recovery_failed")
    finally:
        restarted.close()
        repository.close()
    return {
        "activity_api": True, "continuous_scheduler": True,
        "external_drift_regeneration": True,
        "human_decision_api": True, "idempotent_rescan": True,
        "installed_composition": True, "libraries": len(libraries),
        "pause_resume": True, "restart_resume": True,
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
        if corpus_sources:
            for index, source in enumerate(corpus_sources, 1):
                destination = alpha_dir if index % 2 else beta_dir
                shutil.copyfile(source, destination / f"operator-{index:03d}.epub")
        else:
            _epub(alpha_dir / "synthetic-alpha.epub")
            _epub(beta_dir / "synthetic-beta.epub")
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
                item = results[0].get("id"); items = [value.get("id") for value in results]; break
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
                second_item = second_results[0]["id"]
                break
            time.sleep(1)
        else:
            raise QualificationError("second_library_did_not_scan")
        assert isinstance(second_item, str)
        seeded = _mapping(_request(
            "PATCH", base, "/api/items/" + second_item + "/media",
            token=writer_key, payload={"metadata": {"title": "Beta qualification seed"}},
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
        if corpus_sources:
            desired_values: list[tuple[str, object]] = [
                ("title", "Guarded qualification title"),
                ("subtitle", "Continuous qualification subtitle"),
                ("authors", ["First Qualified Author", "Second Qualified Author"]),
                ("series", [{"name": "Guarded Qualification", "sequence": "2.5"}]),
                ("genres", ["Fiction", "Qualification"]),
                ("publishedYear", "2025"),
                ("publishedDate", "2025-02-03"),
                ("publisher", "Qualification Press"),
                ("description", "A bounded continuous guarded qualification value."),
                ("isbn", "9780306406157"),
                ("asin", "B000QUAL01"),
                ("language", "fr"),
                ("tags", ["continuous", "guarded"]),
            ]
            outcomes = [
                _apply(service, repository, target, capabilities, writes, recovery,
                       items[index % len(items)], destination, desired, clock(),
                       "human" if index % 2 == 0 else "automatic_policy")
                for index, (destination, desired) in enumerate(desired_values)
            ]
            cover_item = item
            _stable_cover_observation(target, cover_item)
            outcomes.append(_apply(
                service, repository, target, capabilities, writes, recovery,
                cover_item, "cover", artifact.artifact_id, clock(),
                "automatic_policy",
            ))
        else:
            outcomes = [_apply(service, repository, target, capabilities, writes, recovery, item, "title", "After", clock()), _apply(service, repository, target, capabilities, writes, recovery, item, "tags", ["canary"], clock())]
            _stable_cover_observation(target, item)
            outcomes.append(_apply(service, repository, target, capabilities, writes, recovery, item, "cover", artifact.artifact_id, clock()))
        installed = _installed_continuous_qualification(
            root, base, writer_key,
            (("alpha", library_id, alpha_dir),
             ("beta", second_library_id, beta_dir)),
            item,
        )
        repository.close()
        digest = _run("docker", "image", "inspect", image, "--format", "{{index .RepoDigests 0}}").stdout.strip()
        expected_digest = image.rsplit("@", 1)[1]
        if not digest.endswith("@" + expected_digest) or any(row["state"] != "verified" for row in outcomes):
            raise QualificationError("guarded_write_not_verified")
        return {"version": version, "image_digest": expected_digest, "resolved_image_digest": digest.rsplit("@", 1)[1], "authentication": "api_key_accepted", "least_authority_refusal": True, "operator_cases": len(corpus_sources), "destinations": len(outcomes), "mixed_origins": {row["origin"] for row in outcomes} == {"human", "automatic_policy"} if corpus_sources else False, "metadata_tags": all(row["destination"] in {"title", "tags"} and row["state"] == "verified" for row in outcomes[:2]) if not corpus_sources else True, "cover": outcomes[-1]["destination"] == "cover" and outcomes[-1]["state"] == "verified", "installed_continuous": installed, "outcomes": outcomes}
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
    print(json.dumps({"schema": "audiobookshelf-field-effect-v1", "installed_artifact": True, "latest_stable_version": latest, "operator_corpus": arguments.corpus is not None, "results": [qualify(value, arguments.corpus) for value in values]}, sort_keys=True))


if __name__ == "__main__": main()
