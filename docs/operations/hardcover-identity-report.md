# Hardcover Identity Report

`hardcover-identity-report` is the installed, disabled-by-default report-only
F09f surface. Enabled operation reads one strict private query, persists bounded
schema-11 work, acquires the deployment gate, prepares the fixed read-only
Hardcover ISBN request, and claims plus durably starts the attempt immediately
before the selected-address HTTPS exchange.
Contributor synthetic scenarios remain credential-free and cannot construct
production provider or network authority.

## Production syntax

```text
mechanical-librarian hardcover-identity-report \
  --config PATH --database PATH --query-file PATH [--format json|text]
```

An absent or explicitly disabled `hardcover_provider` block returns `disabled`
before query, state, secret, DNS, or network access. Enabled operation validates
the query before state and returns `invalid_input` with exit 2 for invalid or
unsafe query files. It durably accepts work before secret/client/DNS/network,
uses at most three starts, and emits only the closed public outcomes in the
[F09f contract](../reference/hardcover-f09f-contract.md). Syntax or
configuration failure emits only a fixed path-free diagnostic.

The production-only `--qualification-one-shot` flag requires an absent state
path in an empty owner-only `0700` directory. It durably limits the aggregate
to one start; a retryable response becomes `retry_exhausted` and never sends a
second request. It is reserved for exact-head installed-wheel qualification.

### Authorized production one-shot procedure

Use this procedure only for the separately authorized final request at an
exact reviewed and qualified head. Build the wheel at that head, record both
identities, and install that exact wheel before creating any provider authority:

```sh
qualification_head="$(git rev-parse HEAD)"
wheel_path="$(find dist -maxdepth 1 -type f -name 'mechanical_librarian-*.whl' -print -quit)"
test -n "$wheel_path"
wheel_sha256="$(shasum -a 256 "$wheel_path" | awk '{print $1}')"
python -m pip install --force-reinstall --no-deps "$wheel_path"
```

Prepare a new owner-only empty stopped-state root and keep its database path
absent. `mktemp -d` is mandatory here so a previous qualification root cannot
silently be reused:

```sh
umask 077
qualification_root="$(mktemp -d /data/qualification/f09f-one-shot.XXXXXX)"
test -z "$(find "$qualification_root" -mindepth 1 -maxdepth 1 -print -quit)"
database_path="$qualification_root/state.sqlite"
install -d -m 0700 /run/mechanical-librarian /run/mechanical-librarian/secrets
install -m 0600 /secure-input/f09f-query.json \
  /run/mechanical-librarian/f09f-query.json
install -m 0600 /secure-input/hardcover-token \
  /run/mechanical-librarian/secrets/hardcover-token
test ! -e "$database_path"
```

Prepare the strict private query as a regular, current-effective-user-owned,
link-count-one file with owner read permission and no group or other permission
bits. Configure `hardcover_provider.enabled: true`, the accepted positive
`authority_revision`, and an absolute `secret_reference`. The referenced
credential must itself be a regular file owned by the current effective user,
have link count one, be owner-readable with no group or other permission bits,
and contain 1–2,048 bytes. Every byte must be ASCII `0x21`–`0x7e`: no
whitespace, scheme, or `Bearer` prefix is allowed. Deliver the raw token with a
secret-management mechanism that does not place it in command history, logs,
or this document. The enabled provider member below is valid JSON and must be
incorporated into a complete valid `config-v1` JSON document. The query file
contains exactly the accepted `isbn` and `title` members described in the
private-query contract.

```json
{
  "hardcover_provider": {
    "enabled": true,
    "secret_reference": "/run/mechanical-librarian/secrets/hardcover-token",
    "authority_revision": 1
  }
}
```

Run exactly the installed production command once, capturing the private
matched report only inside the disposable root:

```sh
result_path="$qualification_root/result.json"
mechanical-librarian hardcover-identity-report \
  --config /etc/mechanical-librarian/config.json \
  --database "$database_path" \
  --query-file /run/mechanical-librarian/f09f-query.json \
  --format json \
  --qualification-one-shot >"$result_path"
```

`matched` is the only qualifying outcome and exits 0. Every other closed
outcome exits 2 for this flag. The durable one-start budget is consumed at the
final gate-held start boundary: a non-match, retryable failure, or ambiguous
transport result does not authorize a second request, and retryable results
close as `retry_exhausted`. Verify the closed matched shape without printing its
private identifiers, count the durable attempted egress, and derive a sanitized
evidence digest from only the head, wheel digest, count, and result digest:

```sh
python - "$result_path" <<'PY'
import json, pathlib, sys
report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert set(report) == {"schema", "outcome", "edition_id", "book_id", "comparison"}
assert report["schema"] == "hardcover-identity-report-v6"
assert report["outcome"] == "matched"
assert type(report["edition_id"]) is int and 1 <= report["edition_id"] <= 2_147_483_647
assert type(report["book_id"]) is int and 1 <= report["book_id"] <= 2_147_483_647
assert report["comparison"] in {"equal", "different"}
PY
attempted_egress="$(python - "$database_path" <<'PY'
import sqlite3, sys
connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
rows = connection.execute(
    "SELECT g.attempt_count FROM hardcover_identity_generations g "
    "JOIN hardcover_identity_egress_policies p USING(work_key,generation) "
    "WHERE p.max_starts=1"
).fetchall()
assert len(rows) == 1 and rows[0] == (1,)
print(1)
PY
)"
result_sha256="$(shasum -a 256 "$result_path" | awk '{print $1}')"
evidence_sha256="$(printf '%s\n' \
  "$qualification_head" "$wheel_sha256" "$attempted_egress" "$result_sha256" \
  | shasum -a 256 | awk '{print $1}')"
printf 'head=%s\nwheel_sha256=%s\nattempted_egress=%s\nresult_sha256=%s\nevidence_sha256=%s\n' \
  "$qualification_head" "$wheel_sha256" "$attempted_egress" \
  "$result_sha256" "$evidence_sha256"
```

Retain only those five aggregate lines. Do not retain the query, credential,
provider response, paths, unrestricted output, database, or private matched
observation. Stop every Mechanical Librarian process using the root, then
remove the entire disposable root plus the private query and credential through
the approved secret-disposal process. Verify all three paths are absent. Never
delete or edit individual state rows while a process is running.

The accepted configuration and redacted export are documented in the
[configuration contract](../reference/configuration.md). Configuration changes
require restart plus candidate and review re-evaluation.

## Private query contract

The enabled path accepts an owner-only regular file of 1–4,096 bytes.
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
Synthetic preview remains credential-free after production activation.

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
authority. At that historical increment, production remained preactivated and
returned only the disabled report.

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
`94380cffa51cd0bb843eafa5aa8edb39e14bc7c254c90b9feb8d08b497ac2d22`.
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

The operation-specific proof booleans are universal predicates over the
selected applicable cases. If a valid strict subset selects no corresponding
operation, that proof is true for the empty applicable set. If one or more
corresponding operations are selected, the proof is true only when every
selected applicable case passes its actual hard check. `all_cases_passed`
still requires every proof boolean to be exactly true. The canonical ten-case
fixture is the separate complete applicability-coverage proof; a successful
strict subset demonstrates semantic evaluation without claiming that coverage.

The executor inspects the persisted 30-second aggregate deadline and caller
deadline before trying the gate. A busy gate returns `in_progress` without a
claim or start. After acquiring the gate, the executor re-inspects the exact
generation without mutation: `pending` may continue, `claimed` or `retry_wait`
returns `in_progress`, and a reconciled still-current `succeeded` returns
`matched`. Missing, inconsistent, or expired current evidence and `failed` or
`cancelled` fail closed as `persistence_failure`; this projection does not
reconstruct a terminal reason. Under the gate, claim still does not consume
budget; atomic renew-and-start requires six seconds remaining, renews the lease
for 15 seconds,
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
`1ca4ff13721a040bae2794032db84beaebd7e80288d8fb02d645bae046bc525d`.
It derives from the merged C4 registry and adds only `safe-executor-v3`.

### Lifecycle orchestration preview

F09f increment 6 adds the cumulative credential-free
`lifecycle-orchestration-v3` scenario. It drives the same manager, SQLite
repository, descriptor gate, and one-attempt executor through persisted retry,
restart, interruption recovery, cancellation, and durable one-shot exhaustion:

```sh
preview_dir="$(mktemp -d)"
uv run --frozen --no-editable --reinstall-package mechanical-librarian \
  mechanical-librarian hardcover-identity-report \
  --mode synthetic-preview --scenario lifecycle-orchestration-v3 --input - \
  --database "$preview_dir/state.sqlite" --format json \
  < tests/fixtures/hardcover_identity/c6-lifecycle-orchestration-input.json
```

One foreground invocation may wait after its own retryable attempt; a restarted
invocation returns `in_progress` before the persisted due time. Backoff is
exactly one second after start 1 and two seconds after start 2, extended by a
valid bounded Retry-After. No generation consumes more than three starts, and a
limit-1 generation cannot send again after restart. Temporary main-thread
SIGINT/SIGTERM handlers set only the local cancellation event and are restored
on every exit. This contributor synthetic-preview scenario composes no
credentials, DNS, sockets, HTTP, proposal, or write authority. The activated
production command is a separate surface and, only after durable authority is
established, reads the configured credential and may perform the fixed HTTPS
request described above.

Stdout must match `c6-lifecycle-orchestration-example-output.json`. The
cumulative registry is `preview-registry-c6-v1.json`, SHA-256
`398479df75c97d92b2e5a329397ab2df4c0efc098a6343a702d8c7dc92687e2c`.

The gate is deployment-scoped, not credential-scoped. Operators must not reuse
one Hardcover credential across independently configured deployments. The
lock file is never individually removed or replaced while a deployment may be
running. Disposal means stopping every process and removing the whole state
root under the stopped-state procedure.

### Durable data lifecycle

Schema 11 stores an opaque work key and bounded durable lifecycle state:
generations, candidates, claims, attempts, retry schedules, events, terminal
results, observations, and the currentness pointer. Candidates, claims,
schedules, events, results, and observations are append-only. Attempts can be
finalized once; generation and current-pointer changes are fenced transactions.
No query ISBN, local title, credential, raw provider payload, path, or
contributor script is stored. A current matched observation privately stores
only outcome, row count, positive edition ID, positive book ID, and the exact
bounded validated provider title needed for truthful reuse and title
comparison. Arbitrary maps and other provider fields cannot enter the
persistence adapter. Legacy two-field observations remain immutable history
but are non-reusable, and migration 11 expires their current pointers.
Schema 10 added one immutable per-generation egress limit of 1 or 3, bound
atomically with the first start so a later caller cannot widen it.

This increment deliberately has no row-level retention or deletion executor.
Keep the work history with the curator database under the configured curator
data backup policy. Aggregate export means a verified stopped-state backup of
the complete `/data` database and its migration ledger; copying selected rows
is not a supported recovery artifact. To erase this preview state, stop every
Mechanical Librarian process, retain any required audit/backup evidence, and
delete the whole disposable database. Never delete individual identity-work
rows or edit the current pointer. Production retention controls and supported
selective export/deletion require a later activated surface.
