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
    ConnectionProbeResult,
    InspectLibraryConnection,
    TargetProbeFacts,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    HealthReport,
    HealthStatus,
    LibraryConnectionDiagnosticsRequest,
    OperationContext,
    PortFailure,
    PortResult,
    ResourceBounds,
    RetryAdvice,
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
COMPATIBILITY_MANIFEST = (
    ROOT / "docs" / "reference" / "audiobookshelf-f04-compatibility.json"
)
COMPATIBILITY_DOCUMENT = ROOT / "docs" / "reference" / "compatibility.md"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LIBRARY_ID = "01234567-89ab-4def-8123-456789abcdef"
LETTER_LIBRARY_ID = "a1234567-89ab-4def-8123-456789abcdef"
SECOND_LIBRARY_ID = "11234567-89ab-4def-8123-456789abcdef"
EXTRA_LIBRARY_ID = "21234567-89ab-4def-8123-456789abcdef"
OTHER_LIBRARY_ID = "31234567-89ab-4def-8123-456789abcdef"


def context(
    *,
    cancelled=lambda: False,
    max_bytes: int = 65_536,
    deadline: datetime | None = None,
) -> OperationContext:
    return OperationContext(
        Deadline(deadline or NOW + timedelta(seconds=30)),
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
        expected_libraries=(LIBRARY_ID,),
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

    def test_pinned_contract_uses_only_status_and_read_only_authorize_post(
        self,
    ) -> None:
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
        self.assertEqual(requests[1].headers["authorization"], "Bearer synthetic-token")
        self.assertNotIn("cookie", requests[0].headers)
        self.assertNotIn("cookie", requests[1].headers)

    def test_status_failures_stop_before_authorization(self) -> None:
        for status, expected in (
            (401, "invalid_response"),
            (403, "invalid_response"),
            (404, "incompatible"),
            (405, "incompatible"),
            (408, "timeout"),
            (429, "rate_limited"),
        ):
            with self.subTest(status=status):
                requests = []

                def handler(request, selected=status):
                    requests.append(request)
                    return httpx.Response(selected)

                result = self.probe(handler)

                self.assertEqual(result.failure.kind.value, expected)
                self.assertEqual(
                    [(request.method, request.url.path) for request in requests],
                    [("GET", "/status")],
                )
                self.assertNotIn("authorization", requests[0].headers)
                self.assertNotIn("synthetic-token", repr(result))

    def test_authorization_route_failures_are_typed_after_pinned_status(self) -> None:
        for status, expected in (
            (401, "authentication"),
            (403, "authorization"),
            (404, "incompatible"),
            (405, "incompatible"),
            (408, "timeout"),
            (429, "rate_limited"),
        ):
            with self.subTest(status=status):
                requests = []

                def handler(request, selected=status):
                    requests.append(request)
                    if request.url.path == "/status":
                        return raw_response(
                            payload=json.dumps(fixture("status.json")).encode("utf-8"),
                            headers={"content-type": "application/json"},
                        )
                    return httpx.Response(selected)

                result = self.probe(handler)

                self.assertEqual(result.failure.kind.value, expected)
                self.assertEqual(
                    [(request.method, request.url.path) for request in requests],
                    [("GET", "/status"), ("POST", "/api/authorize")],
                )
                self.assertNotIn("authorization", requests[0].headers)
                self.assertEqual(
                    requests[1].headers["authorization"],
                    "Bearer synthetic-token",
                )
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

    def test_transport_timeouts_are_clipped_to_remaining_operation_deadline(
        self,
    ) -> None:
        observed = []

        def handler(request):
            observed.append(request.extensions["timeout"])
            payload = (
                fixture("status.json")
                if request.url.path == "/status"
                else fixture("authorize.json")
            )
            return raw_response(
                payload=json.dumps(payload).encode("utf-8"),
                headers={"content-type": "application/json"},
            )

        result = self.probe(
            handler,
            selected_context=context(deadline=NOW + timedelta(seconds=1)),
        )

        self.assertIsNone(result.failure)
        self.assertEqual(len(observed), 2)
        for timeout in observed:
            self.assertEqual(
                timeout,
                {"connect": 1.0, "read": 1.0, "write": 1.0, "pool": 1.0},
            )

    def test_hostile_response_matrix_is_bounded(self) -> None:
        cases = (
            (
                raw_response(302, headers={"location": "https://private.invalid"}),
                "invalid_response",
            ),
            (
                raw_response(payload=b"{}", headers={"content-type": "text/plain"}),
                "invalid_response",
            ),
            (
                raw_response(
                    payload=b"{", headers={"content-type": "application/json"}
                ),
                "invalid_response",
            ),
            (
                raw_response(
                    payload=b"x" * 65_537, headers={"content-type": "application/json"}
                ),
                "resource_exhausted",
            ),
            (
                raw_response(
                    payload=b"{}",
                    headers={
                        "content-type": "application/json",
                        "content-length": "65537",
                    },
                ),
                "resource_exhausted",
            ),
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

    def test_inventory_response_meter_bounds_combined_probe_bodies(self) -> None:
        status = json.dumps(fixture("status.json")).encode("utf-8")
        authorize = json.dumps(fixture("authorize.json")).encode("utf-8")
        observed = 0

        def meter(amount: int) -> bool:
            nonlocal observed
            observed += amount
            return observed <= len(status)

        def handler(request):
            payload = status if request.url.path == "/status" else authorize
            return raw_response(
                payload=payload, headers={"content-type": "application/json"}
            )

        result = AudiobookshelfConnectionProbe(
            ServiceEndpoint("https://synthetic.invalid"),
            client_factory=client_factory(handler),
            response_meter=meter,
        ).probe(
            LibraryConnectionDiagnosticsRequest((LibraryId(LIBRARY_ID),), context())
        )

        self.assertEqual(result.failure.kind, FailureKind.RESOURCE_EXHAUSTED)
        self.assertEqual(observed, len(status) + len(authorize))

    def test_invalid_expected_library_stops_before_client_or_network(self) -> None:
        for value in (
            "synthetic-library",
            "a1234567-89ab-3def-8123-456789abcdef",
            "a1234567-89ab-4def-7123-456789abcdef",
        ):
            with self.subTest(value=value):
                clients = []
                probe = AudiobookshelfConnectionProbe(
                    ServiceEndpoint("https://synthetic.invalid"),
                    client_factory=lambda: clients.append(True),  # type: ignore[arg-type,return-value]
                )

                result = probe.probe(
                    LibraryConnectionDiagnosticsRequest(
                        (LibraryId(value),),
                        context(),
                    )
                )

                self.assertEqual(result.failure.kind, FailureKind.INVALID_REQUEST)
                self.assertEqual(clients, [])

    def test_profile_requires_active_user_and_positive_library_access(self) -> None:
        cases = []
        inactive = fixture("authorize.json")
        inactive["user"]["isActive"] = False
        cases.append(("inactive", inactive, (LIBRARY_ID,), False))
        empty = fixture("authorize.json")
        empty["user"]["permissions"]["accessAllLibraries"] = False
        empty["user"]["librariesAccessible"] = []
        cases.append(("empty", empty, (LIBRARY_ID,), False))
        missing = fixture("authorize.json")
        del missing["user"]["permissions"]["accessAllLibraries"]
        del missing["user"]["librariesAccessible"]
        cases.append(("missing", missing, (LIBRARY_ID,), False))
        missing_flag_covering = fixture("authorize.json")
        del missing_flag_covering["user"]["permissions"]["accessAllLibraries"]
        missing_flag_covering["user"]["librariesAccessible"] = [LIBRARY_ID]
        cases.append(
            (
                "missing-flag-covering",
                missing_flag_covering,
                (LIBRARY_ID,),
                False,
            )
        )
        null_flag_covering = fixture("authorize.json")
        null_flag_covering["user"]["permissions"]["accessAllLibraries"] = None
        null_flag_covering["user"]["librariesAccessible"] = [LIBRARY_ID]
        cases.append(
            (
                "null-flag-covering",
                null_flag_covering,
                (LIBRARY_ID,),
                False,
            )
        )
        malformed = fixture("authorize.json")
        malformed["user"]["permissions"]["accessAllLibraries"] = False
        malformed["user"]["librariesAccessible"] = [1]
        cases.append(("malformed", malformed, (LIBRARY_ID,), False))
        scoped = fixture("authorize.json")
        scoped["user"]["permissions"]["accessAllLibraries"] = False
        scoped["user"]["librariesAccessible"] = [LETTER_LIBRARY_ID]
        cases.append(("scoped", scoped, (LETTER_LIBRARY_ID,), True))
        uuid = LIBRARY_ID
        scoped_uuid = fixture("authorize.json")
        scoped_uuid["user"]["permissions"]["accessAllLibraries"] = False
        scoped_uuid["user"]["librariesAccessible"] = [uuid, uuid]
        cases.append(("scoped-uuid", scoped_uuid, (uuid,), True))
        mismatched_uuid = fixture("authorize.json")
        mismatched_uuid["user"]["permissions"]["accessAllLibraries"] = False
        mismatched_uuid["user"]["librariesAccessible"] = [
            "11234567-89ab-4def-8123-456789abcdef"
        ]
        cases.append(("mismatched-uuid", mismatched_uuid, (uuid,), False))
        malformed_uuid = fixture("authorize.json")
        malformed_uuid["user"]["permissions"]["accessAllLibraries"] = False
        malformed_uuid["user"]["librariesAccessible"] = [
            uuid,
            "01234567-89ab-3def-8123-456789abcdef",
        ]
        cases.append(("malformed-uuid", malformed_uuid, (uuid,), False))
        malformed_letter_uuid = fixture("authorize.json")
        malformed_letter_uuid["user"]["permissions"]["accessAllLibraries"] = False
        malformed_letter_uuid["user"]["librariesAccessible"] = [
            uuid,
            "a1234567-89ab-3def-8123-456789abcdef",
        ]
        cases.append(("malformed-letter-uuid", malformed_letter_uuid, (uuid,), False))
        exact = fixture("authorize.json")
        exact["user"]["permissions"]["accessAllLibraries"] = False
        exact["user"]["librariesAccessible"] = [
            LIBRARY_ID,
            SECOND_LIBRARY_ID,
        ]
        cases.append(("exact-multiple", exact, (LIBRARY_ID, SECOND_LIBRARY_ID), True))
        duplicate_covering = fixture("authorize.json")
        duplicate_covering["user"]["permissions"]["accessAllLibraries"] = False
        duplicate_covering["user"]["librariesAccessible"] = [
            SECOND_LIBRARY_ID,
            EXTRA_LIBRARY_ID,
            LIBRARY_ID,
            SECOND_LIBRARY_ID,
        ]
        cases.append(
            (
                "duplicate-covering",
                duplicate_covering,
                (LIBRARY_ID, SECOND_LIBRARY_ID),
                True,
            )
        )
        duplicate_subset = fixture("authorize.json")
        duplicate_subset["user"]["permissions"]["accessAllLibraries"] = False
        duplicate_subset["user"]["librariesAccessible"] = [
            LIBRARY_ID,
            LIBRARY_ID,
        ]
        cases.append(
            (
                "duplicate-subset",
                duplicate_subset,
                (LIBRARY_ID, SECOND_LIBRARY_ID),
                False,
            )
        )
        duplicate_malformed = fixture("authorize.json")
        duplicate_malformed["user"]["permissions"]["accessAllLibraries"] = False
        duplicate_malformed["user"]["librariesAccessible"] = [
            LIBRARY_ID,
            LIBRARY_ID,
            " bad",
        ]
        cases.append(
            (
                "duplicate-malformed",
                duplicate_malformed,
                (LIBRARY_ID,),
                False,
            )
        )
        subset = fixture("authorize.json")
        subset["user"]["permissions"]["accessAllLibraries"] = False
        subset["user"]["librariesAccessible"] = [LIBRARY_ID]
        cases.append(("subset", subset, (LIBRARY_ID, SECOND_LIBRARY_ID), False))
        mismatch = fixture("authorize.json")
        mismatch["user"]["permissions"]["accessAllLibraries"] = False
        mismatch["user"]["librariesAccessible"] = [OTHER_LIBRARY_ID]
        cases.append(("mismatch", mismatch, (LIBRARY_ID,), False))
        for name, invalid in (
            ("whitespace", f" {LIBRARY_ID}"),
            ("control", LIBRARY_ID + "\n"),
            ("oversized", "a" * 65),
        ):
            profile = fixture("authorize.json")
            profile["user"]["permissions"]["accessAllLibraries"] = False
            profile["user"]["librariesAccessible"] = [invalid]
            cases.append((name, profile, (LIBRARY_ID,), False))
        mixed = fixture("authorize.json")
        mixed["user"]["permissions"]["accessAllLibraries"] = False
        mixed["user"]["librariesAccessible"] = [LIBRARY_ID, " bad"]
        cases.append(("mixed", mixed, (LIBRARY_ID,), False))
        malformed_permissions = fixture("authorize.json")
        malformed_permissions["user"]["permissions"] = []
        cases.append(
            (
                "malformed-permissions",
                malformed_permissions,
                (LIBRARY_ID,),
                False,
            )
        )
        malformed_download = fixture("authorize.json")
        malformed_download["user"]["permissions"]["download"] = "yes"
        cases.append(("malformed-download", malformed_download, (LIBRARY_ID,), False))
        for name, flag in (("list-flag", []), ("dict-flag", {})):
            profile = fixture("authorize.json")
            profile["user"]["permissions"]["accessAllLibraries"] = flag
            profile["user"]["librariesAccessible"] = [LIBRARY_ID]
            cases.append((name, profile, (LIBRARY_ID,), False))

        for name, profile, expected_libraries, expected_complete in cases:
            with self.subTest(name=name):

                def handler(request, selected=profile):
                    value = (
                        fixture("status.json")
                        if request.url.path == "/status"
                        else selected
                    )
                    return raw_response(
                        payload=json.dumps(value).encode("utf-8"),
                        headers={"content-type": "application/json"},
                    )

                result = self.probe(handler, expected_libraries=expected_libraries)
                self.assertIsNone(result.failure)
                self.assertEqual(result.value.profile_complete, expected_complete)
                self.assertEqual(result.value.media_download_allowed, expected_complete)
                self.assertEqual(
                    result.value.health.status,
                    HealthStatus.HEALTHY
                    if expected_complete
                    else HealthStatus.DEGRADED,
                )
                application_result = InspectLibraryConnection(
                    AdapterId("audiobookshelf"),
                    ConnectionId("synthetic-connection"),
                    tuple(LibraryId(value) for value in expected_libraries),
                    ApplicationProfileTests.Probe(result.value),
                ).run(context())
                self.assertEqual(
                    application_result.outcome,
                    (
                        ConnectionProbeOutcome.READY
                        if expected_complete
                        else ConnectionProbeOutcome.AUTHORIZATION_FAILED
                    ),
                )
                self.assertEqual(application_result.succeeded, expected_complete)
                if expected_complete:
                    self.assertIsNotNone(application_result.inspection)
                else:
                    self.assertIsNone(application_result.inspection)

    def test_profile_requires_unrestricted_content_visibility(self) -> None:
        cases = []
        for field in ("accessAllTags", "accessExplicitContent"):
            for name, value in (
                ("false", False),
                ("null", None),
                ("wrong-type", "yes"),
            ):
                profile = fixture("authorize.json")
                profile["user"]["permissions"][field] = value
                cases.append((f"{field}-{name}", profile))
            missing = fixture("authorize.json")
            del missing["user"]["permissions"][field]
            cases.append((f"{field}-missing", missing))

        for mode, selected in (
            ("allowlist", ["private-tag-sentinel"]),
            ("denylist", ["private-tag-sentinel"]),
            ("empty-allowlist", []),
            ("empty-denylist", []),
        ):
            profile = fixture("authorize.json")
            profile["user"]["permissions"]["accessAllTags"] = False
            profile["user"]["permissions"]["selectedTagsNotAccessible"] = mode in {
                "denylist",
                "empty-denylist",
            }
            profile["user"]["itemTagsSelected"] = selected
            cases.append((mode, profile))

        combined = fixture("authorize.json")
        combined["user"]["permissions"]["accessAllTags"] = False
        combined["user"]["permissions"]["accessExplicitContent"] = False
        combined["user"]["itemTagsSelected"] = ["private-tag-sentinel"]
        cases.append(("combined", combined))

        for name, profile in cases:
            with self.subTest(name=name):

                def handler(request, selected=profile):
                    value = (
                        fixture("status.json")
                        if request.url.path == "/status"
                        else selected
                    )
                    return raw_response(
                        payload=json.dumps(value).encode("utf-8"),
                        headers={"content-type": "application/json"},
                    )

                result = self.probe(handler)

                self.assertIsNone(result.failure)
                self.assertFalse(result.value.profile_complete)
                self.assertFalse(result.value.media_download_allowed)
                self.assertEqual(
                    result.value.health.status,
                    HealthStatus.DEGRADED,
                )
                application_result = InspectLibraryConnection(
                    AdapterId("audiobookshelf"),
                    ConnectionId("synthetic-connection"),
                    (LibraryId(LIBRARY_ID),),
                    ApplicationProfileTests.Probe(result.value),
                ).run(context())
                self.assertEqual(
                    application_result.outcome,
                    ConnectionProbeOutcome.AUTHORIZATION_FAILED,
                )
                self.assertIsNone(application_result.inspection)
                self.assertNotIn("private-tag-sentinel", repr(result))

    def test_download_permission_only_controls_media_capability(self) -> None:
        profile = fixture("authorize.json")
        profile["user"]["permissions"]["download"] = False

        def handler(request):
            value = fixture("status.json") if request.url.path == "/status" else profile
            return raw_response(
                payload=json.dumps(value).encode("utf-8"),
                headers={"content-type": "application/json"},
            )

        result = self.probe(handler)

        self.assertIsNone(result.failure)
        self.assertTrue(result.value.profile_complete)
        self.assertFalse(result.value.media_download_allowed)
        self.assertEqual(result.value.health.status, HealthStatus.HEALTHY)
        application_result = InspectLibraryConnection(
            AdapterId("audiobookshelf"),
            ConnectionId("synthetic-connection"),
            (LibraryId(LIBRARY_ID),),
            ApplicationProfileTests.Probe(result.value),
        ).run(context())
        self.assertEqual(application_result.outcome, ConnectionProbeOutcome.READY)
        states = {
            item.capability: item.state
            for item in application_result.inspection.capabilities
        }
        self.assertEqual(
            states[ConnectionCapability.INVENTORY_READ],
            CapabilityState.SUPPORTED,
        )
        self.assertEqual(
            states[ConnectionCapability.MEDIA_FILE_READ],
            CapabilityState.UNAVAILABLE,
        )

    def test_wrong_service_identity_stops_before_authorization(self) -> None:
        for status in ({}, {"app": "other", "serverVersion": "2.35.1"}):
            with self.subTest(status=status):
                requests = []

                def handler(request):
                    requests.append(request)
                    return raw_response(
                        payload=json.dumps(status).encode("utf-8"),
                        headers={"content-type": "application/json"},
                    )

                result = self.probe(handler)

                self.assertEqual(result.failure.kind, FailureKind.INVALID_RESPONSE)
                self.assertIsNone(result.value)
                self.assertEqual(
                    [(request.method, request.url.path) for request in requests],
                    [("GET", "/status")],
                )
                self.assertNotIn("authorization", requests[0].headers)
                self.assertNotIn("synthetic-token", repr(result))

    def test_unqualified_versions_stop_before_authorization_and_remain_typed(
        self,
    ) -> None:
        cases = (
            (None, CompatibilityState.UNKNOWN, None),
            ("private-version-sentinel", CompatibilityState.UNKNOWN, None),
            ("v2.35.1", CompatibilityState.UNKNOWN, None),
            ("2.35.1-beta", CompatibilityState.UNKNOWN, None),
            ("02.35.1", CompatibilityState.UNKNOWN, None),
            ("2.25.9", CompatibilityState.OLDER, ServiceVersion("2.25.9")),
            ("2.34.0", CompatibilityState.OLDER, ServiceVersion("2.34.0")),
            ("2.36.0", CompatibilityState.NEWER, ServiceVersion("2.36.0")),
        )
        for version, expected_compatibility, expected_version in cases:
            with self.subTest(version=version):
                requests = []
                status = fixture("status.json")
                if version is None:
                    status.pop("serverVersion")
                else:
                    status["serverVersion"] = version

                def handler(request):
                    requests.append(request)
                    return raw_response(
                        payload=json.dumps(status).encode("utf-8"),
                        headers={"content-type": "application/json"},
                    )

                result = self.probe(handler)
                self.assertIsNone(result.failure)
                self.assertEqual(result.value.service_version, expected_version)
                self.assertEqual(result.value.health.status, HealthStatus.DEGRADED)
                self.assertFalse(result.value.profile_complete)
                self.assertEqual(
                    [(request.method, request.url.path) for request in requests],
                    [("GET", "/status")],
                )
                self.assertNotIn("authorization", requests[0].headers)
                self.assertNotIn("private-version-sentinel", repr(result))

                application_result = InspectLibraryConnection(
                    AdapterId("audiobookshelf"),
                    ConnectionId("synthetic-connection"),
                    (LibraryId(LIBRARY_ID),),
                    ApplicationProfileTests.Probe(result.value),
                ).run(context())
                self.assertEqual(
                    application_result.outcome,
                    ConnectionProbeOutcome.INCOMPATIBLE,
                )
                self.assertEqual(
                    application_result.inspection.compatibility,
                    expected_compatibility,
                )
                states = {
                    item.capability: item.state
                    for item in application_result.inspection.capabilities
                }
                self.assertEqual(
                    states[ConnectionCapability.AUTHENTICATION],
                    CapabilityState.INCOMPATIBLE,
                )
                self.assertEqual(
                    states[ConnectionCapability.AUTHORIZATION],
                    CapabilityState.INCOMPATIBLE,
                )

    def test_encoded_response_is_rejected_and_stream_cancellation_is_typed(
        self,
    ) -> None:
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
                return real_client(
                    transport=httpx.MockTransport(lambda _r: httpx.Response(200)),
                    **kwargs,
                )

            with patch(
                "mechanical_librarian.infrastructure.security.target_secrets.httpx.Client",
                side_effect=construct,
            ):
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
            with (
                self.subTest(scope=scope),
                patch(
                    "mechanical_librarian.infrastructure.security.target_secrets._read"
                ) as read,
                patch(
                    "mechanical_librarian.infrastructure.security.target_secrets.httpx.Client"
                ) as client,
            ):
                with self.assertRaises(SecretFileUnavailable):
                    credentials.target_client(
                        SecretHandle(reference, scope),
                        ServiceEndpoint("https://synthetic.invalid"),
                    )
                read.assert_not_called()
                client.assert_not_called()

    def test_inventory_credential_is_exact_origin_get_route_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory-key"
            path.write_text("private-inventory-token\n", encoding="utf-8")
            path.chmod(0o600)
            credentials = PrivateFileTargetCredentials()
            handle = SecretHandle(
                SecretReference(str(path)), SecretScope.TARGET_SERVICE
            )
            client = credentials.inventory_client(
                handle, ServiceEndpoint("https://synthetic.invalid")
            )
            try:
                auth = client.auth
                allowed = (
                    f"https://synthetic.invalid/api/libraries/{LIBRARY_ID}/items?page=0",
                    f"https://synthetic.invalid/api/items/{LIBRARY_ID}?expanded=1",
                    "https://synthetic.invalid/api/libraries",
                )
                for url in allowed:
                    request = httpx.Request("GET", url)
                    next(auth.auth_flow(request))
                    self.assertIn("authorization", request.headers)
                for method, url in (
                    ("POST", "https://synthetic.invalid/api/libraries"),
                    ("GET", "https://other.invalid/api/libraries"),
                    ("GET", "https://synthetic.invalid/status"),
                    ("GET", "https://synthetic.invalid/api/authorize"),
                    ("GET", "https://synthetic.invalid/api/items/not-a-uuid"),
                ):
                    request = httpx.Request(method, url)
                    next(auth.auth_flow(request))
                    self.assertNotIn("authorization", request.headers)
            finally:
                client.close()
            with self.assertRaises(SecretFileUnavailable):
                credentials.inventory_client(
                    handle, ServiceEndpoint("https://synthetic.invalid/prefix")
                )
            with self.assertRaises(SecretFileUnavailable):
                credentials.inventory_client(
                    handle, ServiceEndpoint("http://synthetic.invalid")
                )


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
                SafeSummary(
                    "connection profile verified"
                    if complete
                    else "connection profile degraded"
                ),
            ),
        )

    def run_probe(self, facts):
        return InspectLibraryConnection(
            AdapterId("audiobookshelf"),
            ConnectionId("synthetic-connection"),
            (LibraryId("synthetic-library"),),
            self.Probe(facts),
        ).run(context())

    def test_supported_older_newer_unknown_and_partial_profiles(self) -> None:
        supported = self.run_probe(self.facts("2.35.1"))
        self.assertEqual(supported.outcome, ConnectionProbeOutcome.READY)
        self.assertEqual(
            supported.inspection.compatibility, CompatibilityState.SUPPORTED
        )
        states = {
            item.capability: item.state for item in supported.inspection.capabilities
        }
        self.assertEqual(
            states[ConnectionCapability.AUTHORIZATION], CapabilityState.TESTED
        )
        self.assertEqual(
            states[ConnectionCapability.INCREMENTAL_SCAN], CapabilityState.UNAVAILABLE
        )
        self.assertEqual(
            states[ConnectionCapability.METADATA_FIELDS], CapabilityState.SUPPORTED
        )
        self.assertEqual(
            states[ConnectionCapability.ROLLBACK_READ], CapabilityState.UNAVAILABLE
        )
        self.assertEqual(
            states[ConnectionCapability.RATE_LIMITS], CapabilityState.UNAVAILABLE
        )
        older = self.run_probe(self.facts("2.25.9"))
        self.assertEqual(older.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertEqual(
            {item.capability: item.state for item in older.inspection.capabilities}[
                ConnectionCapability.AUTHENTICATION
            ],
            CapabilityState.INCOMPATIBLE,
        )
        self.assertIn(
            CapabilityState.INCOMPATIBLE,
            {item.state for item in older.inspection.capabilities},
        )
        newer = self.run_probe(self.facts("2.36.0"))
        self.assertEqual(newer.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertEqual(newer.inspection.compatibility, CompatibilityState.NEWER)
        self.assertIn(
            CapabilityState.INCOMPATIBLE,
            {item.state for item in newer.inspection.capabilities},
        )
        unknown = self.run_probe(self.facts(None))
        self.assertEqual(unknown.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertEqual(unknown.inspection.compatibility, CompatibilityState.UNKNOWN)
        partial = self.run_probe(self.facts("2.35.1", complete=False, download=False))
        self.assertEqual(partial.outcome, ConnectionProbeOutcome.AUTHORIZATION_FAILED)
        self.assertIsNone(partial.inspection)

    def test_incomplete_incompatible_profile_retains_compatibility_precedence(
        self,
    ) -> None:
        result = self.run_probe(self.facts("2.36.0", complete=False, download=False))

        self.assertEqual(result.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertFalse(result.succeeded)
        self.assertIsNotNone(result.inspection)
        self.assertEqual(result.inspection.compatibility, CompatibilityState.NEWER)
        self.assertIn(
            CapabilityState.INCOMPATIBLE,
            {item.state for item in result.inspection.capabilities},
        )

    def test_failure_sourced_incompatible_result_requires_no_inspection(self) -> None:
        class FailedProbe:
            def probe(self, _request):
                return PortResult.failed(
                    PortFailure(
                        FailureKind.INCOMPATIBLE,
                        RetryAdvice(False),
                        SafeSummary("connection probe unsupported"),
                    )
                )

        result = InspectLibraryConnection(
            AdapterId("audiobookshelf"),
            ConnectionId("synthetic-connection"),
            (LibraryId("synthetic-library"),),
            FailedProbe(),
        ).run(context())

        self.assertEqual(result.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.inspection)

    def test_adapter_incompatible_endpoints_project_without_raising(self) -> None:
        for status in (404, 405):
            with self.subTest(status=status):
                probe = AudiobookshelfConnectionProbe(
                    ServiceEndpoint("https://synthetic.invalid"),
                    client_factory=client_factory(
                        lambda _request, selected=status: httpx.Response(selected)
                    ),
                )
                result = InspectLibraryConnection(
                    AdapterId("audiobookshelf"),
                    ConnectionId("synthetic-connection"),
                    (LibraryId(LIBRARY_ID),),
                    probe,
                ).run(context())

                self.assertEqual(result.outcome, ConnectionProbeOutcome.INCOMPATIBLE)
                self.assertFalse(result.succeeded)
                self.assertIsNone(result.inspection)

    def test_connection_probe_result_rejects_invalid_cardinality(self) -> None:
        inspection = self.run_probe(self.facts("2.35.1")).inspection
        self.assertIsNotNone(inspection)

        with self.assertRaises(ValueError):
            ConnectionProbeResult(ConnectionProbeOutcome.READY)
        with self.assertRaises(ValueError):
            ConnectionProbeResult(
                ConnectionProbeOutcome.AUTHORIZATION_FAILED,
                inspection,
            )
        with self.assertRaises(ValueError):
            ConnectionProbeResult(
                ConnectionProbeOutcome.INCOMPATIBLE,
                object(),  # type: ignore[arg-type]
            )

        self.assertEqual(
            ConnectionProbeResult(ConnectionProbeOutcome.INCOMPATIBLE).outcome,
            ConnectionProbeOutcome.INCOMPATIBLE,
        )

    def test_machine_runtime_and_human_compatibility_views_cannot_drift(self) -> None:
        manifest = json.loads(COMPATIBILITY_MANIFEST.read_text(encoding="utf-8"))
        supported = self.run_probe(self.facts(manifest["pinned_version"]))
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
