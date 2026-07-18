"""Regenerate the public C1-only Hardcover privacy evidence."""

from pathlib import Path

from tests.support.hardcover_c1_privacy import (
    OUTPUT_SCHEMA_PATH,
    PRIVACY_CONTRACT_PATH,
    PRIVACY_SCAN_PATH,
    REGISTRY_PATH,
    build_registry,
    build_scan,
    canonical_bytes,
    output_schema_document,
    privacy_contract_document,
)


ROOT = Path(__file__).parents[1]


def main() -> None:
    (ROOT / OUTPUT_SCHEMA_PATH).write_bytes(canonical_bytes(output_schema_document()))
    (ROOT / PRIVACY_CONTRACT_PATH).write_bytes(
        canonical_bytes(privacy_contract_document())
    )
    scan = canonical_bytes(build_scan(ROOT, verify_registry=False))
    (ROOT / PRIVACY_SCAN_PATH).write_bytes(scan)
    (ROOT / REGISTRY_PATH).write_bytes(canonical_bytes(build_registry(ROOT)))
    if canonical_bytes(build_scan(ROOT)) != scan:
        raise RuntimeError("generated C1 privacy scan is not stable")


if __name__ == "__main__":
    main()
