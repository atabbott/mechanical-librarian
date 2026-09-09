# Configuration reference

The installed `review-serve` runtime and the shared application composition
accept one strict UTF-8 JSON document with schema version `config-v9`. The
source-only contributor workbench may load the same schema only inside its
disposable or explicitly approved test context. Unknown and duplicate fields
are rejected. The generated schema is
[`config-v9.schema.json`](config-v9.schema.json) and is regenerated with:

```text
uv run --frozen python scripts/generate_configuration_schema.py
```

The file remains the complete authority while `/data/config.json` exists.
The accepted store-mode, first-boot, secret, authority, and apply rules are
[ADR-0058](../architecture/decisions/0058-settings-authority-and-first-boot.md);
this reference describes the file-managed `config-v9` contract. In that mode it
also solely supplies the review TLS identity, canonical origin, and trusted
proxy IPs; the store/setup bootstrap document is neither required nor read.

When `/data/config.json` is absent, `review-serve` instead requires one
exact-current owner-settings document and owner verifier in
`/data/application.sqlite3`, plus an owner-only
`/run/secrets/review-bootstrap.json`. A truly empty `/data` volume enters the
guided setup flow; an exact bootstrap-bound incomplete setup store resumes it.
Every other missing, malformed, mixed, or orphaned store/bootstrap state stops
startup without fallback, import, migration, or repair. The bootstrap is a
closed `review-bootstrap-v2` document containing only
`canonical_origin`, optional paired TLS certificate and key paths, up to 16
`trusted_proxy_ips`, up to 32 read-only mount mappings, and the optional
contained-reviewer profile path. It contains no owner credential: the first
visitor creates the owner password in the ordinary application. Ports,
volume/bind mounts, owner-password
recovery, and proxy/TLS deployment remain outside the Settings API.

This build has no exception to ordinary exact-current startup. Its new owner
and reviewer state requires a clean rebuild, and the previous offline adoption
command is retired rather than retargeted.

Authenticated owners use the lazy `/settings` route and revision-CAS
`GET`/`PATCH /api/v1/settings` API. Reads are private and return only a short
secret indicator such as `Configured · ····1a2b`; secret values may only be
set, replaced, or explicitly cleared. A present configuration file makes every
covered write return `409 settings_managed_by_file` naming only
`/data/config.json`; neither the API nor UI consults or exposes store values in
that mode. All store-managed fields apply to the next admitted operation through
one revision-CAS runtime-generation activation. Candidate preparation failure
leaves the saved revision and active runtime unchanged. The private
`pending_restart` member remains compatibility-shaped but is invariant-empty
after activation; deploy-managed TLS, origin, proxy, mount, and listener values
still require an operator restart.

`provider_recovery_generation` is a required positive operator generation for
provider recovery. After correcting a provider credential, authorization, or
configuration condition, increment it and save Settings. It is the
only recovery wake mechanism: the service does not watch configuration files,
derive hashes, or accept environment overrides.

## Example

```json
{
  "schema_version": "config-v9",
  "provider_recovery_generation": 1,
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
      "provider_connections": ["inventaire-main", "google-books-main", "hardcover-main", "open-library-main"]
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
      "name": "inventaire-main",
      "adapter": "inventaire",
      "enabled": true,
      "cover_enabled": true,
      "secret_file": null,
      "contact_email": null
    },
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
    "session_lifetime_seconds": 2592000,
    "library_polling_interval_seconds": 900,
    "graceful_shutdown_seconds": 30,
    "diagnostic_timings_enabled": false,
    "cover_reviewer_profile_file": null,
    "cover_avoid_promotional_text": false,
    "cover_avoid_tie_in_branding": false,
    "cover_prefer_minimal_composition": false,
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
visible-text transformation evidence. Valid ISBN-10 and ISBN-13 forms of one
canonical ISBN family are compared as one proposal while retaining every exact
source representation and evidence reference; that comparison never treats the
forms as manifestation identity or converts an identifier. Identifier
preferences select only an already admitted exact manifestation value.
Series retains every qualified membership in the product result while the
Audiobookshelf projection selects at most its first membership. The qualified
automatic options are all three series modes, genre `fill_empty`, `add`, and
`replace`, tag `fill_empty`, `add`, and `replace`, description
`safe_plain_text`, and both ISBN modes. They may create decision-only automatic
authority when the destination is `automatic`; they never create write
authority. Genre `fill_empty` is now Automatic in the built-in policy, while
tags remain `off`/Ask. Every other rich-field option remains
manual-preview-only.

Automatic series election treats provider-wide unsupported series output with no
named membership as no evidence. A named incomplete or unpositioned membership,
an unresolved alternative, or a failure in a query kind that contributed to the
elected membership remains an inspectable abstention; the failure reason names
that query kind. Closed leading-article and trailing `Series`/`Trilogy` display
variants elect together, without fuzzy matching or a title-specific catalogue.

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

An ebook library requires a named read-only mount. An audiobook library may
give a named read-only mount too: with one, each of its items holding 1 to 512
audio files — counted after the files the service itself excludes are dropped —
is prepared as one local-evidence report, composed across those files, and
enters provider discovery from there. An audiobook library that keeps
`mount: null`, and any audiobook item carrying a file outside the supported
containers, is rejected before parser/provider/publication activity exactly as
before. A mounted item over 512 audio files is listed for attention instead, and
is never prepared. Reading that local evidence requires the `ffprobe`
binary the shipped image builds and carries; a deployment without it records no
audiobook evidence.

Every provider has explicit enablement and one of exactly eight built-in adapter
values. Four read ebook metadata:

- `inventaire` is keyless and forbids both credential fields. It is preselected
  during first-run setup, uses a fixed Inventaire host, and may retrieve covers.

- `google_books` forbids `contact_email` and cover retrieval. When enabled,
  `secret_file` is optional: `null` uses credential-free access and an absolute
  path supplies a Google Books API key through the controller only.
- `hardcover` forbids `contact_email`; when enabled it requires an absolute
  private `secret_file`.
- `open_library` always forbids a secret; when enabled it requires a bounded
  operational `contact_email`.

Four read audiobook metadata, and every one of them is keyless under
[ADR-0061](../architecture/decisions/0061-audiobook-provider-posture.md):
`secret_file` and `contact_email` are always `null`, and no key exists for them
even as an optional upgrade. What each one reaches on the network is stated in
[the security and threat model](../architecture/security.md).

- `audible_catalog` is primary discovery and enrichment, and may retrieve
  covers. When enabled it also carries `marketplaces`: the ordered, non-empty,
  duplicate-free ASIN resolution set drawn from `us`, `uk`, `de`, `fr`, `au`,
  `in`, `ca`, `jp`, `it`, and `es`, defaulting to all ten with `us` first. No
  other adapter, and not the disabled form of this one, accepts the key.
- `audnexus` is per-ASIN enrichment, including chapters.
- `audiosilo` is community corroboration and degraded mode.
- `itunes_search` corroborates only, and may retrieve covers.

`cover_enabled` defaults to `true` on an enabled `inventaire`,
`audible_catalog`, or `itunes_search`; `audnexus` and `audiosilo` restate
Audible's cover and must leave it `false`.

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
- `source_archive_bytes` (1,024–268,435,456; default 268,435,456) — the EPUB
  archive ceiling, and only that
- `audio_source_bytes` (1,024–17,179,869,184; default 8,589,934,592) — the
  audiobook ceiling, separate because an audiobook is read by header rather
  than whole and is routinely larger than any EPUB. It bounds each audio file
  of an item, checked on the opened length before any file is digested, which
  bounds a multi-file item at that ceiling times its file count
- `timeout_seconds` (1–60; default 30)

Optional `review` configuration is required by `review-serve` and ignored by
report-only commands. The server also requires explicit `--catalogue` and
`--artifacts` paths; neither is inferred from configuration or an environment
variable. `canonical_origin` is one exact HTTP or HTTPS browser origin. The owner
hash file is an absolute, owner-only regular no-follow file containing one
Argon2id PHC string. `tls_certificate_file` and `tls_private_key_file` are an
all-or-nothing pair of absolute, owner-controlled regular no-follow files; the
private key must be owner-only and use an unencrypted PKCS#8, RSA, or EC PEM
envelope. Encrypted and non-PEM key envelopes are rejected before SSL parsing,
including encryption with an empty passphrase. There is no passphrase input or
environment source. The server snapshots and validates one matching TLS
identity before it opens the
application database and retains only immutable anonymous snapshot descriptors
for the server's lifetime in direct HTTPS mode. HTTP requires both TLS paths
absent. HTTPS requires either both paths or at least one exact trusted proxy
IP with both paths absent (a plaintext backend). The scheme is authoritative;
there is no transport override or verification bypass.
Session lifetime is 300–2,592,000 seconds (five minutes to 30 days). It defaults
to 30 days and is a fixed absolute owner-session limit: authenticated activity
does not extend it. The global
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

`cover_reviewer_profile_file` is optional deployment authority. When absent,
Codex is unavailable but the model-free path and owner-managed OpenRouter
choice remain available. When present, it must be an absolute owner-only
regular no-follow file containing the already-qualified contained-reviewer
profile; its exact bytes bind every Codex observation. The browser never owns
this path. The supported container profile binds the image-supplied executable
paths:
`/opt/mechanical-librarian/reviewer/bin/codex` and
`/opt/mechanical-librarian/reviewer/bin/bwrap`. Their architecture-specific
digests are recorded inside the image at
`/opt/mechanical-librarian/reviewer/metadata/SHA256SUMS`. Browser and
application-owned settings cannot substitute another executable; deployment
authority remains responsible for the profile itself. Application state
independently owns the exact task-to-engine/model
mapping and credential presence. During ordinary review preparation, the one
selected usable reviewer performs one bounded advisory comparison before the
review package becomes visible. An
absent, unready, unavailable, refusing, malformed, or failed reviewer records
the same safe manual-comparison state instead; it never selects, downloads,
queues, applies, or writes a cover. The three `cover_*` booleans form the
closed deterministic advisory policy. They never grant decision or write
authority, and changing them invalidates pending cover effects until the owner
considers and selects under the current policy.

When the fixed contained-reviewer resource contract changes, explicitly
regenerate and requalify this private canonical profile through the provisioning
procedure that created it, then replace the configured file and restart. The
application never rewrites, migrates, or version-upgrades a profile. Codex
credentials live only in its contained private auth store. OpenRouter keys are
write-only application secrets and are never returned by setup, Settings,
exports, diagnostics, or public documentation.

There are no provider defaults, inheritance, overrides, weights, priorities,
configurable fallback rules, environment substitutions, or undocumented
configuration sources. The fixed complete title-plus-author discovery lane is
part of the report contract rather than operator policy and runs alongside
bounded ISBN-family lanes. Proposal creation uses
only the selected target connection/library/mount and never reads provider
authority; its fixed evaluation policy is not configurable.


## Local transport

An Audiobookshelf `endpoint` explicitly selects `http://` or `https://`, including
service names and LAN addresses. HTTPS always verifies the certificate and
hostname. Credentials stay confined to the configured origin and supported
routes; redirects and ambient proxies remain disabled. Provider and reviewer
egress retain their separate HTTPS requirements.

Browser mode is selected by the canonical origin and TLS identity:

| Origin | TLS paths | Listener |
| --- | --- | --- |
| HTTP | Both absent | HTTP |
| HTTPS | Both present | Direct TLS |
| HTTPS | Both absent, exact trusted proxy IPs present | HTTP behind the configured TLS edge |

Proxy mode requires exactly one `X-Forwarded-Host` and one `X-Forwarded-Proto`
header from the actual trusted peer. The pair must equal the canonical origin.
Duplicate Host or forwarding fields, partial pairs, chains, `Forwarded`, and
untrusted forwarding fail closed. Uvicorn does not interpret proxy headers.
Sessions remain HttpOnly and SameSite Strict. Secure cookies and HSTS follow
the canonical browser scheme, including setup and failure responses.

`config-v9` and `review-bootstrap-v2` require explicit document replacement:
clean rebuild required for these configuration documents. This transport change
does not change database or `owner-settings-v3` schemas, adopt old documents,
or rewrite fingerprints. See [deployment](../operations/deployment.md) for
network exposure and browser trust.
