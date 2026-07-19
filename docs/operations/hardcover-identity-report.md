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

### Synthetic identity decision

```text
mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario identity-decision-v3 \
  --input - --format json
```

The closed input contains one canonical ISBN-13 and its derived ISBN-10 (or
`null` for `979`), one transient local title, and zero to four structural
provider rows. The application calls the provider-neutral `BookIdentityProvider`
port exactly once through an injected in-memory fake, validates every row,
resolves edition and book aliases independently, and emits only outcome,
validated-row count, collapsed-alias count, and coarse title comparison.

The fake performs no decision and grants no file, secret, DNS, socket, network,
database, proposal, review, or write authority. Expected output and registry
resources are qualification oracles and are not read before runtime emission.
Production mode remains `disabled` or `provider_not_activated`.

From the repository root, the canonical demonstration is:

```sh
uv run --frozen --no-editable --reinstall-package mechanical-librarian \
  mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario identity-decision-v3 \
  --input - --format json \
  < tests/fixtures/hardcover_identity/c2-identity-decision-input.json
```

Its stdout must be byte-identical to
`tests/fixtures/hardcover_identity/c2-identity-decision-example-output.json`.
The current cumulative registry is `preview-registry-c2-v6.json`, SHA-256
`77c36a2cb2b2c1a7e8a00292d21b4e2417233dc7f11f411e17d7491a6664f34d`.
It contains only the C1 inert and C2 identity scenarios and binds the C2-scoped
output schema, privacy contract, and privacy scan. See the
[F09f identity contract](../reference/hardcover-f09f-contract.md) for exact
compatibility, alias, precedence, and privacy rules.
