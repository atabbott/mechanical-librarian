"""The refusal a workbench script gives when a database copy's Unicode differs.

Browse facts record the Unicode version they were folded under, and the reader
refuses a database whose version differs from the running Python's
(`docs/reference/compatibility.md`). The checkout's `.python-version` pins the
image's Python minor version, so `uv run` picks a Python that reads a copy of
the install's database. When a script still runs under another Python, the
refusal names both Unicode versions and the remedy instead of sending the
owner for a copy they already have (#2018).
"""

from __future__ import annotations

import platform
import re
from pathlib import Path

from mechanical_librarian.adapters.persistence.schema import BrowseFactUnicodeError

PYTHON_PIN = Path(__file__).resolve().parents[1] / ".python-version"
_UNICODE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def pinned_python() -> str | None:
    """The checkout's pinned Python minor version, such as `3.15`."""
    try:
        value = PYTHON_PIN.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value if re.fullmatch(r"[0-9]+\.[0-9]+", value) else None


def unicode_refusal(error: BrowseFactUnicodeError) -> str:
    """Both Unicode versions, then the Python to run with or the copy to take."""
    running_python = platform.python_version()
    # The stored value is the copy's text; only a version number is repeated.
    stored = (
        f"Unicode {error.stored}"
        if _UNICODE_VERSION.fullmatch(error.stored)
        else "another Unicode version"
    )
    stated = (
        f"This copy uses {stored}, and Python {running_python} uses "
        f"Unicode {error.running}."
    )
    pin = pinned_python()
    if pin is None:
        return (
            f"{stated} Run the script with the Python version your install's "
            "image uses."
        )
    if ".".join(running_python.split(".")[:2]) != pin:
        return f"{stated} Run the script with Python {pin}, as the image does."
    # This Python is the image's, so the copy came from another install.
    return f"{stated} Copy the database from an install built from this checkout."
