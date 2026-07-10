"""Module entrypoint for ``python -m mechanical_librarian``."""

from .bootstrap import main


if __name__ == "__main__":
    raise SystemExit(main())
