# Hardcover title proposal report

`hardcover-title-proposal-report` is the narrow, live-compatible F09b proof-of-
concept command. It preserves the F09a synthetic preview and substitutes a
bounded Hardcover lookup for the deterministic provider fake in one generated
`agree-empty` case. It accepts no title, scenario, configuration, endpoint,
query, or transport-mode input.

## Run the preview

Provider-disabled operation is the safe default:

```console
mechanical-librarian hardcover-title-proposal-report \
  --database /data/mechanical-librarian.sqlite \
  --format json
```

This records the typed `provider_disabled` result without constructing a
provider adapter or client and without reading a secret, resolving DNS, or
making a request.

To exercise the bounded provider seam, pass an absolute secret-file reference:

```console
mechanical-librarian hardcover-title-proposal-report \
  --database /data/mechanical-librarian.sqlite \
  --secret-reference /run/secrets/hardcover-token \
  --format text
```

`--format` may be `json` (the default) or `text`; both expose the same bounded,
secret-free projection. A secret file must be an absolute, owner-only regular
file, not a symlink, contain 1 through 2,048 bytes, and contain only the raw
printable non-whitespace ASCII token. Do not include `Bearer`; the Hardcover-
specific adapter constructs that scheme for the fixed request. A Docker secret
is usable only when its mount meets those rules. The path and token never
appear in output or durable state.

## Outcomes and exits

The generated EPUB, F06 evidence, current synthetic snapshot, and expected
corroborating title are fixed by the command.

| Condition | Persisted result | Exit |
| --- | --- | --- |
| `--secret-reference` omitted | `provider_disabled` | `0` |
| Secret reference syntax is malformed | no state access or publication | `2` |
| Reference is valid but its file is missing, insecure, unreadable, or invalid | `provider_failed` | `0` |
| Provider authentication, rate limit, timeout, transport, GraphQL, shape, or parser failure | `provider_failed` | `0` |
| HTTP `404` | `provider_no_match` | `0` |
| Successful response with no result | `provider_empty` | `0` |
| One usable agreeing title | one fill-empty `needs_review` proposal | `0` |
| State, migration, persistence, serialization, or unexpected failure | no partial publication | `1` |

Exit `0` emits exactly one canonical report on stdout with empty stderr. Exit
`2` emits a fixed syntax diagnostic on stderr before state, secret, DNS, or
network access. Exit `1` emits a fixed runtime diagnostic on stderr with empty
stdout. Provider failure is an evidence outcome, not a process failure: valid
local F06 evidence remains bound and visible.

Each canonical provider observation has a deterministic, non-secret lineage in
the `hardcover-poc-*` namespace. Identical observations replay without
duplication. Disabled, failed, empty, no-match, and agreeing outcomes use
distinct lineages so they coexist without collision or supersession.

Every set remains `mode=synthetic_preview` with
`production_applicable=false`. The command adds no live target or configured
media binding, AI or human decision, approval, apply, provider cache, generic
provider framework, or write authority.

## Compatibility status

Ordinary automated qualification uses a credential-free, structurally faithful
fake through the same command and adapter seam. F09b completion also requires
the exact installed wheel and console command to pass one bounded read-only run
against the fixed endpoint with a dedicated non-production account, fixed
generated title, and disposable database. A valid parsed differing title
produces `local_provider_conflict`; a count-consistent empty result produces
`provider_empty`. A parser or compatibility failure does not qualify. Retain
only the redacted aggregate result and no disposable state or provider traffic.
See the [frozen F09b contract](../reference/hardcover-f09b-contract.md).

This qualification does not add provider configuration, durable provider work,
freshness or reuse, retry/restart behavior, live-target binding, or production
applicability. F09f owns the later target-independent configured provider work,
title plus strict ISBN-backed book/edition identity, retry/restart, freshness,
and reuse. F09g owns the later live target/source join and
proposal-applicability decision.
