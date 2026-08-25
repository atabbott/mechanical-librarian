"""Fixed, least-authority entry point for the supported single container."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.bootstrap.composition import (
    run_review_server,
    run_setup_server,
)
from mechanical_librarian.infrastructure.first_boot import (
    FirstBootMode,
    select_first_boot_mode,
)
from mechanical_librarian.infrastructure.settings_authority import load_review_bootstrap

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


def main() -> None:
    information = DATA.lstat()
    if not stat.S_ISDIR(information.st_mode) or information.st_uid != os.geteuid():
        raise RuntimeError("container data volume is unavailable")
    os.chmod(DATA, 0o700)
    bootstrap_path = Path("/run/secrets/review-bootstrap.json")
    bootstrap = None

    def complete_store() -> bool:
        repository = open_repository(DATABASE, create=False)
        try:
            repository.owner_settings_store().activate_runtime()
            return True
        except Exception:
            return False
        finally:
            repository.close()

    def resumable_setup() -> bool:
        nonlocal bootstrap
        if bootstrap is None:
            bootstrap = load_review_bootstrap(bootstrap_path)
        if bootstrap.setup_owner_verifier is None:
            return False
        repository = open_repository(DATABASE, create=False)
        try:
            repository.setup_state_store().read(bootstrap.setup_owner_verifier)
            return True
        except Exception:
            return False
        finally:
            repository.close()

    mode = select_first_boot_mode(
        DATA, complete_store=complete_store, resumable_setup=resumable_setup
    )
    if mode in {FirstBootMode.INITIALIZE_SETUP, FirstBootMode.RESUME_SETUP}:
        if bootstrap is None:
            bootstrap = load_review_bootstrap(bootstrap_path)
        run_setup_server(
            bootstrap,
            DATABASE,
            HOST,
            PORT,
            initialize=mode is FirstBootMode.INITIALIZE_SETUP,
        )
        return
    ARTIFACTS.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ARTIFACTS, 0o700)
    catalogue = open_catalogue(CATALOGUE, create=True)
    catalogue.close()
    run_review_server(CONFIG, DATABASE, CATALOGUE, ARTIFACTS, HOST, PORT)


if __name__ == "__main__":
    main()
