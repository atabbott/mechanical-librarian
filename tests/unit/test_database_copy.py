"""The refusal for a database copy folded under another Unicode version (#2018)."""

from __future__ import annotations

import os
import sqlite3
import unicodedata
from pathlib import Path

import pytest
from scripts import database_copy

# No supported Python has this Unicode version, so it always differs.
OTHER = "1.0.0"


def other_unicode(database: Path) -> str:
    """Record another Unicode version in a copy; return the refusal it gets.

    The script tests run under whichever Python the tier uses, so the expected
    text is this module's for that Python. The wording is pinned below.
    """
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE browse_fact_identity SET unicode_version=?", (OTHER,)
        )
        connection.commit()
    finally:
        connection.close()
    refusal = database_copy.unicode_refusal(database)
    assert refusal is not None
    assert refusal.startswith(f"This copy uses Unicode {OTHER}, and Python ")
    assert f" uses Unicode {unicodedata.unidata_version}. " in refusal
    return refusal


def _copy(tmp_path: Path, version: object) -> Path:
    database = tmp_path / "application.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE browse_fact_identity (singleton INTEGER PRIMARY KEY, "
        "unicode_version)"
    )
    connection.execute("INSERT INTO browse_fact_identity VALUES (1, ?)", (version,))
    connection.commit()
    connection.close()
    return database


STATED = "This copy uses Unicode 17.0.0, and Python 3.14.8 uses Unicode 16.0.0."


@pytest.fixture
def python_3_14(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(database_copy.platform, "python_version", lambda: "3.14.8")
    monkeypatch.setattr(database_copy.unicodedata, "unidata_version", "16.0.0")
    pin = tmp_path / ".python-version"
    monkeypatch.setattr(database_copy, "PYTHON_PIN", pin)
    return pin


def test_another_python_is_told_the_pinned_python(
    python_3_14: Path, tmp_path: Path
) -> None:
    python_3_14.write_text("3.15\n")
    assert database_copy.unicode_refusal(_copy(tmp_path, "17.0.0")) == (
        f"{STATED} Run the script with Python 3.15, as the image does."
    )


def test_the_pinned_python_is_told_to_copy_from_this_checkout(
    python_3_14: Path, tmp_path: Path
) -> None:
    python_3_14.write_text("3.14\n")
    assert database_copy.unicode_refusal(_copy(tmp_path, "17.0.0")) == (
        f"{STATED} Copy the database from an install built from this checkout."
    )


@pytest.mark.parametrize(
    "pin", [None, "3.15.2\n", "latest\n"], ids=["missing", "patch", "word"]
)
def test_an_unreadable_pin_names_the_image_python(
    python_3_14: Path, tmp_path: Path, pin: str | None
) -> None:
    if pin is not None:
        python_3_14.write_text(pin)
    assert database_copy.unicode_refusal(_copy(tmp_path, "17.0.0")) == (
        f"{STATED} Run the script with the Python version your install's image uses."
    )


def test_only_a_version_number_is_repeated_from_the_copy(
    python_3_14: Path, tmp_path: Path
) -> None:
    python_3_14.write_text("3.15\n")
    assert database_copy.unicode_refusal(_copy(tmp_path, "17.0.0\nsecret")) == (
        "This copy uses another Unicode version, and Python 3.14.8 uses Unicode "
        "16.0.0. Run the script with Python 3.15, as the image does."
    )


@pytest.mark.parametrize(
    "version",
    ["16.0.0", 17, None, "", "9" * 33, "9" * 10_000_000],
    ids=["same", "integer", "null", "empty", "over_limit", "huge"],
)
def test_no_refusal_when_the_copy_records_this_unicode_or_no_text(
    python_3_14: Path, tmp_path: Path, version: object
) -> None:
    python_3_14.write_text("3.15\n")
    assert database_copy.unicode_refusal(_copy(tmp_path, version)) is None


def test_no_refusal_for_a_file_that_is_not_such_a_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    garbage = tmp_path / "garbage.sqlite3"
    garbage.write_bytes(b"not a database")
    other = tmp_path / "other.sqlite3"
    sqlite3.connect(other).close()
    for database in (missing, garbage, other):
        assert database_copy.unicode_refusal(database) is None
    assert not missing.exists()
    assert garbage.read_bytes() == b"not a database"


def test_two_identity_rows_give_no_refusal(python_3_14: Path, tmp_path: Path) -> None:
    database = _copy(tmp_path, "17.0.0")
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO browse_fact_identity VALUES (2, '17.0.0')")
    connection.commit()
    connection.close()
    assert database_copy.unicode_refusal(database) is None


def test_a_pathname_that_is_not_utf8_is_read(python_3_14: Path, tmp_path: Path) -> None:
    python_3_14.write_text("3.15\n")
    folder = Path(os.fsdecode(bytes(tmp_path) + b"/copy-\xff"))
    try:
        folder.mkdir()
    except (OSError, UnicodeError):
        pytest.skip("this filesystem refuses the name")
    database = _copy(folder, "17.0.0")
    assert database_copy.unicode_refusal(database) == (
        f"{STATED} Run the script with Python 3.15, as the image does."
    )


def test_reading_the_version_writes_nothing(tmp_path: Path) -> None:
    database = _copy(tmp_path, OTHER)
    before = database.read_bytes()
    assert database_copy.stored_unicode_version(database) == OTHER
    assert database.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [database.name]


def test_the_checkout_pin_is_read() -> None:
    assert database_copy.pinned_python() == (
        database_copy.PYTHON_PIN.read_text().strip()
    )
