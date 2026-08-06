# Configuration reference

The installed `metadata-report`, `proposal-set-create`, `catalogue`, `batch`,
and `review-serve`
operations accept one strict UTF-8 JSON document with schema version
`config-v5`. Unknown and duplicate fields are rejected. The generated schema is
[`config-v5.schema.json`](config-v5.schema.json) and is regenerated with:

```text
uv run --frozen python scripts/generate_configuration_schema.py
```

## Example

```json
{
  "schema_version": "config-v5",
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
      "provider_connections": ["google-books-main", "hardcover-main", "open-library-main"]
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
      "name": "google-books-main",
      "adapter": "google_books",
      "enabled": true,
      "cover_enabled": false,
      "secret_file": null
    },
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
  ],
  "review": {
    "canonical_origin": "https://review.example.invalid",
    "owner_password_hash_file": "/run/secrets/review-owner.argon2",
    "session_lifetime_seconds": 3600,
    "trusted_proxy_ips": []
  }
}
```

Names are bounded lowercase identifiers. Connection, library, mount, and
provider names are unique. Each library refers to exactly one target connection
and lists its provider connections explicitly; duplicate references are
invalid. List order has no priority meaning and is canonicalized by name.

An ebook library requires a named read-only mount. Audiobook libraries may
coexist and use `mount: null`, but `metadata-report` rejects their items before
parser/provider/publication activity.

Every provider has explicit enablement and one of exactly three built-in adapter
values:

- `google_books` forbids `contact_email` and cover retrieval. When enabled,
  `secret_file` is optional: `null` uses credential-free access and an absolute
  path supplies a Google Books API key through the controller only.
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

Each library may reference several enabled metadata providers and more than one
may set `cover_enabled: true`. The report admits no more than four provider
cover sources per named connection and eight provider sources overall; excess
descriptors produce explicit truncation evidence.

Target, enabled-Hardcover, and keyed Google Books `secret_file` values are
bounded absolute paths.
Relative secret paths are rejected by both the generated schema and runtime
configuration loader.
An enabled Hardcover file contains the bare token value, without an
authorization scheme. The controller adds the provider-required `Bearer`
scheme only to an exact POST to the fixed Hardcover GraphQL URL.

Optional `limits` fields are fixed bounded resource limits, not behavior or
ranking policy:

- `target_response_bytes` (1,024–4,194,304; default 1,048,576)
- `provider_response_bytes` (1,024–262,144; default 65,536)
- `source_archive_bytes` (1,024–268,435,456; default 268,435,456)
- `timeout_seconds` (1–60; default 30)

Optional `review` configuration is required by `review-serve` and ignored by
report-only commands. `canonical_origin` is one exact HTTPS origin. The owner
hash file is an absolute, owner-only regular no-follow file containing one
Argon2id PHC string. Session lifetime is 300–86,400 seconds. Proxy forwarding
is disabled unless the direct peer is one of at most 16 explicitly listed
`trusted_proxy_ips`; forwarded Host or scheme headers from any other peer fail
closed. No password, raw session token, or CSRF token is stored in
configuration or the application database.

There are no provider defaults, inheritance, overrides, weights, priorities,
configurable fallback rules, environment substitutions, or undocumented
configuration sources. The fixed complete title-plus-author discovery lane is
part of the report contract rather than operator policy and runs alongside
bounded ISBN-family lanes. Proposal creation uses
only the selected target connection/library/mount and never reads provider
authority; its fixed evaluation policy is not configurable.
