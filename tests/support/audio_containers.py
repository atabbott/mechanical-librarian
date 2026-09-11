"""Generate the exact audio containers the audiobook tests probe.

Every fixture is written here in code rather than committed as bytes: the suite
distributes no binary media, a reviewer can read what each container asserts,
and a case that needs one more tag adds it as an argument instead of as a new
opaque file.

The MP4 writer emits the smallest tree ffprobe accepts as a real audiobook --
one AAC track described by a valid ``esds``, Nero ``chpl`` chapters, and an
``ilst`` carrying both well-known and freeform atoms.  No sample is decodable
and none needs to be: nothing under test decodes audio.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

_AAC_AUDIO_SPECIFIC_CONFIG = bytes((0x12, 0x10))
_MATRIX = struct.pack(">9i", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
_MP3_FRAME_HEADER = bytes((0xFF, 0xFB, 0x90, 0x00))
_MP3_FRAME_BYTES = 417
_MP3_FRAME_SECONDS = 1152 / 44_100


def _box(name: str, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", 8 + len(body)) + name.encode("latin-1") + body


def _full_box(name: str, version: int, flags: int, *payload: bytes) -> bytes:
    return _box(
        name, struct.pack(">B", version) + struct.pack(">I", flags)[1:], *payload
    )


def _descriptor(tag: int, payload: bytes) -> bytes:
    if len(payload) > 0x7F:
        raise ValueError("test descriptor payload is too long")
    return struct.pack(">BB", tag, len(payload)) + payload


# The MPEG-4 object type an `esds` names for AAC audio, and the one the codec
# case states instead.  ffprobe reads the byte and names the codec from it
# without decoding a sample, so an `.m4b` whose stream is AC-3 is a fact its
# headers already carry.
AAC_OBJECT_TYPE = 0x40
AC3_OBJECT_TYPE = 0xA5
# The ISO marker for "this header states no duration".  Writing zero does not
# work: ffprobe computes a running time from the sample tables instead and
# states that, so the only container that truthfully states none is one whose
# headers say the value is unknown.
_UNKNOWN_DURATION = 0xFFFFFFFF


def _esds(object_type: int = AAC_OBJECT_TYPE) -> bytes:
    specific = _descriptor(0x05, _AAC_AUDIO_SPECIFIC_CONFIG)
    config = _descriptor(
        0x04,
        struct.pack(">BB", object_type, 0x15) + b"\x00" * 3 + b"\x00" * 8 + specific,
    )
    element = _descriptor(
        0x03,
        struct.pack(">HB", 1, 0) + config + _descriptor(0x06, b"\x02"),
    )
    return _full_box("esds", 0, 0, element)


def _sample_entry(name: str = "mp4a", object_type: int = AAC_OBJECT_TYPE) -> bytes:
    """One audio sample entry, under the four-character code it declares.

    The name is a parameter because it is the entry's whole meaning to a
    reader: `mp4a` is an ordinary stream and `drms` is one Apple protected,
    and the protection is stated in the four-CC itself rather than anywhere a
    decoder would have to look.
    """
    return _box(
        name,
        b"\x00" * 6,
        struct.pack(">H", 1),
        b"\x00" * 8,
        struct.pack(">HHHH", 2, 16, 0, 0),
        struct.pack(">I", 44_100 << 16),
        _esds(object_type),
    )


def _stbl(samples: int, offset: int, entry: bytes) -> bytes:
    return _box(
        "stbl",
        _full_box("stsd", 0, 0, struct.pack(">I", 1), entry),
        _full_box("stts", 0, 0, struct.pack(">III", 1, samples, 1024)),
        _full_box("stsc", 0, 0, struct.pack(">IIII", 1, 1, samples, 1)),
        _full_box("stsz", 0, 0, struct.pack(">II", 8, samples)),
        _full_box("stco", 0, 0, struct.pack(">II", 1, offset)),
    )


def _minf(samples: int, offset: int, entry: bytes) -> bytes:
    return _box(
        "minf",
        _full_box("smhd", 0, 0, struct.pack(">HH", 0, 0)),
        _box(
            "dinf",
            _full_box("dref", 0, 0, struct.pack(">I", 1), _full_box("url ", 0, 1)),
        ),
        _stbl(samples, offset, entry),
    )


def _mdia(
    samples: int, offset: int, language: str, entry: bytes, duration: int
) -> bytes:
    packed = 0
    for character in language[:3].ljust(3, "u"):
        packed = (packed << 5) | ((ord(character) - 0x60) & 0x1F)
    return _box(
        "mdia",
        _full_box(
            "mdhd",
            0,
            0,
            struct.pack(">IIII", 0, 0, 44_100, duration),
            struct.pack(">HH", packed, 0),
        ),
        _full_box(
            "hdlr",
            0,
            0,
            struct.pack(">I", 0),
            b"soun",
            b"\x00" * 12,
            b"\x00",
        ),
        _minf(samples, offset, entry),
    )


def _trak(
    samples: int,
    offset: int,
    milliseconds: int,
    language: str,
    entry: bytes,
    stated: bool,
) -> bytes:
    return _box(
        "trak",
        _full_box(
            "tkhd",
            0,
            7,
            struct.pack(
                ">IIIII",
                0,
                0,
                1,
                0,
                milliseconds if stated else _UNKNOWN_DURATION,
            ),
            b"\x00" * 8,
            struct.pack(">hhhh", 0, 0, 0x0100, 0),
            _MATRIX,
            struct.pack(">II", 0, 0),
        ),
        _mdia(
            samples,
            offset,
            language,
            entry,
            samples * 1024 if stated else _UNKNOWN_DURATION,
        ),
    )


def _data(value: str) -> bytes:
    return _box("data", struct.pack(">II", 1, 0), value.encode("utf-8"))


def _ilst(well_known: dict[str, str], freeform: dict[tuple[str, str], str]) -> bytes:
    entries = [_box(name, _data(value)) for name, value in sorted(well_known.items())]
    entries.extend(
        _box(
            "----",
            _full_box("mean", 0, 0, namespace.encode("utf-8")),
            _full_box("name", 0, 0, name.encode("utf-8")),
            _data(value),
        )
        for (namespace, name), value in sorted(freeform.items())
    )
    return _box("ilst", *entries)


def _chpl(chapters: tuple[tuple[int, str], ...]) -> bytes:
    payload = [struct.pack(">IB", 0, len(chapters))]
    for milliseconds, title in chapters:
        encoded = title.encode("utf-8")[:255]
        payload.append(
            struct.pack(">QB", milliseconds * 10_000, len(encoded)) + encoded
        )
    return _full_box("chpl", 1, 0, *payload)


def _cover_trak(milliseconds: int, offset: int) -> bytes:
    """One attached-picture track, which is how MP4 carries embedded art."""
    entry = _box(
        "jpeg",
        b"\x00" * 6,
        struct.pack(">H", 1),
        b"\x00" * 16,
        struct.pack(">HHI", 32, 32, 0x00480000),
        struct.pack(">I", 0x00480000),
        struct.pack(">IHH", 0, 1, 0),
        b"\x00" * 32,
        struct.pack(">hh", 24, -1),
    )
    return _box(
        "trak",
        _full_box(
            "tkhd",
            0,
            7,
            struct.pack(">IIIII", 0, 0, 2, 0, milliseconds),
            b"\x00" * 8,
            struct.pack(">hhhh", 0, 0, 0, 0),
            _MATRIX,
            struct.pack(">II", 32 << 16, 32 << 16),
        ),
        _box(
            "mdia",
            _full_box(
                "mdhd",
                0,
                0,
                struct.pack(">IIII", 0, 0, 1_000, milliseconds),
                struct.pack(">HH", 0x55C4, 0),
            ),
            _full_box(
                "hdlr", 0, 0, struct.pack(">I", 0), b"vide", b"\x00" * 12, b"\x00"
            ),
            _box(
                "minf",
                _full_box("vmhd", 0, 1, struct.pack(">HHHH", 0, 0, 0, 0)),
                _box(
                    "dinf",
                    _full_box(
                        "dref", 0, 0, struct.pack(">I", 1), _full_box("url ", 0, 1)
                    ),
                ),
                _box(
                    "stbl",
                    _full_box("stsd", 0, 0, struct.pack(">I", 1), entry),
                    _full_box("stts", 0, 0, struct.pack(">III", 1, 1, milliseconds)),
                    _full_box("stsc", 0, 0, struct.pack(">IIII", 1, 1, 1, 1)),
                    _full_box("stsz", 0, 0, struct.pack(">II", 8, 1)),
                    _full_box("stco", 0, 0, struct.pack(">II", 1, offset)),
                ),
            ),
        ),
    )


@dataclass(frozen=True)
class Mp4Fixture:
    """One generated MP4 audiobook and the values it was built to assert."""

    milliseconds: int = 4_000
    language: str = "eng"
    well_known: dict[str, str] = field(default_factory=dict)
    freeform: dict[tuple[str, str], str] = field(default_factory=dict)
    chapters: tuple[tuple[int, str], ...] = ()
    embedded_cover: bool = False
    # The four-character code of the audio sample entry.  `drms` is Apple's
    # protected entry, which the probe surfaces verbatim as the stream's codec
    # tag: it is the one encryption marker readable from headers alone.
    sample_entry: str = "mp4a"
    # The MPEG-4 object type the `esds` names, which is what ffprobe reads the
    # codec from.  Not AAC means a codec this build did not expect.
    object_type: int = AAC_OBJECT_TYPE
    # Whether the movie, track and media headers state a running time at all.
    # `False` writes the ISO unknown-duration marker into all three, which is
    # how a real container states that it does not know.
    duration_stated: bool = True


_DEFAULT_MP4_FIXTURE = Mp4Fixture()


def mp4_audiobook(fixture: Mp4Fixture = _DEFAULT_MP4_FIXTURE) -> bytes:
    """Build one single-file MP4 audiobook from an explicit fixture."""
    samples = max(1, fixture.milliseconds * 44_100 // (1_000 * 1024))
    payload = b"\x00" * (8 * (samples + 1))
    metadata = _box(
        "udta",
        _full_box(
            "meta",
            0,
            0,
            _full_box(
                "hdlr", 0, 0, struct.pack(">I", 0), b"mdir", b"appl", b"\x00" * 9
            ),
            _ilst(fixture.well_known, fixture.freeform),
        ),
        *((_chpl(fixture.chapters),) if fixture.chapters else ()),
    )
    # Two passes: the sample-offset tables have to name the real ``mdat``
    # position, which is only known once ``moov`` has its final length.
    offset = 0
    for _ in range(2):
        entry = _sample_entry(fixture.sample_entry, fixture.object_type)
        tracks = [
            _trak(
                samples,
                offset,
                fixture.milliseconds,
                fixture.language,
                entry,
                fixture.duration_stated,
            )
        ]
        if fixture.embedded_cover:
            tracks.append(_cover_trak(fixture.milliseconds, offset))
        moov = _box(
            "moov",
            _full_box(
                "mvhd",
                0,
                0,
                struct.pack(
                    ">IIII",
                    0,
                    0,
                    1_000,
                    fixture.milliseconds
                    if fixture.duration_stated
                    else _UNKNOWN_DURATION,
                ),
                struct.pack(">IH", 0x00010000, 0x0100),
                b"\x00" * 10,
                _MATRIX,
                b"\x00" * 24,
                struct.pack(">I", 3),
            ),
            *tracks,
            metadata,
        )
        header = _box("ftyp", b"M4B ", struct.pack(">I", 0), b"M4B isomiso2")
        offset = len(header) + len(moov) + 8
    return header + moov + _box("mdat", payload)


def _id3_frame(identifier: str, payload: bytes) -> bytes:
    return (
        identifier.encode("latin-1")
        + struct.pack(">I", len(payload))
        + b"\x00\x00"
        + payload
    )


def _synchsafe(value: int) -> bytes:
    return bytes(((value >> shift) & 0x7F) for shift in (21, 14, 7, 0))


def mp3_audiobook(
    *,
    seconds: float = 4.0,
    frames: dict[str, str] | None = None,
    user_text: dict[str, str] | None = None,
) -> bytes:
    """Build one MP3 carrying ID3v2.3 text frames and ``TXXX`` user frames."""
    payload = b"".join(
        _id3_frame(identifier, b"\x03" + value.encode("utf-8") + b"\x00")
        for identifier, value in sorted((frames or {}).items())
    )
    payload += b"".join(
        _id3_frame(
            "TXXX",
            b"\x03"
            + description.encode("utf-8")
            + b"\x00"
            + value.encode("utf-8")
            + b"\x00",
        )
        for description, value in sorted((user_text or {}).items())
    )
    tag = b"ID3\x03\x00\x00" + _synchsafe(len(payload)) + payload
    count = max(1, round(seconds / _MP3_FRAME_SECONDS))
    frame = _MP3_FRAME_HEADER + b"\x00" * (_MP3_FRAME_BYTES - len(_MP3_FRAME_HEADER))
    return tag + frame * count


def multi_track_mp3(
    count: int,
    *,
    seconds: float = 1.0,
    frames: dict[str, str] | None = None,
    user_text: dict[str, str] | None = None,
    per_track_user_text: dict[int, dict[str, str]] | None = None,
) -> tuple[tuple[str, bytes], ...]:
    """Build one item's ordered MP3 tracks, named ``01.mp3`` upward.

    ``per_track_user_text`` replaces one ordinal's ``TXXX`` frames outright,
    which is how a case states that one track disagrees with the rest.
    """
    overrides = per_track_user_text or {}
    return tuple(
        (
            f"{ordinal:02d}.mp3",
            mp3_audiobook(
                seconds=seconds,
                frames={**(frames or {}), "TIT2": f"Part {ordinal}"},
                user_text=overrides.get(ordinal, user_text or {}),
            ),
        )
        for ordinal in range(1, count + 1)
    )


def hostile_container() -> bytes:
    """One structurally plausible container ffprobe must refuse to interpret."""
    return _box("ftyp", b"M4B ", struct.pack(">I", 0), b"M4B ") + _box(
        "moov", b"\x00" * 64
    )


__all__ = [
    "AAC_OBJECT_TYPE",
    "AC3_OBJECT_TYPE",
    "Mp4Fixture",
    "hostile_container",
    "mp3_audiobook",
    "mp4_audiobook",
    "multi_track_mp3",
]
