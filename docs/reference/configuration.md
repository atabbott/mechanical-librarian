# Configuration reference

The installed `review-serve` runtime and the shared application composition
accept one strict UTF-8 JSON document with schema version `config-v5`. The
source-only contributor workbench may load the same schema only inside its
disposable or explicitly approved test context. Unknown and duplicate fields
are rejected. The generated schema is
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
    "tls_certificate_file": "/run/secrets/review-tls.crt",
    "tls_private_key_file": "/run/secrets/review-tls.key",
    "session_lifetime_seconds": 3600,
    "library_polling_interval_seconds": 900,
    "graceful_shutdown_seconds": 30,
    "diagnostic_timings_enabled": false,
    "trusted_proxy_ips": []
  }
}
```

Names are bounded lowercase identifiers. Connection, library, mount, and
provider names are unique. Each library refers to exactly one target connection
and lists its provider connections explicitly; duplicate references are
invalid. List order has no priority meaning and is canonicalized by name.

## Sparse policy overrides

The built-in metadata policy needs no configuration. A library may instead
declare a read-only, sparse `policy_override` with only the named `options` and
`destinations` it differs on. Both maps use the closed policy catalogue; unknown
or removed fields fail configuration loading rather than silently becoming Ask
me or Ignore. The application never rewrites this configuration. `automatic`
is accepted only for the closed qualified category/option matrix and produces
decision-only authority after the owner explicitly confirms an exact Preview;
all other automatic intent is rejected. `explicit` is always `unsupported`.
The v2 rich-field choices are exact:

- `subtitle`: `fill_empty` (default) or `corroborated_split`;
- `series`: `primary_narrative` (default), `source_order`, or `all_qualified`;
- `genres`: `fill_empty` (default), `add`, or `replace`;
- `tags`: `off` (default), `fill_empty`, `add`, or `replace`;
- `description_fill`: `safe_plain_text` (default) or
  `google_visible_text`;
- `isbn_form`: `preserve_exact` (default) or `prefer_isbn13`;
- `asin`: `preserve_exact` (default) or `ebook_manifestation`;
- `explicit`: `unsupported` only.

List Add retains current target order and appends absent values from one exact
qualified source list; Replace uses one exact list and never combines
providers. Description normalization requires the retained Google Books
visible-text transformation evidence. Identifier preferences select only an
already admitted exact manifestation value and never convert or infer one.
Series retains every qualified membership in the product result while the
Audiobookshelf projection selects at most its first membership. The qualified
automatic options are all three series modes, genre `add` and `replace`, tag
`fill_empty`, `add`, and `replace`, description `safe_plain_text`, and both ISBN
modes. They may create decision-only automatic authority when the destination
is `automatic`; they never create write authority, and tags remain `off`/Ask in
the built-in policy. Every other rich-field option remains manual-preview-only.

Corroborated subtitle splitting is atomic and accepts only an already admitted
title/subtitle pair when the current title is their exact concatenation with
one of `: `, ` — `, ` – `, or ` - `. It never performs a general punctuation
or free-text split.

`mechanical-librarian policy-preview --config CONFIG --database DATABASE
--profile PROFILE` reads an existing exact-current database and prints the same
canonical preview returned by the API. It creates no database, draft,
activation, decision, or target effect. Its output includes private current and
desired metadata values and should be protected like the application database.

Activation resolves every configured library override against the exact
immutable draft and persists that canonical resolved document with the new
generation. Proposal evaluation replays it, so a later configuration edit
cannot silently reinterpret an already active generation; another explicit
activation establishes any new resolution.

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
report-only commands. The server also requires explicit `--catalogue` and
`--artifacts` paths; neither is inferred from configuration or an environment
variable. `canonical_origin` is one exact HTTPS origin. The owner
hash file is an absolute, owner-only regular no-follow file containing one
Argon2id PHC string. `tls_certificate_file` and `tls_private_key_file` are an
all-or-nothing pair of absolute, owner-controlled regular no-follow files; the
private key must be owner-only and use an unencrypted PKCS#8, RSA, or EC PEM
envelope. Encrypted and non-PEM key envelopes are rejected before SSL parsing,
including encryption with an empty passphrase. There is no passphrase input or
environment source. The server snapshots and validates one matching TLS
identity before it opens the
application database and retains only immutable anonymous snapshot descriptors
for the server's lifetime. `review-serve` has no plaintext or TLS-disable mode.
Session lifetime is 300–86,400 seconds. The global
`library_polling_interval_seconds` is 60–86,400 seconds and controls only when
configured libraries reconcile; concurrency, provider pacing, and retry bounds
remain maintainer-owned. Proxy forwarding is disabled unless the
direct peer is one of at most 16 explicitly listed `trusted_proxy_ips`;
forwarded Host or scheme headers from any other peer fail closed. No password,
private-key contents, raw session token, or CSRF token is stored in
configuration or the application database.

`graceful_shutdown_seconds` is 1–300 seconds. Shutdown closes claim admission
before waiting up to this deadline for active review-preparation and guarded
field-effect workers. An over-deadline worker remains tracked, causes a typed
shutdown failure before the shared repository closes, and its last durable
checkpoint is recovered on restart.

`diagnostic_timings_enabled` is false by default. When explicitly enabled, the
container retains at most 256 local stage-duration and wait-class records in
its private data area. They contain no work values and are advisory diagnostics,
not durable audit truth.

There are no provider defaults, inheritance, overrides, weights, priorities,
configurable fallback rules, environment substitutions, or undocumented
configuration sources. The fixed complete title-plus-author discovery lane is
part of the report contract rather than operator policy and runs alongside
bounded ISBN-family lanes. Proposal creation uses
only the selected target connection/library/mount and never reads provider
authority; its fixed evaluation policy is not configurable.
