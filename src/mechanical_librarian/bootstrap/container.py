"""Fixed, least-authority entry point for the supported single container."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.bootstrap.composition import run_review_server

DATA = Path("/data")
CONFIG = DATA / "config.json"
DATABASE = DATA / "application.sqlite3"
CATALOGUE = DATA / "catalogue.sqlite3"
ARTIFACTS = DATA / "artifacts"


def main() -> None:
    information = DATA.lstat()
    if (not stat.S_ISDIR(information.st_mode)
            or information.st_uid != os.geteuid()):
        raise RuntimeError("container data volume is unavailable")
    os.chmod(DATA, 0o700)
    ARTIFACTS.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ARTIFACTS, 0o700)
    repository = open_repository(DATABASE, create=True, cross_thread=True)
    repository.close()
    catalogue = open_catalogue(CATALOGUE, create=True)
    catalogue.close()
    run_review_server(CONFIG, DATABASE, CATALOGUE, ARTIFACTS, "0.0.0.0", 8443)


if __name__ == "__main__":
    main()
