from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf import (
    AudiobookshelfConnectionProbe,
)
from mechanical_librarian.application.connection_probe import (
    CapabilityState,
    CompatibilityState,
    ConnectionCapability,
    ConnectionProbeOutcome,
    InspectLibraryConnection,
    TargetProbeFacts,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    HealthReport,
    HealthStatus,
    LibraryConnectionDiagnosticsRequest,
    OperationContext,
    PortResult,
    ResourceBounds,
    SafeSummary,
    SecretHandle,
    SecretScope,
    TimeProbe,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    CompatibilityVersion,
    ConnectionId,
    LibraryId,
    ServiceVersion,
)
from mechanical_librarian.domain.inventory import SecretReference, ServiceEndpoint
from mechanical_librarian.infrastructure.security.target_secrets import (
    PrivateFileTargetCredentials,
    SecretFileUnavailable,
)


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "audiobookshelf" / "2.35.1"
COMPATIBILITY_MANIFEST = ROOT / "docs" / "reference" / "audiobookshelf-f04-compatibility.json"
COMPATIBILITY_DOCUMENT = ROOT / "docs" / "reference" / "compatibility.md"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def context(*, cancelled=lambda: False, max_bytes: int = 65_536) -> OperationContext:
    return OperationContext(
        Deadline(NOW + timedelta(seconds=30)),
        CancellationProbe(CancellationId("abs-probe"), cancelled),
        ResourceBounds(1, max_bytes),
        TimeProbe(lambda: NOW),
    )


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def client_factory(handler, token: str = "synthetic-token"):
    class AuthorizeAuth(httpx.Auth):
        def auth_flow(self, request):
            if request.method == "POST" and request.url.path == "/api/authorize":
                request.headers["Authorization"] = f"Bearer {token}"
            yield request

    def create():
        return PortResult.success(
            httpx.Client(
                transport=httpx.MockTransport(handler),
                auth=AuthorizeAuth(),
                trust_env=False,
                follow_redirects=False,
            )
        )

    return create


def raw_response(
    status: int = 200,
    *,
    payload: bytes = b"",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    selected = dict(headers or {})
    selected.setdefault("content-length", str(len(payload)))
    return httpx.Response(
        status,
        headers=selected,
        stream=httpx.ByteStream(payload),
    )


class AudiobookshelfAdapterTests(unittest.TestCase):
    def probe(
        self,
        handler,
        *,
        selected_context=None,
        expected_libraries=("synthetic-library",),
    ):
        return AudiobookshelfConnectionProbe(
            ServiceEndpoint("https://synthetic.invalid"),
            client_factory=client_factory(handler),
        ).probe(
            LibraryConnectionDiagnosticsRequest(
                tuple(LibraryId(value) for value in expected_libraries),
                selected_context or context(),
            )
        )

    def test_pinned_contract_uses_only_status_and_read_only_authorize_post(self) -> None:
        requests = []

        def handler(request):
            requests.append(request)
            payload = (
                fixture("status.json")
                if request.url.path == "/status"
                else fixture("authorize.json")
            )
            encoded = json.dumps(payload).encode("utf-8")
            return raw_response(
                payload=encoded,
                headers={
                    "content-type": "application/json",
                    **(
                        {"set-cookie": "hostile-session=private; Path=/"}
                        if request.url.path == "/status"
                        else {}
                    ),
                },
            )

        result = self.probe(handler)

        self.assertIsNone(result.failure)
        self.assertEqual(result.value.service_version, ServiceVersion("2.35.1"))
        self.assertEqual(
            [(request.method, request.url.path) for request in requests],
            [("GET", "/status"), ("POST", "/api/authorize")],
        )
        for request in requests:
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn(
                request.url.path.split("/")[1],
                {"libraries", "items", "media", "upload", "update", "delete"},
            )
        self.assertNotIn("authorization", requests[0].headers)
        self.assertEqual(
            requests[1].headers["authorization"], "Bearer synthetic-token"
        )
        self.assertNotIn("cookie", requests[0].headers)
        self.assertNotIn("cookie", requests[1].headers)

    def test_authentication_authorization_and_incompatible_endpoint_are_typed(self) -> None:
        for status, expected in (
            (401, "authentication"),
            (403, "authorization"),
            (404, "incompatible"),
            (405, "incompatible"),
            (408, "timeout"),
            (429, "rate_limited"),
        ):
            with self.subTest(status=status):
                result = self.probe(lambda _request: httpx.Response(status))
                self.assertEqual(result.failure.kind.value, expected)
                self.assertNotIn("synthetic-token", repr(result))

    def test_transport_timeout_and_connect_failure_are_safe(self) -> None:
        for error, expected in (
            (httpx.ReadTimeout("private timeout"), "timeout"),
            (httpx.ConnectError("private tls/connect"), "unavailable"),
        ):
            with self.subTest(expected=expected):
                def handler(request, selected=error):
                    raise selected

                result = self.probe(handler)
                self.assertEqual(result.failure.kind.value, expected)
                self.assertNotIn("private", repr(result))

    def test_hostile_response_matrix_is_bounded(self) -> None:
        cases = (
            (raw_response(302, headers={"location": "https://private.invalid"}), "invalid_response"),
            (raw_response(payload=b"{}", headers={"content-type": "text/plain"}), "invalid_response"),
            (raw_response(payload=b"{", headers={"content-type": "application/json"}), "invalid_response"),
            (raw_response(payload=b"x" * 65_537, headers={"content-type": "application/json"}), "resource_exhausted"),
            (raw_response(payload=b"{}", headers={"content-type": "application/json", "content-length": "65537"}), "resource_exhausted"),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                result = self.probe(lambda _request, value=response: value)
                self.assertEqual(result.failure.kind.value, expected)
                self.assertIsNone(result.value)

    def test_cancellation_and_resource_bounds_stop_before_http(self) -> None:
        called = []
        result = self.probe(
            lambda request: called.append(request),
            selected_context=context(cancelled=lambda: True),
        )
        self.assertEqual(result.failure.kind.value, "cancelled")
        self.assertEqual(called, [])
        result = self.probe(
            lambda request: raw_response(
                payload=json.dumps(fixture("status.json")).encode("utf-8"),
                headers={"content-type": "application/json"},
            ),
            selected_context=context(max_bytes=1),
        )
        self.assertEqual(result.failure.kind.value, "resource_exhausted")

    def test_profile_requires_active_user_and_positive_library_access(self) -> None:
        cases = []
        inactive = fixture("authorize.json")
        inactive["user"]["isActive"] = False
        cases.append(("inactive", inactive, ("synthetic-library",), False))
        empty = fixture("authorize.json")
        empty["user"]["permissions"]["accessAllLibraries"] = False
        empty["user"]["librariesAccessible"] = []
        cases.append(("empty", empty, ("synthetic-library",), False))
        missing = fixture("authorize.json")
        del missing["user"]["permissions"]["accessAllLibraries"]
        del missing["user"]["librariesAccessible"]
        cases.append(("missing", missing, ("synthetic-library",), False))
        malformed = fixture("authorize.json")
        malformed["user"]["permissions"]["accessAllLibraries"] = False
        malformed["user"]["librariesAccessible"] = [1]
        cases.append(("malformed", malformed, ("synthetic-library",), False))
        scoped = fixture("authorize.json")
        scoped["user"]["permissions"]["accessAllLibraries"] = False
        scoped["user"]["librariesAccessible"] = ["synthetic-library"]
        cases.append(("scoped", scoped, ("synthetic-library",), True))
        exact = fixture("authorize.json")
        exact["user"]["permissions"]["accessAllLibraries"] = False
        exact["user"]["librariesAccessible"] = [
            "synthetic-library",
            "second-library",
        ]
        cases.append(
            ("exact-multiple", exact, ("synthetic-library", "second-library"), True)
        )
        subset = fixture("authorize.json")
        subset["user"]["permissions"]["accessAllLibraries"] = False
        subset["user"]["librariesAccessible"] = ["synthetic-library"]
        cases.append(("subset", subset, ("synthetic-library", "second-library"), False))
        mismatch = fixture("authorize.json")
        mismatch["user"]["permissions"]["accessAllLibraries"] = False
        mismatch["user"]["librariesAccessible"] = ["other-library"]
        cases.append(("mismatch", mismatch, ("synthetic-library",), False))
        for name, invalid in (
            ("whitespace", " synthetic-library"),
            ("control", "synthetic\nlibrary"),
            ("oversized", "a" * 65),
        ):
            profile = fixture("authorize.json")
            profile["user"]["permissions"]["accessAllLibraries"] = False
            profile["user"]["librariesAccessible"] = [invalid]
            cases.append((name, profile, ("synthetic-library",), False))
        mixed = fixture("authorize.json")
        mixed["user"]["permissions"]["accessAllLibraries"] = False
        mixed["user"]["librariesAccessible"] = ["synthetic-library", " bad"]
        cases.append(("mixed", mixed, ("synthetic-library",), False))
        for name, flag in (("list-flag", []), ("dict-flag", {})):
            profile = fixture("authorize.json")
            profile["user"]["permissions"]["accessAllLibraries"] = flag
            profile["user"]["librariesAccessible"] = ["synthetic-library"]
            cases.append((name, profile, ("synthetic-library",), False))

        for name, profile, expected_libraries, expected_complete in cases:
            with self.subTest(name=name):
                def handler(request, selected=profile):
                    value = fixture("status.json") if request.url.path == "/status" else selected
                    return raw_response(
                        payload=json.dumps(value).encode("utf-8"),
                        headers={"content-type": "application/json"},
                    )

                result = self.probe(
                    handler, expected_libraries=expected_libraries
                )
                self.assertIsNone(result.failure)
                self.assertEqual(result.value.profile_complete, expected_complete)
                self.assertEqual(
                    result.value.media_download_allowed, expected_complete
                )
                self.assertEqual(
                    result.value.health.status,
                    HealthStatus.HEALTHY
                    if expected_complete
                    else HealthStatus.DEGRADED,
                )

    def test_version_must_be_strict_pinned_semver_before_projection(self) -> None:
        for version in (None, "private-version-sentinel", "v2.35.1", "2.35.1-beta", "02.35.1"):
            with self.subTest(version=version):
                status = fixture("status.json")
                if version is None:
                    status.pop("serverVersion")
                else:
                    status["serverVersion"] = version

                def handler(request):
                    value = status if request.url.path == "/status" else fixture("authorize.json")
                    return raw_response(
                        payload=json.dumps(value).encode("utf-8"),
                        headers={"content-type": "application/json"},
                    )

                result = self.probe(handler)
                self.assertIsNone(result.failure)
                self.assertIsNone(result.value.service_version)
                self.assertEqual(result.value.health.status, HealthStatus.DEGRADED)
                self.assertNotIn("private-version-sentinel", repr(result))

    def test_encoded_response_is_rejected_and_stream_cancellation_is_typed(self) -> None:
        encoded = raw_response(
            payload=b"not-valid-gzip",
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )
        self.assertEqual(
            self.probe(lambda _request: encoded).failure.kind.value,
            "invalid_response",
        )
        compressed_expansion = raw_response(
            payload=b"x" * 65_537,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
        )
        self.assertEqual(
            self.probe(lambda _request: compressed_expansion).failure.kind.value,
            "invalid_response",
        )

        cancelled = [False]

        class CancellingStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"{"
                cancelled[0] = True
                yield b'"app":"audiobookshelf"}'

        response = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=CancellingStream(),
        )
        result = self.probe(
            lambda _request: response,
            selected_context=context(cancelled=lambda: cancelled[0]),
        )
        self.assertEqual(result.failure.kind.value, "cancelled")


class SecretAndClientBoundaryTests(unittest.TestCase):
    def test_import_performs_no_file_or_network_io(self) -> None:
        source = str(ROOT / "src")
        script = (
            "from unittest.mock import patch; "
            "guard=AssertionError('import I/O forbidden'); "
            "p1=patch('os.open', side_effect=guard); "
            "p2=patch('socket.socket', side_effect=guard); "
            "p1.start(); p2.start(); "
            "import mechanical_librarian.adapters.library_services.audiobookshelf; "
            "import mechanical_librarian.infrastructure.security.target_secrets; "
            "print('ok')"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=os.environ | {"PYTHONPATH": source},
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok\n")

    def test_target_scope_private_file_and_replacement_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "api-key"
            path.write_text("first-token\n", encoding="utf-8")
            path.chmod(0o600)
            credentials = PrivateFileTargetCredentials()
            reference = SecretReference(str(path))
            self.assertTrue(credentials.configured(reference))
            handle = SecretHandle(reference, SecretScope.TARGET_SERVICE)
            credentials.target_client(
                handle, ServiceEndpoint("https://synthetic.invalid")
            ).close()

            path.write_text("replacement-token\n", encoding="utf-8")
            path.chmod(0o600)
            captured = {}
            real_client = httpx.Client

            def construct(*args, **kwargs):
                captured.update(kwargs)
                return real_client(transport=httpx.MockTransport(lambda _r: httpx.Response(200)), **kwargs)

            with patch("mechanical_librarian.infrastructure.security.target_secrets.httpx.Client", side_effect=construct):
                client = credentials.target_client(
                    handle, ServiceEndpoint("https://synthetic.invalid")
                )
            try:
                self.assertEqual(captured["headers"]["Accept-Encoding"], "identity")
                self.assertIn("auth", captured)
                self.assertFalse(captured["trust_env"])
                self.assertFalse(captured["follow_redirects"])
                self.assertTrue(captured["verify"])
                self.assertEqual(captured["limits"].max_connections, 2)
                self.assertEqual(captured["timeout"].connect, 3.0)
                self.assertEqual(captured["timeout"].read, 5.0)
                self.assertEqual(captured["timeout"].write, 3.0)
                self.assertEqual(captured["timeout"].pool, 2.0)
            finally:
                client.close()

    def test_missing_and_overpermissive_files_are_safe(self) -> None:
        credentials = PrivateFileTargetCredentials()
        reference = SecretReference("/missing/private-token")
        self.assertFalse(credentials.configured(reference))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "open-key"
            path.write_text("private-sentinel", encoding="utf-8")
            path.chmod(0o644)
            self.assertFalse(credentials.configured(SecretReference(str(path))))

    def test_non_target_scope_is_rejected_before_file_or_network_access(self) -> None:
        credentials = PrivateFileTargetCredentials()
        reference = SecretReference("/private/provider-or-reviewer-secret")
        for scope in (SecretScope.METADATA_PROVIDER, SecretScope.REVIEWER):
            with self.subTest(scope=scope), patch(
                "mechanical_librarian.infrastructure.security.target_secrets._read"
            ) as read, patch(
                "mechanical_librarian.infrastructure.security.target_secrets.httpx.Client"
            ) as client:
                with self.assertRaises(SecretFileUnavailable):
                    credentials.target_client(
                        SecretHandle(reference, scope),
                        ServiceEndpoint("https://synthetic.invalid"),
                    )
                read.assert_not_called()
                client.assert_not_called()


class ApplicationProfileTests(unittest.TestCase):
    class Probe:
        def __init__(self, facts):
            self.facts = facts

        def probe(self, _context):
            return PortResult.success(self.facts)

    def facts(self, version, *, complete=True, download=True):
        return TargetProbeFacts(
            None if version is None else ServiceVersion(version),
            complete,
            download if complete else False,
            AdapterVersion("abs-connection-v1"),
            CompatibilityVersion("abs-2.35.1-fixture-v1"),
            HealthReport(
                HealthStatus.HEALTHY if complete else HealthStatus.DEGRADED,
                NOW,
                SafeSummary("connection profile verified" if complete else "connection profile degraded"),
            ),
        )

    def run(self, facts):
        return InspectLibraryConnection(
            AdapterId("audiobookshelf"),
            ConnectionId("synthetic-connection"),
            (LibraryId("synthetic-library"),),
            self.Probe(facts),
        ).run(context())

    def test_supported_older_newer_unknown_and_partial_profiles(self) -> None:
        supported = self.run(self.facts("2.35.1"))
        self.assertEqual(supported.outcome, ConnectionProbeOutcome.READY)
        self.assertEqual(supported.inspection.compatibility, CompatibilityState.SUPPORTED)
        states = {item.capability: item.state for item in supported.inspection.capabilities}
        self.assertEqual(states[ConnectionCapability.AUTHORIZATION], CapabilityState.TESTED)
        self.assertEqual(states[ConnectionCapability.INCREMENTAL_SCAN], CapabilityState.UNAVAILABLE)
        self.assertEqual(states[ConnectionCapability.METADATA_FIELDS], CapabilityState.SUPPORTED)
        self.assertEqual(states[ConnectionCapability.ROLLBACK_READ], CapabilityState.UNAVAILABLE)
        self.assertEqual(states[ConnectionCapability.RATE_LIMITS], CapabilityState.UNAVAILABLE)
        older = self.run(self.facts("2.25.9"))
        self.assertEqual(older.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertIn(
            CapabilityState.INCOMPATIBLE,
            {item.state for item in older.inspection.capabilities},
        )
        newer = self.run(self.facts("2.36.0"))
        self.assertEqual(newer.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertEqual(newer.inspection.compatibility, CompatibilityState.NEWER)
        self.assertIn(CapabilityState.INCOMPATIBLE, {item.state for item in newer.inspection.capabilities})
        unknown = self.run(self.facts(None))
        self.assertEqual(unknown.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertEqual(unknown.inspection.compatibility, CompatibilityState.UNKNOWN)
        partial = self.run(self.facts("2.35.1", complete=False, download=False))
        partial_states = {item.capability: item.state for item in partial.inspection.capabilities}
        self.assertEqual(partial_states[ConnectionCapability.INVENTORY_READ], CapabilityState.UNAVAILABLE)
        self.assertEqual(partial.inspection.adapter_contract, AdapterVersion("abs-connection-v1"))

    def test_machine_runtime_and_human_compatibility_views_cannot_drift(self) -> None:
        manifest = json.loads(COMPATIBILITY_MANIFEST.read_text(encoding="utf-8"))
        supported = self.run(self.facts(manifest["pinned_version"]))
        runtime = {
            item.capability.value: item.state.value
            for item in supported.inspection.capabilities
        }
        self.assertEqual(runtime, manifest["capability_states"])
        self.assertEqual(
            supported.inspection.adapter_contract.value,
            manifest["adapter_contract"],
        )
        self.assertEqual(
            supported.inspection.fixture_revision.value,
            manifest["fixture_revision"],
        )
        document = COMPATIBILITY_DOCUMENT.read_text(encoding="utf-8")
        table = document.split("<!-- f04-capabilities:start -->", 1)[1].split(
            "<!-- f04-capabilities:end -->", 1
        )[0]
        human = {}
        for line in table.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 2 and cells[0] in manifest["capability_states"]:
                human[cells[0]] = cells[1]
        self.assertEqual(human, manifest["capability_states"])

    def test_hostile_connection_identifier_is_redacted_from_repr(self) -> None:
        result = InspectLibraryConnection(
            AdapterId("audiobookshelf"),
            ConnectionId("private-connection-sentinel"),
            (LibraryId("synthetic-library"),),
            self.Probe(self.facts("2.35.1")),
        ).run(context())
        self.assertTrue(result.succeeded)
        self.assertNotIn("private-connection-sentinel", repr(result))


if __name__ == "__main__":
    unittest.main()
