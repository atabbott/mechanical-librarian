"""Interpret the supported ABS cover declarations without selecting a URL."""

from __future__ import annotations

from typing import Any

from mechanical_librarian.domain.metadata_report import TargetFieldState


def observed_cover_path(
    root: dict[str, Any],
) -> tuple[TargetFieldState, str | None, dict[str, Any]]:
    """Keep all declared locations in the fingerprint and require typed agreement."""
    media = root.get("media")
    media = media if type(media) is dict else {}
    metadata = media.get("metadata")
    metadata = metadata if type(metadata) is dict else {}
    observed = {"present": "coverPath" in root, "value": root.get("coverPath")}
    values = [root["coverPath"]] if "coverPath" in root else []
    for name, location in (("media", media), ("metadata", metadata)):
        if "coverPath" in location:
            observed[name] = {"present": True, "value": location["coverPath"]}
            values.append(location["coverPath"])
    if not values:
        return TargetFieldState.MISSING, None, observed
    value = values[0]
    if any(type(other) is not type(value) or other != value for other in values[1:]):
        return TargetFieldState.MALFORMED, None, observed
    if value is None:
        return TargetFieldState.NULL, None, observed
    if type(value) is not str:
        return TargetFieldState.MALFORMED, None, observed
    if value == "":
        return TargetFieldState.EMPTY, None, observed
    if not value.startswith("/") or len(value) > 1024:
        return TargetFieldState.MALFORMED, None, observed
    return TargetFieldState.VALUE, value, observed
