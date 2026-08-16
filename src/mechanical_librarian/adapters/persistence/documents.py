"""The canonical document text every persisted aggregate is compared against.

`json.dumps` with `ensure_ascii=False` is deliberate and load-bearing: the
persisted bytes are the exact text a digest and an equality check are taken
over, so this is not interchangeable with the domain's `canonical_json`, which
escapes non-ASCII.
"""

from __future__ import annotations

import json


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = ["_canonical_json"]
