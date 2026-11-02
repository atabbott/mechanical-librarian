# Configuration reference

Settings come from one of two places, never both:

- **File mode.** When `/data/config.json` exists, it is the complete authority.
  It is one strict UTF-8 JSON document with schema version `config-v9`,
  described below.
- **Store mode.** When that file is absent, settings live in
  `/data/application.sqlite3` and the owner edits them on the Settings page.
  The deployment supplies a small bootstrap document instead.

The rules for choosing between them, first boot and secrets are in
[ADR-0058](../architecture/decisions/0058-settings-authority-and-first-boot.md).
How to deploy either mode is in [deployment](../operations/deployment.md).
Which document versions are accepted, and when a clean rebuild is needed, is
in [compatibility](compatibility.md).

## Store mode

`review-serve` needs the owner-only bootstrap document
`/run/secrets/review-bootstrap.json` and one exact-current owner-settings
document and owner verifier in `/data/application.sqlite3`. An empty `/data`
volume starts guided setup instead, and an incomplete setup store bound to the
same bootstrap resumes it. Any other missing, malformed, mixed or orphaned
state stops startup without fallback, import, migration or repair.

Creating the owner password needs the one-time setup claim token, which is
runtime state, not configuration
([ADR-0086](../architecture/decisions/0086-deployment-held-setup-claim-token.md)).

The bootstrap is a closed `review-bootstrap-v2` document
([example](../examples/review-bootstrap.json)). It holds only:

| Field | Rule |
| --- | --- |
| `schema_version` | `review-bootstrap-v2` |
| `canonical_origin` | As [`review.canonical_origin`](#review) |
| `tls_certificate_file`, `tls_private_key_file` | Optional pair, as in [`review`](#review) |
| `trusted_proxy_ips` | Up to 16, as in [`review`](#review) |
| `mounts` | Up to 32 read-only mount mappings, as in [`mounts`](#mounts) |
| `cover_reviewer_profile_file` | Optional; defaults to the image profile, see [contained reviewer profile](#contained-reviewer-profile) |

It holds no owner credential.

Owners edit settings on `/settings` (`GET` and `PATCH /api/v1/settings`). A
stale revision is refused. A secret can be set, replaced or cleared, never read
back; reads show only an indicator such as `Configured · ····1a2b`. A saved
change applies to the next operation. If the new runtime cannot be prepared,
the saved revision and the running runtime both stay as they were.

Ports, volumes, owner-password recovery, TLS, the canonical origin, trusted
proxies and mounts are deployment settings. Settings does not change them;
changing them needs a restart.

## File mode

While `/data/config.json` exists, every covered Settings write returns
`409 settings_managed_by_file`, and the browser shows those settings
read-only. The file alone supplies the TLS identity, canonical origin and
trusted proxy IPs; no bootstrap is read. The file is read once at start:
restart after an edit.

The document:

- must be a regular file of at most 262,144 bytes, read without following
  symlinks;
- rejects unknown fields, duplicate keys and the JSON constants `NaN` and
  `Infinity`;
- is described by the generated
  [`config-v9.schema.json`](config-v9.schema.json).

Regenerate the schema after changing the model:

```text
uv run --frozen python scripts/generate_configuration_schema.py
```

Names (of connections, libraries, mounts and providers) match
`^[a-z][a-z0-9_-]{0,63}$` and are unique within their list. List order carries
no priority; lists are sorted by name when loaded.

Secret and file paths are absolute (start with `/`) and at most 1,024
characters. Relative paths are refused by both the schema and the loader.

There are no provider defaults, inheritance, weights, priorities, configurable
fallback rules, environment overrides or other configuration sources.
Discovery lanes and proposal evaluation are fixed, not configurable. Proposal
creation reads only the selected connection, library and mount, never provider
settings.

## Example

A minimal valid file is [`synthetic-config.json`](../examples/synthetic-config.json).
The fuller example below also enables the ebook providers and the `review`
section:

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
    "tls_private_key_file": "/run/secrets/review-tls.key"
  }
}
```

## Top-level fields

| Field | Required | Rule |
| --- | --- | --- |
| `schema_version` | Yes | Exactly `config-v9` |
| `provider_recovery_generation` | Yes | Integer, at least 1 |
| `connections` | Yes | 1–32 Audiobookshelf connections |
| `libraries` | Yes | 1–128 libraries |
| `mounts` | Yes | 0–32 read-only mounts |
| `providers` | Yes | 0–32 metadata providers |
| `limits` | No | Resource limits; every field has a default |
| `review` | No | Required by `review-serve`; ignored by report-only commands |

### Provider recovery generation

`provider_recovery_generation` is the only way to wake provider recovery.
After fixing a provider credential, authorisation or configuration problem:

- in store mode, press **Retry now** on Library or Settings, which raises the
  generation by exactly one (Settings shows no number to edit);
- in file mode, raise it by one in the file and restart.

The service does not watch files, derive hashes or accept environment
overrides for it. See [provider recovery](../operations/provider-recovery.md).

## connections

Each entry is one Audiobookshelf server.

| Field | Rule |
| --- | --- |
| `name` | Name |
| `adapter` | Exactly `audiobookshelf` |
| `endpoint` | 1–2,048 characters, with an explicit `http://` or `https://` scheme |
| `secret_file` | Absolute path to the API token |

`endpoint` may be a service name or LAN address. HTTPS always verifies the
certificate and host name. The token is sent only to the configured origin
and supported routes. Redirects and ambient proxies are disabled. Provider and
reviewer traffic keep their own HTTPS requirement.

## mounts

Each entry maps a path Audiobookshelf reports to the path the container sees.

| Field | Rule |
| --- | --- |
| `name` | Name |
| `container_path` | Absolute path inside this container |
| `service_path` | Absolute path as Audiobookshelf reports it |
| `read_only` | Exactly `true` |

## libraries

| Field | Required | Rule |
| --- | --- | --- |
| `name` | Yes | Name |
| `connection` | Yes | Name of one entry in `connections` |
| `library_id` | Yes | Audiobookshelf library ID, 1–128 characters |
| `mount` | Yes | Name of one entry in `mounts`, or `null` |
| `provider_connections` | Yes | Up to 32 provider names, no duplicates |
| `languages` | No | Up to 8 language tags; default `[]` |
| `policy_override` | No | Sparse policy changes; default `null` |

A library lists its providers explicitly. Adding or enabling a provider does
nothing until a library references it. Several libraries may reference the
same provider.

### Mounts and media

An ebook needs the library to name a read-only mount.

An audiobook is prepared only when its library names a mount too, and only
with 1 to 512 audio files; an item with more is listed for attention. Reading
audio evidence needs `ffprobe`, which the shipped image includes. The full
admission rules are in [metadata report](../operations/metadata-report.md).

### languages

An ordered list of the languages the library holds, the first preferred, for
example `["de", "en"]`. Each entry is a lowercase ISO 639 tag with an optional
script and region, such as `en`, `deu` or `zh-Hant`. No two entries may name the
same language and script. `und`, `mul`, `mis` and `zxx` are refused.

A store-managed library declares its languages on Settings, under Libraries;
there the picker offers ISO 639 languages without a script or region. It is a
matching prior, never written into an item, and only audiobook matching reads
it
([ADR-0064](../architecture/decisions/0064-audiobook-provider-evidence-vocabularies.md)):

- Audible marketplaces are reordered by language, first language first. None
  is added or removed. English keeps the default order.
- A recording that states no language must be in one of the listed
  languages: an edition in any other language is not matched as its
  recording. A language the recording states itself always wins.

Changing the list prepares the library's items again.

### policy_override

The built-in metadata policy needs no configuration. A library may declare a
sparse, read-only override with only the `options` and `destinations` it
changes, each a map keyed by policy category.

| Category | Options (default first) |
| --- | --- |
| `title_layout` | `title_and_subtitle`, `preserve` |
| `date_precision` | `year`, `full_date` |
| `language_rendering` | `display_name` |
| `contributor_names` | `canonical`, `preserve` |
| `publisher_preference` | `publisher`, `imprint` |
| `subtitle` | `fill_empty`, `corroborated_split`, `replace_descriptive` |
| `series` | `primary_narrative`, `source_order`, `all_qualified`, `add_qualified`, `replace_qualified` |
| `genres` | `fill_empty`, `add`, `replace` |
| `tags` | `off`, `fill_empty`, `add`, `replace` |
| `description_fill` | `safe_plain_text`, `google_visible_text`, `replace_safe_plain_text` |
| `isbn_form` | `preserve_exact`, `prefer_isbn13` |
| `cover_order` | `fixed_source_order`, `replace_if_strictly_better` |
| `asin` | `preserve_exact`, `ebook_manifestation` |
| `explicit` | `unsupported`, `adult_product` |

A destination is one of `ask`, `ignore`, `automatic` or `unsupported`. The
built-in destination of `explicit` is `unsupported`.

- An unknown category, option or destination fails loading.
- `automatic` is accepted only for qualified category and option pairs, and
  grants decision-only authority after the owner confirms an exact Preview.
- Activation stores each library's resolved override with the new generation.
  Later edits to the file need another activation.

What each option does, and which are qualified for Automatic, is in
[automation policy](../product/automation-policy.md). Two contracts matter when
choosing an option:

- `add` and `add_qualified` keep the current values in their order and append
  supported values that are absent.
- `corroborated_split` acts only when the current title is exactly an admitted
  title and subtitle joined by `: `, ` — `, ` – ` or ` - `. It never splits on
  other punctuation.

To see the effect of a policy without changing anything:

```text
mechanical-librarian policy-preview --config CONFIG --database DATABASE --profile PROFILE
```

It reads an existing database and prints the same preview as the API,
changing nothing. Its output contains private metadata values; protect it like
the database.

## providers

Every provider has a `name`, an `adapter`, an explicit `enabled`, a
`cover_enabled`, a `secret_file` and, where allowed, a `contact_email`.

A disabled provider must set `cover_enabled` to `false` and both
`secret_file` and `contact_email` to `null`.

### Ebook providers

| Adapter | `secret_file` when enabled | `contact_email` when enabled | `cover_enabled` |
| --- | --- | --- | --- |
| `inventaire` | `null` | `null` | Optional, default `true` |
| `google_books` | `null` or absolute path to an API key | `null` | Must be `false` |
| `hardcover` | Absolute path, required | `null` | Required |
| `open_library` | `null` | Required email address, 3–254 characters | Required |

- `inventaire` is keyless and preselected in guided setup.
- `google_books` with `secret_file: null` uses credential-free access.
- A Hardcover secret file holds the bare token, without a `Bearer` scheme.
- `contact_email` only identifies Open Library HTTP requests. It never
  appears in reports, logs or stored artifacts, and changing it does not
  supersede reports.

### Audiobook-catalogue providers

All four are keyless
([ADR-0061](../architecture/decisions/0061-audiobook-provider-posture.md)):
`secret_file` and `contact_email` are always `null`, and no optional key
exists. What each one contacts is in
[security](../architecture/security.md).

| Adapter | Role | `cover_enabled` |
| --- | --- | --- |
| `audible_catalog` | Primary discovery and enrichment | Optional, default `true` |
| `audnexus` | Per-ASIN enrichment, including chapters | Must be `false` |
| `audiosilo` | Community corroboration and degraded mode | Must be `false` |
| `itunes_search` | Searches audiobook listings to corroborate ebooks; receives no audiobook requests | Optional, default `true` |

An enabled `audible_catalog` also takes `marketplaces`: the ordered,
non-empty, duplicate-free list of Audible marketplaces an ASIN is resolved
in, drawn from `us`, `uk`, `de`, `fr`, `au`, `in`, `ca`, `jp`, `it` and `es`.
It defaults to all ten in that order. No other adapter, and no disabled
Audible provider, accepts it.

### Dispatch

An ebook is sent only to referenced providers in the ebook set:
`google_books`, `hardcover`, `inventaire`, `itunes_search` and
`open_library`. Audible, Audnexus and AudioSilo receive no ebook requests.

An audiobook is sent only to referenced providers in the audiobook set:
`audible_catalog`, `audnexus`, `audiosilo`, `hardcover` and `open_library`.
A library's references narrow that set and never widen it. Google Books,
Inventaire and iTunes receive no audiobook requests.

### Covers

`cover_enabled` makes a provider a cover source. A report admits at most four
provider cover sources per named connection and eight overall; extra sources
are recorded as truncated.

Settings shows a **Cover evidence** switch only for `audible_catalog` (its art
becomes an audiobook cover source) and `itunes_search` (a matching audiobook
listing's art is offered as an ebook cover alternative). The switch is hidden
and off while the provider is disabled. In a configuration file it defaults to
on; guided setup saves `cover_enabled: false` for both, so in store mode their
covers start off.

The screens show one fixed display name per adapter, never the instance `name`.

## limits

Fixed resource limits, not behaviour or ranking policy.

| Field | Range | Default | Meaning |
| --- | --- | --- | --- |
| `target_response_bytes` | 1,024–4,194,304 | 1,048,576 | One Audiobookshelf response |
| `provider_response_bytes` | 1,024–262,144 | 65,536 | One provider response |
| `source_archive_bytes` | 1,024–268,435,456 | 268,435,456 | One EPUB archive |
| `audio_source_bytes` | 1,024–17,179,869,184 | 8,589,934,592 | Each audio file of an item |
| `timeout_seconds` | 1–60 | 30 | One request |

- An Audible audiobook search with a known runtime reads its single
  25-result page under a fixed 262,144-byte limit. Audible's aggregate limit
  is a fixed 1 MiB.
- `audio_source_bytes` applies to each file, so a multi-file item may total
  this limit times its file count.
- During preparation each provider gets a 20-second share of the item's
  60-second window, so `timeout_seconds` above 20 binds provider requests
  only in recovery. Audiobookshelf requests use the full value.

## review

`review-serve` requires this section and explicit `--catalogue` and
`--artifacts` paths. Neither path comes from configuration or the
environment.

| Field | Range | Default |
| --- | --- | --- |
| `canonical_origin` | 8–512 characters | Required |
| `owner_password_hash_file` | Absolute path | Required |
| `tls_certificate_file` | Absolute path or `null` | `null` |
| `tls_private_key_file` | Absolute path or `null` | `null` |
| `trusted_proxy_ips` | 0–16 IP addresses | `[]` |
| `session_lifetime_seconds` | 300–2,592,000 | 2,592,000 (30 days) |
| `library_polling_interval_seconds` | 60–86,400 | 900 |
| `graceful_shutdown_seconds` | 1–300 | 30 |
| `diagnostic_timings_enabled` | Boolean | `false` |
| `cover_reviewer_profile_file` | Absolute path or `null` | `null` |
| `cover_avoid_promotional_text` | Boolean | `false` |
| `cover_avoid_tie_in_branding` | Boolean | `false` |
| `cover_prefer_minimal_composition` | Boolean | `false` |
| `description_remove_marketing` | Boolean | `true` |
| `description_remove_sales_boasts` | Boolean | `true` |
| `description_remove_praise` | Boolean | `true` |
| `description_automatic_application` | Boolean | `false` |

No password, private-key contents, raw session token or CSRF token is stored
in configuration or the application database.

### Origin, TLS and proxies

`canonical_origin` is one exact `http://` or `https://` browser origin. Its
scheme is authoritative; there is no transport override.

| Origin | TLS paths | Listener |
| --- | --- | --- |
| HTTP | Both `null` | HTTP |
| HTTPS | Both set | Direct TLS |
| HTTPS | Both `null`, at least one trusted proxy IP | HTTP behind the trusted TLS proxy |

- The TLS certificate and key are a pair of owner-controlled regular files,
  opened without following symlinks and checked before the database opens.
  The key must be owner-only and an unencrypted PKCS#8, RSA or EC PEM;
  encrypted keys are refused.
- `owner_password_hash_file` is an owner-only regular file, opened without
  following symlinks, holding one Argon2id PHC string.

Forwarded headers are honoured only when the direct peer is one of
`trusted_proxy_ips`. That peer must send exactly one `X-Forwarded-Host` and one
`X-Forwarded-Proto`, and together they must equal the canonical origin.
Duplicate or partial headers, chains, `Forwarded`, and forwarding headers
from any other peer are refused. What the proxy must do with `X-Forwarded-For`
for per-client sign-in throttling is in
[deployment](../operations/deployment.md) and
[security](../architecture/security.md).

Session cookies are HttpOnly and SameSite Strict. The `Secure` flag and HSTS
follow the canonical scheme, including on setup and error responses.

### Sessions, polling and shutdown

- `session_lifetime_seconds` is an absolute limit on an owner session.
  Activity does not extend it.
- `library_polling_interval_seconds` sets only how often configured libraries
  are reconciled. Concurrency, provider pacing and retry bounds are not
  configurable.
- On shutdown the server stops admitting work, then waits up to
  `graceful_shutdown_seconds` for review preparation and field-effect
  workers. A worker still running at the deadline causes a typed shutdown
  failure before the repository closes. Its last durable checkpoint is
  recovered on restart.

### Diagnostic timings

When `diagnostic_timings_enabled` is `true`, the container keeps the newest
256 stage-duration and wait-class records in its private data area. They hold
no work values and are advisory, not an audit record. The stages are listed in
[observability](../operations/observability.md#diagnostic-timings).

### Contained reviewer profile

`cover_reviewer_profile_file` names the contained Codex reviewer profile. It
is set by the deployment, never in the browser.

- Store mode and setup use the image profile
  `/opt/mechanical-librarian/reviewer/profile.json` when no path is given.
- In file mode, `null` leaves Codex unprovisioned. Model-free review and
  OpenRouter remain available.
- An explicit path is used as given and never falls back when missing or
  invalid.

The profile must be an owner-only regular file in canonical form, opened
without following symlinks, whose paths are absolute paths that contain no NUL
character. Its fixed contract is:

| Setting | Value |
| --- | --- |
| Model | `gpt-5.6-sol` |
| Reasoning | `medium` |
| Timeout | 180 seconds |
| Output | 65,536 bytes |
| File | 1,048,576 bytes |
| Memory | 1 GiB |
| Processes | 32 |

It binds the image's checksummed `bin/codex` and `bin/bwrap` in that directory.
Codex credentials and jobs live in `/data/reviewer/auth` and
`/data/reviewer/jobs`.

Start-up checks the profile without running anything or using the network.
A missing, unreadable or mismatched profile reports its remedy: restore the
image profile, correct the path, or regenerate it, then restart. The
application never repairs a profile.

### Reviewer access

Which reviewer serves each task is application state (`reviewer-access-v3`),
not configuration. Cover review and description advice each have an optional
OpenRouter model and share one write-only key that is never returned.

A model is accepted only if it supports structured outputs, returns text and
takes the task's input: images for cover review, text for description advice.
A description model also needs prompt and completion prices. Pricing and the
cost estimate are described in
[ADR-0062](../architecture/decisions/0062-owner-managed-reviewer-access.md) and
[reviewer cost measurement](../development/reviewer-cost-measurement.md).

### Cover advice

The three `cover_*` booleans are the advisory cover policy. During
preparation the selected reviewer makes one bounded comparison before the case
is shown; if none is ready or it fails, the case shows the manual comparison.
Advice never selects or writes a cover. Changing these booleans invalidates
pending cover effects until the owner chooses again.

### Description advice

`description_remove_marketing`, `description_remove_sales_boasts` and
`description_remove_praise` remove marketing material, sales figures and
bestseller boasts, and pull quotes and author praise. In store mode these and
`automatic_application` live in `owner-settings-v4.description_advice` without
the `description_` prefix, and Policy → Curation saves them separately from the
policy draft.

A request carries 1–256 provider-backed alternatives of at most 16,384
characters each, within the 1 MiB request limit. A larger request is refused,
not truncated. What is and is not sent is in
[AI boundary](../architecture/ai-boundary.md). Every result is recorded as
`authored_content` and `advised`.

With `description_automatic_application` off, ordinary description fill
still applies. With it on, a current `ready` result that passes the
description floor is admitted through the normal policy and write rules, and
may replace a populated description. The floor is valid plain text with no
markup or visible cut-off, not a placeholder and not mostly quoted praise. The
owner can still choose a result below it.

- An owner's final choice wins, and in-flight effects are not raced.
- Identical text records no change and sends no PATCH or Attention item.
- Free replacement text is not accepted. Keep current reverses a choice.
- A request made while preparation is paused waits until it resumes. Work
  accepted before the pause completes normally.
