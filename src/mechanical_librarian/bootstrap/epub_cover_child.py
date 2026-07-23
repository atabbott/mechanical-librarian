"""One-shot bounded extraction of an EPUB's declared cover member."""
from __future__ import annotations
import base64, json, os, resource, sys, xml.etree.ElementTree as ET, zipfile

_MAX = 8 * 1024 * 1024

def _limit() -> None:
    for key, maximum in ((resource.RLIMIT_AS, 256 * 1024 * 1024), (resource.RLIMIT_CPU, 30), (resource.RLIMIT_NOFILE, 64)):
        soft, hard = resource.getrlimit(key); accepted = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
        try: resource.setrlimit(key, (accepted, accepted))
        except (OSError, ValueError):
            if sys.platform.startswith("linux"): raise

def _member(archive: zipfile.ZipFile) -> tuple[str, str] | None:
    container = ET.fromstring(archive.read("META-INF/container.xml"))
    root = next((node.attrib.get("full-path") for node in container.iter() if node.tag.endswith("rootfile")), None)
    if not root or root not in archive.namelist(): return None
    package = ET.fromstring(archive.read(root)); directory = root.rsplit("/", 1)[0] + "/" if "/" in root else ""
    manifest = {node.attrib.get("id"): node for node in package.iter() if node.tag.endswith("item")}
    target = next((node for node in manifest.values() if "cover-image" in node.attrib.get("properties", "").split()), None)
    if target is None:
        legacy = next((node.attrib.get("content") for node in package.iter() if node.tag.endswith("meta") and node.attrib.get("name") == "cover"), None)
        target = manifest.get(legacy)
    if target is None: return None
    href, media = target.attrib.get("href"), target.attrib.get("media-type")
    if not href or not media or media not in {"image/jpeg", "image/png", "image/webp"}: raise ValueError
    name = directory + href
    if name.startswith("/") or ".." in name.split("/") or name not in archive.namelist(): raise ValueError
    return name, media

def main() -> int:
    try:
        _limit(); descriptor = int(sys.argv[1]); source = os.fdopen(os.dup(descriptor), "rb")
        with source, zipfile.ZipFile(source) as archive:
            member = _member(archive)
            if member is None: result = {"state":"absent"}
            else:
                name, media = member
                with archive.open(name) as entry: data = entry.read(_MAX + 1)
                if len(data) > _MAX: result = {"state":"oversized"}
                else: result = {"state":"declared", "media_type":media, "data":base64.b64encode(data).decode("ascii"), "identity":name}
        sys.stdout.write(json.dumps(result, separators=(",", ":"))); return 0
    except Exception: return 1
if __name__ == "__main__": raise SystemExit(main())
