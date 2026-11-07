from __future__ import annotations

import io
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import scripts.replay_cover_rules as replay_cover_rules

from mechanical_librarian.domain.cover_rules import (
    COLOUR_BINS,
    COVER_FLOOR_SHORT_EDGE,
    LAYOUT_VALUES,
    SHARPER_COPY_MIN_AREA_RATIO,
)

ALL_INPUTS = list(replay_cover_rules.OPTIONAL_INPUTS)


def _measures(art: int = 0) -> dict[str, object]:
    colour = [0] * COLOUR_BINS
    colour[2 * art] = 100
    block = 0 if art == 0 else ((1 << 16) - 1) << (16 * (art - 1))
    return {
        "phash": f"{block:016x}",
        "dhash": f"{block:016x}",
        "colour": colour,
        "layout": [0] * LAYOUT_VALUES,
    }


def _candidate(
    width: int = 400, height: int = 600, **extra: object
) -> dict[str, object]:
    return {
        "identity": "verified_work_match",
        "width": width,
        "height": height,
        "packet_ordinal": 0,
        **extra,
    }


def _current(width: int = 400, height: int = 600, **extra: object) -> dict[str, object]:
    return {
        "readable": True,
        "identity": "verified_work_match",
        "width": width,
        "height": height,
        **extra,
    }


def _copy_width() -> int:
    width = 400
    while width * (3 * width // 2) < SHARPER_COPY_MIN_AREA_RATIO * 400 * 600:
        width += 2
    return width


def _document(persisted: list[str], fields: Sequence[object]) -> dict[str, object]:
    return {
        "schema": "cover-rules-replay-input-v1",
        "persisted": persisted,
        "fields": fields,
    }


def _run(tmp_path: Path, document: object) -> tuple[int, str, str]:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    stdout, stderr = io.StringIO(), io.StringIO()
    code = replay_cover_rules.main([str(path)], stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


SYNTHETIC_FIELDS: list[object] = [
    # A fault-free ebook cover: kept.
    {
        "medium": "ebook",
        "current": _current(measures=_measures(0)),
        "candidates": [_candidate(measures=_measures(1))],
    },
    # A square ebook cover: replaced, and the square candidate excluded.
    {
        "medium": "ebook",
        "current": _current(500, 500),
        "candidates": [_candidate(500, 500), _candidate(measures=_measures(1))],
    },
    # A sharper copy of the current art.
    {
        "medium": "ebook",
        "current": _current(measures=_measures(0)),
        "candidates": [
            _candidate(_copy_width(), 3 * _copy_width() // 2, measures=_measures(0))
        ],
    },
    # An unreadable current cover: suggested only.
    {
        "medium": "audiobook",
        "current": {"readable": False, "identity": None},
        "candidates": [_candidate(500, 500)],
    },
    # A known-empty audiobook target whose only candidate is under the floor.
    {
        "medium": "audiobook",
        "current": None,
        "candidates": [
            _candidate(COVER_FLOOR_SHORT_EDGE - 1, COVER_FLOOR_SHORT_EDGE - 1)
        ],
    },
    # A title that names another work: held.
    {
        "medium": "ebook",
        "current": _current(),
        "candidates": [_candidate(title_differs=True)],
        "title_conflict": True,
    },
    # A work-level cover with an edition alternative.
    {
        "medium": "ebook",
        "current": _current(measures=_measures(0)),
        "candidates": [
            _candidate(identity="verified_manifestation_match", measures=_measures(2))
        ],
    },
]


def test_replay_reports_aggregates_per_setting_and_medium(tmp_path: Path) -> None:
    code, stdout, stderr = _run(tmp_path, _document(ALL_INPUTS, SYNTHETIC_FIELDS))

    assert (code, stderr) == (0, "")
    report = json.loads(stdout)
    assert report["schema"] == "cover-rules-replay-v1"
    assert report["fields"] == {"ebook": 5, "audiobook": 2}
    assert set(report["rules"].values()) == {"replayed"}
    assert report["outcomes"]["protected"]["ebook"] == {
        "keep": 2,
        "replace": 2,
        "suggest": 0,
        "hold": 1,
        "fill": 0,
        "empty": 0,
    }
    assert report["outcomes"]["protected"]["audiobook"]["suggest"] == 1
    assert report["outcomes"]["protected"]["audiobook"]["empty"] == 1
    assert report["outcomes"]["proposals_only"]["ebook"]["suggest"] == 2
    assert report["outcomes"]["protected+prefer_edition"]["ebook"]["suggest"] == 1
    assert report["outcomes"]["unprotected+prefer_edition"]["ebook"]["replace"] == 3
    assert report["outcomes"]["unprotected"] == report["outcomes"]["protected"]
    assert report["issues"]["ebook"]["wrong_shape"] == 1
    assert report["issues"]["ebook"]["sharper_copy"] == 1
    assert report["issues"]["ebook"]["title_names_other_work"] == 1
    assert report["issues"]["audiobook"]["unreadable"] == 1
    assert report["exclusions"]["ebook"]["wrong_shape"] == 1
    assert report["exclusions"]["audiobook"]["below_floor"] == 1
    assert report["collapsed_images"] == {"ebook": 1, "audiobook": 0}


def test_a_rule_whose_input_is_not_persisted_is_not_replayable(
    tmp_path: Path,
) -> None:
    fields = [
        {
            "medium": "ebook",
            "current": _current(),
            "candidates": [_candidate(_copy_width(), 3 * _copy_width() // 2)],
        }
    ]

    code, stdout, _ = _run(tmp_path, _document([], fields))

    assert code == 0
    report = json.loads(stdout)
    assert report["rules"] == {
        "shape": "replayed",
        "floor": "replayed",
        "same_art": "not_replayable",
        "edition_language": "not_replayable",
        "omnibus": "not_replayable",
        "title": "not_replayable",
    }
    assert report["issues"]["ebook"]["sharper_copy"] == 0
    assert report["outcomes"]["protected"]["ebook"]["keep"] == 1
    assert report["images_without_measures"] == 2


@pytest.mark.parametrize(
    ("persisted", "field"),
    [
        (
            [],
            {
                "medium": "ebook",
                "current": _current(measures=_measures()),
                "candidates": [],
            },
        ),
        (
            [],
            {
                "medium": "ebook",
                "current": None,
                "candidates": [_candidate(title_differs=True)],
            },
        ),
        (
            ["title"],
            {"medium": "ebook", "current": None, "candidates": [], "omnibus": True},
        ),
    ],
    ids=["measures", "title_flag", "omnibus_flag"],
)
def test_an_undeclared_input_is_refused(
    tmp_path: Path, persisted: list[str], field: dict[str, object]
) -> None:
    assert _run(tmp_path, _document(persisted, [field])) == (
        2,
        "",
        "replay-cover-rules: input carries an undeclared input\n",
    )


SECRET = "Private Title /library/book"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"schema": "other", "persisted": [], "fields": []}, "input shape is invalid"),
        (_document(["covers"], []), "input shape is invalid"),
        (
            _document(
                [], [{"medium": "ebook", "current": None, "candidates": [], SECRET: 1}]
            ),
            "input shape is invalid",
        ),
        (
            _document(
                [],
                [
                    {
                        "medium": "ebook",
                        "current": None,
                        "candidates": [_candidate(identity=SECRET)],
                    }
                ],
            ),
            "input values are invalid",
        ),
        (
            _document(
                [],
                [
                    {
                        "medium": "ebook",
                        "current": None,
                        "candidates": [_candidate(), _candidate(width=0)],
                    }
                ],
            ),
            "input values are invalid",
        ),
    ],
    ids=["schema", "persisted", "field_key", "identity", "width"],
)
def test_a_malformed_input_is_refused_without_echoing_it(
    tmp_path: Path, document: object, message: str
) -> None:
    code, stdout, stderr = _run(tmp_path, document)

    assert (code, stdout) == (2, "")
    assert stderr == f"replay-cover-rules: {message}\n"


def test_an_unreadable_input_is_refused(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = replay_cover_rules.main(
        [str(tmp_path / "absent.json")], stdout=io.StringIO(), stderr=stderr
    )

    assert (code, stderr.getvalue()) == (2, "replay-cover-rules: input unreadable\n")
