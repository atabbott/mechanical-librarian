"""The refusal an owner script gives when a database copy's Unicode differs.

Browse facts record the Unicode version they were folded under, and the reader
refuses a database whose version differs from the running Python's
(`docs/reference/compatibility.md`). The checkout's `.python-version` pins the
image's Python minor version, so `uv run` picks a Python that reads a copy of
the install's database. When a script still runs under another Python, the
refusal names both Unicode versions and the remedy instead of sending the
owner for a copy they already have (#2018).

The reader's refusal stays a plain `MetadataReportError`; after it, a script
asks this module whether the copy's recorded Unicode version is the cause.
"""

from __future__ import annotations

import os
import platform
import re
import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import quote

PYTHON_PIN = Path(__file__).resolve().parents[1] / ".python-version"
_UNICODE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
# The schema's CHECK bound; the copy was refused, so it is not trusted to hold.
_STORED_LIMIT = 32


def pinned_python() -> str | None:
    """The checkout's pinned Python minor version, such as `3.15`."""
    try:
        value = PYTHON_PIN.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return value if re.fullmatch(r"[0-9]+\.[0-9]+", value) else None


def stored_unicode_version(database: Path) -> str | None:
    """The Unicode version the copy's browse facts record, read without writing.

    None when the file is not a database that records exactly one short text
    value. Only a bounded prefix is ever loaded.
    """
    try:
        # Bytes keep a pathname that is not valid UTF-8 exactly as the OS has it.
        absolute = quote(os.fsencode(database.absolute()), safe="/")
        connection = sqlite3.connect(f"file:{absolute}?mode=ro&immutable=1", uri=True)
        try:
            rows = connection.execute(
                "SELECT typeof(unicode_version),length(unicode_version),"
                "substr(unicode_version,1,?) FROM browse_fact_identity LIMIT 2",
                (_STORED_LIMIT,),
            ).fetchall()
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError):
        return None
    if len(rows) != 1:
        return None
    kind, length, value = rows[0]
    if kind != "text" or not 1 <= length <= _STORED_LIMIT:
        return None
    return str(value)


def unicode_refusal(database: Path) -> str | None:
    """Both Unicode versions, then the Python to run with or the copy to take.

    None when the copy records this Python's Unicode version, or none at all.
    """
    stored = stored_unicode_version(database)
    running = unicodedata.unidata_version
    if stored is None or stored == running:
        return None
    running_python = platform.python_version()
    # The stored value is the copy's text; only a version number is repeated.
    copy = (
        f"Unicode {stored}"
        if _UNICODE_VERSION.fullmatch(stored)
        else "another Unicode version"
    )
    stated = (
        f"This copy uses {copy}, and Python {running_python} uses Unicode {running}."
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
