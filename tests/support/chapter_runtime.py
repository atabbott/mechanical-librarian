"""Synthetic generated media and its material recorded-probe projection.

The slow/probe consumer verifies the critical fields independently against
ffprobe; fast publication uses this projection through the real audio child.
"""

import json

from tests.support.audio_containers import Mp4Fixture, mp4_audiobook

ASIN = "B08G9PRS1K"
STARTS = (0, 113000, 284000, 527000, 844000, 1245000)
END = 1747998
SPANS = tuple(zip(STARTS, (*STARTS[1:], END), strict=True))


def media() -> bytes:
    return mp4_audiobook(
        Mp4Fixture(
            milliseconds=1748000,
            well_known={"CDEK": ASIN},
            chapters=tuple(
                (start, f"Chapter {i + 1}") for i, start in enumerate(STARTS)
            ),
        )
    )


def recorded_probe() -> bytes:
    return json.dumps(
        {
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": "1747.998",
                "tags": {"CDEK": ASIN},
            },
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "tags": {"language": "eng"},
                }
            ],
            "chapters": [
                {
                    "start_time": f"{start / 1000:.3f}",
                    "end_time": f"{end / 1000:.3f}",
                    "tags": {"title": f"Chapter {i + 1}"},
                }
                for i, (start, end) in enumerate(SPANS)
            ],
        }
    ).encode()


def provider_chapters() -> dict[str, object]:
    return {
        "chapters": [{"lengthMs": end - start} for start, end in SPANS],
        "runtimeLengthSec": END // 1000,
    }


QT_END = 1748000
QT_SPANS = tuple(zip(STARTS, (*STARTS[1:], QT_END), strict=True))


def qt_media(
    *, stated: bool = False, chapter_stated: bool = True, asin: str | None = None
) -> bytes:
    """Build a referenced QuickTime text chapter track, with independent duration.

    Unknown movie/audio headers do not alter the text track or its sample tables.
    Unlike Nero chapters, that text track can retain the last chapter endpoint.
    Samples are synthetic, just as in the existing MP4 header-only fixtures.
    """
    import struct

    from tests.support.audio_containers import (
        _MATRIX,
        _box,
        _full_box,
        _ilst,
        _sample_entry,
        _trak,
    )

    samples = QT_END * 44100 // (1000 * 1024)
    audio_payload = b"\x00" * (8 * (samples + 1))
    texts = tuple(f"Chapter {i + 1}".encode() for i in range(len(QT_SPANS)))
    chapter_samples = tuple(struct.pack(">H", len(text)) + text for text in texts)
    header = _box("ftyp", b"M4B ", struct.pack(">I", 0), b"M4B isomiso2")
    offset = 0
    for _ in range(2):
        audio_track = _trak(samples, offset, QT_END, "eng", _sample_entry(), stated)
        audio_track = _box(
            "trak", audio_track[8:], _box("tref", _box("chap", struct.pack(">I", 2)))
        )
        chapter_duration = QT_END if chapter_stated else 0xFFFFFFFF
        chapter_track = _box(
            "trak",
            _full_box(
                "tkhd",
                0,
                7,
                struct.pack(">IIIII", 0, 0, 2, 0, chapter_duration),
                b"\x00" * 8,
                struct.pack(">hhhh", 0, 0, 0, 0),
                _MATRIX,
                struct.pack(">II", 0, 0),
            ),
            _box(
                "mdia",
                _full_box(
                    "mdhd",
                    0,
                    0,
                    struct.pack(">IIIIHH", 0, 0, 1000, chapter_duration, 0x15C7, 0),
                ),
                _full_box(
                    "hdlr", 0, 0, struct.pack(">I", 0), b"text", b"\x00" * 12, b"\x00"
                ),
                _box(
                    "minf",
                    _full_box("nmhd", 0, 0),
                    _box(
                        "dinf",
                        _full_box(
                            "dref", 0, 0, struct.pack(">I", 1), _full_box("url ", 0, 1)
                        ),
                    ),
                    _box(
                        "stbl",
                        _full_box(
                            "stsd",
                            0,
                            0,
                            struct.pack(">I", 1),
                            _box(
                                "text", b"\x00" * 6, struct.pack(">H", 1), b"\x00" * 43
                            ),
                        ),
                        _full_box(
                            "stts",
                            0,
                            0,
                            struct.pack(">I", len(QT_SPANS)),
                            *(
                                struct.pack(">II", 1, end - start)
                                for start, end in QT_SPANS
                            ),
                        ),
                        _full_box(
                            "stsc", 0, 0, struct.pack(">IIII", 1, 1, len(QT_SPANS), 1)
                        ),
                        _full_box(
                            "stsz",
                            0,
                            0,
                            struct.pack(">II", 0, len(QT_SPANS)),
                            *(
                                struct.pack(">I", len(sample))
                                for sample in chapter_samples
                            ),
                        ),
                        _full_box(
                            "stco",
                            0,
                            0,
                            struct.pack(">II", 1, offset + len(audio_payload)),
                        ),
                    ),
                ),
            ),
        )
        moov = _box(
            "moov",
            _full_box(
                "mvhd",
                0,
                0,
                struct.pack(">IIII", 0, 0, 1000, QT_END if stated else 0xFFFFFFFF),
                struct.pack(">IH", 0x10000, 0x100),
                b"\x00" * 10,
                _MATRIX,
                b"\x00" * 24,
                struct.pack(">I", 3),
            ),
            audio_track,
            chapter_track,
            _box(
                "udta",
                _full_box(
                    "meta",
                    0,
                    0,
                    _full_box(
                        "hdlr",
                        0,
                        0,
                        struct.pack(">I", 0),
                        b"mdir",
                        b"appl",
                        b"\x00" * 9,
                    ),
                    _ilst({} if asin is None else {"CDEK": asin}, {}),
                ),
            ),
        )
        offset = len(header) + len(moov) + 8
    return header + moov + _box("mdat", audio_payload, *chapter_samples)


def qt_recorded_probe(*, asin: str | None = None) -> bytes:
    """Material projection independently checked by the actual probe consumer."""
    value = json.loads(recorded_probe())
    value["format"].pop("duration")
    value["format"]["tags"] = {} if asin is None else {"CDEK": asin}
    value["chapters"][-1]["end_time"] = "1748.000"
    return json.dumps(value).encode()


def qt_provider_chapters() -> dict[str, object]:
    return {
        "chapters": [{"lengthMs": end - start} for start, end in QT_SPANS],
        "runtimeLengthSec": QT_END // 1000,
    }
