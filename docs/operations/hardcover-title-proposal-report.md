# Hardcover title proposal report

`hardcover-title-proposal-report` is the historical F09b proof-of-concept
command. Its credential-free, provider-disabled form remains available for the
retained F09b preview, but its credentialed Hardcover path is retired. Current
configured, durable, read-only Hardcover operation belongs exclusively to
[`hardcover-identity-report`](hardcover-identity-report.md).

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

`--format` may be `json` (the default) or `text`; both expose the same bounded,
secret-free projection. Supplying `--secret-reference`, including an
abbreviated spelling, is rejected before the historical runner is entered and
directs the operator to `hardcover-identity-report`. Do not use this command
for credentialed qualification or provider access.

## Outcomes and exits

The generated EPUB, F06 evidence, current synthetic snapshot, and expected
corroborating title are fixed by the command.

| Condition | Persisted result | Exit |
| --- | --- | --- |
| `--secret-reference` omitted | `provider_disabled` | `0` |
| `--secret-reference` supplied | rejected before runner; use `hardcover-identity-report` | `2` |
| State, migration, persistence, serialization, or unexpected failure | no partial publication | `1` |

Exit `0` emits exactly one canonical report on stdout with empty stderr. Exit
`2` emits a fixed syntax diagnostic on stderr before state, secret, DNS, or
network access. Exit `1` emits a fixed runtime diagnostic on stderr with empty
stdout. In the historical credentialed F09b contract, provider failure was an
evidence outcome rather than a process failure and valid local F06 evidence
remained bound and visible; that behavior is retained history, not current
credentialed authority.

Historical canonical provider observations have deterministic, non-secret
lineages in the `hardcover-poc-*` namespace. Identical observations replay
without duplication. Disabled, failed, empty, no-match, and agreeing outcomes
use distinct lineages so retained history coexists without collision or
supersession.

Every set remains `mode=synthetic_preview` with
`production_applicable=false`. The command adds no live target or configured
media binding, AI or human decision, approval, apply, provider cache, generic
provider framework, or write authority.

## Compatibility status

The retained F09b evidence used a credential-free, structurally faithful fake
through the same historical command and adapter seam. Its completed live proof
is historical evidence, not present authority to repeat a credentialed F09b
request. See the [frozen F09b contract](../reference/hardcover-f09b-contract.md)
for that historical boundary and use the current F09f operator procedure for
any separately authorized one-shot qualification.

F09b does not add provider configuration, durable provider work, freshness or
reuse, retry/restart behavior, live-target binding, or production
applicability. F09f owns the current target-independent configured provider
work, title plus strict ISBN-backed book/edition identity, retry/restart,
freshness, and reuse. F09g owns the later live target/source join and
proposal-applicability decision.
