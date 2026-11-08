"""The linear OPF scanner only ever narrows main's patterns (#1481, T22).

Main's two backtracking patterns are copied here as the oracle, exactly as
they stood before #1481.  Both readers run the same fact derivation; only the
scanner differs.  Over a seeded corpus of small synthetic documents -- well
formed and malformed, single-quoted, reversed, nested and unclosed -- the
scanner's facts and association text must be a subset of the oracle's, and
equal to them on well-formed input.  A widening fails the test.
"""

from __future__ import annotations

import dataclasses
import random
import re

import pytest

from mechanical_librarian.adapters.evidence_extractors import audio_local
from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    AudioPathContext,
    AudioSidecar,
    interpret,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioEvidenceSurface,
    AudioInspectionLimits,
    AudioSidecarReading,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion

# Main's patterns, verbatim (audio_local.py at a00ada2).
_ORACLE_ELEMENT = re.compile(
    r"<(?:[A-Za-z][\w.-]*:)?(language|description|title|creator|contributor)\b"
    r"([^>]*)>([^<]{0,4096})</",
    re.IGNORECASE,
)
_ORACLE_META = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*\"([\w:.-]{1,64})\"[^>]*\bcontent\s*=\s*\"([^\"]{0,512})\"",
    re.IGNORECASE,
)
_PROBE = (
    '{"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "4.000"},'
    ' "streams": [{"codec_type": "audio", "codec_name": "aac"}], "chapters": []}'
)
_SOURCE = Fingerprint("sha256", SchemaVersion("source-v1"), "a" * 64)
_NAMES = ("language", "description", "title", "creator", "contributor", "publisher")
_VALUES = ("English", "Synthetic text", "Example Person", "fr", "A &amp; B", "")
_ROLES = ('opf:role="nrt"', "opf:role='nrt'", 'role="aut"', 'id="x"', "", 'role="edt"')
_META_NAMES = ("calibre:series", "calibre:series_index", "audible:asin", "other")
_META_VALUES = ("Synthetic Series", "2", "B0SYNTH001", "1.5", "")


def _read(
    text: str,
) -> tuple[frozenset[tuple[object, ...]], tuple[str | None, tuple[str, ...]]]:
    interpretation = interpret(
        _PROBE,
        (AudioSidecar(AudioEvidenceSurface.SIDECAR_OPF, "book.opf", text),),
        AudioPathContext("book.m4b"),
        _SOURCE,
        AudioInspectionLimits(),
    )
    if not interpretation.sidecars:
        return frozenset(), (None, ())
    [reading] = interpretation.sidecars
    return (
        frozenset(
            (fact.field, fact.observed_value, fact.canonical_value, fact.role)
            for fact in reading.facts
        ),
        (reading.title, reading.creators),
    )


def _oracle(text: str, monkeypatch: pytest.MonkeyPatch):
    with monkeypatch.context() as patched:
        patched.setattr(
            audio_local,
            "_xml_elements",
            lambda value: _ORACLE_ELEMENT.findall(value)[:64],
        )
        patched.setattr(
            audio_local, "_xml_metas", lambda value: _ORACLE_META.findall(value)[:64]
        )
        return _read(text)


def _element(rng: random.Random, *, well_formed: bool) -> str:
    name = rng.choice(_NAMES)
    prefix = rng.choice(("dc:", "", "DC:"))
    attributes = rng.choice(_ROLES)
    value = rng.choice(_VALUES)
    opening = f"<{prefix}{name}{' ' + attributes if attributes else ''}>"
    if well_formed:
        return f"{opening}{value}</{prefix}{name}>"
    return rng.choice(
        (
            f"{opening}{value}",
            f"{opening}{value}<{prefix}{name}>{value}</{prefix}{name}></{prefix}{name}>",
            f"<{prefix}{name} {attributes}{value}</{prefix}{name}>",
            f"{opening}{value}<b>x</b></{prefix}{name}>",
            f"<{prefix}{name}{'x' * rng.randint(0, 3)}>{value}</",
            f"{opening}{value} < {value}</{prefix}{name}>",
            f'<{prefix}{name} a=">">{value}</{prefix}{name}>',
        )
    )


def _meta(rng: random.Random, *, well_formed: bool) -> str:
    name = rng.choice(_META_NAMES)
    value = rng.choice(_META_VALUES)
    if well_formed:
        return f'<meta name="{name}" content="{value}"/>'
    return rng.choice(
        (
            f"<meta name='{name}' content='{value}'/>",
            f'<meta content="{value}" name="{name}"/>',
            f'<meta name="{name}" content="{value}"',
            f'<meta name="{name}" x="<" content="{value}"/>',
            f'<meta name="{name}" name="other" content="{value}" content="9"/>',
            f'<meta name="{name}"><meta content="{value}"/>',
            f'<META NAME="{name}" CONTENT="{value}">',
            f'<meta data-name="{name}" content="{value}"/>',
        )
    )


def _document(seed: int, *, well_formed: bool) -> str:
    rng = random.Random(seed)
    parts = [
        _element(rng, well_formed=well_formed)
        if rng.random() < 0.6
        else _meta(rng, well_formed=well_formed)
        for _ in range(rng.randint(1, 8))
    ]
    return "<package><metadata>" + rng.choice(("", "\n")).join(parts) + "</metadata>"


@pytest.mark.parametrize("seed", range(400))
def test_the_scanner_never_widens_main_on_any_input(
    seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _document(seed, well_formed=False)
    facts, (title, creators) = _read(text)
    oracle_facts, (oracle_title, oracle_creators) = _oracle(text, monkeypatch)

    assert facts <= oracle_facts, text
    assert title is None or title == oracle_title, text
    assert set(creators) <= set(oracle_creators), text


@pytest.mark.parametrize("seed", range(200))
def test_the_scanner_equals_main_on_well_formed_input(
    seed: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _document(seed, well_formed=True)
    assert _read(text) == _oracle(text, monkeypatch), text


def test_the_oracle_is_wider_than_the_scanner_somewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The corpus does reach the cases where the scanner reads less."""
    narrower = [
        seed
        for seed in range(400)
        if _read(text := _document(seed, well_formed=False))
        != _oracle(text, monkeypatch)
    ]
    assert narrower


def _reading(text: str) -> AudioSidecarReading | None:
    sidecars = interpret(
        _PROBE,
        (AudioSidecar(AudioEvidenceSurface.SIDECAR_OPF, "book.opf", text),),
        AudioPathContext("book.m4b"),
        _SOURCE,
        AudioInspectionLimits(),
    ).sidecars
    return sidecars[0] if sidecars else None


@pytest.mark.parametrize("seed", range(200))
@pytest.mark.parametrize("well_formed", (True, False), ids=("well_formed", "malformed"))
def test_a_byte_order_mark_never_changes_an_opf_reading(
    seed: int, well_formed: bool
) -> None:
    """T2 (#1796): both scanners start at a `<`, so a leading mark is never read.

    The child records the mark and keeps no second reading.
    """
    text = _document(seed, well_formed=well_formed)
    plain = _reading(text)
    marked = _reading("﻿" + text)

    assert plain is not None and marked is not None
    assert marked.byte_order_mark is True
    assert marked.stripped is None
    assert marked.stripped_limit is None
    assert dataclasses.replace(marked, byte_order_mark=False) == plain
