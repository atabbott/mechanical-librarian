"""Fixed, least-authority entry point for the supported single container."""

from __future__ import annotations

import os
import signal
import stat
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.infrastructure.first_boot import select_first_boot_mode

DATA = Path("/data")
CONFIG = DATA / "config.json"
DATABASE = DATA / "application.sqlite3"
CATALOGUE = DATA / "catalogue.sqlite3"
ARTIFACTS = DATA / "artifacts"
# The in-container bind stays on every interface so a published port mapping
# reaches it. Which interfaces the port is published *on* is the host's choice,
# and `compose.yaml` makes it loopback by default.
HOST = "0.0.0.0"
PORT = 8443


def _exit_on_container_stop(_signal_number: int, _frame: object) -> None:
    """Turn Uvicorn's post-shutdown SIGTERM replay into a clean container exit."""
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, _exit_on_container_stop)
    information = DATA.lstat()
    if not stat.S_ISDIR(information.st_mode) or information.st_uid != os.geteuid():
        raise RuntimeError("container data volume is unavailable")
    os.chmod(DATA, 0o700)

    def complete_store() -> bool:
        repository = open_repository(
            DATABASE, create=False, recover_stale_bindings=True
        )
        try:
            repository.owner_settings_store().activate_runtime()
            return True
        except Exception:
            return False
        finally:
            repository.close()

    def resumable_setup() -> bool:
        repository = open_repository(DATABASE, create=False)
        try:
            repository.setup_state_store().read()
            return True
        except Exception:
            return False
        finally:
            repository.close()

    def uninitialized_setup() -> bool:
        repository = open_repository(DATABASE, create=False)
        try:
            return repository.setup_state_store().is_exact_schema_only()
        finally:
            repository.close()

    mode = select_first_boot_mode(
        DATA,
        complete_store=complete_store,
        resumable_setup=resumable_setup,
        uninitialized_setup=uninitialized_setup,
    )
    run_review_server(
        CONFIG,
        DATABASE,
        CATALOGUE,
        ARTIFACTS,
        HOST,
        PORT,
        first_boot_mode=mode,
    )


if __name__ == "__main__":
    main()
