# ADR-0010: Reset and Simplify the Pre-stable 0.4 Baseline

- Status: accepted
- Date: 2026-07-16
- Owners: project maintainers
- Supersedes: ADR-0003
- Superseded by: none

## Context

Mechanical Librarian has no published release or supported upgrade source. The
initial report-only checkpoints deliberately accumulated small executable
demonstrations, durable schema revisions, compatibility identities, future-only
contracts, and qualification fixtures while the application established safe
end-to-end seams.

After the 0.3 live title-report checkpoint, carrying every internal checkpoint
as a permanent upgrade and runtime compatibility surface would preserve two
inventory models, a standalone synthetic job control plane, demonstration
commands, title-preview and synthetic-review persistence lanes, duplicate
qualification controllers, and contracts with no delivered consumer. That
would make later changes harder to reason about without protecting a supported
user deployment.

The consolidation still crosses persistence, hostile-input parsing,
configuration, credentials, network access, and restart behavior. Removing
pre-stable compatibility does not permit silent mutation, automatic deletion,
weaker isolation, or loss of current report-only behavior.

## Decision

This ADR supersedes ADR-0003 and restates the parts of that decision that remain
current. SQLite in WAL mode remains the default state store. Database access
remains behind repository and transaction interfaces owned by the application,
and domain code does not import SQLite or ORM types. Every schema change from a
supported source uses a forward migration with explicit compatibility and
rollback documentation, and CI exercises every supported prior schema. An
unpublished internal source explicitly classified as unsupported is rejected;
creating a separate clean-state baseline is not a migration of that source.

0.4.0 is an internal report-only consolidation checkpoint. It is not a
functional slice, published release, tag, supported-version promise, or release
train.

No pre-0.4 internal development database is a supported 0.4 upgrade source. A
0.4 binary must identify such state and reject it before changing a schema
object, migration record, or application row. Diagnostics are bounded and
redacted and direct the operator to retain a verified copy and rebuild
report-only state explicitly from configured sources. The application never
deletes, resets, repairs, or reinterprets that state automatically.

Until a checkpoint is selected and published as the first supported release,
persisted-schema changes use a versioned, deterministic, atomic clean-state
baseline and rejection tests. They need not retain executable upgrades from
every earlier internal checkpoint. This does not waive schema identity,
migrations for supported sources, transaction fault injection, rollback,
backup and restore design, tamper detection, or interruption tests. The final
0.3 schema and symbol inventory must be refreshed after #238 closes; this
decision does not invent the resulting migration or baseline number.

The 0.4 work also applies these pre-stable boundaries:

- retire the F02 `job-heartbeat` and F03 `synthetic-inventory` commands and
  their dedicated runtime contracts without compatibility aliases;
- retain applicable safety invariants as characterization of the current
  durable-work and inventory models;
- remove future-only runtime and test scaffolding only after a final-0.3
  consumer inventory, while preserving current F07b, F09f, and F09g behavior
  and keeping accepted future requirements in open issues and authoritative
  documents; and
- move future DUX facts to their open owners while retaining source, licence,
  attribution, sanitization, privacy, and current generated-fixture evidence.
  DUX remains separate from the independently authored #21 chaos corpus;
- retain a bespoke product runtime or persistence mechanism only when a current
  final-0.3 runtime consumer exists and the mechanism either serves more than
  one genuinely equivalent current use or enforces a named product or safety
  invariant unavailable from a simpler existing facility. Fixture and
  qualification machinery instead requires a current mandatory assurance gate
  tied to retained behavior or a named invariant, and may be consolidated or
  removed only after its replacement independently reproduces the same
  evidence. Tests and documentation do not count as product-runtime consumers;
- prefer a maintained dependency or platform facility only when it replaces
  the old owner and passes compatibility, resource, isolation, licence,
  container, and hostile-input boundaries. It must not wrap the bespoke owner
  into another layer. A retained custom component records the failed
  alternative and invariant that requires it;
- retire the F09a synthetic preview, F09b fixed-case preview, and F10a synthetic
  review-case runtime and persistence lanes without aliases. Preserve only
  provider, policy, comparison, lineage, and safety behavior consumed by F09f
  or F09g, and transfer the exact production case lifecycle to #240 and #241
  with release qualification in #153; and
- remove the unconsumed #49 configuration export and dry-run import-diff
  runtime helpers in #258 while open WP1 parent #2 retains their accepted future
  setup/administration requirement. Current canonical redacted serialization,
  strict loading, schema generation, and unsafe-input rejection remain.

The exact issue order and checkpoint exit criteria are authoritative in
[Delivery slices](../../product/delivery-slices.md#delivery-checkpoint-040-pre-stable-consolidation).

## Consequences

### Positive

- The first supported release can start from one current inventory model, one
  durable-work substrate, and only contracts owned by delivered behavior.
- Unsupported state fails before mutation instead of relying on a long chain of
  internal-only transformations.
- Operators receive an explicit retain-and-rebuild path without destructive
  automation.
- Current least-authority and hostile-input qualification gates remain
  independently visible requirements of the consolidation.

### Negative

- Contributors with pre-0.4 development state must create new state and rebuild
  report-only observations from configured sources.
- Historical demonstrations stop being supported operator commands.
- Historical title-preview and synthetic-review state cannot be imported or
  reinterpreted as production state.
- Every retained custom mechanism must expose its current consumer, owner,
  invariant, failure evidence, and build-versus-adopt disposition.
- The final 0.3 inventory is a mandatory precondition before implementation can
  select exact symbols, schema objects, or compatibility identities to remove.

### Neutral or deferred

- This decision does not select the first published release or its version.
- It does not authorize review admission, a human or AI decision, approval,
  apply, target or media writes, or destructive behavior.
- Compatibility from the first published release onward remains governed by
  the release, upgrade, deprecation, and migration contracts.

## Alternatives considered

### Carry every internal checkpoint forward

Retaining executable migrations and compatibility aliases for every
unpublished checkpoint would preserve contributor development databases, but
would also make two inventory models, demonstration-only commands, and
future-only contracts part of the permanent support and qualification surface.
That cost does not protect a published deployment, so it was rejected.

### Reset or upgrade unsupported state in place

Automatically deleting, renaming, repairing, copying, reinterpreting, or
upgrading an unsupported database would make startup destructive and could
discard the only recoverable copy. It was rejected in favor of stopping before
mutation and requiring an explicit, separately verified retain-and-rebuild
procedure.

### Defer consolidation until the first published release

Waiting would let obsolete contracts shape additional implementation and make
the first supported compatibility boundary harder to inspect. The
consolidation therefore occurs as a non-release checkpoint while the project
still has no supported upgrade source.

## Validation

- A clean 0.4 state baseline is deterministic and atomic under injected
  interruption.
- Pre-0.4, newer, partial, checksum-mismatched, and tampered state is rejected
  before mutation and remains byte-for-byte available to the operator.
- Retain-and-rebuild guidance contains no private database path, metadata,
  credential, or media content.
- The final-0.3 live title report and exact zero-result partitions retain their
  success, restart, replay, stale-input, disabled-provider, and bounded-failure
  behavior.
- Current target and source validity precedes provider disclosure or I/O, and
  the target plus both evidence lineages are atomically revalidated before
  proposal publication.
- F07b cannot construct provider credential or network authority; F09f cannot
  construct target or media authority.
- Retired F02, F03, F09a, F09b, and F10a commands, public contracts, schema
  objects, fixtures, and qualification paths are exactly absent from the final
  installed artifact and clean state.
- Every retained bespoke runtime or assurance mechanism names its consumer,
  owner, invariant, focused failure proof, and build-versus-adopt disposition.
- A maintained component is adopted only when the prior owner is absent and no
  credential, network, parser, target/media, process, persistence, or mutation
  authority widens.
- Ready-PR traceability uses one raw-body and GitHub-native gate with no
  rendered-HTML parser or tracking-exemption control plane.
- SQLite locking, repository boundaries, forward migrations from every
  supported source, rollback documentation, and unsafe-location rejection
  remain covered by the ADR-0003 validation obligations restated here.
