"""The refusal for a database copy folded under another Unicode version (#2018)."""

from __future__ import annotations

import sqlite3
import unicodedata
from pathlib import Path

import pytest
from scripts import database_copy

from mechanical_librarian.adapters.persistence.schema import BrowseFactUnicodeError


def other_unicode(database: Path) -> str:
    """Record another Unicode version in a copy; return the refusal it gets.

    The script tests run under whichever Python the tier uses, so the expected
    text is this module's for that Python. The wording is pinned above.
    """
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE browse_fact_identity SET unicode_version='15.1.0'")
        connection.commit()
    finally:
        connection.close()
    refusal = database_copy.unicode_refusal(
        BrowseFactUnicodeError("15.1.0", unicodedata.unidata_version)
    )
    assert "This copy uses Unicode 15.1.0, and Python " in refusal
    assert f" uses Unicode {unicodedata.unidata_version}." in refusal
    return refusal


STATED = "This copy uses Unicode 17.0.0, and Python 3.14.8 uses Unicode 16.0.0."


@pytest.fixture
def python_3_14(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(database_copy.platform, "python_version", lambda: "3.14.8")
    pin = tmp_path / ".python-version"
    monkeypatch.setattr(database_copy, "PYTHON_PIN", pin)
    return pin


def test_another_python_is_told_the_pinned_python(python_3_14: Path) -> None:
    python_3_14.write_text("3.15\n")
    assert database_copy.unicode_refusal(
        BrowseFactUnicodeError("17.0.0", "16.0.0")
    ) == (f"{STATED} Run the script with Python 3.15, as the image does.")


def test_the_pinned_python_is_told_to_copy_from_this_checkout(
    python_3_14: Path,
) -> None:
    python_3_14.write_text("3.14\n")
    assert database_copy.unicode_refusal(
        BrowseFactUnicodeError("17.0.0", "16.0.0")
    ) == (f"{STATED} Copy the database from an install built from this checkout.")


@pytest.mark.parametrize(
    "pin", [None, "3.15.2\n", "latest\n"], ids=["missing", "patch", "word"]
)
def test_an_unreadable_pin_names_the_image_python(
    python_3_14: Path, pin: str | None
) -> None:
    if pin is not None:
        python_3_14.write_text(pin)
    assert database_copy.unicode_refusal(
        BrowseFactUnicodeError("17.0.0", "16.0.0")
    ) == (f"{STATED} Run the script with the Python version your install's image uses.")


def test_only_a_version_number_is_repeated_from_the_copy(python_3_14: Path) -> None:
    python_3_14.write_text("3.15\n")
    assert database_copy.unicode_refusal(
        BrowseFactUnicodeError("17.0.0\nsecret", "16.0.0")
    ) == (
        "This copy uses another Unicode version, and Python 3.14.8 uses Unicode "
        "16.0.0. Run the script with Python 3.15, as the image does."
    )


def test_the_checkout_pin_is_read() -> None:
    assert database_copy.pinned_python() == (
        database_copy.PYTHON_PIN.read_text().strip()
    )
