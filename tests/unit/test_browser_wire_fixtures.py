"""The browser's wire fixtures are what the in-repo server sends.

Each fixture under `frontend/tests` that this module names is compared with a
response of the real review application, driven through a signed-in
`TestClient` over the in-repo repository and fakes. The decode test in
`frontend/tests/wireFixtures.decode.test.ts` then decodes every one with the
generated response decoders, so a fixture can neither drift from the server
nor from the decoders (#1576).

Server-generated random identifiers (attempt and candidate identities and what
derives from them) are committed as stable placeholders and substituted from
the live scenario before each comparison, as the identifier-evidence capture
in `test_review_web_read_models.py` does.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    canonical_json,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.description_advice import DescriptionAdviceService
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.description_advice import (
    DescriptionAdviceBinding,
    DescriptionAdviceSettings,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.description_advice import _prepared
from tests.support.field_effects import _EffectEnvironment, _plan
from tests.support.field_effects import effects as effects
from tests.support.proposal_evidence import _report
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from tests.unit.test_reviewer_access_application import _Auth, _OpenRouter

FIXTURES = Path("frontend/tests")
WIRE = FIXTURES / "wire"
ORIGIN = "https://review.test"
IDENTITY = ("gpt-5.6-sol", b"synthetic-contained-profile")
NOW = 1_000


def _runner(
    _profile: bytes,
    instruction: bytes,
    inputs: bytes,
    schema: bytes,
    _images: tuple[tuple[str, str, bytes], ...],
) -> str:
    """The contained reviewer's answer: the authored synthetic description."""
    request = ContainedReviewRequest(
        instruction.decode(), inputs.decode(), schema.decode()
    )
    return canonical_json(
        {
            "request_sha256": request.digest,
            "result": {
                "description": "Authored synthetic description.",
                "disposition": "ready",
            },
        }
    )


@dataclass
class _Live:
    """One signed-in client over a real repository, and its random ids."""

    client: TestClient
    headers: dict[str, str]
    repository: SQLiteMetadataReportRepository
    access: ReviewerAccessOperations | None

    def call(
        self, method: str, path: str, body: object = None, **headers: str
    ) -> tuple[str, str, int, object]:
        response = self.client.request(
            method,
            path,
            headers={**self.headers, **headers},
            json=body,
        )
        assert 200 <= response.status_code < 300, response.text
        return method, path, response.status_code, response.json()

    def placeholders(self) -> dict[str, str]:
        """Each random identity, oldest first, mapped from its placeholder."""
        rows = (
            (
                "description_attempt_",
                32,
                "SELECT attempt_id FROM description_advice_attempts ORDER BY rowid",
            ),
            (
                "description_candidate_",
                64,
                "SELECT candidate_id FROM description_advice_candidates ORDER BY rowid",
            ),
        )
        found: dict[str, str] = {}
        for prefix, width, statement in rows:
            for index, (value,) in enumerate(
                self.repository._connection.execute(statement).fetchall(), start=1
            ):
                found[prefix + format(index, f"0{width}x")] = value
        # An authored decision's identity binds the random candidate it chose.
        for index, (value,) in enumerate(
            self.repository._connection.execute(
                "SELECT decision_id FROM description_decisions ORDER BY rowid"
            ).fetchall(),
            start=1,
        ):
            origin = value[: value.index("_decision_")]
            found[f"{origin}_decision_{index:064x}"] = value
        return found


@contextmanager
def _signed_in(
    root: Path,
    repository: SQLiteMetadataReportRepository,
    access: ReviewerAccessOperations | None = None,
    **composition: Any,
) -> Iterator[_Live]:
    password = root / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin=ORIGIN,
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=lambda: NOW,
        reviewer_access=access,
        **composition,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200, login.text
        yield _Live(
            client,
            {
                "Origin": ORIGIN,
                "X-CSRF-Token": login.json()["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            },
            repository,
            access,
        )


def _access(repository: SQLiteMetadataReportRepository) -> ReviewerAccessOperations:
    """Reviewer access as `_prepared` binds it, with a fake OpenRouter."""
    return ReviewerAccessOperations(
        repository.reviewer_access_store(),
        codex_auth=_Auth("usable"),
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
            True, None, model=IDENTITY[0]
        ),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_identity=lambda: IDENTITY,
        codex_binding=lambda: (*IDENTITY, _runner),
        codex_description_binding=lambda: (*IDENTITY, _runner),
        openrouter_factory=lambda key, model: _OpenRouter(key, model, []),
    )


@contextmanager
def _description_case(
    root: Path, **prepared: Any
) -> Iterator[tuple[_Live, DescriptionAdviceBinding]]:
    """A case with succeeded description advice, served by the real app.

    `_prepared` owns a single-thread connection, so the same database is
    reopened for the threaded transport with the store bindings it made.
    """
    first, _store, binding, _candidate, _ = _prepared(root, **prepared)
    first.close()
    repository = open_repository(root / "reports.sqlite", cross_thread=True)
    store = repository.description_advice_store()
    store.bind_settings(
        DescriptionAdviceSettings(automatic_application=prepared.get("automatic", True))
    )
    profile = hashlib.sha256(IDENTITY[1]).hexdigest()
    store.bind_identity(
        lambda engine, _model: (IDENTITY[0], profile) if engine == "codex" else None
    )
    activity = ContinuousGuardedApplication(
        repository,
        {},
        {},
        PersistedFieldEffectAuthority(repository),
        (("primary", "ebooks"),),
        lambda: NOW,
        lambda *_: None,
        activity_only=True,
    )
    try:
        with _signed_in(
            root, repository, _access(repository), activity_projection=activity
        ) as live:
            yield live, binding
    finally:
        repository.close()


def _advice(live: _Live, case_id: str) -> object:
    _method, _path, _status, detail = live.call(
        "GET", f"/api/v1/review-cases/{case_id}"
    )
    assert isinstance(detail, dict)
    return next(
        field["description_advice"]
        for field in detail["fields"]
        if field["destination"] == "description"
    )


def _attempt(
    live: _Live, binding: DescriptionAdviceBinding, now: int
) -> tuple[str, Callable[[], bool]]:
    """Begin one retried attempt, exactly as preparation does, and stop."""
    store = live.repository.description_advice_store()
    request_identity, engine, model = live.repository._connection.execute(
        "SELECT request_identity,engine,model FROM description_advice_attempts "
        "ORDER BY rowid LIMIT 1"
    ).fetchone()
    current = lambda: store.binding_is_current(binding)  # noqa: E731
    began = store.begin_attempt(
        request_identity, binding, engine, model, now, current, retry=True
    )
    assert began is not None and began[1] is None
    return began[0], current


def _report_of(live: _Live, case_id: str) -> MetadataReport:
    return MetadataReport.from_json(
        live.repository._connection.execute(
            "SELECT m.document FROM review_cases c JOIN metadata_reports m "
            "ON m.report_id=c.report_id WHERE c.review_case_id=?",
            (case_id,),
        ).fetchone()[0]
    )


def _expect(name: Path, actual: object, placeholders: dict[str, str]) -> None:
    text = name.read_text(encoding="utf-8")
    for placeholder, value in placeholders.items():
        text = text.replace(placeholder, value)
    # Round-trip the response so both sides are plain JSON values.
    assert json.loads(text) == json.loads(json.dumps(actual)), name


def _expect_wire(
    slug: str, capture: tuple[str, str, int, object], placeholders: dict[str, str]
) -> None:
    method, path, status, body = capture
    _expect(
        WIRE / f"{slug}.{status}.json",
        {"method": method, "path": path, "status": status, "body": body},
        placeholders,
    )


def test_description_advice_detail_retry_and_reviewer_access(tmp_path) -> None:
    """The case detail, each retry state, and the reviewer-access writes."""
    with _description_case(tmp_path, automatic=False) as (live, binding):
        case_id = binding.review_case_id
        detail = live.call("GET", f"/api/v1/review-cases/{case_id}")[3]
        advice = _advice(live, case_id)
        assert isinstance(advice, dict) and advice["state"] == "succeeded"
        captures = {
            "post-review-cases-description-advice-selection": live.call(
                "POST",
                f"/api/v1/review-cases/{case_id}/description-advice/selection",
                {
                    "candidate_id": advice["candidate"]["candidate_id"],
                    "binding": advice["binding"],
                },
                **{"Idempotency-Key": "synthetic-selection-key"},
            )
        }
        retries = {"selected": _advice(live, case_id)}
        captures["post-review-cases-description-advice-retry"] = live.call(
            "POST", f"/api/v1/review-cases/{case_id}/description-advice/retry"
        )
        attempt, current = _attempt(live, binding, 1_001)
        retries["requested"] = _advice(live, case_id)
        store = live.repository.description_advice_store()
        assert store.start(attempt, 1_001, current)
        retries["running"] = _advice(live, case_id)
        store.finish(attempt, None, 1_002, current, failure="unavailable")
        retries["failed"] = _advice(live, case_id)
        live.call("POST", f"/api/v1/review-cases/{case_id}/description-advice/retry")
        _attempt(live, binding, 1_003)
        store.interrupt_recovered(1_004)
        retries["interrupted"] = _advice(live, case_id)
        live.call("POST", f"/api/v1/review-cases/{case_id}/description-advice/retry")
        assert live.access is not None
        DescriptionAdviceService(
            store, live.access, store.current_settings, lambda: 1_005
        ).prepare(_report_of(live, case_id), binding, current, retry=True)
        retries["succeeded"] = _advice(live, case_id)
        revision = lambda: live.client.get("/api/v1/reviewer-access").json()[  # noqa: E731
            "revision"
        ]
        key = {"api_key": "synthetic-key", "model": "example/vision"}
        reviewer: tuple[tuple[str, str, str, dict[str, str] | None], ...] = (
            ("put-reviewer-access-openrouter", "PUT", "openrouter", key),
            (
                "post-reviewer-access-openrouter-refresh",
                "POST",
                "openrouter/refresh",
                None,
            ),
            (
                "post-reviewer-access-description-model-test",
                "POST",
                "description-model/test",
                {**key, "task": "description_rewrite"},
            ),
            ("delete-reviewer-access-openrouter", "DELETE", "openrouter", {}),
            ("put-reviewer-access-selection", "PUT", "selection", {"engine": "codex"}),
            ("post-reviewer-access-codex-device", "POST", "codex/device", {}),
            ("delete-reviewer-access-codex", "DELETE", "codex", {}),
        )
        for slug, method, suffix, body in reviewer:
            payload = (
                None if body is None else {**body, "expected_revision": revision()}
            )
            captures[slug] = live.call(
                method, f"/api/v1/reviewer-access/{suffix}", payload
            )
        placeholders = live.placeholders()
    _expect(FIXTURES / "descriptionAdviceFixture.json", detail, placeholders)
    _expect(FIXTURES / "descriptionAdviceRetryFixture.json", retries, placeholders)
    for slug, capture in captures.items():
        _expect_wire(slug, capture, placeholders)


def test_description_advice_activity(tmp_path) -> None:
    """Activity after one succeeded and one failed description attempt."""
    with _description_case(tmp_path, automatic=False) as (live, binding):
        live.call(
            "POST",
            f"/api/v1/review-cases/{binding.review_case_id}/description-advice/retry",
        )
        attempt, current = _attempt(live, binding, 1_001)
        store = live.repository.description_advice_store()
        assert store.start(attempt, 1_001, current)
        store.finish(attempt, None, 1_002, current, failure="unavailable")
        activity = live.call("GET", "/api/v1/activity")[3]
        attention = live.call("GET", "/api/v1/attention")
        placeholders = live.placeholders()
    assert isinstance(attention[3], dict) and attention[3]["items"]
    _expect(FIXTURES / "descriptionAdviceActivityFixture.json", activity, placeholders)
    _expect_wire("get-attention", attention, placeholders)


def test_description_advice_preservation(tmp_path) -> None:
    """Retained automatic advice on a preservation-only field during a retry."""
    source, *_ = _automatic_no_change_inputs("description_preserved")
    preserved = _report(
        fields=tuple(
            replace(field, value="A qualified description.")
            if field.destination.value == "description"
            else field
            for field in source.target.fields
        ),
        local_claims=source.local_claims,
        providers=source.providers,
    )
    with _description_case(tmp_path, report_override=preserved) as (live, binding):
        store = live.repository.description_advice_store()
        advice = _advice(live, binding.review_case_id)
        assert isinstance(advice, dict) and advice["candidate"] is not None
        store.admit(
            advice["candidate"]["candidate_id"],
            binding,
            "automatic_policy",
            "c" * 64,
            20,
        )
        live.call(
            "POST",
            f"/api/v1/review-cases/{binding.review_case_id}/description-advice/retry",
        )
        _attempt(live, binding, 1_001)
        detail = live.call("GET", f"/api/v1/review-cases/{binding.review_case_id}")[3]
        placeholders = live.placeholders()
    _expect(
        FIXTURES / "descriptionAdvicePreservationFixture.json", detail, placeholders
    )


def test_policy_documents_and_automatic_confirmation(tmp_path) -> None:
    """One document per policy response the browser reads, then Automatic."""
    repository = open_repository(tmp_path / "policy.sqlite", cross_thread=True)
    try:
        with _signed_in(
            tmp_path, repository, policy_libraries=(("ebooks", None),)
        ) as live:
            state = live.call("GET", "/api/v1/metadata-policy/state")
            profile = dict(state[3]["profile"])
            del profile["schema_version"]
            profile["name"] = "Synthetic automatic covers"
            profile["destinations"] = {
                **profile["destinations"],
                "cover_order": "automatic",
            }
            live.call("POST", "/api/v1/metadata-policy/shadow/enter")
            draft = live.call("POST", "/api/v1/metadata-policy/drafts", profile)
            preview = live.call("POST", "/api/v1/metadata-policy/preview", profile)
            captures = {
                "get-metadata-policy-state": state,
                "post-metadata-policy-preview": preview,
                "post-metadata-policy-drafts": draft,
                "post-metadata-policy-activate": live.call(
                    "POST",
                    "/api/v1/metadata-policy/activate",
                    {
                        "policy_id": draft[3]["policy_id"],
                        "expected_active_policy_id": state[3]["policy_id"],
                        "expected_active_generation": preview[3]["active_generation"],
                    },
                ),
                "get-metadata-policy-transition": live.call(
                    "GET", "/api/v1/metadata-policy/transition"
                ),
                "get-metadata-policy-shadow": live.call(
                    "GET", "/api/v1/metadata-policy/shadow"
                ),
                "get-metadata-policy-shadow-outcomes": live.call(
                    "GET", "/api/v1/metadata-policy/shadow/outcomes"
                ),
                "get-metadata-policy-automatic-safety": live.call(
                    "GET", "/api/v1/metadata-policy/automatic-safety"
                ),
                "post-metadata-policy-shadow-confirm-automatic": live.call(
                    "POST", "/api/v1/metadata-policy/shadow/confirm-automatic"
                ),
                "post-metadata-policy-automatic-safety-suspend": live.call(
                    "POST",
                    "/api/v1/metadata-policy/automatic-safety/suspend",
                    {"reason": "synthetic"},
                ),
            }
    finally:
        repository.close()
    assert captures["get-metadata-policy-transition"][3] is not None
    for slug, capture in captures.items():
        _expect_wire(slug, capture, {})


def test_field_effect_reconciliation(effects: _EffectEnvironment, tmp_path) -> None:
    """Reconciling a verified effect answers with its recorded state."""
    operation = _plan(effects, suffix="b")
    with _signed_in(
        tmp_path, effects.repository, field_application=effects.service
    ) as live:
        path = f"/api/v1/field-effects/{operation.plan.plan_id}"
        live.call("POST", f"{path}/apply")
        capture = live.call("POST", f"{path}/reconcile")
    assert isinstance(capture[3], dict) and capture[3]["state"] == "verified"
    _expect_wire("post-field-effects-reconcile", capture, {})
