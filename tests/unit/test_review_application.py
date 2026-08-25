from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast, get_protocol_members, get_type_hints

import pytest

from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.metadata_policy import (
    AutomaticSafetyControls,
    AutomaticSafetyRepository,
    PolicyImpactRepository,
)
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.application.review_application import (
    ReviewApplication,
    ReviewApplicationError,
    compose_review_application,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPreparationSupervisor,
)
from mechanical_librarian.application.review_web import ReviewWebError
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web import create_review_app
from tests.support.review_cases import _report
from tests.support.review_web_doubles import FakeReviewRepository

_NOW = 1_000


@pytest.fixture
def repository() -> FakeReviewRepository:
    """One fake store holding a single actionable review case."""

    report = _report()
    return FakeReviewRepository(
        ReviewCase.create(evaluate_report(report), report), report
    )


@pytest.fixture
def application(repository: FakeReviewRepository) -> ReviewApplication:
    return compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda value: value == "correct horse",
        clock=lambda: _NOW,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
    )


# The two tests below prove the composed policy store declares both seams it is
# bound to.
#
# `compose_review_application` builds a policy notebook and the owner-only
# automatic-safety controls from one `policy_store`. `AutomaticSafetyControls`
# accepted `object` and cast to the protocol it names, so a store without the
# safety members composed cleanly and turned every safety read, pause,
# suspend, and recover on the served path into "automatic safety is
# unavailable" -- a capability outage wearing a storage-failure message
# (#658).


def test_composed_policy_store_declares_the_safety_seam() -> None:
    safety = get_protocol_members(AutomaticSafetyRepository)
    declared = get_type_hints(compose_review_application)["policy_store"]
    assert safety <= get_protocol_members(declared)
    # The notebook's own contract does not carry these members, so the
    # assertion above is a live check of what the composition declares
    # rather than a restatement of the policy contract.
    assert frozenset() == safety & get_protocol_members(PolicyImpactRepository)


def test_automatic_safety_controls_declare_the_store_they_require() -> None:
    assert (
        AutomaticSafetyRepository
        is get_type_hints(AutomaticSafetyControls.__init__)["repository"]
    )


def test_named_case_operation_returns_bounded_view(
    application: ReviewApplication, repository: FakeReviewRepository
) -> None:
    view = application.case_detail(repository.case.review_case_id)

    assert view.review_case_id == repository.case.review_case_id
    assert view.current
    assert view.to_data()["schema_version"] == "review-case-detail-v2"


def test_named_browse_operations_return_bounded_views(
    application: ReviewApplication,
) -> None:
    library = application.library(lens="all", search=None, limit=20, cursor=None)
    attention = application.attention(search=None, limit=20, cursor=None)

    assert library.schema_version == "review-library-v1"
    assert library.counts["all"] == 1
    assert attention.schema_version == "review-attention-v1"
    assert attention.total == 1


def test_settings_commit_updates_live_cover_policy_inside_the_application_gate(
    repository: FakeReviewRepository,
) -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v1",
            "revision": 2,
            "connections": [{"name": "primary", "endpoint": "https://example.invalid"}],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "library",
                    "media_kind": "ebook",
                }
            ],
            "cover_preferences": {
                "avoid_promotional_text": True,
                "avoid_tie_in_branding": True,
                "prefer_minimal_composition": True,
            },
        }
    )

    class Settings:
        def read(self):
            return SimpleNamespace(document=document)

        def write(self, _document, _revision, _secrets):
            return SimpleNamespace(document=document)

    class Covers:
        def __init__(self) -> None:
            self.policy: CoverPolicy | None = None

        def update_policy(self, policy: CoverPolicy) -> None:
            assert application._gate._is_owned()
            self.policy = policy

    covers = Covers()
    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda _: True,
        clock=lambda: _NOW,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        settings=OwnerSettingsOperations(cast("object", Settings())),
        cover_considerations=cast("CoverConsiderationService", covers),
    )

    result = application.write_owner_settings(document, 1, ())

    assert result.document == document
    assert covers.policy == CoverPolicy(True, True, True)


def test_live_cover_binding_fault_is_a_fixed_settings_failure(
    repository: FakeReviewRepository,
) -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v1",
            "revision": 2,
            "connections": [{"name": "primary", "endpoint": "https://example.invalid"}],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "library",
                    "media_kind": "ebook",
                }
            ],
        }
    )

    class Settings:
        def read(self):
            return SimpleNamespace(document=document)

        def write(self, _document, _revision, _secrets):
            return SimpleNamespace(document=document)

    class Covers:
        def update_policy(self, _policy: CoverPolicy) -> None:
            raise RuntimeError("synthetic binding fault")

    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda _: True,
        clock=lambda: _NOW,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        settings=OwnerSettingsOperations(cast("object", Settings())),
        cover_considerations=cast("CoverConsiderationService", Covers()),
    )

    with pytest.raises(ReviewApplicationError) as raised:
        application.write_owner_settings(document, 1, ())

    assert raised.value.code == "settings_unavailable"
    assert isinstance(raised.value.__cause__, RuntimeError)


def test_session_operations_are_composed_before_transport(
    application: ReviewApplication,
) -> None:
    tokens = application.login("correct horse")

    assert application.authenticate(tokens.session_token) == "owner"
    browser_csrf = application.issue_browser_csrf(tokens.session_token)
    assert application.authenticate(tokens.session_token, browser_csrf) == "owner"


def test_password_verifier_fault_is_not_reported_as_a_wrong_password(
    repository: FakeReviewRepository,
) -> None:
    """A verifier that raises has not decided the password is wrong.

    The verifier's contract is a bool, so `False` is the wrong-password
    answer and a raise is a fault in the hasher or its stored digest. That
    raise was absorbed into `verified = False`, so a broken Argon2 hash, a
    signature change, or an exhausted memory cost all reached the owner as
    `invalid_credentials` and a 401 -- a fault presented as a rejection no
    correct password could clear (#698).
    """
    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda _: (_ for _ in ()).throw(
            MemoryError("argon2 memory cost unavailable")
        ),
        clock=lambda: _NOW,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
    )

    with pytest.raises(ReviewWebError) as raised:
        application.login("correct horse")

    assert raised.value.code == "runtime"
    assert isinstance(raised.value.__cause__, MemoryError)


def test_a_later_shutdown_failure_is_kept_on_the_one_that_propagates(
    repository: FakeReviewRepository,
) -> None:
    """A second lane that also failed to stop must stay visible.

    `stop` attempts every step and then raises the first failure. Raising
    it alone discarded every later one with its traceback, so an owner
    shutting the server down saw one lane's failure and no sign that a
    second background lane was also still running (#698).
    """

    class _FailingPreparation:
        def attach_updates(self, announce: object) -> None:
            return None

        def begin_drain(self) -> None:
            raise RuntimeError("drain lane did not stop")

        def stop(self, deadline: float | None = None) -> None:
            raise RuntimeError("preparation lane did not stop")

    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda value: value == "correct horse",
        clock=lambda: _NOW,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        preparation=cast("ReviewPreparationSupervisor", _FailingPreparation()),
    )

    with pytest.raises(RuntimeError) as raised:
        application.stop()

    assert str(raised.value) == "drain lane did not stop"
    assert getattr(raised.value, "__notes__", []) == [
        "further shutdown failure: RuntimeError('preparation lane did not stop')"
    ]


def test_published_preparation_work_wakes_the_guarded_scheduler(
    repository: FakeReviewRepository,
) -> None:
    class Preparation:
        def attach_updates(self, announce) -> None:
            self.announce = announce

    class FieldApplication(ContinuousGuardedApplication):
        def __init__(self) -> None:
            self.wakes = 0

        def attach_timing(self, _timing) -> None:
            return None

        def attach_serialization(self, _serialize) -> None:
            return None

        def attach_updates(self, _announce) -> None:
            return None

        def wake(self) -> None:
            self.wakes += 1

    preparation = Preparation()
    field_application = FieldApplication()
    compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda value: value == "correct horse",
        clock=lambda: _NOW,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        preparation=cast("ReviewPreparationSupervisor", preparation),
        field_application=field_application,
    )

    preparation.announce("preparation", None)
    assert field_application.wakes == 0
    preparation.announce("library", "review_case")
    assert field_application.wakes == 1


def test_policy_candidate_workflows_are_application_operations(
    application: ReviewApplication,
) -> None:
    candidate = MetadataPolicyProfile.builtin()
    values = (
        "Candidate",
        dict(candidate.options),
        dict(candidate.destinations),
    )
    validated = application.validate_policy(*values)
    created = application.create_policy_draft(*values)
    previewed, impact = application.preview_policy(*values)

    assert validated.policy_id == created.policy_id
    assert previewed.policy_id == created.policy_id
    assert impact.profile_id == created.policy_id


@pytest.mark.parametrize(
    "call",
    (
        lambda application: application.apply_field_effect("effect_plan_" + "a" * 64),
        lambda application: application.reconcile_field_effect(
            "effect_plan_" + "a" * 64
        ),
        lambda application: application.pause_field_effects(),
        lambda application: application.resume_field_effects(),
        lambda application: application.request_review_scan("primary", "books"),
        lambda application: application.retry_review_scan("primary", "books"),
        lambda application: application.pause_review_preparation(),
        lambda application: application.resume_review_preparation(),
        lambda application: application.activity(),
    ),
    ids=(
        "apply_field_effect",
        "reconcile_field_effect",
        "pause_field_effects",
        "resume_field_effects",
        "request_review_scan",
        "retry_review_scan",
        "pause_review_preparation",
        "resume_review_preparation",
        "activity",
    ),
)
def test_unavailable_mutations_have_named_typed_application_failures(
    application: ReviewApplication, call: Callable[[ReviewApplication], object]
) -> None:
    with pytest.raises(ReviewApplicationError) as captured:
        call(application)
    assert captured.value.code == "unavailable"


def test_http_factory_accepts_only_composed_application_and_configuration() -> None:
    assert tuple(inspect.signature(create_review_app).parameters) == (
        "application",
        "configuration",
    )
    source = inspect.getsource(create_review_app)
    assert "ReviewWebRepository" not in source
    assert "_SerializedReviewRepository" not in source
    assert "repository." not in source


def test_connection_probe_does_not_hold_the_global_application_gate(
    repository: FakeReviewRepository,
) -> None:
    started = threading.Event()
    release = threading.Event()
    browse_finished = threading.Event()

    class BlockingPreparation:
        libraries: tuple[tuple[str, str, str], ...] = ()

        def attach_updates(self, announce: object) -> None:
            return None

        def check_connection(self, _connection: str) -> bool:
            started.set()
            assert release.wait(2)
            return True

        def status(self):
            return (), False

        def connection_conditions(self):
            return ()

    preparation = BlockingPreparation()
    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda _: True,
        clock=lambda: _NOW,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        preparation=preparation,
    )
    probe = threading.Thread(
        target=lambda: application.check_review_preparation_connection("primary")
    )
    probe.start()
    assert started.wait(1)
    browse = threading.Thread(
        # The tuple sequences the browse call and the `set()` signal inside one
        # lambda, so the discarded `None` element is the point, not a mistake.
        target=lambda: (
            application.library(lens="all", search=None, limit=20, cursor=None),
            browse_finished.set(),  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        )
    )
    browse.start()
    assert browse_finished.wait(1)
    release.set()
    probe.join(1)
    browse.join(1)
    assert not probe.is_alive()
    assert not browse.is_alive()
