# Audiobookshelf Inventory

F05a adds a report-only inventory command for one configured Audiobookshelf
2.35.1 connection. It authenticates through the F04 compatibility check, then
uses only `GET /api/libraries`, paged `GET /api/libraries/{id}/items`, and
expanded `GET /api/items/{id}` requests. Redirects, cookies, proxy environment
variables, compressed responses, unknown service versions, and non-origin
endpoints fail closed. The bearer credential is attached only to those GET
inventory routes.

Run a reconciliation with:

```console
mechanical-librarian audiobookshelf-inventory \
  --config /run/secrets/config-v1.json \
  --database /var/lib/mechanical-librarian/state.sqlite
```

Inspect its durable state without resolving the target credential or making a
network request:

```console
mechanical-librarian inventory-inspect \
  --config /run/secrets/config-v1.json \
  --database /var/lib/mechanical-librarian/state.sqlite
```

The scan is persisted as `running` before target I/O. Its owner combines the
process ID with an operating-system process-start discriminator, so even a
container restart that reuses PID 1 changes an abandoned scan to
`incomplete/interrupted` without treating a genuinely live process as dead.
Each pass has cumulative page,
item, response-byte, deadline, and stability-delay bounds. Because the pinned
API has neither a stable cursor nor a snapshot token, the application reads two
complete passes and publishes only when their target and fingerprint sets are
equal. A repeated page, duplicate target, changed total, recent item, changed
detail, malformed response, partial read, or bound failure leaves the previous
current inventory untouched. Disappearance is published only within the same
transaction that marks an equal two-pass scan complete.

All configured libraries in one invocation share a durable reconciliation
cohort. Movement is reported only when every distinct member of that cohort
completed and the same connection-scoped external identity is removed in one
member and active in another. Older or disabled-library observations cannot be
reported as movement, and a partial cohort reports none.

Expanded responses are normalized at the adapter boundary. The durable
`inventory-snapshot-v2` representation keeps vendor-neutral typed states for
present, missing, null, empty, malformed, and unsupported fields. File and
cover locations become one-way identities; raw payloads, service paths,
credentials, playback state, and endpoints are never persisted. Immutable
history supports A→B→A reconciliation by re-pointing to the original A snapshot.

Migration 4 is additive: it leaves the F03 `snapshot-v1` tables readable and
adds immutable v2 history, current pointers, scan state, and per-scan items.
The snapshot history tables reject updates and deletes. A scan publication is
fenced against the current set observed when the scan began.

F05a deliberately does not implement event subscriptions, polling schedules,
automatic retry/backoff, or compatibility-drift recovery. Those remain in the
separately tracked F05b outcome.

The disposable qualification changes a file observation, reconciles a
disappearance, and drives partial, repeated, cyclic, malformed, and unstable
sequences through the durable CLI path. Every injected failure must preserve
the prior current set and immutable history.
