# Audiobookshelf Inventory and Polling

F05a adds a report-only inventory command for one configured Audiobookshelf
2.35.1 connection. It authenticates through the F04 compatibility check, then
uses only `GET /api/libraries`, paged `GET /api/libraries/{id}/items`, and
expanded `GET /api/items/{id}` requests. Redirects, cookies, proxy environment
variables, compressed responses, unknown service versions, and non-origin
endpoints fail closed. The bearer credential is attached only to those GET
inventory routes.

F05b adds durable trigger scheduling around the same least-authority reader.
An operator or external scheduler supplies a stable bounded trigger ID; intent,
policy, total budgets, and elapsed deadline are committed before the credential
is resolved or any target request begins:

```console
mechanical-librarian audiobookshelf-poll \
  --trigger nightly-2026-07-13 \
  --config /run/secrets/config-v1.json \
  --database /var/lib/mechanical-librarian/state.sqlite
```

A retry-wait delivery before its persisted eligibility time, or a blocked or
completed delivery of the same trigger, performs no target I/O. Once a
retry-wait poll becomes eligible, redelivery claims the next fenced attempt and
may perform target I/O under the original cumulative budgets. Inspect the
opaque poll ID printed by the command without loading configuration or
credentials:

```console
mechanical-librarian inventory-poll-inspect \
  --poll-id poll_0123456789abcdef0123456789abcdef \
  --database /var/lib/mechanical-librarian/state.sqlite
```

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

Every configured-library row in the reconciliation cohort is persisted as
`running` before the shared compatibility and authorization probe resolves the
target credential or performs target I/O. Probe time and response bytes use the
same inventory operation bounds, and a failed probe terminalizes every begun
row with its typed failure before any library or item request. Its owner
combines the process ID with an operating-system process-start discriminator,
so even a container restart that reuses PID 1 changes an abandoned scan to
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

Complete library results remain process-local until every configured library
is stable. Current pointers, removals, immutable history, and scan completion
then publish in one cohort transaction. A later library failure discards the
prepared observations, terminalizes all cohort scans, and leaves both current
state and history unchanged. This prevents an item move between libraries from
appearing as a disappearance during a partial poll.

Each poll lifecycle transition emits a fixed-schema structured event and
bounded counters for adapter, stage, attempt, pages, items, response bytes,
typed failure, and compatibility state. These diagnostics never include
titles, authors, endpoints, paths, response bodies, or credentials.

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

Migration 5 is also additive. It adds poll intent and attempt records plus the
scan-to-claim binding. Claims use monotonic fences and bounded leases. Every
scan mutation validates the live poll, attempt, worker, fence, lease, backward-
clock guard, and cancellation state in the same SQLite write transaction. A
reclaimed stale worker cannot finish or publish. Abrupt death marks only scans
linked to the expired attempt incomplete; cooperative cancellation is durable
and never acknowledged as success.

Transport, timeout, 408, 429, 5xx, and scan-instability failures use bounded
restart-safe backoff. A valid `Retry-After` delta or HTTP date may extend local
backoff only within the frozen elapsed window. Authentication, authorization,
missing configuration, incompatible version, and contract-drift failures are
blocked until governing configuration changes. A missing item detail is scan
instability; it is never interpreted as a configured-library absence.

Socket.IO remains unavailable: Audiobookshelf 2.35.1 socket authorization does
not accept the supported API key and successful socket authentication mutates
`lastSeen`. No JWT login or socket client is constructed. Recurring cadence is
owned by the caller; each supplied trigger becomes one bounded durable poll.

The disposable qualification changes a file observation, reconciles a
disappearance, and drives partial, repeated, cyclic, malformed, and unstable
sequences through the durable CLI path. Every injected failure must preserve
the prior current set and immutable history.
