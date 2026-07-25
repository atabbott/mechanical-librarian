# Configuration reference

The installed `metadata-report`, `catalogue`, and `batch` operations accept one
strict UTF-8 JSON document with schema version `config-v4`. Unknown and
duplicate fields are rejected. The generated schema is
[`config-v4.schema.json`](config-v4.schema.json) and is regenerated with:

```text
uv run --frozen python scripts/generate_configuration_schema.py
```

## Example

```json
{
  "schema_version": "config-v4",
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
      "provider_connections": ["hardcover-main", "open-library-main"]
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
    },
    {
      "name": "open-library-main",
      "adapter": "open_library",
      "enabled": true,
      "cover_enabled": true,
      "secret_file": null,
      "contact_email": "operator@example.invalid"
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

Every provider has explicit enablement and one of exactly two built-in adapter
values:

- `hardcover` forbids `contact_email`; when enabled it requires an absolute
  private `secret_file`.
- `open_library` always forbids a secret; when enabled it requires a bounded
  operational `contact_email`.

A disabled provider must set both adapter-specific authority fields to `null`.
Adding or enabling a provider does nothing until a library explicitly
references it. The same named provider connection may be referenced by several
libraries. `contact_email` is used only to identify Open Library HTTP requests;
it is excluded from reports, report IDs, manifests, fingerprints, logs, errors,
and persisted application artifacts. Changing it does not supersede reports or
invalidate an already frozen batch.

Each library may reference several enabled metadata providers, but at most one
of them may set `cover_enabled: true`. This preserves the existing closed cover
source bound while allowing independent Hardcover and Open Library metadata
observations in the same report.

Target and enabled-Hardcover `secret_file` values are bounded absolute paths.
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
