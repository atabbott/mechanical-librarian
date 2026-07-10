"""Module entrypoint for ``python -m mechanical_librarian``."""

from .interfaces.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
