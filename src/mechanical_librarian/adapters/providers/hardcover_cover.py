"""Closed Hardcover cover descriptor query and parser (no fetching)."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass

_QUERY = "query CoverEvidenceByEdition($ids:[Int!]!){editions(where:{id:{_in:$ids}},order_by:{id:asc},limit:6){id image{url}}}"

@dataclass(frozen=True, slots=True)
class HardcoverCoverDescriptor:
    source_id: str
    edition_id: int
    url: str

def query(ids: tuple[int, ...]) -> bytes:
    if not ids or len(ids) > 5 or any(type(value) is not int or value < 1 for value in ids): raise ValueError("cover candidates invalid")
    return json.dumps({"operationName":"CoverEvidenceByEdition","query":_QUERY,"variables":{"ids":list(ids)}}, separators=(",",":"), sort_keys=True).encode()

def parse(payload: bytes) -> tuple[HardcoverCoverDescriptor, ...]:
    try:
        root = json.loads(payload.decode("utf-8")); rows = root["data"]["editions"]
        if type(root) is not dict or set(root) != {"data"} or type(root["data"]) is not dict or set(root["data"]) != {"editions"} or type(rows) is not list or len(rows) > 5: raise ValueError
        result = []
        for row in rows:
            if type(row) is not dict or set(row) != {"id", "image"} or type(row["id"]) is not int or row["id"] < 1: raise ValueError
            image = row["image"]
            if image is None: continue
            if type(image) is not dict or set(image) != {"url"} or type(image["url"]) is not str or not image["url"] or len(image["url"]) > 2048: raise ValueError
            result.append(HardcoverCoverDescriptor("hardcover_" + hashlib.sha256(f"{row['id']}:{image['url']}".encode()).hexdigest(), row["id"], image["url"]))
        if len({x.source_id for x in result}) != len(result): raise ValueError
        return tuple(result)
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error: raise ValueError("cover response malformed") from error

__all__ = ["HardcoverCoverDescriptor", "parse", "query"]
