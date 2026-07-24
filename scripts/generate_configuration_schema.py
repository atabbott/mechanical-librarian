"""Regenerate the deterministic config-v3 JSON Schema reference."""

from pathlib import Path

from mechanical_librarian.infrastructure.configuration import configuration_schema_json


ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "docs" / "reference" / "config-v3.schema.json"


def main() -> None:
    OUTPUT.write_text(configuration_schema_json(), encoding="utf-8")


if __name__ == "__main__":
    main()
