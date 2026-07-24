# Configuration reference

The installed `metadata-report`, `catalogue`, and `batch` operations accept one
strict UTF-8 JSON document with schema version `config-v3`. Unknown and
duplicate fields are rejected. The generated schema is
[`config-v3.schema.json`](config-v3.schema.json) and is regenerated with:

```text
uv run --frozen python scripts/generate_configuration_schema.py
```

## Example

```json
{
  "schema_version": "config-v3",
  "connections": [
    {
      "name": "primary",
      "adapter": "audiobookshelf",
      "endpoint": "https://library.example.invalid",
      "secret_file": "/run/secrets/audiobookshelf-token"
    }
  ],
  "libraries": [
    {
      "name": "ebooks",
      "connection": "primary",
      "library_id": "01234567-89ab-4def-8123-456789abcdef",
      "mount": "books",
      "provider_connections": ["hardcover-main"]
    }
  ],
  "mounts": [
    {
      "name": "books",
      "container_path": "/media/books",
      "service_path": "/library/books",
      "read_only": true
    }
  ],
  "providers": [
    {
      "name": "hardcover-main",
      "adapter": "hardcover",
      "enabled": false,
      "cover_enabled": false,
      "secret_file": null
    }
  ]
}
```

Names are bounded lowercase identifiers. Connection, library, mount, and
provider names are unique. Each library refers to exactly one target connection
and lists its provider connections explicitly; duplicate references are
invalid. List order has no priority meaning and is canonicalized by name.

An ebook library requires a named read-only mount. Audiobook libraries may
coexist and use `mount: null`, but `metadata-report` rejects their items before
parser/provider/publication activity.

Every provider has explicit enablement. A disabled provider must use
`secret_file: null`. An enabled provider requires an absolute private secret
file. Adding or enabling a provider does nothing until a library explicitly
references it. The same named provider connection and credential may be
referenced by several libraries.

Target and enabled-provider `secret_file` values are bounded absolute paths.
Relative secret paths are rejected by both the generated schema and runtime
configuration loader.

Optional `limits` fields are fixed bounded resource limits, not behavior or
ranking policy:

- `target_response_bytes` (1,024–4,194,304; default 1,048,576)
- `provider_response_bytes` (1,024–262,144; default 65,536)
- `source_archive_bytes` (1,024–268,435,456; default 268,435,456)
- `timeout_seconds` (1–60; default 30)

There are no provider defaults, inheritance, overrides, weights, priorities,
fallbacks, rules, environment substitutions, or undocumented configuration
sources.
