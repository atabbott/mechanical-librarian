"""One real store-authority runtime with every background lane held closed.

Shared by the composition tests that drive owner actions through a real
activation, its generations and its cutovers.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.owner_settings import OWNER_SETTINGS_SCHEMA
from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob
from mechanical_librarian.infrastructure.settings_authority import (
    REVIEW_BOOTSTRAP_SCHEMA,
)


class StoreRuntime:
    """One real store-authority runtime whose activations reach reconcile."""

    def __init__(self, root: Path, generation: int) -> None:
        from argon2 import PasswordHasher

        from mechanical_librarian.adapters.persistence.documents import _canonical_json
        from mechanical_librarian.application.operation_admission import (
            OperationAdmission,
        )
        from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
        from mechanical_librarian.infrastructure.settings_authority import (
            ReviewBootstrap,
        )
        from mechanical_librarian.interfaces.web.runtime_dispatch import (
            MutableRuntimeDispatcher,
        )

        self.root = root
        self.database = root / "reports.sqlite"
        self.admission = OperationAdmission()
        self.control = open_repository(
            self.database, create=True, cross_thread=True, admission=self.admission
        )
        document = OwnerSettingsDocument.model_validate(
            {
                "schema_version": OWNER_SETTINGS_SCHEMA,
                "revision": 1,
                "connections": [
                    {"name": "primary", "endpoint": "https://library.example.invalid"}
                ],
                "providers": [
                    {"name": "google", "adapter": "google_books", "enabled": True}
                ],
                "libraries": [
                    {
                        "name": "books",
                        "connection": "primary",
                        "library_id": "library-1",
                        "media_kind": "ebook",
                        "provider_connections": ["google"],
                    }
                ],
                "provider_recovery_generation": generation,
            }
        )
        self.control._connection.execute(
            "INSERT INTO owner_settings(singleton,revision,document) VALUES(1,1,?)",
            (_canonical_json(document.model_dump()),),
        )
        self.control._connection.executemany(
            "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?)",
            (
                ("connection.primary.key", "synthetic-target-key"),
                ("owner.password.verifier", PasswordHasher().hash("synthetic-owner")),
            ),
        )
        self.control._connection.commit()
        self.bootstrap = ReviewBootstrap(
            schema_version=REVIEW_BOOTSTRAP_SCHEMA,
            canonical_origin="https://review.example.invalid",
            tls_certificate_file="/synthetic/certificate",
            tls_private_key_file="/synthetic/key",
        )
        composition.CoverArtifactStore(root / "artifacts", create=True)
        self.recovery_path = composition._private_root(self.database) / (
            "provider-recovery.sqlite"
        )
        self.applications: list[object] = []
        self.dispatcher: MutableRuntimeDispatcher | None = None
        self.activation: composition._StoreRuntimeActivation | None = None

    def seed(self) -> None:
        """Waiting rate-limited health with queued jobs at the current generation."""
        store = SQLiteProviderRecoveryStore.open(self.recovery_path)
        generation = self.generation()
        store.observe_failure(
            connection="google",
            adapter="google_books",
            scope="books",
            generation=generation,
            failure="rate_limited",
            now=100,
            retry_after=3_600,
        )
        store.enqueue_jobs(
            tuple(
                ProviderRecoveryJob(
                    "google",
                    "google_books",
                    "books",
                    generation,
                    f"member-{index}",
                    "primary",
                    "books",
                    f"item-{index}",
                    "f" * 64,
                    "report-" + "2" * 64,
                    None,
                    "report-" + "2" * 64,
                    None,
                    None,
                    "report_only",
                    "[]",
                )
                for index in range(3)
            ),
            enqueued_at=100,
        )
        store.close()

    def generation(self) -> int:
        return int(
            json.loads(
                self.control._connection.execute(
                    "SELECT document FROM owner_settings WHERE singleton=1"
                ).fetchone()[0]
            )["provider_recovery_generation"]
        )

    def start(self):
        from mechanical_librarian.interfaces.web.runtime_dispatch import (
            MutableRuntimeDispatcher,
        )

        activation = composition._StoreRuntimeActivation(
            self.bootstrap,
            self.database,
            self.root / "catalogue.sqlite",
            self.root / "artifacts",
            self.control.owner_settings_store(),
            self.control.authority,
            operation_admission=self.admission,
        )
        runtime, _ = composition.compose_store_runtime_generation(
            self.bootstrap,
            self.database,
            self.root / "catalogue.sqlite",
            self.root / "artifacts",
            repository_authority=self.control.authority,
            operation_admission=self.admission,
            activation=activation,
        )
        self.dispatcher = MutableRuntimeDispatcher(runtime)
        activation.bind(self.dispatcher)
        self.activation = activation
        self.dispatcher.start()
        return self.applications[-1]

    def stop(self) -> None:
        if self.dispatcher is not None:
            current = self.dispatcher.current()
            current.stop_admission()
            current.begin_drain()
            current.close()
            self.dispatcher = None

    def recovery_state(self) -> dict[str, list[list[object]]]:
        check = sqlite3.connect(self.recovery_path)
        try:
            return json.loads(
                json.dumps(
                    {
                        table: [
                            list(row)
                            for row in check.execute(
                                f"SELECT * FROM {table} ORDER BY 1,2,3,4,5"
                            )
                        ]
                        for table in (
                            "provider_recovery_health",
                            "provider_recovery_jobs",
                            "provider_recovery_events",
                        )
                    }
                )
            )
        finally:
            check.close()

    def close(self) -> None:
        self.stop()
        self.control.close()


@contextmanager
def quiet_store_runtime(root: Path, generation: int):
    """Hold every background lane closed so only owner actions touch the store."""
    runtime = StoreRuntime(root, generation)
    compose = composition.compose_review_application

    def capture(**kwargs):
        application = compose(**kwargs)
        runtime.applications.append(application)
        return application

    with (
        patch.object(composition.time, "time", return_value=50_000),
        patch.object(composition, "compose_review_application", side_effect=capture),
        patch.object(
            composition.ReviewPreparationSupervisor, "admit", return_value=None
        ),
        patch.object(
            composition.ContinuousGuardedApplication, "admit", return_value=None
        ),
    ):
        try:
            yield runtime
        finally:
            runtime.close()
