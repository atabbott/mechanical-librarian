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
