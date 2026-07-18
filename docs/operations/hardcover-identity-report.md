# Hardcover Identity Report

`hardcover-identity-report` is the installed, report-only F09f surface. In the
current preactivation increment it cannot contact Hardcover or open a private
query, database, or provider secret.

## Production syntax

```text
mechanical-librarian hardcover-identity-report \
  --config PATH --database PATH --query-file PATH [--format json|text]
```

An absent or explicitly disabled `hardcover_provider` block returns
`disabled`. A valid enabled block returns `provider_not_activated`. Both are
successful, versioned, proposal-free reports and both return before the query
file, database, secret, provider/client construction, DNS, proxy, socket, or
network access. Syntax or configuration failure emits only a fixed path-free
diagnostic. No live Hardcover qualification is supported by this increment.

The accepted configuration and redacted export are documented in the
[configuration contract](../reference/configuration.md). Configuration changes
require restart plus candidate and review re-evaluation.

## Private query contract

The future enabled path accepts an owner-only regular file of 1–4,096 bytes.
The reader canonicalizes and opens its parent directory, opens the final
component without following symlinks, requires effective-user ownership,
owner-read with no group/other bits, link count one, and stable device, inode,
mode, owner, link count, and size across one exact bounded read. It strict-
decodes UTF-8 once, rejects a BOM and duplicate/unknown keys, and accepts
exactly `isbn` and `title`.

ISBN-10 or ISBN-13 must be ASCII and checksum-valid; ISBN-10 is converted to
canonical ISBN-13. The local title is at most 512 Unicode scalars and 2,048
UTF-8 bytes and rejects controls, bidi controls, surrogates, noncharacters, and
line separators. Only canonical ISBN is later disclosed and local title
comparison remains transient. Preactivation never opens this file.

Normative artifacts are
[`hardcover-private-query-v1.schema.json`](../reference/hardcover-private-query-v1.schema.json)
and [`hardcover-private-query-v1.json`](../reference/hardcover-private-query-v1.json).

## Contributor synthetic preview

```text
mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario inert-report-v3 \
  --input - --format json
```

The command reads one bounded strict JSON document from stdin and exercises the
real provider-config and private-query validation seams. It emits canonical
schema-v5 JSON with only `outcome`, `private_file_opened`, `database_opened`,
`secret_opened`, and `network_started`. It emits no input digest, ISBN, title,
query, path, resource identity, stable correlation value, proposal, or write
authority. Runtime never loads the expected-output fixture before emission.

The C1-only `c1-inert-report-registry-v1.json` and its golden, alternate,
mutation, boundary, title-only, invalid-extra, privacy, and zero-authority
evidence live under `tests/fixtures/hardcover_identity/`. The public
scanner can be run from the repository root with
`python3 scripts/generate_hardcover_c1_privacy.py`; it regenerates and verifies
the exact present-tree aggregate in `c1-inert-report-privacy-scan-v1.json`.
Neither artifact makes claims about future scenarios; both contain only
`inert-report-v3`.
