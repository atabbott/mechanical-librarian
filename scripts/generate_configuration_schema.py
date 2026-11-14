"""Regenerate the deterministic JSON Schema reference for the current config."""

from pathlib import Path

from mechanical_librarian.infrastructure.configuration import configuration_schema_json

ROOT = Path(__file__).parents[1]
# A fixed name, so a configuration version move renames nothing; the version
# is inside the document.
OUTPUT = ROOT / "docs" / "reference" / "config.schema.json"


def main() -> None:
    OUTPUT.write_text(configuration_schema_json(), encoding="utf-8")


if __name__ == "__main__":
    main()
