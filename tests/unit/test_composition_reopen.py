"""The composition seams the source-only workbench binds, and nothing else.

`reopen_*`, `create_proposal_set` and `run_metadata_report` are not CLI
subcommands; `bootstrap.composition.main` injects five runners and none of them
is here. Their only caller is `WorkbenchOperations.production_operations`, and
`tests/unit/test_runtime_workbench.py` proves the binding by identity while
substituting a fake for every operation, so no test in this suite executed a
line of them (#773). These are the two properties worth proving at that
altitude: a read never creates the database it was pointed at, and a seam whose
whole body is `load_configuration_file` plus a delegation delegates what it
loaded.
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthManager,
    CodexDeviceAuthState,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ReviewWebConfiguration,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.operation_admission import OperationAdmission
from mechanical_librarian.application.review_pipeline import pipeline_scan_id
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    SettingsView,
)
from mechanical_librarian.infrastructure.first_boot import (
    FirstBootMode,
    select_first_boot_mode,
)
from mechanical_librarian.infrastructure.settings_authority import ReviewBootstrap
from mechanical_librarian.interfaces.web.runtime_dispatch import (
    MutableRuntimeDispatcher,
    RuntimeGeneration,
)


async def _runtime_app(scope: object, receive: object, send: object) -> None:
    return None


@pytest.mark.parametrize("activated", (False, True))
@pytest.mark.parametrize("failure", (None, "composition", "http"))
def test_store_runtime_wires_one_owner_reviewer_authority_to_http_and_cover(
    failure: str | None, activated: bool
) -> None:
    repository = Mock()
    settings = repository.owner_settings_store.return_value
    settings.activate_runtime.return_value = (
        SimpleNamespace(document=SimpleNamespace(pending_restart=())),
        {},
        "verifier",
    )
    repository.has_first_boot_preparation.return_value = False
    session_store = Mock()
    reviewer_access = Mock()
    cover_considerations = Mock()
    cover_considerations.current_authority.return_value = ("policy", "replay")
    review_application = Mock()
    close_order: list[str] = []
    repository.close.side_effect = lambda: close_order.append("root")
    preparation = Mock()
    preparation.libraries = (
        ("primary", "fiction", "library-1"),
        ("primary", "history", "library-2"),
        ("primary", "existing", "library-3"),
    )
    preparation.progress.return_value = SimpleNamespace(state="completed")
    admission_events: list[str] = []
    preparation.request_library_addition.side_effect = lambda _connection, library: (
        admission_events.append(f"request:{library}")
    )
    review_application.admit.side_effect = lambda: admission_events.append("admit")
    review = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
        canonical_origin="https://library.invalid",
        owner_password_hash_file="/data/owner.hash",
    )
    configuration = ApplicationConfiguration((), (), (), (), review=review)

    with (
        patch.object(composition, "open_repository", return_value=repository),
        patch.object(
            composition, "_open_review_session_store", return_value=session_store
        ),
        patch.object(
            composition, "store_runtime_configuration", return_value=configuration
        ),
        patch.object(composition, "_store_first_preparation", return_value=False),
        patch.object(
            composition, "create_review_pipeline", return_value=preparation
        ) as create_pipeline,
        patch.object(
            composition.SQLiteProviderRecoveryStore, "open", return_value=Mock()
        ),
        patch.object(
            composition, "owner_password_binding", return_value=(Mock(), Mock())
        ),
        patch.object(
            composition, "_compose_reviewer_access", return_value=reviewer_access
        ),
        patch.object(
            composition,
            "_compose_cover_considerations",
            return_value=cover_considerations,
        ) as compose_cover,
        patch.object(
            composition,
            "compose_review_application",
            return_value=review_application,
            side_effect=RuntimeError("activation failed")
            if failure == "composition"
            else None,
        ) as compose_application,
        patch.object(
            composition,
            "create_review_app",
            return_value=_runtime_app,
            side_effect=RuntimeError("activation failed")
            if failure == "http"
            else None,
        ),
    ):
        if failure is not None:
            with pytest.raises(RuntimeError, match="activation failed"):
                composition.compose_store_runtime_generation(
                    ReviewBootstrap(
                        schema_version="review-bootstrap-v2",
                        canonical_origin="https://library.invalid",
                        tls_certificate_file="/synthetic/certificate",
                        tls_private_key_file="/synthetic/private-key",
                    ),
                    Path("application.sqlite3"),
                    Path("catalogue.sqlite3"),
                    Path("artifacts"),
                    repository_authority=object(),
                    operation_admission=OperationAdmission(),
                )
            assert close_order == ["root"]
            return
        bootstrap = ReviewBootstrap(
            schema_version="review-bootstrap-v2",
            canonical_origin="https://library.invalid",
            tls_certificate_file="/synthetic/certificate",
            tls_private_key_file="/synthetic/private-key",
        )
        # A Settings cutover's generation reuses the process's validated
        # browse documents; a standalone one composes its own (#1226).
        activation = (
            composition._StoreRuntimeActivation(
                bootstrap,
                Path("application.sqlite3"),
                Path("catalogue.sqlite3"),
                Path("artifacts"),
                Mock(),
                object(),
                operation_admission=OperationAdmission(),
            )
            if activated
            else None
        )
        runtime, _configuration = composition.compose_store_runtime_generation(
            bootstrap,
            Path("application.sqlite3"),
            Path("catalogue.sqlite3"),
            Path("artifacts"),
            repository_authority=object(),
            operation_admission=OperationAdmission(),
            activation=activation,
        )

    repository.bind_library_catalogue.assert_called_once_with(
        Path("catalogue.sqlite3"),
        composition.batch_catalogue_fingerprint(configuration),
    )
    assert create_pipeline.call_args.args[4] is reviewer_access
    assert compose_application.call_args.kwargs["library_store"] is repository
    assert compose_application.call_args.kwargs["browse_observation"] is repository
    assert compose_application.call_args.kwargs["validated_documents"] is (
        None if activation is None else activation.validated_documents
    )
    assert compose_cover.call_args.args[3] is reviewer_access
    assert compose_application.call_args.kwargs["reviewer_access"] is reviewer_access
    assert (
        compose_application.call_args.kwargs["cover_considerations"]
        is cover_considerations
    )
    runtime.start_dormant()
    reviewer_access.activate_codex_lease.assert_not_called()
    runtime.admit()
    reviewer_access.activate_codex_lease.assert_called_once_with()
    review_application.admit.assert_called_once_with()
    preparation.request_library_addition.assert_not_called()
    preparation.request.assert_not_called()
    preparation.progress.assert_not_called()
    assert admission_events == ["admit"]
    runtime.admit()
    preparation.request_library_addition.assert_not_called()
    runtime.close()
    review_application.stop.assert_called_once_with()


@pytest.mark.parametrize("failure", ("settings_dormant", "setup_commit"))
def test_candidate_failure_preserves_active_codex_lease_owner(failure: str) -> None:
    coordinator = composition._CodexAuthLeaseCoordinator()
    predecessor_manager = Mock(spec=CodexDeviceAuthManager)
    predecessor_manager.status.return_value = CodexDeviceAuthState("usable")
    predecessor = composition._LazyCodexAuth(
        lambda _current, _guard: predecessor_manager, coordinator
    )
    successor = composition._LazyCodexAuth(
        lambda _current, _guard: Mock(spec=CodexDeviceAuthManager), coordinator
    )
    predecessor.activate_lease()
    predecessor._manager = predecessor_manager
    current = RuntimeGeneration(_runtime_app)
    dispatcher = MutableRuntimeDispatcher(current)
    dispatcher.start()

    def start_dormant() -> None:
        if failure == "settings_dormant":
            raise RuntimeError("candidate start failed")

    candidate = RuntimeGeneration(
        _runtime_app,
        start_dormant=start_dormant,
        admit=successor.activate_lease,
        close=successor.close,
    )
    if failure == "settings_dormant":
        with pytest.raises(RuntimeError, match="candidate start failed"):
            dispatcher.prepare(candidate)
    else:
        dispatcher.prepare(candidate)

        def fail_commit() -> None:
            raise RuntimeError("authority commit failed")

        with pytest.raises(RuntimeError, match="authority commit failed"):
            dispatcher.transition(current, candidate, fail_commit)

    assert predecessor.status().state == "usable"
    predecessor_manager.close.assert_not_called()
    assert predecessor._lease is not None
    assert coordinator.current(predecessor, predecessor._lease)


def test_setup_runtime_reserves_codex_lease_until_admission() -> None:
    repository = Mock()
    reviewer_access = Mock()
    setup_app = Mock()
    bootstrap = ReviewBootstrap(
        schema_version="review-bootstrap-v2",
        canonical_origin="https://library.invalid",
        tls_certificate_file="/synthetic/certificate",
        tls_private_key_file="/synthetic/private-key",
    )
    with (
        patch.object(
            composition, "_compose_reviewer_access", return_value=reviewer_access
        ),
        patch.object(composition, "create_setup_app", return_value=setup_app),
    ):
        runtime, _activation = composition._compose_setup_runtime_generation(
            bootstrap,
            Path("application.sqlite3"),
            Path("catalogue.sqlite3"),
            Path("artifacts"),
            repository,
            Mock(),
            initialize=False,
        )

    runtime.start_dormant()
    reviewer_access.activate_codex_lease.assert_not_called()
    runtime.admit()
    reviewer_access.activate_codex_lease.assert_called_once_with()
    runtime.close()
    reviewer_access.close.assert_called_once_with()


def test_store_runtime_activation_reuses_outer_control_authority_for_successors() -> (
    None
):
    retained = SimpleNamespace(connection="primary", name="retained")
    added = SimpleNamespace(connection="primary", name="added")
    snapshot: tuple[SettingsView, dict[str, str], str] = (
        cast(
            SettingsView,
            SimpleNamespace(document=SimpleNamespace(libraries=(retained, added))),
        ),
        {},
        "verifier",
    )
    authority = object()
    predecessor = cast(
        SettingsView,
        SimpleNamespace(document=SimpleNamespace(libraries=(retained,))),
    )
    settings = SimpleNamespace(
        read=lambda: predecessor, activation_snapshot=lambda *_values: snapshot
    )
    activation = composition._StoreRuntimeActivation(
        ReviewBootstrap(
            schema_version="review-bootstrap-v2",
            canonical_origin="https://library.invalid",
            tls_certificate_file="/synthetic/certificate",
            tls_private_key_file="/synthetic/private-key",
        ),
        Path("application.sqlite3"),
        Path("catalogue.sqlite3"),
        Path("artifacts"),
        settings,
        authority,
        operation_admission=OperationAdmission(),
    )
    candidate = RuntimeGeneration(_runtime_app)
    with patch.object(
        composition,
        "compose_store_runtime_generation",
        return_value=(candidate, object()),
    ) as compose:
        assert activation.prepare(object(), 2, ()) is candidate
        with patch.object(activation, "_activate") as apply:
            activation.activate_setup(snapshot, lambda: None)
    assert compose.call_args_list[0].kwargs["repository_authority"] is authority
    assert "library_additions" not in compose.call_args_list[0].kwargs
    assert compose.call_args_list[1].kwargs["repository_authority"] is authority
    assert compose.call_args_list[1].kwargs["first_boot"] is True
    apply.assert_called_once()


def test_settings_activation_keeps_addition_intent_out_of_candidate_closure() -> None:
    readded = SimpleNamespace(connection="primary", name="fiction")
    predecessor = cast(
        SettingsView, SimpleNamespace(document=SimpleNamespace(libraries=()))
    )
    successor = cast(
        SettingsView,
        SimpleNamespace(document=SimpleNamespace(libraries=(readded,))),
    )
    current = [predecessor]
    settings = SimpleNamespace(
        read=lambda: current[0],
        activation_snapshot=lambda *_values: (successor, {}, "verifier"),
    )
    activation = composition._StoreRuntimeActivation(
        ReviewBootstrap(
            schema_version="review-bootstrap-v2",
            canonical_origin="https://library.invalid",
            tls_certificate_file="/synthetic/certificate",
            tls_private_key_file="/synthetic/private-key",
        ),
        Path("application.sqlite3"),
        Path("catalogue.sqlite3"),
        Path("artifacts"),
        settings,
        object(),
        operation_admission=OperationAdmission(),
    )
    original = RuntimeGeneration(_runtime_app)
    dispatcher = MutableRuntimeDispatcher(original)
    dispatcher.start()
    activation.bind(dispatcher)
    events: list[object] = []

    def compose(*_arguments: object, **keywords: object) -> tuple[object, object]:
        candidate = RuntimeGeneration(
            _runtime_app,
            admit=lambda: events.append("admit"),
        )
        return candidate, object()

    with patch.object(
        composition, "compose_store_runtime_generation", side_effect=compose
    ):
        with pytest.raises(RuntimeError, match="commit refused"):
            activation.activate(
                object(),
                2,
                (),
                lambda: (_ for _ in ()).throw(RuntimeError("commit refused")),
            )
        assert events == []
        assert current[0] is predecessor

        def commit() -> None:
            events.append("commit")
            current[0] = successor

        activation.activate(object(), 2, (), commit)
        activation.activate(object(), 2, (), lambda: events.append("repeat-commit"))

    assert events == [
        "commit",
        "admit",
        "repeat-commit",
        "admit",
    ]


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_setup_and_settings_successors_open_through_live_outer_control(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    control = composition.open_repository(database)
    predecessor = composition.open_repository(database, authority=control.authority)
    snapshot: tuple[SettingsView, dict[str, str], str] = (
        cast(SettingsView, SimpleNamespace(document=SimpleNamespace(libraries=()))),
        {},
        "verifier",
    )
    predecessor_settings = cast(
        SettingsView, SimpleNamespace(document=SimpleNamespace(libraries=()))
    )
    settings = SimpleNamespace(
        read=lambda: predecessor_settings, activation_snapshot=lambda *_values: snapshot
    )
    activation = composition._StoreRuntimeActivation(
        ReviewBootstrap(
            schema_version="review-bootstrap-v2",
            canonical_origin="https://library.invalid",
            tls_certificate_file="/synthetic/certificate",
            tls_private_key_file="/synthetic/private-key",
        ),
        database,
        tmp_path / "catalogue.sqlite3",
        tmp_path / "artifacts",
        settings,
        control.authority,
        operation_admission=OperationAdmission(),
    )

    class ReachedRuntimeComposition(RuntimeError):
        pass

    try:
        with patch.object(
            composition,
            "store_runtime_configuration",
            side_effect=ReachedRuntimeComposition,
        ):
            with pytest.raises(ReachedRuntimeComposition):
                activation.prepare(object(), 2, ())
            predecessor.close()
            with pytest.raises(ReachedRuntimeComposition):
                activation.activate_setup(snapshot, lambda: None)
    finally:
        control.close()


def test_outer_listener_uses_each_committed_shutdown_budget() -> None:
    initial = RuntimeGeneration(_runtime_app, shutdown_seconds=5)
    successor = RuntimeGeneration(_runtime_app, shutdown_seconds=17)
    dispatcher = MutableRuntimeDispatcher(initial)

    class Config:
        def __init__(self, application: object, **values: object) -> None:
            self.application = application
            for name, value in values.items():
                setattr(self, name, value)

    class Server:
        def __init__(self, config: Config) -> None:
            self.config = config
            self.should_exit = False

        def run(self) -> None:
            assert self.config.timeout_graceful_shutdown == 5
            dispatcher.prepare(successor)
            dispatcher.transition(initial, successor, lambda: None)
            assert self.config.timeout_graceful_shutdown == 17

    with (
        patch.object(composition.uvicorn, "Config", Config),
        patch.object(composition.uvicorn, "Server", Server),
    ):
        composition._serve_runtime_dispatcher(
            dispatcher, "127.0.0.1", 7443, "certificate", "private-key"
        )


@pytest.mark.parametrize(
    ("operation", "arguments"),
    (
        ("reopen_metadata_report", ("report_id",)),
        (
            "reopen_current_metadata_report",
            ("primary", "ebooks", "item-1", "c" * 64),
        ),
        ("reopen_proposal_set", ("proposal_set_id",)),
        ("reopen_review_case", ("review_case_id",)),
    ),
)
def test_a_read_seam_refuses_an_absent_database_rather_than_creating_one(
    tmp_path: Path, operation: str, arguments: tuple[str, ...]
) -> None:
    database = tmp_path / "application.sqlite3"

    with pytest.raises(MetadataReportError):
        getattr(composition, operation)(database, *arguments)

    assert not database.exists()
    assert [] == sorted(tmp_path.iterdir())


def test_reopening_a_preview_refuses_an_absent_database_before_the_store(
    tmp_path: Path,
) -> None:
    """The artifact store is never reached, so it is never created either."""

    database = tmp_path / "application.sqlite3"
    artifacts = tmp_path / "artifacts"

    with (
        patch.object(
            composition,
            "CoverArtifactStore",
            side_effect=AssertionError("artifact store opened"),
        ) as store,
        pytest.raises(MetadataReportError),
    ):
        composition.reopen_cover_preview(
            database, artifacts, "report_id", "artifact_id", "thumbnail"
        )

    store.assert_not_called()
    assert not database.exists()
    assert not artifacts.exists()


@pytest.mark.parametrize(
    ("operation", "delegate", "arguments"),
    (
        (
            "create_proposal_set",
            "create_proposal_set_loaded",
            ("report_id",),
        ),
        (
            "run_metadata_report",
            "run_metadata_report_loaded",
            ("primary", "ebooks", "item-1", 0, "d" * 64),
        ),
    ),
)
def test_a_configuration_loading_seam_delegates_what_it_loaded(
    operation: str, delegate: str, arguments: tuple[object, ...]
) -> None:
    configuration = object()
    paths = (Path("/database"), Path("/artifacts"))

    with (
        patch.object(
            composition, "load_configuration_file", return_value=configuration
        ) as load,
        patch.object(composition, delegate, return_value="delegated") as loaded,
    ):
        result = getattr(composition, operation)(
            Path("/config.json"), *paths, *arguments
        )

    assert "delegated" == result
    load.assert_called_once_with(Path("/config.json"))
    assert loaded.call_args.args[0] is configuration
    assert paths == loaded.call_args.args[1:3]
    assert arguments == loaded.call_args.args[3:]


def test_ordinary_server_releases_first_owner_state_for_interruption_and_resume(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = composition.open_repository(database)
    bootstrap = ReviewBootstrap(
        schema_version="review-bootstrap-v2",
        tls_certificate_file="/synthetic/certificate",
        tls_private_key_file="/synthetic/private-key",
        canonical_origin="https://setup.invalid",
    )

    def claim_before_shutdown(_server: object) -> None:
        _server.started = True
        state = repository.setup_state_store()
        document = state.read()
        state.claim_owner(
            expected_revision=document.revision,
            owner_verifier="$argon2id$v=19$m=65536,t=3,p=4$b3duZXI$hash",
        )

    with (
        patch.object(
            composition,
            "open_review_tls_identity",
            return_value=nullcontext(
                SimpleNamespace(
                    certificate_file="certificate", private_key_file="private-key"
                )
            ),
        ),
        patch.object(composition, "open_repository", return_value=repository),
        patch.object(composition, "load_review_bootstrap", return_value=bootstrap),
        patch.object(repository, "close", wraps=repository.close) as close,
        patch.object(
            composition.uvicorn.Server,
            "run",
            autospec=True,
            side_effect=claim_before_shutdown,
        ) as run,
    ):
        try:
            composition.run_review_server(
                tmp_path / "config.json",
                database,
                tmp_path / "catalogue.sqlite3",
                tmp_path / "artifacts",
                "127.0.0.1",
                18444,
                first_boot_mode=FirstBootMode.INITIALIZE_SETUP,
            )
            close.assert_called_once_with()
        finally:
            if not close.called:
                repository.close()

    run.assert_called_once()
    assert not tuple(tmp_path.glob(".metadata-report-binding-*.sqlite"))

    def resumable_setup() -> bool:
        reopened = composition.open_repository(database, create=False)
        try:
            reopened.setup_state_store().read()
            return True
        finally:
            reopened.close()

    assert (
        select_first_boot_mode(
            tmp_path,
            complete_store=lambda: False,
            resumable_setup=resumable_setup,
        )
        is FirstBootMode.RESUME_SETUP
    )


def test_exact_schema_only_kill_boundary_restarts_setup_without_generic_recovery(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = composition.open_repository(database)
    repository.close()

    def uninitialized_setup() -> bool:
        reopened = composition.open_repository(database, create=False)
        try:
            return reopened.setup_state_store().is_exact_schema_only()
        finally:
            reopened.close()

    assert (
        select_first_boot_mode(
            tmp_path,
            complete_store=lambda: False,
            resumable_setup=lambda: False,
            uninitialized_setup=uninitialized_setup,
        )
        is FirstBootMode.INITIALIZE_SETUP
    )

    repository = composition.open_repository(database)
    try:
        repository.setup_state_store().initialize()
    finally:
        repository.close()

    assert (
        select_first_boot_mode(
            tmp_path,
            complete_store=lambda: False,
            resumable_setup=lambda: True,
            uninitialized_setup=uninitialized_setup,
        )
        is FirstBootMode.RESUME_SETUP
    )


def test_first_preparation_presentation_survives_restart_debt_acknowledgement() -> None:
    assert composition._store_first_preparation("store", ("setup_complete",), False)
    assert composition._store_first_preparation("store", (), True)
    assert not composition._store_first_preparation("store", (), False)
    assert not composition._store_first_preparation("file", ("setup_complete",), True)


def test_setup_mount_reachability_is_exact_bounded_and_directory_only(
    tmp_path: Path,
) -> None:
    readable = tmp_path / "books"
    readable.mkdir()
    regular = tmp_path / "not-a-directory"
    regular.write_text("synthetic", encoding="utf-8")
    bootstrap = ReviewBootstrap.model_validate(
        {
            "schema_version": "review-bootstrap-v2",
            "tls_certificate_file": "/synthetic/certificate",
            "tls_private_key_file": "/synthetic/key",
            "canonical_origin": "https://review.invalid",
            "mounts": [
                {
                    "name": "books",
                    "container_path": str(readable),
                    "service_path": "/service/books",
                    "read_only": True,
                },
                {
                    "name": "file",
                    "container_path": str(regular),
                    "service_path": "/service/file",
                    "read_only": True,
                },
                {
                    "name": "missing",
                    "container_path": str(tmp_path / "missing"),
                    "service_path": "/service/missing",
                    "read_only": True,
                },
            ],
        }
    )
    assert composition._reachable_setup_mount_names(bootstrap, "/service/books") == (
        "books",
    )
    assert composition._reachable_setup_mount_names(bootstrap, "/service/file") == ()
    assert composition._reachable_setup_mount_names(bootstrap, "/service/missing") == ()


def test_first_preparation_marker_survives_settings_edit_and_repository_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = composition.open_repository(database)
    assert not repository.has_first_boot_preparation()
    settings = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v4",
            "revision": 1,
            "connections": [{"name": "primary", "endpoint": "https://library.invalid"}],
            "providers": [],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "books",
                    "media_kind": "ebook",
                    "provider_connections": [],
                    "mount": "books",
                }
            ],
        }
    )
    repository._connection.execute(  # noqa: SLF001 - composition persistence proof
        "INSERT INTO owner_settings VALUES(1,1,?)",
        (_canonical_json(settings.model_dump()),),
    )
    repository._connection.executemany(  # noqa: SLF001 - composition persistence proof
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        (
            ("connection.primary.key", "target-secret-1234"),
            ("owner.password.verifier", "$argon2id$synthetic"),
        ),
    )
    requested = repository.request_review_scan(
        "primary", "books", "books", "b" * 64, 1, first_boot=True
    )
    assert requested.scan_id == pipeline_scan_id("primary", "books", first_boot=True)
    repository.owner_settings_store().write(
        settings.model_copy(update={"revision": 2, "reduced_motion": "always_reduced"}),
        1,
        (),
    )
    repository.close()

    reopened = composition.open_repository(database, create=False)
    try:
        assert reopened.has_first_boot_preparation()
        assert reopened.owner_settings_store().read().document.revision == 2
        reopened._connection.execute(  # noqa: SLF001 - lifecycle persistence proof
            "UPDATE review_preparation_scans SET state='completed',total=1,"
            "updated_at=2 WHERE scan_id=?",
            (requested.scan_id,),
        )
        reopened._connection.execute(  # noqa: SLF001 - coherent lifecycle fixture
            "UPDATE review_preparation_presentation SET cycle_state='completed',"
            "updated_at=2 WHERE row_kind='enrollment' AND scan_id=?",
            (requested.scan_id,),
        )
        reopened._connection.execute(  # noqa: SLF001 - coherent lifecycle fixture
            "UPDATE review_preparation_presentation SET state='completed_clean',"
            "updated_at=2,completed_at=2 WHERE row_kind='run'"
        )
        assert reopened.has_first_boot_preparation()
        rescan = reopened.request_review_scan(
            "primary", "books", "books", "b" * 64, 3, first_boot=True
        )
        assert rescan.scan_id == pipeline_scan_id("primary", "books")
        assert reopened.has_first_boot_preparation()
        reopened.acknowledge_first_boot_preparation()
        assert not reopened.has_first_boot_preparation()
    finally:
        reopened.close()

    acknowledged = composition.open_repository(database, create=False)
    try:
        assert not acknowledged.has_first_boot_preparation()
        acknowledged.request_review_scan("primary", "books", "books", "b" * 64, 4)
        assert not acknowledged.has_first_boot_preparation()
    finally:
        acknowledged.close()


def test_normal_store_scan_history_never_becomes_first_preparation(
    tmp_path: Path,
) -> None:
    repository = composition.open_repository(tmp_path / "application.sqlite3")
    try:
        requested = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 1
        )
        assert requested.scan_id == pipeline_scan_id("primary", "books")
        assert not repository.has_first_boot_preparation()
    finally:
        repository.close()


def test_completed_first_library_keeps_marker_until_initial_cohort_is_terminal(
    tmp_path: Path,
) -> None:
    repository = composition.open_repository(tmp_path / "application.sqlite3")
    try:
        first = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 1, first_boot=True
        )
        second = repository.request_review_scan(
            "primary", "history", "history", "b" * 64, 1, first_boot=True
        )
        repository._connection.execute(  # noqa: SLF001 - lifecycle persistence proof
            "UPDATE review_preparation_scans SET state='completed',total=1,"
            "updated_at=2 WHERE scan_id=?",
            (first.scan_id,),
        )

        held = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 3, first_boot=True
        )
        assert held.scan_id == first.scan_id
        assert held.state == "completed"
        assert repository.has_first_boot_preparation()

        repository._connection.execute(  # noqa: SLF001 - lifecycle persistence proof
            "UPDATE review_preparation_scans SET state='completed',total=1,"
            "updated_at=4 WHERE scan_id=?",
            (second.scan_id,),
        )
        ordinary = repository.request_review_scan(
            "primary", "books", "books", "b" * 64, 5, first_boot=True
        )
        assert ordinary.scan_id == pipeline_scan_id("primary", "books")
        assert repository.has_first_boot_preparation()
    finally:
        repository.close()


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing", "profile_not_provisioned"),
        ("mode", "profile_unreadable"),
        ("symlink", "profile_unreadable"),
        ("nested", "profile_unreadable"),
        ("noncanonical", "profile_unreadable"),
        ("model", "profile_contract_mismatch"),
        ("memory", "profile_contract_mismatch"),
        ("processes", "profile_contract_mismatch"),
    ],
)
def test_composed_provisioning_precedes_host_and_refuses_current_drift(
    tmp_path, monkeypatch, mutation, reason
):
    import json

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from mechanical_librarian.application.contained_reviewer_contract import (
        canonical_json,
    )
    from mechanical_librarian.application.reviewer_access import (
        ReviewerAccessApplicationError,
    )
    from tests.support.description_advice import _contained_profile

    profile = _contained_profile(tmp_path)
    repository = open_repository(tmp_path / "reports.sqlite")
    operations = composition._compose_reviewer_access(
        repository,
        ReviewWebConfiguration(
            canonical_origin="http://127.0.0.1:8443",
            owner_password_hash_file="/unused/owner",
            cover_reviewer_profile_file=str(profile),
        ),
    )
    try:
        assert operations.read().codex.model == "gpt-5.6-sol"
        assert operations.current_cover_observation_identity() is None
        if mutation == "missing":
            profile.unlink()
        elif mutation == "mode":
            profile.chmod(0o644)
        elif mutation == "symlink":
            target = profile.with_suffix(".target")
            profile.rename(target)
            profile.symlink_to(target)
        elif mutation == "noncanonical":
            profile.write_bytes(profile.read_bytes() + b"\n")
        else:
            raw = json.loads(profile.read_bytes())
            if mutation == "nested":
                raw["codex"] = []
            elif mutation == "model":
                raw["model"] = "synthetic-model"
            elif mutation == "memory":
                raw["limits"]["memory_bytes"] = 536870912
            else:
                raw["limits"]["processes"] = 16
            profile.write_text(canonical_json(raw))
        # A generic read is cached passive evidence. Every explicit admission
        # re-reads the profile and refuses before host, credentials or process.
        assert operations.read().codex.model == "gpt-5.6-sol"
        monkeypatch.setattr(operations, "_codex_host", lambda: pytest.fail("host read"))
        for action in (
            operations.describe_contained_description,
            operations.recheck_codex,
            lambda: operations.start_codex(expected_revision=1),
            lambda: operations.select_codex(expected_revision=1),
        ):
            with pytest.raises(ReviewerAccessApplicationError) as caught:
                action()
            assert caught.value.code == "not_provisioned"
        view = operations.read()
        assert view.codex.state == "not_provisioned"
        assert view.codex.reason == reason
        assert view.codex.model is None
        assert view.mapping is None and view.description_mapping is None
        assert operations.cancel_codex().state == "ready"
    finally:
        operations.close()
        repository.close()


@pytest.mark.parametrize(
    "drift", ("missing", "invalid", "unsafe_store", "stale", "never_admitted")
)
def test_composed_clear_uses_only_previously_admitted_cleanup_authority(
    tmp_path, monkeypatch, drift
):
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.reviewer_access import (
        ReviewerAccessApplicationError,
    )
    from mechanical_librarian.domain.reviewer_access import CodexTaskSelection
    from tests.support.description_advice import _contained_profile

    profile = _contained_profile(tmp_path)
    auth = tmp_path / "auth"
    auth.mkdir(mode=0o700)
    credential = auth / "auth.json"
    credential.write_text('{"synthetic":"credential"}')
    credential.chmod(0o600)
    if drift == "never_admitted":
        profile.unlink()
    repository = open_repository(tmp_path / "reports.sqlite")
    store = repository.reviewer_access_store()
    document, _ = store.read()
    store.replace(
        document.model_copy(
            update={
                "revision": document.revision + 1,
                "cover_observation": CodexTaskSelection(engine="codex"),
                "description_rewrite": CodexTaskSelection(engine="codex"),
            }
        ),
        expected_revision=document.revision,
    )
    coordinator = composition._CodexAuthLeaseCoordinator()
    review = ReviewWebConfiguration(
        canonical_origin="http://127.0.0.1:8443",
        owner_password_hash_file="/unused/owner",
        cover_reviewer_profile_file=str(profile),
    )
    operations = composition._compose_reviewer_access(repository, review, coordinator)
    successor = None
    try:
        lazy = operations._codex_auth
        assert isinstance(lazy, composition._LazyCodexAuth)
        assert lazy._manager is None
        monkeypatch.setattr(
            composition,
            "prepare_contained_device_auth_command",
            lambda *_: pytest.fail("cleanup executed a command"),
        )
        if drift == "missing":
            profile.unlink()
        elif drift == "invalid":
            profile.write_text("{}")
        elif drift == "unsafe_store":
            profile.unlink()
            auth.chmod(0o755)
        elif drift == "stale":
            lazy.activate_lease()
            successor = composition._compose_reviewer_access(
                repository, review, coordinator
            )
            successor.activate_codex_lease()
            profile.unlink()
        if drift in ("missing", "invalid"):
            cleared = operations.clear_codex(expected_revision=2)
            assert not credential.exists()
            assert cleared.mapping is None and cleared.description_mapping is None
            assert cleared.revision == 3
        else:
            with pytest.raises(ReviewerAccessApplicationError) as caught:
                operations.clear_codex(expected_revision=2)
            assert caught.value.code == "credential_clear_failed"
            assert credential.read_text() == '{"synthetic":"credential"}'
    finally:
        if successor is not None:
            successor.close()
        operations.close()
        repository.close()
