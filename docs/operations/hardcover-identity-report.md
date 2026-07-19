# Hardcover Identity Report

`hardcover-identity-report` is the installed, report-only F09f surface.
Production remains preactivated: it cannot contact Hardcover or open a private
query, database, or provider secret. Contributor synthetic scenarios may open
only an explicitly supplied absent disposable database and never compose a
provider credential or network client.

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
`557dfdf0300bf2d59fa5571fd3af2136fd4f6099e14b1f1d1b953b0cb56a9d11`.
It contains only the C1 inert and C2 identity scenarios and binds the C2-scoped
output schema, privacy contract, and privacy scan. See the
[F09f identity contract](../reference/hardcover-f09f-contract.md) for exact
compatibility, alias, precedence, and privacy rules.

### Durable work and recovery preview

F09f increment 4 installs a repository-backed qualification scenario without
activating the production provider:

```text
mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario durable-work-v3 \
  --input - --database ABSENT_DISPOSABLE_PATH --format json
```

The path must be absolute and absent beneath a newly prepared empty directory
owned by the invoking user with mode `0700`; relative paths, existing entries,
permissive parents, and nonempty parents are rejected before mutation. The
command builds an owner-only staged database, regenerates the genuine schema-8
migration prefix plus sentinel, compares its canonical logical snapshot digest,
checks its recorded checksums, integrity, and foreign keys, then proves the
additive schema-9 migration and publishes the completed database without
replacing a competing entry. Existing databases are rejected unchanged.

The canonical script exercises acceptance, a fenced claim, request-start
accounting, publication rollback, process restart, lease-expiry recovery,
stale-fence rejection, retry, matched publication, and the exact 24-hour
currentness boundary. Gate ownership is an injected structural fact; this
increment installs no deployment gate, process loop, query-file read, secret
lookup, DNS, socket, HTTP, live Hardcover access, proposal, review, or write
authority. Production mode still returns `disabled` or
`provider_not_activated` before opening durable identity work.

From the repository root, the canonical demonstration is:

```sh
preview_dir="$(mktemp -d)"
uv run --frozen --no-editable --reinstall-package mechanical-librarian \
  mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario durable-work-v3 --input - \
  --database "$preview_dir/state.sqlite" --format json \
  < tests/fixtures/hardcover_identity/c4-durable-work-input.json
```

Stdout must be byte-identical to
`tests/fixtures/hardcover_identity/c4-durable-work-example-output.json`.
The cumulative C4 registry is `preview-registry-c4-v6.json`, SHA-256
`3e5183fb4ddff6674717f870c99b82ecb24eb2d232d759cf08957d2506d93e81`.
The registry also binds contributor-independent schema-8 logical snapshot and
ledger resources used only for qualification; runtime reconstructs and
validates the prefix and never loads the expected output before emission.

### Safe executor preview

F09f increment 5 installs the foreground lifecycle and deployment gate behind
one synthetic-only executable scenario:

```text
mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario safe-executor-v3 \
  --input - --database ABSENT_DISPOSABLE_PATH --format json
```

The disposable-path rules are the same as the durable-work preview. The
command publishes an owner-only schema-9 database, retains the owner-only
`.mechanical-librarian-hardcover-identity.lock`, and runs a strict set of
semantic cases through the real one-attempt application executor, repository,
and descriptor-bound nonblocking `flock`. It proves persisted aggregate and
caller deadlines before the gate, one successful and one retryable attempt,
busy-gate behavior, caller-deadline clipping, exact late-result rejection,
closed gate-failure mapping, and stale-generation replacement. There is no DNS,
socket, HTTP, credential, live Hardcover request, proposal, or production
composition. Contributor labels and process names are control-only. One fixed
internal owner crosses the persistence seam; no persisted/runtime identity or
value is derived from those controls, and equal incidental vocabulary is
allowed.
Qualification captures transient WAL/SHM only under the synthetic privacy
observer, using one exactly restored test-only timestamp mutation; no such
materialization path is production authority.

The executor inspects the persisted 30-second aggregate deadline and caller
deadline before trying the gate. A busy gate returns `in_progress` without a
claim or start. Under the gate, claim still does not consume budget; atomic
renew-and-start requires six seconds remaining, renews the lease for 15 seconds,
and alone authorizes one provider attempt. Its context ends at the earlier of
the caller deadline and start plus five seconds. A provider result arriving at
or after that boundary is converted to timeout before durable publication.
Retryable outcomes with at least the exact six-second reserve publish
retry-wait state and return `in_progress`. If insufficient reserve instead
causes publication to commit terminal failure, the result is
`retry_exhausted`. This increment does not wait, retry, recover, cancel, or
install signal handlers.
The gate remains held through durable publication and is released on every
ordinary and base-exception path.

The canonical demonstration is:

```sh
preview_dir="$(mktemp -d)"
uv run --frozen --no-editable --reinstall-package mechanical-librarian \
  mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario safe-executor-v3 --input - \
  --database "$preview_dir/state.sqlite" --format json \
  < tests/fixtures/hardcover_identity/c5-safe-executor-input.json
```

Stdout must be byte-identical to
`tests/fixtures/hardcover_identity/c5-safe-executor-example-output.json`.
The cumulative C5 registry is `preview-registry-c5-v6.json`, SHA-256
`0f0ae4c1284af4562d8c2c90c0626d47dfbbe927c10400c3e8849bd3d4c33a3a`.
It derives from the merged C4 registry and adds only `safe-executor-v3`.

The gate is deployment-scoped, not credential-scoped. Operators must not reuse
one Hardcover credential across independently configured deployments. The
lock file is never individually removed or replaced while a deployment may be
running. Disposal means stopping every process and removing the whole state
root under the stopped-state procedure.

### Durable data lifecycle

Schema 9 stores only an opaque work key and bounded structural state:
generations, candidates, claims, attempts, retry schedules, events, terminal
results, observations, and the currentness pointer. Candidates, claims,
schedules, events, results, and observations are append-only. Attempts can be
finalized once; generation and current-pointer changes are fenced transactions.
No query ISBN, local title, credential, provider payload, path, or contributor
script is stored. The observation is a closed provider-neutral value serialized
only as `{"outcome":"matched","row_count":N}` with `N` from 1 through 4;
arbitrary maps and provider fields cannot enter the persistence adapter.

This increment deliberately has no row-level retention or deletion executor.
Keep the work history with the curator database under the configured curator
data backup policy. Aggregate export means a verified stopped-state backup of
the complete `/data` database and its migration ledger; copying selected rows
is not a supported recovery artifact. To erase this preview state, stop every
Mechanical Librarian process, retain any required audit/backup evidence, and
delete the whole disposable database. Never delete individual identity-work
rows or edit the current pointer. Production retention controls and supported
selective export/deletion require a later activated surface.
