"""The projection that lets a real filesystem's names reach the manifest.

Track names arrive from a mounted filesystem, so they carry whatever wrote
them -- including the C1 control characters a double-encoded en dash leaves
behind in most of one 75-file item's names.  The manifest entry is bounded
evidence, so the name is projected rather than refused: an item is not denied
its whole evidence set over a value nothing is decided from.  These run
against the pure function and the domain value it feeds, with no mount, child,
or probe involved.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.audio_inspection import (
    MAX_SIDECAR_NAME_CHARACTERS,
    AudioTrackDigest,
    project_track_name,
    project_track_name_detail,
)

# What a UTF-8 en dash becomes when it is decoded as Latin-1 and written back:
# one legible character and the two C1 controls the evidence bounds refuse.
MOJIBAKE_EN_DASH = "\u00e2\u0080\u0093"
PROJECTED_EN_DASH = "\u00e2\ufffd\ufffd"


def test_a_double_encoded_en_dash_is_replaced_where_it_stands() -> None:
    """The name keeps its shape; only the refused codepoints stop being them."""
    projected = project_track_name(f"05 {MOJIBAKE_EN_DASH} Endgame.mp3", 5)

    assert projected == f"05 {PROJECTED_EN_DASH} Endgame.mp3"


def test_a_name_the_bounds_already_admit_is_carried_through_unchanged() -> None:
    assert project_track_name("01 - Chapter One.mp3", 1) == "01 - Chapter One.mp3"


def test_the_same_name_in_two_encodings_projects_to_one_value() -> None:
    """Normalization is part of the projection, so two manifests compare."""
    composed = project_track_name("Caf\u00e9 Nights.mp3", 1)

    assert composed == project_track_name("Cafe\u0301 Nights.mp3", 1)
    assert composed == "Caf\u00e9 Nights.mp3"


def test_a_name_with_nothing_projectable_left_is_published_as_its_position() -> None:
    """A row of replacement characters would state a name rather than none."""
    assert project_track_name("\u0080\u0093\u0001 \u0093", 3) == "track-3"


def test_an_empty_name_is_published_as_its_position_rather_than_refused() -> None:
    assert project_track_name("", 12) == "track-12"


def test_a_name_past_the_manifest_bound_is_cut_to_it() -> None:
    projected = project_track_name("x" * 300, 7)

    assert projected == "x" * MAX_SIDECAR_NAME_CHARACTERS


def test_a_name_that_reads_as_a_production_path_is_not_projected_at_all() -> None:
    """Cutting a path down still leaves a path; the position is publishable."""
    assert project_track_name("/srv/audiobooks/Discworld/01.mp3", 2) == "track-2"


def test_the_projected_name_is_exactly_what_the_manifest_admits() -> None:
    """The validator is not loosened: the projection is what is handed to it.

    A retained document whose name still carries a raw control character is
    refused as before, which is what keeps a name that never passed through
    this projection from reaching a reader.
    """
    raw = f"05 {MOJIBAKE_EN_DASH} Endgame.mp3"

    with pytest.raises(ValueError):
        AudioTrackDigest(5, f"{5:064x}", f"{6:064x}", "mp3", raw)

    admitted = AudioTrackDigest(
        5, f"{5:064x}", f"{6:064x}", "mp3", project_track_name(raw, 5)
    )
    assert admitted.to_data()["name"] == f"05 {PROJECTED_EN_DASH} Endgame.mp3"


def test_a_name_of_literal_replacement_characters_is_a_name_the_filesystem_stated() -> (
    None
):
    """Survival is judged on what was stated, not on what the substitution emits."""
    projection = project_track_name_detail("\ufffd\ufffd", 4)

    assert projection.name == "\ufffd\ufffd"
    assert projection.synthesized is False
    assert projection.repaired is False


def test_the_projection_records_whether_it_repaired_or_synthesized() -> None:
    repaired = project_track_name_detail(f"05 {MOJIBAKE_EN_DASH} Endgame.mp3", 5)
    synthesized = project_track_name_detail("\u0080\u0093", 3)
    plain = project_track_name_detail("track-1", 1)

    assert (repaired.synthesized, repaired.repaired) == (False, True)
    assert (synthesized.name, synthesized.synthesized, synthesized.repaired) == (
        "track-3",
        True,
        True,
    )
    assert (plain.name, plain.synthesized, plain.repaired) == ("track-1", False, False)
